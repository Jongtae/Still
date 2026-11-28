"""Adopt git archive for a clean committed tree, as #1295 did by hand.

Use stdlib tarfile, tempfile and subprocess, the existing sweep entry point,
and the compare_versioned_text arm shape.
"""
import json
import os
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory, TemporaryFile

from . import revise
from .arm_compare import _finite_number, text_digest
from .budget import judge_totals
from .paths import REPO, eval_home


def _read_records(location):
    from .cli import read_log
    from .report import records_from_samples
    return records_from_samples(read_log(location).samples)


def run_sweep(copy_root, sweep_args, *, read_records=_read_records):
    """Stream a sweep from the copy and read its report and log."""
    root = Path(copy_root).resolve()
    cwd = root / 'evals'
    home = eval_home().resolve()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, (root / 'src', cwd))),
               AGENTOS_EVAL_HOME=str(home), GIT_CEILING_DIRECTORIES=str(root.parent),
               PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-m', 'agentos_eval', 'sweep', *sweep_args]
    locations = {}
    with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, text=True) as child:
        try:
            for line in child.stdout:
                print(line, end='', flush=True)
                key, separator, location = line.rstrip('\r\n').partition(': ')
                if separator and key in ('report', 'log'):
                    locations[key] = location
            exit_code = child.wait()
        except BaseException:
            child.kill()
            child.wait()
            raise
    report = None
    if locations.get('report'):
        source = cwd / locations['report']
        folder = home / 'arm-reports'
        folder.mkdir(parents=True, exist_ok=True)
        report = source.replace(folder / source.name)
        markdown = source.with_suffix('.md')
        if markdown.is_file():
            markdown.replace(folder / markdown.name)
    if report is None or not locations.get('log'):
        raise ValueError(f'Sweep printed no report or log (exit code {exit_code})')
    run = revise._read_json(report)['run']
    return {'exit_code': exit_code, 'run': run, 'records': read_records(locations['log'])}


def _outside_repo(path, repo):
    path = revise._outside_repo(path)
    if path.is_relative_to(repo):
        raise ValueError('Revision files must be written outside the repository')
    return path


def _splice(copy, target, replacement, expected):
    path = target['path']
    file = copy / path  # load_target already resolved and checked this path.
    text = replacement
    if target['lines'] is not None:
        first, last = target['lines']
        rows = revise.load_target(path, repo=copy)['text'].splitlines(keepends=True)
        if last < len(rows) and not replacement.endswith('\n'):
            raise ValueError('A replacement before more lines must end with a newline')
        text = ''.join(rows[:first - 1]) + replacement + ''.join(rows[last:])
    file.write_text(text, encoding='utf-8', newline='')
    reread = revise.load_target(path, repo=copy)['text']
    if target['lines'] is not None:
        end = first - 1 + len(replacement.splitlines(keepends=True))
        reread = ''.join(reread.splitlines(keepends=True)[first - 1:end])
    digest = text_digest(reread)
    if digest != expected:
        raise ValueError('Spliced text does not match the candidate digest')
    return digest


def _settings(run, records):
    settings = {'worker': run['worker'], 'model': 'unrecorded'}
    pins = [row['worker_pin'] for row in records if row.get('worker_pin')]
    engines = pins or [engine for row in records for work in (row.get('work') or {}).values()
                       for engine in work.get('engine') or []]
    for field in ('model', 'effort'):
        values = sorted({engine[field] for engine in engines if pins or engine.get(field)})
        if len(values) > 1:
            settings[field] = 'per-request: ' + ', '.join(value or '(none)' for value in values)
        elif values and (field == 'model' or values[0]):
            settings[field] = values[0]
    settings.update({key: run[key] for key in ('judge', 'seed') if key in run})
    return settings


def _cost(records):
    judge = judge_totals(records)
    cost = {'worker_runs': len(records), 'judge_calls': judge['calls']}
    for key in ('input_tokens', 'output_tokens', 'usd'):
        value = judge[key]
        if _finite_number(value) and value >= 0:
            cost[f'judge_{key}'] = value
    worker_usd = [row['worker_api'].get('usd') for row in records if isinstance(row.get('worker_api'), dict)]
    observed = [value for value in worker_usd if _finite_number(value) and value >= 0]
    total = sum(observed)
    if observed and _finite_number(total):
        cost['worker_api_usd'] = total
    return cost


