"""#1355 automatic revision loop: temporary git repo, real `run_arm` with an injected sweep, fake proposer.

The fake sweep scores runs from the text it finds in the copy, so a promotion that does not reach the next
round, or a held-out scenario that reaches a loop run, changes what the tests observe.
"""
import asyncio
import functools
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import arm_run, cli, revise, revise_loop
from agentos_eval.arm_compare import compare_versioned_text, text_digest

TARGET = 'src/pkg/SKILL.md'
START = 'Check the result.\n'
BETTER = 'Check the result. BETTER\n'
WORSE = 'Skip checks. WORSE\n'
DEV = ['dev-a', 'dev-b']
HELD = ['held-a', 'held-b']
SCENARIOS = [
    *({'id': i, 'title': f'dev case {i[-1]}', 'split': 'dev', 'source': 'bundled',
       'turns': [{'say': f'dev request {i[-1]}'}], 'good_secretary': 'does the dev thing'} for i in DEV),
    *({'id': i, 'title': f'HELDTITLE {i[-1]}', 'split': 'heldout', 'source': 'bundled',
       'turns': [{'say': f'HELDSAY {i[-1]}'}], 'good_secretary': 'HELDGOOD'} for i in HELD),
    {'id': 'owner-x', 'title': 'owner store', 'split': 'dev', 'source': 'local', 'turns': []},
]
FORBIDDEN = {item['id'] for item in SCENARIOS}
SWEEP = ['--worker', 'codex', '--judge', 'subscription-cli:codex']
TOL = {'safe': 0.05}


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                           *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path / 'home'))
    return tmp_path / 'home'


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    (root / 'src/pkg').mkdir(parents=True)
    (root / TARGET).write_text(START)
    git(root, 'init', '-q')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'base')
    return root


def passed_for(text, scenario):
    """BETTER passes every run; the start text fails dev-a and held-a; WORSE fails everything."""
    if 'WORSE' in text:
        return 0.0
    if 'BETTER' in text:
        return 1.0
    return 0.0 if scenario.endswith('-a') else 1.0


class Sweep:
    def __init__(self, leak=None):
        self.calls, self.leak = [], leak

    def __call__(self, copy_root, sweep_args):
        args = list(sweep_args)
        ids = args[args.index('--scenarios') + 1].split(',')
        epochs = int(args[args.index('--epochs') + 1])
        text = (Path(copy_root) / TARGET).read_text()
        self.calls.append({'ids': ids, 'epochs': epochs, 'text': text})
        ran = ids + ([self.leak] if self.leak else [])
        records = [{'scenario': s, 'epoch': e, 'worker': 'codex', 'judge_status': 'judged',
                    'value': {'passed': passed_for(text, s), 'safe': 1.0},
                    'failures': [] if passed_for(text, s) else [{'kind': 'check', 'name': 'delivered',
                                                                 'reason': f'{s} turn 1: empty'}],
                    'work': {'w': {'refused_effects': 0, 'engine': [{'model': 'm', 'effort': 'medium'}]}}}
                   for s in ran for e in range(1, epochs + 1)]
        return {'exit_code': 0, 'records': records,
                'run': {'id': f'run-{len(self.calls)}', 'worker': 'codex', 'judge': 'subscription-cli:codex',
                        'scenario_ids': ran}}


class Proposer:
    def __init__(self, *texts):
        self.texts, self.prompts, self.closed = list(texts), [], 0

    def __call__(self):
        async def generate(system, prompt):
            self.prompts.append(json.loads(prompt))
            text = self.texts.pop(0)
            reply = {'candidates': [{'scope': 'general', 'path': TARGET, 'text': text, 'summary': 'revise'}]}
            return SimpleNamespace(completion=json.dumps(reply), usage=None, model='fake')

        def close():
            self.closed += 1
        return generate, {'proposer': 'fake'}, close


def loop(repo, tmp_path, proposer, sweep, **overrides):
    options = dict(target=revise.load_target(TARGET, repo=repo), sweep_args=SWEEP, dev_ids=DEV,
                   scenarios=SCENARIOS, rounds=3, epochs=3, max_worker_runs=500, generate_factory=proposer,
                   forbidden=FORBIDDEN, out=tmp_path / 'loop', tolerances=TOL,
                   arm_runner=functools.partial(arm_run.run_arm, repo=repo, runner=sweep))
    options.update(overrides)
    return json.loads(revise_loop.run_loop(**options).read_text())


# --- rounds ---------------------------------------------------------------------------------------

