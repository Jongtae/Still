"""ARCH-THIN-02 (#832): a pre-worker rule decision falls through to the AI worker.

Census rows A25, B13 and B14 (``docs/request-path-rule-census.en.md``).  A
natural-language rule decision no longer answers or fails on its own before
any AI runs: the Work model loop receives the owner's verbatim message plus a
factual note.  An unavailable connector is a note, never a terminal failure;
a canned ambiguous reply is a note; an over-long Telegram message reaches the
worker truncated with a note instead of being dropped.

Evidence class: model-free, fixture transports.  A scripted compatible-API
model, a recording Telegram transport and a FixtureDecisionEngine replace only
the providers; ``AgentService`` (Telegram ingress, ``run_one``), ``run_agent``
and ``Capabilities`` run unchanged.  No live model, Telegram or calendar is
contacted, and none is claimed.
"""
import unittest

from personal_agent.calendar import CALENDAR_SPEC, CALENDAR_WRITE_SPEC, CalendarConnector
from personal_agent.calendar_conversation import STATE_KEY
from personal_agent.connector_contract import ConnectorRegistry, ConnectorState
from personal_agent.google_calendar import CALENDAR_WRITE_SCOPE
from personal_agent.quickstart_service import MAX_OWNER_MESSAGE_CHARS
from scripted_capability_need import capability_need_engine
from test_preparations import _Case, call, engine, finish

NOTE = '[Still observation, not an owner instruction]'
#: A reminder the capability judgment reads as a calendar create (the #832 sweep case).
REMINDER = '이번 일요일 오전 7시 40분에 티타임 있어. 전날 밤 9시에 알려줘.'
#: The owner points at a page that holds the date; the words alone carry none (Still, 2026-10-17).
LINKED_EVENT = ('https://reg.githubuniverse.com/flow/github/universe26/getnotified/page/getnotified-success\n\n'
                '나 여기 등록했어. 해당 일정 추가해줘')
#: Two local rules match (saved-item search and note), so the rules call it ambiguous.
AMBIGUOUS = '내 기록에서 여권 번호 찾아서 메모해줘'


class IntentFallthroughTests(_Case):
    def use_needs(self, needs):
        self.judge = capability_need_engine(needs, base=engine())
        self.service.use_decision_engine(self.judge)

    def worker_message(self, index=0):
        return self.model_calls[index]['messages'][-1]['content']

    def test_a_calendar_create_without_the_connector_reaches_the_worker_which_schedules_a_preparation(self):
        self.use_needs({REMINDER: 'calendar-create'})
        self.assertIsNone(self.service.calendar_for_owner('local-owner'), 'no calendar in this install')
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='reminder',
                                                             goal='티타임 전날 알림', due=self.due_iso(3600))]},
                       finish('f', '1', summary='전날 밤 9시에 알려 드릴게요.')]
        work = self.receive(REMINDER)
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertNotIn('구성되어 있지 않습니다', job.get('error') or '')
        sent = self.worker_message()
        self.assertTrue(sent.startswith(REMINDER), 'the owner words reach the worker verbatim')
        self.assertIn(NOTE, sent)
        self.assertIn('Google Calendar is not connected in this install', sent)
        [row] = self.rows()
        self.assertEqual((row['kind'], row['state'], row['created_from']), ('reminder', 'scheduled', work))
        self.assertEqual(self.store.config('calendar_create', {}), {}, 'no rule-made calendar draft')

    def test_a_calendar_create_missing_its_date_reaches_the_worker_instead_of_asking_the_owner(self):
        self.use_needs({LINKED_EVENT: 'calendar-create'})
        registry = ConnectorRegistry(self.store, (CALENDAR_SPEC, CALENDAR_WRITE_SPEC))
        registry.transition('local-owner', CALENDAR_WRITE_SPEC.connector_id, ConnectorState.CONNECTED,
                            granted_scopes=(CALENDAR_WRITE_SCOPE,))
        self.service.calendar = CalendarConnector(self.store, object(), registry=registry)
        self.script = [{'content': '페이지에서 날짜를 확인해 초안을 만들게요.'}] * 2
        work = self.receive(LINKED_EVENT)
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertNotIn('언제인가요', job['response'])
        sent = self.worker_message()
        self.assertTrue(sent.startswith(LINKED_EVENT), 'the owner words, link included, reach the worker verbatim')
        self.assertIn(NOTE, sent)
        self.assertIn('could not read the event date and time from the message words alone', sent)
        self.assertIn('read what the message gives or points to', sent)
        tools = {tool['function']['name'] for tool in self.model_calls[0].get('tools', [])}
        self.assertTrue({'public_page_read', 'calendar_draft_create'} <= tools, tools)
        self.assertFalse(self.store.config(STATE_KEY, {}), 'no rule-held question is left pending')
        self.assertEqual(self.store.config('calendar_create', {}), {}, 'no rule-made calendar draft')

    def test_an_ambiguous_rule_result_reaches_the_worker_verbatim(self):
        self.use_needs({})
        self.script = [{'content': '여권 번호를 찾아서 메모로 남길게요.'}] * 2
        work = self.receive(AMBIGUOUS)
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertNotIn('아무 작업도 실행하지 않았습니다', job['response'])
        sent = self.worker_message()
        self.assertTrue(sent.startswith(AMBIGUOUS))
        self.assertIn(NOTE, sent)
        self.assertIn('matched more than one capability', sent)
        self.assertEqual(self.store.notes(), [], 'no rule ran one part of the turn')

    def test_a_long_telegram_message_reaches_the_worker_truncated_with_a_note(self):
        self.use_needs({})
        text = '회의록 요약해줘. ' + '가' * 14000
        self.script = [{'content': '요약입니다.'}] * 2
        work = self.receive(text)
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(len(job['message']), MAX_OWNER_MESSAGE_CHARS)
        sent = self.worker_message()
        self.assertTrue(sent.startswith('회의록 요약해줘. 가가가'))
        self.assertIn(f'This message had {len(text)} characters', sent)

    def test_without_an_ai_route_the_rule_keeps_its_own_truthful_answer(self):
        # Nothing could answer instead: the canned reply stays (no setup blocker is invented).
        self.use_needs({})
        self.store.put('model', {})
        work = self.receive(AMBIGUOUS)
        job = self.store.job(work)
        self.assertIn('아무 작업도 실행하지 않았습니다', job['response'])
        self.assertEqual(self.model_calls, [])


if __name__ == '__main__':
    unittest.main()
