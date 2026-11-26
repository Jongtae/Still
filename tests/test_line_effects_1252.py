"""#1252: an entrusted line's runs, account and stop are told what Still recorded of its external changes.

Real local code throughout: the condition-check dispatch, the code runner and approval adapter, the
effect ledger, the line's run input and its stop.  Only the sandbox process and the decision engine are injected.
"""
from personal_agent import preparations as prep
from personal_agent.messages import msg
from test_condition_check_orders_1194 import POST, receipts, run_due, setup_check, switch
from test_condition_checks_972 import HOST, case, current, wake_job  # noqa: F401 - fixture
from test_refused_effects_1250 import record, run, woken


def line_with_check(c):
    line = c.service.lines.open(c.owner, task='Keep a watch', report_promise='Report at the end')['line_id']
    return line, setup_check(c, line=line)


def run_input(c, row, job):
    return c.service.preparations_service.line_run_input(job, current(c, row))


def test_nothing_recorded_adds_nothing(case):
    line, row = line_with_check(case)
    effects = case.service.preparations_service.line_effects(line)
    assert effects == {'sent': 0, 'not_applied': 0, 'unconfirmed': 0, 'refused': 0, 'asked': 0, 'unconfirmed_items': []}
    text = case.service.preparations_service.line_effects_text(effects, account=True)
    assert text == ''


def test_sent_change_reaches_the_account_but_not_every_run(case):
    line, row = line_with_check(case)
    switch(case, True)
    sent = []
    assert run_due(case, sent)['allow'] and sent == [POST]
    effects = case.service.preparations_service.line_effects(line)
    assert (effects['sent'], effects['unconfirmed']) == (1, 0)
    service = case.service.preparations_service
    assert '1' in service.line_effects_text(effects, account=True)
    assert service.line_effects_text(effects) == '', 'a settled send is not repeated to every run'


def test_unanswered_send_is_named_to_the_next_run_and_on_stop(case):
    line, row = line_with_check(case)
    switch(case, True)
    assert run_due(case, [], failure='proxy')['allow']
    receipt, = receipts(case, row)
    assert receipt['status'] == 'dispatching'
    effects = case.service.preparations_service.line_effects(line)
    assert effects['unconfirmed'] == 1 and effects['unconfirmed_items'] == [
        {'operation': 'POST', 'host': HOST, 'identity': 'fixture'}]

    # The next run of the line (here read through the owner's Work: the check still holds the row).
    text = run_input(case, row, case.store.job(case.owner))
    assert msg('lines.effects_unconfirmed', items=f'POST {HOST} fixture') in text

    owner = case.store.job(case.owner)
    stopped = case.service.preparations_service._line_stop(owner, {'line': line, 'scope': 'line'})
    assert stopped['effects']['unconfirmed'] == 1
    assert msg('lines.stopped_unconfirmed', count=1) in stopped['next_step']
    # Stopping cancels what had not started; the unanswered send stays recorded as it was.
    assert receipts(case, row)[0]['status'] == 'dispatching'
    assert case.service.preparations_service.line_effects(line)['unconfirmed'] == 1


def test_refused_change_is_counted_and_told_to_the_next_run(case):
    line, row = line_with_check(case)
    switch(case, False)
    job = woken(case, row)
    sent = []
    result, _decisions = run(case, job, sent)
    assert not sent and result['refused_effects']
    record(case, job, result)
    effects = case.service.preparations_service.line_effects(line)
    assert (effects['refused'], effects['sent'], effects['unconfirmed']) == (1, 0, 0)
    assert msg('lines.effects_recorded', sent=0, not_applied=0, unconfirmed=0, refused=1, asked=0) in run_input(case, row, job)


def test_stop_without_open_changes_says_nothing_extra(case):
    line, row = line_with_check(case)
    owner = case.store.job(case.owner)
    stopped = case.service.preparations_service._line_stop(owner, {'line': line, 'scope': 'line'})
    assert stopped['effects']['unconfirmed'] == 0
    assert msg('lines.stopped_unconfirmed', count=0) not in stopped['next_step']
    assert 'Keep a watch' in stopped['next_step'] or stopped['next_step'] == msg('lines.stopped_next')
    assert current(case, row)['state'] == prep.STATE_CANCELLED
