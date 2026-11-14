"""#818: the owner's own data is not masked in Still's own flows; stored secrets still are.

The Work's saved notes and Memory (the owner's data) used to be replaced by a
mask marker whenever the same words came back in the Work's answer, a kept
prepared answer, a snapshot, a status line or a judgment input.  These tests
drive normal entry points only (a scheduled run, the next run, the watch
judgment, the nightly look-back, a browser snapshot, a progress line, public
lookups) with fake models/transports and a fake clock, and assert what the
owner's AI or the owner actually receives.

A stored secret (the real secrets store behind ``redact_known_secrets``) and
a guarded browser field (password, card number) stay replaced/absent.

Evidence class: model-free unit and service tests.  No network, no live model.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from personal_agent import agent_runtime
from personal_agent import browser_session as bs
from personal_agent import preparations as prep
from personal_agent.agent_runtime import Capabilities, run_agent
from personal_agent.current_context import redact_known_secrets
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore
from personal_agent.telegram_presence import MASK_MARK

from test_browser_session import PAGES, ORIGIN, FakeDriver, PASSWORD
from test_owner_model_dream import Dream, dream
from test_preparations import SECRET, _Case, _WatchCase, call, finish, watch_engine

NOTE = '비자번호 V7654321'
ANSWER = f'{NOTE} 을 메모했고 준비물은 여권, 충전기입니다.'
MEMORY = '보관 위치 M1234567'


def assert_unmasked(test, text, *words):
    test.assertNotRegex(text, MASK_MARK.pattern)
    test.assertNotIn(bs.REDACTED, text)
    for word in words:
        test.assertIn(word, text)


def note_then_answer(answer=ANSWER, note=NOTE):
    """The Work's script: save a note, then finish with an answer that repeats its words."""
    return [{'content': None, 'tool_calls': [call('1', 'save_note', content=note)]}, finish('f', '1', summary=answer)]


class PreparedRun(_Case):
    """1.-2. A scheduled run that saves a note and answers with the same words."""

    def run_first(self, **kwargs):
        row = self.scheduled(goal='여행 준비물 정리', seconds=900, channel='telegram', **kwargs)
        self.now += 1000
        self.tick()
        self.script = note_then_answer()
        self.assertTrue(self.service.run_one())
        self.tick()
        self.service.deliver_one()
        self.tick()
        self.tick()
        return row

    def test_the_run_keeps_and_delivers_its_answer_unchanged(self):
        row = self.run_first()
        [work] = self.runs(row['id'])
        self.assertEqual(work['response'], ANSWER)
        self.assertEqual(work['delivery'], 'sent')
        self.assertEqual(self.service.preparations.get(row['id'])['prepared_text'], ANSWER)
        [delivered] = self.texts()
        assert_unmasked(self, delivered, ANSWER)

    def test_the_next_run_receives_the_previous_answer_unchanged(self):
        row = self.run_first(recurrence='daily')
        self.now += 86400
        seen = []
        self.script = [lambda body: seen.append(json.dumps(body['messages'], ensure_ascii=False)) or {'content': '확인'}] * 2
        self.assertTrue(self.tick())
        self.assertTrue(self.service.run_one())
        self.assertEqual(len(self.runs(row['id'])), 2)
        assert_unmasked(self, seen[0], ANSWER)

    def test_the_watch_judgment_receives_the_run_result_unchanged(self):
        class Watch(_WatchCase):
            def runTest(self):
                pass
        case = Watch()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.judge = watch_engine([False] * 5)
        case.service.use_decision_engine(case.judge)
        case.watched = case.watch()
        case.now = case.service.preparations.get(case.watched['id'])['due_at'] + 1
        case.assertTrue(case.tick())
        case.script = note_then_answer()
        case.assertTrue(case.service.run_one())
        case.service.deliver_one()
        case.assertTrue(case.tick())
        [asked] = case.watch_asks()
        assert_unmasked(self, asked.facts['run_result'], ANSWER)
        self.assertEqual(asked.facts['run_result'], ANSWER)

    def test_a_stored_secret_in_the_answer_is_still_replaced(self):
        answer = f'{NOTE} 메모했고 토큰은 {SECRET} 입니다.'
        row = self.scheduled(goal='여행 준비물 정리', seconds=900, channel='telegram')
        self.now += 1000
        self.tick()
        self.script = note_then_answer(answer)
        self.assertTrue(self.service.run_one())
        self.tick()
        self.service.deliver_one()
        self.tick()
        self.tick()
        [work] = self.runs(row['id'])
        self.assertTrue(self.texts(), 'the answer was delivered')
        for kept in (work['response'], self.service.preparations.get(row['id'])['prepared_text'], *self.texts()):
            self.assertNotIn(SECRET, str(kept))
            self.assertIn('토큰은', str(kept), 'the rest of the answer is still delivered')


