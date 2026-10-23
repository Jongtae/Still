"""#211 CLOUD-AI-01: Claude Code and Codex through Amazon Bedrock, Microsoft Foundry or Azure OpenAI.

Model-free: injected runners, a temporary store and a fake clock.  The
negative matrix of #211: credentials never in the model's shell environment,
argv, logs or Evidence; an undocumented variable name is refused; a spent
daily cap launches nothing and falls back nowhere; a Settings change applies
from the next Work; the subscription route is unchanged.
"""
import json
import tempfile
import unittest
from pathlib import Path

from personal_agent import cloud_engines, owner_mcp
from personal_agent.bounded_execution import AgentOSMcpTools, BoundedExecutionAdapter, ExecutionError, ExecutionResult
from personal_agent.cloud_engines import (BUDGET_EXCEEDED, NOT_CONFIGURED, PROVIDERS, CloudEngines, CloudLaunchError,
                                          secret_slot)
from personal_agent.current_context import stored_secret_values
from personal_agent.decision import OUTCOME_DECIDED, OUTCOME_UNAVAILABLE, DecisionContext
from personal_agent.decision_adapters import SubscriptionCliDecisionEngine
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.settings_orchestrator import SettingsError
from personal_agent.subscription_engines import SubscriptionEngines

BEDROCK_TOKEN = 'ABSKQmVkcm9ja0FQSUtleS1maXh0dXJlLXNlY3JldC0wMDAx'
AZURE_KEY = 'azure-fixture-key-0123456789abcdef'
FOUNDRY_KEY = 'foundry-fixture-key-0123456789abcdef'
OAUTH = 'sk-ant-oat01-' + 'x' * 40
#: The documented names this change forwards, pinned against the vendor docs (2026-10-23).
DOCUMENTED = {
    'bedrock': {'CLAUDE_CODE_USE_BEDROCK', 'AWS_REGION', 'AWS_PROFILE', 'AWS_CONFIG_FILE', 'AWS_SHARED_CREDENTIALS_FILE',
                'ANTHROPIC_BEDROCK_BASE_URL', 'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
                'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'AWS_BEARER_TOKEN_BEDROCK', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
                'AWS_SESSION_TOKEN'},
    'foundry': {'CLAUDE_CODE_USE_FOUNDRY', 'ANTHROPIC_FOUNDRY_RESOURCE', 'ANTHROPIC_FOUNDRY_BASE_URL',
                'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
                'ANTHROPIC_FOUNDRY_API_KEY', 'ANTHROPIC_FOUNDRY_AUTH_TOKEN'},
    'azure-openai': {'AZURE_OPENAI_API_KEY'},
}


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class _Caps:
    job_id = 'job'
    private_provenance = set()

    class store:
        root = '/tmp/agentos-cloud-test'

    def execute(self, name, arguments):
        return {'ok': True}


class _Done:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def answer(argv):
    if argv[0].endswith('claude'):
        return _Done(stdout=json.dumps({'result': 'ok'}))
    return _Done(stdout=json.dumps({'item': {'type': 'agent_message', 'text': 'ok'}}))


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = QuickStore(Path(self.tmp.name) / 'state')
        self.clock = Clock()
        self.cloud = CloudEngines(self.store, clock=self.clock)

    def bedrock(self, cap=5, **settings):
        self.cloud.save_profile('claude-code', 'bedrock', {'AWS_REGION': 'us-east-1', **settings}, cap)
        self.cloud.save_secret('claude-code', 'bedrock', 'AWS_BEARER_TOKEN_BEDROCK', BEDROCK_TOKEN)

    def azure(self, cap=5):
        self.cloud.save_profile('codex', 'azure-openai', {'base_url': 'https://corp.openai.azure.com/openai',
                                                          'api_version': '2025-04-01-preview'}, cap)
        self.cloud.save_secret('codex', 'azure-openai', 'AZURE_OPENAI_API_KEY', AZURE_KEY)


