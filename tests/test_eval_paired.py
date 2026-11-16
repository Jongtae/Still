"""#855 paired judgments: synthetic fixtures, fake engine/clock/git, no live calls."""
import copy
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import judgments as j, paired as p, synthetic_seed
from test_decision_routes import ServiceFixture


@pytest.fixture(autouse=True)
def fixture_eval_home(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path / 'eval-home'))
    monkeypatch.setattr(synthetic_seed, '_system_temp_roots', lambda: {Path('/fixture-system-temp')})


@pytest.fixture
def seed_path():
    path = p.eval_home() / 'seeds/fixture-seed'
    path.mkdir(parents=True)
    (path / 'synthetic-seed.json').write_text(json.dumps(dict(owner_data='none', created_at='2026-11-14', requested_model='pinned-model')))
    return path


SPLITS = {'dev-case': 'dev', 'held-case': 'heldout'}


def report(outcomes=None, *, model='pinned-model', transport='subscription_cli'):
    """Standard judgments.report envelope, not a separate report format."""
    j.load_still(ROOT / 'src')
    outcomes = outcomes or {'dev-case': [True] * 3, 'held-case': [False] * 3}
    rows = []
    for ident, values in outcomes.items():
        for trial, passed in enumerate(values, 1):
            rows.append(dict(id=ident, role='goal_reached', **{'class': 'bounded'}, lang='en',
                             category='completion', targets=[], trial=trial, retries=int(trial > 1),
                             semantic_correct=passed, routing_correct=None, schema_valid=True,
                             outcome='decided', error='none' if passed else 'false_completion',
                             calls=[{'usage': {'input_tokens': 8, 'output_tokens': 2}}], timeouts=0,
                             decision_seconds=.1 * trial, end_to_end_seconds=.2 * trial,
                             startup_seconds=None, models=[], observed={}))
    active = dict(transport=transport, requested_model=model, engine='fixture-engine',
                  model_policy='explicit', cli_version='fixture-cli/1', effort=None,
                  provider='fixture-provider', destination='synthetic.invalid')
    result = j.report(rows, {}, dict(active=active, suite_version='fixture-suite/1'),
                      cases={'fixture': 'unchanged'})
    result['catalogue_digest'] = 'unchanged-catalogue'
    result['pinned_model'] = model
    result['splits'] = {ident: SPLITS.get(ident, 'heldout') for ident in outcomes}
    return result


def compare(before, after):
    return p.compare_paired(before, after, splits=before['splits'])


def test_partner_partition_covers_existing_cases_and_new_cases_once():
    data = p.load_partners()
    old = j.load_cases(ROOT / 'evals/judgments/cases-v1.json')
    held = j.load_cases(ROOT / 'evals/judgments/heldout-v1.json')
    added = j.load_cases(ROOT / 'evals/judgments/cases-v2-202.json')
    added['cases'] += j.load_cases(ROOT / 'evals/judgments/cases-v3-202-plan.json')['cases']
    dev_ids = {ident for part in data['roles'].values() for ident in part['dev']}
    held_ids = {ident for part in data['roles'].values() for ident in part['heldout']}
    moved = 'heldout-goal-legitimate-question'
    assert dev_ids == ({c['id'] for c in old['cases']} | {moved} |
                       {c['id'] for c in added['cases'] if not c['id'].startswith('heldout-')})
    assert held_ids == (({c['id'] for c in held['cases']} - {moved}) |
                        {c['id'] for c in added['cases'] if c['id'].startswith('heldout-')})
    assert not dev_ids & held_ids
    assert set(data['roles']) == {c['role'] for c in old['cases']}
    lookup = {c['id']: c for c in old['cases'] + held['cases'] + added['cases']}
    assert len(p.partners(sorted(dev_ids))) == 90
    assert data['version'] == 'judgment-heldout-partners/3'
    assert data['case_sets'] == ['evals/judgments/cases-v1.json', 'evals/judgments/heldout-v1.json',
                                 'evals/judgments/cases-v2-202.json', 'evals/judgments/cases-v3-202-plan.json']
    for part in data['roles'].values():
        for dev in part['dev']:
            assert part['partners'].get(dev) or part['unpaired'].get(dev)
            for link in part['partners'].get(dev, []):
                assert link['id'] in held_ids and link['reason'].strip()
                assert lookup[dev]['input'] != lookup[link['id']]['input']
    frozen, splits, _ = p.select_cases(['followup-retry-ko', 'followup-retry-en'])
    assert len(frozen['cases']) == 3  # Shared partner isn't double-counted.
    assert list(splits.values()).count('heldout') == 1


def test_202_contrasts_have_bilingual_development_and_fresh_partners():
    data = p.load_partners()
    cases, _, _ = p._case_data(data, ROOT)
    part = data['roles']['goal_reached']
    outcomes = {'failed-hedge': 'no', 'unused-method': 'no', 'second-method': 'yes',
                'no-other-method': 'yes', 'rough-request': 'yes', 'saved-assume-ask': 'yes',
                'saved-ask-only': 'no', 'owner-date': 'yes'}
    for kind, outcome in outcomes.items():
        for lang in ('en', 'ko'):
            ident = f'goal-202-{kind}-{lang}'
            assert ident in part['dev']
            assert cases[ident]['lang'] == lang
            assert cases[ident]['expect'] == {'outcome': outcome}
            assert cases[ident]['targets'] == ['#202']
            [link] = part['partners'][ident]
            assert link['id'] == f'heldout-202-{kind}-{lang}'
            assert link['reason']
            assert link['id'] in part['heldout']
            assert cases[link['id']]['expect'] == {'outcome': outcome}
            assert cases[ident]['input'] != cases[link['id']]['input']
    for ident in ('goal-legitimate-question', 'goal-legitimate-question-ko', 'heldout-goal-legitimate-question'):
        assert ident in part['dev'] and ident not in part['heldout']
        assert all(link['id'].startswith('heldout-202-preference-only-') for link in part['partners'][ident])
        assert all('#202' in link['reason'] and '#827' in link['reason'] for link in part['partners'][ident])


