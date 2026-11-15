"""#842 D2: fixture Telegram wires and real SQLite delivery/restart boundaries."""
import json
import threading
import time
from contextlib import contextmanager
from unittest import mock

import pytest

from personal_agent.conversation_handoff import TelegramNotSent, TelegramRejected
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.providers import ProviderError
from personal_agent.quickstart_service import AgentService, BROWSER_LOGIN_SECONDS
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


def test_after_work_marker_never_shows_in_the_owner_trail(case):
    c, work = _telegram(case)
    run = _finish(c, work)
    c.service._after_work(run)
    with c.store.db() as db:
        tools = [row['tool'] for row in db.execute('SELECT tool FROM tool_events WHERE job_id=?', (work,))]
    assert 'after_work' in tools
    assert all(step[0] != 'after_work' for step in c.service.work_steps(work))
    assert 'after_work' in c.service.LOCAL_NON_TOOL_EVENTS


def _marker(store, work):
    with store.db() as db:
        return dict(db.execute("SELECT * FROM tool_events WHERE job_id=? AND tool='after_work' ORDER BY id DESC", (work,)).fetchone())


@pytest.mark.parametrize('age', [10, BROWSER_LOGIN_SECONDS, 2 * 86400])
def test_sweep_login_respects_requested_age_and_opens_once(case, age):
    c, work = _telegram(case)
    _finish(c, work)
    now = time.time()
    login = {'state': 'requested', 'requested_at': now - age, 'explicit': True,
             'nonce': 'same-nonce', 'url': 'https://example.com/', 'host': 'example.com'}
    c.service._put_browser_login(work, login)
    service = _restart(c)
    with mock.patch.object(service, '_login_cookie_marks', return_value={}), \
         mock.patch.object(service.browser_profile, 'open_for_login', return_value={'state': 'opening', 'window': 'fixture'}) as opened, \
         mock.patch.object(service, 'offer_browser_login', wraps=service.offer_browser_login) as offer:
        assert service.sweep_after_work(now=now) == 1
        assert service.sweep_after_work(now=now + 61) == 0
        assert opened.call_count == offer.call_count == int(age < BROWSER_LOGIN_SECONDS)
    row = service._browser_login(work)
    assert row['nonce'] == login['nonce']
    assert row['requested_at'] == login['requested_at']
    if age >= BROWSER_LOGIN_SECONDS:
        assert row == login  # expiry remains the normal reconciler's job
        service.process_browser_logins()
        assert service._browser_login(work)['state'] == 'expired'


@pytest.mark.parametrize('approved', [False, True])
def test_sweep_context_approval_uses_current_attachment(case, approved):
    c, work = _telegram(case)
    c.store.put('model', {'endpoint': 'https://model.example/v1', 'model': 'fixture'})
    c.store.attach_context(work, ['fixture-context'], c.service.context_assistant_id())
    if approved:c.store.approve_context_attachment(work)
    _finish(c, work)
    with c.store.db() as db:
        db.execute("UPDATE tool_events SET detail=? WHERE job_id=? AND tool='after_work'",
                   (json.dumps({'context_approval_needed': True}), work))
    assert not c.store.notification_of(work, 'context_approval_needed')
    service = _restart(c)
    assert service.sweep_after_work() == 1
    note = c.store.notification_of(work, 'context_approval_needed')
    assert bool(note) is not approved
    assert service.sweep_after_work(now=time.time() + 61) == 0
    if not approved:
        assert note['state'] == 'queued'
        assert service.deliver_notification()
        assert c.store.notification(note['id'])['state'] == 'sent'
        assert not service.deliver_notification()
        assert len(_sent(c)) == 1
        assert not c.store.context_attachment(work)['approved']


def test_sweep_context_prompt_is_cancelled_if_assistant_changes(case):
    c, work = _telegram(case)
    c.store.put('model', {'endpoint': 'https://model.example/v1', 'model': 'fixture'})
    c.store.attach_context(work, ['fixture-context'], c.service.context_assistant_id())
    attachment = c.store.context_attachment(work)
    _finish(c, work)
    with c.store.db() as db:
        db.execute("UPDATE tool_events SET detail=? WHERE job_id=? AND tool='after_work'",
                   (json.dumps({'context_approval_needed': True}), work))
    service = _restart(c)
    assert service.sweep_after_work() == 1
    note = c.store.notification_of(work, 'context_approval_needed')
    assert note['state'] == 'queued'
    c.store.put('model', {'endpoint': 'https://other.example/v1', 'model': 'fixture'})
    assert service.deliver_notification()
    assert c.store.notification(note['id'])['state'] == 'cancelled'
    assert not service.deliver_notification()
    assert not _sent(c)
    assert c.store.context_attachment(work) == attachment


