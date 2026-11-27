"""#1259: a run of a window prepare row adds its first condition check through its own-row change.

Evidence class: model-free, the real QuickStore, AgentService, code_run proof and condition-check
scheduler with fixture processes (as ``test_condition_check_replace_972``).  No live model or API.
"""
from datetime import datetime, timezone
import json

from personal_agent import preparations as prep
from test_condition_check_replace_972 import edit, proof_in
from test_condition_checks_972 import ARGS, HOST, case, current, finish, record, tick  # noqa: F401 - fixture

CHECK = {**ARGS, 'every_seconds': 20}


def window_row(c, *, until=3600, kind='prepare', goal='Judge and act on the plan'):
    """A 5-minute window row the owner's Work set up, with no check."""
    when = lambda offset: datetime.fromtimestamp(c.clock[0] + offset, timezone.utc).isoformat()
    args = {'kind': kind, 'goal': goal, 'due': when(0), 'timezone': 'UTC',
            'every_minutes': '5', 'until': when(until), 'delivery': 'keep'}
    if kind == 'prepare':
        args.update(offer='계획을 정리해 드려요.', title='계획')
    result = c.service.preparation_scheduler(c.store.job(c.owner), '')(args)
    record(c, 'schedule_preparation', result)
    return c.preps.get(result['preparation_id'])


def running(c, row):
    """The row's due slot started as a Work, as the scheduler starts it."""
    assert c.service.run_due_preparation()
    row = current(c, row)
    assert row['state'] == prep.STATE_RUNNING
    work = c.store.job(row['last_run_job_id'])
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (work['id'],))
    return row, c.store.job(work['id'])


def test_a_run_adds_its_first_check_and_the_row_becomes_a_check_until_undone(case):
    c = case
    row, work = running(c, window_row(c))
    assert row['check_spec'] is None and row['every_seconds'] == 300
    assert proof_in(c, work, CHECK)['check_proof']
    applied = edit(c, row, work, CHECK)
    assert applied['applied']
    added = current(c, row)
    spec = json.loads(added['check_spec'])
    assert spec['arguments'] == ARGS and added['every_seconds'] == spec['every_seconds'] == 20
    assert (added['accepted_by'], added['until_at'], added['max_runs']) == (row['accepted_by'], row['until_at'], row['max_runs'])
    [receipt] = [event['trace'] for event in c.store.task_events(work['id']) if event['tool'] == 'preparation_self_change']
    assert receipt['before'] == {'check_spec': None, 'every_seconds': 300}
    # The owner's receipt, Settings row and activity read the first check without a previous one.
    text = c.service.preparations_service.self_change_text(receipt)
    assert spec['digest'][:12] in text and HOST in text
    view = next(item for item in c.service.preparations_status()['preparations'] if item['id'] == row['id'])
    assert spec['digest'][:12] in view['last_result']
    [activity] = [item for item in c.service.settings_orchestrator.read('local-owner')['activity']
                  if item.get('target') == 'preparations.check']
    assert activity['before'] == {'check': None, 'every_seconds': 300}
    assert activity['after']['check']['digest'] == spec['digest'][:12]
    # After the run settles, the check carries the row: it runs on its own cadence, not the slots.
    settled = finish(c, row)
    assert settled['state'] == prep.STATE_SCHEDULED and settled['check_next_at'] == c.clock[0] + 20
    c.clock[0] += 20
    tick(c)
    # The owner's undo returns the row to its timed runs.
    c.preps.restore(row['id'])
    restored = current(c, row)
    assert (restored['check_spec'], restored['every_seconds'], restored['check_next_at']) == (None, 300, None)


def test_a_first_check_must_be_proven_by_this_run_and_fit_its_window(case):
    c = case
    row, work = running(c, window_row(c, until=600))
    refused = edit(c, row, work, CHECK)
    assert not refused.get('applied') and current(c, row)['check_spec'] is None
    late = {**CHECK, 'every_seconds': 900}
    proof_in(c, work, late)
    assert not edit(c, row, work, late).get('applied')
    assert current(c, row)['check_spec'] is None


def test_a_first_check_needs_the_runs_own_window_row(case):
    c = case
    row, work = running(c, window_row(c))
    other = window_row(c, goal='Another plan to judge')
    assert other['id'] != row['id']
    proof_in(c, work, CHECK)
    assert not edit(c, other, work, CHECK).get('applied')
    assert current(c, other)['check_spec'] is None



def test_a_first_check_changed_again_in_the_same_run_stays_undoable_to_the_timed_runs(case):
    c = case
    row, work = running(c, window_row(c))
    proof_in(c, work, CHECK)
    assert edit(c, row, work, CHECK)['applied']
    again = {**CHECK, 'code': 'print("second")', 'every_seconds': 30}
    proof_in(c, work, again)
    assert edit(c, row, work, again)['applied']
    assert current(c, row)['every_seconds'] == 30
    finish(c, row)
    c.preps.restore(row['id'])
    restored = current(c, row)
    assert (restored['check_spec'], restored['every_seconds'], restored['check_next_at']) == (None, 300, None)