def test_v1_partner_mapping_remains_readable_and_byte_identical():
    import hashlib
    path = ROOT / 'evals/judgments/heldout-partners-v1.json'
    # Historical partition remains available for historical comparisons.
    p.validate_partners(json.loads(path.read_text()))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == '25be29792efadfd883c21ecb86f7c8949b5e1bd27423d342795467199ccb200e'


def test_v2_mapping_is_frozen_and_v3_preserves_all_its_partitions():
    import hashlib
    path = ROOT / 'evals/judgments/heldout-partners-v2.json'
    old = p.validate_partners(json.loads(path.read_text()))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == 'd8b8419f5099d4ff90a416054c1cafc121706562ac3c5873d7b2614ac1c45727'
    data = p.load_partners()
    assert data['case_sets'][:-1] == old['case_sets']
    for role, part in old['roles'].items():
        new = data['roles'][role]
        for split in ('dev', 'heldout'):
            assert new[split][:len(part[split])] == part[split]
        assert all(new['partners'][ident] == links for ident, links in part['partners'].items())
        assert new['unpaired'] == part['unpaired']


def test_plan_reading_contrasts_have_bilingual_fresh_heldout_partners():
    data = p.load_partners()
    cases, _, _ = p._case_data(data, ROOT)
    part = data['roles']['goal_reached']
    outcomes = {'met': 'yes', 'ignored-lookup': 'no', 'overbroad': 'yes', 'narrowed': 'no', 'conversation': 'yes'}
    for kind, outcome in outcomes.items():
        for lang in ('en', 'ko'):
            ident = f'goal-202-plan-{kind}-{lang}'
            assert ident in part['dev']
            [link] = part['partners'][ident]
            assert link['id'] == f'heldout-202-plan-{kind}-{lang}'
            assert link['id'] in part['heldout']
            a, b = cases[ident], cases[link['id']]
            assert a['lang'] == b['lang'] == lang
            assert a['expect'] == b['expect'] == {'outcome': outcome}
            assert a['targets'] == b['targets'] == ['#202']
            for field in ('request', 'observations', 'answer', 'plan_reading'):
                assert a['input'][field] != b['input'][field]
            assert a['input']['plan_reading'] and b['input']['plan_reading']
            if kind == 'conversation':
                assert a['input']['conversation'] != b['input']['conversation']
            frozen, splits, _ = p.select_cases([ident])
            assert {case['id'] for case in frozen['cases']} == {ident, link['id']}
            assert splits == {ident: 'dev', link['id']: 'heldout'}


@pytest.mark.parametrize('mutation', ['overlap', 'missing', 'unknown', 'wrong-role', 'reason'])
def test_partner_validation_rejects_bad_partitions(mutation):
    data = p.load_partners()
    part = next(iter(data['roles'].values()))
    dev = part['dev'][0]
    if mutation == 'overlap':
        part['heldout'].append(dev)
    elif mutation == 'missing':
        del part['partners'][dev]
    elif mutation == 'unknown':
        part['partners'][dev][0]['id'] = 'not-a-case'
    elif mutation == 'wrong-role':
        part['dev'].append('goal-served')
    else:
        part['partners'][dev][0]['reason'] = ''
    with pytest.raises(p.PairedError):
        p.validate_partners(data)


def test_partners_can_record_explicitly_unpaired_dev_and_reject_unknown_ids():
    data = p.load_partners()
    part = next(iter(data['roles'].values()))
    dev = part['dev'][0]
    # The other dev in this role still references the shared held-out case.
    del part['partners'][dev]
    part['unpaired'][dev] = 'No independent partner for this proposition.'
    p.validate_partners(data)
    with pytest.raises(p.PairedError):
        p.partners(['missing-case'])


def test_unequal_model_is_incomparable_and_listed():
    result = compare(report(), report(model='another-model'))
    assert not result['comparable']
    assert result['heldout']['verdict'] == 'incomparable'
    assert {'field': 'runtime.requested_model', 'baseline': 'pinned-model',
            'candidate': 'another-model'} in result['conditions']['differences']
    assert 'runtime.requested_model differs' in result['judgment_comparison']['reasons']


def test_missing_cli_version_is_unknown_even_if_missing_in_both():
    a, b = report(), report()
    del a['runtime']['active']['cli_version']
    del b['runtime']['active']['cli_version']
    result = compare(a, b)
    assert not result['comparable']
    assert 'runtime.cli_version' in result['conditions']['unknown']


def test_unpinned_or_moving_routes_are_unverified_even_if_both_arms_match():
    a, b = report(), report()
    del a['pinned_model']
    assert 'pinned_model' in compare(a, b)['conditions']['unknown']
    a['pinned_model'] = 'pinned-model'
    a['runtime']['active']['model_policy'] = b['runtime']['active']['model_policy'] = 'fastest_qualified'
    assert not compare(a, b)['comparable']
    a['runtime']['active']['model_policy'] = b['runtime']['active']['model_policy'] = 'explicit'
    b['pinned_model'] = 'wrong-model'
    assert 'candidate.verified-model-pin' in compare(a, b)['conditions']['unknown']


@pytest.mark.parametrize('key', ['case_set_version', 'case_set_digest', 'catalogue_digest'])
def test_changed_fixture_identity_refuses_pairing(key):
    a, b = report(), report()
    b[key] = 'changed'
    result = compare(a, b)
    assert result['heldout']['verdict'] == 'incomparable'
    assert key in {diff['field'] for diff in result['conditions']['differences']}


def test_missing_effort_differs_from_explicit_default_and_model_policy_is_checked():
    a, b = report(), report()
    assert compare(a, b)['comparable']  # Explicit null effort is a known default.
    del a['runtime']['active']['effort']
    b['runtime']['active']['model_policy'] = 'fastest_qualified'
    result = compare(a, b)
    assert 'runtime.effort' in result['conditions']['unknown']
    assert 'runtime.model_policy' in {diff['field'] for diff in result['conditions']['differences']}


