"""#972: condition checks - the AI's own code watches, and wakes the AI only on a hit.

Evidence class: model-free unit and store-level tests.  The real QuickStore, ``Preparations``,
``AgentService`` (its schedule handler, history, Settings view, payment-autonomy gate) and
``CodeRunner`` request decisions run unchanged; only the sandbox process is a fake runner
(``test_code_run.FakeProcess``) and the clock is a fake clock.  No live model, sandbox,
network or account is contacted, and none is claimed. The stateful proving regression executes
its fixture code in a local Python subprocess behind the fake sandbox protocol. No service is
named: the fixtures are a synthetic API at example hosts.
"""
import json
import os
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from personal_agent import agent_runtime, condition_checks as cc, preparations as prep
from personal_agent.agent_runtime import ToolError, outside_source, record_outside_result, result_outside_source
from personal_agent.api_credentials import ApiCredentials
from personal_agent.api_requests import slots
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

    def executing_process(self, requests=()):
        process = FakeProcess(requests=requests)
        original = process._lines

        def execute():
            config = process.config
            completed = subprocess.run(config['argv'], cwd=config['workdir'], env=config['env'],
                                       capture_output=True, text=True, timeout=10)
            process._result = {'exit': completed.returncode, 'stdout': completed.stdout, 'stderr': completed.stderr}
            yield from original()

        process._lines = execute
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

    def test_stateful_code_proves_unchanged_and_starts_with_fresh_check_state(self):
        code = ('import json, os\n'
                'with open(os.environ["still_check_state_file"]) as handle:\n'
                '    state = json.load(handle)\n'
                'state["runs"] = state.get("runs", 0) + 1\n'
                'with open(os.environ["still_check_state_file"], "w") as handle:\n'
                '    json.dump(state, handle)\n'
                'print(state["runs"])\n')

        job = self.owner_work()
        args = self.check_args(code=code, secrets='')
        # Each proving run starts empty, even after the same code has written to the file.
        for _ in range(2):
            process = self.executing_process()
            result = self.code_run(job, args, process)
            self.assertEqual(result['exit_status'], 0, result['stderr'])
            self.assertEqual(result['stdout'].strip(), '1')
            self.assertIn('check_proof', result)
            self.assertNotIn('state_saved', result)
            self.assertFalse(Path(process.config['env'][cc.STATE_ENV]).exists())
        scheduled = self.schedule(job, args)
        self.assertTrue(scheduled['scheduled'])
        row = self.row(scheduled['preparation_id'])
        spec = cc.spec_of(row)
        self.assertEqual(spec['code'], code)
        credentials = ApiCredentials(self.store)
        self.assertFalse(credentials.read_bound_state(cc.state_key(row['id']), spec['digest']))
        # The actual check runs that exact code, but never inherits the proving run's state.
        self.queue.append(self.executing_process())
        self.tick(0)
        self.assertEqual(cc.status_of(self.row(row['id']))['last_result'], cc.HIT)
        self.assertEqual(json.loads(credentials.read_bound_state(cc.state_key(row['id']), spec['digest'])), {'runs': 1})

    def test_check_secret_set_keeps_its_state_separate_in_proving_and_check_runs(self):
        self.save(name='check', money=False)
        slot = slots(self.store)['check']
        credentials = ApiCredentials(self.store)
        token = 'cached-set-access-token-972'
        self.assertTrue(credentials.write_state('check', slot, json.dumps({'access_token': token, 'runs': 0})))
        code = ('import json, os\n'
                'set_path = os.environ["CHECK_STATE_FILE"]\n'
                'check_path = os.environ["still_check_state_file"]\n'
                'assert set_path != check_path\n'
                'with open(set_path) as handle:\n'
                '    set_state = json.load(handle)\n'
                'with open(check_path) as handle:\n'
                '    check_state = json.load(handle)\n'
                'set_state["runs"] += 1\n'
                'check_state["runs"] = check_state.get("runs", 0) + 1\n'
                'with open(set_path, "w") as handle:\n'
                '    json.dump(set_state, handle)\n'
                'with open(check_path, "w") as handle:\n'
                '    json.dump(check_state, handle)\n')
        job = self.owner_work()
        args = self.check_args(code=code, secrets='check')
        process = self.executing_process()
        result = self.code_run(job, args, process)
        self.assertEqual(result['exit_status'], 0, result['stderr'])
        self.assertEqual(result['state_saved'], {'check': True})
        self.assertIn('check_proof', result)
        self.assertNotEqual(process.config['env']['CHECK_STATE_FILE'],
                            process.config['env']['still_check_state_file'])
        self.assertEqual(json.loads(credentials.read_state('check', slot)), {'access_token': token, 'runs': 1})

        scheduled = self.schedule(job, {**args, 'every_seconds': 15})
        row = self.row(scheduled['preparation_id'])
        spec = cc.spec_of(row)
        self.assertFalse(credentials.read_bound_state(cc.state_key(row['id']), spec['digest']))
        for check_runs in (1, 2):
            process = self.executing_process()
            self.queue.append(process)
            self.tick(0 if check_runs == 1 else 15)
            self.assertEqual(cc.status_of(self.row(row['id']))['last_result'], cc.QUIET)
            self.assertNotEqual(process.config['env']['CHECK_STATE_FILE'],
                                process.config['env']['still_check_state_file'])
            self.assertEqual(json.loads(credentials.read_state('check', slot)),
                             {'access_token': token, 'runs': check_runs + 1})
            self.assertEqual(json.loads(credentials.read_bound_state(cc.state_key(row['id']), spec['digest'])),
                             {'runs': check_runs})

    def test_proving_state_secrets_are_redacted_from_output_and_request_paths_without_retention(self):
        host = 'api.example.com'
        token = 'freshly-obtained-"proving"-token-972'
        plain = 'owner-visible-description-00001'
        code = ('import json, os\n'
                f'token = {token!r}\n'
                f'state = {{"nested": [token], "plain": {{"description": {plain!r}}}}}\n'
                'with open(os.environ["still_check_state_file"], "w") as handle:\n'
                '    json.dump(state, handle)\n')
        known = ApiCredentials(self.store).state_values()
        for ending, field, status in [('print(token)\nprint(json.dumps(token))\nprint(state["plain"]["description"])\n',
                                       'stdout', 0), ('raise RuntimeError(token)\n', 'stderr', 1)]:
            with self.subTest(field=field):
                job = self.owner_work(field)
                process = self.executing_process(requests=[{'method': 'GET', 'host': host, 'path': '/v1/' + token}])
                result = self.code_run(job, self.check_args(code=code + ending, secrets='', hosts=host), process)
                self.assertEqual(result['exit_status'], status, result['stderr'])
                self.assertNotIn(token, result[field])
                self.assertNotIn(json.dumps(token)[1:-1], result[field])
                self.assertIn('[redacted]', result[field])
                if field == 'stdout':
                    self.assertIn(plain, result[field])
                    self.assertIn('check_proof', result)
                self.assertEqual(result['requests'][0]['path'], '/v1/[redacted]')
                self.assertEqual(result['sources'], [f'https://{host}/v1/[redacted]'])
                self.assertNotIn('state_saved', result)
                self.assertFalse(Path(process.config['env'][cc.STATE_ENV]).exists())
                self.assertEqual(ApiCredentials(self.store).state_values(), known)

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
        self.tick(15)
        [work] = self.woken(row['id'])
        self.assertEqual(self.checks.handoff(work)['output'], 'new event')
        self.assertEqual(seen, [json.loads(previous)], 'admission retries the saved hit without polling again')
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


