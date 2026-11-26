"""#1237 through the existing scheduler, encrypted check state and effect broker."""
from datetime import datetime, timezone
import json

import pytest

from personal_agent import agent_runtime as runtime, code_run, condition_checks as checks, preparations as prep
from personal_agent.api_requests import ApiRequests, save_slot
from personal_agent.browser_jar import MemoryKey
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from personal_agent.messages import msg
from test_check_plan_1201 import update
from test_code_run import FakeProcess
from test_condition_check_orders_1194 import ARGS_WITH_SET, POST, receipts, run_due, setup_check, start_piece, switch
from test_condition_check_state_972 import StateProcess, saved
from test_condition_checks_972 import HOST, SECRET, WIRE, case, current, finish, record, status, tick, wake_job  # noqa: F401
from test_preparations import engine


def at(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def owner(c, message, key):
    work_id = c.store.enqueue(message, key, owner_typed=True, owner_words=True)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (work_id,))
    return c.store.job(work_id)


def schedule(c, job, line_id, **overrides):
    args = {'kind': 'prepare', 'offer': 'Work on incoming reports', 'goal': 'Process the next report',
            'due': at(c.clock[0] + 120), 'line': line_id, **overrides}
    result = c.service.preparation_scheduler(job, '')(args)
    return c.preps.get(result['preparation_id'])


def open_line(c, title='Incoming reports'):
    return c.service.lines.open(c.owner, title=title, task=f'Process {title.lower()}',
                                report_promise='Report changes')['line_id']


def start(c, row):
    c.clock[0] = row['due_at']
    assert c.preps.start(row, channel='web', chat_id=None, now=c.clock[0])
    job = wake_job(c, row)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
    return c.store.job(job['id'])


def allowed(**overrides):
    return {'connection': 'fixture', 'method': 'POST', 'host': HOST, 'path': '/orders/*',
            'action_id': 'send-1', 'idempotency_header': 'Idempotency-Key', **overrides}


def install_plan(c, row, **overrides):
    tick(c, {'stdout': 'Update the check plan'})
    work = wake_job(c, row)
    update(c, work['id'], row, {'calls': [allowed()], 'expires': at(c.clock[0] + 120), 'count': 1, **overrides})
    finish(c, row)
    c.clock[0] += 15


@pytest.mark.parametrize('overrides', [
    {},
    {'path': '/orders'},
    {'path': '/orders/%2e%2e/outside'},
    {'method': 'PATCH'},
    {'headers': [['Idempotency-Key', 'different']]},
])
def test_current_plan_is_checked_at_dispatch(case, overrides):
    row = setup_check(case)
    switch(case, True)
    install_plan(case, row)
    request = {**POST, 'path': '/orders/1', 'headers': [['IDEMPOTENCY-KEY', 'send-1']], **overrides}
    sent = []
    decision = run_due(case, sent, request=request)
    assert decision['allow'] is (not overrides)
    assert sent == ([] if overrides else [request])
    assert len(receipts(case, row)) == int(not overrides)


def test_same_action_id_cannot_dispatch_again_with_a_different_body(case):
    row = setup_check(case)
    switch(case, True)
    install_plan(case, row)
    sent = []
    request = {**POST, 'path': '/orders/1', 'headers': [['Idempotency-Key', 'send-1']]}
    assert run_due(case, sent, request=request)['allow']
    update(case, wake_job(case, row)['id'], row, {'expires': at(case.clock[0] + 120)})
    finish(case, row)
    case.clock[0] += 15
    assert not run_due(case, sent, request={**request, 'bodyText': '{"version":2}'})['allow']
    assert sent == [request] and len(receipts(case, row)) == 1


def test_expired_plan_holds_and_replans_on_the_same_row(case):
    row = setup_check(case)
    switch(case, True)
    install_plan(case, row, expires=at(case.clock[0] + 15))
    sent = []
    request = {**POST, 'path': '/orders/1', 'headers': [['Idempotency-Key', 'send-1']]}
    assert not run_due(case, sent, request=request)['allow']
    assert not receipts(case, row)
    job = wake_job(case, row)
    assert prep.preparation_of(job['request_key']) == row['id']
    assert msg('checks.plan_refresh') in checks.handoff_event(case.store, job)['trace']['output']
    assert status(case, row)['wakes'] == 2
    update(case, job['id'], row, {'expires': at(case.clock[0] + 120)})
    finish(case, row)
    case.clock[0] += 15
    assert run_due(case, sent, request=request)['allow']
    assert sent == [request]


