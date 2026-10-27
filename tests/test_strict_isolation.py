"""AGENCY-ISOLATION-01 (#616, AX-15 / AX-S19): the strict-isolated CLI profile.

Two evidence classes, named separately:

* Unit tests (always run): the declaration, the exact argv, the execute-time
  version refusal, the no-model qualification logic and the owner's choice.
  Injected runners; no CLI, model or provider is contacted.
* Process-level qualification (opt-in, ``AGENTOS_CLI_QUALIFICATION=1``): the
  exact argv ``BoundedExecutionAdapter.execute`` builds drives the real
  ``codex exec`` / ``claude -p`` against a scripted local fake model endpoint
  (loopback only; no account, credential or live provider).  The only argv
  additions are the fake provider's endpoint settings.  A fake store and a
  canary file are created and removed; no owner data is read.  Codex runs
  against a synthetic, populated CODEX_HOME (exec rules, AGENTS.md, skills,
  plugins, hooks, a widening config.toml), never the owner's real one.  The bridge's
  public network is stubbed inside the real bridge process through a
  per-turn ``usercustomize`` (the bridge itself is not replaced), as
  ``BoundedProfileHostInvocation`` does in-process.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
import uuid
from pathlib import Path

from personal_agent.bounded_execution import (
    BASELINE_READABLE_ROOTS,
    BOUNDED_PROFILE,
    CLI_PROFILES,
    CODEX_STRICT_PERMISSIONS,
    CODEX_STRICT_TABLE,
    STRICT_PROFILE,
    TRUSTED_LOCAL_LIMITATION,
    AgentOSMcpTools,
    BoundedExecutionAdapter,
    ExecutionError,
    ExecutionResult,
    StrictIsolatedAgentOSMcpTools,
    parse_cli_version,
    strict_allowed_features,
    profile_actions,
    route_unavailable,
)
from personal_agent.decision import OUTCOME_DECIDED, DecisionContext
from personal_agent.decision_routes import CODEX_INSTRUCTION_FILES_QUALIFIED
from personal_agent.decision_adapters import (CODEX_DECISION_CONFIG, SubscriptionCliDecisionEngine, codex_disable_plan,
                                              parse_codex_features)
from personal_agent.quickstart_store import QuickStore

ROOT = Path(__file__).resolve().parents[1]
CODEX_VERSION = CLI_PROFILES[STRICT_PROFILE]['runtimes']['codex']['tested_versions'][0]
CLAUDE_VERSION = CLI_PROFILES[STRICT_PROFILE]['runtimes']['claude-code']['tested_versions'][0]


class _Done:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class _Caps:
    def __init__(self, root):
        self.store = type('S', (), {'root': Path(root)})()
        self.job_id, self.private_provenance = 'job', set()


def _codex_answer():
    return _Done(stdout=json.dumps({'item': {'type': 'agent_message', 'text': 'answer'}}))


class StrictProfileDeclaration(unittest.TestCase):
    def test_strict_profile_is_separate_and_trusted_local_is_unchanged(self):
        strict, trusted = CLI_PROFILES[STRICT_PROFILE], CLI_PROFILES[BOUNDED_PROFILE]
        self.assertEqual((STRICT_PROFILE, strict['trust']), ('strict-isolated', 'strict-isolated'))
        self.assertEqual(trusted['trust'], 'trusted-local')
        self.assertEqual(trusted['limitation'], TRUSTED_LOCAL_LIMITATION)
        self.assertIn('/tmp', strict['limitation'], 'the residual readable paths are stated')
        self.assertEqual(CLI_PROFILES[STRICT_PROFILE]['runtimes']['codex']['tested_versions'], ('0.153.4',))
        self.assertEqual(CODEX_INSTRUCTION_FILES_QUALIFIED, frozenset({'0.153.4', '0.162.1', '0.164.0'}))
        self.assertEqual(CLI_PROFILES[STRICT_PROFILE]['runtimes']['claude-code']['tested_versions'], ('2.1.280',))

    def test_strict_offers_what_the_bridge_serves(self):
        """#701/#774/#826: the bridge is told its profile; strict is trusted-local minus every service-relayed
        tool and minus the connected-folder documents #826 added to trusted-local only.  #23: strict serves
        public_page_read for this Work's own search results; the owner's approved page list stays off it."""
        from personal_agent.agent_runtime import BROWSER_ACTIONS, HOST_RELAYED_ACTIONS
        owner_files = {'find_files', 'read_file', 'list_roots',
                       # #182: the file workspace's result file is written from the owner store's folders.
                       'save_result',
                       # #961: skills are read from the owner store, so strict never serves them either.
                       'skill_load', 'skill_resource', 'method_gap'}
        self.assertEqual(profile_actions(STRICT_PROFILE),
                         tuple(action for action in profile_actions(BOUNDED_PROFILE)
                               if action not in HOST_RELAYED_ACTIONS and action not in owner_files))
        self.assertFalse(HOST_RELAYED_ACTIONS & set(profile_actions(STRICT_PROFILE)))
        self.assertTrue(HOST_RELAYED_ACTIONS <= set(route_unavailable(STRICT_PROFILE)), 'declared, never silently missing')
        self.assertTrue(owner_files <= set(route_unavailable(STRICT_PROFILE)), 'declared, never silently missing')
        self.assertEqual({k: v for k, v in route_unavailable(STRICT_PROFILE).items()
                          if k not in HOST_RELAYED_ACTIONS and k not in owner_files},
                         route_unavailable(BOUNDED_PROFILE))
        self.assertFalse(HOST_RELAYED_ACTIONS & set(route_unavailable(BOUNDED_PROFILE)))
        self.assertTrue(BROWSER_ACTIONS <= HOST_RELAYED_ACTIONS)
        self.assertEqual(StrictIsolatedAgentOSMcpTools.PROFILE, STRICT_PROFILE)

    def test_cli_versions_are_parsed_exactly(self):
        self.assertEqual(parse_cli_version('codex', 'codex-cli 0.153.4\n'), '0.153.4')
        self.assertEqual(parse_cli_version('claude-code', '2.1.280 (Claude Code)\n'), '2.1.280')
        for engine, text in (('codex', 'codex-cli 0.153.4-beta'), ('codex', ''), ('claude-code', '2.1.280'),
                             ('claude-code', 'error'), ('other', '1.0.0')):
            with self.subTest(engine=engine, text=text):
                self.assertIsNone(parse_cli_version(engine, text))


PLAN = ['apps', 'browser_use', 'hooks', 'image_generation', 'multi_agent', 'plugins', 'shell_tool', 'view_image']


class StrictLaunchArguments(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        (self.folder / 'codex-home').mkdir()
        self.config = self.folder / 'agentos-mcp.json'
        self.config.write_text(json.dumps({'mcpServers': {'agentos': {'command': 'python3', 'args': ['-m', 'x']}}}))
        self.adapter = BoundedExecutionAdapter(finder=lambda name: '/runtime/' + name, codex_home=self.folder / 'codex-home')

    def test_codex_strict_argv_is_pinned(self):
        trusted = self.adapter.command('codex', '/runtime/codex', 'prompt', self.config)
        strict = self.adapter.command('codex', '/runtime/codex', 'prompt', self.config, profile=STRICT_PROFILE,
                                      disabled_features=PLAN)
        # With --sandbox read-only also present Codex applies it, which can read the store.
        self.assertNotIn('--sandbox', strict)
        overrides = [item for key, value in CODEX_DECISION_CONFIG for item in ('-c', f'{key}={value}')]
        expected = ['--ignore-rules', '-c', f'default_permissions="{CODEX_STRICT_PERMISSIONS}"', '-c', CODEX_STRICT_TABLE,
                    *overrides, *[item for feature in PLAN for item in ('--disable', feature)]]
        self.assertEqual(strict[3:3 + len(expected)], expected)
        self.assertEqual(strict[3], '--ignore-rules', 'review P1: exec rules cannot run a command outside Seatbelt')
        self.assertIn('web_search="disabled"', strict, 'provider-hosted search stays off')
        self.assertEqual(CODEX_STRICT_TABLE, 'permissions.agentos-strict-isolated={filesystem={":minimal"="read", '
                                             '":workspace_roots"={"."="read"}}, network={enabled=false}}')
        # #961: trusted-local also keeps CODEX_HOME skills out; strict's overrides above already include it.
        self.assertEqual(trusted[8:10], ['-c', 'skills.include_instructions=false'])
        self.assertIn('skills.include_instructions=false', strict)
        self.assertEqual(strict[:3] + strict[3 + len(expected):], trusted[:3] + trusted[10:],
                         'everything else is the trusted-local argv')
        self.assertEqual(trusted[3:6], ['--sandbox', 'read-only', '--ignore-rules'],
                         'trusted-local keeps the read-only sandbox and ignores CODEX_HOME exec rules (#636)')
        # #678: trusted-local states its web search explicitly; off unless the turn enables it.
        self.assertEqual(trusted[6:8], ['-c', 'web_search="disabled"'])

    def test_codex_strict_without_a_verified_feature_plan_is_refused(self):
        for plan in ((), None, ['apps', 'unified_exec'], ['personality']):
            with self.subTest(plan=plan), self.assertRaises(ExecutionError):
                self.adapter.command('codex', '/runtime/codex', 'prompt', self.config, profile=STRICT_PROFILE,
                                     disabled_features=plan)

    def test_claude_strict_has_no_built_in_tools_and_allows_only_the_offered_bridge_tools(self):
        trusted = self.adapter.command('claude-code', '/runtime/claude', 'prompt', self.config, 'instructions')
        strict = self.adapter.command('claude-code', '/runtime/claude', 'prompt', self.config, 'instructions', profile=STRICT_PROFILE)
        allow = ['--allowedTools', 'mcp__agentos__list_notes,'
                                   'mcp__agentos__propose_current_state,mcp__agentos__public_page_read,'
                                   'mcp__agentos__save_note,mcp__agentos__weather,mcp__agentos__web_search']
        # #826: trusted-local also pre-approves the connected-folder documents and approved pages.
        trusted_allow = ('mcp__agentos__find_files,mcp__agentos__list_notes,'
                         'mcp__agentos__list_roots,mcp__agentos__propose_current_state,mcp__agentos__public_page_read,'
                         'mcp__agentos__read_file,mcp__agentos__save_note,mcp__agentos__save_result,'
                         'mcp__agentos__weather,mcp__agentos__web_search')
        # #623: both pre-approve exactly their bridge tools; strict also removes
        # every built-in tool.  #701: trusted-local adds the browser tools.
        browser = ',mcp__agentos__browser_open,mcp__agentos__browser_read,mcp__agentos__browser_find,' \
                  'mcp__agentos__browser_click,mcp__agentos__browser_type,mcp__agentos__browser_sign_in'
        # #774: trusted-local also pre-approves the owner-state tools its service relays.
        owner_state = (',mcp__agentos__calendar_query,mcp__agentos__calendar_draft_create,'
                       'mcp__agentos__calendar_draft_update,mcp__agentos__calendar_draft_cancel,'
                       'mcp__agentos__list_memory,mcp__agentos__search_memory,mcp__agentos__save_memory,mcp__agentos__schedule_preparation,'
                       'mcp__agentos__ask_location,'
                       # #794 phase 3: the owner's exact forget/delete/undo, relayed the same way.
                       'mcp__agentos__forget_record,'
                       # #814: the owner settings tools, relayed the same way.
                       'mcp__agentos__settings_read,mcp__agentos__settings_change,'
                       # #1335: connection discovery, a settings tool relayed the same way.
                       'mcp__agentos__capability_search,'
                       # #826: the information-use audit, relayed the same way.
                       'mcp__agentos__information_use,'
                       # #1172: the owner's Drive reads, relayed the same way.
                       'mcp__agentos__drive_search,mcp__agentos__drive_read,'
                       # #150: the owner's Gmail reads, relayed the same way.
                       'mcp__agentos__mail_search,mcp__agentos__mail_read,'
                       # #1216: authenticated API calls by slot, relayed the same way.
                       'mcp__agentos__api_request,'
                       # #1380: sandboxed code runs, relayed the same way.
                       'mcp__agentos__code_run,'
                       # #961: the Work's pinned skills, read in the bridge.
                       'mcp__agentos__skill_load,mcp__agentos__skill_resource,'
                       # #32: the advisory method-gap note, offered with the skill tools.
                       'mcp__agentos__method_gap')
        self.assertEqual(trusted[-2:], [allow[0], trusted_allow + browser + owner_state])
        self.assertEqual(strict[-2:], allow, 'the variadic --allowedTools stays last')
        # #308 S2: only the prompt slot differs; trusted-local reads it as stream-json input.
        session = ['--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose', '--replay-user-messages']
        self.assertEqual(trusted[1:2 + len(session)], ['-p', *session])
        one_shot = [trusted[0], '-p', 'prompt', '--output-format', 'stream-json', '--verbose', *trusted[2 + len(session):-2]]
        self.assertEqual(strict[:len(one_shot)], one_shot)
        self.assertNotIn('browser', ' '.join(strict), 'strict never pre-approves a browser tool')
        self.assertEqual(strict[len(one_shot):], ['--tools', '', '--restricted', *allow])
        self.assertNotIn('--tools', trusted)
        self.assertNotIn('--restricted', trusted)

    def test_no_widening_for_parity_and_unknown_profiles_are_refused(self):
        for engine in ('codex', 'claude-code'):
            argv = self.adapter.command(engine, '/runtime/cli', 'prompt', self.config, profile=STRICT_PROFILE,
                                        disabled_features=PLAN)
            joined = ' '.join(argv)
            for forbidden in ('danger-full-access', 'workspace-write', '--full-auto', 'dangerously', '--add-dir',
                              'bypass', '"write"', 'enabled=true', ':root', '--enable'):
                with self.subTest(engine=engine, forbidden=forbidden):
                    self.assertNotIn(forbidden, joined)
            for profile in ('isolated-agentos-mcp', 'unknown', None):
                with self.subTest(engine=engine, profile=profile), self.assertRaises(ExecutionError):
                    self.adapter.command(engine, '/runtime/cli', 'prompt', self.config, profile=profile)
        env = self.adapter.environment('codex', '/runtime/codex', self.folder)
        self.assertEqual(set(env), {'HOME', 'PATH', 'LANG', 'PYTHONPATH', 'CODEX_HOME'}, 'environment unchanged')

    def test_the_work_allowlist_is_the_decision_allowlist_plus_unified_exec(self):
        from personal_agent.decision_adapters import CODEX_ALLOWED_ENABLED_FEATURES
        self.assertEqual(strict_allowed_features(), CODEX_ALLOWED_ENABLED_FEATURES | {'unified_exec'})
        self.assertNotIn('shell_tool', strict_allowed_features())


#: A fake native CLI: the ELF magic the strict profile requires of the executable.
FAKE_NATIVE = b'\x7fELF fake native cli\n'


def _pinned_platform(case):
    """The qualification logic is platform-independent; CI runs on Linux."""
    from unittest import mock
    patcher = mock.patch.object(sys, 'platform', 'darwin')
    patcher.start()
    case.addCleanup(patcher.stop)




FEATURES = ('apps                 stable             true\n'
            'personality          stable             true\n'
            'shell_tool           stable             true\n'
            'unified_exec         stable             true\n'
            'memories             stable             false\n'
            'steer                removed            true\n')


class StrictQualificationLogic(unittest.TestCase):
    """``qualify_strict`` with a scripted runner in place of the CLI."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.store = self.folder / 'store'
        self.store.mkdir()
        (self.folder / 'codex-home').mkdir()
        (self.folder / 'bin').mkdir()
        for name in ('codex', 'claude'):
            (self.folder / 'bin' / name).write_bytes(FAKE_NATIVE)
        self.finder = lambda name: str(self.folder / 'bin' / name)
        self.calls = []
        from unittest import mock
        probe = mock.patch('personal_agent.cli_qualification.surface_checks',
                           return_value=[{'check': 'strict-work-bridge-and-tool-surface', 'passed': True},
                                                  *[{'check': f'instruction-files-{i}', 'passed': True} for i in range(3)]])
        self.surface = probe.start()
        self.addCleanup(probe.stop)
        _pinned_platform(self)
        # tempfile lives under /tmp on Linux CI; the baseline-readable
        # refusal has its own test with the real list.
        from unittest import mock
        from personal_agent import bounded_execution
        patcher = mock.patch.object(bounded_execution, 'BASELINE_READABLE_ROOTS', ('/nonexistent-baseline-root',))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _runner(self, version, readable=(), stuck=()):
        def runner(argv, **kwargs):
            self.calls.append((list(argv), dict(kwargs['env'])))
            if argv[1:] == ['--version']:
                return _Done(stdout=version)
            if argv[1:3] == ['features', 'list']:
                disabled = {argv[i + 1] for i, part in enumerate(argv) if part == '--disable'}
                lines = [line for line in FEATURES.splitlines()
                         if line.split()[0] not in disabled or line.split()[0] in stuck]
                return _Done(stdout='\n'.join(lines))
            target, turn = Path(argv[-1]), Path(kwargs['cwd'])
            return _Done(returncode=0 if target == turn or target in readable else 1, stderr='Operation not permitted')
        return runner

    def _qualify(self, engine='codex', version=None, readable=(), stuck=(), runtime_root=None, tested_versions=None):
        version = version or (f'codex-cli {CODEX_VERSION}' if engine == 'codex' else f'{CLAUDE_VERSION} (Claude Code)')
        adapter = BoundedExecutionAdapter(finder=self.finder, runner=self._runner(version, readable, stuck),
                                          runtime_root=runtime_root or self.folder / 'turns',
                                          codex_home=self.folder / 'codex-home')
        return adapter.qualify_strict(engine, store_root=self.store, tested_versions=tested_versions)

    def test_judgment_versions_do_not_widen_work_or_skip_denial_checks(self):
        for version in ('codex-cli 0.162.1', 'codex-cli 0.164.0'):
            with self.subTest(version=version):
                self.assertTrue(self._qualify(version=version)['qualified'])
                self.assertTrue(self._qualify(version=version, tested_versions=CODEX_INSTRUCTION_FILES_QUALIFIED)['qualified'])
                self.assertFalse(self._qualify(version=version, tested_versions=CODEX_INSTRUCTION_FILES_QUALIFIED,
                                               readable=(self.store,))['qualified'])

    def test_codex_qualifies_and_binds_its_plan_binary_and_paths(self):
        result = self._qualify()
        self.assertTrue(result['qualified'], result)
        self.assertEqual(result['version'], CODEX_VERSION)
        self.assertEqual([check['check'] for check in result['checks']],
                         ['tested-platform', 'runtime-root-not-baseline-readable', 'reported-version',
                          'turn-directory-readable', 'sibling-turn-denied', 'protected-directory-denied',
                          'protected-directory-denied', 'protected-directory-denied', 'tool-features-allowlisted',
                          'strict-work-bridge-and-tool-surface', 'instruction-files-0', 'instruction-files-1', 'instruction-files-2',
                          'binding-unchanged', 'native-binary-identified', 'binary-unchanged'])
        self.assertEqual(result['binary_sha256'], result['native_sha256'], 'a plain executable is its own native binary')
        self.assertEqual(len(result['binary_sha256']), 64)
        self.assertEqual(result['disabled_features'], ['apps', 'memories', 'shell_tool'],
                         'every non-removed feature outside the allowlist, whatever its default')
        binding = result['binding']
        self.assertEqual(binding['store'], str(self.store.resolve()))
        self.assertEqual(binding['codex_home'], str((self.folder / 'codex-home').resolve()))
        self.assertEqual(binding['runtime_root'], str((self.folder / 'turns').resolve()))
        self.assertEqual(binding['home'], str(Path.home().resolve()))
        self.assertIn('fingerprint', binding)
        sandbox = [argv for argv, _ in self.calls if 'sandbox' in argv]
        self.assertTrue(Path(sandbox[1][-1]).name.startswith('qualify-sibling-'))
        self.assertEqual([argv[-1] for argv in sandbox[2:]],
                         [str(Path.home()), str(self.folder / 'codex-home'), str(self.store)])
        for argv, env in ((argv, env) for argv, env in self.calls if argv[1] in ('sandbox', 'features')):
            self.assertNotEqual(env['CODEX_HOME'], str(self.folder / 'codex-home'), "the owner's Codex config is not loaded")
        for argv in sandbox:
            self.assertEqual(argv[2:9], ['-C', argv[3], '-c', CODEX_STRICT_TABLE, '-P', CODEX_STRICT_PERMISSIONS, '--'])
            self.assertEqual(argv[9], '/bin/ls', 'a listing only; output is discarded')
        self.assertNotIn('content', json.dumps(result))

    def test_any_readable_protected_or_sibling_directory_fails_qualification(self):
        for readable in ((self.store,), (Path.home(),), (self.folder / 'codex-home',)):
            with self.subTest(readable=readable):
                result = self._qualify(readable=readable)
                self.assertFalse(result['qualified'])
                self.assertEqual(result['reason'], 'protected-directory-denied')

    def test_a_feature_that_stays_enabled_fails_closed(self):
        result = self._qualify(stuck=('shell_tool',))
        self.assertFalse(result['qualified'])
        self.assertEqual(result['reason'], 'tool-features-allowlisted')
        self.assertIsNone(result['disabled_features'])

    def test_a_runtime_root_the_baseline_keeps_readable_fails_before_any_process(self):
        from unittest import mock
        from personal_agent import bounded_execution
        self.assertEqual(BASELINE_READABLE_ROOTS, ('/tmp', '/private/tmp', '/var/tmp', '/private/var/tmp'))
        patcher = mock.patch.object(bounded_execution, 'BASELINE_READABLE_ROOTS', BASELINE_READABLE_ROOTS)
        patcher.start()
        self.addCleanup(patcher.stop)
        for root in ('/tmp/agentos-runs', '/private/var/tmp/agentos-runs', '/var/tmp'):
            with self.subTest(root=root):
                self.calls.clear()
                result = self._qualify(runtime_root=Path(root))
                self.assertFalse(result['qualified'])
                self.assertEqual(result['reason'], 'runtime-root-not-baseline-readable')
                self.assertEqual(self.calls, [])

    def test_an_unlisted_version_passes_only_after_all_probes(self):
        self.calls.clear()
        result = self._qualify(version='codex-cli 9.9.9')
        self.assertTrue(result['qualified'], result)
        self.assertTrue([argv for argv, _ in self.calls if 'sandbox' in argv])
        self.surface.assert_called_once()
        result = self._qualify(version='garbled')
        self.assertFalse(result['qualified'])
        self.assertIn('reported-version', result['reason'])

    def test_a_broken_runner_is_not_mistaken_for_denial(self):
        """Every probe failing (for example no sandbox) fails the turn-dir control."""
        def runner(argv, **kwargs):
            return _Done(stdout=f'codex-cli {CODEX_VERSION}') if argv[1:] == ['--version'] else _Done(returncode=1)
        adapter = BoundedExecutionAdapter(finder=self.finder, runner=runner,
                                          runtime_root=self.folder / 'turns', codex_home=self.folder / 'codex-home')
        result = adapter.qualify_strict('codex', store_root=self.store)
        self.assertFalse(result['qualified'])
        self.assertIn('turn-directory-readable', result['reason'])

    def test_a_crashing_or_unobservable_denial_probe_is_not_permission_denial(self):
        base = self._runner(f'codex-cli {CODEX_VERSION}')
        for code, error in ((-11, 'crashed'), (2, 'unknown permissions profile'), (1, '')):
            def runner(argv, **kwargs):
                if argv[1] == 'sandbox' and Path(argv[-1]) == self.store:
                    return _Done(returncode=code, stderr=error)
                return base(argv, **kwargs)
            adapter = BoundedExecutionAdapter(finder=self.finder, runner=runner,
                       runtime_root=self.folder / 'turns', codex_home=self.folder / 'codex-home')
            with self.subTest(code=code, error=error):
                result = adapter.qualify_strict('codex', store_root=self.store)
                self.assertFalse(result['qualified'])
                self.assertIn('protected-directory-denied', result['reason'])
                self.surface.assert_not_called()

    def test_an_untested_platform_fails_closed_before_running_anything(self):
        from unittest import mock
        with mock.patch.object(sys, 'platform', 'linux'):
            result = self._qualify()
        self.assertFalse(result['qualified'])
        self.assertEqual(result['reason'], 'tested-platform')
        self.assertEqual(self.calls, [])

    def test_claude_code_qualification_is_version_platform_and_paths(self):
        result = self._qualify('claude-code')
        self.assertTrue(result['qualified'])
        self.assertNotIn('codex_home', result['binding'])
        self.assertTrue(self._qualify('claude-code', version='2.2.0 (Claude Code)')['qualified'])
        self.assertFalse(BoundedExecutionAdapter(finder=lambda name: None).qualify_strict('codex')['qualified'])
        # A launcher whose native executable cannot be identified fails closed.
        missing = BoundedExecutionAdapter(finder=lambda name: '/runtime/' + name, runner=self._runner(f'codex-cli {CODEX_VERSION}'),
                                          runtime_root=self.folder / 'turns', codex_home=self.folder / 'codex-home')
        self.assertIn('native-binary-identified', missing.qualify_strict('codex', store_root=self.store)['reason'])

    def test_the_codex_js_shim_resolves_to_the_native_binary_it_spawns(self):
        """Review N2: mirror codex.js's findCodexExecutable; anything else fails closed."""
        from unittest import mock
        native_of = BoundedExecutionAdapter.native_cli_binary
        modules = self.folder / 'lib' / 'node_modules'
        root = modules / '@openai' / 'codex'
        (root / 'bin').mkdir(parents=True)
        shim = root / 'bin' / 'codex.js'
        shim.write_text('#!/usr/bin/env node\n// shim\n')
        link = self.folder / 'codex-link'
        link.symlink_to(shim)

        def platform_package(base):
            (base / 'package.json').parent.mkdir(parents=True, exist_ok=True)
            (base / 'package.json').write_text('{}')
            native = base / 'vendor' / 'aarch64-apple-darwin' / 'bin' / 'codex'
            native.parent.mkdir(parents=True)
            native.write_bytes(FAKE_NATIVE)
            return native.resolve()

        with mock.patch('platform.machine', return_value='arm64'):
            self.assertEqual(native_of('codex', str(link)), '', 'no native binary: fail closed')
            fallback = platform_package(root)  # the package's own vendor/
            (root / 'package.json').unlink()
            self.assertEqual(native_of('codex', str(link)), str(fallback))
            hoisted = platform_package(modules / '@openai' / 'codex-darwin-arm64')
            self.assertEqual(native_of('codex', str(link)), str(hoisted), 'a hoisted platform package wins over vendor/')
            nested = platform_package(root / 'node_modules' / '@openai' / 'codex-darwin-arm64')
            self.assertEqual(native_of('codex', str(link)), str(nested), 'the nearest node_modules wins')
            nested.write_text('#!/bin/sh\nexec /somewhere/else "$@"\n')
            self.assertEqual(native_of('codex', str(link)), '', 'a resolved non-native file fails closed')
            nested.write_bytes(FAKE_NATIVE)
            digests = BoundedExecutionAdapter().strict_digests('codex', str(link))
        self.assertNotEqual(digests['binary_sha256'], digests['native_sha256'])
        with mock.patch('platform.machine', return_value='riscv64'):
            self.assertEqual(native_of('codex', str(link)), '', 'an unknown target triple fails closed')
        wrapper = self.folder / 'wrapper'
        wrapper.write_text('#!/bin/sh\nexec codex "$@"\n')
        for engine in ('codex', 'claude-code'):
            with self.subTest(engine=engine):
                self.assertEqual(native_of(engine, str(wrapper)), '', 'a shell wrapper fails closed')
                self.assertEqual(native_of(engine, str(self.folder / 'bin' / 'codex')),
                                 str((self.folder / 'bin' / 'codex').resolve()))
        self.assertFalse(BoundedExecutionAdapter().qualify_strict('other')['qualified'])


