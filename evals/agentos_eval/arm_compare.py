"""Compare recorded arms of versioned text without calling a model."""

import hashlib
import json
import math
import operator
import re
from collections import defaultdict
from itertools import combinations

from .continuity import stats
from .report import judge_provenance, require_same_judge

DECISION_SPLITS = ('dev', 'heldout')
PARTITIONS = (*DECISION_SPLITS, 'owner_store_check')
_DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


def score(record, measure, *, finite=False):
    """Read a recorded measure, treating NaN as a missing grade."""
    value = (record.get('value') or {}).get(measure)
    if not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (math.isnan(value) or finite and not math.isfinite(value)):
        return None
    return value


def mean(values, *, digits=4):
    values = [v for v in values if v is not None]
    if not values:
        return None
    average = sum(values) / len(values)
    return round(average, digits) if digits is not None else average


def records_by_scenario(records):
    rows = defaultdict(list)
    for record in records:
        rows[record.get('scenario')].append(record)
    return rows


def scenario_means(rows, measures, *, digits=4):
    """Aggregate per-run scores with one mean per scenario and measure."""
    return {ident: {measure: mean((row[measure] for row in runs), digits=digits)
                    for measure in measures}
            for ident, runs in rows.items()}


def partition_ids(ids, meta):
    parts = {part: [] for part in PARTITIONS}
    for ident in ids:
        part = 'owner_store_check' if meta[ident].get('source') == 'local' else meta[ident]['split']
        parts[part].append(ident)
    return parts


def paired_deltas(scores, other, parts, measures, *, digits=4):
    """Average the per-scenario differences for scenarios present in both arms."""
    result = {}
    for part, ids in parts.items():
        shared = [i for i in ids if i in scores and i in other]
        result[part] = {
            measure: mean((scores[i][measure] - other[i][measure] for i in shared
                           if None not in (scores[i][measure], other[i][measure])), digits=digits)
            for measure in measures
        } | {'cases': len(shared)}
    return result


def target_improves(parts, target, *, exceeds=operator.gt):
    return all(parts[part][target] is not None and
               exceeds(parts[part][target], parts[part].get('noise', 0))
               for part in DECISION_SPLITS)


def dimension_within_tolerance(parts, measure, tolerance, *, at_most=operator.le):
    return all(parts[part][measure] is not None and at_most(-parts[part][measure], tolerance)
               for part in DECISION_SPLITS)


def text_digest(text):
    """Identify the exact UTF-8 text assigned to an arm."""
    return 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()


def record_refused(record):
    """Return whether a refusal was recorded, or None when observation is incomplete."""
    if 'refused_effects' in record:
        refused = record['refused_effects']
        if isinstance(refused, list):
            return bool(refused)
        if type(refused) is int:
            return refused > 0
        return None
    work = record.get('work')
    if not isinstance(work, dict) or not work:
        return None
    counts = [row.get('refused_effects') if isinstance(row, dict) else None for row in work.values()]
    if any(type(count) is int and count > 0 for count in counts):
        return True
    return False if all(type(count) is int for count in counts) else None


def _finite_number(value):
    return type(value) is int or type(value) is float and math.isfinite(value)


def _valid_cost(cost):
    return isinstance(cost, dict) and bool(cost) and all(_finite_number(v) and v >= 0 for v in cost.values())


