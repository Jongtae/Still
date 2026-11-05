"""#619 (owner decisions 2026-11-03 on #614): one payment gate, action-bound approvals, open until settled.

Evidence class: model-free unit and service tests over the fake page driver, a fake sandbox
process, the real QuickStore / AgentService / SettingsOrchestrator, and scripted judgments.
No live model, browser, connector or account was contacted, and none is claimed.
"""
import json
import types
import unittest
from unittest import mock

from personal_agent import browser_session as bs
from personal_agent import browser_worker as bw
from personal_agent import owner_mcp, trust_record
from personal_agent import preparations as prep
from personal_agent.agent_runtime import ToolError
from personal_agent.conversation_handoff import JUDGMENT_NO, JUDGMENT_YES, ConversationJudgments, Judgment
from personal_agent.quickstart_service import PAYMENT_APPROVAL_SECONDS, UNATTENDED_APPROVAL_TEXT
from personal_agent.settings_orchestrator import SettingsError

import test_code_run_secrets as code_secrets
import test_connect_effects as connect_effects
from test_browser_session import ORIGIN, Approvals, session
from test_code_run import FakeProcess
from test_owner_settings_conversation import _Case

HOST, FakeApprovals, INPUT = code_secrets.HOST, code_secrets.FakeApprovals, connect_effects.INPUT


class NeverJudged:
    """A judgment surface that fails the test if a press is ever judged."""

    def press_commits_payment(self, *args, **kwargs):
        raise AssertionError('#619: no browser press is judged')


def opened(approvals=None):
    browser, driver = session(judgments=NeverJudged(), approvals=approvals or Approvals())
    browser.open({'url': ORIGIN + '/one-press', 'effect': 'navigate'})
    return browser, driver


# -- decision 1: one deterministic payment gate --------------------------------------------------
class OnePaymentGate(unittest.TestCase):
    def test_no_press_is_judged_and_the_judgment_is_gone(self):
        self.assertFalse(hasattr(ConversationJudgments, 'press_commits_payment'))
        approvals = Approvals()
        browser, driver = opened(approvals)
        browser.click({'target': '예약하기', 'effect': 'mutate'})
        self.assertEqual(driver.approved, [False])
        self.assertEqual(approvals.requests, [])

    def test_a_commit_press_or_a_declared_payment_is_never_pressed_without_the_owner(self):
        for target, effect in (('Buy now', 'mutate'), ('Buy now', 'read'), ('메모 저장', 'payment')):
            with self.subTest(target=target, effect=effect):
                approvals = Approvals()
                browser, driver = opened(approvals)
                with self.assertRaises(ToolError) as caught:
                    browser.click({'target': target, 'effect': effect})
                self.assertEqual(caught.exception.code, 'approval_required')
                self.assertFalse([entry for entry in driver.log if entry[0] == 'click'], 'nothing was pressed')
                self.assertEqual(len(approvals.requests), 1)
                # The owner's approval of exactly that press lets it through, once.
                approvals.issue(approvals.requests[0][0])
                browser.click({'target': target, 'effect': effect})
                self.assertEqual(driver.approved, [True])

    def test_an_approval_of_one_press_is_not_spent_on_another(self):
        approvals = Approvals()
        browser, driver = opened(approvals)
        with self.assertRaises(ToolError):
            browser.click({'target': 'Buy now', 'effect': 'mutate'})
        approvals.issue(approvals.requests[0][0])
        with self.assertRaises(ToolError):
            browser.click({'target': '메모 저장', 'effect': 'payment'})
        self.assertFalse([entry for entry in driver.log if entry[0] == 'click'])


