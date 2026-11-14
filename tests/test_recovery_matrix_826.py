"""#826 recovery spikes: real entry points, temporary SQLite stores, fixture wires.

The matrix's unsupported paths assert the desired invariant under strict xfail;
separate characterizations show the state actually persisted today. No start(),
network, live model, or runtime changes. Reuse review: N/A, tests only, reusing
existing service/store and fixture APIs without introducing runtime machinery.
"""
import json
import time
from unittest import mock

import pytest

from personal_agent.agent_runtime import (ToolError, WorkBudget, WorkLedger, ENGINE_UNMEDIATED,
                                         WORK_STOP_KEY, renew_for_owner_prerequisite)
from personal_agent.conversation_handoff import TelegramNotSent
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.work_execution import WorkRun
from personal_agent import trust_record, preparations as prep

# Import modules, not TestCase classes, so pytest collects only this file's rows.
import test_quickstart as quick
import test_connect_effects as effects
import test_connect_api_effects as api_effects
import test_owner_settings_conversation as settings_cases
import test_connect_mcp_continuation as mcp_cases
import test_connect_schedule as schedule_cases
import test_budget_continue as budget_cases
import test_mattermost_surface as mm_cases
import test_slack_surface as slack_cases


@pytest.fixture
def case(request):
    """Reuse unittest harness lifetimes without collecting their whole suites."""
    def make(cls):
        value = cls(methodName='runTest')
        request.addfinalizer(value.doCleanups)
        request.addfinalizer(value.tearDown)
        value.setUp()
        return value
    return make


@pytest.fixture
def env(tmp_path):
    store = QuickStore(tmp_path / 'state')
    service = AgentService(store)
    service.use_decision_engine(FixtureDecisionEngine())
    return store, service


def _status(store, work, status, delivery=None):
    with store.db() as db:
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, work))
        if delivery is not None:
            db.execute('UPDATE jobs SET delivery=? WHERE id=?', (delivery, work))


def _event(store, work, tool, status, **detail):
    with store.db() as db:
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (work, tool, status, json.dumps(detail), time.time()))


def _restart(store, old=None):
    restarted = AgentService(QuickStore(store.root),
                             telegram_transport=old.telegram_transport if old else None)
    restarted.use_decision_engine(FixtureDecisionEngine())
    restarted.recover_interrupted_work()
    return restarted


def _telegram(case, status='succeeded', delivery='pending'):
    c = case(quick.QuickstartTests)
    c.store.secret('telegram_token', '123456:TEST_TOKEN')
    c.store.put('telegram', {'enabled': True, 'generation': 'g', 'user_id': 42, 'cursor': 0})
    c.service.use_decision_engine(FixtureDecisionEngine())
    work = c.store.enqueue('fixture request', 'original', channel='telegram:g', chat_id=42)
    c.service.telegram_turns.record_source(work, 42, 101)
    _status(c.store, work, status, delivery)
    return c, work


def _payment(case):
    c = case(effects.EffectBoundary)
    trust_record.set_money(c.store, c.target, True)
    assert c.call()['behavior'] == 'deny'
    c.partial()
    return c


def _scheduled(env):
    """The schedule fixture's preparation setup without its unrelated HTTP UI."""
    store, service = env
    now = time.time()
    service.preparations.clock = lambda: now
    row = service.preparations.create(
        kind='prepare', goal='Read requested records', due_at=now, timezone='UTC',
        recurrence=None, channel='web', created_from='settings', state=prep.STATE_SCHEDULED,
        accepted_by=prep.ACCEPTED_OWNER_SETTINGS, window=(600, now + 3600, 1))
    work = service.preparations.start(row, channel='web', chat_id=None, now=now)
    return store, service, row, work, now


def test_q1_queued_request_claimed_once(env):
    """Q1 queued idempotent requests survive restart and are claimed once."""
    store, _ = env
    work = store.enqueue('hello', 'same')
    assert store.enqueue('hello', 'same') == work
    with pytest.raises(ValueError):
        store.enqueue('different', 'same')
    service = _restart(store)
    assert service.store.job(work)['status'] == 'queued'
    assert service._claim_queued_work()['id'] == work
    assert service._claim_queued_work() is False
    assert service.store.job(work)['status'] == 'running'


def test_q2_unknown_dispatch_withholds_guarded_continuation(case):
    """Q2 an unobserved connector dispatch cannot admit another guarded action."""
    c = _payment(case)
    assert c.approve()['resumed']
    assert c.call()['behavior'] == 'allow'
    c.service = _restart(c.store)
    assert c.call()['behavior'] == 'deny'
    assert c.service._work_has_unknown_effect(c.job['id'])
    assert c.service._browser_request(c.job['id']) is None


def test_q3_approval_receipt_and_queue_are_atomic_and_admitted_once(case):
    """Q3 approval persists one queued continuation despite repeated reconciliation."""
    c = _payment(case)
    assert c.approve()['resumed']
    restarted = _restart(c.store)
    restarted.resume_action_work()
    restarted.resume_action_work()
    assert restarted.store.job(c.job['id'])['status'] == 'queued'
    receipts = [e for e in restarted.store.task_events(c.job['id'])
                if e['tool'] == 'action_continuation' and e['status'] == 'queued']
    assert len(receipts) == 1
    assert receipts[0]['trace']['resumed']