def test_sweep_preparation_proposal_uses_existing_proposal(case):
    c, work = _telegram(case)
    proposal = c.service.preparations.create(kind='reminder', goal='fixture reminder', due_at=time.time() + 3600,
        timezone='Asia/Seoul', recurrence=None, channel='telegram', created_from=work, state='proposed')
    _finish(c, work)
    service = _restart(c)
    assert service.sweep_after_work() == 1
    note = c.store.notification_of(work, 'preparation_proposed')
    assert note['state'] == 'queued'
    assert [p['id'] for p in service.offered_proposals(note)[0]] == [proposal['id']]
    assert all(service.preparations.get(proposal['id'])[key] == value for key, value in proposal.items())
    assert service.deliver_notification()
    assert not service.deliver_notification()


@pytest.mark.parametrize('expired', [False, True])
def test_sweep_budget_offer_preserves_nonce_and_absolute_deadline(case, expired):
    c, work = _telegram(case)
    _finish(c, work)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='partial' WHERE id=?", (work,))
    budget = c.service.work_budget(work)
    budget.spend_attempt()
    budget.ledger.pause()
    offer = {'state': 'offered', 'nonce': 'original-nonce', 'deadline': time.time() + (-1 if expired else 3600),
             'ledger_key': budget.ledger.key, 'epoch': c.store.config(budget.ledger.key)['epoch'],
             'generation': c.service._owner_generation(c.service.issuer_owner_id(c.store.job(work))),
             'text': 'Continue the fixture?', 'progress': 'fixture progress'}
    c.store.put('budget_continue:' + work, offer)
    service = _restart(c)
    assert service.sweep_after_work() == 1
    note = c.store.notification_of(work, 'budget_continue')
    assert bool(note) is not expired
    assert service.budget_continue_offer(work) == offer
    if not expired:
        assert note['fingerprint'] == offer['nonce']
        assert service.deliver_notification()
        assert not service.deliver_notification()


def test_sweep_releases_pending_budget_hold_and_photo(case):
    c, work = _telegram(case)
    c.store.attach_telegram_photo(work, 'fixture-photo')
    job = c.store.job(work)
    run = WorkRun(job, time.time())
    run.outcome = 'partial'
    run.response = 'fixture partial'
    with c.store.db() as db:
        db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)",
                   (work, 'model', 'stopped', json.dumps({'code': 'turn_budget'}), time.time()))
    c.service._finish_work(run)
    assert c.service.budget_continue_offer(work) == {'state': 'pending'}
    assert c.store.telegram_photo_file_ids(work) == ['fixture-photo']
    service = _restart(c)
    with mock.patch.object(service, '_offer_budget_continue', side_effect=AssertionError('must not mint an offer')):
        assert service.sweep_after_work() == 1
    assert service.budget_continue_offer(work) == {'state': 'none'}
    assert c.store.telegram_photo_file_ids(work) == []
    assert not c.store.notification_of(work, 'budget_continue')


def test_sweep_expired_settings_draft_is_not_queued(case):
    c, work = _telegram(case)
    with mock.patch.object(c.service.settings_orchestrator, 'TTL_SECONDS', -1):
        _settings_draft(c, work)
    _finish(c, work)
    assert _restart(c).sweep_after_work() == 1
    assert not c.store.notification_of(work, 'settings_change_proposed')


def test_sweep_keeps_worker_exclusion_without_holding_service_lock(case):
    c, work = _telegram(case)
    _finish(c, work)
    c.service._put_browser_login(work, {'state': 'requested', 'requested_at': time.time(), 'explicit': True,
        'nonce': 'same', 'url': 'https://example.com/', 'host': 'example.com'})
    service = _restart(c)
    entered, release, claimed = threading.Event(), threading.Event(), threading.Event()
    def slow_cookies(row):
        entered.set()
        assert release.wait(5)
        return {}
    with mock.patch.object(service, '_login_cookie_marks', side_effect=slow_cookies), \
         mock.patch.object(service.browser_profile, 'open_for_login', return_value={'state': 'opening'}), \
         mock.patch.object(service, '_claim_queued_work', side_effect=lambda: claimed.set() or False):
        sweep = threading.Thread(target=service.sweep_after_work)
        worker = threading.Thread(target=service._run_one)
        sweep.start()
        try:
            assert entered.wait(5)
            assert service.lock.acquire(timeout=1)
            service.lock.release()
            assert not service.worker_lock.acquire(blocking=False)
            worker.start()
            assert not claimed.wait(.1)
        finally:
            release.set()
            sweep.join(5)
            if worker.ident is not None:worker.join(5)
        assert claimed.is_set()
        assert not sweep.is_alive() and not worker.is_alive()


