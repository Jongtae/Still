"""#1269 / #1270: a scheduled run's time and the schedule it belongs to reach the plan and the goal judgment.

Real SQLite and scheduler (as ``test_visible_standing_1262``); the plan and outcome judgments are
fixtures that record what they were asked.  #1269: the plan's ``budget`` names when the next run of
the schedule starts, from the scheduler's own slot function.  #1270: the outcome judgment reads
``schedule``, Still's record of the schedule this Work is a run of or set up, so a later run's part is
a fact it can tell apart from this Work's own gap.
"""
import time

import pytest

from personal_agent import preparations as prep
from personal_agent.agent_runtime import work_schedule_text
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.messages import msg
from personal_agent.orchestrator import Orchestration
from test_condition_checks_972 import case, current  # noqa: F401
from test_continuous_line_1218 import at, owner


def window_row(c, job):
    result = c.service.preparation_scheduler(job, '')({
        'kind': 'prepare', 'goal': 'Check the flow and plan the next five minutes', 'offer': 'Trade the session',
        'due': at(c.clock[0] + 60), 'every_minutes': 5, 'until': at(c.clock[0] + 60 + 3600), 'timezone': 'UTC',
    })
    return c.preps.get(result['preparation_id'])


def run_of(c, row):
    c.clock[0] = row['due_at']
    assert c.preps.start(row, channel='web', chat_id=None, now=c.clock[0])
    return c.store.job(current(c, row)['last_run_job_id'])


def test_a_run_knows_when_its_schedule_starts_the_next_run(case):
    c = case
    row = window_row(c, owner(c, 'Every five minutes until the close, plan and order', 'setup'))
    run = run_of(c, row)
    start = c.service.preparation_next_start(run)
    assert start['kind'] == 'run'
    # The scheduler's own next slot: one period after this run's slot.
    assert start['at'] == row['due_at'] + 300
    assert start['local'] == prep.local_text(row['due_at'] + 300, 'UTC')
    # A Work no preparation started has no run time.
    assert c.service.preparation_next_start(c.store.job(c.owner)) is None


def test_the_plan_budget_names_the_next_start():
    later = {'at': time.time() + 240, 'kind': 'run', 'local': '09:05'}
    with_time = Orchestration(None, None, request='plan', next_start=later)
    text = with_time._budget_text()
    assert 'the next run of the schedule this Work is a run of starts in' in text and '(at 09:05)' in text
    check = Orchestration(None, None, request='plan', next_start={**later, 'kind': 'check'})
    assert 'the next condition check of the schedule' in check._budget_text()
    # Past or absent: nothing is said.
    assert 'next' not in Orchestration(None, None, request='plan', next_start={**later, 'at': time.time() - 5})._budget_text()
    assert 'next' not in Orchestration(None, None, request='plan')._budget_text()


def test_the_goal_judgment_reads_the_run_and_the_schedule_a_work_set_up(case):
    c = case
    setup = owner(c, 'Every five minutes until the close, plan and order', 'setup')
    row = window_row(c, setup)
    told = c.service.work_schedule_fact(setup)
    assert row['id'] in told and row['state'] in told
    assert told == msg('preparations.schedule_set_up', id=row['id'], state=row['state'],
                       next=prep.local_text(row['due_at'], 'UTC'), end=prep.local_text(row['until_at'], 'UTC'))
    run = run_of(c, row)
    fact = c.service.work_schedule_fact(run)
    assert fact == msg('preparations.schedule_run', id=row['id'], run=1,
                       next=prep.local_text(row['due_at'] + 300, 'UTC'), end=prep.local_text(row['until_at'], 'UTC'))
    # A Work with no schedule gets no fact.
    assert c.service.work_schedule_fact(c.store.job(c.owner)) == ''


class _Judgments:
    def __init__(self):
        self.asked = []

    def goal_reached(self, request, observations, failed='', **kwargs):
        self.asked.append(kwargs)
        return None


@pytest.mark.parametrize('reader, sent', [(lambda: 'This Work is run 3 of preparation p.', True),
                                          (lambda: '', False), (None, False),
                                          (lambda: (_ for _ in ()).throw(RuntimeError('db')), False)])
def test_the_orchestrated_outcome_judgment_receives_the_schedule(reader, sent):
    judgments = _Judgments()
    orchestration = Orchestration(judgments, None, request='plan the next five minutes', schedule=reader)
    orchestration.evaluate_answer('Planned.', 'observations', final=True)
    assert ('schedule' in judgments.asked[0]) is sent
    if sent:
        assert judgments.asked[0]['schedule'] == 'This Work is run 3 of preparation p.'


def test_the_judgment_puts_the_schedule_fact_in_front_of_the_model():
    seen = []

    def judge(context, proposition):
        seen.append(dict(context.facts))
        return None

    judgments = ConversationJudgments(FixtureDecisionEngine(judge=judge))
    judgments.goal_reached('plan', 'obs', answer='Planned.', schedule='This Work set up preparation p.')
    judgments.goal_reached('plan', 'obs', answer='Planned.')
    assert seen[0]['schedule'] == 'This Work set up preparation p.'
    assert 'schedule' not in seen[1]


def test_the_direct_route_reads_the_schedule_bound_to_its_work():
    class Bound:
        work_schedule = staticmethod(lambda: 'This Work is run 2 of preparation p.')

    class Failing:
        work_schedule = staticmethod(lambda: 1 / 0)

    assert work_schedule_text(Bound()) == 'This Work is run 2 of preparation p.'
    assert work_schedule_text(Failing()) == '' and work_schedule_text(object()) == ''


@pytest.mark.parametrize('returns_to, expected', [(prep.STATE_SCHEDULED, 'due'), (prep.STATE_PAUSED, None)])
def test_an_owners_run_now_keeps_the_upcoming_slot_as_its_next_start(case, returns_to, expected):
    # Codex review on #1281: an extra run settles back to the preserved slot (#575), not the one after it.
    c = case
    row = window_row(c, owner(c, 'Every five minutes until the close, plan and order', 'setup'))
    if returns_to == prep.STATE_PAUSED:
        with c.store.db() as db:
            db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_PAUSED, row['id']))
        row = current(c, row)
    c.clock[0] = row['due_at'] - 30
    assert c.preps.start(row, channel='web', chat_id=None, now=c.clock[0], immediate=True)
    run = c.store.job(current(c, row)['last_run_job_id'])
    assert current(c, row)['run_return_state'] == returns_to
    start = c.service.preparation_next_start(run)
    if expected is None:
        assert start is None
    else:
        assert start['at'] == row['due_at']
        assert prep.local_text(row['due_at'], 'UTC') in c.service.work_schedule_fact(run)