def test_preferred_candidate_becomes_the_next_rounds_base_and_the_loop_stops_without_room(repo, tmp_path):
    sweep, proposer = Sweep(), Proposer(BETTER)
    record = loop(repo, tmp_path, proposer, sweep)
    first, second = record['rounds']
    assert first['outcome'] == 'promoted' and first['winner_digest'] == text_digest(BETTER)
    # Round 2 ran a fresh base arm on the promoted text, then found nothing left to improve.
    assert second['base_digest'] == text_digest(BETTER) and sweep.calls[-1]['text'] == BETTER
    assert 'base_reused_from' not in second and second['outcome'] == 'stopped'
    assert record['stop_reason'].startswith('no room: no_room_on_dev')
    assert record['final_digest'] == text_digest(BETTER)
    assert (tmp_path / 'loop/final.txt').read_text() == BETTER
    assert (repo / TARGET).read_text() == START and git(repo, 'status', '--porcelain') == ''
    assert record['worker_runs'] == 6 + 6 + 6 + 6 and len(proposer.prompts) == 1 and proposer.closed == 1


def test_rejected_candidate_keeps_the_base_and_the_next_proposal_sees_that_verdict(repo, tmp_path):
    sweep, proposer = Sweep(), Proposer(WORSE, WORSE + 'again\n')
    record = loop(repo, tmp_path, proposer, sweep, rounds=2)
    first, second = record['rounds']
    assert first['outcome'] == second['outcome'] == 'kept'
    assert 'rejected' in first['kept_reason']
    assert second['base_digest'] == text_digest(START) and second['base_reused_from'].endswith('round-1/base.json')
    assert [c['text'] for c in sweep.calls].count(START) == 3  # one base arm, two equal-retries arms
    assert proposer.prompts[0]['prior'] == []
    assert [p['verdict'] for p in proposer.prompts[1]['prior']] == ['rejected']
    assert record['stop_reason'] == 'rounds: 2 rounds ran' and record['final_digest'] == text_digest(START)
    assert record['worker_runs'] == 6 * 5


def test_budget_stops_before_a_round_that_could_overrun_it(repo, tmp_path):
    sweep = Sweep()
    none = loop(repo, tmp_path, Proposer(), sweep, max_worker_runs=29)
    assert none['rounds'] == [] and none['stop_reason'].startswith('budget') and sweep.calls == []
    # One full round fits (base 6 + candidates up to 12 + retries up to 12); the next needs 24 more.
    one = loop(repo, tmp_path, Proposer(WORSE), Sweep(), max_worker_runs=36, out=tmp_path / 'loop2')
    assert len(one['rounds']) == 1 and one['worker_runs'] == 18
    assert one['stop_reason'] == 'budget: a full round needs up to 24 worker runs, 18 remain'


# --- held-out guard -------------------------------------------------------------------------------

@pytest.mark.parametrize('dev', [['dev-a', 'held-a'], ['owner-x'], ['missing']])
def test_loop_refuses_any_scenario_that_is_not_a_bundled_dev_scenario(repo, tmp_path, dev):
    sweep = Sweep()
    with pytest.raises(ValueError):
        loop(repo, tmp_path, Proposer(BETTER), sweep, dev_ids=dev)
    assert sweep.calls == []


@pytest.mark.parametrize('flag', [['--scenarios', 'held-a'], ['--split=heldout'], ['--epochs', '9'],
                                  ['--world', 'w']])
def test_loop_refuses_sweep_flags_that_choose_scenarios(repo, tmp_path, flag):
    sweep = Sweep()
    with pytest.raises(ValueError, match='sets these sweep flags itself'):
        loop(repo, tmp_path, Proposer(BETTER), sweep, sweep_args=SWEEP + flag)
    assert sweep.calls == []


def test_a_held_out_record_in_a_loop_run_stops_the_loop(repo, tmp_path):
    with pytest.raises(ValueError, match='outside its allowed set: held-a'):
        loop(repo, tmp_path, Proposer(BETTER), Sweep(leak='held-a'))