class NightlyLookBack(Dream):
    """2. The nightly look-back reads the turns' answers unchanged."""

    def test_the_look_back_receives_a_turn_that_repeats_a_saved_note_unchanged(self):
        job = self.settled('여행 준비물 정리해 줘', answer=ANSWER)
        with self.store.db() as db:
            db.execute('INSERT INTO notes(id,content,created) VALUES (?,?,?)', (job, NOTE, time.time()))
        self.settled('둘째 요청')
        self.settled('셋째 요청')
        self.dreams = [dream()]
        self.tick()
        [(context, _question, _schema)] = self.asked
        assert_unmasked(self, context.facts['turns'], ANSWER)


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        with self.store.db() as db:
            db.execute("INSERT INTO jobs(id,request_key,message,status,created,owner_typed) VALUES ('job','k-job',?,'running',?,1)",
                       ('여행 준비 도와줘', time.time()))
        self.events = []
        self.wire = Wire()

    def caps(self, **kwargs):
        return Capabilities(self.store, ModelAdapter(lambda *a, **k: {}), {}, '', 'job',
                            lambda *event: self.events.append(event), network=self.wire, **kwargs)


class Wire:
    def __init__(self):
        self.plans = []

    def execute(self, plan):
        self.plans.append(dict(plan))
        if plan['tool'] == 'weather':
            return {'tool': 'weather', 'location': {'name': plan['city']}, 'sources': ['https://open-meteo.com/'],
                    'retrieved_at': 1, 'forecast': {}}
        return {'tool': plan['tool'], 'query': plan.get('query'), 'results': [], 'sources': ['https://example.org/'],
                'retrieved_at': 1}


class BrowserSnapshot(Fixture):
    """3. A page that says what the Work saved shows it; guarded fields stay out."""

    def test_page_text_with_a_saved_memorys_words_is_shown_unchanged(self):
        caps = self.caps(browser=lambda: FakeDriver())
        saved = caps.execute('save_memory', {'memory_key': 'storage.place', 'content': MEMORY})
        self.assertNotIn('error', json.dumps(saved))
        page = caps.execute('browser_open', {'url': ORIGIN + '/product', 'effect': 'read'})
        self.assertEqual(page['state'], 'page')
        assert_unmasked(self, page['text'], MEMORY)
        self.assertIn('가격 12,900원', page['text'])
        caps.close_browser()

    def test_a_saved_note_s_words_on_a_page_title_and_elements_are_shown_unchanged(self):
        caps = self.caps(browser=lambda: FakeDriver())
        caps.execute('save_note', {'content': '세탁세제 3L'})
        page = caps.execute('browser_open', {'url': ORIGIN + '/product', 'effect': 'read'})
        self.assertEqual(page['title'], '세탁세제 3L')
        assert_unmasked(self, json.dumps(page, ensure_ascii=False), '세탁세제 3L')
        caps.close_browser()

    def test_a_guarded_field_value_stays_absent_even_when_the_work_saved_memory(self):
        caps = self.caps(browser=lambda: FakeDriver())
        caps.execute('save_memory', {'memory_key': 'storage.place', 'content': MEMORY})
        account = json.dumps(caps.execute('browser_open', {'url': ORIGIN + '/account', 'effect': 'read'}), ensure_ascii=False)
        self.assertNotIn(PASSWORD, account)
        self.assertNotIn('915533', account)
        checkout = json.dumps(caps.execute('browser_open', {'url': ORIGIN + '/checkout-compound', 'effect': 'read'}),
                              ensure_ascii=False)
        for value in ('4242424242424242', '987', '246810'):
            self.assertNotIn(value, checkout)
        caps.close_browser()


