"""OWNER-ACT-01 (#172): one judgment reads each owner act against everything pending; code keeps only enforcement.

Owner decision 2026-10-21 (revising #22 option A): approval is no longer a
literal word list, a fixed precedence chain or a first-message-only rule.  A
typed message, a reaction, and the message it replies to or reacts on are read
by ``ConversationJudgments.owner_act_answer`` over every open item; only a
judged plain approve or decline of one named item settles, through that item's
own path.

Evidence class: model-free service tests.  A scripted compatible-API model, a
recording Telegram transport, a fixture calendar provider and a scripted
owner-act judgment (``owner_act_script``) replace only the providers; the
collector, the judgment's schema and policy, ``run_one``, the Telegram ingress
and every settle path run unchanged.  No live model, Telegram or Google
Calendar is contacted, and none is claimed.
"""
import unittest

from personal_agent.calendar_conversation import CHANGED_UNDER_US, CREATED, STATE_KEY
from personal_agent.connector_contract import _owner_key
from personal_agent.conversation_handoff import OWNER_ACT_QUESTION, ConversationJudgments
from personal_agent.decision import (OUTCOME_DECIDED, FixtureDecisionEngine, StructuredDecision,
                                     fixture_confidence)
from personal_agent.owner_acts import KIND_CALENDAR, KIND_SETTINGS
from test_calendar_conversation import DENTIST, OWNER, CalendarDraftApprovalCase
from test_no_scenario_code import scenario_tokens
from test_preparations import CHAT, GENERATION


class OwnerActCase(CalendarDraftApprovalCase):
    """The calendar harness, with the owner-act rule each test scripts (``self.rule``)."""

    def setUp(self):
        super().setUp()
        self.rule = None
        self.service.steer_spawn = lambda target: target()

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

    def offered(self, text='치과 일정 잡아줘'):
        """A Work that drafts, its reply delivered to Telegram; returns the reply's message id."""
        self.drafting(('calendar_draft_create', DENTIST))
        work = self.receive(text)
        self.service.deliver_one()
        return self.service.telegram_turns.get(work)['reply_message_id']

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

    def settings_item(self):
        """A settings draft of this Telegram conversation, shown after the calendar preview."""
        self.now += 10
        settings = self.service.settings_orchestrator
        settings.now = lambda: self.now
        owner = self.service.settings_owner({'channel': f'telegram:{GENERATION}', 'chat_id': CHAT})
        draft = settings.propose(owner, f'telegram:{GENERATION}', 'current_context', 'timezone', 'Asia/Tokyo')
        self.now += 10
        return draft['draft_id']

    def settings_state(self, draft_id):
        return self.service.settings_orchestrator._drafts()[draft_id]['state']


def by_kind(kind, verdict='approve', rest=''):
    """A scripted judgment that answers the item of ``kind``."""
    return lambda act, items: (next(item['id'] for item in items if item['kind'] == kind), verdict, rest)


