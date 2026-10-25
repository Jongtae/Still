"""#308 S0: what each subscription CLI does with an owner message sent while it is still answering.

Opt-in process-level fixtures (``AGENTOS_CLI_QUALIFICATION=1``), like
``test_strict_isolation.ProcessLevelQualification``: the real installed CLI, a
loopback scripted model that holds its first answer until the second owner
message was written, a fake store and a synthetic ``CODEX_HOME``.  No live
model, account or owner data.  They pin the observed behaviour the native-steer
slices (#308 S2/S3) build on, for the versions in ``OBSERVED``; another version
is skipped until these are re-run for it.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from personal_agent.bounded_execution import BOUNDED_PROFILE, BoundedExecutionAdapter
from personal_agent.quickstart_store import QuickStore

from test_strict_isolation import _ScriptedModel, _installed

ROOT = Path(__file__).resolve().parents[1]
#: The CLI versions these observations were made on (2026-10-25).
OBSERVED = {'claude-code': '2.1.287', 'codex': '0.153.4'}
FIRST = 'STEER-308 first: what is a good brunch?'
SECOND = 'STEER-308 second: there is a department store right in front of my house'
#: How long the held first answer waits for the test, and how long the CLI gets
#: to read the second message from its input before the first answer is released.
HOLD_SECONDS, SETTLE_SECONDS, TURN_SECONDS = 30, 1.0, 60


class _HeldModel(_ScriptedModel):
    """``_ScriptedModel`` whose first scripted answer waits until ``release`` is set."""

    def __init__(self, dialect, script):
        self.release, self.held = threading.Event(), threading.Event()
        super().__init__(dialect, script)

    def _next(self):
        if self.turn == 0:
            self.held.set()
            self.release.wait(HOLD_SECONDS)
        return super()._next()


def _wait(condition, seconds, what):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError(f'timed out waiting for {what}')


def _kill(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def _lines(process, into):
    def read():
        for line in process.stdout:
            try:
                into.append(json.loads(line))
            except ValueError:
                pass
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return thread


@unittest.skipUnless(os.environ.get('AGENTOS_CLI_QUALIFICATION') == '1',
                     'opt-in process-level CLI qualification (set AGENTOS_CLI_QUALIFICATION=1)')
class ClaudeStreamJsonSteer(unittest.TestCase):
    """Still's own trusted-local Claude Code argv, with the prompt on stream-json input (#308 S2)."""

    def setUp(self):
        self.binary, version = _installed('claude-code')
        if not self.binary:
            self.skipTest('claude is not installed')
        if version != OBSERVED['claude-code']:
            self.skipTest(f'claude {version} is not the observed version {OBSERVED["claude-code"]}')
        tmp = tempfile.TemporaryDirectory(prefix='agentos-308-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = QuickStore(root / 'data')
        with store.db() as db:
            db.execute("INSERT INTO notes VALUES ('n1','fake-note-308',1)")
        job = store.enqueue(FIRST, 'q-308')
        with store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        self.store, self.job, self.root = store, job, root
        self.run_dir = root / 'turn'
        self.run_dir.mkdir()
        config = self.run_dir / 'agentos-mcp.json'
        # The bridge entry BoundedExecutionAdapter._execute writes for a trusted-local turn.
        config.write_text(json.dumps({'mcpServers': {'agentos': {
            'command': sys.executable, 'env': {'CLAUDE_CODE_OAUTH_TOKEN': ''},
            'args': ['-m', 'personal_agent.mcp_bridge', '--data', str(store.root), '--job', job,
                     f'--profile={BOUNDED_PROFILE}', '--session-steers']}}}))
        adapter = BoundedExecutionAdapter(finder=lambda name: self.binary, credentials=lambda engine: '',
                                          runtime_root=root / 'runs')
        slot = 'PROMPT-SLOT-308'
        self.argv = adapter.command('claude-code', self.binary, slot, config, 'Still steer qualification.')
        # #308 S2: Still's own argv; the prompt is stream-json input, never an argv element.
        self.assertEqual(self.argv[1:8], ['-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
                                          '--verbose', '--replay-user-messages'])
        self.assertNotIn(slot, self.argv)
        self.env = adapter.environment('claude-code', self.binary, self.run_dir)

    def _session(self, model):
        """Send FIRST, then SECOND while the model holds its first answer; return the output records."""
        env = dict(self.env, ANTHROPIC_BASE_URL=f'http://127.0.0.1:{model.port}', ANTHROPIC_API_KEY='fake-key',
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
        process = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   cwd=self.run_dir, env=env, text=True, start_new_session=True)
        self.addCleanup(_kill, process)
        self.addCleanup(model.close)
        records = []
        reader = _lines(process, records)

        def send(text):
            process.stdin.write(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': text}}) + '\n')
            process.stdin.flush()

        send(FIRST)
        _wait(model.held.is_set, HOLD_SECONDS, 'the first model request')
        send(SECOND)
        time.sleep(SETTLE_SECONDS)
        model.release.set()
        # #308 S2's answer rule: the answer is the result after the echo of the last owner message.
        _wait(lambda: self._answer(records) is not None, TURN_SECONDS, 'a result after the second echo')
        process.stdin.close()
        process.wait(timeout=TURN_SECONDS)
        reader.join(timeout=5)
        return records

    @staticmethod
    def _echo(records, text):
        return next((at for at, record in enumerate(records) if record.get('type') == 'user'
                     and record.get('message', {}).get('content') == text), None)

    def _answer(self, records):
        echo = self._echo(records, SECOND)
        if echo is None:
            return None
        return next((record for record in records[echo:] if record.get('type') == 'result'), None)

    @staticmethod
    def _results(records):
        return [at for at, record in enumerate(records) if record.get('type') == 'result']

    def test_a_message_sent_before_a_tool_result_reaches_the_model_after_it_in_the_same_answer(self):
        model = _HeldModel('anthropic', [{'tool': 'mcp__agentos__list_notes', 'input': {}},
                                         {'message': 'one answer for both'}])
        records = self._session(model)
        results = self._results(records)
        self.assertEqual(len(results), 1, 'one turn, one result')
        self.assertLess(self._echo(records, SECOND), results[0], 'the CLI read the message inside the turn')
        self.assertEqual(records[results[0]]['result'], 'one answer for both')
        self.assertIn('fake-note-308', model.tool_results()[0], 'the Still bridge served the call on the stream-json argv')
        # With a bridge (MCP) tool the message follows the tool result as its own message in the same
        # request; with the built-in Read it was observed inside the tool_result text instead.
        after_tool = json.dumps([request for request in model.requests if request['body'].get('tools')][-1]['body']['messages'])
        self.assertLess(after_tool.index('"tool_result"'), after_tool.index(SECOND),
                        'the owner message reached the model right after the tool result, in the same turn')

    def test_a_message_sent_while_the_final_text_is_written_runs_as_the_next_turn_of_the_same_session(self):
        model = _HeldModel('anthropic', [{'message': 'stale answer'}, {'message': 'answer with the new context'}])
        records = self._session(model)
        results = self._results(records)
        self.assertEqual(len(results), 2)
        self.assertLess(results[0], self._echo(records, SECOND), 'the first turn ended before the message was read')
        self.assertEqual(records[results[0]]['result'], 'stale answer')
        self.assertEqual(self._answer(records)['result'], 'answer with the new context')
        follow_up = [request for request in model.requests if request['body'].get('tools')][-1]['body']['messages']
        text = json.dumps(follow_up)
        self.assertLess(text.index(FIRST), text.index('stale answer'))
        self.assertLess(text.index('stale answer'), text.index(SECOND), 'the next turn sees the earlier exchange')


    def _through_still(self, model):
        """#308 S2 end to end: ``BoundedExecutionAdapter.execute``; SECOND is queued as a steer while the model holds."""
        from test_claude_session_steer import _Budget, _Caps
        from personal_agent.agent_runtime import STEER_EVENT
        from personal_agent.bounded_execution import AgentOSMcpTools

        class LocalModel(BoundedExecutionAdapter):
            def environment(self, *args, **kwargs):
                return {**super().environment(*args, **kwargs), 'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{model.port}',
                        'ANTHROPIC_API_KEY': 'fake-key', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
        self.addCleanup(model.close)
        adapter = LocalModel(finder=lambda name: self.binary, credentials=lambda engine: '', runtime_root=self.root / 'runs')
        outcome = {}

        def run():
            try:
                outcome['result'] = adapter.execute('claude-code', FIRST, AgentOSMcpTools(_Caps(self.store, self.job, _Budget(TURN_SECONDS))))
            except Exception as exc:  # reported below
                outcome['error'] = exc
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        _wait(model.held.is_set, HOLD_SECONDS, 'the first model request')
        source = self.store.enqueue(SECOND, 'q-308-steer')
        self.assertTrue(self.store.add_steer(self.job, source, SECOND))
        _wait(lambda: self.store.steer_delivered(source), 10, 'the session pump to claim the steer')
        time.sleep(SETTLE_SECONDS)
        model.release.set()
        thread.join(TURN_SECONDS)
        self.assertNotIn('error', outcome, outcome.get('error'))
        with self.store.db() as db:
            events = [row['status'] for row in db.execute('SELECT status FROM tool_events WHERE job_id=? AND tool=? ORDER BY id',
                                                          (self.job, STEER_EVENT))]
        return outcome['result'], events, source

    def test_still_delivers_a_steer_written_mid_answer_as_the_next_turn_and_answers_with_it(self):
        model = _HeldModel('anthropic', [{'message': 'stale answer'}, {'message': 'answer with the new context'}])
        result, events, source = self._through_still(model)
        self.assertEqual(result.content, 'answer with the new context')
        self.assertEqual(events, ['delivered', 'accepted'])
        self.assertTrue(self.store.steer_delivered(source), 'claimed, accepted and not released')
        self.assertIn(SECOND, json.dumps(model.requests[-1]['body']['messages'], ensure_ascii=False))

    def test_still_delivers_a_steer_after_the_next_bridge_tool_result_in_one_answer(self):
        model = _HeldModel('anthropic', [{'tool': 'mcp__agentos__list_notes', 'input': {}},
                                         {'message': 'one answer for both'}])
        result, events, _source = self._through_still(model)
        self.assertEqual(result.content, 'one answer for both')
        self.assertEqual(events, ['delivered', 'accepted'])
        self.assertIn('fake-note-308', model.tool_results()[0], 'the Still bridge served the call')
        self.assertNotIn(SECOND, model.tool_results()[0], 'the session bridge leaves the steer to stdin')


class _Rpc:
    """A minimal JSON-RPC client over a Codex app-server's stdio (initialize handshake done)."""

    def __init__(self, argv, env, cwd):
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        cwd=cwd, env=env, text=True, start_new_session=True)
        self.responses, self.events, self.next_id = {}, [], 0
        threading.Thread(target=self._read, daemon=True).start()
        self.call('initialize', {'clientInfo': {'name': 'agentos-308', 'version': '0'}})
        self._send({'jsonrpc': '2.0', 'method': 'initialized'})

    def _read(self):
        for line in self.process.stdout:
            message = json.loads(line)
            if 'id' in message and ('result' in message or 'error' in message):
                self.responses[message['id']] = message
            else:
                self.events.append(message)

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def call(self, method, params):
        self.next_id += 1
        request = self.next_id
        self._send({'jsonrpc': '2.0', 'id': request, 'method': method, 'params': params})
        _wait(lambda: request in self.responses, 30, method)
        return self.responses[request]

    def items(self, kind):
        return [event['params']['item'] for event in self.events
                if event.get('method') == 'item/completed' and event['params']['item'].get('type') == kind]

    def completed(self):
        return [event for event in self.events if event.get('method') == 'turn/completed']

    def close(self):
        _kill(self.process)


@unittest.skipUnless(os.environ.get('AGENTOS_CLI_QUALIFICATION') == '1',
                     'opt-in process-level CLI qualification (set AGENTOS_CLI_QUALIFICATION=1)')
class CodexAppServerSteer(unittest.TestCase):
    """Codex ``app-server`` ``turn/steer`` and its profile isolation (#308 S3 gate)."""

    def setUp(self):
        self.binary, version = _installed('codex')
        if not self.binary:
            self.skipTest('codex is not installed')
        if version != OBSERVED['codex']:
            self.skipTest(f'codex {version} is not the observed version {OBSERVED["codex"]}')
        tmp = tempfile.TemporaryDirectory(prefix='agentos-308-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home, self.cwd = self.root / 'codex-home', self.root / 'turn'
        self.home.mkdir()
        self.cwd.mkdir()

    def _provider(self, model):
        return ['-c', 'model_provider="fake"', '-c', 'model="fake-model"', '-c',
                f'model_providers.fake={{name="fake", base_url="http://127.0.0.1:{model.port}/v1", '
                'wire_api="responses", env_key="AGENTOS_FAKE_MODEL_KEY", request_max_retries=0, stream_max_retries=0}',
                '-c', 'web_search="disabled"']

    def _env(self):
        return {'HOME': str(self.cwd), 'PATH': f'{Path(self.binary).parent}:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                'CODEX_HOME': str(self.home), 'AGENTOS_FAKE_MODEL_KEY': 'fake'}

    def _server(self, model, *extra):
        rpc = _Rpc([self.binary, 'app-server', *self._provider(model), *extra], self._env(), self.cwd)
        self.addCleanup(rpc.close)
        self.addCleanup(model.close)
        return rpc

    def _thread(self, rpc, **params):
        started = rpc.call('thread/start', {'cwd': str(self.cwd), 'approvalPolicy': 'never', 'sandbox': 'read-only',
                                            'ephemeral': True, **params})
        return started['result']['thread']['id']

    def test_a_steer_while_the_final_answer_is_written_stays_in_the_same_turn(self):
        model = _HeldModel('responses', [{'message': 'stale answer'}, {'message': 'answer with the new context'}])
        rpc = self._server(model)
        thread = self._thread(rpc)
        turn = rpc.call('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': FIRST}]})['result']['turn']['id']
        _wait(model.held.is_set, HOLD_SECONDS, 'the first model request')
        steered = rpc.call('turn/steer', {'threadId': thread, 'expectedTurnId': turn, 'clientUserMessageId': 'steer-308',
                                          'input': [{'type': 'text', 'text': SECOND}]})
        self.assertEqual(steered['result']['turnId'], turn)
        # Success only queued it: the model has not been asked again yet.
        self.assertEqual(len(model.requests), 1)
        model.release.set()
        _wait(lambda: rpc.completed(), TURN_SECONDS, 'turn/completed')
        time.sleep(SETTLE_SECONDS)
        self.assertEqual(len(rpc.completed()), 1, 'one turn')
        self.assertEqual([item['text'] for item in rpc.items('agentMessage')], ['stale answer', 'answer with the new context'])
        steer_item = [item for item in rpc.items('userMessage') if item['content'][0]['text'] == SECOND]
        self.assertEqual([item.get('clientId') for item in steer_item], ['steer-308'],
                         'the steer comes back as a correlated user item: the receipt S3 waits for')
        follow_up = json.dumps(model.requests[-1]['body']['input'])
        self.assertLess(follow_up.index('stale answer'), follow_up.index(SECOND))

    def test_a_steer_without_an_active_turn_is_refused(self):
        model = _ScriptedModel('responses', [{'message': 'done'}])
        rpc = self._server(model)
        thread = self._thread(rpc)
        turn = rpc.call('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': FIRST}]})['result']['turn']['id']
        _wait(lambda: rpc.completed(), TURN_SECONDS, 'turn/completed')
        late = rpc.call('turn/steer', {'threadId': thread, 'expectedTurnId': turn, 'input': [{'type': 'text', 'text': SECOND}]})
        self.assertIn('error', late)
        self.assertIn('no active turn', json.dumps(late['error']))

    def _sentinel_profile(self):
        """A CODEX_HOME whose config.toml names an MCP server that leaves a mark when it starts."""
        mark = self.root / 'SENTINEL-STARTED'
        script = f'open({str(mark)!r}, "w").write("x"); import time; time.sleep(30)'
        (self.home / 'config.toml').write_text(f'[mcp_servers.sentinel]\ncommand = {json.dumps(sys.executable)}\n'
                                               f'args = ["-c", {json.dumps(script)}]\n')
        (self.home / 'AGENTS.md').write_text('AGENTS-MD-CANARY-308\n')
        return mark

    def _probe(self, *extra, **thread_params):
        mark = self._sentinel_profile()
        mark.unlink(missing_ok=True)
        model = _ScriptedModel('responses', [{'message': 'done'}])
        rpc = self._server(model, *extra)
        thread = self._thread(rpc, **thread_params)
        rpc.call('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': 'probe'}]})
        _wait(lambda: rpc.completed(), TURN_SECONDS, 'turn/completed')
        time.sleep(SETTLE_SECONDS)
        rpc.close()
        model.close()
        return mark.exists(), 'AGENTS-MD-CANARY-308' in json.dumps([r['body'] for r in model.requests])

    def test_app_server_starts_the_profiles_mcp_server_that_exec_ignore_user_config_leaves_out(self):
        """The #308 S0 isolation result for 0.153.4: S3 stays closed.

        No app-server flag, ``-c mcp_servers={}`` or ``thread/start`` config keeps
        a profile's MCP server from starting; only naming it does
        (``mcp_servers.<name>.enabled=false``), which is a subtraction of
        servers Still would have to know.  ``exec --ignore-user-config`` with
        the same profile starts none.  The global AGENTS.md reaches the model
        on both entry points (the known #624 limitation, unchanged).
        """
        for extra, params in (((), {}), (('-c', 'mcp_servers={}'), {}), ((), {'config': {'mcp_servers': {}}})):
            started, agents_md = self._probe(*extra, **params)
            self.assertTrue(started, f'app-server {extra} {params}: profile MCP server started')
            self.assertTrue(agents_md)
        started, _ = self._probe('-c', 'mcp_servers.sentinel.enabled=false')
        self.assertFalse(started, 'naming the server disables it')
        mark = self._sentinel_profile()
        model = _ScriptedModel('responses', [{'message': 'done'}])
        self.addCleanup(model.close)
        done = subprocess.run([self.binary, 'exec', '--json', '--sandbox', 'read-only', '--ignore-rules',
                               '--skip-git-repo-check', '--ignore-user-config', '--ephemeral', *self._provider(model),
                               'probe'], cwd=self.cwd, env=self._env(), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=TURN_SECONDS)
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        time.sleep(SETTLE_SECONDS)
        self.assertFalse(mark.exists(), 'exec --ignore-user-config starts no profile MCP server')


if __name__ == '__main__':
    unittest.main()
