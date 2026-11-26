"""#1241: a refused preparation choice reaches the owner without internal ids.

Model-free: real ``QuickStore`` and ``SettingsOrchestrator``; the owner-facing refusal is the
``SettingsError`` text the owner is shown.
"""
from personal_agent import preparations as prep
from personal_agent.settings_orchestrator import SettingsError

from test_owner_settings_conversation import _Case


class RefusedChoicePlain(_Case):
    def refusal(self, setting, value):
        with self.assertRaises(SettingsError) as raised:
            self.settings._normalized('preparations', setting, value)
        return str(raised.exception)

    def scheduled(self, goal):
        return self.service.preparations.create(
            kind='prepare', goal=goal, due_at=self.clock[0] + 3600, timezone='UTC', recurrence=None,
            channel='web', created_from=None, state=prep.STATE_SCHEDULED,
            accepted_by=prep.ACCEPTED_OWNER_SETTINGS, origin=None, window=(600, self.clock[0] + 9000, 8),
            delivery_mode='when_needed')

    def test_a_finished_target_is_named_in_plain_words_without_ids(self):
        text = self.refusal('cancel', 'prep-finished-0001')
        self.assertNotIn('prep-finished-0001', text)
        self.assertIn('이미 끝났거나', text)
        self.assertIn('아무것도 바꾸지 않았어요', text)

    def test_the_other_open_work_is_listed_by_name_not_id(self):
        row = self.scheduled('Check material alpha')
        text = self.refusal('cancel', 'prep-finished-0001')
        self.assertNotIn(row['id'], text)
        self.assertNotIn('prep-finished-0001', text)
        self.assertIn('Check material alpha', text)
        self.assertIn('이미 끝났거나', text)

    def test_no_korean_particle_pair_is_left_in_the_generic_refusal(self):
        from personal_agent.messages import msg
        text = msg('settings_orchestrator.invalid_choice', label='예약 취소', listed='a, b')
        self.assertNotIn('은(는)', text)
