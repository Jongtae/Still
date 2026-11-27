"""#1271: a connector state change Still refused unsent is recorded, holds its Work and counts in the line.

The connector decision (``connector_permission``), the stream recording (``record_connector_calls``),
the Work's effect floor and the line's account are Still's real local code; no account or model call.
"""
import json
import tempfile
from pathlib import Path

from personal_agent import owner_mcp, trust_record
from personal_agent.agent_runtime import connector_change_refused
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from test_condition_checks_972 import case, wake_job  # noqa: F401 - fixture
from test_line_effects_1252 import line_with_check
from test_refused_effects_1250 import woken

ACTION = 'mcp__synthetic__apply_change'
INPUT = {'account': 'owner-account-a', 'item': 'unit-a', 'quantity': 2}


def connect(store, service, *, money=False, operation=None):
    service.set_ai_connections({'enabled': True})
    owner_mcp.set_confirmed(store, 'codex', ['synthetic'])
    target = trust_record.connection('codex', 'owner-mcp', 'synthetic')
    if money:
        trust_record.set_money(store, target, True)
    if operation:
        trust_record.set_operation(store, target, 'apply_change', operation)
    return target


def call(service, job, values=INPUT):
    return service.connector_permission(job, {'engine': 'codex', 'tool_name': ACTION, 'input': values})


def observed(service, job, values=INPUT):
    digest = service.connector_input_digest(ACTION, values)
    service.record_connector_calls(job['id'], [{'tool': ACTION, 'status': 'succeeded', 'input_digest': digest}],
                                   engine='codex')


def permissions(store, job):
    with store.db() as db:
        return [(row['status'], json.loads(row['detail'])['evidence']) for row in db.execute(
            "SELECT status,detail FROM tool_events WHERE job_id=? AND tool='connector_permission' ORDER BY id", (job['id'],))]


def plain():
    temp = tempfile.TemporaryDirectory()
    store = QuickStore(Path(temp.name) / 'state')
    service = AgentService(store)
    job = store.job(store.enqueue('Apply the changes and summarize the observed result', 'original'))
    return temp, store, service, job


def test_unattended_payment_refusal_holds_the_work_and_counts_in_the_line(case):
    line, row = line_with_check(case)
    connect(case.store, case.service, money=True)
    job = woken(case, row)
    assert call(case.service, job)['behavior'] == 'deny'
    (status, evidence), = permissions(case.store, job)
    assert status == 'denied' and evidence['refused'] == 'payment' and evidence['effect'] == 'none'
    assert evidence['input_digest'] == case.service.connector_input_digest(ACTION, INPUT)
    assert 'owner-account-a' not in json.dumps(evidence), 'never the input'
    assert connector_change_refused(case.store, job['id'])
    assert not case.service.goal_upgrade_allowed(job['id']), 'the change it served was never made'
    effects = case.service.preparations_service.line_effects(line)
    assert (effects['refused'], effects['sent'], effects['unconfirmed']) == (1, 0, 0)


def test_a_payment_waiting_for_the_owner_is_asked_not_refused():
    temp, store, service, job = plain()
    with temp:
        connect(store, service, money=True)
        service.connector_approval_note(job, {'tool_name': ACTION, 'input': INPUT, 'approval_note': 'Apply it.'})
        assert call(service, job)['behavior'] == 'deny'
        assert service._browser_request(job['id'])['state'] == 'requested'
        assert all('refused' not in evidence for _status, evidence in permissions(store, job))
        assert not connector_change_refused(store, job['id'])


def refuse_then_allow(store, service, job):
    """A payment refused unsent (no approval note: nothing asked), then the operation reviewed as a plain change."""
    target = connect(store, service, money=True)
    assert call(service, job)['behavior'] == 'deny'
    assert permissions(store, job)[-1][1]['refused'] == 'payment'
    assert connector_change_refused(store, job['id'])
    trust_record.set_money(store, target, False)
    trust_record.set_operation(store, target, 'apply_change', 'mutate')


def test_a_refused_change_made_later_with_the_same_input_clears():
    temp, store, service, job = plain()
    with temp:
        refuse_then_allow(store, service, job)
        assert call(service, job)['behavior'] == 'allow'
        observed(service, job)
        assert not connector_change_refused(store, job['id'])
        assert service.goal_upgrade_allowed(job['id'])


def test_another_input_made_does_not_make_up_for_a_refused_change():
    temp, store, service, job = plain()
    with temp:
        refuse_then_allow(store, service, job)
        other = dict(INPUT, quantity=3)
        assert call(service, job, other)['behavior'] == 'allow'
        observed(service, job, other)
        assert connector_change_refused(store, job['id'])
        assert not service.goal_upgrade_allowed(job['id'])


def test_a_refused_replay_of_a_change_already_made_is_not_owed():
    temp, store, service, job = plain()
    with temp:
        connect(store, service, operation='mutate')
        assert call(service, job)['behavior'] == 'allow'
        observed(service, job)
        assert call(service, job)['behavior'] == 'deny', 'a completed effect is not repeated by this Work'
        assert permissions(store, job)[-1][1]['refused'] == 'mutate'
        assert not connector_change_refused(store, job['id'])
        assert service.goal_upgrade_allowed(job['id'])


def test_reads_and_owner_refused_operations_record_no_refused_change():
    temp, store, service, job = plain()
    with temp:
        connect(store, service, operation='read')
        assert call(service, job)['behavior'] == 'allow'
        connect(store, service, operation='refused')
        assert call(service, job)['behavior'] == 'deny'
        assert all('refused' not in evidence for _status, evidence in permissions(store, job))
        assert not connector_change_refused(store, job['id'])


def test_a_change_waiting_for_the_owner_after_outside_content_is_asked_not_refused():
    """With outside content read, a plain change asks the owner; that wait is not a refusal (#1271 review)."""
    temp, store, service, job = plain()
    with temp:
        connect(store, service, operation='mutate')
        store.mark_outside_content(job['id'], {'category': 'web', 'operation': 'page_read'})
        service.connector_approval_note(job, {'tool_name': ACTION, 'input': INPUT, 'approval_note': 'Apply it.'})
        assert call(service, job)['behavior'] == 'deny'
        assert service.step_approval_pending(job['id']), 'the owner was asked'
        assert all('refused' not in evidence for _status, evidence in permissions(store, job))
        assert not connector_change_refused(store, job['id'])