def test_q4_telegram_cursor_commits_with_typed_work(case):
    """Q4 replaying a typed update after restart does not create another Work."""
    c, seed = _telegram(case)
    update = {'update_id': 12, 'message': {'message_id': 102, 'from': {'id': 42},
              'chat': {'id': 42, 'type': 'private'}, 'text': 'a fresh fixture request'}}
    c.service.ingest_update(update, 'g')
    before = {j['id'] for j in c.store.jobs()}
    restarted = _restart(c.store, c.service)
    restarted.ingest_update(update, 'g')
    assert restarted.store.config('telegram')['cursor'] == 13  # next update offset
    assert {j['id'] for j in restarted.store.jobs()} == before
    assert len(before - {seed}) == 1


def test_r1_running_without_dispatch_gets_one_interrupted_reply(case):
    """R1 claimed but undispatched Work is interrupted and safe for owner retry."""
    c, work = _telegram(case, 'running', 'none')
    service = _restart(c.store, c.service)
    assert service.store.job(work)['status'] == 'interrupted'
    assert service.store.job(work)['delivery'] == 'pending'
    assert service.safe_retry(service.store.job(work)) == (True, None)
    service.deliver_one()
    service.deliver_one()
    assert service.store.job(work)['delivery'] == 'sent'
    assert len([x for x in c.calls if x[0].endswith('/sendMessage')]) == 1
    assert not service.run_one()


@pytest.mark.parametrize('tool,status,detail,reason', [
    ('save_note', 'running', {'call_id': 'cut-off'}, 'unknown'),
    ('subscription_engine', 'running', {}, 'unaccounted'),
])
def test_r2_dispatch_markers_hold_resume_and_preserve_retry_policy(env, tool, status, detail, reason):
    """R2 a cut-off tool or CLI turn is not replayed after recovery."""
    store, _ = env
    work = store.enqueue('fixture', 'dispatch')
    _status(store, work, 'running')
    _event(store, work, tool, status, **detail)
    service = _restart(store)
    assert service.store.job(work)['status'] == 'interrupted'
    assert reason in service.continuation_reasons(work, 'resume')
    if tool == 'subscription_engine':
        # Matrix discrepancy: a bare running engine marker holds resume, but
        # retry only sees unmediated actions if their source was also recorded.
        assert service.continuation_reasons(work, 'retry') == ()
        assert service.safe_retry(service.store.job(work)) == (True, None)
    else:
        assert not service.safe_retry(service.store.job(work))[0]
    assert service.store.task_events(work)[0]['status'] == status
    assert not service.run_one()


def test_r3_receipt_does_not_recover_worker_reply(env):
    """R3 a durable changed-effect receipt does not settle an in-memory answer."""
    store, _ = env
    work = store.enqueue('fixture', 'receipt')
    _status(store, work, 'running')
    _event(store, work, 'browser_click', 'succeeded', call_id='done', effect='changed')
    service = _restart(store)
    assert service.store.job(work)['status'] == 'interrupted'
    assert not service.store.job(work)['response']
    assert 'effect' in service.continuation_reasons(work, 'resume')
    assert service.store.task_events(work)[0]['status'] == 'succeeded'


@pytest.mark.xfail(strict=True, reason='#826 R4: sending prompts lose actionable delivery state on restart')
def test_r4_sending_prompt_remains_actionable(case):
    """R4 a prompt already shown should remain actionable after restart."""
    c = _payment(case)
    note = c.store.queue_notification(c.job['id'], 42, 'g', 'browser_approval_needed',
                                      c.service._browser_request(c.job['id'])['offer_digest'])
    c.store.update_notification(note['id'], 'sending', 201)
    service = _restart(c.store)
    assert service.store.notification(note['id'])['state'] == 'sent'


def test_r4_unknown_prompt_callback_does_not_apply_settings(case):
    """R4 characterize a dead prompt: its callback cannot apply the draft."""
    c, work = _telegram(case, 'partial', 'sent')
    job = c.store.job(work)
    draft = c.service.settings_orchestrator.propose(
        c.service.settings_owner(job), job['channel'], 'current_context',
        'timezone', 'Asia/Tokyo', work_id=work)
    c.service.queue_settings_confirmation(job)
    note = c.store.notification_of(work, 'settings_change_proposed')
    c.store.update_notification(note['id'], 'sending', 201)
    service = _restart(c.store, c.service)
    service.ingest_callback({'id': 'dead-prompt', 'from': {'id': 42},
        'message': {'chat': {'id': 42, 'type': 'private'}, 'message_id': 201},
        'data': f"p7s:{note['id']}:confirm"}, 'g')
    assert service.store.notification(note['id'])['state'] == 'unknown'
    assert service.store.config('settings_change_drafts')[draft['draft_id']]['state'] == 'awaiting-confirmation'
    assert service.context_observations.settings()['timezone'] == ''


