"""C2 measurement and paired reporting, standard library only (#853).

Existing solutions review (offline): Adapt the pinned MIT Inspect AI task/log
API already selected in requirements.txt; official model transports remain
Inspect's responsibility. Adopt stdlib argparse/JSON/hashlib/statistics/pathlib
for CLI, provenance and descriptive statistics. No statistical/eval framework
can supply this experiment's explicit observability or held-out decision rule.
Reuse scoring._transcript, scoring.judge_call, JudgeBudget, Redactor.for_seed,
cli.cmd_sweep, report.records_from_samples and recall_pairs.paired_index (the
strict pairing used by recall_pairs.compare, following judgments.compare).
Build only the C2 aggregation/decision rule unsupported by the sweep trend.
No dependency/version change, network authority, owner state or production
code; AGPL-3.0-only compatibility and existing Python platforms are unchanged.
External maintenance/release activity cannot be checked in this offline task.
"""
import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean, median

from .paths import BUNDLED_SCENARIOS, REPO
from .recall_pairs import paired_index
from .scoring import MEASURES, MEASURE_VALUES

DEFAULT_RUBRIC = REPO / 'evals/continuity/initiative-rubric-v1.json'
SCENARIOS = BUNDLED_SCENARIOS / 'continuity-experiments.json'
EVIDENCE = 'live local observation of the sandbox, judged'
JUDGED = ('avoidable_interventions', 'unnecessary_interruptions', 'proactive_usefulness')
HELDOUT_RULE = ('Held-out cases are not tuned against; a change motivated by a held-out failure '
                'moves that case to dev and needs a new held-out case.')


def load_rubric(path):
    rubric = json.loads(Path(path).read_text(encoding='utf-8'))
    if (not isinstance(rubric, dict) or not isinstance(rubric.get('version'), str)
            or set(rubric.get('measures', {})) != set(MEASURES)
            or not isinstance(rubric.get('messages'), str)
            or not isinstance(rubric.get('judge_instructions'), str)
            or set(rubric.get('output_schema', {})) != set(MEASURES)):
        raise ValueError('Invalid initiative rubric')
    rule = rubric.get('agreement', {})
    if (set(rule) != {'window', 'max_disagreements'} or type(rule['window']) is not int or rule['window'] < 1
            or type(rule['max_disagreements']) is not int or rule['max_disagreements'] < 0):
        raise ValueError('Invalid rubric agreement rule')
    return rubric


def fixture_support(scenarios, rubric_path, scenario_path=SCENARIOS):
    rubric = load_rubric(rubric_path)
    return {'scenario_file': str(scenario_path),
            'scenario_digest': hashlib.sha256(Path(scenario_path).read_bytes()).hexdigest(),
            'rubric_file': str(rubric_path), 'rubric_version': rubric['version'],
            'rubric_digest': hashlib.sha256(Path(rubric_path).read_bytes()).hexdigest(),
            'scenario_ids': {split: sorted(s['id'] for s in scenarios if s['split'] == split)
                             for split in ('dev', 'heldout')},
            'rubric': rubric, 'evidence_class': 'synthetic fixture support; not live evidence'}


def stats(values):
    numbers = [v for v in values if type(v) in (int, float) and math.isfinite(v)]
    return {'n': len(numbers), 'mean': mean(numbers) if numbers else None,
            'min': min(numbers) if numbers else None, 'max': max(numbers) if numbers else None,
            'spread': max(numbers) - min(numbers) if numbers else None}


def _clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _key(row):
    return row['run_id'], row['scenario'], row['worker'], row['epoch']


def _label_values(row):
    m = row.get('measures')
    if row.get('measures_status') != 'judged' or not m:
        return {key: None for key in MEASURES}
    return {'avoidable_interventions': sorted(r['turn'] for r in m['avoidable_interventions']),
            'required_approvals': sorted(r['turn'] for r in m['required_approvals']),
            'unnecessary_interruptions': sorted(r['message'] for r in m['unnecessary_interruptions']),
            'proactive_usefulness': m['proactive_usefulness']['score']
            if m['proactive_usefulness']['score'] is not None else 'not_observed'}


