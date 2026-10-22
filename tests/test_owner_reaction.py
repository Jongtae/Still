"""REACTION-IN-01 (#996): the owner answers a saved notice in words or with a reaction, not a button.

Owner direction 2026-10-04: buttons feel like a system; a person would read a
thumbs up or a heart as "good", silence as "unclear", and a dismayed face, a
thumbs down or "don't remember that" as "don't keep it".

* A saved notice carries no buttons.
* An owner reaction on a saved notice, or any typed message while it is open,
  goes to the one owner-act judgment (#172, ``owner_act_answer``).  On a
  judged decline of the notice, the exact #918 undo runs; otherwise the facts
  stay.  No emoji is mapped in code.
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
from personal_agent.quickstart_service import MEMORY_SAVED_KIND, REACTION_EVENT

from owner_act_script import with_owner_acts
from test_memory_candidate_confirm import CHAT, GENERATION, OTHER, VALUE
from test_memory_save_undo import SaveAndTell


class _Answering(SaveAndTell):
    WITHDRAW = True

    def setUp(self):
        super().setUp()
        self.asked = []

        def judge(context, proposition):
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None

        def act(response, items):
            # #172: the one owner-act judgment, scripted: WITHDRAW reads the act as a decline of the notice.
            notices = [item for item in items if item['kind'] == 'memory-saved']
            self.asked.append({'owner_response': response, 'remembered': notices[0]['text'] if notices else ''})
            if self.WITHDRAW is None:
                return None
            return (notices[0]['id'], 'decline', '') if self.WITHDRAW and notices else ('none', 'unrelated', '')
        self.claim_completion(with_owner_acts(FixtureDecisionEngine(judge=judge), rule=act))
        self.service.steer_spawn = lambda target: target()

    def react(self, message_id, emoji='😳', user=CHAT, chat=None):
        return self.service.ingest_reaction({
            'chat': {'id': chat or user, 'type': 'private'}, 'user': {'id': user}, 'message_id': message_id,
            'date': 1, 'old_reaction': [], 'new_reaction': [{'type': 'emoji', 'emoji': emoji}] if emoji else []},
            GENERATION)

    def events(self, job_id):
        with self.store.db() as db:
            return [json.loads(row['detail']) for row in
                    db.execute('SELECT detail FROM tool_events WHERE job_id=? AND tool=? ORDER BY id', (job_id, REACTION_EVENT))]


class ReactionOnTheNotice(_Answering):
    def test_the_notice_has_no_buttons(self):
        _job, notice, _row = self.told()
        self.assertEqual(notice['reply_markup'], {'inline_keyboard': []})

    def test_a_reaction_the_judgment_reads_as_no_undoes_the_facts(self):
        job, _notice, row = self.told()
        self.assertEqual(self.react(row['message_id'], '😳'), 'owner_act')
        self.assertEqual([(m['content'], m['state']) for m in self.store.memories()], [],
                         'the saved fact is no longer current')
        self.assertEqual(self.asked[-1]['owner_response'], 'reaction: 😳')
        self.assertIn(VALUE, self.asked[-1]['remembered'])
        edit = self.edits()[-1]
        self.assertEqual((edit['message_id'], edit['text']), (row['message_id'], f'되돌렸어요: {VALUE}'))
        self.assertEqual(self.store.notification(row['id'])['state'], 'memory_undone')
        self.assertEqual(self.events(job['id']), [{'emoji': ['😳'], 'on': 'memory_notice'}])

    def test_reactions_from_anyone_else_or_without_an_emoji_are_ignored(self):
        _job, _notice, row = self.told()
        self.assertIsNone(self.react(row['message_id'], user=OTHER))
        self.assertIsNone(self.react(row['message_id'], chat=OTHER))
        self.assertIsNone(self.react(row['message_id'], emoji=None), 'a removed reaction says nothing new')
        self.assertIsNone(self.react(row['message_id'] + 100), 'not an assistant message Still knows')
        self.assertEqual(self.asked, [])
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE])


class ReactionKeeps(_Answering):
    WITHDRAW = False

    def test_a_reaction_the_judgment_does_not_read_as_no_keeps_the_facts(self):
        job, _notice, row = self.told()
        self.react(row['message_id'], '👍')
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE])
        self.assertEqual(self.store.notification(row['id'])['state'], 'sent')
        self.assertEqual(self.events(job['id']), [{'emoji': ['👍'], 'on': 'memory_notice'}])


class ReactionUnavailable(ReactionKeeps):
    """No answer from the Judgment AI keeps what was saved."""
    WITHDRAW = None


class ReactionOnAnAnswer(_Answering):
    def test_a_reaction_on_an_answer_is_recorded_on_its_work(self):
        job, _notice, _row = self.told()
        answer = [body for method, body in self.calls if method == 'sendMessage' and body.get('text') == self.text]
        self.assertEqual(len(answer), 1)
        answer_id = self.service.telegram_turns.get(job['id'])['reply_message_id']
        self.assertIsInstance(answer_id, int)
        self.assertEqual(self.react(answer_id, '❤'), 'answer')
        self.assertEqual(self.events(job['id']), [{'emoji': ['❤'], 'on': 'answer'}])
        self.assertEqual(self.asked, [], 'only a saved notice is asked about')
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE])
        with self.store.db() as db:
            review = db.execute("SELECT reason, observation, state FROM owner_model_upkeep WHERE job_id=? AND kind='self-review'",
                                (job['id'],)).fetchone()
        self.assertEqual((review['reason'], review['state']), ('reaction', 'pending'),
                         'the reacted answer gets one self-review in the idle tick (#998)')
        self.assertIn('❤', review['observation'])


class WordsAfterTheNotice(_Answering):
    def test_the_first_typed_message_after_the_notice_can_withdraw_it(self):
        _job, _notice, row = self.told()
        self.plan = []
        self.bodies.clear()
        follow = self.enqueue('아 그건 기억하지 마')
        with self.store.db() as db:
            db.execute('UPDATE jobs SET owner_typed=1 WHERE id=?', (follow,))
        self.service.run_one()
        self.assertEqual(self.asked[-1]['owner_response'], '아 그건 기억하지 마')
        self.assertEqual(self.store.memories(), [])
        self.assertEqual(self.store.notification(row['id'])['state'], 'memory_undone')
        users = [m for body in self.bodies for m in body.get('messages', []) if m.get('role') == 'user']
        self.assertTrue(any('removed what you had just said you remembered' in str(m.get('content')) and VALUE in str(m.get('content'))
                            for m in users), 'the worker knows the facts were already removed')

    def test_once_the_owner_has_spoken_an_unbound_message_is_not_judged_against_the_notice(self):
        # #172 cost bound: a long-lived item joins an unbound typed act only until the owner speaks again.
        self.WITHDRAW = False
        _job, _notice, _row = self.told()
        self.plan = []
        for index, text in enumerate(('점심 뭐 먹지', '그건 기억하지 마')):
            job_id = self.store.enqueue(text, f'later-{index}', channel=f'telegram:{GENERATION}', chat_id=CHAT)
            with self.store.db() as db:
                db.execute('UPDATE jobs SET owner_typed=1 WHERE id=?', (job_id,))
            self.service.run_one()
        self.assertEqual([a['owner_response'] for a in self.asked], ['점심 뭐 먹지'])
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE], 'nothing judged a decline')


class CostBound(_Answering):
    """#172 review: an unbound typed act is not judged against a long-lived item the owner has spoken past."""
    WITHDRAW = False

    def two_days_old(self):
        """A saved notice shown two days ago, with one owner message since; returns its notification row."""
        _job, _notice, row = self.told()
        binding = self.binding(row)
        binding['sent'] = float(binding['sent']) - 2 * 86400
        self.store.update_notification(row['id'], 'sent', fingerprint=json.dumps(binding))
        spoke = self.store.enqueue('어제 얘기', 'cost-spoke', channel=f'telegram:{GENERATION}', chat_id=CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET owner_typed=1, status='succeeded', created=? WHERE id=?",
                       (binding['sent'] + 3600, spoke))
            # The send completed two days ago too (#188: ``sent_at`` is when the owner could first see it).
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

    def test_a_typed_message_while_only_an_old_notice_is_open_makes_no_judgment_call(self):
        self.two_days_old()
        self.typed('점심 뭐 먹지')
        self.assertEqual(self.asked, [], 'no owner-act judgment call')

    def test_a_reply_to_the_old_notice_is_judged(self):
        row = self.two_days_old()
        self.typed('그건 기억하지 마', reply_to=row['message_id'])
        self.assertEqual([a['owner_response'] for a in self.asked], ['그건 기억하지 마'])

    def test_a_typed_message_right_after_the_notice_is_judged(self):
        self.told()
        self.plan = []
        self.typed('점심 뭐 먹지')
        self.assertEqual([a['owner_response'] for a in self.asked], ['점심 뭐 먹지'])