class FormCheckUnknown(unittest.TestCase):
    """The worker's form-navigation check when the page does not answer which form it submits."""

    WORKER = next(value for value in vars(bw).values() if isinstance(value, type) and hasattr(value, 'check_form_navigation'))

    #: An AI step on a page it saw with no payment field (``confirm_ok``).
    STEP = {'approved': False, 'confirm_ok': True, 'dialogs': []}

    def check(self, method, page=None, step=STEP):
        later = []
        state = types.SimpleNamespace(deciding=0, refused_submits=0, held=[], dialog_step=step)
        state.receive_cancelled = lambda record: state.held.append(record)
        state.AppHelper = types.SimpleNamespace(callLater=lambda seconds, fn: later.append(fn))
        state.run = lambda script, args, done: page(done) if page else None
        decided = []
        self.WORKER.check_form_navigation(state, 'https://shop.test/search', method, decided.append)
        for fn in later:
            fn()
        return decided, state

    def test_an_unanswered_get_form_goes_through_and_a_post_is_refused(self):
        decided, state = self.check('GET')
        self.assertEqual((decided, state.refused_submits, state.deciding), ([True], 0, 0))
        decided, state = self.check('POST')
        self.assertEqual((decided, state.refused_submits, state.deciding), ([False], 1, 0))

    def test_an_unanswered_get_form_is_refused_outside_a_step_or_on_a_payment_page(self):
        """Codex P1 on #626: fail-closed where the page's answer could have named a payment form."""
        for step in (None, {'approved': False, 'confirm_ok': False, 'dialogs': []}, {'approved': False, 'dialogs': []}):
            with self.subTest(step=step):
                decided, state = self.check('GET', step=step)
                self.assertEqual((decided, state.refused_submits), ([False], 1))

    def test_a_script_error_lets_a_get_through_and_refuses_a_post(self):
        decided, state = self.check('get', page=lambda done: done(None, RuntimeError('page went away')))
        self.assertEqual((decided, state.refused_submits), ([True], 0))
        decided, state = self.check('POST', page=lambda done: done(None, RuntimeError('page went away')))
        self.assertEqual((decided, state.refused_submits), ([False], 1))

    def test_a_payment_form_the_page_names_is_held_whatever_its_method(self):
        record = {'dom': 0, 'method': 'get', 'action': 'https://shop.test/pay'}
        decided, state = self.check('GET', page=lambda done: done({'allow': False, 'cancelled': record}, None))
        self.assertEqual((decided, state.held, state.refused_submits), ([False], [record], 0))

    def test_purchase_questions_stay_guarded_on_a_page_with_no_payment_field(self):
        """Codex P1 on #626: an unapproved step's purchase confirmation is declined, with or without an amount."""
        WORKER = next(value for value in vars(bw).values() if isinstance(value, type) and hasattr(value, 'js_dialog'))
        for question in ('Would you like to order this item?', '注文してもよろしいですか?', '주문하시겠습니까?',
                         'Place order?', 'Buy now?', 'Would you like to order this item for $20?',
                         '注文してもよろしいですか? ¥2,000', '12,000원 주문하시겠습니까?', 'Place order ($20)?',
                         'Buy now for €5?', 'Reorder this item?', 'Cancel this order and pay $5?'):
            for effect in ('read', 'navigate', 'mutate'):
                with self.subTest(question=question, effect=effect):
                    self.assertTrue(bs.commit_question(question))
                    state = types.SimpleNamespace(owner_visible=False, dialog_step={
                        'approved': False, 'confirm_ok': True, 'effect': effect, 'dialogs': []})
                    self.assertIs(WORKER.js_dialog(state, 'confirm', question), False)
                    self.assertEqual(state.dialog_step['dialogs'][0]['outcome'], 'declined_payment')
            approved = types.SimpleNamespace(owner_visible=False, dialog_step={'approved': True, 'dialogs': []})
            self.assertIs(WORKER.js_dialog(approved, 'confirm', question), True, 'the owner approved that press')

    def test_every_order_question_stays_guarded_cancellation_included(self):
        """Owner decision on Codex P1 (#626): no keyword exclusion narrows the payment question floor."""
        WORKER = next(value for value in vars(bw).values() if isinstance(value, type) and hasattr(value, 'js_dialog'))
        for question in ('Would you like to order this non-refundable item?', 'This order cannot be cancelled. Continue?',
                         'Cancel this order?', '주문을 취소하시겠습니까?', '注文をキャンセルしますか?', '取消订单?'):
            with self.subTest(question=question):
                self.assertTrue(bs.commit_question(question))
                state = types.SimpleNamespace(owner_visible=False, dialog_step={
                    'approved': False, 'confirm_ok': True, 'effect': 'mutate', 'dialogs': []})
                self.assertIs(WORKER.js_dialog(state, 'confirm', question), False)
        self.assertFalse(hasattr(bs, 'WITHDRAW_QUESTION'))

    def test_the_refusal_no_longer_asks_for_a_payment_approval(self):
        self.assertIn('POST', bs.SUBMIT_REFUSED_TEXT)
        self.assertNotIn('승인을 요청', bs.SUBMIT_REFUSED_TEXT)


