"""#629: every model-facing tool fits OpenAI's 1024-character ``function.description`` limit.

A contributor's Still on the OpenAI API failed every Work with HTTP 400: OpenAI
Chat Completions refuses the whole request when one ``tools[].function.description``
is longer than 1024 characters, and ``save_memory`` and ``forget_record`` (always
offered) were longer.  Argument guidance now lives in each argument's JSON Schema
``description``; one limit holds on every route, with no provider branch.

Evidence class: focused unit tests with a temporary store and a fake HTTP opener
that applies OpenAI's documented limit; no provider is contacted.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from personal_agent import providers
from personal_agent.agent_runtime import (CONNECTOR_PERMISSION_DEFINITION, DEFINITIONS, FINISH_DEFINITION,
                                          TOOL_DESCRIPTION_LIMIT, Capabilities, model_facing_text)
from personal_agent.bounded_execution import (BOUNDED_PROFILE, CLI_PROFILES, ISOLATED_PROFILE, AgentOSMcpTools,
                                              ReadOnlyAgentOSMcpTools, profile_mcp_tools)
from personal_agent.local_tools import LocalTools
from personal_agent.messages import msg
from personal_agent.providers import ModelAdapter, ProviderError
from personal_agent.quickstart_store import QuickStore
from personal_agent.search_providers import BRAVE_TOKEN, NATIVE_REASONS
from test_search_providers import BRAVE_KEY, OPENAI, main_ai, registry

OPENAI_ERROR = {'error': {'message': "Invalid 'tools[0].function.description': string too long. Expected a string "
                                     'with maximum length 1024. Request text: synthetic-owner-words',
                          'type': 'invalid_request_error', 'param': 'tools[0].function.description',
                          'code': 'string_above_max_length'}}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class OpenAIChatCompletions:
    """A fake opener that refuses a request the way OpenAI does when a tool description is too long."""

    def __init__(self):
        self.bodies = []

    def open(self, request, timeout=None):
        body = json.loads(request.data)
        self.bodies.append(body)
        for tool in body.get('tools') or []:
            if len(tool['function'].get('description') or '') > 1024:
                raise HTTPError(request.full_url, 400, 'Bad Request', {}, io.BytesIO(json.dumps(OPENAI_ERROR).encode()))
        return _Response(json.dumps({'model': 'gpt-4o-mini-2024-07-18', 'choices': [
            {'message': {'role': 'assistant', 'content': 'ok'}}]}).encode())


class Wired(unittest.TestCase):
    """Every tool, with every optional binding wired and every search provider configured."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        from personal_agent.api_requests import save_slot
        from personal_agent.context_observations import ContextObservations
        ContextObservations(self.store).set_controls({'enabled': True})
        save_slot(self.store, 'fixture-api', ['api.example.test'], 'fixture-value-0000')
        self.network = LocalTools(providers=registry(main=main_ai(OPENAI), bing_enabled=True,
                                                     **{BRAVE_TOKEN: BRAVE_KEY}))

    def caps(self):
        return Capabilities(self.store, None, {}, '', 'job', lambda *a: None, network=self.network,
                            document_access=False, allowed_tools=None, browser=lambda: None,
                            preparations=lambda *a: None, location_request=lambda *a: None,
                            owner_update=lambda *a: None, settings=lambda *a: None,
                            information_use=lambda *a: None, skills=object())

    def native(self):
        return [definition['function'] for definition in self.caps().definitions()] + [FINISH_DEFINITION['function']]


class EveryRouteFitsTheLimit(Wired):
    def assert_fits(self, tools, route):
        self.assertTrue(tools)
        for tool in tools:
            with self.subTest(route=route, tool=tool['name']):
                self.assertLessEqual(len(tool['description']), TOOL_DESCRIPTION_LIMIT)

    def test_the_native_definitions_the_api_route_sends(self):
        native = self.native()
        self.assertIn('web_search', [tool['name'] for tool in native])
        self.assert_fits(native, 'native')
        self.assert_fits([definition['function'] for definition in DEFINITIONS], 'declared')
        self.assert_fits([CONNECTOR_PERMISSION_DEFINITION['function']], 'connector permission')

    def test_the_mcp_tools_a_cli_route_lists(self):
        caps = self.caps()
        # A search-off turn adds its note to the search tool's description.
        wire = AgentOSMcpTools(caps)
        wire.native_search_reason = next(iter(NATIVE_REASONS))
        listed = wire.definitions()
        self.assertIn("The CLI's own web search is off for this turn",
                      next(tool['description'] for tool in listed if tool['name'] == 'web_search'))
        self.assert_fits(listed, 'bounded wire')
        self.assert_fits(ReadOnlyAgentOSMcpTools(caps).definitions(), 'isolated wire')
        for profile in {*CLI_PROFILES, BOUNDED_PROFILE, ISOLATED_PROFILE}:
            self.assert_fits(profile_mcp_tools(profile), profile)

    def test_the_moved_guidance_still_reaches_the_model(self):
        """The argument texts are part of what is sent: the long notes are still there, beside their argument."""
        native = {tool['name']: tool for tool in self.native()}
        provider = native['web_search']['parameters']['properties']['provider']
        self.assertIn('Providers configured by the owner', provider['description'])
        self.assertNotIn('Providers configured by the owner', native['web_search']['description'])
        self.assertIn('never save the request sentence with save_memory', model_facing_text(native['schedule_preparation']))
        self.assertIn('rerun="approved"', native['code_run']['parameters']['properties']['rerun']['description'])


class OpenAIAcceptsTheWorkTurn(Wired):
    def turn(self, tools):
        opener = OpenAIChatCompletions()
        with mock.patch.object(providers, 'build_opener', lambda *handlers: opener):
            message, _ = ModelAdapter().tool_turn(OPENAI, 'sk-fixture-0001', [{'role': 'user', 'content': 'hi'}],
                                                  tools, report_observed=True)
        return message, opener

    def test_a_fully_wired_work_turn_is_accepted(self):
        message, opener = self.turn(self.caps().definitions())
        self.assertEqual(message['content'], 'ok')
        self.assertEqual(len(opener.bodies[0]['tools']), len(self.caps().definitions()))

    def test_a_refused_request_names_the_providers_identifiers_and_never_its_message(self):
        """Before #629 the owner read only 'HTTP 400, check address, model and authentication'."""
        long_tool = {'type': 'function', 'function': {'name': 'x', 'description': 'd' * (TOOL_DESCRIPTION_LIMIT + 1),
                                                      'parameters': {'type': 'object', 'properties': {}}}}
        with self.assertRaises(ProviderError) as refused:
            self.turn([long_tool])
        text = str(refused.exception)
        self.assertEqual(refused.exception.status, 400)
        self.assertEqual(text, msg('providers.http_error_reason', status=400,
                                   reason='string_above_max_length (tools[0].function.description)'))
        self.assertNotIn('synthetic-owner-words', text, "the provider's free text stays out")

    def test_a_reason_that_is_not_a_plain_identifier_is_left_out(self):
        error = {'error': {'code': 'two words', 'type': 'x"y', 'param': "a'b", 'message': 'm'}}

        class Opener:
            def open(self, request, timeout=None):
                raise HTTPError(request.full_url, 400, 'Bad', {}, io.BytesIO(json.dumps(error).encode()))

        with mock.patch.object(providers, 'build_opener', lambda *handlers: Opener()):
            with self.assertRaises(ProviderError) as refused:
                providers.request_json('https://model.example.test/v1/chat/completions', {})
        self.assertEqual(str(refused.exception), msg('providers.http_error', status=400))


if __name__ == '__main__':
    unittest.main()