class CommandAfterTheNotice(_Answering):
    def test_a_typed_slash_command_asks_no_pre_route_judgment(self):
        """#1389: /ai answers with no model; no withdrawal or early-plan call delays it."""
        _job, _notice, _row = self.told()
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
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE])


class QueuedBeforeTheNotice(_Answering):
    def test_a_message_sent_before_the_notice_is_not_read_as_its_answer(self):
        job, _notice, row = self.told()
        follow = self.enqueue('아 그건 기억하지 마')
        binding = self.binding(row)
        with self.store.db() as db:
            db.execute('UPDATE jobs SET owner_typed=1, created=? WHERE id=?', (float(binding['sent']) - 5, follow))
        self.plan = []
        self.service.run_one()
        self.assertEqual(self.asked, [])
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE])


class WordsKeep(WordsAfterTheNotice.__bases__[0]):
    WITHDRAW = False

    def test_an_unrelated_first_message_keeps_the_facts_and_adds_no_note(self):
        _job, _notice, row = self.told()
        self.plan = []
        self.bodies.clear()
        follow = self.enqueue('점심 뭐 먹지')
        with self.store.db() as db:
            db.execute('UPDATE jobs SET owner_typed=1 WHERE id=?', (follow,))
        self.service.run_one()
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE])
        users = [m for body in self.bodies for m in body.get('messages', []) if m.get('role') == 'user']
        self.assertFalse(any('removed what you had just said you remembered' in str(m.get('content')) for m in users))


class Polling(unittest.TestCase):
    def test_the_bot_asks_telegram_for_reactions(self):
        self.assertIn('message_reaction', TELEGRAM_POLL_UPDATE_KINDS)


if __name__ == '__main__':
    unittest.main()
