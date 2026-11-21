"""#1101: candidate regressions in the API broker's effect rechecks since v0.8.0.

Each test asserts the intended behaviour; it fails while the candidate defect is present.
"""
import pytest

from personal_agent import direct_message_surface as surface
from personal_agent.agent_runtime import WORK_STOP_KEY, EffectAncestryError, external_effect_events
from personal_agent.api_requests import ApiError

from test_effect_continuation_1000 import OPEN, READ, claim, interrupted_dispatch, setup  # noqa: F401


def test_status_read_sent_before_a_stop_still_returns_its_result(setup):
    """B: a sent status GET is a completed read; a stop during it must not turn it into 'not sent'."""
    store, _, wire, _ = setup
    job, _, api = claim(setup, 'Determine the outcome of the dispatch')
    wire.lost = True
    with pytest.raises(ApiError):
        interrupted_dispatch(store, job, api)
    _, [(receipt, _)] = external_effect_events(store, job['id'])
    wire.lost = False
    api.reconcile = lambda *args, **kwargs: 'applied'
    transport = api.transport

    def stop_while_reading(method, url, headers, body, timeout):
        # The owner stops the Work while the status request is on the wire.
        response = transport(method, url, headers, body, timeout)
        store.put(WORK_STOP_KEY, [job['id']])
        return response

    api.transport = stop_while_reading
    result = api.call({**READ, 'reconcile_receipt': str(receipt), 'receipt_ref_field': '$.request_ref'})
    assert wire.sent == ['open', 'state']
    assert result['data']['state'] == 'opened'
    # A stopped Work does not settle the claim; it stays unknown.
    assert 'reconciled_receipt' not in result['provenance']
    assert [event for event, _ in external_effect_events(store, job['id'])[1]] == [receipt]


def test_lineage_lost_after_reservation_is_refused_as_inactive_work(setup, monkeypatch):
    """C: the pre-dispatch recheck maps an unverifiable ancestry like ``_effect_guard`` does."""
    from personal_agent import agent_runtime

    store, _, wire, _ = setup
    job, _, api = claim(setup, 'Apply the change')
    reserve = surface.reserve_outbound

    def reserve_then_lose_lineage(*args):
        # The ancestry becomes unverifiable after the guard reserved the dispatch.
        def broken(db, work_id):
            raise EffectAncestryError('missing line-piece parent')
        monkeypatch.setattr(agent_runtime, 'effect_work_chain', broken)
        return reserve(*args)

    monkeypatch.setattr(surface, 'reserve_outbound', reserve_then_lose_lineage)
    with pytest.raises(ApiError) as error:
        api.call(OPEN)
    assert error.value.code == 'api_work_inactive'
    assert not wire.sent
    monkeypatch.undo()
    assert external_effect_events(store, job['id']) == ([], [])
