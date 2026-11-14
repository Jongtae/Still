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
    return {name: {'records': records, 'run': {'git_head': name, 'worker': 'codex', 'epochs': 1, 'seed': 'fresh',
                                                            'scenario_ids': sorted({r['scenario'] for r in records})}}
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
    assert work['root'] == 'root'
    assert work['ledger'] == {'turns': 2, 'attempts': 5, 'nudges': 1}
    assert work['tools'][:2] == [event('web_search', 'running', 1), event('web_search', 'succeeded', 3)]
    assert len(work['tools']) == 3
    assert work['engine'] == [{'usage': {'input_tokens': 17, 'output_tokens': 5}, 'num_turns': 2,
                              'cost_usd': .03, 'model': 'reported-model', 'effort': 'high', 'cli_tool_calls': 1}]
    assert secret not in json.dumps(captured)
    assert 'detail' not in json.dumps(captured)


def test_missing_database_and_tables(tmp_path):
    empty = {'t': {'root': None, 'ledger': None, 'tools': None, 'engine': None}}
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
    sequential = [{**r, 'scope': 'subscription-mcp-bridge'} for r in sequential]
    concurrent = [{**r, 'scope': 'subscription-mcp-bridge'} for r in concurrent]
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
    interleaved = [event('web_search', 'running', 1), event('web_search', 'running', 2),
                 event('web_search', 'succeeded', 3), event('web_search', 'succeeded', 4)]
    interleaved = [{**r, 'scope': 'subscription-mcp-bridge'} for r in interleaved]
    assert p1.overlap(interleaved, {'web_search'})['peak'] == 2
    assert p1.overlap(interleaved, {'web_search'})['reason'] is None
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
    assert result['missing'] == {'root': None, 'ledger': None, 'tools': None, 'engine': None}


def test_cli_reads_existing_logs_and_writes_outputs(tmp_path, monkeypatch):
    import types
    from agentos_eval import cli
    logs = {}
    for path in ('one.eval', 'four.eval'):
        run = record()
        sample = SimpleNamespace(id='s', epoch=1, metadata={'run': run}, scores={
            'secretary': SimpleNamespace(metadata={'scenario': 's', 'worker': 'codex', 'checks': run['checks']}, value={})})
        logs[path] = SimpleNamespace(samples=[sample], eval=SimpleNamespace(metadata={'id': path, 'git_head': path, 'epochs': 1, 'scenario_ids': ['s']}))
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
    logs['four.eval'].eval.metadata['scenario_ids'] = ['other']
    with pytest.raises(SystemExit, match='scenario_ids differ'):
        cli.main(['p1-report', '--arm', 'limit1=one.eval', '--arm', 'limit4=four.eval'])


def test_native_replay_does_not_contribute_to_governed_peak():
    native = [{**event('web_search', status, at), 'scope': 'cli-native', 'call_id': call}
              for call, status, at in [('n1', 'running', 100), ('n2', 'running', 101),
                                       ('n1', 'succeeded', 102), ('n2', 'succeeded', 103)]]
    governed = [{**event('listed_mcp_search', status, at), 'host_action': 'web_search',
                 'scope': 'subscription-mcp-bridge', 'call_id': 'bridge'}
                for status, at in [('running', 1), ('succeeded', 2)]]
    measured = p1.overlap(governed + native, {'web_search'})
    assert measured['governed_calls'] == 1
    assert measured['native_calls'] == 2
    assert measured['peak'] == 1
    assert p1.overlap(native, {'web_search'})['peak'] == 0
    summary = p1.compare_arms(arms([record(rows=native)], [record(rows=governed + native)]),
                              names={'web_search'})['arms']['limit4']['summary']
    assert summary['native_read_calls']['mean'] == 2
    assert summary['governed_read_calls']['mean'] == 1


def test_same_tool_bridged_calls_pair_by_id():
    rows = [{**event('mcp_alias', status, at), 'host_action': 'web_search',
             'scope': 'subscription-mcp-bridge', 'call_id': call}
            for call, status, at in [('a', 'running', 1), ('b', 'running', 2),
                                     ('b', 'succeeded', 3), ('a', 'failed', 4)]]
    assert p1.overlap(rows, {'web_search'})['peak'] == 2
    assert p1.overlap(rows, {'web_search'})['governed_calls'] == 2
    missing_terminal = rows[:-1]
    assert p1.overlap(missing_terminal, {'web_search'})['peak'] is None
    assert 'missing terminal' in p1.overlap(missing_terminal, {'web_search'})['reason']
    wrong_id = [*rows[:-1], {**rows[-1], 'call_id': 'not-started'}]
    assert p1.overlap(wrong_id, {'web_search'})['peak'] is None


def test_capture_safe_detail_fields_and_response_stages(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    secret = 'https://private.example/sk-SECRET-arguments'
    with sqlite3.connect(private / 'quickstart.db') as db:
        db.executescript('CREATE TABLE config(key TEXT,value TEXT); '
                         'CREATE TABLE tool_events(id INTEGER PRIMARY KEY,job_id TEXT,tool TEXT,status TEXT,detail TEXT,created REAL);')
        detail_rows = [
            ('mcp_alias', 'running', {'call_id': 'a', 'scope': 'subscription-mcp-bridge',
                                    'host_action': 'web_search', 'arguments': {'url': secret}, 'text': secret}),
            ('mcp_alias', 'succeeded', {'call_id': 'a', 'scope': 'subscription-mcp-bridge',
                                      'host_action': 'web_search', 'result': secret}),
            ('response_timing', 'recorded', {'stage': 'queue_wait', 'ms': 954.4, 'url': secret}),
            ('response_timing', 'recorded', {'stage': 'routed', 'ms': 766.2, 'text': secret}),
            ('response_timing', 'recorded', {'stage': 'finished', 'ms': 123235.4, 'total_ms': 124189.8}),
            ('subscription_engine', 'running', {'model': None, 'effort': '', 'prompt': secret}),
            ('ignored_types', 'recorded', {'scope': [], 'call_id': 7, 'host_action': {}, 'stage': 9,
                                         'ms': True, 'total_ms': secret}),
            ('response_timing', 'recorded', {'stage': True, 'ms': True, 'total_ms': secret}),
        ]
        db.executemany('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES(?,?,?,?,?)',
                       [('t', tool, status, json.dumps(detail), at) for at, (tool, status, detail) in enumerate(detail_rows)])
    work = runner.work_measurements(tmp_path, ['t'])
    assert work['t']['tools'][0] == {**event('mcp_alias', 'running', 0), 'call_id': 'a',
                                     'scope': 'subscription-mcp-bridge', 'host_action': 'web_search'}
    assert work['t']['tools'][2] == {**event('response_timing', 'recorded', 2), 'stage': 'queue_wait', 'ms': 954.4}
    assert work['t']['tools'][4]['total_ms'] == 124189.8
    assert work['t']['tools'][6] == event('ignored_types', 'recorded', 6)
    assert work['t']['tools'][7] == event('response_timing', 'recorded', 7)
    assert work['t']['engine'] == [{'model': None, 'effort': None}]
    assert secret not in json.dumps(work)
    assert 'arguments' not in json.dumps(work)
    run = record()
    run['work'] = work
    run['turns'][0]['delivered_epoch'] = 106.123456
    result = p1.compare_arms(arms([run], [run]), names={'web_search'})
    timings = result['arms']['limit1']['summary']['stage_timings']
    assert timings['queue_wait'] == {'n': 1, 'median': 954.4, 'mean': 954.4, 'min': 954.4, 'max': 954.4}
    assert timings['routed']['median'] == 766.2
    assert timings['finished']['max'] == 123235.4
    md = p1.render_markdown(result)
    assert '| queue_wait | 1 | 954 | 954 | 954 |' in md
    assert '| finished | 1 | 123235 | 123235 | 123235 |' in md
    assert '6.1' in md
    assert '6.123456' not in md
    assert '954.4' not in md
    assert 'CLI default (not reported)' in md
    assert 'replayed after the run' in md
    assert result['arms']['limit1']['runs'][0]['latencies'][0] == pytest.approx(6.123456)


@pytest.mark.parametrize('scope', ['main', 'agent:x', None, 'other-scope'])
def test_direct_scopes_count_without_an_observed_peak(scope):
    rows = [{**event('web_search', status, at), **({'scope': scope} if scope is not None else {})}
            for status, at in [('running', 1), ('succeeded', 2)]]
    reads = p1.overlap(rows, {'web_search'})
    assert reads['direct_calls'] == 1
    assert reads['governed_calls'] == 1
    assert reads['bridge_calls'] == 0
    assert reads['direct_peak'] is None
    assert reads['direct_peak_reason'] == 'direct-route records are replayed in call order; overlap not observable from records'
    result = p1.compare_arms(arms([record(rows=rows)], [record(rows=rows)]), names={'web_search'})
    summary = result['arms']['limit1']['summary']
    assert summary['read_counts']['direct'] == 1
    assert summary['read_counts']['governed'] == 1
    assert summary['direct_peak'] is None
    assert summary['direct_peak_reason'] == reads['direct_peak_reason']
    assert summary['runs_peak_ge_2'] == 0
    md = p1.render_markdown(result)
    assert '| direct | 1 |' in md
    assert '| s | 1 | 0 | 1 | 0 | 1 | 0 | unavailable |' in md
    assert reads['direct_peak_reason'] in md


@pytest.mark.parametrize('bridge_peak', [1, 2])
def test_mixed_run_only_bridge_rows_determine_peak(bridge_peak):
    bridge_order = [('a', 'running'), ('a', 'succeeded'), ('b', 'running'), ('b', 'succeeded')]
    if bridge_peak == 2:
        bridge_order = [('a', 'running'), ('b', 'running'), ('a', 'succeeded'), ('b', 'succeeded')]
    bridge = [{**event('web_search', status, at), 'scope': 'subscription-mcp-bridge', 'call_id': call}
              for at, (call, status) in enumerate(bridge_order)]
    # Invalid/incomplete direct replay must not affect bridge pairing or peak.
    direct = [{**event('web_search', 'running', 999), 'call_id': 'a',
               **({'scope': scope} if scope is not None else {})}
              for scope in ('main', 'agent:x', None)]
    native = [{**event('web_search', status, 500), 'scope': 'cli-native', 'call_id': 'a'}
              for status in ('running', 'succeeded')]
    reads = p1.overlap(direct + native + bridge, {'web_search'})
    assert reads['peak'] == reads['bridge_peak'] == bridge_peak
    assert reads['bridge_calls'] == 2
    assert reads['direct_calls'] == 3
    assert reads['native_calls'] == 1
    assert reads['governed_calls'] == 5
    assert reads['direct_peak'] is None
    assert reads['reason'] is None
    run = record(rows=direct + native + bridge)
    result = p1.compare_arms(arms([run, {**run, 'epoch': 2}], [run, {**run, 'epoch': 2}]), names={'web_search'})
    summary = result['arms']['limit1']['summary']
    assert summary['read_counts'] == {'bridge': 4, 'direct': 6, 'native': 2, 'governed': 10}
    assert summary['bridge_peak']['max'] == bridge_peak
    assert summary['runs_peak_ge_2'] == (2 if bridge_peak == 2 else 0)
    assert summary['overlap_unknown'] == 0
    md = p1.render_markdown(result)
    assert '| bridge | 4 |' in md
    assert '| direct | 6 |' in md
    assert '| native | 2 |' in md
    assert 'peak ≥ 2 run counts cover bridge rows only' in md



def test_declared_epochs_mark_identically_interrupted_arms_incomplete():
    data = arms([record()], [record()])
    for arm in data.values():
        arm['run']['epochs'] = 3
    result = p1.compare_arms(data, names=set())
    assert result['comparison_complete'] is False
    for arm in result['arms'].values():
        assert arm['complete'] is False
        assert arm['missing'] == [['s', 2], ['s', 3]]
        assert arm['declared_runs'] == 3
    md = p1.render_markdown(result)
    for label in data:
        assert f'Incomplete comparison: {label} is missing 2 of 3 declared runs: s epoch 2, s epoch 3.' in md
    assert md.index('Incomplete comparison:') < md.index('Read-name source:')


def test_full_declared_grid_is_complete():
    rows = [{**record(epoch=epoch), 'scenario': scenario} for scenario in ('s', 't') for epoch in range(1, 4)]
    data = arms(rows, rows)
    for arm in data.values():
        arm['run'].update(epochs=3, scenario_ids=['s', 't'])
    result = p1.compare_arms(data, names=set())
    assert result['comparison_complete'] is True
    assert all(arm['complete'] is True and arm['missing'] == [] for arm in result['arms'].values())
    assert result['paired_epochs'] == {'s': [1, 2, 3], 't': [1, 2, 3]}
    assert 'Incomplete comparison:' not in p1.render_markdown(result)


def test_deltas_pair_only_shared_epochs_and_allow_asymmetric_stops():
    a = [record(epoch=e) for e in (1, 2, 3)]
    b = [record(epoch=e) for e in (1, 3)]
    a[1]['turns'][0]['delivered_epoch'] = 1000  # unmatched epoch must not skew the baseline
    a[1]['turns'][1]['elapsed'] = 900
    data = arms(a, b)
    for arm in data.values():
        arm['run']['epochs'] = 3
    result = p1.compare_arms(data, names=set())
    assert result['arms']['limit1']['complete'] is True
    assert result['arms']['limit4']['missing'] == [['s', 2]]
    assert result['paired_epochs']['s'] == [1, 3]
    assert result['paired_deltas']['s']['latency_median'] == 0
    assert result['paired_deltas']['s']['verified_rate'] == 0
    assert 'only epochs present in both arms' in p1.render_markdown(result)


def test_usage_limit_records_are_missing_for_completeness_and_pairing():
    rows = [record(epoch=e) for e in (1, 2, 3)]
    stopped = {**record(status='failed', epoch=3), 'failure_classes': ['usage-limit']}
    data = arms(rows, [*rows[:2], stopped])
    for arm in data.values():
        arm['run']['epochs'] = 3
    result = p1.compare_arms(data, names=set())
    assert result['comparison_complete'] is False
    assert result['arms']['limit4']['missing'] == [['s', 3]]
    assert result['arms']['limit4']['summary']['runs'] == 3  # failed run remains reportable
    assert result['paired_epochs']['s'] == [1, 2]
    assert result['paired_deltas']['s']['verified_rate'] == 0


def test_declared_scenario_with_no_observed_runs_is_missing():
    data = arms([record()], [record()])
    for arm in data.values():
        arm['run']['scenario_ids'] = ['s', 't']
    result = p1.compare_arms(data, names=set())
    assert result['arms']['limit1']['missing'] == [['t', 1]]
    assert result['comparison_complete'] is False
    assert result['paired_epochs']['t'] == []
    assert result['paired_deltas']['t']['latency_median'] is None



@pytest.mark.parametrize('shared_root, expected_attempts, expected_cost', [(True, 4, .10), (False, 8, .20)])
def test_work_families_count_once_without_deduplicating_turn_latency(shared_root, expected_attempts, expected_cost):
    import copy
    run = record()
    first = {'root': 'root-1', 'ledger': {'turns': 2, 'attempts': 4, 'nudges': 1},
             'tools': [{**event('web_search', status, at), 'scope': 'subscription-mcp-bridge', 'call_id': 'call-1'}
                       for status, at in [('running', 1), ('succeeded', 2)]],
             'engine': [{'usage': {'input_tokens': 20}, 'cost_usd': .10}]}
    second = copy.deepcopy(first)
    if not shared_root:
        second['root'] = 'root-2'
        for row in second['tools']:
            row['call_id'] = 'call-2'
            row['created'] += 10
    run['work'] = {'turn-1': first, 'turn-2-retry': second}
    measured = p1.measure(run, {'web_search'})
    assert measured['ledger']['attempts'] == expected_attempts
    assert measured['cost_usd'] == pytest.approx(expected_cost)
    families = 1 if shared_root else 2
    assert measured['ledger']['turns'] == 2 * families
    assert measured['ledger']['nudges'] == families
    assert measured['reads']['bridge_calls'] == families
    assert measured['reads']['bridge_peak'] == 1
    assert measured['usage'] == {'input_tokens': 20 * families}
    assert len(measured['engine']) == families
    assert measured['latencies'] == [6, 2]
    summary = p1.compare_arms(arms([run], [run]), names={'web_search'})['arms']['limit1']['summary']
    assert summary['ledger']['attempts']['mean'] == expected_attempts
    assert summary['cost_usd'] == pytest.approx(expected_cost)
    assert summary['latency']['n'] == 2


@pytest.mark.parametrize('failure', [
    {'kind': 'infra', 'name': 'judgment_ai'},
    {'kind': 'check', 'name': 'other_boundary'},
    {'kind': 'authority', 'name': 'uncovered'},
    {'kind': 'check', 'name': 'judge'},  # a coincidental name is not a rubric judge grade
])
def test_non_grade_failures_prevent_verified_completion(failure):
    run = {**record(), 'failures': [{**failure, 'reason': 'configured path was not measured'}]}
    result = p1.compare_arms(arms([run], [run]), names=set())
    arm = result['arms']['limit1']
    key = failure['kind'] + ':' + failure['name']
    assert arm['summary']['verified'] == 0
    assert len(arm['not_verified']) == 1
    assert arm['not_verified'][0]['statuses'] == ['succeeded', 'succeeded']
    assert key in arm['not_verified'][0]['failure_classes']
    assert key in p1.render_markdown(result)


@pytest.mark.parametrize('failure', [
    {'kind': 'rubric', 'name': 'follow_through'},
    {'kind': 'judge', 'name': 'grade'},
    {'kind': 'infra', 'name': 'judge'},  # eval task's judge transport failure
])
def test_rubric_and_eval_judge_failures_do_not_change_verified_completion(failure):
    run = {**record(), 'failures': [failure]}
    result = p1.compare_arms(arms([run], [run]), names=set())
    arm = result['arms']['limit1']
    assert arm['summary']['verified'] == 1
    assert arm['not_verified'] == []



def test_identical_legacy_snapshots_deduplicate_with_stable_keys():
    first = {'ledger': {'turns': 2, 'attempts': 4, 'nudges': 1},
             'tools': [event('web_search', 'running', 1), event('web_search', 'succeeded', 2)],
             'engine': [{'usage': {'input_tokens': 20}, 'cost_usd': .10}]}
    # Different object identities and dictionary insertion order are still one snapshot.
    second = {'engine': [{'cost_usd': .10, 'usage': {'input_tokens': 20}}],
              'tools': [dict(reversed(list(row.items()))) for row in first['tools']],
              'ledger': {'nudges': 1, 'attempts': 4, 'turns': 2}}
    run = record()
    run['work'] = {'turn-1': first, 'turn-2': second}
    measured = p1.measure(run, {'web_search'})
    assert measured['families_deduplicated_without_root'] == 1
    assert measured['ledger']['attempts'] == 4
    assert measured['cost_usd'] == pytest.approx(.10)
    assert measured['reads']['direct_calls'] == 1
    assert measured['usage'] == {'input_tokens': 20}
    assert measured['latencies'] == [6, 2]
    result = p1.compare_arms(arms([run], [run]), names={'web_search'})
    assert result['arms']['limit1']['runs'][0]['families_deduplicated_without_root'] == 1


@pytest.mark.parametrize('changed_field', ['ledger', 'tools', 'engine'])
def test_distinct_legacy_snapshots_still_sum(changed_field):
    import copy
    first = {'ledger': {'turns': 2, 'attempts': 4, 'nudges': 1},
             'tools': [event('web_search', 'running', 1), event('web_search', 'succeeded', 2)],
             'engine': [{'usage': {'input_tokens': 20}, 'cost_usd': .10}]}
    second = copy.deepcopy(first)
    if changed_field == 'ledger':
        second['ledger']['nudges'] = 2
    elif changed_field == 'tools':
        second['tools'][0]['created'] = 1.5
    else:
        second['engine'][0]['usage']['input_tokens'] = 21
    run = record()
    run['work'] = {'turn-1': first, 'turn-2': second}
    measured = p1.measure(run, {'web_search'})
    assert measured['families_deduplicated_without_root'] == 0
    assert measured['ledger']['attempts'] == 8
    assert measured['cost_usd'] == pytest.approx(.20)
    assert measured['reads']['direct_calls'] == 2
    assert measured['usage']['input_tokens'] == (41 if changed_field == 'engine' else 40)


def test_explicit_distinct_roots_override_identical_snapshot_contents():
    import copy
    first = {'root': 'a', 'ledger': {'turns': 2, 'attempts': 4, 'nudges': 1}, 'tools': [],
             'engine': [{'cost_usd': .10}]}
    second = {**copy.deepcopy(first), 'root': 'b'}
    run = record()
    run['work'] = {'turn-1': first, 'turn-2': second}
    measured = p1.measure(run, set())
    assert measured['ledger']['attempts'] == 8
    assert measured['cost_usd'] == pytest.approx(.20)
    assert measured['families_deduplicated_without_root'] == 0


def test_missing_declared_cohort_makes_completeness_unknown():
    data = arms([record()], [record()])
    for arm in data.values():
        del arm['run']['scenario_ids']
    result = p1.compare_arms(data, names=set())
    assert result['comparison_complete'] == 'unknown'
    assert all(arm['complete'] == 'unknown' for arm in result['arms'].values())
    assert all(arm['missing'] is None for arm in result['arms'].values())
    assert 'declared cohort was unavailable' in p1.render_markdown(result)


def _cohort_log(sample_ids=None, declared=None):
    samples = []
    for epoch in range(1, 4):
        run = record(epoch=epoch)
        samples.append(SimpleNamespace(id='s@codex', epoch=epoch, metadata={'run': run}, scores={
            'secretary': SimpleNamespace(metadata={'scenario': 's', 'worker': 'codex', 'checks': run['checks']}, value={})}))
    metadata = {'worker': 'codex', 'id': 'cohort-fixture'}
    if declared is not None:
        metadata['scenario_ids'] = declared
    return SimpleNamespace(samples=samples, eval=SimpleNamespace(
        metadata=metadata, dataset=SimpleNamespace(sample_ids=sample_ids), config=SimpleNamespace(epochs=3)))


@pytest.mark.parametrize('source', ['metadata', 'inspect', 'flag', 'unavailable'])
def test_cli_cohort_recovery_does_not_use_completed_samples(tmp_path, monkeypatch, source):
    import types
    from agentos_eval import cli
    log = _cohort_log(sample_ids=['s@codex', 't@codex'] if source == 'inspect' else None,
                      declared=['s', 't'] if source == 'metadata' else None)
    module = types.ModuleType('inspect_ai.log')
    def read_log(path):
        import copy
        distinct = copy.deepcopy(log)
        distinct.eval.metadata['id'] = path
        return distinct
    module.read_eval_log = read_log
    monkeypatch.setitem(sys.modules, 'inspect_ai.log', module)
    out = tmp_path / 'cohort.json'
    args = ['p1-report', '--arm', 'limit1=one.eval', '--arm', 'limit4=four.eval', '--json', str(out)]
    if source == 'flag':
        args += ['--scenarios', 's,t']
    assert cli.main(args) == 0
    result = json.loads(out.read_text())
    if source == 'unavailable':
        assert result['comparison_complete'] == 'unknown'
        assert result['arms']['limit1']['complete'] == 'unknown'
    else:
        assert result['comparison_complete'] is False
        for arm in result['arms'].values():
            assert arm['identity']['scenario_ids'] == ['s', 't']
            assert arm['missing'] == [['t', 1], ['t', 2], ['t', 3]]
            assert arm['declared_runs'] == 6


@pytest.mark.parametrize('source', ['metadata', 'inspect'])
def test_cli_scenarios_disagreement_refused(monkeypatch, source):
    import types
    from agentos_eval import cli
    log = _cohort_log(declared=['s'] if source == 'metadata' else None,
                      sample_ids=['s@codex'] if source == 'inspect' else None)
    module = types.ModuleType('inspect_ai.log')
    module.read_eval_log = lambda path: log
    monkeypatch.setitem(sys.modules, 'inspect_ai.log', module)
    with pytest.raises(SystemExit, match='--scenarios disagrees with'):
        cli.main(['p1-report', '--arm', 'limit1=a.eval', '--arm', 'limit4=b.eval', '--scenarios', 's,t'])


@pytest.mark.parametrize('sample_ids', [[1, 2], ['s'], ['s@unrecognized-worker']])
def test_unknown_inspect_id_formats_do_not_invent_a_cohort(sample_ids):
    declared, source = p1.declared_cohort(_cohort_log(sample_ids=sample_ids))
    assert declared is None
    assert source == 'unavailable'


def _stub_inspect_task(monkeypatch):
    """Load the real task construction with model-free Inspect stand-ins."""
    import importlib.util
    import types
    package = types.ModuleType('inspect_ai')
    package.__path__ = []
    package.Task = lambda **kwargs: SimpleNamespace(**kwargs)
    package.task = lambda fn: fn
    monkeypatch.setitem(sys.modules, 'inspect_ai', package)
    dataset = types.ModuleType('inspect_ai.dataset')
    dataset.MemoryDataset = lambda samples, name: list(samples)
    dataset.Sample = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, 'inspect_ai.dataset', dataset)
    model = types.ModuleType('inspect_ai.model')
    for name in ('ChatMessageAssistant', 'ChatMessageSystem', 'ChatMessageUser', 'GenerateConfig', 'ModelOutput', 'get_model'):
        setattr(model, name, lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, 'inspect_ai.model', model)
    scorer = types.ModuleType('inspect_ai.scorer')
    scorer.Score, scorer.Target = object, object
    scorer.mean = lambda: None
    scorer.scorer = lambda **kwargs: lambda fn: fn
    monkeypatch.setitem(sys.modules, 'inspect_ai.scorer', scorer)
    solver = types.ModuleType('inspect_ai.solver')
    solver.Generate, solver.TaskState = object, object
    solver.solver = lambda fn: fn
    monkeypatch.setitem(sys.modules, 'inspect_ai.solver', solver)
    util = types.ModuleType('inspect_ai.util')
    util.EarlyStop = object
    monkeypatch.setitem(sys.modules, 'inspect_ai.util', util)
    path = Path(__file__).resolve().parents[1] / 'evals' / 'agentos_eval' / 'task.py'
    spec = importlib.util.spec_from_file_location('agentos_eval.task', path)
    task = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, 'agentos_eval.task', task)
    spec.loader.exec_module(task)
    monkeypatch.setattr(task, 'SandboxPool', lambda **kwargs: None)
    monkeypatch.setattr(task, 'secretary', lambda *args, **kwargs: None)
    return package, task


