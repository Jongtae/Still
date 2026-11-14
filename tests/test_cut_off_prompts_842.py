"""#842 D1 negative tests: a cut-off prompt is offered once more, never answered by an old message.

Model-free, temporary stores, injected Telegram/Mattermost transports.  Fixtures come from
the recovery matrix (#826) and the surface suites they reuse.
"""
import time

import pytest

from personal_agent.conversation_surface import (
    DELIVERY_UNKNOWN_KIND, DELIVERY_UNKNOWN_TELL_SECONDS, notice_binding, OWNER_PROMPT_KINDS, REOFFERED_KEY)
from personal_agent.quickstart_service import PAYMENT_APPROVAL_SECONDS

import test_budget_continue as budget_cases
import test_mattermost_surface as mm_cases
from test_recovery_matrix_826 import (  # noqa: F401  (case, env are pytest fixtures)
    case, env, _payment, _recording_telegram, _restart, _restart_wired, _status, _telegram)

QUIET_KEYS = {REOFFERED_KEY, 'last_recovery', 'telegram_status'}


def _sent_texts(calls):
    return [x[1]['text'] for x in calls if x[0].endswith('/sendMessage')]


def _config_rows(store):
    with store.db() as db:
        return {k: v for k, v in db.execute('SELECT key,value FROM config') if k not in QUIET_KEYS}


def _settings_prompt(case):
    """A Telegram Work with one settings draft whose confirmation prompt was cut off while sending."""
    c, work = _telegram(case, 'partial', 'sent')
    job = c.store.job(work)
    draft = c.service.settings_orchestrator.propose(
        c.service.settings_owner(job), job['channel'], 'current_context', 'timezone', 'Asia/Tokyo', work_id=work)
    c.service.queue_settings_confirmation(job)
    note = c.store.notification_of(work, 'settings_change_proposed')
    c.store.update_notification(note['id'], 'sending')
    return c, work, draft, note


def _tap(service, data, message_id, ident='tap'):
    service.ingest_callback({'id': f'{ident}-{message_id}', 'from': {'id': service.store.config('telegram')['user_id']},
        'message': {'chat': {'id': service.store.config('telegram')['user_id'], 'type': 'private'},
                    'message_id': message_id}, 'data': data}, 'g')


def _draft_state(service, draft):
    return service.store.config('settings_change_drafts')[draft['draft_id']]['state']


def _budget_prompt(case):
    """A Telegram Work whose budget_continue prompt was cut off while sending (old message 123)."""
    c = case(budget_cases.BudgetContinueTests)
    service, store, job, run, _ = c.stopped(channel='telegram:g', chat_id=77)
    transport, sends = _recording_telegram(store, user_id=77)
    assert service.offer_budget_continue(run)
    note = store.notification_of(job, 'budget_continue')
    store.update_notification(note['id'], 'sending')
    return c, store, job, note, transport, sends


def test_1a_old_settings_button_settles_nothing_new_one_applies_the_same_offer(case):
    c, work, draft, note = _settings_prompt(case)
    fingerprint = note['fingerprint']
    service = _restart(c.store, c.service)
    assert service.settle_unknown_deliveries()[0] == 1
    queued = service.store.notification(note['id'])
    assert (queued['state'], queued['message_id'], queued['sent_at']) == ('queued', None, None)
    _tap(service, f"p7s:{note['id']}:confirm", 201)
    assert _draft_state(service, draft) == 'awaiting-confirmation'
    assert service.deliver_notification()
    new = service.store.notification(note['id'])
    assert new['fingerprint'] == fingerprint and new['message_id'] not in (None, 201)
    _tap(service, f"p7s:{note['id']}:confirm", 201)
    assert _draft_state(service, draft) == 'awaiting-confirmation'
    _tap(service, f"p7s:{note['id']}:confirm", new['message_id'])
    assert _draft_state(service, draft) == 'applied'
    assert service.context_observations.settings()['timezone'] == 'Asia/Tokyo'