def test_w1_absolute_approval_expiry_withholds_on_restart(case):
    """W1 downtime expires an approval and persists a withheld continuation."""
    c = _payment(case)
    at = c.service._browser_request(c.job['id'])['requested_at']
    with mock.patch('personal_agent.quickstart_service.time.time', return_value=at + 601):
        service = _restart(c.store)
        service.resume_action_work()
        service.resume_action_work()
    receipts = [e for e in service.store.task_events(c.job['id'])
                if e['tool'] == 'action_continuation' and e['status'] == 'withheld']
    assert len(receipts) == 1
    assert service.store.job(c.job['id'])['status'] == 'partial'
    assert service.store.job(c.job['id'])['owner_cause']


def test_w2_applying_settings_settle_unknown_without_setter(case):
    """W2 an applying draft becomes unknown even if its setter never ran."""
    c = case(settings_cases._Case)
    draft = c.draft('current_context', 'timezone', 'Asia/Seoul')
    rows = c.store.config('settings_change_drafts')
    rows[draft['draft_id']].update(state='applying', applying_at=c.clock[0])
    c.store.put('settings_change_drafts', rows)
    service = _restart(c.store)
    service.settings_orchestrator.now = lambda: c.clock[0]
    service.settings_orchestrator.reconcile()
    service.settings_orchestrator.reconcile()
    assert service.store.config('settings_change_drafts')[draft['draft_id']]['state'] == 'unknown'
    assert service.store.config('settings_audit')[-1]['error_class'] == 'interrupted'
    assert service.context_observations.settings()['timezone'] == ''


def test_w3_committed_mcp_receipt_resumes_same_work_once(case):
    """W3 committed setup receipt recovers queue admission without repeating setup."""
    c = case(mcp_cases.McpContinuation)
    draft = c.propose()
    with mock.patch.object(c.service, 'resume_settings_work'):
        c.confirm(draft)
    assert c.store.config('settings_change_drafts')[draft['draft_id']]['setup_receipt']
    service = _restart(c.store)
    service.mcp_setup_home = c.home
    service.resume_settings_work()
    service.resume_settings_work()
    assert service.store.job(c.job['id'])['status'] == 'queued'
    assert len([e for e in service.store.task_events(c.job['id'])
                if e['tool'] == 'settings_continuation' and e['status'] == 'queued']) == 1
    assert c.calls == ['register', 'observe']


def test_w3_committed_generic_setter_is_reported_unknown(case):
    """W3 a committed generic setter is not read back after losing settlement."""
    c = case(settings_cases._Case)
    draft = c.draft('current_context', 'timezone', 'Asia/Seoul')
    settle = c.settings._settle
    def crash_after_setter(ident, state, *args, **kwargs):
        if state == 'applied':
            raise KeyboardInterrupt('crash before draft settlement')
        return settle(ident, state, *args, **kwargs)
    with mock.patch.object(c.settings, '_settle', side_effect=crash_after_setter):
        with pytest.raises(KeyboardInterrupt):
            c.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
    assert c.context()['timezone'] == 'Asia/Seoul'
    service = _restart(c.store)
    service.settings_orchestrator.now = lambda: c.clock[0]
    service.settings_orchestrator.reconcile()
    assert service.store.config('settings_change_drafts')[draft['draft_id']]['state'] == 'unknown'
    assert service.context_observations.settings()['timezone'] == 'Asia/Seoul'


def _not_sent_notification(case):
    c, work = _telegram(case)
    note = c.store.queue_notification(work, 42, 'g', 'completed')
    original = c.service.telegram_transport
    def offline(url, *args, **kwargs):
        if url.endswith('/sendMessage'):
            raise TelegramNotSent('fixture connection refused before send')
        return original(url, *args, **kwargs)
    c.service.telegram_transport = offline
    assert c.service.deliver_notification()
    return c, note


@pytest.mark.xfail(strict=True, reason='#826 W4: TelegramNotSent notifications become unknown instead of queued')
def test_w4_telegram_definitely_unsent_notice_is_requeued(case):
    """W4 a definitely unsent Telegram notification should remain queued."""
    c, note = _not_sent_notification(case)
    assert c.store.notification(note['id'])['state'] == 'queued'


def test_w4_telegram_definitely_unsent_notice_is_unknown_today(case):
    """W4 characterize definitely unsent Telegram notifications as unknown today."""
    c, note = _not_sent_notification(case)
    assert c.store.notification(note['id'])['state'] == 'unknown'
    service = _restart(c.store, c.service)
    assert not service.deliver_notification()
    assert service.store.notification(note['id'])['state'] == 'unknown'


@pytest.mark.xfail(strict=True, reason='#826 W4: Mattermost cursor commits before covered reactions are applied')
def test_w4_mattermost_reaction_survives_cursor_crash(case):
    """W4 a reaction lost after cursor commit should be replayed after restart."""
    c = case(mm_cases.OwnerActs)
    _, draft, note = c.settings_notice()
    c.fake.react(mm_cases.OWNER, note['message_id'], '+1')
    with mock.patch.object(c.surface, 'ingest_reaction', side_effect=RuntimeError('crash before reaction')):
        with pytest.raises(RuntimeError, match='crash before reaction'):
            c.surface.receive()
    service = _restart(c.store)
    service.mattermost_transport = c.fake
    service.steer_spawn = lambda target: target()
    from owner_act_script import with_owner_acts
    service.use_decision_engine(with_owner_acts(FixtureDecisionEngine()))
    service.mattermost_surface.receive()
    assert service.store.notification(note['id'])['state'] == 'sent'
    assert service.store.config('settings_change_drafts')[draft]['state'] == 'applied'