# -- decision 2: code_run labels only a declared payment as payment -----------------------------
class CodeRunMoneyOnly(unittest.TestCase):
    BODY = '{"note":"renamed"}'

    def setUp(self):
        storage = code_secrets.Storage(methodName='setUp')
        storage.setUp()
        self.addCleanup(storage.doCleanups)
        self.store, self.save = storage.store, storage.save
        self.work = self.store.enqueue('Rename my record', 'rename-1')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (self.work,))
        self.approvals = FakeApprovals()

    def run_change(self, approvals=None):
        from personal_agent.code_run import CodeRunner
        process = FakeProcess(requests=[{'method': 'POST', 'host': HOST, 'path': '/v1/records/1', 'query': '',
                                         'bodyText': self.BODY}], result={'stdout': 'ok', 'stderr': ''})
        runner = CodeRunner(self.store, self.work, popen=lambda *a, **k: process, node='/usr/bin/node',
                            install=lambda store, node: store.root, approvals=approvals or self.approvals)
        return process, runner.run({'code': 'print(1)', 'hosts': '', 'secrets': 'example-broker'})

    def dispatches(self):
        with self.store.db() as db:
            return [row['status'] for row in db.execute("SELECT status FROM tool_events WHERE tool='api_dispatch'")]

    def test_a_non_money_change_is_sent_without_an_approval_once_and_never_replayed(self):
        self.save(money=False)
        process, result = self.run_change()
        self.assertEqual([item['allow'] for item in process.decisions], [True])
        self.assertEqual(self.approvals.requested, [], 'no per-action approval for a non-payment change')
        self.assertEqual(result['effects'], [{'method': 'POST', 'host': HOST, 'path': '/v1/records/1'}])
        self.assertEqual(self.dispatches(), ['observed'])
        process, _result = self.run_change()
        self.assertEqual([item['allow'] for item in process.decisions], [False], 'the same change is never replayed')
        self.assertEqual(self.dispatches(), ['observed'])

    def test_a_money_set_still_needs_the_owners_exact_approval(self):
        self.save(money=True)
        process, result = self.run_change()
        self.assertEqual([item['allow'] for item in process.decisions], [False])
        [(binding, label)] = self.approvals.requested
        self.assertIn('"note":"renamed"', label)
        self.assertEqual(self.dispatches(), [], 'payment never runs without a matching owner confirmation')

    def run_sets(self, names, approvals=None):
        from personal_agent.code_run import CodeRunner
        process = FakeProcess(requests=[{'method': 'POST', 'host': HOST, 'path': '/v1/orders', 'query': '',
                                         'bodyText': '{"KEY":"{{EXAMPLE_BROKER_APP_KEY}}","qty":"2"}'}],
                              result={'stdout': 'ok', 'stderr': ''})
        runner = CodeRunner(self.store, self.work, popen=lambda *a, **k: process, node='/usr/bin/node',
                            install=lambda store, node: store.root, approvals=approvals or self.approvals)
        return process, runner.run({'code': 'print(1)', 'hosts': '', 'secrets': ','.join(names)})

    def test_a_money_set_on_a_shared_host_is_never_hidden_by_another_set(self):
        """Codex P1 on #626: a request does not say which set's credential it used, so any money set counts."""
        for other in ('aaa-notes', 'zzz-notes'):   # sorts before and after the money set
            with self.subTest(other=other):
                self.setUp()
                self.save(money=True)                    # example-broker: moves money
                self.save(name=other, money=False)       # same host, marked not money
                process, _result = self.run_sets([other, 'example-broker'])
                self.assertEqual([item['allow'] for item in process.decisions], [False])
                [(binding, label)] = self.approvals.requested
                self.assertIn('/v1/orders', label)
                self.assertEqual(self.dispatches(), [], "no payment without the owner's matching approval")
                self.approvals.granted.append(binding)
                process, _result = self.run_sets(['example-broker', other])
                self.assertEqual([item['allow'] for item in process.decisions], [True], 'that exact approval sends it')

    def test_a_host_only_non_money_sets_cover_needs_no_approval(self):
        self.save(money=False)
        self.save(name='aaa-notes', money=False)
        process, _result = self.run_sets(['aaa-notes', 'example-broker'])
        self.assertEqual([item['allow'] for item in process.decisions], [True])
        self.assertEqual(self.approvals.requested, [])
        self.assertEqual(self.dispatches(), ['observed'])

    def test_an_unattended_run_asks_nothing_and_says_why(self):
        self.save(money=True)

        class Unattended(FakeApprovals):
            def request(self, binding, label):
                raise ToolError(UNATTENDED_APPROVAL_TEXT, 'unattended_run')
        process, _result = self.run_change(Unattended())
        self.assertEqual([item['allow'] for item in process.decisions], [False])
        self.assertEqual(process.decisions[0]['reason'], UNATTENDED_APPROVAL_TEXT)
        self.assertEqual(self.dispatches(), [])


