"""#970 slice 2: real SQLite races, injected effects, clocks and held lanes. No live calls."""
import json
import random
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, Future
from unittest import mock

import pytest

from personal_agent.agent_runtime import WorkLedger, WorkBudget, ToolError, WORK_STOPPED
from personal_agent.browser_session import BrowserProfile, BROWSER_WAIT_SECONDS, BUSY_WORK_TEXT, BUSY_LOGIN_TEXT
from personal_agent.entrusted_lines import EntrustedLines
from personal_agent.quickstart_service import AgentService, LANE_WORK
from personal_agent.quickstart_store import QuickStore
from personal_agent.providers import ModelAdapter
from personal_agent.decision import (BinaryDecision, SelectionDecision, FixtureDecisionEngine,
                                     fixture_confidence)
from personal_agent import preparations as prep, trust_record
import test_connect_effects as effects


@pytest.fixture
def lane(tmp_path):
    store = QuickStore(tmp_path)
    def transport(*args, **kwargs):
        raise AssertionError("unexpected external call")
    service = AgentService(store, ModelAdapter(transport), transport)
    spawned = []
    service.work_lane_spawn = lambda target, name: spawned.append(target)
    yield service, store, spawned
    service.stop.set()
    service.shutdown_work_lanes()


def enqueue(store, key, owner=True, chat=None):
    return store.enqueue("fixture request", key, owner_typed=owner, channel='web', chat_id=chat)


def finish(store, work):
    with store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (work,))


def race(*calls):
    barrier = threading.Barrier(len(calls))
    def start(call):
        barrier.wait(timeout=10)
        return call()
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(start, call) for call in calls]
        return [future.result(timeout=15) for future in futures]


def scheduled(service, store, key, line=None):
    row = service.preparations.create(kind='prepare', goal='fixture', due_at=1000,
                                     timezone='UTC', channel='web', state=prep.STATE_SCHEDULED, line_id=line,
                                     recurrence='daily', created_from=None)
    work = enqueue(store, prep.request_key(row['id'], 1000), owner=False)
    with store.db() as db:
        db.execute("UPDATE preparations SET state='running',last_run_job_id=? WHERE id=?", (work, row['id']))
    return work, row['id']


def test_default_pool_runs_three_actual_work_phases_together(lane):
    service, store, _ = lane
    assert service.work_lanes == 3
    service.work_lane_spawn = service._spawn_work_lane
    works = [enqueue(store, str(i), chat=i) for i in range(3)]
    entered = threading.Barrier(4)
    release = threading.Event()
    runs = []
    def route(run):
        runs.append(run)
        run.early_plan = Future()
        run.early_plan.set_result(run.job['id'])
        service._work_local.budget = WorkBudget(clock=lambda:1000)
        entered.wait(timeout=10)
        assert release.wait(10)
        assert service.current_work_id == run.early_plan.result()
        service._work_local.budget.check()
        assert service._work_local.budget.attempts_used == 0
        finish(store, run.job['id'])
        return True
    with mock.patch.object(service, '_route_work', side_effect=route):
        try:
            assert service.dispatch_work() == 3
            entered.wait(timeout=10)
            assert service.lane_work_ids() == set(works)
            assert service._work_pool._max_workers == 3
        finally:
            release.set()
            service.shutdown_work_lanes()
    assert not service.lane_work_ids()
    assert all(run.early_plan is None for run in runs)
    assert service.current_work_id is None


@pytest.mark.parametrize('lanes', [1, 3])
def test_racing_claimants_never_execute_one_work_twice(lane, lanes):
    service, store, _ = lane
    service.work_lanes = lanes
    work = enqueue(store, 'effect')
    effects = []
    def route(run):
        effects.append(run.job['id'])
        finish(store, run.job['id'])
        return True
    with mock.patch.object(service, '_route_work', side_effect=route):
        race(service.run_one, service.run_one)
    assert effects == [work]
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM messages WHERE job_id=? AND role='user'", (work,)).fetchone()[0] == 1


