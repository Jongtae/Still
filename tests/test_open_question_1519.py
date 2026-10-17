"""#1519: an answer reaches the question asked before an interleaved request.

Live 2026-10-14: "언제인가요?" was asked, an order request came in between, and
"오늘 지금 하고 있어" ran as its own Work read as being about the order.
"""
import tempfile
import unittest
from pathlib import Path

from personal_agent.conversation_handoff import JUDGMENT_NO, JUDGMENT_YES, Judgment
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import OPEN_QUESTION_SECONDS, AgentService
from personal_agent.quickstart_store import QuickStore


class OpenQuestionLink(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')

        def transport(url, body, headers=None, timeout=60):
            raise AssertionError('no model call is expected: ' + url)

        self.service = AgentService(self.store, ModelAdapter(transport), transport)
        self.asked = []
        self.answer = JUDGMENT_YES

        def answers(earlier_request, earlier_reply, utterance):
            self.asked.append((earlier_request, earlier_reply, utterance))
            return Judgment(self.answer, source='fixture')

        self.service.decision_judge.answers_open_question = answers
        self.keys = 0

    def work(self, message, reply, at):
        self.keys += 1
        job = self.store.enqueue(message, f'k-{self.keys}', channel='telegram:x', chat_id=7)
        with self.store.db() as db:
            db.execute('UPDATE jobs SET created=? WHERE id=?', (at, job))
            if reply:
                db.execute("INSERT INTO messages(role,content,channel,created,job_id) VALUES ('assistant',?,?,?,?)",
                           (reply, 'telegram:x', at + 1, job))
        return job

    def scene(self, gap=60):
        asking = self.work('리더 워크샵을 더블트리 힐튼 판교에서 참석', '언제인가요?', 1000)
        order = self.work('지정가 25950원으로 정정해줘', '정정 승인을 기다리고 있어요.', 1000 + gap)
        answer = self.work('오늘 지금 하고 있어', None, 1000 + gap + 30)
        return asking, order, answer

    def test_the_answer_links_to_the_work_that_asked(self):
        asking, order, answer = self.scene()
        linked = self.service.open_question_link('오늘 지금 하고 있어', self.store.job(order), answer)
        self.assertEqual(linked['relation'], 'reference')
        self.assertEqual(linked['previous']['id'], asking)
        self.assertEqual(self.asked, [('리더 워크샵을 더블트리 힐튼 판교에서 참석', '언제인가요?', '오늘 지금 하고 있어')])

    def test_a_judged_no_links_nothing(self):
        _asking, order, answer = self.scene()
        self.answer = JUDGMENT_NO
        self.assertIsNone(self.service.open_question_link('오늘 지금 하고 있어', self.store.job(order), answer))

    def test_a_question_older_than_the_window_is_not_offered(self):
        _asking, order, answer = self.scene(gap=OPEN_QUESTION_SECONDS)
        self.assertIsNone(self.service.open_question_link('오늘 지금 하고 있어', self.store.job(order), answer))
        self.assertEqual(self.asked, [])

    def test_a_question_already_followed_up_is_not_offered_again(self):
        asking, order, answer = self.scene()
        follow = self.work('오후 3시', None, 1050)
        self.store.link_work_relation(follow, asking, 'reference')
        self.assertIsNone(self.service.open_question_link('오늘 지금 하고 있어', self.store.job(order), answer))
        self.assertEqual(self.asked, [])


if __name__ == '__main__':
    unittest.main()
