"""#679: a proposal still awaiting the owner's acceptance ends a Work the same way on both routes.

Evidence class: model-free service tests.  The direct route runs the real ``run_agent`` with a
scripted compatible-API model; the CLI route runs ``AgentService.run_one`` with a fake subscription
CLI whose turn drives the real ``mcp_bridge.serve`` request handler in-process, relayed to the
service's own Capabilities (only the relay socket and the stdio loop are replaced).  The real
QuickStore, preparations, tool events, Telegram delivery and callback handler run unchanged.  No
live model, CLI, Telegram or account is used.  All text is made up.
"""
import json
import unittest
from unittest import mock

from personal_agent import mcp_bridge
from personal_agent import preparations as prep
from personal_agent.agent_runtime import (AWAITING_OWNER_STEP_FACT, failed_steps_text,
                                          outcome_from_events, trail_steps)
from personal_agent.bounded_execution import ExecutionResult
from personal_agent.conversation_projection import (TERMINAL_FAILED_NOTE, TERMINAL_PARTIAL_NOTE, TERMINAL_UNCONFIRMED_NOTE,
                                                    TERMINAL_UNFINISHED_LABEL)
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.subscription_engines import SubscriptionEngines
from personal_agent.telegram_presence import DONE_REACTION, WROTE_REACTION

from test_preparations import _Case, call, engine, finish

GOAL = 'Example lunch shortlist'
REPLY = 'I proposed a daily example lunch shortlist. Accept it and it will be scheduled.'
REQUEST = 'Prepare an example lunch shortlist for me.'


def bridge_event(evidence, tool='schedule_preparation'):
    """What ``mcp_bridge`` records for a call that returned normally."""
    return (tool, 'succeeded', json.dumps({'scope': 'subscription-mcp-bridge', 'call_id': 'c1', 'host_action': tool,
                                           'evidence': evidence}))


PROPOSED = {'preparation_id': 'p1', 'kind': 'prepare', 'state': 'proposed', 'scheduled': False,
            'requires_owner_acceptance': True}
SCHEDULED = {'preparation_id': 'p2', 'kind': 'prepare', 'state': 'scheduled', 'scheduled': True,
             'accepted_by': 'owner-request'}


class RecordedStepState(unittest.TestCase):
    """The one reading of the CLI route's recorded calls classifies as ``withheld_effect`` does."""

    def test_an_unaccepted_proposal_is_withheld_and_advances_the_work(self):
        [step] = trail_steps([bridge_event(PROPOSED)])
        self.assertEqual(step[2], 'withheld')
        self.assertEqual(outcome_from_events([bridge_event(PROPOSED)]), ('partial', []), 'awaiting, never failed')
        # #170: the judgment reads it as awaiting the owner, not as a failure.
        self.assertEqual(failed_steps_text([step]), f'schedule_preparation: {AWAITING_OWNER_STEP_FACT}')

    def test_a_scheduled_preparation_is_unchanged(self):
        self.assertEqual(trail_steps([bridge_event(SCHEDULED)])[0][2], 'succeeded')
        self.assertEqual(outcome_from_events([bridge_event(SCHEDULED)]), ('succeeded', []))

    def test_every_waiting_owner_decision_is_read_the_same_way(self):
        # Not a preparation rule: each result withheld_effect says advanced the Work while it waits for the
        # owner is withheld on this route too, and the Work is partial.
        draft = {'draft_id': 'd1', 'applied': False, 'requires_owner_confirmation': True}
        for tool, evidence in (('schedule_preparation', PROPOSED), ('settings_change', draft)):
            with self.subTest(tool=tool):
                self.assertEqual(trail_steps([bridge_event(evidence, tool)])[0][2], 'withheld')
                self.assertEqual(outcome_from_events([bridge_event(evidence, tool)]), ('partial', []))
        # A refused write advanced nothing: withheld and failed, as before.
        refused = {'saved': False, 'state': 'refused', 'refused_because': 'secret-shaped-value'}
        self.assertEqual(outcome_from_events([bridge_event(refused, 'save_memory')]), ('failed', []))
        # A draft the owner then completed is no longer waiting (#1445).
        completed = ('settings_continuation', 'queued', json.dumps({'draft_ids': ['d1']}))
        self.assertEqual(outcome_from_events([bridge_event(draft, 'settings_change'), completed]), ('succeeded', []))

    def test_a_missing_sign_in_is_read_by_the_same_rule(self):
        # #690: a sign-in page is withheld on this route too, and waiting for the owner keeps the Work partial.
        event = bridge_event({'state': 'login_required'}, 'browser_open')
        self.assertEqual(trail_steps([event])[0][2], 'withheld')
        self.assertEqual(outcome_from_events([event]), ('partial', []))