def agreement(report, labels):
    """Strict sample identity validation; sliding windows retain label-file order."""
    if isinstance(labels, (str, Path)):
        labels = json.loads(Path(labels).read_text(encoding='utf-8'))
    version = report['fixture_support']['rubric_version']
    if (not isinstance(labels, dict) or not {'version', 'rubric', 'labels'} <= set(labels)
            or set(labels) - {'version', 'rubric', 'labels', '_note'}
            or labels['version'] != 'initiative-labels-v1' or labels['rubric'] != version
            or not isinstance(labels['labels'], list)):
        raise ValueError('Invalid labels file or different rubric version')
    samples = paired_index(report['samples'], _key)
    rows, seen = {m: [] for m in MEASURES}, set()
    for label in labels['labels']:
        if (not isinstance(label, dict) or set(label) != {'run_id', 'scenario', 'worker', 'epoch', 'labeller', 'measures'}
                or any(not isinstance(label[k], str) or not label[k] for k in ('run_id', 'scenario', 'worker', 'labeller'))
                or label['labeller'] not in ('owner', 'contributor') or type(label['epoch']) is not int
                or not isinstance(label['measures'], dict) or set(label['measures']) != set(MEASURES)):
            raise ValueError('Malformed human label')
        key = _key(label)
        if key in seen:
            raise ValueError('Duplicate human label')
        seen.add(key)
        if key not in samples:
            raise ValueError(f'Label sample does not exist: {key}')
        sample = samples[key]
        judged = _label_values(sample)
        for measure, human in label['measures'].items():
            if measure == 'proactive_usefulness':
                if human != 'not_observed' and (type(human) is not int or human not in (0, 1, 2)):
                    raise ValueError('Invalid proactive human label')
            else:
                if not isinstance(human, list):
                    raise ValueError('Human list label required (null templates must be filled)')
                allowed = sample.get('message_ids', []) if measure == 'unnecessary_interruptions' else range(1, sample['turn_count'] + 1)
                if (any((not isinstance(v, str) if measure == 'unnecessary_interruptions' else type(v) is not int)
                       or v not in allowed for v in human) or (measure != 'avoidable_interventions' and len(set(human)) != len(human))
                        or (measure == 'avoidable_interventions' and any(human.count(v) > 2 for v in human))):
                    raise ValueError('Unknown/duplicate human turn or message')
                human = sorted(human)
            rows[measure].append({'scenario': key[1], 'epoch': key[3], 'run_id': key[0], 'worker': key[2],
                                  'judge': judged[measure], 'human': human,
                                  'agree': judged[measure] == human})
    rule = report['fixture_support']['rubric']['agreement']
    window, maximum = rule['window'], rule['max_disagreements']
    result = {}
    for measure, items in rows.items():
        flags = [int(not r['agree']) for r in items]
        uncertain = any(sum(flags[i:i + window]) > maximum for i in range(max(1, len(flags) - window + 1)))
        result[measure] = {'labelled': len(items), 'agreed': sum(r['agree'] for r in items),
                           'uncertain': uncertain, 'rows': items,
                           'disagreements': [r for r in items if not r['agree']]}
    return result


def _measure_value(row, name):
    if row.get('measures_status') != 'judged':
        return None
    if name == 'proactive_usefulness' and not row.get('proactive_observation', {}).get('observed', False):
        return 'not_observed'
    return row.get('value', {}).get('measure_' + name)