def test_sweep_and_task_metadata_record_selected_ids_after_filters_and_limit(monkeypatch):
    from agentos_eval import cli, scenarios
    inspect, task = _stub_inspect_task(monkeypatch)
    ids = [s['id'] for s in scenarios.load(include_local=False, split='dev')[:2]]
    assert len(ids) == 2
    selected = task.agentos_secretary(scenarios=','.join(ids), split='dev', worker='codex',
                                     include_local=False, judge='none', sample_limit=1)
    assert len(selected.dataset) == 1
    assert selected.metadata['scenario_ids'] == [ids[0]]
    captured = {}

    def inspect_eval(selected_task, **kwargs):
        captured['task'] = selected_task
        captured.update(kwargs)
        return [SimpleNamespace()]

    inspect.eval = inspect_eval
    monkeypatch.setattr(cli, 'git_head', lambda: 'fixture-head')
    monkeypatch.setattr(cli, '_write', lambda *args: {'trend': {'regressions': []}})
    assert cli.main(['sweep', '--scenarios', ','.join(ids), '--split', 'dev', '--worker', 'codex',
                     '--judge', 'none', '--no-local', '--limit', '1', '--epochs', '3']) == 0
    assert captured['metadata']['scenario_ids'] == [ids[0]]
    assert captured['task'].metadata['scenario_ids'] == [ids[0]]
    assert len(captured['task'].dataset) == 1