def test_every_loop_run_and_the_proposer_input_exclude_held_out(repo, tmp_path):
    # An experience record from a comparison that included held-out must not reach the proposer.
    revise.record_experience({
        'schema': 'revision-experience/1', 'target_path': TARGET, 'lines': None, 'base_digest': text_digest(START),
        'cause': {'text': 'old cause'}, 'proposer': {}, 'recorded_at': '2026-11-01T00:00:00Z', 'settings': {},
        'scenario_ids': ['dev-a', 'held-a'], 'measure': 'passed', 'tolerances': TOL,
        'candidates': [{'label': 'general', 'scope': 'general', 'candidate_digest': text_digest('x'),
                        'summary': 'HELDVERDICT', 'verdict': 'preferred', 'reasons': [], 'parents': []}],
        'cost': {}}, tmp_path / 'loop' / 'experience')
    sweep, proposer = Sweep(), Proposer(WORSE, WORSE + '2\n', WORSE + '3\n')
    loop(repo, tmp_path, proposer, sweep)
    assert sweep.calls and all(set(call['ids']) == set(DEV) for call in sweep.calls)
    prompts = json.dumps(proposer.prompts)
    for term in ('HELD', *HELD, *DEV):
        assert term not in prompts
    assert 'dev request a' in prompts and '<scenario> turn 1: empty' in prompts
    assert len(proposer.prompts[2]['prior']) == 2  # only the loop's own two dev comparisons


def test_failure_cause_refuses_records_outside_the_loop(repo):
    arm = {'records': [{'scenario': 'held-a', 'value': {'passed': 0.0}}]}
    with pytest.raises(ValueError, match='outside the loop scenarios'):
        revise_loop.failure_cause(arm, SCENARIOS, measure='passed', allowed=set(DEV), forbidden=FORBIDDEN)


# --- final step -----------------------------------------------------------------------------------

def test_final_step_compares_start_and_final_text_on_held_out_only(repo, tmp_path):
    loop(repo, tmp_path, Proposer(BETTER), Sweep())
    sweep = Sweep()
    runner = functools.partial(arm_run.run_arm, repo=repo, runner=sweep)
    with pytest.raises(ValueError, match='not a bundled heldout'):
        revise_loop.final_heldout(tmp_path / 'loop/loop.json', heldout_ids=['dev-a'], scenarios=SCENARIOS, epochs=3,
                                  bar=0.2, max_worker_runs=100, arm_runner=runner)
    result = json.loads(revise_loop.final_heldout(
        tmp_path / 'loop/loop.json', heldout_ids=HELD, scenarios=SCENARIOS, epochs=3, bar=0.2,
        max_worker_runs=100, arm_runner=runner).read_text())
    assert [set(call['ids']) for call in sweep.calls] == [set(HELD)] * 3
    assert [call['text'] for call in sweep.calls] == [START, BETTER, START]
    assert result['verdict'] == 'preferred' and result['gain'] == {'baseline': 0.5, 'equal_retries': 0.5}
    assert result['bar_met'] is True
    high = json.loads(revise_loop.final_heldout(
        tmp_path / 'loop/loop.json', heldout_ids=HELD, scenarios=SCENARIOS, epochs=3, bar=0.6,
        max_worker_runs=100, arm_runner=functools.partial(arm_run.run_arm, repo=repo, runner=Sweep())).read_text())
    assert high['verdict'] == 'preferred' and high['bar_met'] is False


def test_final_step_refuses_an_unfinished_loop_and_reports_no_revision(repo, tmp_path):
    loop(repo, tmp_path, Proposer(WORSE, WORSE + '2\n', WORSE + '3\n'), Sweep())
    sweep = Sweep()
    result = json.loads(revise_loop.final_heldout(
        tmp_path / 'loop/loop.json', heldout_ids=HELD, scenarios=SCENARIOS, epochs=3, bar=0.2, max_worker_runs=100,
        arm_runner=functools.partial(arm_run.run_arm, repo=repo, runner=sweep)).read_text())
    assert result['outcome'] == 'no_revision' and sweep.calls == []
    record = json.loads((tmp_path / 'loop/loop.json').read_text())
    (tmp_path / 'loop/loop.json').write_text(json.dumps(dict(record, state='running')))
    with pytest.raises(ValueError, match='finished'):
        revise_loop.final_heldout(tmp_path / 'loop/loop.json', heldout_ids=HELD, scenarios=SCENARIOS, epochs=3,
                                  bar=0.2, max_worker_runs=100)


# --- comparator splits, room and base text -------------------------------------------------------

def arm(text, passed, ids, epochs=3, role='current'):
    return {'schema': 'revision-arm/1', 'role': role, 'target_path': TARGET, 'lines': None,
            'base_digest': text_digest(START), 'text_digest': text_digest(text),
            'settings': {'worker': 'codex', 'model': 'm', 'judge': 'subscription-cli:codex'}, 'scenario_ids': ids,
            'records': [{'scenario': s, 'epoch': e, 'worker': 'codex', 'judge_status': 'judged',
                         'value': {'passed': passed(s), 'safe': 1.0}, 'refused_effects': 0}
                        for s in ids for e in range(1, epochs + 1)],
            'cost': {'worker_runs': len(ids) * epochs}}


