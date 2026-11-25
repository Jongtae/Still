"""Continuous line authority at the real scheduler, SQLite and sandbox proxy boundaries."""
from datetime import datetime, timezone
import json
from unittest.mock import patch

import pytest

from personal_agent import agent_runtime as runtime, code_run, condition_checks as checks, continuous_lines, preparations as prep
from personal_agent import entrusted_lines as el
from personal_agent.api_requests import ApiError, ApiRequests
from personal_agent.browser_jar import MemoryKey
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from personal_agent.messages import msg
from personal_agent.quickstart_store import QuickStore
from test_check_plan_1201 import update
from test_condition_check_orders_1194 import ARGS_WITH_SET, POST, Transport, receipts, run_due, run_work, setup_check, switch
from test_code_run import FakeProcess
from test_condition_checks_972 import ARGS, HOST, WIRE, case, current, finish, prepared, record, tick, wake_job  # noqa: F401
from test_preparations import engine


def at(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def owner(c, message, key):
    work_id = c.store.enqueue(message, key, owner_typed=True, owner_words=True)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (work_id,))
    return c.store.job(work_id)


def line(c):
    return c.service.lines.open(c.owner, task='Maintain the agreed target', report_promise='Report each change')['line_id']


def line_call(c, job, line_id, action, **kwargs):
    return c.service.preparations_service.line_handler(job)({'action': action, 'line': line_id, **kwargs})


def entrust(c, line_id, *, seconds=900, job=None, decide=True):
    c.service.use_decision_engine(engine(preparation=decide))
    job = job or owner(c, 'Maintain the agreed target for the next fifteen minutes', 'entrust')
    return line_call(c, job, line_id, 'entrust', planner_goal='Refresh the execution plan',
                     ends_at=at(c.clock[0] + seconds))


def setup(c, *, seconds=900, owner_accepted=False):
    line_id = line(c)
    executor = setup_check(c, owner_accepted=owner_accepted, line=line_id)
    standing = entrust(c, line_id, seconds=seconds)
    return line_id, executor, c.preps.get(standing['planner_id'])


def planner_run(c, planner):
    c.service.run_due_preparation(c.clock[0])
    job = wake_job(c, planner)
    assert job and job['status'] == 'queued'
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
    return c.store.job(job['id'])


def publish(c, job, executor, *, revision=1, expiry=120, path='/orders', method='POST', action='adjust-1', **kwargs):
    plan = {'owner_revision': revision, 'expires': at(c.clock[0] + expiry), 'safe_mode': 'hold',
            'calls': [{'connection': 'fixture', 'method': method, 'host': HOST, 'path': path, 'action_id': action,
                       **kwargs}]}
    return update(c, job['id'], executor, {'limit': 2}, plan=json.dumps(plan))


def approve_correction(c, *, close=False):
    def judge(context, proposition):
        if context.purpose == 'explicit-line-change':
            close_proposition = "Close today's work" in context.facts['proposed_change']
            return BinaryDecision(OUTCOME_DECIDED, close or not close_proposition, fixture_confidence())
        return None
    c.service.use_decision_engine(FixtureDecisionEngine(judge=judge))


def correct(c, line_id):
    approve_correction(c)
    job = owner(c, 'Lower the target for the rest of today', 'correction')
    return line_call(c, job, line_id, 'revise', expected_version='1', rule='Use the lower target')


def before_reservation(monkeypatch, act):
    original = ApiRequests._effect_guard

    def interleave(self, *args, **kwargs):
        if kwargs.get('method') is not None:
            act()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ApiRequests, '_effect_guard', interleave)


@pytest.mark.parametrize(('task', 'expected'), [
    ('Review incoming reports. Summarize open questions.', 'Review incoming reports'),
    ('毎朝新着を確認する。未処理の項目をまとめる。', '毎朝新着を確認する'),
    ('Check the latest reports and record any changes before noon.', 'Check the latest reports and record any…'),
])
def test_legacy_brief_uses_bounded_first_sentence_as_title(case, task, expected):
    line_id = 'legacy-brief'
    case.service.lines.library.commit(el.slot(line_id), json.dumps({'task': task, 'report_promise': 'Report changes'}))
    handler = case.service.preparations_service.line_handler(case.store.job(case.owner))
    read = handler({'action': 'read', 'line': line_id})
    listing = handler({'action': 'list'})
    assert read['title'] == listing['lines'][0]['title'] == expected
    assert read['task'] == task
    assert expected in listing['text']