# -- decision 3: an approval binds to the exact action ------------------------------------------
class ConnectorApprovalBinding(unittest.TestCase):
    def setUp(self):
        self.case = connect_effects.EffectBoundary(methodName='run')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.service, self.store = self.case.service, self.case.store
        trust_record.set_money(self.store, self.case.target, True)
        self.assertEqual(self.case.call()['behavior'], 'deny')
        self.assertTrue(self.case.approve()['approved'])

    def test_unrelated_connection_state_never_voids_the_approval(self):
        self.service.set_ai_connections({'enabled': False})
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['synthetic', 'another'])
        self.assertEqual(self.case.call()['behavior'], 'allow')

    def test_a_changed_amount_or_payee_voids_it(self):
        for change in ({'amount': '40.00'}, {'account': 'someone-else'}):
            with self.subTest(change=change):
                self.assertEqual(self.case.call({**INPUT, **change})['behavior'], 'deny')
                pending = self.service._browser_request(self.case.job['id'])
                self.assertEqual(pending['state'], 'requested', 'the owner is asked again, for the new values')
                self.assertIn(list(change.values())[0], pending['label'])

    def test_reconnecting_that_server_voids_it(self):
        owner_mcp.set_confirmed(self.store, 'codex', [])
        owner_mcp.set_confirmed(self.store, 'codex', ['synthetic'])
        self.assertEqual(self.case.call()['behavior'], 'deny')


