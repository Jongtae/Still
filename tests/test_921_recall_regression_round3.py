"""Gzip input boundaries, using only temporary fixtures and file-only validation."""
import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import cli, recall_record, recall_regression_report  # noqa: E402
from agentos_eval.recall_arms import _runtime_catalogue  # noqa: E402
from agentos_eval.recall_regression import (  # noqa: E402
    PLAN_VERSION, digest, prompt_digest, safe_catalogue,
)
from personal_agent.quickstart_store import QuickStore  # noqa: E402
from test_921_recall_regression import tiny, terms_recording  # noqa: E402


def write_input(path, value):
    raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
    path.write_bytes(gzip.compress(raw, mtime=0) if path.suffix == '.gz' else raw)
    return path


@pytest.mark.parametrize('terms_suffix,plans_suffix,explicit_terms',[
    ('.json','.json',False), ('.json.gz','.json.gz',False),
    ('.json','.json.gz',True), ('.json.gz','.json',True),
])
def test_resume_and_terms_load_before_any_route_call(tmp_path,terms_suffix,plans_suffix,explicit_terms):
    data = tiny()
    terms = dict(terms_recording(data),complete=True,tokens='not observable')
    terms['rows']['target']['terms'] = '선택어'
    catalogue = _runtime_catalogue()
    plans = dict(version=PLAN_VERSION,fixture_digest=digest(data),terms_digest=digest(terms),
        catalogue=safe_catalogue(catalogue,QuickStore(tmp_path/'state')),
        plan_prompt_schema_digest=prompt_digest(catalogue),complete=False,heldout_runs=3,
        runs={},tokens='not observable',call_budget=dict(sessions=[dict(admitted=0)]))
    terms_path = write_input(tmp_path/('terms'+terms_suffix),terms)
    plans_path = write_input(tmp_path/('plans'+plans_suffix),plans)
    args = SimpleNamespace(resume=plans_path,heldout_runs=3)
    if explicit_terms:
        args.terms = terms_path
    assert recall_record.load_inputs(args,data,[(1,data['cases'][0])]) == (terms,plans)
    # Terms reuse also works independently of --resume.
    assert recall_record.load_inputs(SimpleNamespace(terms=terms_path),data,[]) == (terms,None)


@pytest.mark.parametrize('terms_suffix,plans_suffix',[
    ('.json','.json'), ('.json.gz','.json.gz'), ('.json','.json.gz'), ('.json.gz','.json'),
])
def test_regression_command_reads_both_input_formats(tmp_path,monkeypatch,terms_suffix,plans_suffix):
    base = tiny()
    additions = dict(version='recall-gate-dev/1',base_digest=digest(base),rows=[],cases=[])
    terms,plans = dict(terms='선택어'),dict(plan='기록')
    fixture = write_input(tmp_path/'fixture.json',base)
    extra = write_input(tmp_path/'additions.json',additions)
    terms_path = write_input(tmp_path/('terms'+terms_suffix),terms)
    plans_path = write_input(tmp_path/('plans'+plans_suffix),plans)
    observed = []
    result = dict(evidence={},store_composition={},terms_status_counts={},arms={},
                  work_condition_checks={},named_cases={})
    def report(*args,**kwargs):
        observed.append((args,kwargs))
        return result
    monkeypatch.setattr(recall_regression_report,'report',report)
    monkeypatch.setattr(cli,'git_head',lambda:'fixture-head')
    out = tmp_path/'report'
    assert cli.main(['recall-regression','--fixture',str(fixture),'--additions',str(extra),
        '--terms',str(terms_path),'--plans',str(plans_path),'--out',str(out)]) == 0
    assert observed == [((base,additions,terms,plans),dict(code_head='fixture-head'))]
    assert json.loads((out/'recall-regression.json').read_text()) == result
    assert (out/'recall-regression.md').is_file()
