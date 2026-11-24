"""#1203 (owner decision 2026-11-25): a request the service itself reported not applied may be sent again unchanged.

RFC 9110 §9.2.2 and payment-API practice treat a request known not to have been applied as safe to
retry.  Only a receipt that an authoritative status read reconciled as ``not-applied`` releases its
replay hold; an unknown or applied receipt still holds the identical request.
"""
import json

import pytest

from personal_agent.agent_runtime import external_effect_events
from personal_agent.api_requests import ApiError, ApiRequests
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import FixtureDecisionEngine, OUTCOME_DECIDED, SelectionDecision, fixture_confidence

from test_effect_continuation_1000 import OPEN, READ, claim, interrupted_dispatch, setup  # noqa: F401


def reconciled(setup, status):  # noqa: F811
    """A lost dispatch, then a status read the service answers with ``status``; returns the broker."""
    store, service, wire, _ = setup
    job, _, api = claim(setup, 'Place the order and check that it went through.')
    wire.lost, wire.status = True, status
    with pytest.raises(ApiError):
        interrupted_dispatch(store, job, api)
    _, [(receipt, _)] = external_effect_events(store, job['id'])

    def choose(context, candidates, _question):
        response = json.loads(context.facts['authoritative_service_response'])
        return SelectionDecision(OUTCOME_DECIDED, response['status'], candidates, fixture_confidence())

    budget = service.work_budget(job['id'])
    api = ApiRequests(store, job['id'], ledger=budget.ledger, transport=wire, clock=lambda: wire.now,
                      reconcile=ConversationJudgments(FixtureDecisionEngine(choose=choose)).effect_reconciled)
    api.call({**READ, 'reconcile_receipt': str(receipt), 'receipt_ref_field': '$.request_ref'})
    wire.lost = False
    return store, job, wire, api


def test_the_same_request_goes_again_after_the_service_reported_it_not_applied(setup):  # noqa: F811
    store, job, wire, api = reconciled(setup, 'not-applied')
    [(_, receipt)], [] = external_effect_events(store, job['id'])
    assert receipt['resolution'] == 'not-applied' and receipt.get('read_digest')
    first_key = wire.first_ref
    api.call(OPEN)
    assert wire.sent == ['open', 'state', 'open'] and wire.state == 'opened'
    # A service that keeps each key's result would return the old failure for the old key (#1203 review).
    assert wire.first_ref and wire.first_ref != first_key
    observed, _ = external_effect_events(store, job['id'])
    assert [row.get('request_ref') for _, row in observed] == [first_key, wire.first_ref]
    # The retry is a dispatch of its own: once it is observed, the identical request is held again.
    with pytest.raises(ApiError) as again:
        api.call(OPEN)
    assert again.value.code == 'api_effect_replayed' and wire.sent.count('open') == 2


@pytest.mark.parametrize('status', ['applied', 'pending'])
def test_an_applied_or_unresolved_receipt_still_holds_the_same_request(setup, status):  # noqa: F811
    store, job, wire, api = reconciled(setup, status)
    with pytest.raises(ApiError) as held:
        api.call(OPEN)
    assert held.value.code in ('api_effect_replayed', 'effect_unknown')
    assert wire.sent.count('open') == 1
