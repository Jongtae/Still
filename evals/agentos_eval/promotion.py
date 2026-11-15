"""Create a reviewable promotion/rollback receipt; never execute its commands."""
import argparse
from collections import Counter
from copy import deepcopy
import json
import re
import shlex
import subprocess
from pathlib import Path
from string import Template

from jsonschema import Draft202012Validator

from .paths import REPO

VERSION = 'promotion-record/1'
DIGEST_UNAVAILABLE = "#825 per-attempt digests are recorded on Work turns, not on judgment calls."


def _decision(record):
    if record['budget']['exceeded']:
        return 'inconclusive', 'Observed calls exceeded the recorded run cap.'
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


def _run_identity(plan):
    required = ('cases_argument', 'seed', 'model', 'repetitions', 'max_calls', 'planned_calls')
    if not isinstance(plan, dict) or any(key not in plan for key in required):
        raise ValueError('Comparison requires its recorded run plan, including cases, seed and budget')
    for key in ('cases_argument', 'seed', 'model'):
        if not isinstance(plan[key], str) or not plan[key].strip():
            raise ValueError('Run plan requires nonempty ' + key)
    for key in ('repetitions', 'max_calls', 'planned_calls'):
        if type(plan[key]) is not int or plan[key] < (1 if key == 'repetitions' else 0):
            raise ValueError('Invalid run plan ' + key)
    run = dict(cases=plan['cases_argument'], seed=plan['seed'], model=plan['model'],
               repetitions=plan['repetitions'], max_calls=plan['max_calls'],
               requalify_attempts=plan.get('requalify_attempts', 1))
    if plan.get('reused_arm'):
        # The rerun's baseline is the original candidate/head. Reuse that
        # report so the original one-new-arm cap still describes the rerun.
        candidate_report = plan.get('report_paths', {}).get('candidate')
        if not candidate_report:
            raise ValueError('Reused run plan needs its candidate report path for an exact rerun')
        run['reuse_arm'] = 'baseline=' + candidate_report
    return run


def _spent_calls(cost, plan):
    if not plan.get('reused_arm'):
        return cost['calls']
    arm = plan['reused_arm']['arm']
    historical = cost[arm]['calls']
    new = cost['candidate' if arm == 'baseline' else 'baseline']['calls']
    if cost.get('new_calls') != new or cost.get('reused_calls') != historical or cost['calls'] != new + historical:
        raise ValueError('Reused/new call counts disagree with per-arm cost')
    return new


def identity_from_pair(paired):
    """Candidate identity, resolved revisions and rerun inputs copied from evidence."""
    conditions = paired['conditions']['candidate']
    revisions = paired.get('revisions', {})
    run = _run_identity(paired.get('plan'))
    model = conditions.get('pinned_model', conditions.get('runtime.requested_model'))
    if model != run['model']:
        raise ValueError('Run plan model disagrees with the candidate model')
    return dict(base_sha=revisions.get('baseline'), head_sha=revisions.get('candidate'),
                model_id=model, route=conditions.get('runtime.transport'),
                effort=conditions.get('runtime.effort'), cli_version=conditions.get('runtime.cli_version'), run=run)


def _evidence_class(evidence):
    classes = evidence.get('classes', {})
    a, b = classes.get('baseline'), classes.get('candidate')
    if not a or a != b:
        raise ValueError('Arm evidence classes disagree or are missing')
    if a in ('fixture', 'owner-live'):
        return a
    manifests = evidence.get('seed_manifests', {})
    if a.startswith('live-route:') and all(
            isinstance(manifests.get(arm), dict) and manifests[arm].get('owner_data') == 'none'
            and manifests[arm].get('created_at') and 'requested_model' in manifests[arm]
            for arm in ('baseline', 'candidate')):
        return 'actual-cli-synthetic'
    raise ValueError('Unsupported arm evidence or missing synthetic seed manifests')


def _git_commits(base, head, *, root=REPO):
    """Adopt Git rev-list's oldest-first topological range; never execute a revert."""
    for sha in (base, head):
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}([0-9a-f]{24})?', sha):
            raise ValueError('Rollback identity requires full resolved commit SHAs')
    result = subprocess.run(['git', '-C', str(root), 'rev-list', '--reverse', '--topo-order', base + '..' + head],
                            check=True, capture_output=True, text=True, timeout=30)
    commits = result.stdout.splitlines()
    if not commits or commits[-1] != head or base in commits:
        raise ValueError('Git rollback range does not end at the candidate')
    return commits