def test_1b_old_budget_button_settles_nothing_new_one_continues_the_same_offer(case):
    c, store, job, note, transport, sends = _budget_prompt(case)
    nonce = note['fingerprint']
    service = _restart_wired(store, transport)
    assert service.settle_unknown_deliveries()[0] == 1
    queued = service.store.notification(note['id'])
    assert (queued['state'], queued['message_id'], queued['sent_at']) == ('queued', None, None)
    assert service.deliver_notification()
    new = service.store.notification(note['id'])
    assert (new['state'], new['fingerprint']) == ('sent', nonce) and new['message_id'] not in (None, 123)
    data = service.telegram_surface._notice_markup(new, notice_binding(service, new))['inline_keyboard'][0][0]['callback_data']
    _tap(service, data, 123)
    assert service.store.job(job)['status'] == 'partial'
    assert service.budget_continue_offer(job)['state'] == 'offered'
    _tap(service, data, new['message_id'])
    assert service.store.job(job)['status'] == 'queued'
    assert service.budget_continue_offer(job)['nonce'] == nonce


def test_2_the_re_offer_is_cut_off_again_the_owner_is_told_not_offered_a_third_time(case):
    c, work, draft, note = _settings_prompt(case)
    service = _restart(c.store, c.service)
    assert service.settle_unknown_deliveries() == (1, 0)
    # The re-offer reaches Telegram, but its receipt is lost too.
    service.store.update_notification(note['id'], 'sending')
    assert service.store.notification(note['id'])['message_id'] is None
    service = _restart(c.store, c.service)
    assert service.store.notification(note['id'])['state'] == 'unknown'
    assert service.settle_unknown_deliveries() == (0, 1)
    assert service.store.notification(note['id'])['state'] == 'unknown'
    assert service.store.notification_of(work, DELIVERY_UNKNOWN_KIND)['state'] == 'queued'
    assert service.deliver_notification()
    texts = _sent_texts(c.calls)
    assert len(texts) == 1
    assert service.settle_unknown_deliveries() == (0, 0)
    assert not service.deliver_notification()
    assert len(_sent_texts(c.calls)) == 1
    assert _draft_state(service, draft) == 'awaiting-confirmation'


def test_3_an_expired_browser_step_is_not_shown_and_nothing_is_approved(case):
    c = _payment(case)
    transport, sends = _recording_telegram(c.store)
    work = c.job['id']
    note = c.store.queue_notification(work, 42, 'g', 'browser_approval_needed',
                                      c.service._browser_request(work)['offer_digest'])
    c.store.update_notification(note['id'], 'sending')
    service = _restart_wired(c.store, transport)
    row = service._browser_request(work)
    assert service._put_browser_request(work, {**row, 'requested_at': row['requested_at'] - PAYMENT_APPROVAL_SECONDS - 5})
    before = _config_rows(c.store)
    assert service.settle_unknown_deliveries()[0] == 1
    assert service.deliver_notification()
    assert not sends
    assert service.store.notification(note['id'])['state'] == 'cancelled'
    assert service._browser_request(work)['state'] == 'requested'
    assert _config_rows(c.store) == before
    assert not [e for e in service.store.task_events(work) if e['tool'] == 'action_continuation']
    assert service.settle_unknown_deliveries() == (0, 0)


def test_4_answered_on_the_web_before_the_re_offer_is_delivered_cancels_it(case):
    c, work, draft, note = _settings_prompt(case)
    service = _restart(c.store, c.service)
    assert service.settle_unknown_deliveries()[0] == 1
    job = service.store.job(work)
    row = service.settings_orchestrator.confirm(service.settings_owner(job), job['channel'], draft['draft_id'], draft['digest'])
    assert row
    assert _draft_state(service, draft) == 'applied'
    assert service.deliver_notification()
    assert service.store.notification(note['id'])['state'] == 'cancelled'
    assert not _sent_texts(c.calls)


