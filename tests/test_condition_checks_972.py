"""#972: condition checks - the AI's own code watches, and wakes the AI only on a hit.

Evidence class: model-free unit and store-level tests.  The real QuickStore, ``Preparations``,
``AgentService`` (its schedule handler, history, Settings view, payment-autonomy gate) and
``CodeRunner`` request decisions run unchanged; only the sandbox process is a fake runner
(``test_code_run.FakeProcess``) and the clock is a fake clock.  No live model, sandbox,
network or account is contacted, and none is claimed.  No service is named: the fixtures are a
synthetic API at example hosts.
"""
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from personal_agent import agent_runtime, condition_checks as cc, preparations as prep
from personal_agent.agent_runtime import ToolError, outside_source, record_outside_result, result_outside_source
from personal_agent.api_credentials import ApiCredentials
from personal_agent.code_run import MAX_SECONDS, CodeRunner
from personal_agent.procedures import ProcedureLibrary
from personal_agent.quickstart_service import AgentService
from test_code_run import FakeProcess
import test_code_run_secrets as code_secrets

HOST = code_secrets.HOST
NOW = 1_800_000_000.0
CHECK_CODE = 'import json, os\nprint(os.environ.get("X", ""))\n'


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='seconds')


class _Base(unittest.TestCase):
    MONEY = False

    def setUp(self):
        storage = code_secrets.Storage(methodName='setUp')
        storage.setUp()
        self.addCleanup(storage.doCleanups)
        self.store, self.save = storage.store, storage.save
        self.save(money=self.MONEY)
        self.service = AgentService(self.store)
        self.now = NOW
        self.service.preparations.clock = lambda: self.now
        #: Each check run takes the next fake runner process; ``runs`` keeps what each was handed.
        self.queue, self.runs = [], []
        self.checks = cc.ConditionChecks(self.service, runner=self.runner)
        self.service.condition_checks = self.checks

    # -- fakes -----------------------------------------------------------------------------

    def runner(self, store, record_id, **kwargs):
        process = self.queue.pop(0) if self.queue else FakeProcess(result={'stdout': '', 'stderr': ''})
        self.runs.append(process)
        return cc.CheckRunner(store, record_id, popen=lambda *a, **k: process, node='/usr/bin/node',
                              install=lambda store, node: store.root, **kwargs)

    def owner_work(self, text='watch it'):
        return self.store.job(self.store.enqueue(text, f'web-{len(self.store.jobs())}', owner_typed=True))

    def code_run(self, job, args, process=None, approvals=None):
        """One ``code_run`` of ``job``, recorded as both worker routes record its Evidence."""
        process = process or FakeProcess(result={'stdout': '', 'stderr': ''})
        runner = CodeRunner(self.store, job['id'], popen=lambda *a, **k: process, node='/usr/bin/node',
                            install=lambda store, node: store.root, approvals=approvals)
        result = runner.run(args)
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job['id'], 'code_run', 'succeeded',
                        json.dumps({'host_action': 'code_run', 'evidence': agent_runtime.evidence_summary('code_run', result)}),
                        self.now))
        return result

    def check_args(self, **changes):
        return {'code': CHECK_CODE, 'hosts': '', 'secrets': 'example-broker', 'timeout_seconds': 30, **changes}

    def proven(self, job=None, **changes):
        job = job or self.owner_work()
        args = self.check_args(**changes)
        self.code_run(job, {key: value for key, value in args.items() if key != 'every_seconds'})
        return job, args

    def check_row(self, every=15, accepted_by=prep.ACCEPTED_OWNER_REQUEST, until=3600, max_runs=None, line_id=None, **changes):
        job, args = self.proven(**changes)
        spec = cc.proven_check(self.store, job['id'], {**args, 'every_seconds': every})
        return self.service.preparations.create(
            kind='prepare', goal='Tell me when the value holds past the level.', due_at=self.now, timezone='UTC',
            recurrence=None, channel='web', created_from=job['id'], state=prep.STATE_SCHEDULED, accepted_by=accepted_by,
            window=(every, self.now + until, max_runs or cc.DAILY_WAKES), delivery_mode=prep.DELIVERY_WHEN_NEEDED,
            line_id=line_id, check=spec)

    def row(self, preparation_id):
        return self.service.preparations.get(preparation_id)

    def woken(self, preparation_id):
        with self.store.db() as db:
            return [dict(item) for item in db.execute('SELECT * FROM jobs WHERE request_key LIKE ? ORDER BY created',
                                                      (prep.REQUEST_KEY_PREFIX + preparation_id + ':%',))]

    def hit(self, text='level held 180 s'):
        self.queue.append(FakeProcess(result={'stdout': text, 'stderr': ''}))

    def state_process(self, state, result):
        process = FakeProcess(result=result)
        original = process._lines

        def write_state():
            with open(process.config['env'][cc.STATE_ENV], 'w') as handle:
                json.dump(state, handle)
            yield from original()
        process._lines = write_state
        return process

    def tick(self, seconds=15):
        self.now += seconds
        return self.checks.run_due()

    def settle(self, preparation_id, status='succeeded'):
        work = self.row(preparation_id)['last_run_job_id']
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status=?,response='done',delivery='none' WHERE id=?", (status, work))
        self.service.preparations.settle(self.row(preparation_id), self.now)
        return work


