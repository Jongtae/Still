"""#919: the recorder can record the dev split first and add held-out later, without model calls."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval.recall_regression import digest  # noqa: E402
from test_921_recall_regression_record import args_for, run_fake  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_eval_home(tmp_path, monkeypatch):
    monkeypatch.setattr('agentos_eval.paths.eval_home', lambda: tmp_path / 'eval-home')


def test_dev_split_records_dev_only_and_resumes_to_complete(tmp_path):
    args = args_for(tmp_path)
    args.splits = 'dev'
    seen = []
    dev = run_fake(args, seen)
    assert dev['failure'] is None and not dev['complete']
    assert dev['stop'] == dict(reason='dev split only; the other split not recorded')
    assert {run: sorted(entries) for run, entries in dev['runs'].items()} == {'1': ['addition', 'one']}
    assert seen.count('work-orchestration') == 2

    resumed_args = copy.copy(args)
    resumed_args.splits = 'all'
    resumed_args.out = tmp_path / 'resumed'
    resumed_args.resume = args.out / 'plans.json'
    resumed_args.requalify = False
    seen = []
    full = run_fake(resumed_args, seen)
    assert full['complete'], full['failure']
    assert seen == ['work-orchestration'] * 6  # three held-out runs of two cases; dev is not re-asked
    assert {run: sorted(entries) for run, entries in full['runs'].items()} == {
        '1': ['addition', 'held-claude', 'held-gpt', 'one'], '2': ['held-claude', 'held-gpt'], '3': ['held-claude', 'held-gpt']}
    assert full['runs']['1']['one'] == dev['runs']['1']['one']
    assert full['runs']['1']['addition'] == dev['runs']['1']['addition']
    assert len(full['call_budget']['sessions']) == 2


def fresh_layer(args, tmp_path):
    base = json.loads(args.fixture.read_text())
    additions = json.loads(args.additions.read_text())
    layer = dict(version='recall-heldout/1', base_digest=digest(base), additions_digest=digest(additions),
                 rows=[dict(ref='fresh-row', key='profile.fresh', content='fresh needle fact')],
                 cases=[dict(id='fresh-one', split='heldout', kind='paraphrase', query='request',
                             needed=['fresh-row'], traps=[], note='[author:claude] [fresh] fixture')])
    path = tmp_path / 'fresh.json'
    path.write_text(json.dumps(layer))
    return path


def test_heldout_layer_reuses_base_terms_and_keys_only_fresh_rows(tmp_path):
    args = args_for(tmp_path)
    first = run_fake(args, [])
    assert first['complete'], first['failure']
    base_terms = json.loads((args.out / 'terms.json').read_text())

    fresh_args = copy.copy(args)
    fresh_args.out = tmp_path / 'fresh-out'
    fresh_args.heldout_layer = fresh_layer(args, tmp_path)
    fresh_args.terms_base = args.out / 'terms.json'
    seen = []
    plans = run_fake(fresh_args, seen)
    assert plans['complete'], plans['failure']
    assert {run: sorted(entries) for run, entries in plans['runs'].items()} == {
        '1': ['addition', 'fresh-one', 'one'], '2': ['fresh-one'], '3': ['fresh-one']}
    terms = json.loads((fresh_args.out / 'terms.json').read_text())
    for ref, entry in base_terms['rows'].items():
        assert terms['rows'][ref] == entry, ref  # shared rows keep the earlier recorded terms
    assert terms['rows']['fresh-row']['meta']['status'] == 'keyed'
    assert terms['terms_base'] == dict(digest=digest(base_terms), fixture_digest=base_terms['fixture_digest'],
                                       reused_rows=len(base_terms['rows']))
    assert seen.count('memory-terms') == 1 and seen.count('work-orchestration') == 5
    assert plans['sandbox_cache_key'] == base_terms['sandbox_cache_key']  # the qualified slot is reused


def test_heldout_split_records_fresh_heldout_only(tmp_path):
    args = args_for(tmp_path)
    assert run_fake(args, [])['complete']
    fresh_args = copy.copy(args)
    fresh_args.out = tmp_path / 'fresh-out'
    fresh_args.heldout_layer = fresh_layer(args, tmp_path)
    fresh_args.terms_base = args.out / 'terms.json'
    assert run_fake(fresh_args, [])['complete']
    held_args = copy.copy(fresh_args)
    held_args.out = tmp_path / 'held-out'
    held_args.terms_base = None
    held_args.terms = fresh_args.out / 'terms.json'
    held_args.splits = 'heldout'
    seen = []
    held = run_fake(held_args, seen)
    assert held['failure'] is None and not held['complete']
    assert held['stop'] == dict(reason='heldout split only; the other split not recorded')
    assert {run: sorted(entries) for run, entries in held['runs'].items()} == {
        '1': ['fresh-one'], '2': ['fresh-one'], '3': ['fresh-one']}
    assert seen == ['work-orchestration'] * 3


def test_terms_base_requires_a_heldout_layer(tmp_path):
    args = args_for(tmp_path)
    assert run_fake(args, [])['complete']
    bad = copy.copy(args)
    bad.out = tmp_path / 'bad'
    bad.terms_base = args.out / 'terms.json'
    with pytest.raises(ValueError, match='--terms-base needs --heldout-layer'):
        run_fake(bad, [])


def test_heldout_layer_flag_reaches_the_report(tmp_path, monkeypatch):
    from agentos_eval import cli, recall_regression_report
    args = args_for(tmp_path)
    layer = fresh_layer(args, tmp_path)
    terms, plans = tmp_path / 'terms.json', tmp_path / 'plans.json'
    terms.write_text('{}')
    plans.write_text('{}')
    seen = []
    result = dict(evidence={}, store_composition={}, terms_status_counts={}, arms={},
                  work_condition_checks={}, named_cases={})
    monkeypatch.setattr(recall_regression_report, 'report', lambda *a, **kw: seen.append(kw) or result)
    monkeypatch.setattr(cli, 'git_head', lambda: 'fixture-head')
    assert cli.main(['recall-regression', '--fixture', str(args.fixture), '--additions', str(args.additions),
                     '--terms', str(terms), '--plans', str(plans), '--out', str(tmp_path / 'report'),
                     '--heldout-layer', str(layer), '--held-plan']) == 0
    assert seen == [dict(code_head='fixture-head', held_plan=True, heldout=json.loads(layer.read_text()))]