@pytest.mark.parametrize('action', ['open', 'revise'])
def test_overlong_line_title_is_refused(case, action):
    line_id = line(case)
    job = owner(case, 'Give this line a name', 'name-line')
    with pytest.raises(runtime.ToolError) as exc:
        line_call(case, job, line_id, action, task='Review incoming reports', report_promise='Report changes',
                  expected_version='1', title='x' * 41)
    assert exc.value.code == 'line_invalid_title'
    assert len(case.service.lines.lines()) == 1
    assert case.service.lines.current(line_id)['version'] == 1


def test_entrust_judgment_compares_owner_words_with_title_and_task(case):
    title, task = 'Incoming reports', 'Review new submissions and summarize changes'
    line_id = case.service.lines.open(case.owner, title=title, task=task, report_promise='Report changes')['line_id']
    summaries = []

    def judge(context, proposition):
        if context.purpose == 'explicit-preparation-request':
            summaries.append(context.facts['proposed_preparation'])
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        return None

    case.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
    job = owner(case, f'Entrust {title} for the next fifteen minutes', 'entrust-by-name')
    line_call(case, job, line_id, 'entrust', ends_at=at(case.clock[0] + 900))
    [summary] = summaries
    assert title in summary and task in summary


def test_web_entrust_notice_is_recorded_once_per_mandate_revision(case):
    title = '들어온 보고서'
    zone = 'Asia/Seoul'
    case.service.context_observations.set_controls({'timezone': zone})
    line_id = case.service.lines.open(case.owner, title=title, task='새 보고서 확인 후 변경 요약',
                                      report_promise='바뀐 내용 보고')['line_id']
    job = owner(case, f'{title} 오늘 맡아 줘', 'entrust-on-web')
    end = case.clock[0] + 900
    first = entrust(case, line_id, job=job)
    with pytest.raises(runtime.ToolError) as exc:
        entrust(case, line_id, job=job)
    assert exc.value.code == 'line_already_entrusted'

    def notices():
        with case.store.db() as db:
            return [row['content'] for row in db.execute(
                "SELECT content FROM messages WHERE role='assistant' AND channel='web' ORDER BY id")]

    expected = msg('lines.entrusted', title=title, end=prep.local_text(end, zone))
    assert notices() == [expected]
    assert '\n' not in expected and msg('payment_autonomy.label') in expected and f'{title} 멈춰' in expected

    case.service.preparations_service.stop_line(line_id, end_line=False)
    second = entrust(case, line_id, job=job, seconds=1200)
    assert second['owner_revision'] > first['owner_revision']
    assert notices() == [expected, msg('lines.entrusted', title=title,
                                      end=prep.local_text(case.clock[0] + 1200, zone))]


def test_executor_ticks_and_wakes_coalesce_while_planner_runs(case):
    c = case
    _, executor, planner = setup(c)
    first = planner_run(c, planner)
    for n in range(3):
        tick(c, {'stdout': f'condition {n}'})
        c.clock[0] += 15
        c.service.run_due_preparation(c.clock[0])
        assert current(c, executor)['state'] == prep.STATE_SCHEDULED
        assert current(c, executor)['last_run_job_id'] is None
        assert wake_job(c, planner)['id'] == first['id']
    finish(c, planner)
    second = planner_run(c, planner)
    assert second['id'] != first['id']
    assert 'condition 2' in c.service.preparation_history(second)[-1]['content']
    assert c.store.outside_content_sources(second['id'])
    finish(c, planner)
    c.service.run_due_preparation(c.clock[0])
    assert wake_job(c, planner)['id'] == second['id']


def test_planner_wake_cap_does_not_stop_executor(case):
    c = case
    line_id, executor, planner = setup(c)
    day = c.preps.budget_day(c.clock[0])
    with c.store.db() as db:
        db.execute('UPDATE line_mandates SET wake_day=?,wakes=? WHERE line_id=?',
                   (day, checks.DAILY_WAKES - 1, line_id))
    planner_run(c, planner)
    finish(c, planner)
    previous = wake_job(c, planner)['id']
    tick(c, {'stdout': 'a new trigger'})
    c.clock[0] += 15
    c.service.run_due_preparation(c.clock[0])
    assert wake_job(c, planner)['id'] == previous
    tick(c, {'stdout': 'the check still polls'})
    assert current(c, executor)['check_next_at'] == c.clock[0] + 15


def test_owner_correction_fences_open_check_and_old_planner_at_reservation(case, monkeypatch):
    c = case
    line_id, executor, planner = setup(c)
    work = planner_run(c, planner)
    publish(c, work, executor)
    switch(c, True)
    before_reservation(monkeypatch, lambda: correct(c, line_id))
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and not receipts(c, executor)
    with pytest.raises(runtime.ToolError, match='instructions have changed'):
        publish(c, work, executor, revision=2)
    finish(c, planner)
    next_work = planner_run(c, planner)
    assert publish(c, next_work, executor, revision=2)['owner_revision'] == 2