class Setup(_Base):
    def schedule(self, job, args, **extra):
        call = self.service.preparation_scheduler(job, None)
        return call({'kind': 'prepare', 'goal': 'Tell me when the value holds past the level.', 'due': iso(self.now),
                     'until': iso(self.now + 3600), 'delivery': 'when_needed', 'check': json.dumps(args), **extra})

    def refused(self, code, job, args, **extra):
        with self.assertRaises(ToolError) as caught:
            self.schedule(job, args, **extra)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.service.preparations.rows(), [])

    def test_a_proven_check_is_scheduled_with_its_pinned_library_and_digest(self):
        library = ProcedureLibrary(self.store)
        library.propose('earlier', 'example-broker', 'def value():\n    """Value."""\n', [{'name': 'value', 'doc': 'Value.'}])
        library.settle('earlier', True)
        job, args = self.proven()
        result = self.schedule(job, {**args, 'every_seconds': 15})
        self.assertTrue(result['scheduled'])
        self.assertEqual(result['check']['every_seconds'], 15)
        spec = cc.spec_of(self.row(result['preparation_id']))
        self.assertEqual(spec['libraries'], {'example-broker': library.current('example-broker')['id']})
        self.assertEqual(spec['digest'], cc.spec_digest(spec))
        # A later library save does not change the running check.
        library.propose('later', 'example-broker', 'def value():\n    """Other."""\n', [{'name': 'value', 'doc': 'Other.'}])
        library.settle('later', True)
        self.assertEqual(cc.spec_of(self.row(result['preparation_id']))['libraries'], spec['libraries'])
        # The setup notice carries one line on the check.
        self.assertIn('15', self.service.preparations_service.scheduled_notice_text([result['preparation_id']]))

    def test_arguments_that_do_not_match_a_successful_run_are_refused(self):
        job, args = self.proven()
        self.refused('check_not_proven', job, {**args, 'code': CHECK_CODE + '# changed\n'})
        self.refused('check_not_proven', job, {**args, 'timeout_seconds': 31})
        self.refused('check_not_proven', self.owner_work('another work'), args)

    def test_a_proving_run_that_timed_out_failed_changed_state_or_asked_is_refused(self):
        cases = {'timed out': FakeProcess(result={'timedOut': True, 'exit': None}),
                 'non-zero exit': FakeProcess(result={'exit': 1, 'stderr': 'boom'})}
        for label, process in cases.items():
            with self.subTest(label):
                job = self.owner_work(label)
                self.code_run(job, self.check_args(), process)
                self.refused('check_not_proven', job, self.check_args())
        # A state change sent from the run (a non-money set's host takes it without an approval).
        job = self.owner_work('changed state')
        change = FakeProcess(requests=[{'method': 'POST', 'host': HOST, 'path': '/v1/records/1', 'query': '', 'bodyText': '{}'}],
                             result={'stdout': '', 'stderr': ''})
        result = self.code_run(job, self.check_args(), change, approvals=code_secrets.FakeApprovals())
        self.assertTrue(result['effects'])
        self.refused('check_not_proven', job, self.check_args())

    def test_a_proving_run_that_asked_the_owner_is_refused(self):
        self.save(money=True)
        job = self.owner_work('asked')
        asking = FakeProcess(requests=[{'method': 'POST', 'host': HOST, 'path': '/v1/orders', 'query': '', 'bodyText': '{}'}],
                             result={'stdout': '', 'stderr': ''})
        result = self.code_run(job, {**self.check_args(), 'approval_note': 'buy one'}, asking,
                               approvals=code_secrets.FakeApprovals())
        self.assertTrue(result['approval_requested'])
        self.refused('check_not_proven', job, self.check_args())

    def test_packages_and_a_short_interval_are_refused_and_timeouts_clamped(self):
        job, args = self.proven()
        self.refused('check_packages', job, {**args, 'packages': 'six==1.16.0'})
        self.refused('invalid_check', job, {**args, 'every_seconds': cc.MIN_EVERY_SECONDS - 1})
        self.refused('invalid_check', job, args, every_minutes='5')
        spec = cc.proven_check(self.store, job['id'], {**args, 'every_seconds': 15})
        self.assertEqual(spec['timeout_seconds'], 15, 'a timeout above the interval is clamped to it')
        slow, slow_args = self.proven(timeout_seconds=600)
        spec = cc.proven_check(self.store, slow['id'], {**slow_args, 'every_seconds': 600})
        self.assertEqual(spec['timeout_seconds'], MAX_SECONDS, 'and never above MAX_SECONDS')

    def test_the_eleventh_active_check_is_refused(self):
        for _ in range(prep.MAX_ACTIVE_CHECKS):
            self.check_row()
        with self.assertRaises(prep.PreparationRefusal) as caught:
            self.check_row()
        self.assertEqual(caught.exception.code, 'too_many_checks')

    def test_a_scheduled_run_cannot_set_up_a_new_check(self):
        row = self.check_row()
        job, args = self.proven()
        self.hit()
        self.tick(0)
        woken = self.store.job(self.row(row['id'])['last_run_job_id'])
        with self.assertRaises(ToolError) as caught:
            self.schedule(woken, {**args, 'every_seconds': 15})
        self.assertEqual(caught.exception.code, 'unattended_run')