class Profiles(StoreCase):
    def test_only_vendor_documented_names_are_offered(self):
        for provider_id, names in DOCUMENTED.items():
            provider = PROVIDERS[provider_id]
            with self.subTest(provider=provider_id):
                self.assertEqual(set(provider.fixed) | set(provider.settings) | set(provider.secrets), names)
                self.assertTrue(all(url.startswith('https://') for url in provider.docs))
        self.assertEqual(PROVIDERS['azure-openai'].config_keys, ('base_url', 'api_version'))

    def test_an_undocumented_variable_name_is_refused(self):
        for engine, provider, name in (('claude-code', 'bedrock', 'NODE_OPTIONS'), ('claude-code', 'bedrock', 'ANTHROPIC_BASE_URL'),
                                       ('claude-code', 'foundry', 'AZURE_CLIENT_SECRET'), ('codex', 'azure-openai', 'OPENAI_BASE_URL'),
                                       # a credential name is never a plain setting
                                       ('claude-code', 'bedrock', 'AWS_SECRET_ACCESS_KEY')):
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as caught:
                    self.cloud.save_profile(engine, provider, {'AWS_REGION': 'us-east-1', name: 'x'}, 5)
                self.assertIn(name, str(caught.exception))
        with self.assertRaises(ValueError):
            self.cloud.save_secret('claude-code', 'bedrock', 'PATH', '/tmp/evil-bin-path')
        with self.assertRaises(ValueError):
            self.cloud.save_secret('codex', 'azure-openai', 'OPENAI_API_KEY', AZURE_KEY)
        self.assertEqual(self.cloud.provider('claude-code'), 'subscription', 'nothing was saved')
        self.assertEqual(self.store.secret_items(cloud_engines.SECRET_PREFIX), {})

    def test_values_and_engines_are_checked(self):
        bad = (('claude-code', 'bedrock', {'AWS_REGION': 'us-east-1/../x'}), ('claude-code', 'bedrock', {'AWS_REGION': 'us-east-1',
               'ANTHROPIC_BEDROCK_BASE_URL': 'http://gateway.corp'}),
               ('claude-code', 'foundry', {'ANTHROPIC_FOUNDRY_RESOURCE': 'https://x.services.ai.azure.com'}),
               ('codex', 'azure-openai', {'base_url': 'https://user:pw@corp.openai.azure.com/openai'}),
               ('codex', 'bedrock', {'AWS_REGION': 'us-east-1'}), ('claude-code', 'azure-openai', {'base_url': 'https://a.b'}))
        for engine, provider, settings in bad:
            with self.subTest(provider=provider, settings=settings):
                with self.assertRaises(ValueError):
                    self.cloud.save_profile(engine, provider, settings, 5)
        for cap in (None, 0, -1, True, 10_001, '5'):
            with self.subTest(cap=cap):
                with self.assertRaises(ValueError):
                    self.cloud.save_profile('claude-code', 'bedrock', {'AWS_REGION': 'us-east-1'}, cap)
        with self.assertRaises(ValueError):
            self.cloud.save_secret('claude-code', 'bedrock', 'AWS_BEARER_TOKEN_BEDROCK', 'has a space in it ok')

    def test_a_named_aws_profile_needs_an_explicit_file(self):
        """#211 review: the per-turn HOME has no ~/.aws, so AWS_PROFILE alone is an unfinished profile."""
        self.cloud.save_profile('claude-code', 'bedrock', {'AWS_REGION': 'us-east-1', 'AWS_PROFILE': 'work'}, 5)
        with self.assertRaises(CloudLaunchError) as raised:
            self.cloud.launch('claude-code')
        self.assertEqual(raised.exception.failure_class, NOT_CONFIGURED)
        self.assertIn('AWS_CONFIG_FILE', str(raised.exception))
        self.cloud.save_profile('claude-code', 'bedrock', {'AWS_REGION': 'us-east-1', 'AWS_PROFILE': 'work',
                                                           'AWS_SHARED_CREDENTIALS_FILE': '/Users/owner/.aws/credentials'}, 5)
        self.assertEqual(self.cloud.launch('claude-code').environment()['AWS_PROFILE'], 'work')

    def test_an_unfinished_profile_never_launches(self):
        self.cloud.save_profile('claude-code', 'bedrock', {'AWS_REGION': 'us-east-1'}, 5)
        with self.assertRaises(CloudLaunchError) as caught:
            self.cloud.launch('claude-code')
        self.assertEqual(caught.exception.failure_class, NOT_CONFIGURED)
        self.assertIn('AWS_BEARER_TOKEN_BEDROCK', str(caught.exception))
        self.cloud.save_secret('claude-code', 'bedrock', 'AWS_BEARER_TOKEN_BEDROCK', BEDROCK_TOKEN)
        self.assertEqual(self.cloud.launch('claude-code').provider, 'bedrock')
        # An AWS profile (SSO or credential_process in the owner's AWS config) is a credential source too.
        self.cloud.save_secret('claude-code', 'bedrock', 'AWS_BEARER_TOKEN_BEDROCK', '')
        self.cloud.save_profile('claude-code', 'bedrock', {'AWS_REGION': 'eu-central-1', 'AWS_PROFILE': 'work',
                                                           'AWS_CONFIG_FILE': '/Users/owner/.aws/config'}, 5)
        env = self.cloud.launch('claude-code').environment()
        self.assertEqual((env['AWS_PROFILE'], env['AWS_CONFIG_FILE']), ('work', '/Users/owner/.aws/config'))

    def test_a_tampered_row_still_forwards_documented_names_only(self):
        self.bedrock()
        rows = self.store.config(cloud_engines.PROFILE_KEY)
        rows['claude-code']['settings'].update({'NODE_OPTIONS': '--require /tmp/x.js', 'AWS_REGION': 'us-east-1'})
        self.store.put(cloud_engines.PROFILE_KEY, rows)
        env = self.cloud.launch('claude-code').environment()
        self.assertNotIn('NODE_OPTIONS', env)
        self.assertLessEqual(set(env), DOCUMENTED['bedrock'])

    def test_a_launch_never_pairs_one_revision_with_another_credential(self):
        """#211 review: a credential saved while a launch resolves makes it resolve again."""
        self.bedrock()
        resolve, raced = self.cloud._resolve, []

        def racing(engine_id):
            launch = resolve(engine_id)
            if not raced:
                raced.append(True)
                self.cloud.save_secret('claude-code', 'bedrock', 'AWS_BEARER_TOKEN_BEDROCK', 'fixture-bedrock-token-0002')
            return launch
        self.cloud._resolve = racing
        launch = self.cloud.launch('claude-code')
        self.assertEqual(launch.identity(), self.cloud.identity('claude-code'))
        self.assertEqual(launch.secret_values['AWS_BEARER_TOKEN_BEDROCK'], 'fixture-bedrock-token-0002')

    def test_a_credential_change_moves_the_identity_before_the_value(self):
        self.bedrock()
        before = self.cloud.identity('claude-code')
        self.cloud.save_secret('claude-code', 'bedrock', 'AWS_BEARER_TOKEN_BEDROCK', 'fixture-bedrock-token-0003')
        self.assertNotEqual(self.cloud.identity('claude-code'), before)
        self.assertEqual(self.cloud.launch('claude-code').identity(), self.cloud.identity('claude-code'))

    def test_status_names_what_is_saved_and_never_a_value(self):
        self.bedrock()
        status = self.cloud.status('claude-code')
        self.assertEqual(status['provider'], 'bedrock')
        self.assertEqual(status['destination'], 'Amazon Bedrock (us-east-1)')
        bedrock = [row for row in status['providers'] if row['id'] == 'bedrock'][0]
        self.assertEqual(bedrock['saved_secrets'], ['AWS_BEARER_TOKEN_BEDROCK'])
        self.assertNotIn(BEDROCK_TOKEN, json.dumps(status))
        self.assertEqual(self.cloud.status('codex')['provider'], 'subscription')

    def test_stored_credentials_are_redacted_like_every_known_secret(self):
        self.bedrock()
        self.assertIn(BEDROCK_TOKEN, stored_secret_values(self.store))

    def test_the_daily_cap_counts_launches_and_resets_the_next_day(self):
        self.bedrock(cap=2)
        launch = self.cloud.launch('claude-code')
        self.assertEqual(self.cloud.admit(launch), 1)
        self.assertEqual(self.cloud.admit(launch), 2)
        with self.assertRaises(CloudLaunchError) as caught:
            self.cloud.admit(launch)
        self.assertEqual(caught.exception.failure_class, BUDGET_EXCEEDED)
        self.assertIn('2회', str(caught.exception))
        self.assertEqual(self.cloud.used_today('claude-code', 'bedrock'), 2, 'a refused launch is not counted')
        self.clock.now += 86_400
        self.assertEqual(self.cloud.admit(launch), 1)

    def test_codex_azure_arguments_are_the_documented_model_providers_keys(self):
        self.azure()
        args = self.cloud.launch('codex').codex_arguments()
        overrides = [args[i + 1] for i, part in enumerate(args) if part == '-c']
        self.assertEqual(overrides, ['model_provider="azure"', 'model_providers.azure.name="Azure OpenAI"',
                                     'model_providers.azure.base_url="https://corp.openai.azure.com/openai"',
                                     'model_providers.azure.env_key="AZURE_OPENAI_API_KEY"',
                                     'model_providers.azure.wire_api="responses"',
                                     'model_providers.azure.query_params={"api-version"="2025-04-01-preview"}'])
        self.assertNotIn(AZURE_KEY, ' '.join(args))

    def test_one_shell_exclusion_carries_every_withheld_name(self):
        merged = owner_mcp.merge_shell_exclusion(['-c', 'mcp_servers.a.command="x"', '-c',
                                                  'shell_environment_policy.exclude=["AGENTOS_MCP_0_H0"]'],
                                                 ['AZURE_OPENAI_API_KEY'])
        self.assertEqual(merged, ['-c', 'mcp_servers.a.command="x"', '-c',
                                  'shell_environment_policy.exclude=["AGENTOS_MCP_0_H0", "AZURE_OPENAI_API_KEY"]',
                                  '--disable', 'shell_snapshot'])
        self.assertEqual(owner_mcp.merge_shell_exclusion([], ['AZURE_OPENAI_API_KEY']),
                         ['-c', 'shell_environment_policy.exclude=["AZURE_OPENAI_API_KEY"]', '--disable', 'shell_snapshot'])
        self.assertEqual(owner_mcp.merge_shell_exclusion(owner_mcp.shell_exclusion(['A']), ['B']),
                         owner_mcp.shell_exclusion(['A', 'B']), 'one exclusion and one snapshot switch, never two')

    def test_an_owner_mcp_server_never_gets_or_replaces_a_cloud_credential(self):
        """#211 review: provider credential names are the CLI's own, not an owner MCP server's."""
        reserved = ('AZURE_OPENAI_API_KEY',)
        cases = {
            'stdio env': {'type': 'stdio', 'command': '/bin/srv', 'env': {'AZURE_OPENAI_API_KEY': 'x' * 12}},
            'named header': {'type': 'streamable_http', 'url': 'https://mcp.example/mcp',
                             'env_http_headers': {'Authorization': 'AZURE_OPENAI_API_KEY'}},
            'bearer token': {'type': 'streamable_http', 'url': 'https://mcp.example/mcp',
                             'bearer_token_env_var': 'AZURE_OPENAI_API_KEY'},
        }
        for label, transport in cases.items():
            with self.subTest(label):
                self.assertEqual(owner_mcp.codex_launch({'srv': transport}, reserved=reserved), ([], {}, []))
                self.assertNotEqual(owner_mcp.codex_launch({'srv': transport})[0], [], 'loads when nothing is reserved')
        args, env, _secret = owner_mcp.codex_launch(
            {'srv': {'type': 'stdio', 'command': '/bin/srv', 'env': {'OTHER_TOKEN': 'y' * 12}}}, reserved=reserved)
        self.assertEqual(env, {'OTHER_TOKEN': 'y' * 12}, 'an unrelated server still loads')

    def test_the_snapshot_that_restores_excluded_values_is_off_whenever_a_value_is_withheld(self):
        # Observed on codex-cli 0.153.4 (local fixture model, #211): with shell_snapshot on, an
        # excluded variable is printed by the model's `env`; the owner's MCP secrets (#1296) too.
        args, env, secret = owner_mcp.codex_launch({'srv': {'type': 'stdio', 'command': '/bin/srv', 'env': {'TOKEN_X': 'v' * 12}}})
        self.assertEqual(secret, ['TOKEN_X'])
        self.assertEqual(args[-4:], ['-c', 'shell_environment_policy.exclude=["TOKEN_X"]', '--disable', 'shell_snapshot'])
        self.assertEqual(owner_mcp.shell_exclusion([]), [])