@pytest.mark.parametrize('lanes', [1, 3])
def test_shutdown_stops_active_work_before_joining_pool(lane, lanes):
    service, store, _ = lane
    service.work_lanes = lanes
    service.work_lane_spawn = service._spawn_work_lane
    works = [enqueue(store, f'shutdown-{i}', chat=i) for i in range(lanes)]
    entered = threading.Barrier(lanes + 1)
    release = threading.Event()
    effects = []

    def route(run):
        budget = run.work_budget = service.work_budget(run.job['id'])
        entered.wait(timeout=10)
        assert release.wait(10)
        # The same budget check gates tools and is polled by running CLIs.
        budget.check()
        effects.append(run.job['id'])
        finish(store, run.job['id'])
        return True

    with mock.patch.object(service, '_route_work', side_effect=route):
        assert service.dispatch_work() == lanes
        entered.wait(timeout=10)
        shutdown = service._work_pool.shutdown

        def join(*args, **kwargs):
            try:
                assert service.stop.is_set(), 'signal cancellation before joining active lanes'
                assert all(service.work_stopped(work) for work in works)
            finally:
                release.set()
                shutdown(*args, **kwargs)

        with mock.patch.object(service._work_pool, 'shutdown', side_effect=join):
            service.shutdown_work_lanes()
    assert effects == []
    assert not service.lane_work_ids()
    assert all(store.job(work)['status'] == 'failed' for work in works)
    assert all(store.job(work)['error'] == WORK_STOPPED for work in works)


def test_same_run_exact_payment_approval_allows_one_effect_under_race():
    case = effects.EffectBoundary('runTest')
    case.setUp()
    try:
        trust_record.set_money(case.store, case.target, True)
        assert case.offer()['behavior'] == 'deny'
        assert case.approve()['resumed']
        results = race(case.call, case.call)
        # This is the actual permission/approval CAS, not a mocked payment guard.
        assert [result['behavior'] for result in results].count('allow') == 1
        assert case.call()['behavior'] == 'deny'
        with case.store.db() as db:
            assert db.execute("SELECT COUNT(*) FROM tool_events WHERE job_id=? AND tool='connector_permission' "
                              "AND status='allowed'", (case.job['id'],)).fetchone()[0] == 1
    finally:
        case.doCleanups()


@pytest.mark.parametrize('key', ['conv', 'line', 'prep', 'chain'])
def test_conflicting_keys_serialize_settlement_and_effect(lane, key):
    service, store, _ = lane
    if key == 'line':
        line = EntrustedLines(store).open(None, task='fixture', report_promise='fixture')['line_id']
        first, _ = scheduled(service, store, 'one', line)
        second, _ = scheduled(service, store, 'two', line)
    elif key == 'prep':
        first, row = scheduled(service, store, 'one')
        second = enqueue(store, prep.request_key(row, 2000), owner=False)
    elif key == 'chain':
        root = enqueue(store, 'root', owner=False)
        finish(store, root)
        first, second = [enqueue(store, str(i), owner=False) for i in range(2)]
        with store.db() as db:
            db.execute("UPDATE jobs SET relation_kind='retry',related_job_id=? WHERE id IN (?,?)", (root, first, second))
    else:
        first, second = [enqueue(store, str(i), chat=1) for i in range(2)]
    results = race(service._claim_queued_work, service._claim_queued_work)
    assert [r['id'] for r in results if isinstance(r, dict)] == [first]
    # A terminal/parked status does not release a lane still settling approvals/effects.
    service.hold_lane(first, LANE_WORK)
    finish(store, first)
    assert service._claim_queued_work() is False
    service.release_lane(first)
    if key == 'prep':
        with store.db() as db:
            db.execute("UPDATE preparations SET last_run_job_id=? WHERE id=?", (second, row))
    assert service._claim_queued_work()['id'] == second


def test_background_reserve_and_owner_first_even_beyond_old_scan_window(lane):
    service, store, _ = lane
    background = [enqueue(store, f'b{i}', owner=False) for i in range(55)]
    assert service.dispatch_work() == 2
    owner = enqueue(store, 'owner', chat=7)
    assert service.dispatch_work() == 1
    assert service.lane_work_ids(LANE_WORK) == {background[0], background[1], owner}
    assert service.dispatch_work() == 0


def test_scheduled_work_does_not_hold_owner_conversation(lane):
    service, store, _ = lane
    background = enqueue(store, 'background', owner=False, chat=7)
    assert service._claim_queued_work()['id'] == background
    owner = enqueue(store, 'owner', chat=7)
    assert service._claim_queued_work()['id'] == owner
    behind = enqueue(store, 'behind', chat=7)
    assert service._claim_queued_work() is False
    assert store.job(behind)['status'] == 'queued'
    assert not service.start_side_answer(store.job(owner), behind)


