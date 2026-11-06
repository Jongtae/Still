"""Local CLI qualification fixtures shared by runtime admission and opt-in tests.

Only stdlib, synthetic state and a loopback scripted model. No owner credentials
or provider requests. A missing observation is a failure, never evidence of safety.
#673 owner decision: peer admission separately observes instruction exclusion
and the complete host relay, using only synthetic owner state.
"""
import json
from contextlib import ExitStack
import threading
import sys
import time
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class _ScriptedModel:
    """A loopback fake model endpoint that plays a fixed tool-call script.

    ``responses`` speaks the OpenAI Responses SSE shape Codex reads;
    ``anthropic`` speaks the Messages SSE shape Claude Code reads.  It records
    every request so a test can read the tool results the CLI sent back.
    """

    def __init__(self, dialect, script):
        self.dialect, self.script, self.requests, self.turn = dialect, list(script), [], 0
        model = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}')
                model.requests.append({'path': self.path, 'body': body,
                                       'authorization': 'authorization' in {k.lower() for k in self.headers.keys()}})
                events = model.events(self.path, body)
                if events is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('content-type', 'text/event-stream')
                self.end_headers()
                for name, data in events:
                    self.wfile.write(f'event: {name}\ndata: {json.dumps(data)}\n\n'.encode())
                self.wfile.flush()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _next(self):
        step = self.script[self.turn] if self.turn < len(self.script) else {'message': 'done'}
        self.turn += 1
        return step

    def events(self, path, body):
        if self.dialect == 'responses':
            if not path.endswith('/responses'):
                return None
            step = self._next()
            if 'message' in step:
                item = {'type': 'message', 'role': 'assistant', 'id': f'm{self.turn}',
                        'content': [{'type': 'output_text', 'text': step['message']}]}
            else:
                item = {'type': 'function_call', 'id': f'f{self.turn}', 'call_id': f'c{self.turn}',
                        'name': step['name'], 'arguments': json.dumps(step['arguments'])}
                if step.get('namespace'):
                    item['namespace'] = step['namespace']
            usage = {'input_tokens': 1, 'input_tokens_details': None, 'output_tokens': 1,
                     'output_tokens_details': None, 'total_tokens': 2}
            return [('response.created', {'type': 'response.created', 'response': {'id': f'r{self.turn}'}}),
                    ('response.output_item.done', {'type': 'response.output_item.done', 'output_index': 0, 'item': item}),
                    ('response.completed', {'type': 'response.completed', 'response': {'id': f'r{self.turn}', 'usage': usage}})]
        if '/v1/messages' not in path or 'count_tokens' in path:
            return None
        # Side requests (for example a title) carry no tools: answer plainly.
        step = self._next() if body.get('tools') else {'message': 'ok'}
        if 'message' in step:
            block, delta, stop = {'type': 'text', 'text': ''}, {'type': 'text_delta', 'text': step['message']}, 'end_turn'
        else:
            block = {'type': 'tool_use', 'id': f'toolu_{self.turn}', 'name': step['tool'], 'input': {}}
            delta, stop = {'type': 'input_json_delta', 'partial_json': json.dumps(step['input'])}, 'tool_use'
        usage = {'input_tokens': 1, 'output_tokens': 1}
        return [('message_start', {'type': 'message_start', 'message': {
                    'id': f'msg_{self.turn}', 'type': 'message', 'role': 'assistant', 'model': body.get('model', 'fake'),
                    'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': usage}}),
                ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': block}),
                ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': delta}),
                ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
                ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None},
                                   'usage': {'output_tokens': 1}}),
                ('message_stop', {'type': 'message_stop'})]

    def tool_results(self):
        """Tool results the CLI returned to the model, in call order."""
        last = self.requests[-1]['body'] if self.requests else {}
        if self.dialect == 'responses':
            results = []
            for request in self.requests:
                for item in request['body'].get('input', []):
                    if item.get('type') == 'function_call_output':
                        results.append(json.dumps(item.get('output')))
            return list(dict.fromkeys(results))
        return [json.dumps(part.get('content')) for message in last.get('messages', [])
                for part in (message.get('content') if isinstance(message.get('content'), list) else [])
                if part.get('type') == 'tool_result']

    def offered_tools(self):
        for request in self.requests:
            if request['body'].get('tools'):
                return request['body']['tools']
        return []


