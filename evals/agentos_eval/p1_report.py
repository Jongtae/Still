"""P1 measurements from existing Inspect records; no judge or model calls.

Adopt Inspect's records_from_samples and stdlib statistics; adapt the persisted
WorkLedger/event schema. No dependency, authority or runtime changes (#16/#827).
"""
import math
from collections import Counter
from statistics import mean, median

# Explicit fallback snapshot of CONCURRENT_CALL_READS at this revision. Resolved
# lazily so reporting also works with only evals/ on PYTHONPATH.
CONCURRENT_READ_NAMES = frozenset('calendar_query capability_search drive_read drive_search find_files information_use '
                                 'list_agents list_memory list_notes list_roots mail_read mail_search public_page_read '
                                 'read_file search_conversation search_memory settings_read weather web_search'.split())


def read_names():
    try:
        from personal_agent.agent_runtime import CONCURRENT_CALL_READS
        return CONCURRENT_CALL_READS, 'personal_agent.agent_runtime.CONCURRENT_CALL_READS'
    except ImportError:
        return CONCURRENT_READ_NAMES, 'explicit module constant CONCURRENT_READ_NAMES (snapshot)'


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def stats(values):
    values = [v for v in values if numeric(v)]
    return {'n': len(values), 'median': median(values) if values else None,
            'mean': mean(values) if values else None, 'min': min(values) if values else None,
            'max': max(values) if values else None}


def overlap(rows, names):
    """Governed peak in database id order; CLI-native rows are post-run replay.

    Call ids validate pairing when available. Without ids, balanced in-flight
    events still establish the peak, including interleaved same-tool calls.
    """
    if rows is None:
        return {'calls': None, 'governed_calls': None, 'native_calls': None,
                'peak': None, 'reason': 'tool timings unavailable'}
    reads = [r for r in rows if (r.get('host_action') or r.get('tool')) in names]
    governed = [r for r in reads if r.get('scope') in (None, 'subscription-mcp-bridge')]
    calls = sum(r.get('status') == 'running' for r in governed)
    native = sum(r.get('status') == 'running' and r.get('scope') == 'cli-native' for r in reads)
    result = {'calls': calls, 'governed_calls': calls, 'native_calls': native, 'peak': None, 'reason': None}
    pending, anonymous = set(), 0
    active = peak = 0
    for row in governed:
        status, call_id = row.get('status'), row.get('call_id')
        if status not in ('running', 'succeeded', 'failed'):
            continue
        if status == 'running':
            if call_id is not None:
                if call_id in pending:
                    return {**result, 'reason': 'duplicate running call id; terminal pairing incomplete'}
                pending.add(call_id)
            else:
                anonymous += 1
            active += 1
            peak = max(peak, active)
        else:
            if call_id is not None:
                if call_id not in pending:
                    return {**result, 'reason': 'terminal call id has no running row'}
                pending.remove(call_id)
            else:
                if anonymous == 0:
                    return {**result, 'reason': 'terminal row has no running row'}
                anonymous -= 1
            active -= 1
    if pending or anonymous:
        return {**result, 'reason': 'missing terminal rows for governed read calls'}
    return {**result, 'peak': peak}