@pytest.mark.parametrize('scheduled', [True, False])
def test_authenticated_steer_clears_plan_before_dispatch_reservation(case, monkeypatch, scheduled):
    c = case
    line_id = open_line(c)
    row = setup_check(c, line=line_id)
    switch(c, True)
    install_plan(c, row, expires=at(c.clock[0] + 600))
    work = owner(c, 'Review incoming reports', 'review')
    if scheduled:
        work = start(c, schedule(c, work, line_id))
    else:
        c.service.preparations_service.line_handler(work)({'action': 'read', 'line': line_id})
    source = c.store.enqueue('Use the revised limit', 'steer', owner_typed=True, owner_words=True)
    guard = ApiRequests._effect_guard

    def correct_before_reserving(api, *args, **kwargs):
        if kwargs.get('method') is not None:
            assert c.store.add_steer(work['id'], source, 'Use the revised limit')
        return guard(api, *args, **kwargs)

    monkeypatch.setattr(ApiRequests, '_effect_guard', correct_before_reserving)
    sent = []
    assert not run_due(c, sent, request={**POST, 'path': '/orders/1',
                                         'headers': [['Idempotency-Key', 'send-1']]})['allow']
    assert not sent and not receipts(c, row)
    assert json.loads(saved(c, row)) == {'calls': [], 'count': 1}


def test_stop_clears_plan_before_a_check_can_be_restored(case):
    line_id = open_line(case)
    row = setup_check(case, line=line_id)
    install_plan(case, row)
    case.service.preparations_service.stop_line(line_id, end_line=False)
    assert current(case, row)['state'] == prep.STATE_CANCELLED
    assert json.loads(saved(case, row)) == {'calls': [], 'count': 1}


def test_check_bookkeeping_cannot_widen_plan_permissions(case):
    row = setup_check(case)
    install_plan(case, row)
    process = StateProcess('{"count":2}')
    case.processes.append(process)
    assert case.checks.run_due()
    state = json.loads(saved(case, row))
    assert state['count'] == 2 and state['calls'] == [allowed()] and state['expires']


def test_autonomy_off_holds_even_a_matching_plan(case):
    row = setup_check(case)
    switch(case, True)
    install_plan(case, row, calls=[allowed(path='/orders', idempotency_header='X-Action')])
    switch(case, False)
    sent = []
    assert not run_due(case, sent, request={**POST, 'headers': [['X-Action', 'send-1']]})['allow']
    assert not sent and not receipts(case, row)


def test_owner_accepts_resume_and_its_check_and_piece_inherit_standing(case):
    c = case
    line_id = open_line(c)
    offered = c.preps.create(kind='prepare', goal='Resume the agreed work today', due_at=c.clock[0] + 120,
                             timezone='UTC', recurrence=None, channel='web', created_from=c.owner,
                             state=prep.STATE_PROPOSED, origin=prep.ORIGIN_LINE_RESUME, line_id=line_id,
                             offer='Continue today?')
    c.service.preparation_request({'operation': 'accept', 'id': offered['id'], 'fingerprint': prep.digest([offered])})
    assert current(c, offered)['accepted_by'] == prep.ACCEPTED_OWNER_BUTTON
    end = c.preps.owner_day_end(c.clock[0])
    notice = msg('lines.entrusted', title='Incoming reports', end=prep.local_text(end, c.service.home_zone()))
    with c.store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM messages WHERE content=?', (notice,)).fetchone()[0] == 1
    job = start(c, current(c, offered))
    c.store.api_slot_key = MemoryKey()
    save_slot(c.store, 'fixture', [HOST], {'TOKEN': SECRET}, encrypted=True, money=True, code={'fields': ['TOKEN']})
    proof = code_run.CodeRunner(c.store, job['id'], popen=lambda *args, **kwargs: FakeProcess(), **WIRE)
    record(c, 'code_run', proof.run(ARGS_WITH_SET), work=job['id'])
    args = {'check': json.dumps({**ARGS_WITH_SET, 'every_seconds': 15}),
            'until': at(c.clock[0] + 900), 'goal': 'Check for a change'}
    with pytest.raises(runtime.ToolError) as exc:
        schedule(c, job, line_id, **{**args, 'until': at(end + 60)})
    assert exc.value.code == 'unattended_run'
    row = schedule(c, job, line_id, **args)
    assert row['accepted_by'] == prep.ACCEPTED_OWNER_BUTTON
    c.clock[0] = row['due_at']
    switch(c, True)
    sent = []
    assert run_due(c, sent)['allow']
    assert sent == [POST]
    piece = start_piece(c, row)
    assert piece['accepted_by'] == prep.ACCEPTED_LINE_PIECE_OWNER


