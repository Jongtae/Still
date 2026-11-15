"""#855: task B's public paired shape, hand-built fixtures, no model calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest
from jsonschema import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import promotion


REAL_GIT_COMMITS = promotion._git_commits


@pytest.fixture(autouse=True)
def fixture_commit_range(monkeypatch):
    monkeypatch.setattr(promotion, '_git_commits', lambda base, head, **kw: [head])


@pytest.fixture
def inputs():
    selection = {'change': {**{k: [] for k in ('kinds', 'paths', 'prompt_fragments', 'propositions', 'routes')},
                            'description': ''},
                 'selected': [{'set': 'judgments', 'id': 'dev-example', 'split': 'dev', 'reasons': ['Prompt changed']}],
                 'unmatched': []}
    paired = {'comparable': True,
              'conditions': {'baseline': {'model': 'pinned', 'effort': 'low', 'fixture': 'v1'},
                             'candidate': {'model': 'pinned', 'effort': 'low', 'fixture': 'v1'},
                             'differences': [], 'unknown': []},
              'cases': [{'id': 'heldout-example', 'split': 'heldout', 'baseline': {'passed': 0, 'trials': 3},
                         'candidate': {'passed': 3, 'trials': 3}, 'change': 'fixed', 'spread': 0.0, 'difference': 1.0}],
              'heldout': {'verdict': 'better', 'reasons': ['Stable held-out improvement'],
                          'fixed': ['heldout-example'], 'regressed': [], 'spread': 0.0, 'difference': 1.0},
              'cost': {'calls': 6, 'usage': 'unavailable', 'baseline': {'calls': 3, 'usage': 'unavailable'}, 'candidate': {'calls': 3, 'usage': 'unavailable'}}, 'latency': {arm: {'p50_ms': 10.0, 'p95_ms': 20.0} for arm in ('baseline', 'candidate')}}
    paired.update(evidence_class={'baseline': 'fixture', 'candidate': 'fixture'},
                  revisions={'baseline': 'a' * 40, 'candidate': 'b' * 40},
                  plan=dict(max_calls=6, planned_calls=6, cases_argument='dev-example', seed='fixture-seed', model='pinned', repetitions=3))
    for arm in ('baseline', 'candidate'):
        paired['conditions'][arm] = {'pinned_model': 'pinned', 'runtime.requested_model': 'pinned',
            'runtime.transport': 'subscription_cli', 'runtime.effort': 'low', 'runtime.cli_version': 'fixture',
            'runtime.engine': 'fixture-engine', 'runtime.model_policy': 'explicit'}
    identity = dict(base_sha='a' * 40, head_sha='b' * 40, model_id='pinned', route='subscription_cli', effort='low', cli_version='fixture')
    options = dict(budget={'calls_cap': 6}, evidence_class='fixture',
                   rollback=None)
    return selection, paired, identity, options


def build(inputs):
    selection, paired, identity, options = inputs
    return promotion.build_record(selection, paired, identity, **options)


def test_schema_and_no_mutation(inputs):
    original = deepcopy(inputs)
    record = build(inputs)
    assert promotion.validate_record(record) is record
    assert record['decision'] == 'promote'
    assert record['budget'] == {'calls_cap': 6, 'planned_calls': 6, 'actual': 6, 'exceeded': False}
    assert record['change_identity']['instructions_digests'] == {'baseline': None, 'candidate': None}
    assert inputs == original


@pytest.mark.parametrize('verdict,decision', [('better', 'promote'), ('worse', 'roll-back'),
    ('no-gain', 'inconclusive'), ('inconclusive', 'inconclusive'), ('incomparable', 'inconclusive')])
def test_decision_rule(inputs, verdict, decision):
    inputs[1]['heldout']['verdict'] = verdict
    assert build(inputs)['decision'] == decision


def test_unequal_and_unknown_conditions_are_inconclusive(inputs):
    inputs[1]['comparable'] = False
    inputs[1]['conditions']['differences'] = [{'field': 'model', 'baseline': 'a', 'candidate': 'b'}]
    assert build(inputs)['decision'] == 'inconclusive'
    inputs[1]['comparable'] = True
    assert build(inputs)['decision'] == 'inconclusive'  # contradictory comparable flag never promotes
    inputs[1]['conditions']['differences'] = []
    inputs[1]['conditions']['unknown'] = ['effort']
    assert build(inputs)['decision'] == 'inconclusive'


def test_certain_heldout_regression_rolls_back(inputs):
    inputs[1]['heldout'].update(verdict='worse', regressed=['heldout-example'])
    inputs[1]['cases'][0]['change'] = 'regressed'
    assert build(inputs)['decision'] == 'roll-back'


def test_promotion_does_not_reapply_a_pooled_spread_gate(inputs):
    # One certain fix among many unchanged cases can have a tiny mean gain.
    inputs[1]['heldout'].update(spread=1, difference=0.01)
    assert build(inputs)['decision'] == 'promote'
    inputs[1]['heldout']['verdict'] = 'inconclusive'
    assert build(inputs)['decision'] == 'inconclusive'


def test_budget_overrun_and_unmatched_disclosed(inputs):
    inputs[3]['budget']['calls_cap'] = 5
    inputs[1]['plan']['max_calls'] = 5
    record = build(inputs)
    assert record['budget']['exceeded'] and record['budget']['actual'] == 6
    inputs[3]['budget']['calls_cap'] = 6
    inputs[1]['plan']['max_calls'] = 6
    inputs[0]['unmatched'] = [{'field': 'paths', 'value': 'unknown.py'}]
    assert build(inputs)['selection']['unmatched'] == inputs[0]['unmatched']


def test_subscription_usage_never_coerced_to_zero(inputs):
    assert build(inputs)['cost']['usage'] == 'unavailable'
    inputs[1]['cost']['usage'] = 0
    with pytest.raises(ValidationError):
        build(inputs)


def test_rollback_exact_sha_and_setting(inputs):
    record = build(inputs)
    record['rollback']['command'] = 'git revert HEAD'
    with pytest.raises(ValueError, match='exact identity base/head'):
        promotion.validate_record(record)
    inputs[1]['revisions']['baseline'] = inputs[2]['base_sha'] = 'b' * 40
    record = build(inputs)
    assert record['rollback']['kind'] == 'setting'
    assert record['rollback']['setting']['requested_model'] == 'pinned'
    assert record['rollback']['command'].startswith('POST /api/decision-route/activate ')
    del record['rollback']['rerun']
    with pytest.raises(ValidationError):
        promotion.validate_record(record)


def test_tampered_record_fails_validation(inputs):
    record = build(inputs)
    record['decision'] = 'roll-back'
    with pytest.raises(ValueError, match='contradicts'):
        promotion.validate_record(record)
    record = build(inputs)
    record['extra'] = True
    with pytest.raises(ValidationError):
        promotion.validate_record(record)
    record = build(inputs)
    record['budget']['actual'] = 0
    with pytest.raises(ValueError, match='observed calls'):
        promotion.validate_record(record)


def test_markdown_contains_receipt_and_escapes_cells(inputs):
    inputs[0]['selected'][0]['reasons'] = ['reason | with <markup>\nand line']
    record = build(inputs)
    text = promotion.markdown(record)
    assert 'unavailable' in text and '6 / 6' in text
    assert 'heldout-example' in text and record['rollback']['command'] in text
    assert 'reason &#124; with &lt;markup&gt;<br>and line' in text
    assert 'Fixture, actual-CLI synthetic' in text


def test_cli_writes_json_and_template(tmp_path, inputs):
    argv = []
    for name, value in zip(('selection', 'paired', 'change-identity'), inputs[:3]):
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value))
        argv.extend(['--' + name, str(path)])
    output = tmp_path / 'record.json'
    assert promotion.main([*argv, '--calls-cap', '6', '--evidence-class', 'fixture',
                           '--output', str(output)]) == 0
    record = json.loads(output.read_text())
    assert record['evidence_class'] == 'fixture'
    assert output.with_suffix('.md').read_text() == promotion.markdown(record)


def test_heldout_summary_cannot_name_an_unobserved_fix(inputs):
    inputs[1]['cases'] = []
    with pytest.raises(ValueError, match='unobserved case'):
        build(inputs)


def measured_contract_inputs():
    """Public A/B seam: injected model transport -> run/report -> compare -> record."""
    from agentos_eval import judgments as j, paired, selection
    from test_eval_judgments import Transport, answers, engine
    selected = selection.select({'propositions': ['goal_reached'], 'tags': ['exp-recall']})
    chosen = paired._selection_rows(selected)
    cases, splits, _ = paired.select_cases(chosen['ids'])
    reports = []
    for candidate in (False, True):
        scripted = []
        for case in cases['cases']:
            for _ in range(3):
                reply = answers(case)[0]
                if case['id'] == 'heldout-goal-served' and not candidate:
                    reply = {**reply, 'answer': False}
                scripted.append(reply)
        transport, audit = Transport(scripted=scripted), []
        routed = engine(transport, audit)
        rows = j.run(cases, j.still().conversation_handoff.ConversationJudgments(routed), routed,
                     audit=audit, roles=['goal_reached'], timer=transport.clock, trials=3)
        runtime = dict(active=dict(transport='direct_api', requested_model='gpt-4o-mini', effort=None,
                                  provider='fixture-provider', destination='synthetic.invalid'), suite_version='fixture/1')
        result = j.report(rows, {'git_head': ('b' if candidate else 'a') * 40}, runtime, cases=cases)
        result.update(catalogue_digest=paired._digest(cases['catalogue']), pinned_model='gpt-4o-mini',
                      splits=splits, selected_not_run=chosen['selected_not_run'])
        reports.append(result)
    # Simulate the prompt-file identities of two synthetic report arms; no model
    # or test result is used to tune a held-out fixture.
    reports[0]['prompt_digests'] = {'personal_agent/prompts/goal_reached.md': 'before', 'unchanged.md': 'same'}
    reports[1]['prompt_digests'] = {'personal_agent/prompts/goal_reached.md': 'after', 'unchanged.md': 'same'}
    comparison = paired.compare_paired(*reports, splits=splits)
    comparison['plan'] = dict(max_calls=comparison['cost']['calls'], planned_calls=comparison['cost']['calls'],
                              cases_argument='@selection.json', seed='synthetic-terra', model='gpt-4o-mini', repetitions=3)
    return selected, comparison


def test_end_to_end_selection_paired_record_schema_and_markdown():
    from jsonschema import Draft202012Validator
    selected, comparison = measured_contract_inputs()
    assert comparison['comparable'] and comparison['heldout']['verdict'] == 'better'
    record = promotion.build_record(selected, comparison, promotion.identity_from_pair(comparison),
        budget={'calls_cap': comparison['cost']['calls']}, evidence_class='fixture')
    schema = json.loads((ROOT / 'evals/schemas/promotion-record-v1.json').read_text())
    Draft202012Validator(schema).validate(record)
    assert record['latency'] == comparison['latency']
    assert record['cost'] == comparison['cost'] and set(record['cost']) >= {'baseline', 'candidate'}
    assert record['heldout'] == comparison['heldout']
    assert record['selected_not_run'] and record['selected_not_run'] == comparison['selected_not_run']
    assert record['change_identity']['prompt_digests'] == {
        'baseline': {'personal_agent/prompts/goal_reached.md': 'before'},
        'candidate': {'personal_agent/prompts/goal_reached.md': 'after'}}
    assert 'Work turns, not on judgment calls' in record['change_identity']['digest_unavailable_reasons']['instructions_digests']
    text = promotion.markdown(record)
    assert '<details>' in text and '</details>' in text
    assert '"recall"' in text and '"scenarios"' in text
    assert all(row['id'] in text for row in record['selected_not_run'])
    assert 'unchanged.md' not in text


def test_from_pair_cli_derives_identity_without_typing_model_route_or_digests(tmp_path):
    selected, comparison = measured_contract_inputs()
    files = {}
    for name, value in {'selection': selected, 'pair': comparison,
                        'rollback': {'kind': 'git-revert', 'command': 'git revert ' + 'b' * 40,
                                     'rerun': 'fixture-only rerun'}}.items():
        files[name] = tmp_path / (name + '.json')
        files[name].write_text(json.dumps(value))
    out = tmp_path / 'record.json'
    assert promotion.main(['--selection', str(files['selection']), '--from-pair', str(files['pair']),
        '--calls-cap', str(comparison['cost']['calls']),
        '--evidence-class', 'fixture', '--output', str(out)]) == 0
    identity = json.loads(out.read_text())['change_identity']
    assert identity['model_id'] == 'gpt-4o-mini' and identity['route'] == 'direct_api'
    assert identity['base_sha'] == 'a' * 40 and identity['head_sha'] == 'b' * 40
    assert identity['effort'] is None and identity['cli_version'] is None
    assert identity['tool_schema_digests'] == identity['instructions_digests'] == {'baseline': None, 'candidate': None}


def test_verifier_evidence_class_cannot_be_relabelled(inputs):
    inputs[1]['evidence_class'] = {'baseline': 'fixture', 'candidate': 'fixture'}
    inputs[3]['evidence_class'] = 'owner-live'
    with pytest.raises(ValueError, match='evidence'):
        build(inputs)


def test_verifier_budget_comes_from_plan_and_overrun_cannot_promote(inputs):
    inputs[1]['plan'].update(max_calls=5, planned_calls=5)
    inputs[3]['budget'] = {'calls_cap': 5}
    record = build(inputs)
    assert record['budget'] == dict(calls_cap=5, planned_calls=5, actual=6, exceeded=True)
    assert record['decision'] == 'inconclusive'
    inputs[3]['budget']['calls_cap'] = 600
    with pytest.raises(ValueError, match='plan'):
        build(inputs)


def test_verifier_from_pair_generates_range_rollback_and_bound_rerun(tmp_path, monkeypatch):
    selected, comparison = measured_contract_inputs()
    comparison['plan'] = dict(max_calls=150, planned_calls=150, cases_argument='@selection.json',
                              seed='synthetic-terra', model='gpt-4o-mini', repetitions=3)
    monkeypatch.setattr(promotion, '_git_commits', lambda *args, **kwargs: ['b' * 40], raising=False)
    source, chosen, out = tmp_path / 'pair.json', tmp_path / 'selection.json', tmp_path / 'record.json'
    source.write_text(json.dumps(comparison)); chosen.write_text(json.dumps(selected))
    assert promotion.main(['--selection', str(chosen), '--from-pair', str(source), '--output', str(out)]) == 0
    record = json.loads(out.read_text())
    assert record['rollback']['commits'] == ['b' * 40]
    assert record['rollback']['command'] == 'git revert --no-edit ' + 'a' * 40 + '..' + 'b' * 40
    import shlex
    rerun = shlex.split(record['rollback']['rerun'])
    assert rerun[rerun.index('--baseline') + 1] == 'b' * 40
    assert rerun[rerun.index('--candidate') + 1] == '<REVERT_SHA>'
    for flag, value in [('--cases', '@selection.json'), ('--seed', 'synthetic-terra'), ('--model', 'gpt-4o-mini')]:
        assert rerun[rerun.index(flag) + 1] == value
    record['rollback']['rerun'] = record['rollback']['rerun'].replace('synthetic-terra', 'different-seed')
    with pytest.raises(ValueError, match='rerun'):
        promotion.validate_record(record)


@pytest.mark.parametrize('classes,manifests,expected', [
    (['fixture', 'fixture'], {}, 'fixture'),
    (['live-route:subscription_cli'] * 2, {a: {'owner_data': 'none', 'created_at': 'fixture-time', 'requested_model': 'pinned'} for a in ('baseline', 'candidate')}, 'actual-cli-synthetic'),
    (['owner-live'] * 2, {}, 'owner-live'),
    (['fixture', 'owner-live'], {}, None),
    (['live-route:subscription_cli'] * 2, {}, None),
])
def test_evidence_derives_from_both_arms_and_manifest(inputs, classes, manifests, expected):
    inputs[1]['evidence_class'] = dict(zip(('baseline', 'candidate'), classes))
    inputs[1]['seed_manifests'] = manifests
    inputs[3]['evidence_class'] = None
    if expected is None:
        with pytest.raises(ValueError, match='evidence'):
            build(inputs)
    else:
        record = build(inputs)
        assert record['evidence_class'] == expected
        record['evidence_class'] = 'fixture' if expected != 'fixture' else 'owner-live'
        with pytest.raises(ValueError, match='evidence'):
            promotion.validate_record(record)


def test_budget_tampering_and_overrun_worse_are_inconclusive(inputs):
    inputs[1]['plan']['max_calls'] = 5
    inputs[3]['budget'] = None
    inputs[1]['heldout']['verdict'] = 'worse'
    record = build(inputs)
    assert record['decision'] == 'inconclusive'
    record['budget']['planned_calls'] += 1
    with pytest.raises(ValueError, match='plan'):
        promotion.validate_record(record)


def test_range_commits_and_every_rerun_parameter_are_bound(inputs):
    record = build(inputs)
    for key, value in [('commits', ['c' * 40]), ('command', 'git revert --no-edit HEAD~1..HEAD'),
                       ('rerun', record['rollback']['rerun'].replace('pinned', 'other-model'))]:
        changed = deepcopy(record)
        changed['rollback'][key] = value
        with pytest.raises(ValueError):
            promotion.validate_record(changed)
    for key, value in [('seed', 'other-seed'), ('cases', '@other.json'), ('repetitions', 7), ('max_calls', 999)]:
        changed = deepcopy(record)
        changed['change_identity']['run'][key] = value
        with pytest.raises(ValueError, match='plan'):
            promotion.validate_record(changed)


def test_real_git_range_order_is_read_only():
    # Exercise Git's actual history boundary, without creating a commit or worktree.
    import subprocess
    head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'c3876f6'], capture_output=True, text=True, check=True).stdout.strip()
    base = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'c3876f6^'], capture_output=True, text=True, check=True).stdout.strip()
    assert REAL_GIT_COMMITS(base, head) == [head]


def test_reused_arm_provenance_and_only_new_spend_enter_record(inputs):
    paired = inputs[1]
    reused = dict(arm='candidate', report_sha256='f' * 64, measured_at='2026-11-14T00:00:00+00:00',
                  report_path='/saved/candidate.json')
    paired['plan'].update(max_calls=3, planned_calls=3, requalify_attempts=2, reused_arm=reused,
                          report_paths={'candidate': '/saved/candidate.json'})
    paired.update(reused_arm=reused, time_gap_seconds=60, time_gap_proxy_seconds=None,
                  time_gap_source='arm completion timestamps')
    paired['cost'].update(new_calls=3, reused_calls=3)
    inputs[3]['budget'] = {'calls_cap': 3}
    record = build(inputs)
    assert record['budget']['actual'] == 3 < record['cost']['calls'] == 6
    assert not record['budget']['exceeded']
    assert record['decision'] == 'promote'
    assert record['reused_arm'] == reused and record['time_gap_seconds'] == 60
    assert record['change_identity']['run']['requalify_attempts'] == 2
    assert '--reuse-arm baseline=/saved/candidate.json' in record['rollback']['rerun']
    assert 'Historical reused calls: 3' in promotion.markdown(record)
    assert 'arm completion timestamps' in promotion.markdown(record)
    changed = deepcopy(record)
    changed['budget']['actual'] = 6
    with pytest.raises(ValueError, match='observed calls'):
        promotion.validate_record(changed)
    changed = deepcopy(record)
    changed['reused_arm']['report_sha256'] = 'e' * 64
    with pytest.raises(ValueError, match='provenance'):
        promotion.validate_record(changed)
