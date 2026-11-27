"""#1272: ending an entrusted line hands the owner's AI the facts for the report the line promised at its end.

Real local code: the line store, the condition-check dispatch, the effect ledger and the line's stop.
"""
from personal_agent import preparations as prep
from personal_agent.messages import msg
from test_condition_check_orders_1194 import POST, run_due, switch
from test_condition_checks_972 import case, current, wake_job  # noqa: F401 - fixture
from test_line_effects_1252 import line_with_check


def stop(c, line, scope='line'):
    owner = c.store.job(c.owner)
    return c.service.preparations_service._line_stop(owner, {'line': line, 'scope': scope})


def test_ending_a_line_carries_its_closing_account(case):
    line, row = line_with_check(case)
    switch(case, True)
    sent = []
    assert run_due(case, sent)['allow'] and sent == [POST]
    stopped = stop(case, line)
    closing = stopped['closing_account']
    assert 'Keep a watch' in closing and 'Report at the end' in closing, 'the brief and its promise'
    assert msg('lines.effects_recorded', sent=1, not_applied=0, unconfirmed=0, refused=0, asked=0) in closing
    assert stopped['next_step'].endswith(msg('lines.stopped_report'))
    assert current(case, row)['state'] == prep.STATE_CANCELLED, 'nothing more is scheduled or run'


def test_a_line_with_nothing_recorded_still_gets_its_closing_report(case):
    line, _row = line_with_check(case)
    stopped = stop(case, line)
    assert 'Report at the end' in stopped['closing_account']
    assert msg('lines.effects_recorded', sent=0, not_applied=0, unconfirmed=0, refused=0, asked=0) not in stopped['closing_account']


def test_stopping_only_the_work_keeps_the_line_and_has_no_closing_report(case):
    line, _row = line_with_check(case)
    stopped = stop(case, line, scope='work')
    assert 'closing_account' not in stopped
    assert msg('lines.stopped_report') not in stopped['next_step']


def test_a_line_already_ended_is_not_reported_again(case):
    line, _row = line_with_check(case)
    stop(case, line)
    again = stop(case, line)
    assert not again['changed'] and 'closing_account' not in again
