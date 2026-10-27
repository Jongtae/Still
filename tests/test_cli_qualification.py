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

    options = SimpleNamespace(leak=False, host_tool=False, bridge=True, output=True, instructions=True)

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
        model.tools = ([{'name': 'exec_command'}] if options.host_tool else [])
        if work:
            denied = 'unsupported call' if engine == 'codex' else 'No such tool available'
            model.results = [denied + ' shell', denied + ' file', 'QUALIFIER-NOTE-346' if options.bridge else 'bridge unavailable']
            if engine != 'codex' and not options.host_tool:
                model.tools = [{'name': f'mcp__agentos__{action}'} for action in profile_actions(STRICT_PROFILE)]
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
