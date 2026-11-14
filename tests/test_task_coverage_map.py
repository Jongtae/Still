"""#462: model-free schema, cross-reference, report and runtime-isolation checks."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import coverage, cli, report  # noqa: E402


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def fixture(root):
    tax = {'id': 'onet-31.0-gwa', 'name': 'Generalized Work Activities', 'publisher': 'USDOL/ETA',
           'version': '31.0', 'url': 'https://www.onetcenter.org/', 'retrieved': '2026-11-14',
           'sha256': 'a' * 64, 'licence': 'CC BY 4.0',
           'attribution': 'O*NET 31.0 Database by USDOL/ETA. Used under CC BY 4.0.',
           'changes': 'Still adds evidence columns.', 'depth': 'GWA', 'status': 'adopted'}
    row = {'id': 'onet-31.0-gwa:4.A.1.a.1', 'taxonomy': 'onet-31.0-gwa', 'code': '4.A.1.a.1',
           'title': 'Getting Information', 'parent': {'code': '4.A.1', 'title': 'Information Input'},
           'still_task': 'Find public information.',
           'needs': {'information': ['public'], 'effects': ['public-read'], 'account': 'none', 'physical': 'none'},
           'status': 'fixture-only', 'evidence': [{'class': 'U', 'kind': 'test',
           'source': 'tests/test_demo.py::Demo::test_read', 'date': '2026-11-14', 'revision': 'abc1234',
           'summary': 'Synthetic fixture read.'}], 'judgment_cases': ['case-one']}
    untouched = copy.deepcopy(row)
    untouched.update(id='onet-31.0-gwa:4.A.1.a.2', code='4.A.1.a.2', title='Monitoring',
                     status='untried', evidence=[], judgment_cases=[])
    blocked = copy.deepcopy(row)
    blocked.update(id='onet-31.0-gwa:4.A.1.a.3', code='4.A.1.a.3', status='blocked-by-still',
                   blocker={'kind': 'routing', 'detail': 'Tool unavailable.', 'code_ref': 'src/example.py:route',
                            'issue': '#462'})
    write_json(root / 'evals/task-coverage/rows.json',
               {'version': 1, 'revision': 'abc1234', 'as_of': '2026-11-14', 'rows': [row, untouched, blocked]})
    write_json(root / 'evals/task-coverage/taxonomies.json', {'version': 1, 'taxonomies': [tax]})
    write_json(root / 'evals/judgments/cases-v1.json', {'cases': [{'id': 'case-one'}]})
    (root / 'tests').mkdir()
    (root / 'tests/test_demo.py').write_text('class Demo:\n    def test_read(self): pass\n')
    scenarios = [{'id': 'demo-one', 'tags': [row['id']]},
                 {'id': 'demo-two', 'tags': [row['id'], blocked['id']]}]
    return coverage.load_map(root), scenarios


@unittest.skipIf(coverage.Draft202012Validator is None, 'requires optional schema-validation extra')
class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cmap, self.scenarios = fixture(Path(self.temp.name))
        self.row = self.cmap.rows[0]

    def problems(self):
        return coverage.validate(self.cmap, self.scenarios)

    def test_valid_fixture_and_schema_dialects(self):
        self.assertEqual(self.problems(), [])
        for name in ('rows', 'taxonomies'):
            schema = json.loads((coverage.SCHEMA_ROOT / f'{name}.schema.json').read_text())
            self.assertEqual(schema['$schema'], 'https://json-schema.org/draft/2020-12/schema')
            coverage.Draft202012Validator.check_schema(schema)

    def test_adopted_taxonomy_requires_every_pin_field(self):
        tax = self.cmap.taxonomies[0]
        for field in ('version', 'url', 'retrieved', 'sha256', 'licence', 'attribution'):
            with self.subTest(field=field):
                value = tax.pop(field)
                self.assertTrue(self.problems())
                tax[field] = value

    def test_considered_taxonomy_needs_only_identity_status_and_reason(self):
        tax = {'id': 'considered', 'name': 'Possible future taxonomy',
               'status': 'considered-not-adopted-yet', 'reason': 'Reuse terms not yet pinned.'}
        self.cmap.taxonomies.append(tax)
        self.assertEqual(self.problems(), [])
        markdown = coverage.coverage_markdown(self.cmap, self.scenarios)
        self.assertIn(tax['reason'], markdown)
        self.assertNotIn('[Source]().', markdown)
        reason = tax.pop('reason')
        self.assertTrue(self.problems())
        # The integrated map records its deferral reason in retrieval_note.
        tax['retrieval_note'] = reason
        self.assertEqual(self.problems(), [])

    def test_untagged_scenario_fails(self):
        self.scenarios[0]['tags'] = ['ordinary']
        self.assertTrue(any('no row tag' in p for p in self.problems()))

    def test_unknown_row_tag_fails(self):
        self.scenarios[0]['tags'].append('onet-31.0-gwa:unknown')
        self.assertTrue(any('unknown row tag' in p for p in self.problems()))

    def test_unknown_status_fails(self):
        self.row['status'] = 'passed'
        self.assertTrue(self.problems())

    def test_observed_live_without_live_evidence_fails(self):
        self.row['status'] = 'observed-live'
        self.assertTrue(any('dated P/L' in p for p in self.problems()))

    def test_fixture_only_without_unit_or_integration_fails(self):
        self.row['evidence'][0]['class'] = 'S'
        self.assertTrue(any('U/I' in p for p in self.problems()))

    def test_blocked_without_blocker_fails(self):
        self.row['status'] = 'blocked-by-still'
        self.assertTrue(self.problems())

    def test_conditional_bounds_and_blocker(self):
        for status, field, value in [('out-of-bounds-by-design', 'bounds', 'Physical only.'),
                                     ('blocked-by-still', 'blocker', self.cmap.rows[2]['blocker'])]:
            with self.subTest(status=status):
                self.row['status'] = status
                evidence = self.row['evidence']
                if status == 'out-of-bounds-by-design':
                    self.row['evidence'] = []
                self.row.pop(field, None)
                self.assertTrue(self.problems())
                self.row[field] = value
                self.assertEqual(self.problems(), [])
                self.row['status'] = 'fixture-only'
                self.assertTrue(self.problems())
                self.row.pop(field)
                self.row['evidence'] = evidence

    def test_unknown_judgment_case_fails(self):
        self.row['judgment_cases'] = ['unknown']
        self.assertTrue(any('unknown judgment case' in p for p in self.problems()))

    def test_missing_test_file_and_function_fail(self):
        for source in ['tests/missing.py::test_read', 'tests/test_demo.py::Demo::test_missing']:
            with self.subTest(source=source):
                self.row['evidence'][0]['source'] = source
                self.assertTrue(any('missing test' in p for p in self.problems()))

    def test_duplicate_and_nonadopted_ids_fail(self):
        self.cmap.rows.append(copy.deepcopy(self.row))
        self.assertTrue(any('duplicate row' in p for p in self.problems()))
        self.cmap.rows.pop()
        self.cmap.taxonomies[0]['status'] = 'considered-not-adopted-yet'
        self.cmap.taxonomies[0]['reason'] = 'Deferred.'
        self.assertTrue(any('adopted taxonomy' in p for p in self.problems()))

    def test_unknown_scenario_evidence_fails(self):
        self.row['evidence'][0].update(source='scenario:missing', kind='scenario', **{'class': 'S'})
        self.assertTrue(any('unknown scenario evidence' in p for p in self.problems()))

    def test_live_evidence_requires_date_revision_and_cannot_be_spec(self):
        self.row['status'] = 'observed-live'
        item = self.row['evidence'][0]
        item.update(source='docs/observations.md#dated-run', kind='live-observation', **{'class': 'P'})
        self.assertEqual(self.problems(), [])
        for field, value in [('date', '2026-02-30'), ('revision', '')]:
            previous = item[field]
            item[field] = value
            self.assertTrue(self.problems())
            item[field] = previous
        item.update(source='scenario:demo-one', kind='scenario')
        self.assertTrue(self.problems())

    def test_scenario_specification_cannot_supply_fixture_evidence(self):
        self.row['evidence'][0].update(source='scenario:demo-one', kind='scenario')
        self.assertTrue(any('requires class S' in p for p in self.problems()))

    def test_empty_evidence_cannot_support_a_block_or_engine_limit(self):
        for status in ('blocked-by-still', 'engine-limit'):
            with self.subTest(status=status):
                self.row['status'] = status
                self.row['evidence'] = []
                if status == 'blocked-by-still':
                    self.row['blocker'] = self.cmap.rows[2]['blocker']
                else:
                    self.row.pop('blocker', None)
                self.assertTrue(self.problems())

    def test_untried_cannot_carry_fixture_evidence(self):
        self.row['status'] = 'untried'
        self.assertTrue(self.problems())

    def test_untried_and_out_of_bounds_accept_only_s_specification_links(self):
        item = self.row['evidence'][0]
        for status in ('untried', 'out-of-bounds-by-design'):
            self.row['status'] = status
            if status == 'out-of-bounds-by-design':
                self.row['bounds'] = 'Physical execution is outside scope.'
            for kind, source in [('scenario', 'scenario:demo-one'), ('judgment-case', 'judgment:case-one'),
                                 ('doc', 'docs/scope.md')]:
                with self.subTest(status=status, kind=kind):
                    item.update(kind=kind, source=source, **{'class': 'S'})
                    self.assertEqual(self.problems(), [])
            for evidence_class in ('U', 'I', 'P', 'L'):
                with self.subTest(status=status, evidence_class=evidence_class):
                    item['class'] = evidence_class
                    self.assertTrue(self.problems())
            for kind in ('test', 'live-observation'):
                with self.subTest(status=status, kind=kind):
                    item.update(kind=kind, **{'class': 'S'})
                    self.assertTrue(self.problems())

    def test_cases_for_rows_sorted_deduplicated_and_status_groups(self):
        self.assertEqual(coverage.cases_for_rows(self.cmap, [self.row['id'], self.cmap.rows[2]['id']], self.scenarios),
                         {'scenarios': ['demo-one', 'demo-two'], 'judgment_cases': ['case-one']})
        self.assertEqual(coverage.cases_for_rows(self.cmap, [], self.scenarios),
                         {'scenarios': [], 'judgment_cases': []})
        with self.assertRaises(ValueError):
            coverage.cases_for_rows(self.cmap, ['unknown'], self.scenarios)
        self.assertEqual(coverage.rows_by_status(self.cmap)['untried'], [self.cmap.rows[1]])

    def test_markdown_evidence_gaps_counts_and_links(self):
        markdown = coverage.coverage_markdown(self.cmap, self.scenarios)
        for text in ['CC BY 4.0', 'O*NET', 'S specification links (not runs)', 'U/I/P/L evidence',
                     '### Untried', '### Still-side blockers', '#### By blocker kind', '#### By issue',
                     'routing', 'Tool unavailable.', '#462', 'scenario:demo-one', 'judgment:case-one',
                     'U:', 'test_read', '| 0 | 1 | 1 | 0 | 0 | 1 |', 'not a score',
                     'PYTHONPATH=evals:src .venv/bin/python -m agentos_eval coverage --write',
                     '`observed-live`:', '`fixture-only`:', '`engine-limit`:', '`out-of-bounds-by-design`:']:
            self.assertIn(text, markdown)
        self.assertEqual(markdown, coverage.coverage_markdown(self.cmap, self.scenarios))
        for forbidden in ['pass rate', 'ranking', 'target']:
            self.assertNotIn(forbidden, markdown)

    def test_markdown_separates_specifications_from_execution_evidence(self):
        spec = copy.deepcopy(self.row['evidence'][0])
        spec.update(kind='scenario', source='scenario:demo-one', **{'class': 'S'})
        self.row['evidence'].append(spec)
        untried = self.cmap.rows[1]
        untried['evidence'] = [spec]
        self.assertEqual(self.problems(), [])
        lines = coverage.coverage_markdown(self.cmap, self.scenarios).splitlines()
        line = next(line for line in lines if line.startswith(f"| {self.row['id']} |"))
        cells = line.split('|')
        self.assertIn('S: [scenario:demo-one]', cells[3])
        self.assertNotIn('test_demo.py', cells[3])
        self.assertIn('U: [tests/test_demo.py', cells[4])
        self.assertNotIn('scenario:', cells[4])
        line = next(line for line in lines if line.startswith(f"| {untried['id']} |"))
        self.assertEqual(line.split('|')[4].strip(), 'none')

    def test_blocker_work_list_groups_kinds_and_issues_by_row_count(self):
        for code, kind, issue in [('4', 'login', '#900'), ('5', 'login', '#900'),
                                  ('6', 'tool-not-exposed', '#900'), ('7', 'approval-boundary', None)]:
            row = copy.deepcopy(self.cmap.rows[2])
            row.update(id=f'onet-31.0-gwa:{code}', code=code)
            row['blocker'].update(kind=kind, issue=issue, code_ref=f'src/example.py:block_{code}')
            self.cmap.rows.append(row)
        self.assertEqual(self.problems(), [])
        markdown = coverage.coverage_markdown(self.cmap, self.scenarios)
        kinds, issues = markdown.split('#### By blocker kind')[1].split('#### By issue')
        self.assertIn('| login | 2 |', kinds)
        self.assertLess(kinds.index('| login |'), kinds.index('| routing |'))
        self.assertIn('| [#900](https://github.com/Jongtae/Still/issues/900) | 3 |', issues)
        self.assertLess(issues.index('| [#900]'), issues.index('| [#462]'))
        self.assertIn('| No linked issue | 1 |', issues)
        for code in ('4', '5', '6', '7'):
            self.assertIn(f'`onet-31.0-gwa:{code}`: `src/example.py:block_{code}`', issues)
        self.assertIn('completion check', markdown)
        self.assertNotIn('%', markdown)

    def test_cli_checks_and_writes_only_valid_maps(self):
        output = Path(self.temp.name) / 'report.md'
        with patch.object(coverage, 'load_map', return_value=self.cmap), \
                patch.object(coverage, 'bundled_scenarios', return_value=self.scenarios):
            self.assertEqual(cli.main(['coverage', '--check', '--write', str(output)]), 0)
            self.assertEqual(output.read_text(), coverage.coverage_markdown(self.cmap, self.scenarios))
            self.row['status'] = 'invalid'
            self.assertEqual(cli.main(['coverage', '--check']), 1)

    def test_sweep_section_does_not_consume_run_results(self):
        with patch.object(coverage, 'load_map', return_value=self.cmap), \
                patch.object(coverage, 'bundled_scenarios', return_value=self.scenarios):
            rendered = report.render_markdown(report.aggregate([], {'id': 'empty'}))
        self.assertTrue(rendered.endswith(coverage.coverage_markdown(self.cmap, self.scenarios)))


class RuntimeIsolationTests(unittest.TestCase):
    def test_runtime_never_names_or_imports_map(self):
        markers = ('evals/task-coverage', 'task-coverage', 'agentos_eval.coverage', 'rows.json')
        for path in sorted((ROOT / 'src/personal_agent').rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                text = path.read_bytes().decode('utf-8', errors='replace')
                for marker in markers:
                    with self.subTest(path=path.relative_to(ROOT), marker=marker):
                        self.assertNotIn(marker, text)

    def test_missing_optional_validator_is_explicit(self):
        with patch.object(coverage, 'Draft202012Validator', None):
            self.assertIn('schema-validation', coverage.validate(None, [])[0])


@unittest.skipIf(coverage.Draft202012Validator is None, 'requires optional schema-validation extra')
class RealMapTests(unittest.TestCase):
    def test_real_map_validates(self):
        self.assertEqual(coverage.validate(coverage.load_map(), coverage.bundled_scenarios()), [])

    def test_generated_document_is_current(self):
        expected = coverage.coverage_markdown(coverage.load_map(), coverage.bundled_scenarios())
        self.assertEqual((ROOT / 'docs/task-coverage-map.en.md').read_text(encoding='utf-8'), expected)