class WorkTurns(StoreCase):
    def adapter(self, runner):
        profile = Path(self.tmp.name) / 'codex-home'
        profile.mkdir(exist_ok=True)
        return BoundedExecutionAdapter(finder=lambda name: '/runtime/' + name, runner=runner,
                                       runtime_root=Path(self.tmp.name) / 'turns', codex_home=profile,
                                       credentials=lambda engine: OAUTH if engine == 'claude-code' else '', cloud=self.cloud)

    def test_claude_code_on_bedrock_gets_documented_names_and_no_shell(self):
        self.bedrock()
        seen = []

        def runner(argv, **kwargs):
            seen.append((argv, kwargs['env'], json.loads((Path(kwargs['cwd']) / 'agentos-mcp.json').read_text())))
            return answer(argv)
        result = self.adapter(runner).execute('claude-code', 'hi', AgentOSMcpTools(_Caps()))
        argv, env, config = seen[0]
        self.assertEqual({key: env[key] for key in ('CLAUDE_CODE_USE_BEDROCK', 'AWS_REGION', 'AWS_BEARER_TOKEN_BEDROCK')},
                         {'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_REGION': 'us-east-1', 'AWS_BEARER_TOKEN_BEDROCK': BEDROCK_TOKEN})
        self.assertEqual(env['CLAUDE_CODE_SUBPROCESS_ENV_SCRUB'], '1', "the CLI's documented scrub of subprocess env")
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', env, 'a cloud turn never carries the personal subscription token')
        self.assertLessEqual(set(env) - {'HOME', 'PATH', 'LANG', 'PYTHONPATH', 'CLAUDE_CODE_SUBPROCESS_ENV_SCRUB'},
                             DOCUMENTED['bedrock'])
        self.assertNotEqual(env['HOME'], str(Path.home()))
        tools = argv[argv.index('--tools') + 1]
        self.assertEqual(tools, '', 'no built-in tool: the model has no Bash to print its environment')
        self.assertNotIn('Bash', ' '.join(argv))
        self.assertNotIn(BEDROCK_TOKEN, json.dumps(argv))
        self.assertEqual(config['mcpServers']['agentos']['env'],
                         {'CLAUDE_CODE_OAUTH_TOKEN': '', 'AWS_BEARER_TOKEN_BEDROCK': ''}, 'the bridge never gets it')
        self.assertEqual(result.meta['cloud_provider'], 'bedrock')
        self.assertEqual(result.meta['cloud_credentials'], ['AWS_BEARER_TOKEN_BEDROCK'])
        self.assertNotIn(BEDROCK_TOKEN, json.dumps(result.meta), 'Evidence names the credential, never its value')

    def test_an_attached_photo_adds_read_only(self):
        self.bedrock()
        seen = []
        adapter = self.adapter(lambda argv, **kw: seen.append(argv) or answer(argv))
        adapter.execute('claude-code', 'hi', AgentOSMcpTools(_Caps()),
                        images=[{'data': b'\xff\xd8\xff', 'mime_type': 'image/jpeg'}])
        argv = seen[0]
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read')
        self.assertEqual(argv[argv.index('--tools') + 2], '--allowedTools')

    def test_codex_on_azure_withholds_the_key_from_shell_commands(self):
        self.azure()
        seen = []
        result = self.adapter(lambda argv, **kw: seen.append((argv, kw['env'])) or answer(argv)).execute(
            'codex', 'hi', AgentOSMcpTools(_Caps()))
        argv, env = seen[0]
        self.assertEqual(env['AZURE_OPENAI_API_KEY'], AZURE_KEY)
        self.assertNotIn(AZURE_KEY, json.dumps(argv))
        self.assertIn('shell_environment_policy.exclude=["AZURE_OPENAI_API_KEY"]', argv)
        self.assertEqual(argv[argv.index('shell_snapshot') - 1], '--disable')
        self.assertIn('model_provider="azure"', argv)
        self.assertIn('--ignore-user-config', argv)
        self.assertIn('web_search="disabled"', argv)
        self.assertEqual(result.meta['cloud_destination'], 'Azure OpenAI (corp.openai.azure.com)')

    def test_a_spent_cap_launches_nothing_and_falls_back_nowhere(self):
        self.bedrock(cap=1)
        calls = []
        adapter = self.adapter(lambda argv, **kw: calls.append(kw['env']) or answer(argv))
        adapter.execute('claude-code', 'first', AgentOSMcpTools(_Caps()))
        with self.assertRaises(ExecutionError) as caught:
            adapter.execute('claude-code', 'second', AgentOSMcpTools(_Caps()))
        self.assertEqual(caught.exception.failure_class, BUDGET_EXCEEDED)
        self.assertEqual(caught.exception.meta, {'launched': False})
        self.assertEqual(len(calls), 1, 'no further model call, on the cloud or the subscription')

    def test_an_echoed_credential_is_removed_from_failures_and_logs(self):
        self.bedrock()
        adapter = self.adapter(lambda argv, **kw: _Done(returncode=1, stderr=f'denied for token {BEDROCK_TOKEN}'))
        with self.assertLogs('personal_agent.engine', level='WARNING') as logs:
            with self.assertRaises(ExecutionError) as caught:
                adapter.execute('claude-code', 'hi', AgentOSMcpTools(_Caps()))
        exposed = str(caught.exception) + json.dumps(caught.exception.diagnostics()) + json.dumps(caught.exception.meta) \
            + '\n'.join(logs.output)
        self.assertNotIn(BEDROCK_TOKEN, exposed)
        self.assertNotIn(BEDROCK_TOKEN[:16], exposed)

    def test_a_work_keeps_the_profile_it_started_with(self):
        self.bedrock()
        seen = []
        adapter = self.adapter(lambda argv, **kw: seen.append(kw['env']) or answer(argv))
        tools = AgentOSMcpTools(_Caps())
        tools.cloud_profile = self.cloud.launch('claude-code')
        self.cloud.save_profile('claude-code', 'subscription')
        adapter.execute('claude-code', 'later attempt of the same Work', tools)
        self.assertEqual(seen[-1].get('CLAUDE_CODE_USE_BEDROCK'), '1', 'the running Work keeps its launch')
        adapter.execute('claude-code', 'the next Work', AgentOSMcpTools(_Caps()))
        self.assertNotIn('CLAUDE_CODE_USE_BEDROCK', seen[-1])
        self.assertEqual(seen[-1]['CLAUDE_CODE_OAUTH_TOKEN'], OAUTH, 'the next Work is on the subscription')

    def test_the_login_check_describes_the_cloud_profile(self):
        self.bedrock()
        seen = []
        adapter = self.adapter(lambda argv, **kw: seen.append((argv, kw['env'])) or _Done(stdout=json.dumps(
            {'loggedIn': True, 'authMethod': 'third_party', 'apiProvider': 'bedrock'})))
        self.assertEqual(adapter.login_status('claude-code'), {'state': 'token-saved', 'detail': 'third_party'},
                         'configured, not validated (observed 2.1.287 output shape)')
        argv, env = seen[0]
        self.assertEqual(argv[1:], ['auth', 'status', '--json'])
        self.assertEqual(env['CLAUDE_CODE_USE_BEDROCK'], '1')
        self.assertIsNone(self.store.config(cloud_engines.USAGE_KEY), 'a login check is not a model call')
        self.azure()
        self.assertEqual(adapter.login_status('codex'), {'state': 'token-saved', 'detail': 'cloud:azure-openai'})
        self.assertEqual(len(seen), 1, 'no personal Codex login is asked about')
        self.cloud.save_secret('codex', 'azure-openai', 'AZURE_OPENAI_API_KEY', '')
        self.assertEqual(adapter.login_status('codex')['state'], 'signed-out')

    def test_a_refused_cloud_credential_says_where_to_fix_it(self):
        self.bedrock()
        adapter = self.adapter(lambda argv, **kw: _Done(returncode=1, stdout=json.dumps(
            {'type': 'result', 'is_error': True, 'result': 'Not logged in · Please run /login'})))
        with self.assertRaises(ExecutionError) as caught:
            adapter.execute('claude-code', 'hi', AgentOSMcpTools(_Caps()))
        self.assertEqual(caught.exception.failure_class, 'auth')
        self.assertIn('Amazon Bedrock', str(caught.exception))
        self.assertIn('다른 AI로 자동 전환하지 않았어요', str(caught.exception))

    def test_the_subscription_route_is_unchanged(self):
        seen = []
        adapter = self.adapter(lambda argv, **kw: seen.append((argv, kw['env'])) or answer(argv))
        adapter.execute('claude-code', 'hi', AgentOSMcpTools(_Caps()))
        argv, env = seen[0]
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], OAUTH)
        self.assertEqual(set(env), {'HOME', 'PATH', 'LANG', 'PYTHONPATH', 'CLAUDE_CODE_OAUTH_TOKEN'})
        self.assertNotIn('--tools', argv)
        self.assertEqual(self.store.config(cloud_engines.USAGE_KEY), None, 'nothing counted off the cloud')