def populate_codex_home(home, store_root):
    """A synthetic, populated CODEX_HOME (review): never the owner's real one.

    An "always allow" exec rule, a global AGENTS.md, a skill, a plugin
    folder, a hooks file and a config.toml that would widen the sandbox.
    """
    home = Path(home)
    (home / 'rules').mkdir(parents=True)
    (home / 'rules' / 'default.rules').write_text(
        'prefix_rule(pattern=["cat"], decision="allow")\nprefix_rule(pattern=["ls"], decision="allow")\n')
    (home / 'AGENTS.md').write_text('AGENTS-MD-CANARY-616 global owner instructions\n')
    (home / 'AGENTS.override.md').write_text('AGENTS-OVERRIDE-CANARY-616 global owner override\n')
    skill = home / 'skills' / 'canary-skill'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: canary-skill-616\ndescription: SKILL-CANARY-616\n---\nSKILL-BODY-CANARY-616\n')
    plugin = home / 'plugins' / 'canary-plugin'
    plugin.mkdir(parents=True)
    (plugin / 'plugin.json').write_text(json.dumps({'name': 'canary-plugin-616', 'description': 'PLUGIN-CANARY-616'}))
    (home / 'hooks.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'hooks': [
        {'type': 'command', 'command': f'cat {store_root}/FAKE-STORE-CANARY.txt'}]}]}}))
    (home / 'config.toml').write_text('sandbox_mode = "danger-full-access"\napproval_policy = "never"\n')



# Tools a strict Codex call may expose without host filesystem or execution authority.
STRICT_CODEX_TOOLS = {'mcp__agentos', 'list_mcp_resources', 'list_mcp_resource_templates',
                      'read_mcp_resource', 'request_user_input'}


def qualification_timeout(cap, *, deadline=None, interrupted=None):
    """Check the caller's cancellation and share its monotonic deadline."""
    from .bounded_execution import EngineInterrupted
    reason = interrupted() if interrupted else None
    if reason:
        raise EngineInterrupted(reason)
    if deadline is None:
        return cap
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EngineInterrupted('deadline_exceeded')
    return min(cap, remaining)


def qualification_run(adapter, argv, *, cwd, env, timeout, deadline=None, interrupted=None):
    """Reuse the process-group kill path for every local qualification probe."""
    from .bounded_execution import bounded_run
    timeout = qualification_timeout(timeout, deadline=deadline, interrupted=interrupted)
    try:
        done = bounded_run(adapter.runner, argv, cwd=cwd, env=env, timeout=timeout, interrupted=interrupted)
    except subprocess.TimeoutExpired:
        # A probe's own cap is a failed observation; a spent caller budget is
        # an interrupted turn and must escape qualification's failure cache.
        qualification_timeout(timeout, deadline=deadline, interrupted=interrupted)
        raise
    qualification_timeout(timeout, deadline=deadline, interrupted=interrupted)
    return done


