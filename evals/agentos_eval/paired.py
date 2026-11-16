"""Change-triggered, model-pinned judgment pairs (#855); no runtime changes.

Reuse review (Adapt): Git's installed ``git worktree -h`` supplies detached
checkouts; Python subprocess/contextlib/tempfile/json supply process and file
lifecycle (stdlib, PSF, no dependency). Existing judgments.load_cases, plan,
run, report, compare, pin_model, requalify_route and CallCap supply measurement,
fingerprints, pinning and admission. No duplicate scoring or qualification
engine is introduced. Existing recall_pairs owns retrieval; this module only
runs judgment cases and discloses other selected sets as selected_not_run.
General eval frameworks cannot supply the repository's shipped
judgment callers or #827 verdict; the thin adapter keeps those semantics here.
No new package, licence, egress or durable-state authority. Offline review only:
external release/maintenance activity could not be checked without network.

All existing judgment fixtures are dev; new, synthetic held-outs live in a
separate case set. A held-out failure used to motivate a change must move to dev
and get a replacement under a new mapping version. Never tune these fixtures.

Live use is explicit (--live, --seed, --model, --max-calls). Each revision runs
in a fresh interpreter on its own copy of a manifest-validated synthetic seed, and
the SAME frozen cases and harness. The total call cap includes qualification,
with equal allocations for new arms (a reused arm spends none of this cap). The plan is
printed before either arm starts. These are host-visible calls, not billing or
CLI-internal model-call counts. No model calls occur in plan/compare modes.

From evals/: python -m agentos_eval.paired --baseline REF --candidate REF
--cases goal-failed --model MODEL --max-calls 40 --out /tmp/pair prints a plan.
Append --live --seed NAME to run it. --compare baseline.json candidate.json
compares saved reports, using their recorded splits or an explicit --splits JSON.
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from datetime import datetime, timezone
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

from . import judgments as j
from .paths import REPO, eval_home
from .sandbox import Sandbox

PARTNER_FILE = 'evals/judgments/heldout-partners-v2.json'
PARTNER_VERSION = 'judgment-heldout-partners/2'
NOT_RUN_REASON = 'no runner for this set in paired.py; run it with its own runner'
PER_CALL_CEILING_SECONDS = 120


def _timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _seconds(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise PairedError('Measurement timestamps must include a timezone')
    return parsed.timestamp()


def _attempt_limit(value):
    if type(value) is not int or not 1 <= value <= 3:
        raise PairedError('--requalify-attempts must be an integer from 1 to 3')
    return value


class PairedError(ValueError):
    """Invalid partition, selection, or run configuration."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def load_partners(*, root=REPO):
    """Load and validate the versioned partition against public case loaders."""
    root = Path(root)
    data = json.loads((root / PARTNER_FILE).read_text(encoding='utf-8'))
    validate_partners(data, root=root)
    return data


def _case_data(data, root):
    cases, catalogue, version = {}, None, None
    for name in data['case_sets']:
        path = (Path(root) / name).resolve()
        if not path.is_relative_to(Path(root).resolve()):
            raise PairedError('Case sets must be inside the repository')
        loaded = j.load_cases(path)
        if catalogue is not None and (loaded['catalogue'] != catalogue or loaded['version'] != version):
            raise PairedError('Partner case sets must share catalogue and schema version')
        catalogue, version = loaded['catalogue'], loaded['version']
        for case in loaded['cases']:
            if case['id'] in cases:
                raise PairedError('Duplicate case id across sets: ' + case['id'])
            cases[case['id']] = case
    return cases, catalogue, version


def validate_partners(data, *, root=REPO):
    """Every case has one split; every dev has a partner or an explicit reason."""
    if data.get('version') not in ('judgment-heldout-partners/1', PARTNER_VERSION) or not data.get('case_sets') or not data.get('roles'):
        raise PairedError('Unsupported or empty partner mapping')
    cases, _, _ = _case_data(data, root)
    assigned = set()
    for role, partition in data['roles'].items():
        dev, heldout = partition['dev'], partition['heldout']
        if not isinstance(dev, list) or not isinstance(heldout, list):
            raise PairedError('Split ids must be lists')
        ids = dev + heldout
        if len(set(ids)) != len(ids) or assigned.intersection(ids):
            raise PairedError('Duplicate id or overlapping dev/heldout partitions')
        for ident in ids:
            if ident not in cases or cases[ident]['role'] != role:
                raise PairedError('Unknown id or wrong judgment role: ' + ident)
        assigned.update(ids)
        links, unpaired = partition['partners'], partition.get('unpaired', {})
        if set(links) & set(unpaired) or set(links) | set(unpaired) != set(dev):
            raise PairedError('Every dev needs partners or an unpaired reason')
        referenced = set()
        for ident, entries in links.items():
            if not entries or len({entry['id'] for entry in entries}) != len(entries):
                raise PairedError('Empty or duplicate partner list: ' + ident)
            for entry in entries:
                if entry['id'] not in heldout or not isinstance(entry.get('reason'), str) or not entry['reason'].strip():
                    raise PairedError('Partner needs a held-out id and pairing reason')
                referenced.add(entry['id'])
        if referenced != set(heldout):
            raise PairedError('Every held-out case needs a dev pairing')
        if any(not isinstance(reason, str) or not reason.strip() for reason in unpaired.values()):
            raise PairedError('Missing reason for an unpaired dev case')
    if assigned != set(cases):
        raise PairedError('Every case in the declared sets needs a split')
    return data


