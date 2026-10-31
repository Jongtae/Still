"""#260: which tests the required ``validate`` check runs for one pull request.

``full`` - the whole Linux suite (#234) - whenever any changed path can affect code:
anything under ``src/``, ``tests/`` or ``scripts/``, the dependency files, a workflow,
or any path this module does not recognise as text.  ``text`` only when every changed
path is repository text (documentation, Markdown, the delivery plan, issue templates);
then the test files that read repository text run.  ``release`` (#508) when the change
only swaps release identifiers - a version, a date, a commit SHA or an archive SHA-256 -
in the release data the release workflow writes; then the release tests run.  Anything
unexpected - no diff, a git error - is ``full``.  The required check runs this script from the protected base
revision, so a pull request cannot loosen its own scope.

Prints ``suite=<full|text|release>`` and, for ``text`` and ``release``, ``tests=<files>`` (GitHub output lines).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
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

#: #508: the tests that check release data - the manifest, the installer pins, the formula, the release-status
#: sentences and the README installer line in every locale, the site that shows them, and the lock.
RELEASE_TESTS = ('tests/test_dependency_update_contract.py', 'tests/test_install_script.py', 'tests/test_public_site.py',
                 'tests/test_readme_localization.py', 'tests/test_release_script.py',
                 'tests/test_release_traceability.py')
#: A release identifier: a version, a date, a full commit SHA or a SHA-256.
_IDENTIFIER = re.compile(r'(?<![0-9A-Za-z.])(?:v?\d+\.\d+\.\d+|\d{4}-\d{2}-\d{2}|[0-9a-f]{64}|[0-9a-f]{40})(?![0-9A-Za-z])')
#: Still's own release pins; the uv installer pins beside them are not release data.
_INSTALLER_PINS = re.compile(r'AGENTOS_(?:VERSION|ARCHIVE_SHA256)="[^"]*"')
#: The formula's own archive and digest, at class level; a ``resource`` block's lines are indented further.
_FORMULA_PINS = re.compile(r'  (?:url "https://github\.com/Jongtae/Still/archive/refs/tags/v[^"]*\.tar\.gz"|sha256 "[0-9a-f]{64}")')


def scope(paths):
    """``'full'`` or ``'text'`` for the changed ``paths``."""
    paths = [path for path in paths if path]
    if not paths:
        return 'full'
    for path in paths:
        if path.startswith(CODE_PREFIXES) or path in CODE_FILES or not _TEXT_PATH.fullmatch(path):
            return 'full'
    return 'text'


def _reads_text(source, names):
    return any(marker in source for marker in TEXT_MARKERS) or any(name and name in source for name in names)


def text_tests(paths, root=ROOT):
    """The test files that read repository text - directly or through a local helper module - plus any
    that names a changed file, sorted.

    Codex review on #261: a test may read text only through a helper in ``tests/`` (``load_plan`` in
    ``delivery_state_invariants``), so a helper that reads text, or imports one that does, marks every
    test that imports it.
    """
    names = {Path(path).name for path in paths} | {Path(path).stem for path in paths}
    tests_dir = root / 'tests'
    sources = {path.stem: path.read_text(encoding='utf-8') for path in tests_dir.glob('*.py')}
    def imports(source, module):
        return re.search(rf'^\s*(?:from\s+{re.escape(module)}\s+import|import\s+{re.escape(module)}\b)', source, re.M)
    # Any module in tests/ (a helper, or a test module whose harness others import) that reads text,
    # or imports one that does, reads text.
    readers = {stem for stem, source in sources.items() if _reads_text(source, names)}
    grew = True
    while grew:
        grew = False
        for stem, source in sources.items():
            if stem not in readers and any(imports(source, reader) for reader in readers):
                readers.add(stem)
                grew = True
    return sorted(f'tests/{stem}.py' for stem in readers if stem.startswith('test_'))


def _swaps_identifiers(before, after, lines=None):
    """Whether ``after`` is ``before`` with only release identifiers replaced, line for line; with ``lines``, every
    changed line must also be one of those lines."""
    old, new = before.splitlines(), after.splitlines()
    if len(old) != len(new):
        return False
    for line_before, line_after in zip(old, new):
        if line_before == line_after:
            continue
        if _IDENTIFIER.sub('#', line_before) != _IDENTIFIER.sub('#', line_after):
            return False
        if lines is not None and not (lines.fullmatch(line_before) and lines.fullmatch(line_after)):
            return False
    return True


def _without_own_version(path, text):
    """The parsed TOML with this project's own version blanked: ``pyproject.toml``'s ``project.version`` and the
    editable package's ``version`` in ``uv.lock``."""
    data = tomllib.loads(text)
    if path == 'pyproject.toml':
        data['project']['version'] = None
    else:
        own = [package for package in data['package'] if package.get('source') == {'editable': '.'}]
        if len(own) != 1:
            raise ValueError('uv.lock has no single editable package')
        own[0]['version'] = None
    return data


def release_change(path, before, after):
    """Whether changing ``path`` from ``before`` to ``after`` only swaps release identifiers in release data (#508)."""
    if before is None or after is None:
        return False
    if path == 'docs/release-manifest.json':
        return True
    if path in ('pyproject.toml', 'uv.lock'):
        return _without_own_version(path, before) == _without_own_version(path, after)
    if path == 'scripts/install.sh':
        return _swaps_identifiers(before, after, _INSTALLER_PINS)
    if path == 'Formula/agentos.rb':
        return _swaps_identifiers(before, after, _FORMULA_PINS)
    if path.endswith('.md') and _TEXT_PATH.fullmatch(path):
        return _swaps_identifiers(before, after)
    return False


def _show(revision, path, root):
    result = subprocess.run(['git', 'show', f'{revision}:{path}'], cwd=root, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def release_only(paths, base, head, root=ROOT):
    """Whether every changed path only swaps release identifiers between the merge base and ``head``."""
    if not paths:
        return False
    merge_base = subprocess.run(['git', 'merge-base', base, head], cwd=root, capture_output=True, text=True,
                                check=True).stdout.strip()
    return all(release_change(path, _show(merge_base, path, root), _show(head, path, root)) for path in paths)


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
        if release_only(paths, args.base, args.head, root):
            suite, tests = 'release', list(RELEASE_TESTS)
        else:
            suite = scope(paths)
            tests = text_tests(paths, root) if suite == 'text' else []
    except Exception as exc:  # noqa: BLE001 - anything unexpected runs everything
        print(f'test scope unavailable ({type(exc).__name__}); running the whole suite', file=sys.stderr)
        suite, tests = 'full', []
    if suite != 'full' and not tests:
        suite = 'full'
    print(f'suite={suite}')
    if tests:
        print('tests=' + ' '.join(tests))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
