"""J4: the model reads the calendar through ``calendar_query``; changes go through the API slot.

EPIC-PA1 J4 asks to "ask what is scheduled, create a new event ... and support
bounded update/cancel behavior with truthful result/recovery state".  #432
(owner decision): the owner's AI creates, changes and cancels events itself
through ``api_request``'s google-calendar slot, which spends the
``google-calendar-write`` connection.  What this file pins is the read half:
the one calendar tool reaches the provider, returns the id and version a
change needs, labels the Work owner-private, and the read transport never
sends a mutation.
"""
import tempfile
import unittest
from pathlib import Path

from personal_agent.agent_runtime import Capabilities, MEMORY_OWNER
from personal_agent.calendar import (CALENDAR_SPEC, CALENDAR_WRITE_SPEC,
                                     CalendarConnector)
from personal_agent.connector_contract import ConnectorRegistry, ConnectorState
from personal_agent.google_calendar import CALENDAR_WRITE_SCOPE
from personal_agent.quickstart_store import QuickStore

CFG = {'provider': 'compatible', 'endpoint': 'https://openrouter.ai/api/v1', 'model': 'm'}
EVENT = {'id': 'ev1', 'summary': '팀 회의', 'version': '"etag1"'}


class Provider:
    """Stands in for GoogleCalendar and records what the policy layer asked."""

    def __init__(self):
        self.calls = []

    def query(self, start, end, timezone, max_results):
        self.calls.append(('query', start, end, timezone))
        return [{**EVENT, 'start': start, 'end': end}]


class Egress:
    def __init__(self):
        self.plans = []

    def execute(self, plan):
        self.plans.append(plan)
        return {'tool': plan['tool'], 'results': [], 'sources': [], 'retrieved_at': 1}


class CalendarToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(Path(self.temp.name) / 'data')
        self.registry = ConnectorRegistry(self.store, (CALENDAR_SPEC, CALENDAR_WRITE_SPEC))
        self.provider = Provider()
        self.calendar = CalendarConnector(self.store, self.provider, registry=self.registry)
        self.egress = Egress()

    def caps(self, calendar=True, **kwargs):
        return Capabilities(self.store, None, CFG, '', 'job', lambda *a: None,
                            network=self.egress,
                            calendar=self.calendar if calendar else None, **kwargs)

    def connect(self):
        self.registry.transition(MEMORY_OWNER, CALENDAR_SPEC.connector_id,
                                 ConnectorState.CONNECTED,
                                 granted_scopes=CALENDAR_SPEC.required_scopes)

    def query(self, caps):
        return caps.execute('calendar_query', {'start': '2026-09-23T00:00:00+09:00',
                                               'end': '2026-09-24T00:00:00+09:00',
                                               'timezone': 'Asia/Seoul'})

    # -- reachability, which is the whole defect --------------------------

    def test_the_one_calendar_tool_is_the_read(self):
        from personal_agent.agent_runtime import DEFINITIONS
        declared = {tool['function']['name'] for tool in DEFINITIONS}
        self.assertEqual(sorted(name for name in declared if name.startswith('calendar_')), ['calendar_query'])

    def test_asking_what_is_scheduled_reaches_the_provider(self):
        self.connect()
        result = self.query(self.caps())
        self.assertEqual(result['events'][0]['summary'], '팀 회의')
        self.assertEqual(self.provider.calls[0][0], 'query')

    def test_the_read_returns_what_a_change_needs(self):
        """A change through the API slot names the event and, with If-Match, its version."""
        self.connect()
        event = self.query(self.caps())['events'][0]
        self.assertTrue(event['id'])
        self.assertTrue(event['version'])

    def test_the_model_has_no_calendar_tool_that_changes_an_event(self):
        """#432: a change is an ``api_request`` through the google-calendar slot, never a calendar tool."""
        caps = self.caps()
        for forbidden in ('approve', 'execute', 'create_calendar', 'calendar_apply'):
            with self.subTest(tool=forbidden):
                self.assertNotIn(forbidden, caps.tools)
        self.assertEqual(sorted(name for name in caps.tools if name.startswith('calendar_')), ['calendar_query'])

    # -- boundaries --------------------------------------------------------

    def test_an_unconfigured_calendar_refuses_cleanly(self):
        """Nothing is read; #606 T5 types it as setup-required for one handoff."""
        caps = self.caps(calendar=False)
        result = self.query(caps)
        self.assertEqual((result['needs_setup'], result['requires']), (True, 'google-calendar'))
        self.assertEqual(result['events'], [])
        self.assertIn('구성되어 있지 않아', result['next_step'])

    def test_reading_the_calendar_is_recorded_and_no_longer_closes_public_destinations(self):
        """Calendar contents are owner-private: the read labels the Work (its information-use audit
        lists the entries).  #826 (owner decision): a web search may follow in the same turn."""
        self.connect()
        caps = self.caps()
        self.query(caps)
        self.assertEqual(caps.private_provenance, {'owner-calendar'})
        caps.execute('web_search', {'query': 'unrelated public question'})
        self.assertEqual([plan['tool'] for plan in self.egress.plans], ['web_search'])

    def test_the_specialist_roles_do_not_get_calendar(self):
        from personal_agent.manifests import BUILTIN_MANIFEST
        for role in BUILTIN_MANIFEST['roles']:
            with self.subTest(role=role['id']):
                self.assertEqual([t for t in role['tools'] if t.startswith('calendar_')], [])