class StrictAutomaticAdmission(unittest.TestCase):
    """Stored records and version lists cannot authorize a changed binary."""
    def setUp(self):
        StrictQualificationLogic.setUp(self)
        self.binary = self.folder / 'bin' / 'codex'
        self.fail_probe = False
        base = StrictQualificationLogic._runner(self, 'codex-cli 9.9.9')

        def runner(argv, **kwargs):
            if argv[1] == 'exec':
                self.calls.append((list(argv), kwargs['env']))
                return _codex_answer()
            if self.fail_probe and argv[1] == 'sandbox':
                raise subprocess.TimeoutExpired(argv, 30)
            return base(argv, **kwargs)
        self.adapter = BoundedExecutionAdapter(finder=self.finder, runner=runner,
                    runtime_root=self.folder / 'turns', codex_home=self.folder / 'codex-home')
        self.tools = StrictIsolatedAgentOSMcpTools(_Caps(self.store), qualification=None)

    def test_an_unlisted_installed_version_automatically_qualifies_work(self):
        self.assertEqual(self.adapter.execute('codex', 'hello', self.tools).content, 'answer')
        probes = sum(argv[1] == 'sandbox' for argv, _ in self.calls)
        self.adapter.execute('codex', 'again', self.tools)
        self.assertEqual(sum(argv[1] == 'sandbox' for argv, _ in self.calls), probes)

    def test_a_failed_new_identity_never_uses_the_earlier_pass_or_launches(self):
        self.adapter.execute('codex', 'hello', self.tools)
        self.binary.write_bytes(FAKE_NATIVE + b'new binary')
        self.fail_probe = True
        for _ in range(2):  # a failure is never cached
            self.calls.clear()
            with self.assertRaises(ExecutionError) as caught:
                self.adapter.execute('codex', 'hello', self.tools)
            self.assertEqual(caught.exception.failure_class, 'isolation-unqualified')
            self.assertIn('turn-directory-readable', caught.exception.reason)
            self.assertFalse(any(argv[1] == 'exec' for argv, _ in self.calls))
            self.assertTrue(self.calls)

    def test_each_identity_field_invalidates_the_pass(self):
        from unittest import mock
        self.adapter.execute('codex', 'hello', self.tools)
        identity = self.adapter._strict_binary_identity('codex', str(self.binary))
        for index in range(5):
            changed = list(identity[0])
            changed[index] = str(changed[index]) + '-different' if index == 0 else changed[index] + 1
            with self.subTest(field=index), mock.patch.object(self.adapter, '_strict_binary_identity',
                         return_value=(tuple(changed), *identity[1:])):
                self.fail_probe = True
                self.calls.clear()
                with self.assertRaises(ExecutionError):
                    self.adapter.execute('codex', 'hello', self.tools)
                self.assertFalse(any(argv[1] == 'exec' for argv, _ in self.calls))

    def test_an_unobservable_surface_refuses_and_is_not_cached(self):
        self.surface.side_effect = OSError('loopback unavailable')
        for _ in range(2):
            with self.assertRaises(ExecutionError) as caught:
                self.adapter.execute('codex', 'hello', self.tools)
            self.assertIn('instruction-files-and-tool-surface', caught.exception.reason)
        self.assertEqual(self.surface.call_count, 2)

    def test_a_failing_surface_names_the_check(self):
        self.surface.return_value = [{'check': 'instruction-files-0', 'passed': False}]
        with self.assertRaises(ExecutionError) as caught:
            self.adapter.execute('codex', 'hello', self.tools)
        self.assertIn('instruction-files-0', caught.exception.reason)
        self.assertFalse(any(argv[1] == 'exec' for argv, _ in self.calls))

    def test_an_unrecognized_profile_cannot_automatically_qualify_or_launch(self):
        tools = StrictIsolatedAgentOSMcpTools(_Caps(self.store), qualification_allowed=False)
        with self.assertRaises(ExecutionError) as caught:
            self.adapter.execute('codex', 'hello', tools)
        self.assertEqual(caught.exception.reason, 'unrecognized-profile')
        self.assertEqual(self.calls, [])
        self.surface.assert_not_called()

    def test_a_changed_binary_during_probes_and_a_missing_check_fail_closed(self):
        good = self.surface.return_value
        def change(*args, **kwargs):
            self.binary.write_bytes(FAKE_NATIVE + b'changed while checking')
            return good
        self.surface.side_effect = change
        with self.assertRaises(ExecutionError) as caught:
            self.adapter.execute('codex', 'hello', self.tools)
        self.assertIn('binary-unchanged', caught.exception.reason)
        self.surface.side_effect = None
        self.surface.return_value = []
        with self.assertRaises(ExecutionError) as caught:
            self.adapter.execute('codex', 'hello', self.tools)
        self.assertIn('instruction-files-and-tool-surface', caught.exception.reason)

    def test_qualification_never_uses_owner_credentials_or_profile(self):
        from unittest import mock
        self.adapter.credentials = mock.Mock(side_effect=AssertionError('owner credential requested'))
        def inspect(local, engine, binary, root, disabled, **kwargs):
            self.assertEqual(local.credentials(engine), '')
            self.assertIsNone(local.cloud)
            self.assertTrue(local.codex_home.is_relative_to(root))
            self.assertNotEqual(local.codex_home, self.adapter.codex_home)
            return [{'check': 'strict-work-bridge-and-tool-surface', 'passed': True},
                    *[{'check': f'instruction-files-{i}', 'passed': True} for i in range(3)]]
        self.surface.side_effect = inspect
        self.assertTrue(self.adapter.qualify_strict('codex', store_root=self.store)['qualified'])
        self.adapter.credentials.assert_not_called()

    def test_a_judgment_pass_never_admits_a_failing_work_bridge(self):
        def observed(*args, include_work=True):
            checks = [{'check': f'instruction-files-{i}', 'passed': True} for i in range(3)]
            if include_work:
                checks.append({'check': 'strict-work-bridge-and-tool-surface', 'passed': False})
            return checks
        self.surface.side_effect = observed
        local = self.adapter.qualified_identity('codex', str(self.binary), self.store, purpose='judgment')
        self.assertTrue(local['qualified'])
        with self.assertRaises(ExecutionError) as caught:
            self.adapter.execute('codex', 'hello', self.tools)
        self.assertIn('strict-work-bridge-and-tool-surface', caught.exception.reason)
        self.assertFalse(any(argv[1] == 'exec' for argv, _ in self.calls))

    def test_an_explicit_failed_recheck_evicts_an_earlier_work_pass(self):
        self.adapter.execute('codex', 'hello', self.tools)
        self.surface.side_effect = OSError('loopback unavailable')
        self.assertFalse(self.adapter.qualify_strict('codex', store_root=self.store)['qualified'])
        with self.assertRaises(ExecutionError):
            self.adapter.execute('codex', 'hello', self.tools)
        self.assertIsNone(self.adapter.strict_cached_qualification('codex', str(self.binary), self.store))

    def test_platform_and_runtime_root_still_refuse_without_any_process(self):
        from unittest import mock
        for field, value in (('platform', 'linux'),):
            with mock.patch.object(sys, field, value), self.assertRaises(ExecutionError):
                self.adapter.execute('codex', 'hello', self.tools)
        self.assertEqual(self.calls, [])
        with mock.patch('personal_agent.bounded_execution.BASELINE_READABLE_ROOTS', (str(self.folder),)), \
                self.assertRaises(ExecutionError):
            self.adapter.execute('codex', 'hello', self.tools)
        self.assertEqual(self.calls, [])