def partners(case_ids, *, root=REPO):
    """Map requested dev ids to held-out ids; [] means explicitly unpaired.

    Unknown ids and held-out ids are errors, never silently treated as dev.
    """
    data = load_partners(root=root)
    lookup = {ident: [entry['id'] for entry in part['partners'].get(ident, [])]
              for part in data['roles'].values() for ident in part['dev']}
    unknown = set(case_ids) - lookup.keys()
    if unknown:
        raise PairedError('Not dev judgment ids: ' + ', '.join(sorted(unknown)))
    return {ident: lookup[ident] for ident in case_ids}


def select_cases(case_ids, *, root=REPO):
    """Freeze selected judgments plus deduplicated partners; return cases/splits/data."""
    data = load_partners(root=root)
    cases, catalogue, version = _case_data(data, root)
    all_splits = {ident: split for part in data['roles'].values()
                  for split in ('dev', 'heldout') for ident in part[split]}
    selected = set(case_ids)
    if not selected or selected - cases.keys():
        raise PairedError('Select known judgment ids')
    for ident in list(selected):
        if all_splits[ident] == 'dev':
            part = data['roles'][cases[ident]['role']]
            selected.update(entry['id'] for entry in part['partners'].get(ident, []))
    return (dict(version=version, catalogue=catalogue, cases=[cases[k] for k in sorted(selected)]),
            {k: all_splits[k] for k in sorted(selected)}, data)


def _conditions(report):
    active = report.get('runtime', {}).get('active', {})
    result = {key: report.get(key) for key in
              ('harness_version', 'case_set_version', 'case_set_digest', 'catalogue_digest', 'evidence_class',
               'pinned_model')}
    for key in ('judge', 'judge_model_resolved'):
        if key in report:
            result[key] = report[key]
    for key in ('transport', 'requested_model', 'effort', 'destination'):
        if key in active:
            result['runtime.' + key] = active[key]
    result['runtime.suite_version'] = report.get('runtime', {}).get('suite_version')
    # Transport distinctions describe execution conditions, not tasks/providers.
    if active.get('transport') == 'subscription_cli':
        for key in ('engine', 'model_policy', 'cli_version'):
            result['runtime.' + key] = active.get(key)
    elif active.get('transport') == 'direct_api':
        result['runtime.provider'] = active.get('provider')
    if any(row.get('role') == 'qualification-suite' for row in report.get('rows', [])):
        result['qualification_identity'] = report.get('qualification_identity')
    for key in ('partner_version', 'partner_digest', 'splits', 'seed_manifest_digest'):
        if key in report:
            result[key] = report[key]
    if 'observed_models' in report.get('runtime', {}):
        result['runtime.observed_models'] = report['runtime']['observed_models']
    observed = report.get('runtime', {}).get('observed_models', [])
    reported = any((entry.get('observed_model') if isinstance(entry, dict) else
                    dict(zip(j.MODEL_FIELDS, entry)).get('observed_model')) not in (None, '', 'not reported')
                   for entry in observed if isinstance(entry, (dict, list, tuple)))
    result['model_observation'] = dict(reported=reported, claim=(
        'requested model pinned; observed model reported' if reported else
        'requested model pinned; observed model not reported by the CLI' if active.get('transport') == 'subscription_cli'
        else 'requested model pinned; observed model not reported'))
    return result


def _passed(row):
    return int(row['outcome'] != 'not_run' and row['semantic_correct'] is True
               and row['routing_correct'] is not False)


def _cost(report):
    calls = sum(len(row['calls']) for row in report['rows']) + report.get('requalification', {}).get('calls', 0)
    usage = 'unavailable'
    if report.get('runtime', {}).get('active', {}).get('transport') == 'direct_api':
        records = [call.get('usage') for row in report['rows'] for call in row['calls']]
        # Preserve only reported tokens, never infer from calls or subscription price.
        if records and all(isinstance(item, dict) and item for item in records):
            keys = set(records[0])
            if all(set(item) == keys and all(type(v) in (int, float) and math.isfinite(v) and v >= 0
                                            for v in item.values()) for item in records):
                usage = {key: sum(item[key] for item in records) for key in sorted(keys)}
        elif records and all(type(item) in (int, float) and math.isfinite(item) and item >= 0 for item in records):
            usage = {'reported_tokens': sum(records)}
    return dict(calls=calls, usage=usage)


def _latency(report):
    timing = report.get('summary', {}).get('total', {}).get('end_to_end_latency') or {}
    return {out: round(timing[key] * 1000, 6) if timing.get(key) is not None else None
            for out, key in (('p50_ms', 'p50'), ('p95_ms', 'p95'))}


