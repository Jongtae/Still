"""#346: exercise shared runtime assertions without a real CLI or socket."""
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import pytest

from personal_agent import cli_qualification as qualifier
from personal_agent.bounded_execution import BoundedExecutionAdapter, profile_actions, STRICT_PROFILE, ExecutionError


@pytest.fixture
def harness(tmp_path):
    models, launches = [], []
    home = tmp_path / 'synthetic-codex'
    home.mkdir()
    binary = str(tmp_path / 'codex')
    (tmp_path / 'codex').write_bytes(b'\x7fELFfake')

    class Model:
        def __init__(self, dialect, script):
            self.dialect, self.script = dialect, script
            self.requests, self.results, self.tools = [], [], []
            self.port, self.closed = 1234, False
            models.append(self)

        def close(self):
            self.closed = True

        def tool_results(self):
            return self.results

        def offered_tools(self):
            return self.tools

    options = SimpleNamespace(leak=False, host_tool=False, bridge=True, output=True, instructions=True,
                              bridge_offered=True, refused=True, later_host_tool=False, extra_bridge_tool=False,
                              attempts=[])

    def runner(argv, **kwargs):
        model = models[-1]
        engine = 'codex' if 'exec' in argv else 'claude-code'
        launches.append((argv, kwargs))
        # This is an injected CLI; synthesize its observed requests from only its synthetic home.
        instruction = ''
        if engine == 'codex' and options.instructions:
            profile = Path(kwargs['env']['CODEX_HOME'])
            for name in ('AGENTS.override.md', 'AGENTS.md'):
                if (profile / name).exists():
                    instruction = (profile / name).read_text()
                    break
        if options.leak:
            instruction += 'SKILL-CANARY-616'
        model.requests = [{'body': {'input': instruction}}]
        work = '--output-schema' not in argv
        if work and options.attempts:
            for name, value in options.attempts.pop(0).items():
                setattr(options, name, value)
        if work and options.leak:
            model.requests[0]['body']['input'] += 'QUALIFIER-PRIVATE-CANARY-346'
        model.tools = ([{'name': 'exec_command'}] if options.host_tool else [])
        if work:
            denied = 'unsupported call' if engine == 'codex' else 'No such tool available'
            model.results = [denied + ' shell' if options.refused else 'shell succeeded', denied + ' file',
                             'QUALIFIER-NOTE-346' if options.bridge else 'bridge unavailable']
            if options.bridge_offered:
                # Production hides propose_current_state while current context is off.
                actions = [a for a in profile_actions(STRICT_PROFILE) if a != 'propose_current_state']
                if options.extra_bridge_tool:
                    actions.append('read_file')
                model.tools += ([{'type': 'namespace', 'name': 'mcp__agentos',
                                  'tools': [{'type': 'function', 'name': a} for a in actions]}] if engine == 'codex' else
                                [{'name': f'mcp__agentos__{a}'} for a in actions])
        model.requests[0]['body']['tools'] = model.tools
        if work and options.later_host_tool:
            model.requests.append({'body': {'tools': [{'name': 'exec_command' if engine == 'codex' else 'Bash'}]}})
        out = (json.dumps({'item': {'type': 'agent_message', 'text': 'done'}}) if engine == 'codex' else
               json.dumps({'type': 'result', 'result': 'done'})) if options.output else 'unparseable'
        return SimpleNamespace(returncode=0, stdout=out, stderr='')

    adapter = BoundedExecutionAdapter(runner=runner, codex_home=home, runtime_root=tmp_path,
                                     credentials=lambda engine: pytest.fail('owner credentials consulted'))
    # Runtime replaces this credential source before calling the harness; this fixture models that seam.
    adapter.credentials = lambda engine: ''
    with patch.object(qualifier, '_ScriptedModel', Model):
        yield SimpleNamespace(adapter=adapter, root=tmp_path, binary=binary, models=models,
                              launches=launches, options=options)