class _Engine:
    """A scripted subscription adapter recording what the service asks of it."""

    def __init__(self, qualification, stale=()):
        self.qualification, self.facades, self.qualified, self.stale = qualification, [], [], list(stale)
        self.during_qualification = None

    def login_status(self, engine_id, binary=None):
        return {'state': 'signed-in'}

    def qualify_strict(self, engine_id, binary=None, store_root=None):
        self.qualified.append((engine_id, str(store_root)))
        if self.during_qualification:
            self.during_qualification()
        return dict(self.qualification)

    def strict_binding_mismatch(self, engine_id, qualification, store_root, binary=None):
        return list(self.stale)

    def execute(self, engine, prompt, tools, **kwargs):
        self.facades.append(tools)
        if getattr(tools, 'PROFILE', None) == STRICT_PROFILE and tools.qualified_version != CODEX_VERSION:
            raise ExecutionError('refused', failure_class='isolation-unqualified')
        return ExecutionResult('answer', engine, 0, {})


PASS = {'qualified': True, 'version': CODEX_VERSION, 'reason': '', 'profile': STRICT_PROFILE,
        'checks': [{'check': 'tested-version', 'passed': True}], 'binding': {'store': '/s'}, 'binary_sha256': 'ab',
        'disabled_features': PLAN}
