"""Continuous line authority at the real scheduler, SQLite and sandbox proxy boundaries."""
from datetime import datetime, timezone
import json

import pytest

from personal_agent import agent_runtime as runtime, condition_checks as checks, preparations as prep
from personal_agent.api_requests import ApiRequests
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from personal_agent.quickstart_store import QuickStore
from test_check_plan_1201 import update
from test_condition_check_orders_1194 import POST, Transport, receipts, run_due, run_work, setup_check, switch
from test_condition_checks_972 import HOST, case, current, finish, tick, wake_job  # noqa: F401
from test_preparations import engine


def at(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def owner(c, message, key):
    work_id = c.store.enqueue(message, key, owner_typed=True, owner_words=True)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (work_id,))
    return c.store.job(work_id)


def line(c):
    return c.service.lines.open(c.owner, task='Maintain the agreed target', report_promise='Report each change')['line_id']


def line_call(c, job, line_id, action, **kwargs):
    return c.service.preparations_service.line_handler(job)({'action': action, 'line': line_id, **kwargs})


def entrust(c, line_id, *, seconds=900, job=None, decide=True):
    c.service.use_decision_engine(engine(preparation=decide))
    job = job or owner(c, 'Maintain the agreed target for the next fifteen minutes', 'entrust')
    return line_call(c, job, line_id, 'entrust', planner_goal='Refresh the execution plan',
                     ends_at=at(c.clock[0] + seconds))


def setup(c, *, seconds=900, owner_accepted=False):
    line_id = line(c)
    executor = setup_check(c, owner_accepted=owner_accepted, line=line_id)
    standing = entrust(c, line_id, seconds=seconds)
    return line_id, executor, c.preps.get(standing['planner_id'])


def planner_run(c, planner):
    c.service.run_due_preparation(c.clock[0])
    job = wake_job(c, planner)
    assert job and job['status'] == 'queued'
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
    return c.store.job(job['id'])


def publish(c, job, executor, *, revision=1, expiry=120, path='/orders', method='POST', action='adjust-1', **kwargs):
    plan = {'owner_revision': revision, 'expires': at(c.clock[0] + expiry), 'safe_mode': 'hold',
            'calls': [{'connection': 'fixture', 'method': method, 'host': HOST, 'path': path, 'action_id': action,
                       **kwargs}]}
    return update(c, job['id'], executor, {'limit': 2}, plan=json.dumps(plan))


def approve_correction(c, *, close=False):
    def judge(context, proposition):
        if context.purpose == 'explicit-line-change':
            close_proposition = "Close today's work" in context.facts['proposed_change']
            return BinaryDecision(OUTCOME_DECIDED, close or not close_proposition, fixture_confidence())
        return None
    c.service.use_decision_engine(FixtureDecisionEngine(judge=judge))


def correct(c, line_id):
    approve_correction(c)
    job = owner(c, 'Lower the target for the rest of today', 'correction')
    return line_call(c, job, line_id, 'revise', expected_version='1', rule='Use the lower target')


def before_reservation(monkeypatch, act):
    original = ApiRequests._effect_guard

    def interleave(self, *args, **kwargs):
        if kwargs.get('method') is not None:
            act()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ApiRequests, '_effect_guard', interleave)


def test_executor_ticks_and_wakes_coalesce_while_planner_runs(case):
    c = case
    _, executor, planner = setup(c)
    first = planner_run(c, planner)
    for n in range(3):
        tick(c, {'stdout': f'condition {n}'})
        c.clock[0] += 15
        c.service.run_due_preparation(c.clock[0])
        assert current(c, executor)['state'] == prep.STATE_SCHEDULED
        assert current(c, executor)['last_run_job_id'] is None
        assert wake_job(c, planner)['id'] == first['id']
    finish(c, planner)
    second = planner_run(c, planner)
    assert second['id'] != first['id']
    assert 'condition 2' in c.service.preparation_history(second)[-1]['content']
    assert c.store.outside_content_sources(second['id'])
    finish(c, planner)
    c.service.run_due_preparation(c.clock[0])
    assert wake_job(c, planner)['id'] == second['id']