def test_expiry_between_preflight_and_reservation_holds_and_wakes(case, monkeypatch):
    c = case
    _, executor, planner = setup(c)
    work = planner_run(c, planner)
    publish(c, work, executor, expiry=10)
    switch(c, True)
    before_reservation(monkeypatch, lambda: c.clock.__setitem__(0, c.clock[0] + 10))
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and not receipts(c, executor)
    finish(c, planner)
    c.clock[0] = current(c, executor)['check_next_at']
    tick(c)
    assert 'plan_expired' in c.service.preparation_history(planner_run(c, planner))[-1]['content']


@pytest.mark.parametrize('path,method', [('/another', 'POST'), ('/orders/item', 'PATCH'), ('/orders/%2e%2e', 'POST')])
def test_call_outside_plan_is_refused(case, path, method):
    c = case
    _, executor, planner = setup(c)
    publish(c, planner_run(c, planner), executor, path='/orders/*')
    switch(c, True)
    sent = []
    request = dict(POST, path=path, method=method)
    assert not run_due(c, sent, request=request)['allow']
    assert sent == [] and not receipts(c, executor)


def test_close_racing_reservation_cancels_linked_window_and_planner(case, monkeypatch):
    c = case
    line_id, executor, planner = setup(c)
    work = planner_run(c, planner)
    publish(c, work, executor)
    switch(c, True)
    closer = owner(c, 'Close today now', 'close')
    approve_correction(c, close=True)
    # The model tried to record the close in open matters, the failure reported in the design.
    before_reservation(monkeypatch, lambda: line_call(c, closer, line_id, 'revise', expected_version='1',
                                                      open_matters='Today has ended'))
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and not receipts(c, executor)
    assert current(c, executor)['state'] == current(c, planner)['state'] == prep.STATE_CANCELLED
    assert c.service.work_stopped(work['id'])


def test_unknown_effect_is_held_across_restart_and_new_plan(case):
    c = case
    _, executor, planner = setup(c)
    work = planner_run(c, planner)
    publish(c, work, executor)
    switch(c, True)
    wire = []

    class AcceptedThenDisconnected(Transport):
        def write(self, text):
            super().write(text)
            if json.loads(text).get('allow'):
                wire.append(self.request)

    c.processes.append(AcceptedThenDisconnected([], failure='proxy'))
    assert c.checks.run_due()
    assert wire == [POST] and receipts(c, executor)[0]['status'] == 'dispatching'

    reopened = QuickStore(c.store.root)
    reopened.recover(can_resume_effect=c.service.can_resume_effect)
    c.checks = c.service.condition_checks = checks.ConditionChecks(c.service, runner=c.checks.runner)
    assert reopened.job(work['id'])['status'] == 'interrupted'
    assert current(c, executor)['state'] == prep.STATE_SCHEDULED
    c.service.run_due_preparation(c.clock[0])
    replanner = planner_run(c, planner)
    assert replanner['id'] != work['id']
    assert 'unknown_effect' in c.service.preparation_history(replanner)[-1]['content']
    publish(c, replanner, executor)
    c.clock[0] += 15
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and len(receipts(c, executor)) == 1


def test_logical_action_is_held_under_new_revision_with_a_different_body(case):
    c = case
    _, executor, planner = setup(c)
    work = planner_run(c, planner)
    first = publish(c, work, executor)
    switch(c, True)
    sent = []
    assert run_due(c, sent)['allow']
    second = publish(c, work, executor)
    assert first['plan_id'] != second['plan_id']
    c.clock[0] += 15
    assert not run_due(c, sent, request=dict(POST, bodyText='{"adjustment":8}'))['allow']
    assert len(sent) == len(receipts(c, executor)) == 1


@pytest.mark.parametrize('first', ['owner', 'executor'])
def test_owner_work_and_executor_share_replay_scope(case, first):
    c = case
    line_id, executor, planner = setup(c)
    publish(c, planner_run(c, planner), executor)
    switch(c, True)
    job = owner(c, 'Continue the same line now', 'owner-effect')
    line_call(c, job, line_id, 'read')
    sent = []
    operations = {'owner': lambda: run_work(c, job, sent), 'executor': lambda: run_due(c, sent)}
    assert operations[first]()['allow']
    second = 'executor' if first == 'owner' else 'owner'
    assert not operations[second]()['allow']
    assert sent == [POST]


