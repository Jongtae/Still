"""Automatic multi-round versioned-text revision on dev, then one final held-out comparison (#1355).

Adapt: Harness² (arXiv 2610.14224) recursive mode, where each round's winner becomes the next base, chained
from the existing per-round tools: `revise.check_room`, `revise.propose`, `arm_run.run_arm`,
`compare_versioned_text` and the experience records. No second comparator and no new dependency.

Held-out guard: the loop runs, compares and proposes from loop-visible dev scenarios only. Its runs are
refused when they would include a held-out or owner-store scenario, its experience records are read only
from comparisons over its own dev scenarios, and the proposer's cause is built from dev records only.
`final_heldout` runs once after the loop ends, on held-out scenarios only.

Candidate text lives in the loop's output folder outside the repository; the working tree is never edited.
"""
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import arm_run, revise
from .arm_compare import DECISION_SPLITS, compare_versioned_text, mean, records_by_scenario, text_digest

LOOP_SCHEMA = 'revision-loop/1'
FINAL_SCHEMA = 'revision-loop-final/1'
LOOP_SPLITS = ('dev',)
FINAL_SPLITS = ('heldout',)
_LOOP_OWNED = ('--scenarios', '--split', '--epochs', '--world')
_CAUSE_LIMIT = 6000


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def owned_flags(sweep_args):
    """Flags the loop sets itself; a caller passing them could widen the scenarios a run sees."""
    return sorted({flag for arg in sweep_args for flag in _LOOP_OWNED
                   if arg == flag or arg.startswith(flag + '=')})


def split_ids(ids, meta, split):
    """Refuse ids that are unknown, from the owner store, or outside ``split``."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise ValueError(f'At least one {split} scenario is required')
    for ident in ids:
        row = meta.get(ident)
        if row is None:
            raise ValueError(f'Unknown scenario {ident!r}')
        if row.get('source') == 'local' or row.get('split') != split:
            raise ValueError(f'Scenario {ident!r} is not a bundled {split} scenario; '
                             f'the loop {"never sees held-out" if split == "dev" else "final step runs held-out only"}')
    return ids


def _scenario_args(sweep_args, ids, epochs):
    return [*sweep_args, '--scenarios', ','.join(ids), '--epochs', str(epochs)]


def _check_arm_scenarios(path, allowed):
    arm = revise._read_json(Path(path))
    seen = set(arm['scenario_ids']) | {row.get('scenario') for row in arm['records']}
    if not seen <= set(allowed):
        raise ValueError('An arm ran scenarios outside its allowed set: ' + ', '.join(sorted(map(str, seen - set(allowed)))))
    return arm


def _scrub(text, forbidden):
    for term in sorted(forbidden, key=len, reverse=True):
        text = text.replace(term, '<scenario>')
    return text


def failure_cause(arm, scenarios, *, measure, allowed, forbidden):
    """Describe the base arm's failed dev runs for the proposer, without any scenario id.

    Only records of ``allowed`` scenarios are used; a held-out record in the arm is refused, not skipped.
    """
    by_id = {item['id']: item for item in scenarios}
    rows = []
    for ident, runs in sorted(records_by_scenario(arm['records']).items()):
        if ident not in allowed:
            raise ValueError(f'The base arm has records outside the loop scenarios: {ident!r}')
        failed = [run for run in runs if (run.get('value') or {}).get(measure) != 1.0]
        if not failed:
            continue
        item = by_id.get(ident, {})
        turns = [turn.get('say') for turn in item.get('turns') or [] if isinstance(turn, dict) and turn.get('say')]
        lines = [f'Case: {item.get("title") or "untitled"} ({len(failed)} of {len(runs)} runs failed)']
        lines += [f'  Owner turn {n}: {say}' for n, say in enumerate(turns, 1)]
        if item.get('good_secretary'):
            lines.append(f'  A good secretary: {item["good_secretary"]}')
        reasons = {}
        for run in failed:
            for failure in run.get('failures') or []:
                key = f'{failure.get("kind")}:{failure.get("name")}: {str(failure.get("reason") or "")[:300]}'
                reasons[key] = reasons.get(key, 0) + 1
        lines += [f'  Failure x{count}: {reason}' for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1])]
        rows.append('\n'.join(lines))
    if not rows:
        return None
    text = ('The current text was run on cases like the ones below; these runs failed. Revise the text so that '
            'the assistant handles this kind of case better in general, not only these cases.\n\n' + '\n\n'.join(rows))
    return _scrub(text, forbidden)[:_CAUSE_LIMIT]


def dev_experience(folder, target_path, allowed, limit=8):
    """Experience records whose comparison used only the loop's dev scenarios (no held-out verdicts)."""
    entries = revise.read_experience(folder, target_path, limit=10 ** 6)
    return [entry for entry in entries
            if isinstance(entry.get('scenario_ids'), list) and set(entry['scenario_ids']) <= set(allowed)][:limit]