def measure(record, names):
    turns = record.get('turns') or []
    statuses = [t.get('status') for t in turns] if turns else record.get('statuses') or []
    checks = record.get('checks')
    checks_pass = bool(checks) and all(v is True for v in checks.values()) if isinstance(checks, dict) else (
        record.get('checks_pass', (record.get('value') or {}).get('checks_pass')) == 1)
    # An unscored/missing run is never verified by vacuous checks.
    verified = record.get('judge_status') != 'not_scored' and bool(statuses) and checks_pass and all(s == 'succeeded' for s in statuses)
    latencies = []
    for turn in turns:
        sent, delivered = turn.get('sent_epoch'), turn.get('delivered_epoch')
        value = delivered - sent if numeric(sent) and numeric(delivered) else turn.get('elapsed')
        if numeric(value) and value >= 0:
            latencies.append(value)
    if not turns:
        latencies = [v for v in record.get('elapsed') or [] if numeric(v) and v >= 0]
    work = record.get('work')
    measurements = list(work.values()) if isinstance(work, dict) else []
    ledgers = [m.get('ledger') for m in measurements]
    counts = {key: sum(l[key] for l in ledgers) if ledgers and all(l and numeric(l.get(key)) for l in ledgers) else None
              for key in ('turns', 'attempts', 'nudges')}
    tools = [r for m in measurements for r in m['tools']] if measurements and all(m.get('tools') is not None for m in measurements) else None
    engines = [e for m in measurements for e in m.get('engine') or []]
    usage = Counter()
    cost = []
    for engine in engines:
        for key, value in (engine.get('usage') or {}).items():
            if numeric(value):
                usage[key] += value
        if numeric(engine.get('cost_usd')):
            cost.append(engine['cost_usd'])
    failure_classes = set(filter(None, record.get('failure_classes') or [t.get('failure_class') for t in turns]))
    failure_classes.update(f"{f.get('kind')}:{f.get('name')}" for f in record.get('failures') or []
                           if f.get('kind') != 'rubric' and f.get('name') != 'judge')
    return {'scenario': record['scenario'], 'epoch': record.get('epoch', 1), 'sample_id': record.get('sample_id'),
            'verified': verified, 'statuses': statuses,
            'failure_classes': sorted(failure_classes),
            'failed_checks': [k for k, v in (checks or {}).items() if v is not True],
            'latencies': latencies, 'ledger': counts, 'reads': overlap(tools, names),
            'usage': dict(usage) or 'unavailable', 'cost_usd': sum(cost) if cost else 'unavailable',
            'engine': engines,
            'stage_timings': {stage: [r['ms'] for r in tools or [] if r.get('tool') == 'response_timing'
                                     and r.get('stage') == stage and numeric(r.get('ms'))]
                              for stage in ('queue_wait', 'routed', 'finished')}}


def summarize(runs):
    usage = Counter()
    costs = []
    for run in runs:
        if isinstance(run['usage'], dict):
            usage.update(run['usage'])
        if numeric(run['cost_usd']):
            costs.append(run['cost_usd'])
    epochs = sorted({r['epoch'] for r in runs})
    return {'runs': len(runs), 'verified': sum(r['verified'] for r in runs),
            'verified_rate': mean(r['verified'] for r in runs) if runs else None,
            'latency': stats(v for r in runs for v in r['latencies']),
            'epoch_latency_medians': {str(e): stats(v for r in runs if r['epoch'] == e for v in r['latencies'])['median'] for e in epochs},
            'ledger': {k: stats(r['ledger'][k] for r in runs) for k in ('turns', 'attempts', 'nudges')},
            'read_calls': stats(r['reads']['calls'] for r in runs),
            'native_read_calls': stats(r['reads']['native_calls'] for r in runs),
            'governed_read_calls': stats(r['reads']['governed_calls'] for r in runs),
            'stage_timings': {stage: stats(v for r in runs for v in r['stage_timings'][stage])
                              for stage in ('queue_wait', 'routed', 'finished')},
            'read_peak': stats(r['reads']['peak'] for r in runs),
            'runs_peak_ge_2': sum(r['reads']['peak'] is not None and r['reads']['peak'] >= 2 for r in runs),
            'overlap_unknown': sum(r['reads']['peak'] is None for r in runs),
            'usage': dict(usage) or 'unavailable', 'cost_usd': sum(costs) if costs else 'unavailable',
            'usage_unavailable_runs': sum(r['usage'] == 'unavailable' for r in runs),
            'cost_unavailable_runs': sum(r['cost_usd'] == 'unavailable' for r in runs)}