@pytest.mark.parametrize('caller', ['scheduled', 'outside'])
def test_only_explicit_owner_request_grants_line_standing(case, caller):
    c = case
    line_id = line(c)
    executor = setup_check(c, owner_accepted=False, line=line_id)
    if caller == 'scheduled':
        tick(c, {'stdout': 'condition met'})
        job = wake_job(c, executor)
    else:
        job = owner(c, 'Read the latest report', 'outside')
        with c.store.db() as db:
            c.store._insert_intake_source(db, job['id'], '{"kind":"code_run","host":"api.example.com"}', c.clock[0])
    with pytest.raises(runtime.ToolError) as exc:
        entrust(c, line_id, job=job, decide=caller != 'outside')
    assert exc.value.code == ('unattended_run' if caller == 'scheduled' else 'line_change_needs_owner')
    with c.store.db() as db:
        assert not db.execute('SELECT 1 FROM line_mandates WHERE line_id=?', (line_id,)).fetchone()


@pytest.mark.parametrize('autonomy', [False, True])
def test_reducing_effect_obeys_current_payment_autonomy(case, autonomy):
    c = case
    _, executor, planner = setup(c)
    publish(c, planner_run(c, planner), executor, method='DELETE', path='/resources/*', action='reduce-1')
    switch(c, True)
    if not autonomy:
        switch(c, False)
    sent = []
    request = dict(POST, method='DELETE', path='/resources/item', bodyText='')
    assert run_due(c, sent, request=request)['allow'] is autonomy
    assert sent == ([request] if autonomy else [])


def test_owner_work_row_payment_inherits_active_line_standing(case):
    c = case
    line_id = line(c)
    setup_check(c, owner_accepted=False, line=line_id)
    switch(c, True)
    sent = []
    for entrusted in (False, True):
        if entrusted:
            creator = owner(c, 'Maintain the agreed target for the next fifteen minutes', 'entrust')
            entrust(c, line_id, job=creator)
        else:
            creator = c.store.job(c.owner)
        c.service.use_decision_engine(engine(preparation=False))
        result = c.service.preparation_scheduler(creator, '')({
            'kind': 'prepare', 'goal': 'Apply the planned adjustment', 'offer': 'Adjust the target',
            'due': at(c.clock[0]), 'line': line_id,
        })
        row = c.preps.get(result['preparation_id'])
        assert row['accepted_by'] == prep.ACCEPTED_OWNER_WORK
        assert c.preps.start(row, channel='web', chat_id=None, now=c.clock[0])
        assert run_work(c, wake_job(c, row), sent)['allow'] is entrusted
        finish(c, row)
    assert sent == [POST]


@pytest.mark.parametrize('stop', ['explicit', 'deadline'])
def test_line_stop_cancels_executor_queued_planner_and_pending_trigger(case, stop):
    c = case
    line_id, executor, planner = setup(c, seconds=90)
    c.service.run_due_preparation(c.clock[0])
    queued = wake_job(c, planner)
    assert queued['status'] == 'queued'
    tick(c, {'stdout': 'buffer this event'})
    if stop == 'explicit':
        c.service.preparations_service.stop_line(line_id)
    else:
        c.clock[0] += 90
        c.service.run_due_preparation(c.clock[0])
    assert c.store.job(queued['id'])['status'] == 'cancelled'
    assert current(c, planner)['state'] == current(c, executor)['state'] == prep.STATE_CANCELLED
    assert not c.checks.run_due() and not c.checks.run_due_planner(c.clock[0])
    with c.store.db() as db:
        assert db.execute('SELECT pending FROM line_mandates WHERE line_id=?', (line_id,)).fetchone()[0] is None


def test_missing_plan_holds_even_with_owner_accepted_row(case):
    c = case
    _, executor, _ = setup(c, owner_accepted=True)
    switch(c, True)
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and not receipts(c, executor)


def test_plan_can_require_a_documented_idempotency_header(case):
    c = case
    _, executor, planner = setup(c)
    publish(c, planner_run(c, planner), executor, idempotency_header='Idempotency-Key')
    switch(c, True)
    sent = []
    assert not run_due(c, sent, request=dict(POST, headers=[['idempotency-key', 'different']]))['allow']
    c.clock[0] += 15
    assert run_due(c, sent, request=dict(POST, headers=[['idempotency-key', 'adjust-1']]))['allow']
    assert len(sent) == len(receipts(c, executor)) == 1


