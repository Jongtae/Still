"""#972 D6 and D7 through the normal tools, scheduler, Settings and line input."""
from datetime import datetime, timezone
import json

import pytest

from personal_agent import agent_runtime, code_run, condition_checks as checks, preparations as prep
from personal_agent.api_credentials import CHECK_STATE_PREFIX, ApiCredentials
from personal_agent.api_requests import save_slot
from personal_agent.browser_jar import MemoryKey
from personal_agent.messages import LANGUAGES, msg
from test_code_run import FakeProcess
from test_condition_checks_972 import (
    ARGS, HOST, SECRET, WIRE, case, current, finish, prepared, prove, record, schedule, status, tick, wake_job,
)


SECOND_HOST = 'data.example.org'
REPLACEMENT = {**ARGS, 'code': 'print("replacement check")', 'every_seconds': 30}


def proof_in(c, work, args=REPLACEMENT, *, exit_status=0):
    process = FakeProcess(result={'exit': exit_status})
    runner = code_run.CodeRunner(c.store, work['id'], popen=lambda *a, **k: process, **WIRE)
    result = runner.run(args)
    assert result['exit_status'] == exit_status
    record(c, 'code_run', result, work=work['id'])
    return result


def edit(c, row, work, check=REPLACEMENT, **extra):
    value = json.dumps(dict(id=row['id'], check=check, **extra))
    args = dict(category='preparations', setting='edit', value=value, reason='Continue observing the condition.')
    return c.service.settings_tools(work)('settings_change', args)


def woken(c, *, initial=ARGS, until=3600):
    prove(c, initial)
    row = schedule(c, initial, until=until)
    tick(c, {'stdout': 'hit'})
    work = wake_job(c, row)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (work['id'],))
    return row, c.store.job(work['id'])


def settings_row(c, row):
    return next(item for item in c.service.preparations_status()['preparations'] if item['id'] == row['id'])


def add_set(c, name):
    c.store.api_slot_key = MemoryKey()
    save_slot(c.store, name, [HOST], {'TOKEN': SECRET}, encrypted=True, code={'fields': ['TOKEN']})


def test_woken_run_replaces_check_atomically_with_receipt_notice_and_new_cadence(case, monkeypatch):
    c = case
    add_set(c, 'fixture')
    row, work = woken(c, initial={**ARGS, 'hosts': [HOST, SECOND_HOST], 'secrets': ['fixture']})
    old = current(c, row)
    original = status(c, row)
    # Exercise realistic failure-repair status and ensure scratch result fields are cleared.
    dirty = {**original, 'failures': 3, 'paused': 'failures', 'pending': {'slot': old['due_at']}}
    with c.store.db() as db:
        db.execute('UPDATE preparations SET check_status=?,run_return_state=? WHERE id=?',
                   (json.dumps(dirty), prep.STATE_PAUSED, row['id']))
    monkeypatch.setattr(c.service.preparations_service, 'notice_destination', lambda _channel: (123, 'gen', 'telegram'))
    args = {**REPLACEMENT, 'hosts': [HOST.upper() + ':443']}
    assert proof_in(c, work, args)['check_proof']
    applied = edit(c, row, work, args)
    assert applied['applied']
    assert args['code'] not in json.dumps(applied)
    updated = current(c, row)
    spec = json.loads(updated['check_spec'])
    assert spec['arguments'] == {**ARGS, 'code': args['code']}
    assert updated['every_seconds'] == spec['every_seconds'] == 30
    assert updated['check_next_at'] == c.clock[0] + 30
    assert updated['run_count'] == old['run_count']
    assert updated['run_return_state'] is None
    assert status(c, row) == {key: value for key, value in original.items() if key not in ('failures', 'last_result')}

    [receipt] = [event['trace'] for event in c.store.task_events(work['id']) if event['tool'] == 'preparation_self_change']
    assert receipt['target'] == 'preparations.check'
    assert receipt['before'] == {'check_spec': old['check_spec']}
    assert receipt['after'] == {'check_spec': updated['check_spec']}
    assert receipt['outside_content']
    queued = c.store.notification_of(work['id'], prep.SELF_CHANGE_NOTICE_PREFIX + receipt['fingerprint'])
    assert queued and queued['state'] == 'queued'
    text = settings_row(c, row)['last_result']
    assert all(value in text for value in ('15', '30', HOST, SECOND_HOST, 'fixture', spec['digest'][:12]))
    assert msg('checks.code_changed') in text
    assert args['code'] not in text
    assert settings_row(c, row)['undo_check'] == receipt['fingerprint']

    c.clock[0] += 47
    assert not c.checks.run_due()
    settled = finish(c, row)
    assert settled['state'] == prep.STATE_SCHEDULED
    assert settled['check_next_at'] == c.clock[0] + 30
    c.clock[0] += 29
    assert not c.checks.run_due()
    c.clock[0] += 1
    tick(c)
    assert status(c, row)['last_result'] == 'quiet'


