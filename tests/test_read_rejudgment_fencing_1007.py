"""Model-free races at the read judgment and terminal publication boundaries (#1007)."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import json
import sqlite3
from threading import Event
from unittest import mock

import pytest

from personal_agent.agent_runtime import Capabilities, WORK_STOP_KEY, LedgerSuperseded, WorkBudget, WorkLedger, run_agent
from personal_agent.conversation_handoff import Judgment
from personal_agent.quickstart_service import AgentService, WORK_SOURCES_KEY
from personal_agent.quickstart_store import QuickStore
from personal_agent.orchestrator import Catalogue
from personal_agent.work_execution import WorkRun
from personal_agent import work_download as reads
from test_read_checkpoint_recovery_1008 import CHECKPOINT


@dataclass
class Case:
    service: AgentService
    work: dict
    budget: WorkBudget
    now: list

    @property
    def store(self): return self.service.store

    @property
    def id(self): return self.work['id']

    def snapshot(self): return reads.checkpoint_snapshot(self.store, self.id)

    def judge(self, call, *, snapshot=None, facts=None, budget=None, purpose='plan'):
        return self.service.read_checkpoint_judgment(
            self.id, purpose, {'answer': 'observed'} if facts is None else facts,
            reads.checkpoint_note(self.store.read_checkpoint(self.id)), call,
            snapshot=self.snapshot() if snapshot is None else snapshot, budget=budget or self.budget)

    def restart(self):
        self.store.recover(can_resume_checkpoint=self.service.can_resume_read_checkpoint)
        work = self.service._claim_queued_work()
        assert isinstance(work, dict)
        budget = WorkBudget(ledger=WorkLedger(self.store, self.id, existing_only=True, wall=lambda: self.now[0]))
        return work, budget


@pytest.fixture
def case(tmp_path, monkeypatch):
    now = [1800000000.0]
    monkeypatch.setattr('time.time', lambda: now[0])
    # This suite exercises admission/fencing, not the separately tested staged byte reader.
    monkeypatch.setattr(reads, 'staged_checkpoint_available', lambda *_: True)
    store = QuickStore(tmp_path)
    service = AgentService(store)
    work_id = store.enqueue('Compare the supplied records.', 'fencing-1007', owner_typed=True)
    work = service._claim_queued_work()
    assert work['id'] == work_id
    budget = WorkBudget(ledger=WorkLedger(store, work_id, fresh=True, wall=lambda: now[0]))
    assert store.record_read_checkpoint(work_id, CHECKPOINT, epoch=budget.ledger.epoch)
    return Case(service, work, budget, now)


def change(case, kind):
    if kind == 'steer':
        source = case.store.enqueue('Use the corrected scope.', 'correction-1007', owner_typed=True)
        with case.store.db() as db:
            db.execute('INSERT INTO work_steers(job_id,source_job_id,content,created) VALUES (?,?,?,?)',
                       (case.id, source, 'Use the corrected scope.', case.now[0]))
    elif kind == 'checkpoint':
        assert case.store.record_read_checkpoint(case.id, {**CHECKPOINT, 'received': 24, 'revision': 2},
                                                 epoch=case.budget.ledger.epoch)
    elif kind == 'mode': case.store.put('work_mode', {'enabled': False, 'revision': 2})
    elif kind == 'grant':
        folder = case.store.root / 'granted-workspace'
        folder.mkdir()
        case.store.put('file_workspace', {'references': [], 'workspace': str(folder), 'workspace_id': 'new-grant'})
    elif kind == 'trust': case.store.put('trust_record', {'hands': {'fixture': {'state': 'forgotten', 'epoch': 'revoked'}}})


@pytest.mark.parametrize('kind', ['steer', 'checkpoint', 'mode', 'grant', 'trust'])
@pytest.mark.parametrize('when', ['before-registration', 'before-commit'])
def test_built_state_must_match_at_both_transactions(case, kind, when):
    snapshot = case.snapshot()
    call = mock.Mock(return_value={'winner': 'stale'})
    if when == 'before-registration': change(case, kind)
    else:
        def response():
            change(case, kind)
            return {'winner': 'stale'}
        call.side_effect = response
    assert case.judge(call, snapshot=snapshot) is None
    assert call.call_count == (when == 'before-commit')
    with case.store.db() as db:
        rows = db.execute('SELECT result FROM work_read_judgments').fetchall()
    assert not rows


def orchestration(case, work=None):
    worker = {'id': 'fixture', 'available': True, 'default_model': '', 'models': [],
              'name': 'Fixture', 'kind': 'api', 'tools': [], 'default': True,
              'cost': '', 'latency': '', 'native_search': False, 'browser': False}
    catalogue = Catalogue([worker], {}, 'fixture')
    with mock.patch('personal_agent.quickstart_service.worker_catalogue', return_value=catalogue):
        return case.service.work_orchestration(work or case.work, case.work['message'], [], {}, case.budget)


def test_plan_binds_state_before_building_owner_corrections(case):
    # The builder, not _ask's later registration, owns the snapshot.
    case.store.recover(can_resume_checkpoint=case.service.can_resume_read_checkpoint)
    work = case.service._claim_queued_work()
    case.budget = WorkBudget(ledger=WorkLedger(case.store, case.id, existing_only=True, wall=lambda: case.now[0]))
    orch = orchestration(case, work)
    change(case, 'steer')
    with mock.patch.object(orch, '_ask_plan', return_value=({'route': 'stale'}, '')) as ask:
        data, _failure = orch._ask(orch.catalogue.workers)
    assert data is None
    ask.assert_not_called()


@pytest.mark.parametrize('race', ['steer', 'checkpoint', 'second_change', 'stale_epoch'])
def test_plan_rejudges_changed_input_at_most_once(case, race):
    work, case.budget = case.restart()
    orch = orchestration(case, work)
    contexts = []

    def ask(_candidates, context):
        contexts.append(context.facts)
        if len(contexts) == 1:
            if race == 'stale_epoch':
                case.restart()
                change(case, 'mode')  # a mismatch still must not let the stale epoch retry
            else:
                change(case, 'checkpoint' if race == 'checkpoint' else 'steer')
        elif race == 'second_change':
            change(case, 'checkpoint')
        return {'route': len(contexts)}, ''

    with mock.patch.object(orch, '_ask_plan', side_effect=ask), \
            mock.patch.object(orch.judgments, 'checkpoint_snapshot', wraps=orch.judgments.checkpoint_snapshot) as snapshot:
        data, failure = orch._ask(orch.catalogue.workers)
    assert snapshot.call_count == (0 if race == 'stale_epoch' else 1)
    assert len(contexts) == (1 if race == 'stale_epoch' else 2)
    assert 'owner_corrections' not in contexts[0]
    if race == 'steer':
        assert 'Use the corrected scope.' in contexts[1]['owner_corrections']
    if race == 'checkpoint':
        assert '24 of 32 bytes verified' in contexts[1]['continues']
    if race in ('stale_epoch', 'second_change'):
        assert data is None and failure
    else:
        assert (data, failure) == ({'route': 2}, '')


def test_goal_reached_does_not_use_corrections_added_after_input_build(case):
    snapshot = case.snapshot()
    change(case, 'steer')
    with case.service.work_decision_scope(case.id, case.budget):
        with mock.patch.object(case.service.decision_judge, '_evaluate_goal') as ask:
            judged = case.service.decision_judge.goal_reached(
                case.work['message'], 'verified', work_id=case.id,
                continues=reads.checkpoint_note(CHECKPOINT), read_snapshot=snapshot)
    assert judged.outcome == 'unavailable'
    ask.assert_not_called()


def run_read_goal(case):
    service = case.service
    adapter = mock.Mock()
    adapter.tool_turn.return_value = ({'content': 'Here is the comparison.'}, 'fixture')
    capabilities = Capabilities(case.store, adapter, {}, None, case.id, lambda *_: None,
                                budget=case.budget, judgments=service.decision_judge)
    with service.work_decision_scope(case.id, case.budget):
        return run_agent(
            adapter, {'provider': 'fixture'}, None, [{'role': 'user', 'content': case.work['message']}],
            '', capabilities, lambda *_: None, definitions=[],
            read_progress=lambda: service.read_checkpoint_note(case.work, include_fresh=True),
            original_goal=case.work['message'])


@pytest.mark.parametrize('race', ['steer', 'checkpoint', 'second_change', 'stale_epoch'])
def test_goal_rejudges_changed_input_at_most_once(case, race):
    facts = []

    def evaluate(inputs, *_):
        facts.append(inputs)
        if len(facts) == 1:
            if race == 'stale_epoch':
                with ThreadPoolExecutor(1) as pool:
                    pool.submit(case.restart).result(10)
                    pool.submit(change, case, 'mode').result(10)
            else:
                change(case, 'checkpoint' if race == 'checkpoint' else 'steer')
        elif race == 'second_change':
            change(case, 'checkpoint')
        return Judgment('yes')

    judge = case.service.decision_judge
    with mock.patch.object(judge, '_evaluate_goal', side_effect=evaluate), \
            mock.patch.object(judge, 'checkpoint_snapshot', wraps=judge.checkpoint_snapshot) as snapshot:
        result = run_read_goal(case)
    assert snapshot.call_count == (3 if race == 'stale_epoch' else 4)
    assert len(facts) == (1 if race == 'stale_epoch' else 2)
    assert facts[0]['owner_request'] == case.work['message']
    if race == 'steer':
        assert facts[1]['owner_request'] == case.work['message'] + '\n\nUse the corrected scope.'
    if race == 'checkpoint':
        assert '24 of 32 bytes verified' in facts[1]['observations']
    if race in ('stale_epoch', 'second_change'):
        assert result.goal_judgment == 'unavailable'
    else:
        assert result.goal_judgment == 'yes'
        assert result.outcome == 'succeeded'


@pytest.mark.parametrize('barrier', ['mode', 'missing_staging'])
def test_first_run_read_is_judged_even_when_auto_resume_is_blocked(case, barrier, monkeypatch):
    block(case, barrier, monkeypatch)
    assert not case.store.read_checkpoint(case.id)['resumed']
    with mock.patch.object(case.service.decision_judge, '_evaluate_goal', return_value=Judgment('yes')) as evaluate:
        result = run_read_goal(case)
    evaluate.assert_called_once()
    assert result.goal_judgment == 'yes'
    assert result.outcome == 'succeeded'


def test_concurrent_current_epoch_calls_once_and_completed_result_survives_restart(case):
    entered, release = Event(), Event()
    calls = []
    def call():
        calls.append('called')
        entered.set()
        assert release.wait(10)
        return {'winner': 'completed'}
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(case.judge, call)
        assert entered.wait(10)
        assert case.judge(mock.Mock(side_effect=AssertionError('duplicate call'))) is None
        release.set()
        assert first.result(10) == {'winner': 'completed'}
    _, later = case.restart()
    assert case.judge(mock.Mock(side_effect=AssertionError('completed result not reused')), budget=later) == {'winner': 'completed'}
    assert calls == ['called']
    changed = mock.Mock(return_value={'winner': 'different facts'})
    assert case.judge(changed, budget=later, facts={'answer': 'new observation'}) == {'winner': 'different facts'}
    changed.assert_called_once()


@pytest.mark.parametrize('new_completed', [False, True])
@pytest.mark.parametrize('old_fails', [False, True])
def test_old_epoch_response_cannot_replace_new_registration_or_result(case, new_completed, old_fails):
    entered, release_old, entered_new, release_new = Event(), Event(), Event(), Event()
    def old_call():
        entered.set()
        assert release_old.wait(10)
        if old_fails:
            raise ValueError('old dispatch failed')
        return {'winner': 'old'}
    def new_call():
        entered_new.set()
        assert release_new.wait(10)
        return {'winner': 'new'}
    def judge_old():
        with case.service.work_decision_scope(case.id, case.budget):
            return case.judge(old_call)
    with ThreadPoolExecutor(2) as pool:
        old = pool.submit(judge_old)
        assert entered.wait(10)
        _, later = case.restart()
        new = pool.submit(case.judge, new_call, budget=later)
        assert entered_new.wait(10)
        assert case.judge(mock.Mock(side_effect=AssertionError('duplicate after restart')), budget=later) is None
        if new_completed:
            release_new.set()
            assert new.result(10) == {'winner': 'new'}
        release_old.set()
        assert old.result(10) is None
        with case.store.db() as db:
            row = db.execute('SELECT epoch,result FROM work_read_judgments').fetchone()
        assert row['epoch'] == later.ledger.epoch
        assert json.loads(row['result']) == {'winner': 'new'} if new_completed else row['result'] is None
        release_new.set()
        assert new.result(10) == {'winner': 'new'}


@pytest.mark.parametrize('exit_type', [SystemExit, KeyboardInterrupt, GeneratorExit])
def test_interrupted_judgment_stays_pending_until_recovery(case, exit_type):
    call = mock.Mock(side_effect=exit_type('interrupted judgment'))
    with pytest.raises(exit_type, match='interrupted judgment'):
        case.judge(call, purpose='goal-reached')
    call.assert_called_once_with()
    with case.store.db() as db:
        row = db.execute('SELECT epoch,result FROM work_read_judgments').fetchone()
        assert row is not None
        assert row['epoch'] == case.budget.ledger.epoch
        assert row['result'] is None

    retry = mock.Mock(return_value={'outcome': 'yes'})
    assert case.judge(retry, purpose='goal-reached') is None
    retry.assert_not_called()
    _, later = case.restart()
    assert later.ledger.epoch == case.budget.ledger.epoch + 1
    assert case.judge(retry, budget=later, purpose='goal-reached') == {'outcome': 'yes'}
    assert case.judge(retry, budget=later, purpose='goal-reached') == {'outcome': 'yes'}
    retry.assert_called_once_with()
    with case.store.db() as db:
        rows = db.execute('SELECT epoch,result FROM work_read_judgments').fetchall()
        assert len(rows) == 1
        assert rows[0]['epoch'] == later.ledger.epoch
        assert json.loads(rows[0]['result']) == {'outcome': 'yes'}


@pytest.mark.parametrize('failure', ['raise', 'none', 'unavailable', 'unserializable-result'])
def test_unavailable_judgment_never_completes_and_restarts_once(case, failure):
    call = mock.Mock(return_value={'outcome': 'unavailable'})
    if failure == 'raise': call.side_effect = ValueError('injected judgment failure')
    elif failure == 'none': call.return_value = None
    elif failure == 'unserializable-result': call.return_value = {'outcome': 'yes', 'bad': object()}
    assert case.judge(call, purpose='goal-reached') is None
    with case.store.db() as db:
        assert db.execute('SELECT result FROM work_read_judgments').fetchone() is None
    retry = mock.Mock(return_value={'outcome': 'yes'})
    assert case.judge(retry, purpose='goal-reached') == {'outcome': 'yes'}
    assert case.judge(retry, purpose='goal-reached') == {'outcome': 'yes'}
    retry.assert_called_once_with()
    _, later = case.restart()
    assert case.judge(retry, budget=later, purpose='goal-reached') == {'outcome': 'yes'}
    retry.assert_called_once_with()


@pytest.mark.parametrize('facts', [{'bad': object()}, {1: 1, 'mixed': 2}, {'bad': float('nan')}])
def test_unserializable_facts_fail_closed_before_registration(case, facts):
    call = mock.Mock(side_effect=AssertionError('invalid facts reached model'))
    assert case.judge(call, facts=facts) is None
    call.assert_not_called()
    with case.store.db() as db:
        assert db.execute('SELECT count(*) FROM work_read_judgments').fetchone()[0] == 0


def block(case, barrier, monkeypatch):
    if barrier == 'stop': case.store.put(WORK_STOP_KEY, [case.id])
    elif barrier == 'paused':
        row = case.store.config(case.budget.ledger.key)
        case.store.put(case.budget.ledger.key, {**row, 'active': False, 'remaining_seconds': 60})
    elif barrier in ('turns', 'attempts'):
        from personal_agent.agent_runtime import WORK_MODEL_TURNS, WORK_TOOL_ATTEMPTS
        row = case.store.config(case.budget.ledger.key)
        case.store.put(case.budget.ledger.key, {**row, barrier: WORK_MODEL_TURNS if barrier == 'turns' else WORK_TOOL_ATTEMPTS})
    elif barrier == 'expired': case.now[0] += 10000
    elif barrier == 'mode': case.store.put('work_mode', {'enabled': True})
    elif barrier == 'missing_staging': monkeypatch.setattr(reads, 'staged_checkpoint_available', lambda *_: False)
    elif barrier == 'owner_wait': monkeypatch.setattr(case.service, 'work_waits_for_owner', lambda _: ['fixture'])
    elif barrier == 'revoked':
        # Inject the existing authority/continuation decision, not a new grant policy.
        original = case.service.may_continue
        monkeypatch.setattr(case.service, 'may_continue', lambda work_id, purpose, **kw:
                            (False, 'revoked') if work_id == case.id else original(work_id, purpose, **kw))
    elif barrier == 'unknown':
        with case.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (case.id, 'api_request', 'failed', '{"effect":"unknown"}', case.now[0]))


@pytest.mark.parametrize('boundary,barrier', [
    pytest.param(boundary, barrier, id=f'{boundary}-{barrier}')
    for boundary in ('judgment', 'recover', 'claim')
    for barrier in ('stop', 'paused', 'expired', 'revoked', 'unknown', 'owner_wait', 'turns', 'attempts',
                    'mode', 'missing_staging')
    if boundary != 'judgment' or barrier not in ('mode', 'missing_staging')
])
def test_existing_boundaries_block_reuse_and_auto_resume(case, barrier, boundary, monkeypatch):
    assert case.judge(lambda: {'winner': 'prior'}) == {'winner': 'prior'}
    if boundary == 'claim':
        case.store.recover(can_resume_checkpoint=case.service.can_resume_read_checkpoint)
    block(case, barrier, monkeypatch)
    if boundary == 'judgment':
        call = mock.Mock(side_effect=AssertionError('blocked judgment'))
        assert case.judge(call) is None
        call.assert_not_called()
    elif boundary == 'recover':
        case.store.recover(can_resume_checkpoint=case.service.can_resume_read_checkpoint)
        assert case.store.job(case.id)['status'] == 'interrupted'
    else:
        assert case.service._claim_queued_work() is True
        assert case.store.job(case.id)['status'] == 'interrupted'


def make_run(case, work, budget, answer):
    run = WorkRun(work, case.now[0])
    run.work_budget = budget
    run.response = answer
    run.work_sources = {answer}
    run.goal_outcome = 'reached'
    run.effect_state = 'none'
    return run


@pytest.mark.parametrize('fail', [False, True])
def test_old_finalizer_loses_all_evidence_and_delivery_writes(case, monkeypatch, fail):
    entered, release = Event(), Event()
    service = case.service
    old_run = make_run(case, case.work, case.budget, 'old')
    original = service.failed_attempt_report if fail else service._with_disclosures
    def delay(*args, **kwargs):
        if args[1] in ('old', 'injected failure'):
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'failed_attempt_report' if fail else '_with_disclosures', delay)
    # Use the real delivery claim and projection with only Telegram's transport replaced.
    case.store.put('telegram', {'enabled': True, 'generation': 'g', 'user_id': 42})
    with case.store.db() as db:
        db.execute("UPDATE jobs SET channel='telegram:g',chat_id=42 WHERE id=?", (case.id,))
    old_run.job = case.store.job(case.id)
    sent = []
    monkeypatch.setattr(service.telegram, 'send_message', lambda *args, **kwargs: sent.append(args) or {'message_id': 123})
    with ThreadPoolExecutor(1) as pool:
        finish = pool.submit(service._fail_work, old_run, ValueError('injected failure')) if fail else pool.submit(service._finish_work, old_run)
        assert entered.wait(10)
        work, budget = case.restart()
        new_run = make_run(case, work, budget, 'new')
        service._finish_work(new_run)
        service.deliver_one()
        release.set()
        assert finish.result(10) is False
    assert service._after_work(old_run) is False
    assert service._fail_work(old_run, ValueError('late')) is False
    service.work_executor._fail_work_terminal(old_run)
    service.deliver_one()
    assert len(sent) == 1
    job = case.store.job(case.id)
    assert (job['response'], job['status'], job['goal_outcome'], job['delivery']) == ('new', 'succeeded', 'reached', 'sent')
    assert case.store.config(WORK_SOURCES_KEY)[case.id] == ['new']
    with case.store.db() as db:
        messages = db.execute("SELECT content FROM messages WHERE job_id=? AND role='assistant'", (case.id,)).fetchall()
        tails = db.execute("SELECT count(*) FROM tool_events WHERE job_id=? AND tool='after_work'", (case.id,)).fetchone()[0]
    assert [row['content'] for row in messages] == ['new']
    assert tails == 1
    assert case.store.turn_provenance(case.id)['goal']['outcome'] == 'succeeded'


def test_late_indirect_writers_and_orchestration_event_keep_the_old_epoch(case):
    service = case.service
    orch = orchestration(case)
    _, new_budget = case.restart()
    with service.work_decision_scope(case.id, new_budget):
        service.record_work_sources(case.id, {'current'})
        service.record_turn_provenance(case.id, status='current')
    with service.work_decision_scope(case.id, case.budget):
        service.record_work_sources(case.id, {'stale'})
        service.record_turn_provenance(case.id, status='stale')
        with pytest.raises(LedgerSuperseded, match='superseded'):
            service.record_decision({'purpose': 'late'})
        service.record_stage(case.id, 'stale', case.now[0])
        orch.record('late', {'winner': 'stale'})
    assert case.store.config(WORK_SOURCES_KEY)[case.id] == ['current']
    assert case.store.turn_provenance(case.id)['status'] == 'current'
    assert not case.store.work_decisions(case.id)
    with case.store.db() as db:
        assert not db.execute("SELECT 1 FROM tool_events WHERE status='late' OR tool='response_timing'").fetchone()


def test_checkpoint_guard_uses_the_scope_at_execute_time(case):
    _, budget = case.restart()
    with case.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        with case.store.read_checkpoint_writer(case.id, case.budget.ledger.epoch):
            with pytest.raises(LedgerSuperseded):
                db.execute('INSERT INTO tool_events(job_id) VALUES (?)', (case.id,))
    with ExitStack() as connection:
        with case.store.read_checkpoint_writer(case.id, case.budget.ledger.epoch):
            db = connection.enter_context(case.store.db())
        db.execute('INSERT INTO tool_events(job_id) VALUES (?)', (case.id,))
        with case.store.read_checkpoint_writer(case.id, budget.ledger.epoch):
            db.execute('INSERT INTO tool_events(job_id) VALUES (?)', (case.id,))


def test_implicit_checkpoint_write_holds_the_lock_during_admission(case, monkeypatch):
    case.store.put('fenced_value', 'original')
    recovery = QuickStore(case.store.root)
    recovery_db = recovery.db
    can_settle = case.store.read_checkpoint_can_settle
    fence_blocked = []

    @contextmanager
    def without_waiting():
        with recovery_db() as db:
            db.execute('PRAGMA busy_timeout=0')
            yield db

    def try_fence_after_check(*args, **kwargs):
        admitted = can_settle(*args, **kwargs)
        try:
            with mock.patch.object(recovery, 'db', without_waiting):
                recovery.recover()
        except sqlite3.OperationalError as exc:
            assert exc.sqlite_errorcode == sqlite3.SQLITE_BUSY
            fence_blocked.append(True)
        return admitted

    epoch = case.budget.ledger.epoch
    with monkeypatch.context() as patch:
        patch.setattr(case.store, 'read_checkpoint_can_settle', try_fence_after_check)
        with case.store.read_checkpoint_writer(case.id, epoch):
            case.store.put('fenced_value', 'admitted')

    assert fence_blocked == [True]
    assert case.store.config('fenced_value') == 'admitted'
    recovery.recover()
    with case.store.read_checkpoint_writer(case.id, epoch):
        with pytest.raises(LedgerSuperseded):
            case.store.put('fenced_value', 'late')
    assert case.store.config('fenced_value') == 'admitted'


def test_checkpoint_guard_leaves_cte_reads_and_autocommit_transaction_free(case):
    case.restart()
    with case.store.read_checkpoint_writer(case.id, case.budget.ledger.epoch), case.store.db() as db:
        assert db.execute('WITH answer AS (SELECT 42) SELECT * FROM answer').fetchone()[0] == 42
        assert not db.in_transaction
        db.autocommit = True
        with pytest.raises(LedgerSuperseded):
            db.execute('INSERT INTO tool_events(job_id) VALUES (?)', (case.id,))
    with case.store.db() as db:
        db.autocommit = True
        db.execute('INSERT INTO tool_events(job_id) VALUES (?)', (case.id,))
        assert not db.in_transaction


def test_effective_folder_grant_is_rechecked_even_when_config_is_unchanged(case, tmp_path):
    folder = tmp_path / 'reference'
    folder.mkdir()
    case.store.put('file_roots', [{'id': 'reference', 'path': str(folder)}])
    snapshot = case.snapshot()
    assert case.judge(lambda: {'winner': 'prior'}, snapshot=snapshot) == {'winner': 'prior'}
    folder.rename(tmp_path / 'moved')
    call = mock.Mock(side_effect=AssertionError('revoked folder reached model'))
    assert case.judge(call, snapshot=snapshot) is None
    call.assert_not_called()


def test_configuring_unavailable_folders_does_not_change_effective_authority(case):
    snapshot = case.snapshot()
    path = str(case.store.root / 'unavailable-reference')
    reference = {'id': 'missing', 'path': path}
    case.store.put('file_roots', [reference])
    case.store.put('file_workspace', {'references': [reference], 'workspace': path, 'workspace_id': 'missing'})
    assert case.snapshot() == snapshot


def test_observation_history_is_not_an_effective_grant_change(case):
    authority = {'state': 'active', 'revision': 'same', 'epoch': 'same', 'operations': {'read': 'read'}}
    case.store.put('trust_record', {'hands': {'fixture': authority}})
    snapshot = case.snapshot()
    assert case.judge(lambda: {'winner': 'prior'}, snapshot=snapshot) == {'winner': 'prior'}
    case.store.put('trust_record', {'hands': {'fixture': {**authority, 'history': [{'at': case.now[0]}]}}})
    assert case.snapshot() == snapshot
    assert case.judge(mock.Mock(side_effect=AssertionError('unnecessary call'))) == {'winner': 'prior'}


def test_successful_settlement_can_pause_its_ledger_and_never_settles_twice(case):
    run = make_run(case, case.work, case.budget, 'current')
    case.service._finish_work(run)
    case.service._settle_work_judgments(run)
    assert case.store.config(case.budget.ledger.key)['active'] is False
    assert case.service._finish_work(run) is False
    assert case.service._fail_work(run, ValueError('late')) is False


def test_read_tool_child_thread_cannot_append_late_intake_evidence(case, monkeypatch):
    from personal_agent.agent_runtime import Capabilities
    capabilities = Capabilities(case.store, None, {}, None, case.id, lambda *_: None, budget=case.budget)
    entered, release = Event(), Event()
    def read(**kwargs):
        entered.set()
        assert release.wait(10)
        return {'content': 'late external text', 'path': 'record.txt', 'read_only': True}
    monkeypatch.setattr(capabilities, 'read_file', read)
    with ThreadPoolExecutor(1) as pool:
        old = pool.submit(capabilities.execute, 'read_file', {'root_id': 'fixture', 'path': 'record.txt'})
        assert entered.wait(10)
        case.restart()
        release.set()
        with pytest.raises(LedgerSuperseded): old.result(10)
    assert not case.store.outside_content_sources(case.id)


@pytest.mark.parametrize('exit_type', [ValueError, SystemExit, KeyboardInterrupt, GeneratorExit])
@pytest.mark.parametrize('checkpoint', [False, True])
def test_only_process_exit_preserves_a_running_checkpoint_ledger(case, monkeypatch, exit_type, checkpoint):
    from concurrent.futures import Future
    from personal_agent.work_execution import PROCEED
    if not checkpoint:
        with case.store.db() as db:
            db.execute('DELETE FROM work_read_checkpoints WHERE job_id=?', (case.id,))
    early, intent = Future(), Future()
    early.set_result('plan')
    intent.set_result('intent')
    def route(run):
        run.work_budget = case.budget
        run.early_plan, run.early_intent = early, intent
        return PROCEED
    def stop(run): raise exit_type('injected exit')
    monkeypatch.setattr(case.service, '_route_work', route)
    monkeypatch.setattr(case.service, '_load_work_photos', lambda _: PROCEED)
    monkeypatch.setattr(case.service, '_run_rule_intent', stop)
    # Leave status running to distinguish exit handling from terminal-status checks.
    monkeypatch.setattr(case.service, '_fail_work', lambda *_: False)
    with mock.patch.object(case.service, 'settle_early_judgment', wraps=case.service.settle_early_judgment) as settled:
        if issubclass(exit_type, Exception):
            assert case.service.work_executor._execute_claimed(case.work) is True
        else:
            with pytest.raises(exit_type, match='injected exit'):
                case.service.work_executor._execute_claimed(case.work)
    assert mock.call(early) in settled.call_args_list
    assert mock.call(intent) in settled.call_args_list
    assert case.store.config(case.budget.ledger.key)['active'] is (checkpoint and not issubclass(exit_type, Exception))
    if checkpoint and not issubclass(exit_type, Exception):
        case.restart()


def test_entry_snapshot_registers_and_rejects_a_late_epoch_response(case):
    def late():
        case.restart()
        return {'outcome': 'yes'}
    call = mock.Mock(side_effect=late)
    assert case.service.read_checkpoint_judgment(
        case.id, 'goal-reached', {'owner_request': case.work['message']},
        reads.checkpoint_note(CHECKPOINT), call, budget=case.budget) is None
    call.assert_called_once()
    with case.store.db() as db:
        rows = db.execute('SELECT epoch,result FROM work_read_judgments').fetchall()
    assert len(rows) == 1
    assert rows[0]['epoch'] == case.budget.ledger.epoch
    assert rows[0]['result'] is None


def test_explicit_missing_build_snapshot_is_not_replaced_at_entry(case):
    call = mock.Mock(side_effect=AssertionError('input predates checkpoint'))
    assert case.service.read_checkpoint_judgment(
        case.id, 'goal-reached', {}, reads.checkpoint_note(CHECKPOINT), call,
        budget=case.budget, snapshot=None) is None
    call.assert_not_called()


def test_goal_entry_snapshot_reuses_each_coverage_once(case):
    judge = case.service.decision_judge
    with case.service.work_decision_scope(case.id, case.budget):
        with mock.patch.object(judge, '_evaluate_goal', return_value=Judgment('no')) as evaluate:
            for coverage in range(2):
                if coverage: change(case, 'checkpoint')
                note = reads.checkpoint_note(case.store.read_checkpoint(case.id))
                for _ in range(2):
                    result = judge.goal_reached(case.work['message'], note, work_id=case.id, continues=note)
                    assert result.outcome == 'no'
                    assert evaluate.call_count == coverage + 1


def test_goal_entry_snapshot_precedes_fact_construction(case):
    class Request:
        def __str__(self):
            change(case, 'steer')
            return case.work['message']
    with case.service.work_decision_scope(case.id, case.budget):
        with mock.patch.object(case.service.decision_judge, '_evaluate_goal') as evaluate:
            result = case.service.decision_judge.goal_reached(
                Request(), 'verified', work_id=case.id, continues=reads.checkpoint_note(CHECKPOINT))
    assert result.outcome == 'unavailable'
    evaluate.assert_not_called()
