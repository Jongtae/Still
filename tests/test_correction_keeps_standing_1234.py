"""#1234: the owner's correction of scheduled work they accepted keeps the owner's standing.

Real SQLite and scheduler; only the judgment is a fixture.  The judgment says yes to a change only
when it is shown the accepted work and the changed place, so a test that passes without the framing
the fix adds cannot exist.
"""
import pytest

from personal_agent import preparations as prep
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from test_condition_check_orders_1194 import switch
from test_condition_checks_972 import case, current  # noqa: F401
from test_continuous_line_1218 import at, owner

ORIGINAL = 'Check the position every five minutes and sell before 15:20 only on a stop loss.'
CORRECTED = 'Check the position every five minutes and sell whenever the exit rule is met.'


def judged(c, *, change_yes=True):
    seen = []

    def judge(context, proposition):
        if context.purpose != 'explicit-preparation-request':
            return None
        text = context.facts['proposed_preparation']
        seen.append(text)
        if context.too_large():  # a production adapter refuses an oversized context unasked
            return None
        if 'Accepted work:' in text:
            verdict = change_yes and 'sell whenever the exit rule is met' in text and 'sell before 15:20' in text
        else:
            verdict = 'sell before 15:20' in text  # the owner's original request
        return BinaryDecision(OUTCOME_DECIDED, verdict, fixture_confidence())

    c.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
    return seen


def schedule(c, job, goal, replaces=None):
    result = c.service.preparation_scheduler(job, '')({
        'kind': 'prepare', 'goal': goal, 'offer': 'Keep the day trading going', 'due': at(c.clock[0] + 60),
        'line': 'none', **({'replaces': replaces} if replaces else {}),
    })
    return c.preps.get(result['preparation_id'])


def accepted_row(c, accepted_by):
    row = schedule(c, owner(c, 'Trade today, sell before 15:20 only on a stop loss', 'first'), ORIGINAL)
    assert row['accepted_by'] == prep.ACCEPTED_OWNER_REQUEST
    if accepted_by != row['accepted_by']:
        with c.store.db() as db:
            db.execute('UPDATE preparations SET accepted_by=? WHERE id=?', (accepted_by, row['id']))
    return c.preps.get(row['id'])


def run_of(c, row):
    c.clock[0] = row['due_at']
    assert c.preps.start(row, channel='web', chat_id=None, now=c.clock[0])
    return c.store.job(current(c, row)['last_run_job_id'])


@pytest.mark.parametrize('accepted_by', [prep.ACCEPTED_OWNER_REQUEST, prep.ACCEPTED_OWNER_BUTTON])
def test_owner_correction_of_accepted_work_keeps_payment_standing(case, accepted_by):
    c = case
    seen = judged(c)
    old = accepted_row(c, accepted_by)
    correction = owner(c, 'Change the rule so it keeps trading during the session', 'correction')
    new = schedule(c, correction, CORRECTED, replaces=old['id'])
    assert 'Accepted work:' in seen[-1] and '->' in seen[-1]
    assert new['accepted_by'] == prep.ACCEPTED_OWNER_REQUEST
    assert c.preps.get(old['id'])['state'] == prep.STATE_CANCELLED
    run = run_of(c, new)
    assert not c.service.self_set_run(run)
    switch(c, True)
    assert c.service._authorize_payment_autonomy(run, None, 'sell') is not None
    switch(c, False)
    assert c.service._authorize_payment_autonomy(run, None, 'sell') is None


def test_a_change_the_owner_did_not_ask_for_stays_self_set(case):
    c = case
    judged(c, change_yes=False)
    old = accepted_row(c, prep.ACCEPTED_OWNER_REQUEST)
    new = schedule(c, owner(c, 'How is the position doing?', 'question'), CORRECTED, replaces=old['id'])
    assert new['accepted_by'] == prep.ACCEPTED_OWNER_WORK
    run = run_of(c, new)
    switch(c, True)
    assert c.service.self_set_run(run)
    assert c.service._authorize_payment_autonomy(run, None, 'sell') is None


def test_replacing_self_set_work_is_judged_as_a_new_schedule(case):
    c = case
    seen = judged(c)
    old = accepted_row(c, prep.ACCEPTED_OWNER_WORK)
    new = schedule(c, owner(c, 'Change the rule so it keeps trading during the session', 'correction'),
                   CORRECTED, replaces=old['id'])
    assert 'Accepted work:' not in seen[-1]
    assert new['accepted_by'] == prep.ACCEPTED_OWNER_WORK


def test_a_long_correction_reaches_the_judgment_whole(case):
    c = case
    seen = judged(c)
    long_original = ORIGINAL + ' ' + 'a' * 3000
    row = schedule(c, owner(c, 'Trade today, sell before 15:20 only on a stop loss', 'first'), long_original)
    assert row['accepted_by'] == prep.ACCEPTED_OWNER_REQUEST
    new = schedule(c, owner(c, 'Change the rule so it keeps trading during the session', 'correction'),
                   CORRECTED + ' ' + 'b' * 3000, replaces=row['id'])
    assert len(seen[-1]) > 6000
    assert new['accepted_by'] == prep.ACCEPTED_OWNER_REQUEST


def test_replacing_self_set_work_the_owner_now_asks_for_takes_the_owners_standing(case):
    """#1259: the owner's own request for the work, judged as a new schedule, is the owner's standing."""
    c = case
    seen = judged(c)
    old = accepted_row(c, prep.ACCEPTED_OWNER_WORK)
    asked = owner(c, 'Keep trading today and sell before 15:20 only on a stop loss, ordering as the rule says',
                  'request')
    new = schedule(c, asked, ORIGINAL, replaces=old['id'])
    assert 'Accepted work:' not in seen[-1]
    assert new['accepted_by'] == prep.ACCEPTED_OWNER_REQUEST
    assert c.preps.get(old['id'])['state'] == prep.STATE_CANCELLED
    run = run_of(c, new)
    switch(c, True)
    assert not c.service.self_set_run(run)
    assert c.service._authorize_payment_autonomy(run, None, 'sell') is not None
