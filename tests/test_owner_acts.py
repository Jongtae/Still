"""OWNER-ACT-01 (#172): one judgment reads each owner act against everything pending; code keeps only enforcement.

Owner decision 2026-10-21 (revising #22 option A): approval is no longer a
literal word list, a fixed precedence chain or a first-message-only rule.  A
typed message, a reaction, and the message it replies to or reacts on are read
by ``ConversationJudgments.owner_act_answer`` over every open item; only a
judged plain approve or decline of one named item settles, through that item's
own path.  The open item these tests offer is a settings change the worker
drafted in a Telegram Work (#432 removed calendar drafts: the owner's AI now
changes calendar events itself).

Evidence class: model-free service tests.  A scripted compatible-API model, a
recording Telegram transport and a scripted owner-act judgment
(``owner_act_script``) replace only the providers; the collector, the
judgment's schema and policy, ``run_one``, the Telegram ingress, the settings
orchestrator and every settle path run unchanged.  No live model or Telegram
is contacted, and none is claimed.
"""
import unittest

from personal_agent.conversation_handoff import OWNER_ACT_QUESTION, ConversationJudgments
from personal_agent.decision import (OUTCOME_DECIDED, FixtureDecisionEngine, StructuredDecision,
                                     fixture_confidence)
from personal_agent.owner_acts import KIND_SETTINGS
from test_no_scenario_code import scenario_tokens
from test_preparations import CHAT, GENERATION, _Case, call

#: What the owner is told when the offered change was applied (the settings path's own words).
APPLIED = '바꿨습니다'
#: The offered change, as its shown text names it.
OFFERED_EFFECT = '현재 맥락 사용'


class OwnerActCase(_Case):
    """A Telegram conversation with an open item, and the owner-act rule each test scripts (``self.rule``)."""

    def setUp(self):
        super().setUp()
        self.rule = None
        self.acts = []
        self.service.steer_spawn = lambda target: target()
        self.settings = self.service.settings_orchestrator
        self.settings.now = lambda: self.now
        self.applies = []
        original = self.service.set_current_context
        self.service.set_current_context = lambda body: (self.applies.append(dict(body)), original(body))[1]

        def script(context, question, schema):
            if context.purpose != 'owner-act':
                return None
            from owner_act_script import items_of
            items = items_of(context)
            self.acts.append((context.facts['owner_message'], items, context.facts.get('act_target')))
            answer = self.rule(context.facts['owner_message'], items) if self.rule else None
            if answer is None:
                return None
            item, verdict, rest, *parts = answer
            data = {'item': item, 'answer': verdict, 'rest': rest, **({'parts': parts[0]} if parts else {})}
            return StructuredDecision(OUTCOME_DECIDED, data, fixture_confidence(getattr(self, 'probability', 1.0)))

        def structured(context, question, schema, valid=None):
            if context.purpose != 'owner-act':
                return FixtureDecisionEngine.structured(self.judge, context, question, schema, valid)
            self.schemas.append(schema)
            result = script(context, question, schema)
            if result is not None and valid is not None and not valid(result.data):
                return StructuredDecision('malformed')
            return result or StructuredDecision('provider_unavailable')
        self.schemas = []
        self.judge.structured = structured

    def drafting(self):
        """The worker's turn: it drafts one settings change, which waits for the owner."""
        self.script = [{'content': None, 'tool_calls': [call('1', 'settings_change', category='current_context',
                                                             setting='enabled', value='on',
                                                             reason='현재 맥락을 켜 달라고 하셨습니다.')]},
                       {'content': '현재 맥락 켜기를 확인해 주세요.'}]

    def offered(self, text='현재 맥락 켜 줘'):
        """A Work that drafts a change, its reply and proposal delivered; returns the proposal's message id."""
        self.drafting()
        work = self.receive(text)
        [draft] = self.settings.pending_for_work(work)
        self.offered_draft = draft['id']
        self.service.deliver_one()
        self.assertTrue(self.service.deliver_notification())
        with self.store.db() as db:
            return db.execute("SELECT message_id FROM telegram_notifications WHERE job_id=? "
                              "AND kind='settings_change_proposed'", (work,)).fetchone()[0]

    def react(self, message_id, emoji='👍'):
        return self.service.ingest_reaction({'chat': {'id': CHAT, 'type': 'private'}, 'user': {'id': CHAT},
                                             'message_id': message_id,
                                             'new_reaction': [{'type': 'emoji', 'emoji': emoji}]}, GENERATION)

    def receive_reply(self, text, reply_to):
        self.update_id += 1
        self.message_id += 1
        self.service.ingest_update({'update_id': self.update_id, 'message': {
            'message_id': self.message_id, 'from': {'id': CHAT}, 'chat': {'id': CHAT, 'type': 'private'},
            'text': text, 'date': int(self.now), 'reply_to_message': {'message_id': reply_to}}}, GENERATION)
        work = self.store.jobs()[0]['id']
        self.assertTrue(self.service.run_one())
        return work

    def typed_on_web(self, text):
        """A message the owner typed in the local web conversation (the other identity)."""
        self.message_id += 1
        work = self.store.enqueue(text, f'web-{self.message_id}', owner_typed=True)
        self.assertTrue(self.service.run_one())
        return work

    def job(self, work):
        return self.store.job(work)

    def worker_message(self, index):
        return self.model_calls[index]['messages'][-1]['content']

    def settings_item(self):
        """A settings draft of this Telegram conversation, shown after the offered change."""
        self.now += 10
        owner = self.service.settings_owner({'channel': f'telegram:{GENERATION}', 'chat_id': CHAT})
        draft = self.settings.propose(owner, f'telegram:{GENERATION}', 'current_context', 'timezone', 'Asia/Tokyo')
        self.now += 10
        return draft['draft_id']

    def settings_state(self, draft_id):
        return self.settings._drafts()[draft_id]['state']

    def offered_state(self):
        return self.settings_state(self.offered_draft)


