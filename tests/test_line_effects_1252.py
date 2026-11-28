"""#1252: an entrusted line's runs, account and stop are told what Still recorded of its external changes.

Real local code throughout: the condition-check dispatch, the code runner and approval adapter, the
effect ledger, the line's run input and its stop.  Only the sandbox process and the decision engine are injected.
"""
import time

from personal_agent import preparations as prep
from personal_agent.browser_signin import PAYMENT_APPROVAL_SECONDS
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
    assert effects == {'sent': 0, 'not_applied': 0, 'unconfirmed': 0, 'refused': 0, 'asked': 0, 'rehearsed': 0, 'unconfirmed_items': [],
                       'awaiting_read_back': 0, 'awaiting_read_back_items': [], 'read_back_not_applied': [],
                       'awaiting_connector_read_back': 0, 'awaiting_connector_read_back_items': [],
                       'read_back_not_applied_reported': []}
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


def test_waiting_for_approval_is_the_current_request_state(case):
    """Counted while a line Work's request is pending, not forever after it was made (#1252 review)."""
    line, row = line_with_check(case)
    job = woken(case, row)
    service = case.service
    asked = lambda: service.preparations_service.line_effects(line)['asked']
    assert asked() == 0
    service._put_browser_request(job['id'], {'state': 'requested', 'requested_at': time.time(), 'digest': 'd'})
    assert service.step_approval_pending(job['id']) and asked() == 1
    service._put_browser_request(job['id'], {'state': 'issued', 'requested_at': time.time(), 'digest': 'd'})
    assert asked() == 0, 'approved: no longer waiting'
    service._put_browser_request(job['id'], {'state': 'requested', 'requested_at': time.time() - PAYMENT_APPROVAL_SECONDS - 5,
                                             'digest': 'd'})
    assert asked() == 0, 'expired: no longer waiting'


def test_unknown_browser_change_is_unconfirmed(case):
    """A browser change with no definite answer has no ledger claim; it still counts (#1252 review)."""
    import json
    line, row = line_with_check(case)
    job = woken(case, row)
    url = 'https://shop.example/checkout'
    with case.store.db() as db:
        for status, detail in [('running', {'arguments': {'url': url}, 'declared_effect': 'mutate'}),
                               ('failed', {'effect': 'unknown', 'declared_effect': 'mutate'})]:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job['id'], 'browser_open', status, json.dumps({'scope': 'main', 'call_id': 'b1', **detail}),
                        case.clock[0]))
    effects = case.service.preparations_service.line_effects(line)
    assert effects['unconfirmed'] == 1
    assert effects['unconfirmed_items'] == [{'operation': 'browser_open', 'host': 'shop.example'}]


def _events(case, job, rows):
    import json
    ids = []
    with case.store.db() as db:
        for tool, status, detail in rows:
            ids.append(db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                                  (job['id'], tool, status, json.dumps(detail), case.clock[0])).lastrowid)
    return ids


def test_failed_connector_change_counts_once(case):
    """The permission claim and the call's unknown record are one change (#1252 review)."""
    line, row = line_with_check(case)
    job = woken(case, row)
    permission, = _events(case, job, [('connector_permission', 'allowed', {
        'scope': 'ai-connection', 'host_action': 'connector_permission',
        'evidence': {'engine': 'claude', 'service': 'Calendar', 'identity': 'cal', 'operation': 'create_event',
                     'kind': 'mcp', 'decision': 'allow', 'effect': 'mutate'}})])
    _events(case, job, [('connector_read', 'failed', {'scope': 'ai-connection', 'host_action': 'connector_read',
                                                      'call_id': 'k1', 'effect': 'unknown', 'permission_event': permission})])
    effects = case.service.preparations_service.line_effects(line)
    assert (effects['unconfirmed'], effects['sent']) == (1, 0)
    assert effects['unconfirmed_items'] == [{'operation': 'create_event', 'identity': 'cal', 'service': 'Calendar'}]


def test_succeeded_browser_change_counts_as_sent_and_a_running_one_as_unconfirmed(case):
    line, row = line_with_check(case)
    job = woken(case, row)
    url = {'url': 'https://shop.example/checkout'}
    _events(case, job, [
        ('browser_open', 'running', {'scope': 'main', 'call_id': 'b1', 'arguments': url, 'declared_effect': 'mutate'}),
        ('browser_open', 'succeeded', {'scope': 'main', 'call_id': 'b1', 'declared_effect': 'mutate'}),
        ('browser_open', 'running', {'scope': 'main', 'call_id': 'b2', 'arguments': url, 'declared_effect': 'mutate'}),
    ])
    effects = case.service.preparations_service.line_effects(line)
    assert effects['sent'] == 1
    assert effects['unconfirmed'] == 1, 'a change still in flight is unconfirmed, not sent'
    assert effects['unconfirmed_items'] == [{'operation': 'browser_open', 'host': 'shop.example'}]
    assert case.service.preparations_service.line_effects_text(effects, account=True)
    owner = case.store.job(case.owner)
    stopped = case.service.preparations_service._line_stop(owner, {'line': line, 'scope': 'line'})
    assert msg('lines.stopped_unconfirmed', count=1) in stopped['next_step']


def test_a_code_run_send_counts_once_through_its_dispatch(case):
    """The run's own Evidence and its dispatch claim are one change (#1252 review)."""
    line, row = line_with_check(case)
    switch(case, True)
    job = woken(case, row)
    sent = []
    result, _ = run(case, job, sent)
    assert sent == [POST]
    record(case, job, result)
    effects = case.service.preparations_service.line_effects(line)
    assert (effects['sent'], effects['unconfirmed']) == (1, 0)


def test_scheduling_and_line_records_are_not_external_sends(case):
    line, row = line_with_check(case)
    job = woken(case, row)
    _events(case, job, [
        ('schedule_preparation', 'succeeded', {'scope': 'main', 'call_id': 's1', 'host_action': 'schedule_preparation'}),
        ('entrusted_line', 'succeeded', {'scope': 'main', 'call_id': 'l1', 'host_action': 'entrusted_line',
                                         'arguments': {'action': 'revise'}}),
        ('update_check_state', 'succeeded', {'scope': 'main', 'call_id': 'u1', 'host_action': 'update_check_state'}),
        ('delegate_agent', 'succeeded', {'scope': 'main', 'call_id': 'd1', 'host_action': 'delegate_agent'}),
    ])
    effects = case.service.preparations_service.line_effects(line)
    assert effects['sent'] == 0 and effects['unconfirmed'] == 0


def test_a_failed_browser_submission_is_unconfirmed(case):
    """A click that posted a form and then timed out changed something; its outcome is open (#1252 review)."""
    line, row = line_with_check(case)
    job = woken(case, row)
    _events(case, job, [
        ('browser_click', 'running', {'scope': 'main', 'call_id': 'c1', 'arguments': {'url': 'https://shop.example/pay'},
                                      'declared_effect': 'navigate'}),
        ('browser_click', 'failed', {'scope': 'main', 'call_id': 'c1', 'declared_effect': 'navigate', 'effect': 'none',
                                     'code': 'browser_timeout', 'observed_step': {'posted': True}}),
    ])
    effects = case.service.preparations_service.line_effects(line)
    assert (effects['unconfirmed'], effects['sent']) == (1, 0)