class PreparationDraftBinding(_Case):
    def setUp(self):
        super().setUp()
        self.clock[0] = 1_791_979_230.0
        self.service.preparations.clock = lambda: self.clock[0]
        self.row = self.service.preparations.create(kind='prepare', goal='자료 준비', due_at=self.clock[0] + 3600,
                                                   timezone='Asia/Seoul', recurrence='daily', channel='web',
                                                   created_from=None, state=prep.STATE_SCHEDULED)

    def test_a_cancel_draft_survives_a_run_of_the_watch(self):
        draft = self.settings.propose('owner', 'http', 'preparations', 'cancel', self.row['id'])
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=?,run_count=run_count+1,last_outcome=? WHERE id=?',
                       (self.row['due_at'] + 86400, 'delivered', self.row['id']))
        self.assertEqual(self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])['state'], 'applied')
        self.assertEqual(self.service.preparations.get(self.row['id'])['state'], prep.STATE_CANCELLED)

    def edit_draft(self, **changes):
        return self.settings.propose('owner', 'http', 'preparations', 'edit', json.dumps({'id': self.row['id'], **changes}))

    def test_a_timezone_change_makes_a_wall_clock_edit_stale(self):
        """Codex on #626: an offset-free ``due`` is bound to the timezone it was resolved in."""
        draft = self.edit_draft(due='2026-11-05T09:00')
        self.assertEqual(json.loads(draft['after'])['bound_timezone'], 'Asia/Seoul')
        self.assertNotIn('timezone', json.loads(draft['after']), 'not passed on as a timezone edit')
        self.service.preparations.edit(self.row['id'], timezone='America/New_York')
        before = self.service.preparations.get(self.row['id'])
        with self.assertRaises(SettingsError):
            self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
        current = self.service.preparations.get(self.row['id'])
        self.assertEqual((current['due_at'], current['timezone']), (before['due_at'], 'America/New_York'),
                         'nothing applies, so the approved time can never move 14 hours')

    def test_an_offset_free_due_resolves_in_the_bound_timezone(self):
        draft = self.edit_draft(due='2026-11-05T09:00')
        self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
        expected = prep.parse_due('2026-11-05T09:00', 'Asia/Seoul', 'Asia/Seoul', self.clock[0])[0]
        current = self.service.preparations.get(self.row['id'])
        self.assertEqual((current['due_at'], current['timezone']), (expected, 'Asia/Seoul'))

    def test_a_recurrence_only_edit_of_a_paused_past_due_preparation_applies(self):
        """Codex P2 on #626: the bound timezone is no due/timezone edit, so the stored due time is kept."""
        self.service.preparations.pause(self.row['id'])
        self.clock[0] = self.row['due_at'] + 3 * 86400
        draft = self.edit_draft(recurrence='weekly')
        self.assertEqual(self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])['state'], 'applied')
        current = self.service.preparations.get(self.row['id'])
        self.assertEqual((current['recurrence'], current['due_at'], current['state']),
                         ('weekly', self.row['due_at'], prep.STATE_PAUSED))

    def test_a_watch_run_does_not_make_an_edit_draft_stale(self):
        draft = self.edit_draft(due='2026-11-05T09:00')
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=?,run_count=run_count+1 WHERE id=?',
                       (self.row['due_at'] + 86400, self.row['id']))
        self.assertEqual(self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])['state'], 'applied')
        expected = prep.parse_due('2026-11-05T09:00', 'Asia/Seoul', 'Asia/Seoul', self.clock[0])[0]
        self.assertEqual(self.service.preparations.get(self.row['id'])['due_at'], expected)

    def test_a_rule_edit_binds_its_timezone_and_an_explicit_one_is_kept(self):
        draft = self.edit_draft(recurrence='weekly')
        self.assertEqual(json.loads(draft['after'])['bound_timezone'], 'Asia/Seoul')
        draft = self.settings.propose('owner', 'http', 'preparations', 'edit',
                                      json.dumps({'id': self.row['id'], 'due': '2026-11-05T09:00+09:00',
                                                  'timezone': 'Asia/Tokyo'}))
        self.assertEqual(json.loads(draft['after'])['timezone'], 'Asia/Tokyo', 'an explicit timezone is kept')

    def proposed(self):
        return self.service.preparations.create(kind='prepare', goal='한 번 준비', due_at=self.clock[0] + 7200,
                                                timezone='Asia/Seoul', recurrence=None, channel='web',
                                                created_from=None, state=prep.STATE_PROPOSED)

    def test_accepting_binds_the_schedule_the_owner_was_shown(self):
        """Codex P1 on #626: accepting authorizes the schedule, so a schedule change since makes it stale."""
        for change in ({'recurrence': 'daily'}, {'due': '2026-11-20T10:00', 'timezone': 'Asia/Seoul'}):
            with self.subTest(change=change):
                proposal = self.proposed()
                draft = self.settings.propose('owner', 'http', 'preparations', 'accept', proposal['id'])
                self.service.preparations.edit(proposal['id'], **change)
                with self.assertRaises(SettingsError):
                    self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
                self.assertEqual(self.service.preparations.get(proposal['id'])['state'], prep.STATE_PROPOSED,
                                 'nothing is scheduled')

    def test_accepting_the_unchanged_proposal_schedules_it(self):
        proposal = self.proposed()
        draft = self.settings.propose('owner', 'http', 'preparations', 'accept', proposal['id'])
        self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
        current = self.service.preparations.get(proposal['id'])
        self.assertEqual((current['state'], current['recurrence']), (prep.STATE_SCHEDULED, None))

    def test_pause_and_cancel_survive_a_watch_run_but_resume_binds_the_paused_schedule(self):
        pause = self.settings.propose('owner', 'http', 'preparations', 'pause', self.row['id'])
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=?,run_count=run_count+1 WHERE id=?',
                       (self.row['due_at'] + 86400, self.row['id']))
        self.settings.confirm('owner', 'http', pause['draft_id'], pause['digest'])
        self.assertEqual(self.service.preparations.get(self.row['id'])['state'], prep.STATE_PAUSED)
        resume = self.settings.propose('owner', 'http', 'preparations', 'resume', self.row['id'])
        self.service.preparations.edit(self.row['id'], recurrence='weekly')
        with self.assertRaises(SettingsError):
            self.settings.confirm('owner', 'http', resume['draft_id'], resume['digest'])
        self.assertEqual(self.service.preparations.get(self.row['id'])['state'], prep.STATE_PAUSED)

    def test_a_draft_names_its_own_preparation(self):
        other = self.service.preparations.create(kind='prepare', goal='다른 준비', due_at=self.clock[0] + 7200,
                                                 timezone='Asia/Seoul', recurrence='daily', channel='web',
                                                 created_from=None, state=prep.STATE_SCHEDULED)
        draft = self.settings.propose('owner', 'http', 'preparations', 'cancel', self.row['id'])
        self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
        self.assertEqual(self.service.preparations.get(other['id'])['state'], prep.STATE_SCHEDULED)