@pytest.mark.parametrize('seed', range(5))
def test_randomized_claim_settle_interleavings_keep_keys_and_reserve(lane, seed):
    service, store, _ = lane
    rng = random.Random(seed)
    works = [enqueue(store, str(i), owner=i % 3 != 0, chat=i % 4) for i in range(16)]
    for _ in range(32):
        race(service._claim_queued_work, service._claim_queued_work)
        with store.db() as db:
            running = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE status='running'")]
        assert len(running) <= 3
        assert sum(not row['owner_typed'] for row in running) <= 2
        owners = [(row['channel'], row['chat_id']) for row in running if row['owner_typed']]
        assert len(owners) == len(set(owners))
        if running:
            finish(store, rng.choice(running)['id'])
    assert all(store.job(work)['status'] != 'queued' for work in works)


def test_restart_fences_every_lane_without_replay_or_stale_keys(lane):
    service, store, _ = lane
    works = [enqueue(store, str(i), chat=i) for i in range(3)]
    assert service.dispatch_work() == 3
    ledgers = [WorkLedger(store, work, wall=lambda:1000) for work in works]
    for ledger in ledgers:
        assert ledger.spend(10)
    store.recover()
    assert all(store.job(work)['status'] == 'interrupted' for work in works)
    assert all(ledger.lapse() == 'superseded' for ledger in ledgers)
    restarted = AgentService(store)
    assert restarted.dispatch_work() == 0
    # Recovery is idempotent; it never queues another reply or invokes an effect.
    before = [store.job(work) for work in works]
    store.recover()
    assert before == [store.job(work) for work in works]
    new = enqueue(store, 'new', chat=0)
    assert restarted._claim_queued_work()['id'] == new


def test_one_lane_rollback_keeps_background_progress_and_owner_order(lane):
    service, store, _ = lane
    store.put('work_lanes', 1)
    rollback = AgentService(store)
    spawned = []
    rollback.work_lane_spawn = lambda target, name: spawned.append(target)
    assert rollback.work_lanes == 1
    bg = enqueue(store, 'background', owner=False)
    assert rollback.dispatch_work() == 1
    owner = enqueue(store, 'owner')
    later = enqueue(store, 'later', owner=False)
    assert rollback.dispatch_work() == 0
    finish(store, bg)
    rollback.release_lane(bg)
    assert rollback.dispatch_work() == 1
    assert rollback.lane_work_ids() == {owner}
    finish(store, owner)
    rollback.release_lane(owner)
    assert rollback.dispatch_work() == 1
    assert rollback.lane_work_ids() == {later}


def test_steer_judgment_reservation_survives_target_finishing(lane):
    service, store, _ = lane
    running = enqueue(store, 'running', chat=7)
    service._claim_queued_work()
    message = enqueue(store, 'message', chat=7)
    judges = []
    service.steer_spawn = judges.append
    assert service.consider_steer(message, 'fixture')
    finish(store, running)
    assert service._claim_queued_work() is False
    judges.pop()()
    assert service._claim_queued_work()['id'] == message


def disposition(service, choice):
    def choose(context, candidates, question):
        return SelectionDecision('decided', choice, candidates, fixture_confidence())
    engine = FixtureDecisionEngine(choose=choose)
    service.use_decision_engine(engine)
    service.steer_spawn = lambda target: target()
    return engine


@pytest.mark.parametrize('choice', ['steer', 'beside', 'wait'])
def test_three_way_disposition_uses_existing_steer_and_full_work_paths(lane, choice):
    service, store, _ = lane
    running = enqueue(store, 'running', chat=7)
    assert service._claim_queued_work()['id'] == running
    message = enqueue(store, 'message', chat=7)
    engine = disposition(service, 'steer:' + running if choice == 'steer' else choice)
    assert service.consider_steer(message, 'fixture request')
    assert not service.steer_messages and not service.steer_judging
    assert len(engine.asked) == 1
    if choice == 'steer':
        assert store.job(message)['related_job_id'] == running
        assert store.claim_steers(running) == ['fixture request']
        assert service._claim_queued_work() is False
        # A handed-over steer stays queued until a delivered reply answers it.
        with store.db() as db:
            store.answer_steers(running, 0, db)
        finish(store, running)
        with mock.patch.object(service, '_route_work', side_effect=AssertionError('duplicate run')):
            assert service.run_one()
        assert store.job(message)['status'] == 'succeeded'
    elif choice == 'beside':
        assert service._steer_events(message) == {'beside': {}}
        effects = []
        def route(run):
            effects.append(run.job['id'])
            finish(store, run.job['id'])
            return True
        with mock.patch.object(service, '_route_work', side_effect=route), \
                mock.patch.object(service, '_side_answer', side_effect=AssertionError('side answer')):
            assert service.run_one()
        assert effects == [message]
        assert store.job(running)['status'] == 'running'
        assert not store.work_steer_texts(running)
    else:
        assert service._claim_queued_work() is False
        assert not service._steer_events(message)
        finish(store, running)
        assert service._claim_queued_work()['id'] == message