def test_5_the_re_offer_leaves_the_offer_itself_byte_identical(case):
    c, work, draft, note = _settings_prompt(case)
    service = _restart(c.store, c.service)
    before = _config_rows(c.store)
    jobs = [dict(j) for j in service.store.jobs()]
    assert service.settle_unknown_deliveries()[0] == 1
    assert service.deliver_notification()
    assert _config_rows(c.store) == before
    assert [dict(j) for j in service.store.jobs()] == jobs
    # the payment offer too (browser_requests) and the budget offer (budget_continue:<work>)
    p = _payment(case)
    transport, sends = _recording_telegram(p.store)
    pnote = p.store.queue_notification(p.job['id'], 42, 'g', 'browser_approval_needed',
                                      p.service._browser_request(p.job['id'])['offer_digest'])
    p.store.update_notification(pnote['id'], 'sending')
    pservice = _restart_wired(p.store, transport)
    pbefore = _config_rows(p.store)
    assert pservice.settle_unknown_deliveries()[0] == 1 and pservice.deliver_notification() and len(sends) == 1
    assert _config_rows(p.store) == pbefore
    b, store, job, bnote, btransport, bsends = _budget_prompt(case)
    bservice = _restart_wired(store, btransport)
    bbefore = _config_rows(store)
    assert bservice.settle_unknown_deliveries()[0] == 1 and bservice.deliver_notification() and len(bsends) == 1
    assert _config_rows(store) == bbefore


@pytest.mark.parametrize('state', ['browser_approved', 'budget_continue_answered', 'settings_confirmed', 'cancelled'])
def test_6_answered_prompts_are_never_requeued(case, state):
    c, work = _telegram(case, 'partial', 'sent')
    rows = {}
    for kind in OWNER_PROMPT_KINDS:
        rows[kind] = c.store.queue_notification(work, 42, 'g', kind, 'binding-' + kind)['id']
        c.store.update_notification(rows[kind], state, 55)
    service = _restart(c.store, c.service)
    assert service.settle_unknown_deliveries() == (0, 0)
    assert not service.deliver_notification()
    assert {service.store.notification(i)['state'] for i in rows.values()} == {state}
    assert not _sent_texts(c.calls)


def test_7_an_unknown_approval_needed_row_is_cancelled_not_sent(case):
    c, work = _telegram(case, 'partial', 'sent')
    note = c.store.queue_notification(work, 42, 'g', 'approval_needed', 'old-consent')
    c.store.update_notification(note['id'], 'sending')
    service = _restart(c.store, c.service)
    assert service.settle_unknown_deliveries() == (1, 0)
    assert service.deliver_notification()
    assert service.store.notification(note['id'])['state'] == 'cancelled'
    assert not _sent_texts(c.calls)


def test_8_an_unknown_reply_is_told_once_never_resent_and_stays_unknown(case):
    c, work = _telegram(case, 'failed', 'sending')
    service = _restart(c.store, c.service)
    service.deliver_one()
    assert service.store.job(work)['delivery'] == 'unknown'
    assert service.store.recovery_summary()['uncertain_deliveries'] == 1
    assert service.settle_unknown_deliveries() == (0, 1)
    assert service.deliver_notification()
    texts = _sent_texts(c.calls)
    assert len(texts) == 1 and 'fixture request' in texts[0]
    service.deliver_one()
    assert service.settle_unknown_deliveries() == (0, 0)
    assert not service.deliver_notification()
    assert len(_sent_texts(c.calls)) == 1
    assert service.store.job(work)['delivery'] == 'unknown'
    assert not service.safe_retry(service.store.job(work))[0]
    assert service.store.recovery_summary()['uncertain_deliveries'] == 0
    assert service.store.notification_of(work, DELIVERY_UNKNOWN_KIND)['state'] == 'sent'


def test_9_a_work_older_than_a_day_is_not_told(case):
    c, work = _telegram(case, 'failed', 'unknown')
    service = _restart(c.store, c.service)
    assert service.settle_unknown_deliveries(now=time.time() + 25 * 3600) == (0, 0)
    assert service.store.notification_of(work, DELIVERY_UNKNOWN_KIND) is None
    assert service.settle_unknown_deliveries(now=time.time() + 23 * 3600) == (0, 1)


def test_10_a_web_work_with_unknown_delivery_queues_nothing_and_does_not_raise(env):
    store, service = env
    work = store.enqueue('web request', 'original')
    _status(store, work, 'failed', 'unknown')
    assert service.settle_unknown_deliveries() == (0, 0)
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM telegram_notifications').fetchone()[0] == 0
    assert store.job(work)['delivery'] == 'unknown'