class JudgmentCalls(StoreCase):
    def engine(self, engine_id, runner, **kwargs):
        profile = Path(self.tmp.name) / 'codex-home'
        profile.mkdir(exist_ok=True)
        execution = BoundedExecutionAdapter(finder=lambda name: '/fixture/bin/' + name, runner=runner,
                                            runtime_root=Path(self.tmp.name) / 'runs', codex_home=profile,
                                            credentials=lambda engine: OAUTH if engine == 'claude-code' else '',
                                            cloud=self.cloud)
        return SubscriptionCliDecisionEngine(execution, engine_id, audit=[].append, **kwargs)

    @staticmethod
    def runner(calls):
        def run(argv, **kwargs):
            calls.append((argv, kwargs['env']))
            if argv[0].endswith('claude'):
                return _Done(stdout=json.dumps({'type': 'result', 'structured_output': {'answer': True, 'confidence': 0.9}}))
            return _Done(stdout=json.dumps({'item': {'type': 'agent_message', 'text': json.dumps({'answer': True, 'confidence': 0.9})}}))
        return run

    def test_judgment_runs_on_the_cloud_profile(self):
        self.bedrock()
        calls = []
        decision = self.engine('claude-code', self.runner(calls)).judge(DecisionContext('x', {'owner_message': 'a'}), 'p?')
        self.assertEqual(decision.outcome, OUTCOME_DECIDED)
        argv, env = calls[0]
        self.assertEqual(env['CLAUDE_CODE_USE_BEDROCK'], '1')
        self.assertEqual(env['CLAUDE_CODE_SUBPROCESS_ENV_SCRUB'], '1')
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', env)
        self.assertNotIn(BEDROCK_TOKEN, json.dumps(argv))
        self.assertEqual(argv[argv.index('--tools') + 1], '')
        self.assertEqual(self.cloud.used_today('claude-code', 'bedrock'), 1, 'a judgment counts against the cap')

    def test_codex_judgment_gets_the_provider_and_the_exclusion(self):
        from personal_agent.bounded_execution import strict_allowed_features
        self.azure()
        calls = []
        engine = self.engine('codex', self.runner(calls), codex_disabled_features=['shell_tool', 'apps'])
        self.assertNotIn('shell_tool', strict_allowed_features())
        engine.judge(DecisionContext('x', {'owner_message': 'a'}), 'p?')
        argv, env = calls[0]
        self.assertIn('model_provider="azure"', argv)
        self.assertIn('shell_environment_policy.exclude=["AZURE_OPENAI_API_KEY"]', argv)
        self.assertIn('Purpose: x', argv[-1], 'the prompt stays the last argument')
        self.assertNotIn(AZURE_KEY, json.dumps(argv))
        self.assertEqual(env['AZURE_OPENAI_API_KEY'], AZURE_KEY)

    def test_the_launch_snapshot_must_be_the_qualified_one(self):
        """#211 review: the exact snapshot that launches is compared with the route's qualified identity."""
        self.bedrock()
        calls = []
        stale = self.engine('claude-code', self.runner(calls), expected_cloud='subscription')
        decision = stale.judge(DecisionContext('x', {'owner_message': 'a'}), 'p?')
        self.assertEqual((decision.outcome, stale.last_failure), (OUTCOME_UNAVAILABLE, 'requalification-needed'))
        self.assertEqual(calls, [], 'no call to a destination the route was not qualified on')
        bound = self.engine('claude-code', self.runner(calls), expected_cloud=self.cloud.identity('claude-code'))
        self.assertEqual(bound.judge(DecisionContext('x', {'owner_message': 'a'}), 'p?').outcome, OUTCOME_DECIDED)

    def test_a_spent_cap_makes_the_judgment_unavailable_without_a_call(self):
        self.bedrock(cap=1)
        calls = []
        engine = self.engine('claude-code', self.runner(calls))
        engine.judge(DecisionContext('x', {'owner_message': 'a'}), 'p?')
        decision = engine.judge(DecisionContext('x', {'owner_message': 'b'}), 'p?')
        self.assertEqual(decision.outcome, OUTCOME_UNAVAILABLE)
        self.assertEqual(engine.last_failure, BUDGET_EXCEEDED)
        self.assertEqual(len(calls), 1)

    def test_an_unfinished_profile_makes_the_judgment_unavailable(self):
        self.cloud.save_profile('claude-code', 'foundry', {'ANTHROPIC_FOUNDRY_RESOURCE': 'corp-ai'}, 5)
        calls = []
        engine = self.engine('claude-code', self.runner(calls))
        decision = engine.judge(DecisionContext('x', {'owner_message': 'a'}), 'p?')
        self.assertEqual((decision.outcome, engine.last_failure), (OUTCOME_UNAVAILABLE, NOT_CONFIGURED))
        self.assertEqual(calls, [], 'never on the subscription instead')