def offered_item(act, items):
    """The id of the offered change among ``items``."""
    return next(item['id'] for item in items if OFFERED_EFFECT in item['text'])


def other_item(act, items):
    """The id of the item that is not the offered change."""
    return next(item['id'] for item in items if OFFERED_EFFECT not in item['text'])


class Positive(OwnerActCase):
    def test_a_thumbs_up_on_the_proposal_approves_it(self):
        reply = self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '') if act == 'reaction: 👍' else None
        self.assertEqual(self.react(reply, '👍'), 'owner_act')
        self.assertEqual(self.applies, [{'enabled': True}])
        self.assertEqual(self.offered_state(), 'applied')
        act, items, target = self.acts[-1]
        self.assertEqual([item['kind'] for item in items], [KIND_SETTINGS])
        self.assertIn('a reaction on', target)
        self.assertTrue(any(APPLIED in text for text in self.texts()), 'the owner is told it was applied')
        with self.store.db() as db:
            reviews = db.execute("SELECT count(*) FROM owner_model_upkeep WHERE kind='self-review'").fetchone()[0]
        self.assertEqual(reviews, 0, 'an answer to an open item is not feedback for self-review')

    def test_a_typed_yes_in_other_words_approves_as_judged(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '') if act == '넵' else None
        job = self.job(self.receive('넵'))
        self.assertIn(APPLIED, job['response'])
        self.assertEqual(len(self.applies), 1)

    def test_an_approve_of_part_of_a_settings_confirmation_executes_nothing(self):
        """#424: only a saved-memory notice settles part of itself; part of a confirmation is not what was shown."""
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '', [1])
        self.receive('첫 번째만 해줘')
        self.assertEqual(self.applies, [])
        self.assertEqual(self.offered_state(), 'awaiting-confirmation', 'still pending, nothing deleted')

    def test_a_hesitation_judged_unclear_executes_nothing(self):
        self.offered()
        self.rule = lambda act, items: ('none', 'unrelated', '')
        self.receive('어...')
        self.assertEqual(self.applies, [])
        self.assertEqual(self.offered_state(), 'awaiting-confirmation', 'still pending, nothing deleted')

    def test_with_two_items_pending_the_act_answers_the_one_the_judgment_names(self):
        self.offered()
        draft = self.settings_item()
        self.rule = lambda act, items: (offered_item(act, items), 'approve', '')
        self.receive('응 현재 맥락 그거')
        self.assertEqual(len(self.acts[-1][1]), 2, 'one judgment over everything open')
        self.assertEqual(self.applies, [{'enabled': True}])
        self.assertEqual(self.settings_state(draft), 'awaiting-confirmation', 'the other item stays pending')

    def test_with_two_items_pending_an_answer_to_the_other_leaves_the_offered_change_pending(self):
        self.offered()
        draft = self.settings_item()
        self.rule = lambda act, items: (other_item(act, items), 'decline', '')
        self.receive('시간대는 그대로 둬')
        self.assertEqual(self.settings_state(draft), 'canceled')
        self.assertEqual(self.offered_state(), 'awaiting-confirmation')
        self.assertEqual(self.applies, [])

    def test_a_reply_to_binds_the_act_to_the_item_shown_on_that_message(self):
        reply = self.offered()
        draft = self.settings_item()
        # The newest item is the other settings draft; the reply names the proposal's message.
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.receive_reply('응', reply)
        [item] = self.acts[-1][1]
        self.assertIn(OFFERED_EFFECT, item['text'])
        self.assertIn('a reply to', self.acts[-1][2])
        self.assertEqual(len(self.applies), 1)
        self.assertEqual(self.settings_state(draft), 'awaiting-confirmation')

    def test_a_reply_to_a_message_that_showed_nothing_open_settles_nothing(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.receive_reply('응', 12345)
        self.assertEqual(self.acts, [], 'nothing open was shown on that message, so nothing is judged')
        self.assertEqual(self.applies, [])
        self.assertEqual(self.offered_state(), 'awaiting-confirmation')

    def test_a_compound_answer_approves_and_runs_the_rest(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '내일 일정 알려줘')
        turn = len(self.model_calls)
        self.script = [{'content': '내일은 일정이 없습니다.'}] * 3
        job = self.job(self.receive('응, 그리고 내일 일정 알려줘'))
        self.assertEqual(self.applies, [{'enabled': True}])
        self.assertIn(APPLIED, job['response'].split('\n\n')[0], job['response'])
        self.assertIn('내일은 일정이 없습니다.', job['response'])
        worker = self.worker_message(turn)
        self.assertIn('응, 그리고 내일 일정 알려줘', worker, "the owner's words reach the worker verbatim")
        self.assertIn('Do not act on that part again', worker)
        self.assertIn('내일 일정 알려줘', worker)

    def test_a_judged_decline_drops_exactly_the_change_shown(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'decline', '')
        self.receive('아냐 하지 마')
        self.assertEqual(self.offered_state(), 'canceled')
        self.assertEqual(self.applies, [])


class Negative(OwnerActCase):
    def test_nothing_executes_without_a_judged_approve_of_a_named_item(self):
        self.offered()
        for answer in (('none', 'approve', ''), ('item-1', 'change', ''), ('item-1', 'unrelated', ''),
                       ('none', 'unrelated', '')):
            with self.subTest(answer=answer):
                self.rule = lambda act, items, answer=answer: answer
                self.receive('승인')
                self.assertEqual(self.applies, [])
                self.assertEqual(self.offered_state(), 'awaiting-confirmation', 'nothing deleted on a miss')

    def test_an_approve_naming_an_id_that_is_not_open_executes_nothing(self):
        self.offered()
        self.rule = lambda act, items: ('item-7', 'approve', '')
        self.receive('승인')
        self.assertEqual(self.applies, [])
        self.assertEqual(self.offered_state(), 'awaiting-confirmation')
        self.assertEqual(self.schemas[-1]['properties']['item']['enum'], ['item-1', 'none'])

    def test_a_judgment_outage_executes_nothing_and_deletes_nothing(self):
        self.offered()
        self.rule = None  # unavailable
        job = self.job(self.receive('승인'))
        self.assertEqual(job['status'], 'succeeded', 'the message runs as an ordinary Work')
        self.assertEqual(self.applies, [])
        self.assertEqual(self.offered_state(), 'awaiting-confirmation')

        def broken(act, items):
            raise RuntimeError('engine down')
        self.rule = broken
        self.receive('승인')
        self.assertEqual(self.applies, [])
        self.assertEqual(self.offered_state(), 'awaiting-confirmation')

    def test_a_low_confidence_approve_executes_nothing(self):
        self.offered()
        self.probability = 0.7  # above the structured threshold, below the approval one
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.receive('승인')
        self.assertEqual(self.applies, [])
        self.assertEqual(self.offered_state(), 'awaiting-confirmation')

    def test_an_expired_change_is_not_open(self):
        self.offered()
        self.now += self.settings.TTL_SECONDS + self.settings.RENEW_GRACE_SECONDS + 1
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.receive('승인')
        self.assertEqual(self.acts, [])
        self.assertEqual(self.applies, [])

    def test_another_channel_never_answers_the_change(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.typed_on_web('승인')
        self.assertEqual(self.acts, [])
        self.assertEqual(self.applies, [])
        self.assertEqual(self.offered_state(), 'awaiting-confirmation')

    def test_a_message_still_did_not_type_and_a_photo_never_answer(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        work = self.store.enqueue('승인', 'not-typed', channel=f'telegram:{GENERATION}', chat_id=CHAT)
        self.assertTrue(self.service.run_one())
        self.update_id += 1
        self.message_id += 1
        self.service.ingest_update({'update_id': self.update_id, 'message': {
            'message_id': self.message_id, 'from': {'id': CHAT}, 'chat': {'id': CHAT, 'type': 'private'},
            'caption': '승인', 'photo': [{'file_id': 'p1', 'width': 1, 'height': 1}], 'date': int(self.now)}},
            GENERATION)
        self.service.run_one()
        self.assertEqual(self.acts, [])
        self.assertEqual(self.applies, [])
        self.assertNotIn(APPLIED, self.job(work)['response'] or '')

    def test_an_act_cannot_answer_an_item_shown_after_it(self):
        self.drafting()
        self.receive('현재 맥락 켜 줘')
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.update_id += 1
        self.message_id += 1
        self.service.ingest_update({'update_id': self.update_id, 'message': {
            'message_id': self.message_id, 'from': {'id': CHAT}, 'chat': {'id': CHAT, 'type': 'private'},
            'text': '승인', 'date': int(self.now) - 30}}, GENERATION)
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.acts, [])
        self.assertEqual(self.applies, [])

    def test_a_reaction_judged_unrelated_settles_nothing_and_queues_no_self_review(self):
        reply = self.offered()
        self.rule = lambda act, items: ('none', 'unrelated', '')
        self.assertEqual(self.react(reply, '🤔'), 'owner_act')
        self.assertEqual(self.applies, [])
        self.assertEqual(self.offered_state(), 'awaiting-confirmation')
        with self.store.db() as db:
            reviews = db.execute("SELECT count(*) FROM owner_model_upkeep WHERE kind='self-review'").fetchone()[0]
        self.assertEqual(reviews, 0)

    def test_a_second_answer_to_the_same_change_executes_nothing(self):
        reply = self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.react(reply, '👍')
        self.react(reply, '👍')
        self.receive('승인')
        self.assertEqual(len(self.applies), 1)


class JudgmentTests(unittest.TestCase):
    ITEMS = [{'id': 'item-1', 'kind': KIND_SETTINGS, 'shown_text': 'preview', 'shown_at': 'just now',
              'expires_at': 'in 14 minutes', 'answers': 'approve applies; decline drops'}]

    def judgments(self, data, probability=1.0):
        asked = []

        def structured(context, question, schema):
            asked.append((context, question, schema))
            return StructuredDecision(OUTCOME_DECIDED, data, fixture_confidence(probability))
        return ConversationJudgments(FixtureDecisionEngine(structured=structured)), asked

    def test_the_question_names_no_service_site_or_request_category(self):
        self.assertEqual(scenario_tokens(OWNER_ACT_QUESTION), [])
        self.assertIn('not approval', OWNER_ACT_QUESTION)

    def test_an_answer_is_one_declared_item_and_answer(self):
        judgments, asked = self.judgments({'item': 'item-1', 'answer': 'approve', 'rest': ' '})
        self.assertEqual(judgments.owner_act_answer('넵', self.ITEMS),
                         {'item': 'item-1', 'answer': 'approve', 'rest': '', 'parts': []})
        context, _question, schema = asked[-1]
        self.assertEqual(context.purpose, 'owner-act')
        self.assertEqual(schema['properties']['item']['enum'], ['item-1', 'none'])
        self.assertEqual(context.facts['owner_message'], '넵')

    def test_parts_name_numbered_entries_and_may_be_absent(self):
        """#424: which numbered entries the act answers alone; absent or empty is the whole item."""
        judgments, asked = self.judgments({'item': 'item-1', 'answer': 'decline', 'rest': '', 'parts': [3, 1, 3, 0]})
        self.assertEqual(judgments.owner_act_answer('첫째랑 셋째는 빼줘', self.ITEMS)['parts'], [1, 3])
        self.assertIn('parts', asked[-1][2]['required'])
        judgments, _ = self.judgments({'item': 'item-1', 'answer': 'decline', 'rest': ''})
        self.assertEqual(judgments.owner_act_answer('빼줘', self.ITEMS)['parts'], [])
        for parts in (['1'], [True], 'all'):
            with self.subTest(parts=parts):
                judgments, _ = self.judgments({'item': 'item-1', 'answer': 'decline', 'rest': '', 'parts': parts})
                self.assertIsNone(judgments.owner_act_answer('빼줘', self.ITEMS))

    def test_an_undeclared_item_or_answer_is_no_answer(self):
        for data in ({'item': 'item-2', 'answer': 'approve', 'rest': ''},
                     {'item': 'item-1', 'answer': 'maybe', 'rest': ''}):
            with self.subTest(data=data):
                judgments, _ = self.judgments(data)
                self.assertIsNone(judgments.owner_act_answer('넵', self.ITEMS))

    def test_approve_needs_the_approval_threshold_but_unrelated_does_not(self):
        judgments, _ = self.judgments({'item': 'item-1', 'answer': 'approve', 'rest': ''}, probability=0.7)
        self.assertIsNone(judgments.owner_act_answer('넵', self.ITEMS))
        judgments, _ = self.judgments({'item': 'none', 'answer': 'unrelated', 'rest': ''}, probability=0.7)
        self.assertEqual(judgments.owner_act_answer('넵', self.ITEMS)['answer'], 'unrelated')

    def test_no_open_item_asks_nothing(self):
        judgments, asked = self.judgments({'item': 'none', 'answer': 'unrelated', 'rest': ''})
        self.assertIsNone(judgments.owner_act_answer('넵', []))
        self.assertEqual(asked, [])


if __name__ == '__main__':
    unittest.main()
