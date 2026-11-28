"""#1333: a line's account groups its runs and recorded changes by the strategy version they ran under.

Real local code: the line store, the condition-check dispatch, the line's account and its effect ledger
reader, with a fixture transport and a fixture decision engine.  Facts only; Still ranks nothing (C16).
"""
import time

from personal_agent import preparations as prep
from personal_agent.messages import msg

from test_condition_check_orders_1194 import POST, finish, run_due, switch
from test_condition_checks_972 import case, current, wake_job  # noqa: F401 - fixture
from test_line_effects_1252 import line_with_check
from test_line_rehearsal_1274 import rehearse


def run_of(c, row, slot, brief=None, status='failed'):
    """One Work of ``row``'s line that read ``brief`` (or none), ended with ``status``."""
    job = c.store.enqueue('Run of the line', f"{prep.REQUEST_KEY_PREFIX}{row['id']}:{slot}")
    with c.store.db() as db:
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, job))
    if brief is not None:
        c.service.preparations_service._record_line_read(job, brief)
    return job


def now(c):
    # Live, Still's clock is the wall clock; a check's ledger rows follow the wall clock and its other
    # records Still's clock, so the fixture clock starts at the wall clock (the test runs well within 15 s).
    c.clock[0] = float(int(time.time()))


def test_a_rehearsal_and_the_live_version_are_told_apart(case):
    lines = case.service.preparations_service
    now(case)
    line, row = line_with_check(case)
    switch(case, True)
    live = case.service.lines.current(line)
    run_of(case, row, 'slot-1', live)
    assert run_due(case, [])['allow'], 'the live version sends'
    finish(case, row)
    case.clock[0] += 15
    rehearse(case, line)
    rehearsal = case.service.lines.current(line)
    run_of(case, row, 'slot-2', rehearsal)
    assert not run_due(case, [], request=dict(POST, query='limit=3'))['allow'], 'the rehearsal records instead'
    # Made after the check ran: a queued Work would be picked up by the service's own lanes meanwhile.
    run_of(case, row, 'slot-3', rehearsal, status='queued')

    newer, older = lines.line_versions(line)
    assert (newer['from_version'], newer['rehearsal'], newer['runs']) == (rehearsal['version'], True, 2)
    assert newer['effects'] == {'sent': 0, 'not_applied': 0, 'unconfirmed': 0, 'refused': 0, 'rehearsed': 1}
    assert newer['outcomes']['failed'] == 1 and newer['outcomes']['open'] == 1
    # Its own run and the Work the check woke after sending, made before the rehearsal version.
    assert (older['from_version'], older['rehearsal'], older['runs']) == (live['version'], False, 2)
    assert older['effects']['sent'] == 1 and older['effects']['rehearsed'] == 0
    # The whole line's totals are unchanged by the grouping.
    total = lines.line_effects(line)
    assert (total['sent'], total['rehearsed']) == (1, 1)

    text = lines.line_versions_text(lines.line_versions(line))
    assert text.startswith(msg('lines.versions_heading'))
    assert text.index(msg('lines.version_rehearsal')) < text.index(msg('lines.version_live')), 'newest first'


def test_a_change_of_run_notes_or_state_stays_in_the_same_strategy(case):
    lines = case.service.preparations_service
    now(case)
    line, row = line_with_check(case)
    first = case.service.lines.current(line)
    case.service.lines.write(line, case.owner, first['version'], 'revised', lambda b: {**b, 'open_matters': 'note'})
    noted = case.service.lines.current(line)
    run_of(case, row, 'slot-1', first)
    run_of(case, row, 'slot-2', noted)
    [only] = lines.line_versions(line)
    assert (only['from_version'], only['to_version'], only['runs']) == (first['version'], noted['version'], 2)
    assert lines.line_versions_text([only]) == '', 'one strategy has nothing to compare'


def test_a_run_with_no_recorded_read_belongs_to_the_version_in_force_when_it_was_made(case):
    lines = case.service.preparations_service
    case.clock[0] = 1000.0
    line, row = line_with_check(case)
    first = case.service.lines.current(line)
    case.clock[0] = 2000.0
    case.service.lines.write(line, case.owner, first['version'], 'revised',
                             lambda b: {**b, 'rules': [{'text': 'Only on weekdays', 'from': case.owner, 'at': 2000.0}]})
    job = run_of(case, row, 'slot-1')
    with case.store.db() as db:
        db.execute('UPDATE jobs SET created=? WHERE id=?', (1500.0, job))
    run_of(case, row, 'slot-2', case.service.lines.current(line))
    newer, older = lines.line_versions(line)
    assert (older['from_version'], older['runs']) == (first['version'], 1)
    assert newer['runs'] == 1


def test_the_account_and_the_closing_report_carry_it(case):
    lines = case.service.preparations_service
    now(case)
    line, row = line_with_check(case)
    run_of(case, row, 'slot-1', case.service.lines.current(line))
    now(case)
    rehearse(case, line)
    run_of(case, row, 'slot-2', case.service.lines.current(line))
    assert msg('lines.versions_heading') in lines.line_closing_account(case.service.lines.current(line))


def test_a_deleted_schedules_changes_stay_in_its_version(case):
    """Deleting a schedule keeps its past Works and Evidence, so its changes stay counted (#1333 review)."""
    from test_sent_change_read_back_1273 import sent_on_line
    lines = case.service.preparations_service
    now(case)
    line, _receipt = sent_on_line(case)
    [row] = lines.line_rows(line)
    finish(case, row)
    case.preps.cancel(row['id'])
    case.preps.delete(row['id'])
    [only] = lines.line_versions(line)
    assert only['effects']['sent'] == lines.line_effects(line)['sent'] == 1
