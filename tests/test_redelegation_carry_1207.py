"""Kept reads cross the attempt boundary in the real loop with scripted workers and judges."""
from types import SimpleNamespace

from personal_agent.agent_runtime import GOAL_OBSERVATION_CHARS, goal_judgment
from personal_agent.decision import BinaryDecision, OUTCOME_DECIDED, fixture_confidence
from personal_agent.orchestrator import PAGE_TEXT_OMITTED, Attempt
from personal_agent.providers import ModelAdapter
from personal_agent.work_executor import (REDELEGATION_CARRY_BYTES, REDELEGATION_DRAFT_BYTES,
                                          REDELEGATION_DRAFT_CUT, redelegation_note)
from test_orchestrator import Harness, plan
from test_read_results_768 import ReadResultsBase
from test_still_record_redelegation_1167 import ScriptedWorker


EARLIER_URL = 'https://archive.example/release'
CURRENT_URL = 'https://current.example/release'
UNREFERENCED_URL = 'https://misc.example/release'
PAGES = {EARLIER_URL: 'The archive puts the previous figure at 40 units.',
         CURRENT_URL: 'The new release puts the figure at 60 units.',
         UNREFERENCED_URL: 'An uncited publication lists unrelated dates.'}
DRAFT = 'The previous figure was 40 units.'
REVISED = 'The figure rose from 40 to 60 units.'


class PageNetwork:
    def __init__(self):
        self.reads = []

    def execute(self, request):
        url = request['url']
        self.reads.append(url)
        return {'tool': 'public_page_read', 'url': url, 'content': PAGES[url], 'sources': [url]}


class JudgmentTests(Harness):
    def script_judge(self, plans):
        engine = self.script(plans)
        original = engine._judge

        def judge(context, proposition):
            original(context, proposition)
            if context.purpose == 'goal-reached':
                facts = context.facts
                observed = facts['observations']
                current, earlier = (observed.find(PAGES[url]) for url in (CURRENT_URL, EARLIER_URL))
                reached = facts['reply'] == REVISED and 0 <= current < earlier
                return BinaryDecision(OUTCOME_DECIDED, reached, fixture_confidence())

        engine._judge = judge

    def check_replacement(self, job):
        self.assertEqual([(detail['outcome'], detail['next']) for _, detail in self.events(job, 'evaluated')],
                         [('not_reached', 'redelegate'), ('reached', 'stop')])
        first, replacement = [context.facts['observations'] for context in self.asked_goals]
        self.assertIn(PAGES[EARLIER_URL], first)
        self.assertNotIn(PAGES[CURRENT_URL], first)
        self.assertLess(replacement.index(PAGES[CURRENT_URL]), replacement.index(PAGES[EARLIER_URL]))
        for url in (CURRENT_URL, EARLIER_URL):
            self.assertEqual(replacement.count(PAGES[url]), 1)
        self.assertEqual(self.store.job(job)['status'], 'succeeded')
        return replacement

    def test_direct_finish_uses_earlier_reads_after_its_cited_observation(self):
        self.use_main('openai')
        job = self.store.enqueue('Compare the two releases.', 'direct-1207', owner_typed=True, owner_words=True)
        worker = ScriptedWorker(lambda: max(1, len(self.events(job, 'planned'))), {
            1: [('a', 'public_page_read', {'url': EARLIER_URL}), DRAFT],
            2: [('b', 'public_page_read', {'url': CURRENT_URL}),
                ('c', 'public_page_read', {'url': UNREFERENCED_URL}),
                ('f', 'finish', {'status': 'done', 'evidence_refs': ['b'], 'summary': REVISED})],
        })
        network = PageNetwork()
        self.service.local_tools = network
        self.service.adapter = ModelAdapter(worker)
        self.script_judge([plan('openai', 'Read the archive.'), plan('openai', 'Compare with the new release.')])

        self.assertTrue(self.service.run_one())
        observed = self.check_replacement(job)
        self.assertNotIn(PAGES[UNREFERENCED_URL], observed)
        self.assertEqual(network.reads, [EARLIER_URL, CURRENT_URL, UNREFERENCED_URL])
        self.assertIn(PAGES[EARLIER_URL], worker.bodies[2][0])
        self.assertIn(DRAFT, worker.bodies[2][0])

    def test_orchestrated_judgment_uses_current_pages_then_earlier_pages(self):
        network = PageNetwork()
        self.service.local_tools = network
        self.engine.answers = [DRAFT, REVISED]

        def read(tools):
            url = EARLIER_URL if len(self.engine.turns) == 1 else CURRENT_URL
            tools.capabilities.execute('public_page_read', {'url': url})

        self.engine.before = read
        self.script_judge([plan('codex', 'Read the archive.'),
                           plan('codex', 'Compare with the new release.', model='gpt-5.6-luna')])
        job, _ = self.run_work('Compare the two releases.', owner_words=True)
        self.check_replacement(job)
        self.assertEqual(network.reads, [EARLIER_URL, CURRENT_URL])

    def test_direct_judgment_reserves_room_and_counts_omitted_reads(self):
        seen = []

        def judge(_goal, observations, *_args, **_kwargs):
            seen.append(observations)
            return SimpleNamespace(outcome='yes')

        pages = [(f'earlier-{n}', '', f'Earlier detail {n}. ' + 'e' * 3000) for n in range(24)]
        verdict = goal_judgment(SimpleNamespace(goal_reached=judge), 'compare',
                                {'evidence_refs': ['current'], 'summary': REVISED},
                                {'current': ('public_page_read', 'public_page_read', 'succeeded',
                                             {'content': 'Current detail. ' + 'c' * GOAL_OBSERVATION_CHARS})},
                                '', earlier_reads=pages)
        self.assertEqual(verdict, 'yes')
        observed, = seen
        self.assertLessEqual(len(observed), GOAL_OBSERVATION_CHARS)
        self.assertLess(observed.index('Current detail.'), observed.index('Earlier detail 0.'))
        shown = sum(f'Earlier detail {n}.' in observed for n in range(len(pages)))
        self.assertGreater(shown, 1)
        self.assertLess(shown, len(pages))
        self.assertIn(PAGE_TEXT_OMITTED.format(count=len(pages) - shown), observed)