@pytest.mark.parametrize('target', ['owner', 'scheduled'])
def test_judgment_sees_all_conversation_works_and_steers_only_the_named_one(lane, target):
    service, store, _ = lane
    owner = enqueue(store, 'owner', chat=7)
    background = enqueue(store, 'scheduled', owner=False, chat=7)
    unrelated = enqueue(store, 'elsewhere', chat=8)
    assert service.dispatch_work() == 3
    message = enqueue(store, 'message', chat=7)
    named = owner if target == 'owner' else background
    engine = disposition(service, 'steer:' + named)
    with mock.patch.object(store, 'shown_preview', side_effect=lambda work: 'shown ' + work):
        assert service.consider_steer(message, 'fixture request')
    _, context, candidates, _ = engine.asked[0]
    assert set(candidates) == {'beside', 'wait', 'steer:' + owner, 'steer:' + background}
    assert context.facts['shown_plan:' + owner] == 'shown ' + owner
    assert context.facts['shown_plan:' + background] == 'shown ' + background
    assert 'request:' + unrelated not in context.facts
    assert store.claim_steers(named) == ['fixture request']
    assert store.claim_steers(background if named == owner else owner) == []


@pytest.mark.parametrize('choice', ['beside', 'wait', 'steer'])
def test_inflight_judgment_is_not_claimable_even_after_target_finishes(lane, choice):
    service, store, _ = lane
    running = enqueue(store, 'running', chat=7)
    service._claim_queued_work()
    message = enqueue(store, 'message', chat=7)
    started, release = threading.Event(), threading.Event()
    def choose(context, candidates, question):
        started.set()
        assert release.wait(10)
        return SelectionDecision('decided', 'steer:' + running if choice == 'steer' else choice,
                                 candidates, fixture_confidence())
    service.use_decision_engine(FixtureDecisionEngine(choose=choose))
    with ThreadPoolExecutor(max_workers=1) as pool:
        service.steer_spawn = pool.submit
        assert service.consider_steer(message, 'fixture request')
        assert started.wait(10)
        try:
            # Even after the target finishes, the message belongs to the judgment.
            finish(store, running)
            assert service._claim_queued_work() is False
            assert store.job(message)['status'] == 'queued'
        finally:
            release.set()
    assert not service.steer_messages and not service.steer_judging
    assert service._claim_queued_work()['id'] == message


@pytest.mark.parametrize('bad', ['unknown-work', 'wrong-type', 'missing', 'malformed', 'low-confidence', 'exception'])
def test_wrong_missing_or_malformed_judgment_waits_without_duplicate_run(lane, bad):
    service, store, _ = lane
    running = enqueue(store, 'running', chat=7)
    service._claim_queued_work()
    message = enqueue(store, 'message', chat=7)
    def choose(context, candidates, question):
        if bad == 'exception':raise ValueError('fixture failure')
        if bad == 'missing':return None
        if bad == 'wrong-type':return BinaryDecision('decided', True, fixture_confidence())
        if bad == 'malformed':return {'choice': 'beside'}
        if bad == 'low-confidence':
            return SelectionDecision('decided', 'beside', candidates, fixture_confidence(0.1))
        # A faulty adapter supplying its own candidates cannot route to another Work.
        return SelectionDecision('decided', 'steer:unknown', ('steer:unknown',), fixture_confidence())
    service.use_decision_engine(FixtureDecisionEngine(choose=choose))
    service.steer_spawn = lambda target: target()
    assert service.consider_steer(message, 'fixture request')
    assert not service.steer_messages and not service.steer_judging
    assert not service._steer_events(message)
    assert not store.work_steer_texts(running)
    assert service._claim_queued_work() is False
    finish(store, running)
    claims = race(service._claim_queued_work, service._claim_queued_work)
    assert [c['id'] for c in claims if isinstance(c, dict)] == [message]