@pytest.mark.xfail(strict=True, reason='#826 W4: Slack press key commits before the answer starts')
def test_w4_slack_press_survives_answer_crash(case):
    """W4 replay of a pressed Slack button should settle its pending draft."""
    c = case(slack_cases.Buttons)
    _, draft, note = c.settings_notice()
    ts = slack_cases.slack_ts(note['message_id'])
    action_at = c.fake.tick()
    # Queueing is the crash boundary: the durable dedupe key is already written.
    with mock.patch.object(c.service, 'steer_spawn', side_effect=RuntimeError('crash before answer')):
        c.press(ts, slack_cases.ACTION_APPROVE, action_ts=action_at)
    # The Socket Mode receiver catches exceptions, after it has acknowledged.
    with c.store.db() as db:
        assert db.execute('SELECT 1 FROM slack_events WHERE key=?',
                          (f'press:{ts}:{action_at}',)).fetchone()
    service = _restart(c.store)
    service.slack_web = c.fake.client
    service.slack_socket = slack_cases.FakeSocket
    service.steer_spawn = lambda target: target()
    c.service, c.surface = service, service.slack_surface
    c.surface.receive()
    c.press(ts, slack_cases.ACTION_APPROVE, action_ts=action_at)
    assert service.store.notification(note['id'])['state'] == 'sent'
    assert service.store.config('settings_change_drafts')[draft]['state'] == 'applied'


def test_i1_interrupted_stays_until_owner_retries(env):
    """I1 recovery does not requeue interrupted Work; an owner retry can do so."""
    store, _ = env
    work = store.enqueue('fixture', 'retry')
    _status(store, work, 'running')
    service = _restart(store)
    assert not service.run_one()
    assert service.store.job(work)['status'] == 'interrupted'
    assert service.safe_retry(service.store.job(work)) == (True, None)
    retry, reason = service.retry_from_control(service.store.job(work), 'g', 42)
    assert reason is None
    assert service.store.job(retry)['status'] == 'queued'
    assert service.store.job(retry)['related_job_id'] == work
    assert service.retry_from_control(service.store.job(work), 'g', 42)[0] is None


def test_i2_cut_off_attempt_refuses_owner_retry(env):
    """I2 a cut-off action remains a retry blocker on an interrupted Work."""
    store, _ = env
    work = store.enqueue('fixture', 'interrupted')
    _status(store, work, 'running')
    _event(store, work, 'save_note', 'running', call_id='cut-off')
    service = _restart(store)
    allowed, reason = service.safe_retry(service.store.job(work))
    assert not allowed
    assert service.retry_from_control(service.store.job(work), 'g', 42) == (None, reason)
    assert [j['id'] for j in service.store.jobs()] == [work]


def test_i3_interrupted_preparation_slot_settles_failed(env):
    """I3 interrupted preparation slots fail rather than executing again."""
    store, _, original, work, now = _scheduled(env)
    _status(store, work, 'running', 'none')
    service = _restart(store)
    service.preparations.clock = lambda: now
    row = service.preparations.get(original['id'])
    assert row['last_run_job_id'] == work
    assert any(r['id'] == row['id'] for r in service.preparations.due(now))
    service.preparations.settle(row, now)
    assert service.preparations.get(row['id'])['last_outcome'] == 'failed'
    assert service.store.job(work)['status'] == 'interrupted'
    assert len(service.store.jobs()) == 1


def test_i4_interrupted_sending_reply_has_no_reply_anchor(case):
    """I4 an ambiguous interrupted reply is not resent and has no reply anchor."""
    c, work = _telegram(case, 'interrupted', 'sending')
    service = _restart(c.store, c.service)
    service.deliver_one()
    assert service.store.job(work)['delivery'] == 'unknown'
    assert service.telegram_turns.get(work)['reply_message_id'] is None
    assert not [x for x in c.calls if x[0].endswith('/sendMessage')]


def test_u1_unknown_work_not_resumable(env):
    """U1 an unknown terminal outcome cannot be automatically retried."""
    store, _ = env
    work = store.enqueue('fixture', 'unknown')
    _status(store, work, 'unknown')
    service = _restart(store)
    assert not service.safe_retry(service.store.job(work))[0]
    assert not service.run_one()
    assert service.store.job(work)['status'] == 'unknown'
    # Matrix discrepancy: status admission, not continuation_reasons, holds it.
    for purpose in ('resume', 'retry', 'redelegate'):
        assert service.continuation_reasons(work, purpose) == ()