class ByteBoundTests(ReadResultsBase):
    def keep(self, job, url, text, tool='public_page_read'):
        self.store.record_read_result(job, tool=tool, text=text, source_kind='public', page_url=url)

    def test_multibyte_carry_keeps_draft_first_and_reserves_room_for_reads(self):
        job, _ = self.exchange()
        for n in range(24):
            self.keep(job, f'https://archive.example/{n}', f'자료-{n} ' + '가' * 3000)
        draft = '🌏' * 5000
        orchestration = SimpleNamespace(gaps={1: '차이' * 1000}, drafts={1: draft})
        note = redelegation_note(self.store, job, orchestration, Attempt(2, 'worker'))
        self.assertLessEqual(len(note.encode('utf-8')), REDELEGATION_CARRY_BYTES)
        before_cut, after_cut = note.split(REDELEGATION_DRAFT_CUT)
        retained = before_cut.rstrip('\n').rsplit('\n', 1)[-1]
        self.assertTrue(retained and draft.startswith(retained))
        draft_size = len((retained + '\n' + REDELEGATION_DRAFT_CUT).encode())
        self.assertLessEqual(draft_size, REDELEGATION_DRAFT_BYTES)
        self.assertLess(REDELEGATION_DRAFT_BYTES - draft_size, len('🌏'.encode()))
        self.assertIn('자료-23 ', after_cut)
        shown = sum(f'https://archive.example/{n}\n' in after_cut for n in range(24))
        self.assertIn(PAGE_TEXT_OMITTED.format(count=24 - shown), after_cut)
        self.assertTrue(note.endswith('New reads are allowed.'))

    def test_same_tool_and_page_keep_only_the_newest_excerpt(self):
        job, _ = self.exchange()
        self.keep(job, EARLIER_URL, 'Old excerpt')
        self.keep(job, EARLIER_URL, 'Latest excerpt')
        self.keep(job, EARLIER_URL, 'Different tool excerpt', tool='browser_read')
        orchestration = SimpleNamespace(gaps={}, drafts={})
        note = redelegation_note(self.store, job, orchestration, Attempt(2, 'worker'))
        self.assertNotIn('Old excerpt', note)
        self.assertEqual(note.count('Latest excerpt'), 1)
        self.assertEqual(note.count('Different tool excerpt'), 1)
        self.assertEqual(note.count(EARLIER_URL), 2)
