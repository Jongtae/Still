"""#1007 model-free entry/restart evidence; no live model, network or account.

Reuse the #505 service/model/delivery harness, #657 decision engine, #1009
HTTP responses and #1008 unknown-effect setup. Only provider/HTTP/clock edges
are fake. Queue claims, staging, recovery, ledger and judgment commits are real.
"""
import hashlib
import json
import re
import threading
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_contextual_capability_handoff as handoff
from test_agency_loop import goal_engine
from test_public_download_1009 import Opener, Response

from personal_agent import work_download
from personal_agent.agent_runtime import LedgerSuperseded, WORK_LEDGER_KEY, WORK_STOP_KEY
from personal_agent.local_tools import PublicPageReader
from personal_agent.public_download import PublicDownload
from personal_agent.quickstart_store import QuickStore
from personal_agent.work_execution import WorkRun

URL = 'https://example.org/reference'
DATA = b'abc-def-ghi'
REQUEST = 'Read all bytes of the reference and report its first and last three characters.'
ANSWER = 'The source starts with abc and ends in ghi.'


class WorkerReleased(BaseException):
    """Unwind a frozen old worker only after the replacement has been tested."""


class EvaluationComplete(BaseException):
    """Stop the progress-only fixture after checking the judgment cache."""


class FrozenWorker:
    """Leave run_one suspended at a provider edge without running finalizers.

    No process is forked. The model/judge callback invokes block only after
    its database operations have returned; checkpoint_judgment also commits
    its reservation before invoking the judge outside the transaction.
    """

    def __init__(self, case, service, job):
        self.case, self.service, self.job = case, service, job
        self.store = service.store
        self.reached = threading.Event()
        self.release = threading.Event()
        self.unwound = False
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True,
                                       name=f'frozen-read-{job}')

    def block(self):
        self.reached.set()
        self.release.wait()
        raise WorkerReleased()

    def run(self):
        try:
            self.service.run_one()
        except WorkerReleased:
            self.unwound = True
        except BaseException:
            self.error = traceback.format_exc()

    def release_and_join(self):
        self.release.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=10)
            self.case.assertFalse(self.thread.is_alive(), 'old worker did not finish after release')
            self.case.assertIsNone(self.error, self.error)
            self.case.assertTrue(self.unwound, 'old worker exited without reaching the release barrier')


def fetch(offset):
    return ('public_download', {'operation': 'fetch', 'offset': offset,
                                **({'url': URL} if offset == 0 else {})})


class RangeTransport:
    """#1009's fake wire behind the real RFC range/validator implementation."""

    def __init__(self):
        self.calls = []
        transport = self

        class FixtureDownload:
            def __init__(self, reader):
                # As in test_work_download_staging: no intranet-capable reader.
                assert reader.declared is None
                self.reader = reader
                self.now = self.reader.clock()
                self.reader.clock = lambda: self.now

            def read_range(self, url, offset, length, **guards):
                chunk = DATA[offset:offset + 4]
                response = Response(chunk, headers=[
                    ('Content-Range', f'bytes {offset}-{offset + len(chunk) - 1}/{len(DATA)}'),
                    ('ETag', '"ref"'), ('Content-Length', str(len(chunk))),
                ])
                opener = Opener([response])
                reader = PublicPageReader(opener=opener, resolver=lambda *a, **k: [
                    (None, None, None, None, ('93.184.216.34', 443))], guard=self.reader.guard,
                                          clock=self.reader.clock)
                try:
                    # A four-byte requested interval lets the production validator
                    # reject truncated/overlong responses, rather than trusting a
                    # fabricated DownloadedRange. The final interval is clipped.
                    return PublicDownload(reader).read_range(url, offset, 4, **guards)
                finally:
                    self.now = guards['deadline']  # Exercise a pause after each committed range.
                    for address, method, headers in opener.calls:
                        transport.calls.append((address, method, int(headers['Range'].split('=')[1].split('-')[0])))

        self.download = FixtureDownload

    @property
    def offsets(self):
        return [row[2] for row in self.calls]


