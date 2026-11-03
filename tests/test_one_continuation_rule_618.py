"""#618 (E1/E2 of #614): one effect class per call and one continuation rule.

Real stores and the real API layer with an injected transport; recorded tool events are inserted as
the routes record them.  No model, browser or account calls.  The negative tests hold invariant c: a
real change or an unknown external effect is never replayed or continued past.
"""
import email.utils
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from personal_agent.agent_runtime import (EFFECT_NONE, EFFECT_UNKNOWN, ToolError, _error_observation, classify_failure,
                                          handed_off, record_effect, refused_before_hand_off)
from personal_agent.api_requests import ApiError, ApiRequests, save_slot
from personal_agent.orchestrator import (EFFECT_UNKNOWN_STOP, NEXT_TEXT, OWNER_NEEDED, STOP_EFFECT, Orchestration)
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

POST = {'slot': 'alpha', 'url': 'https://alpha.example/change', 'method': 'POST', 'effect': 'mutate', 'body': '{"v":1}'}
OTHER = {'slot': 'beta', 'url': 'https://beta.example/change', 'method': 'POST', 'effect': 'mutate', 'body': '{"v":1}'}


class Store(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name))
        self.service = AgentService(self.store)
        self.work = self.store.enqueue('apply the change', 'k618')
        self.job = self.store.job(self.work)
        save_slot(self.store, 'alpha', ['alpha.example'], 'alpha-credential-618')
        save_slot(self.store, 'beta', ['beta.example'], 'beta-credential-618')
        self.sent = []

    def client(self, answer):
        def transport(*args):
            self.sent.append(args)
            if isinstance(answer, BaseException):
                raise answer
            return answer, {'last-modified': email.utils.formatdate(usegmt=True)}, b'{"error":"refused"}'
        return ApiRequests(self.store, self.work, transport=transport,
                           approvals=self.service.browser_approvals_for(self.store.job(self.work)))

    def event(self, tool, status, detail):
        with self.store.db() as db:
            return db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,0)',
                              (self.work, tool, status, json.dumps(detail))).lastrowid

    def call(self, action, terminal, status, call_id):
        base = {'scope': 'subscription-mcp-bridge', 'call_id': call_id, 'host_action': action}
        declared = {key: value for key, value in terminal.items() if key == 'declared_effect'}
        self.event(action, 'running', {**base, **declared})
        self.event(action, status, {**base, **terminal})


class NotHandedOff(unittest.TestCase):
    """E1: every refusal Still makes before a hand-off is ``ran: false``, a click's included."""

    class Tools:
        tools = {'browser_click': {'host_action': 'browser_click'}, 'tell_owner': {'host_action': 'tell_owner'}}

        def __init__(self, before):
            self.before = before

        @refused_before_hand_off
        def execute(self, name, args):
            if not self.before:
                handed_off()
            raise ToolError('held', 'approval_required')

    def test_a_click_refused_before_its_press_is_not_run(self):
        with self.assertRaises(ToolError) as caught:
            self.Tools(before=True).execute('browser_click', {})
        self.assertIs(caught.exception.ran, False)
        observation = _error_observation(caught.exception, True, 'browser_click')
        self.assertEqual(record_effect('browser_click', {**observation, 'declared_effect': 'mutate'}, 'failed'), EFFECT_NONE)

    def test_a_click_that_reached_the_page_is_never_marked(self):
        # Negative: once the press was handed to the page, a failure stays a possible change.
        with self.assertRaises(ToolError) as caught:
            self.Tools(before=False).execute('browser_click', {})
        self.assertIsNone(getattr(caught.exception, 'ran', None))
        observation = _error_observation(caught.exception, True, 'browser_click')
        self.assertNotEqual(record_effect('browser_click', {**observation, 'declared_effect': 'mutate'}, 'failed'), EFFECT_NONE)

    def test_a_pre_send_refusal_naming_an_earlier_unknown_records_no_new_unknown(self):
        held = ToolError('earlier effect unknown', 'effect_unknown', ran=False)
        held.effect = 'unknown'
        self.assertEqual(classify_failure(held, 'api_request'), ('effect_unknown', 'never', 'none'))
        sent = ToolError('no answer', 'effect_unknown')
        sent.effect = 'unknown'
        self.assertEqual(classify_failure(sent, 'api_request'), ('effect_unknown', 'never', 'unknown'))