class Running(_Base):
    def test_new_check_state_secrets_are_redacted_before_the_first_hit_is_persisted(self):
        row = self.check_row()
        token = 'fresh-check-value-opaque-00000001'
        plain = 'owner-visible-description-00001'
        self.queue.append(self.state_process({'nested': [token], 'plain': {'description': plain}},
                                             {'stdout': f'{plain}: {token}', 'stderr': token}))
        self.tick(0)
        [work] = self.woken(row['id'])
        with self.store.db() as db:
            [event] = db.execute('SELECT detail FROM tool_events WHERE job_id=? AND tool=?',
                                (work['id'], prep.CHECK_EVENT_TOOL)).fetchall()
        self.assertNotIn(token, event['detail'], 'redact before SQLite, not only when displaying the handoff')
        self.assertIn('[redacted]', json.loads(event['detail'])['output'])
        self.assertIn(plain, json.loads(event['detail'])['output'])
        self.assertNotIn(token, json.dumps(self.checks.handoff(work)))

    def test_new_check_state_secrets_are_redacted_from_each_failure_and_its_wake(self):
        row = self.check_row()
        for count in range(cc.FAILURES_BEFORE_PAUSE):
            token = f'fresh-check-exception-value-{count:08d}'
            self.queue.append(self.state_process({'token': token},
                                                 {'exit': 1, 'stderr': f'RuntimeError: {token}'}))
            self.tick(15)
            current = self.row(row['id'])
            self.assertNotIn(token, current['check_status'])
            self.assertEqual(cc.status_of(current)['last_error']['diagnostic'], 'RuntimeError: [redacted]')
        [work] = self.woken(row['id'])
        with self.store.db() as db:
            [event] = db.execute('SELECT detail FROM tool_events WHERE job_id=? AND tool=?',
                                (work['id'], prep.CHECK_EVENT_TOOL)).fetchall()
        self.assertNotIn(token, event['detail'])
        self.assertEqual(json.loads(event['detail'])['diagnostic'], 'RuntimeError: [redacted]')

    def test_queue_full_keeps_previous_edge_state_until_the_hit_is_admitted(self):
        row = self.check_row()
        spec, key = cc.spec_of(row), cc.state_key(row['id'])
        credentials = ApiCredentials(self.store)
        previous = json.dumps({'reported': False, 'cursor': 7})
        self.assertTrue(credentials.write_bound_state(key, spec['digest'], previous))
        seen = []

        def edge_process():
            process = FakeProcess()
            original = process._lines

            def report_once():
                path = process.config['env'][cc.STATE_ENV]
                with open(path) as handle:
                    state = json.load(handle)
                seen.append(state.copy())
                process._result = {'stdout': '' if state['reported'] else 'new event'}
                state['reported'] = True
                with open(path, 'w') as handle:
                    json.dump(state, handle)
                yield from original()
            process._lines = report_once
            return process

        self.queue.append(edge_process())
        with mock.patch.object(self.store, 'enqueue', side_effect=ValueError('queue full')):
            self.tick(0)
        self.assertEqual(self.woken(row['id']), [])
        current = self.row(row['id'])
        self.assertEqual(cc.status_of(current)['wake_refused']['reason'], 'queue-full')
        self.assertEqual(credentials.read_bound_state(key, spec['digest']), previous)
        day = self.service.preparations.budget_day(self.now)
        self.assertIsNone(self.store.config(prep.SCHEDULED_USAGE_PREFIX + day))
        self.queue.append(edge_process())
        self.tick(15)
        [work] = self.woken(row['id'])
        self.assertEqual(self.checks.handoff(work)['output'], 'new event')
        self.assertEqual(seen, [json.loads(previous), json.loads(previous)])
        self.assertTrue(json.loads(credentials.read_bound_state(key, spec['digest']))['reported'])
        self.assertEqual(self.store.config(prep.SCHEDULED_USAGE_PREFIX + day), 1)
        self.assertEqual(self.checks.wakes_today(self.row(row['id']), self.now), 1)
        self.assertFalse(self.tick(15), 'one wake at a time')
        self.settle(row['id'])
        self.queue.append(edge_process())
        self.tick(15)
        self.assertEqual(len(self.woken(row['id'])), 1, 'the admitted event is not reported again')

    def test_woken_work_audit_records_the_checks_api_use_without_further_tools(self):
        row = self.check_row()
        self.queue.append(FakeProcess(requests=[
            {'method': 'GET', 'host': HOST, 'path': '/value', 'query': ''},
            {'method': 'GET', 'host': 'refused.example.net', 'path': '/', 'query': ''}],
            result={'stdout': 'new event', 'stderr': ''}))
        self.tick(0)
        work = self.settle(row['id'])
        audit = self.service.work_information_use(work)
        used = {item['category']: item['items'] for item in audit['used']}
        self.assertIn('example-broker', used.get('api', []))
        self.assertEqual(audit['sent_to']['connections'], [
            {'engine': 'agentos', 'phase': 'condition-check', 'destination': HOST,
             'state': 'reached', 'remote_receipt': ''}])
        self.assertNotIn('refused.example.net', json.dumps(audit))
        self.assertEqual([event['tool'] for event in self.store.task_events(work)
                          if event['tool'] == 'code_run'], [])
        # The same authenticated read in an ordinary Work shows the same facts,
        # with its actual route, rather than claiming the woken Work called code_run.
        ordinary = self.owner_work('read the value')
        self.code_run(ordinary, self.check_args(), FakeProcess(requests=[
            {'method': 'GET', 'host': HOST, 'path': '/value', 'query': ''},
            {'method': 'GET', 'host': 'refused.example.net', 'path': '/', 'query': ''}]))
        direct = self.service.work_information_use(ordinary['id'])
        self.assertEqual(direct['used'], audit['used'])
        self.assertEqual(direct['sent_to']['connections'], [
            {**audit['sent_to']['connections'][0], 'phase': 'code-run'}])

    def test_a_quiet_run_costs_no_work_and_runs_again_one_interval_after_it_ended(self):
        row = self.check_row()
        self.assertTrue(self.tick(0))
        self.assertEqual(self.woken(row['id']), [])
        current = self.row(row['id'])
        self.assertEqual(current['state'], prep.STATE_SCHEDULED)
        self.assertEqual(current['check_next_at'], self.now + 15)
        self.assertEqual(cc.status_of(current)['last_result'], cc.QUIET)
        self.assertFalse(self.tick(10), 'not due yet: one indexed query, nothing run')
        self.assertEqual(len(self.runs), 1)
        # The run was the saved arguments under the code_run sandbox, with its state file.
        self.assertIn(cc.STATE_ENV, self.runs[0].config['env'])
        self.assertEqual(self.runs[0].config['allowedDomains'], [HOST])

    def test_a_check_never_sends_a_state_change_even_to_its_sets_host(self):
        for money in (False, True):
            with self.subTest(money=money):
                self.save(money=money)
                row = self.check_row()
                process = FakeProcess(requests=[
                    {'method': 'GET', 'host': HOST, 'path': '/v1/quote', 'query': ''},
                    {'method': 'POST', 'host': HOST, 'path': '/oauth2/token', 'query': '', 'bodyText': ''},
                    {'method': 'POST', 'host': HOST, 'path': '/v1/orders', 'query': '', 'bodyText': '{"qty":"1"}'},
                    {'method': 'GET', 'host': 'other.example.net', 'path': '/', 'query': ''}],
                    result={'stdout': '', 'stderr': ''})
                self.queue.append(process)
                self.tick(15)
                self.assertEqual([item['allow'] for item in process.decisions], [True, True, False, False])
                with self.store.db() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM tool_events WHERE tool='api_dispatch'").fetchone()[0], 0)
                self.service.preparations.cancel(row['id'])

    def test_masked_values_stay_with_the_sets_hosts_and_stored_values_never_reach_the_handoff(self):
        row = self.check_row()
        self.hit(f"value for {code_secrets.VALUES['ACCOUNT']} key {code_secrets.VALUES['APP_KEY']}")
        self.tick(0)
        masked = {item['name']: item['injectHosts'] for item in self.runs[0].config['masked']}
        self.assertEqual(masked, {'EXAMPLE_BROKER_APP_KEY': ['api.example.com'], 'EXAMPLE_BROKER_APP_SECRET': ['api.example.com']})
        handoff = self.checks.handoff(self.store.job(self.row(row['id'])['last_run_job_id']))
        for value in code_secrets.VALUES.values():
            self.assertNotIn(value, json.dumps(handoff))
        # An in_code set limits the run to the set's hosts, so no check reaching another host can be proven.
        from personal_agent.code_run import CodeRunError
        with self.assertRaises(CodeRunError):
            self.proven(hosts='public.example.org')

    def test_a_failure_diagnostic_is_redacted(self):
        row = self.check_row()
        for _ in range(cc.FAILURES_BEFORE_PAUSE):
            self.queue.append(FakeProcess(result={'exit': 1, 'stderr': f"bad key {code_secrets.VALUES['APP_KEY']}"}))
            self.tick(15)
        handoff = self.checks.handoff(self.store.job(self.row(row['id'])['last_run_job_id']))
        self.assertEqual(handoff['kind'], cc.FAILURE)
        self.assertNotIn(code_secrets.VALUES['APP_KEY'], json.dumps(handoff))
        self.assertNotIn(code_secrets.VALUES['APP_KEY'], self.row(row['id'])['check_status'])

    def test_a_hit_wakes_exactly_one_work_and_the_check_waits_for_it(self):
        row = self.check_row()
        self.hit()
        self.tick(0)
        [work] = self.woken(row['id'])
        current = self.row(row['id'])
        self.assertEqual((current['state'], current['last_run_job_id'], current['run_count']), (prep.STATE_RUNNING, work['id'], 1))
        with self.store.db() as db:
            admitted = db.execute("SELECT COUNT(*) FROM tool_events WHERE job_id=? AND tool=? AND status='admitted'",
                                  (work['id'], prep.SCHEDULED_BUDGET_KEY)).fetchone()[0]
        self.assertEqual(admitted, 1, 'the wake is charged to the shared daily budget')
        # While the woken run is active, the check does not run and so cannot wake again.
        self.hit('again')
        self.assertFalse(self.tick(60))
        self.assertEqual(len(self.woken(row['id'])), 1)
        # The woken run reads the brief, then the check's output as data.
        history = self.service.preparations_service.preparation_history(self.store.job(work['id']))
        self.assertTrue(history[-1]['content'].startswith('Tell me when the value holds past the level.'))
        self.assertIn('level held 180 s', history[-1]['content'])
        # It resumes when that run settles.
        self.settle(row['id'])
        self.assertEqual(self.row(row['id'])['state'], prep.STATE_SCHEDULED)
        self.tick(0)
        self.assertEqual(len(self.woken(row['id'])), 2)

    def test_a_replayed_wake_finds_the_same_work(self):
        row = self.check_row()
        current = self.row(row['id'])
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            first = self.service.preparations.wake(db, current, at=self.now, channel='web', chat_id=None, now=self.now,
                                                   handoff={'kind': cc.HIT, 'at': self.now, 'output': 'x'})
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            again = self.service.preparations.wake(db, current, at=self.now, channel='web', chat_id=None, now=self.now,
                                                   handoff={'kind': cc.HIT, 'at': self.now, 'output': 'x'})
        self.assertEqual(first, again)
        self.assertEqual(len(self.woken(row['id'])), 1)

    def test_the_twenty_fifth_wake_of_a_day_is_refused_even_with_the_budget_off(self):
        self.service.preparations.set_budget(0)
        row = self.check_row(every=15, until=86400 - 1, max_runs=100)
        for _ in range(cc.DAILY_WAKES):
            self.hit()
            self.tick(15)
            self.settle(row['id'])
        self.assertEqual(len(self.woken(row['id'])), cc.DAILY_WAKES)
        self.hit()
        self.tick(15)
        self.assertEqual(len(self.woken(row['id'])), cc.DAILY_WAKES)
        current = self.row(row['id'])
        self.assertEqual(current['state'], prep.STATE_SCHEDULED)
        self.assertGreater(current['check_next_at'], self.now + 15, 'no further run until the next owner-local day')

    def test_with_the_budget_spent_a_hit_does_not_wake_and_the_check_waits_for_the_next_day(self):
        self.service.preparations.set_budget(1)
        day = self.service.preparations.budget_day(self.now)
        self.store.put(prep.SCHEDULED_USAGE_PREFIX + day, 1)
        row = self.check_row()
        self.hit()
        self.tick(0)
        self.assertEqual(self.woken(row['id']), [])
        current = self.row(row['id'])
        self.assertEqual(cc.status_of(current)['wake_refused']['reason'], 'budget')
        self.assertEqual(current['check_next_at'], self.checks._next_day_start(self.now))
        with self.store.db() as db:
            notice = db.execute('SELECT content FROM messages WHERE job_id=?', (prep.SCHEDULED_USAGE_PREFIX + day,)).fetchone()
        self.assertIsNotNone(notice, 'the existing budget notice')

    def test_three_failures_pause_the_check_and_wake_once(self):
        row = self.check_row()
        for count in range(1, cc.FAILURES_BEFORE_PAUSE + 1):
            self.queue.append(FakeProcess(result={'exit': 1, 'stderr': 'Traceback: boom'}))
            self.tick(15)
            self.assertEqual(len(self.woken(row['id'])), 0 if count < cc.FAILURES_BEFORE_PAUSE else 1)
        history = self.service.preparations_service.preparation_history(self.store.job(self.row(row['id'])['last_run_job_id']))
        self.assertIn('Traceback: boom', history[-1]['content'])
        self.settle(row['id'])
        self.assertEqual(self.row(row['id'])['state'], prep.STATE_PAUSED)
        self.assertFalse(self.tick(60))
        # The owner's resume runs it again, counting failures afresh.
        self.service.preparations.resume(row['id'])
        self.tick(15)
        status = cc.status_of(self.row(row['id']))
        self.assertEqual((status['failures'], status.get('paused'), status['last_result']), (0, None, cc.QUIET))
        self.assertEqual(len(self.woken(row['id'])), 1, 'one failure wake')

    def test_a_set_that_is_not_ready_pauses_at_once_without_a_wake(self):
        row = self.check_row()
        from personal_agent.api_requests import remove_slot
        remove_slot(self.store, 'example-broker')
        self.tick(0)
        current = self.row(row['id'])
        self.assertEqual(current['state'], prep.STATE_PAUSED)
        self.assertEqual(cc.status_of(current)['paused'], cc.PAUSED_NOT_READY)
        self.assertEqual(self.woken(row['id']), [])

    def test_a_forgotten_pinned_library_pauses_the_check_and_no_other_version_runs(self):
        library = ProcedureLibrary(self.store)
        library.propose('earlier', 'example-broker', 'def value():\n    """Value."""\n', [{'name': 'value', 'doc': 'Value.'}])
        library.settle('earlier', True)
        row = self.check_row()
        pinned = cc.spec_of(row)['libraries']['example-broker']
        library.propose('later', 'example-broker', 'def value():\n    """Other."""\n', [{'name': 'value', 'doc': 'Other.'}])
        library.settle('later', True)
        with self.store.db() as db:
            db.execute("UPDATE procedure_versions SET state='forgotten' WHERE id=?", (pinned,))
        self.tick(0)
        self.assertEqual(self.runs, [], 'nothing ran, the current version included')
        self.assertEqual(cc.status_of(self.row(row['id']))['paused'], cc.PAUSED_LIBRARY)
        self.service.preparations.resume(row['id'])
        self.tick(15)
        self.assertEqual(self.runs, [])
        self.assertEqual(self.row(row['id'])['state'], prep.STATE_PAUSED)

    def test_cancel_pause_line_stop_and_line_forget_stop_the_check(self):
        for stop in ('cancel', 'pause', 'line_stop', 'line_forget'):
            with self.subTest(stop):
                line = self.service.lines.open('w', task='keep watch', report_promise='when it moves')
                row = self.check_row(line_id=line['line_id'])
                if stop == 'cancel':
                    self.service.preparations.cancel(row['id'])
                elif stop == 'pause':
                    self.service.preparations.pause(row['id'])
                elif stop == 'line_stop':
                    self.service.preparations_service.stop_line(line['line_id'])
                else:
                    with self.store.db() as db:
                        db.execute("UPDATE procedure_versions SET state='forgotten' WHERE slot=?",
                                   (prep_line_slot(line['line_id']),))
                before = len(self.runs)
                self.tick(15)
                self.assertEqual(len(self.runs), before, 'no check run')
                self.assertNotEqual(self.row(row['id'])['state'], prep.STATE_SCHEDULED)
                self.service.preparations.cancel(row['id'])

    def test_a_result_whose_row_changed_during_the_run_is_dropped(self):
        row = self.check_row()
        process = FakeProcess(result={'stdout': 'found', 'stderr': ''})
        original = process._lines

        def pause_during_run():
            self.service.preparations.pause(row['id'])
            yield from original()
        process._lines = pause_during_run
        self.queue.append(process)
        self.tick(0)
        self.assertEqual(self.woken(row['id']), [])
        self.assertEqual(self.row(row['id'])['state'], prep.STATE_PAUSED)
        self.assertIsNone(cc.status_of(self.row(row['id'])).get('last_result'))

    def test_after_a_restart_an_overdue_check_runs_once(self):
        row = self.check_row()
        self.now += 3000
        restarted = cc.ConditionChecks(self.service, runner=self.runner)
        self.assertTrue(restarted.run_due())
        self.assertFalse(restarted.run_due())
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.row(row['id'])['check_next_at'], self.now + 15)

    def test_the_window_end_ends_the_check(self):
        row = self.check_row(until=60)
        self.tick(61)
        self.assertEqual(self.runs, [])
        self.assertEqual(self.row(row['id'])['state'], prep.STATE_EXPIRED)

    def test_check_state_persists_encrypted_between_runs_and_is_discarded_when_the_row_ends(self):
        row = self.check_row()
        written = FakeProcess(result={'stdout': '', 'stderr': ''})
        original = written._lines

        def write_state():
            path = written.config['env'][cc.STATE_ENV]
            with open(path, 'w') as handle:
                handle.write('{"below_since": 1}')
            yield from original()
        written._lines = write_state
        self.queue.append(written)
        self.tick(0)
        key = cc.state_key(row['id'])
        self.assertNotIn('below_since', self.store.secret(key), 'kept encrypted')
        read = FakeProcess(result={'stdout': '', 'stderr': ''})
        seen = []
        original_read = read._lines

        def read_state():
            seen.append(open(read.config['env'][cc.STATE_ENV]).read())
            yield from original_read()
        read._lines = read_state
        self.queue.append(read)
        self.tick(15)
        self.assertEqual(seen, ['{"below_since": 1}'])
        self.service.preparations.cancel(row['id'])
        self.checks.sweep_states()
        self.assertEqual(self.store.secret(key), '')

    def test_the_owner_view_shows_counts_hosts_and_code(self):
        row = self.check_row()
        self.queue.append(FakeProcess(requests=[{'method': 'GET', 'host': HOST, 'path': '/v1/quote', 'query': ''}],
                                      result={'stdout': '', 'stderr': ''}))
        self.tick(0)
        [entry] = [item for item in self.service.preparations_service.preparations_status()['preparations'] if item['id'] == row['id']]
        self.assertEqual(entry['check']['code'], CHECK_CODE)
        self.assertIn(HOST + ' 1', entry['check']['text']['en'])
        self.assertIn('today 1 checks, 0/24 wakes', entry['check']['text']['en'])
        with self.assertRaises(prep.PreparationRefusal):
            self.service.preparations.edit(row['id'], every_minutes=10)


