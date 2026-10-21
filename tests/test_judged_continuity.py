"""RULES-OUT-01 (#173, slice B): how a message relates to earlier Work is judged in context.

Before this slice a correction or continuation was read from literal cue
tables ("아니", "말고", "계속", ...), and two lexical skips (a memory-save and a
file-save word check) kept some turns from the follow-up judgment at all.  Now
the ``followup_relation`` judgment decides, and it reads the focused Work's
request and reply, when each was made, and the kinds of what still waits for
the owner.

Evidence class: model-free, fixture transports (``test_preparations._Case``).
A scripted compatible-API model and a scripted DecisionEngine replace only the
providers; ``AgentService`` (Telegram ingress, ``run_one``,
``continuity_relation``) runs unchanged.  No live model is contacted.
"""
import unittest
from unittest.mock import patch

from personal_agent.conversation_handoff import FOLLOWUP_CORRECTION, FOLLOWUP_REFERENCE
from personal_agent.decision import (OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, SelectionDecision,
                                     fixture_confidence)
from personal_agent.owner_acts import OwnerActs
from test_preparations import CHAT, GENERATION, _Case

EARLIER = '내일 오후 3시에 팀 회의 잡아야 하는데 어떤 시간이 좋을까?'
EARLIER_REPLY = '오후 3시는 비어 있어요. 그 시간으로 정리해 둘까요?'


class JudgedContinuityTests(_Case):
    def relations(self, mapping):
        """A scripted engine: the follow-up relation from ``mapping`` (else none-of-these)."""
        def choose(context, candidates, question):
            if context.purpose == 'conversation-followup':
                return SelectionDecision(OUTCOME_DECIDED, mapping.get(context.facts['owner_message'], 'none-of-these'),
                                         candidates, fixture_confidence())
            return None

        def judge(context, proposition):
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None
        self.judge = FixtureDecisionEngine(judge=judge, choose=choose)
        self.service.use_decision_engine(self.judge)
        return self.judge

    def earlier_work(self):
        self.script = [{'content': EARLIER_REPLY}] * 2
        work = self.receive(EARLIER)
        self.assertEqual(self.store.job(work)['status'], 'succeeded')
        return work

    def relate(self, text):
        """Relate ``text`` (a queued Work, not yet run) to the focused Work, as ``_route_work`` does."""
        current = self.store.enqueue(text, f'k-{text}')
        return self.service.continuity_relation(text, current_work_id=current)

    def followup_facts(self):
        return [item[1].facts for item in self.judge.asked
                if item[0] == 'choose' and item[1].purpose == 'conversation-followup']

    def test_a_correction_without_a_cue_word_is_judged_a_correction(self):
        earlier = self.earlier_work()
        self.relations({'아니 4시로': FOLLOWUP_CORRECTION, '4시로 해 줘': FOLLOWUP_CORRECTION})
        for text in ('아니 4시로', '4시로 해 줘'):
            with self.subTest(text=text):
                related = self.relate(text)
                self.assertEqual((related['relation'], related['previous']['id']), (FOLLOWUP_CORRECTION, earlier))
        # The judgment read the focused Work in context, not the message alone.
        facts = self.followup_facts()[-1]
        self.assertEqual(facts['owner_message'], '4시로 해 줘')
        self.assertEqual(facts['previous_owner_message'], EARLIER)
        self.assertEqual(facts['previous_reply'], EARLIER_REPLY)
        self.assertEqual((facts['previous_request_at'], facts['previous_reply_at']), ('just now', 'just now'))
        self.assertEqual(facts['awaiting_owner'], 'nothing')

    def test_a_cue_word_in_an_unrelated_message_makes_no_relation(self):
        self.earlier_work()
        self.relations({})
        for text in ('아니 그건 그렇고 내일 날씨 알려줘', '말고 다른 얘기인데 점심 뭐 먹지?', '계속 비가 오네'):
            with self.subTest(text=text):
                self.assertIsNone(self.relate(text))
        self.assertEqual(len(self.followup_facts()), 3, 'each was judged, none was matched on a word')

    def test_a_memory_or_file_save_followup_is_judged_not_prefiltered(self):
        earlier = self.earlier_work()
        self.relations({'그 시간 기억해 둬': FOLLOWUP_REFERENCE, '방금 답을 파일로 저장해줘': FOLLOWUP_REFERENCE})
        for text in ('그 시간 기억해 둬', '방금 답을 파일로 저장해줘'):
            with self.subTest(text=text):
                related = self.relate(text)
                self.assertEqual((related['relation'], related['previous']['id']), (FOLLOWUP_REFERENCE, earlier))
        self.assertEqual([facts['owner_message'] for facts in self.followup_facts()],
                         ['그 시간 기억해 둬', '방금 답을 파일로 저장해줘'])

    def test_the_judgment_sees_the_kinds_of_open_items_never_their_content(self):
        self.earlier_work()
        self.relations({})
        shown = [{'kind': 'calendar-drafts', 'shown_text': 'PRIVATE-PREVIEW 치과 15:00'},
                 {'kind': 'action-approval', 'shown_text': 'PRIVATE-PROMPT 결제'}]
        with patch.object(OwnerActs, 'open_items', return_value=shown):
            self.relate('그거 괜찮아')
        [facts] = self.followup_facts()
        self.assertEqual(facts['awaiting_owner'], 'action-approval, calendar-drafts')
        self.assertNotIn('PRIVATE-', repr(facts))

    def test_earlier_texts_are_bounded_and_secret_redacted(self):
        secret = 'sk-live-SHOULD-NOT-REACH-THE-JUDGE-1234567890'
        self.script = [{'content': '알겠어요. ' + '가' * 2000}] * 2
        self.receive(f'이 키 {secret} 로 설정 정리해줘')
        self.relations({})
        self.relate('다시 해줘')
        [facts] = self.followup_facts()
        self.assertNotIn(secret, repr(facts))
        self.assertLessEqual(len(facts['previous_reply']), 600)

    def test_a_judged_correction_runs_the_owner_words_and_is_recorded(self):
        earlier = self.earlier_work()
        self.relations({'4시로 해 줘': FOLLOWUP_CORRECTION})
        self.script = [{'content': '4시로 바꿀게요.'}] * 2
        before = len(self.model_calls)
        work = self.receive('4시로 해 줘')
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual((job['relation_kind'], job['related_job_id']), (FOLLOWUP_CORRECTION, earlier))
        self.assertEqual(self.model_calls[before]['messages'][-1]['content'], '4시로 해 줘')