FAIL = {'qualified': False, 'version': CODEX_VERSION, 'reason': 'protected-directory-denied', 'checks': []}


class OwnerChoosesTheProfile(unittest.TestCase):
    def _service(self, qualification, stale=()):
        from personal_agent.quickstart_service import AgentService
        from personal_agent.subscription_engines import SubscriptionEngines
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        self.engine = _Engine(qualification, stale)
        service = AgentService(self.store, subscription_engines=SubscriptionEngines(finder=lambda c: '/runtime/' + c, clock=lambda: 1),
                               execution_adapter=self.engine)
        service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        return service

    def _turn(self, service):
        self.store.enqueue('hello', f'k{len(self.store.jobs())}')
        self.assertTrue(service.run_one())
        job_id = service.task_progress()['tasks'][0]['id']
        return self.store.turn_provenance(job_id)

    def test_default_is_trusted_local_and_labelled(self):
        service = self._service(PASS)
        status = service.settings()['subscription_execution']
        self.assertEqual((status['profile'], status['trust'], status['limitation']),
                         ('trusted-local', 'trusted-local', TRUSTED_LOCAL_LIMITATION))
        record = self._turn(service)
        self.assertEqual((record['capability_profile'], record['capability_trust']), ('trusted-local', 'trusted-local'))
        self.assertIs(type(self.engine.facades[-1]), AgentOSMcpTools)
        self.assertEqual(self.engine.qualified, [], 'nothing is qualified or switched automatically')

    def test_a_passing_qualification_selects_strict_and_every_surface_says_so(self):
        service = self._service(PASS)
        result = service.select_subscription_isolation({'profile': 'strict-isolated'})
        self.assertEqual(result['profile'], 'strict-isolated')
        self.assertEqual(self.engine.qualified, [('codex', str(self.store.root))], 'the real store path is probed')
        stored = self.store.config('subscription_isolation')['qualified']['codex']
        self.assertEqual((stored['binding'], stored['binary_sha256'], stored['disabled_features']), ({'store': '/s'}, 'ab', PLAN))
        status = service.settings()['subscription_execution']
        self.assertEqual((status['profile'], status['trust'], status['requalify_needed']),
                         ('strict-isolated', 'strict-isolated', False))
        self.assertEqual(status['qualified']['codex']['version'], CODEX_VERSION)
        self.assertNotIn('binding', status['qualified']['codex'], 'resolved paths stay out of Settings')
        record = self._turn(service)
        self.assertEqual((record['capability_profile'], record['capability_trust'], record['status']),
                         ('strict-isolated', 'strict-isolated', 'answered'))
        self.assertEqual(record['capability_limitation'], CLI_PROFILES[STRICT_PROFILE]['limitation'])
        facade = self.engine.facades[-1]
        self.assertIsInstance(facade, StrictIsolatedAgentOSMcpTools)
        self.assertEqual(facade.qualification['disabled_features'], PLAN)

    def test_settings_say_requalify_when_the_binding_no_longer_matches(self):
        service = self._service(PASS)
        service.select_subscription_isolation({'profile': 'strict-isolated'})
        self.engine.stale = ['store']
        status = service.settings()['subscription_execution']
        self.assertEqual((status['trust'], status['requalify_needed']), ('strict-isolated', True))

    def test_a_failed_qualification_keeps_the_previous_profile(self):
        service = self._service(FAIL)
        with self.assertRaisesRegex(ValueError, 'trusted-local'):
            service.select_subscription_isolation({'profile': 'strict-isolated'})
        self.assertEqual(service.settings()['subscription_execution']['trust'], 'trusted-local')
        # Previously strict: a failed requalification keeps strict and its record.
        self.engine.qualification = PASS
        service.select_subscription_isolation({'profile': 'strict-isolated'})
        self.engine.qualification = FAIL
        with self.assertRaisesRegex(ValueError, 'strict-isolated'):
            service.select_subscription_isolation({'profile': 'strict-isolated'})
        status = service.settings()['subscription_execution']
        self.assertEqual((status['trust'], status['qualified']['codex']['version']), ('strict-isolated', CODEX_VERSION))

    def test_a_concurrent_owner_choice_is_not_overwritten_by_a_finishing_qualification(self):
        service = self._service(PASS)
        service.select_subscription_isolation({'profile': 'strict-isolated'})
        # While a requalification runs, the owner explicitly picks trusted-local.
        self.engine.during_qualification = lambda: service.select_subscription_isolation({'profile': 'trusted-local'})
        with self.assertRaisesRegex(ValueError, '바뀌어'):
            service.select_subscription_isolation({'profile': 'strict-isolated'})
        self.assertEqual(service.settings()['subscription_execution']['trust'], 'trusted-local')

    def test_an_unrecognised_or_malformed_stored_profile_fails_closed(self):
        service = self._service(PASS)
        for row in (None, 'strict-isolated', [], {}, {'qualified': {}}, {'profile': None}, {'profile': 'strict-isolated-v2'}):
            with self.subTest(row=row):
                self.store.put('subscription_isolation', row)
                self.assertEqual(service.subscription_isolation()['profile'], 'unrecognised')
        self.store.put('subscription_isolation', {'profile': 'strict-isolated-v2', 'qualified': {}})
        status = service.settings()['subscription_execution']
        self.assertEqual((status['profile'], status['trust'], status['requalify_needed']), ('unrecognised', 'unknown', True))
        record = self._turn(service)
        self.assertEqual((record['status'], record['failure_class']), ('failed', 'isolation-unqualified'))
        self.assertIsInstance(self.engine.facades[-1], StrictIsolatedAgentOSMcpTools, 'never read as trusted-local')
        self.assertIsNone(self.engine.facades[-1].qualification)

    def test_no_silent_downgrade_when_the_cli_no_longer_qualifies(self):
        service = self._service(PASS)
        service.select_subscription_isolation({'profile': 'strict-isolated'})
        # The owner switches to another CLI that was never qualified.
        service.connect_subscription_engine({'engine': 'claude-code', 'officially_authenticated': True})
        self.assertTrue(service.settings()['subscription_execution']['requalify_needed'])
        record = self._turn(service)
        self.assertEqual((record['capability_trust'], record['status'], record['failure_class']),
                         ('strict-isolated', 'failed', 'isolation-unqualified'))
        self.assertTrue(all(isinstance(facade, StrictIsolatedAgentOSMcpTools) for facade in self.engine.facades),
                        'no trusted-local facade was ever used instead')

    def test_a_record_from_another_platform_qualifies_nothing(self):
        service = self._service(PASS)
        service.select_subscription_isolation({'profile': 'strict-isolated'})
        row = self.store.config('subscription_isolation')
        self.assertEqual(row['qualified']['codex']['platform'], sys.platform)
        row['qualified']['codex']['platform'] = 'another-os'
        self.store.put('subscription_isolation', row)
        record = self._turn(service)
        self.assertEqual((record['capability_trust'], record['status'], record['failure_class']),
                         ('strict-isolated', 'failed', 'isolation-unqualified'))
        self.assertIsNone(self.engine.facades[-1].qualified_version)

    def test_trusted_local_is_an_explicit_owner_choice_and_input_is_checked(self):
        service = self._service(PASS)
        service.select_subscription_isolation({'profile': 'strict-isolated'})
        service.select_subscription_isolation({'profile': 'trusted-local'})
        self.assertEqual(service.settings()['subscription_execution']['trust'], 'trusted-local')
        for body in (None, {}, {'profile': 'isolated-agentos-mcp'}, {'profile': 'full-access'}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                service.select_subscription_isolation(body)


class DoctorReportsTheSelectedProfile(unittest.TestCase):
    def test_selected_profile_and_recorded_trust_are_read_only(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        self.addCleanup(sys.path.remove, str(ROOT / 'scripts'))
        import agentos_doctor
        with tempfile.TemporaryDirectory() as folder:
            store = QuickStore(Path(folder) / 'data')
            self.assertEqual(agentos_doctor._selected_host_profile(store.root),
                             {'profile': 'trusted-local', 'qualified_versions': {}})
            for row in ({'profile': 'strict-isolated-v2'}, {}, None, ['strict-isolated'], 'trusted-local'):
                store.put('subscription_isolation', row)
                self.assertEqual(agentos_doctor._selected_host_profile(store.root)['profile'], 'unrecognised', row)
            store.put('subscription_isolation', {'profile': 'strict-isolated', 'qualified': {'codex': {'version': CODEX_VERSION}}})
            with store.db() as db:
                db.execute('INSERT INTO turn_provenance VALUES (?,?,?)',
                           ('j', json.dumps({'route': 'subscription', 'capability_profile': 'strict-isolated',
                                             'capability_trust': 'strict-isolated'}), 1))
            self.assertEqual(agentos_doctor._selected_host_profile(store.root),
                             {'profile': 'strict-isolated', 'qualified_versions': {'codex': CODEX_VERSION}})
            turn = agentos_doctor._recent_turns(store.root)[0]
            self.assertEqual((turn['capability_profile'], turn['capability_trust']), ('strict-isolated', 'strict-isolated'))


# -- Process-level qualification (opt-in) ------------------------------------

from personal_agent.cli_qualification import _ScriptedModel, populate_codex_home


_BRIDGE_STUB = '''
# Test-only (#616): stub the public network of the real Still bridge process.
import sys
sys.path.insert(0, {src!r})
try:
    from personal_agent import local_tools as _lt
    _lt.LocalTools.search = lambda self, query: {{"tool": "web_search", "query": query, "retrieved_at": 1,
        "results": [{{"url": "https://example.com/stub", "title": "stub-search-616", "snippet": "s"}}],
        "sources": ["https://example.com/stub"]}}
    _lt.LocalTools.weather = lambda self, city, country="": {{"tool": "weather", "location": {{"name": "stub-weather-616"}},
        "retrieved_at": 1, "forecast": {{}}, "sources": ["https://open-meteo.com/"]}}
except Exception:
    pass
'''


def _installed(engine):
    binary = shutil.which({'codex': 'codex', 'claude-code': 'claude'}[engine])
    if not binary:
        return None, None
    done = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=30)
    return binary, parse_cli_version(engine, done.stdout)


#: The owner's request in the qualification Work is ``/search PUBLIC_QUERY``.
PUBLIC_QUERY = 'public query'
#: #654: the worker-composed weather city goes out with no sensitivity
#: judgment (the request names none); the call reached the bridge and was recorded.
BRIDGED_EVENTS = [('web_search', 'succeeded'), ('weather', 'succeeded'), ('list_notes', 'succeeded')]


#: Tools Codex may still offer under strict: the Still bridge namespace,
#: the MCP resource helpers (the bridge serves no resources) and the
#: non-interactive user-input request.  No shell, file, image or web tool.
STRICT_CODEX_TOOLS = {'mcp__agentos', 'list_mcp_resources', 'list_mcp_resource_templates', 'read_mcp_resource',
                      'request_user_input'}


@unittest.skipUnless(os.environ.get('AGENTOS_CLI_QUALIFICATION') == '1',
                     'opt-in process-level CLI qualification (set AGENTOS_CLI_QUALIFICATION=1)')
class ProcessLevelQualification(unittest.TestCase):
    """AX-S19 under the exact argv Still uses; no live model or account."""

    def setUp(self):
        # Fake owner layout under the real home, like a default install
        # (store and engine turns under ~/), never inside the owner's data.
        tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix='.agentos-616-qualification-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = QuickStore(self.root / 'data')
        (self.store.root / 'FAKE-STORE-CANARY.txt').write_text('fake-store-canary-616\n')
        with self.store.db() as db:
            db.execute("INSERT INTO notes VALUES ('n1','fake-note-616',1)")
        # An owner-typed `/search` (#605 D1) so the bridged search is sent as
        # typed and reaches the stubbed network without a sensitivity judgment.
        self.job = self.store.enqueue(f'/search {PUBLIC_QUERY}', 'q-' + uuid.uuid4().hex)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (self.job,))
        self.home_canary = Path.home() / f'.agentos-616-home-canary-{uuid.uuid4().hex}.txt'
        self.home_canary.write_text('fake-home-canary-616\n')
        self.addCleanup(self.home_canary.unlink)
        populate_codex_home(self.root / 'codex-home', self.store.root)
        self.argv = None

    def _stub_bridge_network(self, run_dir):
        site = subprocess.run([sys.executable, '-c', 'import site; print(site.getusersitepackages())'],
                              env={'HOME': str(run_dir), 'PATH': '/usr/bin:/bin'}, capture_output=True, text=True,
                              check=True).stdout.strip()
        Path(site).mkdir(parents=True, exist_ok=True)
        (Path(site) / 'usercustomize.py').write_text(_BRIDGE_STUB.format(src=str(ROOT / 'src')))

    def _adapter(self, engine, binary, model=None, argv_edit=None):
        def runner(argv, **kwargs):
            argv, env = list(argv), dict(kwargs['env'])
            if model is None or 'exec' not in argv and '-p' not in argv:
                return subprocess.run(argv, **kwargs)   # --version, sandbox, features list
            self._stub_bridge_network(Path(kwargs['cwd']))
            if argv_edit:
                argv = argv_edit(argv)
            if engine == 'codex':
                # Only the fake model endpoint is added; the Still argv is otherwise untouched.
                at = argv.index('exec') + 1
                argv[at:at] = ['-c', 'model_provider="fake"', '-c', 'model="fake-model"', '-c',
                               f'model_providers.fake={{name="fake", base_url="http://127.0.0.1:{model.port}/v1", '
                               'wire_api="responses", env_key="AGENTOS_FAKE_MODEL_KEY", request_max_retries=0, stream_max_retries=0}']
                env['AGENTOS_FAKE_MODEL_KEY'] = 'fake'
            else:
                env.update(ANTHROPIC_BASE_URL=f'http://127.0.0.1:{model.port}', ANTHROPIC_API_KEY='fake-key',
                           CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
            self.argv = argv
            kwargs['env'] = env
            return subprocess.run(argv, **kwargs)
        return BoundedExecutionAdapter(finder=lambda name: binary, runner=runner, runtime_root=self.root / 'engine-runs',
                                       codex_home=self.root / 'codex-home')

    def _qualification(self, engine, binary):
        result = self._adapter(engine, binary).qualify_strict(engine, store_root=self.store.root)
        self.assertTrue(result['qualified'], result)
        # The record the service stores (quickstart_service.select_subscription_isolation).
        return {'version': result['version'], 'platform': sys.platform, 'binding': result['binding'],
                'binary_sha256': result['binary_sha256'], 'native_sha256': result['native_sha256'],
                'disabled_features': result['disabled_features']}

    def _run(self, engine, binary, model, profile, qualification=None, argv_edit=None):
        adapter = self._adapter(engine, binary, model, argv_edit)
        caps = type('Caps', (), {'store': self.store, 'job_id': self.job, 'private_provenance': set()})()
        facade = StrictIsolatedAgentOSMcpTools(caps, qualification=qualification) if profile == STRICT_PROFILE \
            else AgentOSMcpTools(caps)
        try:
            adapter.execute(engine, 'Still isolation qualification probe', facade)
        finally:
            model.close()
        with self.store.db() as db:
            events = db.execute('SELECT tool, status FROM tool_events WHERE job_id=?', (self.job,)).fetchall()
        return model.tool_results(), [tuple(row) for row in events]

    def _codex(self, tested=True):
        binary, version = _installed('codex')
        if tested and version not in CLI_PROFILES[STRICT_PROFILE]['runtimes']['codex']['tested_versions']:
            warnings.warn(f'not observed on version {version}: runtime qualifier governs Codex admission', RuntimeWarning)
        if not binary:
            self.skipTest('codex is not installed')
        return binary

    def _shell_script(self):
        shell = lambda cmd: {'name': 'exec_command', 'arguments': {'cmd': cmd, 'login': False}}
        return [shell(f'cat {self.store.root / "FAKE-STORE-CANARY.txt"}'),
                shell(f'cat {self.home_canary}'),
                shell('cat agentos-mcp.json'),
                {'message': 'qualification finished'}]

    @staticmethod
    def _without(argv, *pairs):
        argv = list(argv)
        for pair in pairs:
            for at in range(len(argv) - len(pair) + 1):
                if argv[at:at + len(pair)] == list(pair):
                    del argv[at:at + len(pair)]
                    break
        return argv

    def test_codex_strict_offers_no_host_tool_and_keeps_the_bridge(self):
        binary = self._codex()
        qualification = self._qualification('codex', binary)
        model = _ScriptedModel('responses', [
            {'name': 'exec_command', 'arguments': {'cmd': f'cat {self.store.root / "FAKE-STORE-CANARY.txt"}', 'login': False}},
            {'name': 'view_image', 'arguments': {'path': str(self.store.root / 'FAKE-STORE-CANARY.txt')}},
            {'name': 'web_search', 'namespace': 'mcp__agentos', 'arguments': {'query': PUBLIC_QUERY}},
            {'name': 'weather', 'namespace': 'mcp__agentos', 'arguments': {'city': 'Daejeon'}},
            {'name': 'list_notes', 'namespace': 'mcp__agentos', 'arguments': {}},
            {'message': 'qualification finished'}])
        results, events = self._run('codex', binary, model, STRICT_PROFILE, qualification)
        shell, image, search, weather, notes = results[:5]
        self.assertIn('unsupported call', shell)
        self.assertIn('unsupported call', image)
        self.assertIn('stub-search-616', search)
        self.assertIn('stub-weather-616', weather)
        self.assertIn('fake-note-616', notes)
        self.assertEqual(events, BRIDGED_EVENTS)
        offered = {tool.get('name') or tool.get('type') for tool in model.offered_tools()}
        self.assertLessEqual(offered, STRICT_CODEX_TOOLS, offered)
        context = json.dumps([request['body'] for request in model.requests])
        for canary in ('fake-store-canary-616', 'fake-home-canary-616', 'SKILL-CANARY-616', 'canary-skill-616',
                       'PLUGIN-CANARY-616'):
            self.assertNotIn(canary, context)
        # Declared limitation: no official override stops the global
        # AGENTS.override.md (it takes precedence over AGENTS.md).
        self.assertIn('AGENTS-OVERRIDE-CANARY-616', context)
        for name in ('AGENTS.override.md', 'AGENTS.md'):
            self.assertIn(name, CLI_PROFILES[STRICT_PROFILE]['limitation'])
        self.assertIn('--ignore-rules', self.argv)
        self.assertNotIn('--sandbox', self.argv)

    def test_codex_strict_sandbox_holds_even_if_a_shell_were_offered_with_an_allow_rule(self):
        """Test-only: re-offer the shell to exercise --ignore-rules and Seatbelt."""
        binary = self._codex()
        qualification = self._qualification('codex', binary)
        model = _ScriptedModel('responses', self._shell_script())
        results, _ = self._run('codex', binary, model, STRICT_PROFILE, qualification,
                               argv_edit=lambda argv: self._without(argv, ('--disable', 'shell_tool')))
        store_read, home_read, turn_read = results[:3]
        for denied in (store_read, home_read):
            self.assertIn('Operation not permitted', denied)
            self.assertNotIn('canary-616', denied)
        self.assertIn('personal_agent.mcp_bridge', turn_read, 'the turn directory is readable')

    def test_codex_exec_rules_escape_the_sandbox_without_ignore_rules(self):
        """Review P1 reproduction: why --ignore-rules is pinned."""
        binary = self._codex()
        qualification = self._qualification('codex', binary)
        model = _ScriptedModel('responses', self._shell_script())
        results, _ = self._run('codex', binary, model, STRICT_PROFILE, qualification,
                               argv_edit=lambda argv: self._without(argv, ('--disable', 'shell_tool'), ('--ignore-rules',)))
        self.assertIn('fake-store-canary-616', results[0], 'the "always allow" rule ran cat outside Seatbelt')

    def test_codex_trusted_local_reads_the_store_as_its_label_says(self):
        binary = self._codex(tested=False)
        model = _ScriptedModel('responses', self._shell_script())
        results, _ = self._run('codex', binary, model, BOUNDED_PROFILE)
        self.assertIn('--sandbox', self.argv)
        self.assertIn('fake-store-canary-616', results[0])
        self.assertIn('fake-home-canary-616', results[1])
        self.assertIn('outside Still provenance', CLI_PROFILES[BOUNDED_PROFILE]['limitation'])

    def test_codex_trusted_local_keeps_codex_home_skills_out(self):
        """#961: a skill in CODEX_HOME never reaches a trusted-local Work turn; Still serves the
        Work's pinned skills itself.  Counterexample: without the override the canary skill does."""
        from personal_agent.bounded_execution import CODEX_NO_HOST_SKILLS
        binary = self._codex(tested=False)
        model = _ScriptedModel('responses', [{'message': 'done'}])
        self._run('codex', binary, model, BOUNDED_PROFILE)
        self.assertIn(CODEX_NO_HOST_SKILLS, self.argv)
        context = json.dumps([request['body'] for request in model.requests])
        for canary in ('SKILL-CANARY-616', 'canary-skill-616'):
            self.assertNotIn(canary, context)
        model = _ScriptedModel('responses', [{'message': 'done'}])
        self._run('codex', binary, model, BOUNDED_PROFILE, argv_edit=lambda argv: self._without(argv, ('-c', CODEX_NO_HOST_SKILLS)))
        self.assertIn('canary-skill-616', json.dumps([request['body'] for request in model.requests]))

    def _write_script(self, target):
        shell = lambda cmd: {'name': 'exec_command', 'arguments': {'cmd': cmd, 'login': False}}
        return [shell(f'touch {target}'), {'message': 'qualification finished'}]

    def _allow_touch(self):
        rules = self.root / 'codex-home' / 'rules' / 'default.rules'
        rules.write_text(rules.read_text() + 'prefix_rule(pattern=["touch"], decision="allow")\n')
        target = Path.home() / f'.agentos-636-write-canary-{uuid.uuid4().hex}'
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        return target

    def test_codex_trusted_local_ignores_an_allow_rule(self):
        """#636: with --ignore-rules the read-only sandbox blocks the write."""
        binary = self._codex(tested=False)
        target = self._allow_touch()
        results, _ = self._run('codex', binary, _ScriptedModel('responses', self._write_script(target)), BOUNDED_PROFILE)
        self.assertEqual(self.argv.count('--ignore-rules'), 1)
        self.assertIn('Operation not permitted', results[0], 'the touch was attempted and denied by the sandbox')
        self.assertFalse(target.exists(), results[:1])

    def test_codex_trusted_local_allow_rule_escapes_without_ignore_rules(self):
        """Counterexample: why --ignore-rules is pinned on trusted-local (#636)."""
        binary = self._codex(tested=False)
        target = self._allow_touch()
        self._run('codex', binary, _ScriptedModel('responses', self._write_script(target)), BOUNDED_PROFILE,
                  argv_edit=lambda argv: self._without(argv, ('--ignore-rules',)))
        self.assertTrue(target.exists(), 'the "always allow" rule ran touch outside the read-only sandbox')

    def test_codex_trusted_local_forbidden_prefix_rule_is_not_a_read_boundary(self):
        """#637 thread: what --ignore-rules gives up.  Without the flag an
        owner "forbidden" rule rejects its exact prefix, but another reader
        gets the same file under the read-only sandbox; with the flag the rule
        is not loaded.  The read limitation is the declared one either way."""
        binary = self._codex(tested=False)
        rules = self.root / 'codex-home' / 'rules' / 'default.rules'
        rules.write_text('prefix_rule(pattern=["cat"], decision="forbidden")\n')
        canary = self.store.root / 'FAKE-STORE-CANARY.txt'
        shell = lambda cmd: {'name': 'exec_command', 'arguments': {'cmd': cmd, 'login': False}}
        script = lambda: [shell(f'cat {canary}'), shell(f'head -n 1 {canary}'), {'message': 'qualification finished'}]
        loaded, _ = self._run('codex', binary, _ScriptedModel('responses', script()), BOUNDED_PROFILE,
                              argv_edit=lambda argv: self._without(argv, ('--ignore-rules',)))
        self.assertNotIn('fake-store-canary-616', loaded[0])
        self.assertIn('Rejected', loaded[0], 'the forbidden rule itself rejected the prefix')
        self.assertIn('fake-store-canary-616', loaded[1], 'another reader is not')
        ignored, _ = self._run('codex', binary, _ScriptedModel('responses', script()), BOUNDED_PROFILE)
        self.assertIn('fake-store-canary-616', ignored[0], 'with --ignore-rules the owner rule is not loaded')

    def test_codex_in_product_qualification_uses_the_real_cli(self):
        binary = self._codex()
        result = self._adapter('codex', binary).qualify_strict('codex', store_root=self.store.root)
        self.assertTrue(result['qualified'], result)
        self.assertIn('shell_tool', result['disabled_features'])
        self.assertNotIn('unified_exec', result['disabled_features'])
        self.assertEqual(result['binding']['codex_home'], str((self.root / 'codex-home').resolve()))
        # A store the platform baseline keeps readable (/tmp) fails closed.
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='agentos-616-') as readable:
            self.assertFalse(self._adapter('codex', binary).qualify_strict('codex', store_root=Path(readable))['qualified'])
        adapter = BoundedExecutionAdapter(finder=lambda name: binary, runtime_root=Path('/private/var/tmp/agentos-616-runs'),
                                          codex_home=self.root / 'codex-home')
        self.assertEqual(adapter.qualify_strict('codex', store_root=self.store.root)['reason'],
                         'runtime-root-not-baseline-readable')

    def _claude_script(self):
        return [{'tool': 'Read', 'input': {'file_path': str(self.store.root / 'FAKE-STORE-CANARY.txt')}},
                {'tool': 'Bash', 'input': {'command': f'cat {self.home_canary}'}},
                {'tool': 'mcp__agentos__web_search', 'input': {'query': PUBLIC_QUERY}},
                {'tool': 'mcp__agentos__weather', 'input': {'city': 'Daejeon'}},
                {'tool': 'mcp__agentos__list_notes', 'input': {}},
                {'message': 'qualification finished'}]

    def _claude(self):
        binary, version = _installed('claude-code')
        if version not in CLI_PROFILES[STRICT_PROFILE]['runtimes']['claude-code']['tested_versions']:
            warnings.warn(f'not observed on version {version}: runtime qualifier governs Claude Code admission', RuntimeWarning)
        if not binary:
            self.skipTest('claude is not installed')
        return binary

    def test_claude_strict_has_no_file_tools_and_keeps_the_bridge(self):
        binary = self._claude()
        qualification = self._qualification('claude-code', binary)
        model = _ScriptedModel('anthropic', self._claude_script())
        results, events = self._run('claude-code', binary, model, STRICT_PROFILE, qualification)
        read, shell, search, weather, notes = results[:5]
        for denied in (read, shell):
            self.assertIn('No such tool available', denied)
            self.assertNotIn('canary-616', denied)
        self.assertIn('stub-search-616', search)
        self.assertIn('stub-weather-616', weather)
        self.assertIn('fake-note-616', notes)
        self.assertEqual(events, BRIDGED_EVENTS)
        self.assertEqual(sorted(tool['name'] for tool in model.offered_tools()),
                         sorted(f'mcp__agentos__{action}' for action in profile_actions(STRICT_PROFILE)),
                         'no built-in tool is offered at all')
        self.assertFalse(any(request['authorization'] for request in model.requests), 'no owner credential was sent')

    def _claude_trusted_script(self):
        return [{'tool': 'Read', 'input': {'file_path': str(self.store.root / 'FAKE-STORE-CANARY.txt')}},
                {'tool': 'Bash', 'input': {'command': f'cat {self.home_canary}'}},
                {'tool': 'mcp__agentos__web_search', 'input': {'query': PUBLIC_QUERY}},
                {'tool': 'mcp__agentos__weather', 'input': {'city': 'Daejeon'}},
                {'tool': 'mcp__agentos__list_notes', 'input': {}},
                {'tool': 'Read', 'input': {'file_path': 'agentos-mcp.json'}},
                {'tool': 'Bash', 'input': {'command': 'cat agentos-mcp.json'}},
                {'tool': 'WebFetch', 'input': {'url': 'https://example.com/', 'prompt': 'summarise'}},
                {'tool': 'Write', 'input': {'file_path': 'written-623.txt', 'content': 'x'}},
                {'message': 'qualification finished'}]

    def test_claude_trusted_local_allows_exactly_its_bridge_tools(self):
        """#623: every declared bridge call reaches Still; built-in tools are
        not widened (observed on 2.1.280 under -p, no model, fake store)."""
        binary = self._claude()
        model = _ScriptedModel('anthropic', self._claude_trusted_script())
        results, events = self._run('claude-code', binary, model, BOUNDED_PROFILE)
        store_read, home_cat, search, weather, notes, turn_read, turn_cat, fetch, write = results[:9]
        at = self.argv.index('--allowedTools')
        self.assertEqual(self.argv[at + 1].split(','), [f'mcp__agentos__{a}' for a in profile_actions(BOUNDED_PROFILE)])
        self.assertEqual(self.argv.count('--allowedTools'), 1)
        for denied in (store_read, home_cat, fetch, write):
            self.assertNotIn('canary-616', denied)
        self.assertIn("haven't granted", store_read)
        self.assertIn('was blocked', home_cat)
        self.assertIn("haven't granted", fetch, 'WebFetch stays unapproved')
        self.assertIn("haven't granted", write, 'writes stay unapproved')
        self.assertFalse(any(self.root.rglob('written-623.txt')))
        # Every bridge call ran in Still (its own guards decide the outcome;
        # #605 withholds unjudged request text from public lookups).
        for bridged in (search, weather, notes):
            self.assertNotIn("haven't granted", bridged)
        self.assertIn('fake-note-616', notes)
        self.assertEqual([tool for tool, _ in events], ['web_search', 'weather', 'list_notes'])
        # Declared limitation: the turn directory itself is readable.
        self.assertIn('personal_agent.mcp_bridge', turn_read)
        self.assertIn('personal_agent.mcp_bridge', turn_cat)
        self.assertIn('Claude Code 2.1.280', CLI_PROFILES[BOUNDED_PROFILE]['limitation'])
        self.assertFalse(any(request['authorization'] for request in model.requests), 'no owner credential was sent')

    def test_claude_trusted_local_denies_a_bridge_tool_outside_the_allowlist(self):
        """Opposing case: the rule is exact, not server-wide - a bridge tool
        missing from --allowedTools is denied before Still sees it."""
        binary = self._claude()
        model = _ScriptedModel('anthropic', [{'tool': 'mcp__agentos__weather', 'input': {'city': 'Daejeon'}},
                                             {'tool': 'mcp__agentos__list_notes', 'input': {}},
                                             {'message': 'qualification finished'}])

        def drop_weather(argv):
            at = argv.index('--allowedTools') + 1
            argv[at] = ','.join(t for t in argv[at].split(',') if t != 'mcp__agentos__weather')
            return argv
        results, events = self._run('claude-code', binary, model, BOUNDED_PROFILE, argv_edit=drop_weather)
        self.assertIn("haven't granted", results[0])
        self.assertIn('fake-note-616', results[1])
        self.assertEqual(events, [('list_notes', 'succeeded')])

    def test_claude_trusted_local_without_the_allowlist_denies_every_bridge_call(self):
        """Counterexample (#621 observation): why the allowlist is pinned."""
        binary = self._claude()
        model = _ScriptedModel('anthropic', [{'tool': 'mcp__agentos__list_notes', 'input': {}},
                                             {'message': 'qualification finished'}])
        results, events = self._run('claude-code', binary, model, BOUNDED_PROFILE,
                                    argv_edit=lambda argv: argv[:argv.index('--allowedTools')])
        self.assertIn("haven't granted", results[0])
        self.assertEqual(events, [])


