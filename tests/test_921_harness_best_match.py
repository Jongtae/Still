"""The #921 regression harness follows #965's best-match gate and can hold plans fixed, without model calls."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import cli, recall_regression_report  # noqa: E402
from agentos_eval.recall_record import PurposeRouter  # noqa: E402
from agentos_eval.recall_regression import Executor, digest, fixture_service, replay_case, seed_fixture  # noqa: E402
from agentos_eval.recall_regression_report import facet_report, gate_row  # noqa: E402
from personal_agent.orchestrator import worker_catalogue  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402
from test_921_recall_regression import plan, tiny  # noqa: E402


def two_matches():
    """Two old rows match the selector 'needle'; the needed one ranks first (higher term frequency)."""
    data = tiny()
    data['rows'][0]['content'] = 'needle needle needle owner preference'
    data['rows'].insert(1, dict(ref='second', key='profile.second', content='needle other note'))
    data['cases'] = [
        dict(id='unseen', split='dev', kind='paraphrase', query='request', needed=['target'], traps=[], note='fixture'),
        dict(id='seen', split='dev', kind='paraphrase', query='owner', needed=['target'], traps=[], note='fixture'),
    ]
    return data


def executor_for(tmp_path, data):
    store = QuickStore(tmp_path / 'state')
    service = fixture_service(store)
    refs = seed_fixture(store, data)
    return Executor(service, data, refs, worker_catalogue(service)), refs


def matched_ranks(observed):
    return {i['ref']: i['rank'] for i in observed['outcome']['checked_items'] if i['matched']}


def test_only_the_best_ranked_new_match_is_attributed(tmp_path):
    data = two_matches()
    executor, refs = executor_for(tmp_path, data)
    case = data['cases'][0]
    observed = executor.run(case, PurposeRouter(plan=plan(selector='needle')).engine)
    ranks = matched_ranks(observed)
    assert ranks == {'target': 1, 'second': 2}
    assert not {'target', 'second'} & {i['ref'] for i in observed['inventory']}
    # Both matches are new to the plan, but the gate counts only the best one.
    assert observed['outcome']['withheld'] and observed['outcome']['receipt_counts']['new'] == 1
    row = gate_row(case, dict(accepted=True), observed, data, refs)  # #959's count (2) raised here
    assert row['withheld'] and not row['noise_only_withheld']


def test_noise_only_when_the_best_new_match_is_noise(tmp_path):
    data = two_matches()
    data['rows'][0]['content'] = 'needle owner preference with many more words in it'
    data['rows'][1]['content'] = 'needle needle needle'
    executor, refs = executor_for(tmp_path, data)
    case = data['cases'][0]
    observed = executor.run(case, PurposeRouter(plan=plan(selector='needle')).engine)
    assert matched_ranks(observed) == {'second': 1, 'target': 2}
    row = gate_row(case, dict(accepted=True), observed, data, refs)
    assert row['withheld'] and row['noise_only_withheld']


def test_a_new_lower_match_behind_a_seen_best_match_does_not_withhold(tmp_path):
    data = two_matches()
    executor, refs = executor_for(tmp_path, data)
    case = data['cases'][1]  # the request 'owner' shows the target row to the plan
    observed = executor.run(case, PurposeRouter(plan=plan(selector='needle')).engine)
    shown = {i['ref'] for i in observed['inventory']}
    assert 'target' in shown and 'second' not in shown
    assert matched_ranks(observed) == {'target': 1, 'second': 2}
    assert not observed['outcome']['withheld']
    row = gate_row(case, dict(accepted=True), observed, data, refs)
    assert not row['withheld'] and not row['noise_only_withheld']


def test_strict_replay_raises_and_held_plan_reports_changes(tmp_path):
    data = two_matches()
    executor, refs = executor_for(tmp_path, data)
    case = data['cases'][1]
    recorded = executor.run(case, PurposeRouter(plan=plan(selector='needle')).engine)
    entry = dict(plan=plan(selector='needle'), inventory=recorded['inventory'],
                 accepted=recorded['accepted'], outcome=recorded['outcome'])
    assert replay_case(executor, case, entry)['inventory'] == recorded['inventory']
    # A recording made before #965 has no ranks on its checked items; strict replay still matches it.
    legacy = dict(entry, outcome=dict(entry['outcome'], checked_items=[
        {k: v for k, v in i.items() if k != 'rank'} for i in entry['outcome']['checked_items']]))
    replay_case(executor, case, legacy)
    changed = dict(entry, inventory=[i for i in entry['inventory'] if i['ref'] != 'target'])
    with pytest.raises(ValueError, match='Replay inventory A mismatch'):
        replay_case(executor, case, changed)
    assert replay_case(executor, case, changed, strict=False)['inventory_changed'] is True
    assert replay_case(executor, case, entry, strict=False)['inventory_changed'] is False
    drifted = dict(entry, outcome=dict(entry['outcome'], withheld=not entry['outcome']['withheld']))
    with pytest.raises(ValueError, match='Replay executor outcome mismatch'):
        replay_case(executor, case, drifted)
    held = replay_case(executor, case, drifted, strict=False)
    row = gate_row(case, drifted, held, data, refs)
    assert row['inventory_changed'] is False
    counted = facet_report(data, [[row, dict(row, id='unseen', inventory_changed=True)]])
    assert counted['facets']['dev']['inventory_changed'] == 1
    assert 'inventory_changed' not in facet_report(data, [[{k: v for k, v in row.items() if k != 'inventory_changed'}]])['facets']['dev']


def test_held_plan_flag_reaches_the_report(tmp_path, monkeypatch):
    base = tiny()
    additions = dict(version='recall-gate-dev/1', base_digest=digest(base), rows=[], cases=[])
    paths = {}
    for name, value in (('fixture', base), ('additions', additions), ('terms', dict(terms='t')), ('plans', dict(plans='p'))):
        paths[name] = tmp_path / (name + '.json')
        paths[name].write_text(json.dumps(value))
    seen = []
    result = dict(evidence={}, store_composition={}, terms_status_counts={}, arms={},
                  work_condition_checks={}, named_cases={})
    monkeypatch.setattr(recall_regression_report, 'report', lambda *a, **kw: seen.append(kw) or result)
    monkeypatch.setattr(cli, 'git_head', lambda: 'fixture-head')
    command = ['recall-regression', '--fixture', str(paths['fixture']), '--additions', str(paths['additions']),
               '--terms', str(paths['terms']), '--plans', str(paths['plans']), '--out', str(tmp_path / 'out')]
    assert cli.main(command) == 0 and cli.main(command + ['--held-plan']) == 0
    assert seen == [dict(code_head='fixture-head'), dict(code_head='fixture-head', held_plan=True)]