def prep_line_slot(line_id):
    from personal_agent.entrusted_lines import slot
    return slot(line_id)


class Replacement(_Base):
    def wake(self, row):
        self.hit()
        self.tick(0)
        return self.store.job(self.row(row['id'])['last_run_job_id'])

    def replace(self, job, args):
        call = self.service.preparation_scheduler(job, None)
        return call({'kind': 'prepare', 'goal': 'x', 'due': iso(self.now), 'replaces': self.row(prep.preparation_of(job['request_key']))['id'],
                     'check': json.dumps(args)})

    def test_the_woken_run_replaces_its_own_check_within_its_sets_and_hosts(self):
        row = self.check_row()
        job = self.wake(row)
        args = self.check_args(code=CHECK_CODE + '# new level\n')
        self.code_run(job, args)
        result = self.replace(job, {**args, 'every_seconds': 30})
        self.assertTrue(result['check_replaced'])
        self.assertEqual(cc.spec_of(self.row(row['id']))['every_seconds'], 30)

    def test_a_replacement_that_adds_a_host_or_a_set_is_refused(self):
        api = __import__('personal_agent.api_requests', fromlist=['save_slot'])
        api.save_slot(self.store, 'second-set', ['api2.example.com'], {'APP_KEY': 'other-key-value-0003'}, note='',
                      money=False, encrypted=True, code={'fields': [{'name': 'APP_KEY', 'label': 'k', 'use': 'header_or_body'}],
                                                        'reads': []})
        row = self.check_row(secrets='second-set')
        job = self.wake(row)
        for changes in ({'hosts': 'public.example.org'}, {'secrets': 'second-set,example-broker'}):
            with self.subTest(changes):
                args = self.check_args(**{'secrets': 'second-set', **changes})
                self.code_run(job, args)
                with self.assertRaises(ToolError) as caught:
                    self.replace(job, args)
                self.assertEqual(caught.exception.code, 'check_widened')
        self.assertEqual(cc.spec_of(self.row(row['id'])), cc.spec_of(row))

    def test_a_fixed_check_resumes_after_its_failure_wake(self):
        row = self.check_row()
        for _ in range(cc.FAILURES_BEFORE_PAUSE):
            self.queue.append(FakeProcess(result={'exit': 1, 'stderr': 'boom'}))
            self.tick(15)
        job = self.store.job(self.row(row['id'])['last_run_job_id'])
        args = self.check_args(code=CHECK_CODE + '# fixed\n')
        self.code_run(job, args)
        self.replace(job, {**args, 'every_seconds': 15})
        self.settle(row['id'])
        self.assertEqual(self.row(row['id'])['state'], prep.STATE_SCHEDULED)


