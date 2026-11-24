"""#1184: a re-delegated attempt starts from the earlier draft and what the Work already read.

Part A runs the real worker loop and ``Orchestration.next`` with a scripted direct-route worker, a scripted
Judgment AI and a fake public network (no model, network or account calls): the replacement attempt's brief
carries the earlier reads and draft, its outcome judgment reads them, and nothing forbids a new read.
Part B pins the real Telegram delivery of a budget stop: the whole answer the AI wrote is its own bubble, sent
before the Continue offer, and a stop without an answer keeps today's offer.
"""
import json
import time
import unittest

from personal_agent import agent_runtime as ar
from personal_agent.messages import msg
from personal_agent.orchestrator import Attempt, Orchestration
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore
from personal_agent.work_execution import WorkRun

from test_orchestrator import Harness, plan
from test_still_record_redelegation_1167 import ScriptedWorker
import test_budget_continue

PAGES = {f'https://news{index}.example/item': f'PAGE-{index} said the figure was {index}0 units.' for index in range(1, 4)}
NEW_PAGE = 'https://late.example/release'
DRAFT = 'DRAFT-1184: ' + ' '.join(f'item {index} is {index}0 units.' for index in range(1, 4)) + ' ' + 'x' * 900


class PageNetwork:
    """The public wire: every page read returns its fixture text; each plan is counted."""

    def __init__(self):
        self.plans = []

    def execute(self, plan):
        self.plans.append(dict(plan))
        url = plan.get('url')
        text = PAGES.get(url, 'LATE said the release shipped today.')
        return {'tool': 'public_page_read', 'url': url, 'retrieved_at': time.time(), 'content': text,
                'content_truncated': False, 'content_bytes': len(text), 'sources': [url]}


class RealLoop(Harness):
    def run_case(self, second):
        job = {}

        def attempt():
            return max(1, len(self.events(job['id'], 'planned'))) if job else 1

        reads = [(f'r{index}', 'public_page_read', {'url': url}) for index, url in enumerate(PAGES, 1)]
        holder = []
        if callable(second):
            second = second(lambda: holder[0])
        worker = ScriptedWorker(attempt, {1: [*reads, DRAFT], 2: second})
        holder.append(worker)
        self.network = PageNetwork()
        self.service.local_tools = self.network
        self.service.adapter = ModelAdapter(worker)
        self.use_main('openai')
        # Attempt 1's own judgment says no; the replacement attempt's says yes.
        self.script([plan('openai', 'Gather and write the briefing.'), plan('openai', 'Repair the unsupported sentence.')],
                    goals=[False] + [True] * 5)
        job['id'] = self.store.enqueue('오늘 소식을 정리해 줘', 'k1184-' + str(len(second)), owner_typed=True, owner_words=True)
        self.assertTrue(self.service.run_one())
        return job['id'], worker

    def evaluated(self, work):
        return [(detail['outcome'], detail['next']) for _status, detail in self.events(work, 'evaluated')]

    def test_a1_the_next_brief_carries_the_earlier_reads_and_the_draft(self):
        work, worker = self.run_case(['고친 답이에요.'])
        self.assertEqual(self.evaluated(work)[0], ('not_reached', 'redelegate'))
        brief = json.loads(worker.bodies[2][0])
        text = json.dumps(brief, ensure_ascii=False)
        for url, page in PAGES.items():
            self.assertIn(url, text)
            self.assertIn(page, text)
        self.assertIn('DRAFT-1184', text)
        self.assertIn('x' * 900, text, 'the whole draft, not the plan excerpt')
        self.assertNotIn('DRAFT-1184', json.dumps(json.loads(worker.bodies[1][0]), ensure_ascii=False),
                         'the first attempt has no earlier draft')

    def test_a2_a_worker_that_repairs_only_the_gap_finishes_without_reading_again(self):
        def worker_steps(worker):
            # A worker that reads again whatever its brief does not show, then answers.
            def step():
                brief = worker().bodies[2][-1]
                gathered = [plan.get('url') for plan in self.network.plans[len(PAGES):]]
                missing = [url for url in PAGES if url not in brief and url not in gathered]
                return (f'g{len(gathered)}', 'public_page_read', {'url': missing[0]}) if missing else '고친 답이에요.'
            return [step]
        work, _worker = self.run_case(worker_steps)
        self.assertEqual(self.evaluated(work), [('not_reached', 'redelegate'), ('reached', 'stop')])
        self.assertEqual(self.store.job(work)['status'], 'succeeded')
        self.assertEqual(len(self.network.plans), len(PAGES), 'no source was read twice')
        # The replacement attempt's outcome judgment read what the earlier attempt read (``take_browser_pages``).
        facts = json.dumps(self.asked_goals[-1].facts, ensure_ascii=False)
        self.assertIn(PAGES['https://news2.example/item'], facts)

    def test_a3_negative_a_gap_that_needs_a_new_source_still_reads_it(self):
        work, worker = self.run_case([('n1', 'public_page_read', {'url': NEW_PAGE}), '새 자료까지 넣었어요.'])
        self.assertIn(NEW_PAGE, [plan.get('url') for plan in self.network.plans])
        with self.store.db() as db:
            statuses = [row['status'] for row in db.execute(
                "SELECT status FROM tool_events WHERE job_id=? AND tool='public_page_read' AND detail LIKE ?",
                (work, f'%{NEW_PAGE}%'))]
        self.assertIn('succeeded', statuses)
        self.assertEqual(self.store.job(work)['status'], 'succeeded')
        # The run's own judgment cites only the new read, and still reads what the earlier attempt read.
        facts = json.dumps(self.asked_goals[-1].facts, ensure_ascii=False)
        self.assertIn('LATE said the release shipped today.', facts)
        self.assertIn(PAGES['https://news1.example/item'], facts)
        self.assertIn('DRAFT-1184', worker.bodies[2][0])


