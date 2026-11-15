"""Inspect AI task: Still as a black-box solver, scored by checks plus a rubric judge.

Run with Inspect directly::

    inspect eval evals/agentos_eval/task.py@agentos_secretary -T worker=both -T instances=3 \\
        -T seed=~/.local/share/agentos-evals/seeds/owner --epochs 3 --max-samples 6

or through ``python -m agentos_eval sweep`` (evals/README.md), which also writes
the trend report.  This is the only module that imports Inspect AI.
"""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

if __package__ in (None, ''):
    # `inspect eval evals/agentos_eval/task.py` loads this file by path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inspect_ai import Task, task, eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import (ChatMessageAssistant, ChatMessageSystem, ChatMessageUser, GenerateConfig,
                              ModelOutput, get_model)
from inspect_ai.scorer import Score, Target, mean, scorer
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.util import EarlyStop

from agentos_eval import scenarios as scenario_module
from agentos_eval.budget import JudgeBudget
from agentos_eval.paths import eval_home
from agentos_eval.redaction import Redactor
from agentos_eval.runner import WORKERS, StopOnUsageLimit, run_scenario
from agentos_eval.sandbox import SandboxPool
from agentos_eval.scoring import JUDGE_MAX_TOKENS, score_run


def workers_for(worker):
    if worker in ('both', 'split'):
        return WORKERS
    if worker not in WORKERS:
        raise ValueError(f'worker must be codex, claude-code or both (got {worker!r})')
    return (worker,)


def dataset(scenario_ids='', split='', worker='codex', include_local=True):
    ids = [item for item in scenario_ids.split(',') if item] if isinstance(scenario_ids, str) else list(scenario_ids)
    loaded = scenario_module.load(include_local=include_local, split=split or None, ids=ids or None)
    samples = []
    for scenario in loaded:
        for each in workers_for(worker):
            samples.append(Sample(id=f"{scenario['id']}@{each}", input=scenario['turns'][0]['say'],
                                  metadata={'scenario': scenario, 'worker': each}))
    return MemoryDataset(samples, name='agentos-secretary')


@solver
def agentos_blackbox(pool, turn_timeout=900, judgment_timeout=300):
    async def solve(state: TaskState, generate: Generate):
        meta = state.metadata
        record = await asyncio.to_thread(run_scenario, meta['scenario'], meta['worker'], pool,
                                         turn_timeout=turn_timeout, judgment_timeout=judgment_timeout)
        messages = []
        for turn in record['turns']:
            messages.append(ChatMessageUser(content=turn['say']))
            messages.append(ChatMessageAssistant(content=turn['answer'] or f"(no answer; status {turn['status']})"))
        state.messages = messages or state.messages
        last = record['turns'][-1]['answer'] if record['turns'] else ''
        state.output = ModelOutput.from_content(model='agentos', content=last or '')
        state.metadata['run'] = record
        state.completed = True
        return state
    return solve


@scorer(metrics={'*': [mean()]})
def secretary(judge='none', budget_path=None, seed=None, measures=''):
    enabled = bool(judge) and judge != 'none'
    budget = JudgeBudget.from_environ(budget_path or eval_home() / 'judge-budget.json') if enabled else None
    redact = Redactor.for_seed(seed) if enabled else None
    if measures:
        from agentos_eval.continuity import load_rubric
        rubric = load_rubric(measures)
        rubric_digest = hashlib.sha256(Path(measures).read_bytes()).hexdigest()
    else:
        rubric, rubric_digest = None, None

    async def generate(system, prompt):
        model = get_model(role='grader') if judge == 'grader' else get_model(judge)
        return await model.generate([ChatMessageSystem(content=system), ChatMessageUser(content=prompt)],
                                    config=GenerateConfig(max_tokens=JUDGE_MAX_TOKENS))

    async def score(state: TaskState, target: Target):
        scenario, run = state.metadata['scenario'], state.metadata.get('run') or {'error': 'solver did not run'}
        value, metadata = await score_run(scenario, run, judge, budget, redact, generate, rubric)
        judgment, failures = metadata['judgment'], metadata['failures']
        if measures:
            metadata['measures_rubric_digest'] = rubric_digest
        explanation = (judgment or {}).get('summary') or '; '.join(f"{row['name']}: {row['reason']}" for row in failures)
        return Score(value=value, answer=(state.output.completion or '')[:500], explanation=explanation or 'ok',
                     metadata=metadata)
    return score


@task
def agentos_secretary(scenarios='', split='', worker='codex', instances=3, seed='', judge='',
                      turn_timeout=900, judgment_timeout=300, include_local=True, keep_sandboxes=False, measures='',
                      sample_limit=None, judge_notice=True):
    """Multi-turn owner scenarios against fresh Still sandboxes, on Codex and/or Claude Code."""
    seed = seed or os.environ.get('AGENTOS_EVAL_SEED', '')
    from agentos_eval.cli import judge_notice as announce_judge, selected_judge
    judge = selected_judge(judge)
    if str(judge_notice).lower() not in ('0', 'false', 'no'):
        announce_judge(judge)
    pool = SandboxPool(size=int(instances), seed=Path(seed).expanduser() if seed else None,
                       keep=str(keep_sandboxes).lower() in ('1', 'true', 'yes'))
    include_local = str(include_local).lower() not in ('0', 'false', 'no')
    selected = dataset(scenarios, split, worker, include_local)
    if sample_limit is not None:
        selected = selected[:int(sample_limit)]
    scenario_ids = sorted({sample.metadata['scenario']['id'] for sample in selected})
    return Task(dataset=selected,
                solver=agentos_blackbox(pool, int(turn_timeout), int(judgment_timeout)),
                scorer=secretary('none' if judge == 'none' else 'grader', seed=Path(seed).expanduser() if seed else None,
                                 measures=measures),
                model='none/none',
                early_stopping=StopOnUsageLimit(EarlyStop),
                model_roles=None if judge == 'none' else {'grader': judge},
                metadata={'worker': worker, 'instances': int(instances), 'seed': bool(seed), 'judge': judge,
                          'scenario_ids': scenario_ids})
