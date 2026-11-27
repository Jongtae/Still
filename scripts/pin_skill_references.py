#!/usr/bin/env python3
"""Pin Still's reference skills to one exact commit and content digest (#1291, C7).

Usage: ``PYTHONPATH=src python3 scripts/pin_skill_references.py [COMMIT] [--check]``

For every entry of ``src/personal_agent/skill_references.json`` whose address
is a folder of this repository, the folder is read from ``COMMIT`` (default:
``origin/main``) with ``git archive``, inspected with ``skills.inspect_skill``
(the same digest an install computes), and the entry's ``address`` and
``digest`` are rewritten to that commit.  ``--check`` changes nothing and fails
when an entry is unpinned or its pin does not match that commit's content.

The commit must be one GitHub serves (pushed), since an install fetches it.
"""
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from personal_agent.skills import GITHUB_TREE, REFERENCES_FILE, inspect_skill  # noqa: E402

REPO = 'Jongtae/Still'


def folder_digest(commit, path):
    data = subprocess.run(['git', 'archive', '--format=tar', commit, path], cwd=ROOT,
                          check=True, capture_output=True).stdout
    with tempfile.TemporaryDirectory() as temporary:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(temporary, filter='data')
        return inspect_skill(Path(temporary, path))['digest']


def main(argv):
    check = '--check' in argv
    args = [arg for arg in argv if arg != '--check']
    commit = subprocess.run(['git', 'rev-parse', args[0] if args else 'origin/main'], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.strip()
    rows = json.loads(REFERENCES_FILE.read_text(encoding='utf-8'))
    problems = []
    for row in rows:
        match = GITHUB_TREE.match(row['address'])
        if not match or f'{match.group(1)}/{match.group(2)}' != REPO:
            problems.append(f"{row['name']}: not a folder of {REPO}")
            continue
        path = match.group(4)
        if check:
            pinned = match.group(3)
            if len(pinned) != 40 or not row.get('digest'):
                problems.append(f"{row['name']}: not pinned")
            elif folder_digest(pinned, path) != row['digest']:
                problems.append(f"{row['name']}: digest differs from {pinned[:7]}")
            continue
        row['address'] = f'github.com/{REPO}/tree/{commit}/{path}'
        row['digest'] = folder_digest(commit, path)
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        return 1
    if not check:
        text = '[\n' + ',\n'.join('  ' + json.dumps(row, ensure_ascii=False) for row in rows) + '\n]\n'
        REFERENCES_FILE.write_text(text, encoding='utf-8')
        print(f'pinned {len(rows)} reference skills to {commit}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