@pytest.mark.parametrize('failure', ['job', 'forgotten', 'stopped', 'settlement'])
def test_sweep_contains_row_failures_and_bounds_attempts(case, failure):
    c, work = _telegram(case)
    _finish(c, work)
    service = _restart(c)
    original_db = service.store.db
    @contextmanager
    def failing_db():
        with original_db() as db:
            class Connection:
                def execute(self, sql, args=()):
                    if (failure == 'forgotten' and 'JOIN forgotten_messages' in sql or
                            failure == 'settlement' and "SET status='completed'" in sql):
                        raise RuntimeError('fixture row failure')
                    return db.execute(sql, args)
            yield Connection()
    target = service.store if failure == 'job' else service
    with mock.patch.object(service.store, 'db', side_effect=failing_db), \
         mock.patch.object(target, 'job' if failure == 'job' else 'work_stopped',
                           **({'side_effect': RuntimeError('fixture row failure')} if failure in ('job', 'stopped') else {'return_value': False})):
        now = time.time()
        for attempt in range(3):
            assert service.sweep_after_work(now=now + attempt * 61) == 0
            assert json.loads(_marker(c.store, work)['detail'])['attempts'] == attempt + 1
    assert _marker(c.store, work)['status'] == 'failed'
    assert service.sweep_after_work(now=now + 183) == 0


def test_fifty_poisoned_markers_do_not_starve_newer_work(case):
    c, work = _telegram(case)
    _finish(c, work)
    # Fifty separate terminal Works ahead of a healthy one.
    bad = [work]
    for index in range(49):
        job = c.store.enqueue('fixture', 'poison-' + str(index))
        bad.append(job)
    for job in bad[1:]:_finish(c, job)
    newer = c.store.enqueue('fixture', 'healthy', channel='telegram:g', chat_id=42)
    _settings_draft(c, newer)
    _finish(c, newer)
    service = _restart(c)
    original = service.store.job
    def job_read(job):
        if job in bad:raise RuntimeError('poison')
        return original(job)
    with mock.patch.object(service.store, 'job', side_effect=job_read):
        now = time.time()
        for attempt in range(3):assert service.sweep_after_work(now=now + attempt * 61) == 0
        assert service.sweep_after_work(now=now + 183) == 1
    assert all(_marker(c.store, job)['status'] == 'failed' for job in bad)
    assert c.store.notification_of(newer, 'settings_change_proposed')['state'] == 'queued'


def test_start_continues_when_startup_sweep_fails(case):
    c, _ = _telegram(case)
    with mock.patch.object(c.service, 'sweep_after_work', side_effect=RuntimeError('fixture startup failure')) as sweep, \
         mock.patch('personal_agent.quickstart_service.threading.Thread') as thread, \
         mock.patch('personal_agent.quickstart_service.bridge_sdk_problem', return_value=None):
        c.service.start()
        sweep.assert_called_once_with()
        assert thread.call_count == 4
        assert thread.return_value.start.call_count == 4


def test_anchor_fallback_failure_keeps_loop_alive_and_recovery_never_resends(case):
    c, work = _telegram(case)
    original_db = c.store.db
    @contextmanager
    def failing_fallback():
        with original_db() as db:
            class Connection:
                def execute(self, sql, args=()):
                    if "SET delivery='unknown'" in sql:raise RuntimeError('fallback unavailable')
                    return db.execute(sql, args)
            yield Connection()
    c.service.presence[work] = {'fixture': True}
    with mock.patch.object(c.service.telegram_turns, 'record_reply', side_effect=RuntimeError('anchor unavailable')), \
         mock.patch.object(c.store, 'db', side_effect=failing_fallback), \
         mock.patch.object(c.service, '_present_outcome') as present:
        c.service.deliver_one()
        assert present.call_args.kwargs['delivered'] is False
    assert work not in c.service.presence
    assert c.store.job(work)['delivery'] == 'sending'
    service = _restart(c)
    assert c.store.job(work)['delivery'] == 'unknown'
    service.deliver_one()
    assert len(_sent(c)) == 1


@pytest.mark.parametrize('detail', ['broken json', '[]', '{"attempts":"invalid"}'])
def test_sweep_unreadable_marker_is_terminal(case, detail):
    c, work = _telegram(case)
    _finish(c, work)
    with c.store.db() as db:
        db.execute("UPDATE tool_events SET detail=? WHERE job_id=? AND tool='after_work'", (detail, work))
    assert _restart(c).sweep_after_work() == 0
    assert _marker(c.store, work)['status'] == 'failed'