def test_planner_wake_cap_does_not_stop_executor(case):
    c = case
    line_id, executor, planner = setup(c)
    day = c.preps.budget_day(c.clock[0])
    with c.store.db() as db:
        db.execute('UPDATE line_mandates SET wake_day=?,wakes=? WHERE line_id=?',
                   (day, checks.DAILY_WAKES - 1, line_id))
    planner_run(c, planner)
    finish(c, planner)
    previous = wake_job(c, planner)['id']
    tick(c, {'stdout': 'a new trigger'})
    c.clock[0] += 15
    c.service.run_due_preparation(c.clock[0])
    assert wake_job(c, planner)['id'] == previous
    tick(c, {'stdout': 'the check still polls'})
    assert current(c, executor)['check_next_at'] == c.clock[0] + 15


def test_owner_correction_fences_open_check_and_old_planner_at_reservation(case, monkeypatch):
    c = case
    line_id, executor, planner = setup(c)
    work = planner_run(c, planner)
    publish(c, work, executor)
    switch(c, True)
    before_reservation(monkeypatch, lambda: correct(c, line_id))
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and not receipts(c, executor)
    with pytest.raises(runtime.ToolError, match='instructions have changed'):
        publish(c, work, executor, revision=2)
    finish(c, planner)
    next_work = planner_run(c, planner)
    assert publish(c, next_work, executor, revision=2)['owner_revision'] == 2


def test_expiry_between_preflight_and_reservation_holds_and_wakes(case, monkeypatch):
    c = case
    _, executor, planner = setup(c)
    work = planner_run(c, planner)
    publish(c, work, executor, expiry=10)
    switch(c, True)
    before_reservation(monkeypatch, lambda: c.clock.__setitem__(0, c.clock[0] + 10))
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and not receipts(c, executor)
    finish(c, planner)
    c.clock[0] = current(c, executor)['check_next_at']
    tick(c)
    assert 'plan_expired' in c.service.preparation_history(planner_run(c, planner))[-1]['content']


@pytest.mark.parametrize('path,method', [('/another', 'POST'), ('/orders/item', 'PATCH'), ('/orders/%2e%2e', 'POST')])
def test_call_outside_plan_is_refused(case, path, method):
    c = case
    _, executor, planner = setup(c)
    publish(c, planner_run(c, planner), executor, path='/orders/*')
    switch(c, True)
    sent = []
    request = dict(POST, path=path, method=method)
    assert not run_due(c, sent, request=request)['allow']
    assert sent == [] and not receipts(c, executor)


def test_close_racing_reservation_cancels_linked_window_and_planner(case, monkeypatch):
    c = case
    line_id, executor, planner = setup(c)
    work = planner_run(c, planner)
    publish(c, work, executor)
    switch(c, True)
    closer = owner(c, 'Close today now', 'close')
    approve_correction(c, close=True)
    # The model tried to record the close in open matters, the failure reported in the design.
    before_reservation(monkeypatch, lambda: line_call(c, closer, line_id, 'revise', expected_version='1',
                                                      open_matters='Today has ended'))
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and not receipts(c, executor)
    assert current(c, executor)['state'] == current(c, planner)['state'] == prep.STATE_CANCELLED
    assert c.service.work_stopped(work['id'])


