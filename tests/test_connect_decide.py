"""CONNECT-DECIDE-01 (#1296): one Still decision point for every AI-side tool call.

Model-free: scripted CLIs, the real service relay and the real hook process;
nothing reaches a model or an external service.
"""
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import codex_permission_hook, owner_mcp, trust_record
from personal_agent.bounded_execution import BoundedExecutionAdapter, cli_metadata
from personal_agent.quickstart_store import QuickStore

SAVE = 'mcp__notes__save_note'
LIST = 'mcp__notes__list_notes'


class ToolNameTests(unittest.TestCase):
    def test_names_split_into_kind_service_and_operation(self):
        self.assertEqual(owner_mcp.tool_server('mcp__claude_ai_Google_Drive__search_files'),
                         ('ai-connection', 'Google Drive', 'search_files'))
        self.assertEqual(owner_mcp.tool_server(SAVE), ('owner-mcp', 'notes', 'save_note'))
        self.assertEqual(owner_mcp.tool_server('mcp__notes__get__nested'), ('owner-mcp', 'notes', 'get__nested'))
        # Review P1: no owner server can carry a claude.ai connector's tool name.
        self.assertIsNone(owner_mcp.tool_server('mcp__Claude_AI_Google_Drive__search_files'))
        self.assertEqual(owner_mcp.codex_launch({'claude_ai_Google_Drive': {'type': 'stdio', 'command': '/x'}}), ([], {}, []))
        for name in ('mcp__agentos__file_read', 'Bash', 'mcp__', 'mcp__notes', 'mcp____x', 'mcp__claude_ai_', None, 'x' * 300):
            self.assertIsNone(owner_mcp.tool_server(name), name)

    def test_confirmed_servers_are_validated_and_bounded(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = QuickStore(Path(temp.name) / 'state')
        self.assertEqual(owner_mcp.confirmed(store, 'codex'), [], 'nothing is confirmed by default')
        self.assertEqual(owner_mcp.set_confirmed(store, 'codex', ['notes', 'notes', 'files']), ['notes', 'files'])
        for bad in (['agentos'], ['has space'], ['a' * 65], ['claude_ai_Google_Drive'], ['Claude_AI_x']):
            with self.assertRaises(ValueError):
                owner_mcp.set_confirmed(store, 'codex', bad)
        with self.assertRaises(ValueError):
            owner_mcp.set_confirmed(store, 'other', ['notes'])
        store.put(owner_mcp.CONFIRMED_KEY, {'codex': ['notes', 'agentos', 7, 'bad name', 'claude_ai_Google_Drive']})
        self.assertEqual(owner_mcp.confirmed(store, 'codex'), ['notes'], 'a stored value is re-validated')


class CodexLaunchTests(unittest.TestCase):
    def test_secret_values_travel_only_in_the_environment(self):
        args, env, secret = owner_mcp.codex_launch({
            'notes': {'type': 'stdio', 'command': '/usr/bin/notes', 'args': ['serve'], 'env': {'NOTES_KEY': 'sk-secret-1'}},
            'web': {'type': 'streamable_http', 'url': 'https://mcp.example.test/mcp',
                    'http_headers': {'Authorization': 'Bearer sk-secret-2'}, 'bearer_token_env_var': None}})
        flat = ' '.join(args)
        self.assertNotIn('sk-secret', flat, 'no value may appear in an argument')
        self.assertEqual(env['NOTES_KEY'], 'sk-secret-1')
        self.assertIn('Bearer sk-secret-2', env.values())
        self.assertIn('mcp_servers.notes.env_vars=["NOTES_KEY"]', args)
        self.assertTrue(any(a.startswith('mcp_servers.web.env_http_headers={"Authorization": "AGENTOS_MCP_') for a in args))
        self.assertIn('mcp_servers.notes.default_tools_approval_mode="approve"', args)
        self.assertIn(f'shell_environment_policy.exclude={json.dumps(secret)}', args,
                      "the model's shell never inherits the values")
        self.assertEqual(set(secret), set(env))

    def test_unexpressible_or_reserved_definitions_are_skipped_whole(self):
        args, env, _secret = owner_mcp.codex_launch({
            'agentos': {'type': 'stdio', 'command': '/x'},
            'path': {'type': 'stdio', 'command': '/x', 'env': {'PATH': '/evil'}},
            'plain': {'type': 'streamable_http', 'url': 'http://insecure.test/mcp'},
            'odd': {'type': 'sse', 'url': 'https://x.test'},
            'bad name': {'type': 'stdio', 'command': '/x'}})
        self.assertEqual((args, env), ([], {}))

    def test_two_servers_cannot_share_a_name_with_different_values(self):
        args, env, _secret = owner_mcp.codex_launch({
            'a': {'type': 'stdio', 'command': '/a', 'env': {'KEY': 'one'}},
            'b': {'type': 'stdio', 'command': '/b', 'env': {'KEY': 'two'}}})
        self.assertEqual(env, {'KEY': 'one'})
        self.assertFalse(any('mcp_servers.b.' in a for a in args))

    def test_definition_comes_from_the_cli_and_disabled_servers_are_absent(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            enabled = argv[3] == 'notes'
            return subprocess.CompletedProcess(argv, 0, json.dumps({'name': argv[3], 'enabled': enabled,
                                                                    'transport': {'type': 'stdio', 'command': '/n'}}), '')
        self.assertEqual(owner_mcp.codex_definition('/runtime/codex', 'notes', {}, runner=runner),
                         {'type': 'stdio', 'command': '/n'})
        self.assertIsNone(owner_mcp.codex_definition('/runtime/codex', 'off', {}, runner=runner))
        self.assertEqual(calls[0], ['/runtime/codex', 'mcp', 'get', 'notes', '--json'])
        self.assertIsNone(owner_mcp.codex_definition('/runtime/codex', 'x', {}, runner=lambda *a, **k: (_ for _ in ()).throw(OSError())))

    def test_the_hook_needs_the_trust_flag_and_the_command_carries_only_the_relay(self):
        args = owner_mcp.codex_hook_arguments('/relay/dir')
        self.assertEqual(args[0], '--dangerously-bypass-hook-trust',
                         'without it codex-cli 0.153.4 skips a session-flag hook and the call runs')
        self.assertIn('matcher="^mcp__"', args[2])
        self.assertIn('personal_agent.codex_permission_hook', args[2])
        self.assertIn('/relay/dir', args[2])

    def test_the_command_places_owner_arguments_before_the_prompt(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = Path(temp.name) / 'agentos-mcp.json'
        config.write_text(json.dumps({'mcpServers': {'agentos': {'command': 'python3', 'args': ['-m', 'bridge']}}}))
        adapter = BoundedExecutionAdapter(finder=lambda name: '/runtime/' + name)
        argv = adapter.command('codex', '/runtime/codex', 'the prompt', config,
                               owner_mcp_args=('-c', 'mcp_servers.notes.command="/n"'))
        self.assertLess(argv.index('mcp_servers.notes.command="/n"'), argv.index('the prompt'))
        self.assertNotIn('--dangerously-bypass-hook-trust', adapter.command('codex', '/runtime/codex', 'p', config),
                         'without confirmed servers nothing changes')


class HookTests(unittest.TestCase):
    def run_hook(self, call, decision=None, error=None):
        class Relay:
            def __init__(self, directory):
                pass

            def call(self, name, arguments):
                if error:
                    raise error
                self.seen = (name, arguments)
                return decision
        out = io.StringIO()
        with mock.patch.object(codex_permission_hook, 'RelayClient', Relay):
            codex_permission_hook.main(['--relay', '/r'], stdin=io.StringIO(call if isinstance(call, str) else json.dumps(call)), stdout=out)
        return out.getvalue()

    def test_allow_prints_nothing_and_deny_prints_a_reasoned_deny(self):
        call = {'tool_name': SAVE, 'tool_input': {'text': 'x'}}
        self.assertEqual(self.run_hook(call, {'behavior': 'allow', 'updatedInput': {}}), '')
        denied = json.loads(self.run_hook(call, {'behavior': 'deny', 'message': '막았습니다.'}))
        self.assertEqual(denied['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(denied['hookSpecificOutput']['permissionDecisionReason'], '막았습니다.')

    def test_anything_unexpected_denies(self):
        for call, kwargs in ((('not json'), {}), ({'tool_input': {}}, {}),
                             ({'tool_name': SAVE}, {'error': OSError('gone')}),
                             ({'tool_name': SAVE}, {'decision': {'behavior': 'maybe'}}),
                             ({'tool_name': SAVE}, {'decision': None})):
            output = json.loads(self.run_hook(call, **kwargs))
            self.assertEqual(output['hookSpecificOutput']['permissionDecision'], 'deny', call)
            self.assertTrue(output['hookSpecificOutput']['permissionDecisionReason'], 'Codex requires a non-empty reason')

    def test_the_bridge_decides_its_own_tools(self):
        self.assertEqual(self.run_hook({'tool_name': 'mcp__agentos__file_read'}, error=AssertionError('not asked')), '')


class HookFailsClosedTests(unittest.TestCase):
    """#222: codex-cli 0.153.4 runs the call on any hook failure except exit 2 with a stderr
    reason, so every failure of the decision process must end exactly there."""

    def run_main(self, argv=('--relay', '/r'), call=None, relay=None, stdout=None):
        out, err = stdout or io.StringIO(), io.StringIO()
        with mock.patch.object(codex_permission_hook, 'RelayClient', relay or (lambda directory: None)):
            status = codex_permission_hook.main(list(argv), stdin=io.StringIO(json.dumps(call or {'tool_name': SAVE})),
                                                stdout=out, stderr=err)
        return status, out.getvalue(), err.getvalue()

    def assert_blocked(self, status, stderr):
        self.assertEqual(status, codex_permission_hook.BLOCK_EXIT)
        self.assertEqual(stderr.strip(), codex_permission_hook.REFUSED_TEXT)

    def test_a_failure_inside_main_exits_2_with_a_reason(self):
        def broken(directory):
            raise RuntimeError('constructor failed')
        status, out, err = self.run_main(relay=broken)
        self.assertEqual(out, '')
        self.assert_blocked(status, err)
        status, _, err = self.run_main(argv=())
        self.assert_blocked(status, err)  # argparse exits; still the one reason

        class Unwritable(io.StringIO):
            def write(self, text):
                raise BrokenPipeError()

        class Deny:
            def __init__(self, directory):
                pass

            def call(self, name, arguments):
                return {'behavior': 'deny', 'message': 'no'}
        status, _, err = self.run_main(relay=Deny, stdout=Unwritable())
        self.assert_blocked(status, err)

    def test_the_allow_and_deny_answers_are_unchanged(self):
        class Answer:
            behavior = 'allow'

            def __init__(self, directory):
                pass

            def call(self, name, arguments):
                return {'behavior': self.behavior, 'message': 'no'}
        self.assertEqual(self.run_main(relay=Answer), (0, '', ''))
        Answer.behavior = 'deny'
        status, out, err = self.run_main(relay=Answer)
        self.assertEqual((status, err), (0, ''))
        self.assertEqual(json.loads(out)['hookSpecificOutput']['permissionDecision'], 'deny')

    def shell(self, script, shell='/bin/sh'):
        """Run the generated hook command with the decision process replaced by ``script``."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        fake = Path(temp.name) / 'python'
        fake.write_text('#!/bin/sh\n' + script + '\n')
        fake.chmod(0o755)
        with mock.patch.object(owner_mcp.sys, 'executable', str(fake)):
            command = owner_mcp.codex_hook_command('/relay/dir')
        return subprocess.run([shell, '-c', command], input='{}', capture_output=True, text=True, timeout=30)

    def test_every_process_failure_ends_as_exit_2_with_a_reason_in_each_shell(self):
        shells = [shell for shell in ('/bin/sh', '/bin/bash', '/bin/zsh') if Path(shell).exists()]
        failures = {'non-zero exit': 'exit 1', 'uncaught exception status': 'echo Traceback >&2; exit 1',
                    'killed by a signal': 'kill -9 $$', 'exit 2 without a reason': 'exit 2',
                    'interpreter missing': 'exec /nonexistent/python3 "$@"'}
        for shell in shells:
            for name, script in failures.items():
                with self.subTest(shell=shell, failure=name):
                    result = self.shell(script, shell)
                    self.assertEqual(result.returncode, codex_permission_hook.BLOCK_EXIT)
                    self.assertIn(codex_permission_hook.REFUSED_TEXT, result.stderr)
            with self.subTest(shell=shell, failure='none'):
                allowed = self.shell('exit 0', shell)
                self.assertEqual((allowed.returncode, allowed.stdout, allowed.stderr), (0, '', ''), 'an allow still runs')
                denied = self.shell("printf '%s' '{\"hookSpecificOutput\":{}}'", shell)
                self.assertEqual((denied.returncode, denied.stdout), (0, '{"hookSpecificOutput":{}}'))

    def test_the_hook_config_carries_the_command_and_a_timeout_above_the_relay_bound(self):
        import tomllib
        from personal_agent.cli_browser_relay import CALL_SECONDS
        args = owner_mcp.codex_hook_arguments('/relay/dir')
        [group] = tomllib.loads(args[2])['hooks']['PreToolUse']
        [handler] = group['hooks']
        self.assertEqual(group['matcher'], '^mcp__')
        self.assertEqual(handler['command'], owner_mcp.codex_hook_command('/relay/dir'))
        self.assertEqual(handler['timeout'], owner_mcp.CODEX_HOOK_TIMEOUT_SECONDS)
        self.assertGreater(owner_mcp.CODEX_HOOK_TIMEOUT_SECONDS, CALL_SECONDS)


class ServiceDecisionTests(unittest.TestCase):
    def setUp(self):
        from personal_agent.quickstart_service import AgentService
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'state')
        self.service = AgentService(self.store)
        self.job = {'id': self.store.enqueue('notes', 'k1')}
        self.service.set_ai_connections({'enabled': True})
        reviewed = {'Google Drive': frozenset({'search_files'}), 'notes': frozenset({'list_notes'})}
        patcher = mock.patch.object(type(self.service), 'reviewed_connector_reads', staticmethod(lambda: reviewed))
        patcher.start()
        self.addCleanup(patcher.stop)

    def decide(self, tool):
        return self.service.connector_permission(self.job, {'tool_name': tool, 'input': {'text': 'private'}, 'engine': 'codex'})['behavior']

    def events(self, tool):
        with self.store.db() as db:
            return [dict(row) for row in db.execute('SELECT status,detail FROM tool_events WHERE tool=?', (tool,))]

    def test_an_owner_server_is_decided_only_once_confirmed(self):
        self.assertEqual(self.decide(LIST), 'deny', 'an unconfirmed server is refused even for a reviewed read')
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        self.assertEqual(self.decide(LIST), 'allow')
        self.assertEqual(self.decide(SAVE), 'deny', 'an unreviewed operation is not a read')
        self.assertEqual(self.decide('mcp__claude_ai_Google_Drive__search_files'), 'allow', 'claude.ai connectors unchanged')
        self.assertNotIn('private', json.dumps(self.events('connector_permission')), 'the input is never recorded')
        self.assertEqual(json.loads(self.events('connector_permission')[0]['detail'])['evidence']['kind'], 'owner-mcp')

    def test_same_server_name_on_another_engine_has_a_separate_trust_record(self):
        owner_mcp.set_confirmed(self.store, 'claude-code', ['notes'])
        notes = trust_record.connection('codex', 'owner-mcp', 'notes')
        claude_notes = trust_record.connection('claude-code', 'owner-mcp', 'notes')
        trust_record.set_money(self.store, notes, True)
        trust_record.set_money(self.store, claude_notes, True)
        trust_record.set_operation(self.store, notes, 'list_notes', 'read')
        decision = self.service.connector_permission(self.job, {'tool_name': LIST, 'input': {}, 'engine': 'claude-code'})
        self.assertEqual(decision['behavior'], 'deny')
        self.assertIn('승인을 기다립니다', decision['message'])
        self.assertEqual(self.service._browser_request(self.job['id'])['action'], 'connector_call')
        self.assertFalse(trust_record.is_read(self.store, claude_notes, 'list_notes'))

    def test_ai_connections_off_refuses_owner_servers_too(self):
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        self.service.set_ai_connections({'enabled': False})
        self.assertEqual(self.decide(LIST), 'deny')

    def test_a_call_that_ran_without_a_decision_switches_owner_servers_off(self):
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        owner_mcp.set_confirmed(self.store, 'claude-code', ['notes'])
        self.assertEqual(self.decide(LIST), 'allow')
        self.service.record_turn_worker(self.job['id'], {'engine': 'codex'})
        self.service.record_turn_provenance(self.job['id'], connector_reads=[{'tool': LIST, 'status': 'succeeded'}])
        self.assertEqual(self.events('connector_undecided'), [], 'a decided call is fine')
        self.service.record_turn_provenance(self.job['id'], connector_reads=[{'tool': SAVE, 'status': 'succeeded'},
                                                                             {'tool': SAVE, 'status': 'failed'}])
        undecided = self.events('connector_undecided')
        self.assertEqual(len(undecided), 1, 'only a call that ran counts')
        self.assertEqual(json.loads(undecided[0]['detail'])['evidence'], {'engine': 'codex', 'service': 'notes', 'identity': 'notes',
                                                                          'operation': 'save_note', 'kind': 'owner-mcp'})
        self.assertEqual((owner_mcp.confirmed(self.store, 'codex'), owner_mcp.confirmed(self.store, 'claude-code')), ([], []),
                         'fail closed: the owner confirms again')


class StreamTests(unittest.TestCase):
    def test_codex_owner_server_calls_are_recorded_and_the_bridge_is_not(self):
        stream = '\n'.join(json.dumps(record) for record in (
            {'type': 'item.completed', 'item': {'id': 'i1', 'type': 'mcp_tool_call', 'server': 'notes', 'tool': 'list_notes',
                                                'arguments': {'q': 'private'}, 'status': 'completed', 'error': None}},
            {'type': 'item.completed', 'item': {'id': 'i2', 'type': 'mcp_tool_call', 'server': 'notes', 'tool': 'save_note',
                                                'status': 'failed', 'error': {'message': 'x'}}},
            {'type': 'item.completed', 'item': {'id': 'i3', 'type': 'mcp_tool_call', 'server': 'agentos', 'tool': 'file_read',
                                                'status': 'completed'}},
            {'type': 'turn.completed', 'usage': {}}))
        meta = cli_metadata('codex', stream)
        self.assertEqual(meta['connector_reads'], [{'tool': LIST, 'status': 'succeeded'}, {'tool': SAVE, 'status': 'failed'}])
        self.assertNotIn('private', json.dumps(meta['connector_reads']))

    def test_claude_owner_server_calls_are_matched_like_connectors(self):
        stream = '\n'.join(json.dumps(record) for record in (
            {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': LIST, 'input': {}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'x'}]}},
            {'type': 'result', 'subtype': 'success', 'result': 'ok'}))
        self.assertEqual(cli_metadata('claude-code', stream)['connector_reads'], [{'tool': LIST, 'status': 'succeeded'}])


class CodexWireTests(unittest.TestCase):
    """A Codex turn with a confirmed owner server: the real hook process asks the service through the turn's relay."""

    def setUp(self):
        from personal_agent.browser_session import BrowserProfile
        from personal_agent.providers import ModelAdapter
        from personal_agent.quickstart_service import AgentService
        from personal_agent.subscription_engines import SubscriptionEngines
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'codex-home').mkdir()
        self.store = QuickStore(root / 'state')
        self.argv, self.env, self.hook_outputs = None, None, {}

        class Done:
            returncode = 0
            stdout = json.dumps({'type': 'item.completed', 'item': {'id': 'm', 'type': 'agent_message', 'text': 'engine answer'}})

        def runner(argv, **kwargs):
            if argv[1:3] == ['mcp', 'get']:
                return subprocess.CompletedProcess(argv, 0, json.dumps({'name': argv[3], 'enabled': True, 'transport': {
                    'type': 'stdio', 'command': '/usr/bin/notes', 'args': [], 'env': {'NOTES_KEY': 'sk-owner-secret'}}}), '')
            if argv[1:2] != ['exec']:
                return subprocess.CompletedProcess(argv, 0, 'codex-cli 0.153.4\n', '')
            self.argv, self.env = argv, kwargs['env']
            self.config_mode = (Path(kwargs['cwd']) / 'agentos-mcp.json').stat().st_mode & 0o777
            hook = next((a for a in argv if a.startswith('hooks.PreToolUse=')), None)
            if hook:
                import tomllib
                # As Codex does: the TOML handler's command, run through a shell (#222).
                command = tomllib.loads(hook)['hooks']['PreToolUse'][0]['hooks'][0]['command']
                for tool in getattr(self, 'hook_tools', (LIST, SAVE, 'mcp__agentos__file_read')):
                    done = subprocess.run(['/bin/sh', '-c', command], input=json.dumps({'tool_name': tool, 'tool_input': {'q': 1}}),
                                          capture_output=True, text=True, timeout=30, env=kwargs['env'])
                    self.hook_outputs[tool] = done.stdout
            if hasattr(self, 'after_hook'):
                return self.after_hook(argv, kwargs)
            return Done()

        adapter = BoundedExecutionAdapter(finder=lambda name: '/runtime/' + name, runner=runner,
                                          runtime_root=root / 'turns', codex_home=root / 'codex-home')
        self.service = AgentService(self.store, adapter=ModelAdapter(lambda *a: {'choices': [{'message': {'content': 'x'}}]}),
                                    subscription_engines=SubscriptionEngines(finder=lambda _: '/runtime/codex', clock=lambda: 1),
                                    execution_adapter=adapter,
                                    browser_profile=BrowserProfile(root / 'browser', available=lambda: False))
        self.service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        self.service.cli_native_search = lambda *args: (False, 'refused')
        reviewed = {'notes': frozenset({'list_notes'})}
        patcher = mock.patch.object(type(self.service), 'reviewed_connector_reads', staticmethod(lambda: reviewed))
        patcher.start()
        self.addCleanup(patcher.stop)

    def turn(self):
        self.store.enqueue('notes please', f'wire-{id(self)}')
        self.assertTrue(self.service.run_one())

    def test_without_confirmed_servers_the_launch_is_unchanged(self):
        self.service.set_ai_connections({'enabled': True})
        self.turn()
        self.assertNotIn('--dangerously-bypass-hook-trust', self.argv)
        self.assertFalse(any(a.startswith('hooks.') for a in self.argv))

    def test_the_hook_asks_the_service_and_the_secret_stays_out_of_arguments(self):
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        self.turn()
        self.assertIn('--dangerously-bypass-hook-trust', self.argv)
        self.assertNotIn('sk-owner-secret', ' '.join(self.argv))
        self.assertEqual(self.config_mode, 0o600, 'the per-turn MCP configuration is owner-only')
        self.assertEqual(self.env['NOTES_KEY'], 'sk-owner-secret')
        self.assertEqual(self.hook_outputs[LIST], '', 'a reviewed read runs')
        self.assertEqual(json.loads(self.hook_outputs[SAVE])['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(self.hook_outputs['mcp__agentos__file_read'], '')
        with self.store.db() as db:
            decided = [row['status'] for row in db.execute("SELECT status FROM tool_events WHERE tool='connector_permission'")]
        self.assertEqual(decided, ['allowed', 'denied'])

    def test_read_review_resumes_through_fresh_cli_and_relay_to_original_result(self):
        self.read_review_round_trip()

    def test_observed_failed_read_turn_can_resume_after_its_pending_review(self):
        self.read_review_round_trip(fail_after_read=True)

    def test_resumed_original_input_is_not_a_new_owner_confirmation(self):
        self.read_review_round_trip(check_replayed_input=True)

    def test_effect_then_read_review_then_payment_continues_only_remaining_work(self):
        """Actual hook process, relay, stream parser, owner callbacks and worker queue."""
        from personal_agent.cli_browser_relay import RelayClient
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store,'codex',['notes'])
        target=trust_record.connection('codex','owner-mcp','notes')
        trust_record.set_operation(self.store,target,'save_note','mutate')
        trust_record.set_operation(self.store,target,'finish_change','payment')
        payment='mcp__notes__finish_change'
        self.hook_tools=(SAVE,LIST)
        turns=[]
        observed=[]

        def reply(argv,kwargs):
            turns.append(kwargs.get('input') or argv[-1])
            records=[]
            if len(turns)==1:
                self.assertEqual(self.hook_outputs[SAVE],'')
                self.assertEqual(json.loads(self.hook_outputs[LIST])['hookSpecificOutput']['permissionDecision'],'deny')
                config=json.loads((Path(kwargs['cwd'])/'agentos-mcp.json').read_text())
                directory=next(arg.split('=',1)[1] for arg in config['mcpServers']['agentos']['args'] if arg.startswith('--browser-relay='))
                draft=RelayClient(directory).call('settings_change',{'category':'ai_connections','setting':'review',
                    'value':'codex|owner-mcp|notes|list_notes|read'})
                self.assertFalse(draft['applied'])
                operation='save_note';answer='변경했고 결과 조회 권한 확인을 기다립니다.'
            elif len(turns)==2:
                self.assertIn('Observed completed action',turns[-1])
                self.assertIn('save_note',turns[-1])
                self.assertEqual(self.hook_outputs[LIST],'')
                self.assertEqual(json.loads(self.hook_outputs[payment])['hookSpecificOutput']['permissionDecision'],'deny')
                operation='list_notes';answer='변경을 확인했고 남은 결제 승인을 기다립니다.'
            else:
                self.assertIn('remaining obligations',turns[-1])
                self.assertEqual(self.hook_outputs[payment],'')
                operation='finish_change';answer='변경 한 건과 승인된 결제 한 건이 완료됐습니다.'
            observed.append(operation)
            records.extend([
                {'type':'item.completed','item':{'id':f'call-{len(turns)}','type':'mcp_tool_call','server':'notes',
                    'tool':operation,'arguments':{'q':1},'status':'completed'}},
                {'type':'item.completed','item':{'id':'answer','type':'agent_message','text':answer}},
                {'type':'turn.completed'}])
            return subprocess.CompletedProcess(argv,0,'\n'.join(json.dumps(row) for row in records),'')

        self.after_hook=reply
        with mock.patch.object(type(self.service),'reviewed_connector_reads',staticmethod(lambda:{})):
            self.turn()
            job=self.store.jobs()[0]
            self.assertEqual(job['status'],'awaiting_connection',self.store.task_events(job['id']))
            self.service.work_settings_draft(job['id'],{'action':'confirm'})
            self.assertEqual(self.store.job(job['id'])['status'],'queued')
            self.hook_tools=(LIST,payment)
            self.assertTrue(self.service.run_one())
            pending=self.service._browser_request(job['id'])
            self.assertIsNotNone(pending,self.store.job(job['id']))
            approved=self.service.browser_step_decision({'work_id':job['id'],'decision':'approve','digest':pending['offer_digest']})
            self.assertTrue(approved['resumed'],(approved,self.store.job(job['id'])))
            self.hook_tools=(payment,)
            self.assertTrue(self.service.run_one())
        result=self.store.job(job['id'])
        self.assertEqual(result['status'],'succeeded',result)
        self.assertIn('승인된 결제 한 건',result['response'])
        self.assertEqual(observed,['save_note','list_notes','finish_change'])
        self.assertEqual(len(self.store.jobs()),1)
        completed,unknown=self.service.connector_effect_progress(job['id'])
        self.assertEqual(len(completed),2)
        self.assertEqual(unknown,[])

    def read_review_round_trip(self, fail_after_read=False, check_replayed_input=False):
        from personal_agent.cli_browser_relay import RelayClient
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        self.hook_tools = (LIST,)
        if fail_after_read:
            trust_record.set_operation(self.store, trust_record.connection('codex', 'owner-mcp', 'notes'), 'get_status', 'read')
            self.hook_tools = (LIST, 'mcp__notes__get_status')
        turns = []

        def reply(argv, kwargs):
            turns.append(kwargs.get('input'))
            records = []
            if len(turns) == 1:
                self.assertEqual(json.loads(self.hook_outputs[LIST])['hookSpecificOutput']['permissionDecision'], 'deny')
                config = json.loads((Path(kwargs['cwd']) / 'agentos-mcp.json').read_text())
                args = config['mcpServers']['agentos']['args']
                directory = next(arg.split('=', 1)[1] for arg in args if arg.startswith('--browser-relay='))
                draft = RelayClient(directory).call('settings_change', {'category': 'ai_connections', 'setting': 'review',
                            'value': 'codex|owner-mcp|notes|list_notes|read'})
                self.assertFalse(draft['applied'])
                if fail_after_read:
                    from personal_agent.bounded_execution import ExecutionError
                    stream = '\n'.join(json.dumps(row) for row in [
                        {'type': 'item.completed', 'item': {'id': 'read', 'type': 'mcp_tool_call', 'server': 'notes',
                                                          'tool': 'get_status', 'status': 'completed'}},
                        {'type': 'turn.failed'}])
                    raise ExecutionError('The worker stopped after an observed read.', failure_class='process',
                                         meta=cli_metadata('codex', stream))
                answer = '읽기 권한 확인을 기다립니다.'
            else:
                self.assertEqual(self.hook_outputs[LIST], '')
                records.append({'type': 'item.completed', 'item': {'id': 'read', 'type': 'mcp_tool_call',
                                'server': 'notes', 'tool': 'list_notes', 'status': 'completed'}})
                answer = '두 메모의 공통 내용은 금요일 회의 준비입니다.'
            records.extend([{'type': 'item.completed', 'item': {'id': 'answer', 'type': 'agent_message', 'text': answer}},
                            {'type': 'turn.completed'}])
            return subprocess.CompletedProcess(argv, 0, '\n'.join(json.dumps(row) for row in records), '')

        self.after_hook = reply
        with mock.patch.object(type(self.service), 'reviewed_connector_reads', staticmethod(lambda: {})):
            self.turn()
            job = self.store.jobs()[0]
            self.assertEqual(job['status'], 'awaiting_connection', (self.service.settings_orchestrator._drafts(), self.store.task_events(job['id'])))
            self.service.work_settings_draft(job['id'], {'action': 'confirm'})
            self.assertEqual(self.store.job(job['id'])['status'], 'queued')
            if check_replayed_input:
                with self.store.db() as db:
                    db.execute('UPDATE jobs SET owner_typed=1 WHERE id=?', (job['id'],))
                with mock.patch.object(self.service.owner_acts, 'read', side_effect=AssertionError('replayed input is not new owner text')):
                    self.assertTrue(self.service.run_one())
            else:
                self.assertTrue(self.service.run_one())
            result = self.store.job(job['id'])
            self.assertEqual(result['status'], 'succeeded', result)
            self.assertIn('금요일 회의', result['response'])
            self.assertEqual(len(turns), 2)
            self.assertEqual(len(self.store.jobs()), 1, 'confirmation did not create a replacement request')


if __name__ == '__main__':
    unittest.main()