def test_u2_read_does_not_reconcile_unknown_write(case):
    """U2 a successful read after an unknown API write cannot clear the effect."""
    c = case(api_effects.ApiEffects)
    def timeout(*args):
        raise TimeoutError('fixture lost receipt')
    with pytest.raises(api_effects.ApiError):
        c.client(timeout).call(api_effects.CALL)
    result = c.api.call({**api_effects.CALL, 'method': 'GET', 'effect': 'read', 'body': None})
    assert result['data'] == {'result': 'changed'}
    service = _restart(c.store)
    assert service._work_has_unknown_effect(c.work)
    assert not service.safe_retry(service.store.job(c.work))[0]
    assert any(e['tool'] == 'api_dispatch' for e in service.store.task_events(c.work))


def test_u3_dispatch_without_settlement_holds_slot_and_host(case):
    """U3 a lost API settlement stays dispatching and blocks subsequent writes."""
    c = case(api_effects.ApiEffects)
    with mock.patch.object(c.api, '_settle_effect', side_effect=RuntimeError('lost settlement')):
        with pytest.raises(RuntimeError, match='lost settlement'):
            c.api.call(api_effects.CALL)
    service = _restart(c.store)
    api = api_effects.ApiRequests(service.store, c.work, transport=c.transport)
    with pytest.raises(api_effects.ApiError) as caught:
        api.call({**api_effects.CALL, 'body': '{"different":true}'})
    assert caught.value.code == 'effect_unknown'
    assert service._work_has_unknown_effect(c.work)
    assert any(e['tool'] == 'api_dispatch' and e['status'] == 'dispatching'
               for e in service.store.task_events(c.work))
    assert len(c.sent) == 1


def test_u4_unknown_delivery_is_not_retried(case):
    """U4 unknown replies and notifications remain unknown and block retry."""
    c, work = _telegram(case, 'failed', 'unknown')
    note = c.store.queue_notification(work, 42, 'g', 'failed')
    c.store.update_notification(note['id'], 'unknown')
    service = _restart(c.store, c.service)
    service.deliver_one()
    assert not service.deliver_notification()
    assert not service.safe_retry(service.store.job(work))[0]
    assert service.store.job(work)['delivery'] == 'unknown'
    assert service.store.notification(note['id'])['state'] == 'unknown'
    assert not [x for x in c.calls if x[0].endswith('/sendMessage')]


def test_d1_pending_reply_and_notice_sent_once_after_restart(case):
    """D1 pending delivery survives restart and is sent exactly once."""
    c, work = _telegram(case)
    note = c.store.queue_notification(work, 42, 'g', 'completed')
    service = _restart(c.store, c.service)
    service.deliver_one()
    assert service.deliver_notification()
    service.deliver_one()
    assert not service.deliver_notification()
    assert service.store.job(work)['delivery'] == 'sent'
    assert service.store.notification(note['id'])['state'] == 'sent'
    assert len([x for x in c.calls if x[0].endswith('/sendMessage')]) == 2


def test_d2_sending_reply_becomes_unknown(case):
    """D2 an in-flight reply becomes unknown and is never resent."""
    c, work = _telegram(case, delivery='sending')
    service = _restart(c.store, c.service)
    service.deliver_one()
    assert service.store.job(work)['delivery'] == 'unknown'
    assert not [x for x in c.calls if x[0].endswith('/sendMessage')]


def _lost_anchor(case):
    c, work = _telegram(case)
    with mock.patch.object(c.service.telegram_turns, 'record_reply', side_effect=RuntimeError('crash after sent')):
        with pytest.raises(RuntimeError, match='crash after sent'):
            c.service.deliver_one()
    service = _restart(c.store, c.service)
    service.deliver_one()
    assert service.store.job(work)['delivery'] == 'sent'
    return c, service, work


@pytest.mark.xfail(strict=True, reason='#826 D3: sent delivery and reply anchor are separate commits with no recovery')
def test_d3_sent_reply_anchor_survives_lost_record_reply(case):
    """D3 a confirmed sent reply should retain an anchor for reaction lookup."""
    c, service, work = _lost_anchor(case)
    sent = [x for x in c.calls if x[0].endswith('/sendMessage')]
    assert len(sent) == 1
    assert service.ingest_reaction({'chat': {'id': 42, 'type': 'private'},
        'user': {'id': 42}, 'message_id': 1,
        'new_reaction': [{'type': 'emoji', 'emoji': '👍'}]}, 'g') == 'answer'
    assert isinstance(service.telegram_turns.get(work)['reply_message_id'], int)


def test_d3_sent_reply_anchor_is_lost_today(case):
    """D3 characterize the missing reply anchor after a confirmed send today."""
    c, service, work = _lost_anchor(case)
    assert service.telegram_turns.get(work)['reply_message_id'] is None
    assert len([x for x in c.calls if x[0].endswith('/sendMessage')]) == 1
    assert service.ingest_reaction({'chat': {'id': 42, 'type': 'private'},
        'user': {'id': 42}, 'message_id': 1,
        'new_reaction': [{'type': 'emoji', 'emoji': '👍'}]}, 'g') is None