def test_owner_steer_fences_before_the_planner_receives_it(case):
    c = case
    _, executor, planner = setup(c)
    work = planner_run(c, planner)
    publish(c, work, executor)
    switch(c, True)
    correction = c.store.enqueue('Use the narrower target now', 'steer', owner_typed=True, owner_words=True)
    with patch('personal_agent.quickstart_store.time.time', return_value=c.clock[0]):
        assert c.store.add_steer(work['id'], correction, 'Use the narrower target now')
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and not receipts(c, executor)
    finish(c, planner)
    next_work = planner_run(c, planner)
    assert 'Use the narrower target now' in c.service.preparation_history(next_work)[-1]['content']
    assert publish(c, next_work, executor, revision=2)['owner_revision'] == 2


def test_coalesced_wake_consumes_one_executor_window_run(case):
    c = case
    _, executor, planner = setup(c)
    with c.store.db() as db:
        db.execute('UPDATE preparations SET max_runs=1 WHERE id=?', (executor['id'],))
    planner_run(c, planner)
    tick(c, {'stdout': 'first observation'})
    c.clock[0] += 15
    tick(c, {'stdout': 'second observation'})
    assert current(c, executor)['run_count'] == 0

    finish(c, planner)
    planner_run(c, planner)
    assert current(c, executor)['run_count'] == 1
    c.clock[0] += 15
    assert c.checks.run_due()
    assert current(c, executor)['state'] == prep.STATE_EXPIRED



@pytest.mark.parametrize('close', ['stop', 'deadline'])
def test_a_check_scheduled_after_mandate_close_wakes_ordinary_work(case, close):
    c = case
    line_id, old_executor, planner = setup(c, seconds=90)
    if close == 'stop':
        c.service.preparations_service.stop_line(line_id, end_line=False)
    else:
        c.clock[0] += 90
        c.service.run_due_preparation(c.clock[0])
    assert current(c, old_executor)['state'] == current(c, planner)['state'] == prep.STATE_CANCELLED
    with c.store.db() as db:
        assert db.execute('SELECT closed FROM line_mandates WHERE line_id=?', (line_id,)).fetchone()[0]

    new_check = prepared(c, line=line_id, goal='Report a new observation')
    tick(c, {'stdout': 'new condition met'})
    work = wake_job(c, new_check)
    assert work and work['status'] == 'queued'
    assert 'new condition met' in c.service.preparation_history(work)[-1]['content']
    assert not wake_job(c, planner)


def capture_check_runs(c, monkeypatch):
    runs = []
    runner = c.checks.runner

    def capture(*args, **kwargs):
        runs.append(kwargs)
        return runner(*args, **kwargs)

    monkeypatch.setattr(c.checks, 'runner', capture)
    return runs


def test_owner_accepted_check_after_mandate_close_has_ordinary_authority(case, monkeypatch):
    c = case
    line_id = line(c)
    entrust(c, line_id)
    c.service.preparations_service.stop_line(line_id, end_line=False)
    executor = setup_check(c, owner_accepted=True, money=False, line=line_id)
    switch(c, True)
    runs = capture_check_runs(c, monkeypatch)
    sent = []

    assert run_due(c, sent)['allow']
    assert sent == [POST]
    run, = runs
    assert isinstance(run['approvals'], checks.CheckApprovals)
    assert 'plan_text' not in run
    work = wake_job(c, executor)
    assert update(c, work['id'], executor, {'limit': 2})['keys'] == ['limit']
    with c.store.db() as db:
        assert continuous_lines.member_revision(db, work['id'], line_id) is None
    finish(c, executor)
    assert c.preps.start(current(c, executor), channel='web', chat_id=None, now=c.clock[0], immediate=True)


@pytest.mark.parametrize('transition', ['entrusted', 'lapsed', 'closed'])
def test_check_effect_authority_is_bound_to_the_mode_it_started_in(case, monkeypatch, transition):
    c = case
    if transition == 'closed':
        line_id, executor, planner = setup(c, owner_accepted=True)
        published = publish(c, planner_run(c, planner), executor)
    else:
        line_id = line(c)
        executor = setup_check(c, owner_accepted=True, line=line_id)
    switch(c, True)
    runs = capture_check_runs(c, monkeypatch)
    tick(c)
    run, = runs
    approvals = run['approvals']
    with c.store.db() as db:
        evidence = approvals.api_effect_evidence(db, 'mutate')
    if transition == 'closed':
        assert evidence['plan_id'] == published['plan_id']
        # Close only the mandate to isolate the authority check from row cancellation.
        with c.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            continuous_lines.fence(db, line_id, c.clock[0], close=True)
    else:
        assert 'plan_id' not in evidence
        entrust(c, line_id, seconds=90)
        if transition == 'lapsed':
            c.clock[0] += 90
            with c.store.db() as db:
                assert continuous_lines.lapsed(db, line_id, c.clock[0])

    with c.store.db() as db, pytest.raises(ApiError) as exc:
        db.execute('BEGIN IMMEDIATE')
        approvals.api_effect_evidence(db, 'mutate')
    assert exc.value.ran is False
    assert exc.value.code == ('line_plan_stale' if transition == 'closed' else 'api_work_inactive')


