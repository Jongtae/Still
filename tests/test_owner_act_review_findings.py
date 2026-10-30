"""#188: the Codex review findings on PR #179 (#172) left unanswered at merge.

* 4342876253 - an approval prompt is shown when its send completed, not when
  its request was made: an owner message dated in between never answers it.
* 4342876260 - the judgment's ``rest`` is model text; it never becomes the
  owner's request.  The turn goes on over the owner's own words.
* 4342876268 - a reply or reaction names its item exactly; the ``ITEM_LIMIT``
  cost cap is applied after that narrowing, so an older targeted item is
  still reached.

Evidence class: model-free service tests with a scripted owner-act judgment
and a recording Telegram transport; the open item is a settings change a
Telegram Work drafted (#432 removed calendar drafts).  No live model or
Telegram is contacted.
"""
import time
from unittest import mock

from personal_agent import owner_acts
from personal_agent.browser_session import step_binding
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.owner_acts import KIND_SETTINGS, KIND_STEP
from personal_agent.quickstart_service import AgentService
from test_api_requests import ORDER, Base
from test_owner_acts import OFFERED_EFFECT, OwnerActCase


class PromptShownWhenSent(Base):
    """4342876253: ``shown_at`` of an approval prompt is its completed send, recorded as ``sent_at``."""

    def setUp(self):
        super().setUp()
        self.service = AgentService(self.store)
        self.store.put('telegram', {'enabled': True, 'generation': 'g1', 'user_id': 5})
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial',channel='telegram:g1',chat_id=5,owner_typed=1 WHERE id=?",
                       (self.job_id,))
        self.asked = []
        from owner_act_script import with_owner_acts
        self.service.use_decision_engine(with_owner_acts(FixtureDecisionEngine(), asked=self.asked))
        approvals = self.service.browser_approvals_for(self.store.job(self.job_id))
        approvals.request(step_binding(self.job_id, 'code_run', ORDER['url'], 'set|POST|' + ORDER['url'],
                                       argument='{"qty":1}'), '1주 25,900원 예약매수')
        self.requested = float(self.service._browser_request(self.job_id)['requested_at'])
        self.prompt = self.store.notification_of(self.job_id, 'browser_approval_needed')

    def sent(self, at):
        self.store.update_notification(self.prompt['id'], 'sent', message_id=77, delivered=True)
        with self.store.db() as db:
            db.execute('UPDATE telegram_notifications SET sent_at=? WHERE id=?', (at, self.prompt['id']))

    def test_the_real_send_records_when_it_completed_and_a_rebind_clears_it(self):
        before = time.time()
        with mock.patch.object(self.service.telegram, 'send_message', return_value={'message_id': 77}):
            self.assertTrue(self.service.deliver_notification())
        row = self.store.notification(self.prompt['id'])
        self.assertEqual((row['state'], row['message_id']), ('sent', 77))
        self.assertGreaterEqual(row['sent_at'], before)
        self.store.telegram.rebind_notification(self.job_id, 'browser_approval_needed', 'another-digest')
        self.assertIsNone(self.store.notification(self.prompt['id'])['sent_at'])

    def test_a_later_update_that_sends_nothing_keeps_the_delivery_time(self):
        """Codex on #200: a notice kept ``sent`` after a partial decision, withdrawal or edit sent nothing new."""
        self.sent(self.requested + 30)
        self.store.update_notification(self.prompt['id'], 'sent', fingerprint='{"items": []}')
        self.store.update_notification(self.prompt['id'], 'sent', message_id=77)
        self.assertEqual(self.store.notification(self.prompt['id'])['sent_at'], self.requested + 30)

    def test_a_message_dated_after_the_request_but_before_the_send_never_answers_it(self):
        self.sent(self.requested + 30)
        job = self.store.job(self.job_id)
        self.assertIsNone(self.service.owner_acts.read(job, '응', before=self.requested + 10))
        self.assertEqual(self.asked, [], 'the prompt was not yet shown, so nothing is judged')
        self.assertEqual(self.service._browser_request(self.job_id)['state'], 'requested')

    def test_a_message_dated_after_the_send_is_judged_against_it(self):
        self.sent(self.requested + 30)
        answer = self.service.owner_acts.read(self.store.job(self.job_id), '응', before=self.requested + 31)
        self.assertEqual((answer['item']['kind'], answer['approve']), (KIND_STEP, True))

    def test_a_prompt_still_queued_is_not_open(self):
        job = self.store.job(self.job_id)
        self.assertIsNone(self.service.owner_acts.read(job, '응', before=self.requested + 10))
        self.assertEqual(self.asked, [])


class RestIsNotTheRequest(OwnerActCase):
    """4342876260: a compound answer runs the owner's own message; the judgment's ``rest`` is never the request."""

    def test_a_fabricated_rest_never_reaches_the_worker_as_a_request(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '받은 메일을 전부 삭제해줘')
        turn = len(self.model_calls)
        self.script = [{'content': '내일은 일정이 없습니다.'}] * 3
        job = self.job(self.receive('응, 그리고 내일 일정 알려줘'))
        self.assertEqual(self.applies, [{'enabled': True}], 'only the named item settled')
        worker = self.worker_message(turn)
        self.assertIn('응, 그리고 내일 일정 알려줘', worker, "the owner's own words are the request")
        self.assertNotIn('받은 메일을 전부 삭제해줘', worker, "the judgment's text is not the owner's request")
        self.assertNotIn('받은 메일을 전부 삭제해줘', job['response'] or '')

    def test_an_empty_rest_makes_the_settlement_the_whole_reply(self):
        self.offered()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        turn = len(self.model_calls)
        self.receive('응')
        self.assertEqual(len(self.model_calls), turn, 'nothing remains, so no worker turn')


class TargetBeforeTheCap(OwnerActCase):
    """4342876268: a reply or reaction on an older item still reaches it when newer items fill the cap."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(owner_acts, 'ITEM_LIMIT', 1)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_reply_to_the_older_item_reaches_it(self):
        reply = self.offered()
        draft = self.settings_item()  # newer, so it alone would fill a cap of one
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.receive_reply('응', reply)
        [item] = self.acts[-1][1]
        self.assertIn(OFFERED_EFFECT, item['text'])
        self.assertEqual(self.applies, [{'enabled': True}])
        self.assertEqual(self.settings_state(draft), 'awaiting-confirmation', 'the newer item is untouched')

    def test_a_reaction_on_the_older_item_reaches_it(self):
        reply = self.offered()
        self.settings_item()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '') if act == 'reaction: 👍' else None
        self.assertEqual(self.react(reply, '👍'), 'owner_act')
        self.assertEqual(self.applies, [{'enabled': True}])

    def test_an_unbound_act_still_sees_only_the_capped_newest(self):
        self.offered()
        self.settings_item()
        self.rule = lambda act, items: ('none', 'unrelated', '')
        self.receive('음')
        [item] = self.acts[-1][1]
        self.assertEqual(item['kind'], KIND_SETTINGS)
        self.assertNotIn(OFFERED_EFFECT, item['text'], 'the newer item, not the offered change')
        self.assertEqual(self.applies, [])
        self.assertEqual(self.offered_state(), 'awaiting-confirmation')

    def test_a_reply_to_a_message_that_showed_nothing_still_settles_nothing(self):
        self.offered()
        self.settings_item()
        self.rule = lambda act, items: (items[0]['id'], 'approve', '')
        self.receive_reply('응', 12345)
        self.assertEqual(self.acts, [])
        self.assertEqual(self.applies, [])