class ModelDraftBinding(_Case):
    """Codex P1 on #626: a model change is applied on the route current at apply time, so it binds that route."""

    def setUp(self):
        super().setUp()
        self.route = ['openai']
        self.applied = []
        self.service.main_ai.current = lambda: self.route[0]
        self.service.activate_main_ai = lambda body: self.applied.append(dict(body))
        self.row = {'route': {'value': self.route[0]}, 'model': {'value': ''}}
        self.settings._snapshot = lambda category: self.row if category == 'main_ai' else {}
        self.settings._normalized = lambda category, setting, value: (value, self.row)

    def test_a_route_switch_before_confirming_makes_the_model_draft_stale(self):
        draft = self.settings.propose('owner', 'http', 'main_ai', 'model', 'model-x')
        self.assertEqual(draft['before'], ' @ openai')
        self.route[0] = 'openrouter'
        with self.assertRaises(SettingsError):
            self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
        self.assertEqual(self.applied, [], 'the model is never set on another provider')

    def test_on_the_same_route_it_applies(self):
        draft = self.settings.propose('owner', 'http', 'main_ai', 'model', 'model-x')
        self.assertEqual(self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])['state'], 'applied')
        self.assertEqual(self.applied, [{'route': 'openai', 'model': 'model-x'}])


# -- decision 4: non-payment approvals stay open; payment keeps its bound -------------------------
class OpenUntilSettled(_Case):
    def test_a_settings_draft_has_no_expiry_and_applies_a_week_later(self):
        draft = self.draft('current_context', 'timezone', 'Asia/Seoul')
        self.assertIsNone(draft['expires_at'])
        self.clock[0] += 7 * 86400
        self.assertEqual([row['id'] for row in self.settings.pending_drafts()], [draft['draft_id']])
        self.assertEqual(self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])['state'], 'applied')
        self.assertEqual(self.context()['timezone'], 'Asia/Seoul')

    def test_cancelling_the_work_ends_what_it_left_waiting(self):
        job = self.store.job(self.store.enqueue('시간대를 서울로 바꿔 줘', 'tz-1'))
        draft = self.settings.propose('owner', 'http', 'current_context', 'timezone', 'Asia/Seoul', work_id=job['id'])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job['id'],))
        self.assertEqual(self.settings.pending_drafts(), [])
        with self.assertRaises(SettingsError):
            self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
        self.assertNotEqual(self.context().get('timezone'), 'Asia/Seoul')

    def test_a_payment_authorization_keeps_its_bounded_expiry(self):
        self.assertEqual(PAYMENT_APPROVAL_SECONDS, 600)
        job = self.store.job(self.store.enqueue('결제해 줘', 'pay-1'))
        binding = bs.step_binding(job['id'], 'api_request', 'https://pay.test/charge', 'slot|POST|https://pay.test/charge',
                                  argument='{"amount":"10"}', state='api-payment-v2|slot@1|payment')
        self.service._request_browser_step(job, binding, 'POST /charge')
        self.assertTrue(self.service.step_approval_pending(job['id']))
        row = self.service._browser_request(job['id'])
        with mock.patch('personal_agent.quickstart_service.time.time',
                        return_value=float(row['requested_at']) + PAYMENT_APPROVAL_SECONDS + 1):
            self.assertFalse(self.service.step_approval_pending(job['id']))
            self.assertFalse(self.service._decide_browser_step(job['id'], True, row['offer_digest'])['approved'])


