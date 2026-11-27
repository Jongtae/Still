"""#1269 and #1270: a schedule's time and later runs reach the plan and the goal judgment as facts.

#1269: a scheduled run's plan reads how long it has before the next run is due, and the run's
brief says it, so the plan can choose a model and effort that finish in time (the decision model
chooses; Still adds no effort rule).  #1270: the outcome judgment of a scheduled run, and of a Work
that set up a schedule, reads that schedule, so a later run's part is not counted as this Work's gap.
The fixture plan and judge below decide only from those facts, so each test fails without them.
"""
import unittest

from personal_agent import preparations as prep
from personal_agent.decision import BinaryDecision, OUTCOME_DECIDED
from personal_agent.messages import msg
from personal_agent.orchestrator import PURPOSE

from test_orchestrator import Harness, decided, fixture_confidence, plan


class ScheduleFacts(Harness):
    def setUp(self):
        super().setUp()
        self.service.preparations.clock = self.clock
        self.service.decision_routes.bundled_efforts = lambda engine: (
            {'gpt-5.6-luna': ['low', 'medium', 'high']} if engine == 'codex' else {})

    def scheduled_run(self, every=300):
        """Start the first run of an owner-accepted watch that runs every ``every`` seconds for an hour."""
        start = self.clock.now
        row = self.service.preparations.create(kind='prepare', goal='Check the price and act when the condition holds',
                                               due_at=start, timezone='Asia/Seoul', recurrence=None, channel='web',
                                               created_from='settings', state=prep.STATE_SCHEDULED,
                                               accepted_by=prep.ACCEPTED_OWNER_SETTINGS, window=(every, start + 3600, 12))
        work = self.service.preparations.start(self.service.preparations.get(row['id']), channel='web', chat_id=None,
                                               now=start)
        return row, work

    def judged(self, decide):
        """The Judgment AI: plans decide from the plan facts, goal judgments from the judgment facts."""
        self.asked_goals = []

        def structured(context, question, schema):
            if context.purpose != PURPOSE:
                return None
            self.asked_plans.append((context, question, schema))
            seconds = context.facts.get('run_time_available')
            # A planner that weighs the run's time: low effort and a stated limit within a short one.
            if seconds and int(seconds.split()[0]) < 600:
                return decided(plan('codex', f'Finish within {seconds.split()[0]} seconds.', model='gpt-5.6-luna', effort='low'))
            return decided(plan('codex', 'Take care.', model='gpt-5.6-luna', effort='high'))

        def judge(context, proposition):
            if context.purpose != 'goal-reached':
                return None
            self.asked_goals.append(context)
            return BinaryDecision(OUTCOME_DECIDED, decide(context.facts), fixture_confidence())

        from personal_agent.decision import FixtureDecisionEngine
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured, judge=judge))

    def test_a_scheduled_runs_plan_reads_its_time_until_the_next_run_and_its_brief_says_it(self):
        row, work = self.scheduled_run(every=300)
        self.judged(lambda facts: True)
        self.assertTrue(self.service.run_one())
        context = self.asked_plans[0][0]
        self.assertEqual(context.facts['run_time_available'].split()[0], '300')
        turn = self.engine.turns[0]
        self.assertEqual(turn['effort'], 'low', 'the plan chose its effort from the run time')
        self.assertIn('Finish within 300 seconds.', turn['prompt'])
        due = prep.local_text(row['due_at'] + 300, 'Asia/Seoul')
        self.assertIn(msg('preparations.run_finish_before', due=due), turn['prompt'])
        # The plan digest leaves the moving seconds out, as it leaves the budget's seconds out.
        orchestration = self.service.work_orchestration(self.store.job(work), 'x', [], {}, None)
        catalogue = orchestration.catalogue.available()
        before = orchestration.plan_digest(catalogue)
        self.clock.now += 30
        self.assertEqual(orchestration.plan_digest(catalogue), before)

    def test_an_owner_work_plans_without_a_run_time(self):
        self.judged(lambda facts: True)
        self.run_work('Look it up')
        self.assertNotIn('run_time_available', self.asked_plans[0][0].facts)
        self.assertEqual(self.engine.turns[0]['effort'], 'high')

    def test_a_scheduled_runs_goal_judgment_reads_the_schedule(self):
        row, work = self.scheduled_run()
        # A judge that counts the later checks as missing unless it is told they belong to the schedule.
        self.judged(lambda facts: row['id'] in facts.get('schedule', ''))
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(work)['status'], 'succeeded')
        self.assertEqual(len(self.engine.turns), 1, 'no re-delegation for the later runs')
        self.assertIn(msg('preparations.run_next', due=prep.local_text(row['due_at'] + 300, 'Asia/Seoul')),
                      self.asked_goals[0].facts['schedule'])

    def test_a_work_that_set_up_a_schedule_is_judged_with_it(self):
        created = []

        def schedule(tools):
            # What schedule_preparation records: a row this Work created and the owner's request accepted.
            if created:
                return
            job_id = tools.capabilities.job_id
            created.append(self.service.preparations.create(
                kind='prepare', goal='Check every 5 minutes until 15:20', due_at=self.clock.now + 300,
                timezone='Asia/Seoul', recurrence=None, channel='web', created_from=job_id,
                state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_REQUEST,
                window=(300, self.clock.now + 3600, 12)))
        self.engine.before = schedule
        self.judged(lambda facts: bool(created) and created[0]['id'] in facts.get('schedule', ''))
        job, row = self.run_work('Check it every 5 minutes until 15:20 and act when it holds')
        self.assertEqual(row['status'], 'succeeded')
        self.assertEqual(len(self.engine.turns), 1, 'no re-delegation for checks a later run makes')
        self.assertIn('This Work set up these schedules', self.asked_goals[0].facts['schedule'])

    def test_a_work_that_set_up_nothing_is_judged_without_a_schedule(self):
        self.judged(lambda facts: True)
        self.run_work('Look it up')
        self.assertNotIn('schedule', self.asked_goals[0].facts)


if __name__ == '__main__':
    unittest.main()
