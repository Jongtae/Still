"""#308 S2: a Claude Code turn is a stream-json session that takes the owner's steers live.

Model-free.  ``SessionAnswer`` drives ``ClaudeSteerSession`` with exact line
orders (the races are deterministic there).  ``SessionProcess`` runs the real
``bounded_run`` supervisor over a scripted stand-in for ``claude`` that reads
stream-json input and echoes each message as Claude Code 2.1.287 does with
``--replay-user-messages`` (pinned against the real CLI by the opt-in
``test_steer_cli_qualification``).  No model, account or owner data.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from personal_agent import bounded_execution, mcp_bridge
from personal_agent.agent_runtime import STEER_EVENT, STEER_LEAD
from personal_agent.bounded_execution import (STRICT_PROFILE, AgentOSMcpTools, BoundedExecutionAdapter,
                                              ClaudeSteerSession, ExecutionError, session_message)
from personal_agent.quickstart_store import QuickStore

FIRST = '브런치 하기 좋은 곳 알려줘'
REMARK = '집 바로 앞에 백화점이 있어'
LATER = '야외 자리면 좋겠어'


def _result(text):
    return json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': text,
                       'num_turns': 1, 'usage': {'output_tokens': len(text)}})


def _echo(text):
    return json.dumps({'type': 'user', 'isReplay': True, 'message': {'role': 'user', 'content': text}})


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class SessionAnswer(unittest.TestCase):
    """Which result answers the turn, when stdin closes, and what is released."""

    def setUp(self):
        read, write = os.pipe()
        self.reader = os.fdopen(read, 'rb')
        self.stdin = os.fdopen(write, 'wb')
        os.set_blocking(write, False)
        self.addCleanup(self.reader.close)
        self.addCleanup(lambda: self.stdin.closed or self.stdin.close())
        self.notes, self.claims, self.accepted, self.released = [], [], [], []
        self.clock = _Clock()

    def _claim(self):
        self.claims.append(self.clock())
        return self.notes.pop(0) if self.notes else None

    def _session(self):
        return ClaudeSteerSession(FIRST, claim=self._claim, accepted=lambda: self.accepted.append(True),
                                  release=lambda since, count: self.released.append((since, count)),
                                  clock=self.clock, wall=self.clock)

    def _written(self):
        self.stdin.closed or self.stdin.flush()
        data = b''
        os.set_blocking(self.reader.fileno(), False)
        try:
            while True:
                chunk = os.read(self.reader.fileno(), 65536)
                if not chunk:
                    break
                data += chunk
        except BlockingIOError:
            pass
        return [json.loads(line)['message']['content'] for line in data.decode().splitlines()]

    def test_with_no_steer_the_first_result_answers_and_stdin_closes(self):
        session = self._session()
        session.pump(self.stdin)
        self.assertEqual(self._written(), [FIRST])
        for line in (_echo(FIRST), _result('답')):
            session.on_line(line)
        session.pump(self.stdin)
        self.assertTrue(self.stdin.closed)
        self.assertEqual(json.loads(session.answer_line())['result'], '답')
        session.finish()
        self.assertEqual(self.released, [])

    def test_a_steer_echoed_before_the_result_is_answered_by_that_result(self):
        session = self._session()
        self.notes.append(REMARK)
        session.pump(self.stdin)
        self.assertEqual(self._written(), [FIRST, REMARK])
        for line in (_echo(FIRST), _echo(REMARK), _result('하나의 답')):
            session.on_line(line)
        self.assertEqual(self.accepted, [True], 'accepted only on the echo')
        session.pump(self.stdin)
        self.assertTrue(self.stdin.closed)
        self.assertEqual(json.loads(session.answer_line())['result'], '하나의 답')
        session.finish()
        self.assertEqual(self.released, [])

    def test_a_result_that_races_ahead_of_the_echo_is_not_the_answer(self):
        session = self._session()
        self.notes.append(REMARK)
        session.pump(self.stdin)
        for line in (_echo(FIRST), _result('예전 답')):
            session.on_line(line)
        self.assertIsNone(session.answer)
        session.pump(self.stdin)
        self.assertFalse(self.stdin.closed, 'stdin stays open for the echo of the steer it carries')
        self.assertEqual(self.accepted, [])
        for line in (_echo(REMARK), _result('새 답')):
            session.on_line(line)
        session.pump(self.stdin)
        self.assertTrue(self.stdin.closed)
        self.assertEqual(json.loads(session.answer_line())['result'], '새 답')

    def test_two_steers_are_two_writes_and_the_answer_follows_the_last_echo(self):
        session = self._session()
        self.notes.append(REMARK)
        session.pump(self.stdin)
        session.on_line(_echo(FIRST))
        session.on_line(_echo(REMARK))
        self.clock.now += 1
        self.notes.append(LATER)
        session.pump(self.stdin)
        self.assertEqual(self._written(), [FIRST, REMARK, LATER])
        session.on_line(_result('첫 말만 본 답'))
        session.on_line(_echo(LATER))
        session.on_line(_result('둘 다 본 답'))
        self.assertEqual(self.accepted, [True, True])
        session.pump(self.stdin)
        self.assertEqual(json.loads(session.answer_line())['result'], '둘 다 본 답')
        session.finish()
        self.assertEqual(self.released, [])

    def test_no_steer_is_claimed_or_written_after_the_answer(self):
        session = self._session()
        session.pump(self.stdin)
        session.on_line(_echo(FIRST))
        session.on_line(_result('답'))
        self.notes.append(REMARK)
        claims = len(self.claims)
        session.pump(self.stdin)
        session.pump(self.stdin)
        self.assertEqual(len(self.claims), claims, 'the steer stays unclaimed for the finish guard (S1)')
        self.assertEqual(self.notes, [REMARK])
        self.assertTrue(self.stdin.closed)

    def test_a_process_that_ends_before_the_echo_releases_the_steer_and_keeps_the_last_result(self):
        session = self._session()
        self.notes.append(REMARK)
        session.pump(self.stdin)
        claimed = self.claims[-1]
        for line in (_echo(FIRST), _result('예전 답')):
            session.on_line(line)
        session.finish()
        self.assertEqual(json.loads(session.answer_line())['result'], '예전 답')
        self.assertEqual(self.released, [(claimed, 1)])
        self.assertEqual(self.accepted, [])
        session.finish()
        self.assertEqual(len(self.released), 1, 'released once')

    def test_an_echoed_steer_no_result_followed_is_released(self):
        session = self._session()
        self.notes.append(REMARK)
        session.pump(self.stdin)
        for line in (_echo(FIRST), _result('예전 답'), _echo(REMARK)):
            session.on_line(line)
        session.finish()
        self.assertEqual(len(self.released), 1, 'accepted is not answered')

    def test_an_echo_that_never_comes_closes_stdin_after_the_bound(self):
        session = self._session()
        self.notes.append(REMARK)
        session.pump(self.stdin)
        session.on_line(_result('예전 답'))
        self.clock.now += bounded_execution.STEER_ECHO_SECONDS + 1
        self.notes.append(LATER)
        claims = len(self.claims)
        session.pump(self.stdin)
        self.assertTrue(self.stdin.closed)
        self.assertEqual(len(self.claims), claims)

    def test_a_broken_pipe_closes_the_session_and_releases_what_was_written(self):
        session = self._session()
        self.reader.close()
        self.notes.append(REMARK)
        session.pump(self.stdin)
        self.assertTrue(session.closed)
        session.finish()
        self.assertEqual(len(self.released), 1)

    def test_an_echo_that_is_not_the_steer_is_not_acceptance(self):
        session = self._session()
        self.notes.append(REMARK)
        session.pump(self.stdin)
        session.on_line(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': REMARK}}))
        session.on_line(_echo('다른 말'))
        session.on_line(json.dumps({'type': 'user', 'isReplay': True,
                                    'message': {'content': [{'type': 'tool_result', 'content': REMARK}]}}))
        self.assertEqual(self.accepted, [])


#: The stand-in for ``claude``: it reads stream-json input, echoes each message
#: it reads, and follows ``plan.json`` beside it (the closed env carries nothing).
FAKE_CLI = r'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
plan = json.load(open(os.path.join(here, 'plan.json')))
def emit(record):
    sys.stdout.write(json.dumps(record) + '\n'); sys.stdout.flush()
emit({'type': 'system', 'subtype': 'init', 'model': 'fake', 'mcp_servers': [{'name': 'agentos', 'status': 'connected'}]})
for step in plan:
    kind, _, value = step.partition(':')
    if kind == 'read':
        line = sys.stdin.readline()
        if not line:
            break
        emit({'type': 'user', 'isReplay': True, 'message': json.loads(line)['message']})
    elif kind == 'result':
        emit({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': value, 'num_turns': 1})
    elif kind == 'touch':
        open(os.path.join(here, value), 'w').close()
    elif kind == 'sleep':
        time.sleep(float(value))
    elif kind == 'eof':
        for line in sys.stdin:
            message = json.loads(line)['message']
            emit({'type': 'user', 'isReplay': True, 'message': message})
            emit({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'late: ' + message['content'][:20]})
    elif kind == 'exit':
        sys.exit(int(value))
'''


class _Caps:
    """The Work's capabilities as the facade reads them: a real store, job and Evidence record."""

    def __init__(self, store, job_id, budget=None):
        from personal_agent.manifests import runtime_packages
        self.store, self.job_id, self.budget = store, job_id, budget
        self.tools = {tool['id']: tool for package in runtime_packages([]) for tool in package['tools']}

    def definitions(self):
        from personal_agent.agent_runtime import action_definitions
        return action_definitions(self.tools, set(self.tools))

    def record(self, tool, status, detail):
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (self.job_id, tool, status, detail, time.time()))


