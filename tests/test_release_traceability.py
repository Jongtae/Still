"""The release artifact must be traceable from this repository.

For most of this program the only links between this repository and the
published build were a prose URL in QUICKSTART.md and a Python default buried
in `delivery.py`.  That was enough for an audit to run `git ls-files`, find no
`.rb` and no checksum, and conclude that no release artifact existed at all --
while a live tap, `Jongtae/homebrew-agentos`, was serving `v1.0.4` and was
installed on the machine doing the audit.  The absence was real; the inference
from it was wrong, and it was repeated into several permanent records.

Still keeps the formula in this repository instead (#45), so the relationship
is now a file. These tests keep it checkable instead of narrated, keep the
state before the first Still release honest, and make the two drift
conditions fail loudly:

* the in-tree version silently equalling the newest published release, which
  makes source indistinguishable from the shipped build;
* a formula template acquiring a fabricated checksum, which would review as
  valid and install nothing.
"""
import json
import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_readme_localization import BREW_INSTALL, readme_release_boundary, visible_prose

TEMPLATE = ROOT / 'deploy' / 'homebrew' / 'agentos.rb.template'
FORMULA = ROOT / 'Formula' / 'agentos.rb'
MANIFEST = ROOT / 'docs' / 'release-manifest.json'
PREDECESSOR_MANIFEST = ROOT / 'docs' / 'history' / 'predecessor-release-manifest.json'
RUNBOOK = ROOT / 'docs' / 'release.en.md'
READMES = ('README.md', 'docs/i18n/README.ko.md', 'docs/i18n/README.ja.md', 'docs/i18n/README.zh-CN.md')

#: This repository is its own tap. Hardcoded on purpose: if the tap moves,
#: this test should fail and make someone update the runbook and the
#: template together.
TAP_COMMAND = 'brew tap jongtae/still https://github.com/Jongtae/Still'
FORMULA_NAME = 'jongtae/still/agentos'

#: The executable body, pinned literally. Changing the formula means changing
#: this too, deliberately, in the same commit.
EXPECTED_FORMULA = '''class Agentos < Formula
  desc "Self-hosted personal agent with browser setup and Telegram"
  homepage "https://github.com/Jongtae/Still"
  url "https://github.com/Jongtae/Still/archive/refs/tags/v__VERSION__.tar.gz"
  sha256 "__SHA256__"
  version_scheme 1
  depends_on "python@3.13"

  def install
    system Formula["python@3.13"].opt_bin/"python3.13", "-m", "venv", libexec
    system libexec/"bin/pip", "install", "#{buildpath}[mcp-host]"
    (bin/"agentos").write <<~PYTHON
      #!#{libexec}/bin/python
      from personal_agent.quickstart import main
      main()
    PYTHON
  end

  def caveats
    <<~EOS
      Run agentos start to open browser setup.
      Data: ~/.local/share/agentos
      To start Still at login and after a restart, without a terminal (macOS):
        #{opt_bin}/agentos service install --cli-path #{opt_bin}/agentos
      Otherwise keep agentos start running to receive Telegram requests.
    EOS
  end

  test do
    assert_match "personal agent", shell_output("#{bin}/agentos --help")
    system libexec/"bin/python", "-c", "from mcp.server.stdio import stdio_server"
  end
end'''


def project_version():
    with open(ROOT / 'pyproject.toml', 'rb') as handle:
        return tomllib.load(handle)['project']['version']


def manifest():
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


def version_key(value):
    """Numeric ordering. ``sorted()`` on the strings puts 1.0.4 after 1.0.10."""
    return tuple(int(part) for part in value.split('.'))


def newest_release():
    """The newest published Still release, or None before the first one."""
    published = manifest()['published']
    return max(published, key=lambda row: version_key(row['version'])) if published else None


def formula_pin(body):
    """The version and SHA-256 that ``Formula/agentos.rb`` installs."""
    version = re.search(r'refs/tags/v(\d+\.\d+\.\d+)\.tar\.gz"', body)
    sha256 = re.search(r'^\s*sha256 "([0-9a-f]{64})"', body, re.M)
    return (version.group(1) if version else None, sha256.group(1) if sha256 else None)