class CalendarTransportGrantTests(unittest.TestCase):
    """The read transport sends only GET with the read grant (#432: changes go through the API slot)."""

    def setUp(self):
        from personal_agent.calendar_oauth import (EncryptedCalendarSecretStore,
                                                   calendar_transport)
        from cryptography.fernet import Fernet
        self.calendar_transport = calendar_transport
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(Path(self.temp.name) / 'data')
        self.secrets = EncryptedCalendarSecretStore(self.store, Fernet.generate_key().decode())
        self.registry = ConnectorRegistry(self.store, (CALENDAR_SPEC, CALENDAR_WRITE_SPEC))
        self.seen = []

    def opener(self, method, url, body, headers):
        self.seen.append((method, headers.get('Authorization')))
        return {'items': []}

    def transport(self, **kwargs):
        return self.calendar_transport(self.secrets, self.registry, MEMORY_OWNER,
                                       opener=self.opener, **kwargs)

    def connect(self, grant, spec, scope):
        """Connect a grant AND store a usable token.

        Both matter. A first version of these tests transitioned the registry
        and stored nothing, so every call raised for want of a token and two
        mutations survived: a write spending the read grant, and a read
        carrying a body. The tests passed for the wrong reason.
        """
        from personal_agent.calendar_oauth import (TOKEN_SECRET_KEY, _owner_key,
                                                   _secret_slot)
        status = self.registry.transition(MEMORY_OWNER, spec.connector_id,
                                          ConnectorState.CONNECTED,
                                          granted_scopes=tuple(scope.split()))
        self.secrets.secret(_secret_slot(TOKEN_SECRET_KEY, grant, MEMORY_OWNER), {
            'access_token': f'{grant}-token',
            'expires_at': 4_102_444_800.0,
            'owner': _owner_key(MEMORY_OWNER),
            'grant': grant,
            'scope': list(spec.required_scopes),
            'connection_revision': status.connection_revision,
        })
        return status

    def test_a_read_only_transport_refuses_a_mutation_before_loading_a_token(self):
        send = self.transport()
        from personal_agent.calendar_oauth import CalendarOAuthError
        for method in ('POST', 'PATCH', 'DELETE', 'PUT'):
            with self.subTest(method=method):
                with self.assertRaises(CalendarOAuthError):
                    send(method, 'https://www.googleapis.com/calendar/v3/calendars/primary/events',
                         {'summary': 'x'}, {})
        self.assertEqual(self.seen, [], 'nothing may reach the opener')

    def test_a_connected_write_grant_does_not_make_the_transport_send_a_mutation(self):
        """The write grant is spent by the API slot, never by this transport."""
        from personal_agent.calendar_oauth import CalendarOAuthError, READ_GRANT, WRITE_GRANT
        self.connect(READ_GRANT, CALENDAR_SPEC, " ".join(CALENDAR_SPEC.required_scopes))
        self.connect(WRITE_GRANT, CALENDAR_WRITE_SPEC, CALENDAR_WRITE_SCOPE)
        send = self.transport()
        send('GET', 'https://www.googleapis.com/calendar/v3/calendars/primary/events', None, {})
        with self.assertRaises(CalendarOAuthError):
            send('POST', 'https://www.googleapis.com/calendar/v3/calendars/primary/events', {'summary': 'x'}, {})
        self.assertEqual(self.seen, [('GET', 'Bearer read-token')])

    def test_a_read_carrying_a_body_is_refused_even_with_a_valid_token(self):
        from personal_agent.calendar_oauth import CalendarOAuthError, READ_GRANT
        self.connect(READ_GRANT, CALENDAR_SPEC, " ".join(CALENDAR_SPEC.required_scopes))
        with self.assertRaises(CalendarOAuthError):
            self.transport()('GET', 'https://www.googleapis.com/calendar/v3/calendars/primary/events',
                             {'summary': 'x'}, {})
        self.assertEqual(self.seen, [])


if __name__ == '__main__':
    unittest.main()
