"""#972 slice 1. Real SQLite and scheduler, injected sandbox and model transports."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from personal_agent import agent_runtime as runtime, code_run, condition_checks as checks, preparations as prep
from personal_agent.api_credentials import ApiCredentials
from personal_agent.api_requests import save_slot, slots
from personal_agent.browser_jar import MemoryKey
from personal_agent.information_use import work_information_use
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from test_code_run import FakeProcess
from test_preparations import START, engine

HOST = 'api.example.com'
CODE = 'print(1)'
ARGS = {'code': CODE, 'hosts': [HOST], 'secrets': [], 'timeout_seconds': 90}
SECRET = 'stored-masked-test-credential_97'
WIRE = {'node': '/fixture/node', 'install': lambda store, node: store.root}


@pytest.fixture
def case(tmp_path):
    def offline(*args, **kwargs):
        pytest.fail('unexpected external transport')

    store = QuickStore(tmp_path)
    service = AgentService(store, ModelAdapter(offline), offline)
    service.use_decision_engine(engine())
    clock = [START]
    service.preparations.clock = lambda: clock[0]
    owner = store.enqueue('Run on a condition', 'owner', owner_typed=True)
    processes = []

    def runner(store, record_id, **kwargs):
        return checks.CheckRunner(store, record_id, popen=lambda *a, **k: processes.pop(0), **WIRE, **kwargs)

    service.condition_checks.runner = runner
    yield SimpleNamespace(service=service, store=store, clock=clock, owner=owner, processes=processes,
                          preps=service.preparations, checks=service.condition_checks)
    service.stop.set()
    service.shutdown_work_lanes()


def record(c, action, result, work=None):
    with c.store.db() as db:
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (work or c.owner, action, 'succeeded', json.dumps({
                       'host_action': action, 'evidence': runtime.evidence_summary(action, result)}), c.clock[0]))


def prove(c, args=ARGS, process=None, **kwargs):
    run = code_run.CodeRunner(c.store, c.owner, popen=lambda *a, **k: process or FakeProcess(), **WIRE, **kwargs)
    result = run.run(args)
    record(c, 'code_run', result)
    return result


def schedule(c, args=ARGS, *, every=15, until=3600, goal='Report the observation', max_runs=None, line=None, replaces=None):
    value = dict(args)
    if every is not None:
        value['every_seconds'] = every
    result = c.service.preparation_scheduler(c.store.job(c.owner), '')({
        'kind': 'prepare', 'goal': goal, 'due': datetime.fromtimestamp(c.clock[0], timezone.utc).isoformat(),
        'until': datetime.fromtimestamp(c.clock[0] + until, timezone.utc).isoformat(), 'timezone': 'UTC', 'check': json.dumps(value),
        **({'max_runs': max_runs} if max_runs is not None else {}),
        **({'line': line} if line is not None else {}),
        **({'replaces': replaces} if replaces is not None else {}),
    })
    record(c, 'schedule_preparation', result)
    return c.preps.get(result['preparation_id'])


def prepared(c, **kwargs):
    prove(c)
    return schedule(c, **kwargs)


def tick(c, result=None, requests=(), error=None):
    process = FakeProcess(requests=requests, result=result, error=error)
    c.processes.append(process)
    assert c.checks.run_due()
    return process


def current(c, row):
    return c.preps.get(row['id'])


def wake_job(c, row):
    return c.store.job(current(c, row)['last_run_job_id'])


def finish(c, row):
    row = current(c, row)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded',response='Done' WHERE id=?", (row['last_run_job_id'],))
    return c.preps.settle(row, c.clock[0])


def status(c, row):
    return json.loads(current(c, row)['check_status'])


def test_redaction_on_handoff_error_notice_owner_view_and_setup_evidence(case, monkeypatch, caplog):
    c = case
    c.store.secret('telegram_token', SECRET)
    literal = dict(ARGS, code=f'print({SECRET!r})')
    prove(c, literal)
    with pytest.raises(runtime.ToolError) as exc:
        schedule(c, literal)
    assert exc.value.code == 'check_credential'
    assert SECRET not in str(exc.value)
    assert SECRET not in json.dumps(runtime.recorded_arguments(
        'schedule_preparation', {'check': json.dumps(literal)}, redact=str))
    assert not c.preps.rows()

    row = prepared(c)
    tick(c, {'stdout': f'data {SECRET}'}, [{'host': HOST, 'method': 'GET', 'path': '/' + SECRET}])
    hit = wake_job(c, row)
    assert 'data [redacted]' in c.service.preparation_history(hit)[-1]['content']
    assert SECRET not in json.dumps(c.store.task_events(hit['id']))
    assert SECRET not in json.dumps(work_information_use(c.store, hit['id']))
    assert SECRET not in c.service.scheduled_notice_text([row['id']])
    finish(c, row)

    class ErrorRunner(checks.CheckRunner):
        def run(self, args, **kwargs):
            self.started, self.allowed_hosts = True, set()
            raise code_run.CodeRunError(f'diagnostic {SECRET}', 'code_run_failed')

    monkeypatch.setattr(c.checks, 'runner', ErrorRunner)
    for _ in range(3):
        c.clock[0] += 15
        assert c.checks.run_due()
    failed = wake_job(c, row)
    assert 'diagnostic [redacted]' in c.service.preparation_history(failed)[-1]['content']
    assert SECRET not in json.dumps(c.store.task_events(failed['id']))
    assert SECRET not in caplog.text

    # Even if a literal only becomes a stored credential after setup, the owner view redacts it.
    later = 'rotated-secret-value-972'
    code = f'print({later!r})'
    prove(c, {**ARGS, 'code': code})
    later_row = schedule(c, {**ARGS, 'code': code})
    c.store.secret('telegram_token', later)
    view = next(item['check'] for item in c.service.preparations_status()['preparations'] if item['id'] == later_row['id'])
    assert view['code'] == "print('[redacted]')"
    assert later not in json.dumps(c.store.task_events(c.owner))
    assert c.checks.run_due()
    assert current(c, later_row)['state'] == prep.STATE_PAUSED
    scrubbed = current(c, later_row)['check_spec']
    assert later not in scrubbed
    assert json.loads(scrubbed)['arguments']['code'] == view['code']
    c.preps.resume(later_row['id'])
    c.clock[0] += 15
    monkeypatch.setattr(c.checks, 'runner', lambda *a, **k: pytest.fail('scrubbed check ran'))
    assert c.checks.run_due()
    assert status(c, later_row)['paused'] == 'credential'
    with c.store.db() as db:
        assert later not in str([item['content'] for item in db.execute('SELECT content FROM messages')])


@pytest.mark.parametrize(('stream', 'exit_code'), [('stdout', 0), ('stderr', 1)])
def test_truncated_output_cannot_expose_a_credential_fragment(case, stream, exit_code):
    c = case
    c.store.secret('telegram_token', SECRET)
    row = prepared(c)
    fragment = SECRET[:-1]
    prefix = 'x' * (code_run.OUTPUT_BYTES - len(fragment))
    for _ in range(3 if exit_code else 1):
        tick(c, {'exit': exit_code, stream: prefix + fragment, 'truncated': [stream]})
        c.clock[0] += 15
    job = wake_job(c, row)
    event = checks.handoff_event(c.store, job)
    assert '[output exceeded capture limit]' in event['trace']['output']
    assert fragment not in json.dumps(event)
    history = c.service.preparation_history(job)[-1]['content']
    assert '[output exceeded capture limit]' in history
    assert fragment not in history


def test_scratch_state_values_are_redacted_without_saving_state(case):
    c = case
    args = dict(ARGS, secrets=['fixture'])
    c.store.api_slot_key = MemoryKey()
    save_slot(c.store, 'fixture', [HOST], {'TOKEN': SECRET}, encrypted=True, code={'fields': ['TOKEN']})
    prove(c, args)
    row = schedule(c, args)
    slot = slots(c.store)['fixture']
    credentials = ApiCredentials(c.store)
    original = '{"plain":{"cursor":"previous"}}'
    assert credentials.write_state('fixture', slot, original)
    fresh = 'fresh-opaque-test-token-from-state'

    class ScratchProcess(FakeProcess):
        def _lines(self):
            path = Path(self.config['env']['FIXTURE_STATE_FILE'])
            assert path.read_text() == original
            path.write_text(json.dumps({'auth': {'token': fresh}, 'plain': {'cursor': 'new cursor'}}))
            yield from super()._lines()

    c.processes.append(ScratchProcess(result={'stdout': f'observed {fresh}; new cursor'}))
    assert c.checks.run_due()
    job = wake_job(c, row)
    assert checks.handoff_event(c.store, job)['trace']['output'] == 'observed [redacted]; new cursor'
    assert 'observed [redacted]; new cursor' in c.service.preparation_history(job)[-1]['content']
    assert credentials.read_state('fixture', slot) == original


def test_unavailable_credential_key_masks_only_the_check_view(case):
    c = case
    row = prepared(c)
    regular = c.preps.create(kind='prepare', goal='Ordinary preparation', due_at=c.clock[0],
        timezone='UTC', recurrence=None, channel='web', created_from=c.owner, state=prep.STATE_SCHEDULED)
    key = c.store.api_slot_key = MemoryKey()
    save_slot(c.store, 'fixture', [HOST], SECRET, encrypted=True)
    key.delete()

    listing = {item['id']: item for item in c.service.preparations_status()['preparations']}
    assert listing[row['id']]['check']['code'] == '[redacted]'
    assert set(listing[row['id']]['recurrence_labels'].values()) == {'[redacted]'}
    assert listing[regular['id']]['goal'] == regular['goal_text']


def test_hit_and_failure_provenance_distinguishes_allowed_from_contact(case, monkeypatch):
    c = case
    monkeypatch.setattr(code_run.CodeRunner, 'secret_sets', lambda *a: ([], {}, {}))
    args = dict(ARGS, secrets=['fixture'])
    prove(c, args)
    row = schedule(c, args)
    requests = [
        {'method': 'GET', 'host': HOST, 'path': '/'},
        {'method': 'GET', 'host': 'blocked.example.net', 'path': '/'},
    ]
    tick(c, {'stdout': 'hit'}, requests)

    def assert_provenance(kind):
        job = wake_job(c, row)
        event = checks.handoff_event(c.store, job)['trace']
        assert event['kind'] == kind
        assert event['allowed_secret_sets'] == ['fixture'] and event['allowed_hosts'] == [HOST]
        history = c.service.preparation_history(job)[-1]['content']
        assert f'허용된 호스트: {HOST}.' in history
        assert '허용된 비밀값 묶음: fixture.' in history
        audit = work_information_use(c.store, job['id'], compare_held_values=False)
        connections = audit['sent_to']['connections']
        assert [(item['destination'], item['state']) for item in connections] == [(HOST, 'allowed')]
        assert 'fixture' not in json.dumps(audit['used'])
        return job

    assert_provenance('hit')
    finish(c, row)
    for _ in range(3):
        c.clock[0] += 15
        tick(c, requests=requests, error='transport stopped')
    assert_provenance('failure')
    assert finish(c, row)['state'] == prep.STATE_PAUSED
    assert not c.checks.run_due()


def test_failure_before_sandbox_reports_secret_sets_as_allowed(case):
    c = case
    args = dict(ARGS, secrets=['fixture'])
    c.store.api_slot_key = MemoryKey()
    save_slot(c.store, 'fixture', [HOST], {'TOKEN': SECRET}, encrypted=True, code={'fields': ['TOKEN']})
    prove(c, args)
    row = schedule(c, args)

    def install(*args):
        raise code_run.CodeRunError('local setup failed', 'code_run_unavailable')

    c.checks.runner = lambda store, record_id, **kwargs: checks.CheckRunner(
        store, record_id, node=WIRE['node'], install=install,
        popen=lambda *a, **k: pytest.fail('sandbox started'), **kwargs)
    for _ in range(3):
        assert c.checks.run_due()
        c.clock[0] += 15
    job = wake_job(c, row)
    event = checks.handoff_event(c.store, job)['trace']
    assert event['allowed_secret_sets'] == ['fixture']
    assert event['allowed_hosts'] == []
    assert '허용된 비밀값 묶음: fixture.' in c.service.preparation_history(job)[-1]['content']
    assert 'fixture' not in json.dumps(work_information_use(c.store, job['id'])['used'])


def test_dedup_and_wake_replay_bind_full_check_spec(case):
    c = case
    row = prepared(c)
    assert schedule(c)['id'] == row['id']
    prove(c, {**ARGS, 'timeout_seconds': 91})
    assert schedule(c, {**ARGS, 'timeout_seconds': 91})['id'] != row['id']
    spec = json.loads(row['check_spec'])
    spec['libraries'] = {'fixture': {'id': 'other-version', 'digest': 'other-digest'}}
    different_pin = c.preps.create(kind='prepare', goal=row['goal_text'], due_at=row['due_at'], timezone='UTC',
        recurrence=None, channel='web', created_from=c.owner, state=prep.STATE_SCHEDULED,
        window=(15, row['until_at'], row['max_runs']), check=spec)
    assert different_pin['id'] != row['id']
    assert c.preps.cancel(different_pin['id'])['changed']

    wake = {'slot': row['check_next_at'], 'outside': None, 'return_state': None,
            'handoff': {'kind': 'hit', 'check': json.loads(row['check_spec'])['digest'],
                        'at': c.clock[0], 'output': 'observed'}}
    def start(candidate):
        return c.preps.start(candidate, channel='web', chat_id=None, now=c.clock[0], wake=wake)

    job = start(row)
    assert job and start(row) == job
    with c.store.db() as db:
        assert db.execute("SELECT count(*) FROM tool_events WHERE tool='condition_check' AND job_id=?",
                          (job,)).fetchone()[0] == 1
    assert c.store.config(prep.SCHEDULED_USAGE_PREFIX + c.preps.budget_day(c.clock[0])) == 1
    finish(c, row)

    # Same row, slot and embedded digest, with a different full specification.
    changed_spec = json.dumps(spec, sort_keys=True)
    with c.store.db() as db:
        db.execute('UPDATE preparations SET check_spec=? WHERE id=?', (changed_spec, row['id']))
    changed_job = start(current(c, row))
    assert changed_job and changed_job != job
    assert wake_job(c, row)['id'] == changed_job


def test_keyring_work_happens_without_sqlite_write_lock(case, monkeypatch):
    c = case
    key = c.store.api_slot_key = MemoryKey()
    row = prepared(c)
    save_slot(c.store, 'fixture', [HOST], SECRET, encrypted=True)
    c.preps.set_budget(1)
    day = c.preps.budget_day(c.clock[0])
    c.store.put(prep.SCHEDULED_USAGE_PREFIX + day, 1)
    unlocked = []

    def check_sqlite():
        with c.store.db() as db:
            db.execute('PRAGMA busy_timeout=0')
            db.execute('BEGIN IMMEDIATE')
        unlocked.append(True)
        return key.key

    # Cold redaction has to decrypt the newly saved encrypted credential.
    monkeypatch.setattr(key, 'get', check_sqlite)
    settle = c.checks._settle

    def cold_settle(*args, **kwargs):
        # Force the notification/redaction side itself to read the key again.
        c.store._known_secret_material = c.store._redaction_secret_values = None
        return settle(*args, **kwargs)

    monkeypatch.setattr(c.checks, '_settle', cold_settle)
    tick(c, {'stdout': SECRET})
    assert len(unlocked) >= 2
    assert current(c, row)['run_count'] == 0
    assert current(c, row)['check_next_at'] == row['until_at']
    with c.store.db() as db:
        assert db.execute('SELECT 1 FROM messages WHERE job_id=?',
                          (prep.SCHEDULED_USAGE_PREFIX + day,)).fetchone()


def test_run_now_uses_no_wake_or_wake_key(case):
    c = case
    row = prepared(c)
    manual = c.preps.run_now(row['id'], channel='web', chat_id=None)
    assert current(c, row)['run_count'] == 0
    assert checks.handoff_event(c.store, c.store.job(manual)) is None
    finish(c, row)
    tick(c, {'stdout': 'hit'})
    woken = wake_job(c, row)
    assert manual != woken['id']
    assert '~manual' in c.store.job(manual)['request_key']
    assert '~wake-' in woken['request_key']
    assert current(c, row)['run_count'] == status(c, row)['wakes'] == 1
    assert c.store.config(prep.SCHEDULED_USAGE_PREFIX + c.preps.budget_day(c.clock[0])) == 1


def test_queue_full_retries_the_original_hit_without_another_check(case, monkeypatch):
    c = case
    row = prepared(c)
    queued = [c.store.enqueue('Queue filler', f'queued:{i}') for i in range(99)]
    tick(c, {'stdout': 'transient observation'})
    assert current(c, row)['state'] == prep.STATE_SCHEDULED
    assert current(c, row)['run_count'] == status(c, row)['wakes'] == 0
    usage = prep.SCHEDULED_USAGE_PREFIX + c.preps.budget_day(c.clock[0])
    assert c.store.config(usage) is None

    monkeypatch.setattr(c.checks, 'runner', lambda *a, **k: pytest.fail('pending retry reran the sandbox'))
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (queued[0],))
    c.clock[0] += 1
    assert c.checks.run_due()
    job = wake_job(c, row)
    event = checks.handoff_event(c.store, job)['trace']
    assert event['output'] == 'transient observation'
    assert event['at'] == row['check_next_at']
    assert f":{row['check_next_at']}~wake-" in job['request_key']
    assert current(c, row)['run_count'] == status(c, row)['wakes'] == c.store.config(usage) == 1
    assert status(c, row)['runs'] == 1
    assert 'pending' not in status(c, row)


def test_replacement_is_atomic_to_the_scheduler(case, monkeypatch):
    c = case
    old = prepared(c)
    create = c.preps.create

    def observe(*args, **kwargs):
        new = create(*args, **kwargs)
        # An independent scheduler connection sees the state committed so far.
        with c.store.db() as db:
            scheduled = {row['id'] for row in db.execute("SELECT id FROM preparations WHERE state='scheduled'")}
        assert len(scheduled & {old['id'], new['id']}) <= 1
        return new

    monkeypatch.setattr(c.preps, 'create', observe)
    new = schedule(c, goal='Report the revised observation', replaces=old['id'])
    assert current(c, old)['state'] == prep.STATE_CANCELLED
    assert current(c, new)['state'] == prep.STATE_SCHEDULED
    tick(c, {'stdout': 'hit'})
    assert wake_job(c, new)
    assert not wake_job(c, old)


def test_cap_rechecked_on_resume_accept_restore_and_unpause(case):
    c = case
    first = prepared(c, goal='Paused')
    c.preps.pause(first['id'])
    own_pause = schedule(c, goal='Paused by the woken Work')
    tick(c, {'stdout': 'pause this watch'})
    c.preps.change_own(own_pause['id'], wake_job(c, own_pause)['id'], 'pause')
    assert finish(c, own_pause)['state'] == prep.STATE_PAUSED
    cancelled = schedule(c, goal='Cancelled')
    c.preps.cancel(cancelled['id'])
    spec = json.loads(first['check_spec'])
    proposal = c.preps.create(kind='prepare', goal='Proposed', due_at=first['due_at'], timezone='UTC',
        recurrence=None, channel='web', created_from=c.owner, state=prep.STATE_PROPOSED,
        window=(15, first['until_at'], first['max_runs']), check=spec)
    active = [schedule(c, goal=f'Active {i}') for i in range(10)]
    assert c.preps.get(active[-1]['id'])['state'] == prep.STATE_SCHEDULED
    for operation in (lambda: schedule(c, goal='Eleventh'),
                      lambda: c.preps.resume(first['id']),
                      lambda: c.preps.restore(cancelled['id']),
                      lambda: c.preps.resume(own_pause['id']),
                      lambda: c.preps.accept(proposal['id'], prep.ACCEPTED_OWNER_REQUEST)):
        with pytest.raises((prep.PreparationRefusal, runtime.ToolError)) as exc:
            operation()
        assert exc.value.code == 'too_many_checks'
    c.preps.pause(active[0]['id'])
    assert c.preps.resume(first['id'])['state'] == prep.STATE_SCHEDULED


def test_next_due_is_capped_and_window_expires_without_another_run(case):
    c = case
    row = prepared(c, every=None, until=20)
    tick(c)
    assert current(c, row)['check_next_at'] == row['until_at']
    assert status(c, row)['runs'] == 1
    c.clock[0] += 20
    assert c.checks.run_due()
    assert current(c, row)['state'] == prep.STATE_EXPIRED
    assert status(c, row)['runs'] == 1


def test_check_runs_outside_work_lanes_and_serializes_across_services(case):
    c = case
    row = prepared(c)
    entered, release = threading.Event(), threading.Event()

    class HeldProcess(FakeProcess):
        def _lines(self):
            entered.set()
            assert release.wait(5)
            yield from super()._lines()

    c.processes.append(HeldProcess())
    other = checks.ConditionChecks(c.service, runner=lambda *a, **k: pytest.fail('second check ran'))
    spawned = []
    c.service.work_lane_spawn = lambda target, name: spawned.append(target)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(c.checks.run_due)
        try:
            assert entered.wait(5)
            assert not c.service.lane_work_ids()
            assert c.service.dispatch_work() == 1
            assert c.service.lane_work_ids() == {c.owner}
            second = schedule(c, goal='A second due check')
            assert current(c, second)['check_next_at'] == c.clock[0]
            assert not other.run_due()
            c.preps.pause(row['id'])
        finally:
            release.set()
        assert pending.result(timeout=5)
    assert not wake_job(c, row)
    assert current(c, row)['check_status'] is None


def test_outside_content_parity_and_page_mail_agent_standing(case, monkeypatch):
    c = case
    row = prepared(c)

    def ordinary(action, key):
        work = c.store.enqueue('read', key)
        caps = SimpleNamespace(store=c.store, job_id=work, tools={action: {'host_action': action}})
        returned = runtime.record_outside_result(lambda *args: {'text': 'outside observation'})(caps, action, {})
        assert returned['text']
        return c.store.outside_content_sources(work)

    tick(c, {'stdout': 'hit'})
    assert c.store.outside_content_sources(wake_job(c, row)['id']) == ordinary('code_run', 'code')
    assert ordinary('code_run', 'marked')

    # Counterfactual standing uses the same rule and cannot acquire a wake-only mark.
    monkeypatch.setattr(runtime, 'OWNER_ONLY_RESULT_ACTIONS', runtime.OWNER_ONLY_RESULT_ACTIONS | {'code_run'})
    finish(c, row)
    c.clock[0] += 15
    tick(c, {'stdout': 'hit again'})
    assert c.store.outside_content_sources(wake_job(c, row)['id']) == ordinary('code_run', 'unmarked') == []
    for action in ('public_page_read', 'mail_read', 'a2a_message'):
        assert ordinary(action, action)[0]['operation'] == action


def test_location_continuation_preserves_wake_output_and_outside_content(case):
    c = case
    row = prepared(c)
    tick(c, {'stdout': 'outside observation'})
    wake = wake_job(c, row)
    marks = c.store.outside_content_sources(wake['id'])
    assert marks
    with c.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        continuation = c.service.continue_located_work(
            db, {'job_id': wake['id'], 'request_id': 'location-1', 'observation_id': 'position-1'},
            {'message_id': 1}, 'fixture-generation', 123)

    assert continuation and continuation != wake['id']
    assert c.store.outside_content_sources(continuation) == marks
    assert 'outside observation' in c.service.preparation_history(c.store.job(continuation))[-1]['content']


def test_same_work_exit_zero_proof_and_read_only_sandbox(case):
    c = case
    prove(c, process=FakeProcess(result={'exit': 1}))
    with pytest.raises(runtime.ToolError, match='first') as exc:
        schedule(c)
    assert exc.value.code == 'check_not_proven'
    row = prepared(c)
    with pytest.raises(runtime.ToolError) as exc:
        schedule(c, {**ARGS, 'code': 'print(2)'})
    assert exc.value.code == 'check_not_proven'
    with pytest.raises(runtime.ToolError):
        schedule(c, {**ARGS, 'packages': 'example==1'})
    with pytest.raises(runtime.ToolError):
        schedule(c, every=14)
    c.owner = c.store.enqueue('another Work', 'another', owner_typed=True)
    with pytest.raises(runtime.ToolError) as exc:
        schedule(c)
    assert exc.value.code == 'check_not_proven'
    process = tick(c, requests=[{'method': 'POST', 'host': HOST, 'path': '/change'}])
    assert [entry['allow'] for entry in process.decisions] == [False]
    assert process.config['timeoutMs'] == 15000
    assert current(c, row)['run_count'] == 0


def test_wake_limit_and_owner_payment_autonomy(case):
    c = case
    c.service.context_observations.set_controls({'timezone': 'Asia/Seoul'})
    c.clock[0] = datetime(2026, 11, 19, 14, 53, tzinfo=timezone.utc).timestamp()
    c.preps.set_budget(0)
    row = prepared(c)
    assert row['max_runs'] == 48
    for i in range(24):
        tick(c, {'stdout': 'same hit'})
        job = wake_job(c, row)
        assert not c.service.self_set_run(job)
        if i == 0:
            assert c.service._authorize_payment_autonomy(job, None, 'pay') is None
            c.service.set_payment_autonomy({'enabled': True, 'revision': 0})
            assert c.service._authorize_payment_autonomy(job, None, 'pay')['enabled']
            assert not c.checks.run_due()
        finish(c, row)
        c.clock[0] += 15
    # The cap must prevent dispatch even though the row has window wakes left.
    unused = FakeProcess(result={'stdout': 'cap should prevent this hit'})
    c.processes.append(unused)
    assert c.checks.run_due()
    assert unused.config is None and c.processes == [unused]
    assert current(c, row)['run_count'] == status(c, row)['wakes'] == 24
    midnight = datetime(2026, 11, 19, 15, tzinfo=timezone.utc).timestamp()
    assert current(c, row)['check_next_at'] == midnight
    c.clock[0] = midnight
    assert c.checks.run_due()
    assert unused.config is not None
    assert current(c, row)['run_count'] == 25
    assert status(c, row)['wakes'] == 1


def test_pinned_library_survives_current_version_change_and_pauses_on_forget(case):
    from personal_agent.procedures import ProcedureLibrary

    c = case
    c.store.api_slot_key = MemoryKey()
    save_slot(c.store, 'fixture', [HOST], {'TOKEN': SECRET}, encrypted=True,
              code={'fields': ['TOKEN']})
    library = ProcedureLibrary(c.store)
    old = library.commit('fixture', 'def read(): return "old"')
    args = dict(ARGS, secrets=['fixture'])
    prove(c, args)
    row = schedule(c, args)
    new = library.commit('fixture', 'def read(): return "new"')
    assert json.loads(row['check_spec'])['libraries']['fixture'] == {'id': old['id'], 'digest': old['digest']}

    class CapturesLibrary(checks.CheckRunner):
        def _session(self, node, runtime_path, workdir, *a, **k):
            assert (Path(workdir) / 'lib_fixture.py').read_text().startswith(old['source'])
            assert not self.approvals
            return super()._session(node, runtime_path, workdir, *a, **k)

    c.checks.runner = lambda store, record_id, **kwargs: CapturesLibrary(
        store, record_id, popen=lambda *a, **k: c.processes.pop(0), **WIRE, **kwargs)
    tick(c)
    assert library.current('fixture')['id'] == new['id']
    with c.store.db() as db:
        db.execute("UPDATE procedure_versions SET state='forgotten' WHERE id=?", (old['id'],))
    c.clock[0] += 15
    assert c.checks.run_due()
    assert current(c, row)['state'] == prep.STATE_PAUSED
    assert status(c, row)['paused'] == 'library'


def test_proving_a_run_that_asked_approval_or_sent_an_effect_is_refused(case):
    from test_code_run_secrets import FakeApprovals

    c = case
    c.store.api_slot_key = MemoryKey()
    save_slot(c.store, 'fixture', [HOST], {'TOKEN': SECRET}, encrypted=True, money=True,
              code={'fields': ['TOKEN']})
    approvals = FakeApprovals()
    args = dict(ARGS, secrets=['fixture'])
    request = {'method': 'POST', 'host': HOST, 'path': '/change', 'bodyText': '{}'}
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (c.owner,))

    def run():
        return prove(c, dict(args, approval_note='Make the requested change'),
                     FakeProcess(requests=[request]), approvals=approvals)

    assert run()['approval_requested'] and approvals.requested
    with pytest.raises(runtime.ToolError) as exc:
        schedule(c, args)
    assert exc.value.code == 'check_not_proven'
    approvals.granted.append(approvals.requested[0][0])
    assert run()['effects']
    with pytest.raises(runtime.ToolError) as exc:
        schedule(c, args)
    assert exc.value.code == 'check_not_proven'


def test_forgetting_a_line_cancels_its_checks_even_if_the_brief_is_restored(case):
    from personal_agent.owner_forget import OwnerForget

    c = case
    line = c.service.lines.open('owner-work', task='Watch the condition', report_promise='Report findings')['line_id']
    prove(c)
    row = schedule(c, line=line)
    forget = OwnerForget(c.store)
    receipt = forget.forget('local-owner', 'line:' + line)
    assert current(c, row)['state'] == prep.STATE_CANCELLED
    forget.undo('local-owner', receipt['receipt'])
    assert not c.checks.run_due()
