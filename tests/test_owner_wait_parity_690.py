"""#690: a wait only the owner can resolve ends ``partial`` on both routes, never ``failed``.

Owner decision 2026-11-06: a page that needs the owner's sign-in (#709/#981) and a missing
connection (#774) end ``partial`` on the direct route and on the subscription-CLI route, whether or
not another step advanced first.  Both routes read the call with the one typed rule
(``withheld_effect`` / ``withheld_partial``); a refused write still fails.

Evidence class: model-free unit and service tests.  The direct route runs the real ``run_agent``
with a scripted compatible-API model and the real Capabilities (fake page driver, no connections);
the CLI route reads the records its bridge writes for the same results (``evidence_summary``, as
``mcp_bridge`` records a normal return), and the service tests run ``AgentService.run_one`` on both
routes with the real in-flow login.  No live model, CLI, site or account is used.  All text is made up.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import mcp_bridge
from personal_agent import work_status as ws
from personal_agent import agent_runtime as runtime
from personal_agent.agent_runtime import (Capabilities, evidence_summary, outcome_from_events,
                                          run_agent, trail_steps)
from personal_agent.bounded_execution import ExecutionResult
from personal_agent.conversation_projection import TERMINAL_FAILED_HEADER, TERMINAL_FAILED_NOTE
from personal_agent.messages import msg
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.subscription_engines import SubscriptionEngines

from test_browser_session import CFG, ORIGIN, FakeDriver, Script, call
from test_flow_login import LoginHarness

REQUEST = 'Made-up request for the example account page.'
REPLY = 'Made-up reply.'
SIGN_IN = msg('agent_runtime.this_page_needs_a_sign_in')

#: One call per owner-resolvable wait, as the model writes it; the tool itself returns the wait.
WAITS = {
    'sign-in page': call('w', 'browser_open', url=ORIGIN + '/login', effect='navigate'),
    'sign-in request': call('w', 'browser_sign_in', url=ORIGIN + '/account'),
    'calendar connection': call('w', 'calendar_query', start='2026-11-06T00:00:00Z', end='2026-11-07T00:00:00Z',
                                 timezone='UTC'),
    'drive connection': call('w', 'drive_search', query='made-up file'),
    'mail connection': call('w', 'mail_search', query='made-up message'),
}
#: A step that advances the Work before the wait.
ADVANCE = call('a', 'list_notes')


def bridge_rows(results):
    """What the CLI route's bridge records for these normal returns (``mcp_bridge`` ``tools/call``)."""
    return [(name, 'succeeded', json.dumps({'scope': 'subscription-mcp-bridge', 'call_id': str(index), 'host_action': name,
                                            'evidence': evidence_summary(name, result)}))
            for index, (name, result) in enumerate(results)]