def _loopback_run(adapter, engine_id, binary, argv, run_dir, model, *, deadline=None, interrupted=None, peer=False):
    """Only the scripted loopback model is added to the exact production argv."""
    qualification_timeout(45, deadline=deadline, interrupted=interrupted)
    argv = list(argv)
    with ExitStack() as scope:
        env = (scope.enter_context(adapter.peer_environment_scope(engine_id, binary, run_dir, interrupted=interrupted, deadline=deadline)) if peer
               else adapter.environment(engine_id, binary, run_dir))
        if engine_id == 'codex':
            argv[2:2] = ['-c', 'model_provider="fake"', '-c', 'model="fake-model"', '-c',
                f'model_providers.fake={{name="fake", base_url="http://127.0.0.1:{model.port}/v1", '
                'wire_api="responses", env_key="AGENTOS_FAKE_MODEL_KEY", request_max_retries=0, stream_max_retries=0}']
            env['AGENTOS_FAKE_MODEL_KEY'] = 'fake'
        else:
            env.update(ANTHROPIC_BASE_URL=f'http://127.0.0.1:{model.port}', ANTHROPIC_API_KEY='fake-key',
                       CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
        done = qualification_run(adapter, argv, cwd=run_dir, env=env, timeout=45,
                                 deadline=deadline, interrupted=interrupted)
        if done.returncode != 0:
            raise ValueError('scripted CLI turn failed')
        adapter._content(engine_id, done.stdout)  # crashes/unparseable output never count as success
        if not model.requests:
            raise ValueError('no scripted model request observed')
        return json.dumps([request['body'] for request in model.requests])


def _work_bridge_check(adapter, engine_id, binary, root, disabled, *, deadline=None, interrupted=None):
    """Retry a bridge round trip that did not complete; security is observed on every attempt.

    Codex may build its first request before the bridge MCP server is ready
    (#346, live on 0.164.0). A missed round trip is retried, at most three
    attempts in all; any security defect refuses at once.
    """
    for attempt in range(3):
        qualification_timeout(45, deadline=deadline, interrupted=interrupted)
        attempt_root = root / f'work-attempt-{attempt}'
        attempt_root.mkdir()
        safe, reached = _work_bridge_attempt(adapter, engine_id, binary, attempt_root, disabled,
                                            deadline=deadline, interrupted=interrupted)
        if not safe or reached:
            break
    return {'check': 'strict-work-bridge-and-tool-surface', 'passed': bool(safe and reached)}


def _work_bridge_attempt(adapter, engine_id, binary, root, disabled, *, deadline=None, interrupted=None):
    """Relevant #616 process checks: no host tools, bridge round trip, no canary leak.

    Reuse Still's existing temporary store and MCP bridge, with only list_notes;
    no public network tool or owner data is exercised by this probe.
    """
    qualification_timeout(45, deadline=deadline, interrupted=interrupted)
    from .bounded_execution import STRICT_PROFILE, profile_actions
    from .quickstart_store import QuickStore
    run_dir = root / 'work-surface'
    run_dir.mkdir()
    store = QuickStore(root / 'synthetic-store')
    with store.db() as db:
        db.execute("INSERT INTO notes VALUES ('q346','QUALIFIER-NOTE-346',1)")
    job = store.enqueue('Local isolation probe', 'qualifier-346')
    with store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
    target = store.root / 'home-canary.txt'
    target.write_text('QUALIFIER-PRIVATE-CANARY-346')
    config = run_dir / 'agentos-mcp.json'
    config.write_text(json.dumps({'mcpServers': {'agentos': {
        'command': sys.executable, 'env': {'CLAUDE_CODE_OAUTH_TOKEN': ''},
        'args': ['-m', 'personal_agent.mcp_bridge', '--data', str(store.root), '--job', job,
                 f'--profile={STRICT_PROFILE}']}}}))
    script = ([{'name': 'exec_command', 'arguments': {'cmd': f'cat {target}', 'login': False}},
               {'name': 'view_image', 'arguments': {'path': str(target)}},
               {'name': 'list_notes', 'namespace': 'mcp__agentos', 'arguments': {}}] if engine_id == 'codex' else
              [{'tool': 'Bash', 'input': {'command': f'cat {target}'}},
               {'tool': 'Read', 'input': {'file_path': str(target)}},
               {'tool': 'mcp__agentos__list_notes', 'input': {}}])
    model = _ScriptedModel('responses' if engine_id == 'codex' else 'anthropic',
                           [*script, {'message': 'qualification finished'}])
    try:
        argv = adapter.command(engine_id, binary, 'Local isolation qualification.', config,
                               profile=STRICT_PROFILE, disabled_features=disabled)
        context = _loopback_run(adapter, engine_id, binary, argv, run_dir, model,
                                deadline=deadline, interrupted=interrupted)
        results = model.tool_results()
        denied = 'unsupported call' if engine_id == 'codex' else 'No such tool available'
        actions = set(profile_actions(STRICT_PROFILE))
        safe_tools, bridge_offered = True, False
        # Inspect every request, including catalogs that change after startup.
        for request in model.requests:
            for tool in request['body'].get('tools', []):
                name = tool.get('name') or tool.get('type')
                if engine_id == 'codex':
                    safe_tools &= name in STRICT_CODEX_TOOLS
                    if name == 'mcp__agentos':
                        names = {item.get('name') for item in tool.get('tools', [])}
                        safe_tools &= names <= actions
                        bridge_offered |= 'list_notes' in names
                else:
                    safe_tools &= name in {f'mcp__agentos__{action}' for action in actions}
                    bridge_offered |= name == 'mcp__agentos__list_notes'
        safe = bool(safe_tools and len(results) >= 2 and all(denied in item for item in results[:2])
                    and all(marker not in context for marker in
                            ('QUALIFIER-PRIVATE-CANARY-346', 'SKILL-CANARY-616', 'canary-skill-616',
                             'SKILL-BODY-CANARY-616', 'PLUGIN-CANARY-616')))
        reached = bridge_offered and len(results) >= 3 and 'QUALIFIER-NOTE-346' in results[2]
        return safe, reached
    finally:
        model.close()


def surface_checks(adapter, engine_id, binary, root, disabled, *, include_work=True, deadline=None, interrupted=None):
    """Shared runtime/process harness; every property must be observed.

    All state is synthetic under the runtime root, with empty credentials and
    loopback endpoints. Raw request bodies die with the temporary fixture.
    """
    qualification_timeout(45, deadline=deadline, interrupted=interrupted)
    from .decision_adapters import SubscriptionCliDecisionEngine
    home = Path(adapter.codex_home) if engine_id == 'codex' else None
    if home is not None:
        populate_codex_home(home, root / 'synthetic-store')
    checks = [_work_bridge_check(adapter, engine_id, binary, root, disabled,
                                 deadline=deadline, interrupted=interrupted)] if include_work else []
    if engine_id != 'codex':
        return checks
    expected = [('AGENTS-OVERRIDE-CANARY-616', 'AGENTS-MD-CANARY-616'),
                ('AGENTS-MD-CANARY-616', 'AGENTS-OVERRIDE-CANARY-616'), ('', '')]
    for index, (present, absent) in enumerate(expected):
        qualification_timeout(45, deadline=deadline, interrupted=interrupted)
        if index == 1:
            (home / 'AGENTS.override.md').unlink()
        elif index == 2:
            (home / 'AGENTS.md').unlink()
        model = _ScriptedModel('responses', [{'message': json.dumps({'choice': 'retry', 'confidence': 0.9})}])
        try:
            run_dir = root / f'surface-{index}'
            run_dir.mkdir()
            engine = SubscriptionCliDecisionEngine(adapter, engine_id, codex_disabled_features=disabled)
            schema = {'type': 'object', 'properties': {'choice': {'type': 'string'},
                      'confidence': {'type': 'number'}}, 'required': ['choice', 'confidence'],
                      'additionalProperties': False}
            schema_path = run_dir / 'schema.json'
            schema_path.write_text(json.dumps(schema))
            argv = engine.argv(binary, 'Local isolation qualification. Reply retry.', schema_path, schema)
            context = _loopback_run(adapter, engine_id, binary, argv, run_dir, model,
                                    deadline=deadline, interrupted=interrupted)
            offered = {tool.get('name') or tool.get('type') for tool in model.offered_tools()}
            forbidden = ['SKILL-CANARY-616', 'canary-skill-616', 'PLUGIN-CANARY-616', 'SKILL-BODY-CANARY-616']
            if index == 2:
                forbidden += ['AGENTS-MD-CANARY-616', 'AGENTS-OVERRIDE-CANARY-616', '# AGENTS.md instructions', '<INSTRUCTIONS>']
            checks.append({'check': f'instruction-files-{index}',
                           'passed': bool(all(marker not in context for marker in forbidden)
                                          and (not present or present in context) and (not absent or absent not in context)
                                          and offered <= STRICT_CODEX_TOOLS)})
        finally:
            model.close()
    return checks


def peer_instruction_canaries(root, run_dir, store):
    """#673 pass 2 owner decision: control only synthetic locations, never real ancestors.

    Fresh HOME is run_dir. All cwd ancestors through root, user/project
    instructions, rules and settings below it are ours. Ancestors above root
    and OS-managed settings are not ours and have no documented exclusion in
    the restricted, non-safe-mode Claude launch. A clean probe cannot certify
    those uncontrolled inputs.
    """
    root, run_dir = Path(root).resolve(), Path(run_dir).resolve()
    if root != run_dir and root not in run_dir.parents:
        raise ValueError('instruction canaries require a controlled fixture root')
    markers = []
    directory = run_dir
    while directory == root or root in directory.parents:
        marker = 'CLAUDE-ANCESTOR-CANARY-673-' + str(len(markers))
        (directory / 'CLAUDE.md').write_text(marker)
        hidden = directory / '.claude'
        hidden.mkdir(exist_ok=True)
        (hidden / 'CLAUDE.md').write_text(marker + '-HIDDEN')
        (hidden / 'settings.json').write_text(json.dumps({'env': {'STILL_PROBE_CANARY': marker + '-SETTINGS'}}))
        (hidden / 'settings.local.json').write_text(json.dumps({'env': {'STILL_PROBE_CANARY': marker + '-LOCAL'}}))
        (hidden / 'rules').mkdir(exist_ok=True)
        (hidden / 'rules' / 'canary.md').write_text(marker + '-RULE')
        markers.append(marker)
        directory = directory.parent
    # There is no advertised HOME-relative override for the real platform's
    # managed settings location. Do not write /Library, /etc, or owner files.
    (store.root / 'CLAUDE.md').write_text('CLAUDE-STORE-CANARY-673')
    return markers + ['CLAUDE-STORE-CANARY-673']


def peer_surface_checks(adapter, engine_id, binary, root, disabled, *, deadline=None, interrupted=None):
    """#673 owner decision: observe an auth-only peer launch and the real relay.

    Synthetic homes, store, credentials and scripted loopback model only.
    Missing MCP, unsupported flags, extra tools or any ambient instruction
    canary refuse this binary. No saved ordinary strict pass substitutes.
    """
    from .bounded_execution import RelayedWorkMcpTools, STRICT_PROFILE
    from .agent_runtime import Capabilities, WorkBudget
    from .cli_browser_relay import BrowserRelay
    from .quickstart_store import QuickStore
    run_dir = root / 'peer-surface'
    run_dir.mkdir()
    store = QuickStore(root / 'peer-store')
    with store.db() as db:
        db.execute("INSERT INTO notes VALUES ('private','PEER-PRIVATE-CANARY-673',1)")
    instruction_markers = peer_instruction_canaries(root, run_dir, store)
    if engine_id == 'codex':
        populate_codex_home(adapter.codex_home, store.root)
        auth = Path(adapter.codex_home) / 'auth.json'
        auth.write_text('{"OPENAI_API_KEY":"synthetic-not-used"}')
        auth.chmod(0o600)
    records, calls = [], []
    class Scoped(Capabilities):
        peer_guard = staticmethod(lambda: None)
        def execute(self, name, args):
            calls.append(name)
            return super().execute(name, args)
    caps = Scoped(store, None, {}, '', 'peer-probe', lambda *row: records.append(row),
                  allowed_tools={'list_memory'}, delegated=True, budget=WorkBudget(), secret_redactor=lambda text: text)
    tools = RelayedWorkMcpTools(caps)
    relay = BrowserRelay(tools, full_surface=True)
    script = ([{'name': 'exec_command', 'arguments': {'cmd': 'pwd'}},
               {'name': 'view_image', 'arguments': {'path': str(store.root)}},
               {'name': 'list_notes', 'namespace': 'mcp__agentos', 'arguments': {}},
               {'name': 'list_memory', 'namespace': 'mcp__agentos', 'arguments': {}}] if engine_id == 'codex' else
              [{'tool': 'Bash', 'input': {'command': 'pwd'}},
               {'tool': 'Read', 'input': {'file_path': str(store.root)}},
               {'tool': 'mcp__agentos__list_notes', 'input': {}},
               {'tool': 'mcp__agentos__list_memory', 'input': {}}])
    model = None
    try:
        config = run_dir / 'mcp.json'
        config.write_text(json.dumps({'mcpServers': {'agentos': {'command': sys.executable,
            'env': {'PYTHONPATH': str(Path(__file__).resolve().parents[1]), 'CLAUDE_CODE_OAUTH_TOKEN': ''},
            'args': ['-m', 'personal_agent.mcp_bridge', '--data', str(store.root), '--job', 'peer-probe',
                     f'--profile={STRICT_PROFILE}', f'--peer-relay={relay.address}']}}}))
        model = _ScriptedModel('responses' if engine_id == 'codex' else 'anthropic',
                               [*script, {'message': 'qualification finished'}])
        argv = adapter.command(engine_id, binary, 'Local peer qualification.', config,
                               instructions='Answer only from the supplied peer context.',
                               profile=STRICT_PROFILE, disabled_features=disabled, peer_tools=['list_memory'])
        context = _loopback_run(adapter, engine_id, binary, argv, run_dir, model, peer=True,
                                deadline=deadline, interrupted=interrupted)
        safe, offered = True, False
        for request in model.requests:
            for tool in request['body'].get('tools', []):
                name = tool.get('name') or tool.get('type')
                if engine_id == 'codex':
                    safe &= name in STRICT_CODEX_TOOLS
                    if name == 'mcp__agentos':
                        names = {item.get('name') for item in tool.get('tools', [])}
                        safe &= names == {'list_memory'}
                        offered |= names == {'list_memory'}
                else:
                    safe &= name == 'mcp__agentos__list_memory'
                    offered |= name == 'mcp__agentos__list_memory'
        forbidden = ('PEER-PRIVATE-CANARY-673', 'AGENTS-MD-CANARY-616', 'AGENTS-OVERRIDE-CANARY-616',
                     'SKILL-CANARY-616', 'SKILL-BODY-CANARY-616', 'PLUGIN-CANARY-616',
                     *instruction_markers)
        results = model.tool_results()
        denied = 'unsupported call' if engine_id == 'codex' else 'No such tool available'
        passed = bool(safe and offered and calls == ['list_memory'] and len(results) >= 4
                      and all(denied in value for value in results[:2])
                      and all(marker not in context for marker in forbidden))
        checks = [{'check': 'peer-instructions-and-relay', 'passed': passed}]
        if engine_id == 'claude-code':
            checks.append({'check': 'peer-instruction-locations-covered', 'passed': False,
                           'uncontrolled': ['ancestors-above-fixture', 'platform-managed-settings']})
        return checks
    finally:
        if model is not None:
            model.close()
        relay.close()


def failure_message(result):
    """Plain owner-facing reason on the existing execution/route failure paths."""
    from .messages import msg
    code = result.get('reason') or 'local-check-unavailable'
    labels = []
    for check in code.split(', '):
        label = ('platform' if check == 'tested-platform' else
                 'root' if check == 'runtime-root-not-baseline-readable' else
                 'version' if check == 'reported-version' else
                 'sandbox' if check in ('turn-directory-readable', 'sibling-turn-denied', 'protected-directory-denied') else
                 'features' if check == 'tool-features-allowlisted' else
                 'surface' if check in ('strict-work-bridge-and-tool-surface', 'strict-tool-surface') else
                 'instructions' if check.startswith(('instruction-files', 'peer-instruction')) else
                 'identity' if check in ('binary-unchanged', 'binding-unchanged', 'native-binary-identified') else 'local')
        labels.append(msg('engine.check_' + label))
    errors = sorted({check['error'] for check in result.get('checks', ()) if check.get('error') and not check.get('passed')})
    if errors:
        return msg('engine.local_qualification_unavailable', check=', '.join(dict.fromkeys(labels)),
                   code=code, error=', '.join(errors))
    return msg('engine.local_qualification_failed', check=', '.join(dict.fromkeys(labels)), code=code)