@pytest.mark.parametrize(('case_name', 'code'), [
    ('other-work', 'check_not_proven'),
    ('different-arguments', 'check_not_proven'),
    ('failed-proof', 'check_not_proven'),
    ('new-host', 'check_widened'),
    ('new-set', 'check_widened'),
    ('under-floor', 'invalid_check'),
    ('past-window', 'check_window'),
    ('mixed-goal', None),
    ('mixed-narrowing', None),
    ('other-row', None),
    ('no-longer-running', None),
    ('credential', 'check_credential'),
])
def test_refused_replacement_leaves_the_entire_row_unchanged(case, case_name, code):
    c = case
    row, work = woken(c)
    args = dict(REPLACEMENT)
    extra = {}
    if case_name == 'new-host':
        args['hosts'] = [HOST, SECOND_HOST]
    elif case_name == 'new-set':
        add_set(c, 'fixture')
        args['secrets'] = ['fixture']
    elif case_name == 'under-floor':
        args['every_seconds'] = 14
    elif case_name == 'past-window':
        args['every_seconds'] = 3601
    elif case_name == 'mixed-goal':
        extra['goal'] = 'Rewrite the brief.'
    elif case_name == 'mixed-narrowing':
        extra['max_runs'] = 1
    elif case_name == 'other-row':
        row = prepared(c, goal='Report a separate observation')
        tick(c, {'stdout': 'another hit'})
        assert current(c, row)['state'] == prep.STATE_RUNNING
    elif case_name == 'credential':
        c.store.secret('telegram_token', SECRET)
        args['code'] = 'print("'+SECRET+'")'
    if case_name == 'other-work':
        assert prove(c, args)['check_proof']
    elif case_name == 'different-arguments':
        assert proof_in(c, work, {**args, 'timeout_seconds': 12})['check_proof']
    else:
        proof = proof_in(c, work, args, exit_status=1 if case_name == 'failed-proof' else 0)
        assert ('check_proof' in proof) == (case_name != 'failed-proof')
    if case_name == 'no-longer-running':
        finish(c, row)
    before = current(c, row)
    audit = c.store.config('settings_audit', [])
    result = edit(c, row, work, args, **extra)
    assert result['applied'] is False
    assert result.get('code') == code
    assert current(c, row) == before
    assert c.store.config('settings_audit', []) == audit
    assert not any(event['tool'] == 'preparation_self_change' for event in c.store.task_events(work['id']))
    assert not c.service.settings_orchestrator._drafts()


def test_ordinary_work_with_a_proof_does_not_own_the_running_check(case):
    c = case
    row, _work = woken(c)
    assert prove(c, REPLACEMENT)['check_proof']
    before = current(c, row)
    with pytest.raises(prep.PreparationRefusal) as exc:
        c.preps.change_own(row['id'], c.owner, 'check', {'check': REPLACEMENT})
    assert exc.value.code == 'self_change_refused'
    assert current(c, row) == before


