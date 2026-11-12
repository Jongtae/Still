"""#802: an entrusted line's reads appear in the information-use audit, and an account interval change takes effect.

Evidence class: model-free, fixture transports (the ``test_entrusted_lines_798`` harness).
"""
from personal_agent.information_use import work_information_use

from test_entrusted_lines_798 import RULE, _LineCase


def lines_used(store, work_id):
    return [row for row in work_information_use(store, work_id).get('used') or [] if row['category'] == 'lines']


class AuditTests(_LineCase):
    def test_a_runs_brief_and_an_explicit_read_are_listed_under_the_line_they_gave_the_ai(self):
        brief, _work = self.open_line(rules=[RULE])
        [row] = self.work_rows(brief['line_id'])
        self.run_line(row)
        [run] = self.runs(row['id'])
        [used] = lines_used(self.store, run['id'])
        self.assertEqual(used['items'], [f"맡은 일 {brief['line_id']} (버전 1)"])
        # The version the run read is still what reaches its next step.
        self.assertEqual(self.service.preparations_service.line_version_read(run['id']), (brief['line_id'], 1))

        self.line_call('관심 목록 요즘 어때?', action='read', line=brief['line_id'])
        reader = self.store.jobs()[0]['id']
        [used] = lines_used(self.store, reader)
        self.assertEqual(used['items'], [f"맡은 일 {brief['line_id']} (버전 1)"])

    def test_a_work_that_read_no_line_lists_none(self):
        brief, _work = self.open_line()
        self.script = [{'content': '국수 어때요?'}] * 2
        work = self.receive('오늘 저녁 뭐 먹지?')
        self.assertEqual(lines_used(self.store, work), [])


class AccountIntervalTests(_LineCase):
    def account(self, brief):
        return self.service.preparations_service.line_account_row(brief['line_id'])

    def test_a_shorter_interval_moves_the_pending_account_closer(self):
        brief, _work = self.open_line()
        opened = self.now
        self.line_call('정기 보고는 매일 해 줘', action='revise', line=brief['line_id'], expected_version='1',
                       account_every_days='1')
        row = self.account(brief)
        self.assertEqual((row['recurrence'], row['due_at']), ('RRULE:FREQ=DAILY;INTERVAL=1', opened + 86400))

    def test_a_longer_interval_moves_it_later_and_a_passed_point_is_due_soon(self):
        brief, _work = self.open_line()
        opened = self.now
        self.line_call('정기 보고는 2주마다 해 줘', action='revise', line=brief['line_id'], expected_version='1',
                       account_every_days='14')
        self.assertEqual(self.account(brief)['due_at'], opened + 14 * 86400)
        self.now = opened + 3 * 86400
        self.line_call('정기 보고는 이틀마다 해 줘', action='revise', line=brief['line_id'], expected_version='2',
                       account_every_days='2')
        row = self.account(brief)
        self.assertEqual((row['recurrence'], row['due_at']), ('RRULE:FREQ=DAILY;INTERVAL=2', self.now + 60))

    def test_undo_of_an_interval_change_restores_the_earlier_deadline(self):
        brief, _work = self.open_line()
        opened = self.now
        self.line_call('정기 보고는 매일 해 줘', action='revise', line=brief['line_id'], expected_version='1',
                       account_every_days='1')
        self.service.preparations_service.undo_line_version(brief['line_id'], 2)
        row = self.account(brief)
        self.assertEqual((row['recurrence'], row['due_at']), ('RRULE:FREQ=DAILY;INTERVAL=7', opened + 7 * 86400))

    def test_a_clamped_due_time_does_not_shift_a_later_change(self):
        brief, _work = self.open_line()
        opened = self.now
        self.now = opened + 3 * 86400
        self.line_call('정기 보고는 매일 해 줘', action='revise', line=brief['line_id'], expected_version='1',
                       account_every_days='1')
        self.assertEqual(self.account(brief)['due_at'], self.now + 60)
        self.service.preparations_service.undo_line_version(brief['line_id'], 2)
        self.assertEqual(self.account(brief)['due_at'], opened + 7 * 86400)

    def test_after_an_account_ran_its_period_starts_at_that_run(self):
        brief, _work = self.open_line()
        [work] = self.work_rows(brief['line_id'])
        self.service.cancel_preparation(work['id'])
        account = self.account(brief)
        self.run_line(account, [{'content': '지난주 정리입니다.'}])
        ran_at = account['due_at']
        self.line_call('정기 보고는 3일마다 해 줘', action='revise', line=brief['line_id'], expected_version='1',
                       account_every_days='3')
        self.assertEqual(self.account(brief)['due_at'], ran_at + 3 * 86400)
