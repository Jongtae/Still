"""#961: a prepare needs the AI's one-line offer; without it nothing is scheduled."""
import json

from test_preparations import _Case, call, engine


class PrepareOfferRequiredTests(_Case):
    def schedule(self, **extra):
        self.service.use_decision_engine(engine(preparation=False))
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='prepare', goal='아침 요약 준비',
                                                             due=self.due_iso(86400), **extra)]},
                       {'content': '네.'}]
        self.receive('아침마다 요약해줘')
        results = [json.loads(m['content']) for m in self.model_calls[-1]['messages'] if m['role'] == 'tool']
        return results[-1]

    def test_a_prepare_without_an_offer_is_refused_before_anything_is_scheduled(self):
        for extra in ({}, {'offer': '  '}, {'title': '아침 요약'}):
            result = self.schedule(**extra)
            self.assertEqual((result['code'], result['retry'], result.get('ran')), ('offer_required', 'permanent', False))
            self.assertIn('offer', result['error'])
            self.assertEqual(self.rows(), [])

    def test_a_prepare_with_an_offer_is_scheduled(self):
        result = self.schedule(offer='평일 아침에 요약을 준비해 둬요.')
        self.assertNotEqual(result.get('code'), 'offer_required')
        self.assertEqual(len(self.rows()), 1)

    def test_a_reminder_needs_no_offer(self):
        self.service.use_decision_engine(engine(preparation=False))
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='reminder', goal='출발해요',
                                                             due=self.due_iso(3600))]}, {'content': '네.'}]
        self.receive('한 시간 뒤에 알려줘')
        self.assertEqual(len(self.rows()), 1)
