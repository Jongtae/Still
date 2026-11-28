"""#1298 revision arm runner and dev-room precheck: temporary git repos, injected sweep runner, no model call."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import arm_run, cli, revise
from agentos_eval.arm_compare import compare_versioned_text, text_digest

TARGET = 'src/pkg/SKILL.md'
BASE = 'line one\nline two\nline three\n'
FILES = {TARGET: BASE, 'src/pkg/other.py': 'x = 1\n', 'evals/scenarios/s.json': '{}\n', 'README.md': 'readme\n'}
SWEEP = ['--worker', 'codex', '--epochs', '3', '--scenarios', 'dev-a,held-a', '--made-up-later-flag', 'v']


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                           *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    folder = tmp_path / 'home'
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(folder))
    monkeypatch.delenv('AGENTOS_EVAL_JUDGE_MODEL', raising=False)
    return folder


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    git(root, 'init', '-q')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'base')
    return root


def candidate(tmp_path, text, lines=None, *, base=None, path=TARGET, label='general'):
    folder = tmp_path / 'cands'
    folder.mkdir(exist_ok=True)
    if base is None:
        base = BASE if lines is None else ''.join(BASE.splitlines(keepends=True)[lines[0] - 1:lines[1]])
    (folder / f'{label}.txt').write_bytes(text.encode())
    (folder / f'{label}.json').write_text(json.dumps({
        'schema': 'revision-candidate/1', 'label': label, 'scope': label, 'target_path': path,
        'lines': list(lines) if lines else None, 'base_digest': text_digest(base),
        'candidate_digest': text_digest(text), 'summary': 's', 'parents': []}))
    return folder / f'{label}.txt'


def record(scenario, epoch=1, passed=1.0, engine=(('gpt-x', 'high'),), **extra):
    return {'scenario': scenario, 'epoch': epoch, 'worker': 'codex', 'judge_status': 'judged',
            'value': {'passed': passed, 'safe': 1.0},
            'work': {'w1': {'refused_effects': 0, 'engine': [{'model': m, 'effort': e} for m, e in engine]}},
            'judge_usage': {'r': {'input_tokens': 10, 'output_tokens': 2, 'usd': 0}}, **extra}


def sweep(records=None, *, exit_code=0, run=None, seen=None):
    records = [record('dev-a'), record('held-a')] if records is None else records

    def runner(copy_root, sweep_args):
        copy_root = Path(copy_root)
        if seen is not None:
            seen['root'] = copy_root
            seen['args'] = list(sweep_args)
            seen['files'] = {p.relative_to(copy_root).as_posix(): p.read_bytes().decode()
                             for p in copy_root.rglob('*') if p.is_file()}
        return {'exit_code': exit_code, 'records': records,
                'run': {'id': 'run-1', 'worker': 'codex', 'judge': 'subscription-cli:codex', 'seed': 'synthetic',
                        'scenario_ids': sorted({r['scenario'] for r in records}), **(run or {})}}
    return runner


def never(*args):
    raise AssertionError('the sweep must not run')


def snapshot(repo):
    return ({p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob('*')
             if p.is_file() and '.git' not in p.relative_to(repo).parts},
            git(repo, 'status', '--porcelain'), git(repo, 'rev-parse', 'HEAD'), git(repo, 'worktree', 'list'))


# --- the copy -------------------------------------------------------------------------------------

@pytest.mark.parametrize('lines, text, expected', [
    (None, 'revised whole\n', 'revised whole\n'),
    ((2, 2), 'new two\nnew two b\n', 'line one\nnew two\nnew two b\nline three\n'),
    ((3, 3), 'last without newline', 'line one\nline two\nlast without newline'),
])
def test_candidate_arm_changes_only_the_target_in_a_head_copy(repo, tmp_path, home, lines, text, expected):
    # Uncommitted work must neither reach the copy nor be touched.
    (repo / 'src/pkg/other.py').write_text('dirty\n')
    (repo / 'untracked.txt').write_text('u\n')
    before = snapshot(repo)
    seen = {}
    out = arm_run.run_arm(SWEEP, candidate=candidate(tmp_path, text, lines), repo=repo,
                          runner=sweep(seen=seen), out=tmp_path / 'arm.json')
    arm = json.loads(out.read_text())
    assert seen['files'] == {**FILES, TARGET: expected}
    assert not seen['root'].is_relative_to(repo) and not seen['root'].exists()
    assert seen['args'] == SWEEP
    assert snapshot(repo) == before
    assert arm['schema'] == 'revision-arm/1' and arm['role'] == 'candidate' and arm['label'] == 'general'
    assert arm['text_digest'] == text_digest(text) and arm['target_path'] == TARGET
    assert arm['lines'] == (list(lines) if lines else None)


def test_current_arm_runs_head_text_not_the_working_tree(repo, tmp_path):
    (repo / TARGET).write_text('uncommitted edit\n')
    seen = {}
    out = arm_run.run_arm(SWEEP, target=TARGET, lines=(1, 2), repo=repo, runner=sweep(seen=seen))
    arm = json.loads(out.read_text())
    assert seen['files'] == FILES
    assert arm['role'] == 'current' and arm['text_digest'] == arm['base_digest'] == text_digest('line one\nline two\n')
    assert not out.is_relative_to(repo)
    retries = arm_run.run_arm(SWEEP, target=TARGET, lines=(1, 2), equal_retries=True, repo=repo, runner=sweep())
    assert retries != out and json.loads(retries.read_text())['role'] == 'equal_retries'
    assert (repo / TARGET).read_text() == 'uncommitted edit\n'


def test_arm_records_feed_the_comparator_unchanged(repo, tmp_path):
    meta = {'dev-a': {'split': 'dev'}, 'held-a': {'split': 'heldout'}}

    def rows(passed, epochs):
        return [record(s, e, passed) for s in meta for e in range(1, epochs + 1)]
    arms = {}
    for name, options, records in (
            ('current', {'target': TARGET}, rows(0.0, 3)),
            ('general', {'candidate': candidate(tmp_path, 'revised\n')}, rows(1.0, 3)),
            ('retries', {'target': TARGET, 'equal_retries': True}, rows(0.0, 3))):
        arms[name] = json.loads(arm_run.run_arm(SWEEP, repo=repo, runner=sweep(records), **options).read_text())
    assert arms['current']['settings'] == {'worker': 'codex', 'model': 'gpt-x', 'effort': 'high',
                                           'judge': 'subscription-cli:codex', 'seed': 'synthetic'}
    assert arms['current']['scenario_ids'] == ['dev-a', 'held-a']
    assert arms['current']['cost']['worker_runs'] == 6 and arms['current']['cost']['judge_calls'] == 6
    result = compare_versioned_text(arms['current'], {'general': arms['general']}, meta=meta, target='passed',
                                    tolerances={'safe': 0.05}, equal_retries=arms['retries'])
    assert result['candidates']['general']['verdict'] == 'preferred'


def test_a_per_request_model_or_effort_stays_visible_and_is_refused_by_the_comparator(repo, tmp_path):
    mixed = [record('dev-a', engine=(('gpt-x', 'high'),)), record('held-a', engine=(('gpt-y', 'ultra'),))]
    arm = json.loads(arm_run.run_arm(SWEEP, target=TARGET, repo=repo, runner=sweep(mixed)).read_text())
    assert arm['settings']['model'] not in ('gpt-x', 'gpt-y') and arm['settings']['effort'] not in ('high', 'ultra')
    assert arm['records'] == mixed
    other = json.loads(arm_run.run_arm(SWEEP, candidate=candidate(tmp_path, 'revised\n'), repo=repo,
                                       runner=sweep(mixed)).read_text())
    meta = {'dev-a': {'split': 'dev'}, 'held-a': {'split': 'heldout'}}
    with pytest.raises(ValueError, match='Recorded engine'):
        compare_versioned_text(arm, {'general': other}, meta=meta, target='passed', tolerances={'safe': 0})


# --- refusals -------------------------------------------------------------------------------------

def test_splice_refuses_when_head_differs_from_the_candidate_base(repo, tmp_path):
    file = candidate(tmp_path, 'revised\n')
    (repo / TARGET).write_text('moved on\n')
    git(repo, 'commit', '-q', '-am', 'change')
    before = snapshot(repo)
    with pytest.raises(ValueError, match='base digest'):
        arm_run.run_arm(SWEEP, candidate=file, repo=repo, runner=never)
    assert snapshot(repo) == before


def test_a_candidate_matching_only_the_working_tree_is_refused(repo, tmp_path):
    (repo / TARGET).write_text('uncommitted\n')
    with pytest.raises(ValueError, match='base digest'):
        arm_run.run_arm(SWEEP, candidate=candidate(tmp_path, 'revised\n', base='uncommitted\n'), repo=repo,
                        runner=never)


def test_candidate_text_changed_after_proposal_is_refused(repo, tmp_path):
    file = candidate(tmp_path, 'revised\n')
    file.write_text('edited by hand\n')
    with pytest.raises(ValueError, match='candidate digest'):
        arm_run.run_arm(SWEEP, candidate=file, repo=repo, runner=never)


def test_a_range_candidate_that_would_join_the_next_line_is_refused(repo, tmp_path):
    with pytest.raises(ValueError, match='newline'):
        arm_run.run_arm(SWEEP, candidate=candidate(tmp_path, 'no newline', (2, 2)), repo=repo, runner=never)


@pytest.mark.parametrize('path', ['evals/scenarios/s.json', 'README.md', '../outside.md', 'src/pkg/link.md'])
def test_a_target_outside_src_or_through_a_link_is_refused(repo, tmp_path, path):
    (tmp_path / 'outside.md').write_text(BASE)
    (repo / 'src/pkg/link.md').symlink_to('../../README.md')
    (repo / 'README.md').write_bytes(BASE.encode())
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'link')
    with pytest.raises(ValueError):
        arm_run.run_arm(SWEEP, candidate=candidate(tmp_path, 'revised\n', path=path), repo=repo, runner=never)
    assert (repo / 'README.md').read_text() == BASE


@pytest.mark.parametrize('args', [['--world', 'appworld'], ['--world=appworld'], ['--worker', 'both'],
                                  ['--worker=both']])
def test_sweeps_that_cannot_make_a_comparable_arm_are_refused_before_running(repo, args):
    with pytest.raises(ValueError):
        arm_run.run_arm(args, target=TARGET, repo=repo, runner=never)


@pytest.mark.parametrize('options', [{}, {'equal_retries': True}])
def test_the_arm_needs_exactly_one_text(repo, tmp_path, options):
    with pytest.raises(ValueError):
        arm_run.run_arm(SWEEP, repo=repo, runner=never, **options)
    with pytest.raises(ValueError):
        arm_run.run_arm(SWEEP, candidate=candidate(tmp_path, 'revised\n'), target=TARGET, repo=repo, runner=never,
                        **options)
    with pytest.raises(ValueError):
        arm_run.run_arm(SWEEP, candidate=candidate(tmp_path, 'revised\n'), equal_retries=True, repo=repo,
                        runner=never)


def test_an_arm_record_inside_the_repository_is_refused_before_running(repo):
    with pytest.raises(ValueError, match='outside the repository'):
        arm_run.run_arm(SWEEP, target=TARGET, repo=repo, runner=never, out=repo / 'arm.json')
    with pytest.raises(ValueError, match='outside the repository'):
        arm_run.run_arm(SWEEP, target=TARGET, repo=repo, runner=never, out=revise.REPO / 'arm.json')


@pytest.mark.parametrize('runner', [sweep(exit_code=2), sweep(exit_code=3), sweep(run={'stopped': {'skipped': 1}}),
                                    sweep([])])
def test_an_incomplete_sweep_writes_no_record_and_removes_the_copy(repo, tmp_path, home, runner):
    seen = {}

    def tracked(root, args):
        seen['root'] = Path(root)
        return runner(root, args)
    with pytest.raises(ValueError):
        arm_run.run_arm(SWEEP, target=TARGET, repo=repo, runner=tracked, out=tmp_path / 'arm.json')
    assert not (tmp_path / 'arm.json').exists() and not seen['root'].exists()


def test_a_sweep_with_trend_regressions_still_writes_the_arm(repo, tmp_path):
    assert arm_run.run_arm(SWEEP, target=TARGET, repo=repo, runner=sweep(exit_code=1)).exists()


def test_the_copy_is_removed_when_the_sweep_raises(repo):
    seen = {}

    def broken(root, args):
        seen['root'] = Path(root)
        raise RuntimeError('boom')
    with pytest.raises(RuntimeError):
        arm_run.run_arm(SWEEP, target=TARGET, repo=repo, runner=broken)
    assert not seen['root'].exists()


# --- the real process boundary, with a stand-in sweep entry point ---------------------------------

FAKE_ENTRY = """
import json, os, sys
from pathlib import Path
import agentos_eval
home = Path(os.environ['AGENTOS_EVAL_HOME'])
home.mkdir(parents=True, exist_ok=True)
(home / 'run.json').write_text(json.dumps({'run': {'id': 'r', 'worker': 'codex'}}))
seen = {'argv': sys.argv[1:], 'cwd': os.getcwd(), 'pythonpath': os.environ['PYTHONPATH'],
        'package': agentos_eval.__file__, 'home': str(home)}