def _one_epoch_log(arm, index):
    log = _cohort_log(declared=['s'])
    log.samples = log.samples[:1]
    log.eval.metadata.update(epochs=1, id=f'{arm}-run-{index}', started=1000 + index * 100,
                             git_head=f'{arm}-head')
    log.eval.config.epochs = 1
    return log


def test_cli_combines_three_logs_per_arm_in_epoch_order(tmp_path, monkeypatch):
    import types
    from agentos_eval import cli
    logs = {f'{arm}-{i}.eval': _one_epoch_log(arm, i) for arm in ('limit1', 'limit4') for i in range(1, 4)}
    read_paths = []
    module = types.ModuleType('inspect_ai.log')

    def read_log(path):
        read_paths.append(path)
        return logs[path]

    module.read_eval_log = read_log
    monkeypatch.setitem(sys.modules, 'inspect_ai.log', module)
    out, md = tmp_path / 'multi.json', tmp_path / 'multi.md'
    assert cli.main(['p1-report', '--arm', 'limit1=limit1-1.eval,limit1-2.eval,limit1-3.eval',
                     '--arm', 'limit4=limit4-1.eval,limit4-2.eval,limit4-3.eval',
                     '--json', str(out), '--md', str(md)]) == 0
    result = json.loads(out.read_text())
    assert result['comparison_complete'] is True
    assert read_paths == [f'{arm}-{i}.eval' for arm in ('limit1', 'limit4') for i in range(1, 4)]
    for arm in ('limit1', 'limit4'):
        measured = result['arms'][arm]
        assert measured['identity']['epochs'] == 3
        assert measured['identity']['git_head'] == f'{arm}-head'
        assert [r['epoch'] for r in measured['runs']] == [1, 2, 3]
        assert measured['identity']['runs'] == [
            {'id': f'{arm}-run-{i}', 'started': 1000 + i * 100, 'epochs': 1, 'epoch_offset': i - 1}
            for i in range(1, 4)]
        assert measured['summary']['latency']['n'] == 6
        assert measured['missing'] == []
        assert measured['complete'] is True
        for i in range(1, 4):
            assert f'{arm}-run-{i}' in md.read_text()
            assert str(1000 + i * 100) in md.read_text()
    # Input log epochs remain local: reporting must not mutate the logs.
    assert all(log.samples[0].epoch == 1 for log in logs.values())


