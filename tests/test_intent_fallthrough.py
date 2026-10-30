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
#: Two retired keyword rules (saved-item search and note) once called this ambiguous (#173).
AMBIGUOUS = '내 기록에서 여권 번호 찾아서 메모해줘'
#: A turn the scripted capability judgment reads as several tasks (#672 review).
SEVERAL = '내일 회의 일정 잡고 메모도 남겨줘'


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

    def test_a_calendar_create_reaches_the_worker_with_the_owner_words_only(self):
        # #22: no kernel rule reads the date from the words any more, so there is
        # neither a Still-held question nor a parse note; the worker reads the
        # message and what it points to, and (#432) adds the event itself through
        # api_request's google-calendar slot.
        self.use_needs({LINKED_EVENT: 'calendar-create'})
        registry = ConnectorRegistry(self.store, (CALENDAR_SPEC, CALENDAR_WRITE_SPEC))
        registry.transition('local-owner', CALENDAR_SPEC.connector_id, ConnectorState.CONNECTED,
                            granted_scopes=CALENDAR_SPEC.required_scopes)
        registry.transition('local-owner', CALENDAR_WRITE_SPEC.connector_id, ConnectorState.CONNECTED,
                            granted_scopes=(CALENDAR_WRITE_SCOPE,))
        self.service.calendar = CalendarConnector(self.store, object(), registry=registry)
        # The slot's token is read only when a call is made; no call is made here.
        self.service.connector_registry, self.service.calendar_oauth = registry, object()
        self.script = [{'content': '페이지에서 날짜를 확인해 일정을 추가할게요.'}] * 2
        work = self.receive(LINKED_EVENT)
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertNotIn('언제인가요', job['response'])
        sent = self.worker_message()
        self.assertTrue(sent.startswith(LINKED_EVENT), 'the owner words, link included, reach the worker verbatim')
        self.assertNotIn('could not read the event', sent)
        self.assertNotIn(NOTE, sent, 'a connected calendar needs no note')
        tools = {tool['function']['name'] for tool in self.model_calls[0].get('tools', [])}
        self.assertTrue({'public_page_read', 'calendar_query', 'api_request'} <= tools, tools)
        self.assertFalse(self.service.connection_slots(job)['google-calendar']['read_only'],
                         'the worker changes the calendar through the write-granted slot')

    def test_a_former_two_rule_turn_reaches_the_worker_verbatim_with_no_rule_note(self):
        # #173: no keyword rule reads this turn any more, so nothing is ambiguous
        # and no rule note is added: the worker reads the owner's words alone.
        self.use_needs({})
        self.script = [{'content': '여권 번호를 찾아서 메모로 남길게요.'}] * 2
        work = self.receive(AMBIGUOUS)
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertNotIn('아무 작업도 실행하지 않았습니다', job['response'])
        self.assertEqual(self.worker_message(), AMBIGUOUS)
        self.assertEqual(self.store.notes(), [], 'no rule ran one part of the turn')

    def test_a_judged_several_tasks_turn_reaches_the_worker_verbatim_with_a_note(self):
        self.use_needs({SEVERAL: 'several-tasks'})
        self.script = [{'content': '일정과 메모를 차례로 처리할게요.'}] * 2
        work = self.receive(SEVERAL)
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertNotIn('아무 작업도 실행하지 않았습니다', job['response'])
        sent = self.worker_message()
        self.assertTrue(sent.startswith(SEVERAL))
        self.assertIn(NOTE, sent)
        self.assertIn('more than one task', sent)
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

    def test_without_an_ai_route_the_judged_clarification_keeps_its_own_truthful_answer(self):
        # Nothing could answer instead: the canned reply stays (no setup blocker is invented).
        self.use_needs({SEVERAL: 'several-tasks'})
        self.store.put('model', {})
        work = self.receive(SEVERAL)
        job = self.store.job(work)
        self.assertIn('아무 작업도 실행하지 않았습니다', job['response'])
        self.assertEqual(self.model_calls, [])


if __name__ == '__main__':
    unittest.main()
