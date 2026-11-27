"""#1273: a line run is told which sent changes have no read-back of their outcome yet.

The receipt broker, the status-read reconciliation and the judgment are Still's real local code with a
fixture transport and a fixture decision engine; the line's account reads the same effect ledger.
"""
import json
import time

import pytest

from personal_agent.agent_runtime import external_effect_events
from personal_agent.api_requests import ApiError, ApiRequests
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import FixtureDecisionEngine, OUTCOME_DECIDED, SelectionDecision, fixture_confidence
from personal_agent.messages import msg

from test_condition_checks_972 import case, wake_job  # noqa: F401 - fixture
from test_effect_continuation_1000 import OPEN, READ, claim, setup  # noqa: F401 - fixture
from test_line_effects_1252 import line_with_check
from test_refused_effects_1250 import woken


def read_back(setup, status):  # noqa: F811
    """A change the service answered as sent, then a status read the service answers with ``status``."""
    store, service, wire, _ = setup
    job, _, api = claim(setup, 'Place the order and check later whether it went through.')
    api.call(OPEN)
    [(receipt, sent)], [] = external_effect_events(store, job['id'])
    assert not sent.get('resolution') and sent.get('request_ref')

    def choose(context, candidates, _question):
        response = json.loads(context.facts['authoritative_service_response'])
        return SelectionDecision(OUTCOME_DECIDED, response['status'], candidates, fixture_confidence())

    wire.status = status
    budget = service.work_budget(job['id'])
    api = ApiRequests(store, job['id'], ledger=budget.ledger, transport=wire, clock=lambda: wire.now,
                      reconcile=ConversationJudgments(FixtureDecisionEngine(choose=choose)).effect_reconciled)
    result = api.call({**READ, 'reconcile_receipt': str(receipt), 'receipt_ref_field': '$.request_ref'})
    [(again, evidence)], [] = external_effect_events(store, job['id'])
    assert again == receipt
    return store, api, wire, result, evidence


@pytest.mark.parametrize('status', ['applied', 'not-applied', 'unknown'])
def test_a_status_read_of_a_sent_receipt_records_what_it_read_back(setup, status):  # noqa: F811
    store, api, wire, result, evidence = read_back(setup, status)
    assert result['provenance']['reconciled_receipt']
    assert evidence['read_back']['outcome'] == status and evidence['read_back']['read_digest']
    assert 'resolution' not in evidence, 'the receipt stays a send: a read-back never rewrites it'
    # Only a not-applied resolution of an unknown claim releases a replay (#1203); a read-back never does.
    with pytest.raises(ApiError) as held:
        api.call(OPEN)
    assert held.value.code == 'api_effect_replayed' and wire.sent.count('open') == 1


def test_a_read_for_another_request_records_nothing(setup):  # noqa: F811
    store, service, wire, _ = setup
    job, _, api = claim(setup, 'Place the order and check later whether it went through.')
    api.call(OPEN)
    [(receipt, _)], [] = external_effect_events(store, job['id'])
    wire.reference = 'another-request'
    budget = service.work_budget(job['id'])
    api = ApiRequests(store, job['id'], ledger=budget.ledger, transport=wire, clock=lambda: wire.now,
                      reconcile=lambda *a, **k: 'applied')
    result = api.call({**READ, 'reconcile_receipt': str(receipt), 'receipt_ref_field': '$.request_ref'})
    assert 'reconciled_receipt' not in result['provenance']
    [(_, evidence)], [] = external_effect_events(store, job['id'])
    assert 'read_back' not in evidence


def sent_on_line(c, read=None):
    """A line Work with one API change the service answered as sent, as the broker records it."""
    line, row = line_with_check(c)
    job = woken(c, row)
    evidence = {'engine': 'agentos', 'kind': 'api-slot', 'identity': 'fixture', 'operation': 'POST',
                'effect': 'mutate', 'host': 'receipt.example', 'input_digest': 'd' * 64, 'work_id': job['id'],
                'request_ref': 'ref-1', 'response_status': 200}
    if read:
        evidence['read_back'] = {'outcome': read, 'read_digest': 'r' * 64, 'at': time.time()}
    with c.store.db() as db:
        receipt = db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                             (job['id'], 'api_dispatch', 'observed',
                              json.dumps({'scope': 'api', 'evidence': evidence}), time.time())).lastrowid
    return line, receipt


def test_a_line_run_is_told_a_sent_change_with_no_read_back(case):
    line, receipt = sent_on_line(case)
    lines = case.service.preparations_service
    effects = lines.line_effects(line)
    assert (effects['sent'], effects['awaiting_read_back']) == (1, 1)
    [item] = effects['awaiting_read_back_items']
    assert item == {'receipt': str(receipt), 'operation': 'POST', 'host': 'receipt.example',
                    'identity': 'fixture', 'read': 'none'}
    text = lines.line_effects_text(effects)
    assert msg('lines.effects_receipt', receipt=receipt) in text and 'reconcile_receipt' in text


def test_a_read_back_still_open_keeps_it_named(case):
    line, _ = sent_on_line(case, read='unknown')
    effects = case.service.preparations_service.line_effects(line)
    assert effects['awaiting_read_back'] == 1 and effects['awaiting_read_back_items'][0]['read'] == 'open'


def test_a_settled_read_back_is_no_longer_owed(case):
    line, _ = sent_on_line(case, read='applied')
    lines = case.service.preparations_service
    effects = lines.line_effects(line)
    assert effects['awaiting_read_back'] == 0
    assert lines.line_effects_text(effects) == '', 'nothing open: a run is not told'
    assert 'reconcile_receipt' not in lines.line_effects_text(effects, account=True)


def test_a_read_back_of_not_applied_is_told_in_the_account_only(case):
    line, receipt = sent_on_line(case, read='not-applied')
    lines = case.service.preparations_service
    effects = lines.line_effects(line)
    assert effects['awaiting_read_back'] == 0 and lines.line_effects_text(effects) == ''
    account = lines.line_effects_text(effects, account=True)
    assert msg('lines.effects_read_back_not_applied',
               items=' '.join([msg('lines.effects_receipt', receipt=receipt), 'POST', 'receipt.example', 'fixture'])) in account
