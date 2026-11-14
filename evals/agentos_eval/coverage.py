"""Development-only task inventory; evidence classes never become a quality score.

Adopt JSON Schema 2020-12 via the existing schema-validation extra (jsonschema),
stdlib JSON/date/path APIs, and the skill-eval-01 table shape. Adapt the existing
scenario tags for cross-references. No runtime dependency or authority is added.
"""
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # optional schema-validation extra, like test_draft_formats
    Draft202012Validator = FormatChecker = None

from .paths import REPO

STATUSES = ('observed-live', 'fixture-only', 'blocked-by-still', 'engine-limit',
            'out-of-bounds-by-design', 'untried')
Row = dict
SCHEMA_ROOT = REPO / 'evals/task-coverage/schema'


@dataclass
class CoverageMap:
    root: Path
    document: dict
    taxonomy_document: dict

    @property
    def rows(self) -> list[Row]:
        return self.document['rows']

    @property
    def taxonomies(self):
        return self.taxonomy_document['taxonomies']


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def load_map(root: Path | None = None) -> CoverageMap:
    """Load from a repository root (including temporary fixture repositories)."""
    root = Path(root) if root is not None else REPO
    folder = root / 'evals/task-coverage'
    return CoverageMap(root, _read(folder / 'rows.json'), _read(folder / 'taxonomies.json'))


def bundled_scenarios(root=REPO):
    """All bundled scenarios, including world cases; never owner-local text."""
    from .scenarios import load_file
    return [item for path in sorted((Path(root) / 'evals/scenarios').glob('*.json'))
            for item in load_file(path)]


def _dated(item):
    try:
        date.fromisoformat(item.get('date', ''))
        return bool(item.get('revision', '').strip())
    except (ValueError, TypeError):
        return False


def validate(cmap, scenarios) -> list[str]:
    """Schema and static semantic problems, without imports or live execution."""
    if Draft202012Validator is None:
        return ['Structural validation unavailable: install the schema-validation extra (jsonschema).']
    problems = []
    for name, document in [('rows', cmap.document), ('taxonomies', cmap.taxonomy_document)]:
        validator = Draft202012Validator(_read(SCHEMA_ROOT / f'{name}.schema.json'),
                                         format_checker=FormatChecker())
        problems.extend(f'{name}{error.json_path}: {error.message}' for error in sorted(
            validator.iter_errors(document), key=lambda e: (e.json_path, e.message)))
    if problems:
        return problems  # malformed objects cannot safely enter semantic checks
    scenarios = list(scenarios)
    ids = [row['id'] for row in cmap.rows]
    taxonomy_ids = [tax['id'] for tax in cmap.taxonomies]
    for label, values in [('row', ids), ('taxonomy', taxonomy_ids)]:
        problems.extend(f'duplicate {label} id: {ident}' for ident, count in Counter(values).items() if count > 1)
    adopted = {tax['id'] for tax in cmap.taxonomies if tax['status'] == 'adopted'}
    scenario_ids = {scenario['id'] for scenario in scenarios}
    try:
        judgment_ids = {case['id'] for case in _read(cmap.root / 'evals/judgments/cases-v1.json')['cases']}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        problems.append(f'cannot read judgment cases: {exc}')
        judgment_ids = set()
    for row in cmap.rows:
        ident, status, evidence = row['id'], row['status'], row['evidence']
        if row['taxonomy'] not in adopted or ident != f"{row['taxonomy']}:{row['code']}":
            problems.append(f'{ident}: id must be adopted taxonomy:code matching its fields')
        if status == 'observed-live' and not any(
                item['class'] in ('P', 'L') and item['kind'] in ('live-observation', 'doc')
                and _dated(item) for item in evidence):
            problems.append(f'{ident}: observed-live needs dated P/L live evidence with revision')
        if status == 'fixture-only' and not any(item['class'] in ('U', 'I') for item in evidence):
            problems.append(f'{ident}: fixture-only needs U/I evidence')
        if status == 'untried' and evidence:
            problems.append(f'{ident}: untried must have no Still evidence')
        for case in row['judgment_cases']:
            if case not in judgment_ids:
                problems.append(f'{ident}: unknown judgment case {case}')
        for item in evidence:
            source = item['source']
            if source.startswith('scenario:'):
                if source[9:] not in scenario_ids:
                    problems.append(f'{ident}: unknown scenario evidence {source}')
                if item['class'] != 'S':
                    problems.append(f'{ident}: scenario specification requires class S: {source}')
            if source.startswith('judgment:'):
                if source[9:] not in judgment_ids:
                    problems.append(f'{ident}: unknown judgment evidence {source}')
                if item['class'] != 'S':
                    problems.append(f'{ident}: judgment specification requires class S: {source}')
            if item['kind'] in ('test', 'judgment-case') and item['class'] in ('P', 'L'):
                problems.append(f'{ident}: fixture/specification is not live evidence: {source}')
            if source.startswith('tests/'):
                parts = source.split('::')
                path = (cmap.root / parts[0]).resolve()
                if not path.is_relative_to(cmap.root.resolve() / 'tests') or not path.is_file():
                    problems.append(f'{ident}: missing test source {source}')
                    continue
                text = path.read_text(encoding='utf-8')
                if len(parts) < 2 or any(not re.search(
                        r'^\s*(?:async\s+def|def|class)\s+' + re.escape(name) + r'\b', text, re.M)
                        for name in parts[1:]):
                    problems.append(f'{ident}: missing test function or class {source}')
    for scenario in scenarios:
        tags = set(scenario.get('tags', []))
        if not tags.intersection(ids):
            problems.append(f"{scenario['id']}: scenario has no row tag")
        for tag in sorted(tags):
            if ':' in tag and tag.split(':', 1)[0] in taxonomy_ids and tag not in ids:
                problems.append(f"{scenario['id']}: unknown row tag {tag}")
    return problems


