"""#1299: a line's account ends with the line's work; an early-close judgment sees the window's time.

Evidence class: model-free, fixture transports (as ``test_entrusted_lines_798``): scripted model,
FixtureDecisionEngine and a fake clock over the real QuickStore and ``AgentService``.
"""
import json

from personal_agent import preparations as prep
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from personal_agent.messages import msg

from test_entrusted_lines_798 import _LineCase
from test_preparations import _Case


def account_of(case, line_id):
    return next(row for row in case.service.preparations_service.line_rows(line_id)
                if row.get('origin') == prep.ORIGIN_LINE_ACCOUNT)


def first_user_text(bodies):
    users = [message for message in bodies[0]['messages'] if message.get('role') == 'user']
    content = users[-1]['content']
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


class AccountLifecycleTests(_LineCase):
    def test_the_last_work_ending_brings_the_closing_account_forward_and_ends_it(self):
        brief, _ = self.open_line(recurrence=None)
        line = brief['line_id']
        [work] = self.work_rows(line)
        self.assertEqual(account_of(self, line)['due_at'], self.now + 7 * 86400)
        self.run_line(work)
        self.assertNotIn(self.service.preparations.get(work['id'])['state'], prep.ACTIVE_STATES)
        account = account_of(self, line)
        self.assertEqual(account['state'], prep.STATE_SCHEDULED)
        self.assertLessEqual(account['due_at'], self.now)  # not a week later
        bodies = self.run_line(account, [{'content': '맡긴 확인을 마쳤어요. 더 보고하지 않아요.'}])
        self.assertIn(msg('lines.account_closing'), first_user_text(bodies))
        self.assertNotIn(self.service.preparations.get(account['id'])['state'], prep.ACTIVE_STATES)

    def test_a_line_with_work_left_keeps_its_weekly_account(self):
        brief, _ = self.open_line()  # daily work: always a next slot
        line = brief['line_id']
        [work] = self.work_rows(line)
        due = account_of(self, line)['due_at']
        self.run_line(work)
        account = account_of(self, line)
        self.assertEqual(account['due_at'], due)
        # A week later the daily work is due too; keep running until the account has settled.
        start = len(self.model_calls)
        self.script = [{'content': '이번 주 정리예요.'}] * 40
        self.now = account['due_at'] + 1
        for _ in range(6):
            self.tick()
            self.service.run_one()
            self.deliver_all()
            self.tick()
            if self.service.preparations.get(account['id'])['state'] == prep.STATE_SCHEDULED:
                break
        self.assertNotIn(msg('lines.account_closing'), json.dumps([b['messages'] for b in self.model_calls[start:]], ensure_ascii=False))
        after = self.service.preparations.get(account['id'])
        self.assertEqual(after['state'], prep.STATE_SCHEDULED)
        self.assertGreater(after['due_at'], self.now + 6 * 86400)


    def test_an_ended_account_starts_again_when_the_line_gets_work_again(self):
        brief, _ = self.open_line(recurrence=None)
        line = brief['line_id']
        [work] = self.work_rows(line)
        self.run_line(work)
        ended = account_of(self, line)
        self.run_line(ended, [{'content': '맡긴 확인을 마쳤어요.'}])
        self.assertNotIn(self.service.preparations.get(ended['id'])['state'], prep.ACTIVE_STATES)
        self.service.preparations.create(kind=prep.KIND_PREPARE, goal='다시 확인', due_at=self.now + 3600,
                                         timezone='Asia/Seoul', recurrence=None, channel='telegram',
                                         created_from='settings', state=prep.STATE_SCHEDULED,
                                         accepted_by=prep.ACCEPTED_OWNER_WORK, line_id=line)
        account = account_of(self, line)
        self.assertNotEqual(account['id'], ended['id'])
        self.assertEqual((account['state'], account['accepted_by']), (prep.STATE_SCHEDULED, ended['accepted_by']))

    def test_a_cancelled_account_is_not_started_again_by_new_work(self):
        brief, _ = self.open_line()
        line = brief['line_id']
        cancelled = account_of(self, line)
        self.service.preparations.cancel(cancelled['id'])
        self.service.preparations.create(kind=prep.KIND_PREPARE, goal='또 다른 확인', due_at=self.now + 3600,
                                         timezone='Asia/Seoul', recurrence=None, channel='telegram',
                                         created_from='settings', state=prep.STATE_SCHEDULED,
                                         accepted_by=prep.ACCEPTED_OWNER_WORK, line_id=line)
        self.assertEqual(account_of(self, line)['id'], cancelled['id'])


class WindowFactsTests(_Case):
    def test_checks_left_counts_the_window_slots_after_now(self):
        row = {'every_seconds': 43200, 'until_at': 1000 + 43200 * 5, 'due_at': 1000, 'run_count': 1,
               'max_runs': 6, 'window_terms': None}
        self.assertEqual(prep.window_checks_left(row, 1000), 5)
        self.assertEqual(prep.window_checks_left({**row, 'max_runs': 3}, 1000), 2)
        self.assertEqual(prep.window_checks_left({**row, 'until_at': 1000}, 1000), 0)

    def test_the_early_close_judgment_is_told_the_time_and_what_is_left_of_the_window(self):
        first = self.now + 60
        row = self.service.preparations.create(
            kind=prep.KIND_PREPARE, goal='예보 변화 확인', due_at=first, timezone='Asia/Seoul',
            recurrence=None, channel='telegram', created_from='settings',
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
            window=(43200, first + 43200 * 4, 5), delivery_mode=prep.DELIVERY_WHEN_NEEDED)
        seen = []

        def judge(context, proposition):
            if context.purpose == 'watch-window-closed':
                seen.append(dict(context.facts))
                self.assertIn('checks are still left', proposition)
                return BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence())
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None

        self.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
        self.now = first + 1
        self.assertTrue(self.tick())
        self.script = [{'content': '직전과 같아요. 기록만 남겨요.'}] * 2
        self.assertTrue(self.service.run_one())
        self.assertTrue(self.tick())
        [facts] = seen
        self.assertEqual(facts['checks_left'], '4')
        self.assertEqual(facts['window_last_check'], prep.local_text(first + 43200 * 4, 'Asia/Seoul', self.service.home_zone()))
        self.assertTrue(facts['judged_at'])
        updated = self.service.preparations.get(row['id'])
        self.assertEqual((updated['state'], updated['due_at']), (prep.STATE_SCHEDULED, first + 43200))