def _rerun(identity):
    run = identity['run']
    argv = ['python', '-m', 'agentos_eval.paired', '--baseline', identity['head_sha'],
                       '--candidate', '<REVERT_SHA>', '--cases', run['cases'], '--seed', run['seed'],
                       '--model', run['model'], '--repetitions', str(run['repetitions']),
            '--max-calls', str(run['max_calls']), '--requalify-attempts', str(run['requalify_attempts']),
            '--out', 'paired-revert', '--live']
    if run.get('reuse_arm'):
        argv.extend(['--reuse-arm', run['reuse_arm']])
    return shlex.join(argv)


def _prior_setting(conditions):
    # Values come only from the observed baseline. The activation body uses
    # the shipped POST /api/decision-route/activate schema, not a new restorer.
    keys = ('transport', 'engine', 'provider', 'requested_model', 'model_policy', 'effort', 'destination')
    setting = {key: conditions['runtime.' + key] for key in keys if 'runtime.' + key in conditions}
    if not setting.get('transport') or not setting.get('requested_model'):
        raise ValueError('Baseline conditions cannot establish the prior route/model setting')
    body = {key: setting[key] for key in ('transport', 'engine', 'model_policy', 'effort') if key in setting}
    body['model'] = setting['requested_model']
    return setting, 'POST /api/decision-route/activate ' + json.dumps(body, sort_keys=True, separators=(',', ':'))


def _rollback(identity, conditions, *, root=REPO):
    base, head = identity['base_sha'], identity['head_sha']
    if base == head:
        setting, command = _prior_setting(conditions['baseline'])
        return dict(kind='setting', setting=setting, command=command, rerun=_rerun(identity))
    commits = _git_commits(base, head, root=root)
    identity['commits'] = commits
    return dict(kind='git-revert', commits=commits,
                command='git revert --no-edit ' + base + '..' + head, rerun=_rerun(identity))