@pytest.mark.parametrize('key,value', [('git_head', 'another-head'), ('worker', 'claude-code'),
                                       ('scenario_ids', ['s', 'another-scenario'])])
def test_cli_rejects_different_identities_within_one_arm(monkeypatch, key, value):
    import types
    from agentos_eval import cli
    a, b = _one_epoch_log('limit1', 1), _one_epoch_log('limit1', 2)
    b.eval.metadata[key] = value
    if key == 'worker':
        b.samples[0].scores['secretary'].metadata['worker'] = value
    module = types.ModuleType('inspect_ai.log')
    module.read_eval_log = {'a.eval': a, 'b.eval': b}.__getitem__
    monkeypatch.setitem(sys.modules, 'inspect_ai.log', module)
    with pytest.raises(SystemExit, match=f'Cannot combine arm limit1: {key} differs between log 1 and log 2'):
        cli.main(['p1-report', '--arm', 'limit1=a.eval,b.eval', '--arm', 'limit4=a.eval'])


def test_log_epoch_offsets_use_declared_counts_even_when_a_log_stops_early():
    first, second = _one_epoch_log('limit1', 1), _one_epoch_log('limit1', 2)
    first.eval.metadata['epochs'] = 2  # local epoch 2 did not run
    combined = p1.combine_arm_logs([first, second], 'limit1')
    assert combined['run']['epochs'] == 3
    assert [r['epoch'] for r in combined['records']] == [1, 3]
    assert [r['epoch_offset'] for r in combined['run']['runs']] == [0, 2]
    other_first, other_second = _one_epoch_log('limit4', 1), _one_epoch_log('limit4', 2)
    other_first.eval.metadata['epochs'] = 2
    other = p1.combine_arm_logs([other_first, other_second], 'limit4')
    result = p1.compare_arms({'limit1': combined, 'limit4': other}, names=set())
    assert result['comparison_complete'] is False
    assert result['arms']['limit1']['missing'] == [['s', 2]]
    assert result['paired_epochs']['s'] == [1, 3]