class RecoveryAndRedaction(_Base):
    class Crash(BaseException):
        pass

    def crash_after_check_write(self):
        write = self.checks._write_state

        def crash(*args):
            write(*args)
            raise self.Crash()
        return mock.patch.object(self.checks, '_write_state', side_effect=crash)

    def restart(self):
        self.checks = cc.ConditionChecks(self.service, runner=self.runner)
        self.service.condition_checks = self.checks

    def test_hit_replays_after_secrets_write_and_database_rollback(self):
        row = self.check_row()
        token = 'pending-state-secret-000000000972'
        self.queue.append(self.state_process({'reported': True, 'token': token}, {'stdout': 'one event ' + token}))
        with self.crash_after_check_write(), self.assertRaises(self.Crash):
            self.tick(0)
        self.assertEqual(self.woken(row['id']), [])
        saved = ApiCredentials(self.store).read_bound_state(cc.state_key(row['id']), cc.spec_of(row)['digest'])
        self.assertTrue(json.loads(saved)['reported'], 'the external write really happened')
        self.assertNotIn(token, self.store.secret_path.read_text())
        self.assertNotIn(token, self.row(row['id'])['check_status'] or '')
        self.restart()
        with mock.patch.object(self.checks, 'runner', side_effect=AssertionError('must replay, not rerun code')):
            self.tick(15)
        [work] = self.woken(row['id'])
        self.assertEqual(self.checks.handoff(work)['output'], 'one event [redacted]')
        day = self.service.preparations.budget_day(self.now)
        self.assertEqual(self.store.config(prep.SCHEDULED_USAGE_PREFIX + day), 1)
        self.assertEqual(self.checks.wakes_today(self.row(row['id']), self.now), 1)
        # The committed marker also survives a restart before pending-envelope cleanup.
        self.settle(row['id'])
        self.restart()
        self.tick(15)
        self.assertEqual(len(self.woken(row['id'])), 1)
        self.checks.sweep_states()
        self.assertFalse(self.store.secret(cc.state_key(row['id']) + ':pending'))

    def test_quiet_and_failure_state_writes_replay_without_losing_run_accounting(self):
        for exit_status in (0, 1):
            with self.subTest(exit_status=exit_status):
                row = self.check_row()
                self.queue.append(self.state_process({'cursor': 2}, {'exit': exit_status, 'stderr': 'diagnostic'}))
                with self.crash_after_check_write(), self.assertRaises(self.Crash):
                    self.tick(0)
                self.assertNotIn('last_result', cc.status_of(self.row(row['id'])))
                self.restart()
                with mock.patch.object(self.checks, 'runner', side_effect=AssertionError('must replay')):
                    self.tick(15)
                status = cc.status_of(self.row(row['id']))
                self.assertEqual(status['failures'], exit_status)
                self.assertEqual(sum(day['runs'] for day in status['days'].values()), 1)
                self.service.preparations.cancel(row['id'])

    def set_state_process(self, result):
        process = FakeProcess(result=result)
        original = process._lines

        def write():
            Path(process.config['env']['EXAMPLE_BROKER_STATE_FILE']).write_text('{"reported": true}')
            yield from original()
        process._lines = write
        return process

    def test_set_state_does_not_advance_on_refused_or_discarded_hit(self):
        from personal_agent.api_requests import slots
        credentials = ApiCredentials(self.store)
        slot = slots(self.store)['example-broker']
        for reason in ('queue-full', 'paused'):
            with self.subTest(reason=reason):
                row = self.check_row()
                credentials.write_state('example-broker', slot, '{"reported": false}')
                process = self.set_state_process({'stdout': 'one event'})
                self.queue.append(process)
                if reason == 'queue-full':
                    with mock.patch.object(self.store, 'enqueue', side_effect=ValueError('queue full')):
                        self.tick(0)
                else:
                    original = process._lines

                    def pause():
                        self.service.preparations.pause(row['id'])
                        yield from original()
                    process._lines = pause
                    self.tick(0)
                self.assertEqual(json.loads(credentials.read_state('example-broker', slot)), {'reported': False})
                self.assertEqual(self.woken(row['id']), [])
                self.service.preparations.cancel(row['id'])

    def test_set_state_write_crash_retains_the_hit_for_replay(self):
        from personal_agent.api_requests import slots
        row = self.check_row()
        self.queue.append(self.set_state_process({'stdout': 'set cursor event'}))
        write = ApiCredentials.write_state

        def crash(credentials, *args):
            write(credentials, *args)
            raise self.Crash()
        with mock.patch.object(ApiCredentials, 'write_state', crash), self.assertRaises(self.Crash):
            self.tick(0)
        self.assertEqual(self.woken(row['id']), [])
        self.assertEqual(json.loads(ApiCredentials(self.store).read_state('example-broker', slots(self.store)['example-broker'])),
                         {'reported': True})
        self.restart()
        with mock.patch.object(self.checks, 'runner', side_effect=AssertionError('must replay')):
            self.tick(15)
        [work] = self.woken(row['id'])
        self.assertEqual(self.checks.handoff(work)['output'], 'set cursor event')

    def test_unsaved_set_state_is_redacted_from_proving_and_check_outputs(self):
        token = 'new-unsaved-set-state-token-000972'
        for scheduled in (False, True):
            with self.subTest(scheduled=scheduled):
                row = self.check_row() if scheduled else None
                process = self.state_process({}, {'stdout': token, 'stderr': token})
                original = process._lines

                def write():
                    Path(process.config['env']['EXAMPLE_BROKER_STATE_FILE']).write_text(json.dumps({'token': token}))
                    yield from original()
                process._lines = write
                with mock.patch.object(ApiCredentials, 'write_state', return_value=False):
                    if scheduled:
                        self.queue.append(process)
                        self.tick(0)
                        [work] = self.woken(row['id'])
                        result = self.checks.handoff(work)
                    else:
                        result = self.code_run(self.owner_work(), self.check_args(), process)
                self.assertNotIn(token, json.dumps(result))
                self.assertIn('[redacted]', json.dumps(result))

    def test_proving_error_redacts_state_before_directory_cleanup_and_diagnostic_truncation(self):
        from personal_agent.code_run import CodeRunError
        token = 'new-exception-check-token-0000000972'

        def fail(runner, *args, **kwargs):
            Path(kwargs['plain'][cc.STATE_ENV]).write_text(json.dumps({'token': token}))
            raise CodeRunError(token + ' ' + code_secrets.VALUES['APP_KEY'], 'code_run_failed', requires=token)
        with mock.patch.object(CodeRunner, '_session', fail):
            with self.assertRaises(CodeRunError) as caught:
                self.code_run(self.owner_work(), self.check_args())
        self.assertEqual(str(caught.exception), '[redacted] [redacted]')
        self.assertEqual(caught.exception.requires, '[redacted]')
        row = self.check_row()
        with mock.patch.object(CodeRunner, '_session', fail):
            for _ in range(cc.FAILURES_BEFORE_PAUSE):
                self.tick(15)
        [work] = self.woken(row['id'])
        self.assertEqual(self.checks.handoff(work)['diagnostic'], '[redacted] [redacted]')
        self.assertNotIn(token, self.row(row['id'])['check_status'] or '')

    def test_request_host_and_metadata_are_redacted_in_result_evidence_and_audit(self):
        token = 'new-state-host-token-000000972'
        host = token + '.example.org'
        job = self.owner_work()
        process = self.state_process({'token': token}, {'stdout': 'hit'})
        process._requests = [{'method': 'GET', 'host': host, 'path': '/value', 'query': ''}]
        result = self.code_run(job, self.check_args(secrets='', hosts=host), process)
        self.assertNotIn(token, json.dumps(result))
        self.assertNotIn(token, json.dumps(self.store.task_events(job['id'])))
        self.assertNotIn(token, json.dumps(self.service.work_information_use(job['id'])))
        self.assertIn('[redacted].example.org', json.dumps(result))

    def test_refused_recovery_keeps_pending_hit_and_counts_the_run_once(self):
        row = self.check_row()
        self.queue.append(self.state_process({'reported': True}, {'stdout': 'recover this event'}))
        with self.crash_after_check_write(), self.assertRaises(self.Crash):
            self.tick(0)
        self.restart()
        with mock.patch.object(self.store, 'enqueue', side_effect=ValueError('queue full')):
            self.tick(15)
        self.assertEqual(self.woken(row['id']), [])
        self.assertIsNotNone(self.checks._pending(self.row(row['id'])))
        self.restart()
        with mock.patch.object(self.checks, 'runner', side_effect=AssertionError('must replay')):
            self.tick(15)
        [work] = self.woken(row['id'])
        self.assertEqual(self.checks.handoff(work)['output'], 'recover this event')
        status = cc.status_of(self.row(row['id']))
        self.assertEqual(sum(day['runs'] for day in status['days'].values()), 1)
        self.assertEqual(sum(day['wakes'] for day in status['days'].values()), 1)

    def test_identical_replacement_resets_state_even_if_postcommit_discard_crashes(self):
        row = self.check_row()
        self.queue.append(self.state_process({'reported': True}, {'stdout': 'first event'}))
        self.tick(0)
        [work] = self.woken(row['id'])
        args = {**self.check_args(), 'every_seconds': 15}
        self.code_run(work, args)
        with mock.patch.object(ApiCredentials, 'discard_bound_state', side_effect=self.Crash), self.assertRaises(self.Crash):
            self.checks.replace_own(work, args)
        self.assertEqual(cc.spec_of(self.row(row['id']))['digest'], cc.spec_of(row)['digest'])
        self.settle(row['id'])
        self.restart()
        seen = []
        process = FakeProcess()
        original = process._lines

        def read():
            seen.append(json.loads(Path(process.config['env'][cc.STATE_ENV]).read_text()))
            yield from original()
        process._lines = read
        self.queue.append(process)
        self.tick(15)
        self.assertEqual(seen, [{}])

    def test_setup_error_does_not_repeat_a_secret_in_invalid_package_arguments(self):
        token = code_secrets.VALUES['APP_KEY']
        with self.assertRaises(prep.PreparationRefusal) as caught:
            cc.proven_check(self.store, self.owner_work()['id'], self.check_args(packages=token))
        self.assertNotIn(token, str(caught.exception))
        self.assertIn('[redacted]', str(caught.exception))

    def test_owner_view_redacts_known_check_state_and_stored_secrets_in_code(self):
        token = 'owner-view-check-state-token-0000972'
        row = self.check_row(code=CHECK_CODE + f'# {token} {code_secrets.VALUES["APP_KEY"]}')
        self.queue.append(self.state_process({'token': token}, {'stdout': ''}))
        self.tick(0)
        view = json.dumps(self.checks.view(self.row(row['id'])))
        self.assertNotIn(token, view)
        self.assertNotIn(code_secrets.VALUES['APP_KEY'], view)
        self.assertIn('[redacted]', view)

    def test_library_result_metadata_is_redacted_before_model_and_evidence(self):
        token = 'library-metadata-state-token-0000972'
        process = self.state_process({'token': token}, {'stdout': 'ok'})
        job = self.owner_work()
        with mock.patch.object(CodeRunner, '_keep_libraries', return_value={
                'example-broker': {'state': 'not_saved', 'reason': token, 'failed': [token]}}):
            result = self.code_run(job, self.check_args(), process)
        self.assertNotIn(token, json.dumps(result))
        self.assertNotIn(token, json.dumps(self.store.task_events(job['id'])))
        self.assertEqual(result['library']['example-broker']['reason'], '[redacted]')