def _reported_cost(cost):
    try:
        json.dumps(cost, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return None
    return cost


def _validate_arm(arm, baseline, meta, *, same_text):
    if not isinstance(arm, dict):
        raise ValueError('Each arm must be a mapping of arm fields')
    digest = arm.get('text_digest')
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError('An arm must have a sha256 text digest')
    if (digest == baseline['text_digest']) != same_text:
        raise ValueError('Equal retries must use the baseline text' if same_text
                         else 'A candidate must revise the baseline text')
    settings = arm.get('settings')
    if not isinstance(settings, dict) or any(not isinstance(settings.get(key), str) or not settings[key].strip()
                                             for key in ('worker', 'model')):
        raise ValueError('Arm settings must specify a worker and model')
    if settings != baseline['settings']:
        raise ValueError('Arm settings differ from the baseline')
    ids = arm.get('scenario_ids')
    if (not isinstance(ids, (list, tuple)) or not ids or
            any(not isinstance(i, str) or i not in meta for i in ids) or len(set(ids)) != len(ids)):
        raise ValueError('An arm must declare distinct, known scenario ids')
    if set(ids) != set(baseline['scenario_ids']):
        raise ValueError('Arm scenario ids differ from the baseline')
    for ident in ids:
        if meta[ident].get('source') != 'local' and meta[ident].get('split') not in DECISION_SPLITS:
            raise ValueError(f'Scenario {ident!r} needs a dev or heldout split')
    seen = set()
    declared = set(ids)
    for record in arm['records']:
        ident = record.get('scenario')
        if not isinstance(ident, str) or ident not in declared:
            raise ValueError(f'Record scenario {ident!r} is outside the arm')
        if record.get('worker') != settings['worker']:
            raise ValueError(f'Record worker for {ident!r} differs from arm settings')
        key = (ident, record.get('epoch'))
        if key in seen:
            raise ValueError(f'Duplicate (scenario, epoch) {key!r}')
        seen.add(key)


def _run_scores(record, measures, target, refused):
    values = {measure: score(record, measure, finite=True) for measure in measures}
    if refused is True:
        values[target] = 0.0
    elif refused is None and values[target] is not None and values[target] > 0:
        values[target] = None
    return values


class _TextArm:
    def __init__(self, arm, parts, measures, target, min_trials):
        records = arm['records']
        refusals = [record_refused(record) for record in records]
        self.runs = {ident: {} for ids in parts.values() for ident in ids}
        for record, refused in zip(records, refusals):
            self.runs[record['scenario']][record.get('epoch')] = _run_scores(record, measures, target, refused)
        observed = {ident: runs.values() for ident, runs in self.runs.items() if runs}
        # Keep the precision of the grades through the gate and round the report.
        self.scores = scenario_means(observed, measures, digits=None)
        self.spreads = {part: self._spread(parts[part], target, min_trials) for part in DECISION_SPLITS}
        cost = _reported_cost(arm.get('cost'))
        self.report = {
            'text_digest': arm['text_digest'], 'executions': len(records), 'cost': cost,
            'refused_runs': refusals.count(True),
            'partitions': {
                part: {measure: mean(self.scores[i][measure] for i in ids if i in self.scores)
                       for measure in measures} |
                {'cases': sum(i in observed for i in ids), 'trials': sum(len(self.runs[i]) for i in ids)}
                for part, ids in parts.items()
            },
        }

    def graded(self, ident, measure, min_trials):
        runs = self.runs[ident]
        return len(runs) >= min_trials and all(row[measure] is not None for row in runs.values())

    def _spread(self, ids, target, min_trials):
        if not ids or not all(self.graded(i, target, min_trials) for i in ids):
            return None
        epochs = self.runs[ids[0]].keys()
        if any(self.runs[i].keys() != epochs for i in ids):
            return None
        return stats(mean((self.runs[i][epoch][target] for i in ids), digits=None) for epoch in epochs)['spread']


def _applicable(arms, parts, tolerances):
    return {part: {measure: [i for i in parts[part]
                             if any(arm.scores.get(i, {}).get(measure) is not None for arm in arms)]
                   for measure in tolerances}
            for part in DECISION_SPLITS}


def _evidence_gaps(arm, applicable, min_trials, label):
    reasons = []
    for part, dimensions in applicable.items():
        if arm.spreads[part] is None:
            reasons.append(f'{label} has incomplete target grades or unbalanced epochs on {part}')
        for measure, ids in dimensions.items():
            if not ids:
                reasons.append(f'{measure} has no applicable scenarios on {part}')
            elif any(not arm.graded(i, measure, min_trials) for i in ids):
                reasons.append(f'{label} has incomplete {measure} grades on {part}')
    return reasons


def _comparison(arm, other, parts, measures):
    deltas = paired_deltas(arm.scores, other.scores, parts, measures, digits=None)
    for part, row in deltas.items():
        spreads = (arm.spreads.get(part), other.spreads.get(part))
        row['noise'] = max(spreads) if None not in spreads else None
    return deltas


def _exceeds(value, limit):
    return value > limit and not math.isclose(value, limit, abs_tol=1e-12)


def _worse(parts, target):
    return [part for part in DECISION_SPLITS
            if _exceeds(-parts[part][target], parts[part]['noise'])
            or (parts[part]['noise'] == 0 and not _exceeds(parts[part][target], 0))]


def _verdict(arm, base, retries, against, parts, tolerances, target, min_trials, candidate_runs):
    applicable = _applicable((base, arm), parts, tolerances)
    gaps = (_evidence_gaps(base, applicable, min_trials, 'Baseline') +
            _evidence_gaps(arm, applicable, min_trials, 'Candidate'))
    if gaps:
        return 'insufficient_evidence', gaps
    baseline = against['baseline']
    regressions = [f'{measure} falls beyond its tolerance against baseline'
                   for measure, tolerance in tolerances.items()
                   if not dimension_within_tolerance(baseline, measure, tolerance,
                                                     at_most=lambda loss, limit: not _exceeds(loss, limit))]
    if regressions:
        return 'rejected', regressions

    if retries is None:
        retry_gaps = ['Equal retries are missing']
    else:
        retry_applicable = _applicable((base, arm, retries), parts, tolerances)
        retry_gaps = (_evidence_gaps(retries, retry_applicable, min_trials, 'Equal retries') +
                      _evidence_gaps(arm, retry_applicable, min_trials, 'Candidate'))
    comparable = {'baseline': baseline}
    if not retry_gaps:
        comparable['equal_retries'] = against['equal_retries']
    worse = [f'{target} does not improve against {label} on {part}'
             for label, deltas in comparable.items() for part in _worse(deltas, target)]
    if worse:
        return 'rejected', worse

    gaps = retry_gaps + [f'{target} is within noise against {label}' for label, deltas in comparable.items()
                         if not target_improves(deltas, target, exceeds=_exceeds)]
    if retries is not None and any(len(retries.runs[i]) < count for i, count in candidate_runs.items()):
        gaps.append('Equal retries do not cover the candidate path executions per scenario')
    for label, current in (('Baseline', base), ('Candidate', arm), ('Equal retries', retries)):
        if current is not None and not _valid_cost(current.report['cost']):
            gaps.append(f'{label} has no valid reported cost')
    return ('insufficient_evidence', gaps) if gaps else ('preferred', [])


def compare_versioned_text(baseline, candidates, *, meta, target, tolerances, equal_retries=None, min_trials=3):
    """Report paired deltas and decide each candidate against baseline and equal retries."""
    if not candidates:
        raise ValueError('At least one candidate is required')
    if not tolerances or any(not _finite_number(t) or t < 0 for t in tolerances.values()):
        raise ValueError('At least one finite, non-negative tolerance is required')
    if type(min_trials) is not int or min_trials < 2:
        raise ValueError('At least two trials per scenario are required')
    _validate_arm(baseline, baseline, meta, same_text=True)
    for arm in candidates.values():
        _validate_arm(arm, baseline, meta, same_text=False)
    if equal_retries is not None:
        _validate_arm(equal_retries, baseline, meta, same_text=True)

    measures = tuple(dict.fromkeys((target, *tolerances)))
    if any(measure.startswith('rubric_') for measure in measures):
        arms = [baseline, *candidates.values()]
        if equal_retries is not None:
            arms.append(equal_retries)
        judges = [judge_provenance(arm['records'], {'judge': arm['settings'].get('judge')}) for arm in arms]
        for first, second in combinations(judges, 2):
            require_same_judge(first, second)

    ids = sorted(baseline['scenario_ids'])
    parts = partition_ids(ids, meta)
    base = _TextArm(baseline, parts, measures, target, min_trials)
    retries = _TextArm(equal_retries, parts, measures, target, min_trials) if equal_retries is not None else None
    texts = {label: _TextArm(arm, parts, measures, target, min_trials) for label, arm in candidates.items()}
    candidate_runs = {i: sum(len(arm.runs[i]) for arm in texts.values())
                      for part in DECISION_SPLITS for i in parts[part]}
    rows = {}
    for label, arm in texts.items():
        against = {'baseline': _comparison(arm, base, parts, measures)}
        if retries is not None:
            against['equal_retries'] = _comparison(arm, retries, parts, measures)
        verdict, reasons = _verdict(arm, base, retries, against, parts, tolerances, target,
                                    min_trials, candidate_runs)
        for deltas in against.values():
            for row in deltas.values():
                for measure in measures:
                    if row[measure] is not None:
                        row[measure] = round(row[measure], 4)
        rows[label] = {**arm.report, 'verdict': verdict, 'preferred': verdict == 'preferred',
                       'reasons': reasons, 'against': against}
    return {'schema': 'arm-compare/1', 'target': target, 'tolerances': dict(tolerances), 'min_trials': min_trials,
            'settings': dict(baseline['settings']), 'scenario_ids': ids,
            'candidate_path_executions': sum(row['executions'] for row in rows.values()),
            'baseline': base.report, 'equal_retries': retries.report if retries is not None else None,
            'candidates': rows}
