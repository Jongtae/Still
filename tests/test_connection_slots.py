"""CONN-API: Still-held connections as read-only ``api_request`` slots."""
import json
import tempfile
import unittest
from pathlib import Path

from personal_agent import connection_slots
from personal_agent.agent_runtime import Capabilities, evidence_summary
from personal_agent.api_requests import ApiError, ApiRequests, context_line, save_slot
from personal_agent.connector_contract import ConnectorState
from personal_agent.quickstart_store import QuickStore

TOKEN = 'ya29.connection-access-token-value'
EVENT_URL = 'https://www.googleapis.com/calendar/v3/calendars/primary/events/lunch'


def calendar_slot(token=lambda: TOKEN):
    return connection_slots._slot('google-calendar', token)


class Transport:
    def __init__(self, body=None, status=200):
        self.calls, self.body, self.status = [], body, status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers)))
        payload = self.body if self.body is not None else {'id': 'lunch', 'attendees': [{'email': 'kim@example.com'}]}
        return self.status, {}, json.dumps(payload).encode()


class ConnectionSlots(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')

    def api(self, transport, slots=None, **kwargs):
        held = slots if slots is not None else {'google-calendar': calendar_slot()}
        return ApiRequests(self.store, 'w', transport=transport, connections=lambda: held, **kwargs)

    def test_the_ai_reads_whatever_the_provider_api_returns_with_a_token_it_never_sees(self):
        transport = Transport()
        result = self.api(transport).call({'slot': 'google-calendar', 'url': EVENT_URL, 'effect': 'read'})
        self.assertEqual(result['data']['attendees'], [{'email': 'kim@example.com'}])
        method, url, headers = transport.calls[0]
        self.assertEqual((method, url, headers['Authorization']), ('GET', EVENT_URL, 'Bearer ' + TOKEN))
        self.assertEqual(result['provenance']['source']['connection'], 'google-calendar')
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_a_connection_read_is_as_of_retrieval_unless_a_reference_time_is_asked_for(self):
        # #1329: the provider's live API returns the owner's records as they are now; no as-of-unknown flag.
        result = self.api(Transport()).call({'slot': 'google-calendar', 'url': EVENT_URL, 'effect': 'read'})
        record = result['provenance']
        self.assertEqual((record['as_of_source'], record['freshness']), ('live-record', 'fresh'))
        self.assertEqual(record['as_of'], record['retrieved_at'])
        self.assertFalse(result['as_of_unknown'])
        asked = self.api(Transport()).call({'slot': 'google-calendar', 'url': EVENT_URL, 'effect': 'read',
                                            'as_of_field': '$.updated'})
        self.assertEqual(asked['provenance']['freshness'], 'unknown')
        self.assertTrue(asked['as_of_unknown'])

    def test_an_owner_registered_slot_is_never_live(self):
        save_slot(self.store, 'google-calendar', ['www.googleapis.com'], 'owner-registered-secret-1')
        transport = Transport()
        result = ApiRequests(self.store, 'w', transport=transport).call(
            {'slot': 'google-calendar', 'url': EVENT_URL, 'effect': 'read'})
        self.assertEqual(result['provenance']['freshness'], 'unknown')
        self.assertTrue(result['as_of_unknown'])

    def test_a_token_echoed_by_the_provider_is_scrubbed(self):
        result = self.api(Transport({'id': 'x', 'echo': TOKEN})).call({'slot': 'google-calendar', 'url': EVENT_URL})
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_the_token_is_resolved_for_every_call(self):
        issued = []

        def token():
            issued.append(len(issued))
            return f'token-{len(issued)}-value-long-enough'
        transport = Transport()
        api = self.api(transport, {'google-calendar': calendar_slot(token)})
        api.call({'slot': 'google-calendar', 'url': EVENT_URL})
        api.call({'slot': 'google-calendar', 'url': EVENT_URL})
        self.assertEqual([c[2]['Authorization'] for c in transport.calls],
                         ['Bearer token-1-value-long-enough', 'Bearer token-2-value-long-enough'])

    def test_an_undeclared_effect_on_a_read_grant_is_a_read(self):
        transport = Transport()
        self.api(transport).call({'slot': 'google-calendar', 'url': EVENT_URL})
        self.assertEqual(len(transport.calls), 1)

    def test_a_read_grant_never_sends_a_change(self):
        transport = Transport()
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            with self.subTest(method=method), self.assertRaises(ApiError) as caught:
                self.api(transport).call({'slot': 'google-calendar', 'url': EVENT_URL, 'method': method,
                                          'effect': 'mutate', 'body': '{}' if method != 'DELETE' else None})
            self.assertEqual(caught.exception.code, 'api_read_only')
        self.assertEqual(transport.calls, [])

    def test_the_token_reaches_only_its_own_api_path_and_host(self):
        transport = Transport()
        for url in ('https://www.googleapis.com/drive/v3/files', 'https://www.googleapis.com/oauth2/v3/userinfo',
                    'https://evil.example/calendar/v3/x', 'http://www.googleapis.com/calendar/v3/x'):
            with self.subTest(url=url), self.assertRaises(ApiError) as caught:
                self.api(transport).call({'slot': 'google-calendar', 'url': url})
            self.assertEqual(caught.exception.code, 'api_host_not_allowed')
        self.assertEqual(transport.calls, [])

    def test_dot_segments_and_encoded_separators_cannot_walk_out_of_the_prefix(self):
        """Review P3."""
        transport = Transport()
        for path in ('/calendar/v3/../drive/v3/files', '/calendar/v3/%2e%2e/drive/v3/files', '/calendar/v3/x%2fy',
                     '/calendar/v3/./events', '/calendar/v3/%252e%252e/x'):
            with self.subTest(path=path), self.assertRaises(ApiError) as caught:
                self.api(transport).call({'slot': 'google-calendar', 'url': 'https://www.googleapis.com' + path})
            self.assertEqual(caught.exception.code, 'api_host_not_allowed')
        self.assertEqual(transport.calls, [])

    def test_after_the_call_a_401_marks_reauth_and_a_changed_connection_withholds_the_response(self):
        """Review P2: the connector's post-call checks run for every connection call."""
        seen = []

        def token_with(check):
            return lambda: (TOKEN, check)
        ok = self.api(Transport(), {'google-calendar': calendar_slot(token_with(seen.append))}).call(
            {'slot': 'google-calendar', 'url': EVENT_URL})
        self.assertEqual(seen, [200])
        self.assertIn('data', ok)
        with self.assertRaises(ApiError) as caught:
            self.api(Transport(status=401), {'google-calendar': calendar_slot(token_with(seen.append))}).call(
                {'slot': 'google-calendar', 'url': EVENT_URL})
        self.assertEqual((seen[-1], caught.exception.code), (401, 'api_unauthorized'))

        def revoked(status):
            raise ValueError('connection revision changed')
        with self.assertRaises(ApiError) as caught:
            self.api(Transport({'id': 'lunch', 'secret_data': 'x'}), {'google-calendar': calendar_slot(token_with(revoked))}).call(
                {'slot': 'google-calendar', 'url': EVENT_URL})
        self.assertEqual(caught.exception.code, 'needs_setup')

    def test_an_unusable_connection_is_setup_required_and_nothing_is_sent(self):
        def broken():
            raise ValueError('reauthentication required')
        transport = Transport()
        with self.assertRaises(ApiError) as caught:
            self.api(transport, {'google-calendar': calendar_slot(broken)}).call({'slot': 'google-calendar', 'url': EVENT_URL})
        self.assertEqual((caught.exception.code, caught.exception.requires), ('needs_setup', 'connection:google-calendar'))
        self.assertEqual(transport.calls, [])

    def test_an_owner_slot_of_the_same_name_wins_and_slots_are_listed_for_the_ai(self):
        save_slot(self.store, 'google-calendar', ['example.com'], 'owner-registered-secret-1')
        api = self.api(Transport())
        self.assertEqual(list(api.slots()['google-calendar']['hosts']), ['example.com'])
        line = context_line(self.store, {'google-drive': connection_slots._slot('google-drive', lambda: TOKEN)})
        self.assertIn('google-drive (www.googleapis.com', line)
        self.assertNotIn(TOKEN, line)

    def test_capabilities_offer_api_request_when_only_a_connection_exists(self):
        job = self.store.enqueue('점심은 누구랑?', 'k')
        bare = Capabilities(self.store, None, {}, '', job, lambda *a: None)
        self.assertNotIn('api_request', bare.offered_tools())
        held = Capabilities(self.store, None, {}, '', job, lambda *a: None, connections=lambda: {'google-calendar': calendar_slot()})
        self.assertIn('api_request', held.offered_tools())


class Calendar:
    """A calendar whose fixed-shape read returns title and time only, as the real list read does."""

    def query(self, owner, start, end, timezone):
        return {'events': [{'id': 'lunch', 'summary': '점심', 'start': '2026-10-12T12:30:00+09:00',
                            'end': '2026-10-12T13:30:00+09:00'}], 'window': {}, 'evidence': {}}


class ShortcutReads(unittest.TestCase):
    """#1325: a fixed-shape read says what it did not read and where the rest is."""

    WINDOW = {'start': '2026-10-12T00:00:00+09:00', 'end': '2026-10-13T00:00:00+09:00', 'timezone': 'Asia/Seoul'}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')
        self.job = self.store.enqueue('내일 점심 누구랑?', 'k')
        self.calendar = Calendar()

    def query(self, connections=None):
        capabilities = Capabilities(self.store, None, {}, '', self.job, lambda *a: None, calendar=self.calendar,
                                    calendar_owner='owner', connections=connections)
        return capabilities.execute('calendar_query', dict(self.WINDOW))

    def test_the_result_names_the_fields_it_read_and_the_slot_that_reads_an_item_in_full(self):
        result = self.query(lambda: {'google-calendar': calendar_slot()})
        self.assertEqual(result['read_fields'], ['end', 'id', 'start', 'summary'])
        self.assertIn('api_request slot "google-calendar"', result['unread'])
        self.assertEqual(result['events'][0]['summary'], '점심')

    def test_the_judgment_sees_which_fields_were_read(self):
        summary = evidence_summary('calendar_query', self.query(lambda: {'google-calendar': calendar_slot()}))
        self.assertEqual(summary['read_fields'], ['end', 'id', 'start', 'summary'])
        self.assertEqual(summary['unread_via'], 'api_request')

    def test_what_was_read_survives_a_bounded_observation_line_and_leads_the_result(self):
        long_events = {'events': [{'id': f'event-{n}' * 6, 'summary': '긴 일정 제목 ' * 8, 'start': '2026-10-12T12:30:00+09:00',
                                   'end': '2026-10-12T13:30:00+09:00'} for n in range(12)], 'window': {}, 'evidence': {}}
        self.calendar.query = lambda *args: long_events
        result = self.query(lambda: {'google-calendar': calendar_slot()})
        self.assertEqual(list(result)[:2], ['read_fields', 'unread'])
        # The goal judgment reads each observation cut to 600 characters (quickstart_service.orchestration_step).
        line = json.dumps(evidence_summary('calendar_query', result), ensure_ascii=False)[:600]
        self.assertIn('"read_fields": ["end", "id", "start", "summary"]', line)
        self.assertIn('"unread_via": "api_request"', line)

    def test_without_the_connection_slot_no_slot_is_named(self):
        for connections in (None, lambda: {'google-drive': connection_slots._slot('google-drive', lambda: TOKEN)}):
            result = self.query(connections)
            self.assertEqual(result['read_fields'], ['end', 'id', 'start', 'summary'])
            self.assertNotIn('unread', result)
            self.assertNotIn('unread_via', evidence_summary('calendar_query', result))

    def test_every_shortcut_is_an_offered_tool_and_belongs_to_one_slot(self):
        from personal_agent.agent_runtime import DEFINITIONS
        names = {definition['function']['name'] for definition in DEFINITIONS}
        shortcuts = [tool for spec in connection_slots.SLOT_SPECS.values() for tool in spec['shortcuts']]
        self.assertEqual(len(shortcuts), len(set(shortcuts)))
        self.assertTrue(set(shortcuts) <= names)
        self.assertEqual(connection_slots.shortcut_slot('calendar_query'), 'google-calendar')
        self.assertIsNone(connection_slots.shortcut_slot('read_file'))


class CliBridge(unittest.TestCase):
    """#1325: the Codex/Claude Code bridge offers api_request for the Work's connections, holding no token."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = QuickStore(self.root / 'data')
        self.job = self.store.enqueue('내일 점심 누구랑?', 'k')

    def listed(self, connection_slots, relay=True):
        import contextlib
        import io
        import sys
        from unittest import mock
        from personal_agent import mcp_bridge
        requests = '\n'.join(json.dumps(r) for r in [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]) + '\n'
        out = io.StringIO()
        with mock.patch.object(sys, 'stdin', io.StringIO(requests)), contextlib.redirect_stdout(out):
            mcp_bridge.serve(str(self.store.root), self.job, browser_relay=str(self.root / 'relay') if relay else None,
                             relay_browser=False, connection_slots=connection_slots)
        replies = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        return {tool['name'] for tool in replies[1]['result']['tools']}

    def test_a_connection_lists_api_request_on_the_bridge(self):
        self.assertIn('api_request', self.listed(['google-calendar']))

    def test_no_connection_no_relay_or_an_unknown_name_lists_no_api_request(self):
        self.assertNotIn('api_request', self.listed([]))
        self.assertNotIn('api_request', self.listed(['google-calendar'], relay=False))
        self.assertNotIn('api_request', self.listed(['not-a-connection']))

    def test_the_bridge_placeholder_never_holds_a_token(self):
        slot = connection_slots.relayed(['google-calendar'])['google-calendar']
        with self.assertRaises(RuntimeError):
            slot['token']()
        self.assertEqual(connection_slots.relayed(['nope']), {})

    def test_the_host_hands_the_bridge_connection_names_never_tokens(self):
        from unittest import mock
        from personal_agent.bounded_execution import AgentOSMcpTools, BoundedExecutionAdapter
        seen = {}
        original = Path.write_text

        def capture(path, text, *args, **kwargs):
            if path.name == 'agentos-mcp.json':
                seen['config'] = json.loads(text)
            return original(path, text, *args, **kwargs)
        caps = Capabilities(self.store, None, {}, '', self.job, lambda *a: None,
                            connections=lambda: {'google-calendar': calendar_slot()})
        save_slot(self.store, 'broker', ['api.example.com'], 'owner-registered-secret-1')
        tools = AgentOSMcpTools(caps)
        tools.browser_relay = str(self.root / 'relay')
        adapter = BoundedExecutionAdapter(finder=lambda name: '/runtime/' + name, runner=lambda *a, **k: None,
                                          runtime_root=self.root / 'turns', codex_home=self.root)
        with mock.patch.object(Path, 'write_text', capture):
            try:
                adapter.execute('codex', 'prompt', tools)
            except Exception:
                pass   # the fake process returns nothing; the config was written first
        args = seen['config']['mcpServers']['agentos']['args']
        self.assertEqual([arg for arg in args if arg.startswith('--connection-slot')], ['--connection-slot=google-calendar'])
        self.assertNotIn(TOKEN, json.dumps(seen['config']))


class Build(unittest.TestCase):
    """Only a connected read connector becomes a slot."""

    class Registry:
        def __init__(self, connected):
            self.connected = connected

        def status(self, owner, connector_id):
            state = ConnectorState.CONNECTED if connector_id in self.connected else ConnectorState.DISCONNECTED
            return type('Status', (), {'state': state})()

    def service(self, connected, **attrs):
        return type('Service', (), {'connector_registry': self.Registry(connected), 'calendar_oauth': object(),
                                    'drive_oauth': object(), 'gmail': None, **attrs})()

    def test_connected_connectors_become_slots(self):
        built = connection_slots.build(self.service({'google-calendar'}), 'owner')
        self.assertEqual(sorted(built), ['google-calendar'])
        self.assertTrue(built['google-calendar']['read_only'])
        self.assertEqual(connection_slots.build(self.service(set()), 'owner'), {})
        self.assertEqual(connection_slots.build(type('S', (), {})(), 'owner'), {})
        both = connection_slots.build(self.service({'google-calendar', 'google-drive'}), 'owner')
        self.assertEqual(sorted(both), ['google-calendar', 'google-drive'])


if __name__ == '__main__':
    unittest.main()
