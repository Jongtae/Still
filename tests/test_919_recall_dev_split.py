"""#919: the recorder can record the dev split first and add held-out later, without model calls."""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

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
    assert dev['stop'] == dict(reason='dev split only; held-out not recorded')
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
