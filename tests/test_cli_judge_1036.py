"""#1036: the subscription-CLI judge asks the shipped structured judgment and counts calls, never USD; arms are text only."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import cli, cli_judge as cj  # noqa: E402
from agentos_eval.scoring import judge_call  # noqa: E402


def test_spec_and_notice_take_no_api_key(capsys):
    assert cj.is_cli_judge('subscription-cli:codex') and not cj.is_cli_judge('openai/gpt-6.1-terra')
    assert cj.parse_spec('subscription-cli:codex:gpt-5.6-terra') == ('codex', 'gpt-5.6-terra')
    with pytest.raises(ValueError):
        cj.parse_spec('subscription-cli:other')
    called = []
    assert cli.judge_notice('subscription-cli:codex', key_loader=lambda j: called.append(j)) == 'subscription-cli'
    assert called == [] and 'no API key' in capsys.readouterr().out
    judge, scorer, roles, _ = cli.task_judge_settings('subscription-cli:codex')
    assert scorer == judge and roles is None


def test_call_budget_refuses_past_the_cap_and_costs_no_usd():
    budget = cj.CallBudget(2)
    assert budget.reserve(1, 1) == 0.0 and budget.reserve(1, 1) == 0.0 and budget.reserve(1, 1) is None
    assert budget.settle(0.0, None, None) == 0.0


class FakeService:
    def __init__(self):
        self.asked = []
        engine = self
        self.decision_engine = SimpleNamespace(structured=lambda ctx, q, schema, valid=None: engine._structured(ctx, q, schema))
        self.decision_judge = SimpleNamespace(_context=lambda purpose, facts, **o: SimpleNamespace(purpose=purpose, facts=facts, **o))

    def _structured(self, ctx, question, schema):
        self.asked.append((ctx.purpose, ctx.facts['transcript'], question, schema))
        return SimpleNamespace(outcome='decided', data={'reply': '{"dimensions": {}}'},
                               confidence=SimpleNamespace(observed_model='gpt-fixture'))

    from contextlib import contextmanager

    @contextmanager
    def work_decision_scope(self, *args):
        yield


def test_the_judge_reaches_the_structured_judgment_and_feeds_judge_call(monkeypatch):
    judge = cj.CliJudge('subscription-cli:codex', 'seed', max_calls=3)
    service = FakeService()
    judge._service = service
    budget = cj.CallBudget(3)
    parsed, status, usage, error = asyncio.run(judge_call('transcript text', lambda text: json.loads(text), judge.generate,
                                                          budget, lambda t: t))
    assert (parsed, status, error) == ({'dimensions': {}}, 'judged', None)
    assert usage['judge_model_resolved'] == 'gpt-fixture' and usage['usd'] == 0.0
    purpose, transcript, _, schema = service.asked[0]
    assert purpose == 'eval-judge' and transcript == 'transcript text' and schema == cj.SCHEMA


def test_an_unanswered_judgment_is_a_judge_error_not_a_score():
    judge = cj.CliJudge('subscription-cli:codex', 'seed')
    service = FakeService()
    service._structured = lambda *a: SimpleNamespace(outcome='unavailable', data=None, confidence=None)
    judge._service = service
    _, status, _, error = asyncio.run(judge_call('t', lambda text: text, judge.generate, cj.CallBudget(1), lambda t: t))
    assert status == 'judge_error' and 'no reply' in error


def test_arms_are_instruction_text_for_progress_cases_only():
    pytest.importorskip('inspect_ai')
    from agentos_eval import task
    assert task.arm_prefix('') == '' and task.arm_prefix('current') == ''
    assert task.arm_prefix('bluf') and task.arm_prefix('exception')
    with pytest.raises(ValueError):
        task.arm_prefix('unknown')
    data = json.loads((ROOT / 'evals/progress-update-arms.json').read_text())
    assert set(data['arms']) == {'current', 'bluf', 'exception'}
    samples = task.dataset('progress-price-watch-change-short,communication-durable-fact-in-passing', arm='bluf').samples
    by = {s.metadata['scenario']['id']: s.metadata['scenario']['turns'][0]['say'] for s in samples}
    assert by['progress-price-watch-change-short'].endswith(task.arm_prefix('bluf'))
    assert task.arm_prefix('bluf') not in by['communication-durable-fact-in-passing']
