"""#825: each worker attempt keeps the instructions and tool contract it was actually given.

Per-attempt ``attempt_inputs`` entries on the turn record: instructions and
offered-tool digests at the dispatch boundary, whether the input was sent, and
``unavailable`` where Still cannot observe a definition.
"""
import json
import secrets
import tempfile
import unittest
from pathlib import Path

from personal_agent import attempt_identity
from personal_agent.bounded_execution import NOT_LAUNCHED, ExecutionError, ExecutionResult
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.subscription_engines import SubscriptionEngines


class _Engine:
    def __init__(self, fail=None):
        self.fail = fail

    def login_status(self, engine_id, binary=None):
        return {'state': 'signed-in'}

    def execute(self, engine, prompt, tools, **kwargs):
        if self.fail:
            raise self.fail
        return ExecutionResult('answer', engine, 0, {})


def _tool(name, description='Reads one thing.', **properties):
    return {'type': 'function', 'function': {'name': name, 'description': description,
                                             'parameters': {'type': 'object', 'properties': properties}}}


class RecordedAttemptInputs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        self.service = AgentService(self.store, subscription_engines=SubscriptionEngines(finder=lambda _: None, clock=lambda: 1))
        self.job = self.store.enqueue('hello', 'k1')

    def _send(self, attempt, instructions, tools, sent='the request'):
        entry = {'attempt': attempt, 'route': 'direct-api',
                 'offered': attempt_identity.offered_tools_identity(tools, representation='still-host-api')}
        return self.service.record_turn_sent(self.job, sent=sent, instructions=instructions,
                                             instructions_channel='system-message', attempt_input=entry, status='sent')

    def test_two_attempts_with_changed_instructions_keep_separate_identities(self):
        first = self._send(1, 'Instructions A', [_tool('weather', city={'type': 'string'})])
        second = self._send(2, 'Instructions B', [_tool('weather', city={'type': 'string'})])
        rows = self.store.turn_provenance(self.job)['attempt_inputs']
        self.assertEqual([row['ref'] for row in rows], [first, second])
        self.assertEqual([row['attempt'] for row in rows], [1, 2])
        self.assertEqual(rows[0]['instructions_digest'], attempt_identity.instructions_digest('Instructions A'))
        self.assertNotEqual(rows[0]['instructions_digest'], rows[1]['instructions_digest'])
        self.assertEqual(rows[0]['offered']['digest'], rows[1]['offered']['digest'], 'unchanged tools keep their identity')
        # The historical top-level fields stay last-writer, as their readers expect.
        self.assertEqual(self.store.turn_provenance(self.job)['instructions_digest'], rows[1]['instructions_digest'])

    def test_same_tool_name_with_changed_schema_or_description_has_a_new_digest(self):
        self._send(1, 'I', [_tool('weather', city={'type': 'string'})])
        self._send(2, 'I', [_tool('weather', city={'type': 'string'}, days={'type': 'integer'})])
        self._send(3, 'I', [_tool('weather', 'Reads the forecast.', city={'type': 'string'})])
        rows = self.store.turn_provenance(self.job)['attempt_inputs']
        self.assertEqual(len({row['offered']['digest'] for row in rows}), 3)
        self.assertEqual(len({row['offered']['tools'][0]['digest'] for row in rows}), 3)
        self.assertEqual({row['offered']['tools'][0]['name'] for row in rows}, {'weather'})

    def test_input_is_prepared_until_its_dispatch_is_known(self):
        ref = self._send(1, 'I', [])
        self.assertEqual(self.store.turn_provenance(self.job)['attempt_inputs'][-1]['input'], 'prepared')
        self.service.record_attempt_input(self.job, ref, 'not-sent')
        self.assertEqual(self.store.turn_provenance(self.job)['attempt_inputs'][-1]['input'], 'not-sent')
        self.service.record_attempt_input(self.job, ref, 'invented-state')
        self.assertEqual(self.store.turn_provenance(self.job)['attempt_inputs'][-1]['input'], 'not-sent')

    def test_bounded_entries_count_what_was_dropped_and_a_dropped_ref_changes_nothing(self):
        refs = [self._send(number, f'I{number}', []) for number in range(1, 9)]
        record = self.store.turn_provenance(self.job)
        self.assertEqual([row['attempt'] for row in record['attempt_inputs']], [3, 4, 5, 6, 7, 8])
        self.assertEqual(record['attempt_inputs_dropped'], 2)
        self.service.record_attempt_input(self.job, refs[0], 'sent')
        self.assertEqual(self.store.turn_provenance(self.job)['attempt_inputs'], record['attempt_inputs'])

    def test_historical_worker_lists_stay_readable_and_say_what_the_bound_removed(self):
        legacy = {'route': 'subscription', 'workers': [{'attempt': n, 'engine': 'codex'} for n in range(1, 7)],
                  'instructions_digest': 'abcdefabcdefabcd'}
        self.store.put_turn_provenance(self.job, legacy)
        self.service.record_turn_worker(self.job, {'attempt': 7, 'route': 'subscription', 'engine': 'codex', 'model': None})
        record = self.store.turn_provenance(self.job)
        self.assertEqual([row['attempt'] for row in record['workers']], [2, 3, 4, 5, 6, 7])
        self.assertEqual(record['workers'][-1], {'attempt': 7, 'route': 'subscription', 'engine': 'codex'})
        self.assertEqual(record['workers_dropped'], 1)
        self.assertNotIn('attempt_inputs', record, 'a historical record gains no invented attempt identities')
        self.assertEqual(record['instructions_digest'], 'abcdefabcdefabcd')

    def test_no_instruction_text_schema_or_case_payload_is_kept_in_the_entries(self):
        tool = _tool('weather', 'DESCRIPTION-MARKER-825', PROPERTY_MARKER_825={'type': 'string'})
        self._send(1, 'INSTRUCTIONS-MARKER-825', [tool], sent='CASE-PAYLOAD-825')
        rows = json.dumps(self.store.turn_provenance(self.job)['attempt_inputs'])
        for marker in ('DESCRIPTION-MARKER-825', 'PROPERTY_MARKER_825', 'INSTRUCTIONS-MARKER-825', 'CASE-PAYLOAD-825'):
            self.assertNotIn(marker, rows)

    def test_a_bridge_listing_lands_only_on_the_running_cli_attempt(self):
        served = [{'name': 'list_notes', 'description': 'd', 'inputSchema': {'type': 'object'}}]
        entry = {'attempt': 1, 'route': 'subscription'}
        ref = self.service.record_turn_sent(self.job, sent='x', instructions='I', instructions_channel='prompt', attempt_input=entry)
        attempt_identity.record_served(self.store, self.job, served, 'still-host-mcp:bridge')
        row = self.store.turn_provenance(self.job)['attempt_inputs'][-1]
        self.assertEqual(row['served'], attempt_identity.offered_tools_identity(served, representation='still-host-mcp:bridge'))
        self.service.record_attempt_input(self.job, ref, 'sent')
        attempt_identity.record_served(self.store, self.job, [*served, {'name': 'late'}], 'still-host-mcp:bridge')
        self.assertEqual(self.store.turn_provenance(self.job)['attempt_inputs'][-1]['served']['count'], 1,
                         'a finished attempt is never rewritten by a later bridge')