class ServerSaidNotExecuted(Store):
    """E1: a request the service refused before executing it is ``none``; no answer stays ``unknown``."""

    def test_a_pre_execution_refusal_is_settled_rejected_and_classified_none(self):
        for status in (400, 401, 403, 409, 412, 422, 429):
            with self.subTest(status=status):
                self.setUp()
                with self.assertRaises(ApiError) as caught:
                    self.client(status).call(POST)
                self.assertIs(caught.exception.executed, False)
                self.assertIsNone(caught.exception.effect)
                with self.store.db() as db:
                    [dispatch] = db.execute("SELECT status FROM tool_events WHERE tool='api_dispatch'").fetchall()
                self.assertEqual(dispatch['status'], 'rejected')
                error = ToolError(str(caught.exception), caught.exception.code)
                error.executed = False
                observation = _error_observation(error, True, 'api_request')
                self.assertIs(observation['executed'], False)
                self.call('api_request', {'declared_effect': 'mutate', **observation}, 'failed', 'c1')
                self.assertEqual(self.service.continuation_reasons(self.work, 'retry'), ())
                self.assertEqual(self.service.continuation_reasons(self.work, 'redelegate'), ())
                self.assertEqual(self.service.continuation_reasons(self.work, 'resume'), ())
                self.assertFalse(self.service._work_has_unknown_effect(self.work))
                # The corrected request may be sent: nothing was applied, nothing is unknown.
                self.assertEqual(len(self.sent), 1)
                with self.assertRaises(ApiError):
                    self.client(status).call(POST)
                self.assertEqual(len(self.sent), 2, 'the refused request did not hold a corrected one')

    def test_a_server_error_or_no_answer_stays_unknown_and_holds_the_same_slot(self):
        # Negative: a 5xx or a lost answer is indeterminate; the same change is never sent again.
        for answer in (500, TimeoutError()):
            with self.subTest(answer=type(answer).__name__):
                self.setUp()
                with self.assertRaises(ApiError) as caught:
                    self.client(answer).call(POST)
                self.assertEqual(caught.exception.effect, 'unknown')
                self.assertIsNone(caught.exception.executed)
                with self.assertRaises(ApiError) as again:
                    self.client(200).call({**POST, 'body': '{"v":2}'})
                self.assertEqual(again.exception.code, 'effect_unknown')
                self.assertEqual(len(self.sent), 1, 'nothing more was sent to the slot whose outcome is unknown')
                self.assertTrue(self.service._work_has_unknown_effect(self.work))
                for purpose in ('retry', 'redelegate', 'resume', 'steer'):
                    self.assertEqual(self.service.may_continue(self.work, purpose), (False, 'unknown'))


class UnknownIsPerCall(Store):
    """E1: an unknown effect holds its own target until reconciled, not every later write."""

    def test_an_unknown_on_one_slot_does_not_hold_another_slot(self):
        with self.assertRaises(ApiError):
            self.client(TimeoutError()).call(POST)
        result = self.client(200).call(OTHER)
        self.assertEqual(result['provenance']['status'], 200)
        self.assertEqual([args[1] for args in self.sent], [POST['url'], OTHER['url']])

    def test_the_same_slot_and_a_slot_sharing_its_host_stay_held(self):
        save_slot(self.store, 'alpha-two', ['alpha.example'], 'alpha-two-credential-618')
        with self.assertRaises(ApiError):
            self.client(TimeoutError()).call(POST)
        for args in (POST, {**POST, 'slot': 'alpha-two'}):
            with self.subTest(slot=args['slot']):
                with self.assertRaises(ApiError) as caught:
                    self.client(200).call({**args, 'body': '{"v":3}'})
                self.assertEqual(caught.exception.code, 'effect_unknown')
        self.assertEqual(len(self.sent), 1)

    def test_an_unscoped_or_cross_route_unknown_still_holds_the_slot(self):
        # Negative: a browser press of unknown outcome (another route) and an unknown Still cannot scope.
        for detail in ({'declared_effect': 'mutate', 'effect': 'unknown', 'code': 'effect_unknown',
                        'arguments': {'target': '3'}, 'url': 'https://shop.example/cart'},
                       {'effect': 'unknown', 'code': 'effect_unknown'}):
            with self.subTest(detail=sorted(detail)):
                self.setUp()
                self.call('browser_click', detail, 'failed', 'c9')
                with self.assertRaises(ApiError) as caught:
                    self.client(200).call(POST)
                self.assertEqual(caught.exception.code, 'effect_unknown')
                self.assertEqual(self.sent, [])


