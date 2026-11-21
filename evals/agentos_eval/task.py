"""Inspect AI task: Still as a black-box solver, scored by checks plus a rubric judge.

Run with Inspect directly::

    inspect eval evals/agentos_eval/task.py@agentos_secretary -T worker=both -T instances=3 \\
        -T seed=~/.local/share/agentos-evals/seeds/owner --epochs 3 --max-samples 6

or through ``python -m agentos_eval sweep`` (evals/README.md), which also writes
the trend report.  This is the only module that imports Inspect AI.
"""
import asyncio
import hashlib
import json
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
from agentos_eval.budget import scorer_judge_resources
from agentos_eval.runner import WORKERS, StopOnUsageLimit, record_messages, run_scenario
from agentos_eval.sandbox import SandboxPool
from agentos_eval.scoring import JUDGE_MAX_TOKENS, score_run


def workers_for(worker):
    if worker in ('both', 'split'):
        return WORKERS
    if worker not in WORKERS:
        raise ValueError(f'worker must be codex, claude-code or both (got {worker!r})')
    return (worker,)


def arm_prefix(arm):
    """#1036: a candidate reporting form as instruction text for progress-update turns ('' for no arm)."""
    if not arm:
        return ''
    arms = json.loads((Path(__file__).resolve().parents[1] / 'progress-update-arms.json').read_text(encoding='utf-8'))['arms']
    if arm not in arms:
        raise ValueError(f'Unknown arm {arm!r}; known: {sorted(arms)}')
    return arms[arm]


def dataset(scenario_ids='', split='', worker='codex', include_local=True, arm=''):
    prefix = arm_prefix(arm)
    ids = [item for item in scenario_ids.split(',') if item] if isinstance(scenario_ids, str) else list(scenario_ids)
    loaded = scenario_module.load(include_local=include_local, split=split or None, ids=ids or None)
    samples = []
    for scenario in loaded:
        if prefix and 'progress-update' in scenario['tags']:
            turns = [dict(scenario['turns'][0], say=f"{scenario['turns'][0]['say']} {prefix}"), *scenario['turns'][1:]]
            scenario = {**scenario, 'turns': turns}
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
        # Preserve the observation even when log rendering/Inspect conversion fails.
        state.metadata['run'] = record
        rendered = record_messages(record)
        message_types = {'user': ChatMessageUser, 'assistant': ChatMessageAssistant}
        messages = [message_types[row['role']](content=row['content']) for row in rendered]
        state.messages = messages or state.messages
        last = rendered[-1]['content'] if rendered else ''
        state.output = ModelOutput.from_content(model='agentos', content=last)
        state.completed = True
        return state
    return solve


@scorer(metrics={'*': [mean()]})
def secretary(judge='none', budget_path=None, seed=None, measures=''):
    cli_judge = None
    if judge.startswith('subscription-cli:'):
        # #1036: a subscription-CLI judge counts calls, not USD, and needs no provider key.
        from agentos_eval.redaction import Redactor
        from agentos_eval.cli_judge import CallBudget, CliJudge
        limit = int(os.environ.get('AGENTOS_EVAL_JUDGE_MAX_CALLS') or 400)
        budget, redact = CallBudget(limit), Redactor.for_seed(seed)
        cli_judge = CliJudge(judge, seed, max_calls=limit)
    else:
        budget, redact = scorer_judge_resources(judge, budget_path, seed)
    if measures:
        from agentos_eval.continuity import load_rubric
        rubric = load_rubric(measures)
        rubric_digest = hashlib.sha256(Path(measures).read_bytes()).hexdigest()
    else:
        rubric, rubric_digest = None, None

    async def generate(system, prompt):
        if cli_judge is not None:
            return await cli_judge.generate(system, prompt)
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
                      sample_limit=None, judge_notice=True, arm=''):
    """Multi-turn owner scenarios against fresh Still sandboxes, on Codex and/or Claude Code."""
    seed = seed or os.environ.get('AGENTOS_EVAL_SEED', '')
    from agentos_eval.cli import judge_notice as announce_judge, task_judge_settings
    judge, scorer_judge, model_roles, announce = task_judge_settings(judge, judge_notice)
    if announce:
        announce_judge(judge)
    pool = SandboxPool(size=int(instances), seed=Path(seed).expanduser() if seed else None,
                       keep=str(keep_sandboxes).lower() in ('1', 'true', 'yes'))
    include_local = str(include_local).lower() not in ('0', 'false', 'no')
    selected = dataset(scenarios, split, worker, include_local, arm)
    if sample_limit is not None:
        selected = selected[:int(sample_limit)]
    scenario_ids = sorted({sample.metadata['scenario']['id'] for sample in selected})
    return Task(dataset=selected,
                solver=agentos_blackbox(pool, int(turn_timeout), int(judgment_timeout)),
                scorer=secretary(scorer_judge, seed=Path(seed).expanduser() if seed else None,
                                 measures=measures),
                model='none/none',
                early_stopping=StopOnUsageLimit(EarlyStop),
                model_roles=model_roles,
                metadata={'worker': worker, 'instances': int(instances), 'seed': bool(seed), 'judge': judge,
                          'scenario_ids': scenario_ids, 'arm': arm})
