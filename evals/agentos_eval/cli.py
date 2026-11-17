"""Small CLI around the Inspect task: seed snapshots, sweeps, reports and the judge budget.

    python -m agentos_eval snapshot --from ~/.local/share/agentos --name owner
    python -m agentos_eval scenarios
    python -m agentos_eval sweep --seed owner --worker codex --instances 3 --epochs 3
    python -m agentos_eval sweep --world appworld --scenarios world-appworld-pay-approved
    python -m agentos_eval report --log <eval log>
    python -m agentos_eval budget

Run from the ``evals`` folder (or with it on ``PYTHONPATH``).  ``sweep`` and
``report`` need the eval extra (``evals/requirements.txt``); a ``--world``
sweep needs no Inspect or judge, only the world's own package. Coverage
validation uses the optional schema-validation extra; the rest is standard library.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import scenarios as scenario_module
from .budget import JudgeBudget
from .paths import LIVE_DATA, REPO, eval_home
from .report import records_from_samples, world_records, write_report
from .sandbox import WORLD_WORKER, run_id, snapshot_seed

#: Where a judge key may come from at run time (never stored by this tool).
KEY_ENV = {'openai': 'OPENAI_API_KEY', 'anthropic': 'ANTHROPIC_API_KEY', 'google': 'GOOGLE_API_KEY'}
KEYCHAIN_SERVICE = 'agentos-evals'

# #868: fresh qualification took ~410 s on 2026-11-15; allow about 2× margin
# for a loaded machine and concurrent slots.
JUDGMENT_TIMEOUT = 900


def keychain_secret(account, service=KEYCHAIN_SERVICE, runner=subprocess.run):
    """Read one generic password from the login Keychain; '' when absent.  Never logged."""
    try:
        done = runner(['/usr/bin/security', 'find-generic-password', '-s', service, '-a', account, '-w'],
                      capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return done.stdout.strip() if done.returncode == 0 else ''


def ensure_judge_key(judge, environ=None, keychain=keychain_secret):
    """Make the judge provider's key available to this process only; returns where it came from."""
    environ = os.environ if environ is None else environ
    if not judge or judge == 'none':
        return 'none'
    provider = judge.split('/', 1)[0]
    name = KEY_ENV.get(provider)
    if not name:
        return 'provider-default'
    if environ.get(name):
        return 'environment'
    value = keychain(provider)
    if value:
        environ[name] = value
        return 'keychain'
    raise SystemExit(f'No judge key: set {name} or store it with '
                     f'`security add-generic-password -s {KEYCHAIN_SERVICE} -a {provider} -w` '
                     f'(or pass --judge none for checks only).')


def selected_judge(value='', environ=None):
    environ = os.environ if environ is None else environ
    return (value or environ.get('AGENTOS_EVAL_JUDGE_MODEL') or 'none').strip() or 'none'


def task_judge_settings(judge='', judge_notice=True):
    """Inspect task wiring without importing Inspect or loading credentials."""
    judge = selected_judge(judge)
    scorer_judge = 'none' if judge == 'none' else 'grader'
    model_roles = None if judge == 'none' else {'grader': judge}
    announce = str(judge_notice).lower() not in ('0', 'false', 'no')
    return judge, scorer_judge, model_roles, announce


def judge_notice(judge, key_loader=None):
    """Resolve credentials only after opt-in, and disclose the effective budget once."""
    if judge == 'none':
        print('Checks only; eval judge is off. Enable with --judge <model> or AGENTOS_EVAL_JUDGE_MODEL=<model>.')
        return 'none'
    source = (key_loader or ensure_judge_key)(judge)
    budget = JudgeBudget.from_environ(eval_home() / 'judge-budget.json')
    placeholders = [name for name in ('IN', 'OUT') if f'AGENTOS_EVAL_JUDGE_PRICE_{name}' not in os.environ]
    suffix = f"; placeholder default prices: {', '.join(placeholders)} (2/8 USD per million)" if placeholders else ''
    print(f"Eval judge: {judge}; key source: {'Keychain' if source == 'keychain' else source}; "
          f"daily cap: USD {budget.cap:g}; prices per million tokens: input USD {budget.price_in:g}, "
          f"output USD {budget.price_out:g}{suffix}.")
    return source