class Budget:
    def __init__(self, max_worker_runs):
        if type(max_worker_runs) is not int or max_worker_runs < 1:
            raise ValueError('The worker-run budget must be a positive integer')
        self.limit, self.spent = max_worker_runs, 0

    def allows(self, runs):
        return self.spent + runs <= self.limit

    def charge(self, arm):
        self.spent += int(arm['cost'].get('worker_runs', len(arm['records'])))


def _arm_summary(arm, measure):
    rows = records_by_scenario(arm['records'])
    return {'text_digest': arm['text_digest'], 'cost': arm['cost'], 'run_id': arm.get('run_id'),
            measure: mean(mean((r.get('value') or {}).get(measure) for r in runs) for runs in rows.values())}


def _cost_total(costs):
    total = {}
    for cost in costs:
        for key, value in (cost or {}).items():
            if isinstance(value, (int, float)):
                total[key] = total.get(key, 0) + value
    return total


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temp.replace(path)


def _winner(comparison, measure):
    order = {'general': 0, 'scoped': 1, 'composed': 2}
    preferred = [(label, row) for label, row in comparison['candidates'].items() if row['preferred']]
    if not preferred:
        return None
    return max(preferred, key=lambda item: ((item[1]['partitions']['dev'][measure] or 0),
                                            -order.get(item[0], 9)))[0]


