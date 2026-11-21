"""#1101: candidate condition-check regressions since v0.8.0.

Candidates 1 (budget-refused fresh hit) and 2 (timeout clamped to the interval) are the
documented design (docs/design/condition-checks-972.en.md D11, lines 86, 150, 156, 239) and
are already pinned by tests/test_condition_checks_972.py. Candidate 3 is checked here: a check
wake bypasses the tick's held-line routing, which is safe only if no check row can be held.
"""
import json

from personal_agent import preparations as prep
from test_condition_checks_972 import case, current, prepared, tick  # noqa: F401 - fixture


def _look_back_line(c):
    line = c.service.lines.open(c.owner, task='Look-back follow-up', report_promise='Report in the bundle')['line_id']
    with c.store.db() as db:
        db.execute("UPDATE procedure_versions SET stats_json=? WHERE slot LIKE ? AND version=1",
                   (json.dumps({'change': 'opened', 'by': 'look-back'}), '%' + line))
    assert c.service.lines.provenance(line).get('by') == 'look-back'
    return line


def test_check_row_on_held_line_is_never_held_so_a_direct_wake_matches_the_tick(case):
    c = case
    spec = json.loads(prepared(c)['check_spec'])
    line = _look_back_line(c)
    service = c.service.preparations_service
    common = dict(kind=prep.KIND_PREPARE, due_at=c.clock[0], timezone='UTC', channel='telegram',
                  created_from=c.owner, state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_DREAM, line_id=line)

    # Control: the same unattended look-back row without a check window is held (bundled).
    plain = c.preps.create(goal='Plain look-back row', recurrence=None, **common)
    assert service.held_row(plain)

    # A check row is always a window row (preparations.create), and held_row excludes windows.
    checked = c.preps.create(goal='Check on a look-back line', recurrence=None,
                             window=(15, c.clock[0] + 3600, 48), check=spec, **common)
    assert checked['recurrence'] == prep.RECURRENCE_WINDOW and checked['check_spec']
    assert not service.held_row(checked)
    assert service.preparation_delivery(checked) != 'bundle'

    # Line upkeep does not reroute the check row to web or record a line_held receipt for it.
    service.hold_line_rows(c.clock[0])
    assert current(c, checked)['channel'] == 'telegram'
    with c.store.db() as db:
        assert not db.execute("SELECT 1 FROM tool_events WHERE tool='line_held' "
                              "AND json_extract(detail,'$.preparation_id')=?", (checked['id'],)).fetchone()