def test_keep_today_judgment_uses_line_title_and_emits_one_notice(case):
    c = case
    line_id = open_line(c)
    other = open_line(c, 'Weekly summaries')
    seen = []

    def judge(context, proposition):
        if context.purpose != 'explicit-preparation-request':
            return None
        preview = context.facts['proposed_preparation']
        seen.append(preview)
        accepted = 'Continue the line' in preview and all(
            word in context.facts['owner_message'] for word in ('Incoming reports', 'today'))
        return BinaryDecision(OUTCOME_DECIDED, accepted, fixture_confidence())

    c.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
    ambiguous = owner(c, 'Keep it running today', 'ambiguous')
    assert schedule(c, ambiguous, other)['accepted_by'] == prep.ACCEPTED_OWNER_WORK
    job = owner(c, 'Keep Incoming reports running today', 'continue-today')
    row = schedule(c, job, line_id, every_minutes='5', until=at(c.clock[0] + 900))
    assert row['accepted_by'] == prep.ACCEPTED_OWNER_REQUEST
    assert 'Incoming reports' in seen[-1] and 'Process incoming reports' in seen[-1]
    assert schedule(c, job, line_id, every_minutes='5', until=at(c.clock[0] + 900))['id'] == row['id']
    with c.store.db() as db:
        texts = [r['content'] for r in db.execute("SELECT content FROM messages WHERE role='assistant'")]
    [notice] = texts
    assert 'Incoming reports' in notice and prep.local_text(row['until_at'], c.service.home_zone()) in notice
    assert '멈춰' in notice or 'stop' in notice
    assert '\n' not in notice
    assert 'ask which title' in runtime.ENTRUSTED_LINE_ARGUMENTS['action']


@pytest.mark.parametrize('steered', [False, True])
@pytest.mark.parametrize('standing', [prep.ACCEPTED_OWNER_BUTTON, prep.ACCEPTED_OWNER_WORK])
def test_correction_keeps_exact_row_standing(case, steered, standing):
    c = case
    line_id = open_line(c)
    original = c.preps.create(kind='prepare', goal='Old instruction', due_at=c.clock[0] + 60, timezone='UTC',
                               recurrence=None, channel='web', created_from=c.owner, state=prep.STATE_SCHEDULED,
                               accepted_by=standing, line_id=line_id)
    c.service.use_decision_engine(engine(preparation=True))
    if steered:
        job = start(c, original)
        source = c.store.enqueue('Use the revised limits', 'correction', owner_typed=True, owner_words=True)
        assert c.store.add_steer(job['id'], source, 'Use the revised limits')
        c.store.claim_steers(job['id'])
    else:
        job = owner(c, 'Use the revised limits', 'correction')
    replacement = schedule(c, job, line_id, replaces=original['id'], goal='Use the revised limits')
    assert replacement['accepted_by'] == standing
    assert current(c, original)['state'] == prep.STATE_CANCELLED


@pytest.mark.parametrize(('origin', 'standing'), [
    (prep.ORIGIN_DREAM, prep.ACCEPTED_DREAM),
    (prep.ORIGIN_DAY_PLAN, prep.ACCEPTED_DAY_PLAN),
    (prep.ORIGIN_LINE_ACCOUNT, prep.ACCEPTED_OWNER_WORK),
])
def test_self_set_runs_cannot_pay_and_explain_why(case, origin, standing):
    c = case
    line_id = open_line(c)
    c.store.api_slot_key = MemoryKey()
    save_slot(c.store, 'fixture', [HOST], SECRET, encrypted=True, money=True)
    row = c.preps.create(kind='prepare', goal='Report changes', due_at=c.clock[0] + 60, timezone='UTC',
                         recurrence=None, channel='web', created_from=c.owner, state=prep.STATE_SCHEDULED,
                         origin=origin, line_id=line_id, accepted_by=standing)
    job = start(c, row)
    switch(c, True)
    api = ApiRequests(c.store, job['id'], approvals=c.service.browser_approvals_for(job),
                      transport=lambda *_a, **_kw: pytest.fail('sent without owner standing'))
    with pytest.raises(runtime.ToolError) as exc:
        api.call({'slot': 'fixture', 'url': f'https://{HOST}/orders', 'method': 'POST', 'body': '{}'})
    assert exc.value.code == 'unattended_run'
    assert str(exc.value) == msg('payment_autonomy.standing_required')
    assert not c.service.browser_step_requests()
