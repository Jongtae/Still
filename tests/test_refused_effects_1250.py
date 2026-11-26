"""#1250: a state change Still refused unsent is recorded, and a run that served it is not done.

The sandbox, the approval adapter, the dispatch claim and the Work's effect floor are Still's real
local code; only the sandbox process (``FakeProcess``) and the decision engine are injected.
"""
import json

from personal_agent import code_run, work_status as ws
from personal_agent.agent_runtime import evidence_summary, refused_state_change, state_change_short, trail_steps
from personal_agent.quickstart_service import UNATTENDED_APPROVAL_TEXT
from test_code_run import FakeProcess
from test_condition_check_orders_1194 import ARGS_WITH_SET, POST, SECRET, Transport, receipts, setup_check, switch
from test_condition_checks_972 import HOST, WIRE, case, wake_job  # noqa: F401 - fixture


def run(c, job, sent, *, args=ARGS_WITH_SET, process=None):
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
    process = process or Transport(sent)
    runner = code_run.CodeRunner(c.store, job['id'], approvals=c.service.browser_approvals_for(job),
                                 popen=lambda *a, **k: process, **WIRE)
    return runner.run(args), process.decisions


def woken(c, row):
    """The scheduled run of the check's row: the unattended Work no owner typed."""
    assert c.preps.run_now(row['id'], channel='web', chat_id=None)
    job = wake_job(c, row)
    assert job and job['owner_typed'] != 1 and c.service.unattended_work(job)
    return job


def record(c, job, result, call_id='call-1'):
    """The call as either worker route records it: its Evidence summary, never its output."""
    detail = {'scope': 'worker', 'call_id': call_id, 'host_action': 'code_run',
              'evidence': evidence_summary('code_run', result)}
    with c.store.db() as db:
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (job['id'], 'code_run', 'succeeded', json.dumps(detail), c.clock[0]))
    return detail


def test_unattended_refusal_is_recorded_and_holds_the_check(case):
    """A scheduled run with payment autonomy off: nothing is sent, and the check is not stored as done."""
    row = setup_check(case)
    switch(case, False)
    job = woken(case, row)
    sent = []
    result, (decision,) = run(case, job, sent)
    assert not decision['allow'] and decision['reason'] == UNATTENDED_APPROVAL_TEXT
    assert not sent and not receipts(case, row)
    assert result['exit_status'] == 0, 'the code read the refusal and ended normally'
    refused, = result['refused_effects']
    assert refused == {'method': 'POST', 'host': HOST, 'path': '/orders', 'layer': 'still', 'sent': False,
                       'reason': UNATTENDED_APPROVAL_TEXT}
    assert 'approval_requested' not in result and 'effects' not in result

    detail = record(case, job, result)
    kept = json.dumps(detail)
    assert 'fixture-1' not in kept and SECRET not in kept, 'never the body or a value'
    assert refused_state_change('code_run', detail)
    # The effect floor of the run: withheld, so a reached judgment cannot make it succeeded.
    assert not case.service.goal_upgrade_allowed(job['id'])
    effect = case.service.work_effect_state(job['id'])
    assert effect == ws.EFFECT_WITHHELD
    assert ws.derive_status(ws.GOAL_REACHED, effect, 'succeeded') == 'partial'
    assert not ws.request_met(ws.derive_status(ws.GOAL_REACHED, effect, 'succeeded'), ws.GOAL_REACHED)


def test_standing_authority_sends_and_nothing_is_refused(case):
    """The same scheduled run with the owner's standing autonomy: sent once, no refusal recorded."""
    row = setup_check(case)
    switch(case, True)
    job = woken(case, row)
    sent = []
    result, (decision,) = run(case, job, sent)
    assert decision['allow'] and sent == [POST]
    assert 'refused_effects' not in result and result['effects'] == [{'method': 'POST', 'host': HOST, 'path': '/orders'}]
    record(case, job, result)
    assert case.service.goal_upgrade_allowed(job['id'])
    assert case.service.work_effect_state(job['id']) == ws.EFFECT_APPLIED


