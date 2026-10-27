"""Local CLI qualification fixtures shared by runtime admission and opt-in tests.

Only stdlib, synthetic state and a loopback scripted model. No owner credentials
or provider requests. A missing observation is a failure, never evidence of safety.
"""
import json
import threading
import sys
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


def _loopback_run(adapter, engine_id, binary, argv, run_dir, model):
    """Only the scripted loopback model is added to the exact production argv."""
    from .bounded_execution import bounded_run
    argv = list(argv)
    env = adapter.environment(engine_id, binary, run_dir)
    if engine_id == 'codex':
        argv[2:2] = ['-c', 'model_provider="fake"', '-c', 'model="fake-model"', '-c',
            f'model_providers.fake={{name="fake", base_url="http://127.0.0.1:{model.port}/v1", '
            'wire_api="responses", env_key="AGENTOS_FAKE_MODEL_KEY", request_max_retries=0, stream_max_retries=0}']
        env['AGENTOS_FAKE_MODEL_KEY'] = 'fake'
    else:
        env.update(ANTHROPIC_BASE_URL=f'http://127.0.0.1:{model.port}', ANTHROPIC_API_KEY='fake-key',
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
    done = bounded_run(adapter.runner, argv, cwd=run_dir, env=env, timeout=45)
    if done.returncode != 0:
        raise ValueError('scripted CLI turn failed')
    adapter._content(engine_id, done.stdout)  # crashes/unparseable output never count as success
    if not model.requests:
        raise ValueError('no scripted model request observed')
    return json.dumps([request['body'] for request in model.requests])


def _work_bridge_check(adapter, engine_id, binary, root, disabled):
    """Retry only an unavailable bridge, with security observed on every attempt."""
    for attempt in range(3):
        attempt_root = root / f'work-attempt-{attempt}'
        attempt_root.mkdir()
        safe, reached, unavailable = _work_bridge_attempt(adapter, engine_id, binary, attempt_root, disabled)
        if not safe or reached or not unavailable:
            break
    return {'check': 'strict-work-bridge-and-tool-surface', 'passed': bool(safe and reached)}


def _work_bridge_attempt(adapter, engine_id, binary, root, disabled):
    """Relevant #616 process checks: no host tools, bridge round trip, no canary leak.

    Reuse Still's existing temporary store and MCP bridge, with only list_notes;
    no public network tool or owner data is exercised by this probe.
    """
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
        context = _loopback_run(adapter, engine_id, binary, argv, run_dir, model)
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
        unavailable = not bridge_offered or (len(results) >= 3 and any(
            marker in results[2] for marker in
            (denied, 'tool call failed', 'Connection closed', 'Transport closed', 'bridge unavailable')))
        return safe, reached, unavailable
    finally:
        model.close()


def surface_checks(adapter, engine_id, binary, root, disabled, *, include_work=True):
    """Shared runtime/process harness; every property must be observed.

    All state is synthetic under the runtime root, with empty credentials and
    loopback endpoints. Raw request bodies die with the temporary fixture.
    """
    from .decision_adapters import SubscriptionCliDecisionEngine
    home = Path(adapter.codex_home) if engine_id == 'codex' else None
    if home is not None:
        populate_codex_home(home, root / 'synthetic-store')
    checks = [_work_bridge_check(adapter, engine_id, binary, root, disabled)] if include_work else []
    if engine_id != 'codex':
        return checks
    expected = [('AGENTS-OVERRIDE-CANARY-616', 'AGENTS-MD-CANARY-616'),
                ('AGENTS-MD-CANARY-616', 'AGENTS-OVERRIDE-CANARY-616'), ('', '')]
    for index, (present, absent) in enumerate(expected):
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
            context = _loopback_run(adapter, engine_id, binary, argv, run_dir, model)
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
                 'instructions' if check.startswith('instruction-files') else
                 'identity' if check in ('binary-unchanged', 'binding-unchanged', 'native-binary-identified') else 'local')
        labels.append(msg('engine.check_' + label))
    errors = sorted({check['error'] for check in result.get('checks', ()) if check.get('error') and not check.get('passed')})
    if errors:
        return msg('engine.local_qualification_unavailable', check=', '.join(dict.fromkeys(labels)),
                   code=code, error=', '.join(errors))
    return msg('engine.local_qualification_failed', check=', '.join(dict.fromkeys(labels)), code=code)
