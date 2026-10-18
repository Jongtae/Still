"""The release workflow's file edits, checked on temporary copies (#1472).

`.github/workflows/release.yml` makes every GitHub call; `scripts/dev/release.py`
makes every repository edit. These tests run the edits against copies of the
real files, then hold the result to the same invariants
tests/test_release_traceability.py and tests/test_install_script.py hold main
to, and pin the workflow's authority shape: nothing publishes before the
`release` environment (owner approval) and its credential. Both the first Still
release (#45) and a later one are exercised.
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

    def tearDown(self):
        self.tmp.cleanup()

    def publish_previous(self, version='8.0.0', tag_date='2026-10-30'):
        """Put the copy in the state a recorded earlier release leaves behind."""
        data = release.load_manifest(self.root)
        if data['published']:
            return release.newest_published(data)
        data['published'].append({'version': version, 'tag': f'v{version}', 'commit': COMMIT,
                                  'archive_sha256': 'd' * 64, 'state': 'published', 'tag_date': tag_date})
        release.save_manifest(self.root, data)
        for name in release.RELEASE_PROSE:
            before, after = release.FIRST_RELEASE_PROSE[name]
            path = self.root / name
            path.write_text(path.read_text(encoding='utf-8').replace(
                before, after.format(version=version, date=tag_date)), encoding='utf-8')
        return data['published'][-1]

    def before_first_release(self):
        """Put the copy in the state before the first Still release (#45), whatever main holds today."""
        data = release.load_manifest(self.root)
        newest = release.newest_published(data)
        if newest is None:
            return
        for name in release.RELEASE_PROSE:
            before, after = release.FIRST_RELEASE_PROSE[name]
            path, released = self.root / name, after.format(version=newest['version'], date=newest['tag_date'])
            text = path.read_text(encoding='utf-8')
            self.assertIn(released, text, name)
            path.write_text(text.replace(released, before), encoding='utf-8')
        installer = self.root / release.INSTALLER
        installer.write_text(re.sub(r'^(AGENTOS_VERSION|AGENTOS_ARCHIVE_SHA256)="[^"]*"$', r'\1=""',
                                    installer.read_text(encoding='utf-8'), flags=re.M), encoding='utf-8')
        data['published'] = []
        release.save_manifest(self.root, data)

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

    def test_the_first_release_needs_no_earlier_one(self):
        self.before_first_release()
        self.assertIsNone(release.newest_published(release.load_manifest(self.root)))
        self.prepare(version='0.1.0')
        self.assertEqual(release.check(self.root), '0.1.0')

    def test_a_version_not_newer_than_the_published_one_is_refused(self):
        previous = self.publish_previous()
        self.prepare(version=previous['version'])
        with self.assertRaisesRegex(release.ReleaseError, 'not newer'):
            release.check(self.root)

    def test_notes_carry_the_scope_and_the_boundary(self):
        self.prepare()
        body = release.notes(self.root, '9.0.0', 'https://github.com/Jongtae/Still/milestone/99')
        self.assertIn('every order asks first', body)
        self.assertIn('**Not claimed:** No live order was observed.', body)
        self.assertIn('milestone/99', body)

    def test_record_leaves_main_in_the_state_the_traceability_tests_require(self):
        self.prepare()
        following = release.record(self.root, '9.0.0', COMMIT, SHA, '2026-11-02',
                                   'https://github.com/Jongtae/Still/pull/99')
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

    def test_the_first_record_replaces_every_no_release_sentence(self):
        self.before_first_release()
        self.prepare(version='0.1.0')
        release.record(self.root, '0.1.0', COMMIT, SHA, '2026-11-02', 'formula-pr')
        for name in release.RELEASE_PROSE:
            before, after = release.FIRST_RELEASE_PROSE[name]
            with self.subTest(name=name):
                text = (self.root / name).read_text(encoding='utf-8')
                self.assertNotIn(before, text)
                self.assertIn(after.format(version='0.1.0', date='2026-11-02'), text)
        installer = (self.root / release.INSTALLER).read_text(encoding='utf-8')
        self.assertIn('AGENTOS_VERSION="0.1.0"', installer)

    def test_the_first_record_refuses_a_reworded_no_release_sentence_and_changes_nothing(self):
        self.before_first_release()
        self.prepare(version='0.1.0')
        status = self.root / 'docs/product-status.ko.md'
        before, _after = release.FIRST_RELEASE_PROSE['docs/product-status.ko.md']
        status.write_text(status.read_text(encoding='utf-8').replace(before, '배포 예정입니다.'), encoding='utf-8')
        readme_before = (self.root / 'README.md').read_text(encoding='utf-8')
        with self.assertRaisesRegex(release.ReleaseError, 'product-status.ko.md no longer carries'):
            release.record(self.root, '0.1.0', COMMIT, SHA, '2026-11-02', 'formula-pr')
        self.assertEqual((self.root / 'README.md').read_text(encoding='utf-8'), readme_before)

    def test_record_moves_every_release_status_sentence_to_the_new_version(self):
        previous = self.publish_previous()
        self.prepare()
        release.record(self.root, '9.0.0', COMMIT, SHA, '2026-11-02', 'formula-pr')
        old = 'v' + previous['version']
        for name in release.RELEASE_PROSE:
            with self.subTest(name=name):
                text = (self.root / name).read_text(encoding='utf-8')
                self.assertIn('v9.0.0', text)
                self.assertNotRegex(text, re.escape(old) + r'(?![\d.])')
        for name in release.READMES:
            with self.subTest(name=name):
                self.assertIn('2026-11-02', (self.root / name).read_text(encoding='utf-8'))

    def test_record_refuses_a_reworded_status_sentence(self):
        previous = self.publish_previous()
        self.prepare()
        readme = self.root / 'README.md'
        readme.write_text(readme.read_text(encoding='utf-8').replace('v' + previous['version'], 'the latest'),
                          encoding='utf-8')
        with self.assertRaisesRegex(release.ReleaseError, 'README.md no longer names'):
            release.record(self.root, '9.0.0', COMMIT, SHA, '2026-11-02', 'formula-pr')

    def test_record_refuses_values_that_are_not_measurements(self):
        self.prepare()
        for commit, sha in (('main', SHA), (COMMIT, 'latest'), (COMMIT, 'A' * 64)):
            with self.subTest(commit=commit, sha=sha), self.assertRaises(release.ReleaseError):
                release.record(self.root, '9.0.0', commit, sha, '2026-11-02', 'formula-pr')

    def test_the_first_formula_is_written_from_the_template(self):
        """No formula exists before the first release; `formula` creates it."""
        self.assertFalse((self.root / release.FORMULA).exists())
        release.formula(self.root, '0.1.0', SHA)
        text = (self.root / release.FORMULA).read_text(encoding='utf-8')
        self.assertTrue(text.startswith(release.FORMULA_HEADER))
        self.assertIn('url "https://github.com/Jongtae/Still/archive/refs/tags/v0.1.0.tar.gz"', text)
        self.assertIn(f'sha256 "{SHA}"', text)
        self.assertNotIn('__', text)

    def test_formula_moves_an_existing_formula_to_the_new_archive(self):
        release.formula(self.root, '0.1.0', 'c' * 64)
        release.formula(self.root, '9.0.0', SHA)
        text = (self.root / release.FORMULA).read_text(encoding='utf-8')
        self.assertIn('url "https://github.com/Jongtae/Still/archive/refs/tags/v9.0.0.tar.gz"', text)
        self.assertIn(f'sha256 "{SHA}"', text)
        self.assertNotIn('v0.1.0', text)

    def test_formula_refuses_a_formula_that_drifted_from_the_template(self):
        """`brew test` runs the formula's own test, so a weakened body would pass it."""
        release.formula(self.root, '0.1.0', 'c' * 64)
        path = self.root / release.FORMULA
        before = path.read_text(encoding='utf-8').replace('[mcp-host]', '')
        path.write_text(before, encoding='utf-8')
        with self.assertRaisesRegex(release.ReleaseError, 'differs from'):
            release.formula(self.root, '9.0.0', SHA)
        self.assertEqual(path.read_text(encoding='utf-8'), before)

    def test_formula_never_takes_the_template_placeholder(self):
        """A formula holding the template's `__SHA256__` slot is not a release formula."""
        path = self.root / release.FORMULA
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / 'deploy' / 'homebrew' / 'agentos.rb.template', path)
        with self.assertRaisesRegex(release.ReleaseError, 'sha256'):
            release.formula(self.root, '9.0.0', SHA)

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

    def test_every_workflow_pr_merges_only_after_its_checks_pass(self):
        """#79: auto-merge needs branch protection on main, so no step relies on it."""
        steps = [step for step in self.jobs['publish']['steps'] if 'gh pr create' in step.get('run', '')]
        self.assertEqual(len(steps), 3)
        for step in steps:
            run = step['run']
            with self.subTest(step=step.get('name')):
                self.assertLess(run.index('gh pr checks'), run.index('gh pr merge'))
                self.assertIn('--fail-fast', run)
                self.assertIn('--repo "$GITHUB_REPOSITORY"', run)
                self.assertNotIn('--auto', run)

    def test_no_separate_tap_is_written(self):
        """The formula lives in this repository (#45)."""
        text = WORKFLOW.read_text(encoding='utf-8')
        self.assertNotIn('homebrew-agentos', text)
        self.assertNotIn('TAP', text)

    def test_every_wait_is_bounded(self):
        self.assertIn('timeout-minutes', self.jobs['publish'])
        for step in self.jobs['publish']['steps']:
            if 'gh pr checks' in step.get('run', '') or 'sleep 30' in step.get('run', ''):
                with self.subTest(step=step.get('name')):
                    self.assertIn('timeout-minutes', step)


if __name__ == '__main__':
    unittest.main()
