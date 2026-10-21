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
from test_preparations import _Case

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


if __name__ == '__main__':
    unittest.main()