class ProposalRoutes(_Case):
    """One Work whose only call is an unaccepted proposal, on the direct and on the CLI route."""

    def setUp(self):
        super().setUp()
        self.reactions = []
        transport = self._telegram

        def telegram(url, body=None, headers=None, timeout=60):
            if url.endswith('/setMessageReaction'):
                self.reactions.append(body)
            return transport(url, body, headers, timeout)
        self._telegram = telegram
        self.service = self.make_service()
        self.service.use_decision_engine(engine(preparation=False))

    def arguments(self):
        return {'kind': 'prepare', 'goal': GOAL, 'due': self.due_iso(1800), 'recurrence': 'daily', 'delivery': 'send'}

    def direct(self, owner_typed=False):
        """``owner_typed`` False (#717): a Work the owner did not type, where the call only proposes."""
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', **self.arguments())]},
                       finish('f', '1', summary=REPLY)]
        return self.receive(REQUEST, owner_typed=owner_typed)

    def cli(self, preparation=False, owner_typed=False):
        test = self

        class Cli:
            def execute(self, engine_id, prompt, tools, **kwargs):
                class Relay:
                    def call(self, name, args):
                        return tools.call(name, args)

                def protocol(handle, concurrent=None):
                    reply = handle('tools/call', {'name': 'schedule_preparation', 'arguments': test.arguments()})
                    test.assertNotIn('isError', reply, reply)
                with mock.patch.object(mcp_bridge, 'RelayClient', return_value=Relay()), \
                        mock.patch.object(mcp_bridge, '_serve_stdio', side_effect=protocol):
                    mcp_bridge.serve(str(test.store.root), tools.capabilities.job_id, browser_relay='/fixture-relay')
                return ExecutionResult(REPLY, engine_id, 0, {})

            def login_status(self, engine_id, binary=None):
                return {'state': 'signed-in'}

        self.service = AgentService(self.store, ModelAdapter(self._model), self._telegram, execution_adapter=Cli(),
                                    subscription_engines=SubscriptionEngines(finder=lambda _: '/runtime/cli', clock=lambda: 1))
        self.service.preparations.clock = lambda: self.now
        self.service.context_observations.clock = lambda: self.now
        self.service.use_decision_engine(engine(preparation=preparation))
        self.service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        # The sandbox forbids Unix socket listeners; only the relay's construction is replaced.
        with mock.patch('personal_agent.quickstart_service.BrowserRelay') as relay:
            relay.return_value.address = '/fixture-relay'
            return self.receive(REQUEST, owner_typed=owner_typed)

    def finish_work(self, work):
        before = len(self.sends())
        self.service.deliver_one()
        self.service.deliver_notification()
        return self.store.job(work), [body['text'] for body in self.sends()[before:]]

    def assert_awaiting(self, work):
        row, texts = self.finish_work(work)
        [preparation] = self.rows()
        self.assertEqual((preparation['state'], preparation['accepted_by']), ('proposed', None), 'nothing was scheduled')
        self.assertEqual(row['status'], 'partial')
        # No success reaction: a proposal is not a done request.
        self.assertFalse([body for body in self.reactions
                          if any(item.get('emoji') in (DONE_REACTION, WROTE_REACTION) for item in body.get('reaction') or ())])
        # The closing line never calls the waiting proposal unfinished or failed.
        reply = texts[0]
        for unfinished in (TERMINAL_UNFINISHED_LABEL, TERMINAL_PARTIAL_NOTE, TERMINAL_FAILED_NOTE, TERMINAL_UNCONFIRMED_NOTE):
            self.assertNotIn(unfinished, reply)
        # The owner's next step is said: the proposal message with its accept button.
        self.assertTrue(any(GOAL in text for text in texts[1:]), texts)
        return row, preparation

    def accept(self, work, preparation):
        with self.store.db() as db:
            notification = dict(db.execute("SELECT * FROM telegram_notifications WHERE job_id=? AND kind='preparation_proposed'",
                                           (work,)).fetchone())
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        accepted = self.service.preparations.get(preparation['id'])
        self.assertEqual((accepted['state'], accepted['accepted_by']), (prep.STATE_SCHEDULED, 'owner-button'))

    def test_both_routes_store_the_same_status_and_acceptance_still_schedules(self):
        statuses = {}
        for route in ('direct', 'cli'):
            with self.subTest(route=route):
                for row in self.rows():
                    self.service.preparations.cancel(row['id'])
                with self.store.db() as db:
                    db.execute('DELETE FROM preparations')
                # No procedure is published from a Work that only proposed (#1486: only a reached Work).
                with mock.patch('personal_agent.procedures.ProcedureLibrary.settle', return_value=[]) as settle:
                    work = getattr(self, route)()
                settle.assert_not_called()
                row, preparation = self.assert_awaiting(work)
                statuses[route] = row['status']
                self.accept(work, preparation)
        self.assertEqual(statuses, {'direct': 'partial', 'cli': 'partial'})

    def test_what_the_owners_typed_work_sets_up_ends_it_succeeded_on_both_routes(self):
        """#717: in the owner's own typed Work the preparation is scheduled (``owner-work``), not left waiting."""
        for route in ('direct', 'cli'):
            with self.subTest(route=route):
                with self.store.db() as db:
                    db.execute('DELETE FROM preparations')
                work = getattr(self, route)(owner_typed=True)
                row, _texts = self.finish_work(work)
                [preparation] = self.rows()
                self.assertEqual((preparation['state'], preparation['accepted_by']),
                                 (prep.STATE_SCHEDULED, prep.ACCEPTED_OWNER_WORK))
                self.assertEqual(row['status'], 'succeeded')

    def test_an_accepted_preparation_is_unchanged_on_the_cli_route(self):
        work = self.cli(preparation=True, owner_typed=True)
        row, _texts = self.finish_work(work)
        [preparation] = self.rows()
        self.assertEqual(preparation['state'], prep.STATE_SCHEDULED)
        self.assertEqual(row['status'], 'succeeded')


if __name__ == '__main__':
    unittest.main()