class _Budget:
    def __init__(self, seconds=30, stop=None):
        self.seconds, self.stop = seconds, stop

    def remaining(self):
        return self.seconds

    def interrupted(self):
        return self.stop() if self.stop else None


class SessionProcess(unittest.TestCase):
    """The real supervisor over a scripted CLI process: steers on stdin, the answer, release."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agentos-308-s2-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.cli = self.bin / 'claude'
        self.cli.write_text(f'#!{sys.executable}\n{FAKE_CLI}')
        self.cli.chmod(0o755)
        self.store = QuickStore(self.root / 'data')
        self.job = self.store.enqueue(FIRST, 'k-1')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (self.job,))
        self.adapter = BoundedExecutionAdapter(finder=lambda name: str(self.cli), runtime_root=self.root / 'runs')
        self.adapter.bridge_check = lambda: None

    def _plan(self, *steps):
        (self.bin / 'plan.json').write_text(json.dumps(list(steps)))

    def _steer(self, text, key):
        source = self.store.enqueue(text, key)
        self.assertTrue(self.store.add_steer(self.job, source, text))
        return source

    def _run(self, budget=None):
        return self.adapter.execute('claude-code', FIRST, AgentOSMcpTools(_Caps(self.store, self.job, budget)))

    def _events(self):
        with self.store.db() as db:
            return [row['status'] for row in db.execute('SELECT status FROM tool_events WHERE job_id=? AND tool=? ORDER BY id',
                                                        (self.job, STEER_EVENT))]

    def _claimed(self, source):
        with self.store.db() as db:
            return db.execute('SELECT delivered_at FROM work_steers WHERE source_job_id=?', (source,)).fetchone()['delivered_at']

    def test_a_steer_read_inside_the_turn_gives_one_answer(self):
        source = self._steer(REMARK, 'k-2')
        self._plan('read', 'read', 'result:하나의 답', 'eof')
        result = self._run()
        self.assertEqual(result.content, '하나의 답')
        self.assertEqual(self._events(), ['delivered', 'accepted'])
        self.assertIsNotNone(self._claimed(source), 'claimed once, with its receipt')
        self.assertEqual(self.store.claim_steers(self.job), [], 'never handed over a second time')

    def test_a_result_ahead_of_the_echo_is_followed_by_the_next_turn(self):
        self._steer(REMARK, 'k-2')
        self._plan('read', 'result:예전 답', 'read', 'result:새 답', 'eof')
        result = self._run()
        self.assertEqual(result.content, '새 답')
        self.assertEqual(self._events(), ['delivered', 'accepted'])

    def test_a_process_that_exits_before_the_echo_releases_the_steer(self):
        source = self._steer(REMARK, 'k-2')
        self._plan('read', 'result:예전 답', 'exit:0')
        result = self._run()
        self.assertEqual(result.content, '예전 답')
        self.assertEqual(self._events(), ['delivered', 'released'])
        self.assertIsNone(self._claimed(source), 'back to the finish guard (S1)')
        self.assertEqual(self.store.unanswered_steers(self.job, time.time()), [REMARK])

    def test_two_steers_reach_one_session_as_two_messages(self):
        self._steer(REMARK, 'k-2')
        self._plan('read', 'read', 'touch:first-read', 'read', 'result:둘 다 본 답', 'eof')
        inserted = threading.Event()

        def later():
            for _ in range(200):
                if (self.bin / 'first-read').exists():
                    self._steer(LATER, 'k-3')
                    inserted.set()
                    return
                time.sleep(0.02)
        thread = threading.Thread(target=later, daemon=True)
        thread.start()
        result = self._run()
        thread.join(5)
        self.assertTrue(inserted.is_set())
        self.assertEqual(result.content, '둘 다 본 답')
        self.assertEqual(self._events(), ['delivered', 'accepted', 'delivered', 'accepted'])

    def test_a_steer_after_the_answer_is_not_written_to_the_session(self):
        self._plan('read', 'result:답', 'sleep:0.8', 'touch:answered', 'eof')
        added = []

        def later():
            for _ in range(250):
                if (self.bin / 'answered').exists():
                    added.append(self._steer(REMARK, 'k-2'))
                    return
                time.sleep(0.02)
        thread = threading.Thread(target=later, daemon=True)
        thread.start()
        result = self._run()
        thread.join(6)
        self.assertEqual(result.content, '답', 'not a "late:" answer to a message written after the result')
        self.assertEqual(self._events(), [])
        if added:
            self.assertIsNone(self._claimed(added[0]), 'left for the finish guard (S1)')

    def test_echoed_owner_text_stays_out_of_the_turn_record(self):
        self._steer(REMARK, 'k-2')
        self._plan('read', 'read', 'result:하나의 답', 'eof')
        result = self._run()
        recorded = json.dumps(result.meta, ensure_ascii=False)
        self.assertNotIn(REMARK, recorded)
        self.assertNotIn(FIRST, recorded)

    def test_stop_kills_a_session_that_waits_on_stdin(self):
        self._plan('read', 'sleep:30')
        stop = []
        threading.Timer(0.5, lambda: stop.append('stopped')).start()
        started = time.monotonic()
        with self.assertRaises(ExecutionError) as caught:
            self._run(_Budget(stop=lambda: stop[0] if stop else None))
        self.assertEqual(caught.exception.failure_class, 'stopped')
        self.assertLess(time.monotonic() - started, 10)

    def test_the_deadline_ends_a_session_that_never_answers(self):
        self._plan('read', 'eof')
        started = time.monotonic()
        with self.assertRaises(ExecutionError) as caught:
            self._run(_Budget(seconds=1))
        self.assertEqual(caught.exception.failure_class, 'deadline_exceeded')
        self.assertLess(time.monotonic() - started, 10)

    def test_the_stream_bound_still_kills_a_session(self):
        self._plan('read', 'eof')
        original = bounded_execution.MAX_STREAM_BYTES
        bounded_execution.MAX_STREAM_BYTES = 100
        self.addCleanup(setattr, bounded_execution, 'MAX_STREAM_BYTES', original)
        with self.assertRaises(ExecutionError) as caught:
            self._run(_Budget(seconds=10))
        self.assertEqual(caught.exception.failure_class, 'invalid-output')


class SessionLaunch(unittest.TestCase):
    """Only the prompt slot changes; the bridge leaves steers to the session; strict keeps ``-p``."""

    def _config(self, engine, tools):
        seen = {}

        def runner(argv, **kwargs):
            seen['argv'], seen['kwargs'] = argv, kwargs
            seen['config'] = json.loads(Path(argv[argv.index('--mcp-config') + 1]).read_text()) if '--mcp-config' in argv else None
            class Done:
                returncode = 0
                stdout = (_result('ok') if engine == 'claude-code'
                          else json.dumps({'item': {'type': 'agent_message', 'text': 'ok'}}))
            return Done()
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / 'codex-home'
            profile.mkdir()
            adapter = BoundedExecutionAdapter(finder=lambda name: '/runtime/' + name, runner=runner,
                                              runtime_root=Path(folder) / 'turns', codex_home=profile)
            adapter.bridge_check = lambda: None
            adapter.execute(engine, FIRST, tools)
        return seen

    def test_claude_code_reads_the_prompt_from_stdin_and_its_bridge_leaves_steers_to_the_session(self):
        store_root = type('Store', (), {'root': Path('/safe-owner-runtime')})()
        caps = type('Caps', (), {'store': store_root, 'job_id': 'fixture-job', 'definitions': lambda self: [],
                                 'execute': lambda self, *a: {}})()
        seen = self._config('claude-code', AgentOSMcpTools(caps))
        self.assertEqual(seen['argv'][1:8], ['-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
                                             '--verbose', '--replay-user-messages'])
        self.assertNotIn(FIRST, seen['argv'])
        self.assertEqual(seen['kwargs']['input'].encode(), session_message(FIRST))
        self.assertIn('--session-steers', seen['config']['mcpServers']['agentos']['args'])

    def test_strict_profile_keeps_the_one_shot_argv(self):
        adapter = BoundedExecutionAdapter(finder=lambda name: '/runtime/' + name)
        argv = adapter.command('claude-code', '/runtime/claude', FIRST, '/tmp/c.json', profile=STRICT_PROFILE)
        self.assertEqual(argv[:3], ['/runtime/claude', '-p', FIRST])
        self.assertNotIn('--input-format', argv)
        self.assertNotIn('--replay-user-messages', argv)

    def test_codex_keeps_exec_and_its_bridge_still_carries_steers(self):
        caps = type('Caps', (), {'store': type('Store', (), {'root': Path('/safe-owner-runtime')})(), 'job_id': 'fixture-job',
                                 'definitions': lambda self: [], 'execute': lambda self, *a: {}})()
        seen = self._config('codex', AgentOSMcpTools(caps))
        self.assertEqual(seen['kwargs']['stdin'], subprocess.DEVNULL)
        bridge = next(value for value in seen['argv'] if 'mcp_servers.agentos.args=' in value)
        self.assertNotIn('--session-steers', bridge)


class BridgeOnSessionRoute(unittest.TestCase):
    def test_a_session_bridge_leaves_the_steer_for_the_session_pump(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QuickStore(Path(folder) / 'data')
            job = store.enqueue(FIRST, 'k-1')
            source = store.enqueue(REMARK, 'k-2')
            with store.db() as db:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
                db.execute("INSERT INTO notes VALUES ('n1','note',1)")
            self.assertTrue(store.add_steer(job, source, REMARK))
            replies = []
            for flags in (['--session-steers'], []):
                process = subprocess.Popen([sys.executable, '-m', 'personal_agent.mcp_bridge', '--data', str(store.root),
                                            '--job', job, *flags], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                           env={**os.environ, 'PYTHONPATH': str(Path(bounded_execution.__file__).resolve().parents[1])})
                try:
                    process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}) + '\n')
                    process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                                    'params': {'name': 'list_notes', 'arguments': {}}}) + '\n')
                    process.stdin.flush()
                    replies.append([json.loads(process.stdout.readline()) for _ in range(2)][1])
                finally:
                    process.stdin.close()
                    process.wait(timeout=10)
            on_session, without = replies
            self.assertFalse(any(STEER_LEAD in part.get('text', '') for part in on_session['result']['content']))
            self.assertTrue(any(STEER_LEAD in part.get('text', '') for part in without['result']['content']),
                            'a route without a session still gets the steer with the tool result')


if __name__ == '__main__':
    unittest.main()