def run_loop(*, target, sweep_args, dev_ids, scenarios, rounds, epochs, max_worker_runs, generate_factory,
             forbidden, out, measure='passed', tolerances, compose=False, min_trials=3, prior=8,
             arm_runner=arm_run.run_arm, clock=_now):
    """Run up to ``rounds`` rounds with no human step between them and write ``loop.json`` after each.

    ``target`` comes from `revise.load_target` (committed text). ``scenarios`` is the loaded scenario set
    (true splits); ``dev_ids`` must all be bundled dev scenarios. ``generate_factory()`` returns the
    proposer transport ``(generate, settings, close)`` for one round.
    """
    if owned_flags(sweep_args):
        raise ValueError('The loop sets these sweep flags itself: ' + ', '.join(owned_flags(sweep_args)))
    if type(rounds) is not int or rounds < 1 or type(epochs) is not int or epochs < min_trials:
        raise ValueError('Rounds must be at least 1 and epochs at least min_trials')
    meta = {item['id']: {'split': item['split'], 'source': item.get('source', 'bundled')} for item in scenarios}
    dev_ids = split_ids(dev_ids, meta, 'dev')
    out = revise._outside_repo(out)
    experience = out / 'experience'
    budget = Budget(max_worker_runs)
    planned_candidates = 3 if compose else 2
    start = dict(target)
    base_text, base_arm_path = target['text'], None
    record = {'schema': LOOP_SCHEMA, 'target_path': target['path'], 'lines': target['lines'],
              'start_digest': target['digest'], 'start_text_file': 'start.txt', 'measure': measure,
              'tolerances': dict(tolerances), 'min_trials': min_trials, 'epochs': epochs, 'rounds_planned': rounds,
              'dev_ids': dev_ids, 'sweep_args': list(sweep_args), 'max_worker_runs': max_worker_runs,
              'compose': compose, 'started_at': clock(), 'rounds': [], 'state': 'running'}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'start.txt').write_text(start['text'], encoding='utf-8', newline='')
    _write(out / 'loop.json', record)
    allowed = set(dev_ids)

    def run(label, folder, row, **kwargs):
        path = arm_runner(_scenario_args(sweep_args, dev_ids, kwargs.pop('epochs')), out=folder / f'{label}.json',
                          base_text=base_text, **kwargs)
        arm = _check_arm_scenarios(path, allowed)
        budget.charge(arm)
        row['cost'] = _cost_total([row.get('cost'), arm['cost']])
        return path, arm

    stop = None
    for number in range(1, rounds + 1):
        folder = out / f'round-{number}'
        view = {'path': target['path'], 'lines': target['lines'], 'text': base_text, 'digest': text_digest(base_text)}
        row = {'round': number, 'base_digest': view['digest'], 'base_text_file': f'round-{number}/base.txt',
               'started_at': clock(), 'arms': {}, 'proposer_calls': [], 'candidates': [], 'cost': {}}
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'base.txt').write_text(base_text, encoding='utf-8', newline='')
        base_runs = 0 if base_arm_path is not None else len(dev_ids) * epochs
        round_runs = base_runs + len(dev_ids) * epochs * planned_candidates * 2
        if not budget.allows(round_runs):
            stop = f'budget: a full round needs up to {round_runs} worker runs, {budget.limit - budget.spent} remain'
            break
        if base_arm_path is None:
            base_arm_path, base_arm = run('base', folder, row, target=target['path'], lines=target['lines'],
                                          epochs=epochs)
        else:
            base_arm = revise._read_json(base_arm_path)
            row['base_reused_from'] = str(base_arm_path)
        row['arms']['base'] = _arm_summary(base_arm, measure)
        record['rounds'].append(row)
        try:
            revise.check_room(base_arm, view, meta=meta, measure=measure, min_trials=min_trials, splits=LOOP_SPLITS)
        except ValueError as exc:
            if not re.match(r'no_(room|detectable_gain)_on_', str(exc)):
                raise
            stop = f'no room: {exc}'
            row.update(outcome='stopped', finished_at=clock())
            break
        cause_text = failure_cause(base_arm, scenarios, measure=measure, allowed=allowed, forbidden=forbidden)
        if cause_text is None:
            row.update(outcome='kept', kept_reason='the base arm has no failed run to learn from', finished_at=clock())
            _write(out / 'loop.json', record)
            continue
        cause = {'kind': 'regression', 'source': f'revise-loop round {number}', 'text': cause_text}
        generate, settings, close = generate_factory()
        try:
            result = asyncio.run(revise.propose(view, cause, generate, forbidden=forbidden, compose=compose,
                                                experience=dev_experience(experience, view['path'], allowed, prior),
                                                limit=prior))
        finally:
            close()
        row['proposer_calls'] = result['calls']
        row['refused'] = result['refused']
        candidates_dir = revise.write_candidates(result, view, cause, folder / 'candidates', settings=settings)
        if not result['candidates']:
            row.update(outcome='kept', kept_reason='the proposer returned no usable candidate', finished_at=clock())
            _write(out / 'loop.json', record)
            continue
        arms = {}
        for candidate in result['candidates']:
            label = candidate['label']
            _, arms[label] = run(label, folder, row, candidate=candidates_dir / f'{label}.txt', epochs=epochs)
        _, retries = run('equal_retries', folder, row, target=target['path'], lines=target['lines'],
                         equal_retries=True, epochs=epochs * len(arms))
        row['arms'].update({label: _arm_summary(arm, measure) for label, arm in arms.items()})
        row['arms']['equal_retries'] = _arm_summary(retries, measure)
        comparison = compare_versioned_text(base_arm, arms, meta=meta, target=measure, tolerances=tolerances,
                                            equal_retries=retries, min_trials=min_trials, splits=LOOP_SPLITS)
        _write(folder / 'comparison.json', comparison)
        entry = revise.experience_entry(candidates_dir, comparison, recorded_at=clock())
        revise.record_experience(entry, experience)
        row['candidates'] = [{'label': label, 'digest': row_['text_digest'], 'text_file': f'round-{number}/candidates/{label}.txt',
                              'summary': next(c['summary'] for c in result['candidates'] if c['label'] == label),
                              'verdict': row_['verdict'], 'reasons': row_['reasons'],
                              f'{measure}_dev': row_['partitions']['dev'][measure]}
                             for label, row_ in comparison['candidates'].items()]
        winner = _winner(comparison, measure)
        if winner is None:
            row.update(outcome='kept', kept_reason='no candidate was preferred: ' + '; '.join(
                f"{c['label']} {c['verdict']} ({', '.join(c['reasons']) or 'no reason'})" for c in row['candidates']))
        else:
            base_text = next(c['text'] for c in result['candidates'] if c['label'] == winner)
            base_arm_path = None  # A promoted text gets a fresh base arm, not its selection run.
            row.update(outcome='promoted', winner=winner, winner_digest=text_digest(base_text))
        row['finished_at'] = clock()
        _write(out / 'loop.json', record)
    else:
        stop = f'rounds: {rounds} rounds ran'
    final_text = base_text
    (out / 'final.txt').write_text(final_text, encoding='utf-8', newline='')
    record.update(state='finished', stop_reason=stop, final_digest=text_digest(final_text), final_text_file='final.txt',
                  worker_runs=budget.spent, finished_at=clock(),
                  cost=_cost_total(row_.get('cost') for row_ in record['rounds']),
                  proposer_calls=sum(len(row_['proposer_calls']) for row_ in record['rounds']))
    _write(out / 'loop.json', record)
    return out / 'loop.json'