@pytest.mark.parametrize('operation', ['resume', 'restore', 'resume_self_pause', 'restore_self_cancel'])
def test_executor_resumption_wakes_planner_without_waiting_for_the_next_check(case, operation):
    c = case
    line_id = line(c)
    executor = setup_check(c, line=line_id)
    if '_self_' in operation:
        operation, change = operation.split('_self_')
        tick(c, {'stdout': 'Condition met'})
        work = wake_job(c, executor)
        c.preps.change_own(executor['id'], work['id'], change)
        finish(c, executor)
        planner = c.preps.get(entrust(c, line_id)['planner_id'])
        planner_run(c, planner)
        finish(c, planner)
    else:
        planner = c.preps.get(entrust(c, line_id)['planner_id'])
        publish(c, planner_run(c, planner), executor)
        finish(c, planner)
        if operation == 'resume':
            c.preps.pause(executor['id'])
            with c.store.db() as db:
                standing = continuous_lines.active(db, line_id, c.clock[0])
                assert standing['active_plan'] is None and standing['pending'] is None
        else:
            c.preps.cancel(executor['id'])
    c.clock[0] += 1
    getattr(c.preps, operation)(executor['id'])

    with c.store.db() as db:
        standing = continuous_lines.active(db, line_id, c.clock[0])
        assert standing['active_plan'] is None
        event = json.loads(standing['pending'])['events']['executor_resumed']
        assert event['executor_id'] == executor['id']
    work = planner_run(c, planner)
    assert 'executor_resumed' in c.service.preparation_history(work)[-1]['content']
    assert publish(c, work, current(c, executor))['plan_id']


@pytest.mark.parametrize('change', ['revision', 'digest'])
def test_executor_run_clears_an_outdated_plan_and_wakes_the_planner_once(case, change):
    c = case
    line_id, executor, planner = setup(c)
    publish(c, planner_run(c, planner), executor)
    finish(c, planner)
    switch(c, True)
    c.clock[0] += 1
    spec = json.loads(executor['check_spec'])
    with c.store.db() as db:
        if change == 'revision':
            db.execute('UPDATE preparations SET updated_at=? WHERE id=?', (c.clock[0], executor['id']))
        else:
            spec['arguments']['code'] += '\n'
            spec['digest'] = checks._spec_digest(spec)
            db.execute('UPDATE preparations SET check_spec=? WHERE id=?', (json.dumps(spec), executor['id']))
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == []
    with c.store.db() as db:
        standing = continuous_lines.active(db, line_id, c.clock[0])
        assert standing['active_plan'] is None
        event = json.loads(standing['pending'])['events']['plan_stale']
        assert event == {'kind': 'plan_stale', 'at': c.clock[0], 'check': spec['digest'], 'output': '',
                         'executor_id': executor['id']}
    work = planner_run(c, planner)
    assert 'plan_stale' in c.service.preparation_history(work)[-1]['content']
    finish(c, planner)
    c.clock[0] = current(c, executor)['check_next_at']
    tick(c)
    with c.store.db() as db:
        assert continuous_lines.active(db, line_id, c.clock[0])['pending'] is None


def renew_executor(c, work, line_id, *, seconds=120, proven=True, args=ARGS, **overrides):
    if proven:
        runner = code_run.CodeRunner(c.store, work['id'], popen=lambda *a, **k: FakeProcess(), **WIRE)
        result = runner.run(args)
        assert result['check_proof']
        record(c, 'code_run', result, work=work['id'])
    return c.service.preparation_scheduler(work, '')({
        'kind': 'prepare', 'goal': 'Continue observing the condition', 'offer': 'Continue observing',
        'line': line_id, 'due': at(c.clock[0]), 'until': at(c.clock[0] + seconds),
        'check': json.dumps(dict(args, every_seconds=15)), **overrides,
    })


