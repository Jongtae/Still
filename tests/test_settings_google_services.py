"""#535: Settings shows Google access per service, with the route in use.

The row model only regroups the existing per-grant rows: each grant stays a
level with its own state and its own disconnect, and an AI-side route is shown
only from what Still can observe (an AI CLI's configured server names, or a
connection a Work was seen calling).
"""
import unittest

from personal_agent import connection_slots, owner_mcp, trust_record
from personal_agent.connector_contract import ConnectorState

import test_connection_slots as slot_tests


class Fixture(unittest.TestCase):
    """A service with real Gmail read, modify and filters connectors (the #502 fixture), without its tests."""
    FILTER_TOKEN = slot_tests.GmailFilters.FILTER_TOKEN
    setUp = slot_tests.GmailFilters.setUp
    exchange = slot_tests.GmailFilters.exchange
    connect = slot_tests.GmailFilters.connect
    read_connect = slot_tests.GmailFilters.read_connect
    connect_filters = slot_tests.GmailFilters.connect_filters


def row(connector_id, state='disconnected', **extra):
    return {'connector_id': connector_id, 'label': connector_id, 'state': state, 'required_scopes': [],
            'connect_path': '/' + connector_id, **extra}


class GmailAsOneService(Fixture):
    """Real registry rows: Gmail read, modify and filters are one service with three levels."""

    def gmail(self):
        services = [service for service in self.service.google_service_rows(routes=[]) if service['service'] == 'gmail']
        self.assertEqual(len(services), 1, 'one Gmail row, not one per grant')
        return services[0]

    def test_read_modify_and_filters_are_three_levels_of_one_row(self):
        self.connect()
        self.connect_filters()
        gmail = self.gmail()
        self.assertEqual([(level['connector_id'], level['level'], level['state']) for level in gmail['levels']],
                         [('google-gmail-read', 'read', 'disconnected'), ('gmail-modify', 'modify', 'connected'),
                          ('gmail-filters', 'filters', 'connected')])
        # The levels are the existing rows unchanged (state, scopes, connect path), plus their level name.
        flat = {item['connector_id']: item for item in self.service.google_connection_rows()}
        for level in gmail['levels']:
            self.assertEqual({key: value for key, value in level.items() if key != 'level'}, flat[level['connector_id']])

    def test_disconnecting_one_level_disconnects_only_that_grant(self):
        self.connect()
        self.connect_filters()
        self.service.google_revoke_transport = lambda *args: (200, {}, b'')
        revoker = self.service.google_revocation()
        offer = revoker.preview('owner-session', 'gmail-filters')
        revoker.disconnect('owner-session', 'gmail-filters', offer['confirmation'])
        states = {level['connector_id']: level['state'] for level in self.gmail()['levels']}
        self.assertEqual(states, {'google-gmail-read': 'disconnected', 'gmail-modify': 'connected', 'gmail-filters': 'disconnected'})
        self.assertEqual(self.registry.status('local-owner', 'gmail-modify').state, ConnectorState.CONNECTED)
        self.assertEqual(connection_slots.names(connection_slots.build(self.service, 'local-owner')), ('gmail', 'gmail-modify'))

    def test_the_own_client_is_the_method_of_the_still_owned_levels(self):
        self.read_connect()
        self.connect()
        self.service.google_client_id = 'client'
        levels = {level['connector_id']: level for level in self.gmail()['levels']}
        self.assertEqual(levels['gmail-modify'].get('source'), 'own-client')
        self.assertFalse(any(service['service'] == 'google-own-client' for service in self.service.google_service_rows(routes=[])),
                         'the own client is never a peer service row')


class ServiceRows(Fixture):
    """The grouping itself, from declared data and fixture rows."""

    def test_every_write_grant_belongs_to_a_declared_service(self):
        declared = {connector_id for spec in self.service.GOOGLE_SERVICES for connector_id, _level in spec['levels']}
        self.assertLessEqual(connection_slots.write_grants(), declared)
        self.assertLessEqual({connector.connector_id for connector in self.registry.definitions()}, declared)

    def test_grouping_order_and_unknown_connectors(self):
        rows = [row('google-calendar-write', 'reauth_required'), row('gmail-filters'), row('google-gmail-read', 'connected'),
                row('google-calendar'), row('gmail-modify'), row('fixture-other', label='Other')]
        services = self.service.google_service_rows(rows, [])
        self.assertEqual([service['service'] for service in services], ['gmail', 'google-calendar', 'fixture-other'])
        self.assertEqual([level['level'] for level in services[0]['levels']], ['read', 'modify', 'filters'])
        self.assertEqual([level['connector_id'] for level in services[1]['levels']], ['google-calendar', 'google-calendar-write'])
        self.assertEqual(services[2]['name'], 'Other', 'a connector in no declared service keeps a row of its own')
        self.assertEqual(self.service.google_service_rows([], []), [], 'nothing to show is no rows')

    def test_ai_routes_attach_to_the_service_their_name_names(self):
        routes = [{'name': 'Google Drive', 'engine': 'claude-code'}, {'name': 'gmail-mcp', 'engine': 'codex'},
                  {'name': 'notes', 'engine': 'codex'}, {'name': 'gmailish', 'engine': 'codex'}]
        services = {service['service']: service for service in self.service.google_service_rows([row('google-gmail-read')], routes)}
        self.assertEqual([route['name'] for route in services['gmail']['ai_routes']], ['gmail-mcp'])
        self.assertEqual([route['name'] for route in services['google-drive']['ai_routes']], ['Google Drive'],
                         'an AI-side route alone gives the service a row')
        self.assertNotIn('google-calendar', services, 'no Still level and no AI route: no row')
        self.assertFalse(any(route['name'] in ('notes', 'gmailish') for service in services.values() for route in service['ai_routes']))
        self.assertEqual(self.service.google_service_rows([row('google-gmail-read')], [])[0]['ai_routes'], [], 'absent route, none shown')


