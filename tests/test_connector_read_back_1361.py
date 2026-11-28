"""#1361: a connector change a line sent is named until the owner's AI reads it back, then carries that read-back.

The connector decision (``connector_permission``), the stream recording (``record_connector_calls``), the
effect ledger, the line's ``entrusted_line`` handler and its account are Still's real local code, with a
fixture MCP connection and no account or model call.  The read-back is the owner's AI's report: Still
checks only that this Work read the same connection after the send.
"""
import json

import pytest

from personal_agent import trust_record
from personal_agent.agent_runtime import ToolError, external_effect_events
from personal_agent.messages import msg

from test_condition_checks_972 import case, wake_job  # noqa: F401 - fixture
from test_connector_refused_1271 import ACTION, INPUT, call, connect, observed
from test_line_effects_1252 import line_with_check
from test_refused_effects_1250 import woken

READ_ACTION = 'mcp__synthetic__read_status'
READ_INPUT = {'account': 'owner-account-a'}


def sent_on_line(c):
    """A line Work that sent one connector change, as the decision and the worker's stream record it."""
    line, row = line_with_check(c)
    connect(c.store, c.service, operation='mutate')
    target = trust_record.connection('codex', 'owner-mcp', 'synthetic')
    trust_record.set_operation(c.store, target, 'read_status', 'read')
    job = woken(c, row)
    assert call(c.service, job)['behavior'] == 'allow'
    observed(c.service, job)
    [(receipt, evidence)], [] = external_effect_events(c.store, job['id'])
    assert evidence['effect'] == 'mutate' and evidence['identity'] == 'synthetic'
    return line, job, receipt


def read_connection(c, job, action=READ_ACTION, values=READ_INPUT):
    """The owner's AI reads through a connection: decided as a read, then seen completing in the stream."""
    allowed = c.service.connector_permission(job, {'engine': 'codex', 'tool_name': action, 'input': values})
    assert allowed['behavior'] == 'allow'
    digest = c.service.connector_input_digest(action, values)
    c.service.record_connector_calls(job['id'], [{'tool': action, 'status': 'succeeded', 'input_digest': digest}],
                                     engine='codex')


def report(c, job, line, receipt, outcome):
    handle = c.service.preparations_service.line_handler(job)
    return handle({'action': 'read_back', 'line': line, 'receipt': str(receipt), 'outcome': outcome})


def evidence_of(c, job, receipt):
    return dict(external_effect_events(c.store, job['id'])[0])[receipt]


def test_a_sent_connector_change_is_named_until_read_back(case):
    line, job, receipt = sent_on_line(case)
    lines = case.service.preparations_service
    effects = lines.line_effects(line)
    assert (effects['sent'], effects['awaiting_connector_read_back']) == (1, 1)
    [item] = effects['awaiting_connector_read_back_items']
    assert item['receipt'] == str(receipt) and item['operation'] == 'apply_change'
    assert item['identity'] == 'synthetic' and item['read'] == 'none'
    assert effects['awaiting_read_back'] == 0, 'an API read-back is not owed for a connector change'
    text = lines.line_effects_text(effects)
    assert msg('lines.effects_receipt', receipt=receipt) in text and 'read_back' in text
    assert 'owner-account-a' not in text and 'unit-a' not in text, 'never an input or a value'


def test_a_read_back_reported_after_reading_the_connection_is_recorded_as_the_ais_report(case):
    line, job, receipt = sent_on_line(case)
    read_connection(case, job)
    report(case, job, line, receipt, 'applied')
    read_back = evidence_of(case, job, receipt)['read_back']
    assert read_back['outcome'] == 'applied' and read_back['attributed_to'] == 'owner-ai'
    assert isinstance(read_back['read_event'], int) and read_back['read_event'] > receipt
    assert 'read_digest' not in read_back, 'Still did not read the service answer itself'
    effects = case.service.preparations_service.line_effects(line)
    assert effects['awaiting_connector_read_back'] == 0
    assert case.service.preparations_service.line_effects_text(effects) == ''


def test_a_read_back_still_open_keeps_it_named(case):
    line, job, receipt = sent_on_line(case)
    read_connection(case, job)
    report(case, job, line, receipt, 'unknown')
    effects = case.service.preparations_service.line_effects(line)
    assert effects['awaiting_connector_read_back'] == 1
    assert effects['awaiting_connector_read_back_items'][0]['read'] == 'open'


def test_not_applied_is_named_in_the_account_as_reported(case):
    line, job, receipt = sent_on_line(case)
    read_connection(case, job)
    report(case, job, line, receipt, 'not-applied')
    lines = case.service.preparations_service
    effects = lines.line_effects(line)
    assert effects['awaiting_connector_read_back'] == 0 and effects['not_applied'] == 0, 'the receipt stays a send'
    [item] = effects['read_back_not_applied_reported']
    assert item['receipt'] == str(receipt)
    assert lines.line_effects_text(effects) == '', 'nothing open: a run is not told'
    account = lines.line_effects_text(effects, account=True)
    named = ' '.join([msg('lines.effects_receipt', receipt=receipt), 'apply_change', 'synthetic'])
    assert msg('lines.effects_read_back_not_applied_reported', items=named) in account
    assert msg('lines.effects_read_back_not_applied', items=named) not in account, 'never told as the service\'s answer'