def test_wait_disposition_never_waits_for_scheduled_runs(lane):
    service, store, _ = lane
    background = enqueue(store, 'scheduled', owner=False, chat=7)
    service._claim_queued_work()
    message = enqueue(store, 'message', chat=7)
    disposition(service, 'wait')
    assert service.consider_steer(message, 'fixture request')
    assert service._claim_queued_work()['id'] == message
    assert store.job(background)['status'] == 'running'


def test_beside_respects_capacity_and_holds_conversation_for_later_messages(lane):
    service, store, _ = lane
    running = [enqueue(store, str(i), chat=i) for i in range(3)]
    service.dispatch_work()
    message = enqueue(store, 'beside', chat=0)
    disposition(service, 'beside')
    assert service.consider_steer(message, 'fixture request')
    assert service._claim_queued_work() is False
    finish(store, running[2])
    service.release_lane(running[2])
    assert service._claim_queued_work()['id'] == message
    finish(store, running[0])
    service.release_lane(running[0])
    later = enqueue(store, 'later', chat=0)
    assert service._claim_queued_work() is False
    finish(store, message)
    assert service._claim_queued_work()['id'] == later


def test_independent_beside_message_can_pass_an_earlier_waiting_message(lane):
    service, store, _ = lane
    running = enqueue(store, 'running', chat=7)
    service._claim_queued_work()
    waiting = enqueue(store, 'waiting', chat=7)
    disposition(service, 'wait')
    assert service.consider_steer(waiting, 'fixture request')
    beside = enqueue(store, 'beside', chat=7)
    disposition(service, 'beside')
    assert service.consider_steer(beside, 'fixture request')
    assert service._claim_queued_work()['id'] == beside
    assert store.job(waiting)['status'] == 'queued'
    finish(store, running)
    assert service._claim_queued_work() is False
    finish(store, beside)
    assert service._claim_queued_work()['id'] == waiting


@pytest.mark.parametrize('case', [case for case in json.loads(
    (Path(__file__).resolve().parents[1] / 'evals/judgments/cases-v1.json').read_text())['cases']
    if case['role'] == 'steers_running_work'], ids=lambda case: case['id'])
def test_before_after_existing_judgment_scenarios(lane, case):
    """Scripted routing comparison, not a measurement of live semantic quality.

    Before: binary yes steers, no waits with several lanes. After: correction
    still steers; the fixture's independent question starts beside the Work.
    """
    service, store, _ = lane
    running = store.enqueue(case['input']['running_request'], 'running', owner_typed=True)
    service._claim_queued_work()
    message = store.enqueue(case['input']['utterance'], 'message', owner_typed=True)
    steers = case['expect']['outcome'] == 'yes'
    service.use_decision_engine(FixtureDecisionEngine(judge=lambda *args:
        BinaryDecision('decided', steers, fixture_confidence())))
    assert service.judge_steer(running, message, case['input']['utterance']) == steers
    assert service._claim_queued_work() is False
    disposition(service, 'steer:' + running if steers else 'beside')
    assert service.consider_steer(message, case['input']['utterance'])
    if steers:
        assert store.claim_steers(running) == [case['input']['utterance']]
        assert service._claim_queued_work() is False
    else:
        assert service._claim_queued_work()['id'] == message


class FakeClockLock:
    """Lock timeouts advance fake monotonic time; no sleeps."""
    def __init__(self):
        self.now = 0.
        self.held = False
        self.release_at = None
    def acquire(self, blocking=True, timeout=-1):
        if self.held and blocking:
            self.now += timeout
            if self.release_at is not None and self.now >= self.release_at:
                self.held = False
        if self.held:
            return False
        self.held = True
        return True
    def release(self):
        assert self.held
        self.held = False


@pytest.fixture
def profile(tmp_path):
    lock = FakeClockLock()
    profile = BrowserProfile(tmp_path, jar=object(), wait_clock=lambda:lock.now)
    profile._lock = lock
    profile._acquire('a')
    return profile, lock


def test_browser_wait_times_out_with_existing_busy_text(profile):
    browser, clock = profile
    with pytest.raises(ToolError) as error:
        browser._acquire('b')
    assert str(error.value) == BUSY_WORK_TEXT
    assert error.value.code == 'browser_busy'
    assert clock.now == BROWSER_WAIT_SECONDS == 120
    assert browser._holder == 'a'
    browser._release()