class ReleaseTraceabilityTests(unittest.TestCase):
    def test_the_release_manifest_and_runbook_exist(self):
        self.assertTrue(MANIFEST.is_file())
        self.assertTrue(RUNBOOK.is_file())
        self.assertTrue(TEMPLATE.is_file())

    def test_the_in_tree_version_is_not_a_published_version(self):
        """Source must be distinguishable from the newest shipped build.

        `pyproject.toml` read `1.0.4` while `v1.0.4` was the newest tag, so
        nothing in the tree said whether a checkout was the release or nine
        merged children past it.
        """
        published = {row['version'] for row in manifest()['published']}
        self.assertNotIn(
            project_version(), published,
            'pyproject version equals a published release, so source and '
            'shipped build are indistinguishable by version')

    def test_every_unpublished_entry_refuses_to_invent_a_checksum(self):
        for row in manifest()['unpublished']:
            with self.subTest(version=row['version']):
                self.assertIsNone(row['archive_sha256'])
                self.assertIsNone(row['tag'])
                self.assertTrue(row.get('why_no_checksum'),
                                'an absent checksum must say why')
                # The tagged commit is the squash-merge commit, which does
                # not exist yet. Any SHA recorded here is wrong on merge.
                self.assertIsNone(row['commit'])
                self.assertTrue(row.get('why_no_commit'))

    def test_every_published_entry_carries_a_real_checksum(self):
        for row in manifest()['published']:
            with self.subTest(version=row['version']):
                self.assertRegex(row['archive_sha256'], r'^[0-9a-f]{64}$')
                self.assertEqual(row['tag'], 'v' + row['version'])

    def test_the_current_version_is_in_the_manifest(self):
        known = {row['version'] for row in manifest()['published']}
        known |= {row['version'] for row in manifest()['unpublished']}
        self.assertIn(project_version(), known,
                      'the version in pyproject.toml is not recorded anywhere')

    def test_predecessor_releases_are_history_not_still_releases(self):
        """v1.0.4-v1.4.1 were tags of the private predecessor, kept unchanged."""
        history = json.loads(PREDECESSOR_MANIFEST.read_text(encoding='utf-8'))
        versions = {row['version'] for row in history['published']}
        self.assertIn('1.4.1', versions)
        for row in history['published']:
            with self.subTest(version=row['version']):
                self.assertRegex(row['archive_sha256'], r'^[0-9a-f]{64}$')
        self.assertFalse(versions & {row['version'] for row in manifest()['published']},
                         'a predecessor tag is recorded as a Still release')
        self.assertIn('docs/history/predecessor-release-manifest.json', ' '.join(manifest()['$comment']))

    def test_the_formula_exists_exactly_when_a_release_does(self):
        """Before the first release there is no archive for a formula to name."""
        release = newest_release()
        if release is None:
            self.assertFalse(FORMULA.exists(), 'a formula exists before any Still release')
            return
        version, sha256 = formula_pin(FORMULA.read_text(encoding='utf-8'))
        self.assertIsNotNone(version, 'the formula names no tag archive')
        self.assertIsNotNone(sha256, 'the formula names no SHA-256')
        if version == release['version']:
            self.assertEqual(sha256, release['archive_sha256'])
            return
        # The release workflow merges the formula PR before the record PR adds
        # the release to `published` (docs/release.en.md steps 7 and 9, #301).
        # In between, the formula names the release in progress: the in-tree
        # version, still unpublished and newer than the newest published one.
        self.assertEqual(version, project_version(),
                         'the formula names neither the newest release nor the release in progress')
        self.assertIn(version, {row['version'] for row in manifest()['unpublished']})
        self.assertGreater(version_key(version), version_key(release['version']))

    def test_the_published_formula_matches_the_template(self):
        """A template change must re-sync ``Formula/agentos.rb`` in the same PR.

        ``scripts/dev/release.py formula`` refuses a formula whose body differs
        from the template beyond url and sha256, so drift left here would only
        surface during the next release (#96).
        """
        release = newest_release()
        if release is None:
            self.skipTest('no Still release yet, so no formula')
        sys.path.insert(0, str(SCRIPTS / 'dev'))
        try:
            import release as release_script
        finally:
            sys.path.remove(str(SCRIPTS / 'dev'))
        body = FORMULA.read_text(encoding='utf-8')
        rendered = release_script.render_formula(*formula_pin(body))
        self.assertEqual(release_script._formula_body(body), release_script._formula_body(rendered))

    # -- the template ------------------------------------------------------

    def test_the_template_keeps_its_placeholders(self):
        body = TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('__VERSION__', body)
        self.assertIn('sha256 "__SHA256__"', body)

    def test_the_template_never_carries_a_plausible_fake_checksum(self):
        """A fabricated hex string would review as valid and install nothing."""
        body = TEMPLATE.read_text(encoding='utf-8')
        for found in re.findall(r'sha256\s+"([^"]*)"', body):
            with self.subTest(sha256=found):
                self.assertEqual(found, '__SHA256__')
        self.assertIsNone(re.search(r'\b[0-9a-f]{64}\b', body),
                          'the template contains a 64-hex string; the archive '
                          'it would describe cannot exist before the tag is pushed')

    def test_the_template_has_the_shape_the_tap_formula_has(self):
        body = TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('class Agentos < Formula', body)
        self.assertIn('homepage "https://github.com/Jongtae/Still"', body)
        self.assertIn('url "https://github.com/Jongtae/Still/archive/'
                      'refs/tags/v__VERSION__.tar.gz"', body)
        for block in ('def install', 'def caveats', 'test do'):
            self.assertIn(block, body)

    def test_the_template_records_where_the_published_formula_lives(self):
        """The link the repository was missing."""
        body = TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('Formula/agentos.rb', body)
        self.assertIn('brew tap jongtae/still', body)
        self.assertNotIn('homebrew-agentos', body)

    # -- the claims made to the owner --------------------------------------

    def test_the_runbook_names_the_tap_and_the_post_publication_boundary(self):
        body = RUNBOOK.read_text(encoding='utf-8').replace('\n', ' ')
        self.assertIn(TAP_COMMAND, body)
        self.assertIn('Formula/agentos.rb', body)
        self.assertIn('Before the first release', body)
        self.assertIn('everything below publishes', body)
        self.assertIn('owner_validation_pending', body)

    def test_no_readme_offers_brew_install_without_saying_what_it_installs(self):
        """`brew install` works. Calling it "the current baseline" did not.

        It resolves the newest published release, which can precede Presence
        work on main. Each README must state that boundary within sight of the
        command, not only in QUICKSTART.
        """
        release = newest_release()
        for name in READMES:
            body = (ROOT / name).read_text(encoding='utf-8')
            if BREW_INSTALL not in body:
                continue
            with self.subTest(readme=name):
                self.assertIn(TAP_COMMAND, body)
                index = body.index(BREW_INSTALL)
                window = body[index:index + 1200]
                if release is not None:
                    self.assertIn(release['version'], window,
                                  f'{name} offers brew install without naming the '
                                  f'version it actually installs')
                # Naming the version is not enough. Review replaced the
                # caveat with "v1.0.4, the fully current baseline with every
                # feature described below" -- the exact claim this test
                # exists to prevent -- and it still passed. Before the first
                # release the same slot must say no release exists.
                boundary = readme_release_boundary(name, release)
                self.assertIn(
                    boundary, window,
                    f'{name} must state the published-release/main boundary '
                    f'within 1200 characters of brew install')
                # The raw window begins inside the brew code fence. Normalize
                # the full document so its closing fence keeps that context.
                self.assertIn(
                    visible_prose(boundary), visible_prose(body),
                    f'{name} must state the release boundary as prose, not code')
                for claim in ('current baseline', '최신 빌드', '现行基线'):
                    self.assertNotIn(claim, window,
                                     f'{name} still presents the published '
                                     f'release as current')

    def test_the_template_body_is_the_expected_formula_and_nothing_else(self):
        """The part that actually runs on the owner's machine.

        The other template tests pin four strings -- class, homepage, url and
        the placeholder slots -- and leave the install block free. Review
        inserted a ``curl … | sh`` into ``def install`` and all twelve tests
        passed. This is a file whose whole purpose is to be rendered into
        something Homebrew executes, so the executable body is pinned
        literally and a change has to be made deliberately here.
        """
        body = TEMPLATE.read_text(encoding='utf-8')
        # Everything from the class declaration on; the comment header above
        # it is documentation and may change freely.
        formula = body[body.index('class Agentos < Formula'):]
        self.assertEqual(formula.strip(), EXPECTED_FORMULA.strip())

    def test_the_template_contains_no_shell_execution(self):
        """A second, independent net: pinning equality above could be updated
        carelessly, so name the shapes that must never appear at all."""
        body = TEMPLATE.read_text(encoding='utf-8').casefold()
        for forbidden in ('curl', 'wget', '/bin/sh', '/bin/bash', 'eval',
                          'base64', 'system "sh"', 'popen'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_the_executable_remediation_names_a_formula_that_exists(self):
        """`brew reinstall personal-agentos` fails: that is the PyPI name.

        This string is the advice given on the one path that fires when the
        installed executable cannot be resolved, so it misfired exactly when
        the owner was already stuck.
        """
        body = (ROOT / 'src' / 'personal_agent' / 'service_control.py').read_text(encoding='utf-8')
        self.assertIn('brew reinstall ' + FORMULA_NAME, body)
        self.assertNotIn('brew reinstall personal-agentos', body)


if __name__ == '__main__':
    unittest.main()
