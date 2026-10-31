"""Content-free process timing for subscription-CLI judgments (#309 L3)."""
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest

from personal_agent.bounded_execution import BoundedExecutionAdapter
from personal_agent.decision import (DecisionContext, OUTCOME_TIMEOUT, OUTCOME_UNAVAILABLE)
from personal_agent.decision_adapters import SubscriptionCliDecisionEngine


PROMPT_TEXT = 'prompt-content-must-stay-out-of-the-record'
MODEL_TEXT = 'model-answer-content-must-stay-out-of-the-record'
STDOUT_LINE = json.dumps({'type': 'thread.started', 'private': MODEL_TEXT})
ANSWER_LINE = json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '',
                          'structured_output': {'reply': MODEL_TEXT, 'confidence': 0.9}, 'modelUsage': {}})


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class StreamingRunner:
    """An injected runner that opts into bounded_run's stdout-line callback."""
    def __init__(self, clock):
        self.clock = clock

    def __call__(self, argv, *, on_line=None, **_kwargs):
        self.clock.advance(2)
        on_line(STDOUT_LINE)
        self.clock.advance(4)
        return types.SimpleNamespace(returncode=0, stdout='\n'.join((STDOUT_LINE, ANSWER_LINE)), stderr='')


class BatchRunner:
    def __init__(self, clock):
        self.clock = clock

    def __call__(self, _argv, **_kwargs):
        self.clock.advance(3)
        return types.SimpleNamespace(returncode=0, stdout='\n'.join((STDOUT_LINE, ANSWER_LINE)), stderr='')


class TimeoutRunner:
    def __init__(self, clock):
        self.clock = clock

    def __call__(self, argv, **_kwargs):
        self.clock.advance(3)
        raise subprocess.TimeoutExpired(argv, 90)


class StartFailureRunner:
    def __call__(self, _argv, **_kwargs):
        raise OSError('not executable')


class JudgmentCliTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def engine(self, runner, clock):
        records = []
        adapter = BoundedExecutionAdapter(finder=lambda _name: '/fixture/bin/claude-code', runner=runner,
                                          runtime_root=Path(self.temp.name) / 'runs')
        engine = SubscriptionCliDecisionEngine(adapter, 'claude-code', now=clock, audit=records.append)
        return engine, records

    @staticmethod
    def ask(engine):
        return engine.structured(DecisionContext('timing', {'owner_message': PROMPT_TEXT}), 'reply?',
                                 {'type': 'object', 'properties': {'reply': {'type': 'string'}}, 'required': ['reply']})

    def test_success_records_ordered_process_moments_without_content(self):
        clock = Clock()
        decision, records = self.engine(StreamingRunner(clock), clock)

        result = self.ask(decision)

        self.assertEqual((result.outcome, result.data), ('decided', {'reply': MODEL_TEXT}))
        timing = records[-1]['cli_timing']
        self.assertEqual(timing, {'spawn_s': 0.0, 'first_event_s': 2.0, 'answered_s': None, 'exit_s': 6.0})
        self.assertLessEqual(timing['spawn_s'], timing['first_event_s'])
        self.assertLessEqual(timing['first_event_s'], timing['exit_s'])
        self.assertTrue(all(value is None or isinstance(value, (int, float)) and not isinstance(value, bool)
                            for value in timing.values()))
        serialized = json.dumps(records[-1])
        for text in (PROMPT_TEXT, MODEL_TEXT, STDOUT_LINE, ANSWER_LINE):
            self.assertNotIn(text, serialized)

    def test_batch_timeout_and_start_failure_leave_unobserved_moments_null(self):
        cases = (
            ('batch', BatchRunner, 'decided', {'spawn_s': 0.0, 'first_event_s': None, 'answered_s': None, 'exit_s': 3.0}),
            ('timeout', TimeoutRunner, OUTCOME_TIMEOUT, {'spawn_s': 0.0, 'first_event_s': None, 'answered_s': None, 'exit_s': None}),
            ('start-failed', StartFailureRunner, OUTCOME_UNAVAILABLE,
             {'spawn_s': None, 'first_event_s': None, 'answered_s': None, 'exit_s': None}),
        )
        for name, runner_type, outcome, expected in cases:
            with self.subTest(name=name):
                clock = Clock()
                engine, records = self.engine(runner_type(clock) if runner_type is not StartFailureRunner else runner_type(), clock)
                result = self.ask(engine)
                self.assertEqual(result.outcome, outcome)
                self.assertEqual(records[-1]['cli_timing'], expected)