def compare_paired(baseline_report, candidate_report, *, splits):
    """Extend judgments.compare with split-local, repetition-aware #827 results.

    Pass includes semantic AND routing correctness, using judgments.compare's
    rule. Per-case fixed/regressed means a certain increase/decrease in pass rate;
    equal rates with differing paired trial outcomes are mixed. Missing, not-run
    or unequal trials are unverified, never a fix. Dev never enters the verdict.

    Each case's spread is the larger of its arms' (max - min) repetition pass
    indicators, each 0 or 1. Difference is candidate minus baseline pass rate.
    A nonzero change is certain only if spread <= abs(difference); otherwise it
    is uncertain-fixed/uncertain-regressed. Any uncertain held-out regression
    blocks better; a certain regression is worse. Sampling arms require three
    trials per held-out case, unless both reports explicitly declare fixture
    arms deterministic. One repetition has range 0, but does not prove a sampled
    gain. Equal
    rates with changed trial outcomes are mixed. Dev never enters the verdict.
    Top-level spread (maximum per-case held-out spread) and difference (mean
    held-out rate change) are DISPLAY ONLY; uncertainty is assessed per case.
    """
    before, after = baseline_report, candidate_report
    from .report import require_same_judge
    require_same_judge(before, after)
    a, b = _conditions(before), _conditions(after)
    required = {'runtime.transport', 'runtime.requested_model', 'runtime.effort', 'runtime.destination'}
    differences, unknown = [], []
    for field in sorted(set(a) | set(b) | required):
        av, bv = a.get(field), b.get(field)
        nullable = field == 'runtime.effort'
        if field not in a or field not in b or (not nullable and (av in (None, '', []) or bv in (None, '', []))):
            unknown.append(field)
        if field in a and field in b and av != bv:
            differences.append(dict(field=field, baseline=av, candidate=bv))
    for arm, conditions in (('baseline', a), ('candidate', b)):
        pin = conditions.get('pinned_model')
        for index, entry in enumerate(conditions.get('runtime.observed_models', [])):
            values = entry if isinstance(entry, dict) else dict(zip(j.MODEL_FIELDS, entry)) if isinstance(entry, (list, tuple)) else {}
            for field in ('requested_model', 'observed_model'):
                value = values.get(field)
                if value != pin and not (field == 'observed_model' and value == 'not reported'):
                    unknown.append(f'{arm}.observed_models[{index}].{field}={value!r} does not match pin {pin!r}')
        if conditions.get('pinned_model') != conditions.get('runtime.requested_model'):
            unknown.append(arm + '.verified-model-pin')
        if (conditions.get('runtime.transport') == 'subscription_cli'
                and conditions.get('runtime.model_policy') != 'explicit'):
            unknown.append(arm + '.explicit-model-policy')
        if 'splits' in conditions and conditions['splits'] != splits:
            unknown.append(arm + '.verified-split-assignment')
    for arm, report in (('baseline', before), ('candidate', after)):
        if report.get('status') == 'unavailable' or report.get('runtime', {}).get('active', {}).get('available') is False:
            unknown.append(arm + '.route-unavailable')
    # Reuse the existing conditions gate, including its future extra checks.
    try:
        comparison = j.compare(before, after)
    except j.CaseError as exc:
        comparison = dict(same_conditions=False, reasons=[str(exc)], changes=[])
    if not comparison['same_conditions'] and not differences and not unknown:
        differences.append(dict(field='judgments.compare', baseline=comparison['reasons'], candidate=None))
    indices = []
    for label, report in (('baseline', before), ('candidate', after)):
        grouped = defaultdict(dict)
        for row in report['rows']:
            trial = row.get('trial', 1)
            if trial in grouped[row['id']]:
                unknown.append(f'{label}.{row["id"]}.duplicate-trial')
            grouped[row['id']][trial] = row
        indices.append(grouped)
    ids = sorted(set(indices[0]) | set(indices[1]) | set(splits))
    table, ranges = [], []
    for ident in ids:
        ar, br = indices[0].get(ident, {}), indices[1].get(ident, {})
        split = splits.get(ident)
        is_qualification = all(row.get('role') == 'qualification-suite' for row in [*ar.values(), *br.values()])
        if split not in ('dev', 'heldout') and not (ident in splits and split is None and is_qualification):
            unknown.append('split.' + ident)
        if not ar or not br or set(ar) != set(br):
            differences.append(dict(field='trials.' + ident, baseline=sorted(ar), candidate=sorted(br)))
        if any(row.get('semantic_correct') is None or row.get('outcome') in ('not_run', 'budget_refused')
               for row in [*ar.values(), *br.values()]):
            unknown.append('results.' + ident)
        av, bv = [_passed(row) for row in ar.values()], [_passed(row) for row in br.values()]
        am = sum(av) / len(av) if av else None
        bm = sum(bv) / len(bv) if bv else None
        difference = bm - am if am is not None and bm is not None else None
        spread = float(max((max(v) - min(v) for v in (av, bv) if v), default=0))
        if difference is None:
            change = 'mixed'
        elif difference != 0:
            change = 'fixed' if difference > 0 else 'regressed'
            if spread > abs(difference) + 1e-12:
                change = 'uncertain-' + change
        else:
            change = 'mixed' if any(_passed(ar[t]) != _passed(br[t]) for t in set(ar) & set(br)) else 'same'
        table.append(dict(id=ident, set='qualification' if is_qualification else 'judgments',
                          split=split, baseline=dict(passed=sum(av), trials=len(av)),
                          candidate=dict(passed=sum(bv), trials=len(bv)), change=change,
                          difference=difference, spread=spread))
        if split == 'heldout':
            ranges.extend(max(values) - min(values) for values in (av, bv) if values)
    comparable = not differences and not unknown and comparison['same_conditions']
    held = [row for row in table if row['split'] == 'heldout']
    fixed = [row['id'] for row in held if row['change'] == 'fixed']
    regressed = [row['id'] for row in held if row['change'] == 'regressed']
    uncertain_regressed = [row['id'] for row in held if row['change'] == 'uncertain-regressed']
    deterministic = all(report.get('deterministic') is True and report['evidence_class'] == 'fixture'
                        for report in (before, after))
    short_trials = [row['id'] for row in held if any(row[arm]['trials'] < 3 for arm in ('baseline', 'candidate'))]
    rates = {arm: sum(row[arm]['passed'] / row[arm]['trials'] for row in held if row[arm]['trials']) / len(held)
             if held else None for arm in ('baseline', 'candidate')}
    difference = rates['candidate'] - rates['baseline'] if held else 0.0
    spread = float(max(ranges, default=0))
    if not comparable:
        verdict, reasons = 'incomparable', ['Unequal or unknown conditions; see conditions and judgment_comparison.']
    elif not held:
        verdict, reasons = 'inconclusive', ['No held-out observations; dev gains cannot establish promotion.']
    elif short_trials and not deterministic:
        verdict, reasons = 'inconclusive', ['Sampling arms require at least three trials per held-out case: ' + ', '.join(short_trials)]
    elif regressed:
        verdict, reasons = 'worse', ['At least one held-out case has a certain regression.']
    elif uncertain_regressed:
        verdict, reasons = 'inconclusive', ['An uncertain held-out regression blocks better: ' + ', '.join(uncertain_regressed)]
    elif fixed and difference >= -1e-12:
        verdict, reasons = 'better', ['At least one certain held-out fix, no certain regression, mean rate not lower.']
    elif all(row['change'] == 'same' for row in held):
        verdict, reasons = 'no-gain', ['Every held-out case is unchanged.']
    else:
        verdict, reasons = 'inconclusive', ['Only uncertain/mixed changes, or a lower mean rate without a certain regression.']
    costs = {arm: _cost(result) for arm, result in (('baseline', before), ('candidate', after))}
    usage = 'unavailable'
    au, bu = costs['baseline']['usage'], costs['candidate']['usage']
    if isinstance(au, dict) and isinstance(bu, dict) and set(au) == set(bu):
        usage = {key: au[key] + bu[key] for key in au}
    result = dict(comparable=comparable, conditions=dict(baseline=a, candidate=b,
                differences=differences, unknown=sorted(set(unknown))), cases=table,
                heldout=dict(verdict=verdict, reasons=reasons, fixed=fixed, regressed=regressed,
                             uncertain_regressed=uncertain_regressed,
                             spread=spread, difference=difference, pass_rates=rates,
                             counts=dict(Counter(row['change'] for row in held))),
                counts=dict(Counter(row['change'] for row in table)),
                cost=dict(calls=sum(c['calls'] for c in costs.values()), usage=usage, **costs),
                latency={arm: _latency(result) for arm, result in (('baseline', before), ('candidate', after))},
                evidence_class=dict(baseline=before['evidence_class'], candidate=after['evidence_class']),
                seed_manifests=dict(baseline=before.get('seed_manifest'), candidate=after.get('seed_manifest')),
                plan=before.get('plan') if before.get('plan') == after.get('plan') else None,
                revisions=dict(baseline=before.get('git_head'), candidate=after.get('git_head')),
                prompt_digests=dict(baseline=before.get('prompt_digests'), candidate=after.get('prompt_digests')),
                selected_not_run=_not_run_rows(before.get('selected_not_run', []) + after.get('selected_not_run', [])),
                judgment_comparison=comparison)
    # A resumed run can keep the original saved report byte-for-byte. Recover
    # its new plan only when that plan binds the other arm's complete JSON.
    if result['plan'] is None:
        for fresh, saved_arm, saved in ((before, 'candidate', after), (after, 'baseline', before)):
            plan = fresh.get('plan') or {}
            reused = plan.get('reused_arm') or {}
            if reused.get('arm') == saved_arm and reused.get('report_content_digest') == _digest(saved):
                result['plan'] = plan
                break
    plan = result['plan'] or {}
    if plan.get('reused_arm'):
        reused = plan['reused_arm']
        result.update(reused_arm=reused, **_arm_gap(reused, before, after))
        result['cost'].update(reused_calls=costs[reused['arm']]['calls'],
                              new_calls=costs['candidate' if reused['arm'] == 'baseline' else 'baseline']['calls'])
    return result


