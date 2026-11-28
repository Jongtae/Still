"""#1297: run by the eval virtualenv's Python; fails when the judge/proposer path cannot import.

Imports every Still and eval-harness module and builds the service the subscription-CLI judge
builds (``judgments.service_factory``) on an empty temporary store. Model-free, no network.
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


def main():
    import inspect_ai.model  # noqa: F401 - the proposer's and an API judge's transport

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
    if failed:
        print('Eval environment is missing modules:\n  ' + '\n  '.join(failed), file=sys.stderr)
        return 1
    print('Eval environment imports the judge and proposer path.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
