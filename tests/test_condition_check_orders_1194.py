"""#1194: current standing authority for proven condition checks."""
from datetime import datetime, timezone
import json

import pytest

from personal_agent import code_run, condition_checks as checks, preparations as prep
from personal_agent.api_requests import UNKNOWN_EFFECT_TEXT, save_slot
from personal_agent.browser_jar import MemoryKey
from personal_agent.conversation_surface import notice_binding, notice_text
from personal_agent.messages import msg
from personal_agent.quickstart_service import UNATTENDED_APPROVAL_TEXT
from test_code_run import FakeProcess
from test_condition_checks_972 import (  # noqa: F401 - fixture
    ARGS, HOST, SECRET, WIRE, case, current, finish, prove, schedule, status, tick, wake_job,
)
from test_preparations import engine

ARGS_WITH_SET = {
    **ARGS,
    'secrets': ['fixture'],
    'code': f'''import os
from urllib.request import Request, urlopen

if os.environ.get('CHECKSTATEFILE'):
    urlopen(Request("https://{HOST}/orders", b'{{"order_id":"fixture-1"}}', method="POST"))
''',
}
POST = {'method': 'POST', 'host': HOST, 'path': '/orders', 'bodyText': '{"order_id":"fixture-1"}'}


class Transport(FakeProcess):
    """The sandbox proxy forwards only requests authorized by the host."""

    def __init__(self, sent, *, request=POST, stdout='', failure=None):
        super().__init__(requests=[request], result={'stdout': stdout, 'exit': int(failure == 'exit')},
                         error='proxy stopped' if failure == 'proxy' else None)
        self.request = request
        self.sent = sent
        self.failure = failure

    def write(self, text):
        super().write(text)
        message = json.loads(text)
        if message.get('allow') is True and not self.failure:
            self.sent.append(self.request)


def setup_check(c, *, money=True, owner_accepted=True, telegram=False, line=None):
    if telegram:
        c.store.put('telegram', {'enabled': True, 'user_id': 42, 'generation': 'fixture'})
        c.owner = c.store.enqueue('Run on a condition', 'telegram-owner', channel='telegram:fixture',
                                  chat_id=42, owner_typed=True)
    c.store.api_slot_key = MemoryKey()
    save_slot(c.store, 'fixture', [HOST], {'TOKEN': SECRET}, encrypted=True, money=money,
              code={'fields': ['TOKEN']})
    c.service.use_decision_engine(engine(preparation=owner_accepted))
    assert prove(c, ARGS_WITH_SET)['check_proof']
    row = schedule(c, ARGS_WITH_SET, delivery='send' if telegram else None, line=line)
    assert row['accepted_by'] in (prep.OWNER_ACCEPTED if owner_accepted else prep.SELF_SET)
    return row


def run_due(c, sent, **kwargs):
    process = Transport(sent, **kwargs)
    c.processes.append(process)
    assert c.checks.run_due()
    assert len(process.decisions) == 1
    return process.decisions[0]


def run_work(c, job, sent, **kwargs):
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
    process = Transport(sent, **kwargs)
    runner = code_run.CodeRunner(c.store, job['id'], approvals=c.service.browser_approvals_for(job),
                                 popen=lambda *args, **kwargs: process, **WIRE)
    if kwargs.get('failure') == 'proxy':
        with pytest.raises(code_run.CodeRunError):
            runner.run(ARGS_WITH_SET)
    else:
        runner.run(ARGS_WITH_SET)
    decision, = process.decisions
    return decision


def start_piece(c, row):
    parent = wake_job(c, row)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (parent['id'],))
    result = c.service.preparation_scheduler(c.store.job(parent['id']), '')({
        'offer': 'Check the result', 'kind': 'prepare', 'goal': 'Verify the recorded change',
        'due': datetime.fromtimestamp(c.clock[0] + 600, timezone.utc).isoformat(),
    })
    finish(c, row)
    piece = c.preps.get(result['preparation_id'])
    assert piece['origin'] == prep.ORIGIN_LINE_PIECE
    c.clock[0] = piece['due_at']
    assert c.preps.start(piece, channel='web', chat_id=None, now=c.clock[0])
    return piece