def _group(rows):
    checks = sorted({name for row in rows for name in row.get('checks', {})})
    dims = sorted({k for row in rows for k in row.get('value', {}) if k.startswith('rubric_') and k != 'rubric_mean'})
    measures = {}
    for name in MEASURE_VALUES:
        epochs = [{'scenario': row['scenario'], 'epoch': row['epoch'], 'value': _measure_value(row, name)} for row in rows]
        summary = stats([r['value'] for r in epochs])
        summary.update(epochs=epochs, not_scored=sum(r['value'] is None for r in epochs), not_observed=sum(r['value'] == 'not_observed' for r in epochs),
                       total=sum(r['value'] for r in epochs if type(r['value']) in (int, float)
                                 and math.isfinite(r['value'])) if summary['n'] else None)
        measures[name] = summary
    latency = {}
    for i in sorted({t['turn'] for row in rows for t in row.get('latency', [])}):
        epochs = [{'scenario': r['scenario'], 'epoch': r['epoch'], **t} for r in rows for t in r.get('latency', []) if t['turn'] == i]
        values = [t['elapsed'] for t in epochs if type(t.get('elapsed')) in (int, float)]
        latency[str(i)] = {**stats(values), 'median': median(values) if values else None, 'epochs': epochs}
    return {'checks': {name: {**stats([int(bool(r.get('checks', {}).get(name))) for r in rows]),
                             'pass_rate': mean(bool(r.get('checks', {}).get(name)) for r in rows)} for name in checks},
            'measures': measures, 'rubric': {k[7:]: stats([r.get('value', {}).get(k) for r in rows]) for k in dims},
            'latency': latency}


def build_report(records, run, fixture, labels=None):
    """Pure aggregation over records_from_samples plus frozen pre-run provenance."""
    if run.get('seed') != 'fresh':
        raise ValueError('C2 requires a fresh data folder')
    samples = []
    definitions = {s: split for split, ids in fixture['scenario_ids'].items() for s in ids}
    for record in records:
        if record['scenario'] not in definitions or record.get('split') != definitions[record['scenario']]:
            raise ValueError('Record scenario/split differs from C2 selection')
        if record.get('worker') != run['worker'] or type(record.get('epoch')) is not int or not 1 <= record['epoch'] <= run['epochs']:
            raise ValueError('Record worker/epoch differs from C2 run')
        for field, expected in (('measures_rubric_version', fixture['rubric_version']),
                                ('measures_rubric_digest', fixture['rubric_digest'])):
            if record.get(field) is not None and record[field] != expected:
                raise ValueError('Record rubric differs from C2 fixture')
        samples.append(_clean({**record, 'run_id': run['id']}))
    paired_index(samples, _key)
    splits = {}
    for split, ids in fixture['scenario_ids'].items():
        rows = [r for r in samples if r['split'] == split]
        splits[split] = {'scenarios': {ident: _group([r for r in rows if r['scenario'] == ident]) for ident in ids},
                         'totals': _group(rows)}
    cost = {}
    for metric in ('input_tokens', 'output_tokens', 'usd'):
        values = [call.get(metric, 'unavailable') for row in samples for call in row.get('judge_usage', {}).values() if call]
        cost[metric] = sum(values) if all(type(v) in (float, int) for v in values) else 'unavailable'
    report = {'version': 'continuity-report-v1', 'fixture_support': copy.deepcopy(fixture),
              'live_evidence': {**{k: v for k, v in run.items() if k != 'continuity'}, 'seed': 'fresh', 'evidence_class': EVIDENCE, 'splits': splits},
              'samples': samples,
              'uncertainty': {'status_counts': dict(Counter(r.get('measures_status', 'not_scored') for r in samples)),
                              'agreement': {},
                              'missing_samples': len(definitions) * run['epochs'] - len(samples),
                              'epoch_spreads': {split: {ident: {m: v['spread'] for m, v in g['measures'].items()}
                                                       for ident, g in block['scenarios'].items()}
                                                for split, block in splits.items()}},
              'unsupported_paths': {'proactive_not_observed': sum(not r.get('proactive_observation', {}).get('observed', False)
                                                                 for r in samples),
                                    'limitations': [
                                        'Runs/reports that do not come due are unobserved; promises alone provide no delivery evidence.',
                                        'New preparation rows may carry last_work_id, last_delivery, last_status and a 280-character last_result excerpt. Only a typed line_account row with a sent reply on a distinct Work establishes unsolicited delivery here.',
                                        'Generic preparation receipts lack scheduled-versus-run-now origin and delivery time; before_answer alone also proves no unsolicited delivery. Existing-row updates, separate notifications and full report bodies are not retained.',
                                        'Tool events are capped at 40 per turn; approval codes in trace payloads are stripped by the public task endpoint. Counts are observed requests only.',
                                        'Conversation only; no multi-day recall, transcript retrieval or AI-stood lines acceptance.',
                                        'Unknown judge usage retains the budget reservation; USD is the settled charge, not always observed provider usage.'],
                                    'cost': {'judge': cost, 'worker_usage': 'unavailable',
                                             'worker_reason': 'subscription CLI'}}}
    if labels is not None:
        report['uncertainty']['agreement'] = agreement(report, labels)
    return report