class SameRuleBothRoutes(unittest.TestCase):
    """The same tool results end with the same status on the direct route and on the CLI route."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = QuickStore(Path(self.tmp.name) / 'data')

    def routes(self, calls):
        """``(direct outcome, CLI outcome, the CLI route's records)`` for one scripted turn."""
        events, results = [], []
        record = lambda tool, status, detail: events.append((tool, status, detail))
        caps = Capabilities(self.store, ModelAdapter(Script({'tool_calls': calls}, {'content': REPLY})), CFG, '', 'job', record,
                            browser=lambda: FakeDriver())
        offered = {definition['function']['name'] for definition in caps.definitions()}
        self.assertTrue({item['function']['name'] for item in calls} <= offered, offered)
        execute = caps.execute

        def observed(name, args):
            result = execute(name, args)
            results.append((name, result))
            return result
        caps.execute = observed
        direct = run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': REQUEST}], '', caps, record)
        rows = bridge_rows(results)
        return direct.outcome, outcome_from_events(rows)[0], rows

    def test_each_wait_is_partial_on_both_routes_whether_or_not_another_step_advanced_first(self):
        for kind, wait in WAITS.items():
            for advanced in (False, True):
                with self.subTest(kind=kind, advanced=advanced):
                    calls = [ADVANCE, wait] if advanced else [wait]
                    direct, cli, rows = self.routes(calls)
                    self.assertEqual((direct, cli), ('partial', 'partial'), 'waiting for the owner, never failed')
                    # The CLI route reads the wait as the direct route does: withheld, with the owner's step.
                    self.assertEqual(trail_steps(rows)[-1][2], 'withheld')
                    waits = runtime.owner_wait_steps(rows)
                    self.assertEqual([tool for tool, _reason in waits], [wait['function']['name']])
                    self.assertTrue(all(isinstance(reason, str) and reason for _tool, reason in waits))

    def test_a_refused_write_still_fails(self):
        # Not an owner-resolvable wait: a value the owner never asked to keep advanced nothing (as #679 pinned).
        refused = {'saved': False, 'state': 'refused', 'refused_because': 'value-not-in-owner-request'}
        self.assertEqual(outcome_from_events(bridge_rows([('save_memory', refused)])), ('failed', []))
        self.assertEqual(runtime.owner_wait_steps(bridge_rows([('save_memory', refused)])), [])

    def test_a_wait_without_the_typed_shape_is_not_an_owner_wait(self):
        # A read that found nothing waits for nothing: it is a finished step on both routes.
        self.assertEqual(outcome_from_events(bridge_rows([('drive_search', {'files': []})])), ('succeeded', []))


class SignInRoutes(LoginHarness):
    """One Work that meets a sign-in page, on the direct route and on the CLI route, and its resume."""

    def cli_service(self):
        """The same store, browser profile and Telegram owner, with a subscription CLI whose bridge opens the page."""
        test = self

        class Cli:
            def execute(self, engine_id, prompt, tools, **kwargs):
                class Relay:
                    def call(self, name, args):
                        return tools.call(name, args)

                def protocol(handle, concurrent=None):
                    handle('tools/call', {'name': 'browser_open', 'arguments': {'url': ORIGIN + '/login', 'effect': 'navigate'}})
                with mock.patch.object(mcp_bridge, 'RelayClient', return_value=Relay()), \
                        mock.patch.object(mcp_bridge, '_serve_stdio', side_effect=protocol):
                    mcp_bridge.serve(str(test.store.root), tools.capabilities.job_id, browser_relay='/fixture-relay')
                # The CLI's own stream: its one call was the bridge's, and the turn ended.
                return ExecutionResult(REPLY, engine_id, 0, {
                    'tool_calls': [{'type': 'mcp_tool_call', 'server': 'agentos', 'name': 'browser_open'}],
                    'stream_tail': ['turn.completed']})

            def login_status(self, engine_id, binary=None):
                return {'state': 'signed-in'}

        self.service = AgentService(self.store, ModelAdapter(self.model), self.transport, browser_profile=self.profile,
                                    execution_adapter=Cli(),
                                    subscription_engines=SubscriptionEngines(finder=lambda _: '/runtime/cli', clock=lambda: 1))
        self.service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})

    def run_route(self, route):
        if route == 'cli':
            self.cli_service()
            # The sandbox forbids Unix socket listeners; only the relay's construction is replaced.
            with mock.patch('personal_agent.quickstart_service.BrowserRelay') as relay:
                relay.return_value.address = '/fixture-relay'
                job_id = self.receive(REQUEST)
                self.assertTrue(self.service.run_one())
        else:
            self.scripts = [Script({'tool_calls': [call('1', 'browser_open', url=ORIGIN + '/login', effect='navigate')]},
                                   {'content': REPLY})]
            job_id = self.receive(REQUEST)
            self.assertTrue(self.service.run_one())
        return job_id

    def assert_waits_and_resumes(self, route):
        job_id = self.run_route(route)
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'partial', 'waiting for the owner, never failed')
        # The closing note names the owner's next step, the sign-in.
        self.assertIn(SIGN_IN, job['owner_note'] or '')
        self.shown(job_id)
        self.service.deliver_one()
        [reply] = [body['text'] for method, body in self.calls if method == 'sendMessage' and REPLY in (body.get('text') or '')]
        for failed in (TERMINAL_FAILED_HEADER, TERMINAL_FAILED_NOTE):
            self.assertNotIn(failed, reply)
        # The sign-in resumes the Work exactly as before: partial is a resumable continuation (#709).
        self.assertTrue(ws.is_resumable_continuation(job['status']))
        self.owner_closes(job_id)
        self.assertEqual(self.state(job_id), 'resumed')
        self.assertEqual(self.store.job(job_id)['status'], 'queued')
        return reply

    def test_the_direct_route_stores_partial_and_resumes_after_the_sign_in(self):
        self.assert_waits_and_resumes('direct')

    def test_the_cli_route_stores_partial_and_resumes_after_the_sign_in(self):
        self.assert_waits_and_resumes('cli')


if __name__ == '__main__':
    unittest.main()
