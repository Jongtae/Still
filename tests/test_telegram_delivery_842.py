"""#842 D2: fixture Telegram wires and real SQLite delivery/restart boundaries."""
import time
from unittest import mock

import pytest

from personal_agent.conversation_handoff import TelegramNotSent, TelegramRejected
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.providers import ProviderError
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.work_execution import WorkRun
import test_quickstart as quick


@pytest.fixture
def case(request):
    def make(cls):
        value = cls(methodName='runTest')
        request.addfinalizer(value.doCleanups)
        request.addfinalizer(value.tearDown)
        value.setUp()
        return value
    return make


def _telegram(case):
    c = case(quick.QuickstartTests)
    c.store.secret('telegram_token', '123456:TEST_TOKEN')
    c.store.put('telegram', {'enabled': True, 'generation': 'g', 'user_id': 42, 'cursor': 0})
    c.service.use_decision_engine(FixtureDecisionEngine())
    work = c.store.enqueue('fixture request', 'original', channel='telegram:g', chat_id=42)
    c.service.telegram_turns.record_source(work, 42, 101)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded',response='fixture answer',delivery='pending' WHERE id=?", (work,))
    return c, work


def _restart(c):
    service = AgentService(QuickStore(c.store.root), telegram_transport=c.service.telegram_transport)
    service.use_decision_engine(FixtureDecisionEngine())
    service.recover_interrupted_work()
    return service


def _sent(c):
    return [call for call in c.calls if call[0].endswith('/sendMessage')]


def _settings_draft(c, work):
    job = c.store.job(work)
    return c.service.settings_orchestrator.propose(c.service.settings_owner(job), job['channel'],
        'current_context', 'enabled', 'on', work_id=work)


def _finish(c, work, fail=False):
    job = c.store.job(work)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
    run = WorkRun(job, time.time())
    run.response = 'fixture answer'
    if fail:
        c.service._fail_work(run, ValueError('fixture failure'))
    else:
        c.service._finish_work(run)
    return run


def test_sent_reply_and_anchor_commit_together(case):
    c, work = _telegram(case)
    c.service.deliver_one()
    assert c.store.job(work)['delivery'] == 'sent'
    assert c.service.telegram_turns.get(work)['reply_message_id'] == 1
    assert c.service.ingest_reaction({'chat': {'id': 42, 'type': 'private'},
        'user': {'id': 42}, 'message_id': 1,
        'new_reaction': [{'type': 'emoji', 'emoji': '👍'}]}, 'g') == 'answer'
    c.service.deliver_one()
    assert len(_sent(c)) == 1


def test_anchor_failure_rolls_back_receipt_and_anchor_without_resending(case):
    c, work = _telegram(case)
    original = c.service.telegram_turns.record_reply
    def lost_anchor(*args, **kwargs):
        assert kwargs['db'] is not None
        original(*args, **kwargs)
        raise RuntimeError('cut off before commit')
    with mock.patch.object(c.service.telegram_turns, 'record_reply', side_effect=lost_anchor):
        with pytest.raises(RuntimeError, match='before commit'):
            c.service.deliver_one()
    assert c.store.job(work)['delivery'] == 'unknown'
    assert c.service.telegram_turns.get(work)['reply_message_id'] is None
    service = _restart(c)
    service.deliver_one()
    assert len(_sent(c)) == 1


def test_notice_not_sent_requeues_with_shared_bounded_backoff(case):
    c, work = _telegram(case)
    note = c.store.queue_notification(work, 42, 'g', 'completed')
    original = c.service.telegram_transport
    attempts = []
    def offline(url, *args, **kwargs):
        if url.endswith('/sendMessage'):
            attempts.append(url)
            raise TelegramNotSent('fixture connect failed')
        return original(url, *args, **kwargs)
    c.service.telegram_transport = offline
    with mock.patch('personal_agent.telegram_surface.time.monotonic', return_value=1000):
        assert c.service.deliver_notification()
        assert c.store.notification(note['id'])['state'] == 'queued'
        assert c.service._unsent_retry_at == 1015
        assert not c.service.deliver_notification()
    with mock.patch('personal_agent.telegram_surface.time.monotonic', return_value=1015):
        assert c.service.deliver_notification()
        assert c.service._unsent_backoff == 30
        assert not c.service.deliver_notification()
    assert len(attempts) == 2
    c.service.telegram_transport = original
    with mock.patch('personal_agent.telegram_surface.time.monotonic', return_value=1045):
        assert c.service.deliver_notification()
        assert not c.service.deliver_notification()
    assert c.store.notification(note['id'])['state'] == 'sent'
    assert c.service._unsent_backoff == 0
    assert len(_sent(c)) == 1


def test_multipart_notice_last_part_not_sent_is_unknown(case):
    c, work = _telegram(case)
    _settings_draft(c, work)
    c.service.queue_settings_confirmation(c.store.job(work))
    note = c.store.notification_of(work, 'settings_change_proposed')
    original = c.service.telegram_transport
    attempts = []
    def partial(url, *args, **kwargs):
        if url.endswith('/sendMessage'):
            attempts.append(url)
            if len(attempts) == 3:
                raise TelegramNotSent('last part never sent')
        return original(url, *args, **kwargs)
    c.service.telegram_transport = partial
    with mock.patch('personal_agent.telegram_surface.notice_text', return_value='x' * 4000):
        assert c.service.deliver_notification()
    assert c.store.notification(note['id'])['state'] == 'unknown'
    assert len(_sent(c)) == 2
    service = _restart(c)
    assert not service.deliver_notification()
    assert len(attempts) == 3