def test_unknown_effect_is_held_across_restart_and_new_plan(case):
    c = case
    _, executor, planner = setup(c)
    work = planner_run(c, planner)
    publish(c, work, executor)
    switch(c, True)
    wire = []

    class AcceptedThenDisconnected(Transport):
        def write(self, text):
            super().write(text)
            if json.loads(text).get('allow'):
                wire.append(self.request)

    c.processes.append(AcceptedThenDisconnected([], failure='proxy'))
    assert c.checks.run_due()
    assert wire == [POST] and receipts(c, executor)[0]['status'] == 'dispatching'

    reopened = QuickStore(c.store.root)
    reopened.recover(can_resume_effect=c.service.can_resume_effect)
    c.checks = c.service.condition_checks = checks.ConditionChecks(c.service, runner=c.checks.runner)
    assert reopened.job(work['id'])['status'] == 'interrupted'
    assert current(c, executor)['state'] == prep.STATE_SCHEDULED
    c.service.run_due_preparation(c.clock[0])
    replanner = planner_run(c, planner)
    assert replanner['id'] != work['id']
    assert 'unknown_effect' in c.service.preparation_history(replanner)[-1]['content']
    publish(c, replanner, executor)
    c.clock[0] += 15
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and len(receipts(c, executor)) == 1


def test_logical_action_is_held_under_new_revision_with_a_different_body(case):
    c = case
    _, executor, planner = setup(c)
    work = planner_run(c, planner)
    first = publish(c, work, executor)
    switch(c, True)
    sent = []
    assert run_due(c, sent)['allow']
    second = publish(c, work, executor)
    assert first['plan_id'] != second['plan_id']
    c.clock[0] += 15
    assert not run_due(c, sent, request=dict(POST, bodyText='{"adjustment":8}'))['allow']
    assert len(sent) == len(receipts(c, executor)) == 1


@pytest.mark.parametrize('first', ['owner', 'executor'])
def test_owner_work_and_executor_share_replay_scope(case, first):
    c = case
    line_id, executor, planner = setup(c)
    publish(c, planner_run(c, planner), executor)
    switch(c, True)
    job = owner(c, 'Continue the same line now', 'owner-effect')
    line_call(c, job, line_id, 'read')
    sent = []
    operations = {'owner': lambda: run_work(c, job, sent), 'executor': lambda: run_due(c, sent)}
    assert operations[first]()['allow']
    second = 'executor' if first == 'owner' else 'owner'
    assert not operations[second]()['allow']
    assert sent == [POST]


@pytest.mark.parametrize('caller', ['scheduled', 'outside'])
def test_only_explicit_owner_request_grants_line_standing(case, caller):
    c = case
    line_id = line(c)
    executor = setup_check(c, owner_accepted=False, line=line_id)
    if caller == 'scheduled':
        tick(c, {'stdout': 'condition met'})
        job = wake_job(c, executor)
    else:
        job = owner(c, 'Read the latest report', 'outside')
        with c.store.db() as db:
            c.store._insert_intake_source(db, job['id'], '{"kind":"code_run","host":"api.example.com"}', c.clock[0])
    with pytest.raises(runtime.ToolError) as exc:
        entrust(c, line_id, job=job, decide=caller != 'outside')
    assert exc.value.code == ('unattended_run' if caller == 'scheduled' else 'line_change_needs_owner')
    with c.store.db() as db:
        assert not db.execute('SELECT 1 FROM line_mandates WHERE line_id=?', (line_id,)).fetchone()


@pytest.mark.parametrize('autonomy', [False, True])
def test_reducing_effect_obeys_current_payment_autonomy(case, autonomy):
    c = case
    _, executor, planner = setup(c)
    publish(c, planner_run(c, planner), executor, method='DELETE', path='/resources/*', action='reduce-1')
    switch(c, True)
    if not autonomy:
        switch(c, False)
    sent = []
    request = dict(POST, method='DELETE', path='/resources/item', bodyText='')
    assert run_due(c, sent, request=request)['allow'] is autonomy
    assert sent == ([request] if autonomy else [])