def test_replay_is_refused_even_after_a_second_replacement_and_undo_restores_exact_spec(case):
    c = case
    row, work = woken(c)
    assert proof_in(c, work)['check_proof']
    first = edit(c, row, work)
    assert first['applied']
    newer = {**REPLACEMENT, 'code': 'print("second replacement")', 'every_seconds': 45}
    assert proof_in(c, work, newer)['check_proof']
    second = edit(c, row, work, newer)
    assert second['applied']
    before = current(c, row)
    assert not edit(c, row, work)['applied']
    assert current(c, row) == before
    finish(c, row)
    c.clock[0] += 45
    tick(c)
    assert status(c, row)['runs'] == 2
    c.service.preparation_request({'operation': 'restore', 'id': row['id'], 'fingerprint': first['draft_id']})
    restored = current(c, row)
    assert restored['check_spec'] == row['check_spec']
    assert restored['every_seconds'] == 15
    assert status(c, row)['runs'] == 2
    assert not settings_row(c, row).get('undo_check')


@pytest.mark.parametrize('locked', [False, True], ids=['swept', 'kept-without-key'])
def test_sweep_retires_only_replaced_state_it_can_read(case, locked):
    c = case
    c.store.api_slot_key = MemoryKey()
    row, work = woken(c)
    old_digest = json.loads(row['check_spec'])['digest']
    credentials = ApiCredentials(c.store)
    saved = json.dumps({'token': SECRET})
    assert credentials.write_check_state(row['id'], old_digest, saved)
    before = c.store.secret_items(CHECK_STATE_PREFIX)
    assert proof_in(c, work)['check_proof']
    applied = edit(c, row, work)
    assert applied['applied']
    if locked:
        c.store.api_slot_key.delete()
        assert c.checks.sweep_states() == 0
        assert c.store.secret_items(CHECK_STATE_PREFIX) == before
        return
    assert c.checks.sweep_states() == 1
    assert not credentials.check_state_ids()
    assert checks._redact(c.store, SECRET) == '[redacted]'
    assert c.service.preparations_service.undo_self_change(applied['draft_id'])
    assert credentials.read_check_state(row['id'], old_digest) == ''


def test_replacement_cannot_reset_the_daily_wake_limit_with_shared_budget_off(case):
    c = case
    c.preps.set_budget(0)
    row, work = woken(c, until=86400)
    wake_status = {**status(c, row), 'wakes': checks.DAILY_WAKES, 'total_wakes': checks.DAILY_WAKES}
    with c.store.db() as db:
        db.execute('UPDATE preparations SET check_status=? WHERE id=?', (json.dumps(wake_status), row['id']))
    assert proof_in(c, work)['check_proof']
    assert edit(c, row, work)['applied']
    finish(c, row)
    c.clock[0] += 30
    unused = FakeProcess(result={'stdout': 'another hit'})
    c.processes.append(unused)
    assert c.checks.run_due()
    assert unused.config is None
    assert status(c, row)['wakes'] == checks.DAILY_WAKES
    assert wake_job(c, row)['id'] == work['id']


def test_settings_show_allowed_request_counts_for_the_owner_local_day(case):
    c = case
    c.service.context_observations.set_controls({'timezone': 'Asia/Seoul'})
    c.clock[0] = datetime(2026, 11, 19, 14, 59, 40, tzinfo=timezone.utc).timestamp()
    midnight = datetime(2026, 11, 19, 15, tzinfo=timezone.utc).timestamp()
    row = prepared(c)
    request = {'host': HOST, 'method': 'GET', 'path': '/'}
    denied = {**request, 'host': SECOND_HOST}
    tick(c, requests=[request, denied, {**request, 'host': HOST + ':9443'}])
    view = settings_row(c, row)['check']
    assert view['every_seconds'] == 15 and view['daily_wakes'] == 24
    assert view['status'] == dict(day=c.preps.budget_day(c.clock[0]), runs=1, wakes=0, last_result='quiet',
                                  last_run_at=c.clock[0], last_run_local=prep.local_text(c.clock[0], row['timezone'], c.service.home_zone()),
                                  paused=None, host_requests={HOST: 2})
    assert view['status']['last_run_local']
    labels = c.service.preparations_status()['management']
    for key in ('check_today', 'check_last_run', 'check_result_quiet', 'check_result_hit', 'check_result_failure',
                'check_allowed_requests', 'check_failures'):
        assert set(labels[key]) == set(LANGUAGES)
    c.clock[0] = midnight
    view = settings_row(c, row)['check']['status']
    assert view['host_requests'] == {} and view['runs'] == view['wakes'] == 0
    tick(c, requests=[request])
    assert settings_row(c, row)['check']['status']['host_requests'] == {HOST: 1}
    assert status(c, row)['total_runs'] == 2
    c.store.secret('telegram_token', HOST)
    assert settings_row(c, row)['check']['status']['host_requests'] == {'[redacted]': 1}

    process = FakeProcess(requests=[request, request])
    runner = code_run.CodeRunner(c.store, c.owner, popen=lambda *a, **k: process, **WIRE)
    result = runner.run(ARGS)
    assert [item['allow'] for item in process.decisions] == [True, True]
    assert runner.allowed_hosts == {HOST}
    assert not runner.allowed_requests and 'allowed_requests' not in result
    c.preps.pause(row['id'])
    assert settings_row(c, row)['check']['status']['paused'] == 'requested_pause'