def run_arm(sweep_args, *, candidate=None, target=None, lines=None, equal_retries=False,
            out=None, repo=REPO, runner=run_sweep, base_text=None):
    """Run one arm from committed text and write its comparison record.

    ``base_text`` replaces the committed target text as the base (#1355): a current or equal-retries arm
    runs it in place of the committed text, and a candidate must name it as its base. An automatic loop
    uses it to carry a promoted winner into the next round without touching the working tree.
    """
    if (candidate is None) == (target is None):
        raise ValueError('Specify exactly one candidate or current target')
    if candidate is not None and (lines is not None or equal_retries):
        raise ValueError('Lines and equal retries apply only to current text')
    if any(arg == '--world' or arg.startswith('--world=') for arg in sweep_args):
        raise ValueError('World sweeps have no refusal observation')
    if '--worker=both' in sweep_args or any(pair == ('--worker', 'both') for pair in zip(sweep_args, sweep_args[1:])):
        raise ValueError('An arm needs one worker')

    repo = Path(repo).resolve()
    folder = _outside_repo(eval_home(), repo)
    copies = _outside_repo(folder / 'arm-copies', repo)
    out = _outside_repo(out, repo) if out is not None else None
    role = label = 'equal_retries' if equal_retries else 'current'
    if candidate is not None:
        file = Path(candidate).expanduser()
        meta = revise._read_json(file.with_suffix('.json'))
        if not isinstance(meta, dict) or meta.get('schema') != 'revision-candidate/1':
            raise ValueError('Expected revision-candidate/1 metadata')
        with file.open(encoding='utf-8', newline='') as source:
            replacement = source.read()
        candidate_digest = meta.get('candidate_digest')
        if text_digest(replacement) != candidate_digest:
            raise ValueError('Text does not match the candidate digest')
        target, lines, label = meta['target_path'], meta['lines'], meta['label']
        role = 'candidate'

    target = Path(target)
    if target.is_absolute():
        target = target.relative_to(repo)
    copies.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=copies) as directory, TemporaryFile(dir=copies) as archive:
        copy = Path(directory)
        subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar', 'HEAD'], stdout=archive, check=True)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tree:
            git_head = tree.pax_headers['comment']  # git archive records the archived commit in the tar header.
            tree.extractall(copy, filter='data')
        selected = revise.load_target(target, lines, repo=copy)
        base = digest = selected['digest']
        if base_text is not None:
            base = digest = text_digest(base_text)
            if candidate is None and digest != selected['digest']:
                _splice(copy, selected, base_text, digest)
        if candidate is not None:
            if base != meta.get('base_digest'):
                raise ValueError('The base text does not match the candidate base digest')
            digest = _splice(copy, selected, replacement, candidate_digest)
        if out is None:
            folder = _outside_repo(folder / 'revisions' / revise._slug(selected['path']) / 'arms', repo)
        result = runner(copy, sweep_args)

    run, records, exit_code = result['run'], result['records'], result['exit_code']
    if exit_code not in (0, 1) or run.get('stopped') or not records:
        raise ValueError(f'Incomplete sweep (exit code {exit_code})')
    scenario_ids = run['scenario_ids'] if 'scenario_ids' in run else sorted({row['scenario'] for row in records})
    arm = {'schema': 'revision-arm/1', 'role': role, 'label': label, 'target_path': selected['path'],
           'lines': selected['lines'], 'base_digest': base, 'text_digest': digest, 'settings': _settings(run, records),
           'scenario_ids': scenario_ids, 'records': records, 'cost': _cost(records), 'run_id': run['id'],
           'git_head': git_head, 'sweep_args': sweep_args}
    contents = revise._json(arm)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(contents, encoding='utf-8')
        return out
    folder.mkdir(parents=True, exist_ok=True)
    prefix = f'{revise._time_digest(datetime.now(timezone.utc), digest)}-{role}-'
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, prefix=prefix, suffix='.json',
                            delete=False) as output:
        output.write(contents)
    return Path(output.name)
