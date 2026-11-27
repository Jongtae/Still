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
from collections import defaultdict
from pathlib import Path

from .arm_compare import (dimension_within_tolerance, mean, paired_deltas, partition_ids, records_by_scenario,
                          scenario_means, score, target_improves)
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


def _group(ids, scores, meta, axis):
    groups = defaultdict(list)
    for ident in ids:
        groups[meta[ident][axis]].append(scores[ident])
    return {key: {dim: mean(row[dim] for row in rows) for dim in DIMENSIONS} | {'cases': len(rows)}
            for key, rows in sorted(groups.items(), key=lambda item: str(item[0]))}


def summarize(records, meta):
    """One arm: means by partition (dev, heldout, owner-store check) and by domain, variant, reader."""
    rows = {ident: [{dim: score(record, 'rubric_' + dim) for dim in DIMENSIONS} for record in runs]
            for ident, runs in records_by_scenario(records).items()}
    scores = scenario_means(rows, DIMENSIONS)
    known = {ident: s for ident, s in scores.items() if ident in meta}
    parts = partition_ids(known, meta)
    return {'cases': len(known), 'unknown_scenarios': sorted(set(scores) - set(meta)),
            'partitions': {name: {dim: mean(known[i][dim] for i in ids) for dim in DIMENSIONS} | {'cases': len(ids)}
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
            deltas = paired_deltas(summary['_scores'], base['_scores'], summary['_parts'], DIMENSIONS)
            readers = {}
            for reader in sorted({meta[i]['reader'] for i in shared if meta[i]['source'] != 'local'}, key=str):
                ids = [i for i in shared if meta[i]['reader'] == reader and meta[i]['source'] != 'local']
                readers[reader] = mean(summary['_scores'][i]['progress_update'] - base['_scores'][i]['progress_update']
                                        for i in ids if None not in (summary['_scores'][i]['progress_update'],
                                                                     base['_scores'][i]['progress_update']))
            row['paired_delta'] = deltas
            row['reader_delta'] = readers
            row['preferred'] = (target_improves(deltas, 'progress_update')
                                and all(v is not None and v >= 0 for v in readers.values()) and bool(readers)
                                and dimension_within_tolerance(deltas, 'honest_unverified', HONESTY_FLOOR))
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
        if str(path).endswith('.eval'):  # an Inspect sweep log (``sweep --arm <label>``)
            from .report import records_from_samples
            from .task import read_eval_log
            arms[label] = records_from_samples(read_eval_log(str(path)).samples)
            continue
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        arms[label] = data['records'] if isinstance(data, dict) else data
    return compare_arms(arms, meta, baseline=baseline), meta
