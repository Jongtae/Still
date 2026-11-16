"""#902 fixture evidence: typed SQLite records, no model, network or socket."""
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
from agentos_eval import report, runner, scoring
from agentos_eval.client import AgentOSClient
from test_eval_loop import FakePool, FakeServer, scenario, turn


def fallback_db(data):
    path = data / 'private' / 'quickstart.db'
    path.parent.mkdir(exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE config(key TEXT,value TEXT); '
                        'CREATE TABLE jobs(id TEXT,relation_kind TEXT,related_job_id TEXT,request_key TEXT,channel TEXT,chat_id TEXT); '
                        'CREATE TABLE tool_events(id INTEGER PRIMARY KEY,job_id TEXT,tool TEXT,status TEXT,detail TEXT,created REAL);')
        db.executemany('INSERT INTO jobs VALUES(?,?,?,?,?,?)', [
            ('owner', '', None, 'owner', 'web', '1'),
            ('retry', 'retry', 'owner', 'retry-key', 'web', '1'),
            ('due', '', None, 'preparation:p:1', 'telegram', '1'),
            ('outside', '', None, 'outside', 'web', '1')])
        rows = [('owner', 'search', 'succeeded', '{}', i) for i in range(45)]
        rows += [(job, tool, status, json.dumps({'code': code, 'text': 'private payload'}), 50 + i)
                 for i, (job, tool, status, code) in enumerate([
                     ('owner', 'orchestrator', 'fallback', 'judgment_unavailable'),
                     ('owner', 'orchestrator', 'fallback', 'judgment_unavailable'),
                     ('retry', 'orchestrator', 'fallback', 'judgment_unavailable'),
                     ('due', 'orchestrator', 'fallback', 'judgment_qualifying'),
                     ('due', 'orchestrator', 'fallback', 'plan_invalid'),
                     ('due', 'orchestrator', 'fallback', 'plan_malformed'),
                     ('due', 'orchestrator', 'fallback', 'plan_unconfident'),
                     ('due', 'orchestrator', 'fallback', 'budget_short'),
                     ('outside', 'orchestrator', 'fallback', 'judgment_unavailable'),
                     ('owner', 'other', 'fallback', 'judgment_unavailable'),
                     ('owner', 'orchestrator', 'planned', 'judgment_unavailable'),
                 ])]
        rows += [('owner', 'orchestrator', 'fallback', bad, 99) for bad in ('{', '[]', '{"code": false}')]
        db.executemany('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES(?,?,?,?,?)', rows)
    return path


@pytest.fixture
def fallback_run(monkeypatch):
    class Pool(FakePool):
        @contextmanager
        def sandbox(self, worker=None):
            with super().sandbox(worker) as box:
                fallback_db(box.data)
                yield box

    turns = [turn(task_id='owner', answer='judgment_unavailable is only quoted text'),
             turn(task_id='retry'),
             {'kind': 'wait_due', 'due_observation': {
                 'status': 'completed', 'completed': True, 'work_id': 'due', 'admission': {'observed': True}}}]
    monkeypatch.setattr(runner, 'run_turns', lambda *args, **kwargs: turns)
    server = FakeServer(statuses=['succeeded'])
    result = runner.run_scenario(scenario(), 'codex', Pool(),
                                client_factory=lambda url: AgentOSClient(url, transport=server, sleep=lambda _: None))
    assert result['error'] is None
    return result


EXPECTED = {'judgment_unavailable': 2, 'judgment_qualifying': 1, 'plan_invalid': 1,
            'plan_malformed': 1, 'plan_unconfident': 1, 'budget_short': 1}


def test_judgment_infra_fallback_codes_mirror_matches_runtime():
    from personal_agent.orchestrator import FALLBACK_UNAVAILABLE, FALLBACK_QUALIFYING
    assert runner.JUDGMENT_INFRA_FALLBACK_CODES == frozenset((FALLBACK_UNAVAILABLE, FALLBACK_QUALIFYING))


def test_runner_counts_fallback_works_across_owner_families_and_wait_due(fallback_run):
    assert fallback_run['judgment_fallbacks'] == EXPECTED
    assert fallback_run['judgment_fallback_work_count'] == 3
    tools = fallback_run['work']['retry']['tools']
    fallback = next(t for t in tools if t.get('code') == 'judgment_unavailable')
    assert fallback['status'] == 'fallback' and fallback['job_id'] == 'owner'
    assert 'private payload' not in json.dumps(fallback_run['work'])


def test_score_and_sweep_count_fallback_samples_and_works(fallback_run):
    checks, failures = scoring.deterministic_checks(scenario(), fallback_run)
    value, meta = scoring.combine(scenario(), fallback_run, checks, failures)
    assert meta['judgment_fallbacks'] == EXPECTED
    assert meta['judgment_fallback_work_count'] == 3
    sample = SimpleNamespace(id='s', epoch=1, metadata={'run': fallback_run}, scores={
        'secretary': SimpleNamespace(metadata=meta, value=value)})
    records = report.records_from_samples([sample])
    summary = report.aggregate(records)['totals']['judgment_fallbacks']
    assert summary == {'samples': 1, 'works': 3, 'by_code': {
        code: {'samples': 1, 'works': n} for code, n in EXPECTED.items()}}
    markdown = report.render_markdown(report.aggregate(records))
    assert 'Judgment AI fallbacks: 1 samples, 3 Works' in markdown
    for code, n in EXPECTED.items():
        assert f'{code}: 1 samples, {n} Works' in markdown


@pytest.mark.parametrize('scored', [False, True])
def test_report_adapter_keeps_run_fallbacks_even_without_score_metadata(fallback_run, scored):
    sample = SimpleNamespace(id='s', epoch=1, metadata={'run': fallback_run}, scores={
        'secretary': SimpleNamespace(metadata={}, value={'passed': 0})} if scored else {})
    record = report.records_from_samples([sample])[0]
    assert record['judgment_fallbacks'] == EXPECTED
    assert record['judgment_fallback_work_count'] == 3


def test_reply_text_and_old_compact_events_do_not_create_fallbacks():
    run = {'turns': [turn(answer='judgment_unavailable', events=[{'tool': 'orchestrator', 'status': 'fallback'}])]}
    _, meta = scoring.combine(scenario(), run, {'delivered': True}, [])
    assert not meta.get('judgment_fallbacks')
    assert not meta.get('judgment_fallback_work_count')