def run(h, engine='codex'):
    return qualifier.surface_checks(h.adapter, engine, h.binary, h.root, ['shell_tool'])


def test_shared_harness_checks_all_instruction_states_and_bridge(harness):
    h = harness
    checks = run(h)
    assert all(check['passed'] for check in checks)
    assert {check['check'] for check in checks} == {'strict-work-bridge-and-tool-surface',
                                                  'instruction-files-0', 'instruction-files-1', 'instruction-files-2'}
    assert len(h.launches) == 4
    for argv, kwargs in h.launches:
        assert kwargs['env']['CODEX_HOME'] == str(h.adapter.codex_home)
        assert kwargs['env']['AGENTOS_FAKE_MODEL_KEY'] == 'fake'
        assert str(kwargs['cwd']).startswith(str(h.root))
        assert '--ignore-user-config' in argv and '--ignore-rules' in argv
        assert kwargs['shell'] is False and kwargs['timeout'] == 45
    assert all(model.closed for model in h.models)


@pytest.mark.parametrize('defect', ['leak', 'host_tool', 'bridge', 'instructions'])
def test_missing_property_is_a_named_failure(harness, defect):
    h = harness
    setattr(h.options, defect, defect in ('leak', 'host_tool'))
    checks = run(h)
    assert any(not check['passed'] for check in checks)
    assert all(model.closed for model in h.models)


def test_unparseable_output_is_not_a_pass_and_always_closes_server(harness):
    h = harness
    h.options.output = False
    with pytest.raises(ExecutionError):
        run(h)
    assert h.models[0].closed


def test_claude_harness_uses_strict_work_flags_and_only_fake_key(harness):
    h = harness
    assert run(h, 'claude-code') == [{'check': 'strict-work-bridge-and-tool-surface', 'passed': True}]
    argv, kwargs = h.launches[0]
    assert argv[argv.index('--tools') + 1] == ''
    assert '--restricted' in argv and '--strict-mcp-config' in argv
    assert kwargs['env']['ANTHROPIC_BASE_URL'] == 'http://127.0.0.1:1234'
    assert kwargs['env']['ANTHROPIC_API_KEY'] == 'fake-key'
    assert 'CLAUDE_CODE_OAUTH_TOKEN' not in kwargs['env']


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_bridge_startup_race_retries_only_the_work_turn(harness, engine):
    h = harness
    h.options.attempts = [dict(bridge_offered=False, bridge=False), dict(bridge_offered=True, bridge=True)]
    checks = run(h, engine)
    assert all(check['passed'] for check in checks)
    assert len(h.launches) == (5 if engine == 'codex' else 2)
    assert all(model.closed for model in h.models)


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_unreachable_offered_bridge_retries(harness, engine):
    h = harness
    h.options.attempts = [dict(bridge=False), dict(bridge=True)]
    assert all(check['passed'] for check in run(h, engine))
    assert len(h.launches) == (5 if engine == 'codex' else 2)


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
@pytest.mark.parametrize('defect', ['leak', 'host_tool', 'later_host_tool', 'extra_bridge_tool', 'refused'])
@pytest.mark.parametrize('attempt', [0, 1, 2])
def test_security_defect_on_any_attempt_refuses_without_retry(harness, engine, defect, attempt):
    h = harness
    h.options.attempts = [dict(bridge=False)] * attempt + [{defect: defect != 'refused', 'bridge': False}]
    check = qualifier._work_bridge_check(h.adapter, engine, h.binary, h.root, ['shell_tool'])
    assert check == {'check': 'strict-work-bridge-and-tool-surface', 'passed': False}
    assert len(h.launches) == attempt + 1
    assert all(model.closed for model in h.models)


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_bridge_retry_exhaustion_is_a_named_failure(harness, engine):
    h = harness
    h.options.bridge_offered = h.options.bridge = False
    check = qualifier._work_bridge_check(h.adapter, engine, h.binary, h.root, ['shell_tool'])
    assert check == {'check': 'strict-work-bridge-and-tool-surface', 'passed': False}
    assert len(h.launches) == 3
    assert all(model.closed for model in h.models)


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
@pytest.mark.parametrize('reason', ['stopped', 'deadline_exceeded'])
def test_no_bridge_retry_starts_after_caller_ends(harness, monkeypatch, engine, reason):
    from personal_agent.bounded_execution import EngineInterrupted
    h = harness
    now, stopped = [100.0], [False]
    monkeypatch.setattr(qualifier.time, 'monotonic', lambda: now[0])
    h.options.bridge = False
    original = qualifier._work_bridge_attempt
    attempts = []
    def attempt(*args, **kwargs):
        attempts.append(args)
        result = original(*args, **kwargs)
        if reason == 'stopped':
            stopped[0] = True
        else:
            now[0] = 101.0
        return result
    monkeypatch.setattr(qualifier, '_work_bridge_attempt', attempt)
    with pytest.raises(EngineInterrupted) as caught:
        qualifier.surface_checks(h.adapter, engine, h.binary, h.root, ['shell_tool'], deadline=101.0,
                                 interrupted=lambda: 'stopped' if stopped[0] else None)
    assert caught.value.reason == reason
    assert len(attempts) == len(h.launches) == 1
    assert all(model.closed for model in h.models)