def test_notice_uncertain_provider_error_is_never_retried(case):
    c, work = _telegram(case)
    note = c.store.queue_notification(work, 42, 'g', 'completed')
    with mock.patch.object(c.service.telegram, 'send_message', side_effect=ProviderError('response lost')) as send:
        assert c.service.deliver_notification()
        assert not c.service.deliver_notification()
        assert send.call_count == 1
    assert c.store.notification(note['id'])['state'] == 'unknown'
    assert not _restart(c).deliver_notification()


@pytest.mark.parametrize('fail', [False, True])
def test_after_work_sweep_recovers_only_missing_queueing_once(case, fail):
    c, work = _telegram(case)
    _settings_draft(c, work)
    _finish(c, work, fail=fail)  # stop after the terminal commit, before _after_work
    assert not c.store.notification_of(work, 'settings_change_proposed')
    service = _restart(c)
    assert service.sweep_after_work() == 1
    note = c.store.notification_of(work, 'settings_change_proposed')
    assert note['state'] == 'queued'
    assert service.sweep_after_work(now=time.time() + 61) == 0
    assert service.deliver_notification()
    assert not service.deliver_notification()
    assert len(_sent(c)) == 1
    assert len(c.store.task_notifications(work)) == 1


def test_completed_after_work_is_not_swept(case):
    c, work = _telegram(case)
    _settings_draft(c, work)
    run = _finish(c, work)
    c.service._after_work(run)
    service = _restart(c)
    with mock.patch.object(service, 'queue_settings_confirmation') as queue:
        assert service.sweep_after_work() == 0
        queue.assert_not_called()


@pytest.mark.parametrize('excluded', ['legacy', 'cancelled', 'forgotten', 'settled', 'old'])
def test_after_work_sweep_does_not_reopen_owner_settled_or_legacy_work(case, excluded):
    c, work = _telegram(case)
    _settings_draft(c, work)
    if excluded != 'legacy':
        _finish(c, work)
    if excluded == 'cancelled':
        with c.store.db() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (work,))
    elif excluded == 'forgotten':
        with c.store.db() as db:
            db.execute("INSERT INTO forgotten_messages(message_id,receipt_id,created) SELECT id,'fixture',? FROM messages WHERE job_id=?",
                       (time.time(), work))
    elif excluded == 'settled':
        c.service.queue_settings_confirmation(c.store.job(work))
        note = c.store.notification_of(work, 'settings_change_proposed')
        c.store.update_notification(note['id'], 'settings_canceled')
    elif excluded == 'old':
        with c.store.db() as db:
            db.execute("UPDATE tool_events SET created=? WHERE job_id=? AND tool='after_work'", (time.time()-8*86400, work))
    service = _restart(c)
    service.sweep_after_work()
    note = c.store.notification_of(work, 'settings_change_proposed')
    assert note is None or note['state'] == 'settings_canceled'
    assert not service.deliver_notification()


def test_folded_parse_refusal_then_not_sent_is_safe_to_retry(case):
    c, work = _telegram(case)
    _settings_draft(c, work)
    c.service.queue_settings_confirmation(c.store.job(work))
    note = c.store.notification_of(work, 'settings_change_proposed')
    # The folded path is selected by preparation proposals; the binding and
    # text are presentation seams, while the send/receipt path stays real.
    with c.store.db() as db:
        db.execute("UPDATE telegram_notifications SET kind='preparation_proposed' WHERE id=?", (note['id'],))
    with mock.patch('personal_agent.telegram_surface.notice_binding', return_value={}), \
         mock.patch.object(c.service, 'preparation_proposal_html', return_value='<b>offer</b>'), \
         mock.patch('personal_agent.telegram_surface.notice_text', return_value='offer'), \
         mock.patch.object(c.service.telegram, 'send_message', side_effect=[
             TelegramRejected(400, "can't parse entities"), TelegramNotSent('offline')]) as send:
        assert c.service.deliver_notification()
        assert send.call_count == 2
    assert c.store.notification(note['id'])['state'] == 'queued'
    with mock.patch('personal_agent.telegram_surface.notice_binding', return_value={}), \
         mock.patch.object(c.service, 'preparation_proposal_html', return_value='<b>offer</b>'), \
         mock.patch('personal_agent.telegram_surface.notice_text', return_value='offer'), \
         mock.patch('personal_agent.telegram_surface.time.monotonic', return_value=c.service._unsent_retry_at):
        assert c.service.deliver_notification()
    assert c.store.notification(note['id'])['state'] == 'sent'
    assert len(_sent(c)) == 1


def test_notice_cut_off_while_sending_is_not_replayed(case):
    c, work = _telegram(case)
    note = c.store.queue_notification(work, 42, 'g', 'completed')
    with mock.patch.object(c.service.telegram, 'send_message', side_effect=RuntimeError('process cut off')):
        with pytest.raises(RuntimeError):
            c.service.deliver_notification()
    assert c.store.notification(note['id'])['state'] == 'sending'
    service = _restart(c)
    assert c.store.notification(note['id'])['state'] == 'unknown'
    assert not service.deliver_notification()


def test_sweep_repeats_partial_queueing_without_duplicating_notice(case):
    c, work = _telegram(case)
    _settings_draft(c, work)
    run = _finish(c, work)
    with mock.patch.object(c.service, 'offer_budget_continue', side_effect=RuntimeError('cut off after queues')):
        with pytest.raises(RuntimeError):
            c.service._after_work(run)
    note = c.store.notification_of(work, 'settings_change_proposed')
    service = _restart(c)
    assert service.sweep_after_work() == 1
    assert c.store.notification_of(work, 'settings_change_proposed')['id'] == note['id']
    assert len(c.store.task_notifications(work)) == 1
    assert service.deliver_notification()
    assert not service.deliver_notification()
    assert len(_sent(c)) == 1