class CarryUnits(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        self.work = self.store.enqueue('정리해 줘', 'k-units', owner_typed=True)

    def keep(self, url, text, tool='public_page_read'):
        return self.store.record_read_result(self.work, tool=tool, text=text, source_kind='public', page_url=url)

    def test_reads_are_bounded_in_bytes_and_every_source_keeps_its_label(self):
        for index in range(40):
            self.keep(f'https://s{index}.example/', '가' * 3000)
        text = ar.earlier_reads_text(self.store, self.work)
        self.assertLessEqual(len(text.encode()), ar.EARLIER_READS_BYTES)
        self.assertIn('https://s39.example/', text, 'newest first')
        self.assertIn(ar.EARLIER_READS_MORE, text)
        self.assertIn('https://s30.example/', text)

    def test_a_source_read_twice_is_shown_once_with_its_newest_read(self):
        self.keep('https://same.example/', 'OLD TEXT')
        self.keep('https://same.example/', 'NEW TEXT')
        text = ar.earlier_reads_text(self.store, self.work)
        self.assertIn('NEW TEXT', text)
        self.assertNotIn('OLD TEXT', text)

    def test_a_long_draft_is_cut_with_a_marker_and_the_carry_stays_bounded(self):
        for index in range(40):
            self.keep(f'https://s{index}.example/', '가' * 3000)
        reads, note = ar.redelegation_carry(self.store, self.work, '나' * 5000, 'gap')
        self.assertIn(ar.EARLIER_DRAFT_CUT, note)
        self.assertIn('https://s39.example/', note)
        self.assertLessEqual(len(note.encode()), ar.EARLIER_CARRY_BYTES + len(ar.prompt('redelegation_carry').encode()) + 200)
        self.assertGreaterEqual(len(reads.encode()), ar.EARLIER_READS_MIN_BYTES // 2)

    def test_a_fresh_attempts_pages_include_earlier_reads_for_its_judgment(self):
        # Each attempt (CLI or direct) builds its own Capabilities; what it hands the outcome judgment
        # (``take_browser_pages``) starts from the Work's first kept read, so earlier reads are judged too.
        self.keep('https://first.example/', 'FIRST ATTEMPT TEXT')
        pages = ar.Capabilities(self.store, None, {}, '', self.work, lambda *args: None).take_browser_pages()
        self.assertIn('FIRST ATTEMPT TEXT', json.dumps(pages, ensure_ascii=False))

    def test_nothing_to_carry_is_no_note(self):
        self.assertEqual(ar.earlier_reads_text(self.store, self.work), '')
        self.assertEqual(ar.redelegation_note(self.store, '', 'gap only', ''), '')

    def test_a_stored_secret_never_reaches_the_note(self):
        self.store.secret('api_key', 'sk-SECRET-1184-value')
        note = ar.redelegation_note(self.store, 'draft with sk-SECRET-1184-value', '', '')
        self.assertNotIn('sk-SECRET-1184-value', note)

    def test_carried_draft_is_the_whole_answer_with_its_gap(self):
        orchestration = Orchestration.__new__(Orchestration)
        orchestration.history, orchestration.gaps, orchestration.drafts = [], {}, {}
        orchestration._redact = lambda text, **_: text
        attempt = Attempt(1, 'openai')
        orchestration.history.append((attempt, 'not_reached', DRAFT[:600], ''))
        orchestration.drafts[1], orchestration.gaps[1] = DRAFT, 'one sentence is unsupported'
        self.assertEqual(orchestration.carried_draft(), (DRAFT, 'one sentence is unsupported'))
        orchestration.drafts.clear()
        self.assertEqual(orchestration.carried_draft(), ('', ''))


class BudgetStopDelivery(unittest.TestCase):
    """Part B on the real Telegram delivery: the Work's reply goes out before its Continue offer."""

    service = test_budget_continue.BudgetContinueTests.service
    stopped = test_budget_continue.BudgetContinueTests.stopped

    def telegram_stop(self, response):
        service, store, job, run, _ = self.stopped(channel='telegram:g', chat_id=77)
        sent = []
        service.telegram.send_message = lambda chat, text, *args, **kwargs: (sent.append(text) or {'message_id': 100 + len(sent)})
        store.put('telegram', {'enabled': True, 'generation': 'g', 'user_id': 77})
        with store.db() as db:
            db.execute("UPDATE jobs SET response=?,delivery='pending' WHERE id=?", (response, job))
        self.assertTrue(service.offer_budget_continue(run))
        return service, store, job, sent

    def test_b1_the_written_answer_reaches_the_owner_before_the_continue_offer(self):
        answer = 'ANSWER-1184 첫 줄\n자세한 내용이에요.'
        service, store, job, sent = self.telegram_stop(answer)
        # One work-loop tick (``_work_iteration``): the reply step runs before the notice step.
        for step in ('deliver_one', 'deliver_notification'):
            getattr(service, step)()
        self.assertEqual(len(sent), 2, sent)
        self.assertIn('ANSWER-1184', sent[0])
        self.assertIn('자세한 내용이에요.', sent[0], 'the whole answer, not its first line only')
        self.assertIn(msg('budget_continue.remaining'), sent[1])

    def test_b2_a_budget_stop_without_an_answer_keeps_todays_message(self):
        service, store, job, sent = self.telegram_stop(None)
        for step in ('deliver_one', 'deliver_notification'):
            getattr(service, step)()
        self.assertEqual(len(sent), 2, sent)
        self.assertIn(msg('budget_continue.done_steps', count=1), sent[1])


if __name__ == '__main__':
    unittest.main()