def test_d4_delivered_but_unacknowledged_reply_is_unknown(case):
    """D4 a reply sent before process death stays unknown after restart."""
    c, work = _telegram(case)
    original = c.service.telegram_transport
    def delivered_then_crashed(url, *args, **kwargs):
        result = original(url, *args, **kwargs)
        if url.endswith('/sendMessage'):
            raise KeyboardInterrupt('crash after surface accepted reply')
        return result
    c.service.telegram_transport = delivered_then_crashed
    with pytest.raises(KeyboardInterrupt):
        c.service.deliver_one()
    assert c.store.job(work)['delivery'] == 'sending'
    c.service.telegram_transport = original
    service = _restart(c.store, c.service)
    service.deliver_one()
    assert service.store.job(work)['delivery'] == 'unknown'
    assert len([x for x in c.calls if x[0].endswith('/sendMessage')]) == 1


def test_x1_legacy_ledger_defaults_and_login_expiry(env):
    """X1 legacy budget counters default once and windowless login rows expire."""
    store, _ = env
    work = store.enqueue('fixture', 'legacy')
    _status(store, work, 'partial')
    store.put('work_budget:' + work, {'attempts': 2, 'deadline': time.time() + 300})
    service = _restart(store)
    ledger = WorkLedger(service.store, work)
    assert ledger.used() == 2
    assert ledger.used('turns') == 0
    assert service.store.config('work_budget:' + work)['epoch'] == 0
    service._put_browser_login(work, {'work_id': work, 'host': 'fixture.test', 'state': 'offered',
                                    'window': None, 'nonce': 'legacy', 'intent': 'resume',
                                    'offered_at': time.time()})
    assert service.process_browser_logins() == [work]
    assert service._browser_login(work)['state'] == 'expired'
    assert service.store.job(work)['status'] == 'partial'


def test_x2_ledger_deadline_includes_downtime(env):
    """X2 restart does not extend an absolute ledger deadline."""
    store, service = env
    work = store.enqueue('fixture', 'deadline')
    _status(store, work, 'running')
    ledger = service.work_budget(work).ledger
    before = store.config(ledger.key)
    restarted = _restart(store)
    with mock.patch.object(ledger, 'wall', return_value=before['deadline'] + 1):
        assert ledger.refusal(40) == 'deadline'
    assert restarted.store.config(ledger.key)['deadline'] == before['deadline']
    assert restarted.store.config(ledger.key)['attempts'] == before['attempts']


def test_x3_scheduled_run_creates_no_settings_wait(env):
    """X3 current unattended contract refuses to create scheduled settings waits."""
    store, service, _, work, _ = _scheduled(env)
    result = service.settings_tools(store.job(work))('settings_change', {
        'category': 'api_connections', 'setting': 'connect',
        'value': json.dumps(schedule_cases.PUBLIC), 'continue_work': 'yes'})
    assert result['state'] == 'not_proposed'
    service = _restart(store)
    service.retire_unattended_waits()
    assert service.store.config('settings_change_drafts', {}) == {}
    assert service.work_waits_for_owner(service.store.job(work)) == ()


def _stale_bridge(env):
    store, service = env
    work = store.enqueue('fixture', 'stale')
    _status(store, work, 'running')
    service.work_budget(work)
    # Exactly mcp_bridge.serve's constructor: no fresh=True on the child.
    bridge = WorkBudget(stop=lambda: service.work_stopped(work), ledger=WorkLedger(store, work))
    assert bridge.ledger.refusal(40) is None
    before = store.config('work_budget:' + work)
    restarted = _restart(store)
    assert restarted.store.job(work)['status'] == 'interrupted'
    with restarted.store.db() as db:
        db.execute("UPDATE jobs SET status='queued' WHERE id=?", (work,))
    assert restarted._claim_queued_work()['id'] == work
    restarted.work_budget(work)
    return restarted.store, bridge, before


@pytest.mark.xfail(strict=True, reason='#826 X4: recovery does not fence an active bridge for the same Work id')
def test_x4_pre_restart_bridge_is_superseded_after_same_id_resume(env):
    """X4 a pre-restart bridge must be superseded once the same Work resumes."""
    _, bridge, _ = _stale_bridge(env)
    assert bridge.ledger.refusal(40) == 'superseded'


def test_x4_pre_restart_bridge_can_still_spend_today(env):
    """X4 characterize an old bridge spending after restart and same-id resume."""
    store, bridge, before = _stale_bridge(env)
    assert bridge.ledger.refusal(40) is None
    after = store.config(bridge.ledger.key)
    assert after['attempts'] == before['attempts'] + 1
    assert after['epoch'] == before['epoch']
    assert after['deadline'] == before['deadline']


