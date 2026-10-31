"""Small CLI around the Inspect task: seed snapshots, sweeps, reports and the judge budget.

    python -m agentos_eval snapshot --from ~/.local/share/agentos --name owner
    python -m agentos_eval scenarios
    python -m agentos_eval sweep --seed owner --worker codex --instances 3 --epochs 3
    python -m agentos_eval sweep --world appworld --scenarios world-appworld-pay-approved
    python -m agentos_eval report --log <eval log>
    python -m agentos_eval budget

Run from the ``evals`` folder (or with it on ``PYTHONPATH``).  ``sweep`` and
``report`` need the eval extra (``evals/requirements.txt``); a ``--world``
sweep needs no Inspect or judge, only the world's own package; the rest is
standard library.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import scenarios as scenario_module
from .budget import JudgeBudget
from .paths import LIVE_DATA, REPO, eval_home
from .report import records_from_samples, world_records, write_report
from .sandbox import WORLD_WORKER, run_id, snapshot_seed

#: Where a judge key may come from at run time (never stored by this tool).
KEY_ENV = {'openai': 'OPENAI_API_KEY', 'anthropic': 'ANTHROPIC_API_KEY', 'google': 'GOOGLE_API_KEY'}
KEYCHAIN_SERVICE = 'agentos-evals'


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
    if judge == 'none':
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


def resolve_seed(value):
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.exists() and '/' not in value:
        path = eval_home() / 'seeds' / value
    if path.resolve() == LIVE_DATA.resolve():
        raise SystemExit('Use `snapshot` to make a seed; the live data folder is never used directly.')
    return path


def git_head():
    try:
        return subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--short', 'HEAD'], capture_output=True,
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


def _write(log, run):
    records = records_from_samples(log.samples)
    stopped = usage_limit_stops(log)
    if stopped:
        run = {**run, 'stopped': stopped}  # #887: an incomplete sweep is never a later trend baseline
    json_path, md_path, report = write_report(records, eval_home() / 'reports', run)
    print(md_path.read_text(encoding='utf-8'))
    print(f'report: {json_path}\nlog: {log.location}')
    if stopped:
        print(f"STOPPED: a subscription usage limit was hit ({stopped['tripped_by']}); "
              f"{stopped['skipped']} runs were not started. Results cover only the runs before it.")
    return report


def run_world_sweep(args, run_scenario=None, pool_factory=None, folder=None):
    """``sweep --world``: each scenario × epoch in a fresh world and a fresh sandbox, on Codex; no judge (#484)."""
    from .runner import USAGE_LIMIT, run_world_scenario
    from .sandbox import SandboxPool
    from .worlds import EVIDENCE_CLASS
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
           'evidence_class': EVIDENCE_CLASS, 'seed': seed.name if seed else 'fresh'}
    pool = (pool_factory or SandboxPool)(size=args.instances, seed=seed, keep=args.keep_sandboxes)
    run_scenario = run_scenario or run_world_scenario
    jobs = [(scenario, epoch) for epoch in range(1, args.epochs + 1) for scenario in loaded][:args.limit]
    tripped = threading.Event()

    def one(job):
        # #887: past a usage limit every run fails the same way; start no more.
        if tripped.is_set():
            return None
        record = run_scenario(job[0], pool, turn_timeout=args.turn_timeout)
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
    if tripped.is_set():
        return 2
    return 1 if report['trend']['regressions'] else 0


def cmd_sweep(args):
    if args.world:
        return run_world_sweep(args)
    from inspect_ai import eval as inspect_eval  # the eval extra
    from .task import agentos_secretary
    judge = args.judge or os.environ.get('AGENTOS_EVAL_JUDGE_MODEL') or 'openai/gpt-6.1-terra'
    key_source = ensure_judge_key(judge)
    if args.cap_usd is not None:
        os.environ['AGENTOS_EVAL_JUDGE_CAP_USD'] = str(args.cap_usd)
    seed = resolve_seed(args.seed)
    run = {'id': run_id(), 'started': time.time(), 'git_head': git_head(), 'worker': args.worker,
           'instances': args.instances, 'epochs': args.epochs, 'judge': judge, 'judge_key': key_source,
           'seed': seed.name if seed else 'fresh'}
    task = agentos_secretary(scenarios=args.scenarios, split=args.split, worker=args.worker,
                             instances=args.instances, seed=str(seed or ''), judge=judge,
                             turn_timeout=args.turn_timeout, include_local=not args.no_local,
                             keep_sandboxes=args.keep_sandboxes)
    logs = inspect_eval(task, epochs=args.epochs, max_samples=args.instances, limit=args.limit,
                        log_dir=str(eval_home() / 'logs'), display=args.display, fail_on_error=False,
                        tags=['agentos-eval', args.worker], metadata=run)
    report = _write(logs[0], run)
    if usage_limit_stops(logs[0]):
        return 2
    return 1 if report['trend']['regressions'] else 0


def usage_limit_stops(log):
    """``{'tripped_by', 'skipped'}`` when the sweep stopped on a usage limit (#887), else ``None``."""
    summary = getattr(getattr(log, 'results', None), 'early_stopping', None)
    if summary is None or not (summary.metadata or {}).get('tripped_by'):
        return None
    return {'tripped_by': summary.metadata['tripped_by'], 'skipped': len(summary.early_stops)}


def cmd_report(args):
    from inspect_ai.log import read_eval_log
    log = read_eval_log(args.log)
    run = dict((log.eval.metadata or {}))
    run.setdefault('id', run_id())
    _write(log, run)


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
    sweep.add_argument('--limit', type=int, default=None)
    sweep.add_argument('--keep-sandboxes', action='store_true')
    sweep.add_argument('--display', default='plain')
    sweep.add_argument('--world', default='', help='run the world scenarios of this world (e.g. appworld), on Codex')
    rep = sub.add_parser('report', help='rebuild the trend report from an Inspect log')
    rep.add_argument('--log', required=True)
    sub.add_parser('budget', help="show today's judge spend")
    args = parser.parse_args(argv)
    handler = {'snapshot': cmd_snapshot, 'scenarios': cmd_scenarios, 'sweep': cmd_sweep, 'report': cmd_report,
               'budget': cmd_budget}[args.command]
    return handler(args) or 0


if __name__ == '__main__':
    sys.exit(main())
