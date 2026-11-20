"""#1050 Judgment API choices, exercised with fixture transports and a fake clock."""
import json
from urllib.parse import parse_qs, urlsplit

from personal_agent.decision_routes import DecisionRouteError
from test_decision_qualification_jobs import JobFixture
from test_main_ai_routes import Transport


class JudgmentTransport(Transport):
    def __init__(self):
        super().__init__()
        self.requests = []

    def __call__(self, url, body, headers=None, timeout=60):
        self.requests.append((url, body, headers))
        return super().__call__(url, body, headers, timeout)


class ApiModeTests(JobFixture):
    def service(self):
        service = super().service(openai=JudgmentTransport())
        self.list_calls = []
        service.decision_routes.models_transport = self.list_transport
        # The configs handed to the shared adapter: the effort rides there and the
        # adapter encodes it per provider (#1051), so it is checked at this seam.
        self.turns = []
        original = service.adapter.tool_turn

        def recording(config, *args, **kwargs):
            self.turns.append(dict(config))
            return original(config, *args, **kwargs)
        service.adapter.tool_turn = recording
        return service

    def list_transport(self, url, body, headers, timeout):
        self.list_calls.append((url, body, headers))
        parts = urlsplit(url)
        if parts.hostname == 'api.openai.com':
            return {'data': [{'id': model} for model in
                             ('gpt-6.2-sol', 'gpt-6.2-astra', 'gpt-6.1-terra', 'gpt-4o-mini', 'gpt-6-luna')]}
        if parts.hostname == 'api.anthropic.com':
            if 'after_id' not in parse_qs(parts.query):
                return {'has_more': True, 'last_id': 'claude-haiku-4-5-20251001', 'data': [
                    {'id': 'claude-opus-5', 'capabilities': {'effort': {'supported': False, 'low': {'supported': True}}}},
                    {'id': 'claude-haiku-4-5-20251001'}]}
            return {'has_more': False, 'data': [
                {'id': 'claude-haiku-5-5-20261001', 'capabilities': {'effort': {
                    'supported': True, 'high': {'supported': True}, 'max': {'supported': True},
                    'low': {'supported': True}, 'medium': {'supported': False}, 'xhigh': None}}}]}
        if parts.hostname == 'openrouter.ai':
            return {'data': [
                {'id': 'local/unknown', 'supported_parameters': ['reasoning']},
                {'id': 'openai/gpt-6-luna', 'reasoning': {'supported_efforts': ['low', 'high', 'invalid']}},
                {'id': 'anthropic/claude-haiku-5-5', 'reasoning': {'supported_efforts': None}},
                {'id': 'local/full', 'reasoning': {'default_effort': 'medium'}}]}
        self.fail(f'Unexpected model list endpoint {url}')

    def test_openai_full_list_uses_documented_efforts_and_slot_key(self):
        service = self.service()
        with self.assertRaises(DecisionRouteError):
            service.list_decision_models('openai')
        self.assertEqual(self.list_calls, [])
        self.store.secret('api_key:openai', 'openai-slot-fixture')
        record = service.list_decision_models('openai')
        self.assertEqual(self.list_calls, [('https://api.openai.com/v1/models', None,
                                           {'Authorization': 'Bearer openai-slot-fixture'})])
        all_levels = ['low', 'medium', 'high', 'xhigh', 'max']
        self.assertEqual([(row['id'], row['recommended'], row['rank'], row['efforts']) for row in record['models']], [
            ('gpt-6-luna', True, 0, all_levels),
            ('gpt-6.1-terra', True, 1, all_levels),
            ('gpt-4o-mini', False, None, []),
            ('gpt-6.2-astra', False, None, all_levels),
            ('gpt-6.2-sol', False, None, all_levels),
        ])
        status = service.decision_routes.status()
        api = next(row for row in status['apis'] if row['id'] == 'openai')
        self.assertTrue(status['direct_api']['configured'])
        self.assertEqual((api['configured'], api['key_source'], api['has_decision_key']), (True, 'slot', False))
        self.assertEqual(api['model_efforts']['gpt-6.2-sol'], all_levels)
        self.assertEqual(status['active']['transport'], 'none')
        self.assertNotIn('openai-slot-fixture', json.dumps(status))
        self.assertEqual(self.openai.requests, [])

    def test_anthropic_full_list_paginates_and_keeps_dated_models_and_aliases(self):
        service = self.service()
        self.store.secret('api_key:anthropic', 'anthropic-slot-fixture')
        record = service.list_decision_models('anthropic')
        url = 'https://api.anthropic.com/v1/models?limit=1000'
        headers = {'x-api-key': 'anthropic-slot-fixture', 'anthropic-version': '2023-06-01'}
        self.assertEqual(self.list_calls, [(url, None, headers),
                                           (url + '&after_id=claude-haiku-4-5-20251001', None, headers)])
        efforts = ['low', 'high', 'max']
        self.assertEqual([(row['id'], row['recommended'], row['efforts']) for row in record['models']], [
            ('claude-haiku-5-5', True, efforts), ('claude-haiku-4-5', True, []),
            ('claude-haiku-4-5-20251001', False, []), ('claude-haiku-5-5-20261001', False, efforts),
            ('claude-opus-5', False, []),
        ])
        self.assertEqual(service.decision_routes.api_model_efforts('anthropic', 'claude-haiku-5-5'), tuple(efforts))

    def test_anthropic_pagination_stops_after_five_pages(self):
        service = self.service()
        self.store.secret('api_key:anthropic', 'anthropic-slot-fixture')
        calls = []

        def page(url, *_args):
            calls.append(url)
            model = f'model-{len(calls)}'
            return {'has_more': True, 'last_id': model, 'data': [{'id': model}]}

        service.decision_routes.models_transport = page
        self.assertEqual(len(service.list_decision_models('anthropic')['models']), 5)
        self.assertEqual(len(calls), 5)

    def test_openrouter_full_list_only_offers_declared_or_conservative_efforts(self):
        service = self.service()
        self.store.secret('api_key:openrouter', 'openrouter-slot-fixture')
        record = service.list_decision_models('openrouter')
        self.assertEqual(self.list_calls, [('https://openrouter.ai/api/v1/models', None,
                                           {'Authorization': 'Bearer openrouter-slot-fixture'})])
        self.assertEqual([(row['id'], row['recommended'], row['efforts']) for row in record['models']], [
            ('openai/gpt-6-luna', True, ['low', 'high']), ('anthropic/claude-haiku-5-5', True, ['low', 'medium', 'high']),
            ('local/full', False, []), ('local/unknown', False, []),
        ])

    def test_separate_openai_key_precedence_is_shared_by_status_and_listing(self):
        service = self.service()
        self.store.put('model', {'provider': 'openai', 'endpoint': 'https://api.openai.com/v1', 'model': 'gpt-5'})
        for slot, source in (('model_key', 'main'), ('api_key:openai', 'slot'), ('decision_model_key', 'decision')):
            with self.subTest(source=source):
                key = f'fixture-{source}'
                self.store.secret(slot, key)
                status = service.decision_routes.status()
                self.assertEqual(status['direct_api']['key_source'], source)
                self.assertEqual({api['id']: api['key_source'] for api in status['apis']},
                                 {'openai': source, 'anthropic': '', 'openrouter': ''})
                service.list_decision_models('openai')
                self.assertEqual(self.list_calls[-1][2], {'Authorization': f'Bearer {key}'})

    def test_direct_api_auto_and_pinned_qualify_then_use_the_same_model_effort(self):
        cases = (
            ('anthropic', 'Anthropic API', 'https://api.anthropic.com/v1/messages',
             'claude-haiku-5-5', 'claude-haiku-5-5-20261001', 'output_config', 'max'),
            ('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1/chat/completions',
             'openai/gpt-6-luna', 'anthropic/claude-haiku-5-5', 'reasoning', 'medium'),
        )
        service = self.service()
        for api, label, url, ranked, pinned, field, pinned_effort in cases:
            with self.subTest(api=api):
                base = {'transport': 'direct_api', 'api': api}
                with self.assertRaisesRegex(DecisionRouteError, label):
                    service.activate_decision_route(base)
                self.assertEqual(service.decision_routes._checks()[f'direct_api:{api}']['failure'], 'not-configured')
                key = f'{api}-slot-fixture'
                self.store.secret(f'api_key:{api}', key)
                service.list_decision_models(api)
                for params, model, effort, policy in (({}, ranked, 'low', 'fastest_qualified'),
                                                      ({'model': pinned, 'effort': pinned_effort},
                                                       pinned, pinned_effort, 'explicit')):
                    self.openai.requests.clear()
                    self.turns.clear()
                    status = service.activate_decision_route({**base, **params})
                    active = status['active']
                    self.assertEqual((active['api'], active['requested_model'], active['effort'], active['model_policy']),
                                     (api, model, effort, policy))
                    self.assertEqual((active['destination'], active['key_source']), (urlsplit(url).hostname, 'slot'))
                    self.assertEqual(status['effective']['params']['label'], label)
                    self.assertEqual(next(row for row in status['apis'] if row['id'] == api)['check']['state'], 'active')
                    qualified = {turn['model']: turn.get('effort') for turn in self.turns}
                    self.assertEqual(qualified[model], effort)
                    if policy == 'fastest_qualified':
                        self.assertEqual(len(qualified), 2)
                        if api == 'anthropic':
                            self.assertIsNone(qualified['claude-haiku-4-5'])
                    self.assertEqual(self.judge(service).value, 'retry')
                    actual_url, body, headers = self.openai.requests[-1]
                    self.assertEqual((actual_url, body['model'], self.turns[-1].get('effort')), (url, model, effort))
                    self.assertIn(key, (value.removeprefix('Bearer ') for value in headers.values()))
                    self.assertEqual(self.store.config('decision_audit')[-1]['effort'], effort)
                self.assertIsNone(self.store.config('decision_model'))
                self.store.secret(f'api_key:{api}', '')
                self.store.secret('api_key:openai', 'other-provider-key')
                count = len(self.openai.requests)
                self.assertFalse(service.decision_routes.status()['active']['available'])
                self.assertEqual(self.judge(service).outcome, 'unavailable')
                self.assertEqual(len(self.openai.requests), count)

    def test_refuses_unknown_and_unsupported_api_effort_without_a_model_call(self):
        service = self.service()
        self.store.secret('api_key:openai', 'openai-slot-fixture')
        before = service.activate_decision_route({'transport': 'off'})['active']
        for model, effort in (('gpt-6.2-sol', 'minimal'), ('unknown-model', 'low'), ('gpt-6-luna', 'typo')):
            with self.subTest(model=model, effort=effort), self.assertRaises(DecisionRouteError):
                service.activate_decision_route({'transport': 'direct_api', 'model': model, 'effort': effort})
        self.assertEqual(self.openai.requests, [])
        self.assertEqual(service.decision_routes.status()['active'], before)

    def test_fallback_uses_the_separately_qualified_provider_label_and_key(self):
        service = self.service()
        service.save_main_ai_key({'provider': 'anthropic', 'key': 'anthropic-slot-fixture'})
        service.activate_decision_route({'transport': 'direct_api', 'api': 'anthropic', 'model': 'claude-haiku-5-5'})
        previous = service.decision_routes.active()
        self.store.put('decision_route', {**previous, 'mode': 'follow_main', 'main': 'anthropic'})
        service.save_main_ai_key({'provider': 'openai', 'key': 'openai-slot-fixture'})
        self.openai.refuse_decide.add('api.openai.com')
        service.activate_main_ai({'route': 'openai'})
        self.assertTrue(service.run_due_qualification())
        status = service.decision_routes.status()
        self.assertEqual((status['active']['source'], status['active']['destination']), ('fallback', 'api.anthropic.com'))
        self.assertEqual(status['effective']['params']['label'], 'Anthropic API')
        self.assertEqual(self.judge(service).value, 'retry')
        self.assertEqual(self.openai.requests[-1][2]['x-api-key'], 'anthropic-slot-fixture')

    def test_follow_switches_from_explicit_route_and_preserves_pins_per_main(self):
        service = self.service()
        self.store.secret('api_key:openai', 'openai-slot-fixture')
        service.activate_decision_route({'transport': 'direct_api', 'model': 'gpt-6-luna', 'effort': 'medium'})
        service.activate_main_ai({'route': 'openai'})
        before = service.decision_routes.active()
        count, turns = len(self.openai.requests), len(self.turns)
        pinned = {'model': 'gpt-6.2-sol', 'effort': 'high'}
        status = service.activate_decision_route({'transport': 'follow_main', **pinned})
        self.assertEqual((status['mode'], status['qualification']['state']), ('explicit', 'queued'))
        self.assertEqual(status['follow_pin'], pinned)
        self.assertTrue(status['follow']['pinned'])
        self.assertEqual(status['follow']['candidates'], ['gpt-6.2-sol'])
        self.assertEqual(service.decision_routes.active(), before)
        self.assertEqual(len(self.openai.requests), count)
        self.assertTrue(service.run_due_qualification())
        status = service.decision_routes.status()
        active = status['active']
        self.assertEqual((status['mode'], active['model_policy'], active['requested_model'], active['effort']),
                         ('follow_main', 'explicit', 'gpt-6.2-sol', 'high'))
        self.assertTrue(self.turns[turns:])
        self.assertTrue(all(turn.get('effort') == 'high' for turn in self.turns[turns:]))
        self.store.secret('api_key:openai', 'unverified-replacement')
        self.assertEqual(self.judge(service).value, 'retry')
        self.assertEqual(self.openai.requests[-1][2]['Authorization'], 'Bearer openai-slot-fixture')

        service.save_main_ai_key({'provider': 'anthropic', 'key': 'anthropic-slot-fixture'})
        service.activate_main_ai({'route': 'anthropic'})
        self.assertTrue(service.run_due_qualification())
        status = service.decision_routes.status()
        self.assertEqual(status['active']['requested_model'], 'claude-haiku-5-5')
        self.assertFalse(status['follow']['pinned'])
        self.assertIsNone(status['follow_pin'])

        service.activate_main_ai({'route': 'openai'})
        self.assertTrue(service.run_due_qualification())
        self.assertEqual(service.decision_routes.active()['requested_model'], 'gpt-6.2-sol')

        before = service.decision_routes.active()
        status = service.activate_decision_route({'transport': 'follow_main'})
        self.assertIsNone(status['follow_pin'])
        self.assertFalse(status['follow']['pinned'])
        self.assertEqual(service.decision_routes.active(), before)
        self.assertTrue(service.run_due_qualification())
        active = service.decision_routes.active()
        self.assertEqual((active['model_policy'], active['requested_model'], active['effort']),
                         ('fastest_qualified', 'gpt-6-luna', 'low'))
        self.assertEqual(self.judge(service).value, 'retry')
        self.assertEqual(self.turns[-1].get('effort'), 'low')