META = {item['id']: {'split': item['split'], 'source': item['source']} for item in SCENARIOS}


def test_dev_only_decision_prefers_what_the_default_cannot_decide_and_refuses_held_out():
    base = arm(START, lambda s: passed_for(START, s), DEV)
    better = arm(BETTER, lambda s: 1.0, DEV, role='candidate')
    retries = arm(START, lambda s: passed_for(START, s), DEV, epochs=3)
    default = compare_versioned_text(base, {'g': better}, meta=META, target='passed', tolerances=TOL,
                                     equal_retries=retries)
    assert default['candidates']['g']['verdict'] == 'insufficient_evidence'
    dev = compare_versioned_text(base, {'g': better}, meta=META, target='passed', tolerances=TOL,
                                 equal_retries=retries, splits=('dev',))
    assert dev['candidates']['g']['verdict'] == 'preferred' and dev['splits'] == ['dev']
    mixed = [*DEV, 'held-a']
    with pytest.raises(ValueError, match='outside the decision splits'):
        compare_versioned_text(arm(START, lambda s: 0.0, mixed), {'g': arm(BETTER, lambda s: 1.0, mixed)},
                               meta=META, target='passed', tolerances=TOL, splits=('dev',))


def test_room_check_on_dev_alone_accepts_a_dev_only_baseline():
    base = arm(START, lambda s: passed_for(START, s), DEV)
    target = {'path': TARGET, 'lines': None, 'digest': text_digest(START)}
    with pytest.raises(ValueError, match='no heldout records'):
        revise.check_room(base, target, meta=META, measure='passed')
    revise.check_room(base, target, meta=META, measure='passed', splits=('dev',))
    full = arm(START, lambda s: 1.0, DEV)
    with pytest.raises(ValueError, match='no_room_on_dev'):
        revise.check_room(full, target, meta=META, measure='passed', splits=('dev',))


def test_base_text_runs_in_place_of_head_and_a_candidate_must_name_it(repo, tmp_path):
    sweep = Sweep()
    out = arm_run.run_arm(SWEEP + ['--scenarios', 'dev-a', '--epochs', '3'], target=TARGET, base_text=BETTER,
                          repo=repo, runner=sweep, out=tmp_path / 'b.json')
    assert sweep.calls[-1]['text'] == BETTER and json.loads(out.read_text())['text_digest'] == text_digest(BETTER)
    folder = tmp_path / 'c'
    folder.mkdir()
    (folder / 'general.txt').write_text(WORSE)
    (folder / 'general.json').write_text(json.dumps({
        'schema': 'revision-candidate/1', 'label': 'general', 'target_path': TARGET, 'lines': None,
        'base_digest': text_digest(START), 'candidate_digest': text_digest(WORSE)}))
    with pytest.raises(ValueError, match='base text does not match'):
        arm_run.run_arm(SWEEP + ['--scenarios', 'dev-a', '--epochs', '3'], candidate=folder / 'general.txt',
                        base_text=BETTER, repo=repo, runner=sweep, out=tmp_path / 'c.json')


# --- compare CLI ----------------------------------------------------------------------------------

def test_compare_cli_writes_the_same_result_as_the_function(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.scenario_module, 'load', lambda *a, **k: SCENARIOS)
    ids = [*DEV, *HELD]
    arms = {'base': arm(START, lambda s: passed_for(START, s), ids),
            'cand': arm(BETTER, lambda s: 1.0, ids, role='candidate'),
            'retries': arm(START, lambda s: passed_for(START, s), ids)}
    for name, value in arms.items():
        (tmp_path / f'{name}.json').write_text(json.dumps(value))
    cli.main(['revise', 'compare', '--baseline', str(tmp_path / 'base.json'),
              '--candidate', f'general={tmp_path / "cand.json"}', '--equal-retries', str(tmp_path / 'retries.json'),
              '--tolerance', 'safe=0.05', '--out', str(tmp_path / 'cmp.json')])
    written = json.loads((tmp_path / 'cmp.json').read_text())
    expected = compare_versioned_text(arms['base'], {'general': arms['cand']}, meta=META, target='passed',
                                      tolerances=TOL, equal_retries=arms['retries'])
    assert written == json.loads(json.dumps(expected)) and written['schema'] == 'arm-compare/1'
    assert 'general preferred' in capsys.readouterr().out
    with pytest.raises(SystemExit, match='outside the repository'):
        cli.main(['revise', 'compare', '--baseline', str(tmp_path / 'base.json'),
                  '--candidate', f'general={tmp_path / "cand.json"}', '--out', str(ROOT / 'cmp.json')])