def test_single_log_retains_epoch_and_identity():
    log = _one_epoch_log('limit1', 1)
    single = p1.combine_arm_logs([log], 'limit1')
    assert single['run']['id'] == 'limit1-run-1'
    assert single['run']['epochs'] == 1
    assert single['records'][0]['epoch'] == 1
    assert single['run']['runs'] == [{'id': 'limit1-run-1', 'started': 1100, 'epochs': 1, 'epoch_offset': 0}]



@pytest.mark.parametrize('duplicate_field', ['metadata.id', 'run_id', 'eval_id'])
def test_repeated_run_aliases_within_arm_rejected_before_offset(duplicate_field):
    import copy
    first = _one_epoch_log('limit1', 1)
    second, third = copy.deepcopy(first), copy.deepcopy(first)
    for index, log in enumerate((first, second, third), 1):
        log.eval.metadata['id'] = f'unique-metadata-{index}'
        if duplicate_field == 'metadata.id':
            log.eval.metadata['id'] = 'reused-trial'
        else:
            setattr(log.eval, duplicate_field, 'reused-trial')
    with pytest.raises(ValueError, match=f'Duplicate run identity .*reused-trial'):
        p1.combine_arm_logs([first, second, third], 'limit1')
    assert first.samples[0].epoch == second.samples[0].epoch == third.samples[0].epoch == 1


