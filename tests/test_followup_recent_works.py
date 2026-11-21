"""#1087: the follow-up judgment chooses among the recent Works of the chat.

Before #1087 a short message was judged against the one focused Work, and one
extra judgment (#1519) looked at the Work just before it for an open question.
When two topics interleave, a message about the topic two turns back could only
be none-of-these.  Now the judgment is given the last few Works of the chat and
chooses the Work and the relation together, as reply-to-link disentanglement does.

Evidence class: model-free.  A scripted DecisionEngine replaces only the
provider; ``AgentService.continuity_relation`` and the store run unchanged.
"""
import tempfile
import unittest
from pathlib import Path

from personal_agent.conversation_handoff import (FOLLOWUP_RECENT_WORKS, FOLLOWUP_REFERENCE, FOLLOWUP_RETRY,
                                                 ConversationFocus)
from personal_agent.decision import (NO_CANDIDATE, OUTCOME_DECIDED, FixtureDecisionEngine, SelectionDecision,
                                     fixture_confidence)
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore


class FollowupAmongRecentWorks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')

        def transport(url, body, headers=None, timeout=60):
            raise AssertionError('no model call is expected: ' + url)

        self.service = AgentService(self.store, ModelAdapter(transport), transport)
        self.answer = NO_CANDIDATE
        self.asked = []

        def choose(context, candidates, question):
            if context.purpose != 'conversation-followup':
                return None
            self.asked.append((context.facts, candidates))
            return SelectionDecision(OUTCOME_DECIDED, self.answer, candidates, fixture_confidence())

        self.service.use_decision_engine(FixtureDecisionEngine(choose=choose))
        self.keys = 0

    def work(self, message, reply, at, chat=7):
        self.keys += 1
        job = self.store.enqueue(message, f'k-{self.keys}', channel='telegram:x', chat_id=chat)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET created=?, status='succeeded' WHERE id=?", (at, job))
            if reply:
                db.execute("INSERT INTO messages(role,content,channel,created,job_id) VALUES ('assistant',?,?,?,?)",
                           (reply, 'telegram:x', at + 1, job))
        return job

    def focus(self, work):
        self.store.put(ConversationFocus.KEY, {'intent': 'conversation', 'at': self.service.conversation_focus.now(),
                                               'work_id': work})

    def relate(self, text, current):
        return self.service.continuity_relation(text, current_work_id=current)

    def interleaved(self):
        """Two topics alternate in one chat: A, B, A, B; the next short message is about A."""
        a1 = self.work('topic A: plan the first step', 'Here is the first step for A.', 1000)
        b1 = self.work('topic B: list three options', 'Three options for B.', 1060)
        a2 = self.work('topic A: and the second step?', 'The second step for A.', 1120)
        b2 = self.work('topic B: which option is cheapest?', 'Option two for B is cheapest.', 1180)
        self.focus(b2)
        current = self.work('the step after that?', None, 1240)
        return a1, b1, a2, b2, current

    def test_an_interleaved_topic_links_to_the_older_work_it_is_about(self):
        _a1, _b1, a2, b2, current = self.interleaved()
        self.answer = f'{FOLLOWUP_REFERENCE} w2'
        related = self.relate('the step after that?', current)
        self.assertEqual((related['relation'], related['previous']['id']), (FOLLOWUP_REFERENCE, a2))
        [(facts, candidates)] = self.asked
        # Most recent first; the current Work is never its own candidate.
        self.assertEqual(facts['w1_owner_message'], 'topic B: which option is cheapest?')
        self.assertEqual(facts['w2_owner_message'], 'topic A: and the second step?')
        self.assertEqual(facts['w4_reply'], 'Here is the first step for A.')
        self.assertNotIn('w5_owner_message', facts)
        self.assertIn(f'{FOLLOWUP_RETRY} w4', candidates)
        self.assertNotEqual(related['previous']['id'], b2)

    def test_an_answer_to_a_question_asked_before_an_interleaved_request_still_links(self):
        # The former #1519 shape: a question, an unrelated request in between, then the answer.
        asking = self.work('attend the workshop at the venue', 'When is it?', 1000)
        order = self.work('change the limit price', 'Waiting for your approval.', 1060)
        self.focus(order)
        current = self.work('today, it is happening now', None, 1090)
        self.answer = f'{FOLLOWUP_REFERENCE} w2'
        related = self.relate('today, it is happening now', current)
        self.assertEqual((related['relation'], related['previous']['id']), (FOLLOWUP_REFERENCE, asking))
        [(facts, _candidates)] = self.asked
        self.assertEqual((facts['w2_owner_message'], facts['w2_reply']), ('attend the workshop at the venue', 'When is it?'))

    def test_none_of_these_links_nothing(self):
        *_rest, current = self.interleaved()
        self.answer = NO_CANDIDATE
        self.assertIsNone(self.relate('what is the weather tomorrow?', current))
        self.assertEqual(len(self.asked), 1)

    def test_an_answer_naming_no_offered_work_links_nothing(self):
        *_rest, current = self.interleaved()
        self.answer = f'{FOLLOWUP_REFERENCE} w9'
        self.assertIsNone(self.relate('the step after that?', current))

    def test_the_window_is_bounded_and_stays_in_the_chat(self):
        for n in range(FOLLOWUP_RECENT_WORKS + 2):
            self.work(f'old request {n}', f'reply {n}', 1000 + n)
        self.work('another chat', 'elsewhere', 1050, chat=8)
        stale = self.work('too old', 'long ago', 1100 - ConversationFocus.TTL_SECONDS - 1)
        current = self.work('and that one?', None, 1100)
        self.relate('and that one?', current)
        [(facts, candidates)] = self.asked
        requests = [facts[f'w{n}_owner_message'] for n in range(1, FOLLOWUP_RECENT_WORKS + 1)]
        self.assertEqual(requests, [f'old request {n}' for n in range(FOLLOWUP_RECENT_WORKS + 1, 1, -1)])
        self.assertEqual(len(candidates), FOLLOWUP_RECENT_WORKS * 4)
        self.assertNotIn('another chat', repr(facts))
        self.assertNotIn('too old', repr(facts))
        self.assertTrue(stale)

    def test_the_focused_work_from_elsewhere_is_still_offered(self):
        elsewhere = self.store.enqueue('a request typed in the web view', 'k-web', channel='web')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET created=1000 WHERE id=?", (elsewhere,))
        self.focus(elsewhere)
        current = self.work('save that', None, 1100)
        self.answer = f'{FOLLOWUP_REFERENCE} w1'
        related = self.relate('save that', current)
        self.assertEqual(related['previous']['id'], elsewhere)

    def test_a_linked_candidate_names_the_candidate_it_was_linked_to(self):
        a1, _b1, a2, _b2, current = self.interleaved()
        self.store.link_work_relation(a2, a1, FOLLOWUP_REFERENCE)
        self.relate('the step after that?', current)
        [(facts, _candidates)] = self.asked
        self.assertEqual((facts['w2_linked_to'], facts['w1_linked_to']), ('w4', 'none'))

    def test_no_candidate_asks_nothing(self):
        current = self.work('hello', None, 1000)
        self.assertIsNone(self.relate('hello', current))
        self.assertEqual(self.asked, [])


if __name__ == '__main__':
    unittest.main()