class CheckSpecLifecycle(_Base):
    """#981 P1: full specifications survive every admission and lifecycle boundary."""

    schedule = Setup.schedule

    def variations(self, spec):
        changes = {'code': spec['code'] + '# changed\n', 'hosts': ['other.example.org'],
                   'secrets': [], 'timeout_seconds': 12, 'every_seconds': 45,
                   'libraries': {'example-broker': 'another-version'}, 'proven_in': 'another-work',
                   'digest': 'another-digest', 'future_field': {'nested': ['value']}}
        for field, value in changes.items():
            yield field, {**spec, field: value}

    def create(self, spec, **changes):
        return self.service.preparations.create(**{
            'kind': 'prepare', 'goal': 'same goal', 'due_at': self.now, 'timezone': 'UTC',
            'recurrence': None, 'channel': 'web', 'created_from': 'creator',
            'state': prep.STATE_PROPOSED, 'window': (15, self.now + 3600, 24),
            'check': spec, **changes})

    def test_same_work_and_cross_work_dedup_compare_every_spec_field(self):
        job, args = self.proven()
        spec = cc.proven_check(self.store, job['id'], args)
        for cross_work in (False, True):
            for field, changed in self.variations(spec):
                if field == 'proven_in':
                    continue  # Proof provenance does not change what executes.
                with self.subTest(cross_work=cross_work, field=field):
                    first = self.create(spec)
                    second = self.create(changed, created_from='other' if cross_work else 'creator')
                    self.assertNotEqual(first['id'], second['id'])
                    self.assertEqual(cc.spec_of(self.row(second['id'])), changed)
                    # Key order is not identity; an exact retry returns the changed row.
                    retry = self.create(dict(reversed(list(changed.items()))),
                                        created_from='other' if cross_work else 'creator')
                    self.assertEqual(retry['id'], second['id'])
                    for row in (first, second):
                        self.service.preparations.delete(row['id'])
        first = self.create(spec)
        retry = self.create(spec, created_from='other')
        self.assertEqual(first['id'], retry['id'])
        self.assertTrue(retry['already_proposed'])
        ordinary = self.create(None)
        self.assertNotEqual(first['id'], ordinary['id'])
        self.assertIsNone(cc.spec_of(ordinary))

    def test_owner_replacement_same_goal_and_due_keeps_new_spec_and_cancels_old(self):
        job, args = self.proven()
        first = self.schedule(job, args)
        changed = {**args, 'code': args['code'] + '# replacement\n', 'hosts': 'public.example.org', 'secrets': '',
                   'every_seconds': 45}
        self.code_run(job, changed)
        second = self.schedule(job, changed, replaces=first['preparation_id'])
        self.assertNotEqual(first['preparation_id'], second['preparation_id'])
        self.assertEqual(self.row(first['preparation_id'])['state'], prep.STATE_CANCELLED)
        saved = cc.spec_of(self.row(second['preparation_id']))
        self.assertEqual(saved, cc.proven_check(self.store, job['id'], changed))
        self.assertEqual(second['check']['check'], saved['digest'][:16])

    def test_reproving_same_arguments_uses_latest_successful_library_versions(self):
        library = ProcedureLibrary(self.store)
        job, args = self.proven()
        first = self.schedule(job, args)
        library.propose('later', 'example-broker', 'def value():\n    """Value."""\n',
                        [{'name': 'value', 'doc': 'Value.'}])
        library.settle('later', True)
        self.code_run(job, args)
        second = self.schedule(job, args)
        self.assertNotEqual(first['preparation_id'], second['preparation_id'])
        self.assertEqual(cc.spec_of(self.row(second['preparation_id']))['libraries'],
                         {'example-broker': library.current('example-broker')['id']})
        self.assertEqual(cc.spec_of(self.row(first['preparation_id']))['libraries'], {})

    def test_accept_preserves_exact_spec_and_runs_it(self):
        job, args = self.proven(hosts='public.example.org', secrets='')
        spec = cc.proven_check(self.store, job['id'], args)
        row = self.create(spec)
        self.service.preparation_request({'operation': 'accept', 'id': row['id']})
        self.assertEqual(cc.spec_of(self.row(row['id'])), spec)
        with mock.patch.object(self.checks, '_run', wraps=self.checks._run) as run:
            self.tick(0)
        self.assertEqual(run.call_args.args[1], spec)

    def test_replacement_interval_survives_pause_resume_and_restore(self):
        row = self.check_row()
        self.hit()
        self.tick(0)
        job = self.store.job(self.row(row['id'])['last_run_job_id'])
        args = self.check_args(code=CHECK_CODE + '# fixed\n', every_seconds=45)
        self.code_run(job, args)
        self.checks.replace_own(job, args)
        spec = cc.proven_check(self.store, job['id'], args)
        self.settle(row['id'])
        for stop, resume in (('pause', 'resume'), ('cancel', 'restore')):
            with self.subTest(stop=stop):
                self.service.preparation_request({'operation': stop, 'id': row['id']})
                self.assertEqual(cc.spec_of(self.row(row['id'])), spec)
                self.assertFalse(self.checks.run_due())
                self.service.preparation_request({'operation': resume, 'id': row['id']})
                current = self.row(row['id'])
                self.assertEqual(cc.spec_of(current), spec)
                self.assertEqual(current['every_seconds'], 45)
                self.assertEqual(current['due_at'], self.now + 45)
                self.assertEqual(current['check_next_at'], current['due_at'])
                self.assertFalse(self.checks.run_due())
                self.tick(45)
                self.assertEqual(self.row(row['id'])['check_next_at'], self.now + 45)

    def test_settings_bindings_compare_every_spec_field_for_each_operation(self):
        from personal_agent.settings_orchestrator import SettingsOrchestrator, PREPARATION_BINDINGS
        settings = SettingsOrchestrator(self.store, service=self.service)
        row = self.check_row()
        spec = cc.spec_of(row)
        for operation in PREPARATION_BINDINGS:
            value = json.dumps({'id': row['id']}) if operation == 'edit' else row['id']
            for field, changed in self.variations(spec):
                with self.subTest(operation=operation, field=field):
                    with self.store.db() as db:
                        db.execute('UPDATE preparations SET check_spec=? WHERE id=?', (row['check_spec'], row['id']))
                    before = settings._before('preparations', operation, value, {})
                    with self.store.db() as db:
                        db.execute('UPDATE preparations SET check_spec=? WHERE id=?', (json.dumps(changed, sort_keys=True), row['id']))
                    self.assertNotEqual(before, settings._before('preparations', operation, value, {}))

    def test_changed_spec_cannot_claim_pause_or_wake_from_stale_result(self):
        row = self.check_row()
        spec = cc.spec_of(row)
        for field, changed in self.variations(spec):
            with self.subTest(field=field):
                with self.store.db() as db:
                    db.execute('UPDATE preparations SET check_spec=? WHERE id=?', (json.dumps(changed, sort_keys=True), row['id']))
                self.assertIsNone(self.checks._claim(row, self.now))
                self.checks._pause(row, spec, cc.PAUSED_NOT_READY, self.now)
                self.checks._commit(row, spec, {'result': {'exit_status': 0, 'stdout': 'stale hit'}}, self.now)
                current = self.row(row['id'])
                self.assertEqual(current['state'], prep.STATE_SCHEDULED)
                self.assertEqual(cc.spec_of(current), changed)
                self.assertEqual(self.woken(row['id']), [])
                self.assertIsNone(current['check_status'])

    def test_stop_and_forget_during_check_drop_hit_and_preserve_spec(self):
        from personal_agent.owner_forget import OwnerForget
        for operation in ('cancel', 'pause', 'delete', 'line_stop', 'line_forget'):
            with self.subTest(operation=operation):
                line = self.service.lines.open('w', task='keep watch', report_promise='when it moves')
                row = self.check_row(line_id=line['line_id'])
                spec = cc.spec_of(row)
                self.assertIsNotNone(self.checks._claim(row, self.now))
                if operation == 'line_stop':
                    self.service.preparations_service.stop_line(line['line_id'])
                elif operation == 'line_forget':
                    receipt = OwnerForget(self.store).forget('local-owner', 'line:' + line['line_id'])
                    OwnerForget(self.store).undo('local-owner', receipt['receipt'])
                    self.assertEqual(self.row(row['id'])['state'], prep.STATE_CANCELLED)
                else:
                    self.service.preparation_request({'operation': operation, 'id': row['id']})
                self.checks._commit(row, spec, {'result': {'exit_status': 0, 'stdout': 'late hit'}}, self.now)
                self.assertEqual(self.woken(row['id']), [])
                current = self.row(row['id'])
                if current:
                    self.assertEqual(cc.spec_of(current), spec)
                    self.assertNotEqual(current['state'], prep.STATE_SCHEDULED)

    def test_run_now_preserves_full_check_spec(self):
        row = self.check_row()
        self.service.preparation_request({'operation': 'run_now', 'id': row['id']})
        self.assertEqual(cc.spec_of(self.row(row['id'])), cc.spec_of(row))
        self.assertEqual(len(self.woken(row['id'])), 1)

    def test_same_second_wakes_bind_full_spec_and_handoff_survives_continuation(self):
        row = self.check_row()
        spec = cc.spec_of(row)
        jobs = []
        for field, changed in [('original', spec), *self.variations(spec)]:
            with self.subTest(field=field):
                with self.store.db() as db:
                    db.execute('UPDATE preparations SET state=?,check_spec=? WHERE id=?',
                               (prep.STATE_SCHEDULED, json.dumps(changed, sort_keys=True), row['id']))
                current = self.row(row['id'])
                with self.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    first = self.service.preparations.wake(db, current, at=self.now, channel='web', chat_id=None,
                                                          now=self.now, handoff={'kind': cc.HIT, 'output': field})
                    again = self.service.preparations.wake(db, current, at=self.now, channel='web', chat_id=None,
                                                          now=self.now, handoff={'kind': cc.HIT, 'output': field})
                self.assertEqual(first, again)
                work = self.store.job(first[0])
                self.assertNotIn(work['id'], jobs)
                jobs.append(work['id'])
                self.assertEqual(prep.preparation_of(work['request_key']), row['id'])
                continued = {**work, 'request_key': work['request_key'] + '/loc:answer'}
                self.assertEqual(self.checks.handoff(continued)['output'], field)
                self.settle(row['id'])
        self.assertEqual(len(self.woken(row['id'])), len(jobs))

    def test_old_claim_and_failure_cannot_pause_a_resumed_revision(self):
        row = self.check_row()
        self.service.preparations.pause(row['id'])
        self.service.preparations.resume(row['id'])
        self.now += 15
        self.assertIsNone(self.checks._claim(row, self.now))
        self.checks._pause(row, cc.spec_of(row), cc.PAUSED_NOT_READY, self.now)
        self.assertEqual(self.row(row['id'])['state'], prep.STATE_SCHEDULED)
        self.assertIsNone(self.row(row['id'])['check_status'])

    def test_forgetting_running_check_then_undo_does_not_restart_it(self):
        from personal_agent.owner_forget import OwnerForget
        line = self.service.lines.open('w', task='keep watch', report_promise='when it moves')
        row = self.check_row(line_id=line['line_id'])
        self.hit()
        self.tick(0)
        forget = OwnerForget(self.store)
        receipt = forget.forget('local-owner', 'line:' + line['line_id'])
        forget.undo('local-owner', receipt['receipt'])
        self.settle(row['id'])
        self.assertEqual(self.row(row['id'])['state'], prep.STATE_CANCELLED)
        self.assertEqual(cc.spec_of(self.row(row['id'])), cc.spec_of(row))
        self.assertFalse(self.checks.run_due())

    def test_check_schedule_edit_preview_is_refused_without_changing_spec(self):
        row = self.check_row()
        with self.assertRaises(prep.PreparationRefusal) as caught:
            self.service.preparations.preview_edit(row, {'max_runs': 2}, self.now)
        self.assertEqual(caught.exception.code, 'invalid_edit')
        self.assertEqual(cc.spec_of(self.row(row['id'])), cc.spec_of(row))

    def test_run_now_after_spec_replacement_does_not_reuse_same_second_work(self):
        row = self.check_row()
        first = self.service.preparations.run_now(row['id'], channel='web', chat_id=None)
        self.settle(row['id'])
        changed = {**cc.spec_of(row), 'libraries': {'example-broker': 'new-version'}}
        with self.store.db() as db:
            db.execute('UPDATE preparations SET check_spec=? WHERE id=?', (json.dumps(changed, sort_keys=True), row['id']))
        second = self.service.preparations.run_now(row['id'], channel='web', chat_id=None)
        self.assertNotEqual(first, second)
        self.assertEqual(cc.spec_of(self.row(row['id'])), changed)

    def test_line_stop_between_claim_and_cancel_cannot_wake(self):
        from personal_agent import entrusted_lines as el
        line = self.service.lines.open('w', task='keep watch', report_promise='when it moves')
        row = self.check_row(line_id=line['line_id'])
        self.assertIsNotNone(self.checks._claim(row, self.now))
        self.service.lines.write(line['line_id'], None, None, 'stopped', lambda brief: {**brief, 'state': el.STATE_STOPPED})
        self.checks._commit(row, cc.spec_of(row), {'result': {'exit_status': 0, 'stdout': 'late hit'}}, self.now)
        self.assertEqual(self.woken(row['id']), [])
        self.assertIsNone(self.row(row['id'])['check_status'])

    def test_replacement_refuses_a_spec_or_revision_changed_during_proof_lookup(self):
        row = self.check_row()
        self.hit()
        self.tick(0)
        job = self.store.job(self.row(row['id'])['last_run_job_id'])
        args = self.check_args(code=CHECK_CODE + '# new\n')
        self.code_run(job, args)
        proven_check = cc.proven_check
        for field, changed in self.variations(cc.spec_of(row)):
            with self.subTest(field=field):
                def change_while_proving(*a, **kw):
                    with self.store.db() as db:
                        db.execute('UPDATE preparations SET check_spec=? WHERE id=?', (json.dumps(changed, sort_keys=True), row['id']))
                    return proven_check(*a, **kw)
                with self.store.db() as db:
                    db.execute('UPDATE preparations SET check_spec=? WHERE id=?', (row['check_spec'], row['id']))
                with mock.patch.object(cc, 'proven_check', side_effect=change_while_proving):
                    with self.assertRaises(prep.PreparationRefusal) as caught:
                        self.checks.replace_own(job, args)
                self.assertEqual(caught.exception.code, 'check_not_own')
                self.assertEqual(cc.spec_of(self.row(row['id'])), changed)

    def test_own_pause_undo_preserves_spec_and_resumes_check_at_returned_time(self):
        row = self.check_row(every=45)
        self.hit()
        self.tick(0)
        job = self.store.job(self.row(row['id'])['last_run_job_id'])
        self.service.preparations.change_own(row['id'], job['id'], 'pause')
        self.settle(row['id'])
        self.assertEqual(self.row(row['id'])['state'], prep.STATE_PAUSED)
        current = self.service.preparations.resume(row['id'])
        self.assertEqual(cc.spec_of(current), cc.spec_of(row))
        self.assertEqual(current['check_next_at'], current['due_at'])
        self.assertFalse(self.checks.run_due())
        self.tick(45)
        self.assertEqual(len(self.runs), 2)

    def test_check_slot_time_is_read_without_losing_spec_bound_handoff(self):
        row = self.check_row()
        self.hit()
        self.tick(30)
        [job] = self.woken(row['id'])
        self.assertEqual(self.service.preparations_service.account_period_start(row), self.now)
        self.assertEqual(self.service.preparations_service.account_period_start(row, exclude=job['request_key']),
                         row['created_at'])
        self.assertEqual(self.checks.handoff(job)['output'], 'level held 180 s')


