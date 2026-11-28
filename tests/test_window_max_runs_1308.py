"""#1308: ``every_minutes`` with ``max_runs`` and no ``until`` is a bounded window.

Real SQLite and scheduler (as ``test_run_time_and_schedule_1269_1270``).
"""
import pytest

from personal_agent import preparations as prep
from personal_agent.agent_runtime import ToolError
from test_condition_checks_972 import case, current  # noqa: F401
from test_continuous_line_1218 import at, owner


def schedule(c, **extra):
    job = owner(c, 'Every five minutes, three runs only', 'setup')
    return c.service.preparation_scheduler(job, '')({
        'kind': 'prepare', 'goal': 'Check it', 'offer': 'Check it', 'due': at(c.clock[0] + 60),
        'every_minutes': 5, 'timezone': 'UTC', **extra,
    })


@pytest.mark.parametrize('extra, runs', [({}, 3), ({}, 1), ({'recurrence': 'daily'}, 3), ({'recurrence': 'daily'}, 1)])
def test_max_runs_alone_schedules_exactly_that_many_runs(case, extra, runs):
    c = case
    row = c.preps.get(schedule(c, max_runs=runs, **extra)['preparation_id'])
    assert row['max_runs'] == runs
    assert row['until_at'] == row['due_at'] + 300 * (runs - 1)
    # The scheduler's own slot function yields the remaining runs of this window and no more.
    slots, slot = [row['due_at']], row['due_at']
    for count in range(1, runs + 2):
        slot = prep.next_window_slot({**row, 'due_at': slot, 'run_count': count}, slot)
        if slot is None:
            break
        slots.append(slot)
    assert slots == [row['due_at'] + 300 * index for index in range(runs)]


@pytest.mark.parametrize('extra, code', [
    ({}, 'invalid_window'),
    ({'max_runs': 0}, 'invalid_max_runs'),
    ({'max_runs': 'many'}, 'invalid_max_runs'),
    ({'max_runs': 10 ** 9}, 'invalid_max_runs'),
])
def test_a_window_without_a_usable_bound_is_refused(case, extra, code):
    with pytest.raises(ToolError) as caught:
        schedule(case, **extra)
    assert caught.value.code == code
