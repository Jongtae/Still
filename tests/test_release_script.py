"""The release workflow's file edits, checked on temporary copies (#1472).

`.github/workflows/release.yml` makes every GitHub call; `scripts/dev/release.py`
makes every repository edit. These tests run the edits against copies of the
real files, then hold the result to the same invariants
tests/test_release_traceability.py and tests/test_install_script.py hold main
to, and pin the workflow's authority shape: nothing publishes before the
`release` environment (owner approval) and its credential.
"""
import json
import re
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'dev'))

import release  # noqa: E402

WORKFLOW = ROOT / '.github' / 'workflows' / 'release.yml'
SHA = 'a' * 64
COMMIT = 'b' * 40


class ReleaseScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in (release.MANIFEST, release.INSTALLER, release.PYPROJECT, *release.RELEASE_PROSE):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, target)
        self.previous = release.newest_published(release.load_manifest(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, version='9.0.0', not_claimed='No live order was observed.'):
        """What a release-preparation PR does: name the version, state its boundary."""
        pyproject = self.root / release.PYPROJECT
        pyproject.write_text(re.sub(r'^version = "[^"]+"$', f'version = "{version}"',
                                    pyproject.read_text(encoding='utf-8'), count=1, flags=re.M),
                             encoding='utf-8')
        data = release.load_manifest(self.root)
        data['unpublished'] = [{
            'version': version, 'tag': None, 'commit': None, 'why_no_commit': 'not merged',
            'archive_sha256': None, 'state': 'unpublished', 'why_no_checksum': 'not tagged',
            'carries': 'Brokerage connections: reads run freely, every order asks first.',
            'not_claimed': not_claimed,
        }]
        release.save_manifest(self.root, data)

    def test_the_post_release_placeholder_is_not_publishable(self):
        """The entry `record` leaves behind, not whatever main holds today."""
        self.prepare(version='9.0.1')
        data = release.load_manifest(self.root)
        del data['unpublished'][0]['not_claimed']
        release.save_manifest(self.root, data)
        with self.assertRaisesRegex(release.ReleaseError, 'not_claimed'):
            release.check(self.root)

    def test_a_prepared_release_is_publishable(self):
        self.prepare()
        self.assertEqual(release.check(self.root), '9.0.0')

    def test_a_release_must_say_what_it_does_not_claim(self):
        self.prepare(not_claimed='  ')
        with self.assertRaisesRegex(release.ReleaseError, 'not_claimed'):
            release.check(self.root)

    def test_a_version_not_newer_than_the_published_one_is_refused(self):
        self.prepare(version=self.previous['version'])
        with self.assertRaisesRegex(release.ReleaseError, 'not newer'):
            release.check(self.root)

    def test_notes_carry_the_scope_and_the_boundary(self):
        self.prepare()
        body = release.notes(self.root, '9.0.0', 'https://github.com/Jongtae/agentos/milestone/99')
        self.assertIn('every order asks first', body)
        self.assertIn('**Not claimed:** No live order was observed.', body)
        self.assertIn('milestone/99', body)

    def test_record_leaves_main_in_the_state_the_traceability_tests_require(self):
        self.prepare()
        following = release.record(self.root, '9.0.0', COMMIT, SHA, '2026-11-02',
                                   'https://github.com/Jongtae/homebrew-agentos/pull/99')
        self.assertEqual(following, '9.0.1')
        data = release.load_manifest(self.root)
        published = release.newest_published(data)
        self.assertEqual((published['version'], published['tag'], published['commit'], published['archive_sha256']),
                         ('9.0.0', 'v9.0.0', COMMIT, SHA))
        self.assertEqual(published['not_claimed'], 'No live order was observed.')
        self.assertIn('not yet recorded', published['observed'])
        with open(self.root / release.PYPROJECT, 'rb') as handle:
            in_tree = tomllib.load(handle)['project']['version']
        self.assertEqual(in_tree, '9.0.1')
        self.assertNotIn(in_tree, {row['version'] for row in data['published']})
        [placeholder] = data['unpublished']
        self.assertEqual(placeholder['version'], '9.0.1')
        self.assertIsNone(placeholder['tag'])
        self.assertIsNone(placeholder['commit'])
        self.assertIsNone(placeholder['archive_sha256'])
        self.assertNotIn('not_claimed', placeholder)
        installer = (self.root / release.INSTALLER).read_text(encoding='utf-8')
        self.assertIn('AGENTOS_VERSION="9.0.0"', installer)
        self.assertIn(f'AGENTOS_ARCHIVE_SHA256="{SHA}"', installer)

    def test_record_moves_every_release_status_sentence_to_the_new_version(self):
        self.prepare()
        release.record(self.root, '9.0.0', COMMIT, SHA, '2026-11-02', 'tap-pr')
        old = 'v' + self.previous['version']
        for name in release.RELEASE_PROSE:
            with self.subTest(name=name):
                text = (self.root / name).read_text(encoding='utf-8')
                self.assertIn('v9.0.0', text)
                self.assertNotRegex(text, re.escape(old) + r'(?![\d.])')
        for name in release.READMES:
            with self.subTest(name=name):
                self.assertIn('2026-11-02', (self.root / name).read_text(encoding='utf-8'))

    def test_record_refuses_a_reworded_status_sentence(self):
        self.prepare()
        readme = self.root / 'README.md'
        readme.write_text(readme.read_text(encoding='utf-8').replace('v' + self.previous['version'], 'the latest'),
                          encoding='utf-8')
        with self.assertRaisesRegex(release.ReleaseError, 'README.md no longer names'):
            release.record(self.root, '9.0.0', COMMIT, SHA, '2026-11-02', 'tap-pr')

    def test_record_refuses_values_that_are_not_measurements(self):
        self.prepare()
        for commit, sha in (('main', SHA), (COMMIT, 'latest'), (COMMIT, 'A' * 64)):
            with self.subTest(commit=commit, sha=sha), self.assertRaises(release.ReleaseError):
                release.record(self.root, '9.0.0', commit, sha, '2026-11-02', 'tap-pr')

    def published_formula(self):
        """The tap formula as published: the template body with real values, no header."""
        template = (ROOT / 'deploy' / 'homebrew' / 'agentos.rb.template').read_text(encoding='utf-8')
        body = template[template.index('class Agentos'):]
        formula = self.root / 'agentos.rb'
        formula.write_text(body.replace('__VERSION__', '1.3.0').replace('__SHA256__', 'c' * 64), encoding='utf-8')
        return formula

    def test_formula_points_at_the_tag_archive_and_its_checksum(self):
        formula = self.published_formula()
        release.formula(formula, 'Jongtae/agentos', '9.0.0', SHA)
        text = formula.read_text(encoding='utf-8')
        self.assertIn('url "https://github.com/Jongtae/agentos/archive/refs/tags/v9.0.0.tar.gz"', text)
        self.assertIn(f'sha256 "{SHA}"', text)

    def test_formula_refuses_a_tap_body_that_drifted_from_the_template(self):
        """`brew test` runs the formula's own test, so a weakened body would pass it."""
        formula = self.published_formula()
        before = formula.read_text(encoding='utf-8').replace('[mcp-host]', '')
        formula.write_text(before, encoding='utf-8')
        with self.assertRaisesRegex(release.ReleaseError, 'differs from'):
            release.formula(formula, 'Jongtae/agentos', '9.0.0', SHA)
        self.assertEqual(formula.read_text(encoding='utf-8'), before)

    def test_formula_never_takes_the_template_placeholder(self):
        """The in-repo template keeps `__SHA256__`; it is not a render source."""
        formula = self.root / 'agentos.rb'
        shutil.copy(ROOT / 'deploy' / 'homebrew' / 'agentos.rb.template', formula)
        with self.assertRaisesRegex(release.ReleaseError, 'sha256'):
            release.formula(formula, 'Jongtae/agentos', '9.0.0', SHA)

    def test_pin_moves_every_readme_installer_url(self):
        release.pin(self.root, COMMIT)
        for name in release.READMES:
            with self.subTest(name=name):
                pins = {m.group(2) for m in release.PIN.finditer((self.root / name).read_text(encoding='utf-8'))}
                self.assertEqual(pins, {COMMIT})


class ReleaseWorkflowShapeTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
        self.jobs = self.workflow['jobs']

    def test_it_runs_only_from_main(self):
        self.assertEqual(self.workflow[True], {'push': {'branches': ['main'],
                                                        'paths': ['pyproject.toml', 'docs/release-manifest.json']}})
    def test_the_read_only_job_holds_no_secret_and_no_write_permission(self):
        prepare = json.dumps(self.jobs['prepare'])
        self.assertNotIn('secrets.', prepare)
        self.assertEqual(self.jobs['prepare']['permissions'],
                         {'contents': 'read', 'pull-requests': 'read', 'issues': 'read'})
        self.assertEqual(self.workflow['permissions'], {'contents': 'read'})

    def test_credentialed_code_runs_from_the_approved_commit(self):
        """A newer main merged during the approval wait must not run with the token."""
        steps = self.jobs['publish']['steps']
        checkout = next(step for step in steps if step.get('uses', '').startswith('actions/checkout'))
        self.assertEqual(checkout['with']['ref'], '${{ github.sha }}')
        for step in steps:
            run = step.get('run', '')
            if 'origin/main' in run:
                with self.subTest(step=step.get('name')):
                    self.assertIn('worktree add', run)
                    self.assertIn('--root "$tree"', run)

    def test_publication_waits_for_validate_on_the_release_commit(self):
        steps = self.jobs['publish']['steps']
        gate = next(i for i, step in enumerate(steps) if 'check_name=validate' in step.get('run', ''))
        first_mutation = next(i for i, step in enumerate(steps) if 'gh release create' in step.get('run', ''))
        self.assertLess(gate, first_mutation)
        self.assertIn('completed:success', steps[gate]['run'])

    def test_a_rerun_accepts_only_a_merged_pr(self):
        for step in self.jobs['publish']['steps']:
            run = step.get('run', '')
            if 'gh pr create' in run:
                with self.subTest(step=step.get('name')):
                    self.assertIn('MERGED)', run)
                    self.assertIn('closed without merging', run)

    def test_publication_waits_for_the_release_environment_and_its_credential(self):
        publish = self.jobs['publish']
        self.assertEqual(publish['environment'], 'release')
        self.assertEqual(publish['needs'], 'prepare')
        steps = publish['steps']
        self.assertIn('RELEASE_TOKEN', steps[0]['run'])
        self.assertIn('exit 1', steps[0]['run'])
        first_mutation = next(i for i, step in enumerate(steps) if 'gh release create' in step.get('run', ''))
        self.assertGreater(first_mutation, 0)

    def test_the_tap_merges_only_after_its_checks_pass(self):
        tap = next(step for step in self.jobs['publish']['steps'] if step.get('id') == 'tap')['run']
        self.assertLess(tap.index('gh pr checks'), tap.index('gh pr merge'))
        self.assertIn('--fail-fast', tap)

    def test_every_wait_is_bounded(self):
        self.assertIn('timeout-minutes', self.jobs['publish'])
        for step in self.jobs['publish']['steps']:
            if 'gh pr checks' in step.get('run', '') or 'sleep 30' in step.get('run', ''):
                with self.subTest(step=step.get('name')):
                    self.assertIn('timeout-minutes', step)


if __name__ == '__main__':
    unittest.main()
