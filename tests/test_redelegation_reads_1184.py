"""#1184: kept reads reach the judgment and the owner's replacement worker."""
from types import SimpleNamespace

from personal_agent.agent_runtime import ATTEMPT_RESULT_CHARS, READ_RESULTS_BLOCK_CHARS, read_results_block
from personal_agent.orchestrator import ATTEMPT_DRAFT_CHARS, Attempt, observations_with_pages
from personal_agent.work_executor import redelegation_note
from test_orchestrator import OPENAI_KEY, Harness, plan
from test_read_results_768 import ReadResultsBase


class KeptReadTests(ReadResultsBase):
    def test_an_evicted_public_page_still_reaches_the_judgment(self):
        caps = self.caps()
        first_url = 'https://example.test/0'
        for n in range(12):
            caps._record_read_result('public_page_read', {
                'url': f'https://example.test/{n}', 'title': f'Page {n}',
                'content': f'source-detail-{n} ' + 'x' * (ATTEMPT_RESULT_CHARS // 8),
            })
        first, *_, latest = self.rows(caps.job_id)
        pages = caps.take_browser_pages()
        self.assertIn((first_url, 'Page 0', first['excerpt']), pages)
        self.assertEqual(sum('source-detail-11 ' in text for _, _, text in pages), 1)
        self.assertGreater(len(pages[0][2]), len(latest['excerpt']))
        self.assertIn('source-detail-0 ', observations_with_pages([], pages))
        self.assertEqual(caps.take_browser_pages(), [])

    def test_a_replaced_browser_view_hides_its_older_excerpt_until_evicted(self):
        caps = self.caps()
        url = 'https://example.test/page'
        first = 'earlier text ' + 'x' * (ATTEMPT_RESULT_CHARS // 8)
        caps._record_browser_result('browser_read', {'url': url, 'title': 'Page', 'text': first}, keep=True)
        caps._record_browser_result('browser_read', {'url': url, 'title': 'Page', 'text': 'latest text'}, keep=True)
        self.assertEqual(caps.take_browser_pages(), [(url, 'Page', 'latest text')])

    def test_read_results_block_keeps_its_default_limit(self):
        caps = self.caps()
        for n in range(3):
            caps._record_read_result('public_page_read', {
                'url': f'https://example.test/{n}', 'content': f'read-{n} ' + 'x' * 3000,
            })
        rows = self.store.read_results(caps.job_id)
        block, count = read_results_block(self.store, rows)
        self.assertTrue(block.startswith('What this earlier Work read'))
        self.assertEqual((len(block), count), (READ_RESULTS_BLOCK_CHARS, 2))
        self.assertEqual(read_results_block(self.store, rows, limit=READ_RESULTS_BLOCK_CHARS), (block, count))

    def test_the_note_is_empty_without_records_and_excluded_from_other_attempts(self):
        caps = self.caps()
        orchestration = SimpleNamespace(gaps={}, drafts={})
        following = Attempt(2, 'owner-worker')
        self.assertEqual(redelegation_note(self.store, caps.job_id, orchestration, following), '')
        caps._record_read_result('public_page_read', {'url': 'https://example.test/', 'content': 'kept source'})
        orchestration.gaps[1] = 'shortfall'
        orchestration.drafts[1] = 'draft'
        for attempt, delegated in ((Attempt(1, 'owner-worker'), False), (following, True)):
            self.assertEqual(redelegation_note(self.store, caps.job_id, orchestration, attempt, delegated=delegated), '')
        following.steer = True
        self.assertEqual(redelegation_note(self.store, caps.job_id, orchestration, following), '')


class ReplacementBriefTests(Harness):
    def test_the_replacement_worker_receives_the_reads_gap_and_bounded_redacted_draft(self):
        gap, draft_detail = 'Explain the difference between the listed options.', 'Detail past plan history.'
        draft_tail = 'Draft beyond the cap.'
        self.engine.answers = ['x' * 700 + f' {draft_detail} {OPENAI_KEY} ' + 'y' * ATTEMPT_DRAFT_CHARS + draft_tail,
                               'Revised reply.']
        decision = self.script([plan('codex', 'Read the options.'),
                                plan('codex', 'Explain the difference.', model='gpt-5.6-luna')],
                               goals=[False, True])
        judge = decision._judge

        def judge_with_gap(context, proposition):
            result = judge(context, proposition)
            if result is not None and result.answer is False:
                result.gap = gap
            return result

        def read_in_first_attempt(tools):
            if len(self.engine.turns) != 1:
                return
            for n in range(6):
                tools.capabilities._record_read_result('public_page_read', {
                    'url': f'https://example.test/{n}', 'content': f'option-source-{n} ' + 'z' * 1400,
                })

        decision._judge = judge_with_gap
        self.engine.before = read_in_first_attempt
        job, row = self.run_work('Compare the options.')

        first, following = self.engine.turns
        self.assertNotIn('Records from earlier attempts', first['context']['brief'])
        brief = following['context']['brief']
        self.assertIn('Still host note: Records from earlier attempts', brief)
        self.assertIn("The owner's message takes precedence.", brief)
        self.assertIn(gap, brief)
        self.assertIn(f'{draft_detail} [redacted]', brief)
        self.assertNotIn(OPENAI_KEY, brief)
        self.assertNotIn(draft_tail, brief)
        self.assertNotIn(draft_detail, self.asked_plans[1][0].facts['previous_attempts'])
        for n in range(6):
            self.assertIn(f'option-source-{n} ', brief)
        self.assertIn('read or search again only as needed to address it. New reads are allowed.', brief)
        self.assertEqual((row['status'], row['response']), ('succeeded', 'Revised reply.'))
        self.assertNotIn(draft_detail, str(self.store.history(through=job)))
