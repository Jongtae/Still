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
    identity = dict(base_sha='a' * 40, head_sha='b' * 40, model_id='pinned', route='judgment', effort='low', cli_version='fixture')
    options = dict(budget={'calls_cap': 6}, evidence_class='fixture',
                   rollback={'kind': 'git-revert', 'command': 'git revert ' + 'b' * 40,
                             'rerun': 'python -m agentos_eval.paired --help'})
    return selection, paired, identity, options


def build(inputs):
    selection, paired, identity, options = inputs
    return promotion.build_record(selection, paired, identity, **options)


def test_schema_and_no_mutation(inputs):
    original = deepcopy(inputs)
    record = build(inputs)
    assert promotion.validate_record(record) is record
    assert record['decision'] == 'promote'
    assert record['budget'] == {'calls_cap': 6, 'actual': 6, 'exceeded': False}
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
    record = build(inputs)
    assert record['budget']['exceeded'] and record['budget']['actual'] == 6
    inputs[3]['budget']['calls_cap'] = 6
    inputs[0]['unmatched'] = [{'field': 'paths', 'value': 'unknown.py'}]
    assert build(inputs)['selection']['unmatched'] == inputs[0]['unmatched']


def test_subscription_usage_never_coerced_to_zero(inputs):
    assert build(inputs)['cost']['usage'] == 'unavailable'
    inputs[1]['cost']['usage'] = 0
    with pytest.raises(ValidationError):
        build(inputs)


def test_rollback_exact_sha_and_setting(inputs):
    inputs[3]['rollback']['command'] = 'git revert HEAD'
    with pytest.raises(ValueError, match='exact candidate SHA'):
        build(inputs)
    inputs[3]['rollback'] = {'kind': 'setting', 'command': 'restore route=judgment model=prior-id effort=low',
                            'rerun': 'python -m agentos_eval.paired --help'}
    assert build(inputs)['rollback']['kind'] == 'setting'
    del inputs[3]['rollback']['rerun']
    with pytest.raises(ValidationError):
        build(inputs)


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
    for name, value in zip(('selection', 'paired', 'change-identity', 'rollback'), (*inputs[:3], inputs[3]['rollback'])):
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value))
        argv.extend(['--' + name, str(path)])
    output = tmp_path / 'record.json'
    assert promotion.main([*argv, '--calls-cap', '6', '--evidence-class', 'actual-cli-synthetic',
                           '--output', str(output)]) == 0
    record = json.loads(output.read_text())
    assert record['evidence_class'] == 'actual-cli-synthetic'
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
    return selected, comparison


def test_end_to_end_selection_paired_record_schema_and_markdown():
    from jsonschema import Draft202012Validator
    selected, comparison = measured_contract_inputs()
    assert comparison['comparable'] and comparison['heldout']['verdict'] == 'better'
    record = promotion.build_record(selected, comparison, promotion.identity_from_pair(comparison),
        budget={'calls_cap': comparison['cost']['calls']}, evidence_class='fixture',
        rollback={'kind': 'git-revert', 'command': 'git revert ' + 'b' * 40, 'rerun': 'fixture-only rerun'})
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
        '--rollback', str(files['rollback']), '--calls-cap', str(comparison['cost']['calls']),
        '--evidence-class', 'fixture', '--output', str(out)]) == 0
    identity = json.loads(out.read_text())['change_identity']
    assert identity['model_id'] == 'gpt-4o-mini' and identity['route'] == 'direct_api'
    assert identity['base_sha'] == 'a' * 40 and identity['head_sha'] == 'b' * 40
    assert identity['effort'] is None and identity['cli_version'] is None
    assert identity['tool_schema_digests'] == identity['instructions_digests'] == {'baseline': None, 'candidate': None}