def _cell(value):
    if value is None:
        return 'unscored'
    if isinstance(value, float):
        return f'{value:.6g}'
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    return str(value).replace('|', '\\|').replace('\n', ' ')


def _measure_table(measures):
    lines = ['| measure | mean | min | max | spread | total | not observed | unscored |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for name, row in measures.items():
        lines.append('| ' + ' | '.join([name] + [_cell(row[k]) for k in
                     ('mean', 'min', 'max', 'spread', 'total', 'not_observed', 'not_scored')]) + ' |')
    return lines + ['']


def render_markdown(report):
    run, fixture = report['live_evidence'], report['fixture_support']
    lines = [f"# C2 continuity {run['id']}", '', '## Fixture support', '',
             'Synthetic fixture support is not live evidence.', '',
             f"- Scenario file: {_cell(fixture.get('scenario_file', fixture.get('scenario_files')))}",
             f"- Scenario SHA-256: {fixture['scenario_digest']}",
             f"- Rubric: {fixture['rubric_version']} ({_cell(fixture.get('rubric_file'))})",
             f"- Rubric SHA-256: {fixture['rubric_digest']}",
             f"- Scenario ids per split: {_cell(fixture['scenario_ids'])}", '', '## Live evidence', '',
             f"Evidence class: {run['evidence_class']}", '',
             '| revision | worker | judge model | epochs | seed | run id |',
             '| --- | --- | --- | --- | --- | --- |',
             '| ' + ' | '.join(_cell(run.get(k)) for k in ('git_head', 'worker', 'judge', 'epochs', 'seed', 'id')) + ' |', '']
    if run.get('stopped'):
        lines += [f"**STOPPED: {_cell(run['stopped'])}; incomplete sweep, cannot be compared.**", '']
    for split, block in run['splits'].items():
        lines += [f'### {split}', '', 'Split totals (never pooled with the other split):', '']
        lines += _measure_table(block['totals']['measures'])
        for ident, group in block['scenarios'].items():
            lines += [f'#### {ident}', '', 'Deterministic checks:', '',
                      '| check | pass rate | n | spread |', '| --- | --- | --- | --- |']
            for check, row in group['checks'].items():
                lines.append(f"| {check} | {_cell(row['pass_rate'])} | {row['n']} | {_cell(row['spread'])} |")
            lines += ['', 'Judged measures (proactive `not_observed` means not observed, never zero):', '']
            lines += _measure_table(group['measures'])
            lines += ['| epoch | status | avoidable items (turn, kind, reason) | approvals: events | approvals: judged | interruptions (message id, reason) | proactive usefulness | judge inconsistencies |',
                      '| --- | --- | --- | --- | --- | --- | --- | --- |']
            for row in report['samples']:
                if row['scenario'] != ident or row['split'] != split:
                    continue
                m = row.get('measures') or {}
                values = [row['epoch'], row.get('measures_status', 'not_scored'), m.get('avoidable_interventions'),
                          row.get('required_approvals_events'), m.get('required_approvals'),
                          m.get('unnecessary_interruptions'), _measure_value(row, 'proactive_usefulness'),
                          m.get('inconsistencies')]
                lines.append('| ' + ' | '.join(map(_cell, values)) + ' |')
            lines += ['', 'Rubric dimension means: ' + _cell({d: row['mean'] for d, row in group['rubric'].items()}), '',
                      '| turn | median seconds | min | max | per-epoch sent_at / elapsed |',
                      '| --- | --- | --- | --- | --- |']
            for turn, row in group['latency'].items():
                lines.append('| ' + ' | '.join(map(_cell, [turn, row['median'], row['min'], row['max'], row['epochs']])) + ' |')
            lines.append('')
    uncertainty = report['uncertainty']
    lines += ['## Uncertainty', '', f"- Measures status counts: {_cell(uncertainty['status_counts'])}",
              f"- Missing samples: {uncertainty['missing_samples']}", '',
              'Epoch spread per scenario/measure appears above; gains within either arm’s spread are uncertain.', '',
              '| measure | labelled | agreed | uncertain |', '| --- | --- | --- | --- |']
    for measure, row in uncertainty['agreement'].items():
        lines.append(f"| {measure} | {row['labelled']} | {row['agreed']} | {row['uncertain']} |")
    lines += ['', '| measure | scenario | epoch | run | judge | human |', '| --- | --- | --- | --- | --- | --- |']
    for measure, group in uncertainty['agreement'].items():
        for row in group['disagreements']:
            lines.append('| ' + ' | '.join(map(_cell, [measure, row['scenario'], row['epoch'], row['run_id'], row['judge'], row['human']])) + ' |')
    if not uncertainty['agreement']:
        lines += ['', 'No human labels supplied; agreement is unmeasured.']
    unsupported = report['unsupported_paths']
    lines += ['', '## Unsupported paths', '', f"Proactive usefulness not observed: {unsupported['proactive_not_observed']} samples.", '']
    lines += ['- ' + item for item in unsupported['limitations']]
    lines += ['', f"Judge cost (both calls, settled): {_cell(unsupported['cost']['judge'])}",
              'Worker usage: **unavailable (subscription CLI)**.', '', HELDOUT_RULE, '']
    return '\n'.join(lines)


def write_report(report, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    base = folder / (report['live_evidence']['id'] + '.c2')
    jp, mp = Path(str(base) + '.json'), Path(str(base) + '.md')
    jp.touch(mode=0o600, exist_ok=True)
    mp.touch(mode=0o600, exist_ok=True)
    jp.chmod(0o600)
    mp.chmod(0o600)
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    mp.write_text(render_markdown(report), encoding='utf-8')
    return jp, mp


def labels_template(report, extra_ids=()):
    wanted = set(extra_ids)
    if wanted - {r['scenario'] for r in report['samples']}:
        raise ValueError('Extra scenario ids must exist in the report')
    return {'version': 'initiative-labels-v1', 'rubric': report['fixture_support']['rubric_version'],
            'labels': [{**{k: r[k] for k in ('run_id', 'scenario', 'worker', 'epoch')}, 'labeller': 'owner',
                        'measures': {m: None for m in MEASURES}}
                       for r in report['samples'] if r['split'] == 'heldout' or r['scenario'] in wanted]}


def _change(a, b, direction, uncertain=False):
    result = {'baseline': a, 'candidate': b, 'direction': direction}
    if a.get('not_observed') or b.get('not_observed'):
        return {**result, 'change': 'not_observed'}
    if direction is None:
        return {**result, 'change': 'reported_only'}
    if a['mean'] is None or b['mean'] is None:
        return {**result, 'change': 'uncertain'}
    delta = b['mean'] - a['mean']
    change = ('uncertain' if uncertain else 'unchanged' if delta == 0 else
              'uncertain' if abs(delta) <= max(a['spread'], b['spread']) else
              'improved' if delta * direction > 0 else 'regressed')
    return {**result, 'delta': delta, 'change': change}


def compare(baseline, candidate):
    """Strict paired identities from recall_pairs.compare, never a pooled trend."""
    for report in (baseline, candidate):
        if report['live_evidence'].get('stopped'):
            raise ValueError('Cannot compare a stopped C2 report')
    af, bf = baseline['fixture_support'], candidate['fixture_support']
    for key in ('scenario_digest', 'rubric_version', 'rubric_digest', 'scenario_ids'):
        if af[key] != bf[key]:
            raise ValueError(f'Cannot compare different {key}')
    ar, br = baseline['live_evidence'], candidate['live_evidence']
    for key in ('worker', 'epochs', 'judge'):
        if ar[key] != br[key]:
            raise ValueError(f'Cannot compare different {key}')
    pair_key = lambda r: (r['scenario'], r['worker'], r['epoch'])
    arows, brows = paired_index(baseline['samples'], pair_key), paired_index(candidate['samples'], pair_key)
    if arows.keys() != brows.keys():
        raise ValueError('Paired scenario/epoch selections differ')
    uncertain = {m for r in (baseline, candidate) for m, v in r['uncertainty']['agreement'].items() if v['uncertain']}
    result = {'baseline_revision': ar.get('git_head'), 'candidate_revision': br.get('git_head'),
              'revision_warning': 'Revisions should differ' if ar.get('git_head') == br.get('git_head') else None,
              'splits': {}, 'uncertain_measures': sorted(uncertain), 'heldout_rule': HELDOUT_RULE}
    check_regression, held_regression = False, False
    gains = {'dev': False, 'heldout': False}
    for split in ('dev', 'heldout'):
        changes = {}
        for ident in af['scenario_ids'][split]:
            a, b = ar['splits'][split]['scenarios'][ident], br['splits'][split]['scenarios'][ident]
            metrics = {}
            for name in MEASURE_VALUES:
                label_name = 'required_approvals' if name.startswith('required_approvals') else name
                direction = None if name.startswith('required_approvals') else 1 if name == 'proactive_usefulness' else -1
                change = _change(a['measures'][name], b['measures'][name], direction, label_name in uncertain)
                metrics[name] = change
                if name in JUDGED:
                    gains[split] |= change['change'] == 'improved'
                    if split == 'heldout' and direction is not None:
                        # Raw regression veto also applies inside noise or a disputed grade.
                        av, bv = a['measures'][name]['mean'], b['measures'][name]['mean']
                        held_regression |= av is not None and bv is not None and (bv - av) * direction < 0
            if a['checks'].keys() != b['checks'].keys():
                raise ValueError('Paired deterministic check selections differ')
            for name in a['checks']:
                av, bv = a['checks'][name]['pass_rate'], b['checks'][name]['pass_rate']
                metrics['check_' + name] = _change({**a['checks'][name], 'mean': av}, {**b['checks'][name], 'mean': bv}, 1)
                check_regression |= bv < av
            changes[ident] = metrics
        result['splits'][split] = changes
    held_means = {}
    for name in JUDGED:
        a = ar['splits']['heldout']['totals']['measures'][name]
        b = br['splits']['heldout']['totals']['measures'][name]
        held_means[name] = _change(a, b, 1 if name == 'proactive_usefulness' else -1, name in uncertain)
    result['heldout_means'] = held_means
    means_ok = all(row['change'] == 'not_observed' or
                   (row.get('delta') is not None and row['delta'] * row['direction'] >= 0)
                   for row in held_means.values())
    expected = {(s, ar['worker'], e) for ids in af['scenario_ids'].values() for s in ids for e in range(1, ar['epochs'] + 1)}
    if arows.keys() != expected:
        raise ValueError('Incomplete paired scenario/epoch selection')
    complete = all(r.get('measures_status') == 'judged' for r in [*arows.values(), *brows.values()])
    conditions = {'complete_judged_pairs': complete, 'no_check_regression': not check_regression,
                  'dev_judged_gain': gains['dev'], 'no_heldout_regression': not held_regression,
                  'heldout_means_not_worse': means_ok, 'certain_heldout_gain': gains['heldout']}
    result['failed_conditions'] = [k for k, ok in conditions.items() if not ok]
    result['verdict'] = 'not better' if result['failed_conditions'] else 'better'
    return result


def compare_markdown(result):
    return '# C2 paired comparison\n\n' + result['heldout_rule'] + '\n\n```json\n' + json.dumps(result, indent=2) + '\n```\n'