class MattermostCutOff(mm_cases.OwnerActs):
    """11: the sweep is surface-generic; a Mattermost prompt cut off while sending is re-offered the same way."""

    def test_a_cut_off_settings_prompt_on_mattermost_is_offered_once_more(self):
        job, draft_id, notice = self.settings_notice()
        old = notice['message_id']
        # Legacy unknown row retaining a message id and send time.
        self.store.update_notification(notice['id'], 'unknown')
        self.store.recover()
        self.assertEqual(self.store.notification(notice['id'])['state'], 'unknown')
        self.assertEqual(self.service.settle_unknown_deliveries(), (1, 0))
        queued = self.store.notification(notice['id'])
        self.assertEqual((queued['state'], queued['message_id'], queued['sent_at']), ('queued', None, None))
        self.fake.react(mm_cases.OWNER, old, '+1')
        self.surface.receive()
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'awaiting-confirmation')
        before = len(self.fake.bot_posts())
        self.assertTrue(self.service.deliver_notification())
        new = self.store.notification(notice['id'])
        self.assertEqual((new['state'], new['surface']), ('sent', 'mattermost'))
        self.assertNotEqual(new['message_id'], old)
        self.assertEqual(len(self.fake.bot_posts()), before + 1)
        self.fake.react(mm_cases.OWNER, old, '+1')
        self.surface.receive()
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'awaiting-confirmation')
        self.fake.react(mm_cases.OWNER, new['message_id'], '+1')
        self.surface.receive()
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'applied')
        self.assertEqual(self.service.settle_unknown_deliveries(), (0, 0))


def test_11_the_sweep_is_surface_generic(case):
    c = case(MattermostCutOff)
    c.test_a_cut_off_settings_prompt_on_mattermost_is_offered_once_more()


def _context_prompt(case):
    c, work = _telegram(case, 'failed', 'sent')
    c.store.put('model', {'endpoint': 'https://model.example/v1', 'model': 'fixture'})
    c.store.attach_context(work, ['fixture-context'], c.service.context_assistant_id())
    note = c.store.queue_notification(work, 42, 'g', 'context_approval_needed')
    c.store.update_notification(note['id'], 'sending')
    return c, work, note


@pytest.mark.parametrize('kind', ['context_approval_needed', 'settings_change_proposed', 'preparation_proposed'])
def test_old_unknown_prompts_stay_unknown_without_sends(case, kind):
    if kind == 'context_approval_needed':
        c, work, note = _context_prompt(case)
    else:
        c, work = _telegram(case, 'partial', 'sent')
        note = c.store.queue_notification(work, 42, 'g', kind, 'legacy-offer')
        c.store.update_notification(note['id'], 'unknown', 201)
    service = _restart(c.store, c.service)
    # Work age, not the notification's creation time, governs both sweep paths.
    with c.store.db() as db:
        db.execute('UPDATE jobs SET created=? WHERE id=?', (time.time() - 90 * 86400, work))
    before = c.store.notification(note['id'])
    assert service.settle_unknown_deliveries() == (0, 0)
    assert service.store.notification(note['id']) == before
    assert not service.deliver_notification()
    assert not _sent_texts(c.calls)
    assert service.store.notification_of(work, DELIVERY_UNKNOWN_KIND) is None
    assert service.store.recovery_summary()['uncertain_notifications'] == 1


@pytest.mark.parametrize('offset, expected', [(-1, 0), (0, 1), (1, 1)])
def test_prompt_reoffer_uses_the_shared_work_age_boundary(case, offset, expected):
    c, work, draft, note = _settings_prompt(case)
    service = _restart(c.store, c.service)
    now = time.time()
    with c.store.db() as db:
        db.execute('UPDATE jobs SET created=? WHERE id=?', (now - DELIVERY_UNKNOWN_TELL_SECONDS + offset, work))
    assert service.settle_unknown_deliveries(now=now) == (expected, 0)
    assert service.store.notification(note['id'])['state'] == ('queued' if expected else 'unknown')


