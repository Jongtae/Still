"""Create a reviewable promotion/rollback receipt; never execute its commands."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from string import Template

from jsonschema import Draft202012Validator

from .paths import REPO

VERSION = 'promotion-record/1'
DIGEST_UNAVAILABLE = "#825 per-attempt digests are recorded on Work turns, not on judgment calls."


def _decision(record):
    heldout = record['heldout']
    conditions = record['conditions']
    if (not record['comparable'] or conditions['differences'] or conditions['unknown']
            or heldout['verdict'] == 'incomparable'):
        return 'inconclusive', 'Conditions are unequal or unknown; no comparative claim is supported.'
    if heldout['verdict'] == 'worse':
        return 'roll-back', 'The paired held-out verdict reports a certain regression.'
    if heldout['verdict'] == 'better':
        return 'promote', 'The paired held-out verdict establishes a certain fix, no certain regression and no lower mean rate.'
    return 'inconclusive', 'The paired held-out verdict does not establish a gain.'


def _prompt_changes(paired):
    digests = paired.get('prompt_digests', {})
    a, b = digests.get('baseline'), digests.get('candidate')
    if not isinstance(a, dict) or not isinstance(b, dict):
        return {'baseline': None, 'candidate': None}
    changed = sorted(key for key in set(a) | set(b) if a.get(key) != b.get(key))
    return {'baseline': {key: a.get(key) for key in changed},
            'candidate': {key: b.get(key) for key in changed}}


def identity_from_pair(paired):
    """Candidate execution identity and both resolved revisions, copied from evidence."""
    conditions = paired['conditions']['candidate']
    revisions = paired.get('revisions', {})
    return dict(base_sha=revisions.get('baseline'), head_sha=revisions.get('candidate'),
                model_id=conditions.get('pinned_model', conditions.get('runtime.requested_model')),
                route=conditions.get('runtime.transport'), effort=conditions.get('runtime.effort'),
                cli_version=conditions.get('runtime.cli_version'))


def validate_record(record, *, root=REPO):
    """Validate Draft 2020-12 shape and cross-field receipt invariants; return record."""
    schema = json.loads((Path(root) / 'evals/schemas/promotion-record-v1.json').read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(record)
    # JSON Schema numbers alone also accept Python NaN; receipts must be portable JSON.
    json.dumps(record, allow_nan=False)
    if record['budget']['actual'] != record['cost']['calls']:
        raise ValueError('Budget actual must equal observed calls')
    if record['budget']['exceeded'] != (record['budget']['actual'] > record['budget']['calls_cap']):
        raise ValueError('Incorrect budget exceeded flag')
    if record['decision'] != _decision(record)[0]:
        raise ValueError('Decision contradicts evidence')
    seen = set()
    for case in record['cases']:
        key = (case.get('set'), case['id'])
        if key in seen:
            raise ValueError('Duplicate paired case')
        seen.add(key)
        for arm in ('baseline', 'candidate'):
            if case[arm]['passed'] > case[arm]['trials']:
                raise ValueError('Passed count exceeds trials')
    heldout_ids = {case['id'] for case in record['cases'] if case['split'] == 'heldout'}
    for key in ('fixed', 'regressed'):
        if set(record['heldout'][key]) - heldout_ids:
            raise ValueError('Held-out verdict references an unobserved case')
    rollback = record['rollback']
    if rollback['kind'] == 'git-revert' and rollback['command'] != 'git revert ' + record['change_identity']['head_sha']:
        raise ValueError('git-revert must name the exact candidate SHA; use setting for a prior route/model')
    return record


def build_record(selection, paired, change_identity, *, budget, evidence_class, rollback, root=REPO):
    """Consume task B's compare_paired output without rescoring or running an arm.

    ``budget`` is {"calls_cap": int}; actual calls come only from paired.cost.
    Unknown identity fields are null, not an inferred model/CLI/prompt identity.
    """
    if not isinstance(budget, dict) or set(budget) != {'calls_cap'}:
        raise ValueError('budget must contain only calls_cap')
    identity = deepcopy(change_identity)
    for key in ('model_id', 'route', 'effort', 'cli_version'):
        identity.setdefault(key, None)
    identity['prompt_digests'] = _prompt_changes(paired)
    for key in ('instructions_digests', 'tool_schema_digests'):
        identity[key] = {'baseline': None, 'candidate': None}
    identity['digest_unavailable_reasons'] = {key: DIGEST_UNAVAILABLE for key in
                                             ('instructions_digests', 'tool_schema_digests')}
    record = dict(version=VERSION, change_identity=identity, selection=deepcopy(selection),
                  **{key: deepcopy(paired[key]) for key in
                     ('comparable', 'conditions', 'cases', 'heldout', 'cost', 'latency')},
                  selected_not_run=deepcopy(paired.get('selected_not_run', [])),
                  counts=deepcopy(paired.get('counts', dict(Counter(c['change'] for c in paired['cases'])))),
                  evidence_class=evidence_class, rollback=deepcopy(rollback))
    calls = record['cost']['calls']
    cap = budget['calls_cap']
    if type(calls) is not int or type(cap) is not int or min(calls, cap) < 0:
        raise ValueError('Observed calls and calls_cap must be nonnegative integers')
    record['budget'] = {'calls_cap': cap, 'actual': calls, 'exceeded': calls > cap}
    record['decision'], record['decision_reason'] = _decision(record)
    return validate_record(record, root=root)


def _cell(value):
    return str('unknown' if value is None else value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('`', '&#96;').replace('\r', '').replace('\n', '<br>')


def markdown(record, *, root=REPO):
    """Render the versioned PR template, keeping reasons and condition disclosures."""
    validate_record(record, root=root)
    identity = record['change_identity']
    cases = ['| Case | Split | Baseline passed/trials | Candidate passed/trials | Change | Spread | Difference |',
             '|---|---|---:|---:|---|---:|---:|']
    for c in record['cases']:
        cases.append('| ' + ' | '.join(map(_cell, (c['id'], c['split'],
                     f'{c["baseline"]["passed"]}/{c["baseline"]["trials"]}',
                     f'{c["candidate"]["passed"]}/{c["candidate"]["trials"]}', c['change'],
                     c.get('spread'), c.get('difference')))) + ' |')
    selection = ['| Set / case | Split | Selection reasons |', '|---|---|---|']
    for c in record['selection']['selected']:
        selection.append(f'| {_cell(c["set"] + "/" + c["id"])} | {_cell(c["split"])} | {_cell("; ".join(c["reasons"]))} |')
    values = {
        'decision': record['decision'], 'reason': record['decision_reason'],
        'base': identity['base_sha'], 'head': identity['head_sha'],
        'model': _cell(identity['model_id']), 'route': _cell(identity['route']),
        'effort': _cell(identity['effort']), 'cli': _cell(identity['cli_version']),
        'evidence': record['evidence_class'], 'comparable': str(record['comparable']).lower(),
        'conditions': _cell(json.dumps(record['conditions'], ensure_ascii=False, sort_keys=True)),
        'digests': _cell(json.dumps({k: v for k, v in identity.items() if k.endswith('digests')}, sort_keys=True)),
        'digest_reasons': _cell(json.dumps(identity['digest_unavailable_reasons'], sort_keys=True)),
        'heldout': _cell(json.dumps(record['heldout'], ensure_ascii=False)),
        'calls': record['cost']['calls'], 'cap': record['budget']['calls_cap'],
        'usage': _cell(json.dumps(record['cost']['usage'], ensure_ascii=False)),
        'latency': _cell(json.dumps(record['latency'])),
        'selection': '\n'.join(selection), 'cases': '\n'.join(cases),
        'unmatched': _cell(json.dumps(record['selection']['unmatched'], ensure_ascii=False)),
        'not_run_counts': _cell(json.dumps(dict(sorted(Counter(c['set'] for c in record['selected_not_run']).items())))),
        'not_run': '\n'.join('| ' + ' | '.join(_cell(c[k]) for k in ('set', 'id', 'split', 'reason')) + ' |'
                             for c in record['selected_not_run']),
        'counts': _cell(json.dumps(record['counts'], sort_keys=True)),
        'rollback': _cell(record['rollback']['command']), 'rerun': _cell(record['rollback']['rerun']),
    }
    return Template((Path(root) / 'evals/templates/promotion-comment.md').read_text()).substitute(values)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('selection', 'rollback'):
        parser.add_argument('--' + name, type=Path, required=True, help='JSON file')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--paired', type=Path, help='Comparison JSON; requires --change-identity')
    source.add_argument('--from-pair', type=Path, help='Comparison JSON; derive all identity fields from observed reports/conditions')
    parser.add_argument('--change-identity', type=Path, help='JSON identity for legacy --paired mode')
    parser.add_argument('--calls-cap', type=int, required=True, help='Disclosed cap across both arms')
    parser.add_argument('--evidence-class', required=True, choices=('fixture', 'actual-cli-synthetic', 'owner-live'))
    parser.add_argument('--output', type=Path, required=True, help='JSON record destination')
    parser.add_argument('--markdown', type=Path, help='Defaults to the output path with .md suffix')
    args = parser.parse_args(argv)
    try:
        from jsonschema.exceptions import ValidationError
        paired = json.loads((args.from_pair or args.paired).read_text())
        if args.from_pair and args.change_identity:
            raise ValueError('--from-pair derives identity; do not supply --change-identity')
        if args.paired and not args.change_identity:
            raise ValueError('--paired requires --change-identity, or use --from-pair')
        identity = identity_from_pair(paired) if args.from_pair else json.loads(args.change_identity.read_text())
        record = build_record(json.loads(args.selection.read_text()), paired, identity,
                              rollback=json.loads(args.rollback.read_text()),
                              budget={'calls_cap': args.calls_cap}, evidence_class=args.evidence_class)
        comment = markdown(record)
        comment_path = args.markdown or args.output.with_suffix('.md')
        if comment_path.resolve() == args.output.resolve():
            raise ValueError('JSON and Markdown destinations must differ')
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        comment_path.write_text(comment)
    except (ValueError, OSError, KeyError, TypeError, ValidationError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