def test_destination_and_partition_changes_cannot_hide_behind_the_same_model():
    a, b = report(), report()
    b['runtime']['active']['destination'] = 'another-synthetic.invalid'
    b['splits']['dev-case'], b['splits']['held-case'] = 'heldout', 'dev'
    result = compare(a, b)
    assert not result['comparable']
    fields = {diff['field'] for diff in result['conditions']['differences']}
    assert {'runtime.destination', 'splits'} <= fields
    # Passing a replacement partition cannot relabel existing observations.
    result = p.compare_paired(a, a, splits=b['splits'])
    assert 'baseline.verified-split-assignment' in result['conditions']['unknown']


def test_stable_heldout_fix_without_regression_is_better_dev_is_not_pooled():
    a = report({'dev-case': [True] * 3, 'held-case': [False] * 3})
    b = report({'dev-case': [False] * 3, 'held-case': [True] * 3})
    result = compare(a, b)
    assert result['comparable']
    assert result['heldout']['verdict'] == 'better'
    assert result['heldout']['fixed'] == ['held-case']
    assert result['heldout']['spread'] == 0
    assert result['heldout']['difference'] == 1
    assert next(row for row in result['cases'] if row['split'] == 'dev')['change'] == 'regressed'


def test_any_heldout_regression_is_worse_even_when_total_rate_improves():
    a = report({'held-case': [True] * 3, 'held-other': [False] * 3, 'held-third': [False] * 3})
    b = report({'held-case': [False] * 3, 'held-other': [True] * 3, 'held-third': [True] * 3})
    result = compare(a, b)
    assert result['heldout']['difference'] > 0
    assert result['heldout']['verdict'] == 'worse'
    assert result['heldout']['regressed'] == ['held-case']


def test_repetition_spread_over_difference_is_inconclusive():
    a = report({'held-case': [False, False, False]})
    b = report({'held-case': [False, True, True]})
    result = compare(a, b)
    assert result['heldout']['verdict'] == 'inconclusive'
    assert result['heldout']['spread'] == 1
    assert result['heldout']['difference'] == pytest.approx(2 / 3)


def test_stable_equal_results_have_no_gain():
    baseline, candidate = report(), report()
    assert 'judge' not in baseline and 'judge' not in candidate
    result = compare(baseline, candidate)
    assert result['comparable'] is True
    assert result['heldout']['verdict'] == 'no-gain'
    assert result['heldout']['fixed'] == result['heldout']['regressed'] == []


def test_dev_fix_alone_cannot_claim_heldout_gain():
    a = report({'dev-case': [False] * 3})
    b = report({'dev-case': [True] * 3})
    assert compare(a, b)['heldout']['verdict'] == 'inconclusive'


def test_missing_or_duplicate_trials_and_unrun_results_are_not_fixes():
    for mutation in ('missing', 'duplicate', 'not-run', 'missing-split'):
        a = report()
        b = report({'dev-case': [True] * 3, 'held-case': [True] * 3})
        if mutation == 'missing':
            b['rows'].pop()
        elif mutation == 'duplicate':
            b['rows'].append(copy.deepcopy(b['rows'][-1]))
        elif mutation == 'not-run':
            b['rows'][-1].update(semantic_correct=None, outcome='not_run')
        else:
            del a['splits']['held-case']
        assert not compare(a, b)['comparable'], mutation


def test_mixed_trial_directions_are_shown_and_not_promoted():
    a = report({'held-case': [True, False, True]})
    b = report({'held-case': [False, True, True]})
    result = compare(a, b)
    assert result['cases'][0]['change'] == 'mixed'
    assert result['heldout']['verdict'] == 'inconclusive'


def test_subscription_usage_is_unavailable_not_zero_and_latency_is_per_arm():
    a, b = report(), report()
    a['requalification'] = {'calls': 11}
    result = compare(a, b)
    assert result['cost']['usage'] == 'unavailable'
    assert result['cost']['baseline'] == {'calls': 17, 'usage': 'unavailable'}
    assert result['cost']['candidate'] == {'calls': 6, 'usage': 'unavailable'}
    assert result['cost']['calls'] == 23
    assert result['latency']['baseline'] == {'p50_ms': 400, 'p95_ms': 600}


def test_direct_api_tokens_are_reported_only_when_observed():
    a, b = report(transport='direct_api'), report(transport='direct_api')
    result = compare(a, b)
    assert result['cost']['baseline']['usage'] == {'input_tokens': 48, 'output_tokens': 12}
    assert result['cost']['usage'] == {'input_tokens': 96, 'output_tokens': 24}
    for row in b['rows']:
        del row['calls'][0]['usage']
    assert compare(a, b)['cost']['usage'] == 'unavailable'


class FakeGit:
    def __init__(self):
        self.calls, self.paths = [], []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        assert kwargs == dict(check=True, capture_output=True, text=True)
        command = argv[3:]
        if command[0] == 'rev-parse':
            assert command[1:3] == ['--verify', '--end-of-options']
            return SimpleNamespace(stdout=command[-1].removesuffix('^{commit}') + '-sha\n')
        if command[:2] == ['worktree', 'add']:
            path = Path(command[-2])
            path.mkdir()
            self.paths.append(path)
        return SimpleNamespace(stdout='')