def receipts(c, row, tool='api_dispatch'):
    return [event for event in c.store.task_events('check:' + row['id']) if event['tool'] == tool]


def notices(c, row):
    with c.store.db() as db:
        return [dict(notice) for notice in db.execute('SELECT * FROM telegram_notifications WHERE fingerprint=? AND kind=?',
                                                       (row['id'], prep.CHECK_DISPATCH_NOTICE_KIND))]


def switch(c, enabled):
    mode = c.service.payment_autonomy_settings()
    c.service.set_payment_autonomy({'enabled': enabled, 'revision': mode['revision']})


def test_owner_accepted_check_sends_and_records_payment(case):
    row = setup_check(case, telegram=True)
    switch(case, True)
    sent = []
    request = dict(POST, query='limit=2', bodyText=json.dumps({'order_id': 'fixture-1', 'token': SECRET}))
    assert run_due(case, sent, request=request, stdout='order response')['allow']
    assert sent == [request]

    payment, = receipts(case, row, 'payment_autonomy')
    assert payment['status'] == 'authorized'
    assert payment['trace']['evidence']['authorized_by'] == 'payment-autonomy'
    effect, = receipts(case, row)
    assert effect['status'] == 'observed'
    evidence = effect['trace']['evidence']
    assert evidence['effect'] == 'payment' and evidence['operation'] == 'POST'
    assert evidence['host'] == HOST and evidence['identity'] == 'fixture'
    assert evidence['check_id'] == row['id']
    assert evidence['check_digest'] == json.loads(row['check_spec'])['digest']
    assert SECRET not in json.dumps(effect)
    assert not case.service.browser_step_requests()

    notice, = notices(case, row)
    assert (notice['surface'], notice['chat_id'], notice['state']) == ('telegram', 42, 'queued')
    assert notice['job_id'].endswith(f":effect:{effect['id']}")
    text = notice_text(case.service, notice, notice_binding(case.service, notice))
    assert text.startswith(msg('checks.dispatched'))
    exact = f'POST {HOST}/orders'
    assert exact in text and '?limit=2' in text and 'fixture-1' in text
    assert '[redacted]' in text and SECRET not in text
    assert payment['trace']['evidence']['label'].split('\n\n')[-1] in text

    history = case.service.preparation_history(wake_job(case, row))[-1]['content']
    assert exact in history and 'order response' in history and SECRET not in history


@pytest.mark.parametrize('capped', [False, True])
def test_empty_stdout_still_notifies_and_wakes_only_if_allowed(case, capped, monkeypatch):
    row = setup_check(case, telegram=True)
    switch(case, True)
    if capped:
        day = case.preps.budget_day(case.clock[0])
        check_status = {'day': day, 'runs': checks.DAILY_WAKES, 'wakes': checks.DAILY_WAKES}
        with case.store.db() as db:
            db.execute('UPDATE preparations SET check_status=? WHERE id=?', (json.dumps(check_status), row['id']))
    sent = []
    assert run_due(case, sent, stdout='')['allow']
    notice, = notices(case, row)
    shown = []

    def send(chat_id, text, reply_markup=None):
        shown.append((chat_id, text))
        return {'message_id': 7}

    monkeypatch.setattr(case.service.telegram, 'send_message', send)
    assert case.service.deliver_notification()
    assert not case.service.deliver_notification()
    (chat_id, text), = shown
    assert chat_id == 42 and POST['bodyText'] in text
    assert case.store.telegram.notification(notice['id'])['state'] == 'sent'
    if capped:
        assert status(case, row)['wakes'] == checks.DAILY_WAKES
        assert not current(case, row)['last_run_job_id']
        assert current(case, row)['check_next_at'] == case.clock[0] + 15
    else:
        assert status(case, row)['wakes'] == 1
        job = wake_job(case, row)
        assert checks.handoff_event(case.store, job)['trace']['output'] == ''
        assert POST['bodyText'] in case.service.preparation_history(job)[-1]['content']