def judge_totals_line(judge, records):
    from .budget import judge_totals
    if judge == 'none':
        print('Eval judge totals: off (checks only).')
        return
    totals = judge_totals(records)
    print(f"Eval judge totals: {judge}; calls: {totals['calls']}; input tokens: {totals['input_tokens']} "
          f"(uncached {totals['input_tokens_uncached']}, cache write {totals['input_tokens_cache_write']}, "
          f"cache read {totals['input_tokens_cache_read']}); "
          f"output tokens: {totals['output_tokens']}; settled USD: {totals['usd']}.")


def resolve_seed(value):
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.exists() and '/' not in value:
        path = eval_home() / 'seeds' / value
    if path.resolve() == LIVE_DATA.resolve():
        raise SystemExit('Use `snapshot` to make a seed; the live data folder is never used directly.')
    return path


def git_head(repo=REPO):
    try:
        return subprocess.run(['git', '-C', str(repo), 'rev-parse', '--short', 'HEAD'], capture_output=True,
                              text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cmd_snapshot(args):
    target = eval_home() / 'seeds' / args.name
    summary = snapshot_seed(Path(args.source).expanduser(), target, overwrite=args.overwrite)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def cmd_scenarios(args):
    loaded = scenario_module.load(include_local=not args.no_local, split=args.split or None, world=args.world or None)
    for scenario in loaded:
        # Owner-local scenarios may hold real conversation text: list their ids only.
        title = scenario['title'] if scenario['source'] == 'bundled' else '(local)'
        print(f"{scenario['id']:<44} {scenario['split']:<8} {len(scenario['turns'])} turns  {title}")
    print(f'{len(loaded)} scenarios')


def cmd_budget(args):
    print(json.dumps(JudgeBudget.from_environ(eval_home() / 'judge-budget.json').status(), indent=1))


def _write(log, run, folder=None):
    records = records_from_samples(log.samples)
    stopped = usage_limit_stops(log)
    if stopped:
        run = {**run, 'stopped': stopped}  # #887: an incomplete sweep is never a later trend baseline
    json_path, md_path, report = write_report(records, folder or eval_home() / 'reports', run)
    print(md_path.read_text(encoding='utf-8'))
    print(f'report: {json_path}\nlog: {log.location}')
    if stopped:
        print(f"STOPPED: a subscription usage limit was hit ({stopped['tripped_by']}); "
              f"{stopped['skipped']} runs were not started. Results cover only the runs before it.")
    if run.get('continuity'):
        from . import continuity
        result = continuity.build_report(records, run, run['continuity'])
        _, path = continuity.write_report(result, folder or eval_home() / 'reports')
        print(path)
    return report


def run_world_sweep(args, run_scenario=None, pool_factory=None, folder=None):
    """``sweep --world``: each scenario × epoch in a fresh world and a fresh sandbox, on Codex; no judge (#484)."""
    from .runner import USAGE_LIMIT, run_world_scenario
    from .sandbox import SandboxPool
    from .worlds import EVIDENCE_CLASS
    notice = 'World sweep is checks only; the eval judge is not used.'
    if selected_judge(getattr(args, 'judge', '')) != 'none':
        notice += ' The named judge is ignored for world sweeps.'
    print(notice)
    if args.worker != WORLD_WORKER:
        raise SystemExit('A world sweep runs on the Codex worker only.')
    ids = [item for item in args.scenarios.split(',') if item]
    loaded = scenario_module.load(include_local=not args.no_local, split=args.split or None, ids=ids or None,
                                  world=args.world)
    if not loaded:
        raise SystemExit(f'No {args.world} world scenarios to run.')
    seed = resolve_seed(args.seed)
    run = {'id': run_id(), 'started': time.time(), 'git_head': git_head(), 'worker': WORLD_WORKER,
           'instances': args.instances, 'epochs': args.epochs, 'judge': 'none', 'world': args.world,
           'evidence_class': EVIDENCE_CLASS, 'seed': seed.name if seed else 'fresh',
           'judgment_timeout': getattr(args, 'judgment_timeout', JUDGMENT_TIMEOUT)}
    pool = (pool_factory or SandboxPool)(size=args.instances, seed=seed, keep=args.keep_sandboxes)
    run_scenario = run_scenario or run_world_scenario
    jobs = [(scenario, epoch) for epoch in range(1, args.epochs + 1) for scenario in loaded][:args.limit]
    run['scenario_ids'] = sorted({scenario['id'] for scenario, _ in jobs})
    tripped = threading.Event()

    def one(job):
        # #887: past a usage limit every run fails the same way; start no more.
        if tripped.is_set():
            return None
        record = run_scenario(job[0], pool, turn_timeout=args.turn_timeout,
                              judgment_timeout=run['judgment_timeout'])
        if any(turn.get('failure_class') == USAGE_LIMIT for turn in record.get('turns') or ()):
            tripped.set()
        return job[0], record, job[1]

    with ThreadPoolExecutor(max_workers=args.instances) as executor:
        done = [item for item in executor.map(one, jobs) if item is not None]
    if tripped.is_set():
        run['stopped'] = {'tripped_by': 'world sweep', 'skipped': len(jobs) - len(done)}
    json_path, md_path, report = write_report(world_records(done), folder or eval_home() / 'reports', run)
    print(md_path.read_text(encoding='utf-8'))
    print(f'report: {json_path}')
    judge_totals_line('none', [])
    if tripped.is_set():
        return 2
    return 1 if report['trend']['regressions'] else 0


def cmd_sweep(args, folder=None, inspect_eval=None, task_factory=None, key_loader=None):
    if args.world:
        return run_world_sweep(args)
    if inspect_eval is None or task_factory is None:
        from .task import agentos_secretary, inspect_eval as default_eval
        inspect_eval = inspect_eval or default_eval
        task_factory = task_factory or agentos_secretary
    judge = selected_judge(args.judge)
    if args.cap_usd is not None:
        os.environ['AGENTOS_EVAL_JUDGE_CAP_USD'] = str(args.cap_usd)
    key_source = judge_notice(judge, key_loader)
    seed = resolve_seed(args.seed)
    run = {'id': run_id(), 'started': time.time(), 'git_head': git_head(), 'worker': args.worker,
           'instances': args.instances, 'epochs': args.epochs, 'judge': judge, 'judge_key': key_source,
           'judgment_timeout': getattr(args, 'judgment_timeout', JUDGMENT_TIMEOUT),
           'seed': seed.name if seed else 'fresh'}
    if getattr(args, 'continuity', None):
        run['continuity'] = args.continuity
        run['observation_version'] = args.continuity['observation_version']
    task = task_factory(scenarios=args.scenarios, split=args.split, worker=args.worker,
                             instances=args.instances, seed=str(seed or ''), judge=judge,
                             turn_timeout=args.turn_timeout, judgment_timeout=run['judgment_timeout'],
                             include_local=not args.no_local,
                             keep_sandboxes=args.keep_sandboxes, measures=getattr(args, 'measures', ''),
                             sample_limit=args.limit, judge_notice=False)
    if 'scenario_ids' in (getattr(task, 'metadata', None) or {}):
        run['scenario_ids'] = task.metadata['scenario_ids']
    if getattr(args, 'continuity', None) and getattr(task, 'metadata', None) is not None:
        # Inspect merges Task.metadata with eval(metadata=...), and the task's
        # seed bool otherwise replaces the sweep's canonical 'fresh' value.
        task.metadata.update(run)
    logs = inspect_eval(task, epochs=args.epochs, max_samples=args.instances, limit=args.limit,
                        log_dir=str(eval_home() / 'logs'), display=args.display, fail_on_error=False,
                        tags=['agentos-eval', args.worker], metadata=run)
    try:
        report = _write(logs[0], run, folder)
    finally:
        judge_totals_line(judge, records_from_samples(getattr(logs[0], 'samples', None)))
    if usage_limit_stops(logs[0]):
        return 2
    return 1 if report['trend']['regressions'] else 0


def usage_limit_stops(log):
    """``{'tripped_by', 'skipped'}`` when the sweep stopped on a usage limit (#887), else ``None``."""
    summary = getattr(getattr(log, 'results', None), 'early_stopping', None)
    if summary is None or not (summary.metadata or {}).get('tripped_by'):
        return None
    return {'tripped_by': summary.metadata['tripped_by'], 'skipped': len(summary.early_stops)}


def read_log(path):
    from .task import read_eval_log
    return read_eval_log(path)


def continuity_run_from_log(log):
    """Recover C2 provenance without consulting today's settings or clock."""
    from .continuity import normalize_run
    run = dict(log.eval.metadata or {})
    if run.get('started') is None:
        created = getattr(log.eval, 'created', None)
        try:
            moment = datetime.fromisoformat(created) if isinstance(created, str) else created
            if isinstance(moment, datetime) and moment.tzinfo is not None:
                run['started'] = moment.timestamp()
                run['started_source'] = 'inspect.eval.created'
        except ValueError:
            pass  # Missing/invalid historical timestamps stay unavailable.
    if run.get('instances') is None:
        maximum = getattr(getattr(log.eval, 'config', None), 'max_samples', None)
        if type(maximum) is int and maximum > 0:
            run['instances'] = maximum
    stopped = usage_limit_stops(log)
    if stopped:
        run['stopped'] = stopped
    return normalize_run(run)


def cmd_report(args):
    log = read_log(args.log)
    run = dict((log.eval.metadata or {}))
    if run.get('continuity'):
        run = continuity_run_from_log(log)
    run.setdefault('id', run_id())
    _write(log, run)


def cmd_p1_report(args):
    from inspect_ai.log import read_eval_log
    from .p1_report import compare_arms, render_markdown, combine_arm_logs
    arms = {}
    seen_identities = {}
    for value in args.arm:
        label, separator, path = value.partition('=')
        if not separator or not label or not path or label in arms:
            raise SystemExit('--arm must be a unique label=<log.eval>')
        paths = [part.strip() for part in path.split(',')]
        if any(not part for part in paths):
            raise SystemExit('--arm must be label=<log.eval>[,<log.eval>...] with no empty paths')
        logs = [read_eval_log(part) for part in paths]
        try:
            arms[label] = combine_arm_logs(logs, label, getattr(args, 'scenarios', None),
                                           paths=paths, seen_identities=seen_identities)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    try:
        report = compare_arms(arms)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    markdown = render_markdown(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.md:
        Path(args.md).write_text(markdown, encoding='utf-8')
    print(markdown)


def cmd_judgments(args):
    from . import judgments
    if args.action == 'plan':
        print(json.dumps(judgments.plan(judgments.load_cases(args.cases)), indent=2))
    elif args.action == 'compare':
        print(judgments.compare_markdown(judgments.compare(
            json.loads(Path(args.baseline).read_text()), json.loads(Path(args.head).read_text()))))
    else:
        judgments.live_run(args)


def run_continuity(args, sweep=None, folder=None):
    """C2 fixes selection/freshness, then delegates all execution to cmd_sweep."""
    from . import continuity
    if getattr(args, 'seed', None) is not None or os.environ.get('AGENTOS_EVAL_SEED'):
        raise SystemExit('C2 refuses --seed/AGENTOS_EVAL_SEED: use a fresh data folder.')
    judge = selected_judge(args.judge)
    if judge == 'none':
        raise SystemExit('C2 refuses checks-only runs: judged measures need --judge <model> or AGENTOS_EVAL_JUDGE_MODEL=<model> (none is off).')
    if args.epochs < 1 or args.instances < 1:
        raise SystemExit('C2 epochs and instances must be positive.')
    loaded = [s for s in scenario_module.load(include_local=False) if 'exp-continuity' in s['tags']]
    if not loaded:
        raise SystemExit('No bundled C2 scenarios.')
    args.seed, args.no_local, args.world, args.split = '', True, '', ''
    args.limit, args.keep_sandboxes = None, False
    args.judgment_timeout = getattr(args, 'judgment_timeout', JUDGMENT_TIMEOUT)
    args.judge, args.scenarios, args.measures = judge, ','.join(s['id'] for s in loaded), str(args.rubric)
    args.continuity = continuity.fixture_support(loaded, args.rubric)
    return (sweep or cmd_sweep)(args, **({'folder': folder} if folder is not None else {}))


def cmd_continuity(args):
    from . import continuity
    if args.action == 'run':
        return run_continuity(args)
    if args.action == 'report':
        log = read_log(args.log)
        run = continuity_run_from_log(log)
        if not run.get('continuity'):
            raise ValueError('Log lacks frozen C2 fixture provenance; run continuity run first.')
        result = continuity.build_report(records_from_samples(log.samples), run, run['continuity'], args.labels)
        _, path = continuity.write_report(result, eval_home() / 'reports')
        print(path)
    elif args.action == 'compare':
        reports = [json.loads(Path(p).read_text()) for p in (args.baseline, args.candidate)]
        if args.labels:
            # Each file is strict against its arm. Reject unknown or ambiguous run ids.
            by_run = {r['live_evidence']['id']: r for r in reports}
            if len(by_run) != 2:
                raise ValueError('Comparison labels require distinct run ids')
            combined = {key: {'version': 'initiative-labels-v1', 'rubric': r['fixture_support']['rubric_version'],
                              'labels': []} for key, r in by_run.items()}
            for path in args.labels:
                data = json.loads(Path(path).read_text())
                if (not isinstance(data, dict) or not {'version', 'rubric', 'labels'} <= set(data)
                        or set(data) - {'version', 'rubric', 'labels', '_note', 'not_labellable', 'references'}
                        or data['version'] != 'initiative-labels-v1' or not isinstance(data['labels'], list)
                        or any(data['rubric'] != r['fixture_support']['rubric_version'] for r in reports)):
                    raise ValueError('Invalid comparison labels file')
                for label in data['labels']:
                    if not isinstance(label, dict) or label.get('run_id') not in by_run:
                        raise ValueError('Label sample does not exist in either report')
                    combined[label['run_id']]['labels'].append(label)
            reports = [continuity.with_labels(r, combined[r['live_evidence']['id']])
                       if combined[r['live_evidence']['id']]['labels'] else r for r in reports]
        result = continuity.compare(*reports, allow_partial=args.allow_partial)
        markdown = continuity.compare_markdown(result)
        base = Path(args.candidate).with_suffix('')
        jp, mp = Path(str(base) + '.compare.json'), Path(str(base) + '.compare.md')
        jp.write_text(json.dumps(result, indent=2) + '\n')
        mp.write_text(markdown)
        print(markdown)
        print(mp)
    else:
        result = continuity.labels_template(json.loads(Path(args.report).read_text()), args.ids)
        print(json.dumps(result, indent=2))


def cmd_recall(args):
    import shlex
    from . import recall_pairs, recall_report

    if args.runs < 1:
        raise SystemExit('--runs must be a positive integer')
    names = list(dict.fromkeys([args.baseline, *(args.arm or [])]))
    if (args.phraser_cmd is not None or args.phrasings is not None) and 'phrasing' not in names:
        raise SystemExit('--phraser-cmd and --phrasings require --arm phrasing or --baseline phrasing')
    folder = (args.out or eval_home() / 'reports').expanduser().resolve()
    if folder.is_relative_to(REPO.resolve()):
        raise SystemExit('Recall reports belong outside the repository; choose --out or AGENTOS_EVAL_HOME')
    data = recall_pairs.load(args.fixture)
    phraser = None
    if args.phraser_cmd:
        phraser = recall_report.command_phraser(shlex.split(args.phraser_cmd))
    elif args.phrasings:
        phraser = recall_report.replay_phraser(json.loads(args.phrasings.read_text(encoding='utf-8')), data=data)
    # Resolve everything before running any arm (especially before an opt-in model command).
    arms = {name: recall_report.resolve_arm(name, phraser=phraser) for name in names}
    results = {name: recall_report.run_arm(data, arm, args.runs) for name, arm in arms.items()}
    result = recall_report.report(data, results, baseline=args.baseline)
    markdown = recall_report.report_markdown(result)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ('recall-' + run_id())
    target.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    target.with_suffix('.md').write_text(markdown, encoding='utf-8')
    print(markdown)
    print(f"report: {target.with_suffix('.json')}")
    return 0


def cmd_coverage(args):
    from .coverage import bundled_scenarios, coverage_markdown, load_map, validate
    try:
        cmap = load_map()
        loaded = bundled_scenarios(cmap.root)
        problems = validate(cmap, loaded)
    except (OSError, ValueError) as exc:
        print(f'Coverage map: {exc}', file=sys.stderr)
        return 1
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        return 1
    markdown = coverage_markdown(cmap, loaded, heading_level=1)
    if args.write:
        args.write.write_text(markdown, encoding='utf-8')
    if args.check:
        print('Task coverage map valid.')
    elif not args.write:
        print(markdown, end='')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='agentos_eval', description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    snap = sub.add_parser('snapshot', help='make a sanitized seed from an Still data folder')
    snap.add_argument('--from', dest='source', default=str(LIVE_DATA))
    snap.add_argument('--name', default='owner')
    snap.add_argument('--overwrite', action='store_true')
    listing = sub.add_parser('scenarios', help='list scenarios')
    listing.add_argument('--split', choices=scenario_module.SPLITS)
    listing.add_argument('--no-local', action='store_true')
    listing.add_argument('--world', default='', help='list the world scenarios of this world instead')
    sweep = sub.add_parser('sweep', help='run scenarios on fresh sandboxes and write the trend report')
    sweep.add_argument('--worker', choices=('codex', 'claude-code', 'both'), default='codex')
    sweep.add_argument('--instances', type=int, default=3)
    sweep.add_argument('--epochs', type=int, default=1)
    sweep.add_argument('--seed', default=os.environ.get('AGENTOS_EVAL_SEED', ''),
                       help='seed name under <eval home>/seeds or a path; empty = fresh data folder (Codex only)')
    sweep.add_argument('--scenarios', default='', help='comma-separated scenario ids')
    sweep.add_argument('--split', choices=scenario_module.SPLITS)
    sweep.add_argument('--no-local', action='store_true', help='bundled synthetic scenarios only')
    sweep.add_argument('--judge', default='', help="Inspect model name, e.g. openai/gpt-6.1-terra, or 'none'")
    sweep.add_argument('--cap-usd', type=float, default=None, help='daily judge spend cap (default 10)')
    sweep.add_argument('--turn-timeout', type=int, default=900)
    sweep.add_argument('--judgment-timeout', type=int, default=JUDGMENT_TIMEOUT, help='seconds to wait for Judgment AI qualification')
    sweep.add_argument('--limit', type=int, default=None)
    sweep.add_argument('--keep-sandboxes', action='store_true')
    sweep.add_argument('--display', default='plain')
    sweep.add_argument('--world', default='', help='run the world scenarios of this world (e.g. appworld), on Codex')
    rep = sub.add_parser('report', help='rebuild the trend report from an Inspect log')
    rep.add_argument('--log', required=True)
    p1 = sub.add_parser('p1-report', help='compare P1 read-concurrency arms from Inspect logs')
    p1.add_argument('--arm', action='append', required=True, help='label=<log.eval>[,<log.eval>...] in epoch order (two arms)')
    p1.add_argument('--scenarios', default=None, help='declared comma-separated scenario ids for older logs')
    p1.add_argument('--json')
    p1.add_argument('--md')
    coverage = sub.add_parser('coverage', help='validate or render the task coverage map (no live runs)')
    coverage.add_argument('--check', action='store_true')
    coverage.add_argument('--write', type=Path)
    sub.add_parser('budget', help="show today's judge spend")
    recall = sub.add_parser('recall', help='run R1 paired recall arms and write split-local reports')
    recall.add_argument('--arm', action='append', help='built-in name or module:factory (repeatable)')
    recall.add_argument('--baseline', default='lexical', help='reference arm name (always run; default: lexical)')
    recall.add_argument('--runs', type=int, default=3, help='repetitions for sampling arms')
    from .recall_report import DEFAULT_FIXTURE
    recall.add_argument('--fixture', type=Path, default=DEFAULT_FIXTURE)
    recall.add_argument('--out', type=Path)
    phrasers = recall.add_mutually_exclusive_group()
    phrasers.add_argument('--phraser-cmd', help='explicit stdin/stdout worker command for the phrasing arm')
    phrasers.add_argument('--phrasings', type=Path, help='recorded run -> case id -> words JSON for replay')
    # Explicit live recorder is separate from every default/model-free arm.
    for command in ('recall-record','recall-regression'):
        regression = sub.add_parser(command)
        regression.add_argument('--fixture',type=Path,default=REPO/'evals/recall/recall-pairs-v4.json')
        regression.add_argument('--additions',type=Path,default=REPO/'evals/recall/recall-gate-dev-1.json')
        regression.add_argument('--out',type=Path,required=True)
        if command=='recall-record':
            from .paths import SRC
            regression.add_argument('--src',type=Path,default=SRC)
            regression.add_argument('--seed')
            regression.add_argument('--max-calls',type=int)
            regression.add_argument('--requalify',action='store_true')
            regression.add_argument('--terms',type=Path,help='reuse a complete terms recording')
            regression.add_argument('--resume',type=Path,help='continue incomplete plans; terms default to its sibling terms.json')
            regression.add_argument('--heldout-runs',type=int,default=3)
        else:
            regression.add_argument('--terms',type=Path,required=True)
            regression.add_argument('--plans',type=Path,required=True)
    judgments = sub.add_parser('judgments', help='measure shipped judgments (#42)')
    actions = judgments.add_subparsers(dest='action', required=True)
    from .judgments import DEFAULT_CASES
    planning = actions.add_parser('plan', help='print maximum calls without calling a model')
    planning.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    running = actions.add_parser('run', help='opt-in live route measurement on a sanitized seed')
    running.add_argument('--seed', required=True)
    running.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    from .paths import SRC
    running.add_argument('--src', type=Path, default=SRC)
    running.add_argument('--roles', default='')
    running.add_argument('--max-calls', type=int)
    running.add_argument('--trials', type=int, default=1)
    running.add_argument('--model', help='pin the sandbox CLI judgment model without fallback; use --requalify to qualify it')
    running.add_argument('--requalify', action='store_true',
                         help='run the shipped CLI update qualification first, sharing --max-calls with measured cases')
    running.add_argument('--keep-text', action='store_true')
    running.add_argument('--out', type=Path)
    comparison = actions.add_parser('compare')
    comparison.add_argument('baseline')
    comparison.add_argument('head')
    from .continuity import DEFAULT_RUBRIC
    c2 = sub.add_parser('continuity', help='C2 judged continuity experiment')
    c2actions = c2.add_subparsers(dest='action', required=True)
    c2run = c2actions.add_parser('run')
    c2run.add_argument('--worker', choices=('codex',), default='codex')
    c2run.add_argument('--epochs', type=int, default=3)
    c2run.add_argument('--cap-usd', type=float)
    c2run.add_argument('--judge', default='')
    c2run.add_argument('--instances', type=int, default=3)
    c2run.add_argument('--turn-timeout', type=int, default=900)
    c2run.add_argument('--judgment-timeout', type=int, default=JUDGMENT_TIMEOUT, help='seconds to wait for Judgment AI qualification')
    c2run.add_argument('--display', default='plain')
    c2run.add_argument('--rubric', type=Path, default=DEFAULT_RUBRIC)
    c2run.add_argument('--seed', default=None, help='refused: C2 always uses fresh data')
    c2report = c2actions.add_parser('report')
    c2report.add_argument('--log', required=True)
    c2report.add_argument('--labels', type=Path)
    c2compare = c2actions.add_parser('compare')
    c2compare.add_argument('baseline')
    c2compare.add_argument('candidate')
    c2compare.add_argument('--labels', type=Path, action='append', default=[])
    c2compare.add_argument('--allow-partial', action='store_true',
                           help='allow missing measures from unavailable Judgment AI; verdict cannot be better')
    c2labels = c2actions.add_parser('labels-template')
    c2labels.add_argument('report')
    c2labels.add_argument('ids', nargs='*', help='additional scenario ids already in the report')
    args = parser.parse_args(argv)
    if args.command=='recall-record':
        from .recall_record import record
        record(args)
        return 0
    if args.command=='recall-regression':
        from .recall_regression_report import command
        command(args)
        return 0
    handler = {'snapshot': cmd_snapshot, 'scenarios': cmd_scenarios, 'sweep': cmd_sweep, 'report': cmd_report,
               'p1-report': cmd_p1_report, 'budget': cmd_budget, 'judgments': cmd_judgments, 'continuity': cmd_continuity,
               'coverage': cmd_coverage, 'recall': cmd_recall}[args.command]
    return handler(args) or 0


if __name__ == '__main__':
    sys.exit(main())