# -- decision 5: unattended runs never leave anything waiting ------------------------------------
class UnattendedRuns(_Case):
    def setUp(self):
        super().setUp()
        self.preparation = self.service.preparations.create(
            kind='prepare', goal='매일 확인', due_at=self.clock[0], timezone='UTC', recurrence='daily', channel='web',
            created_from=None, state=prep.STATE_SCHEDULED)
        self.run = self.store.job(self.store.enqueue('매일 확인', prep.request_key(self.preparation['id'], self.clock[0])))
        self.owned = self.store.job(self.store.enqueue('직접 요청', 'owner-1', owner_typed=True))

    def test_a_scheduled_run_drafts_nothing_and_asks_no_judgment(self):
        with mock.patch.object(self.service.decision_judge, 'explicit_settings_request',
                               side_effect=AssertionError('no owner to ask about')):
            result = self.service.settings_tools(self.run)('settings_change', {
                'category': 'preparations', 'setting': 'cancel', 'value': self.preparation['id']})
        self.assertEqual((result['state'], result['applied']), ('not_proposed', False))
        self.assertEqual(self.settings._drafts(), {})
        self.assertEqual(self.service.preparations.get(self.preparation['id'])['state'], prep.STATE_SCHEDULED)
        self.assertEqual(self.service.work_waits_for_owner(self.run), ())

    def test_a_scheduled_run_records_no_step_approval(self):
        binding = bs.step_binding(self.run['id'], 'browser_click', 'https://shop.test/', 'pay', 'pay', 'state')
        with self.assertRaises(ToolError) as caught:
            self.service.browser_approvals_for(self.run).request(binding, 'pay')
        self.assertEqual(caught.exception.code, 'unattended_run')
        self.assertIsNone(self.service._browser_request(self.run['id']))
        self.assertFalse(self.service.browser_approvals_for(self.run).consume(binding))
        self.assertFalse(self.service.browser_approvals_for(self.owned).consume(binding))

    def test_its_refusals_are_classified_none_under_the_one_effect_rule(self):
        """#618: what Still refused or drafted nothing for changed nothing outside, so it never blocks continuing."""
        from personal_agent.agent_runtime import EFFECT_NONE, _evidence_detail, record_effect
        result = self.service.settings_tools(self.run)('settings_change', {
            'category': 'preparations', 'setting': 'cancel', 'value': self.preparation['id']})
        self.assertEqual(record_effect('settings_change', {'evidence': _evidence_detail('settings_change', result)},
                                       'succeeded'), EFFECT_NONE)
        with self.assertRaises(ToolError) as caught:
            self.service.preparation_scheduler(self.run, self.run['message'])({'kind': 'prepare', 'goal': 'x', 'due': '2030-01-01T09:00'})
        self.assertIs(caught.exception.ran, False)
        binding = bs.step_binding(self.run['id'], 'api_request', 'https://pay.test/', 'x', argument='', state='s')
        with self.assertRaises(ToolError) as caught:
            self.service._request_browser_step(self.run, binding, 'pay')
        self.assertIs(caught.exception.ran, False)
        self.assertEqual(record_effect('schedule_preparation', {'code': 'unattended_run', 'ran': False}, 'failed'),
                         EFFECT_NONE)

    def test_a_withdrawal_receipt_does_not_block_continuing(self):
        self.settings.propose('owner', 'http', 'current_context', 'timezone', 'Asia/Seoul', work_id=self.run['id'])
        self.assertEqual(self.service.retire_unattended_waits(), 1)
        self.assertEqual(self.service.may_continue(self.run['id'], 'resume'), (True, None))

    def test_what_earlier_scheduled_runs_left_waiting_is_withdrawn_with_a_receipt(self):
        draft = self.settings.propose('owner', 'http', 'current_context', 'timezone', 'Asia/Seoul', work_id=self.run['id'])
        kept = self.settings.propose('owner', 'http', 'current_context', 'enabled', 'on', work_id=self.owned['id'])
        binding = bs.step_binding(self.run['id'], 'api_request', 'https://pay.test/', 'x', argument='', state='s')
        requests = {self.run['id']: {'work_id': self.run['id'], 'action': 'api_request', 'state': 'requested',
                                     'requested_at': self.clock[0], 'offer_digest': 'o' * 64,
                                     **self.service._browser_step_keys(binding)}}
        self.store.put('browser_step_requests', requests)
        proposal = self.service.preparations.create(kind='prepare', goal='새 제안', due_at=self.clock[0] + 86400,
                                                    timezone='UTC', recurrence=None, channel='web',
                                                    created_from=self.run['id'], state=prep.STATE_PROPOSED)
        self.assertEqual(self.service.retire_unattended_waits(), 3)
        rows = self.settings._drafts()
        self.assertEqual(rows[draft['draft_id']]['state'], 'canceled', 'withdrawn, never deleted')
        self.assertEqual(rows[kept['draft_id']]['state'], 'awaiting-confirmation', 'an owner Work keeps its draft')
        self.assertIsNone(self.service._browser_request(self.run['id']))
        self.assertEqual(self.service.preparations.get(proposal['id'])['state'], prep.STATE_EXPIRED)
        receipts = [e['trace'] for e in self.store.task_events(self.run['id']) if e['tool'] == 'action_continuation']
        self.assertEqual(sorted(r['withdrawn'] for r in receipts),
                         ['preparation-proposal', 'settings-draft', 'step-approval'])
        self.assertTrue(all(r['reason'] == 'unattended-run' and r['resumed'] is False for r in receipts))
        audit = [e for e in self.store.config('settings_audit', []) if e.get('draft_ref') == draft['draft_id']]
        self.assertEqual((audit[-1]['terminal'], audit[-1]['error_class']), ('canceled', 'unattended-run'))
        self.assertEqual(self.service.retire_unattended_waits(), 0, 'once')


