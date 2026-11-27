"""#1291: a Work adds a fitting Still reference skill from its pin, says so in passing, and removal sticks.

Evidence class: model-free unit tests over the real ``SkillLibrary``,
``SkillBinding``, ``Capabilities`` and settings path, with a fake GitHub
transport and a temporary reference list.  No network, model or live store.
"""
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

from personal_agent.agent_runtime import SKILLS_HEADING, Capabilities, ToolError
from personal_agent.information_use import READ_CATEGORIES, _event_items
from personal_agent.messages import msg
from personal_agent.skills import (REFERENCE_MARK, REFERENCES_FILE, SkillBinding, SkillLibrary, reference_skills,
                                   tree_digest)
from test_owner_settings_conversation import _Case
from test_skill_supply import ADDRESS, CFG, COMMIT, PINNED, UPSTREAM, FakeGitHub, tarball, upstream_files

ROOT = Path(__file__).resolve().parents[1]
SKILL = 'internal-comms/internal-comms'
FORK, FORKED = 'someone/fork', 'c' * 40
#: The same content at an address outside Still's curated list.
OTHER = f'https://github.com/{FORK}/tree/{FORKED}/skills/internal-comms'


class AutoAdopt(_Case):
    def setUp(self):
        super().setUp()
        self.github = FakeGitHub({COMMIT: tarball(UPSTREAM, COMMIT, upstream_files()),
                                  FORKED: tarball(FORK, FORKED, upstream_files())}, {'main': COMMIT})
        self.library = SkillLibrary(self.store, transport=self.github)
        self.service.skill_transport = self.github
        self.job = self.store.job(self.store.enqueue('Write the project update.', 'original'))
        self.references(tree_digest(PINNED))

    def references(self, digest, address=ADDRESS.removeprefix('https://')):
        path = Path(self.store.root) / 'skill_references.json'
        path.write_text(json.dumps([{'name': 'internal-comms', 'description': 'Internal update writing.',
                                     'address': address, 'digest': digest}]))
        patcher = mock.patch('personal_agent.skills.reference_skills', side_effect=lambda: reference_skills(path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def caps(self, binding=None):
        self.events = []
        return Capabilities(self.store, None, CFG, '', self.job['id'], lambda *a: self.events.append(a),
                            skills=binding if binding is not None else self.library.binding(),
                            settings=self.service.settings_tools(self.job))

    def manifests(self):
        return sorted(path.name for path in self.library.root.glob('*.json'))

    def test_a_fresh_store_offers_the_pinned_reference_and_nothing_is_fetched_yet(self):
        binding = self.library.binding()
        self.assertIsNotNone(binding)
        self.assertIn(f'- {SKILL}: Internal update writing.{REFERENCE_MARK}', binding.catalogue_text())
        self.assertIn('pinned revision', SKILLS_HEADING)
        self.assertEqual(self.github.urls, [])
        self.assertEqual(self.manifests(), [])

    def test_skill_load_adds_it_from_the_pin_switches_skills_on_and_tells_the_ai_to_say_so(self):
        caps = self.caps()
        loaded = caps.execute('skill_load', {'skill': SKILL})
        self.assertTrue(loaded['adopted'])
        self.assertTrue(loaded['skills_switched_on'])
        self.assertEqual((loaded['revision'], loaded['digest']), (COMMIT, tree_digest(PINNED)))
        self.assertIn(msg('skills.adopted_note', skill='internal-comms'), loaded['note'])
        self.assertIn(msg('skills.adopted_switched_note'), loaded['note'])
        self.assertTrue(loaded['instructions'])
        self.assertEqual(self.github.urls, [f'https://codeload.github.com/{UPSTREAM}/tar.gz/{COMMIT}'])
        self.assertTrue(self.library.enabled())
        self.assertEqual(self.manifests(), ['internal-comms.json'])
        # The Work's record and the information-use view name the added skill and its pin.
        evidence = caps.evidence[-1]['result']
        self.assertEqual((evidence['adopted'], evidence['reference'], evidence['digest']),
                         (True, ADDRESS.removeprefix('https://'), tree_digest(PINNED)))
        self.assertEqual(READ_CATEGORIES['skill_load'], 'skills')
        label = _event_items('skill_load', evidence)[0]
        self.assertIn(SKILL, label)
        self.assertIn(COMMIT, label)
        # Resources open after the load; a second load is an ordinary one (no second fetch, no note).
        self.assertIn('content', caps.execute('skill_resource', {'skill': SKILL, 'path': 'examples/faq-answers.md'}))
        again = caps.execute('skill_load', {'skill': SKILL})
        self.assertNotIn('adopted', again)
        self.assertEqual(len(self.github.urls), 1)
        # A later Work sees it as installed, not as a reference.
        self.assertNotIn(REFERENCE_MARK, self.library.binding().catalogue_text())

    def test_a_cli_bridge_rebuilds_the_reference_from_its_ref_and_adopts_it_there(self):
        refs = self.library.binding().refs()
        bridge = SkillBinding.from_refs(SkillLibrary(self.store, transport=self.github), refs)
        self.assertTrue(bridge.load(SKILL)['adopted'])
        # The host's binding of the same Work finds it installed and does not fetch again.
        host = SkillBinding.from_refs(self.library, refs)
        self.assertNotIn('adopted', host.load(SKILL))
        self.assertEqual(len(self.github.urls), 1)

    def test_content_that_differs_from_the_pin_is_refused_and_nothing_is_installed(self):
        self.references('0' * 64)
        caps = self.caps()
        with self.assertRaises(ToolError) as refused:
            caps.execute('skill_load', {'skill': SKILL})
        self.assertEqual(refused.exception.code, 'pin_mismatch')
        self.assertEqual(self.manifests(), [])
        self.assertFalse(self.library.content.exists() and any(self.library.content.iterdir()))
        self.assertFalse(self.library.enabled())

    def test_an_owner_switch_off_during_the_download_cancels_the_adoption(self):
        """Codex P2 on #1294: an explicit off made while the skill downloads is never overwritten."""
        fetch = self.library.transport
        def switch_off(url, *args, **kwargs):
            self.library.set_enabled(False)
            return fetch(url, *args, **kwargs)
        self.library.transport = switch_off
        with self.assertRaises(ToolError) as cancelled:
            self.caps().execute('skill_load', {'skill': SKILL})
        self.assertEqual(cancelled.exception.code, 'skill_unavailable')
        self.assertFalse(self.library.enabled())
        self.assertEqual(self.manifests(), [])

    def test_an_unpinned_reference_is_not_offered(self):
        self.references(tree_digest(PINNED), address=ADDRESS.replace(COMMIT, 'main').removeprefix('https://'))
        self.assertEqual(self.library.adoptable(), [])
        self.assertIsNone(self.library.binding())

    def test_a_reference_header_is_not_fetched(self):
        header = self.caps().execute('skill_load', {'skill': SKILL, 'part': 'header'})
        self.assertEqual((header['header'], header['note']), ('', msg('skills.reference_header_note')))
        self.assertEqual(self.github.urls, [])

    def test_an_address_outside_the_curated_list_still_needs_the_owner(self):
        caps = self.caps()
        with self.assertRaises(ToolError) as unlisted:
            caps.execute('skill_load', {'skill': 'fork/internal-comms'})
        self.assertEqual(unlisted.exception.code, 'skill_unavailable')
        draft = self.settings.propose('owner', 'web', 'skills', 'add', OTHER)
        self.assertEqual(draft['state'], 'awaiting-confirmation')
        self.assertTrue(draft['requires_owner_confirmation'])
        self.assertEqual(self.manifests(), [])
        candidate = self.caps().execute('capability_search', {'source': 'skill', 'query': OTHER})
        self.assertEqual(candidate['note'], msg('quickstart_service.skill_candidate_inspected'))
        curated = self.caps().execute('capability_search', {'source': 'skill', 'query': 'internal-comms'})
        self.assertEqual(curated['note'], msg('quickstart_service.skill_candidate_adoptable'))

    def test_skills_switched_off_by_the_owner_offers_nothing(self):
        self.library.set_enabled(False)
        self.assertEqual(self.library.adoptable(), [])
        self.assertIsNone(self.library.binding())

    def test_a_removed_reference_is_never_added_again_unless_the_owner_asks(self):
        self.caps().execute('skill_load', {'skill': SKILL})
        old = self.library.binding().refs()
        self.service.apply_skill_setting('remove', self.service.skill_setting_value('remove', 'internal-comms'))
        self.assertEqual(self.manifests(), [])
        self.assertEqual(self.library.declined(), {'internal-comms'})
        self.assertNotIn(SKILL, self.library.binding().entries if self.library.binding() else {})
        # A Work bound before the removal cannot add it back either.
        stale = SkillBinding.from_refs(self.library, old)
        with self.assertRaises(Exception):
            stale.load(SKILL)
        self.assertEqual(self.manifests(), [])
        self.assertEqual(len(self.github.urls), 1)
        # The owner's own add brings it back and clears the refusal.
        self.service.apply_skill_setting('add', self.service.skill_setting_value('add', 'internal-comms'))
        self.assertEqual(self.manifests(), ['internal-comms.json'])
        self.assertEqual(self.library.declined(), set())

    def test_adding_a_skill_grants_no_tool(self):
        before = set(self.caps().offered_tools())
        self.caps().execute('skill_load', {'skill': SKILL})
        self.assertEqual(set(self.caps().offered_tools()) - before, set())


def test_every_published_reference_is_pinned_to_a_commit_and_digest():
    rows = json.loads(REFERENCES_FILE.read_text(encoding='utf-8'))
    listed = {row['name']: row for row in reference_skills()}
    for row in rows:
        assert listed[row['name']]['pinned'], row['name']
        assert re.search(r'/tree/[0-9a-f]{40}/skills/' + re.escape(row['name']) + '$', row['address']), row['name']
    commit = re.search(r'/tree/([0-9a-f]{40})/', rows[0]['address']).group(1)
    present = subprocess.run(['git', 'cat-file', '-e', f'{commit}^{{commit}}'], cwd=ROOT, capture_output=True).returncode == 0
    if present:   # a shallow CI checkout may not hold the pinned commit
        check = subprocess.run([sys.executable, 'scripts/pin_skill_references.py', '--check'], cwd=ROOT,
                               capture_output=True, text=True, env={'PYTHONPATH': 'src', 'PATH': '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin'})
        assert check.returncode == 0, check.stderr
