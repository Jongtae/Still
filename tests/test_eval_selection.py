"""#855: synthetic selection metadata only; no model or local-owner fixtures."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import judgments, scenarios, selection


@pytest.fixture(scope='module')
def entries():
    return selection.inventory()


def test_prompt_fragment_selects_outcome_and_escalation_with_reasons(entries):
    result = selection.select({'prompt_fragments': ['goal_reached']})
    expected = {e['id'] for e in entries if e['role'] in ('goal_reached', 'orchestration_escalation')}
    assert {r['id'] for r in result['selected']} == expected
    assert all('goal_reached' in ' '.join(r['reasons']) for r in result['selected'])
    assert not result['unmatched']


def test_proposition_selects_only_its_role(entries):
    result = selection.select({'propositions': ['owner_act_answer']})
    assert {r['id'] for r in result['selected']} == {e['id'] for e in entries if e['role'] == 'owner_act_answer'}
    assert all(r['reasons'] for r in result['selected'])


@pytest.mark.parametrize('change', [{'routes': ['fastest_qualified']}, {'kinds': ['model']}, {'routes': ['effort=high']}])
def test_route_covers_every_judgment_and_qualification(change, entries):
    result = selection.select(change)
    assert {(e['set'], e['id']) for e in entries if e['set'] in ('judgments', 'qualification')} <= {
        (r['set'], r['id']) for r in result['selected']}
    assert all('route' in ' '.join(r['reasons']) for r in result['selected'])
    assert not result['unmatched']


def test_unknown_signals_are_not_hidden_by_a_matching_signal():
    result = selection.select({'paths': ['unknown/file.py'], 'propositions': ['owner_act_answer', 'new-role'],
                               'description': 'Unmapped free text'})
    assert result['selected']
    assert result['unmatched'] == [{'field': 'paths', 'value': 'unknown/file.py'},
                                   {'field': 'propositions', 'value': 'new-role'},
                                   {'field': 'description', 'value': 'Unmapped free text'}]
    assert selection.select({})['unmatched']


def test_scenario_tags_and_recall_keep_splits():
    result = selection.select({'tags': ['exp-recall']})
    for name in ('recall', 'scenarios'):
        assert {r['split'] for r in result['selected'] if r['set'] == name} == {'dev', 'heldout'}
    assert all(r['reasons'] for r in result['selected'])


def test_paths_and_reasons_are_deduplicated():
    result = selection.select({'paths': ['./src/personal_agent/prompts/goal_reached.md'] * 2,
                               'prompt_fragments': ['goal_reached']})
    assert result['change']['paths'] == ['src/personal_agent/prompts/goal_reached.md']
    assert len(result['selected']) == len({(r['set'], r['id']) for r in result['selected']})
    assert all(len(r['reasons']) == len(set(r['reasons'])) for r in result['selected'])


@pytest.mark.parametrize('mutation', ['unknown-key', 'missing-reason', 'unknown-id', 'unknown-role', 'unknown-tag', 'empty-target'])
def test_bad_rules_fail_even_when_change_would_not_match(mutation, tmp_path, entries, monkeypatch):
    data = {'version': selection.VERSION, 'rules': [dict(id='rule', match={'paths': ['a.py']},
            select=[{'set': 'judgments', 'ids': [entries[0]['id']]}], reason='Synthetic test reason')]}
    rule = data['rules'][0]
    if mutation == 'unknown-key':
        rule['typo'] = True
    elif mutation == 'missing-reason':
        del rule['reason']
    elif mutation == 'empty-target':
        rule['select'] = []
    else:
        key = {'unknown-id': 'ids', 'unknown-role': 'roles', 'unknown-tag': 'tags'}[mutation]
        rule['select'] = [{'set': 'judgments', key: ['does-not-exist']}]
    folder = tmp_path / 'evals/selection'
    folder.mkdir(parents=True)
    (folder / 'affected-cases-v1.json').write_text(json.dumps(data))
    monkeypatch.setattr(selection, 'inventory', lambda **kw: entries)
    with pytest.raises(selection.SelectionError):
        selection.select({'paths': ['not-a.py']}, root=tmp_path)


def test_diff_call_is_injectable_argv_only():
    calls = []
    def git_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout='a path.py\0new\nline.py\0')
    assert selection.diff_paths('base..head', git_run=git_run) == ['a path.py', 'new\nline.py']
    assert calls[0][0] == ['git', 'diff', '--name-only', '-z', 'base..head', '--']
    assert calls[0][1]['check'] is True
    with pytest.raises(selection.SelectionError):
        selection.diff_paths('--bad..head', git_run=git_run)


def test_cli_merges_description_and_diff(tmp_path, monkeypatch):
    source, output = tmp_path / 'change.json', tmp_path / 'selected.json'
    source.write_text(json.dumps({'propositions': ['owner_act_answer']}))
    monkeypatch.setattr(selection, 'diff_paths', lambda *a, **kw: ['unmapped.py'])
    assert selection.main(['--change', str(source), '--diff', 'base..head', '--output', str(output)]) == 0
    data = json.loads(output.read_text())
    assert data['selected'] and data['unmatched'] == [{'field': 'paths', 'value': 'unmapped.py'}]


def test_case_content_is_unchanged_by_selection():
    path = ROOT / 'evals/judgments/cases-v1.json'
    before = path.read_bytes()
    selection.select({'routes': ['judgment']})
    assert path.read_bytes() == before
    assert judgments.load_cases(path)['version'] == 'judgment-validation/3'


def test_proposition_covers_related_qualification():
    result = selection.select({'propositions': ['WITHDRAWAL_PROPOSITION']})
    assert {r['id'] for r in result['selected'] if r['set'] == 'qualification'} == {
        'qualification-parked-request-withdrawn'}


def test_shared_prompt_selects_every_consumer(entries):
    result = selection.select({'prompt_fragments': ['ask_or_assume']})
    selected = {(r['set'], r['id']) for r in result['selected']}
    assert {(e['set'], e['id']) for e in entries if e['set'] == 'scenarios' or
            e['role'] in ('goal_reached', 'orchestration_plan', 'orchestration_escalation')} == selected


@pytest.mark.parametrize('path', sorted((ROOT / 'src/personal_agent/prompts').glob('*.md')),
                         ids=lambda path: path.stem)
def test_every_prompt_file_selects_cases_by_path(path):
    result = selection.select({'paths': [path.relative_to(ROOT).as_posix()]})
    assert result['selected'], path.name
    assert not result['unmatched']


@pytest.mark.parametrize('role', ['answers_open_question', 'followup_relation', 'owner_act_answer',
                                 'parked_work_withdrawn', 'steers_running_work'])
def test_extracted_role_prompt_path_selects_its_judgments(role, entries):
    result = selection.select({'paths': [f'src/personal_agent/prompts/{role}.md']})
    assert {r['id'] for r in result['selected'] if r['set'] == 'judgments'} == {
        e['id'] for e in entries if e['role'] == role}


@pytest.mark.parametrize('path', ['skills/map-directions/SKILL.md',
                                 'skills/new-bundled-skill/SKILL.md',
                                 'src/personal_agent/bundled_skills/new-bundled-skill/SKILL.md'])
def test_bundled_skills_select_worker_scenarios_and_goal_judgments(path, entries):
    result = selection.select({'paths': [path]})
    selected = {(r['set'], r['id']) for r in result['selected']}
    travel = scenarios.load_file(ROOT / 'evals/scenarios/travel.json')
    assert travel
    assert {('scenarios', case['id']) for case in travel} <= selected
    # Match the worker-instructions rule's whole scenario set and outcome roles.
    assert {(e['set'], e['id']) for e in entries if e['set'] == 'scenarios' or
            e['role'] in ('goal_reached', 'orchestration_escalation')} == selected
    assert not result['unmatched']


@pytest.mark.parametrize('path', ['docs/current-context-contract.en.md',
                                 'docs/professional-roles-and-communication-contract.en.md'])
def test_restated_contracts_select_outcome_and_planner_judgments(path, entries):
    result = selection.select({'paths': [path]})
    assert {r['id'] for r in result['selected']} == {
        e['id'] for e in entries if e['role'] in ('goal_reached', 'orchestration_plan')}
    assert not result['unmatched']


def test_judgment_inventory_uses_declared_sets_and_splits(entries):
    from agentos_eval import paired
    partition = paired.load_partners()
    wanted = {ident: split for part in partition['roles'].values()
              for split in ('dev', 'heldout') for ident in part[split]}
    observed = {row['id']: row['split'] for row in entries if row['set'] == 'judgments'}
    assert observed == wanted
    assert len(observed) == 167
    selected = selection.select({'propositions': ['goal_reached']})
    assert {row['split'] for row in selected['selected']} == {'dev', 'heldout'}


@pytest.mark.parametrize('role', ['goal_reached', 'orchestration_plan'])
def test_202_case_file_is_reachable_by_role_rules(role):
    cases = judgments.load_cases(ROOT / 'evals/judgments/cases-v2-202.json')
    wanted = {c['id'] for c in cases['cases'] if c['role'] == role}
    selected = selection.select({'propositions': [role]})
    assert wanted <= {row['id'] for row in selected['selected']}


def test_plan_reading_cases_are_selected_from_the_v3_partition():
    cases = judgments.load_cases(ROOT / 'evals/judgments/cases-v3-202-plan.json')
    wanted = {case['id'] for case in cases['cases']}
    selected = selection.select({'propositions': ['goal_reached']})
    observed = {row['id']: row['split'] for row in selected['selected'] if row['id'] in wanted}
    assert set(observed) == wanted
    assert all(split == ('heldout' if ident.startswith('heldout-') else 'dev')
               for ident, split in observed.items())