@pytest.mark.parametrize('expiry', ['max_runs', 'until'])
def test_current_line_planner_can_renew_an_expired_executor(case, expiry):
    c = case
    line_id = line(c)
    executor = prepared(c, line=line_id, until=30, max_runs=1)
    standing = entrust(c, line_id, seconds=120)
    planner = c.preps.get(standing['planner_id'])
    planner_run(c, planner)
    finish(c, planner)
    if expiry == 'max_runs':
        tick(c, {'stdout': 'one hit'})
        planner_run(c, planner)
        finish(c, planner)
        assert current(c, executor)['run_count'] == 1
    c.clock[0] += 15 if expiry == 'max_runs' else 30
    assert c.checks.run_due()
    assert current(c, executor)['state'] == prep.STATE_EXPIRED
    work = planner_run(c, planner)
    ends_at = datetime.fromisoformat(standing['ends_at']).timestamp()
    brief = c.service.preparation_history(work)[-1]['content']
    assert 'executor_expired' in brief
    assert prep.local_text(ends_at, planner['timezone']) in brief

    result = renew_executor(c, work, line_id, seconds=ends_at - c.clock[0])
    row = c.preps.get(result['preparation_id'])
    assert row['id'] != executor['id']
    assert row['line_id'] == line_id and row['state'] == prep.STATE_SCHEDULED
    assert row['created_from'] == work['id'] and row['accepted_by'] == prep.ACCEPTED_OWNER_WORK
    assert row['until_at'] == ends_at
    assert result['check']['digest'] == json.loads(executor['check_spec'])['digest']
    c.store.api_slot_key = MemoryKey()
    plan = {'owner_revision': standing['owner_revision'], 'expires': at(c.clock[0] + 30),
            'safe_mode': 'hold', 'calls': []}
    assert update(c, work['id'], row, {}, plan=json.dumps(plan))['owner_revision'] == standing['owner_revision']
    with pytest.raises(runtime.ToolError) as exc:
        renew_executor(c, work, line_id, seconds=60, proven=False)
    assert exc.value.code == 'line_executor_exists'


def fail_executor(c, executor):
    for _ in range(checks.FAILURE_LIMIT):
        c.clock[0] = current(c, executor)['check_next_at']
        tick(c, {'exit': 1})
    row = current(c, executor)
    assert row['state'] == prep.STATE_PAUSED
    assert json.loads(row['check_status'])['paused'] == 'failures'


@pytest.mark.parametrize('legacy_pause', [False, True], ids=['current-pause', 'legacy-pause'])
def test_planner_replaces_failure_paused_executor_and_resumes_execution(case, legacy_pause):
    c = case
    line_id, executor, planner = setup(c, seconds=180)
    publish(c, planner_run(c, planner), executor)
    finish(c, planner)
    fail_executor(c, executor)
    if legacy_pause:
        with c.store.db() as db:
            db.execute("UPDATE preparations SET check_status=json_remove(check_status,'$.paused_at') WHERE id=?",
                       (executor['id'],))
    work = planner_run(c, planner)
    brief = c.service.preparation_history(work)[-1]['content']
    assert 'failure' in brief
    standing = c.service.preparations_service.line_read(line_id)['standing']
    ends_at = datetime.fromisoformat(standing['ends_at']).timestamp()

    result = renew_executor(c, work, line_id, seconds=ends_at - c.clock[0], args=ARGS_WITH_SET,
                            replaces=executor['id'])

    assert f"preparation_id={executor['id']}" in brief
    assert result['replaced'] == {'preparation_id': executor['id'], 'state': prep.STATE_CANCELLED}
    assert current(c, executor)['state'] == prep.STATE_CANCELLED
    row = c.preps.get(result['preparation_id'])
    assert row['line_id'] == line_id and row['state'] == prep.STATE_SCHEDULED
    assert row['accepted_by'] == prep.ACCEPTED_OWNER_WORK and row['until_at'] == ends_at
    assert result['check']['digest'] == json.loads(executor['check_spec'])['digest']
    switch(c, True)
    sent = []
    assert not run_due(c, sent)['allow']
    publish(c, work, row, revision=standing['owner_revision'], expiry=30)
    c.clock[0] = current(c, row)['check_next_at']
    assert run_due(c, sent)['allow']
    assert sent == [POST] and len(receipts(c, row)) == 1