@pytest.mark.parametrize('change', ['approved', 'missing', 'assistant_changed', 'local_model'])
def test_context_offer_invalidated_before_delivery_is_cancelled(case, change):
    c, work, note = _context_prompt(case)
    service = _restart(c.store, c.service)
    assert service.settle_unknown_deliveries() == (1, 0)
    assert notice_binding(service, service.store.notification(note['id'])) == {}
    if change == 'approved':
        c.store.approve_context_attachment(work)
    elif change == 'missing':
        with c.store.db() as db:
            db.execute('DELETE FROM context_job_attachments WHERE job_id=?', (work,))
    else:
        model = {'endpoint': 'http://localhost/v1', 'model': 'fixture'} if change == 'local_model' else {'endpoint': 'https://other.example/v1'}
        c.store.put('model', model)
        if change == 'local_model':
            with c.store.db() as db:
                db.execute('UPDATE context_job_attachments SET assistant_id=? WHERE job_id=?',
                           (service.context_assistant_id(), work))
    assert service.deliver_notification()
    assert service.store.notification(note['id'])['state'] == 'cancelled'
    assert not _sent_texts(c.calls)
    assert service.store.job(work)['status'] == 'failed'


def test_legacy_context_message_is_cleared_before_send_and_only_new_button_approves(case):
    c, work, note = _context_prompt(case)
    c.store.update_notification(note['id'], 'unknown', 201, delivered=True)
    legacy = c.store.notification(note['id'])
    assert legacy['message_id'] == 201 and legacy['sent_at'] is not None
    service = _restart(c.store, c.service)
    before = c.store.context_attachment(work)
    assert service.settle_unknown_deliveries() == (1, 0)
    queued = c.store.notification(note['id'])
    assert (queued['state'], queued['message_id'], queued['sent_at']) == ('queued', None, None)
    data = f"v1c:{note['id']}:approve"
    _tap(service, data, 201)
    assert c.store.context_attachment(work) == before
    assert c.store.job(work)['status'] == 'failed'
    assert service.deliver_notification()
    _tap(service, data, 201)
    assert c.store.context_attachment(work) == before
    new = c.store.notification(note['id'])
    _tap(service, data, new['message_id'])
    assert c.store.context_attachment(work)['approved'] == 1
    assert c.store.job(work)['status'] == 'queued'


@pytest.mark.parametrize('state', ['queued', 'sending', 'unknown', 'sent', 'settings_confirmed', 'cancelled', 'deleted'])
def test_reoffer_marks_keep_only_unsettled_rows(case, state):
    c, work, draft, note = _settings_prompt(case)
    service = _restart(c.store, c.service)
    assert service.settle_unknown_deliveries() == (1, 0)
    if state == 'deleted':
        with c.store.db() as db:
            db.execute('DELETE FROM telegram_notifications WHERE id=?', (note['id'],))
    else:
        c.store.update_notification(note['id'], state)
    service.settle_unknown_deliveries()
    marks = c.store.config(REOFFERED_KEY)
    assert (note['id'] in marks) == (state in ('queued', 'sending', 'unknown'))
    if state == 'unknown':
        assert c.store.notification(note['id'])['state'] == 'unknown'


@pytest.mark.parametrize('reason', ['pairing_changed', 'work_mode'])
def test_cancelled_reoffer_clears_uncertainty_without_notice_and_preserves_wait(case, reason):
    from personal_agent import work_mode
    c, work, draft, note = _settings_prompt(case)
    service = _restart(c.store, c.service)
    assert c.store.recovery_summary()['uncertain_notifications'] == 1
    assert service.settle_unknown_deliveries() == (1, 0)
    if reason == 'pairing_changed':
        c.store.put('telegram', {**c.store.config('telegram'), 'generation': 'new-pairing'})
    else:
        work_mode.set_enabled(c.store, True, surface='settings')
        work_mode.set_enabled(c.store, False, surface='settings')
    assert service.deliver_notification()
    assert c.store.notification(note['id'])['state'] == 'cancelled'
    assert not _sent_texts(c.calls)
    assert c.store.recovery_summary()['uncertain_notifications'] == 0
    assert service.settle_unknown_deliveries() == (0, 0)
    assert c.store.config(REOFFERED_KEY) == {}
    assert c.store.notification_of(work, DELIVERY_UNKNOWN_KIND) is None
    job = c.store.job(work)
    assert service.settings_orchestrator.confirm(service.settings_owner(job), job['channel'], draft['draft_id'], draft['digest'])
    assert _draft_state(service, draft) == 'applied'