def compare_arms(arms, names=None):
    """arms: label -> {'records': records_from_samples(...), 'run': log.eval.metadata}."""
    if len(arms) != 2:
        raise ValueError('P1 requires exactly two arms')
    names, source = read_names() if names is None else (frozenset(names), 'explicit argument')
    output = {}
    for label, arm in arms.items():
        records, run = arm['records'], arm.get('run') or {}
        scenarios = sorted({r['scenario'] for r in records})
        workers = sorted({r.get('worker') or run.get('worker') or 'unknown' for r in records})
        epochs = run.get('epochs') or len({r.get('epoch', 1) for r in records})
        measured = [measure(r, names) for r in records]
        groups = {s: [r for r in measured if r['scenario'] == s] for s in scenarios}
        output[label] = {'identity': {**{k: run.get(k) for k in ('id', 'git_head', 'seed', 'judge', 'model')},
                                      'worker': workers, 'epochs': epochs, 'scenario_ids': scenarios},
                         'summary': summarize(measured), 'per_scenario': {s: summarize(g) for s, g in groups.items()},
                         'per_scenario_epoch': {s: {str(e): summarize([r for r in g if r['epoch'] == e])
                                                    for e in sorted({r['epoch'] for r in g})} for s, g in groups.items()},
                         'runs': measured, 'not_verified': [r for r in measured if not r['verified']]}
    labels = list(output)
    a, b = [output[l] for l in labels]
    for key in ('scenario_ids', 'worker', 'epochs'):
        if a['identity'][key] != b['identity'][key]:
            raise ValueError(f'Cannot compare arms: {key} differ')
    # Also reject incomplete or duplicated epoch pairings, even if metadata says 3.
    cohorts = [Counter((r['scenario'], r['epoch']) for r in arm['runs']) for arm in (a, b)]
    if cohorts[0] != cohorts[1] or any(n != 1 for n in cohorts[0].values()):
        raise ValueError('Cannot compare arms: scenario × epoch runs differ or are duplicated')
    if set(labels) == {'limit1', 'limit4'}:
        labels = ['limit1', 'limit4']
        a, b = [output[l] for l in labels]
    deltas = {}
    for s in a['identity']['scenario_ids']:
        x, y = a['per_scenario'][s], b['per_scenario'][s]
        def delta(v, w):
            return w - v if numeric(v) and numeric(w) else None
        deltas[s] = {'verified_rate': delta(x['verified_rate'], y['verified_rate']),
                     'latency_median': delta(x['latency']['median'], y['latency']['median']),
                     'turns_mean': delta(x['ledger']['turns']['mean'], y['ledger']['turns']['mean']),
                     'attempts_mean': delta(x['ledger']['attempts']['mean'], y['ledger']['attempts']['mean']),
                     'read_calls_mean': delta(x['read_calls']['mean'], y['read_calls']['mean']),
                     'native_read_calls_mean': delta(x['native_read_calls']['mean'], y['native_read_calls']['mean']),
                     'read_peak_mean': delta(x['read_peak']['mean'], y['read_peak']['mean']),
                     'cost_usd': delta(x['cost_usd'], y['cost_usd']) if numeric(x['cost_usd']) and numeric(y['cost_usd']) else 'unavailable',
                     'usage': {k: y['usage'][k] - x['usage'][k] for k in sorted(x['usage'].keys() & y['usage'].keys())}
                              if isinstance(x['usage'], dict) and isinstance(y['usage'], dict) else 'unavailable'}
    return {'schema_version': 2, 'arms': output, 'read_names_source': source, 'read_names': sorted(names),
            'delta_direction': f'{labels[1]} − {labels[0]}', 'paired_deltas': deltas}