class OneContinuationRule(Store):
    """E2: one rule for re-delegation, resume, retry, parking, steer and budget-continue."""

    def test_owner_local_writes_and_refusals_never_hold_a_continuation(self):
        self.call('tell_owner', {'evidence': {'sent': True}}, 'succeeded', 't1')
        self.call('tell_owner', {'code': 'tool_failed', 'effect': 'unknown'}, 'failed', 't2')
        self.call('save_memory', {'evidence': {'state': 'current'}}, 'succeeded', 'm1')
        self.call('browser_click', {'declared_effect': 'mutate', 'code': 'approval_required', 'effect': 'none',
                                    'ran': False}, 'failed', 'b1')
        self.call('browser_click', {'declared_effect': 'navigate', 'code': 'effect_unknown', 'effect': 'unknown',
                                    'observed_step': {'posted': False, 'approved': False}}, 'failed', 'b2')
        for purpose in ('retry', 'redelegate', 'resume', 'steer'):
            with self.subTest(purpose=purpose):
                self.assertEqual(self.service.may_continue(self.work, purpose), (True, None))
        self.assertEqual(self.service.safe_retry({**self.job, 'status': 'failed'}), (True, None))

    def test_a_real_change_is_never_replayed(self):
        # Negative: a definite change holds every purpose that would start the request again.
        self.call('browser_click', {'declared_effect': 'mutate', 'evidence': {'state': 'page'}}, 'succeeded', 'b1')
        for purpose in ('retry', 'redelegate', 'steer', 'resume'):
            with self.subTest(purpose=purpose):
                self.assertEqual(self.service.may_continue(self.work, purpose), (False, 'effect'))
        allowed, _reason = self.service.safe_retry({**self.job, 'status': 'failed'})
        self.assertFalse(allowed)

    def test_a_change_the_replay_key_guards_is_carried_by_a_resume_only(self):
        self.client(200).call(POST)  # a 200 settles the dispatch observed for this Work
        with self.store.db() as db:
            [dispatch] = db.execute("SELECT id,status FROM tool_events WHERE tool='api_dispatch'").fetchall()
        self.assertEqual(dispatch['status'], 'observed')
        self.call('api_request', {'declared_effect': 'mutate', 'evidence': {'dispatch_event': dispatch['id']}}, 'succeeded', 'a1')
        self.assertEqual(self.service.may_continue(self.work, 'resume'), (True, None))
        for purpose in ('retry', 'redelegate', 'steer'):
            with self.subTest(purpose=purpose):
                self.assertEqual(self.service.may_continue(self.work, purpose), (False, 'effect'))
        # And the key itself still refuses the same input on the resumed Work.
        with self.assertRaises(ApiError) as caught:
            self.client(200).call(POST)
        self.assertEqual(caught.exception.code, 'api_effect_replayed')

    def test_a_call_cut_off_before_its_answer_is_unknown(self):
        self.event('browser_click', 'running', {'scope': 's', 'call_id': 'cut', 'host_action': 'browser_click',
                                                'declared_effect': 'mutate'})
        for purpose in ('retry', 'redelegate', 'resume', 'steer'):
            self.assertEqual(self.service.may_continue(self.work, purpose), (False, 'unknown'))

    def test_owner_prerequisites_are_a_named_reason_and_unknown_is_stronger(self):
        self.assertEqual(self.service.may_continue(self.work, 'redelegate', owner_waits=True), (False, 'owner'))
        self.assertEqual(self.service.may_continue(self.work, 'resume'), (True, None))
        self.call('settings_change', {'evidence': {'applied': False, 'requires_owner_confirmation': True}}, 'succeeded', 's1')
        self.assertEqual(self.service.may_continue(self.work, 'redelegate'), (False, 'owner'))
        self.assertEqual(self.service.may_continue(self.work, 'resume'), (True, None), 'a draft is no external effect')
        self.call('api_request', {'declared_effect': 'mutate', 'code': 'effect_unknown', 'effect': 'unknown'}, 'failed', 'u1')
        self.assertEqual(self.service.continuation_reasons(self.work, 'redelegate', unaccounted=True),
                         ('unknown', 'unaccounted', 'owner'))


class OwnerFacingReason(unittest.TestCase):
    """E2: the stop text names what was recorded; ``owner_needed`` only with a recorded prerequisite."""

    def test_the_effect_stop_never_claims_an_irreversible_action(self):
        self.assertNotIn('되돌릴 수 없는', NEXT_TEXT[STOP_EFFECT])
        self.assertNotIn('실행되어', NEXT_TEXT[STOP_EFFECT + ':unknown'])
        self.assertEqual(EFFECT_UNKNOWN_STOP, EFFECT_UNKNOWN)

    def test_a_question_alone_is_no_owner_prerequisite(self):
        orchestration = Orchestration(SimpleNamespace(), SimpleNamespace(), request='how long?')
        asked = SimpleNamespace(outcome='partial', report={'question': 'From where?'}, judgment=None)
        self.assertNotEqual(orchestration.evaluate_run(asked), OWNER_NEEDED)
        self.assertEqual(orchestration.evaluate_run(asked, owner_needed=True), OWNER_NEEDED)


if __name__ == '__main__':
    unittest.main()