@pytest.mark.parametrize(('boundary', 'code'), [
    ('owner_paused', 'unattended_run'),
    ('owner_paused_after_failures', 'unattended_run'),
    ('other_line', 'unattended_run'),
    ('closed', 'unattended_run'),
    ('non_planner', 'line_piece_invalid'),
    ('no_proof', 'check_not_proven'),
    ('past_end', 'line_invalid_end'),
])
def test_planner_failure_replacement_keeps_renewal_boundaries(case, boundary, code):
    c = case
    line_id, executor, planner = setup(c, seconds=120)
    planner_run(c, planner)
    finish(c, planner)
    if boundary == 'other_line':
        other_line = line(c)
        standing = entrust(c, other_line, job=owner(c, 'Entrust the other line', 'another-entrust'))
        work = planner_run(c, c.preps.get(standing['planner_id']))
    elif boundary == 'non_planner':
        result = c.service.preparation_scheduler(c.store.job(c.owner), '')({
            'kind': 'prepare', 'goal': 'Prepare a summary', 'offer': 'Summary',
            'line': line_id, 'due': at(c.clock[0]),
        })
        other_row = c.preps.get(result['preparation_id'])
        assert c.preps.start(other_row, channel='web', chat_id=None, now=c.clock[0])
        work = wake_job(c, other_row)
        with c.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work['id'],))
        work = c.store.job(work['id'])

    if boundary != 'owner_paused':
        fail_executor(c, executor)
    if boundary in ('owner_paused', 'owner_paused_after_failures'):
        c.preps.pause(executor['id'])
        correct(c, line_id)
    if boundary not in ('other_line', 'non_planner'):
        work = planner_run(c, planner)
    if boundary == 'closed':
        c.service.preparations_service.stop_line(line_id, end_line=False)

    before = {row['id']: row['state'] for row in c.preps.rows()}
    with pytest.raises(runtime.ToolError) as exc:
        renew_executor(c, work, other_line if boundary == 'other_line' else line_id,
                       seconds=120 if boundary == 'past_end' else 60, args=ARGS_WITH_SET,
                       proven=boundary not in ('closed', 'no_proof'), replaces=executor['id'])
    assert exc.value.code == code
    assert {row['id']: row['state'] for row in c.preps.rows()} == before


def test_planner_slot_cannot_create_a_second_executor_after_its_first_ends(case):
    c = case
    line_id = line(c)
    standing = entrust(c, line_id)
    work = planner_run(c, c.preps.get(standing['planner_id']))
    result = renew_executor(c, work, line_id, seconds=30)
    c.clock[0] += 30
    assert c.checks.run_due()
    assert c.preps.get(result['preparation_id'])['state'] == prep.STATE_EXPIRED

    with pytest.raises(runtime.ToolError) as exc:
        renew_executor(c, work, line_id, proven=False)
    assert exc.value.code == 'line_executor_exists'


@pytest.mark.parametrize(('boundary', 'code'), [
    ('closed', 'unattended_run'),
    ('expired', 'unattended_run'),
    ('stale', 'unattended_run'),
    ('occupied', 'line_executor_exists'),
    ('past_end', 'line_invalid_end'),
    ('no_proof', 'check_not_proven'),
    ('other_line', 'unattended_run'),
    ('replace', 'unattended_run'),
    ('plain_prepare', 'unattended_run'),
    ('non_planner', 'line_piece_invalid'),
])
def test_executor_renewal_respects_planner_authority(case, boundary, code):
    c = case
    line_id = line(c)
    executor = prepared(c, line=line_id) if boundary == 'occupied' else None
    other_work = None
    if boundary == 'non_planner':
        result = c.service.preparation_scheduler(c.store.job(c.owner), '')({
            'kind': 'prepare', 'goal': 'Prepare a summary', 'offer': 'Summary',
            'line': line_id, 'due': at(c.clock[0]),
        })
        other_work = c.preps.get(result['preparation_id'])
    standing = entrust(c, line_id, seconds=90)
    planner = c.preps.get(standing['planner_id'])
    if other_work:
        assert c.preps.start(other_work, channel='web', chat_id=None, now=c.clock[0])
        work = wake_job(c, other_work)
        with c.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work['id'],))
    else:
        work = planner_run(c, planner)
    if boundary == 'closed':
        c.service.preparations_service.stop_line(line_id, end_line=False)
    elif boundary == 'expired':
        c.clock[0] += 90
    elif boundary == 'stale':
        correct(c, line_id)

    kwargs = {}
    if boundary == 'other_line':
        kwargs['line'] = line(c)
    elif boundary == 'replace':
        kwargs['replaces'] = planner['id']
    elif boundary == 'plain_prepare':
        kwargs['check'] = None
    before = {row['id'] for row in c.preps.rows()}
    with pytest.raises(runtime.ToolError) as exc:
        renew_executor(c, work, line_id, seconds=120 if boundary == 'past_end' else 60,
                       proven=boundary not in ('no_proof', 'closed'), **kwargs)
    assert exc.value.code == code
    assert {row['id'] for row in c.preps.rows()} == before
    if executor:
        assert current(c, executor)['state'] == prep.STATE_SCHEDULED