(home / 'seen.json').write_text(json.dumps(seen))
print('report: ' + str(home / 'run.json'))
print('log: ' + str(home / 'x.eval'))
sys.exit(int(os.environ.get('FAKE_EXIT', '0')))
"""


def test_the_default_runner_runs_the_copys_own_sweep_entry_point(tmp_path, home, monkeypatch):
    copy = tmp_path / 'copy'
    (copy / 'src').mkdir(parents=True)
    package = copy / 'evals' / 'agentos_eval'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / '__main__.py').write_text(FAKE_ENTRY)
    monkeypatch.setenv('FAKE_EXIT', '1')
    monkeypatch.setenv('PYTHONPATH', str(ROOT / 'evals'))
    logs = []

    def read_records(location):
        logs.append(location)
        return [record('dev-a')]
    result = arm_run.run_sweep(copy, ['--epochs', '3'], read_records=read_records)
    seen = json.loads((home / 'seen.json').read_text())
    assert result == {'exit_code': 1, 'run': {'id': 'r', 'worker': 'codex'}, 'records': [record('dev-a')]}
    assert logs == [str(home / 'x.eval')]
    assert seen['argv'] == ['sweep', '--epochs', '3']
    assert Path(seen['package']).resolve().is_relative_to(copy.resolve())
    assert Path(seen['cwd']).resolve().is_relative_to(copy.resolve())
    parts = [Path(p).resolve() for p in seen['pythonpath'].split(os.pathsep)]
    assert parts[:2] == [(copy / 'src').resolve(), (copy / 'evals').resolve()]
    assert (ROOT / 'evals').resolve() not in parts


def test_a_sweep_that_prints_no_report_is_an_error(tmp_path):
    copy = tmp_path / 'copy'
    package = copy / 'evals' / 'agentos_eval'
    package.mkdir(parents=True)
    (copy / 'src').mkdir()
    (package / '__init__.py').write_text('')
    (package / '__main__.py').write_text('raise SystemExit("no judge key")')
    with pytest.raises(ValueError, match='no report'):
        arm_run.run_sweep(copy, [], read_records=never)


# --- CLI ------------------------------------------------------------------------------------------

def test_cli_arm_passes_sweep_arguments_after_the_separator_untouched(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake(sweep_args, **options):
        seen.update(options, sweep_args=sweep_args)
        return tmp_path / 'arm.json'
    monkeypatch.setattr(arm_run, 'run_arm', fake)
    assert cli.main(['revise', 'arm', '--current', '--target', TARGET, '--lines', '1-2', '--equal-retries',
                     '--', '--worker', 'codex', '--worker-model', 'm', '--epochs', '6']) == 0
    assert seen['sweep_args'] == ['--worker', 'codex', '--worker-model', 'm', '--epochs', '6']
    assert (seen['target'], seen['lines'], seen['equal_retries'], seen['candidate']) == (
        Path(TARGET), (1, 2), True, None)
    assert str(tmp_path / 'arm.json') in capsys.readouterr().out
    assert cli.main(['revise', 'arm', '--candidate', 'c.txt', '--', '--epochs', '3']) == 0
    assert seen['candidate'] == Path('c.txt') and seen['target'] is None and seen['sweep_args'] == ['--epochs', '3']


@pytest.mark.parametrize('argv', [['revise', 'arm'], ['revise', 'arm', '--current', '--candidate', 'c.txt'],
                                  ['revise', 'arm', '--current']])
def test_cli_arm_needs_a_text_to_run(monkeypatch, argv):
    monkeypatch.setattr(arm_run, 'run_sweep', never)
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    assert stop.value.code not in (0, None)


# --- room on dev ----------------------------------------------------------------------------------

META = {'dev-a': {'split': 'dev', 'source': 'bundled'}, 'dev-b': {'split': 'dev', 'source': 'bundled'},
        'held-a': {'split': 'heldout', 'source': 'bundled'}, 'mine': {'split': 'dev', 'source': 'local'}}
DIGEST = text_digest(BASE)


def baseline(records, digest=DIGEST, **extra):
    return {'schema': 'revision-arm/1', 'role': 'current', 'text_digest': digest, 'target_path': TARGET,
            'lines': None, 'records': records, **extra}


def room(arm, measure='passed'):
    return revise.check_room(arm, {'path': TARGET, 'lines': None, 'digest': DIGEST}, meta=META, measure=measure)


def test_a_dev_measure_already_at_its_maximum_leaves_no_room():
    # Held-out and owner-store failures give no room: only dev must be able to improve.
    full = [record('dev-a'), record('dev-b'), record('held-a', passed=0.0), record('mine', passed=0.0)]
    with pytest.raises(ValueError, match='no_room_on_dev'):
        room(baseline(full))


@pytest.mark.parametrize('rows', [
    [record('dev-a'), record('dev-b', passed=0.0)],
    [record('dev-a', 1), record('dev-a', 2, 0.5)],
    [record('dev-a', work={'w': {'refused_effects': 1}})],  # a refused run scores 0, as in the comparator
])
def test_a_dev_shortfall_leaves_room(rows):
    room(baseline(rows))


@pytest.mark.parametrize('arm, message', [
    (baseline([record('held-a', passed=0.0)]), 'no dev'),
    (baseline([record('mine', passed=0.0)]), 'no dev'),
    (baseline([record('dev-a', judge_status='judge_error')]), 'incomplete'),
    (baseline([record('dev-a', passed=0.0), record('dev-b', work={})]), 'incomplete'),
    (baseline([record('unknown', passed=0.0)]), 'unknown'),
    (baseline([record('dev-a', passed=0.0)], digest=text_digest('other')), 'current text'),
    (baseline([record('dev-a', passed=0.0)], role='candidate'), 'baseline'),
    ({'records': []}, 'baseline'),
    (None, 'baseline'),
])
def test_a_baseline_that_cannot_show_room_is_refused(arm, message):
    with pytest.raises(ValueError, match=message):
        room(arm)


def test_room_is_measured_on_the_named_measure():
    rows = [record('dev-a', value={'passed': 1.0, 'rubric_x': 0.5})]
    room(baseline(rows), 'rubric_x')
    with pytest.raises(ValueError, match='incomplete'):
        room(baseline(rows), 'rubric_missing')


def propose_argv(tmp_path, *extra):
    cause = tmp_path / 'cause.txt'
    cause.write_text('The text stops too early.')
    return ['revise', 'propose', '--target', 'src/personal_agent/bundled_skills/agentos-skills/SKILL.md',
            '--cause-kind', 'regression', '--cause-source', 'x', '--cause-file', str(cause),
            '--proposer', 'fake/model', '--out', str(tmp_path / 'out'), *extra]


@pytest.mark.parametrize('passed, message', [(None, '--baseline'), (1.0, 'no_room_on_dev')])
def test_cli_propose_refuses_before_any_model_call(tmp_path, monkeypatch, passed, message):
    from agentos_eval import scenarios
    dev = next(s['id'] for s in scenarios.load(include_local=False) if s['split'] == 'dev')
    made = []
    monkeypatch.setattr(revise, 'make_generate', lambda *a, **k: made.append(a) or (never, {}, lambda: None))
    extra = []
    if passed is not None:
        target = revise.load_target('src/personal_agent/bundled_skills/agentos-skills/SKILL.md')
        arm = tmp_path / 'baseline.json'
        arm.write_text(json.dumps({**baseline([record(dev, passed=passed)], target['digest']),
                                   'target_path': target['path']}))
        extra = ['--baseline', str(arm)]
    with pytest.raises(SystemExit) as stop:
        cli.main(propose_argv(tmp_path, *extra))
    assert message in str(stop.value.code) if passed is not None else stop.value.code not in (0, None)
    assert not made and not (tmp_path / 'out').exists()


def test_cli_propose_has_no_force(tmp_path):
    with pytest.raises(SystemExit) as stop:
        cli.main(propose_argv(tmp_path, '--force'))
    assert stop.value.code == 2