def test_second_check_run_refuses_same_order_identity(case):
    row = setup_check(case, telegram=True)
    switch(case, True)
    sent = []
    assert run_due(case, sent)['allow']
    finish(case, row)
    case.clock[0] += 15
    assert not run_due(case, sent)['allow']
    assert sent == [POST]
    assert len(receipts(case, row)) == len(receipts(case, row, 'payment_autonomy')) == 1
    assert len(notices(case, row)) == 1


@pytest.mark.parametrize('failure', [None, 'exit', 'proxy'])
def test_woken_work_cannot_repeat_the_check_dispatch(case, failure):
    row = setup_check(case)
    switch(case, True)
    sent = []
    assert run_due(case, sent, failure=failure)['allow']
    receipt, = receipts(case, row)
    assert receipt['status'] == ('dispatching' if failure else 'observed')

    decision = run_work(case, wake_job(case, row), sent)
    assert not decision['allow']
    if failure:
        assert decision['reason'] == UNKNOWN_EFFECT_TEXT
        other = dict(POST, bodyText='{"order_id":"fixture-2"}')
        decision = run_work(case, wake_job(case, row), sent, request=other)
        assert not decision['allow'] and decision['reason'] == UNKNOWN_EFFECT_TEXT
        assert not sent
    else:
        assert '이미 보낸 API 동작' in decision['reason']
        assert sent == [POST]


@pytest.mark.parametrize('failure', [None, 'exit'])
def test_unrelated_preparation_can_send_the_same_request(case, failure):
    line = case.service.lines.open(case.owner, task='Watch a condition', report_promise='Report findings')['line_id']
    row = setup_check(case, line=line)
    switch(case, True)
    sent = []
    assert run_due(case, sent, failure=failure)['allow']
    other = schedule(case, ARGS_WITH_SET, goal='Make the independent change', line=line)
    assert row['id'] != other['id']
    assert case.preps.run_now(other['id'], channel='web', chat_id=None)
    piece = start_piece(case, other)
    assert run_work(case, wake_job(case, piece), sent)['allow']
    assert sent == ([POST] if failure else [POST, POST])


def test_line_piece_inherits_its_parent_preparations_check_receipt(case):
    line = case.service.lines.open(case.owner, task='Watch a condition', report_promise='Report findings')['line_id']
    row = setup_check(case, line=line)
    switch(case, True)
    sent = []
    assert run_due(case, sent)['allow']
    piece = start_piece(case, start_piece(case, row))
    decision = run_work(case, wake_job(case, piece), sent)
    assert not decision['allow'] and '이미 보낸 API 동작' in decision['reason']
    assert sent == [POST]


@pytest.mark.parametrize('failure', [None, 'proxy'])
@pytest.mark.parametrize('deleted', [False, True])
def test_check_cannot_repeat_a_line_piece_dispatch(case, failure, deleted):
    line = case.service.lines.open(case.owner, task='Watch a condition', report_promise='Report findings')['line_id']
    row = setup_check(case, line=line)
    switch(case, True)
    tick(case, result={'stdout': 'Condition met', 'exit': 0})
    piece = start_piece(case, row)
    sent = []
    assert run_work(case, wake_job(case, piece), sent, failure=failure)['allow']
    if deleted:
        finish(case, piece)
        case.preps.delete(piece['id'])

    decision = run_due(case, sent)
    assert not decision['allow']
    if failure:
        assert decision['reason'] == UNKNOWN_EFFECT_TEXT
        assert not sent
    else:
        assert '이미 보낸 API 동작' in decision['reason']
        assert sent == [POST]
    assert not receipts(case, row)


