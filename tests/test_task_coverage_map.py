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
                self.row.pop(field, None)
                self.assertTrue(self.problems())
                self.row[field] = value
                self.assertEqual(self.problems(), [])
                self.row['status'] = 'fixture-only'
                self.assertTrue(self.problems())
                self.row.pop(field)

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
        self.assertTrue(any('no Still evidence' in p for p in self.problems()))

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
        for text in ['CC BY 4.0', 'O*NET', 'Evidence (class)', '### Untried', '### Blocked by Still',
                     'routing', 'Tool unavailable.', '#462', 'scenario:demo-one', 'judgment:case-one',
                     'U:', 'test_read', '| 0 | 1 | 1 | 0 | 0 | 1 |']:
            self.assertIn(text, markdown)
        self.assertEqual(markdown, coverage.coverage_markdown(self.cmap, self.scenarios))
        for forbidden in ['pass rate', 'ranking', 'target']:
            self.assertNotIn(forbidden, markdown)

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


@unittest.skipUnless((ROOT / 'evals/task-coverage/rows.json').exists(),
                     'Task A has not supplied rows.json yet; integrator must re-run when it lands.')
@unittest.skipIf(coverage.Draft202012Validator is None, 'requires optional schema-validation extra')
class RealMapTests(unittest.TestCase):
    def test_real_map_validates(self):
        self.assertEqual(coverage.validate(coverage.load_map(), coverage.bundled_scenarios()), [])

    def test_generated_document_is_current(self):
        expected = coverage.coverage_markdown(coverage.load_map(), coverage.bundled_scenarios())
        self.assertEqual((ROOT / 'docs/task-coverage-map.en.md').read_text(encoding='utf-8'), expected)
