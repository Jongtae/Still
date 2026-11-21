"""Model-free comparison of progress-update forms across domains and reader profiles (#1036).

Reuse: Adopt the #943 communication scenarios and rubric format (``scenarios.py``) and the
per-run records ``report.records_from_samples`` already writes (``scenario``, ``source``,
``split``, ``value['rubric_<dimension>']``). This module only groups and pairs those records;
it calls no model. Each arm is one run of the same scenarios under one set of reporting rules
(the current rules, or a candidate default form derived from external guidance such as BLUF /
SITREP order or exception-based reporting).

Cases from the owner's own store (``source == 'local'``) are a check, never the target: they are
summarized apart and never enter the dev or held-out means a form is chosen on.
"""
import json
import math
from collections import defaultdict
from pathlib import Path

from .scenarios import load

DIMENSIONS = ('progress_update', 'reader_form', 'honest_unverified')
#: A candidate must not lose this much on the no-false-claim dimension to be preferred.
HONESTY_FLOOR = 0.05
_AXES = {'domain': 'domain-', 'variant': 'variant-', 'reader': 'reader-'}


def progress_scenarios(**options):
    """Progress-update scenarios keyed by id, with their domain, variant and reader axes."""
    found = {}
    for scenario in load(**options):
        if 'progress-update' not in scenario['tags']:
            continue
        axes = {axis: next((t[len(prefix):] for t in scenario['tags'] if t.startswith(prefix)), None)
                for axis, prefix in _AXES.items()}
        found[scenario['id']] = {'split': scenario['split'], 'source': scenario.get('source', 'bundled'), **axes}
    return found


def _score(record, dimension):
    value = (record.get('value') or {}).get('rubric_' + dimension)
    return value if isinstance(value, (int, float)) and not math.isnan(value) else None


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def _by_scenario(records):
    rows = defaultdict(list)
    for record in records:
        rows[record.get('scenario')].append(record)
    return {ident: {dim: _mean(_score(r, dim) for r in rs) for dim in DIMENSIONS} for ident, rs in rows.items()}


def _group(ids, scores, meta, axis):
    groups = defaultdict(list)
    for ident in ids:
        groups[meta[ident][axis]].append(scores[ident])
    return {key: {dim: _mean(row[dim] for row in rows) for dim in DIMENSIONS} | {'cases': len(rows)}
            for key, rows in sorted(groups.items(), key=lambda item: str(item[0]))}


def summarize(records, meta):
    """One arm: means by partition (dev, heldout, owner-store check) and by domain, variant, reader."""
    scores = _by_scenario(records)
    known = {ident: s for ident, s in scores.items() if ident in meta}
    parts = {'dev': [], 'heldout': [], 'owner_store_check': []}
    for ident in known:
        parts['owner_store_check' if meta[ident]['source'] == 'local' else meta[ident]['split']].append(ident)
    return {'cases': len(known), 'unknown_scenarios': sorted(set(scores) - set(meta)),
            'partitions': {name: {dim: _mean(known[i][dim] for i in ids) for dim in DIMENSIONS} | {'cases': len(ids)}
                           for name, ids in parts.items()},
            'by': {axis: _group([i for i in parts['dev'] + parts['heldout']], known, meta, axis) for axis in _AXES},
            '_scores': known, '_parts': parts}


def compare_arms(arms, meta, *, baseline):
    """Paired comparison of every arm against ``baseline`` on the scenarios both ran.

    A candidate is ``preferred`` only when its dev and held-out progress-update means both improve,
    no reader profile gets worse, and the no-false-claim dimension stays within ``HONESTY_FLOOR``.
    The owner-store check is reported beside that decision and never decides it.
    """
    if baseline not in arms:
        raise ValueError('The baseline arm is missing')
    summaries = {label: summarize(records, meta) for label, records in arms.items()}
    base = summaries[baseline]
    result = {'baseline': baseline, 'arms': {}}
    for label, summary in summaries.items():
        row = {'cases': summary['cases'], 'unknown_scenarios': summary['unknown_scenarios'],
               'partitions': summary['partitions'], 'by': summary['by']}
        if label != baseline:
            shared = set(summary['_scores']) & set(base['_scores'])
            deltas = {}
            for name, key in (('dev', 'dev'), ('heldout', 'heldout'), ('owner_store_check', 'owner_store_check')):
                ids = [i for i in summary['_parts'][key] if i in shared]
                deltas[name] = {dim: _mean(summary['_scores'][i][dim] - base['_scores'][i][dim]
                                           for i in ids if None not in (summary['_scores'][i][dim], base['_scores'][i][dim]))
                                for dim in DIMENSIONS} | {'cases': len(ids)}
            readers = {}
            for reader in sorted({meta[i]['reader'] for i in shared if meta[i]['source'] != 'local'}, key=str):
                ids = [i for i in shared if meta[i]['reader'] == reader and meta[i]['source'] != 'local']
                readers[reader] = _mean(summary['_scores'][i]['progress_update'] - base['_scores'][i]['progress_update']
                                        for i in ids if None not in (summary['_scores'][i]['progress_update'],
                                                                     base['_scores'][i]['progress_update']))
            positive = lambda value: value is not None and value > 0
            row['paired_delta'] = deltas
            row['reader_delta'] = readers
            row['preferred'] = (positive(deltas['dev']['progress_update']) and positive(deltas['heldout']['progress_update'])
                                and all(v is not None and v >= 0 for v in readers.values()) and bool(readers)
                                and all((deltas[part]['honest_unverified'] or 0) >= -HONESTY_FLOOR
                                        for part in ('dev', 'heldout')))
        result['arms'][label] = row
    return result


def render_markdown(result):
    lines = [f'# Progress-update forms (baseline: {result["baseline"]})', '']
    for label, row in result['arms'].items():
        lines.append(f'## {label}: {row["cases"]} cases')
        for name, part in row['partitions'].items():
            lines.append(f'- {name}: ' + ', '.join(f'{k} {v}' for k, v in part.items()))
        if 'paired_delta' in row:
            lines.append(f'- preferred over {result["baseline"]}: {row["preferred"]}')
            for name, part in row['paired_delta'].items():
                lines.append(f'- delta {name}: ' + ', '.join(f'{k} {v}' for k, v in part.items()))
            lines.append('- delta by reader profile: ' + ', '.join(f'{k} {v}' for k, v in row['reader_delta'].items()))
        lines.append('')
    return '\n'.join(lines)


def run(baseline, arm_files, *, scenarios_kwargs=None):
    """Load ``label=records.json`` files (lists of records) and compare them."""
    meta = progress_scenarios(**(scenarios_kwargs or {}))
    arms = {}
    for label, path in arm_files.items():
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        arms[label] = data['records'] if isinstance(data, dict) else data
    return compare_arms(arms, meta, baseline=baseline), meta