class AiSideRoutes(Fixture):
    """What Still knows of the AI-side hand, and nothing it cannot observe."""

    def setUp(self):
        Fixture.setUp(self)
        self.configured = {'claude-code': [], 'codex': []}
        self.service.owner_mcp_available = lambda: self.configured
        self.service.ai_connections_status = lambda: {'enabled': self.enabled, 'available': True, 'note': ''}
        self.enabled = True

    def routes(self):
        self.service._ai_servers_cache = None
        return {(route['engine'], route['name']): route for route in self.service.ai_side_routes()}

    def test_configured_is_not_used_and_a_seen_call_is(self):
        self.configured['codex'] = ['gmail-mcp']
        route = self.routes()[('codex', 'gmail-mcp')]
        self.assertEqual((route['state'], route['configured'], route['last_used_at']), ('configured', True, None))
        self.assertEqual((route['usable'], route['unusable_reason']), (False, 'not_confirmed'),
                         'a server the owner has not confirmed for Works is not offered to them')
        owner_mcp.set_confirmed(self.store, 'codex', ['gmail-mcp'])
        self.assertTrue(self.routes()[('codex', 'gmail-mcp')]['usable'])
        target = trust_record.connection('codex', 'owner-mcp', 'gmail-mcp')
        trust_record.observe(self.store, target, 'search', 'succeeded', 'work-1')
        route = self.routes()[('codex', 'gmail-mcp')]
        self.assertEqual(route['state'], 'used')
        self.assertIsNotNone(route['last_used_at'])
        self.assertEqual(route['engine_name'], 'Codex')

    def test_a_claude_ai_connector_appears_only_once_seen(self):
        self.assertEqual(self.routes(), {}, 'no connector is guessed before a Work calls it')
        target = trust_record.connection('claude-code', 'ai-connection', 'Gmail', 'Gmail')
        trust_record.observe(self.store, target, 'search_threads', 'failed', 'work-1')
        route = self.routes()[('claude-code', 'Gmail')]
        self.assertEqual((route['state'], route['last_outcome'], route['kind']), ('used', 'failed', 'ai-connection'))
        services = {service['service']: service for service in self.service.google_service_rows([], self.service.ai_side_routes())}
        self.assertEqual([item['identity'] for item in services['gmail']['ai_routes']], ['Gmail'])

    def test_the_switch_and_a_family_instance(self):
        self.configured['claude-code'] = ['gmail']
        owner_mcp.set_confirmed(self.store, 'claude-code', ['gmail'])
        self.enabled = False
        route = self.routes()[('claude-code', 'gmail')]
        self.assertEqual((route['usable'], route['unusable_reason']), (False, 'off'))
        self.service.ai_connections_status = lambda: {'enabled': False, 'available': False, 'note': ''}
        self.assertEqual(self.routes(), {}, 'a family instance shows no AI-side route')

    def test_names_only_and_forgotten_records_are_not_shown(self):
        target = trust_record.connection('codex', 'owner-mcp', 'gmail-mcp')
        trust_record.observe(self.store, target, 'search', 'succeeded', 'work-1')
        route = self.routes()[('codex', 'gmail-mcp')]
        self.assertEqual(set(route), {'engine', 'engine_name', 'kind', 'identity', 'name', 'configured', 'last_used_at',
                                      'last_outcome', 'state', 'confirmed', 'usable', 'unusable_reason'})
        self.assertEqual(route['state'], 'used')
        trust_record._write(self.store, target, {'change': 'forget'}, lambda record: record.update(state='forgotten'))
        self.assertEqual(self.routes(), {})

    def test_settings_payload_carries_the_service_rows(self):
        self.configured['codex'] = ['gmail-mcp']
        self.read_connect()
        settings = self.service.settings()
        self.assertEqual([service['service'] for service in settings['google_services']][:1], ['gmail'])
        self.assertEqual(settings['google_services'][0]['ai_routes'][0]['name'], 'gmail-mcp')
        self.assertEqual(settings['ai_connections']['routes'][0]['name'], 'gmail-mcp')
        self.assertEqual({item['connector_id'] for item in settings['connectors']},
                         {level['connector_id'] for service in settings['google_services'] for level in service['levels']},
                         'every flat connector row is a level of exactly one service row')


if __name__ == '__main__':
    unittest.main()
