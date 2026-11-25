"""Schedule tool refusals and redundant report intervals (#1214)."""
from personal_agent import preparations as prep
from personal_agent.agent_runtime import ToolError
from personal_agent.quickstart_service import UNATTENDED_APPROVAL_TEXT

from test_preparations import _Case


class ScheduleRefusals(_Case):
    def schedule_args(self, **extra):
        return {
            'kind': 'prepare',
            'goal': 'Check for changes',
            'offer': 'I’ll check for changes.',
            'due': self.due_iso(60),
            **extra,
        }

    def owner_schedule(self):
        job = self.store.job(self.receive('변경이 있는지 확인해 줘'))
        self.assertTrue(self.service.owner_typed_work(job))
        return self.service.preparation_scheduler(job, '')

    def test_owner_work_can_retry_without_the_day_plan_only_argument(self):
        schedule = self.owner_schedule()
        args = self.schedule_args()
        with self.assertRaises(ToolError) as refusal:
            schedule({**args, 'resume_line': True})
        self.assertEqual(refusal.exception.code, 'resume_line_day_plan_only')
        self.assertIn('resume_line은 하루 계획에서만 쓸 수 있어요', str(refusal.exception))
        self.assertIn('이 항목을 빼고 다시 예약해 주세요', str(refusal.exception))
        self.assertFalse(refusal.exception.ran)
        self.assertEqual(self.rows(), [])
        self.assertTrue(schedule(args)['scheduled'])

    def test_unattended_work_keeps_the_unattended_refusal(self):
        row = self.scheduled()
        self.now = row['due_at']
        self.assertTrue(self.tick())
        [job] = self.runs(row['id'])
        schedule = self.service.preparation_scheduler(job, '')
        with self.assertRaises(ToolError) as refusal:
            schedule(self.schedule_args(resume_line=True))
        self.assertEqual(refusal.exception.code, 'unattended_run')
        self.assertEqual(str(refusal.exception), UNATTENDED_APPROVAL_TEXT)
        self.assertFalse(refusal.exception.ran)

    def test_send_window_drops_a_report_interval_equal_to_the_check_interval(self):
        schedule = self.owner_schedule()
        result = schedule(self.schedule_args(
            delivery='send', every_minutes=10, report_every_minutes='10', until=self.due_iso(1860)))
        self.assertTrue(result['scheduled'])
        self.assertEqual(result['delivery'], 'send')
        self.assertEqual(result['every_minutes'], 10)
        self.assertNotIn('report_every_minutes', result)
        row = self.service.preparations.get(result['preparation_id'])
        self.assertNotIn('report_seconds', prep.stored_window_terms(row))

    def test_invalid_report_intervals_are_still_refused(self):
        schedule = self.owner_schedule()
        for delivery, minutes in [('send', 15), ('send', 20), ('when_needed', 15), ('keep', 10)]:
            with self.subTest(delivery=delivery, minutes=minutes):
                with self.assertRaises(ToolError) as refusal:
                    schedule(self.schedule_args(delivery=delivery, every_minutes=10,
                                                report_every_minutes=minutes, until=self.due_iso(1860)))
                self.assertEqual(refusal.exception.code, 'invalid_window')
                self.assertFalse(refusal.exception.ran)
        self.assertEqual(self.rows(), [])