@pytest.mark.parametrize('across_arms', [False, True])
def test_cli_rejects_same_log_under_different_paths(monkeypatch, across_arms):
    import copy
    import types
    from agentos_eval import cli
    log = _one_epoch_log('limit1', 1)
    module = types.ModuleType('inspect_ai.log')
    module.read_eval_log = lambda path: copy.deepcopy(log)
    monkeypatch.setitem(sys.modules, 'inspect_ai.log', module)
    first_arm = 'limit1=a.eval' if across_arms else 'limit1=a.eval,alias.eval,another-alias.eval'
    with pytest.raises(SystemExit, match='Duplicate run identity metadata.id=limit1-run-1'):
        cli.main(['p1-report', '--arm', first_arm, '--arm', 'limit4=copy.eval'])


@pytest.mark.parametrize('across_arms', [False, True])
def test_idless_log_copies_are_rejected_by_file_digest(tmp_path, monkeypatch, across_arms):
    import copy
    import hashlib
    import types
    from agentos_eval import cli
    log = _one_epoch_log('limit1', 1)
    del log.eval.metadata['id']
    content = b'fixture Inspect log bytes without run ids'
    first, alias = tmp_path / 'original.eval', tmp_path / 'copy.eval'
    first.write_bytes(content)
    alias.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    module = types.ModuleType('inspect_ai.log')
    module.read_eval_log = lambda path: copy.deepcopy(log)
    monkeypatch.setitem(sys.modules, 'inspect_ai.log', module)
    first_arm = f'limit1={first}' if across_arms else f'limit1={first},{alias}'
    with pytest.raises(SystemExit, match=f'Duplicate run identity sha256={digest}'):
        cli.main(['p1-report', '--arm', first_arm, '--arm', f'limit4={alias}'])


def test_distinct_idless_log_files_remain_distinct(tmp_path):
    logs, paths = [], []
    for index in range(1, 4):
        log = _one_epoch_log('limit1', index)
        del log.eval.metadata['id']
        path = tmp_path / f'{index}.eval'
        path.write_bytes(f'distinct fixture log {index}'.encode())
        logs.append(log)
        paths.append(path)
    combined = p1.combine_arm_logs(logs, 'limit1', paths=paths)
    assert combined['run']['epochs'] == 3
    assert [r['epoch'] for r in combined['records']] == [1, 2, 3]



def test_comparison_rejects_reuse_even_when_arms_were_combined_separately():
    log = _one_epoch_log('limit1', 1)
    a = p1.combine_arm_logs([log], 'limit1')
    b = p1.combine_arm_logs([log], 'limit4')
    with pytest.raises(ValueError, match='Duplicate run identity metadata.id=limit1-run-1'):
        p1.compare_arms({'limit1': a, 'limit4': b}, names=set())