def _final_candidate(folder, loop, start_digest, final_text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'final.txt').write_text(final_text, encoding='utf-8', newline='')
    (folder / 'final.json').write_text(json.dumps({
        'schema': 'revision-candidate/1', 'label': 'final', 'scope': 'final', 'target_path': loop['target_path'],
        'lines': loop['lines'], 'base_digest': start_digest, 'candidate_digest': text_digest(final_text),
        'summary': 'final text of the automatic loop', 'parents': []}, indent=2) + '\n', encoding='utf-8')
    return folder / 'final.txt'


def final_heldout(loop_file, *, heldout_ids, scenarios, epochs, bar, max_worker_runs, arm_runner=arm_run.run_arm,
                  clock=_now):
    """After the loop: start text, final text and start-text equal retries on held-out only."""
    loop_file = Path(loop_file)
    loop = revise._read_json(loop_file)
    if loop.get('schema') != LOOP_SCHEMA or loop.get('state') != 'finished':
        raise ValueError('The final step needs a finished revision-loop/1 record')
    out = revise._outside_repo(loop_file.parent)
    meta = {item['id']: {'split': item['split'], 'source': item.get('source', 'bundled')} for item in scenarios}
    heldout_ids = split_ids(heldout_ids, meta, 'heldout')
    if set(heldout_ids) & set(loop['dev_ids']):
        raise ValueError('Held-out scenarios overlap the loop scenarios')
    start_text = (out / loop['start_text_file']).read_text(encoding='utf-8')
    final_text = (out / loop['final_text_file']).read_text(encoding='utf-8')
    if text_digest(start_text) != loop['start_digest'] or text_digest(final_text) != loop['final_digest']:
        raise ValueError('Start or final text does not match the loop record')
    folder = out / 'final'
    result = {'schema': FINAL_SCHEMA, 'loop': str(loop_file), 'heldout_ids': heldout_ids, 'epochs': epochs,
              'bar': bar, 'measure': loop['measure'], 'started_at': clock()}
    if final_text == start_text:
        result.update(outcome='no_revision', reason='the loop promoted no text; nothing to compare', finished_at=clock())
        _write(folder / 'final.json', result)
        return folder / 'final.json'
    runs = len(heldout_ids) * epochs * 3
    if runs > max_worker_runs:
        raise ValueError(f'The final step needs {runs} worker runs; the budget is {max_worker_runs}')
    args = _scenario_args(loop['sweep_args'], heldout_ids, epochs)
    paths = {
        'start': arm_runner(args, target=loop['target_path'], lines=loop['lines'], base_text=start_text,
                            out=folder / 'start.json'),
        'final': arm_runner(args, candidate=_final_candidate(folder / 'candidate', loop, loop['start_digest'],
                                                             final_text), base_text=start_text,
                            out=folder / 'final-arm.json'),
        'equal_retries': arm_runner(args, target=loop['target_path'], lines=loop['lines'], base_text=start_text,
                                    equal_retries=True, out=folder / 'equal_retries.json'),
    }
    arms = {label: _check_arm_scenarios(path, heldout_ids) for label, path in paths.items()}
    comparison = compare_versioned_text(arms['start'], {'final': arms['final']}, meta=meta, target=loop['measure'],
                                        tolerances=loop['tolerances'], equal_retries=arms['equal_retries'],
                                        min_trials=loop['min_trials'], splits=FINAL_SPLITS)
    _write(folder / 'comparison.json', comparison)
    row = comparison['candidates']['final']
    gains = {label: row['against'][label]['heldout'][loop['measure']] for label in ('baseline', 'equal_retries')}
    noise = {label: row['against'][label]['heldout']['noise'] for label in ('baseline', 'equal_retries')}
    met = row['verdict'] == 'preferred' and all(g is not None and g >= bar for g in gains.values())
    result.update(outcome='compared', verdict=row['verdict'], reasons=row['reasons'], gain=gains, noise=noise,
                  bar_met=met, arms={label: _arm_summary(arm, loop['measure']) for label, arm in arms.items()},
                  cost=_cost_total(arm['cost'] for arm in arms.values()), finished_at=clock())
    _write(folder / 'final.json', result)
    return folder / 'final.json'


__all__ = ['run_loop', 'final_heldout', 'failure_cause', 'dev_experience', 'split_ids', 'owned_flags',
           'DECISION_SPLITS']