def test_browser_wait_acquires_only_after_release(profile):
    browser, clock = profile
    clock.release_at = 10
    browser._acquire('b')
    assert clock.now == 10
    assert browser._holder == 'b'
    browser._release()


def test_browser_stop_interrupts_wait_without_releasing_holder(profile):
    browser, clock = profile
    budget = WorkBudget(clock=lambda:clock.now, stop=lambda:clock.now >= 1)
    with pytest.raises(ToolError) as error:
        browser._acquire('b', check=budget.check)
    assert str(error.value) == WORK_STOPPED
    assert clock.now == 1
    assert clock.held and browser._holder == 'a'
    assert budget.attempts_used == 0


def test_browser_rollback_and_login_still_fail_fast(profile):
    browser, clock = profile
    with pytest.raises(ToolError, match=BUSY_WORK_TEXT):
        browser._acquire('b', wait_seconds=0)
    browser._holder = 'login'
    with pytest.raises(ToolError) as error:
        browser._acquire('b')
    assert str(error.value) == BUSY_LOGIN_TEXT
    assert clock.now == 0


def test_browser_real_lock_never_has_two_holders(tmp_path):
    browser = BrowserProfile(tmp_path, jar=object())
    acquired, release = threading.Event(), threading.Event()
    def first():
        browser._acquire('a')
        acquired.set()
        assert release.wait(10)
        browser._release()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first)
        assert acquired.wait(10)
        try:
            with pytest.raises(ToolError):
                browser._acquire('b', wait_seconds=0)
            assert browser._holder == 'a'
        finally:
            release.set()
        future.result(timeout=10)
    browser._acquire('b')
    browser._release()


def test_observed_same_connector_overlap_records_ids_not_arguments(lane):
    service, store, _ = lane
    first, second = [enqueue(store, str(i), chat=i) for i in range(2)]
    service.dispatch_work()
    detail = {'evidence': {'kind':'owner-mcp', 'identity':'fixture'}, 'arguments':{'private':'not in overlap'}}
    with store.db() as db:
        db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)",
                   (first, 'connector_read', 'succeeded', json.dumps(detail), 1000))
        service.record_connector_overlap(db, second, 'connector_read', detail)
        service.record_connector_overlap(db, second, 'connector_read', detail)
        receipts = db.execute("SELECT detail FROM tool_events WHERE tool='connector_overlap'").fetchall()
    assert len(receipts) == 1
    assert json.loads(receipts[0][0]) == {'other_work_id':first, 'kind':'connection', 'identity':'owner-mcp:fixture'}


def test_approval_settlement_finishes_before_same_conversation_effect():
    case = effects.EffectBoundary('runTest')
    case.setUp()
    try:
        service, store = case.service, case.store
        with store.db() as db:
            db.execute("UPDATE jobs SET owner_typed=1 WHERE id=?", (case.job['id'],))
        trust_record.set_money(store, case.target, True)
        assert case.offer()['behavior'] == 'deny'
        case.partial()
        settler = enqueue(store, 'settler')
        assert service._claim_queued_work()['id'] == settler
        service.hold_lane(settler, LANE_WORK)
        assert case.approve()['resumed']  # actual exact-action settlement queues the original Work
        assert service._claim_queued_work() is False
        finish(store, settler)
        assert service._claim_queued_work() is False  # settlement's tail still owns conv:
        service.release_lane(settler)
        assert service._claim_queued_work()['id'] == case.job['id']
        assert case.call()['behavior'] == 'allow'
        assert case.call()['behavior'] == 'deny'
    finally:
        case.doCleanups()


def test_same_scheduled_slot_is_claimed_once(lane):
    service, store, _ = lane
    work, row = scheduled(service, store, 'slot')
    assert enqueue(store, prep.request_key(row, 1000), owner=False) == work
    results = race(service._claim_queued_work, service._claim_queued_work)
    assert [result['id'] for result in results if isinstance(result, dict)] == [work]


def test_failed_pool_lane_is_terminal_and_capacity_is_reusable(lane):
    service, store, _ = lane
    first = enqueue(store, 'first')
    service.work_lane_spawn = service._spawn_work_lane
    with mock.patch.object(service.work_executor, '_run_claimed', side_effect=RuntimeError('fixture failure')):
        assert service.dispatch_work() == 1
        service.shutdown_work_lanes()
    assert store.job(first)['status'] == 'failed'
    assert not service.lane_work_ids()
    second = enqueue(store, 'second')
    assert service._claim_queued_work()['id'] == second
