"""#1101 candidate 1: an outcome-unknown dispatch must not block the owner's own local controls.

The pre-execution fence added in #1020 holds further external mutations while a dispatch's outcome is
unknown.  Saving a note or forgetting a remembered fact changes nothing outside Still, so it is not a
mutation that fence protects (C17): those calls still run.
"""
import pytest

from personal_agent.agent_runtime import MEMORY_OWNER, Capabilities, external_effect_events
from personal_agent.api_requests import ApiError

from test_effect_continuation_1000 import claim, interrupted_dispatch, setup  # noqa: F401


@pytest.fixture
def unknown(setup):  # noqa: F811
    store, _, wire, _ = setup
    job, budget, api = claim(setup, 'Apply a change and check the current state.')
    wire.lost = True
    with pytest.raises(ApiError):
        interrupted_dispatch(store, job, api)
    _, pending = external_effect_events(store, job['id'])
    assert pending
    return store, Capabilities(store, None, {}, '', job['id'], lambda *args: None, budget=budget)


def test_note_saves_while_a_dispatch_is_unknown(unknown):
    _, caps = unknown
    assert caps.execute('save_note', {'content': 'Remember to check the order.'})


def test_forget_runs_while_a_dispatch_is_unknown(unknown):
    store, caps = unknown
    row = store.save_memory('city', 'Seoul', MEMORY_OWNER, work_id='w-source')
    forgot = caps.execute('forget_record', {'action': 'forget', 'ref': 'memory:' + row['id']})
    assert forgot['receipt']