if __name__ == '__main__':
    unittest.main()


class ReviewRegressions(_Base):
    def test_state_symlinks_never_read_or_persist_private_host_json(self):
        private = self.store.root / 'private.json'
        token = 'private-host-token-never-read-000972'
        private.write_text(json.dumps({'token': token}))
        credentials = ApiCredentials(self.store)
        slot = slots(self.store)['example-broker']
        credentials.write_state('example-broker', slot, '{"cursor": 7}')
        row = self.check_row()
        spec = cc.spec_of(row)
        for proving in (True, False):
            with self.subTest(proving=proving):
                process = FakeProcess(result={'stdout': token})
                original = process._lines

                def replace_files():
                    for name in (cc.STATE_ENV, 'EXAMPLE_BROKER_STATE_FILE'):
                        path = Path(process.config['env'][name])
                        path.unlink()
                        path.symlink_to(private)
                    yield from original()

                process._lines = replace_files
                if proving:
                    result = self.code_run(self.owner_work(), self.check_args(), process)
                    # The collector must not even learn a value from a host file.
                    self.assertEqual(result['stdout'], token)
                else:
                    self.queue.append(process)
                    self.tick(0)
                    self.assertFalse(credentials.read_bound_state(cc.state_key(row['id']), spec['digest']))
                self.assertEqual(json.loads(credentials.read_state('example-broker', slot)), {'cursor': 7})
                self.assertNotIn(token, credentials.state_values())

    def test_state_initialization_refuses_unsafe_existing_files_without_overwriting(self):
        from personal_agent.api_credentials import MAX_STATE_BYTES
        private = self.store.root / 'private.json'
        private.write_text('{"private": true}')
        for runner_type in (CodeRunner, cc.CheckRunner):
            for filename in ('check-state.json', 'state-example-broker.json'):
                for kind in ('symlink', 'directory', 'oversized', 'fifo'):
                    with self.subTest(runner=runner_type, filename=filename, kind=kind):
                        private.write_text('{"private": true}')
                        directory = self.store.root / f'init-{runner_type.__name__}-{filename}-{kind}'
                        directory.mkdir()
                        path = directory / filename
                        if kind == 'symlink':
                            path.symlink_to(private)
                        elif kind == 'directory':
                            path.mkdir()
                        elif kind == 'fifo':
                            os.mkfifo(path)
                            # Keep a reader open so unsafe writers cannot hang this test.
                            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                        else:
                            path.write_bytes(b'x' * (MAX_STATE_BYTES + 1))
                        runner = runner_type(self.store, 'fixture', **({'pinned': {}} if runner_type is cc.CheckRunner else {}))
                        try:
                            with self.assertRaises((OSError, ValueError)):
                                runner._load_states(['example-broker'], directory, {})
                        finally:
                            if kind == 'fifo':
                                os.close(fd)
                        self.assertEqual(private.read_text(), '{"private": true}')
                        if kind == 'oversized':
                            self.assertEqual(path.stat().st_size, MAX_STATE_BYTES + 1)

    def test_oversized_state_is_not_collected_or_staged(self):
        from personal_agent.api_credentials import MAX_STATE_BYTES
        token = 'oversized-state-token-0000000972'
        process = self.state_process({'token': token, 'padding': 'x' * MAX_STATE_BYTES}, {'stdout': token})
        runner = cc.CheckRunner(self.store, 'fixture', pinned={}, popen=lambda *a, **k: process,
                                node='/usr/bin/node', install=lambda store, node: store.root)
        result = runner.run(self.check_args())
        self.assertIsNone(runner.state_out)
        self.assertEqual(result['stdout'], token)

    def test_state_reads_reject_special_files_and_bound_bytes(self):
        from personal_agent.api_credentials import MAX_STATE_BYTES
        from personal_agent.code_run import _state_file_text
        path = self.store.root / 'state-read.json'
        for kind in ('directory', 'fifo', 'oversized'):
            with self.subTest(kind=kind):
                if kind == 'directory':
                    path.mkdir()
                elif kind == 'fifo':
                    os.mkfifo(path)
                else:
                    path.write_bytes(b'x' * (MAX_STATE_BYTES + 1))
                try:
                    with self.assertRaises((OSError, ValueError)):
                        _state_file_text(path)
                finally:
                    path.rmdir() if kind == 'directory' else path.unlink()
        text = json.dumps({'token': 'x' * (MAX_STATE_BYTES - len('{"token": ""}'))})
        _state_file_text(path, text)
        self.assertEqual(_state_file_text(path), text)

    def test_state_open_rejects_symlink_swapped_in_at_open(self):
        from personal_agent.code_run import _state_file_text
        path, private = self.store.root / 'swap.json', self.store.root / 'private.json'
        private.write_text('{"private": true}')
        original_open = os.open
        for text in (None, '{}'):
            with self.subTest(writing=text is not None):
                if path.is_symlink():
                    path.unlink()
                path.write_text('{}')

                def swap_at_open(target, *args, **kwargs):
                    path.unlink()
                    path.symlink_to(private)
                    return original_open(target, *args, **kwargs)

                with mock.patch('personal_agent.code_run.os.open', side_effect=swap_at_open):
                    with self.assertRaises(OSError):
                        _state_file_text(path, text)
                self.assertEqual(private.read_text(), '{"private": true}')

    def test_new_state_secret_crossing_request_cap_is_redacted_before_truncation(self):
        token = 'newly-written-state-token-000000000972'
        for proving in (True, False):
            with self.subTest(proving=proving):
                process = self.state_process({'token': token}, {'stdout': 'hit'})
                process._requests = [{'method': 'GET', 'host': HOST, 'path': '/' + 'a' * 279 + token}]
                if proving:
                    job = self.owner_work()
                    result = self.code_run(job, self.check_args(), process)
                    evidence = self.store.task_events(job['id'])
                else:
                    row = self.check_row()
                    self.queue.append(process)
                    self.tick(0)
                    result = self.checks._pending(self.row(row['id']))
                    # The settled envelope is encrypted, but still must contain redacted diagnostics.
                    raw = self.store.secret(cc.state_key(row['id']) + ':pending')
                    evidence = json.loads(ApiCredentials(self.store)._cipher().decrypt(raw.encode()))['outcome']['result']
                self.assertNotIn(token[:20], json.dumps([result, evidence]))
                self.assertIn('[redacted]', json.dumps(evidence))

    def test_fresh_unadmitted_hit_survives_restart_and_condition_clearing(self):
        row = self.check_row()
        self.queue.append(self.state_process({'reported': True}, {'stdout': 'transient event'}))
        with mock.patch.object(self.store, 'enqueue', side_effect=ValueError('queue full')):
            self.tick(0)
        self.assertIsNotNone(self.checks._pending(self.row(row['id'])))
        self.checks = cc.ConditionChecks(self.service, runner=self.runner)
        # The next sandbox run would be quiet. Admission must replay the saved hit instead.
        with mock.patch.object(self.checks, 'runner', side_effect=AssertionError('must replay')):
            self.tick(15)
        [work] = self.woken(row['id'])
        self.assertEqual(self.checks.handoff(work)['output'], 'transient event')
        status = cc.status_of(self.row(row['id']))
        self.assertEqual(sum(day['runs'] for day in status['days'].values()), 1)
        self.assertEqual(sum(day['wakes'] for day in status['days'].values()), 1)

    def test_independently_proven_identical_checks_reuse_pending_proposal(self):
        first_job, args = self.proven()
        second_job, _ = self.proven()
        first_spec = cc.proven_check(self.store, first_job['id'], args)
        second_spec = cc.proven_check(self.store, second_job['id'], args)
        self.assertNotEqual(first_spec['proven_in'], second_spec['proven_in'])
        create = CheckSpecLifecycle.create
        first = create(self, first_spec, created_from=first_job['id'])
        second = create(self, second_spec, created_from=second_job['id'])
        self.assertEqual(first['id'], second['id'])
        self.assertTrue(second['already_proposed'])
        self.assertEqual(cc.spec_of(second), first_spec)

    def assert_api_audit(self, work_id, hosts):
        audit = self.service.work_information_use(work_id)
        used = {item['category']: item['items'] for item in audit['used']}
        self.assertEqual(used.get('api'), ['example-broker'])
        connections = audit['sent_to']['connections']
        self.assertEqual(sorted(item['destination'] for item in connections), sorted(hosts))
        self.assertTrue(all(item['state'] == 'reached' for item in connections))

    def test_failure_wake_carries_api_provenance(self):
        row = self.check_row()
        for _ in range(cc.FAILURES_BEFORE_PAUSE):
            self.queue.append(FakeProcess(requests=[{'method': 'GET', 'host': HOST}], result={'exit': 1}))
            self.tick()
        [work] = self.woken(row['id'])
        self.assert_api_audit(work['id'], [HOST])
        self.assertEqual(self.checks.handoff(work)['reached'], [HOST])
        self.assertEqual(self.checks.handoff(work)['secrets'], ['example-broker'])

    def test_each_location_continuation_audits_the_consumed_handoff_once(self):
        row = self.check_row()
        self.queue.append(FakeProcess(requests=[{'method': 'GET', 'host': HOST}], result={'stdout': 'hit'}))
        self.tick()
        [work] = self.woken(row['id'])
        for suffix in ('', '/loc:first', '/loc:first/loc:second'):
            continued = work if not suffix else self.store.job(self.store.enqueue(
                'location answer', work['request_key'] + suffix))
            self.assertEqual(self.checks.handoff(continued)['output'], 'hit')
            self.assert_api_audit(continued['id'], [HOST])
        unrelated = self.owner_work('unrelated')
        self.assertEqual(self.service.work_information_use(unrelated['id'])['sent_to'].get('connections', []), [])

    def test_reached_hosts_survive_both_diagnostic_caps(self):
        late = 'late.example.org'
        self.save(hosts=[HOST, late], money=False)
        requests = ([{'method': 'GET', 'host': HOST}] * 201
                    + [{'method': 'GET', 'host': late}] * 2
                    + [{'method': 'GET', 'host': 'refused.example.org'}])
        row = self.check_row()
        self.queue.append(FakeProcess(requests=requests, result={'stdout': 'hit'}))
        self.tick()
        [work] = self.woken(row['id'])
        self.assertEqual(cc.status_of(self.row(row['id']))['hosts_today'], {HOST: 201, late: 2})
        self.assertEqual(self.checks.handoff(work)['reached'], sorted([HOST, late]))
        self.assert_api_audit(work['id'], [HOST, late])

    def test_ordinary_code_run_audit_survives_evidence_diagnostic_cap(self):
        late = 'late.example.org'
        self.save(hosts=[HOST, late], money=False)
        requests = ([{'method': 'GET', 'host': HOST}] * 201
                    + [{'method': 'GET', 'host': late}]
                    + [{'method': 'GET', 'host': 'refused.example.org'}])
        ordinary = self.owner_work()
        result = self.code_run(ordinary, self.check_args(), FakeProcess(requests=requests))
        self.assertEqual(len(result['requests']), 200)
        evidence = self.store.task_events(ordinary['id'])[-1]['trace']['evidence']
        self.assertEqual(len(evidence['requests']), 50)
        self.assert_api_audit(ordinary['id'], [HOST, late])

    def test_runner_exceptions_keep_redacted_request_provenance(self):
        for failure in ('channel-lost', 'deadline', 'unexpected'):
            with self.subTest(failure=failure):
                row = self.check_row()
                for _ in range(cc.FAILURES_BEFORE_PAUSE):
                    process = FakeProcess(requests=[
                        {'method': 'GET', 'host': HOST, 'path': '/' + code_secrets.VALUES['APP_KEY']},
                        {'method': 'GET', 'host': 'refused.example.org'}], silent=True)
                    original_runner = self.runner

                    def failing_runner(*args, **kwargs):
                        runner = original_runner(*args, **kwargs)
                        if failure == 'deadline':
                            runner.clock = lambda: 1000 if len(process.decisions) == 2 else 0
                        elif failure == 'unexpected':
                            flush = process.flush

                            def fail_after_requests():
                                if len(process.decisions) == 2:
                                    raise RuntimeError(code_secrets.VALUES['APP_KEY'])
                                flush()
                            process.flush = fail_after_requests
                        return runner

                    self.queue.append(process)
                    with mock.patch.object(self.checks, 'runner', side_effect=failing_runner):
                        self.tick()
                [work] = self.woken(row['id'])
                status = cc.status_of(self.row(row['id']))
                self.assertEqual(status['hosts_today'], {HOST: 3})
                self.assertEqual(status['last_error']['code'],
                                 'code_run_timeout' if failure == 'deadline' else 'code_run_failed')
                self.assert_api_audit(work['id'], [HOST])
                self.assertNotIn(code_secrets.VALUES['APP_KEY'], json.dumps(self.checks.handoff(work)))
                self.settle(row['id'])

    def test_check_expiry_at_deadline_queues_one_durable_notice(self):
        self.assert_expiry_notice('deadline')

    def test_check_expiry_after_deadline_queues_one_durable_notice(self):
        self.assert_expiry_notice('after-deadline')

    def test_exhausted_check_queues_one_durable_notice(self):
        self.assert_expiry_notice('run-limit')

    def assert_expiry_notice(self, reason):
        self.service.preparations.notice_destination = lambda channel: ('owner', 'pairing', 'telegram')
        row = self.check_row()
        with self.store.db() as db:
            db.execute("UPDATE preparations SET channel='telegram' WHERE id=?", (row['id'],))
        row = self.row(row['id'])
        if reason != 'run-limit':
            self.now = row['until_at'] + (1 if reason == 'after-deadline' else 0)
        else:
            with self.store.db() as db:
                db.execute('UPDATE preparations SET run_count=max_runs WHERE id=?', (row['id'],))
        self.tick(0)
        self.assertEqual(self.row(row['id'])['state'], prep.STATE_EXPIRED)
        notice = self.store.next_notification('telegram')
        self.assertIsNotNone(notice)
        self.assertEqual(notice['kind'], prep.EXPIRED_NOTICE_KIND)
        self.assertEqual(notice['fingerprint'], row['id'])
        from personal_agent.messages import msg
        self.assertEqual(self.service.preparations_service.status_notice_text(notice),
                         msg('preparations.notice_elapsed', title=prep.owner_title(row)))
        self.assertEqual(self.woken(row['id']), [])
        # A restarted runner and a stale claim must not queue the notice again.
        restarted = cc.ConditionChecks(self.service)
        restarted.run_due(self.now)
        restarted._claim(row, self.now)
        with self.store.db() as db:
            notices = db.execute('SELECT * FROM telegram_notifications WHERE fingerprint=?',
                                 (row['id'],)).fetchall()
        self.assertEqual(len(notices), 1)
        with self.store.db() as db:
            db.execute("UPDATE telegram_notifications SET state='sent' WHERE fingerprint=?", (row['id'],))