def test_line_listing_and_periodic_account_use_check_counts_and_pin_each_slot(case):
    c = case
    service = c.service.preparations_service
    brief = c.service.lines.open(c.owner, task='Observe the condition', report_promise='Report findings')
    line_id = brief['line_id']
    account = service.ensure_line_account(c.store.job(c.owner), brief, channel='web')
    row = prepared(c, line=line_id)
    tick(c)
    c.clock[0] += 15
    tick(c, {'stdout': 'hit'})
    finish(c, row)
    listing = service.lines_listing()
    [item] = listing['lines'][0]['checks']
    assert (item['every_seconds'], item['last_result'], item['runs'], item['wakes']) == (15, 'hit', 2, 1)
    assert '오늘 검사 2회, AI 깨움 1/24회' in listing['text']

    def start_account(account):
        job = c.preps.run_now(account['id'], channel='web', chat_id=None)
        assert job
        return c.store.job(job)

    def account_input(job):
        return c.service.preparation_history(job)[-1]['content']

    first = start_account(account)
    first_line = msg('lines.account_checks', runs=2, wakes=1)
    assert first_line in account_input(first)
    c.clock[0] += 15
    tick(c)
    assert first_line in account_input(first)
    finish(c, account)
    c.preps.cancel(row['id'])
    c.preps.delete(row['id'])
    c.preps.cancel(account['id'])
    replacement = service.ensure_line_account(c.store.job(c.owner), brief, channel='web')
    second = start_account(replacement)
    assert msg('lines.account_checks', runs=1, wakes=0) in account_input(second)
    assert first_line not in account_input(second)
    finish(c, replacement)
    c.preps.cancel(replacement['id'])
    c.preps.delete(replacement['id'])
    third = start_account(service.ensure_line_account(c.store.job(c.owner), brief, channel='web'))
    assert msg('lines.account_checks', runs=0, wakes=0) in account_input(third)


def test_replacement_call_records_omit_the_check_source_even_if_refused(case):
    args = dict(category='preparations', setting='edit',
                value=json.dumps({'id': 'row', 'check': REPLACEMENT, 'goal': 'mixed edit'}))
    assert REPLACEMENT['code'] not in agent_runtime.recorded_arguments('settings_change', args, str)['value']


def test_long_check_receipt_keeps_both_host_lists_and_one_undo_button(case, monkeypatch):
    c = case
    hosts = [f'host-{index}.' + ('a' * 60 + '.') * 3 + 'example.org' for index in range(code_run.MAX_HOSTS)]
    row, work = woken(c, initial={**ARGS, 'hosts': hosts})
    c.store.put('telegram', {'enabled': True, 'user_id': 123, 'generation': 'gen'})
    sent = []

    def send(chat, text, markup=None):
        assert chat == 123 and len(text) <= 4096
        sent.append((text, markup))
        return {'message_id': len(sent)}

    monkeypatch.setattr(c.service.telegram, 'send_message', send)
    args = {**REPLACEMENT, 'hosts': hosts[:-1]}
    assert proof_in(c, work, args)['check_proof']
    assert edit(c, row, work, args)['applied']
    assert c.service.deliver_notification()
    combined = ''.join(text for text, _ in sent)
    assert len(sent) > 1 and combined.count(hosts[0]) == 2 and combined.count(hosts[-1]) == 1
    assert all(markup is None for _, markup in sent[:-1])
    assert ':undo' in json.dumps(sent[-1][1])