def _superseded_bridge_steer(env):
    """Drive serve's real refused-call/_with_steer path; only stdio is replaced.

    X4's missing restart fence is tested separately. Pause/reopen here really
    supersedes the old budget, isolating the steer bug even after that fix.
    """
    from personal_agent import mcp_bridge

    store, bridge, _ = _stale_bridge(env)
    work = bridge.ledger.job_id
    bridge.ledger.pause()
    env[1].work_budget(work)
    assert bridge.ledger.refusal(40) == 'superseded'
    source = store.enqueue('Use the updated comparison criteria', 'stale-steer')
    assert store.add_steer(work, source, 'Use the updated comparison criteria')
    replies = []

    def drive(handle, concurrent):
        replies.append(handle('tools/call', {'name': 'list_notes', 'arguments': {}}))

    with mock.patch.object(mcp_bridge, 'WorkBudget', return_value=bridge), \
            mock.patch.object(mcp_bridge, '_serve_stdio', side_effect=drive):
        mcp_bridge.serve(str(store.root), work)
    result = replies[0]
    assert result['isError']
    assert result['structuredContent']['code'] == 'run_superseded'
    failed = [e for e in store.task_events(work)
              if e['tool'] == 'list_notes' and e['status'] == 'failed']
    assert len(failed) == 1
    assert failed[0]['trace']['code'] == 'run_superseded'
    assert failed[0]['trace']['ran'] is False
    return store, work, source, result


@pytest.mark.xfail(strict=True, reason='#826 X4: a refused superseded bridge call still claims owner steers')
def test_x4_superseded_bridge_call_leaves_owner_steer_unclaimed(env):
    """X4 a refused stale call records ran:false and claims no owner steer."""
    store, work, source, result = _superseded_bridge_steer(env)
    assert not store.steer_delivered(source)
    assert not any('updated comparison criteria' in part.get('text', '')
                   for part in result['content'])
    assert not any(e['tool'] == 'owner_steer' for e in store.task_events(work))


def test_x4_superseded_bridge_call_claims_owner_steer_today(env):
    """X4 characterize the real refused call handing a steer to the stale worker."""
    from personal_agent.agent_runtime import STEER_EVENT

    store, work, source, result = _superseded_bridge_steer(env)
    assert store.steer_delivered(source)
    assert not store.steer_answered(source)
    assert any('updated comparison criteria' in part.get('text', '')
               for part in result['content'])
    delivered = [e for e in store.task_events(work) if e['tool'] == STEER_EVENT]
    assert len(delivered) == 1
    assert delivered[0]['status'] == 'delivered'
    assert delivered[0]['trace'] == {'count': 1}


def test_x5_cancelled_work_survives_restart(case):
    """X5 cancellation prevents approval-driven dispatch after restart."""
    c = _payment(case)
    _status(c.store, c.job['id'], 'cancelled')
    service = _restart(c.store)
    request = service._browser_request(c.job['id'])
    result = service.browser_step_decision({'work_id': c.job['id'], 'decision': 'approve',
                                           'digest': request['offer_digest']})
    assert not result['resumed']
    assert service.store.job(c.job['id'])['status'] == 'cancelled'
    assert not service.run_one()


def test_x5_stop_survives_restart_and_refuses_first_spend(env):
    """X5 durable stop is honored by the resumed host budget before spending."""
    store, service = env
    work = store.enqueue('fixture', 'stopped')
    _status(store, work, 'running')
    budget = service.work_budget(work)
    store.put(WORK_STOP_KEY, [work])
    before = store.config(budget.ledger.key)['attempts']
    restarted = _restart(store)
    resumed = restarted.work_budget(work)
    assert resumed.interrupted() == 'stopped'
    with pytest.raises(ToolError) as caught:
        resumed.spend_attempt()
    assert caught.value.code == 'stopped'
    assert restarted.store.config(budget.ledger.key)['attempts'] == before


def test_x5_forgotten_request_cancels_at_claim(env):
    """X5 a forgotten queued request is cancelled at the real claim boundary."""
    store, service = env
    work = store.enqueue('fixture', 'forgotten')
    # A previously attempted request has a transcript row; fresh queued Works
    # do not acquire one until their first claim.
    assert service._claim_queued_work()['id'] == work
    restarted = _restart(store)
    _status(store, work, 'queued')
    with store.db() as db:
        source = db.execute("SELECT id FROM messages WHERE job_id=? AND role='user'", (work,)).fetchone()
        db.execute('INSERT INTO forgotten_messages(message_id,receipt_id,created) VALUES (?,?,?)',
                   (source['id'], 'fixture-forget', time.time()))
    restarted = _restart(store)
    restarted._claim_queued_work()
    assert restarted.store.job(work)['status'] == 'cancelled'
    assert restarted.store.job(work)['error'] == 'forgotten'


def test_x6_restart_preserves_budget_and_continuation_count(case):
    """X6 restart preserves durable continuation limits and budget counters."""
    c = case(budget_cases.BudgetContinueTests)
    service, store, work, run, old = c.stopped()
    assert service.offer_budget_continue(run)
    assert c.press(service, work)['resumed']
    fresh = service.work_budget(work)
    fresh.spend_attempt()
    fresh.ledger.pause()
    _status(store, work, 'partial')
    for index in range(7):
        _event(store, work, 'action_continuation', 'queued', resumed=True, offer_digest=str(index))
    before = store.config('work_budget:' + work)
    restarted = _restart(store)
    assert 'limit' in restarted.continuation_reasons(work, 'resume')
    assert restarted.store.config('work_budget:' + work) == before
    assert old.ledger.refusal(200) == 'superseded'
    assert not restarted.offer_budget_continue(run)