def validate_record(record, *, root=REPO):
    """Validate Draft 2020-12 shape and cross-field receipt invariants; return record."""
    schema = json.loads((Path(root) / 'evals/schemas/promotion-record-v1.json').read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(record)
    # JSON Schema numbers alone also accept Python NaN; receipts must be portable JSON.
    json.dumps(record, allow_nan=False)
    from .report import require_same_judge
    require_same_judge(record['conditions']['baseline'], record['conditions']['candidate'])
    if record['evidence_class'] != _evidence_class(record['evidence']):
        raise ValueError('Record evidence class disagrees with arm evidence')
    if record['change_identity']['run'] != _run_identity(record['plan']):
        raise ValueError('Identity run inputs disagree with recorded plan')
    if record['change_identity']['model_id'] != record['change_identity']['run']['model']:
        raise ValueError('Identity model disagrees with its rerun model')
    if (record['budget']['calls_cap'] != record['plan']['max_calls']
            or record['budget']['planned_calls'] != record['plan']['planned_calls']):
        raise ValueError('Budget disagrees with the recorded plan')
    if record.get('reused_arm') != record['plan'].get('reused_arm'):
        raise ValueError('Reused arm provenance disagrees with the recorded plan')
    if record['budget']['actual'] != _spent_calls(record['cost'], record['plan']):
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
    rollback, identity = record['rollback'], record['change_identity']
    base, head = identity['base_sha'], identity['head_sha']
    if rollback['kind'] == 'git-revert':
        if base == head or rollback['command'] != 'git revert --no-edit ' + base + '..' + head:
            raise ValueError('git-revert must match the exact identity base/head range')
        commits = rollback.get('commits', [])
        if (not commits or commits != identity.get('commits') or commits[-1] != head or base in commits
                or len(commits) != len(set(commits))):
            raise ValueError('Rollback commits disagree with identity range')
        if commits != _git_commits(base, head, root=root):
            raise ValueError('Rollback commits disagree with the observed Git base/head range')
    else:
        setting, command = _prior_setting(record['conditions']['baseline'])
        if base != head or rollback.get('setting') != setting or rollback['command'] != command:
            raise ValueError('Rollback setting must match the observed baseline activation')
    if rollback['rerun'] != _rerun(identity):
        raise ValueError('Rollback rerun must match identity model/seed/cases, repetitions, budget and revisions')

    return record


def build_record(selection, paired, change_identity=None, *, budget=None, evidence_class=None, rollback=None, root=REPO):
    """Consume task B's compare_paired output without rescoring or running an arm.

    Budget and evidence are derived from the run; optional caller values are assertions.
    Rollback and rerun use Git rev-list and the recorded run inputs, never typed commands.
    Unknown identity fields are null, not an inferred model/CLI/prompt identity.
    """
    from .report import require_same_judge
    require_same_judge(paired['conditions']['baseline'], paired['conditions']['candidate'])
    evidence = dict(classes=deepcopy(paired.get('evidence_class', {})),
                    seed_manifests=deepcopy(paired.get('seed_manifests', {})))
    derived_class = _evidence_class(evidence)
    if evidence_class is not None and evidence_class != derived_class:
        raise ValueError('Asserted evidence class does not match the arm evidence')
    plan = deepcopy(paired.get('plan'))
    identity = identity_from_pair(paired)
    if change_identity is not None and any(change_identity.get(key) != identity[key]
                                           for key in ('base_sha', 'head_sha', 'model_id', 'route', 'effort', 'cli_version')
                                           if key in change_identity):
        raise ValueError('Asserted change identity does not match observed arm identity')
    if budget is not None and budget != {'calls_cap': plan['max_calls']}:
        raise ValueError('Asserted calls cap does not match the recorded plan')
    generated_rollback = _rollback(identity, paired['conditions'], root=root)
    if rollback is not None and rollback != generated_rollback:
        raise ValueError('Asserted rollback does not match the generated exact identity base/head range and rerun')
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
                  evidence_class=derived_class, evidence=evidence, plan=plan, rollback=generated_rollback)
    record.update(reused_arm=deepcopy(paired.get('reused_arm')),
                  **{key: deepcopy(paired.get(key)) for key in ('time_gap_seconds', 'time_gap_proxy_seconds', 'time_gap_source')})
    calls = _spent_calls(record['cost'], plan)
    cap = plan['max_calls']
    if type(calls) is not int or type(cap) is not int or min(calls, cap) < 0:
        raise ValueError('Observed calls and calls_cap must be nonnegative integers')
    record['budget'] = {'calls_cap': cap, 'planned_calls': plan['planned_calls'], 'actual': calls, 'exceeded': calls > cap}
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
        'evidence_details': _cell(json.dumps(record['evidence'], ensure_ascii=False, sort_keys=True)),
        'conditions': _cell(json.dumps(record['conditions'], ensure_ascii=False, sort_keys=True)),
        'digests': _cell(json.dumps({k: v for k, v in identity.items() if k.endswith('digests')}, sort_keys=True)),
        'digest_reasons': _cell(json.dumps(identity['digest_unavailable_reasons'], sort_keys=True)),
        'heldout': _cell(json.dumps(record['heldout'], ensure_ascii=False)),
        'calls': record['cost']['calls'], 'cap': record['budget']['calls_cap'],
        'planned_calls': record['budget']['planned_calls'], 'budget_exceeded': str(record['budget']['exceeded']).lower(),
        'budget_actual': record['budget']['actual'], 'reused_calls': record['cost'].get('reused_calls', 0),
        'reuse': _cell(json.dumps({key: record.get(key) for key in
                                 ('reused_arm', 'time_gap_seconds', 'time_gap_proxy_seconds', 'time_gap_source')}, ensure_ascii=False)),
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
    for name in ('selection',):
        parser.add_argument('--' + name, type=Path, required=True, help='JSON file')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--paired', type=Path, help='Comparison JSON; requires --change-identity')
    source.add_argument('--from-pair', type=Path, help='Comparison JSON; derive all identity fields from observed reports/conditions')
    parser.add_argument('--change-identity', type=Path, help='JSON identity for legacy --paired mode')
    parser.add_argument('--calls-cap', type=int, help='Optional assertion of the recorded plan cap')
    parser.add_argument('--rollback', type=Path, help='Optional assertion of the generated rollback JSON')
    parser.add_argument('--evidence-class', help='Optional assertion of derived arm evidence', choices=('fixture', 'actual-cli-synthetic', 'owner-live'))
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
                              rollback=json.loads(args.rollback.read_text()) if args.rollback else None,
                              budget={'calls_cap': args.calls_cap} if args.calls_cap is not None else None,
                              evidence_class=args.evidence_class)
        comment = markdown(record)
        comment_path = args.markdown or args.output.with_suffix('.md')
        if comment_path.resolve() == args.output.resolve():
            raise ValueError('JSON and Markdown destinations must differ')
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        comment_path.write_text(comment)
    except (ValueError, OSError, KeyError, TypeError, ValidationError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
