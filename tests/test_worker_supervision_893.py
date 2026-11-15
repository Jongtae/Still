"""#893: contain worker failures using real Work/preparation/SQLite boundaries.

Only phase faults and external AI/Telegram transports are injected; no network.
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from personal_agent import preparations as prep
from personal_agent.bounded_execution import ExecutionError
from personal_agent.messages import msg
from personal_agent.quickstart_service import UNKNOWN_EFFECT_RETRY_REFUSAL
from test_preparations import CHAT, GENERATION, SECRET, _WatchCase, watch_engine


@pytest.fixture
def service_case():
    value = _WatchCase('runTest')
    value.setUp()
    try:
        value.judge = watch_engine([False])
        value.service.use_decision_engine(value.judge)
        yield value
    finally:
        value.doCleanups()


@pytest.fixture
def case(service_case):
    service_case.watched = service_case.watch(runs=1)
    service_case.now = service_case.watched['due_at'] + 1
    assert service_case.tick()
    return service_case


def assistant_messages(case, work):
    with case.store.db() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM messages WHERE job_id=? AND role='assistant'", (work,))]


def work_id(case):
    [work] = case.runs(case.watched['id'])
    return work['id']


def markers(case):
    with case.store.db() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM tool_events WHERE job_id=? AND tool='after_work'", (work_id(case),))]


def assert_settled(case, status):
    assert case.store.job(work_id(case))['status'] == status
    assert len(markers(case)) == 1
    assert case.service.preparations.get(case.watched['id'])['state'] == 'running'
    assert case.tick()
    row = case.service.preparations.get(case.watched['id'])
    assert row['state'] == row['last_outcome'] == prep.STATE_FAILED


def test_unexpected_phase_failure_settles_work_and_preparation(case, caplog):
    with patch.object(case.service, '_run_worker_attempts',
                      side_effect=sqlite3.OperationalError('database is locked')):
        assert case.service.run_one()
    assert_settled(case, 'failed')
    assert markers(case)[0]['status'] == 'completed'
    assert 'OperationalError' in caplog.text
    assert 'database is locked' not in caplog.text


@pytest.mark.parametrize('unknown_effect', [False, True])
def test_failure_handler_fault_uses_terminal_transaction(case, unknown_effect, caplog):
    work = work_id(case)
    if unknown_effect:
        # A real unknown-effect event, read by the normal continuation/retry rule.
        with case.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work, 'api_request', 'failed', json.dumps({
                           'host_action': 'api_request', 'code': 'effect_unknown',
                           'retry': 'never', 'effect': 'unknown'}), case.now))
    with patch.object(case.service, '_run_worker_attempts', side_effect=ExecutionError(
            'invalid engine output', failure_class='invalid-output')), \
         patch.object(case.service, 'steer_disposition_notice', return_value=SECRET), \
         patch.object(case.service, 'record_work_sources',
                      side_effect=sqlite3.OperationalError('failure handler locked')):
        assert case.service.run_one()
    [reply] = assistant_messages(case, work)
    body = (msg('compose.i_couldn_t_complete_this_request', reason='invalid engine output')
            if unknown_effect else msg('projection.i_couldn_t_complete_this_request'))
    assert reply['content'] == case.service.scrub_work_text(work, SECRET + '\n\n' + body)
    assert SECRET not in reply['content']
    assert case.store.job(work)['delivery'] == case.service.reply_delivery(case.store.job(work))
    assert_settled(case, 'unknown' if unknown_effect else 'failed')
    assert markers(case)[0]['status'] == 'pending'
    assert 'OperationalError' in caplog.text
    assert 'failure handler locked' not in caplog.text
    if unknown_effect:
        assert case.service.safe_retry(case.store.job(work)) == (False, UNKNOWN_EFFECT_RETRY_REFUSAL)
    # A settled, single-slot preparation is no longer eligible for prompts;
    # its pending tail still completes without reopening the preparation.
    case.service.sweep_after_work()
    assert markers(case)[0]['status'] == 'completed'


@pytest.mark.parametrize('channel', ['telegram', 'web'])
@pytest.mark.parametrize('unknown_effect', [False, True])
def test_failure_handler_fault_queues_one_terminal_reply(service_case, channel, unknown_effect):
    case = service_case
    work = case.store.enqueue('Please finish this request', 'terminal-reply',
                              channel=f'telegram:{GENERATION}' if channel == 'telegram' else 'web',
                              chat_id=CHAT if channel == 'telegram' else None)
    job = case.store.job(work)
    if unknown_effect:
        with case.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work, 'api_request', 'failed', json.dumps({
                           'host_action': 'api_request', 'code': 'effect_unknown',
                           'retry': 'never', 'effect': 'unknown'}), case.now))
    notice = 'This answers your earlier request.'
    with patch.object(case.service, '_run_worker_attempts', side_effect=ExecutionError('invalid engine output')), \
         patch.object(case.service, 'steer_disposition_notice', return_value=notice), \
         patch.object(case.service, 'record_work_sources',
                      side_effect=sqlite3.OperationalError('failure handler locked')):
        assert case.service.run_one()
    terminal = case.store.job(work)
    assert terminal['status'] == ('unknown' if unknown_effect else 'failed')
    assert terminal['delivery'] == case.service.reply_delivery(job) == ('pending' if channel == 'telegram' else 'none')
    [reply] = assistant_messages(case, work)
    body = (msg('compose.i_couldn_t_complete_this_request', reason='invalid engine output')
            if unknown_effect else msg('projection.i_couldn_t_complete_this_request'))
    assert reply['content'] == notice + '\n\n' + body
    assert reply['channel'] == job['channel']
    assert reply['workspace_id'] == job['workspace_id']
    assert reply['created'] > 0
    assert reply['delivery_projection'] is None
    case.telegram.clear()
    case.service.deliver_one()
    case.service.deliver_one()
    if channel == 'telegram':
        [sent] = case.sends()
        assert sent['chat_id'] == CHAT
        assert msg('projection.i_couldn_t_complete_this_request') in sent['text']
        assert case.store.job(work)['delivery'] == 'sent'
    else:
        assert not case.sends()
        assert case.store.job(work)['delivery'] == case.service.reply_delivery(job)
    assert assistant_messages(case, work) == [reply]


def test_terminal_reply_insert_failure_rolls_back_status_and_delivery(case):
    work = work_id(case)
    with case.store.db() as db:
        db.execute("CREATE TRIGGER reject_assistant_reply BEFORE INSERT ON messages "
                   "WHEN NEW.role='assistant' BEGIN SELECT RAISE(ABORT, 'reply insert failed'); END")
    with patch.object(case.service, '_run_worker_attempts', side_effect=ExecutionError('invalid output')), \
         patch.object(case.service, 'record_work_sources',
                      side_effect=sqlite3.OperationalError('failure handler locked')):
        assert case.service.run_one()
    job = case.store.job(work)
    assert job['status'] == 'running'
    assert job['delivery'] == 'none'
    assert job['error'] is None
    assert not assistant_messages(case, work)
    assert not markers(case)


@pytest.mark.parametrize('step', ['fold_delivered_steer', 'record_stage', 'action_continuation_note'])
def test_unexpected_post_claim_setup_failure_is_terminal(case, step):
    with patch.object(case.service, step, side_effect=TypeError('invalid record')):
        assert case.service.run_one()
    assert_settled(case, 'failed')


@pytest.mark.parametrize('status', ['interrupted', 'cancelled'])
def test_last_resort_preserves_status_changed_during_failure(case, status):
    def fail_sources(*args):
        with case.store.db() as db:
            db.execute('UPDATE jobs SET status=? WHERE id=?', (status, work_id(case)))
        raise sqlite3.OperationalError('failure handler locked')

    with patch.object(case.service, '_run_worker_attempts', side_effect=ExecutionError('invalid output')), \
         patch.object(case.service, 'record_work_sources', side_effect=fail_sources):
        assert case.service.run_one()
    assert case.store.job(work_id(case))['status'] == status
    assert case.store.job(work_id(case))['delivery'] == 'none'
    assert not assistant_messages(case, work_id(case))
    assert not markers(case), 'no new terminal commit means no new tail'


def test_tail_failure_preserves_committed_outcome_and_enrolment(case):
    with patch.object(case.service, '_run_worker_attempts', side_effect=ExecutionError('invalid output')), \
         patch.object(case.service, '_after_work', side_effect=TypeError('tail failure')):
        assert case.service.run_one()
    assert_settled(case, 'failed')
    assert markers(case)[0]['status'] == 'pending'
    assert len(assistant_messages(case, work_id(case))) == 1
    job = case.store.job(work_id(case))
    assert job['delivery'] == case.service.reply_delivery(job)


STEPS = (
    'run_due_preparation', 'run_due_qualification', 'run_one', 'deliver_one',
    'deliver_notification', 'expire_memory_prompts', 'release_memory_saved_notices',
    'sweep_after_work', 'process_browser_logins', 'start_session_keepalive',
    'purge_forgotten', 'settle_unknown_deliveries', 'run_owner_model_upkeep',
)


@pytest.mark.parametrize('failing_step', STEPS)
def test_iteration_continues_after_each_step_failure(case, monkeypatch, caplog, failing_step):
    calls = []
    failed = False

    def step(name):
        def run():
            nonlocal failed
            calls.append(name)
            if name == failing_step and not failed:
                failed = True
                raise RuntimeError('private failure detail')
        return run

    for name in STEPS:
        monkeypatch.setattr(case.service, name, step(name))
    monkeypatch.setattr('personal_agent.quickstart_service.time.monotonic', lambda: 100)
    deadlines = case.service._work_iteration(0, 0)
    assert calls == list(STEPS), 'later steps run even when run_one (or another step) fails'
    case.service._work_iteration(*deadlines)
    assert calls[len(STEPS):] == [name for name in STEPS if name not in (
        'purge_forgotten', 'settle_unknown_deliveries')]
    assert calls.count('run_due_preparation') == 2
    assert 'RuntimeError' in caplog.text
    assert 'private failure detail' not in caplog.text


def test_restart_recovery_settles_running_preparation_regression_pin(case):
    """Regression pin: passes before #893; restart recovery already works."""
    work = work_id(case)
    assert isinstance(case.service._claim_queued_work(), dict)
    assert case.store.job(work)['status'] == 'running'
    case.store.recover()
    assert case.store.job(work)['status'] == 'interrupted'
    assert case.tick()
    row = case.service.preparations.get(case.watched['id'])
    assert row['state'] == row['last_outcome'] == prep.STATE_FAILED


@pytest.mark.parametrize('signal', [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_process_signals_propagate_regression_pin(case, signal):
    """Regression pin: direct BaseException subclasses already propagate."""
    with patch.object(case.service, '_run_worker_attempts', side_effect=signal), pytest.raises(signal):
        case.service.run_one()
    assert case.store.job(work_id(case))['status'] == 'running'
    assert not markers(case)
