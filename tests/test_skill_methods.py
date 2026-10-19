"""QUALITY-METHODS-01 (#32): reusable analysis methods, bounded header reads and an advisory method gap.

Evidence classes: content checks of the three method skills Still publishes;
unit tests of ``SkillBinding`` header reads and gap notes; model-free runs of
``run_agent`` with a scripted model transport and a fixture outcome judgment;
and the package boundary a CLI bridge rebuilds from recorded tool events.  A
fake GitHub transport serves this repository's own ``skills/`` folders.  No
live model, site, account or network is used, so nothing here shows how a
live model applies a method.

Reused, not repeated here: install pinning, licence and containment
(``test_skill_supply``); the reference-skill settings flow (``test_skill_shop``);
no core branch on a site, provider or category (``test_no_scenario_code``).
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from personal_agent.agent_runtime import Capabilities, ToolError, evidence_summary, run_agent
from personal_agent.plugins import PluginRegistry
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore
from personal_agent.skills import (MAX_GAP_CHARS, MAX_GAP_NOTES, MAX_HEADER_BYTES, MAX_HEADER_READS, SkillBinding,
                                   SkillError, SkillLibrary, inspect_skill, parse_frontmatter, reference_skills, usage_header)
from test_agency_loop import CFG, Script, call, finish, judgments
from test_no_scenario_code import SITE_TOKENS
from test_skill_shop import COMMIT, REPO, archive

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src' / 'personal_agent'
METHODS = ('summary-or-analysis', 'claim-analysis', 'source-cross-check')
SKILL = {name: f'{name}/{name}' for name in METHODS}


class GitHub:
    """Serves the method folders of this repository at one fake commit."""

    def __init__(self):
        self.data = archive({f'skills/{name}': ROOT / 'skills' / name for name in METHODS})

    def __call__(self, url, limit=None, timeout=None):
        return COMMIT.encode() if url.startswith('https://api.github.com/') else self.data


def method_text(name):
    return (ROOT / 'skills' / name / 'SKILL.md').read_text()


class MethodContent(unittest.TestCase):
    """The methods are reusable content: supported, owner-removable, about evidence and form, never a probe."""

    def test_each_method_is_supported_instruction_only_content_listed_for_the_owner_to_add(self):
        listed = {row['name']: row for row in reference_skills()}
        guide = (SRC / 'bundled_skills' / 'agentos-skills' / 'SKILL.md').read_text()
        for name in METHODS:
            with self.subTest(method=name):
                record = inspect_skill(ROOT / 'skills' / name)
                self.assertEqual((record['status'], record['licence']), (['supported_as_is'], 'AGPL-3.0-only'))
                self.assertEqual(listed[name]['address'], f'github.com/{REPO}/tree/main/skills/{name}')
                self.assertIn(f'https://github.com/{REPO}/tree/main/skills/{name}', guide)

    def test_each_method_opens_with_a_bounded_usage_header(self):
        for name in METHODS:
            with self.subTest(method=name):
                _frontmatter, body = parse_frontmatter(method_text(name))
                header, cut = usage_header(body)
                self.assertFalse(cut, 'the whole usage header fits the header read')
                self.assertIn('Use this', header)
                self.assertIn('Do not use it', header)
                self.assertNotIn('\n## ', '\n' + header)

    def test_the_methods_cover_evidence_disagreement_uncertainty_and_justified_conclusions(self):
        claim, check = method_text('claim-analysis'), method_text('source-cross-check')
        for phrase in ('Separate', 'counter-evidence', 'disagree', 'Say how sure you are', 'Conclude only what the evidence justifies',
                       'Do not present a claim as checked when you only read it in the material'):
            self.assertIn(phrase.lower(), claim.lower())
        for phrase in ('origin', 'independent', 'Date', 'Definition', 'Single source', 'Conflicting', 'Unverifiable', 'do not average them'):
            self.assertIn(phrase.lower(), check.lower())

    def test_completion_is_shaped_by_the_requested_form_not_the_topic_or_a_count(self):
        text = method_text('summary-or-analysis')
        for form in ('**Summary:**', '**Comparison:**', '**Analysis:**', '**Recommendation:**'):
            self.assertIn(form, text.split('## 3. When the answer is finished', 1)[1])
        self.assertIn('judged by its form, never by its length or by a fixed number of points', text)
        self.assertIn('a starting guess, not a rule', text)
        self.assertIn('The kind of answer decides what you do and when you are finished; the topic does not.', text)

    def test_no_method_names_a_site_provider_or_probe_and_no_core_module_names_a_method(self):
        tokens = re.compile(r'(?<![a-z0-9])(' + '|'.join(map(re.escape, SITE_TOKENS)) + r')')
        for name in METHODS:
            with self.subTest(method=name):
                self.assertIsNone(tokens.search(method_text(name).lower()))
        for module in SRC.glob('*.py'):
            source = module.read_text()
            for name in METHODS:
                self.assertNotIn(name, source, f'{module.name} names the {name} skill')


class _Methods(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')
        PluginRegistry(self.store.root)
        self.library = SkillLibrary(self.store, transport=GitHub())
        for name in METHODS:
            self.library.install(f'https://github.com/{REPO}/tree/main/skills/{name}')
        self.library.set_enabled(True)

    def caps(self, transport=None, job='job', **kwargs):
        return Capabilities(self.store, ModelAdapter(transport) if transport else None, CFG, '', job, lambda *a: None,
                            skills=self.library.binding(), **kwargs)


class HeaderReads(_Methods):
    def test_a_header_read_returns_only_the_usage_header_and_loads_nothing(self):
        caps = self.caps()
        result = caps.execute('skill_load', {'skill': SKILL['claim-analysis'], 'part': 'header'})
        _frontmatter, body = parse_frontmatter(method_text('claim-analysis'))
        self.assertEqual(result['header'], usage_header(body)[0])
        self.assertEqual((result['part'], result['revision'], result['header_truncated']), ('header', COMMIT, False))
        self.assertLessEqual(len(result['header'].encode()), MAX_HEADER_BYTES)
        self.assertNotIn('instructions', result)
        self.assertNotIn('## 1.', json.dumps(result, ensure_ascii=False))
        self.assertNotIn(SKILL['claim-analysis'], caps.skills.loaded, 'a header read is not a load')
        self.assertEqual(caps.skills.loads, 0)
        with self.assertRaises(ToolError) as raised:
            caps.execute('skill_resource', {'skill': SKILL['claim-analysis'], 'path': 'SKILL.md'})
        self.assertEqual(raised.exception.code, 'skill_unavailable', 'resources stay closed until the full load')
        self.assertEqual(evidence_summary('skill_load', result)['part'], 'header')
        self.assertNotIn('header', evidence_summary('skill_load', result), 'the record names the revision, never the text')

    def test_a_long_opening_is_cut_at_the_byte_bound(self):
        body = 'Use this when ' + '가' * (MAX_HEADER_BYTES) + '\n\n## 1. Steps\nSecret step.'
        header, cut = usage_header(body)
        self.assertTrue(cut)
        self.assertLessEqual(len(header.encode()), MAX_HEADER_BYTES)
        self.assertNotIn('Secret step', header)

    def test_header_reads_are_bounded_per_work_and_a_full_load_still_works(self):
        binding = self.library.binding()
        for _ in range(MAX_HEADER_READS):
            binding.header(SKILL['source-cross-check'])
        with self.assertRaises(SkillError) as raised:
            binding.header(SKILL['source-cross-check'])
        self.assertEqual(raised.exception.code, 'skill_limit')
        loaded = binding.load(SKILL['source-cross-check'])
        self.assertIn('## 1. Find where the fact comes from', '## ' + loaded['instructions'].split('## ', 1)[1])
        self.assertNotIn(SKILL['source-cross-check'], binding.screened)

    def test_unknown_disabled_or_withdrawn_skills_cannot_be_screened_or_used(self):
        caps = self.caps()
        with self.assertRaises(ToolError) as raised:
            caps.execute('skill_load', {'skill': 'someone/unlisted', 'part': 'header'})
        self.assertEqual(raised.exception.code, 'skill_unavailable')
        # A method the owner disabled leaves the catalogue of the next Work and cannot be screened in this one.
        PluginRegistry(self.store.root).set_enabled('claim-analysis', False)
        self.assertNotIn(SKILL['claim-analysis'], self.library.binding().entries)
        with self.assertRaises(ToolError) as raised:
            caps.execute('skill_load', {'skill': SKILL['claim-analysis'], 'part': 'header'})
        self.assertEqual(raised.exception.code, 'skill_unavailable')
        # A method removed after this Work screened it stops the Work's later calls, like a loaded one.
        caps.execute('skill_load', {'skill': SKILL['source-cross-check'], 'part': 'header'})
        self.library.remove('source-cross-check')
        self.assertNotIn(SKILL['source-cross-check'], self.library.binding().entries)
        with self.assertRaises(ToolError) as raised:
            caps.execute('list_notes', {})
        self.assertEqual(raised.exception.code, 'skill_revoked')

    def test_switching_skills_off_offers_no_method_header_or_gap_tool(self):
        self.library.set_enabled(False)
        self.assertIsNone(self.library.binding())
        caps = Capabilities(self.store, None, CFG, '', 'job', lambda *a: None, skills=None)
        offered = set(caps.offered_tools())
        self.assertFalse({'skill_load', 'skill_resource', 'method_gap'} & offered)


class MethodGapAndProvenance(_Methods):
    def run_script(self, *messages):
        script = Script(*messages)
        events = []
        caps = self.caps(script, judgments=judgments(True))
        caps.record = lambda *event: events.append(event)
        result = run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': 'Is this article convincing?'}], '', caps,
                           lambda *event: events.append(event))
        return result, events, script

    @staticmethod
    def details(events, tool, status='succeeded'):
        return [json.loads(detail) for name, state, detail in events if name == tool and state == status]

    def test_loaded_revisions_and_the_gap_note_stay_in_the_work_record_and_the_work_completes(self):
        note = 'a method for weighing statistical studies against each other'
        result, events, _script = self.run_script(
            {'tool_calls': [call('h', 'skill_load', skill=SKILL['claim-analysis'], part='header')]},
            {'tool_calls': [call('l', 'skill_load', skill=SKILL['summary-or-analysis'])]},
            {'tool_calls': [call('g', 'method_gap', note=note)]},
            finish('f', 'l', summary='The article is partly convincing.'))
        self.assertEqual(result.outcome, 'succeeded', 'a method gap is advice, not a failure')
        loads = self.details(events, 'skill_load')
        self.assertEqual([(row['evidence']['skill'], row['evidence']['revision'], row['evidence'].get('part')) for row in loads],
                         [(SKILL['claim-analysis'], COMMIT, 'header'), (SKILL['summary-or-analysis'], COMMIT, None)])
        self.assertTrue(all(len(row['evidence']['digest']) == 64 for row in loads))
        [gap] = self.details(events, 'method_gap')
        self.assertEqual(gap['evidence']['method_gap'], note)
        self.assertFalse(self.details(events, 'method_gap', 'failed'))

    def test_a_request_no_method_fits_still_completes_with_only_the_bounded_note(self):
        result, events, _script = self.run_script(
            {'tool_calls': [call('g', 'method_gap', note='  a way to audit spreadsheet formulas  ' + 'x' * 400)]},
            {'content': 'Here is my answer without a method.'})
        self.assertNotEqual(result.outcome, 'failed')
        self.assertIn('Here is my answer without a method.', result.content)
        [gap] = self.details(events, 'method_gap')
        self.assertEqual(len(gap['evidence']['method_gap']), MAX_GAP_CHARS)
        self.assertTrue(gap['evidence']['method_gap'].startswith('a way to audit spreadsheet formulas'))
        self.assertFalse(self.details(events, 'skill_load'), 'no skill was loaded')

    def test_gap_notes_are_bounded_and_need_a_note(self):
        binding = self.library.binding()
        with self.assertRaises(SkillError):
            binding.gap('   ')
        for _ in range(MAX_GAP_NOTES):
            binding.gap('a missing method')
        with self.assertRaises(SkillError) as raised:
            binding.gap('one more')
        self.assertEqual(raised.exception.code, 'skill_limit')

    def test_each_header_read_and_gap_note_is_one_ordinary_work_attempt(self):
        _result, events, _script = self.run_script(
            {'tool_calls': [call('h', 'skill_load', skill=SKILL['claim-analysis'], part='header')]},
            {'tool_calls': [call('g', 'method_gap', note='a missing method')]},
            {'content': 'done'})
        running = [json.loads(detail) for name, state, detail in events if state == 'running' and name in ('skill_load', 'method_gap')]
        self.assertEqual([(row['host_action'], row['attempt']) for row in running], [('skill_load', 1), ('method_gap', 1)])


class BridgeBoundary(_Methods):
    """A CLI bridge rebuilds the binding from refs and recalls what the Work read from its recorded events."""

    def test_a_recalled_header_read_never_unlocks_resources_and_a_recalled_load_does(self):
        host = self.caps(job=self.store.enqueue('analyse', 'k-bridge'))
        job = host.job_id
        header = host.execute('skill_load', {'skill': SKILL['claim-analysis'], 'part': 'header'})
        loaded = host.execute('skill_load', {'skill': SKILL['summary-or-analysis']})
        with self.store.db() as db:
            for result in (header, loaded):
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,0)',
                           (job, 'skill_load', 'succeeded', json.dumps({'evidence': evidence_summary('skill_load', result)})))
        bridge = SkillBinding.from_refs(self.library, host.skills.refs())
        bridge.recall(self.store, job)
        self.assertEqual((sorted(bridge.loaded), sorted(bridge.screened)), ([SKILL['summary-or-analysis']], [SKILL['claim-analysis']]))
        with self.assertRaises(SkillError):
            bridge.resource(SKILL['claim-analysis'], 'SKILL.md')
        self.assertEqual(bridge.resource(SKILL['summary-or-analysis'], 'SKILL.md')['path'], 'SKILL.md')
        # A screened method withdrawn later stops the bridge's calls too.
        self.library.remove('claim-analysis')
        with self.assertRaises(SkillError) as raised:
            bridge.check_current()
        self.assertEqual(raised.exception.code, 'skill_revoked')


if __name__ == '__main__':
    unittest.main()