def render_markdown(report):
    import json
    lines = ['# P1 read concurrency report', '', '## Fixture support', '',
             'P1 measurement schema v2. Test results and the harness revision are not embedded in the logs; '
             'model-free fixtures do not establish live worker performance.', '',
             '## Live evidence', '', f"Read-name source: {report['read_names_source']}", '']
    def seconds(value):
        return f'{value:.1f}' if numeric(value) else 'unavailable'

    for label, arm in report['arms'].items():
        lines += [f'### {label}', '', 'Identity: ' + json.dumps(arm['identity'], ensure_ascii=False), '',
                  '| scope | runs | verified | rate | latency n / median / mean / min / max (s) | turns median / mean / min / max | attempts median / mean / min / max | governed read calls median / mean / min / max | native read calls median / mean / min / max | governed read peak median / mean / min / max | peak ≥ 2 / unknown | usage | USD |',
                  '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
        for scope, row in [('all', arm['summary']), *arm['per_scenario'].items()]:
            def cell(stat, keys=('median', 'mean', 'min', 'max'), timing=False):
                return ' / '.join(seconds(stat[k]) if timing and k != 'n' else str(stat[k]) for k in keys)
            lines.append(f"| {scope} | {row['runs']} | {row['verified']} | {row['verified_rate']} | "
                         f"{cell(row['latency'], ('n', 'median', 'mean', 'min', 'max'), timing=True)} | {cell(row['ledger']['turns'])} | "
                         f"{cell(row['ledger']['attempts'])} | {cell(row['read_calls'])} | {cell(row['native_read_calls'])} | {cell(row['read_peak'])} | "
                         f"{row['runs_peak_ge_2']} / {row['overlap_unknown']} | {row['usage']} | {row['cost_usd']} |")
        lines += ['', '| response stage | n | median (ms) | min (ms) | max (ms) |',
                  '| --- | --- | --- | --- | --- |']
        for stage, timing in arm['summary']['stage_timings'].items():
            values = [str(timing['n']), *[str(round(timing[k])) if numeric(timing[k]) else 'unavailable'
                                         for k in ('median', 'min', 'max')]]
            lines.append('| ' + ' | '.join([stage, *values]) + ' |')
        lines += ['', 'Engine model / effort (as reported):']
        engines = [e for r in arm['runs'] for e in r['engine']]
        reported = sorted({(json.dumps(e.get('model')), json.dumps(e.get('effort'))) for e in engines
                           if 'model' in e or 'effort' in e})
        for model, effort in reported:
            display = lambda v: 'CLI default (not reported)' if v in ('null', '""') else v
            lines.append(f'- model: {display(model)}; effort: {display(effort)}')
        if not reported:
            lines.append('- unavailable')
        lines += ['', 'Unverified runs (statuses and failure classes):']
        lines += ['- ' + json.dumps({k: r[k] for k in ('scenario', 'epoch', 'statuses', 'failure_classes', 'failed_checks')})
                  for r in arm['not_verified']] or ['- none']
    lines += ['', f"### Paired deltas ({report['delta_direction']})", '',
              '| scenario | verified rate | median latency (s) | mean turns | mean attempts | mean governed read calls | mean native read calls | mean governed read peak | USD | usage |',
              '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for scenario, row in report['paired_deltas'].items():
        lines.append('| ' + ' | '.join([scenario, *[seconds(v) if k == 'latency_median' else str(v) for k, v in row.items()]]) + ' |')
    lines += ['', '## Uncertainty', '', 'Epoch latency medians (seconds):', '']
    for label, arm in report['arms'].items():
        lines.append(f"- {label} / all: { {k: seconds(v) for k, v in arm['summary']['epoch_latency_medians'].items()} }")
        for scenario, row in arm['per_scenario'].items():
            lines.append(f"- {label} / {scenario}: { {k: seconds(v) for k, v in row['epoch_latency_medians'].items()} }")
        for run in arm['runs']:
            if run['reads']['reason']:
                lines.append(f"- {label} / {run['scenario']} / epoch {run['epoch']}: {run['reads']['reason']}")
    lines += ['', 'No significance or causal conclusion is inferred. Judge grades are excluded.', '',
              '## Unsupported paths', '',
              'Missing worker usage/cost is unavailable; reported totals cover reported runs only. '
              'API calls whose read effect depends on arguments cannot be classified from these rows. '
              'Native-search rows are replayed after the run and are not governed by CONCURRENT_READ_LIMIT; '
              'they are counted separately and excluded from the governed peak. Without call ids, '
              'the peak uses an in-flight event counter. Browser/code mutations, '
              'delegated specialists, approvals/interventions, judge/human agreement and budget/model equivalence '
              'are not established by this report. Identity and observed engine fields remain in JSON.']
    return '\n'.join(lines) + '\n'
