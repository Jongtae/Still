"""#855: selection picks the right cases for distinct change kinds and records why."""
import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import selection

CASES = json.loads((ROOT / 'evals/judgments/cases-v1.json').read_text())['cases']
RULES = json.loads((ROOT / 'evals/selection/affected-cases-v1.json').read_text())
RULES_BY_ID = {rule['id']: rule for rule in RULES['rules']}


def _ids(result, set_name):
    return {r['id'] for r in result['selected'] if r['set'] == set_name}


def _role_ids(role):
    return {c['id'] for c in CASES if c['role'] == role}


def _reasons(result, set_name, ident):
    rows = [r for r in result['selected'] if r['set'] == set_name and r['id'] == ident]
    assert len(rows) == 1
    return ' '.join(rows[0]['reasons'])


def _qualification_ids():
    return {e['id'] for e in selection.inventory() if e['set'] == 'qualification'}


def test_prompt_fragment_selects_its_judgment_role_with_rule_and_signal():
    result = selection.select({'prompt_fragments': ['goal_reached']})
    judged = _ids(result, 'judgments')
    assert _role_ids('goal_reached') <= judged
    assert not judged & _role_ids('connector_effect')
    assert not judged & _role_ids('owner_act_answer')
    for ident in _role_ids('goal_reached'):
        reason = _reasons(result, 'judgments', ident)
        assert 'role-goal_reached' in reason and 'prompt_fragments=goal_reached' in reason
    for row in result['selected']:
        assert row['reasons'] and all(r.strip() for r in row['reasons'])
        assert any('prompt_fragments=goal_reached' in r for r in row['reasons'])
    assert not result['unmatched']


def test_shared_prompt_path_reaches_every_consumer_group():
    path = 'src/personal_agent/prompts/ask_or_assume.md'
    result = selection.select({'paths': [path]})
    judged = _ids(result, 'judgments')
    assert _role_ids('goal_reached') <= judged
    assert _role_ids('orchestration_plan') <= judged
    assert _role_ids('orchestration_escalation') <= judged
    assert _ids(result, 'scenarios')
    for role, rule in (('goal_reached', 'role-goal_reached'), ('orchestration_plan', 'role-orchestration_plan')):
        for ident in _role_ids(role):
            reason = _reasons(result, 'judgments', ident)
            assert rule in reason and f'paths={path}' in reason
    for ident in _ids(result, 'scenarios'):
        reason = _reasons(result, 'scenarios', ident)
        assert 'worker-instructions' in reason and f'paths={path}' in reason
    assert not result['unmatched']


def test_judgment_proposition_selects_followups_and_qualification_not_goal_reached():
    result = selection.select({'propositions': ['FOLLOWUP_QUESTION']})
    judged = _ids(result, 'judgments')
    assert _role_ids('followup_relation') <= judged
    assert not judged & _role_ids('goal_reached')
    expected = set(RULES_BY_ID['role-followup_relation']['select'][1]['ids'])
    assert expected and expected <= _qualification_ids()
    assert _ids(result, 'qualification') == expected
    for row in result['selected']:
        text = ' '.join(row['reasons'])
        assert 'role-followup_relation' in text and 'propositions=FOLLOWUP_QUESTION' in text
    assert not result['unmatched']


def test_model_route_selects_every_role_and_qualification_id():
    keys = RULES_BY_ID['route-model-effort']['match']
    assert 'routes' in keys
    result = selection.select({'routes': ['example-route']})
    judged = _ids(result, 'judgments')
    assert {c['role'] for c in CASES if c['id'] in judged} >= {c['role'] for c in CASES}
    assert {c['id'] for c in CASES} <= judged
    assert _qualification_ids() <= _ids(result, 'qualification')
    for row in result['selected']:
        assert any('route-model-effort' in r and 'routes=example-route' in r for r in row['reasons'])
    assert not result['unmatched']


def test_unknown_change_selects_nothing_and_is_reported():
    result = selection.select({'paths': ['docs/TRADEMARKS.md']})
    assert result['selected'] == []
    assert result['unmatched'] == [{'field': 'paths', 'value': 'docs/TRADEMARKS.md'}]


def test_empty_change_reports_unmatched():
    result = selection.select({})
    assert result['selected'] == []
    assert result['unmatched']


def test_every_rule_has_a_nonempty_reason():
    assert RULES['rules']
    for rule in RULES['rules']:
        assert isinstance(rule['reason'], str) and rule['reason'].strip(), rule['id']


@pytest.fixture
def mirror(tmp_path):
    for sub in ('evals/judgments', 'evals/scenarios', 'evals/recall', 'evals/selection'):
        shutil.copytree(ROOT / sub, tmp_path / sub)
    (tmp_path / 'src').symlink_to(ROOT / 'src', target_is_directory=True)
    return tmp_path


def _write_rules(root, data):
    (root / 'evals/selection/affected-cases-v1.json').write_text(json.dumps(data))


def test_mirror_baseline_selects(mirror):
    assert selection.select({'prompt_fragments': ['goal_reached']}, root=mirror)['selected']


def test_rule_without_reason_raises(mirror):
    data = copy.deepcopy(RULES)
    del data['rules'][0]['reason']
    _write_rules(mirror, data)
    with pytest.raises(selection.SelectionError):
        selection.select({'prompt_fragments': ['goal_reached']}, root=mirror)


def test_rule_with_blank_reason_raises(mirror):
    data = copy.deepcopy(RULES)
    data['rules'][0]['reason'] = '  '
    _write_rules(mirror, data)
    with pytest.raises(selection.SelectionError):
        selection.select({'prompt_fragments': ['goal_reached']}, root=mirror)


def test_rule_referencing_unknown_case_id_raises(mirror):
    data = copy.deepcopy(RULES)
    rule = next(r for r in data['rules'] if r['id'] == 'role-parked_work_withdrawn')
    rule['select'][1]['ids'] = ['qualification-does-not-exist']
    _write_rules(mirror, data)
    with pytest.raises(selection.SelectionError):
        selection.select({'propositions': ['parked_work_withdrawn']}, root=mirror)


def test_union_deduplicates_rows_and_merges_reasons():
    both = selection.select({'prompt_fragments': ['goal_reached'],
                             'propositions': ['GOAL_REACHED_PROPOSITION']})
    keys = [(r['set'], r['id']) for r in both['selected']]
    assert len(keys) == len(set(keys))
    for ident in _role_ids('goal_reached'):
        rows = [r for r in both['selected'] if r['set'] == 'judgments' and r['id'] == ident]
        assert len(rows) == 1
        text = ' '.join(rows[0]['reasons'])
        assert 'prompt_fragments=goal_reached' in text
        assert 'propositions=GOAL_REACHED_PROPOSITION' in text
        assert len(rows[0]['reasons']) == len(set(rows[0]['reasons']))
