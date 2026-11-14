"""P1 fixture evidence only: SQLite and injected records, no Inspect or network."""
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
from agentos_eval import p1_report as p1, report, runner


def event(tool, status, at):
    return {'tool': tool, 'status': status, 'created': at}


def record(status='succeeded', rows=None, epoch=1):
    return {'scenario': 's', 'worker': 'codex', 'epoch': epoch, 'checks': {'fixture': True},
            'turns': [{'status': status, 'sent_epoch': 100, 'delivered_epoch': 106, 'elapsed': 99},
                      {'status': 'succeeded', 'elapsed': 2}],
            'work': {'t': {'ledger': {'turns': 3, 'attempts': 4, 'nudges': 1}, 'tools': rows, 'engine': None}}}


def arms(a, b):
    return {name: {'records': records, 'run': {'git_head': name, 'worker': 'codex', 'epochs': 1, 'seed': 'fresh'}}
            for name, records in [('limit1', a), ('limit4', b)]}


def test_work_measurements_readonly_chain_and_allowlist(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    path = private / 'quickstart.db'
    secret = 'https://private.example/sk-secret-value'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE config(key TEXT, value TEXT); '
                         'CREATE TABLE jobs(id TEXT, relation_kind TEXT, related_job_id TEXT, request_key TEXT, channel TEXT, chat_id TEXT); '
                         'CREATE TABLE context_location_requests(id TEXT,job_id TEXT,state TEXT); '
                         'CREATE TABLE tool_events(id INTEGER PRIMARY KEY,job_id TEXT,tool TEXT,status TEXT,detail TEXT,created REAL);')
        db.executemany('INSERT INTO jobs VALUES(?,?,?,?,?,?)',
                       [('root', '', None, 'owner', 'web', '1'), ('retry', 'retry', 'root', 'retry-key', 'web', '1'),
                        ('continue', 'reference', 'retry', 'loc:location', 'web', '1'),
                        ('unrelated', '', None, 'other', 'web', '1')])
        db.execute("INSERT INTO context_location_requests VALUES('location','retry','consumed')")
        db.execute('INSERT INTO config VALUES(?,?)', ('work_budget:root', json.dumps(
            {'turns': [{'task_id': 'root'}, {'task_id': 'retry'}], 'attempts': 5, 'nudges': 1})))
        db.executemany('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES(?,?,?,?,?)',
                       [('retry', 'web_search', 'running', secret, 1),
                        ('continue', 'web_search', 'succeeded', secret, 3),
                        ('continue', 'subscription_engine', 'succeeded', json.dumps(
                            {'usage': {'input_tokens': 17, 'output_tokens': 5, 'text': secret, 'bool': True},
                             'num_turns': 2, 'cost_usd': .03, 'model': 'reported-model', 'effort': 'high',
                             'cli_tool_calls': [{'url': secret}], 'error': secret}), 4),
                        ('unrelated', 'read_file', 'running', secret, 2)])
    before = path.read_bytes()
    captured = runner.work_measurements(tmp_path, ['root', 'retry', 'continue'])
    assert path.read_bytes() == before
    assert captured['continue'] == captured['root'] == captured['retry']
    work = captured['root']
    assert work['ledger'] == {'turns': 2, 'attempts': 5, 'nudges': 1}
    assert work['tools'][:2] == [event('web_search', 'running', 1), event('web_search', 'succeeded', 3)]
    assert len(work['tools']) == 3
    assert work['engine'] == [{'usage': {'input_tokens': 17, 'output_tokens': 5}, 'num_turns': 2,
                              'cost_usd': .03, 'model': 'reported-model', 'effort': 'high', 'cli_tool_calls': 1}]
    assert secret not in json.dumps(captured)
    assert 'detail' not in json.dumps(captured)


def test_missing_database_and_tables(tmp_path):
    empty = {'t': {'ledger': None, 'tools': None, 'engine': None}}
    assert runner.work_measurements(tmp_path, ['t']) == empty
    (tmp_path / 'private').mkdir()
    sqlite3.connect(tmp_path / 'private' / 'quickstart.db').close()
    assert runner.work_measurements(tmp_path, ['t']) == empty


def test_verified_latency_counts_and_unavailable():
    records = [record(), record('partial')]
    records[1]['epoch'] = 2
    result = p1.compare_arms(arms(records, records), names={'web_search'})
    arm = result['arms']['limit1']
    assert arm['summary']['verified'] == 1
    assert arm['summary']['verified_rate'] == .5
    assert arm['not_verified'][0]['statuses'] == ['partial', 'succeeded']
    assert arm['summary']['latency'] == {'n': 4, 'median': 4, 'mean': 4, 'min': 2, 'max': 6}
    assert arm['summary']['epoch_latency_medians'] == {'1': 4, '2': 4}
    assert arm['summary']['ledger']['attempts']['median'] == 4
    assert arm['summary']['ledger']['turns']['mean'] == 3
    assert arm['summary']['cost_usd'] == 'unavailable'
    assert arm['summary']['usage'] == 'unavailable'
    assert result['paired_deltas']['s']['latency_median'] == 0
    assert result['paired_deltas']['s']['cost_usd'] == 'unavailable'
    assert all(section in p1.render_markdown(result) for section in
               ('Fixture support', 'Live evidence', 'Uncertainty', 'Unsupported paths'))