class OutsideContentParity(_Base):
    """Owner decision 2026-11-18 on #972: a woken run is never narrower than a run that ran the same code itself.

    The rule: the woken Work starts with exactly the mark ``record_outside_result`` gives a Work when
    the same ``code_run`` result reaches it (``result_outside_source``).  Code-run output is
    ``unattributed-tool-evidence`` today, so both start marked; were it ever an owner-only result,
    both would start unmarked.  Payment follows the row's rules, unchanged.
    """

    def ordinary_mark(self):
        job = self.store.job(self.store.enqueue('ordinary run', 'ordinary-1'))
        capabilities = type('Caps', (), {'tools': {'code_run': {'host_action': 'code_run'}}, 'store': self.store,
                                         'job_id': job['id']})()
        record_outside_result(lambda self, name, args: {'stdout': 'x'})(capabilities, 'code_run', self.check_args())
        return job, self.store.outside_content_sources(job['id'])

    def test_the_woken_work_starts_with_the_same_mark_as_the_same_codes_output(self):
        row = self.check_row()
        self.hit()
        self.tick(0)
        work = self.row(row['id'])['last_run_job_id']
        [woken] = self.store.outside_content_sources(work)
        _job, [ordinary] = self.ordinary_mark()
        self.assertEqual(woken['category'], ordinary['category'])
        self.assertEqual(woken['category'], agent_runtime.UNATTRIBUTED_PROVENANCE)
        self.assertEqual(woken['operation'], 'code_run')
        self.assertEqual({key: value for key, value in woken.items() if key != 'check'},
                         outside_source('code_run', 'code_run', {}))

    def test_if_code_output_marked_nothing_the_woken_work_would_start_unmarked(self):
        with mock.patch.object(agent_runtime, 'OWNER_ONLY_RESULT_ACTIONS',
                               agent_runtime.OWNER_ONLY_RESULT_ACTIONS | {'code_run'}):
            self.assertIsNone(result_outside_source('code_run'))
            _job, ordinary = self.ordinary_mark()
            self.assertEqual(ordinary, [])
            row = self.check_row()
            self.hit()
            self.tick(0)
            self.assertEqual(self.store.outside_content_sources(self.row(row['id'])['last_run_job_id']), [])


