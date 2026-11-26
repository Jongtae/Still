"""REACTION-IN-01 (#996): the owner answers an open item in words or with a reaction, not only a button.

Owner direction 2026-10-04: buttons feel like a system; a person would read a
thumbs up or a heart as "good", silence as "unclear", and a dismayed face, a
thumbs down or "don't remember that" as "don't keep it".

* An owner reaction on an open item's message, or a typed message while it is
  open, goes to the one owner-act judgment (#172, ``owner_act_answer``); the
  item's own settle path runs only on a judged answer.  No emoji is mapped in
  code.  The open item here is a memory proposal (#818); the #918 saved
  notice it was first shown on was retired by #962 after #955.
* A reaction on an answer is recorded on that Work (``owner_reaction``).
* The bot asks Telegram for ``message_reaction`` updates.

Evidence class: model-free service tests with a scripted model, an injected
Telegram transport and a FixtureDecisionEngine.  Telegram documents reaction
delivery for chats where the bot is an administrator; whether private chats
deliver it was not observed here, and no live Telegram is claimed.
"""
import json
import unittest

from personal_agent.conversation_handoff import TELEGRAM_POLL_UPDATE_KINDS
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from personal_agent.quickstart_service import REACTION_EVENT

from owner_act_script import with_owner_acts
from test_memory_candidate_confirm import CHAT, GENERATION, OTHER, VALUE
from test_memory_save_undo import SaveDirectly


class _Answering(SaveDirectly):
    #: How the scripted judgment reads an act on the open memory proposal: 'decline', 'approve', None (unavailable)
    #: or False (unrelated).  ``PARTS`` (#424) names numbered entries it answers alone.
    ANSWER = 'decline'
    PARTS = None

    def setUp(self):
        super().setUp()
        self.asked = []

        def judge(context, proposition):
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None

        def act(response, items):
            proposals = [item for item in items if item['kind'] == 'memory-proposal']
            self.asked.append({'owner_response': response, 'shown': proposals[0]['text'] if proposals else ''})
            if self.ANSWER is None:
                return None
            if not (self.ANSWER and proposals):
                return ('none', 'unrelated', '')
            return (proposals[0]['id'], self.ANSWER, '', *([self.PARTS] if self.PARTS is not None else []))
        self.claim_completion(with_owner_acts(FixtureDecisionEngine(judge=judge), rule=act))
        self.service.steer_spawn = lambda target: target()

    def offered(self):
        """A plain reply, then one memory proposal (#818) delivered; returns its notification row."""
        job_id = self.turn()
        self.service.deliver_one()
        self.assertTrue(self.service.deliver_notification())
        [row] = self.notification(job_id)
        return row

    def candidates(self):
        return [c['state'] for c in self.store.memory_candidates(include_decided=True)]

    def react(self, message_id, emoji='😳', user=CHAT, chat=None):
        return self.service.ingest_reaction({
            'chat': {'id': chat or user, 'type': 'private'}, 'user': {'id': user}, 'message_id': message_id,
            'date': 1, 'old_reaction': [], 'new_reaction': [{'type': 'emoji', 'emoji': emoji}] if emoji else []},
            GENERATION)

    def events(self, job_id):
        with self.store.db() as db:
            return [json.loads(row['detail']) for row in
                    db.execute('SELECT detail FROM tool_events WHERE job_id=? AND tool=? ORDER BY id', (job_id, REACTION_EVENT))]


class ReactionOnTheAsk(_Answering):
    def test_a_reaction_the_judgment_reads_as_no_declines_the_proposal(self):
        row = self.offered()
        self.assertEqual(self.react(row['message_id'], '😳'), 'owner_act')
        self.assertEqual(self.asked[-1]['owner_response'], 'reaction: 😳')
        self.assertIn(VALUE, self.asked[-1]['shown'])
        self.assertEqual(self.candidates(), ['rejected'])
        self.assertEqual(self.store.memories(), [])

    def test_reactions_from_anyone_else_or_without_an_emoji_are_ignored(self):
        row = self.offered()
        self.assertIsNone(self.react(row['message_id'], user=OTHER))
        self.assertIsNone(self.react(row['message_id'], chat=OTHER))
        self.assertIsNone(self.react(row['message_id'], emoji=None), 'a removed reaction says nothing new')
        self.assertIsNone(self.react(row['message_id'] + 100), 'not an assistant message Still knows')
        self.assertEqual(self.asked, [])
        self.assertEqual(self.candidates(), ['pending'])


class ReactionUnavailable(_Answering):
    """No answer from the Judgment AI settles nothing."""
    ANSWER = None

    def test_an_unavailable_judgment_keeps_the_proposal_open(self):
        row = self.offered()
        self.react(row['message_id'], '👎')
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.candidates(), ['pending'])