@unittest.skipUnless(os.environ.get('AGENTOS_CLI_QUALIFICATION') == '1',
                     'opt-in process-level CLI qualification (set AGENTOS_CLI_QUALIFICATION=1)')
class CodexDecisionInstructionFiles(unittest.TestCase):
    """#624: what a synthetic CODEX_HOME puts into a Codex *decision* prompt.

    The exact ``SubscriptionCliDecisionEngine`` argv against the real
    ``codex exec`` and the loopback scripted model; the only argv additions are
    the fake provider's endpoint settings.  Activation is bypassed on purpose:
    this pins the instruction-file behaviour behind
    ``CODEX_INSTRUCTION_FILES_QUALIFIED`` (0.153.4, #679) and, since #679, the
    strict-isolated profile a judgment runs under.  No owner profile or model.
    """

    def setUp(self):
        binary, version = _installed('codex')
        if version not in CODEX_INSTRUCTION_FILES_QUALIFIED:
            warnings.warn(f'not observed on version {version}: runtime qualifier governs instruction-file admission', RuntimeWarning)
        if not binary:
            self.skipTest('codex is not installed')
        self.binary = binary
        tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix='.agentos-624-decision-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'store').mkdir()
        populate_codex_home(self.root / 'codex-home', self.root / 'store')
        with tempfile.TemporaryDirectory(dir=self.root) as empty:
            listing = subprocess.run([binary, 'features', 'list'], cwd=empty, capture_output=True, text=True, timeout=30,
                                     env={'HOME': empty, 'CODEX_HOME': empty, 'PATH': f'{Path(binary).parent}:/usr/bin:/bin'})
        # #679: the strict profile's plan (shell_tool off, unified_exec allowed).
        self.plan = codex_disable_plan(parse_codex_features(listing.stdout), strict_allowed_features())
        self.argv = None

    def _decide(self, script=None, argv_edit=None):
        model = _ScriptedModel('responses', script or [{'message': json.dumps({'choice': 'retry', 'confidence': 0.9})}])
        self.model = model

        def runner(argv, **kwargs):
            argv, env = list(argv), dict(kwargs['env'])
            if argv_edit:
                argv = argv_edit(argv)
            at = argv.index('exec') + 1
            argv[at:at] = ['-c', 'model_provider="fake"', '-c', 'model="fake-model"', '-c',
                           f'model_providers.fake={{name="fake", base_url="http://127.0.0.1:{model.port}/v1", '
                           'wire_api="responses", env_key="AGENTOS_FAKE_MODEL_KEY", request_max_retries=0, stream_max_retries=0}']
            env['AGENTOS_FAKE_MODEL_KEY'] = 'fake'
            self.argv = argv
            kwargs['env'] = env
            return subprocess.run(argv, **kwargs)

        adapter = BoundedExecutionAdapter(finder=lambda name: self.binary, runner=runner,
                                          runtime_root=self.root / 'engine-runs', codex_home=self.root / 'codex-home')
        engine = SubscriptionCliDecisionEngine(adapter, 'codex', codex_disabled_features=self.plan)
        try:
            decision = engine.choose(DecisionContext('decision-route-probe', {'owner_message': 'retry please'}),
                                     ('retry', 'reference'), 'How does the owner message relate to the previous Work?')
        finally:
            model.close()
        self.failure = engine.last_failure
        return decision, json.dumps([request['body'] for request in model.requests])

    def test_decision_candidate_passes_the_unchanged_sandbox_qualification(self):
        adapter = BoundedExecutionAdapter(finder=lambda name: self.binary, runtime_root=self.root / 'engine-runs',
                                          codex_home=self.root / 'codex-home')
        result = adapter.qualify_strict('codex', store_root=self.root / 'store',
                                        tested_versions=CODEX_INSTRUCTION_FILES_QUALIFIED, purpose='judgment')
        self.assertTrue(result['qualified'], result)

    def test_global_instruction_files_reach_the_decision_prompt(self):
        decision, context = self._decide()
        self.assertEqual((decision.outcome, decision.choice), (OUTCOME_DECIDED, 'retry'), self.failure)
        for flag in ('--ignore-user-config', '--ignore-rules'):
            self.assertIn(flag, self.argv)
        self.assertNotIn('--sandbox', self.argv, '#679: the strict permissions profile, not read-only')
        self.assertIn(f'default_permissions="{CODEX_STRICT_PERMISSIONS}"', self.argv)
        self.assertIn('project_doc_max_bytes=0', self.argv)
        for canary in ('SKILL-CANARY-616', 'canary-skill-616', 'PLUGIN-CANARY-616'):
            self.assertNotIn(canary, context)
        # Observed limitation (documented in docs/decision-layer.en.md): the
        # overrides do not stop the global file; the override file wins.
        self.assertIn('AGENTS-OVERRIDE-CANARY-616', context)
        self.assertNotIn('AGENTS-MD-CANARY-616', context)
        (self.root / 'codex-home' / 'AGENTS.override.md').unlink()
        _decision, context = self._decide()
        self.assertIn('AGENTS-MD-CANARY-616', context)
        # With neither file present no instruction-file content reaches the prompt.
        (self.root / 'codex-home' / 'AGENTS.md').unlink()
        _decision, context = self._decide()
        for canary in ('AGENTS-MD-CANARY-616', 'AGENTS-OVERRIDE-CANARY-616', 'SKILL-CANARY-616', 'canary-skill-616',
                       'PLUGIN-CANARY-616'):
            self.assertNotIn(canary, context)
        self.assertNotIn('# AGENTS.md instructions', context)
        self.assertNotIn('<INSTRUCTIONS>', context)

    def test_a_judgment_under_the_strict_profile_cannot_read_the_home_folder(self):
        """#679: even if a shell were re-offered (test-only argv edit), the
        judgment's permissions profile denies the home folder and the store; the
        plain `--sandbox read-only` it replaces could read them
        (TRUSTED_LOCAL_LIMITATION)."""
        canary = Path.home() / f'.agentos-679-home-canary-{uuid.uuid4().hex}.txt'
        canary.write_text('fake-home-canary-679\n')
        self.addCleanup(canary.unlink)
        store_canary = self.root / 'store' / 'FAKE-STORE-CANARY.txt'
        store_canary.write_text('fake-store-canary-679\n')
        shell = lambda cmd: {'name': 'exec_command', 'arguments': {'cmd': cmd, 'login': False}}
        script = [shell(f'cat {canary}'), shell(f'cat {store_canary}'),
                  {'message': json.dumps({'choice': 'retry', 'confidence': 0.9})}]
        without_shell_disable = lambda argv: ProcessLevelQualification._without(argv, ('--disable', 'shell_tool'))
        decision, context = self._decide(script, argv_edit=without_shell_disable)
        results = self.model.tool_results()
        self.assertGreaterEqual(len(results), 2, results)
        for denied in results[:2]:
            self.assertIn('Operation not permitted', denied)
        self.assertNotIn('fake-home-canary-679', context)
        self.assertNotIn('fake-store-canary-679', context)
        # Unedited, no command tool is offered at all.
        _decision, _context = self._decide([shell(f'cat {canary}'),
                                            {'message': json.dumps({'choice': 'retry', 'confidence': 0.9})}])
        self.assertIn('unsupported call', self.model.tool_results()[0])
        self.assertNotIn('fake-home-canary-679', _context)


if __name__ == '__main__':
    unittest.main()