class PaymentParity(_Base):
    """A woken run of an owner-accepted row follows payment autonomy like any run of that row; no new refusal."""

    MONEY = True

    def switch(self, enabled):
        settings = self.service.settings_orchestrator
        draft = settings.propose('owner', 'http', 'payment_autonomy', 'enabled', 'on' if enabled else 'off')
        return settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])

    def pay(self, job):
        process = FakeProcess(requests=[{'method': 'POST', 'host': HOST, 'path': '/v1/orders', 'query': '',
                                         'bodyText': '{"qty":"1"}'}], result={'stdout': 'ordered', 'stderr': ''})
        runner = CodeRunner(self.store, job['id'], popen=lambda *a, **k: process, node='/usr/bin/node',
                            install=lambda store, node: store.root, approvals=self.service.browser_approvals_for(job))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
        runner.run({'code': 'print(1)', 'secrets': 'example-broker', 'approval_note': 'buy one'})
        return [item['allow'] for item in process.decisions]

    def woken_job(self, accepted_by):
        row = self.check_row(accepted_by=accepted_by)
        self.hit('crossed the level')
        self.tick(0)
        job = self.store.job(self.row(row['id'])['last_run_job_id'])
        self.assertTrue(self.store.outside_content_sources(job['id']))
        return row, job

    def ordinary_job(self, accepted_by):
        """A run of an ordinary owner-accepted row that already ran code_run (and so is marked by its output)."""
        row = self.service.preparations.create(kind='prepare', goal='pay', due_at=self.now - 1, timezone='UTC',
                                               recurrence='daily', channel='web', created_from=None,
                                               state=prep.STATE_SCHEDULED, accepted_by=accepted_by)
        job = self.store.job(self.service.preparations.start(row, channel='web', chat_id=None, now=self.now))
        self.store.mark_outside_content(job['id'], outside_source('code_run', 'code_run', {}))
        return job

    def test_owner_accepted_woken_and_ordinary_runs_pay_alike_under_the_mode(self):
        self.switch(True)
        _row, woken = self.woken_job(prep.ACCEPTED_OWNER_REQUEST)
        ordinary = self.ordinary_job(prep.ACCEPTED_OWNER_REQUEST)
        self.assertEqual(self.pay(ordinary), [True])
        self.assertEqual(self.pay(woken), [True], 'the woken run is not narrower than the ordinary one')
        with self.store.db() as db:
            receipts = db.execute("SELECT COUNT(*) FROM tool_events WHERE tool='payment_autonomy' AND job_id=?",
                                  (woken['id'],)).fetchone()[0]
        self.assertEqual(receipts, 1)

    def test_a_self_set_rows_woken_run_never_pays_as_today(self):
        self.switch(True)
        _row, woken = self.woken_job(prep.ACCEPTED_OWNER_WORK)
        ordinary = self.ordinary_job(prep.ACCEPTED_OWNER_WORK)
        self.assertEqual(self.pay(ordinary), [False])
        self.assertEqual(self.pay(woken), [False])


if __name__ == '__main__':
    unittest.main()
