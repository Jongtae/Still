"""#469: a Codex judgment is taken at ``turn.completed``; the CLI exits (or is killed) and is reaped afterwards."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from personal_agent import bounded_execution
from personal_agent.bounded_execution import BoundedExecutionAdapter
from personal_agent.decision import DecisionContext
from personal_agent.decision_adapters import SubscriptionCliDecisionEngine, codex_answer_complete

ANSWER = json.dumps({'reply': 'ok', 'confidence': 0.9})
EVENTS = {
    'started': {'type': 'thread.started', 'thread_id': 't'},
    'message': {'type': 'item.completed', 'item': {'id': 'i', 'type': 'agent_message', 'text': ANSWER}},
    'completed': {'type': 'turn.completed', 'usage': {}},
    'failed': {'type': 'turn.failed', 'error': {'message': 'boom'}},
}

#: A stand-in `codex` binary: prints the named events, records its pid (and an
#: optional child's), then lingers or fails the way the case asks.
FAKE_CLI = '''#!{python}
import json, os, subprocess, sys, time
case = json.loads({case!r})
pids = [os.getpid()]
if case.get('child'):
    pids.append(subprocess.Popen(['/bin/sleep', '30']).pid)
open({pid_file!r}, 'w').write(' '.join(map(str, pids)))
for name in case['events']:
    print(json.dumps({events!r}[name]), flush=True)
if case.get('stderr'):
    print(case['stderr'], file=sys.stderr, flush=True)
time.sleep(case['linger'])
sys.exit(case['exit'])
'''


def gone(pid):
    """True once ``pid`` no longer exists - a zombie still answers ``kill(pid, 0)``, so this also proves reaping."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


class AnswerOnTurnCompletedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'codex-home').mkdir()
        self.runs = self.root / 'runs'
        self.pid_file = self.root / 'pids'

    def engine(self, **case):
        script = self.root / 'codex'
        script.write_text(FAKE_CLI.format(python=sys.executable, case=json.dumps(case), events=EVENTS,
                                          pid_file=str(self.pid_file)))
        script.chmod(0o700)
        adapter = BoundedExecutionAdapter(finder=lambda _name: str(script), runtime_root=self.runs,
                                          codex_home=self.root / 'codex-home')
        records = []
        return SubscriptionCliDecisionEngine(adapter, 'codex', codex_disabled_features=('shell_tool',),
                                             audit=records.append), records

    @staticmethod
    def ask(engine):
        return engine.structured(DecisionContext('timing', {'note': 'synthetic'}), 'reply?',
                                 {'type': 'object', 'properties': {'reply': {'type': 'string'}}, 'required': ['reply']})

    def pids(self):
        return [int(value) for value in self.pid_file.read_text().split()]

    def wait_until(self, check, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if check():
                return True
            time.sleep(0.05)
        return check()

    def test_the_answer_returns_before_the_cli_exits_and_the_process_is_reaped_afterwards(self):
        engine, records = self.engine(events=['started', 'message', 'completed'], linger=2.0, exit=0)
        started = time.monotonic()

        result = self.ask(engine)

        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual((result.outcome, result.data), ('decided', {'reply': 'ok'}))
        timing = records[-1]['cli_timing']
        self.assertIsNotNone(timing['answered_s'])
        self.assertIsNone(timing['exit_s'])
        (pid,) = self.pids()
        self.assertFalse(gone(pid), 'the CLI is still exiting when the answer is taken')
        self.assertTrue(self.wait_until(lambda: gone(pid), 5), 'the CLI was not reaped after it exited')
        self.assertTrue(self.wait_until(lambda: not any(self.runs.iterdir()), 2),
                        'the per-call directory is removed once the CLI is gone')

    def test_a_cli_that_does_not_exit_after_answering_is_killed_with_its_group(self):
        original = bounded_execution.ANSWERED_EXIT_GRACE_SECONDS
        bounded_execution.ANSWERED_EXIT_GRACE_SECONDS = 0.3
        self.addCleanup(setattr, bounded_execution, 'ANSWERED_EXIT_GRACE_SECONDS', original)
        engine, _records = self.engine(events=['started', 'message', 'completed'], linger=30, exit=0, child=True)

        result = self.ask(engine)

        self.assertEqual(result.outcome, 'decided')
        for pid in self.pids():
            self.assertTrue(self.wait_until(lambda: gone(pid), 5), f'process {pid} outlived the grace period')

    def test_a_cli_that_fails_before_turn_completed_is_still_classified_by_its_exit(self):
        cases = (
            ('no turn.completed', ['started', 'failed']),
            ('turn.completed without an agent message', ['started', 'completed']),
        )
        for name, events in cases:
            with self.subTest(name=name):
                engine, records = self.engine(events=events, linger=0.5, exit=1, stderr='engine broke')
                started = time.monotonic()

                result = self.ask(engine)

                self.assertGreaterEqual(time.monotonic() - started, 0.5, 'the run waited for the exit')
                self.assertEqual(result.outcome, 'provider_unavailable')
                self.assertEqual(records[-1]['failure'], 'engine-failed')
                self.assertEqual(records[-1]['exit_code'], 1)
                self.assertIsNone(records[-1]['cli_timing']['answered_s'])
                self.assertTrue(all(gone(pid) for pid in self.pids()))
                self.assertFalse(any(self.runs.iterdir()))


class CodexAnswerCompleteTests(unittest.TestCase):
    def test_only_turn_completed_after_a_non_empty_agent_message_is_an_answer(self):
        def run(*records):
            answered = codex_answer_complete()
            return [answered(json.dumps(record) + '\n') for record in records]

        self.assertEqual(run(EVENTS['started'], EVENTS['message'], EVENTS['completed']), [False, False, True])
        self.assertEqual(run(EVENTS['started'], EVENTS['completed']), [False, False])
        empty = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '  '}}
        self.assertEqual(run(empty, EVENTS['completed']), [False, False])
        reasoning = {'type': 'item.completed', 'item': {'type': 'reasoning', 'text': ANSWER}}
        self.assertEqual(run(reasoning, EVENTS['completed']), [False, False])
        answered = codex_answer_complete()
        self.assertFalse(answered('not json\n'))
        self.assertFalse(answered('[1, 2]\n'))


if __name__ == '__main__':
    unittest.main()