SAVED_CONTEXT_REQUEST = '저장한 기록 참고해서 회의 준비해줘'


class JudgedSavedContextTests(_Case):
    """The saved-context choice opens when the per-turn capability-need judgment selects it (#173 slice B).

    It replaces the 컨텍스트/저장한 기록 + 함께/참고/사용/포함 keyword trigger: no word
    list, no new judgment call, the same owner-only keyboard and callbacks.
    """

    def setUp(self):
        super().setUp()
        # A local route, so the chosen item may reach the worker once the owner picks it.
        self.service.save_model({'provider': 'compatible', 'endpoint': 'http://127.0.0.1:11434/v1',
                                 'model': 'test-model', 'api_key': ''})
        self.assertTrue(self.service.test_model()['ok'])
        self.service.set_context_telegram_policy({'approved': True})
        inbox = self.service.context_inbox()
        inbox.configure({'sources': {'text': True}})
        self.item = inbox.capture({'source_kind': 'text', 'content': 'PRIVATE-ROADMAP 회의 안건 세 가지'})
        self.model_calls.clear()

    def needs(self, mapping):
        def choose(context, candidates, question):
            if context.purpose == 'capability-need':
                return SelectionDecision(OUTCOME_DECIDED, mapping.get(context.facts['owner_message'], 'none-of-these'),
                                         candidates, fixture_confidence())
            return None

        def judge(context, proposition):
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None
        self.judge = FixtureDecisionEngine(judge=judge, choose=choose)
        self.service.use_decision_engine(self.judge)

    def asked(self, purpose):
        return [item for item in self.judge.asked if item[1].purpose == purpose]

    def choice_keyboards(self):
        return [body for body in self.sends()
                if any(str(button.get('callback_data', '')).startswith('p7x:')
                       for row in (body.get('reply_markup') or {}).get('inline_keyboard', []) for button in row)]

    def test_a_judged_saved_context_need_opens_the_choice_and_resumes_once(self):
        self.needs({SAVED_CONTEXT_REQUEST: 'saved-context'})
        work = self.receive(SAVED_CONTEXT_REQUEST)
        self.assertEqual(self.store.job(work)['status'], 'awaiting_context')
        self.assertEqual(self.model_calls, [], 'the worker waits for the owner\'s choice')
        [offer] = self.choice_keyboards()
        self.assertNotIn('PRIVATE-ROADMAP', str(offer), 'the labels hold no captured content')
        token = offer['reply_markup']['inline_keyboard'][0][0]['callback_data']
        choice = self.store.telegram_context_choice(token[4:])
        self.tap(token, choice['message_id'])
        self.assertEqual(self.store.job(work)['status'], 'queued')
        self.assertEqual(self.store.context_attachment(work)['event_ids'], [self.item['id']])
        self.script = [{'content': '회의 준비를 정리했어요.'}] * 2
        self.assertTrue(self.service.run_one())
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        sent = self.model_calls[0]['messages'][-1]['content']
        self.assertTrue(sent.startswith(SAVED_CONTEXT_REQUEST))
        self.assertIn('PRIVATE-ROADMAP', sent, 'the owner-chosen item reached the worker')
        self.assertEqual(len(self.choice_keyboards()), 1, 'the resumed Work is not offered the choice again')
        # No judgment call is added: one capability-need for the whole Work, as for any turn.
        self.assertEqual(len(self.asked('capability-need')), 1)

    def choose_the_item(self, text):
        self.needs({text: 'saved-context'})
        work = self.receive(text)
        [offer] = self.choice_keyboards()
        token = offer['reply_markup']['inline_keyboard'][0][0]['callback_data']
        self.tap(token, self.store.telegram_context_choice(token[4:])['message_id'])
        return work

    def test_the_context_sources_footer_comes_from_the_typed_record(self):
        # #173 slice C: an answer that itself says "컨텍스트:" no longer hides or changes the footer.
        from personal_agent.messages import msg
        work = self.choose_the_item(SAVED_CONTEXT_REQUEST)
        answer = '컨텍스트: 회의 안건을 참고했어요.'
        self.script = [{'content': answer}] * 2
        self.assertTrue(self.service.run_one())
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        head, footer = job['response'].split('\n\n' + msg('service.context_sources') + '\n', 1)
        self.assertEqual(head, answer)
        attachment = self.store.context_attachment(work)
        self.assertEqual(attachment['event_ids'], [self.item['id']])
        [line] = footer.splitlines()
        self.assertTrue(line.startswith('컨텍스트: text · ' + self.item['id'] + ' · '), line)
        self.assertNotIn('PRIVATE-ROADMAP', footer, 'the footer names the item, never its content')

    def test_an_unrelated_request_the_judgment_does_not_select_opens_nothing(self):
        self.needs({SAVED_CONTEXT_REQUEST: 'saved-context'})
        self.script = [{'content': '좋아요.'}] * 2
        work = self.receive('컨텍스트라는 단어를 함께 쓰는 문장 예시 알려줘')
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(self.choice_keyboards(), [])
        self.assertIsNone(self.store.context_attachment(work))
        self.assertEqual(len(self.asked('capability-need')), 1, 'one capability-need call per turn, as before')

    def test_the_explicit_context_command_still_opens_the_choice(self):
        self.needs({})
        self.update_id += 1
        self.service.ingest_update({'update_id': self.update_id, 'message': {
            'message_id': 777, 'from': {'id': CHAT}, 'chat': {'id': CHAT, 'type': 'private'},
            'text': '/context 회의 준비해줘', 'date': int(self.now)}}, GENERATION)
        job = self.store.jobs()[0]
        self.assertEqual((job['status'], job['message']), ('awaiting_context', '회의 준비해줘'))
        self.assertEqual(len(self.choice_keyboards()), 1)


if __name__ == '__main__':
    unittest.main()
