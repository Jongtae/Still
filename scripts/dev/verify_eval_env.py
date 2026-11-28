"""#1297: run by the eval virtualenv's Python; fails when the judge/proposer path cannot import.

Imports every Still and eval-harness module and builds the service the subscription-CLI judge
builds (``judgments.service_factory``) on an empty temporary store. It then runs a one-sample
Inspect eval on the offline ``mockllm`` model, because Inspect imports more only once an eval
starts. Model-free: no model request.
"""
import importlib
import pkgutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'evals')]

#: Needs the optional ``a2a`` extra and is imported only when the owner uses A2A; not on the judge path.
OPTIONAL = frozenset({'personal_agent.a2a_transport'})


def run_offline_eval(log_dir):
    from inspect_ai import Task, eval as inspect_eval
    from inspect_ai.dataset import Sample

    log = inspect_eval(Task(dataset=[Sample(input='x')]), model='mockllm/model', log_dir=log_dir,
                       display='none')[0]
    return None if log.status == 'success' else f'offline Inspect eval ended {log.status}'


def main():
    failed = []
    for package in ('personal_agent', 'agentos_eval'):
        for found in pkgutil.walk_packages(importlib.import_module(package).__path__, package + '.'):
            if found.name in OPTIONAL or found.name.endswith('.__main__') or '.sandbox_site' in found.name:
                continue
            try:
                importlib.import_module(found.name)
            except ImportError as exc:
                failed.append(f'{found.name}: {exc}')
    if not failed:
        from agentos_eval import judgments
        with tempfile.TemporaryDirectory() as data:
            judgments.service_factory(Path(data))
        try:
            with tempfile.TemporaryDirectory() as log_dir:
                error = run_offline_eval(log_dir)
        except ImportError as exc:
            error = f'offline Inspect eval: {exc}'
        if error:
            failed.append(error)
    if failed:
        print('Eval environment is missing modules:\n  ' + '\n  '.join(failed), file=sys.stderr)
        return 1
    print('Eval environment imports the judge and proposer path.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