class RouteAttemptInputs(unittest.TestCase):
    def _cli(self, engine=None, isolated=None):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        options = {}
        if isolated is not None:
            from personal_agent.isolated_mcp_proxy import TaskCapabilityRegistry
            options = {'isolated_engine_adapter': isolated, 'isolated_mcp_registry': TaskCapabilityRegistry()}
        service = AgentService(self.store, subscription_engines=SubscriptionEngines(finder=lambda c: '/runtime/' + c, clock=lambda: 1),
                               execution_adapter=engine or _Engine(), **options)
        service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        self.store.enqueue('hello', 'k1')
        service.run_one()
        job_id = self.store.jobs()[0]['id']
        return self.store.turn_provenance(job_id)

    def _api(self, transport):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        config = {'provider': 'compatible', 'endpoint': 'http://127.0.0.1:9999', 'model': 'fixture-model'}
        service = AgentService(self.store, adapter=ModelAdapter(transport),
                               subscription_engines=SubscriptionEngines(finder=lambda _: None, clock=lambda: 1))
        self.store.put('model', config)
        self.store.put('model_test', {'ok': True, 'tools_ok': True, 'time': 9999999999, 'fingerprint': service.model_fingerprint(config)})
        self.store.enqueue('what is on today', 'k1')
        service.run_one()
        return self.store.turn_provenance(self.store.jobs()[0]['id'])

    def test_direct_api_identity_is_the_tool_list_the_provider_received(self):
        bodies = []

        def transport(url, body, headers):
            bodies.append(body)
            return {'choices': [{'message': {'content': 'api answer'}}]}
        record = self._api(transport)
        entry = record['attempt_inputs'][-1]
        self.assertEqual((entry['route'], entry['input'], entry['instructions_channel']), ('direct-api', 'sent', 'system-message'))
        self.assertEqual(entry['offered'], attempt_identity.offered_tools_identity(bodies[0]['tools'], representation='still-host-api'))
        self.assertIn('finish', [tool['name'] for tool in entry['offered']['tools']])
        self.assertNotIn('native', entry, 'a direct call offers no provider-side tools')

    def test_direct_api_failure_without_a_response_is_unknown_not_sent(self):
        def transport(url, body, headers):
            raise OSError('connection refused')
        record = self._api(transport)
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['attempt_inputs'][-1]['input'], 'unknown')

    def test_cli_sent_input_carries_the_listing_and_marks_native_tools_unavailable(self):
        record = self._cli()
        entry = record['attempt_inputs'][-1]
        self.assertEqual((entry['route'], entry['engine'], entry['input']), ('subscription', 'codex', 'sent'))
        self.assertEqual(entry['offered']['representation'], 'still-host-mcp:parent-listing')
        self.assertEqual(sorted(tool['name'] for tool in entry['offered']['tools']), sorted(record['exposed_tools'])[:40])
        self.assertEqual(entry['offered']['count'], len(record['exposed_tools']))
        self.assertIsNone(entry['native']['digest'])
        self.assertEqual(entry['native']['known_enabled'], record['cli_native_tools'])
        self.assertEqual(entry['instructions_channel'], 'developer-instructions')
        self.assertEqual(entry['instructions_digest'], record['instructions_digest'])

    def test_cli_preflight_failure_is_distinguished_from_sent_input(self):
        failure = ExecutionError('binary missing', failure_class='not-installed')
        failure.meta = dict(NOT_LAUNCHED)
        record = self._cli(_Engine(fail=failure))
        self.assertEqual(record['attempt_inputs'][0]['input'], 'not-sent')
        launched = ExecutionError('cli exited', failure_class='engine-error')
        launched.meta = {'argv': ['codex', 'exec']}
        record = self._cli(_Engine(fail=launched))
        self.assertEqual(record['attempt_inputs'][0]['input'], 'unknown')

    def test_isolated_route_records_the_sidecar_listing_as_unavailable(self):
        token = secrets.token_urlsafe(32)

        class Isolated:
            def issue_task_token(self, **kwargs):
                return token

            def execute(self, **kwargs):
                return 'isolated answer'
        record = self._cli(isolated=Isolated())
        entry = record['attempt_inputs'][-1]
        self.assertEqual((entry['route'], entry['input'], entry['instructions_channel']), ('isolated', 'sent', 'prompt'))
        self.assertEqual([tool['name'] for tool in entry['offered']['tools']], ['list_notes'])
        self.assertEqual(entry['served'], attempt_identity.unavailable('isolated-mcp:sidecar',
                                                                       'served inside the isolated sidecar; not observed by Still'))
        self.assertNotIn(token, json.dumps(record))

    def test_isolated_failure_before_dispatch_is_not_sent(self):
        class Isolated:
            def issue_task_token(self, **kwargs):
                raise ValueError('prompt is empty')

            def execute(self, **kwargs):
                raise AssertionError('never dispatched')
        record = self._cli(isolated=Isolated())
        self.assertEqual(record['attempt_inputs'][0]['input'], 'not-sent')


if __name__ == '__main__':
    unittest.main()