def test_x6_active_crash_ledger_is_not_renewed(env):
    """X6 an active ledger left by restart does not gain a prerequisite allowance."""
    store, service = env
    work = store.enqueue('fixture', 'active-budget')
    _status(store, work, 'running')
    budget = service.work_budget(work)
    budget.spend_attempt()
    before = store.config(budget.ledger.key)
    restarted = _restart(store)
    with restarted.store.db() as db:
        assert not renew_for_owner_prerequisite(db, work)
    assert restarted.store.config(budget.ledger.key) == before


@pytest.mark.parametrize('terminal', ['finish', 'fail'])
@pytest.mark.parametrize('protected', ['interrupted', 'cancelled'])
@pytest.mark.xfail(strict=True, reason='#826 X7: terminal writes do not compare the current Work status')
def test_x7_late_terminal_writer_preserves_protected_status(env, terminal, protected):
    """X7 a late terminal writer must not overwrite interrupted or cancelled status."""
    store, service = env
    work = store.enqueue('fixture', 'late')
    _status(store, work, 'running')
    run = WorkRun(store.job(work), time.time())
    run.response = 'late worker response'
    _restart(store)
    assert store.job(work)['status'] == 'interrupted'
    if protected == 'cancelled':
        _status(store, work, 'cancelled')
    if terminal == 'finish':
        service._finish_work(run)
    else:
        service._fail_work(run, RuntimeError('late worker failure'))
    assert store.job(work)['status'] == protected


def test_x8_trusted_local_unaccounted_actions_block_retry(env):
    """X8 trusted-local CLI actions remain unaccounted after restart."""
    store, service = env
    work = store.enqueue('fixture', 'unmediated')
    _status(store, work, 'failed')
    service.record_work_sources(work, {ENGINE_UNMEDIATED})
    restarted = _restart(store)
    assert not restarted.safe_retry(restarted.store.job(work))[0]
    # Source labels are a separate safe_retry gate, not a continuation reason.
    assert restarted.continuation_reasons(work, 'resume') == ()
    assert restarted.continuation_reasons(work, 'retry') == ()
    assert not restarted.run_one()
    assert restarted.store.job(work)['status'] == 'failed'


def _recovery_commit_gap(case):
    """X9 stop just after recovery's status commit, before its pending update."""
    c, work = _telegram(case, 'running', 'none')
    c.store.recover()  # The middle transaction of recover_interrupted_work.
    assert c.store.job(work)['status'] == 'interrupted'
    assert c.store.job(work)['delivery'] == 'none'
    service = _restart(c.store, c.service)
    service.deliver_one()
    service.deliver_one()
    sent = [call for call in c.calls if call[0].endswith('/sendMessage')]
    return service, work, sent


@pytest.mark.xfail(strict=True, reason='#826 X9: crash after recover commits loses the interrupted reply')
def test_x9_recovery_commit_gap_still_delivers_one_interrupted_reply(case):
    """X9 restarting recovery must queue the interrupted Telegram reply once."""
    service, work, sent = _recovery_commit_gap(case)
    assert service.store.job(work)['status'] == 'interrupted'
    assert service.store.job(work)['delivery'] == 'sent'
    assert len(sent) == 1


def test_x9_recovery_commit_gap_leaves_delivery_none_today(case):
    """X9 characterize the stranded interrupted row after recovery itself crashes."""
    service, work, sent = _recovery_commit_gap(case)
    assert service.store.job(work)['status'] == 'interrupted'
    assert service.store.job(work)['delivery'] == 'none'
    assert sent == []


def test_x11_finish_before_pause_leaves_active_ledger_without_continue(env):
    """X11 crash after _finish_work but before finally pauses the ledger.

    Characterization only: no recovery/renewal policy for this gap is specified.
    Use a partial budget-boundary result with observed progress so lack of a
    Continue offer is attributable to the active ledger, not a successful Work.
    """
    store, service = env
    work = store.enqueue('fixture', 'finish-before-pause')
    _status(store, work, 'running')
    run = WorkRun(store.job(work), time.time())
    run.work_budget = service.work_budget(work)
    run.work_budget.spend_attempt()
    run.outcome = 'partial'
    run.response = 'Read some material; comparison remains unfinished.'
    _event(store, work, 'list_notes', 'succeeded', evidence={'count': 1})
    _event(store, work, 'list_notes', 'failed', code='attempt_budget', ran=False, effect='none')
    service._finish_work(run)
    assert store.job(work)['status'] == 'partial'
    before = store.config(run.work_budget.ledger.key)
    assert before['active']
    restarted = _restart(store)
    assert restarted.store.job(work)['status'] == 'partial'
    with restarted.store.db() as db:
        assert not renew_for_owner_prerequisite(db, work)
    assert not restarted.offer_budget_continue(run)
    assert restarted.budget_continue_offer(work).get('state') != 'offered'
    assert restarted.store.config(run.work_budget.ledger.key) == before
    # Positive control: with the ordinary finalizer's pause, this same result
    # is eligible, ruling out unrelated offer prerequisites as the cause.
    run.work_budget.ledger.pause()
    assert restarted.offer_budget_continue(run)
