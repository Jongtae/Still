"""#1262: a run and the owner's Work are told whether a scheduled row may pay, by the payment gate's own rule.

Real SQLite, scheduler and payment gate (as ``test_correction_keeps_standing_1234``); only the
judgment is a fixture.  Each case checks the told standing against the gate's actual decision.
"""
import pytest

from personal_agent import preparations as prep
from personal_agent.messages import msg
from test_condition_check_orders_1194 import switch
from test_condition_checks_972 import case, current  # noqa: F401
from test_correction_keeps_standing_1234 import accepted_row, judged, run_of


def told(c, run):
    return c.service.preparations_service.preparation_run_fact(run)


@pytest.mark.parametrize(('accepted_by', 'autonomy', 'key', 'pays'), [
    (prep.ACCEPTED_OWNER_REQUEST, True, 'payment_autonomy.run_allowed', True),
    (prep.ACCEPTED_OWNER_REQUEST, False, 'payment_autonomy.run_approval', False),
    (prep.ACCEPTED_OWNER_WORK, True, 'payment_autonomy.run_self_set', False),
    (prep.ACCEPTED_OWNER_WORK, False, 'payment_autonomy.run_self_set', False),
])
def test_a_run_is_told_the_standing_the_payment_gate_applies(case, accepted_by, autonomy, key, pays):
    c = case
    judged(c)
    row = accepted_row(c, accepted_by)
    run = run_of(c, row)
    switch(c, autonomy)
    fact = told(c, run)
    assert msg(key) in fact
    assert sum(msg(other) in fact for other in ('payment_autonomy.run_allowed', 'payment_autonomy.run_approval',
                                                 'payment_autonomy.run_self_set', 'payment_autonomy.run_standing_ended')) == 1
    assert (c.service._authorize_payment_autonomy(run, None, 'buy') is not None) is pays


def test_an_ended_standing_is_told_as_ended(case):
    c = case
    judged(c)
    row = accepted_row(c, prep.ACCEPTED_OWNER_REQUEST)
    run = run_of(c, row)
    with c.store.db() as db:
        db.execute('UPDATE preparations SET standing_end=? WHERE id=?', (c.clock[0] - 1, row['id']))
    switch(c, True)
    assert msg('payment_autonomy.run_standing_ended') in told(c, run)
    assert c.service._authorize_payment_autonomy(run, None, 'buy') is None


def test_the_owners_work_reads_each_rows_standing_in_settings(case):
    c = case
    judged(c)
    asked = accepted_row(c, prep.ACCEPTED_OWNER_REQUEST)
    switch(c, True)
    listing = c.service.settings_orchestrator.read('local-owner', 'preparations')
    text = str(listing)
    assert msg('payment_autonomy.run_allowed') in text
    with c.store.db() as db:
        db.execute('UPDATE preparations SET accepted_by=? WHERE id=?', (prep.ACCEPTED_OWNER_WORK, asked['id']))
    text = str(c.service.settings_orchestrator.read('local-owner', 'preparations'))
    assert msg('payment_autonomy.run_self_set') in text and msg('payment_autonomy.run_allowed') not in text