class PartOfAnItem(_Answering):
    """#424: an act that answers part of an item settles nothing."""
    PARTS = [1]

    def test_a_partial_answer_settles_nothing(self):
        row = self.offered()
        self.react(row['message_id'], '👎')
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.candidates(), ['pending'])


class ReactionOnAnAnswer(_Answering):
    def test_a_reaction_on_an_answer_is_recorded_on_its_work(self):
        job = self.told()
        answer = [body for method, body in self.calls if method == 'sendMessage' and body.get('text') == self.text]
        self.assertEqual(len(answer), 1)
        answer_id = self.service.telegram_turns.get(job['id'])['reply_message_id']
        self.assertIsInstance(answer_id, int)
        self.assertEqual(self.react(answer_id, '❤'), 'answer')
        self.assertEqual(self.events(job['id']), [{'emoji': ['❤'], 'on': 'answer'}])
        self.assertEqual(self.asked, [], 'nothing open is asked about')
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE])
        with self.store.db() as db:
            review = db.execute("SELECT reason, observation, state FROM owner_model_upkeep WHERE job_id=? AND kind='self-review'",
                                (job['id'],)).fetchone()
        self.assertEqual((review['reason'], review['state']), ('reaction', 'pending'),
                         'the reacted answer gets one self-review in the idle tick (#998)')
        self.assertIn('❤', review['observation'])


class CostBound(_Answering):
    """#172 review: an unbound typed act is not judged against a long-lived item the owner has spoken past."""
    ANSWER = False

    def hours_old(self, hours=12):
        """A memory proposal shown ``hours`` ago, with one owner message since; returns its notification row."""
        row = self.offered()
        binding = self.binding(row)
        binding['sent'] = float(binding['sent']) - hours * 3600
        self.store.update_notification(row['id'], 'sent', fingerprint=json.dumps(binding))
        spoke = self.store.enqueue('어제 얘기', 'cost-spoke', channel=f'telegram:{GENERATION}', chat_id=CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET owner_typed=1, status='succeeded', created=? WHERE id=?",
                       (binding['sent'] + 3600, spoke))
            # #188: ``sent_at`` is when the owner could first see it.
            db.execute('UPDATE telegram_notifications SET sent_at=? WHERE id=?', (binding['sent'], row['id']))
        self.plan = []
        return row

    def typed(self, text, reply_to=None):
        job_id = self.store.enqueue(text, 'cost-' + text, channel=f'telegram:{GENERATION}', chat_id=CHAT)
        with self.store.db() as db:
            db.execute('UPDATE jobs SET owner_typed=1 WHERE id=?', (job_id,))
        if reply_to is not None:
            self.service.telegram_turns.record_source(job_id, CHAT, 4242, reply_to=reply_to)
        self.service.run_one()
        return job_id

    def test_a_typed_message_while_only_an_older_ask_is_open_makes_no_judgment_call(self):
        self.hours_old()
        self.typed('점심 뭐 먹지')
        self.assertEqual(self.asked, [], 'no owner-act judgment call')

    def test_a_reply_to_the_older_ask_is_judged(self):
        row = self.hours_old()
        self.typed('그건 기억하지 마', reply_to=row['message_id'])
        self.assertEqual([a['owner_response'] for a in self.asked], ['그건 기억하지 마'])

    def test_a_typed_message_right_after_the_ask_is_judged(self):
        self.offered()
        self.plan = []
        self.typed('점심 뭐 먹지')
        self.assertEqual([a['owner_response'] for a in self.asked], ['점심 뭐 먹지'])


class CommandAfterTheAsk(_Answering):
    def test_a_typed_slash_command_asks_no_pre_route_judgment(self):
        """#1389: /ai answers with no model; no withdrawal or early-plan call delays it."""
        self.offered()
        self.plan = []
        planned = []
        self.service.early_work_plan = lambda *args, **kwargs: planned.append(args)
        follow = self.enqueue('/ai')
        with self.store.db() as db:
            db.execute('UPDATE jobs SET owner_typed=1 WHERE id=?', (follow,))
        self.service.run_one()
        self.assertEqual(self.store.job(follow)['status'], 'succeeded')
        self.assertIn('기본 AI:', self.store.job(follow)['response'])
        self.assertEqual((self.asked, planned), ([], []))
        self.assertEqual([c['state'] for c in self.store.memory_candidates()], ['pending'])


class Polling(unittest.TestCase):
    def test_the_bot_asks_telegram_for_reactions(self):
        self.assertIn('message_reaction', TELEGRAM_POLL_UPDATE_KINDS)


if __name__ == '__main__':
    unittest.main()