def test_run_pair_freezes_cases_pins_each_revision_and_cleans_up(tmp_path, capsys, seed_path):
    git = FakeGit()
    requests = []
    def arm(request):
        # Plan is printed before even an injected engine can spend a call.
        assert 'Planned calls:' in capsys.readouterr().out or requests
        assert Path(request['checkout']).exists()
        assert request['model'] == 'pinned-model' and request['live']
        cases = j.load_cases(request['cases_path'])
        requests.append(request)
        output = report({c['id']: [bool(len(requests) - 1)] * 3 for c in cases['cases']})
        output.update(splits=request['splits'], git_head=request['commit'])
        return output
    result = p.run_pair('base', 'candidate', ['goal-failed'], model='pinned-model',
                        seed=seed_path, live=True, max_calls=51, git_runner=git,
                        arm_runner=arm, out=tmp_path, requalify_attempts=1)
    assert len(requests) == 2
    assert requests[0]['cases_path'] == requests[1]['cases_path']
    assert requests[0]['max_calls'] == requests[1]['max_calls'] == 25
    assert result['plan']['measurement_calls'] == 12
    assert result['plan']['unused_call_allowance'] == 1
    assert all(not path.exists() for path in git.paths)
    assert len([argv for argv in git.calls if argv[3:5] == ['worktree', 'remove']]) == 2
    assert {path.name for path in tmp_path.glob('*.json')} == {'baseline.json', 'candidate.json', 'comparison.json'}
    assert result['heldout']['verdict'] == 'better'


def test_worktree_is_cleaned_up_on_arm_failure(seed_path):
    git = FakeGit()
    def fail(_):
        raise RuntimeError('fixture failure')
    with pytest.raises(RuntimeError, match='fixture failure'):
        p.run_pair('base', 'candidate', ['goal-failed'], model='pinned-model', seed=seed_path,
                   live=True, max_calls=50, git_runner=git, arm_runner=fail, requalify_attempts=1)
    assert len(git.paths) == 1 and not git.paths[0].exists()
    assert git.calls[-1][3:6] == ['worktree', 'remove', '--force']


def test_default_plan_spends_nothing_even_with_engine_and_caps(tmp_path, seed_path):
    def forbidden(*args, **kwargs):
        pytest.fail('Plan tried to check out a revision or run an engine')
    result = p.run_pair('base', 'candidate', ['goal-failed'], model='pinned-model',
                        seed=seed_path, max_calls=50, git_runner=forbidden, arm_runner=forbidden, requalify_attempts=1)
    assert not result['live'] and not result['owner_data']
    with pytest.raises(p.PairedError, match='--model'):
        p.run_pair('base', 'candidate', ['goal-failed'], model='', max_calls=50, requalify_attempts=1)
    with pytest.raises(p.PairedError, match='--max-calls'):
        p.run_pair('base', 'candidate', ['goal-failed'], model='pinned-model',
                   seed=seed_path, live=True, max_calls=1, git_runner=forbidden, requalify_attempts=1)


def test_cli_requires_model_and_live_optin_and_can_compare_saved_reports(tmp_path):
    common = ['--baseline', 'base', '--candidate', 'candidate', '--cases', 'goal-failed',
              '--max-calls', '50', '--out', str(tmp_path)]
    with redirect_stderr(io.StringIO()) as err, pytest.raises(SystemExit) as exc:
        p.main(common)
    assert exc.value.code == 2 and '--model' in err.getvalue()
    with redirect_stdout(io.StringIO()):
        assert p.main([*common, '--model', 'pinned-model']) == 0
    assert (tmp_path / 'plan.json').exists() and not (tmp_path / 'baseline.json').exists()
    a, b = tmp_path / 'a.json', tmp_path / 'b.json'
    a.write_text(json.dumps(report()))
    b.write_text(json.dumps(report({'dev-case': [True] * 3, 'held-case': [True] * 3})))
    with redirect_stdout(io.StringIO()):
        assert p.main(['--compare', str(a), str(b), '--out', str(tmp_path)]) == 0
    assert json.loads((tmp_path / 'comparison.json').read_text())['heldout']['verdict'] == 'better'


def test_selection_json_input_is_supported_and_unsupported_sets_are_explicit(tmp_path):
    path = tmp_path / 'selection.json'
    path.write_text(json.dumps({'selected': [{'set': 'judgments', 'id': 'goal-failed'}]}))
    assert p._selection('@' + str(path)) == {'ids': ['goal-failed'], 'selected_not_run': [], 'cases_argument': '@' + str(path)}
    path.write_text(json.dumps({'selected': [{'set': 'recall', 'id': 'other'}]}))
    with pytest.raises(p.PairedError, match='own runners'):
        p._selection('@' + str(path))


class TestMeasuredArm(ServiceFixture):
    """Real service/pinning/qualification/admission; fixture CLI transport only."""
    def test_measurement_reuses_shipped_pin_suite_and_callcap(self):
        cases, splits, data = p.select_cases(['goal-served'])
        path = Path(self.root) / 'frozen.json'
        path.write_text(json.dumps(cases))
        request = dict(checkout=str(ROOT), commit='fixture-sha', cases_path=str(path),
                       model='gpt-5.6-terra', seed='fixture-seed', max_calls=13, repetitions=3,
                       splits=splits, partner_version=data['version'], partner_digest=p._digest(data))
        def factory(folder):
            # The copied seed owns the store; keep the fixture CLI transport.
            from personal_agent.quickstart_store import QuickStore
            self.store = QuickStore(folder)
            return self.service()
        synthetic_seed.build('fixture-seed', model='gpt-5.6-terra', factory=factory)
        seed_bytes = (p.eval_home() / 'seeds/fixture-seed/private/quickstart.db').read_bytes()
        times = iter([float(v) for v in range(100)])
        with patch.object(j, 'pin_model', wraps=j.pin_model) as pin:
            result = p.measure_arm(request, factory=factory, timer=lambda: next(times))
        pin.assert_called_once()
        assert result['pinned_model'] == 'gpt-5.6-terra'
        assert result['git_head'] == 'fixture-sha'
        assert result['evidence_class'] == 'fixture'
        assert result['requalification']['calls'] == 11
        assert result['call_budget']['admitted'] == 13
        assert result['call_budget']['refused']
        assert len(result['rows']) == 6
        assert sum(len(row['calls']) for row in result['rows']) == 2
        assert {row['id'] for row in result['rows']} == set(splits)
        calls = [call['argv'] for call in self.runner.calls if '--output-schema' in call['argv']]
        assert len(calls) == 13
        assert {argv[argv.index('--model') + 1] for argv in calls} == {'gpt-5.6-terra'}
        assert p._cost(result)['usage'] == 'unavailable'
        assert result['seed_manifest']['owner_data'] == 'none'
        assert (p.eval_home() / 'seeds/fixture-seed/private/quickstart.db').read_bytes() == seed_bytes
        assert list((p.eval_home() / 'sandboxes').iterdir()) == []