# -- decision 6: the owner's direct request is its own approval, except payment ------------------
class OwnerDirectRequest(_Case):
    def setUp(self):
        super().setUp()
        self.preparation = self.service.preparations.create(
            kind='prepare', goal='주가 확인', due_at=self.clock[0] + 3600, timezone='UTC', recurrence='daily',
            channel='web', created_from=None, state=prep.STATE_SCHEDULED)
        self.asked = []

    def judged(self, outcome):
        def answer(message, change, conversation='', work_id=None):
            self.asked.append((message, change))
            return Judgment(outcome)
        return mock.patch.object(self.service.decision_judge, 'explicit_settings_request', side_effect=answer)

    def change(self, job, category='preparations', setting='cancel', value=None):
        return self.service.settings_tools(job)('settings_change', {
            'category': category, 'setting': setting, 'value': self.preparation['id'] if value is None else value})

    def test_the_owners_own_typed_request_applies_without_a_second_confirmation(self):
        job = self.store.job(self.store.enqueue('주가 확인 예약 취소해 줘', 'owner-1', owner_typed=True))
        with self.judged(JUDGMENT_YES):
            result = self.change(job)
        self.assertEqual((result['state'], result['applied'], result['owner_request']), ('applied', True, True))
        self.assertNotIn('requires_owner_confirmation', result)
        self.assertEqual(self.service.preparations.get(self.preparation['id'])['state'], prep.STATE_CANCELLED)
        [(message, change)] = self.asked
        self.assertEqual(message, '주가 확인 예약 취소해 줘')
        self.assertIn('주가 확인', change, 'the judgment reads Still\'s exact preview')
        self.assertEqual(self.settings.pending_drafts(), [])
        terminals = [e['terminal'] for e in self.store.config('settings_audit', []) if e.get('draft_ref') == result['draft_id']]
        self.assertIn('owner-request', terminals)

    def test_a_change_the_owner_did_not_ask_for_waits_for_the_owner(self):
        job = self.store.job(self.store.enqueue('오늘 날씨 알려줘', 'owner-2', owner_typed=True))
        with self.judged(JUDGMENT_NO):
            result = self.change(job)
        self.assertTrue(result['requires_owner_confirmation'])
        self.assertEqual(self.service.preparations.get(self.preparation['id'])['state'], prep.STATE_SCHEDULED)

    def test_a_message_still_ran_or_a_continuation_is_never_its_own_approval(self):
        from personal_agent.context_observations import CONTINUATION_KEY_PREFIX
        replayed = self.store.job(self.store.enqueue('주가 확인 예약 취소해 줘', 'replayed-1'))
        continued = self.store.job(self.store.enqueue('주가 확인 예약 취소해 줘', CONTINUATION_KEY_PREFIX + 'r1',
                                                      owner_typed=True))
        for job in (replayed, continued):
            with self.subTest(key=job['request_key']), self.judged(JUDGMENT_YES):
                self.assertIsNone(self.service.owner_settings_request(job))
                result = self.change(job)
                self.assertTrue(result['requires_owner_confirmation'])
        self.assertEqual(self.asked, [])
        self.assertEqual(self.service.preparations.get(self.preparation['id'])['state'], prep.STATE_SCHEDULED)

    def test_a_change_that_widens_authority_or_a_destination_always_waits(self):
        job = self.store.job(self.store.enqueue('AI 연결 켜 줘', 'owner-3', owner_typed=True))
        with self.judged(JUDGMENT_YES):
            result = self.service.settings_tools(job)('settings_change', {
                'category': 'ai_connections', 'setting': 'enabled', 'value': 'on'})
        self.assertTrue(result['requires_owner_confirmation'])
        self.assertEqual(self.asked, [], 'not even asked')


if __name__ == '__main__':
    unittest.main()