def cases_for_rows(cmap, row_ids, scenarios):
    """Stable sorted union of scenario tags and explicit judgment-case links."""
    wanted = set(row_ids)
    unknown = wanted - {row['id'] for row in cmap.rows}
    if unknown:
        raise ValueError(f'unknown row ids: {sorted(unknown)}')
    return {'scenarios': sorted({item['id'] for item in scenarios if wanted.intersection(item.get('tags', []))}),
            'judgment_cases': sorted({case for row in cmap.rows if row['id'] in wanted
                                      for case in row['judgment_cases']})}


def rows_by_status(cmap) -> dict[str, list[Row]]:
    return {status: [row for row in cmap.rows if row['status'] == status] for status in STATUSES}


def _cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def _link(source, scenario_files=None):
    # Repository-relative links work in the generated document under docs/.
    if source.startswith('scenario:'):
        path = (scenario_files or {}).get(source[9:], 'evals/scenarios/')
        return f'[{_cell(source)}](../{path})'
    if source.startswith('judgment:'):
        return f'[{_cell(source)}](../evals/judgments/cases-v1.json)'
    path = source.split('::', 1)[0]
    return f'[{_cell(source)}](../{path})'


def coverage_markdown(cmap, scenarios) -> str:
    """Deterministic map of evidence and gaps, independent of sweep results."""
    scenarios = list(scenarios)
    scenario_files = {}
    for path in sorted((cmap.root / 'evals/scenarios').glob('*.json')):
        raw = _read(path)
        for item in raw if isinstance(raw, list) else [raw]:
            scenario_files[item['id']] = path.relative_to(cmap.root).as_posix()
    lines = ['## Task coverage map', '',
             f"As of {cmap.document['as_of']}; evidence read at revision `{cmap.document['revision']}`.",
             'This inventory records evidence and gaps, not a score. U: unit/fixture; I: model-free integration; '
             'S: static/specification; P: live public read; L: live model/account.', '',
             '### Taxonomies and attribution', '']
    for tax in cmap.taxonomies:
        lines.append(f"- **{_cell(tax['id'])}** ({tax['status']}): {_cell(tax['name'])}; "
                     f"{_cell(tax.get('version', 'unversioned'))}. "
                     f"[Source]({tax.get('url', '')}). {_cell(tax.get('licence', ''))}. "
                     f"{_cell(tax.get('attribution', ''))} Changes: {_cell(tax.get('changes', 'none recorded'))}")
    lines += ['', '### Tier-1 groups by status', '',
              '| Taxonomy | Group | ' + ' | '.join(STATUSES) + ' |',
              '| --- | --- | ' + ' | '.join('---' for _ in STATUSES) + ' |']
    groups = sorted({(row['taxonomy'], row['parent']['code'], row['parent']['title']) for row in cmap.rows})
    for taxonomy, code, title in groups:
        counts = Counter(row['status'] for row in cmap.rows
                         if row['taxonomy'] == taxonomy and row['parent']['code'] == code)
        lines.append(f'| {_cell(taxonomy)} | {_cell(code)} {_cell(title)} | '
                     + ' | '.join(str(counts[status]) for status in STATUSES) + ' |')
    lines += ['', '### Evidence inventory', '', '| Row | Inventory item | Evidence (class) | Status |',
              '| --- | --- | --- | --- |']
    for row in cmap.rows:
        evidence = '<br>'.join(f"{item['class']}: {_link(item['source'], scenario_files)} — {_cell(item['summary'])} "
                              f"({item['date']}, `{item['revision']}`)" for item in row['evidence']) or 'none'
        lines.append(f"| {_cell(row['id'])} | {_cell(row['title'])}: {_cell(row['still_task'])} | "
                     f"{evidence} | {row['status']} |")
    lines += ['', '### Untried', '']
    lines += [f"- `{row['id']}`: {_cell(row['title'])}" for row in rows_by_status(cmap)['untried']] or ['- none']
    lines += ['', '### Blocked by Still', '']
    lines += [f"- `{row['id']}`: {row['blocker']['kind']} — {_cell(row['blocker']['detail'])}; "
              f"`{row['blocker']['code_ref']}`; issue {row['blocker']['issue'] or 'none'}"
              for row in rows_by_status(cmap)['blocked-by-still']] or ['- none']
    lines += ['', '### Regression links', '', '| Row | Scenarios | Judgment cases |', '| --- | --- | --- |']
    for row in cmap.rows:
        cases = cases_for_rows(cmap, [row['id']], scenarios)
        lines.append(f"| {_cell(row['id'])} | "
                     + ', '.join(_link('scenario:' + item, scenario_files) for item in cases['scenarios'])
                     + ' | ' + ', '.join(_link('judgment:' + item) for item in cases['judgment_cases']) + ' |')
    return '\n'.join(lines) + '\n'