def test_replay_refusal_after_a_send_is_recorded_not_resent(case):
    """A woken run cannot send the check's change again; it records the refusal instead of passing as done."""
    row = setup_check(case)
    switch(case, True)
    job = woken(case, row)
    sent = []
    run(case, job, sent)
    assert sent == [POST]
    result, (decision,) = run(case, job, sent)  # the same run's code again
    assert not decision['allow'] and sent == [POST], 'never sent twice'
    refused, = result['refused_effects']
    assert refused['sent'] is False and refused['reason'] == decision['reason']


def test_plain_read_run_is_untouched(case):
    """A run that only reads (a non-trading lookup) records no refusal and stays a clean read."""
    row = setup_check(case)
    job = woken(case, row)
    read = {'method': 'GET', 'host': HOST, 'path': '/status', 'bodyText': ''}
    result, (decision,) = run(case, job, [], process=Transport([], request=read))
    assert decision['allow'] and 'refused_effects' not in result
    record(case, job, result)
    assert case.service.goal_upgrade_allowed(job['id'])
    assert case.service.work_effect_state(job['id']) == ws.EFFECT_NONE


def test_check_phase_refusals_are_not_recorded(case):
    """A parameter-flow check refuses every request by design; that is not a refused change."""
    runner = code_run.CodeRunner(case.store, 'work', popen=lambda *a, **k: FakeProcess(requests=[POST]), **WIRE)
    runner._owners = {HOST: [('fixture', {'revision': 1})]}
    runner._note_refused(dict(POST, method='GET'), 'read')
    assert runner._refused == []
    runner._note_refused(dict(POST, host='elsewhere.example'), 'host not allowed')
    assert runner._refused == [], 'only a secret set host is a state change Still refused'


def test_refused_records_redact_run_local_values_or_withhold(case):
    """A token the run obtained itself is known only after the run; a refused path never shows it."""
    runner = code_run.CodeRunner(case.store, 'work', popen=lambda *a, **k: FakeProcess(requests=[POST]), **WIRE)
    runner._owners = {HOST: [('fixture', {'revision': 1})]}
    runner._note_refused(dict(POST, path='/orders/run-token-4711'), 'refused for run-token-4711')
    runner.scratch_secrets, runner._state_unknown = ['run-token-4711'], False
    row, = runner._redacted_refusals()
    assert 'run-token-4711' not in json.dumps(row) and row['path'].startswith('/orders/')
    runner.scratch_secrets, runner._state_unknown = None, True
    row, = runner._redacted_refusals()
    assert row['path'] == row['reason'] == '[withheld]', 'unknown state values: withheld, never shown as sent'


def test_a_later_run_that_sent_the_same_change_clears_the_hold():
    """A retry is a new call (new id, masked code); it clears the hold by the change it sent."""
    change = {'method': 'POST', 'host': HOST, 'path': '/orders'}
    refused = {'scope': 'w', 'call_id': 'c1', 'host_action': 'code_run',
               'evidence': {'refused_effects': [{**change, 'layer': 'still', 'sent': False}]}}
    retried = {'scope': 'w', 'call_id': 'c2', 'host_action': 'code_run', 'evidence': {'effects': [change]}}
    elsewhere = {'scope': 'w', 'call_id': 'c3', 'host_action': 'code_run',
                 'evidence': {'effects': [{**change, 'path': '/other'}]}}
    other_read = {'scope': 'w', 'call_id': 'c4', 'host_action': 'code_run', 'evidence': {}}
    rows = lambda *details: [('code_run', 'succeeded', json.dumps(d)) for d in details]
    assert state_change_short(trail_steps(rows(refused)))
    assert state_change_short(trail_steps(rows(refused, other_read))), 'an unrelated read does not clear it'
    assert state_change_short(trail_steps(rows(refused, elsewhere))), 'a different change does not clear it'
    assert state_change_short(trail_steps(rows(retried, refused))), 'only a later send clears it'
    assert not state_change_short(trail_steps(rows(refused, retried)))
    assert not state_change_short(trail_steps(rows(other_read)))
