"""#891: protocol selection stays bound to probes and qualified routes.

Evidence class: unit tests with a fixture HTTP transport and temporary stores.
No live provider, account, credential or subscription CLI is used.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

from personal_agent.decision_routes import DecisionRouteError
from personal_agent.providers import ModelAdapter, ProviderError
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.subscription_engines import SubscriptionEngines
from test_main_ai_routes import Transport, _Engine


CONFIG = {'provider': 'openai', 'endpoint': 'https://api.openai.com/v1', 'model': 'fixture-model'}
KEY = 'fixture-only-key'


class ProtocolTransport(Transport):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.refuse_api = set()

    def __call__(self, url, body, headers=None, timeout=60):
        self.urls.append(url)
        api = 'responses' if url.endswith('/responses') else 'chat_completions'
        if api in self.refuse_api:
            raise ProviderError('Fixture protocol unavailable', status=400)
        if api == 'responses':
            chat_body = {**body, 'messages': body['input'],
                         'tools': [{'type': 'function', 'function': {
                             key: value for key, value in tool.items() if key != 'type'}}
                                   for tool in body.get('tools', [])]}
            answer = super().__call__(url, chat_body, headers, timeout)
            message = answer['choices'][0]['message']
            output = [{'type': 'function_call', 'id': 'fc_' + call['id'],
                       'call_id': call['id'], **call['function']}
                      for call in message.get('tool_calls', [])]
            if message.get('content'):
                output.append({'type': 'message', 'role': 'assistant', 'content': [
                    {'type': 'output_text', 'text': message['content']}]})
            return {'status': 'completed', 'model': body['model'], 'output': output}
        return super().__call__(url, body, headers, timeout)


class ProtocolLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'state'
        self.transport = ProtocolTransport()
        self.service = self.make_service()
        self.store = self.service.store

    def make_service(self):
        service = AgentService(QuickStore(self.root), adapter=ModelAdapter(self.transport),
                               subscription_engines=SubscriptionEngines(finder=lambda _: None),
                               execution_adapter=_Engine())
        service.decision_routes.spawn = lambda run: run()
        service.decision_routes.timer = lambda: 0.0
        return service

    def assert_protocol(self, api):
        suffix = '/responses' if api == 'responses' else '/chat/completions'
        self.assertTrue(self.transport.urls)
        self.assertTrue(all(url.endswith(suffix) for url in self.transport.urls), self.transport.urls)
        self.transport.urls.clear()

    def activate(self, **selection):
        return self.service.activate_decision_route({
            'transport': 'direct_api', 'model': CONFIG['model'], **selection})

    def test_legacy_fingerprint_and_explicit_chat_are_equivalent(self):
        legacy = hashlib.sha256('openai|https://api.openai.com/v1|fixture-model'.encode()).hexdigest()
        self.assertEqual(self.service.model_fingerprint(CONFIG), legacy)
        self.assertEqual(self.service.model_fingerprint({**CONFIG, 'api': 'chat_completions'}), legacy)
        self.assertNotEqual(self.service.model_fingerprint({**CONFIG, 'api': 'responses'}), legacy)

    def test_protocol_suffix_in_model_name_does_not_alias_a_responses_proof(self):
        responses = {**CONFIG, 'api': 'responses'}
        chat = {**CONFIG, 'model': CONFIG['model'] + '|api=responses'}
        self.assertNotEqual(self.service.model_fingerprint(responses), self.service.model_fingerprint(chat))

    def test_strict_proof_and_readiness_are_bound_to_the_protocol(self):
        for checked_api, other_api in [('responses', 'chat_completions'), ('chat_completions', 'responses')]:
            with self.subTest(checked=checked_api):
                draft = {**CONFIG, 'api': checked_api, 'api_key': KEY}
                tested = self.service.test_model(draft, strict=True)
                self.assertTrue(tested['ok'], tested)
                pending = self.store.config('model_draft_test')
                before = self.store.config('model', {})
                with self.assertRaises(ValueError):
                    self.service.save_model({**draft, 'api': other_api,
                                             'test_proof': tested['test_proof']}, strict=True)
                self.assertEqual(self.store.config('model', {}), before)
                self.assertFalse(self.service.model_ready({**CONFIG, 'api': other_api}, pending['record']))
                self.assert_protocol(checked_api)  # refused apply sends nothing
                self.service.save_model({**draft, 'test_proof': tested['test_proof']}, strict=True)
                self.assertTrue(self.make_service().model_ready())

    def test_existing_responses_readiness_requires_a_protocol_specific_recheck(self):
        chat_proof = self.service.probe_model(CONFIG, KEY)
        self.assertTrue(chat_proof['ok'])
        self.store.put('model', {**CONFIG, 'api': 'responses'})
        self.store.secret('model_key', KEY)
        self.store.put('model_test', chat_proof)
        self.assertFalse(self.service.model_ready())
        self.transport.urls.clear()
        self.service.check_main_ai()
        self.assertTrue(self.service.model_ready())
        self.assert_protocol('responses')

    def test_decision_key_reselection_and_restart_keep_responses(self):
        self.service.save_decision_route_credential({'transport': 'direct_api', 'key': KEY})
        self.transport.refuse_api.add('chat_completions')
        self.activate(api='responses')
        self.assert_protocol('responses')
        self.assertEqual(self.store.config('decision_model')['api'], 'responses')
        self.activate()
        self.assert_protocol('responses')
        restarted = self.make_service()
        restarted.decision_judge.followup_relation('Try again', 'research', 'failed')
        self.assert_protocol('responses')
        self.assertEqual(restarted.store.config('decision_route')['api'], 'responses')

    def test_main_key_reselection_keeps_responses(self):
        self.service.save_model({**CONFIG, 'api_key': KEY})
        self.transport.refuse_api.add('chat_completions')
        self.activate(api='responses')
        self.assert_protocol('responses')
        self.activate()
        self.assert_protocol('responses')
        self.assertEqual(self.store.config('model'), CONFIG)

    def test_pre_fix_route_with_no_api_in_decision_model_can_be_reselected(self):
        self.service.save_decision_route_credential({'transport': 'direct_api', 'key': KEY})
        self.activate(api='responses')
        self.assert_protocol('responses')
        # The original PR wrote the protocol only to decision_route.
        self.store.put('decision_model', CONFIG)
        self.transport.refuse_api.add('chat_completions')
        self.activate()
        self.assert_protocol('responses')

    def test_explicit_chat_switch_is_qualified_persisted_and_used_after_restart(self):
        self.service.save_decision_route_credential({'transport': 'direct_api', 'key': KEY})
        self.activate(api='responses')
        self.assert_protocol('responses')
        self.activate(api='chat_completions')
        self.assert_protocol('chat_completions')
        self.assertNotIn('api', self.store.config('decision_model'))
        self.assertNotIn('api', self.store.config('decision_route'))
        self.make_service().decision_judge.followup_relation('Try again', 'research', 'failed')
        self.assert_protocol('chat_completions')

    def test_failed_protocol_change_preserves_active_config_and_key(self):
        self.service.save_decision_route_credential({'transport': 'direct_api', 'key': KEY})
        self.activate(api='responses')
        self.assert_protocol('responses')
        route, config = self.store.config('decision_route'), self.store.config('decision_model')
        self.transport.refuse_api.add('chat_completions')
        with self.assertRaises(DecisionRouteError):
            self.activate(api='chat_completions')
        self.assert_protocol('chat_completions')
        self.assertEqual(self.store.config('decision_route'), route)
        self.assertEqual(self.store.config('decision_model'), config)
        self.assertEqual(self.store.secret('decision_model_key'), KEY)
        self.service.decision_judge.followup_relation('Try again', 'research', 'failed')
        self.assert_protocol('responses')

    def test_reactivate_after_off_uses_saved_decision_protocol(self):
        self.service.save_decision_route_credential({'transport': 'direct_api', 'key': KEY})
        self.activate(api='responses')
        self.assert_protocol('responses')
        self.service.activate_decision_route({'transport': 'off'})
        self.transport.refuse_api.add('chat_completions')
        self.activate()
        self.assert_protocol('responses')

    def test_qualified_chat_route_overrides_responses_in_base_config(self):
        self.store.put('decision_model', {**CONFIG, 'api': 'responses'})
        self.store.secret('decision_model_key', KEY)
        route = {'provider': 'openai', 'requested_model': CONFIG['model']}
        config, key = self.service.decision_routes._direct_resolve(route)
        self.assertNotIn('api', config)
        self.assertEqual(key, KEY)


if __name__ == '__main__':
    unittest.main()