@pytest.mark.parametrize('failure', [None, 'proxy'])
def test_check_cannot_repeat_a_work_dispatch_for_the_same_preparation(case, failure):
    row = setup_check(case)
    switch(case, True)
    sent = []
    job_id = case.preps.run_now(row['id'], channel='web', chat_id=None)
    assert run_work(case, case.store.job(job_id), sent, failure=failure)['allow']
    finish(case, row)

    decision = run_due(case, sent)
    assert not decision['allow']
    if failure:
        assert decision['reason'] == UNKNOWN_EFFECT_TEXT
        assert not sent
    else:
        assert '이미 보낸 API 동작' in decision['reason']
        assert sent == [POST]
    assert not receipts(case, row)


@pytest.mark.parametrize('failure', ['exit', 'proxy'])
def test_failed_transport_notifies_once_without_claiming_a_send(case, failure, monkeypatch):
    row = setup_check(case, telegram=True)
    switch(case, True)
    day = case.preps.budget_day(case.clock[0])
    with case.store.db() as db:
        db.execute('UPDATE preparations SET check_status=? WHERE id=?',
                   (json.dumps({'day': day, 'runs': checks.DAILY_WAKES, 'wakes': checks.DAILY_WAKES}), row['id']))
    sent = []
    request = dict(POST, bodyText=json.dumps({'order_id': 'fixture-1', 'token': SECRET}))
    assert run_due(case, sent, request=request, failure=failure)['allow']
    assert not sent and not current(case, row)['last_run_job_id']
    effect, = receipts(case, row)
    assert effect['status'] == 'dispatching'
    notice, = notices(case, row)
    assert notice['job_id'].endswith(f":effect:{effect['id']}")
    shown = []

    def send(chat_id, text, reply_markup=None):
        shown.append((chat_id, text))
        return {'message_id': 7}

    monkeypatch.setattr(case.service.telegram, 'send_message', send)
    assert case.service.deliver_notification()
    assert not case.service.deliver_notification()
    (chat_id, text), = shown
    assert chat_id == 42 and text.startswith(msg('checks.dispatch_unconfirmed'))
    assert msg('checks.dispatched') not in text
    assert f'POST {HOST}/orders' in text and 'fixture-1' in text
    assert '[redacted]' in text and SECRET not in text


def test_autonomy_disabled_after_proof_refuses_without_asking(case, monkeypatch):
    switch(case, True)
    row = setup_check(case, telegram=True)
    switch(case, False)
    monkeypatch.setattr(case.service, '_request_browser_step', lambda *_: pytest.fail('check asked for approval'))
    sent = []
    decision = run_due(case, sent)
    assert not decision['allow'] and decision['reason'] == UNATTENDED_APPROVAL_TEXT
    assert not sent and not receipts(case, row) and not receipts(case, row, 'payment_autonomy')
    assert not notices(case, row)
    assert not case.service.browser_step_requests()


@pytest.mark.parametrize('provenance', ['self-set', 'null', 'missing-row'])
def test_non_owner_standing_keeps_runner_without_approvals(case, monkeypatch, provenance):
    row = setup_check(case, owner_accepted=provenance != 'self-set')
    switch(case, True)
    monkeypatch.setattr(case.service, '_request_browser_step', lambda *_: pytest.fail('check asked for approval'))
    if provenance == 'null':
        with case.store.db() as db:
            db.execute('UPDATE preparations SET accepted_by=NULL WHERE id=?', (row['id'],))
    elif provenance == 'missing-row':
        run = case.checks._run

        def remove_before_run(*args):
            with case.store.db() as db:
                db.execute('DELETE FROM preparations WHERE id=?', (row['id'],))
            return run(*args)

        monkeypatch.setattr(case.checks, '_run', remove_before_run)

    runner = case.checks.runner

    def without_approvals(*args, **kwargs):
        assert kwargs['approvals'] is None
        return runner(*args, **kwargs)

    monkeypatch.setattr(case.checks, 'runner', without_approvals)
    sent = []
    assert not run_due(case, sent)['allow']
    assert not sent and not receipts(case, row) and not receipts(case, row, 'payment_autonomy')
    assert not case.service.browser_step_requests()


