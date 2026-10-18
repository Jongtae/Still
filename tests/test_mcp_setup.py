"""Official setup boundaries, fake CLI wires; no account or network operation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from personal_agent.mcp_setup import (SDK_VERSION, RemoteMcpSetup, environment, observed_status,
                                      remote_url, sdk_observe, status_result)


def public_dns(host, port, **kwargs):
    return [(2, 1, 6, '', ('8.8.8.8', port))]


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []
        def runner(argv, **kwargs):
            self.calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, 'untrusted-secret', 'untrusted-secret')
        self.setup = RemoteMcpSetup('/owner/bin/claude', self.root, runner=runner, resolver=public_dns)

    def test_registration_is_exact_public_remote_and_does_not_claim_readiness(self):
        self.assertEqual(self.setup.register('claude-code', 'unseen', 'https://unseen.invalid/mcp'), {'state': 'registration-returned', 'configuration': 'unknown'})
        argv, options = self.calls[0]
        self.assertEqual(argv, ['/owner/bin/claude', 'mcp', 'add', '--transport', 'http', '--scope', 'user',
                                'unseen', 'https://unseen.invalid/mcp'])
        self.assertFalse(options['shell'])
        self.assertTrue(options['start_new_session'])
        self.assertEqual(options['timeout'], 15)
        self.assertFalse(Path(options['cwd']).exists())
        self.assertNotEqual(options['cwd'], str(self.root))
        self.assertNotIn('CLAUDE_CONFIG_DIR', options['env'], 'use the same profile as owner_mcp.claude_definitions')

    def test_never_overwrites_existing_or_malformed_configuration(self):
        for config in ({'mcpServers': {'unseen': None}}, {'mcpServers': []}, [], 'bad json'):
            with self.subTest(config=config):
                (self.root / '.claude.json').write_text(config if isinstance(config, str) else json.dumps(config))
                self.assertIn(self.setup.register('claude-code', 'unseen', 'https://unseen.invalid/mcp')['state'],
                              ('already-present', 'unknown'))
        self.assertFalse(self.calls)

    def test_explicit_cli_path_is_required_no_bundled_fallback(self):
        for binary in ('', None, 'claude'):
            with self.subTest(binary=binary), self.assertRaises(ValueError):
                RemoteMcpSetup(binary, self.root)

    def test_unsupported_engine_never_starts(self):
        self.assertEqual(self.setup.register('codex', 'unseen', 'https://unseen.invalid')['state'], 'unsupported')
        self.assertEqual(self.setup.login('codex', 'unseen', 'https://unseen.invalid')['state'], 'unsupported')
        self.assertEqual(self.setup.observe('codex', 'unseen', 'https://unseen.invalid')['state'], 'unsupported')
        self.assertFalse(self.calls)

    def test_credentials_templates_and_arguments_are_not_public_remote_definitions(self):
        for value in ('http://host/mcp', 'https://a:b@host/mcp', 'https://host?key=secret',
                      'https://host/#token', 'https://host/{key}', 'https://host\\evil/mcp',
                      'https://host:bad/mcp', 'https://host/mcp\n--dangerous', '--command'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.setup.register('claude-code', 'unseen', value)
        for name in ('agentos', 'claude_ai_alias', '--option', 'two words', 'sk-abcdefghijklmno'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.setup.register('claude-code', name, 'https://unseen.invalid/mcp')
        self.assertFalse(self.calls)

    def test_credential_shaped_and_known_secret_url_paths_never_dispatch(self):
        from urllib.parse import quote
        secret = 'sk-abcdefghijklmno'
        encoded = ''.join('%' + format(ord(c), '02X') for c in secret)
        for path in (secret, encoded, quote(encoded), 'github_pat_abcdefghijklmno',
                     'api_key=abcdef', 'api_key%3Dabcdef'):
            url = 'https://unseen.invalid/mcp/' + path
            for operation in (self.setup.register, self.setup.login, self.setup.observe):
                with self.subTest(path=path, operation=operation), self.assertRaises(ValueError):
                    operation('claude-code', 'unseen', url)
        self.setup.redact = lambda value: value.replace('opaque-private-value', '[redacted]')
        for path in ('opaque-private-value', 'opaque%2Dprivate%2Dvalue'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.setup.observe('claude-code', 'unseen', 'https://unseen.invalid/' + path)
        with self.assertRaises(ValueError):
            self.setup.register('claude-code', 'opaque-private-value', 'https://unseen.invalid')
        self.assertFalse(self.calls)

    def test_parent_credentials_and_custom_profile_are_not_inherited(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'private', 'CLAUDE_CONFIG_DIR': '/another/account',
                                         'CLAUDE_CODE_OAUTH_TOKEN': 'private', 'TRACEPARENT': 'private',
                                         'USER': 'fixture-owner', 'LOGNAME': 'fixture-owner'}):
            env = environment(self.root)
        self.assertEqual(set(env), {'HOME', 'PATH', 'LANG', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', 'USER', 'LOGNAME'})
        self.assertEqual((env['USER'], env['LOGNAME']), ('fixture-owner', 'fixture-owner'))
        self.assertNotIn('private', str(env))

    def test_login_never_returns_raw_output_or_claims_authentication(self):
        (self.root / '.claude.json').write_text(json.dumps({'mcpServers': {'unseen': {'type': 'http', 'url': 'https://unseen.invalid'}}}))
        self.assertEqual(self.setup.login('claude-code', 'unseen', 'https://unseen.invalid'),
                         {'state': 'login-returned', 'authentication': 'unknown'})
        self.assertEqual(self.calls[0][0], ['/owner/bin/claude', 'mcp', 'login', 'unseen'])
        self.assertEqual(self.calls[0][1]['timeout'], 180)

    def test_login_cannot_use_an_unrelated_or_changed_definition(self):
        for definition in (None, {'type': 'http', 'url': 'https://other.invalid'}, {'command': 'execute'},
                           {'type': 'http', 'url': 'https://unseen.invalid', 'headers': {'Authorization': 'private'}}):
            with self.subTest(definition=definition):
                (self.root / '.claude.json').write_text(json.dumps({'mcpServers': {'unseen': definition}}))
                self.assertEqual(self.setup.login('claude-code', 'unseen', 'https://unseen.invalid')['state'], 'definition-changed')
        self.assertFalse(self.calls)

    def test_timeout_is_unknown_not_registration_failure_or_readiness(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired('hidden', 1, output='secret', stderr='secret')
        self.setup.runner = timeout
        (self.root / '.claude.json').write_text(json.dumps({'mcpServers': {'unseen': {'type': 'http', 'url': 'https://unseen.invalid'}}}))
        self.assertEqual(self.setup.register('claude-code', 'new', 'https://unseen.invalid')['state'], 'unknown')
        self.assertEqual(self.setup.login('claude-code', 'unseen', 'https://unseen.invalid')['state'], 'unknown')
        self.assertEqual(self.setup.observe('claude-code', 'unseen', 'https://unseen.invalid')['state'], 'unknown')

    def test_already_cancelled_setup_never_dispatches_a_fast_real_child(self):
        import shlex
        marker = self.root / 'started'
        binary = self.root / 'fast-cli'
        binary.write_text('#!/bin/sh\n: > ' + shlex.quote(str(marker)) + '\n')
        binary.chmod(0o700)
        (self.root / '.claude.json').write_text(json.dumps({'mcpServers': {
            'existing': {'type': 'http', 'url': 'https://unseen.invalid'}}}))
        for stop in (lambda: 'owner-stopped', lambda: (_ for _ in ()).throw(OSError('private'))):
            calls = []
            def interrupted():
                calls.append(True)
                return stop()
            setup = RemoteMcpSetup(binary, self.root, interrupted=interrupted)
            actions = (lambda: setup.register('claude-code', 'new', 'https://unseen.invalid'),
                       lambda: setup.login('claude-code', 'existing', 'https://unseen.invalid'),
                       lambda: setup.observe('claude-code', 'existing', 'https://unseen.invalid'))
            for action in actions:
                with self.subTest(stop=stop, action=action):
                    self.assertEqual(action()['state'], 'cancelled')
                    self.assertFalse(marker.exists(), 'the fast child must never start after Stop')
            self.assertEqual(len(calls), 3)

    def test_helper_result_is_revalidated(self):
        for payload in ({'state': 'connected', 'tools': ['safe'], 'secret': 'private'},
                        {'state': 'connected', 'tools': ['bad\nname']},
                        {'state': 'connected', 'tools': ['sk-abcdefghijklmno']},
                        {'state': 'connected', 'tools': None}, {'state': 'connected', 'tools': 'safe'}, {'state': 'invented'}, ['bad']):
            with self.subTest(payload=payload):
                self.setup.runner = lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(payload), 'secret')
                result = self.setup.observe('claude-code', 'unseen', 'https://unseen.invalid')
                self.assertNotIn('private', str(result))
                self.assertEqual(result['state'], 'connected' if isinstance(payload, dict) and payload.get('tools') == ['safe'] else 'unknown')


class Status(unittest.TestCase):
    def test_ready_is_not_verified_account_scope_schema_or_authority(self):
        payload = {'mcpServers': [{'name': 'unseen', 'status': 'connected', 'scope': 'user',
                    'config': {'headers': {'Authorization': 'secret'}}, 'error': 'secret',
                    'tools': [{'name': 'inspect', 'description': 'secret', 'annotations': {'readOnly': True}}]}]}
        result = observed_status(payload, 'unseen')
        self.assertEqual(result, status_result('connected', ['inspect']))
        self.assertNotIn('secret', str(result))
        self.assertEqual(result['granted_scopes'], 'unknown')

    def test_wrong_server_extra_server_incomplete_and_duplicate_tools_fail_closed(self):
        for rows in ([], [{'name': 'other', 'status': 'connected', 'tools': []}],
                     [{'name': 'unseen', 'status': 'connected'}],
                     [{'name': 'unseen', 'status': 'connected', 'tools': [{'name': 'read'}] * 2}],
                     [{'name': 'unseen', 'status': 'connected', 'tools': []}] * 2):
            with self.subTest(rows=rows):
                self.assertEqual(observed_status({'mcpServers': rows}, 'unseen')['state'], 'unknown')

    def test_auth_failure_pending_disabled_are_not_ready(self):
        for state in ('needs-auth', 'failed', 'pending', 'disabled'):
            with self.subTest(state=state):
                self.assertEqual(observed_status({'mcpServers': [{'name': 'unseen', 'status': state,
                                'error': 'secret'}]}, 'unseen'), status_result(state))

    def test_sdk_options_are_selective_and_no_model_prompt_or_tool_call_is_possible(self):
        import anyio
        seen = []
        class Client:
            def __init__(self, options): seen.append(options)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def get_mcp_status(self):
                return {'mcpServers': [{'name': 'unseen', 'status': 'connected', 'tools': [{'name': 'inspect'}]}]}
            async def query(self, *args): raise AssertionError('a readiness check must never call a model')
        sdk = types.SimpleNamespace(ClaudeAgentOptions=lambda **k: k, ClaudeSDKClient=Client,
                                    PermissionResultDeny=lambda **k: {'behavior': 'deny', **k})
        async def check():
            result = await sdk_observe('/explicit/claude', 'unseen', 'https://unseen.invalid', sdk=sdk)
            self.assertEqual(result['tools'], ['inspect'])
            self.assertEqual((await seen[0]['can_use_tool']('anything', {}, None))['behavior'], 'deny')
        anyio.run(check)
        options = seen[0]
        self.assertEqual(options['setting_sources'], [])
        self.assertTrue(options['strict_mcp_config'])
        self.assertEqual(options['mcp_servers'], {'unseen': {'type': 'http', 'url': 'https://unseen.invalid'}})
        self.assertEqual(options['tools'], [])
        self.assertEqual(options['cli_path'], '/explicit/claude')
        self.assertIsNone(options['stderr']('secret'))
        self.assertFalse(Path(options['cwd']).exists())

    def test_real_sdk_process_uses_only_control_requests_and_sanitizes_status(self):
        try:
            import claude_agent_sdk
        except ImportError:
            self.skipTest('host optional SDK is not installed')
        if claude_agent_sdk.__version__ != SDK_VERSION:
            # sdk_observe refuses an unpinned SDK by design; that is environment drift, not this path.
            self.skipTest(f'host SDK {claude_agent_sdk.__version__} is not the pinned {SDK_VERSION}; run uv sync')
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root) / 'fixture-cli'
            audit = Path(root) / 'audit.jsonl'
            binary.write_text('#!' + sys._base_executable + '\n' + '''
import json,sys,os
from pathlib import Path
if '--version' in sys.argv or '-v' in sys.argv:
    print('2.1.280 (fixture)'); sys.exit(0)
audit=Path(__file__).with_name('audit.jsonl')
with audit.open('a') as f: f.write(json.dumps({'argv':sys.argv,'env':dict(os.environ)})+'\\n')
for line in sys.stdin:
    req=json.loads(line)
    with audit.open('a') as f: f.write(json.dumps(req)+'\\n')
    if req.get('type') != 'control_request': sys.exit(9)
    subtype=req['request']['subtype']
    response={}
    if subtype=='mcp_status':
        response={'mcpServers':[{'name':'unseen','status':'connected','scope':'user','config':{'secret':'private'},'tools':[{'name':'inspect','description':'private'}]}]}
    elif subtype!='initialize': sys.exit(10)
    print(json.dumps({'type':'control_response','response':{'subtype':'success','request_id':req['request_id'],'response':response}}),flush=True)
''')
            binary.chmod(0o700)
            with mock.patch.dict(os.environ, {'HOST_SECRET': 'private'}):
                result = RemoteMcpSetup(binary, root, resolver=public_dns).observe('claude-code', 'unseen', 'https://unseen.invalid')
            self.assertEqual(result, status_result('connected', ['inspect']))
            rows = [json.loads(line) for line in audit.read_text().splitlines()]
            self.assertNotIn('HOST_SECRET', rows[0]['env'])
            self.assertIn('--strict-mcp-config', rows[0]['argv'])
            self.assertEqual([row['request']['subtype'] for row in rows[1:]], ['initialize', 'mcp_status'])


class SetupDestinationPolicy(unittest.TestCase):
    setUp = Setup.setUp

    def test_private_or_unavailable_dns_never_dispatches_setup_commands(self):
        (self.root / '.claude.json').write_text(json.dumps({'mcpServers': {
            'unseen': {'type': 'http', 'url': 'https://unseen.invalid'}}}))
        for answer in ('127.0.0.1', '192.168.1.5', '169.254.169.254', '::1', None, 'timeout'):
            def resolver(host, port, **kwargs):
                self.assertLessEqual(kwargs['timeout'], 12)
                if answer is None:
                    raise OSError('private resolver error')
                if answer == 'timeout':
                    raise TimeoutError('private resolver timeout')
                return [(2, 1, 6, '', (answer, port))]
            self.setup.destinations.resolver = resolver
            for action in (lambda: self.setup.register('claude-code', 'new', 'https://unseen.invalid'),
                           lambda: self.setup.login('claude-code', 'unseen', 'https://unseen.invalid'),
                           lambda: self.setup.observe('claude-code', 'unseen', 'https://unseen.invalid')):
                with self.subTest(answer=answer):
                    self.assertEqual(action()['state'], 'unknown')
            self.assertFalse(self.calls)

    def test_stop_during_resolution_is_checked_before_fast_command(self):
        stopped = [False]
        def resolve(host, port, **kwargs):
            stopped[0] = True
            return public_dns(host, port, **kwargs)
        self.setup.destinations.resolver = resolve
        self.setup.interrupted = lambda: 'stopped' if stopped[0] else None
        self.assertEqual(self.setup.register('claude-code', 'new', 'https://unseen.invalid')['state'], 'cancelled')
        self.assertFalse(self.calls)

    def test_changed_selected_definition_after_dns_cannot_start_login(self):
        config = self.root / '.claude.json'
        expected = {'type': 'http', 'url': 'https://unseen.invalid'}
        for replacement in ({'type': 'http', 'url': 'https://different.invalid'}, None,
                            {**expected, 'headers': {'Authorization': 'private'}}):
            config.write_text(json.dumps({'mcpServers': {'unseen': expected}}))
            def resolve(host, port, **kwargs):
                config.write_text(json.dumps({'mcpServers': {'unseen': replacement}}))
                return public_dns(host, port, **kwargs)
            self.setup.destinations.resolver = resolve
            with self.subTest(replacement=replacement):
                self.assertEqual(self.setup.login('claude-code', 'unseen', expected['url'])['state'], 'unknown')
                self.assertFalse(self.calls)

    def test_definition_appearing_during_dns_is_not_overwritten(self):
        config = self.root / '.claude.json'
        def resolve(host, port, **kwargs):
            config.write_text(json.dumps({'mcpServers': {'unseen': {'type': 'http', 'url': 'https://different.invalid'}}}))
            return public_dns(host, port, **kwargs)
        self.setup.destinations.resolver = resolve
        self.assertEqual(self.setup.register('claude-code', 'unseen', 'https://unseen.invalid')['state'], 'unknown')
        self.assertFalse(self.calls)
        self.assertIn('different.invalid', config.read_text())