def test_loopback_probes_share_remaining_time(harness, monkeypatch):
    h = harness
    now = [100.0]
    monkeypatch.setattr(qualifier.time, 'monotonic', lambda: now[0])
    original = h.adapter.runner
    def run(argv, **kwargs):
        assert kwargs['timeout'] == 110.0 - now[0]
        now[0] += 0.25
        return original(argv, **kwargs)
    h.adapter.runner = run
    assert all(check['passed'] for check in qualifier.surface_checks(
        h.adapter, 'codex', h.binary, h.root, ['shell_tool'], deadline=110.0))
    assert [kwargs['timeout'] for _, kwargs in h.launches] == [10.0, 9.75, 9.5, 9.25]


@pytest.mark.parametrize('include_work', [True, False])
def test_stop_during_loopback_probe_kills_real_process_and_starts_no_later_probe(harness, monkeypatch, include_work):
    import os
    import subprocess
    import sys
    import time
    from personal_agent import bounded_execution as be
    h = harness
    pid_file = h.root / 'probe.pid'
    script = (f'from pathlib import Path; import os, time; '
              f'Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)')
    argv = [sys.executable, '-c', script]
    # Keep the actual process boundary; replace only the CLI with a sleeping
    # Python child and the scripted HTTP server with the socket-free fixture.
    h.adapter.runner = subprocess.run
    monkeypatch.setattr(h.adapter, 'command', lambda *args, **kwargs: argv)
    monkeypatch.setattr(h.adapter, 'environment', lambda *args, **kwargs: {})
    if not include_work:
        from personal_agent.decision_adapters import SubscriptionCliDecisionEngine
        monkeypatch.setattr(SubscriptionCliDecisionEngine, 'argv', lambda *args, **kwargs: argv)
    original = be.bounded_run
    calls = []
    def run(runner, cli_argv, **kwargs):
        calls.append(kwargs)
        # Codex loopback configuration arguments are immaterial to this child.
        return original(runner, argv, **kwargs)
    monkeypatch.setattr(be, 'bounded_run', run)
    started = time.monotonic()
    with pytest.raises(be.EngineInterrupted) as caught:
        qualifier.surface_checks(h.adapter, 'claude-code' if include_work else 'codex', h.binary,
                                 h.root, ['shell_tool'], include_work=include_work,
                                 deadline=started + 5,
                                 interrupted=lambda: 'stopped' if pid_file.exists() else None)
    assert caught.value.reason == 'stopped'
    assert time.monotonic() - started < 3
    assert len(calls) == len(h.models) == 1
    assert calls[0]['interrupted'] is not None and 0 < calls[0]['timeout'] <= 5
    assert h.models[0].closed
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