class StatusLine(Fixture):
    """4. The running line of a tool call keeps the owner's words."""

    def test_a_status_and_query_that_repeat_a_saved_note_are_unchanged(self):
        status = f'{NOTE} 관련 자료 찾는 중'
        replies = [
            {'choices': [{'message': {'content': None, 'tool_calls': [call('1', 'save_note', content=NOTE)]}}]},
            {'choices': [{'message': {'content': None, 'tool_calls': [
                call('2', 'web_search', query=f'{NOTE} 신청 방법', status=status)]}}]},
            {'choices': [{'message': {'content': '찾았습니다.'}}]},
            {'choices': [{'message': {'content': '찾았습니다.'}}]},
        ]
        caps = self.caps()
        run_agent(ModelAdapter(lambda *a, **k: replies.pop(0)), {'provider': 'compatible', 'endpoint': 'http://127.0.0.1:9', 'model': 'm'},
                  '', [{'role': 'user', 'content': '여행 준비 도와줘'}], '', caps, lambda *e: self.events.append(e))
        running = [json.loads(detail) for tool, state, detail in self.events if tool == 'web_search' and state == 'running']
        [step] = [item['step'] for item in running]
        self.assertEqual(step['status'], status)
        self.assertEqual(step['query'], f'{NOTE} 신청 방법')
        assert_unmasked(self, json.dumps(step, ensure_ascii=False), NOTE)

    def test_a_stored_secret_in_a_status_line_is_still_replaced(self):
        self.store.secret('telegram_token', SECRET)
        redactor = lambda text: redact_known_secrets(self.store, text)
        caps = self.caps(secret_redactor=redactor)
        caps.execute('save_note', {'content': NOTE})
        step = agent_runtime.progress_step('web_search', {'query': f'{NOTE} {SECRET}'}, f'{NOTE} {SECRET} 찾는 중',
                                           caps.judgment_text)
        self.assertNotIn(SECRET, json.dumps(step, ensure_ascii=False))
        self.assertIn('찾는 중', step['status'])


class PublicLookups(Fixture):
    """6. web_search and weather send the worker's own words, owner-saved words included."""

    def test_web_search_sends_an_owner_saved_word_unchanged(self):
        caps = self.caps()
        caps.execute('save_note', {'content': NOTE})
        caps.execute('web_search', {'query': f'{NOTE} 발급 방법'})
        self.assertEqual(self.wire.plans, [{'tool': 'web_search', 'query': f'{NOTE} 발급 방법'}])

    def test_weather_sends_an_owner_saved_place_unchanged(self):
        caps = self.caps()
        caps.execute('save_note', {'content': '대전 유성구'})
        caps.execute('weather', {'city': '유성구', 'country': 'KR'})
        self.assertEqual(self.wire.plans, [{'tool': 'weather', 'city': '유성구', 'country': 'KR'}])

    def test_the_runtime_no_longer_computes_a_lookup_exclusion_set(self):
        # The lookup binding stays (a stopped Work still cannot look up); its exclusion set is gone.
        caps = self.caps()
        caps.execute('save_note', {'content': NOTE})
        self.assertEqual(agent_runtime.lookup_sources(self.store, 'job'), {'current': '여행 준비 도와줘'})
        self.assertFalse(hasattr(agent_runtime, 'work_written_values'))

    def test_a_stopped_work_still_cannot_look_up(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id='job'")
        with self.assertRaises(ValueError):
            agent_runtime.lookup_sources(self.store, 'job')


class NoNewSuppressionRule(_Case):
    """The withdrawn #818 design item 4: a reply mostly made of redaction marks is still delivered."""

    def test_a_reply_mostly_of_secret_marks_is_delivered_and_not_failed(self):
        answer = ' '.join([SECRET] * 10) + ' 정리했습니다.'
        row = self.scheduled(goal='여행 준비물 정리', seconds=900, channel='telegram')
        self.now += 1000
        self.tick()
        self.script = note_then_answer(answer)
        self.assertTrue(self.service.run_one())
        self.tick()
        self.service.deliver_one()
        self.tick()
        self.tick()
        [work] = self.runs(row['id'])
        self.assertEqual(work['status'], 'succeeded')
        self.assertEqual(work['delivery'], 'sent')
        self.assertFalse(work['error'])
        [delivered] = self.texts()
        self.assertNotIn(SECRET, delivered)
        self.assertIn('정리했습니다.', delivered)


if __name__ == '__main__':
    unittest.main()