class CompatibleGateway(unittest.TestCase):
    """#211 acceptance: the existing ``compatible`` API route against an OpenAI-compatible company gateway.

    A company may put Bedrock or Azure behind a gateway such as LiteLLM, which
    serves ``POST /v1/chat/completions`` with a Bearer key.  Checked once
    against a local fixture server over real HTTP (the repository's own
    ``request_json`` transport): no provider is contacted, so this shows the
    route's request shape, not any gateway's behaviour.
    """

    def test_a_tool_turn_reaches_the_gateway_shape(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from personal_agent.providers import ModelAdapter
        seen = []

        class Gateway(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append((self.path, self.headers.get('Authorization'), body))
                reply = json.dumps({'model': 'bedrock/us.anthropic.claude-sonnet-fixture', 'choices': [{'message': {
                    'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {
                        'name': 'weather', 'arguments': '{"place": "Seoul"}'}}]}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        config = {'provider': 'compatible', 'endpoint': f'http://127.0.0.1:{server.server_port}/v1',
                  'model': 'bedrock-claude-sonnet'}
        tools = [{'type': 'function', 'function': {'name': 'weather', 'description': 'w',
                                                   'parameters': {'type': 'object', 'properties': {}}}}]
        message, model = ModelAdapter().tool_turn(config, 'sk-gateway-fixture-0001', [{'role': 'user', 'content': 'hi'}], tools)
        path, auth, body = seen[0]
        self.assertEqual((path, auth), ('/v1/chat/completions', 'Bearer sk-gateway-fixture-0001'))
        self.assertEqual((body['model'], body['tools'][0]['function']['name']), ('bedrock-claude-sonnet', 'weather'))
        self.assertEqual(message['tool_calls'][0]['function']['name'], 'weather')
        self.assertEqual(model, 'bedrock/us.anthropic.claude-sonnet-fixture', 'the gateway-reported model is what is recorded')


class _Engine:
    """A fake CLI adapter recording what each Work launch received."""

    def __init__(self, fail=None):
        self.fail, self.launches = fail, []

    def login_status(self, engine_id, binary=None):
        return {'state': 'signed-in'}

    def execute(self, engine, prompt, tools, **kwargs):
        self.launches.append((engine, getattr(tools, 'cloud_profile', 'unset'), getattr(tools, 'native_search', None)))
        if self.fail:
            raise self.fail
        return ExecutionResult('answer', engine, 0)


class ServiceWork(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = QuickStore(Path(self.tmp.name) / 'state')

    def service(self, engine, **kwargs):
        service = AgentService(self.store, subscription_engines=SubscriptionEngines(finder=lambda c: '/runtime/' + c, clock=lambda: 1),
                               execution_adapter=engine, **kwargs)
        service.connect_subscription_engine({'engine': 'claude-code', 'officially_authenticated': True})
        return service

    def configure(self, service, cap=5):
        service.save_cloud_profile({'engine': 'claude-code', 'provider': 'bedrock',
                                    'settings': {'AWS_REGION': 'us-east-1'}, 'daily_calls': cap})
        return service.save_cloud_secret({'engine': 'claude-code', 'provider': 'bedrock',
                                          'name': 'AWS_BEARER_TOKEN_BEDROCK', 'value': BEDROCK_TOKEN})

    def test_a_work_launches_with_its_profile_and_without_native_search(self):
        engine = _Engine()
        service = self.service(engine)
        status = self.configure(service)
        self.assertNotIn(BEDROCK_TOKEN, json.dumps(status))
        self.assertNotIn(BEDROCK_TOKEN, json.dumps(service.settings(), ensure_ascii=False))
        self.store.enqueue('hello', 'k1')
        self.assertTrue(service.run_one())
        _engine, launch, native = engine.launches[0]
        self.assertEqual((launch.provider, native), ('bedrock', False))
        route = [row for row in service.main_ai.status()['routes'] if row['id'] == 'claude-code'][0]
        self.assertEqual(route['destination'], 'Amazon Bedrock (us-east-1)')

    def test_a_spent_cap_stops_the_work_with_its_reason_and_no_redelegation(self):
        engine = _Engine(fail=ExecutionError(cloud_engines.msg('cloud_engines.budget_exceeded', provider='Amazon Bedrock', cap=5),
                                             failure_class=BUDGET_EXCEEDED, meta={'launched': False}))
        service = self.service(engine)
        self.configure(service)
        self.store.enqueue('hello', 'k1')
        self.assertTrue(service.run_one())
        self.assertEqual(len(engine.launches), 1, 'no other worker or route was tried')
        with self.store.db() as db:
            job = db.execute('SELECT status,error FROM jobs ORDER BY id DESC LIMIT 1').fetchone()
            reply = db.execute("SELECT content FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(job['status'], 'failed')
        self.assertIn('호출 한도', reply['content'])
        self.assertIn('다른 AI로 자동 전환하지 않았어요', reply['content'])

    def test_a_refused_cloud_credential_stops_the_work_and_is_not_redelegated(self):
        meta = {'launched': True, 'cloud_provider': 'bedrock', 'cloud_destination': 'Amazon Bedrock (us-east-1)'}
        engine = _Engine(fail=ExecutionError(cloud_engines.msg('cloud_engines.auth_failed', provider='Amazon Bedrock'),
                                             failure_class='auth', meta=meta))
        service = self.service(engine)
        self.configure(service)
        redelegations, logins = [], []
        service.orchestration_step = lambda *args, **kwargs: redelegations.append(args)
        service._remember_engine_login = lambda *args: logins.append(args)
        self.store.enqueue('hello', 'k1')
        self.assertTrue(service.run_one())
        self.assertEqual(redelegations, [], 'a refused cloud credential is never handed to another worker')
        self.assertEqual(logins, [], 'the CLI login is not marked signed out by a cloud refusal')
        self.assertEqual(len(engine.launches), 1)
        with self.store.db() as db:
            job = db.execute('SELECT status FROM jobs ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual(job['status'], 'failed')

    def test_a_spent_cap_is_refused_before_the_turn_is_recorded_as_sent(self):
        """#211 review: no sent envelope or egress record for a launch the cap refused."""
        engine = _Engine()
        service = self.service(engine)
        self.configure(service, cap=1)
        service.cloud_engines.admit(service.cloud_engines.launch('claude-code'))
        job = self.store.enqueue('hello', 'k1')
        self.assertTrue(service.run_one())
        self.assertEqual(engine.launches, [], 'nothing launched')
        record = self.store.turn_provenance(job) or {}
        self.assertNotIn('prompt_envelope', record)
        self.assertNotEqual(record.get('status'), 'sent')
        self.assertEqual(service.cloud_engines.used_today('claude-code', 'bedrock'), 1, 'not counted twice')

    def test_an_admitted_work_turn_counts_once(self):
        engine = _Engine()
        service = self.service(engine)
        self.configure(service, cap=5)
        self.store.enqueue('hello', 'k1')
        self.assertTrue(service.run_one())
        self.assertEqual(len(engine.launches), 1)
        self.assertEqual(service.cloud_engines.used_today('claude-code', 'bedrock'), 1)

    def test_an_unfinished_profile_stops_the_work_before_any_launch(self):
        engine = _Engine()
        service = self.service(engine)
        service.save_cloud_profile({'engine': 'claude-code', 'provider': 'bedrock', 'settings': {'AWS_REGION': 'us-east-1'},
                                    'daily_calls': 5})
        self.store.enqueue('hello', 'k1')
        self.assertTrue(service.run_one())
        self.assertEqual(engine.launches, [])
        with self.store.db() as db:
            reply = db.execute("SELECT content FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn('AWS_BEARER_TOKEN_BEDROCK', reply['content'])

    def test_the_isolated_deployment_refuses_a_cloud_profile(self):
        engine = _Engine()
        service = self.service(engine)
        self.configure(service)
        run = type('Run', (), {})()
        with self.assertRaises(ValueError) as caught:
            service.work_cloud_profile(run, 'claude-code', isolated=True)
        self.assertIn('격리 런타임', str(caught.exception))

    def test_a_codex_cloud_profile_can_be_selected_without_a_personal_login(self):
        tmp = Path(self.tmp.name)
        (tmp / 'codex-home').mkdir()
        service = AgentService(self.store, subscription_engines=SubscriptionEngines(finder=lambda c: '/runtime/' + c, clock=lambda: 1),
                               execution_adapter=BoundedExecutionAdapter(
                                   finder=lambda name: '/runtime/' + name, runtime_root=tmp / 'turns', codex_home=tmp / 'codex-home',
                                   runner=lambda argv, **kw: _Done(returncode=1, stdout='Not logged in'),
                                   cloud=CloudEngines(self.store)))
        service.execution_adapter.cloud = service.cloud_engines
        with self.assertRaises(ValueError):
            service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        service.save_cloud_profile({'engine': 'codex', 'provider': 'azure-openai', 'daily_calls': 3,
                                    'settings': {'base_url': 'https://corp.openai.azure.com/openai/v1'}})
        service.save_cloud_secret({'engine': 'codex', 'provider': 'azure-openai', 'name': 'AZURE_OPENAI_API_KEY', 'value': AZURE_KEY})
        self.assertEqual(service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})['selected'], 'codex')

    def test_the_subscription_work_is_pinned_as_none(self):
        engine = _Engine()
        service = self.service(engine)
        self.store.enqueue('hello', 'k1')
        self.assertTrue(service.run_one())
        self.assertIsNone(engine.launches[0][1])

    def test_the_conversation_cannot_change_a_cloud_profile(self):
        service = self.service(_Engine())
        for setting in ('cloud', 'provider', 'cloud_provider'):
            with self.subTest(setting=setting):
                with self.assertRaises(SettingsError):
                    service.settings_orchestrator.propose('local-owner', 'web', 'main_ai', setting, 'bedrock')
        self.assertEqual(service.cloud_engines.provider('claude-code'), 'subscription')

    def test_settings_endpoints_take_cloud_credentials_on_this_computer_only(self):
        import threading
        from http.cookiejar import CookieJar
        from http.server import ThreadingHTTPServer
        from urllib.error import HTTPError
        from urllib.request import HTTPCookieProcessor, Request, build_opener
        from personal_agent.quickstart import make_handler
        service = self.service(_Engine())
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        url = 'http://127.0.0.1:' + str(server.server_port)

        def post(path, body, headers=None):
            req = Request(url + path, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', **(headers or {})})
            with client.open(req, timeout=5) as response:
                return json.load(response)
        secret = {'engine': 'claude-code', 'provider': 'bedrock', 'name': 'AWS_BEARER_TOKEN_BEDROCK', 'value': BEDROCK_TOKEN}
        with self.assertRaises(HTTPError) as error:
            post('/api/cloud-engines/credential', secret)
        self.assertEqual(error.exception.code, 401, 'a session is required')
        post('/api/claim', {'password': 'long-password-test'})
        for path, body in (('/api/cloud-engines/credential', secret),
                           ('/api/cloud-engines/profile', {'engine': 'claude-code', 'provider': 'bedrock', 'daily_calls': 3,
                                                           'settings': {'AWS_REGION': 'us-east-1'}})):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as error:
                    post(path, body, {'X-Forwarded-For': '203.0.113.9'})
                self.assertEqual(error.exception.code, 400)
                self.assertIn('이 컴퓨터', json.loads(error.exception.read())['error'])
        self.assertEqual(self.store.secret_items(cloud_engines.SECRET_PREFIX), {}, 'a relayed request stored nothing')
        status = post('/api/cloud-engines/credential', secret)
        self.assertNotIn(BEDROCK_TOKEN, json.dumps(status))
        self.assertEqual(self.store.secret(secret_slot('claude-code', 'bedrock', 'AWS_BEARER_TOKEN_BEDROCK')), BEDROCK_TOKEN)

    def test_the_secret_slot_is_the_one_redaction_reads(self):
        service = self.service(_Engine())
        self.configure(service)
        self.assertEqual(self.store.secret(secret_slot('claude-code', 'bedrock', 'AWS_BEARER_TOKEN_BEDROCK')), BEDROCK_TOKEN)
        self.assertIn(BEDROCK_TOKEN, service.cloud_engines.secret_values())


if __name__ == '__main__':
    unittest.main()