class Positive(OwnerActCase):
    def test_a_thumbs_up_on_the_calendar_preview_approves_it(self):
        reply = self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '') if act == 'reaction: 👍' else None
        self.assertEqual(self.react(reply, '👍'), 'owner_act')
        self.assertEqual(self.provider.calls, [('create', DENTIST)])
        self.assertEqual(self.states(), ['completed'])
        act, items, target = self.acts[-1]
        self.assertEqual([item['kind'] for item in items], [KIND_CALENDAR])
        self.assertIn('a reaction on', target)
        self.assertTrue(any(CREATED in text for text in self.texts()), 'the owner is told it was added')
        with self.store.db() as db:
            reviews = db.execute("SELECT count(*) FROM owner_model_upkeep WHERE kind='self-review'").fetchone()[0]
        self.assertEqual(reviews, 0, 'an answer to an open item is not feedback for self-review')

    def test_a_typed_yes_in_other_words_approves_as_judged(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '') if act == '넵' else None
        job = self.job(self.receive('넵'))
        self.assertIn(CREATED, job['response'])
        self.assertEqual(len(self.provider.calls), 1)

    def test_an_approve_of_part_of_a_calendar_preview_executes_nothing(self):
        """#424: only a saved-memory notice settles part of itself; part of a preview is not what was shown."""
        self.offered()
        shown = self.pending()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '', [1])
        self.receive('첫 번째만 해줘')
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.pending(), shown, 'still pending, nothing deleted')

    def test_a_hesitation_judged_unclear_executes_nothing(self):
        self.offered()
        shown = self.pending()
        self.rule = lambda act, items: ('none', 'unrelated', '')
        self.receive('어...')
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.pending(), shown, 'still pending, nothing deleted')

    def test_with_two_items_pending_the_act_answers_the_one_the_judgment_names(self):
        self.offered()
        draft = self.settings_item()
        self.rule = by_kind(KIND_CALENDAR)
        self.receive('응 일정 그거')
        self.assertEqual({item['kind'] for item in self.acts[-1][1]}, {KIND_CALENDAR, KIND_SETTINGS},
                         'one judgment over everything open')
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.settings_state(draft), 'awaiting-confirmation', 'the other item stays pending')

    def test_with_two_items_pending_a_settings_answer_leaves_the_calendar_drafts_pending(self):
        self.offered()
        draft = self.settings_item()
        shown = self.pending()
        self.rule = by_kind(KIND_SETTINGS, 'decline')
        self.receive('시간대는 그대로 둬')
        self.assertEqual(self.settings_state(draft), 'canceled')
        self.assertEqual(self.pending(), shown)
        self.assertEqual(self.provider.calls, [])

    def test_a_reply_to_binds_the_act_to_the_item_shown_on_that_message(self):
        reply = self.offered()
        draft = self.settings_item()
        # The newest item is the settings draft; the reply names the preview's message.
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.receive_reply('응', reply)
        self.assertEqual([item['kind'] for item in self.acts[-1][1]], [KIND_CALENDAR])
        self.assertIn('a reply to', self.acts[-1][2])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.settings_state(draft), 'awaiting-confirmation')

    def test_a_reply_to_a_message_that_showed_nothing_open_settles_nothing(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.receive_reply('응', 12345)
        self.assertEqual(self.acts, [], 'nothing open was shown on that message, so nothing is judged')
        self.assertEqual(self.provider.calls, [])
        self.assertTrue(self.pending())

    def test_a_compound_answer_approves_and_runs_the_rest(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '내일 일정 알려줘')
        turn = len(self.model_calls)
        self.script = [{'content': '내일은 일정이 없습니다.'}] * 3
        job = self.job(self.receive('응, 그리고 내일 일정 알려줘'))
        self.assertEqual(self.provider.calls, [('create', DENTIST)])
        self.assertTrue(job['response'].startswith(CREATED), job['response'])
        self.assertIn('내일은 일정이 없습니다.', job['response'])
        worker = self.worker_message(turn)
        self.assertIn('응, 그리고 내일 일정 알려줘', worker, "the owner's words reach the worker verbatim")
        self.assertIn('Do not act on that part again', worker)
        self.assertIn('내일 일정 알려줘', worker)

    def test_a_judged_decline_drops_exactly_the_drafts_shown(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'decline', '')
        self.receive('아냐 하지 마')
        self.assertEqual(self.pending(), {})
        self.assertEqual(self.provider.calls, [])


class Negative(OwnerActCase):
    def test_nothing_executes_without_a_judged_approve_of_a_named_item(self):
        self.offered()
        for answer in (('none', 'approve', ''), ('item-1', 'change', ''), ('item-1', 'unrelated', ''),
                       ('none', 'unrelated', '')):
            with self.subTest(answer=answer):
                self.rule = lambda act, items, answer=answer: answer
                self.receive('승인')
                self.assertEqual(self.provider.calls, [])
                self.assertTrue(self.pending(), 'nothing deleted on a miss')

    def test_an_approve_naming_an_id_that_is_not_open_executes_nothing(self):
        self.offered()
        self.rule = lambda act, items: ('item-7', 'approve', '')
        self.receive('승인')
        self.assertEqual(self.provider.calls, [])
        self.assertTrue(self.pending())
        self.assertEqual(self.schemas[-1]['properties']['item']['enum'], ['item-1', 'none'])

    def test_a_judgment_outage_executes_nothing_and_deletes_nothing(self):
        self.offered()
        shown = self.pending()
        self.rule = None  # unavailable
        job = self.job(self.receive('승인'))
        self.assertEqual(job['status'], 'succeeded', 'the message runs as an ordinary Work')
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.pending(), shown)

        def broken(act, items):
            raise RuntimeError('engine down')
        self.rule = broken
        self.receive('승인')
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.pending(), shown)

    def test_a_low_confidence_approve_executes_nothing(self):
        self.offered()
        self.probability = 0.7  # above the structured threshold, below the approval one
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.receive('승인')
        self.assertEqual(self.provider.calls, [])
        self.assertTrue(self.pending())

    def test_a_changed_payload_never_executes(self):
        self.offered()
        row = dict(self.pending())
        row['drafts'] = [{**row['drafts'][0], 'payload_hash': 'not-the-hash-that-was-shown'}]
        self.store.put(STATE_KEY, {_owner_key(OWNER): row})
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        job = self.job(self.receive('승인'))
        self.assertEqual(job['response'], CHANGED_UNDER_US)
        self.assertEqual(self.provider.calls, [])

    def test_an_expired_preview_is_not_open(self):
        self.offered()
        self.now += 901
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.receive('승인')
        self.assertEqual(self.acts, [])
        self.assertEqual(self.provider.calls, [])

    def test_another_channel_never_answers_the_drafts(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.typed_on_web('승인')
        self.assertEqual(self.acts, [])
        self.assertEqual(self.provider.calls, [])
        self.assertTrue(self.pending())

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
        self.assertEqual(self.provider.calls, [])
        self.assertNotIn(CREATED, self.job(work)['response'] or '')

    def test_an_act_cannot_answer_an_item_shown_after_it(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.update_id += 1
        self.message_id += 1
        self.service.ingest_update({'update_id': self.update_id, 'message': {
            'message_id': self.message_id, 'from': {'id': CHAT}, 'chat': {'id': CHAT, 'type': 'private'},
            'text': '승인', 'date': int(self.now) - 30}}, GENERATION)
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.acts, [])
        self.assertEqual(self.provider.calls, [])

    def test_a_reaction_judged_unrelated_settles_nothing_and_queues_no_self_review(self):
        reply = self.offered()
        self.rule = lambda act, items: ('none', 'unrelated', '')
        self.assertEqual(self.react(reply, '🤔'), 'owner_act')
        self.assertEqual(self.provider.calls, [])
        self.assertTrue(self.pending())
        with self.store.db() as db:
            reviews = db.execute("SELECT count(*) FROM owner_model_upkeep WHERE kind='self-review'").fetchone()[0]
        self.assertEqual(reviews, 0)

    def test_a_second_answer_to_the_same_preview_executes_nothing(self):
        reply = self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.react(reply, '👍')
        self.react(reply, '👍')
        self.receive('승인')
        self.assertEqual(len(self.provider.calls), 1)


class JudgmentTests(unittest.TestCase):
    ITEMS = [{'id': 'item-1', 'kind': 'calendar-drafts', 'shown_text': 'preview', 'shown_at': 'just now',
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