class TestReadWorkEntryIntegration1007(handoff.HandoffTestCase):
    def setUp(self):
        super().setUp()
        self.wire = RangeTransport()
        transport_patch = patch.object(work_download, 'PublicDownload', self.wire.download)
        transport_patch.start()
        self.addCleanup(transport_patch.stop)
        self.text = ANSWER

    def enqueue(self, key='read-entry', *, telegram=False):
        return self.store.enqueue(REQUEST, key, owner_typed=True,
                                  **({'channel': f'telegram:{handoff.GENERATION}',
                                      'chat_id': handoff.CHAT} if telegram else {}))

    def rows(self, table, job):
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                f'SELECT * FROM {table} WHERE job_id=? ORDER BY rowid', (job,))]

    def fresh_service(self):
        self.store = QuickStore(self.store.root)
        self.service = self.make_service()
        return self.service

    def start_frozen_worker(self, worker):
        # Registered before starting so even a failed reached assertion cannot
        # leak a blocked daemon. Cleanup runs before the provider patches stop.
        self.addCleanup(worker.release_and_join)
        worker.thread.start()
        self.assertTrue(worker.reached.wait(timeout=10),
                        worker.error or 'worker never reached the provider barrier')
        self.assertTrue(worker.thread.is_alive())
        # A separate writer must be able to start while the worker is frozen.
        # This would fail if the model/judge barrier held a write transaction.
        with worker.store.db() as db:
            db.execute('PRAGMA busy_timeout=1000')
            db.execute('BEGIN IMMEDIATE')
            db.rollback()

    def canonical_snapshot(self, worker):
        with worker.store.db() as db:
            db.execute('BEGIN')
            job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (worker.job,)).fetchone())
            tables = {table: [dict(row) for row in db.execute(
                f'SELECT * FROM {table} WHERE job_id=? ORDER BY rowid', (worker.job,))]
                for table in ('messages', 'tool_events', 'work_read_checkpoints', 'work_read_judgments')}
            # The cumulative WorkLedger row is shared by every epoch of this Work;
            # a late old-epoch pause/spend must not touch it.
            ledger = db.execute("SELECT value FROM config WHERE key LIKE ? || ':' || ?",
                                (WORK_LEDGER_KEY, worker.job)).fetchall()
        tables['ledger'] = [row['value'] for row in ledger]
        self.assertEqual(len(tables['ledger']), 1)
        return {'job': job, **tables, 'sent': list(self.sent),
                'range_calls': list(self.wire.calls)}

    def release_frozen_worker(self, worker):
        # Every successful frozen-worker test calls this after its main
        # assertions. Compare raw rows, with no ID/timestamp normalization.
        before = self.canonical_snapshot(worker)
        worker.release_and_join()
        self.assertEqual(self.canonical_snapshot(worker), before,
                         'late old-epoch worker changed canonical state')

    def interrupt_after_range(self, job):
        """Freeze run_one at the next model call after the range commits."""
        self.plan = [fetch(0)]
        self.claim_completion()
        service = self.service
        original = service.adapter.transport
        worker = FrozenWorker(self, service, job)

        def model(*args, **kwargs):
            # read_checkpoint closes its connection before block; no SQLite
            # context or transaction is kept open across the event wait.
            checkpoint = worker.store.read_checkpoint(job)
            if checkpoint and checkpoint['received'] == 4:
                worker.block()
            return original(*args, **kwargs)

        provider_patch = patch.object(service.adapter, 'transport', model)
        provider_patch.start()
        self.addCleanup(provider_patch.stop)
        self.start_frozen_worker(worker)
        self.assertEqual(self.store.job(job)['status'], 'running')
        self.assertEqual(self.store.read_checkpoint(job)['received'], 4)
        self.assertEqual(self.wire.offsets[-1:], [0])
        self.assertEqual(self.staged(job), DATA[:4])
        events = self.rows('tool_events', job)
        self.assertEqual(sum(e['tool'] == 'public_download' and e['status'] == 'succeeded'
                             for e in events), 1)
        return worker

    def staged(self, job):
        checkpoint = self.store.read_checkpoint(job)
        data = (self.store.private / 'work-downloads' / checkpoint['artifact']).read_bytes()
        self.assertEqual(len(data), checkpoint['received'])
        self.assertEqual(hashlib.sha256(data).hexdigest(), checkpoint['digest'])
        return data

    def finish_script(self):
        self.plan = [fetch(4), fetch(8), ('public_download', {'operation': 'preview', 'offset': 0})]
        self.claim_completion()

    def test_budget_continue_reopens_checkpoint_and_fences_old_worker(self):
        job = self.enqueue('read-budget-continue')
        service = self.service
        work_budget = service.work_budget
        budgets = []

        def limited_budget(work_id):
            budget = work_budget(work_id)
            budget.attempts = 1
            budgets.append(budget)
            return budget

        self.plan = [fetch(0), fetch(4)]
        self.text = 'The first range was read.'
        with patch.object(service, 'work_budget', limited_budget):
            self.assertTrue(service.run_one())
        paused = self.store.job(job)
        self.assertEqual(paused['status'], 'partial')
        self.assertEqual(self.wire.offsets, [0])
        self.assertEqual(self.staged(job), DATA[:4])
        old_budget, = budgets
        self.assertFalse(old_budget.ledger.counts()['active'])

        offer = service.budget_continue_offer(job)
        self.assertEqual(offer['state'], 'offered')
        self.assertTrue(service.budget_continue_decision({'work_id': job, 'nonce': offer['nonce']})['resumed'])
        self.assertEqual(self.store.job(job)['status'], 'queued')
        claimed = service._claim_queued_work()
        self.assertEqual(claimed['id'], job)
        self.assertNotIn('read_checkpoint_resume', claimed)
        stale_run = WorkRun(paused, self.context_clock())
        stale_run.work_budget = old_budget

        transport = service.adapter.transport
        stale_checks = []

        def check_stale_worker(*args, **kwargs):
            if not stale_checks:
                self.assertEqual(self.store.job(job)['status'], 'running')
                self.assertGreater(service.current_decision_budget().ledger.epoch, old_budget.ledger.epoch)
                with service.work_decision_scope(job, old_budget):
                    with self.assertRaises(LedgerSuperseded):
                        service.record_decision({'purpose': 'stale-worker'})
                    self.assertIs(service._finish_work(stale_run), False)
                stale_checks.append(True)
            return transport(*args, **kwargs)

        self.text = ANSWER
        self.finish_script()
        with patch.object(service.adapter, 'transport', check_stale_worker):
            self.assertTrue(service.work_executor._run_claimed(claimed))
        self.assertEqual(self.store.job(job)['status'], 'succeeded')
        self.assertIn(ANSWER, self.store.job(job)['response'])
        self.assertEqual(stale_checks, [True])
        queue_waits = [e for e in self.rows('tool_events', job)
                       if e['tool'] == 'response_timing' and json.loads(e['detail'])['stage'] == 'queue_wait']
        self.assertEqual(len(queue_waits), 2, 'both claims record queue timing before routing')
        self.assertEqual(self.wire.offsets, [0, 4, 8])
        self.assertEqual(self.staged(job), DATA)

    def pause_completed_download(self, job):
        service = self.service
        work_budget = service.work_budget
        budgets = []

        def limited_budget(work_id):
            budget = work_budget(work_id)
            budget.attempts = 3
            budgets.append(budget)
            return budget

        self.plan = [fetch(0), fetch(4), fetch(8), fetch(len(DATA))]
        self.text = 'The download finished before the budget boundary.'
        with patch.object(service, 'work_budget', limited_budget):
            self.assertTrue(service.run_one())
        self.assertEqual(self.store.job(job)['status'], 'partial')
        self.assertEqual(self.staged(job), DATA)
        checkpoint = self.store.read_checkpoint(job)
        self.assertEqual(checkpoint['received'], checkpoint['total'])
        old_budget, = budgets
        self.assertFalse(old_budget.ledger.counts()['active'])
        return old_budget, checkpoint

    def continue_download(self, job):
        offer = self.service.budget_continue_offer(job)
        self.assertEqual(offer['state'], 'offered')
        self.assertTrue(self.service.budget_continue_decision({'work_id': job, 'nonce': offer['nonce']})['resumed'])
        self.text = ANSWER
        self.plan = [fetch(len(DATA)), ('public_download', {'operation': 'preview', 'offset': 0})]
        self.claim_completion()

    def assert_completed_continuation(self, job, checkpoint):
        finished = self.store.job(job)
        self.assertEqual(finished['status'], 'succeeded')
        self.assertEqual(finished['goal_outcome'], 'reached')
        self.assertIn(ANSWER, finished['response'])
        rebound = self.store.read_checkpoint(job)
        self.assertGreater(rebound['epoch'], checkpoint['epoch'])
        self.assertEqual({**rebound, 'epoch': checkpoint['epoch']}, checkpoint)
        self.assertTrue(any(
            row['epoch'] == rebound['epoch'] and row['result'] and json.loads(row['result'])['outcome'] == 'yes'
            for row in self.rows('work_read_judgments', job)
        ))
        self.assertEqual(self.wire.offsets, [0, 4, 8])

    def test_budget_continue_judges_completed_download_and_refuses_old_commit(self):
        job = self.enqueue('read-budget-completed')
        service = self.service
        transport = service.adapter.transport
        reserved, release = threading.Event(), threading.Event()
        pending = None

        def late_decision():
            reserved.set()
            if not release.wait(timeout=10):
                raise WorkerReleased()
            return {'outcome': 'yes', 'value': 'stale judgment'}

        def reserve_before_pause(*args, **kwargs):
            nonlocal pending
            checkpoint = self.store.read_checkpoint(job)
            if pending is None and checkpoint and checkpoint['received'] == len(DATA):
                pending = pool.submit(
                    service.read_checkpoint_judgment, job, 'goal-reached', {'owner_request': REQUEST},
                    service.read_checkpoint_note({'id': job}, include_fresh=True), late_decision,
                    budget=service.current_decision_budget(),
                )
                self.assertTrue(reserved.wait(timeout=10))
            return transport(*args, **kwargs)

        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                with patch.object(service.adapter, 'transport', reserve_before_pause):
                    old_budget, checkpoint = self.pause_completed_download(job)
                stale_run = WorkRun(self.store.job(job), self.context_clock())
                stale_run.work_budget = old_budget
                self.continue_download(job)
                checked = []

                def finish_stale_worker(*args, **kwargs):
                    if not checked:
                        epoch = service.current_decision_budget().ledger.epoch
                        self.assertGreater(epoch, old_budget.ledger.epoch)
                        self.assertEqual(self.store.read_checkpoint(job)['epoch'], epoch)
                        before = (self.store.job(job), self.rows('messages', job), self.rows('work_read_judgments', job))
                        self.assertEqual(len(before[2]), 1)
                        self.assertIsNone(before[2][0]['result'])
                        release.set()
                        self.assertIsNone(pending.result(timeout=10))
                        self.assertIs(service._finish_work(stale_run), False)
                        self.assertEqual(
                            (self.store.job(job), self.rows('messages', job), self.rows('work_read_judgments', job)),
                            before,
                        )
                        checked.append(True)
                    return transport(*args, **kwargs)

                with patch.object(service.adapter, 'transport', finish_stale_worker):
                    self.assertTrue(service.run_one())
                self.assertEqual(checked, [True])
            finally:
                release.set()
        self.assert_completed_continuation(job, checkpoint)

    def test_budget_continue_retries_failed_early_budget_lookup(self):
        job = self.enqueue('read-budget-lookup-failure')
        service = self.service
        work_budget = service.work_budget
        _, checkpoint = self.pause_completed_download(job)
        self.continue_download(job)
        claimed = service._claim_queued_work()
        self.assertEqual(claimed['id'], job)
        self.assertNotIn('read_checkpoint_resume', claimed)
        self.assertEqual(self.store.job(job)['status'], 'running')

        with patch.object(service, 'work_budget', wraps=work_budget) as lookup:
            def fail_first_lookup(work_id):
                if lookup.call_count == 1:
                    raise RuntimeError('early budget lookup failed')
                return work_budget(work_id)

            lookup.side_effect = fail_first_lookup
            self.assertTrue(service.work_executor._run_claimed(claimed))
        self.assertGreaterEqual(lookup.call_count, 2)
        self.assert_completed_continuation(job, checkpoint)

    def test_rebind_requires_a_current_running_unstopped_writer(self):
        job = self.enqueue('read-budget-rebind-refused')
        service = self.service
        old_budget, checkpoint = self.pause_completed_download(job)
        self.continue_download(job)
        admitted = service.work_budget(job).ledger
        self.assertGreater(admitted.epoch, checkpoint['epoch'])
        self.assertEqual(self.store.job(job)['status'], 'queued')
        self.assertFalse(self.store.rebind_read_checkpoint(job, epoch=admitted.epoch))
        self.assertEqual(self.store.read_checkpoint(job), checkpoint)

        self.assertEqual(service._claim_queued_work()['id'], job)
        self.assertEqual(self.store.job(job)['status'], 'running')
        # Budget renewal advances the ledger before the reopen. Even an
        # intermediate epoch newer than the checkpoint is a stale writer.
        stale_epoch = admitted.epoch - 1
        self.assertGreater(stale_epoch, old_budget.ledger.epoch)
        self.assertFalse(self.store.rebind_read_checkpoint(job, epoch=stale_epoch))
        self.assertEqual(self.store.read_checkpoint(job), checkpoint)

        self.store.put(WORK_STOP_KEY, [job])
        self.assertTrue(service.work_stopped(job))
        self.assertFalse(self.store.rebind_read_checkpoint(job, epoch=admitted.epoch))
        self.assertEqual(self.store.read_checkpoint(job), checkpoint)

    def test_duplicate_claim_after_restart_preserves_identity_scope_and_budget(self):
        job = self.enqueue()
        original_service = self.service
        frozen = self.interrupt_after_range(job)
        before_job = self.store.job(job)
        old_budget = self.service.work_budget(job)
        before = old_budget.ledger.counts()
        self.assertGreater(before['turns'], 0)
        self.assertGreater(before['attempts'], 0)
        checkpoint = self.store.read_checkpoint(job)
        self.fresh_service().recover_interrupted_work()
        self.assertEqual(self.store.job(job)['status'], 'queued')
        services = [self.service, self.fresh_service()]
        barrier = threading.Barrier(2)

        def claim(service):
            barrier.wait(timeout=10)
            return service, service._claim_queued_work()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, services))
        winners = [(service, row) for service, row in results if isinstance(row, dict)]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(sum(row is False for _, row in results), 1, results)
        service, claimed = winners[0]
        self.assertIsNot(service, original_service)
        self.assertEqual(claimed['id'], job)
        self.assertEqual(claimed['status'], 'queued')  # the claimed snapshot's queue phase
        self.assertEqual(self.store.job(job)['status'], 'running')
        for key in ('message', 'request_key', 'channel', 'chat_id', 'owner_typed',
                    'workspace_id', 'related_job_id', 'relation_kind'):
            self.assertEqual(claimed[key], before_job[key], key)
        later = service.work_budget(job)
        after = later.ledger.counts()
        self.assertEqual(later.ledger.epoch, old_budget.ledger.epoch + 1)
        self.assertEqual(old_budget.ledger.lapse(), 'superseded')
        for key in ('turns', 'attempts', 'nudges', 'presentation', 'active_work_id'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(later.turns_used, before['turns'])
        self.assertLessEqual(after['deadline'], before['deadline'])
        self.assertEqual(self.store.read_checkpoint(job)['artifact'], checkpoint['artifact'])
        self.assertEqual(self.store.read_checkpoint(job)['received'], 4)
        self.service = service
        self.finish_script()
        # Dispatch has already claimed it; use the same production phase runner
        # used by run_one/dispatch rather than manufacturing a second claim.
        self.assertTrue(service.work_executor._run_claimed(claimed))
        self.assertEqual(self.store.job(job)['status'], 'succeeded')
        self.assertEqual(self.wire.offsets, [0, 4, 8])
        self.assertEqual(self.staged(job), DATA)
        finished_counts = self.store.config(later.ledger.key)
        self.assertGreater(finished_counts['turns'], before['turns'])
        self.assertGreater(finished_counts['attempts'], before['attempts'])
        self.assertLessEqual(finished_counts['deadline'], before['deadline'])
        users = [r for r in self.rows('messages', job) if r['role'] == 'user']
        self.assertEqual([r['content'] for r in users], [REQUEST])
        self.assertFalse(service._claim_queued_work())
        self.release_frozen_worker(frozen)

    def test_killed_judgment_is_unfinished_and_reinvoked_once_after_recovery(self):
        job = self.enqueue()
        calls = []
        frozen = FrozenWorker(self, self.service, job)

        def judge(facts):
            calls.append(dict(facts))
            self.assertEqual(self.store.read_checkpoint(job)['received'], len(DATA))
            if len(calls) == 1:
                frozen.block()
            return True

        self.plan = [fetch(0), fetch(4), fetch(8),
                     ('public_download', {'operation': 'preview', 'offset': 0})]
        self.claim = True
        self.service.use_decision_engine(goal_engine(judge))
        self.start_frozen_worker(frozen)
        unfinished = self.rows('work_read_judgments', job)
        self.assertEqual(len(unfinished), 1)
        self.assertIsNone(unfinished[0]['result'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.job(job)['status'], 'running')
        self.assertFalse(any(r['role'] == 'assistant' for r in self.rows('messages', job)))
        self.fresh_service().recover_interrupted_work()
        self.assertEqual(self.store.job(job)['status'], 'queued')
        self.assertIsNone(self.rows('work_read_judgments', job)[0]['result'])
        self.plan = [('public_download', {'operation': 'preview', 'offset': 0})]
        self.claim = True
        self.service.use_decision_engine(goal_engine(judge))
        self.assertTrue(self.service.run_one())
        self.assertEqual(len(calls), 2)
        committed = [json.loads(r['result']) for r in self.rows('work_read_judgments', job)
                     if r['result'] is not None]
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]['outcome'], 'yes')
        self.assertEqual(self.store.job(job)['status'], 'succeeded')
        self.assertEqual(self.wire.offsets, [0, 4, 8])
        self.assertFalse(self.service.run_one())
        self.assertEqual(len(calls), 2)
        self.release_frozen_worker(frozen)
        self.assertEqual(len(calls), 2)

    # #1007: a graceful stop (signal -> SystemExit) inside a judgment keeps auto-resume.
    def test_graceful_exit_during_judgment_still_resumes_checkpointed_work(self):
        job = self.enqueue('graceful-exit')
        calls = []

        def judge(facts):
            calls.append(dict(facts))
            raise SystemExit('graceful stop inside judgment')

        self.plan = [fetch(0)]
        self.claim = True
        self.service.use_decision_engine(goal_engine(judge))
        with self.assertRaisesRegex(SystemExit, 'graceful stop inside judgment'):
            self.service.run_one()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.job(job)['status'], 'running')
        unfinished = self.rows('work_read_judgments', job)
        self.assertEqual(len(unfinished), 1)
        self.assertIsNone(unfinished[0]['result'])
        self.assertEqual(self.store.read_checkpoint(job)['received'], 4)
        self.fresh_service().recover_interrupted_work()
        self.assertEqual(self.store.job(job)['status'], 'queued')

    def test_old_epoch_judgment_cannot_commit_or_deliver_after_reclaim(self):
        job = self.enqueue(telegram=True)
        frozen = self.interrupt_after_range(job)
        old_service = self.service
        old_job = self.store.job(job)
        old_budget = old_service.work_budget(job)
        note = old_service.read_checkpoint_note({'id': job}, include_fresh=True)
        calls, current_state = [], []
        late = {'outcome': 'yes', 'value': 'stale result must never be published'}

        def in_flight_decision():
            calls.append(True)
            self.fresh_service().recover_interrupted_work()
            claimed = self.service._claim_queued_work()
            self.assertEqual(claimed['id'], job)
            self.assertNotEqual(claimed['read_checkpoint_resume'], old_budget.ledger.epoch)
            current_state.append((self.store.job(job), self.rows('messages', job), list(self.sent)))
            return late

        result = old_service.read_checkpoint_judgment(
            job, 'goal-reached', {'owner_request': REQUEST}, note,
            in_flight_decision, budget=old_budget)
        self.assertIsNone(result)
        self.assertEqual(calls, [True])
        judgments = self.rows('work_read_judgments', job)
        self.assertEqual(len(judgments), 1)
        self.assertIsNone(judgments[0]['result'])
        # Reuse #1008's stale WorkRun check as well: even a late worker
        # reaching the terminal phase cannot publish a reply for the new run.
        stale_run = WorkRun(old_job, self.context_clock())
        stale_run.work_budget = old_budget
        self.assertIs(old_service._finish_work(stale_run), False)
        self.assertEqual((self.store.job(job), self.rows('messages', job), self.sent), current_state[0])
        self.assertEqual(self.store.job(job)['status'], 'running')
        self.assertEqual(self.store.job(job)['delivery'], 'none')
        self.service.deliver_one()
        self.service.deliver_one()
        self.assertEqual(self.sent, current_state[0][2])
        self.assertFalse(any(r['role'] == 'assistant' for r in self.rows('messages', job)))
        self.assertEqual(self.wire.offsets, [0])
        self.release_frozen_worker(frozen)

    def read_evidence(self, job):
        """Compare complete typed public-read Evidence, normalising IDs only.

        Model/attempt audit events differ legitimately across restart. These
        are every successful read's full Evidence rows, without field filtering.
        """
        checkpoint = self.store.read_checkpoint(job)
        evidence = []
        for row in self.rows('tool_events', job):
            detail = json.loads(row['detail'])
            if row['tool'] == 'public_download' and row['status'] == 'succeeded':
                self.assertIn('evidence', detail)
                # Artifact is a random host ID, not an evidence difference.
                item = json.loads(json.dumps(detail['evidence']).replace(checkpoint['artifact'], '<artifact-id>'))
                evidence.append({'tool': row['tool'], 'status': row['status'], 'evidence': item})
        self.assertEqual(len(evidence), 4)  # three fetches plus the local preview
        return evidence

    def test_uninterrupted_control_matches_resumed_bytes_answer_and_evidence(self):
        control = self.enqueue('control')
        self.plan = [fetch(0), *[fetch(n) for n in (4, 8)],
                     ('public_download', {'operation': 'preview', 'offset': 0})]
        self.claim_completion()
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(control)['status'], 'succeeded')
        expected_bytes = self.staged(control)
        expected_answer = self.store.job(control)['response']
        expected_evidence = self.read_evidence(control)
        # A separate store, not a new Work sharing the control's transcript.
        self.store = QuickStore(self.root / 'resumed-control')
        self.service = self.make_service()
        resumed = self.enqueue('resumed')
        frozen = self.interrupt_after_range(resumed)
        self.fresh_service().recover_interrupted_work()
        self.finish_script()
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(resumed)['status'], 'succeeded')
        self.assertEqual(self.staged(resumed), expected_bytes)
        self.assertEqual(expected_bytes, DATA)
        self.assertEqual(self.store.job(resumed)['response'], expected_answer)
        self.assertEqual(expected_answer, ANSWER)
        self.assertEqual(self.read_evidence(resumed), expected_evidence)
        self.assertEqual(self.wire.offsets, [0, 4, 8, 0, 4, 8])
        self.release_frozen_worker(frozen)

    def test_same_preview_offset_runs_again_after_fetch_advances_checkpoint(self):
        job = self.enqueue()
        preview = ('public_download', {'operation': 'preview', 'offset': 0})
        self.plan = [fetch(0), fetch(4), preview, fetch(8), preview]
        self.claim_completion()

        self.assertTrue(self.service.run_one())

        observations = [json.loads(row['detail'])['evidence'] for row in self.rows('tool_events', job)
                        if row['tool'] == 'public_download' and row['status'] == 'succeeded']
        self.assertEqual([(item['offset'], item['received']) for item in observations if 'offset' in item],
                         [(0, 8), (0, len(DATA))])

    def test_same_preview_offset_is_duplicate_at_unchanged_checkpoint(self):
        job = self.enqueue()
        preview = ('public_download', {'operation': 'preview', 'offset': 0})
        self.plan = [fetch(0), preview, preview]
        self.claim_completion()

        self.assertTrue(self.service.run_one())

        events = [row for row in self.rows('tool_events', job) if row['tool'] == 'public_download']
        observations = [json.loads(row['detail'])['evidence'] for row in events if row['status'] == 'succeeded']
        self.assertEqual([(item.get('offset'), item['received']) for item in observations], [(None, 4), (0, 4)])
        failures = [json.loads(row['detail']) for row in events if row['status'] == 'failed']
        self.assertEqual([(item['code'], item['ran']) for item in failures], [('duplicate_call', False)])

    def test_owner_intake_final_answer_and_delivery_are_each_exactly_once(self):
        job = self.enqueue(telegram=True)
        frozen = self.interrupt_after_range(job)
        self.fresh_service().recover_interrupted_work()
        self.finish_script()
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(job)['status'], 'succeeded')
        rows = self.rows('messages', job)
        self.assertEqual([r['content'] for r in rows if r['role'] == 'user'], [REQUEST])
        self.assertEqual([r['content'] for r in rows if r['role'] == 'assistant'], [ANSWER])
        self.assertEqual(self.store.job(job)['delivery'], 'pending')
        before = list(self.sent)
        self.service.deliver_one()
        self.assertEqual(self.store.job(job)['delivery'], 'sent')
        delivered = list(self.sent)
        self.assertEqual(delivered[len(before):], [ANSWER])
        self.service.deliver_one()
        self.assertFalse(self.service.run_one())
        self.assertEqual(self.sent, delivered)
        self.assertEqual(self.rows('messages', job), rows)
        self.assertEqual(self.wire.offsets, [0, 4, 8])
        self.release_frozen_worker(frozen)

    def test_auto_resume_is_blocked_by_stop_expiry_or_unknown_effect(self):
        for barrier in ('stop', 'expired', 'unknown_effect'):
            with self.subTest(barrier=barrier):
                self.store = QuickStore(self.root / barrier)
                self.service = self.make_service()
                job = self.enqueue(barrier)
                frozen = self.interrupt_after_range(job)
                budget = self.service.work_budget(job)
                if barrier == 'stop':
                    self.store.put(WORK_STOP_KEY, [job])
                elif barrier == 'expired':
                    self.store.put(budget.ledger.key, {**budget.ledger.counts(), 'deadline': 0})
                else:
                    # Same pre-existing ambiguous action as #1008, on top of a
                    # real validated/staged checkpoint rather than a fake row.
                    with self.store.db() as db:
                        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                                   (job, 'api_request', 'failed', json.dumps({'effect': 'unknown'}),
                                    self.context_clock()))
                    self.assertIn('unknown', self.service.continuation_reasons(job, 'resume'))
                offsets = list(self.wire.offsets)
                self.fresh_service().recover_interrupted_work()
                self.assertEqual(self.store.job(job)['status'], 'interrupted')
                self.assertFalse(self.store.read_checkpoint(job)['resume_pending'])
                self.assertFalse(self.service._claim_queued_work())
                self.assertFalse(self.service.run_one())
                self.assertEqual(self.wire.offsets, offsets)
                self.assertEqual(self.store.read_checkpoint(job)['received'], 4)
                self.release_frozen_worker(frozen)

    def test_new_interval_judgment_sees_scope_and_identical_progress_reuses_result(self):
        job = self.enqueue()
        calls, replays, coverages = [], [], []
        original = self.service.adapter.transport
        self.plan = [fetch(0), fetch(4)]
        self.claim = True

        def judge(facts):
            calls.append(dict(facts))
            checkpoint = self.store.read_checkpoint(job)
            coverages.append((checkpoint['revision'], checkpoint['received']))
            self.assertGreater(checkpoint['total'] - checkpoint['received'], 0)
            # Compare quantities, without pinning any model-facing phrase.
            for field in ('continues', 'observations'):
                numbers = [int(n) for n in re.findall(r'\b\d+\b', facts[field])]
                self.assertIn([checkpoint['received'], checkpoint['total'],
                               checkpoint['total'] - checkpoint['received']],
                              [numbers[n:n+3] for n in range(len(numbers)-2)])
            return False

        self.service.use_decision_engine(goal_engine(judge))

        def next_model_call(*args, **kwargs):
            checkpoint = self.store.read_checkpoint(job)
            if checkpoint and checkpoint['received'] == 4 and not calls:
                # Evaluate the first committed interval before delegating the
                # next range. Its completed result must not stand in for the
                # real finish judgment after the new interval arrives.
                note = self.service.read_checkpoint_note({'id': job}, include_fresh=True)
                first = self.service.decision_judge.goal_reached(
                    REQUEST, note, work_id=job, answer=ANSWER, continues=note)
                self.assertEqual(first.outcome, 'no')
                self.assertEqual(len(calls), 1)
            if len(calls) == 2:
                # The rejected finish claim returns to the model. Repeat the
                # identical evaluation while the Work still holds its epoch.
                # Both evaluations use ConversationJudgments and the real
                # service/checkpoint_judgment transaction, never a patched cache.
                facts = calls[-1]
                before = self.rows('work_read_judgments', job)
                self.assertEqual(len(before), 2)
                self.assertTrue(all(json.loads(row['result'])['outcome'] == 'no' for row in before))
                replay = self.service.decision_judge.goal_reached(
                    facts['owner_request'], facts['observations'], facts['failed_steps'],
                    work_id=job, answer=facts['reply'], conversation=facts['recent_conversation'],
                    owner_context=facts['owner_context'], continues=facts['continues'],
                    attachments=facts['attachments'], awaiting_owner=facts['awaiting_owner'],
                    reply_source=facts['reply_source'], reply_delivery=facts['reply_delivery'],
                    plan_reading=facts.get('plan_reading', ''))
                self.assertEqual(replay.outcome, 'no')
                self.assertEqual(len(calls), 2)
                self.assertEqual(self.rows('work_read_judgments', job), before)
                replays.append(replay)
                raise EvaluationComplete('after identical evaluation')
            return original(*args, **kwargs)

        with patch.object(self.service.adapter, 'transport', next_model_call):
            with self.assertRaisesRegex(EvaluationComplete, 'identical evaluation'):
                self.service.run_one()
        self.assertEqual(len(replays), 1)
        self.assertEqual(len(calls), 2)  # one per distinct committed coverage
        self.assertEqual(coverages, [(1, 4), (2, 8)])
        self.assertEqual(self.wire.offsets, [0, 4])
        self.assertEqual(self.store.job(job)['status'], 'running')
        self.assertEqual(self.staged(job), DATA[:8])