def test_fresh_process_uses_measured_checkout_and_fixture_engine(tmp_path):
    """Cross the actual process/source boundary, without a git or model network call."""
    checkout = tmp_path / 'revision'
    shutil.copytree(ROOT / 'src', checkout / 'src', ignore=shutil.ignore_patterns('__pycache__'))
    prompt = next((checkout / 'src/personal_agent/prompts').glob('*.md'))
    prompt.write_text(prompt.read_text() + '\nSynthetic revision marker for process-isolation test.\n')
    cases, splits, data = p.select_cases(['goal-served'])
    frozen = tmp_path / 'cases.json'
    frozen.write_text(json.dumps(cases))
    request = dict(checkout=str(checkout), commit='fixture-revision', cases_path=str(frozen),
                   model='gpt-5.6-terra', seed='fixture-seed', max_calls=17, repetitions=3,
                   splits=splits, partner_version=data['version'], partner_digest=p._digest(data))
    request_file, result_file = tmp_path / 'request.json', tmp_path / 'result.json'
    request_file.write_text(json.dumps(request))
    script = '''
import json, sys
from pathlib import Path
from agentos_eval import paired as p
from test_decision_routes import ServiceFixture
from agentos_eval import synthetic_seed
synthetic_seed._system_temp_roots = lambda: {Path("/fixture-system-temp")}
fixture = ServiceFixture()
fixture.setUp()
try:
    from personal_agent.quickstart_store import QuickStore
    p.j.load_still(Path(json.loads(Path(sys.argv[1]).read_text())['checkout']) / 'src')
    def factory(data):
        fixture.store = QuickStore(data)
        return fixture.service()
    synthetic_seed.build('fixture-seed', model='gpt-5.6-terra', factory=factory)
    result = p.measure_arm(json.loads(Path(sys.argv[1]).read_text()), factory=factory, timer=lambda: 0.)
    Path(sys.argv[2]).write_text(json.dumps(result))
finally:
    fixture.doCleanups()
'''
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / 'evals'), str(ROOT / 'tests'), str(checkout / 'src')]))
    child = subprocess.run([sys.executable, '-c', script, str(request_file), str(result_file)],
                           cwd=checkout, env=env, capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stderr
    result = json.loads(result_file.read_text())
    assert Path(result['src']).resolve() == (checkout / 'src').resolve()
    assert result['git_head'] == 'fixture-revision' and result['evidence_class'] == 'fixture'
    assert result['requalification']['calls'] == 11
    assert result['summary']['total']['calls'] == 6
    assert result['call_budget']['admitted'] == 17
    relative = str(prompt.relative_to(checkout / 'src'))
    import hashlib
    assert result['prompt_digests'][relative] == hashlib.sha256(prompt.read_bytes()).hexdigest()[:12]


@pytest.mark.parametrize('baseline,candidate,change,spread,difference', [
    ([False] * 3, [True] * 3, 'fixed', 0, 1),
    ([True, False, False], [True] * 3, 'uncertain-fixed', 1, 2 / 3),
    ([False] * 3, [True, True, False], 'uncertain-fixed', 1, 2 / 3),
    ([True] * 3, [True, False, False], 'uncertain-regressed', 1, -2 / 3),
    ([True] * 3, [False] * 3, 'regressed', 0, -1),
    ([False], [True], 'fixed', 0, 1),
])
def test_uncertainty_is_measured_per_case(baseline, candidate, change, spread, difference):
    result = compare(report({'held-case': baseline}), report({'held-case': candidate}))
    row = result['cases'][0]
    assert row['change'] == change and row['spread'] == spread
    assert row['difference'] == pytest.approx(difference)
    assert result['heldout']['counts'] == {change: 1}
    assert result['counts'] == {change: 1}
    if change.startswith('uncertain-'):
        assert result['heldout']['verdict'] == 'inconclusive'
        assert result['heldout']['fixed'] == result['heldout']['regressed'] == []


def test_a_certain_fix_is_not_diluted_by_other_cases_spread():
    a = report({'held-case': [False] * 3, 'held-noisy': [True, False, True]})
    b = report({'held-case': [True] * 3, 'held-noisy': [True, False, True]})
    result = compare(a, b)
    assert result['heldout']['verdict'] == 'better'
    assert result['heldout']['spread'] == 1
    assert result['heldout']['difference'] == pytest.approx(.5)
    assert result['heldout']['fixed'] == ['held-case']


def test_certain_fix_with_lower_mean_from_uncertain_losses_is_inconclusive():
    a = report({'held-case': [False] * 3, 'held-two': [True] * 3, 'held-three': [True] * 3})
    b = report({'held-case': [True] * 3, 'held-two': [True, False, False], 'held-three': [True, False, False]})
    result = compare(a, b)
    assert result['heldout']['difference'] < 0
    assert result['heldout']['regressed'] == []
    assert result['heldout']['verdict'] == 'inconclusive'


def test_mixed_selection_runs_judgments_and_lists_unrun_sets(tmp_path, seed_path):
    path = tmp_path / 'selection.json'
    path.write_text(json.dumps({'selected': [
        {'set': 'judgments', 'id': 'goal-served', 'split': 'dev'},
        {'set': 'recall', 'id': 'r-one', 'split': 'heldout'},
        {'set': 'qualification', 'id': 'qualification-parked-request-withdrawn', 'split': None}]}))
    chosen = p._selection('@' + str(path))
    def arm(request):
        cases = j.load_cases(request['cases_path'])
        result = report({c['id']: [True] * 3 for c in cases['cases']})
        result['splits'] = request['splits']
        for row in result['rows']:
            if request['splits'].get(row['id']) is None:
                row['role'] = 'qualification-suite'
        return result
    result = p.run_pair('base', 'candidate', chosen, model='pinned-model', seed=seed_path,
                        max_calls=100, live=True, git_runner=FakeGit(), arm_runner=arm, requalify_attempts=1)
    assert result['selected_not_run'] == result['plan']['selected_not_run'] == chosen['selected_not_run']
    assert {row['set'] for row in result['selected_not_run']} == {'recall'}
    assert all(row['reason'] == p.NOT_RUN_REASON for row in result['selected_not_run'])


def test_selection_deduplicates_an_explicit_dev_and_its_partner():
    partner = p.partners(['goal-served'])['goal-served'][0]
    cases, splits, _ = p.select_cases(['goal-served', partner])
    assert len(cases['cases']) == 2
    assert splits == {'goal-served': 'dev', partner: 'heldout'}


@pytest.mark.parametrize('kind', ['missing', 'owner-data', 'missing-time'])
def test_invalid_synthetic_manifest_fails_before_checkout(kind, seed_path):
    manifest = seed_path / 'synthetic-seed.json'
    if kind == 'missing':
        manifest.unlink()
    else:
        data = json.loads(manifest.read_text())
        if kind == 'owner-data':
            data['owner_data'] = 'copied'
        else:
            del data['created_at']
        manifest.write_text(json.dumps(data))
    def forbidden(*a, **kw):
        pytest.fail('Invalid seed tried to run')
    with pytest.raises(p.PairedError, match='manifest'):
        p.run_pair('base', 'candidate', ['goal-served'], model='pinned-model', seed=seed_path,
                   live=True, max_calls=50, git_runner=forbidden, arm_runner=forbidden, requalify_attempts=1)


def test_live_requires_a_seed_and_stops_sandbox_on_prepare_failure(seed_path, tmp_path):
    with pytest.raises(p.PairedError, match='--seed'):
        p.run_pair('base', 'candidate', ['goal-served'], model='pinned-model', live=True, max_calls=50, requalify_attempts=1)
    class FailingSandbox:
        stopped = False
        def __init__(self, root, *, seed, src):
            assert root == p.eval_home() / 'sandboxes' and seed == seed_path
        def prepare(self):
            raise RuntimeError('fixture prepare failure')
        def stop(self):
            FailingSandbox.stopped = True
    cases, splits, data = p.select_cases(['goal-served'])
    source = tmp_path / 'cases.json'
    source.write_text(json.dumps(cases))
    request = dict(seed=str(seed_path), checkout=str(ROOT), cases_path=str(source),
                   model='pinned-model', max_calls=50, repetitions=3)
    with pytest.raises(RuntimeError, match='fixture prepare failure'):
        p.measure_arm(request, sandbox_factory=FailingSandbox)
    assert FailingSandbox.stopped


@pytest.mark.parametrize('field,value', [('requested_model', 'different-request'), ('observed_model', 'different-observation')])
def test_verifier_pin_checked_against_every_observed_model(field, value):
    a, b = report(), report()
    entry = dict(zip(j.MODEL_FIELDS, ['subscription_cli', 'fixture-engine', 'fixture-provider',
                                     'pinned-model', 'not reported', 'explicit']))
    entry[field] = value
    for arm in (a, b):
        arm['runtime']['observed_models'] = [[entry[key] for key in j.MODEL_FIELDS]]
    result = compare(a, b)
    assert not result['comparable']
    assert value in str(result['conditions']['unknown'])


def test_verifier_model_observation_claim_is_explicit():
    a, b = report(), report()
    for arm in (a, b):
        arm['runtime']['observed_models'] = [['subscription_cli', 'fixture-engine', 'fixture-provider',
                                            'pinned-model', 'not reported', 'explicit']]
    result = compare(a, b)
    assert result['comparable']
    condition = result['conditions']['candidate']['model_observation']
    assert not condition['reported']
    assert condition['claim'] == 'requested model pinned; observed model not reported by the CLI'


def test_verifier_sampling_needs_three_trials_unless_both_deterministic():
    a, b = report({'held-case': [False]}), report({'held-case': [True]})
    a['deterministic'] = b['deterministic'] = False
    result = compare(a, b)
    assert result['heldout']['verdict'] == 'inconclusive'
    assert 'three' in ' '.join(result['heldout']['reasons'])
    a['deterministic'] = b['deterministic'] = True
    assert compare(a, b)['heldout']['verdict'] == 'better'
    b['deterministic'] = False
    assert compare(a, b)['heldout']['verdict'] == 'inconclusive'


def test_verifier_live_refuses_one_or_two_trials_before_any_checkout(seed_path):
    for repetitions in (1, 2):
        with pytest.raises(p.PairedError, match='three'):
            p.run_pair('base', 'candidate', ['goal-served'], model='pinned-model', seed=seed_path,
                       repetitions=repetitions, live=True, max_calls=50,
                       arm_runner=lambda _: pytest.fail('spent calls'), requalify_attempts=1)


def test_verifier_uncertain_regression_blocks_better_even_with_positive_mean():
    a = report({'held-case': [False] * 3, 'held-other': [True] * 3})
    b = report({'held-case': [True] * 3, 'held-other': [True, False, False]})
    result = compare(a, b)
    assert result['heldout']['difference'] > 0
    assert result['heldout']['verdict'] == 'inconclusive'
    assert 'uncertain' in ' '.join(result['heldout']['reasons'])


def test_verifier_qualification_selection_runs_suite_and_counts_calls():
    qualification = 'qualification-' + j.still().decision_qualification.CASE_IDS[0]
    chosen = p._selection_rows({'selected': [dict(set='judgments', id='goal-served', split='dev'),
                                           dict(set='qualification', id=qualification, split=None)]})
    plan = p.run_pair('base', 'candidate', chosen, model='pinned-model', max_calls=1000, requalify_attempts=1)
    assert not plan['selected_not_run']
    assert qualification in plan['cases']
    assert plan['measurement_calls'] == 2 * 3 * (2 + 11)
    assert plan['planned_calls'] == plan['measurement_calls'] + 22


def test_verifier_worker_stderr_and_timeout_are_exposed_and_worktree_removed(seed_path, monkeypatch):
    request = dict(checkout=str(ROOT), planned_calls=7)
    def failed(*args, **kwargs):
        assert kwargs['timeout'] >= 7 * 120
        raise subprocess.CalledProcessError(2, args[0], stderr='fixture worker reason')
    monkeypatch.setattr(p.subprocess, 'run', failed)
    with pytest.raises(p.PairedError, match='fixture worker reason'):
        p._process_arm(request)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'], stderr=b'fixture stalled')
    monkeypatch.setattr(p.subprocess, 'run', timeout)
    git = FakeGit()
    with pytest.raises(p.PairedError, match='timed out'):
        p.run_pair('base', 'candidate', ['goal-served'], model='pinned-model', seed=seed_path,
                   live=True, max_calls=50, git_runner=git, requalify_attempts=1)
    assert git.calls[-1][3:6] == ['worktree', 'remove', '--force']


class TestMeasuredQualificationArm(ServiceFixture):
    def test_selected_qualification_rows_are_measured_separately_from_setup(self):
        from personal_agent.quickstart_store import QuickStore
        def factory(folder):
            self.store = QuickStore(folder)
            return self.service()
        synthetic_seed.build('fixture-seed', model='gpt-5.6-terra', factory=factory)
        cases, splits, data = p.select_cases(['goal-served'])
        ids = ['qualification-' + ident for ident in j.still().decision_qualification.CASE_IDS]
        splits.update({ident: None for ident in ids})
        source = Path(self.root) / 'cases.json'
        source.write_text(json.dumps(cases))
        request = dict(seed='fixture-seed', checkout=str(ROOT), commit='fixture-sha', cases_path=str(source),
                       model='gpt-5.6-terra', max_calls=50, repetitions=3, splits=splits,
                       partner_version=data['version'], partner_digest=p._digest(data), qualification_selected=[ids[0]])
        result = p.measure_arm(request, factory=factory, timer=lambda: 0.)
        measured = [row for row in result['rows'] if row['role'] == 'qualification-suite']
        assert len(measured) == 33
        assert {row['id'] for row in measured} == set(ids)
        assert result['requalification']['calls'] == 11
        assert result['summary']['total']['calls'] == 39
        assert result['call_budget']['admitted'] == 50
        assert p._cost(result)['calls'] == 50
        comparison = p.compare_paired(result, result, splits=splits)
        assert comparison['comparable']
        assert {row['id'] for row in comparison['cases'] if row['set'] == 'qualification'} == set(ids)


@pytest.mark.parametrize('attempts', [0, 4, -1, True])
def test_requalify_attempt_bounds(attempts):
    with pytest.raises(p.PairedError, match='requalify-attempts'):
        p.run_pair('a', 'b', ['goal-served'], model='pinned-model', max_calls=100, requalify_attempts=attempts)


def test_requalify_default_plans_two_full_suites_without_calls():
    planned = p.run_pair('a', 'b', ['goal-served'], model='pinned-model', max_calls=100)
    assert planned['requalify_attempts'] == 2
    assert planned['qualification_calls_per_arm'] == 22
    assert planned['planned_calls'] == 2 * (6 + 22)


@pytest.mark.parametrize('unavailable_arm', ['baseline', 'candidate'])
def test_unavailable_all_not_run_arm_is_always_incomparable(unavailable_arm):
    a, b = report(), report()
    unavailable = a if unavailable_arm == 'baseline' else b
    unavailable['runtime']['active']['available'] = False
    for row in unavailable['rows']:
        row.update(outcome='not_run', semantic_correct=None, calls=[])
    result = compare(a, b)
    assert not result['comparable']
    assert result['heldout']['verdict'] == 'incomparable'
    assert unavailable_arm + '.route-unavailable' in result['conditions']['unknown']


class TestRequalificationAttempts(ServiceFixture):
    def make_request(self, *, attempts=2, cap=50, fail_every=False):
        from personal_agent.quickstart_store import QuickStore
        from test_decision_routes import oracle
        def factory(folder):
            self.store = QuickStore(folder)
            return self.service()
        synthetic_seed.build('fixture-seed', model='gpt-5.6-terra', factory=factory)
        cases, splits, data = p.select_cases(['goal-served'])
        path = Path(self.root) / 'cases.json'
        path.write_text(json.dumps(cases))
        count = [0]
        def flaky(*args):
            value, confidence = oracle(*args)
            count[0] += 1
            if fail_every or count[0] == 3:
                value = not value if isinstance(value, bool) else next(v for v in args[3] if v != value)
            return value, confidence
        def copied(folder):
            service = factory(folder)
            self.runner.judges['gpt-5.6-terra'] = flaky
            return service
        request = dict(seed='fixture-seed', checkout=str(ROOT), commit='fixture-sha', cases_path=str(path),
                       model='gpt-5.6-terra', max_calls=cap, repetitions=3, splits=splits,
                       partner_version=data['version'], partner_digest=p._digest(data), requalify_attempts=attempts)
        return request, copied

    def test_failed_shipped_job_is_retried_and_only_job_can_qualify(self):
        request, factory = self.make_request(cap=20)
        with patch.object(j, 'pin_model', wraps=j.pin_model) as pin, patch.object(j, 'requalify_route', wraps=j.requalify_route) as job:
            result = p.measure_arm(request, factory=factory, timer=lambda: 0.)
        assert pin.call_count == job.call_count == 2
        assert [(a['state'], a['calls']) for a in result['requalification']['attempts']] == [('failed', 3), ('passed', 11)]
        assert result['requalification']['calls'] == 14
        assert result['call_budget']['admitted'] == 20
        assert result['summary']['total']['calls'] == 6
        assert result['status'] == 'available'

    def test_attempt_limit_and_shared_cap_never_admit_extra_calls(self):
        request, factory = self.make_request(attempts=3, cap=2, fail_every=True)
        result = p.measure_arm(request, factory=factory, timer=lambda: 0.)
        assert result['status'] == 'unavailable' and result['unavailable_reason']
        assert result['call_budget']['admitted'] == 2
        assert len(result['requalification']['attempts']) == 2
        assert result['requalification']['calls'] == 2
        assert all(row['outcome'] == 'not_run' for row in result['rows'])
        assert p.compare_paired(result, result, splits=request['splits'])['heldout']['verdict'] == 'incomparable'


def saved_candidate(tmp_path, seed_path, *, arm='candidate', measured=True):
    cases, splits, data = p.select_cases(['goal-served'])
    value = report({c['id']: [True] * 3 for c in cases['cases']})
    value.update(git_head=('b' if arm == 'candidate' else 'a') * 40, splits=splits,
                 partner_version=data['version'], partner_digest=p._digest(data),
                 case_set_digest=p._digest(cases), catalogue_digest=p._digest(cases['catalogue']),
                 seed_manifest=json.loads((seed_path / 'synthetic-seed.json').read_text()),
                 requalification={'calls': 11}, status='available')
    value['seed_manifest_digest'] = p._digest(value['seed_manifest'])
    if measured:
        value['measured_at'] = '2026-11-14T00:00:00+00:00'
    path = tmp_path / (arm + '.json')
    path.write_text(json.dumps(value))
    return path, value


@pytest.mark.parametrize('saved_arm', ['baseline', 'candidate'])
def test_reuse_runs_only_other_arm_and_preserves_old_bytes(tmp_path, seed_path, saved_arm):
    import hashlib
    path, saved = saved_candidate(tmp_path, seed_path, arm=saved_arm)
    original = path.read_bytes()
    git, requests = FakeGit(), []
    def engine(request):
        requests.append(request)
        cases = j.load_cases(request['cases_path'])
        result = report({c['id']: [True] * 3 for c in cases['cases']})
        result.update(splits=request['splits'], requalification={'calls': 14}, git_head=request['commit'],
                      measured_at='2026-11-14T00:01:00+00:00')
        return result
    result = p.run_pair('a' * 40, 'b' * 40, ['goal-served'], model='pinned-model', seed=seed_path,
                        max_calls=28, reuse_arm={saved_arm: path}, live=True, out=tmp_path,
                        git_runner=git, arm_runner=engine)
    assert len(requests) == len(git.paths) == 1
    assert requests[0]['max_calls'] == 28
    assert result['plan']['planned_calls'] == 28
    assert result['cost']['calls'] == 37 and result['cost']['new_calls'] == 20 and result['cost']['reused_calls'] == 17
    assert result['reused_arm']['report_sha256'] == hashlib.sha256(original).hexdigest()
    assert result['reused_arm']['arm'] == saved_arm
    assert result['time_gap_seconds'] == 60
    assert path.read_bytes() == original
    assert result['plan']['reused_arm'] == result['reused_arm']
    assert p.compare_paired(saved, saved, splits=saved['splits'])['heldout']['verdict'] == 'no-gain'


@pytest.mark.parametrize('field', ['git_head', 'splits', 'partner_digest', 'case_set_digest', 'catalogue_digest',
                                   'pinned_model', 'seed_manifest', 'seed_manifest_digest'])
def test_reuse_mismatches_are_rejected_before_spending(tmp_path, seed_path, field):
    path, saved = saved_candidate(tmp_path, seed_path)
    saved[field] = 'wrong' if field != 'git_head' else 'c' * 40
    path.write_text(json.dumps(saved))
    def no_calls(_):
        pytest.fail('Reuse mismatch spent calls')
    with pytest.raises(p.PairedError, match='Reused report'):
        p.run_pair('a' * 40, 'b' * 40, ['goal-served'], model='pinned-model', seed=seed_path,
                   max_calls=28, reuse_arm={'candidate': path}, live=True, git_runner=FakeGit(), arm_runner=no_calls)


def test_reuse_legacy_time_is_unknown_and_mtime_is_labelled_proxy(tmp_path, seed_path):
    path, saved = saved_candidate(tmp_path, seed_path, measured=False)
    plan = p.run_pair('a' * 40, 'b' * 40, ['goal-served'], model='pinned-model', seed=seed_path,
                      max_calls=28, reuse_arm='candidate=' + str(path), git_runner=FakeGit())
    assert plan['reused_arm']['measured_at'] is None
    assert plan['time_gap_seconds'] is None and plan['time_gap_proxy_seconds'] >= 0
    assert 'proxy' in plan['time_gap_source']


def test_reuse_cli_plan_is_read_only_and_duplicate_reuse_is_refused(tmp_path, seed_path):
    source, _ = saved_candidate(tmp_path, seed_path)
    out = tmp_path / 'plans'
    argv = ['--baseline', 'a' * 40, '--candidate', 'b' * 40, '--cases', 'goal-served',
            '--seed', str(seed_path), '--model', 'pinned-model', '--requalify-attempts', '2',
            '--max-calls', '28', '--reuse-arm', 'candidate=' + str(source), '--out', str(out)]
    # Use a narrow validation seam for CLI parsing; separate tests exercise Git.
    with patch.object(p, '_validate_reuse') as validate, patch.object(p, '_process_arm', side_effect=AssertionError('spent calls')):
        with redirect_stdout(io.StringIO()):
            assert p.main(argv) == 0
        validate.assert_called_once()
    assert json.loads((out / 'plan.json').read_text())['reused_arm']['arm'] == 'candidate'
    with redirect_stderr(io.StringIO()), pytest.raises(SystemExit):
        p.main([*argv, '--reuse-arm', 'baseline=' + str(source)])
