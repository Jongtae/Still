"""#260: which tests the required ``validate`` check runs for one pull request.

``full`` - the whole Linux suite (#234) - whenever any changed path can affect code:
anything under ``src/``, ``tests/`` or ``scripts/``, the dependency files, a workflow,
or any path this module does not recognise as text.  ``text`` only when every changed
path is repository text (documentation, Markdown, the delivery plan, issue templates);
then the test files that read repository text run.  Anything unexpected - no diff, a
git error - is ``full``.  The required check runs this script from the protected base
revision, so a pull request cannot loosen its own scope.

Prints ``suite=<full|text>`` and, for ``text``, ``tests=<files>`` (GitHub output lines).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: A changed path under one of these, or one of these files, always runs the whole suite.
CODE_PREFIXES = ('src/', 'tests/', 'scripts/', '.github/workflows/')
CODE_FILES = frozenset({'pyproject.toml', 'uv.lock', 'conftest.py', 'pytest.ini', 'setup.cfg', 'tox.ini'})
#: Repository text: only these may take the ``text`` scope.
_TEXT_PATH = re.compile(r'(?:docs/.*|.*\.md|delivery-plan\.yaml|\.github/ISSUE_TEMPLATE/.*|LICENSE|NOTICE)')
#: A test file that names any of these reads repository text, so it runs for a text-only change.
TEXT_MARKERS = ('docs', '.md', 'AGENTS', 'README', 'CLAUDE', 'GOVERNANCE', 'CONTRIBUTING', 'delivery-plan',
                '.github', 'LICENSE', 'NOTICE', 'TRADEMARKS')


def scope(paths):
    """``'full'`` or ``'text'`` for the changed ``paths``."""
    paths = [path for path in paths if path]
    if not paths:
        return 'full'
    for path in paths:
        if path.startswith(CODE_PREFIXES) or path in CODE_FILES or not _TEXT_PATH.fullmatch(path):
            return 'full'
    return 'text'


def text_tests(paths, root=ROOT):
    """The test files that read repository text, plus any that names a changed file, sorted."""
    names = {Path(path).name for path in paths} | {Path(path).stem for path in paths}
    selected = []
    for test in sorted((root / 'tests').glob('test_*.py')):
        source = test.read_text(encoding='utf-8')
        if any(marker in source for marker in TEXT_MARKERS) or any(name and name in source for name in names):
            selected.append(test.relative_to(root).as_posix())
    return selected


def changed(base, head, root=ROOT):
    # ``--no-renames``: a rename is a deletion plus an addition, so a code path moved into ``docs/``
    # still counts as code (Codex review on #261).
    out = subprocess.run(['git', 'diff', '--name-only', '--no-renames', f'{base}...{head}'], cwd=root, capture_output=True,
                         text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--root', default=str(ROOT), help='the checkout (the script may run from elsewhere)')
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        paths = changed(args.base, args.head, root)
        suite = scope(paths)
        tests = text_tests(paths, root) if suite == 'text' else []
    except Exception as exc:  # noqa: BLE001 - anything unexpected runs everything
        print(f'test scope unavailable ({type(exc).__name__}); running the whole suite', file=sys.stderr)
        suite, tests = 'full', []
    if suite == 'text' and not tests:
        suite = 'full'
    print(f'suite={suite}')
    if tests:
        print('tests=' + ' '.join(tests))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
