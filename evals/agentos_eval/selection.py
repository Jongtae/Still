"""Data-driven affected-case selection; never runs an engine or reads owner cases."""
import argparse
import json
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path

from . import judgments, paired, recall_pairs, scenarios
from .paths import REPO

VERSION = 'affected-cases/1'
FIELDS = ('kinds', 'paths', 'prompt_fragments', 'propositions', 'routes', 'tags')
SETS = ('judgments', 'qualification', 'scenarios', 'recall')


class SelectionError(ValueError):
    pass


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise SelectionError(f'Expected keys {sorted(required)}; optional {sorted(optional)}')


def _strings(values):
    if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
        raise SelectionError('Expected a nonempty list of nonempty strings')


def normalize_change(change):
    _keys(change, (), (*FIELDS, 'description'))
    result = {}
    for key in FIELDS:
        values = change.get(key, [])
        if values != []:
            _strings(values)
        result[key] = sorted(set(values))
    if any(Path(p).is_absolute() or '..' in Path(p).parts for p in result['paths']):
        raise SelectionError('Paths must be repository-relative without ..')
    result['paths'] = sorted({Path(p).as_posix() for p in result['paths']})
    description = change.get('description', '')
    if not isinstance(description, str):
        raise SelectionError('description must be text')
    result['description'] = description.strip()
    return result


def inventory(*, root=REPO):
    """Read shipped synthetic cases via their public loaders; qualification IDs only."""
    root = Path(root)
    partition = paired.load_partners(root=root)
    splits = {ident: split for part in partition['roles'].values()
              for split in ('dev', 'heldout') for ident in part[split]}
    entries = [dict(set='judgments', id=c['id'], split=splits[c['id']], role=c['role'],
                    tags=[c['category'], *c.get('targets', [])])
               for name in partition['case_sets'] for c in judgments.load_cases(root / name)['cases']]
    suite = judgments.load_still(root / 'src').decision_qualification
    entries.extend(dict(set='qualification', id='qualification-' + ident, split=None,
                        role='qualification-suite', tags=[]) for ident in suite.CASE_IDS)
    for path in sorted((root / 'evals/scenarios').glob('*.json')):
        entries.extend(dict(set='scenarios', id=c['id'], split=c['split'], role=None, tags=c['tags'])
                       for c in scenarios.load_file(path))
    recall = recall_pairs.load(root / 'evals/recall/recall-pairs-v1.json')
    entries.extend(dict(set='recall', id=c['id'], split=c['split'], role=None, tags=[c['kind']])
                   for c in recall['cases'])
    if len({(e['set'], e['id']) for e in entries}) != len(entries):
        raise SelectionError('Duplicate case IDs within a set')
    return entries


def _selected(entry, selector):
    return entry['set'] == selector['set'] and all(
        entry['id'] in values if key == 'ids' else
        entry['role'] in values if key == 'roles' else
        bool(set(entry['tags']) & set(values)) if key == 'tags' else
        entry['split'] in values
        for key, values in selector.items() if key != 'set')


def validate_rules(data, entries):
    """Strict rules and references: typos fail before matching any change."""
    _keys(data, ('version', 'rules'))
    if data['version'] != VERSION or not isinstance(data['rules'], list) or not data['rules']:
        raise SelectionError('Unsupported or empty selection rules')
    seen = set()
    for rule in data['rules']:
        _keys(rule, ('id', 'match', 'select', 'reason'))
        for key in ('id', 'reason'):
            if not isinstance(rule[key], str) or not rule[key].strip():
                raise SelectionError('Rule needs an id and reason')
        if rule['id'] in seen:
            raise SelectionError('Duplicate rule id')
        seen.add(rule['id'])
        _keys(rule['match'], (), (*FIELDS, 'description'))
        if not rule['match']:
            raise SelectionError('Rule needs match criteria')
        for values in rule['match'].values():
            _strings(values)
        if not isinstance(rule['select'], list) or not rule['select']:
            raise SelectionError('Rule needs selectors')
        for selector in rule['select']:
            _keys(selector, ('set',), ('ids', 'roles', 'tags', 'splits'))
            if selector['set'] not in SETS:
                raise SelectionError('Unknown eval set')
            members = [e for e in entries if e['set'] == selector['set']]
            known = {'ids': {e['id'] for e in members}, 'roles': {e['role'] for e in members},
                     'tags': {t for e in members for t in e['tags']}, 'splits': {'dev', 'heldout'}}
            for key, values in selector.items():
                if key == 'set':
                    continue
                _strings(values)
                if set(values) - known[key]:
                    raise SelectionError(f'{rule["id"]}: unknown {key}: {sorted(set(values) - known[key])}')
            if not any(_selected(e, selector) for e in members):
                raise SelectionError(f'{rule["id"]}: selector matches no cases')
    return data


def select(change, *, root=REPO):
    """Union matching rules, deduplicate cases/reasons, disclose every unmatched input."""
    change = normalize_change(change)
    entries = inventory(root=root)
    data = validate_rules(json.loads((Path(root) / 'evals/selection/affected-cases-v1.json').read_text()), entries)
    tokens = [(key, value) for key, values in change.items()
              for value in ([values] if key == 'description' and values else [] if key == 'description' else values)]
    matched, selected = set(), {}
    for rule in data['rules']:
        hits = [(key, value) for key, value in tokens
                if any(fnmatchcase(value, pattern) for pattern in rule['match'].get(key, []))]
        if not hits:
            continue
        matched.update(hits)
        reason = f'{rule["id"]}: {rule["reason"]} (' + '; '.join(f'{k}={v}' for k, v in hits) + ')'
        for entry in entries:
            if any(_selected(entry, selector) for selector in rule['select']):
                row = selected.setdefault((entry['set'], entry['id']),
                                          {k: entry[k] for k in ('set', 'id', 'split')} | {'reasons': []})
                row['reasons'].append(reason)
    unmatched = [{'field': key, 'value': value} for key, value in tokens if (key, value) not in matched]
    if not tokens:
        unmatched.append({'field': 'change', 'value': 'No change signals supplied'})
    return {'change': change, 'selected': [selected[key] for key in sorted(selected)], 'unmatched': unmatched}


def diff_paths(revisions, *, root=REPO, git_run=subprocess.run):
    """Injectable, argv-only git call; NUL-delimited paths preserve whitespace."""
    if '..' not in revisions or revisions.startswith('-') or any(c.isspace() for c in revisions):
        raise SelectionError('--diff must be base..head')
    result = git_run(['git', 'diff', '--name-only', '-z', revisions, '--'], cwd=root,
                     check=True, capture_output=True, text=True)
    return [path for path in result.stdout.split('\0') if path]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--change', type=Path, help='JSON change description')
    parser.add_argument('--diff', help='base..head (changed paths)')
    parser.add_argument('--root', type=Path, default=REPO)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if not args.change and not args.diff:
        parser.error('provide --change and/or --diff')
    try:
        change = json.loads(args.change.read_text()) if args.change else {}
        change = normalize_change(change)
        if args.diff:
            change['paths'].extend(diff_paths(args.diff, root=args.root))
        result = json.dumps(select(change, root=args.root), ensure_ascii=False, indent=2) + '\n'
        if args.output:
            args.output.write_text(result)
        else:
            print(result, end='')
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