def test_owner_work_row_payment_inherits_active_line_standing(case):
    c = case
    line_id = line(c)
    setup_check(c, owner_accepted=False, line=line_id)
    switch(c, True)
    sent = []
    for entrusted in (False, True):
        if entrusted:
            creator = owner(c, 'Maintain the agreed target for the next fifteen minutes', 'entrust')
            entrust(c, line_id, job=creator)
        else:
            creator = c.store.job(c.owner)
        c.service.use_decision_engine(engine(preparation=False))
        result = c.service.preparation_scheduler(creator, '')({
            'kind': 'prepare', 'goal': 'Apply the planned adjustment', 'offer': 'Adjust the target',
            'due': at(c.clock[0]), 'line': line_id,
        })
        row = c.preps.get(result['preparation_id'])
        assert row['accepted_by'] == prep.ACCEPTED_OWNER_WORK
        assert c.preps.start(row, channel='web', chat_id=None, now=c.clock[0])
        assert run_work(c, wake_job(c, row), sent)['allow'] is entrusted
        finish(c, row)
    assert sent == [POST]


@pytest.mark.parametrize('stop', ['explicit', 'deadline'])
def test_line_stop_cancels_executor_queued_planner_and_pending_trigger(case, stop):
    c = case
    line_id, executor, planner = setup(c, seconds=90)
    c.service.run_due_preparation(c.clock[0])
    queued = wake_job(c, planner)
    assert queued['status'] == 'queued'
    tick(c, {'stdout': 'buffer this event'})
    if stop == 'explicit':
        c.service.preparations_service.stop_line(line_id)
    else:
        c.clock[0] += 90
        c.service.run_due_preparation(c.clock[0])
    assert c.store.job(queued['id'])['status'] == 'cancelled'
    assert current(c, planner)['state'] == current(c, executor)['state'] == prep.STATE_CANCELLED
    assert not c.checks.run_due() and not c.checks.run_due_planner(c.clock[0])
    with c.store.db() as db:
        assert db.execute('SELECT pending FROM line_mandates WHERE line_id=?', (line_id,)).fetchone()[0] is None


def test_missing_plan_holds_even_with_owner_accepted_row(case):
    c = case
    _, executor, _ = setup(c, owner_accepted=True)
    switch(c, True)
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and not receipts(c, executor)


def test_plan_can_require_a_documented_idempotency_header(case):
    c = case
    _, executor, planner = setup(c)
    publish(c, planner_run(c, planner), executor, idempotency_header='Idempotency-Key')
    switch(c, True)
    sent = []
    assert not run_due(c, sent, request=dict(POST, headers=[['idempotency-key', 'different']]))['allow']
    c.clock[0] += 15
    assert run_due(c, sent, request=dict(POST, headers=[['idempotency-key', 'adjust-1']]))['allow']
    assert len(sent) == len(receipts(c, executor)) == 1


def test_owner_steer_fences_before_the_planner_receives_it(case):
    c = case
    _, executor, planner = setup(c)
    work = planner_run(c, planner)
    publish(c, work, executor)
    switch(c, True)
    correction = c.store.enqueue('Use the narrower target now', 'steer', owner_typed=True, owner_words=True)
    assert c.store.add_steer(work['id'], correction, 'Use the narrower target now')
    sent = []
    assert not run_due(c, sent)['allow']
    assert sent == [] and not receipts(c, executor)
    finish(c, planner)
    next_work = planner_run(c, planner)
    assert 'Use the narrower target now' in c.service.preparation_history(next_work)[-1]['content']
    assert publish(c, next_work, executor, revision=2)['owner_revision'] == 2


def test_coalesced_wake_consumes_one_executor_window_run(case):
    c = case
    _, executor, planner = setup(c)
    with c.store.db() as db:
        db.execute('UPDATE preparations SET max_runs=1 WHERE id=?', (executor['id'],))
    planner_run(c, planner)
    tick(c, {'stdout': 'first observation'})
    c.clock[0] += 15
    tick(c, {'stdout': 'second observation'})
    assert current(c, executor)['run_count'] == 0

    finish(c, planner)
    planner_run(c, planner)
    assert current(c, executor)['run_count'] == 1
    c.clock[0] += 15
    assert c.checks.run_due()
    assert current(c, executor)['state'] == prep.STATE_EXPIRED