def test_parameter_proof_records_request_without_sending(case, tmp_path):
    row = setup_check(case)
    switch(case, True)
    sent = []
    process = Transport(sent)
    record_id = 'check:' + row['id']
    digest = json.loads(row['check_spec'])['digest']
    approvals = checks.CheckApprovals(case.service, row, record_id, digest)
    runner = checks.CheckRunner(case.store, record_id, pinned={}, approvals=approvals,
                                popen=lambda *args, **kwargs: process, **WIRE)
    masked, plain, policy = runner.secret_sets(['fixture'], [HOST])
    session = (WIRE['node'], case.store.root, ['python3'], policy, [], masked, plain)

    recorded = runner._check_call('lib_fixture', 'order', {}, str(tmp_path), session, 'proof', 10)
    assert process.decisions == [{'id': 1, 'allow': False, 'reason': code_run.CHECK_TEXT}]
    assert f'POST {HOST}/orders?' in recorded and POST['bodyText'] in recorded
    assert not sent and not receipts(case, row) and not receipts(case, row, 'payment_autonomy')


def test_autonomy_revoked_before_dispatch_refuses_existing_ticket(case, monkeypatch):
    row = setup_check(case)
    switch(case, True)
    authorize = case.service._authorize_payment_autonomy

    def revoke_after_ticket(*args, **kwargs):
        result = authorize(*args, **kwargs)
        if result and not kwargs.get('dispatch') and kwargs.get('ticket') is None:
            switch(case, False)
        return result

    monkeypatch.setattr(case.service, '_authorize_payment_autonomy', revoke_after_ticket)
    sent = []
    assert not run_due(case, sent)['allow']
    assert not sent and not receipts(case, row) and not receipts(case, row, 'payment_autonomy')
    assert case.service.payment_autonomy_settings()['enabled'] is False
    assert not case.service.browser_step_requests()


def test_changed_check_arguments_cannot_dispatch_with_an_old_digest(case):
    row = setup_check(case)
    switch(case, True)
    spec = json.loads(row['check_spec'])
    spec['arguments']['code'] += '\nprint("changed")'
    with case.store.db() as db:
        db.execute('UPDATE preparations SET check_spec=? WHERE id=?', (json.dumps(spec), row['id']))
    sent = []
    assert not run_due(case, sent)['allow']
    assert not sent and not receipts(case, row) and not receipts(case, row, 'payment_autonomy')


def test_non_payment_mutation_still_uses_the_write_gate(case, monkeypatch):
    row = setup_check(case, money=False, telegram=True)
    sent = []
    assert not run_due(case, sent)['allow']
    assert not sent and not receipts(case, row) and not notices(case, row)
    case.clock[0] += 15
    switch(case, True)
    gate = case.service.self_set_write
    calls = []

    def record_gate(*args, **kwargs):
        calls.append(kwargs['request'])
        return gate(*args, **kwargs)

    monkeypatch.setattr(case.service, 'self_set_write', record_gate)
    assert run_due(case, sent)['allow']
    assert calls == [False]
    assert sent == [POST]
    effect, = receipts(case, row)
    assert effect['trace']['evidence']['effect'] == 'mutate'
    assert not receipts(case, row, 'payment_autonomy')
    assert not case.service.browser_step_requests()
    notice, = notices(case, row)
    assert notice['job_id'].endswith(f":effect:{effect['id']}")
    assert POST['bodyText'] in notice_text(case.service, notice, notice_binding(case.service, notice))