@contextmanager
def revision_worktree(ref, *, root=REPO, git_runner=subprocess.run):
    """Resolve ref to a commit, add only our detached worktree, always remove it."""
    def git(*args):
        return git_runner(['git', '-C', str(root), *args], check=True, capture_output=True, text=True)
    commit = git('rev-parse', '--verify', '--end-of-options', ref + '^{commit}').stdout.strip()
    with tempfile.TemporaryDirectory(prefix='still-paired-') as folder:
        checkout = Path(folder) / 'checkout'
        git('worktree', 'add', '--detach', str(checkout), commit)
        try:
            yield checkout, commit
        finally:
            # Never prune/remove another session's worktree. --force only removes
            # generated files in this disposable checkout, not any user branch.
            git('worktree', 'remove', '--force', str(checkout))


def synthetic_manifest(seed):
    """Resolve a named synthetic seed; never accept an owner snapshot."""
    from .cli import resolve_seed
    from .synthetic_seed import validate_location
    if not seed:
        raise PairedError('--live requires --seed (a synthetic seed)')
    path = Path(resolve_seed(str(seed))).resolve()
    validate_location(path)
    validate_location(eval_home() / 'sandboxes')
    if not path.is_relative_to((eval_home() / 'seeds').resolve()):
        raise PairedError('Synthetic seeds must live under eval_home()/seeds; use synthetic_seed --out NAME')
    try:
        manifest = json.loads((path / 'synthetic-seed.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise PairedError('Seed requires a synthetic-seed.json manifest: ' + str(exc)) from exc
    if not isinstance(manifest, dict) or manifest.get('owner_data') != 'none':
        raise PairedError('Seed manifest must declare owner_data: "none"; owner snapshots use judgments run')
    if not manifest.get('created_at') or 'requested_model' not in manifest:
        raise PairedError('Seed manifest must record created_at and requested_model')
    return path, manifest


def measure_arm(request, *, factory=None, timer=time.monotonic, sandbox_factory=Sandbox):
    """Copy a synthetic seed through the shipped sandbox/pinning/qualification path.

    factory(data), timer and sandbox_factory are model-free test seams. The seed
    route supplies engine and effort; no route row is constructed here. Copied
    path-bound proof is requalified under the same CallCap as measurement.
    """
    if not request.get('model'):
        raise PairedError('An explicit model is required')
    if not factory and request.get('repetitions', 0) < 3:
        raise PairedError('Live sampling arms require at least three repetitions')
    seed, manifest = synthetic_manifest(request.get('seed'))
    if request.get('seed_manifest_digest') and request['seed_manifest_digest'] != _digest(manifest):
        raise PairedError('Synthetic seed manifest changed after planning')
    src = Path(request['checkout']) / 'src'
    j.load_still(src)
    cases = j.load_cases(request['cases_path'])
    cap = j.CallCap(request['max_calls'])
    attempt_limit = _attempt_limit(request.get('requalify_attempts', 2))
    sandbox = sandbox_factory(eval_home() / 'sandboxes', seed=seed, src=src)
    try:
        data = sandbox.prepare()
        copied_manifest = json.loads((Path(data) / 'synthetic-seed.json').read_text(encoding='utf-8'))
        if copied_manifest != manifest:
            raise PairedError('Copied synthetic seed manifest differs from the selected seed')
        service = (factory or j.service_factory)(data)
        route = service.decision_routes.active() or {}
        if route.get('transport') != 'subscription_cli' or not route.get('engine'):
            raise PairedError('Synthetic seed must have a shipped, qualified subscription CLI route')
        if request.get('effort') is not None and request['effort'] != route.get('effort'):
            raise PairedError('Requested effort differs from the seed; build a matching synthetic seed')
        audit = []
        original = service.record_decision
        def capture(record):
            original(record)
            audit.append(dict(record))
        service.record_decision = capture
        j.pin_model(service, request['model'])
        attempts = []
        for attempt in range(attempt_limit):
            if attempt:
                # Reuse the shipped pin seam to clear the failed job's cache.
                # Only the same shipped job can qualify the unchanged model.
                j.pin_model(service, request['model'])
            qualification = j.requalify_route(service, cap)
            attempts.append(dict(qualification))
            if j.effective_runtime(service)['active'].get('available') is True:
                break
            if qualification.get('state') != 'failed' or cap.refused or cap.used >= cap.max_calls:
                break
        qualification = dict(qualification, attempts=attempts, calls=sum(a['calls'] for a in attempts),
                             attempted=any(a.get('attempted') for a in attempts))
        # The update job restores its own audit wrapper; capture remains installed.
        rows = j.run(cases, service.decision_judge, service.decision_engine, cap=cap, audit=audit,
                     scope=service.work_decision_scope, roles=request.get('roles') or sorted(
                         {c['role'] for c in cases['cases']} | ({'qualification-suite'} if request.get('qualification_selected') else set())),
                     trials=request['repetitions'], timer=timer)
        runtime = j.effective_runtime(service, audit)
        result = j.report(rows, dict(src=src, git_head=request['commit'],
                                    case_set_version=cases['version'],
                                    evidence_class='fixture' if factory else 'live-route:' + runtime['active'].get('transport', 'unknown')),
                          runtime, cases=cases)
        result.update(pinned_model=request['model'], requalification=qualification,
                      call_budget=dict(max_calls=cap.max_calls, admitted=cap.used, refused=cap.refused),
                      catalogue_digest=_digest(cases['catalogue']), splits=request['splits'],
                      partner_version=request['partner_version'], partner_digest=request['partner_digest'],
                      seed_manifest=manifest, seed_manifest_digest=_digest(manifest),
                      deterministic=False, plan=request.get('plan'),
                      measured_at=_timestamp(time.time()),
                      selected_not_run=request.get('selected_not_run', []))
        if runtime['active'].get('available') is not True:
            result.update(status='unavailable', unavailable_reason=(
                f'Route unavailable after {len(attempts)} of {attempt_limit} requalification attempts; '
                f'last state: {qualification.get("state")}; budget refused: {cap.refused}'))
        else:
            result['status'] = 'available'
        return result
    finally:
        sandbox.stop()


def _process_arm(request):
    """Use this harness with that revision's src; no mixed-checkout imports."""
    with tempfile.TemporaryDirectory(prefix='still-paired-request-') as folder:
        source, target = Path(folder) / 'request.json', Path(folder) / 'report.json'
        source.write_text(json.dumps(request), encoding='utf-8')
        env = dict(os.environ, PYTHONPATH=str(REPO / 'evals'))
        calls = request.get('planned_calls', request.get('max_calls', 0))
        timeout = max(1, calls) * PER_CALL_CEILING_SECONDS + 60
        try:
            subprocess.run([sys.executable, '-m', 'agentos_eval.paired', '--worker', str(source), str(target)],
                           cwd=request['checkout'], env=env, check=True, capture_output=True, text=True, timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            tail = exc.stderr or ''
            if isinstance(tail, bytes):
                tail = tail.decode('utf-8', errors='replace')
            detail = (f'timed out after {timeout}s ({calls} planned calls × {PER_CALL_CEILING_SECONDS}s + 60s startup)'
                      if isinstance(exc, subprocess.TimeoutExpired) else f'exited with status {exc.returncode}')
            raise PairedError(f'Paired worker {detail}; stderr tail:\n{tail[-4000:]}') from exc
        return json.loads(target.read_text(encoding='utf-8'))


def _reuse_input(value):
    if not value:
        return None
    if isinstance(value, str):
        arm, sep, name = value.partition('=')
        if not sep:
            raise PairedError('--reuse-arm must be baseline=REPORT or candidate=REPORT')
    else:
        if not isinstance(value, dict) or len(value) != 1:
            raise PairedError('Exactly one arm can be reused')
        arm, name = next(iter(value.items()))
    if arm not in ('baseline', 'candidate') or not str(name):
        raise PairedError('--reuse-arm must be baseline=REPORT or candidate=REPORT')
    path = Path(name).expanduser().resolve()
    content = path.read_bytes()
    report = json.loads(content)
    measured_at = report.get('measured_at')
    if measured_at is not None:
        _seconds(measured_at)
    metadata = dict(arm=arm, report_path=str(path), report_sha256=hashlib.sha256(content).hexdigest(),
                    report_content_digest=_digest(report), measured_at=measured_at,
                    report_modified_at=_timestamp(path.stat().st_mtime),
                    measured_at_source='report' if measured_at else 'unavailable; file mtime is a proxy only')
    return report, metadata


def _arm_gap(reused, baseline=None, candidate=None, *, now=None):
    fresh = candidate if reused['arm'] == 'baseline' else baseline
    new_time = fresh.get('measured_at') if fresh else None
    end = _seconds(new_time) if new_time else time.time() if now is None else now
    if reused['measured_at']:
        gap = abs(end - _seconds(reused['measured_at']))
        source = 'arm completion timestamps' if new_time else 'planning time versus saved arm completion; provisional'
        proxy = None
    else:
        gap = None
        proxy = abs(end - _seconds(reused['report_modified_at']))
        source = 'measurement time unavailable; report-file-mtime proxy only'
    return dict(time_gap_seconds=gap, time_gap_proxy_seconds=proxy, time_gap_source=source)


def _validate_reuse(report, metadata, *, ref, root, git_runner, cases, splits, data, model, manifest, repetitions, effort):
    def resolve(value):
        return git_runner(['git', '-C', str(root), 'rev-parse', '--verify', '--end-of-options', value + '^{commit}'],
                          check=True, capture_output=True, text=True).stdout.strip()
    saved = report.get('git_head')
    if not isinstance(saved, str) or not saved or any(c not in '0123456789abcdef' for c in saved):
        raise PairedError('Reused report must name an observed Git commit SHA')
    if resolve(saved) != resolve(ref):
        raise PairedError('Reused report git_head does not resolve to the selected ref')
    expected = dict(splits=splits, partner_digest=_digest(data), partner_version=data['version'],
                    case_set_digest=_digest(cases), case_set_version=cases['version'],
                    catalogue_digest=_digest(cases['catalogue']), pinned_model=model,
                    seed_manifest=manifest, seed_manifest_digest=_digest(manifest))
    for field, value in expected.items():
        if report.get(field) != value:
            raise PairedError('Reused report does not match new plan: ' + field)
    active = report.get('runtime', {}).get('active', {})
    if active.get('requested_model') != model or active.get('model_policy') != 'explicit':
        raise PairedError('Reused report model pin does not match new plan')
    if effort is not None and active.get('effort') != effort:
        raise PairedError('Reused report effort does not match new plan')
    for entry in report.get('runtime', {}).get('observed_models', []):
        fields = entry if isinstance(entry, dict) else dict(zip(j.MODEL_FIELDS, entry))
        if fields.get('requested_model') != model or fields.get('observed_model') not in (model, 'not reported'):
            raise PairedError('Reused report observed model contradicts the pin')
    trials = defaultdict(list)
    for row in report['rows']:
        trials[row['id']].append(row.get('trial', 1))
    if set(trials) != set(splits) or any(sorted(values) != list(range(1, repetitions + 1)) for values in trials.values()):
        raise PairedError('Reused report cases/repetitions do not match new plan')


def run_pair(baseline, candidate, case_ids, *, model, repetitions=3, max_calls,
             seed=None, effort=None, root=REPO, live=False, out=None, requalify_attempts=2, reuse_arm=None,
             git_runner=subprocess.run, arm_runner=None):
    """Plan or run frozen cases; injected git/arm runners never imply live evidence."""
    requalify_attempts = _attempt_limit(requalify_attempts)
    reuse = _reuse_input(reuse_arm)
    if not isinstance(model, str) or not model.strip():
        raise PairedError('--model is required (no route-selected fallback)')
    if type(repetitions) is not int or repetitions < 1 or type(max_calls) is not int or max_calls < 0:
        raise PairedError('Positive repetitions and a nonnegative total max-calls are required')
    if live and repetitions < 3:
        raise PairedError('Live sampling arms require at least three repetitions')
    selected = _selection_rows(case_ids) if isinstance(case_ids, dict) and 'selected' in case_ids else (
        case_ids if isinstance(case_ids, dict) else dict(ids=case_ids, selected_not_run=[]))
    qualification_ids = ['qualification-' + ident for ident in j.still().decision_qualification.CASE_IDS]
    qualification_selected = sorted(set(selected['ids']) & set(qualification_ids))
    judgment_ids = [ident for ident in selected['ids'] if ident not in qualification_ids]
    if judgment_ids:
        cases, splits, data = select_cases(judgment_ids, root=root)
        roles = {c['role'] for c in cases['cases']}
    elif qualification_selected:
        data = load_partners(root=root)
        cases, splits, roles = j.load_cases(Path(root) / data['case_sets'][0]), {}, set()
    else:
        raise PairedError('No judgment or qualification rows selected')
    if qualification_selected:
        # Public j.run includes the complete suite, exactly as judgments.live_run.
        # Expand and budget it explicitly rather than masking unselected calls.
        splits.update({ident: None for ident in qualification_ids})
        roles.add('qualification-suite')
    # Use public plan call counts, excluding its automatically appended qualification
    # suite. That suite is setup overhead, reported separately under the SAME cap.
    counts = j.plan(cases)['roles']
    per_arm = sum(v['max_calls'] for role, v in counts.items() if role in roles) * repetitions
    qualification_calls = counts.get('qualification-suite', {}).get('max_calls', 0)
    new_arms = 1 if reuse else 2
    per_arm_cap = max_calls // new_arms
    planned = dict(model=model, seed=str(seed) if seed else None, effort=effort, baseline=baseline, candidate=candidate,
                   selected_not_run=selected['selected_not_run'],
                   cases_argument=selected.get('cases_argument', ','.join(sorted(set(selected['ids'])))),
                   qualification_selected=qualification_selected,
                   qualification_measured=qualification_ids if qualification_selected else [],
                   cases=sorted(splits), splits=splits, repetitions=repetitions,
                   measurement_calls=new_arms * per_arm, max_calls=max_calls, max_calls_per_arm=per_arm_cap,
                   planned_calls=new_arms * (per_arm + qualification_calls * requalify_attempts),
                   requalify_attempts=requalify_attempts, new_arms=new_arms,
                   qualification_calls_per_arm=qualification_calls * requalify_attempts,
                   qualification_calls_per_attempt=qualification_calls,
                   qualification='Estimate from current suite; revision changes may differ. Included in max_calls.',
                   unused_call_allowance=max_calls % new_arms, owner_data=False, live=live)
    seed_path, manifest = (synthetic_manifest(seed) if live or reuse else (None, None))
    if reuse:
        saved_report, reused = reuse
        _validate_reuse(saved_report, reused, ref=baseline if reused['arm'] == 'baseline' else candidate,
                        root=root, git_runner=git_runner, cases=cases, splits=splits, data=data,
                        model=model, manifest=manifest, repetitions=repetitions, effort=effort)
        planned.update(reused_arm=reused, **_arm_gap(reused))
    if out:
        planned['report_paths'] = {arm: str((Path(out) / (arm + '.json')).resolve()) for arm in ('baseline', 'candidate')}
        if reuse:
            planned['report_paths'][reused['arm']] = reused['report_path']
    print('Planned calls: ' + json.dumps(planned, sort_keys=True), flush=True)
    if not live:
        return planned
    if max_calls < planned['planned_calls']:
        raise PairedError('--max-calls cannot cover the planned measurement calls (plus qualification overhead)')
    reports = {}
    with tempfile.TemporaryDirectory(prefix='still-paired-cases-') as folder:
        case_path = Path(folder) / 'cases.json'
        case_path.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
        for arm, ref in (('baseline', baseline), ('candidate', candidate)):
            if reuse and arm == reused['arm']:
                reports[arm] = deepcopy(saved_report)
                continue
            with revision_worktree(ref, root=root, git_runner=git_runner) as (checkout, commit):
                request = dict(checkout=str(checkout), commit=commit, cases_path=str(case_path), model=model,
                               seed=str(seed_path), seed_manifest_digest=_digest(manifest), effort=effort,
                               selected_not_run=selected['selected_not_run'], repetitions=repetitions, max_calls=per_arm_cap, requalify_attempts=requalify_attempts,
                               qualification_selected=qualification_selected, roles=sorted(roles), plan=planned,
                               planned_calls=per_arm + qualification_calls * requalify_attempts,
                               splits=splits, partner_version=data['version'], partner_digest=_digest(data), live=True)
                reports[arm] = (arm_runner or _process_arm)(request)
                reports[arm]['selected_not_run'] = selected['selected_not_run']
                reports[arm]['plan'] = planned
                reports[arm].setdefault('measured_at', _timestamp(time.time()))
            if out:
                _write_json(Path(out) / (arm + '.json'), reports[arm])
    result = compare_paired(reports['baseline'], reports['candidate'], splits=splits)
    result['plan'] = planned
    if reuse:
        result.update(reused_arm=reused, **_arm_gap(reused, reports['baseline'], reports['candidate']))
        result['cost'].update(reused_calls=result['cost'][reused['arm']]['calls'],
                              new_calls=result['cost']['candidate' if reused['arm'] == 'baseline' else 'baseline']['calls'])
    if out:
        _write_json(Path(out) / 'comparison.json', result)
    return result


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _not_run_rows(rows):
    return [dict(set=key[0], id=key[1], split=row.get('split'), reason=NOT_RUN_REASON)
            for key, row in sorted({(row['set'], row['id']): row for row in rows}.items())]


def _selection_rows(data):
    ids = sorted({row['id'] for row in data['selected'] if row['set'] in ('judgments', 'qualification')})
    if not ids:
        raise PairedError('No judgment or qualification rows selected; run the selected sets with their own runners')
    return dict(ids=ids, selected_not_run=_not_run_rows([row for row in data['selected'] if row['set'] not in ('judgments', 'qualification')]))


def _selection(value):
    if value.startswith('@'):
        return dict(_selection_rows(json.loads(Path(value[1:]).read_text(encoding='utf-8'))), cases_argument=value)
    ids = sorted({ident.strip() for ident in value.split(',') if ident.strip()})
    if not ids:
        raise PairedError('No judgment rows selected')
    return dict(ids=ids, selected_not_run=[], cases_argument=value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline')
    parser.add_argument('--candidate')
    parser.add_argument('--cases', help='comma-separated ids or @selection.json')
    parser.add_argument('--model', help='required for running/planning; never use a moving route')
    parser.add_argument('--seed', help='synthetic seed name under eval_home()/seeds; required with --live')
    parser.add_argument('--effort')
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--max-calls', type=int, help='hard cap for newly spent calls, including requalification')
    parser.add_argument('--requalify-attempts', type=int, default=2, choices=range(1, 4), help='shipped-job attempts per new arm (default 2)')
    parser.add_argument('--reuse-arm', action='append', help='baseline=REPORT or candidate=REPORT; validate and spend only on the other arm')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--live', action='store_true', help='opt in to spending the disclosed call budget')
    parser.add_argument('--compare', nargs=2, metavar=('BASELINE_JSON', 'CANDIDATE_JSON'))
    parser.add_argument('--splits', type=Path, help='id-to-split JSON for external reports')
    parser.add_argument('--worker', nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.reuse_arm and len(args.reuse_arm) != 1:
        parser.error('Exactly one --reuse-arm may be supplied')
    try:
        if args.worker:
            # An internal worker also requires explicit live intent in its request.
            request = json.loads(Path(args.worker[0]).read_text(encoding='utf-8'))
            if not request.get('live'):
                raise PairedError('Worker requires explicit live intent')
            _write_json(Path(args.worker[1]), measure_arm(request))
            return 0
        if args.compare:
            baseline, candidate = [json.loads(Path(p).read_text(encoding='utf-8')) for p in args.compare]
            splits = json.loads(args.splits.read_text()) if args.splits else baseline.get('splits', {})
            result = compare_paired(baseline, candidate, splits=splits)
        else:
            if not all((args.baseline, args.candidate, args.cases, args.model, args.out)) or args.max_calls is None:
                parser.error('--baseline, --candidate, --cases, --model, --max-calls and --out are required')
            result = run_pair(args.baseline, args.candidate, _selection(args.cases), model=args.model,
                              seed=args.seed, effort=args.effort, repetitions=args.repetitions,
                              max_calls=args.max_calls, out=args.out, live=args.live,
                              requalify_attempts=args.requalify_attempts, reuse_arm=args.reuse_arm[0] if args.reuse_arm else None)
        if args.out:
            _write_json(args.out / ('comparison.json' if args.compare or args.live else 'plan.json'), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PairedError, j.CaseError, OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