def test_a_report_with_no_read_of_that_connection_records_nothing(case):
    line, job, receipt = sent_on_line(case)
    with pytest.raises(ToolError):
        report(case, job, line, receipt, 'applied')
    assert 'read_back' not in evidence_of(case, job, receipt)


def test_a_read_of_another_connection_records_nothing(case):
    line, job, receipt = sent_on_line(case)
    connect(case.store, case.service, operation='mutate')
    from personal_agent import owner_mcp
    owner_mcp.set_confirmed(case.store, 'codex', ['synthetic', 'other'])
    other = trust_record.connection('codex', 'owner-mcp', 'other')
    trust_record.set_operation(case.store, other, 'read_status', 'read')
    read_connection(case, job, 'mcp__other__read_status')
    with pytest.raises(ToolError):
        report(case, job, line, receipt, 'applied')
    assert 'read_back' not in evidence_of(case, job, receipt)


def test_a_read_made_before_the_send_records_nothing(case):
    line, row = line_with_check(case)
    connect(case.store, case.service, operation='mutate')
    trust_record.set_operation(case.store, trust_record.connection('codex', 'owner-mcp', 'synthetic'), 'read_status', 'read')
    job = woken(case, row)
    read_connection(case, job)
    # The read's outside-content mark would send the change to the owner for approval; this test isolates
    # the ordering rule only, so the mark is taken away and the change is decided as a plain one.
    with case.store.db() as db:
        db.execute('DELETE FROM work_intake_sources WHERE job_id=?', (job['id'],))
    assert call(case.service, job)['behavior'] == 'allow'
    observed(case.service, job)
    [(receipt, _)], [] = external_effect_events(case.store, job['id'])
    with pytest.raises(ToolError):
        report(case, job, line, receipt, 'applied')
    assert 'read_back' not in evidence_of(case, job, receipt)


def test_a_report_for_a_receipt_that_is_not_a_sent_connector_change_records_nothing(case):
    line, job, receipt = sent_on_line(case)
    read_connection(case, job)
    with case.store.db() as db:
        before = [row['detail'] for row in db.execute('SELECT detail FROM tool_events ORDER BY id')]
        newest = db.execute('SELECT MAX(id) FROM tool_events').fetchone()[0]
    for other in (newest, newest + 50, 'x'):
        with pytest.raises(ToolError):
            report(case, job, line, other, 'applied')
    with case.store.db() as db:
        after = [row['detail'] for row in db.execute('SELECT detail FROM tool_events ORDER BY id')]
    assert after[:len(before)] == before, 'no record changed'
    assert 'read_back' not in evidence_of(case, job, receipt)


def test_an_invalid_outcome_records_nothing(case):
    line, job, receipt = sent_on_line(case)
    read_connection(case, job)
    with pytest.raises(ToolError):
        report(case, job, line, receipt, 'done')
    assert 'read_back' not in evidence_of(case, job, receipt)


@pytest.mark.parametrize('outcome', ['applied', 'not-applied', 'unknown'])
def test_after_any_read_back_the_identical_change_is_still_refused_as_a_replay(case, outcome):
    line, job, receipt = sent_on_line(case)
    read_connection(case, job)
    report(case, job, line, receipt, outcome)
    evidence = evidence_of(case, job, receipt)
    assert 'resolution' not in evidence, 'a read-back never rewrites the send'
    assert call(case.service, job)['behavior'] == 'deny', 'a read-back never releases the replay hold'
    with case.store.db() as db:
        last = json.loads(db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='connector_permission' "
                                     "ORDER BY id DESC LIMIT 1", (job['id'],)).fetchone()['detail'])
    assert last['evidence']['refused'] == 'mutate'


def test_a_later_report_replaces_the_earlier_one(case):
    line, job, receipt = sent_on_line(case)
    read_connection(case, job)
    report(case, job, line, receipt, 'unknown')
    read_connection(case, job)
    report(case, job, line, receipt, 'applied')
    assert evidence_of(case, job, receipt)['read_back']['outcome'] == 'applied'


def test_a_later_run_of_the_line_can_read_back_an_earlier_runs_change(case):
    """The usual path: the next run of the line is told the receipt, reads the connection and reports."""
    from test_condition_checks_972 import finish
    line, job, receipt = sent_on_line(case)
    row = case.service.preparations_service.line_rows(line)[0]
    finish(case, row)
    case.clock[0] += 3600
    later = woken(case, row)
    assert later['id'] != job['id']
    read_connection(case, later)
    report(case, later, line, receipt, 'applied')
    effects = case.service.preparations_service.line_effects(line)
    assert effects['awaiting_connector_read_back'] == 0
    assert evidence_of(case, job, receipt)['read_back']['attributed_to'] == 'owner-ai'


def test_a_receipt_of_another_line_is_not_read_back(case):
    line, job, receipt = sent_on_line(case)
    other_line = case.service.lines.open(case.owner, task='Another watch', report_promise='Report')['line_id']
    # The owner's own Work names the other line: the receipt is not one of that line's changes.
    plain = case.store.job(case.store.enqueue('Did the change on the other watch go through?', 'plain-1361'))
    read_connection(case, plain)
    with pytest.raises(ToolError):
        report(case, plain, other_line, receipt, 'applied')
    # The line's run names the other line: it may not write another line's records.
    read_connection(case, job)
    with pytest.raises(ToolError):
        report(case, job, other_line, receipt, 'applied')
    assert 'read_back' not in evidence_of(case, job, receipt)