def test_overlap_and_reported_usage():
    sequential = [event('web_search', 'running', 1), event('web_search', 'succeeded', 2),
                  event('read_file', 'running', 2), event('read_file', 'failed', 4)]
    concurrent = [event('web_search', 'running', 1), event('read_file', 'running', 2),
                  event('web_search', 'succeeded', 3), event('read_file', 'failed', 4)]
    a, b = record(rows=sequential), record(rows=concurrent)
    b['work']['t']['engine'] = [{'usage': {'input_tokens': 20}, 'cost_usd': .25},
                               {'usage': {'input_tokens': 5}, 'cost_usd': .1}]
    result = p1.compare_arms(arms([a], [b]), names={'web_search', 'read_file'})
    assert result['arms']['limit1']['runs'][0]['reads']['peak'] == 1
    summary = result['arms']['limit4']['summary']
    assert summary['runs_peak_ge_2'] == 1
    assert summary['read_peak']['max'] == 2
    assert summary['read_calls']['mean'] == 2
    assert result['paired_deltas']['s']['read_peak_mean'] == 1
    assert summary['usage'] == {'input_tokens': 25}
    assert summary['cost_usd'] == pytest.approx(.35)
    ambiguous = [event('web_search', 'running', 1), event('web_search', 'running', 2),
                 event('web_search', 'succeeded', 3), event('web_search', 'succeeded', 4)]
    assert p1.overlap(ambiguous, {'web_search'})['peak'] is None
    assert 'ambiguous' in p1.overlap(ambiguous, {'web_search'})['reason']
    assert p1.overlap(sequential[:-1], {'web_search', 'read_file'})['peak'] is None


@pytest.mark.parametrize('field,value', [('scenario', 'other'), ('worker', 'claude-code')])
def test_mismatched_cohort_refused(field, value):
    a, b = record(), record()
    b[field] = value
    with pytest.raises(ValueError, match='differ'):
        p1.compare_arms(arms([a], [b]), names=set())


def test_mismatched_epochs_refused():
    data = arms([record()], [record()])
    data['limit4']['run']['epochs'] = 3
    with pytest.raises(ValueError, match='epochs differ'):
        p1.compare_arms(data, names=set())


def test_existing_inspect_record_adapter_preserves_measurements():
    run = record()
    sample = SimpleNamespace(id='s', epoch=1, metadata={'run': run}, scores={
        'secretary': SimpleNamespace(metadata={'scenario': 's', 'checks': {'ok': True}}, value={'passed': 0})})
    converted = report.records_from_samples([sample])[0]
    assert converted['work'] == run['work']
    assert converted['turns'][0]['delivered_epoch'] == 106


def test_turn_wall_clock_injected():
    class Client:
        def send(self, *args): return 'task'
        def wait(self, *args, **kwargs): return {'status': 'succeeded'}
        def answer(self, *args): return {'response': 'done'}
    ticks = iter([100, 107])
    turns = runner.run_turns(Client(), {'id': 's', 'turns': [{'say': 'hi'}]}, wall=lambda: next(ticks), clock=lambda: 0)
    assert turns[0]['sent_epoch'] == 100
    assert turns[0]['delivered_epoch'] == 107
    assert turns[0]['elapsed'] == 0


def test_checks_are_required_and_judge_does_not_verify():
    good = record()
    good['value'] = {'passed': 0, 'rubric_mean': 0}
    assert p1.measure(good, set())['verified'] is True
    bad = record()
    bad['checks']['fixture'] = False
    bad['value'] = {'passed': 1, 'rubric_mean': 1}
    assert p1.measure(bad, set())['verified'] is False
    bad['checks'] = {}
    assert p1.measure(bad, set())['verified'] is False
    del bad['checks']
    bad['value']['checks_pass'] = 1.0
    assert p1.measure(bad, set())['verified'] is True


def test_missing_ledger_row_and_corrupt_json_do_not_fail_capture(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    with sqlite3.connect(private / 'quickstart.db') as db:
        db.executescript('CREATE TABLE config(key TEXT,value TEXT); '
                         'CREATE TABLE tool_events(id INTEGER PRIMARY KEY,job_id TEXT,tool TEXT,status TEXT,detail TEXT,created REAL);')
        db.execute("INSERT INTO config VALUES('work_budget:bad', '[]')")
        db.execute("INSERT INTO tool_events VALUES(1,'bad','read_file','running','private text',1)")
    result = runner.work_measurements(tmp_path, ['bad', 'missing'])
    assert result['bad']['ledger'] is None
    assert result['bad']['tools'] == [event('read_file', 'running', 1)]
    assert result['missing'] == {'ledger': None, 'tools': None, 'engine': None}


def test_cli_reads_existing_logs_and_writes_outputs(tmp_path, monkeypatch):
    import types
    from agentos_eval import cli
    logs = {}
    for path in ('one.eval', 'four.eval'):
        run = record()
        sample = SimpleNamespace(id='s', epoch=1, metadata={'run': run}, scores={
            'secretary': SimpleNamespace(metadata={'scenario': 's', 'worker': 'codex', 'checks': run['checks']}, value={})})
        logs[path] = SimpleNamespace(samples=[sample], eval=SimpleNamespace(metadata={'git_head': path, 'epochs': 1}))
    module = types.ModuleType('inspect_ai.log')
    module.read_eval_log = logs.__getitem__
    monkeypatch.setitem(sys.modules, 'inspect_ai.log', module)
    out, md = tmp_path / 'out.json', tmp_path / 'out.md'
    assert cli.main(['p1-report', '--arm', 'limit1=one.eval', '--arm', 'limit4=four.eval',
                     '--json', str(out), '--md', str(md)]) == 0
    data = json.loads(out.read_text())
    assert data['arms']['limit4']['identity']['git_head'] == 'four.eval'
    assert data['arms']['limit1']['summary']['latency']['median'] == 4
    assert 'limit4 − limit1' in md.read_text()
    logs['four.eval'].samples[0].scores['secretary'].metadata['scenario'] = 'other'
    with pytest.raises(SystemExit, match='scenario_ids differ'):
        cli.main(['p1-report', '--arm', 'limit1=one.eval', '--arm', 'limit4=four.eval'])
