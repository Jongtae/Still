"""AUDIT-OUTBOUND-01 (#167, #28 option A): held values seen in outbound tool text are shown, never blocked.

After an outbound call Still mediates (a search or weather lookup, a public
page read, ``browser_open``, ``browser_type``, ``api_request``, a code run's
requests), its outbound text is compared with the values Still holds for the
owner - current Memory whichever Work saved it, and this Work's own private
writes - using the #605 matcher.  Each match is recorded on the call's
Evidence as labels only (where the value is kept, the tool, the destination
host) and shown in the information-use view.  Nothing is blocked or changed,
and neither the value nor the outbound text is stored.

Evidence class: unit and controlled local integration with injected model
transports, a fake browser driver over fixture pages, fake public networks and
temporary stores.  No model, provider, network, account or owner data is used.
"""
import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import agent_runtime, information_use, mcp_bridge
from personal_agent.agent_runtime import (Capabilities, held_value_matches, outbound_evidence, outbound_texts,
                                          evidence_summary, run_agent)
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore

from test_browser_session import CFG as BROWSER_CFG, ORIGIN, FakeDriver, Script, call
from test_information_use import Wire, _ServiceBase, steps

MEMBER = 'M1234-5678'
PHONE = '010-9876-5432'
PLACE = '합정 분짜집 비밀메뉴'


def held(*rows):
    return [('memory', key, value) for key, value in rows]


class Matcher(unittest.TestCase):
    """The #605 matcher decides a match; the result is labels, never the value or the text."""

    HELD = held(('member-number', MEMBER), ('phone', PHONE), ('favorite-place', PLACE))

    def keys(self, text):
        return [row['key'] for row in held_value_matches([(text, 'search.example')], self.HELD)]

    def test_spelling_variants_the_matcher_covers_are_detected(self):
        for text, key in (('회원 m1234 5678 혜택', 'member-number'),      # spacing and case
                          ('M12345678 조회', 'member-number'),             # digit run without its separator
                          ('연락처 01098765432', 'phone'),                 # phone digits joined
                          ('010 9876 5432 누구', 'phone'),                 # digits split by spaces
                          ('합정분짜집 메뉴', 'favorite-place')):           # words joined without a space
            with self.subTest(text=text):
                self.assertEqual(self.keys(text), [key])

    def test_unrelated_text_does_not_match(self):
        for text in ('서울 내일 날씨', 'python list sort', '2026 공휴일', '회원 가입 방법'):
            with self.subTest(text=text):
                self.assertEqual(self.keys(text), [])

    def test_a_match_is_labels_only(self):
        [row] = held_value_matches([('m1234 5678 혜택', 'search.example')], self.HELD)
        self.assertEqual(row, {'source': 'memory', 'key': 'member-number', 'destination': 'search.example'})
        self.assertNotIn('1234', json.dumps(row))


FIXTURE = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'held_values_multilingual.json').read_text(encoding='utf-8'))


class MultilingualMemory(unittest.TestCase):
    """#245: on a realistic ko/en/ja/zh Memory, an everyday word shared with a value is not a held value going out.

    Measured on ``fixtures/held_values_multilingual.json`` (65 Memory rows, 68 unrelated outbound
    texts, 33 carrying a held value).  Before #245 the matcher labelled 57 of the 68 unrelated texts
    (88 labels); the bounds below pin what remains, so a looser matcher fails here.
    """

    HELD = [('memory', key, value) for key, value in FIXTURE['memory']]
    UNRELATED_TEXTS_MATCHED_MAX = 21
    UNRELATED_LABELS_MAX = 23
    WRONG_LABELS_ON_CARRIED_MAX = 1

    def keys(self, text, held=None):
        return [row['key'] for row in held_value_matches([(text, 'search.example')], held or self.HELD)]

    def test_false_matches_stay_bounded_and_every_carried_value_is_still_found(self):
        false = {text: keys for text in FIXTURE['unrelated'] if (keys := self.keys(text))}
        self.assertLessEqual(len(false), self.UNRELATED_TEXTS_MATCHED_MAX, false)
        self.assertLessEqual(sum(map(len, false.values())), self.UNRELATED_LABELS_MAX, false)
        wrong = []
        for text, expected in FIXTURE['carried']:
            with self.subTest(text=text):
                found = self.keys(text)
                self.assertIn(expected[0], found, 'a carried value is still found')
                wrong.extend(key for key in found if key not in expected)
        self.assertLessEqual(len(wrong), self.WRONG_LABELS_ON_CARRIED_MAX, wrong)

    def test_everyday_words_shared_with_a_value_are_not_matches(self):
        for text in ('생일 선물 추천',            # 생일 of "아내 생일은 3월 14일"
                     '저녁 메뉴 추천',            # 저녁 of four rows
                     '초등학생 수학 학원 추천',   # 초등 inside 서초초등학교
                     '병원 예약 방법', '가족 여행지 추천', '등산화 추천', '/search q=주말 날씨',
                     'marathon training plan', 'flat white vs latte', 'book club questions',
                     'weather in Tokyo tomorrow', '東京 天気 明日', '京都 観光', '上海 天气', '出差 注意事项'):
            with self.subTest(text=text):
                self.assertEqual(self.keys(text), [])
        # The same words with the value's own specific part still match.
        self.assertEqual(self.keys('김민지 생일 선물'), ['family.wife-name'])
        self.assertEqual(self.keys('아내 생일 3월 14일'), ['family.wife-birthday'])
        self.assertEqual(self.keys('Chuncheon marathon October'), ['hobby.running'])
        self.assertEqual(self.keys('柊家 京都 旅館'), ['travel.ryokan'])

    def test_hangul_spelled_in_bare_jamo_still_matches(self):
        rows = held(('name', '김철수'))
        for text in ('기ᄆ처ᄅ수님', 'ㄱㅣㅁㅊㅓㄹㅅㅜ', '김철수님 연락처'):
            with self.subTest(text=text):
                self.assertEqual(self.keys(text, rows), ['name'])


class ComparisonCut(unittest.TestCase):
    """#245: a comparison cut off by its bounds is named on the Evidence and in the view, never silent."""

    def test_text_past_the_compared_length_is_named_and_text_within_it_is_not(self):
        caps = Recording.Caps(held(('member-number', MEMBER)))
        long_text = '가' * agent_runtime.OUTBOUND_TEXT_MAX + ' ' + MEMBER
        added = outbound_evidence(caps, 'browser_type', {'text': long_text}, {'url': 'https://a.example/'})
        self.assertNotIn('outbound_matches', added, 'the value after the compared characters is not seen')
        self.assertEqual(added['outbound_comparison_cut'], [{'kind': 'text_chars', 'limit': agent_runtime.OUTBOUND_TEXT_MAX}])
        short = outbound_evidence(caps, 'browser_type', {'text': MEMBER}, {'url': 'https://a.example/'})
        self.assertNotIn('outbound_comparison_cut', short)
        self.assertEqual(outbound_evidence(Recording.Caps(), 'browser_type', {'text': long_text}, {'url': 'https://a.example/'}), {},
                         'nothing held: nothing was compared, so nothing was cut off')
        self.assertEqual(agent_runtime.attempted_outbound(caps, 'browser_type', {'text': long_text}, RuntimeError('x')),
                         {'outbound_comparison_cut': [{'kind': 'text_chars', 'limit': agent_runtime.OUTBOUND_TEXT_MAX}]},
                         'a failed attempt names the cut too')

    def test_memory_past_the_compared_rows_is_named(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QuickStore(Path(folder) / 'state')
            for index in range(agent_runtime.HELD_MEMORY_MAX):
                store.save_memory(f'fact-{index}', f'value {index:04d} kept')
            job = store.enqueue('찾아줘', 'cut')
            caps = Capabilities(store, None, {}, '', job, lambda *a: None)
            self.assertEqual(len(caps.held_values()), agent_runtime.HELD_MEMORY_MAX)
            self.assertNotIn('outbound_comparison_cut', outbound_evidence(caps, 'browser_type', {'text': 'hello'}, {}),
                             'exactly the compared number of rows: nothing left out')
            store.save_memory('newest', 'value 9999 newest')
            self.assertEqual(len(caps.held_values()), agent_runtime.HELD_MEMORY_MAX)
            added = outbound_evidence(caps, 'browser_type', {'text': 'hello'}, {})
            self.assertEqual(added['outbound_comparison_cut'], [{'kind': 'memory_rows', 'limit': agent_runtime.HELD_MEMORY_MAX}])

    def test_the_view_says_where_the_comparison_was_cut_off_even_without_a_match(self):
        self.assertEqual(information_use.COMPARISON_CUTS,
                         (agent_runtime.COMPARISON_CUT_MEMORY, agent_runtime.COMPARISON_CUT_TEXT))
        with tempfile.TemporaryDirectory() as folder:
            store = QuickStore(Path(folder) / 'state')
            job = store.enqueue('찾아줘', 'cut')
            text_cut = {'kind': 'text_chars', 'limit': 1000}
            memory_cut = {'kind': 'memory_rows', 'limit': 500}
            rows = [('browser_type', 'succeeded', {'call_id': 'c1', 'host_action': 'browser_type',
                                                   'evidence': {'outbound_comparison_cut': [text_cut]}}),
                    ('web_search', 'failed', {'call_id': 'c2', 'host_action': 'web_search', 'error': 'x',
                                              'outbound_comparison_cut': [memory_cut, {'kind': 'other', 'limit': 3}]})]
            with store.db() as db:
                for tool, status, detail in rows:
                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                               (job, tool, status, json.dumps(detail), 1))
            audit = information_use.work_information_use(store, job)
            self.assertEqual(audit['sent_to']['held_value_matches'], [])
            self.assertEqual(audit['sent_to']['held_value_comparison_cut'], [memory_cut, text_cut])
            text = information_use.render_korean(audit)
            self.assertIn('- 보관 정보 비교는 최근 기억 500개까지만 했습니다.', text)
            self.assertIn('- 밖으로 나간 내용은 건마다 앞 1,000자까지만 보관 정보와 비교했습니다.', text)
            other = store.enqueue('다른 작업', 'plain')
            self.assertNotIn('held_value_comparison_cut', information_use.work_information_use(store, other)['sent_to'])


class OutboundTexts(unittest.TestCase):
    """What each mediated outbound call carried, and where; read for the comparison only."""

    def test_each_outbound_tool_gives_its_text_and_destination(self):
        cases = {
            'web_search': ({'query': 'raw words'}, {'sent': {'query': '보낸 검색어'}, 'provider': 'brave'},
                           [('보낸 검색어', 'brave')]),
            'weather': ({'city': '성남', 'country': 'kr'}, {'sent': {'city': '성남', 'country': 'KR'},
                                                         'sources': ['https://api.open-meteo.com/v1/forecast?x=1']},
                        [('성남', 'geocoding-api.open-meteo.com'), ('KR', 'geocoding-api.open-meteo.com')]),
            'public_page_read': ({'url': 'https://news.example/a/b?q=%ED%95%A9%EC%A0%95#M1234-5678'}, {},
                                 [('/a/b q=합정', 'news.example')]),
            'browser_open': ({'url': 'https://shop.example/', 'effect': 'read'}, {}, []),
            'browser_type': ({'target': '3', 'text': 'M1234-5678', 'effect': 'mutate'}, {'url': 'https://shop.example/form'},
                             [('M1234-5678', 'shop.example')]),
            'api_request': ({'slot': 's', 'url': 'https://api.example/v1/users?id=7', 'body': '{"phone": "010"}', 'effect': 'read'},
                            {}, [('/v1/users id=7 {"phone": "010"}', 'api.example')]),
            'code_run': ({'code': 'x'}, {'requests': [{'host': 'data.example', 'path': '/q?name=%EA%B9%80', 'allowed': True},
                                                      {'host': 'blocked.example', 'path': '/secret', 'allowed': False}]},
                         [('/q name=김', 'data.example')]),
        }
        for action, (args, result, expected) in cases.items():
            with self.subTest(action=action):
                self.assertEqual(outbound_texts(action, args, result), expected)

    def test_a_tool_that_sends_nothing_outside_has_no_text(self):
        self.assertEqual(outbound_texts('list_memory', {}, {}), [])
        self.assertEqual(outbound_evidence(object(), 'read_file', {'path': MEMBER}, {}), {})


class Recording(unittest.TestCase):
    """What a call's Evidence gains, and that the comparison can neither fail nor change a call."""

    class Caps:
        def __init__(self, values=(), error=None):
            self.values, self.error = list(values), error

        def held_values(self):
            if self.error:
                raise self.error
            return self.values

    def test_matches_are_added_with_the_tool_and_browser_open_names_its_destination(self):
        caps = self.Caps(held(('member-number', MEMBER)))
        added = outbound_evidence(caps, 'browser_open', {'url': 'https://shop.example/find?no=M1234-5678'}, {})
        self.assertEqual(added, {'destination': 'shop.example', 'outbound_matches': [
            {'source': 'memory', 'key': 'member-number', 'destination': 'shop.example', 'tool': 'browser_open'}]})
        self.assertEqual(outbound_evidence(caps, 'browser_open', {'url': 'https://shop.example/'}, {}),
                         {'destination': 'shop.example'})

    def test_a_redirect_to_another_host_is_compared_and_named_too(self):
        caps = self.Caps(held(('member-number', MEMBER)))
        added = outbound_evidence(caps, 'browser_open', {'url': 'http://shop.example/find?no=M1234-5678'},
                                  {'url': 'https://www.shop-cdn.example/find?no=M1234-5678'})
        self.assertEqual((added['destination'], added['landed']), ('shop.example', 'www.shop-cdn.example'))
        self.assertEqual([row['destination'] for row in added['outbound_matches']], ['shop.example', 'www.shop-cdn.example'])

    def test_a_withheld_browser_open_still_shows_its_match_and_host_as_an_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QuickStore(Path(folder) / 'state')
            job = store.enqueue('열어줘', 'withheld')
            detail = {'call_id': 'c1', 'host_action': 'browser_open', 'state': 'withheld', 'error': 'login required',
                      'evidence': {'state': 'login_required', 'url': 'https://shop.example/login', 'destination': 'shop.example',
                                   'outbound_matches': [{'source': 'memory', 'key': 'member-number',
                                                         'destination': 'shop.example', 'tool': 'browser_open'}]}}
            with store.db() as db:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job, 'browser_open', 'failed', json.dumps(detail), 1))
            audit = information_use.work_information_use(store, job)
            self.assertEqual(audit['sent_to']['browser_destinations'], ['shop.example'])
            self.assertEqual(audit['sent_to']['held_value_matches'], [{'source': 'memory', 'key': 'member-number',
                             'tool': 'browser_open', 'destination': 'shop.example', 'status': 'failed'}])

    def test_a_url_fragment_is_not_compared_because_it_is_not_sent(self):
        caps = self.Caps(held(('member-number', MEMBER)))
        self.assertEqual(outbound_evidence(caps, 'browser_open', {'url': 'https://shop.example/page#M1234-5678'}, {}),
                         {'destination': 'shop.example'})

    def test_a_code_run_compares_the_query_body_and_headers_its_runner_held(self):
        caps = self.Caps(held(('member-number', MEMBER), ('phone', PHONE)))
        caps._code_runner = type('Runner', (), {'sent': [('/v1/find\nno=M1234-5678', 'data.example'),
                                                         ('/v1/x\n{"tel": "010 9876 5432"}', 'other.example')]})()
        result = {'requests': [{'phase': 'run', 'method': 'GET', 'host': 'data.example', 'path': '/v1/find', 'allowed': True}]}
        added = outbound_evidence(caps, 'code_run', {'code': 'x'}, result)
        self.assertEqual(added['outbound_matches'], [
            {'source': 'memory', 'key': 'member-number', 'destination': 'data.example', 'tool': 'code_run'},
            {'source': 'memory', 'key': 'phone', 'destination': 'other.example', 'tool': 'code_run'}])
        self.assertNotIn('5678', json.dumps({**evidence_summary('code_run', result), **added}))

    def test_a_failing_comparison_leaves_the_evidence_as_before(self):
        caps = self.Caps(error=RuntimeError('store gone'))
        self.assertEqual(outbound_evidence(caps, 'browser_type', {'text': MEMBER}, {'url': 'https://a.example/'}), {})

    def test_an_api_request_keeps_its_body_out_of_the_evidence(self):
        caps = self.Caps(held(('phone', PHONE)))
        args = {'slot': 'crm', 'url': 'https://api.example/v1/contacts', 'body': json.dumps({'phone': PHONE}), 'effect': 'read'}
        result = {'data': {}, 'provenance': {'source': {'slot': 'crm', 'host': 'api.example', 'path': '/v1/contacts'}},
                  'sources': ['https://api.example/v1/contacts']}
        evidence = {**evidence_summary('api_request', result), **outbound_evidence(caps, 'api_request', args, result)}
        self.assertEqual(evidence['outbound_matches'], [{'source': 'memory', 'key': 'phone', 'destination': 'api.example',
                                                         'tool': 'api_request'}])
        recorded = json.dumps({'arguments': agent_runtime.recorded_arguments('api_request', args), 'evidence': evidence},
                              ensure_ascii=False)
        self.assertNotIn('9876', recorded, 'the body stays length-only and the match is a label')


class HeldValues(unittest.TestCase):
    """What Still holds: current Memory whichever Work saved it, then this Work's own private writes."""

    def test_memory_from_an_earlier_work_and_this_works_writes_are_held(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QuickStore(Path(folder) / 'state')
            earlier = store.enqueue('회원번호 기억해', 'earlier')
            store.save_memory('member-number', MEMBER, work_id=earlier)
            store.save_memory('old-phone', '010-1111-2222')
            store.save_memory('old-phone', PHONE)
            job = store.enqueue('혜택 찾아줘', 'later')
            caps = Capabilities(store, None, {}, '', job, lambda *a: None)
            caps.written_private = ['이번 작업 메모']
            self.assertEqual(sorted(caps.held_values()), [('memory', 'member-number', MEMBER), ('memory', 'old-phone', PHONE),
                                                          ('work_write', '', '이번 작업 메모')],
                             'a superseded Memory value is no longer held')


class BrowserRoute(unittest.TestCase):
    """The direct route through ``run_agent``: an earlier Work's Memory typed into a page is shown, not stored."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        earlier = self.store.enqueue('회원번호 기억해', 'earlier')
        self.store.save_memory('member-number', MEMBER, work_id=earlier)
        self.job = self.store.enqueue('쇼핑몰에서 회원 혜택 찾아줘', 'later')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (self.job,))

    def record(self, tool, status, detail):
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (self.job, tool, status, detail, time.time()))

    def test_typed_memory_value_is_labelled_with_the_page_host_and_never_stored(self):
        script = Script({'tool_calls': [call('1', 'browser_open', url=ORIGIN + '/product', effect='navigate')]},
                        {'tool_calls': [call('2', 'browser_type', target='검색어', text='m1234 5678', effect='mutate')]},
                        {'content': '검색어를 넣었습니다.'})
        driver = FakeDriver()
        caps = Capabilities(self.store, ModelAdapter(script), BROWSER_CFG, '', self.job, self.record, browser=lambda: driver)
        run_agent(caps.adapter, BROWSER_CFG, '', [{'role': 'user', 'content': '쇼핑몰에서 회원 혜택 찾아줘'}], '', caps, self.record)
        caps.close_browser()
        typed = [values for values in driver.values.values()]
        self.assertTrue(any('m1234 5678' in values.values() for values in typed), 'the text was typed as written')
        audit = information_use.work_information_use(self.store, self.job)
        self.assertEqual(audit['sent_to']['browser_destinations'], ['fixture.test'])
        self.assertEqual(audit['sent_to']['held_value_matches'], [
            {'source': 'memory', 'key': 'member-number', 'tool': 'browser_type', 'destination': 'fixture.test',
             'status': 'succeeded'}])
        text = information_use.render_korean(audit)
        self.assertIn("- browser_type → fixture.test: 기억 'member-number'의 값과 겹치는 내용이 들어 있었습니다", text)
        self.assertIn('- 브라우저로 연 곳: fixture.test', text)
        # Negative: no persisted row and no Evidence holds the value or the typed text.
        with self.store.db() as db:
            details = ' '.join(row['detail'] for row in db.execute('SELECT detail FROM tool_events WHERE job_id=?', (self.job,)))
        for fragment in ('m1234', 'M1234', '5678'):
            self.assertNotIn(fragment, details)
            self.assertNotIn(fragment, json.dumps(audit, ensure_ascii=False))
            self.assertNotIn(fragment, json.dumps(self.store.evidence_summary(self.job), ensure_ascii=False))


class ApiRoute(_ServiceBase):
    """The shipped service: a later Work's search carries an earlier Work's Memory; the lookup still goes out."""

    class ProviderWire(Wire):
        def execute(self, plan):
            return {**super().execute(plan), 'provider': 'brave'}

    def service(self, script):
        from personal_agent.quickstart_service import AgentService
        from test_information_use import CFG
        service = AgentService(self.store, adapter=ModelAdapter(lambda url, body, *a, **k: script(body['messages'])))
        service.local_tools = self.wire
        self.store.put('model', CFG)
        self.store.put('model_test', {'ok': True, 'tools_ok': True, 'time': 9999999999,
                                      'fingerprint': service.model_fingerprint(CFG)})
        return service

    def test_an_earlier_works_memory_in_a_later_search_is_shown_and_the_search_still_goes_out(self):
        self.wire = self.ProviderWire()
        service = self.service(steps([('save_memory', {'memory_key': 'member-number', 'content': MEMBER})]))
        self.turn(service, f'내 회원번호는 {MEMBER}야. 기억해')
        service = self.service(steps([('web_search', {'query': 'm1234 5678 회원 혜택'}),
                                      ('web_search', {'query': '서울 내일 날씨'})]))
        job = self.turn(service, '회원 혜택이랑 날씨 찾아줘')
        self.assertEqual(sorted(plan['query'] for plan in self.wire.plans), ['m1234 5678 회원 혜택', '서울 내일 날씨'],
                         'detection only: both lookups went out as written')
        audit = service.work_information_use(job)
        self.assertEqual(audit['sent_to']['held_value_matches'], [
            {'source': 'memory', 'key': 'member-number', 'tool': 'web_search', 'destination': 'brave', 'status': 'succeeded'}])
        text = information_use.render_korean(audit)
        self.assertIn("- web_search → brave: 기억 'member-number'의 값과 겹치는 내용이 들어 있었습니다", text)
        self.assertEqual(text.count('선택한 AI 제공자가 처리합니다'), 1, 'said once')
        self.assertNotIn('AI CLI가 스스로 하는 웹 검색', text, 'no CLI worker in this Work')
        with self.store.db() as db:
            rows = [json.loads(row['detail']) for row in db.execute(
                "SELECT detail FROM tool_events WHERE job_id=? AND status='succeeded' AND tool='web_search'", (job,))]
        matches = sorted((row['evidence'].get('outbound_matches') for row in rows), key=lambda found: found is None)
        self.assertEqual(matches, [[{'source': 'memory', 'key': 'member-number', 'destination': 'brave', 'tool': 'web_search'}],
                                   None])
        self.assertNotIn('1234', json.dumps(matches), 'the match record holds no value')


class FailedAfterDispatch(ApiRoute):
    """A lookup that failed after it was dispatched may have carried a held value out: shown as an attempt."""

    class FailingWire(Wire):
        def execute(self, plan):
            self.plans.append(dict(plan))
            raise ValueError('response rejected after the request was sent')

    def test_a_failed_lookup_carrying_memory_is_shown_as_an_attempt(self):
        self.wire = Wire()
        service = self.service(steps([('save_memory', {'memory_key': 'member-number', 'content': MEMBER})]))
        self.turn(service, f'내 회원번호는 {MEMBER}야. 기억해')
        self.wire = self.FailingWire()
        service = self.service(steps([('web_search', {'query': 'm1234 5678 회원 혜택'})]))
        job = self.turn(service, '회원 혜택 찾아줘')
        self.assertTrue(self.wire.plans, 'the lookup was dispatched')
        audit = service.work_information_use(job)
        self.assertEqual(audit['sent_to']['held_value_matches'], [
            {'source': 'memory', 'key': 'member-number', 'tool': 'web_search', 'destination': '', 'status': 'failed'}])
        self.assertIn("- web_search: 실패한 시도에 기억 'member-number'의 값과 겹치는 내용이 들어 있었습니다(상대에게 닿았는지는 알 수 없습니다)",
                      information_use.render_korean(audit))
        with self.store.db() as db:
            details = [row['detail'] for row in db.execute("SELECT detail FROM tool_events WHERE job_id=? AND status='failed'", (job,))]
        recorded = [json.loads(detail).get('outbound_matches') for detail in details]
        self.assertIn([{'source': 'memory', 'key': 'member-number', 'destination': '', 'tool': 'web_search'}], recorded)
        self.assertNotIn('1234', json.dumps(recorded), 'the match record holds no value')

    test_an_earlier_works_memory_in_a_later_search_is_shown_and_the_search_still_goes_out = None


class BridgeRoute(unittest.TestCase):
    """The CLI's MCP bridge records the same labels on its Evidence."""

    def test_a_bridge_lookup_carrying_memory_is_labelled(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QuickStore(Path(folder) / 'state')
            store.save_memory('phone', PHONE)
            job = store.enqueue('연락처로 찾아줘', 'bridge')
            with store.db() as db:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
            sent = []

            def fake_execute(self, plan, budget=None):
                sent.append(dict(plan))
                return {'tool': plan['tool'], 'results': [], 'sources': [], 'retrieved_at': 1, 'provider': 'brave'}

            requests = [{'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}},
                        {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                         'params': {'name': 'web_search', 'arguments': {'query': '01098765432 누구'}}}]
            out = io.StringIO()
            with mock.patch('personal_agent.local_tools.LocalTools.execute', fake_execute), \
                    mock.patch.object(sys, 'stdin', io.StringIO(''.join(json.dumps(r) + '\n' for r in requests))), \
                    contextlib.redirect_stdout(out):
                mcp_bridge.serve(str(store.root), job)
            self.assertEqual([plan.get('query') for plan in sent], ['01098765432 누구'], out.getvalue())
            audit = information_use.work_information_use(store, job)
            self.assertEqual(audit['sent_to']['held_value_matches'], [
                {'source': 'memory', 'key': 'phone', 'tool': 'web_search', 'destination': 'brave', 'status': 'succeeded'}])


class CodeRunnerMemory(unittest.TestCase):
    """``CodeRunner.sent`` holds what the allowed requests carried, in memory only; the result keeps no more than before."""

    def test_allowed_requests_are_held_in_memory_and_not_returned(self):
        from test_code_run import FakeProcess, runner
        process = FakeProcess(requests=[
            {'method': 'GET', 'host': 'api.example.com', 'path': '/v1/find', 'query': 'no=M1234-5678',
             'headers': [['X-Member', 'm12345678']]},
            {'method': 'GET', 'host': 'other.example.net', 'path': '/', 'query': 'tel=01098765432'}],
            result={'stdout': 'ok', 'stderr': ''})
        with mock.patch('personal_agent.current_context.redact_known_secrets', lambda store, text: text):
            run = runner(process)
            result = run.run({'code': 'print(1)', 'hosts': 'api.example.com'})
        self.assertEqual(run.sent, [('/v1/find\nno=M1234-5678\nm12345678', 'api.example.com')],
                         'only the request the sandbox let out')
        self.assertNotIn('M1234', json.dumps(result))
        self.assertNotIn('M1234', json.dumps(evidence_summary('code_run', result)))


class HandOff(unittest.TestCase):
    """#233: only a call handed to a transport can have carried a held value out; a local refusal carried nothing."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        earlier = self.store.enqueue('회원번호 기억해', 'earlier')
        self.store.save_memory('member-number', MEMBER, work_id=earlier)
        self.job = self.store.enqueue('회원 혜택 찾아줘', 'later')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (self.job,))

    def record(self, tool, status, detail):
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (self.job, tool, status, detail, time.time()))

    def failed(self, tool):
        with self.store.db() as db:
            return [json.loads(row['detail']) for row in db.execute(
                "SELECT detail FROM tool_events WHERE job_id=? AND tool=? AND status='failed'", (self.job, tool))]

    class Network:
        def __init__(self, error=None):
            self.plans, self.error = [], error

        def execute(self, plan, budget=None):
            self.plans.append(dict(plan))
            if self.error:
                raise self.error
            return {'tool': plan['tool'], 'url': plan.get('url'), 'text': 'page', 'sources': [], 'retrieved_at': 1}

    def test_out_of_scope_page_reads_carrying_memory_record_no_attempt(self):
        # Two consecutive reads start together (#16), so each refusal is marked on its own thread.
        script = Script({'tool_calls': [call('1', 'public_page_read', url='https://shop.example/find?no=M1234-5678'),
                                        call('2', 'public_page_read', url='https://other.example/q?m=m1234%205678')]},
                        {'content': '읽지 못했습니다.'})
        network = self.Network()
        caps = Capabilities(self.store, ModelAdapter(script), BROWSER_CFG, '', self.job, self.record, network=network)
        run_agent(caps.adapter, BROWSER_CFG, '', [{'role': 'user', 'content': '회원 혜택 찾아줘'}], '', caps, self.record)
        self.assertEqual(network.plans, [], 'nothing was sent')
        failed = self.failed('public_page_read')
        self.assertEqual(len(failed), 2)
        for detail in failed:
            self.assertIs(detail.get('ran'), False)
            self.assertNotIn('outbound_matches', detail)
        self.assertEqual(information_use.work_information_use(self.store, self.job)['sent_to']['held_value_matches'], [])

    def test_the_same_read_failing_after_it_was_sent_is_still_shown_as_an_attempt(self):
        url = 'https://shop.example/find?no=M1234-5678'
        script = Script({'tool_calls': [call('1', 'public_page_read', url=url)]}, {'content': '읽지 못했습니다.'})
        network = self.Network(error=ValueError('response rejected after the request was sent'))
        caps = Capabilities(self.store, ModelAdapter(script), BROWSER_CFG, '', self.job, self.record, network=network,
                            public_page_scope=[url])
        run_agent(caps.adapter, BROWSER_CFG, '', [{'role': 'user', 'content': '회원 혜택 찾아줘'}], '', caps, self.record)
        self.assertEqual(len(network.plans), 1, 'the read was sent')
        [detail] = self.failed('public_page_read')
        self.assertNotIn('ran', detail)
        self.assertEqual(detail['outbound_matches'], [{'source': 'memory', 'key': 'member-number',
                                                       'destination': 'shop.example', 'tool': 'public_page_read'}])

    def test_local_refusals_of_each_outbound_tool_are_marked_not_run_and_carry_nothing(self):
        caps = Capabilities(self.store, None, {}, '', self.job, self.record, network=self.Network())
        cases = (('browser_open', {'url': 'https://shop.example/find?no=M1234-5678', 'effect': 'read'}),   # no browser
                 ('weather', {}),                                                                         # no place
                 ('api_request', {'slot': 'none', 'url': 'https://api.example/v1?no=M1234-5678'}),         # no such slot
                 ('code_run', {'code': 'print("M1234-5678")', 'hosts': 'not a host!'}))                   # invalid run
        for name, args in cases:
            with self.subTest(name=name):
                with self.assertRaises((ValueError, TypeError)) as caught:
                    caps.execute(name, args)
                self.assertEqual(agent_runtime.ran_field(caught.exception), {'ran': False})
                self.assertEqual(agent_runtime.attempted_outbound(caps, name, args, caught.exception), {})
        self.assertEqual(caps.network.plans, [])

    def test_a_bridge_page_read_refused_locally_records_no_attempt(self):
        requests = [{'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}},
                    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                     'params': {'name': 'public_page_read', 'arguments': {'url': 'https://shop.example/find?no=M1234-5678'}}}]
        sent = []
        out = io.StringIO()
        with mock.patch('personal_agent.local_tools.LocalTools.execute', lambda self, plan, budget=None: sent.append(plan)), \
                mock.patch.object(sys, 'stdin', io.StringIO(''.join(json.dumps(r) + '\n' for r in requests))), \
                contextlib.redirect_stdout(out):
            mcp_bridge.serve(str(self.store.root), self.job)
        self.assertEqual(sent, [])
        [detail] = self.failed('public_page_read')
        self.assertIs(detail.get('ran'), False, out.getvalue())
        self.assertNotIn('outbound_matches', detail)

    def test_a_failed_browser_open_still_lists_the_host_it_was_sent_to(self):
        script = Script({'tool_calls': [call('1', 'browser_open', url=ORIGIN + '/missing?no=m1234%205678', effect='read')]},
                        {'content': '열지 못했습니다.'})
        driver = FakeDriver()
        caps = Capabilities(self.store, ModelAdapter(script), BROWSER_CFG, '', self.job, self.record, browser=lambda: driver)
        run_agent(caps.adapter, BROWSER_CFG, '', [{'role': 'user', 'content': '회원 혜택 찾아줘'}], '', caps, self.record)
        caps.close_browser()
        self.assertTrue(any(entry[0] == 'goto' for entry in driver.log), 'the browser was sent to the address')
        [detail] = self.failed('browser_open')
        self.assertNotIn('ran', detail, 'a failure after the hand-off is not a local refusal')
        self.assertEqual(detail['destination'], 'fixture.test')
        audit = information_use.work_information_use(self.store, self.job)
        self.assertEqual(audit['sent_to']['browser_destinations'], ['fixture.test'])
        self.assertEqual(audit['sent_to']['held_value_matches'], [
            {'source': 'memory', 'key': 'member-number', 'tool': 'browser_open', 'destination': 'fixture.test',
             'status': 'failed'}])

    def test_a_browser_that_could_not_start_received_nothing(self):
        def broken():
            raise RuntimeError('profile locked')
        args = {'url': 'https://shop.example/find?no=M1234-5678', 'effect': 'read'}
        for label, factory in (('factory raised', broken), ('no driver', lambda: None)):
            with self.subTest(label):
                caps = Capabilities(self.store, None, BROWSER_CFG, '', self.job, self.record, browser=factory)
                with self.assertRaises(ValueError) as caught:
                    caps.execute('browser_open', args)
                self.assertEqual(agent_runtime.ran_field(caught.exception), {'ran': False})
                self.assertEqual(agent_runtime.attempted_outbound(caps, 'browser_open', args, caught.exception), {})

    def code_runner_caps(self, process=None, popen=None):
        from test_code_run import runner
        caps = Capabilities(self.store, None, {}, '', self.job, self.record)
        caps._code_runner = runner(process)
        if popen is not None:
            caps._code_runner.popen = popen
        return caps

    def test_a_sandbox_that_could_not_start_received_nothing(self):
        def failing_popen(*args, **kwargs):
            raise OSError('cannot start the runner')
        args = {'code': 'print("M1234-5678")', 'hosts': 'api.example.com'}
        caps = self.code_runner_caps(popen=failing_popen)
        with mock.patch('personal_agent.current_context.redact_known_secrets', lambda store, text: text):
            with self.assertRaises(OSError) as caught:
                caps.execute('code_run', args)
        self.assertEqual(agent_runtime.ran_field(caught.exception), {'ran': False})
        self.assertEqual(agent_runtime.attempted_outbound(caps, 'code_run', args, caught.exception), {})

    def test_a_run_that_failed_after_its_sandbox_started_is_still_an_attempt(self):
        from test_code_run import FakeProcess
        process = FakeProcess(requests=[{'method': 'GET', 'host': 'api.example.com', 'path': '/v1/find',
                                         'query': 'no=M1234-5678'}], error='runner crashed')
        args = {'code': 'print(1)', 'hosts': 'api.example.com'}
        caps = self.code_runner_caps(process)
        with mock.patch('personal_agent.current_context.redact_known_secrets', lambda store, text: text):
            with self.assertRaises(ValueError) as caught:
                caps.execute('code_run', args)
        self.assertEqual(agent_runtime.ran_field(caught.exception), {}, 'the code ran: not a local refusal')
        self.assertEqual(agent_runtime.attempted_outbound(caps, 'code_run', args, caught.exception)['outbound_matches'],
                         [{'source': 'memory', 'key': 'member-number', 'destination': 'api.example.com', 'tool': 'code_run'}])

    def test_a_code_run_refused_after_a_sending_run_reports_nothing_stale(self):
        from test_code_run import FakeProcess, runner
        from personal_agent.code_run import CodeRunError
        process = FakeProcess(requests=[{'method': 'GET', 'host': 'api.example.com', 'path': '/v1/find',
                                         'query': 'no=M1234-5678'}], result={'stdout': 'ok', 'stderr': ''})
        with mock.patch('personal_agent.current_context.redact_known_secrets', lambda store, text: text):
            run = runner(process)
            run.run({'code': 'print(1)', 'hosts': 'api.example.com'})
        self.assertTrue(run.sent, 'the first run sent the value')
        caps = Recording.Caps(held(('member-number', MEMBER)))
        caps._code_runner = run
        with self.assertRaises(CodeRunError):
            run.run({'code': 'print(1)', 'hosts': 'not a host!'})
        self.assertEqual(run.sent, [])
        self.assertEqual(outbound_evidence(caps, 'code_run', {'code': 'print(1)'}, {}), {})


class NetworkLocalRefusals(HandOff):
    """#233 (review of PR #257): a network's own checks before it sends are refusals; anything after is an attempt."""

    PAGE = 'https://shop.example/find?no=M1234-5678'

    @staticmethod
    def resolver(address):
        return lambda host, port, type=None, timeout=None: [(None, None, None, None, (address, port))]

    def reader_caps(self, address, opener=None):
        from personal_agent.local_tools import LocalTools, PublicPageReader
        network = LocalTools(page_reader=PublicPageReader(opener=opener, resolver=self.resolver(address)))
        return Capabilities(self.store, None, {}, '', self.job, self.record, network=network, public_page_scope=[self.PAGE])

    def execute(self, caps, name, args):
        from personal_agent.providers import ProviderError
        with self.assertRaises((ValueError, TypeError, OSError, ProviderError)) as caught:
            caps.execute(name, args)
        return caught.exception, agent_runtime.attempted_outbound(caps, name, args, caught.exception)

    def test_an_unavailable_search_provider_received_nothing(self):
        from personal_agent.local_tools import LocalTools
        from personal_agent.search_providers import ProviderRegistry
        caps = Capabilities(self.store, None, {}, '', self.job, self.record,
                            network=LocalTools(providers=ProviderRegistry.from_config({})))
        for args in ({'query': 'm1234 5678 회원 혜택'}, {'query': 'm1234 5678 회원 혜택', 'provider': 'brave'}):
            with self.subTest(args=args):
                exc, attempted = self.execute(caps, 'web_search', args)
                self.assertEqual(getattr(exc, 'code', None) in ('provider_unavailable', 'native_search_unavailable'), True)
                self.assertEqual(agent_runtime.ran_field(exc), {'ran': False})
                self.assertEqual(attempted, {})

    def test_a_page_host_resolving_to_a_private_address_received_nothing(self):
        class Opener:
            opened = []

            def open(self, request, timeout=None):
                self.opened.append(request.full_url)
                raise AssertionError('never reached')
        opener = Opener()
        exc, attempted = self.execute(self.reader_caps('10.0.0.5', opener), 'public_page_read', {'url': self.PAGE})
        self.assertIn('개인 네트워크', str(exc))
        self.assertEqual(opener.opened, [])
        self.assertEqual(agent_runtime.ran_field(exc), {'ran': False})
        self.assertEqual(attempted, {})

    def test_a_page_read_failing_once_the_http_client_was_called_is_an_attempt(self):
        class Opener:
            def open(self, request, timeout=None):
                raise OSError('connection reset after the request was written')
        exc, attempted = self.execute(self.reader_caps('93.184.216.34', Opener()), 'public_page_read', {'url': self.PAGE})
        self.assertEqual(agent_runtime.ran_field(exc), {})
        self.assertEqual(attempted['outbound_matches'], [{'source': 'memory', 'key': 'member-number',
                                                          'destination': 'shop.example', 'tool': 'public_page_read'}])

    def test_a_redirect_refused_after_the_first_request_is_an_attempt(self):
        class Response:
            status, url = 302, None
            headers = {'Location': 'http://10.0.0.1/next'}

            def read(self, size=-1):
                return b''

            def close(self):
                pass

        class Opener:
            def open(self, request, timeout=None):
                return Response()
        exc, attempted = self.execute(self.reader_caps('93.184.216.34', Opener()), 'public_page_read', {'url': self.PAGE})
        self.assertIn('개인 네트워크', str(exc))
        self.assertEqual(agent_runtime.ran_field(exc), {}, 'the first request was sent')
        self.assertTrue(attempted.get('outbound_matches'))

    def test_a_network_implementation_that_marks_nothing_is_an_attempt(self):
        url = self.PAGE
        for error in (ValueError('refused inside an unknown implementation'), OSError('socket closed')):
            with self.subTest(error=type(error).__name__):
                network = self.Network(error=error)
                caps = Capabilities(self.store, None, {}, '', self.job, self.record, network=network, public_page_scope=[url])
                exc, attempted = self.execute(caps, 'public_page_read', {'url': url})
                self.assertTrue(network.plans, 'handed to the network (a transient failure is retried once)')
                self.assertEqual(agent_runtime.ran_field(exc), {})
                self.assertTrue(attempted.get('outbound_matches'))

    def test_a_city_weather_whose_geocoding_answer_is_refused_is_an_attempt(self):
        from personal_agent.local_tools import LocalTools
        caps = Capabilities(self.store, None, {}, '', self.job, self.record, network=LocalTools())
        sent = []

        def geocoding(url, body, timeout=None):
            sent.append(url)
            return {'results': [{'name': MEMBER, 'latitude': 999, 'longitude': 0, 'population': 1}]}
        with mock.patch('personal_agent.local_tools.request_json', geocoding):
            exc, attempted = self.execute(caps, 'weather', {'city': MEMBER})
        self.assertEqual(len(sent), 1, 'the city went to the geocoding host')
        self.assertEqual(agent_runtime.ran_field(exc), {}, 'refused after a request of this call')
        self.assertEqual(attempted['outbound_matches'], [{'source': 'memory', 'key': 'member-number',
                                                          'destination': 'geocoding-api.open-meteo.com', 'tool': 'weather'}])

    def test_a_forecast_for_bad_coordinates_of_the_callers_own_is_refused_before_sending(self):
        from personal_agent.local_tools import LocalTools
        with mock.patch('personal_agent.local_tools.request_json', side_effect=AssertionError('never sent')):
            for call_it in (lambda tools: tools.forecast(999, 0),
                            lambda tools: tools.execute({'tool': 'weather', 'latitude': 0, 'longitude': float('nan')})):
                with self.assertRaises(ValueError) as caught:
                    call_it(LocalTools())
                self.assertEqual(agent_runtime.ran_field(caught.exception), {'ran': False})

    def test_a_retry_refused_before_its_own_request_follows_a_sent_attempt(self):
        addresses = iter(['93.184.216.34', '10.0.0.5'])

        def resolver(host, port, type=None, timeout=None):
            return [(None, None, None, None, (next(addresses), port))]

        class Opener:
            def open(self, request, timeout=None):
                raise OSError('connection reset after the request was written')
        from personal_agent.local_tools import LocalTools, PublicPageReader
        network = LocalTools(page_reader=PublicPageReader(opener=Opener(), resolver=resolver))
        caps = Capabilities(self.store, None, {}, '', self.job, self.record, network=network, public_page_scope=[self.PAGE])
        exc, attempted = self.execute(caps, 'public_page_read', {'url': self.PAGE})
        self.assertIn('개인 네트워크', str(exc), 'the retry was refused before its own request')
        self.assertEqual(agent_runtime.ran_field(exc), {}, 'the first attempt was sent')
        self.assertTrue(attempted.get('outbound_matches'))

    # The inherited cases already ran in ``HandOff``.
    test_out_of_scope_page_reads_carrying_memory_record_no_attempt = None
    test_the_same_read_failing_after_it_was_sent_is_still_shown_as_an_attempt = None
    test_local_refusals_of_each_outbound_tool_are_marked_not_run_and_carry_nothing = None
    test_a_bridge_page_read_refused_locally_records_no_attempt = None
    test_a_failed_browser_open_still_lists_the_host_it_was_sent_to = None
    test_a_browser_that_could_not_start_received_nothing = None
    test_a_sandbox_that_could_not_start_received_nothing = None
    test_a_run_that_failed_after_its_sandbox_started_is_still_an_attempt = None
    test_a_code_run_refused_after_a_sending_run_reports_nothing_stale = None


class ProviderNotes(unittest.TestCase):
    def test_the_cli_search_note_follows_the_recorded_own_search_state(self):
        def text(own):
            return information_use.render_korean({'recorded': True, 'used': [], 'sent_to': {'workers': [
                {'route': 'subscription', 'engine': 'codex', 'own_web_search': own}]}})
        self.assertIn('AI CLI가 스스로 하는 웹 검색은 Still을 거치지 않습니다', text(True))
        self.assertNotIn('AI CLI가 스스로 하는 웹 검색', text(False), 'a CLI without its own search has no such blind spot')
        self.assertEqual(text(False).count('선택한 AI 제공자가 처리합니다'), 1)
        unrecorded = information_use.render_korean({'recorded': False, 'used': [], 'sent_to': {'workers': []}})
        self.assertNotIn('선택한 AI 제공자가 처리합니다', unrecorded, 'no recorded worker: no provider claim')


class WebView(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_the_web_view_renders_matches_destinations_and_the_provider_note(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        app = self.ROOT / 'src' / 'personal_agent' / 'web' / 'app.js'
        script = r"""
const ui=require(process.argv[1]);
class Node{constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.className='';}
  append(...nodes){this.children.push(...nodes);}}
global.document={createElement:tag=>new Node(tag)};
const box=new Node('div');
ui.setLanguage('ko');
ui.informationUseView({information_use:{recorded:true,used:[],conversation_turns:0,sent_to:{
  workers:[{route:'subscription',engine:'codex',own_web_search:true}],lookups:[],
  browser_destinations:['shop.example'],
  held_value_matches:[{source:'memory',key:'member-number',tool:'browser_type',destination:'shop.example'},
                      {source:'work_write',key:'',tool:'web_search',destination:''}]}}},box);
const text=JSON.stringify(box,(key,value)=>key==='tag'||key==='className'?undefined:value);
console.log(text);
"""
        out = subprocess.run([node, '-e', script, str(app)], capture_output=True, text=True, check=True).stdout
        self.assertIn('shop.example', out)
        self.assertIn("브라우저에 입력 → shop.example: 기억 'member-number'의 값과 겹치는 내용", out)
        self.assertIn('웹 검색: 이 작업에서 저장한 값과 겹치는 내용', out)
        self.assertIn('이 작업의 대화는 선택한 AI 제공자가 처리합니다.', out)
        self.assertIn('AI CLI가 스스로 하는 웹 검색은 Still을 거치지 않습니다.', out)

    def test_the_web_view_says_where_the_comparison_was_cut_off(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        app = self.ROOT / 'src' / 'personal_agent' / 'web' / 'app.js'
        script = r"""
const ui=require(process.argv[1]);
class Node{constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.className='';}
  append(...nodes){this.children.push(...nodes);}}
global.document={createElement:tag=>new Node(tag)};
for(const lang of ['ko','en']){
  const box=new Node('div');ui.setLanguage(lang);
  ui.informationUseView({information_use:{recorded:true,used:[],conversation_turns:0,sent_to:{workers:[],lookups:[],
    held_value_matches:[],held_value_comparison_cut:[{kind:'memory_rows',limit:500},{kind:'text_chars',limit:1000},{kind:'other',limit:1}]}}},box);
  console.log(JSON.stringify(box,(key,value)=>key==='tag'||key==='className'?undefined:value));
}
"""
        ko, en = subprocess.run([node, '-e', script, str(app)], capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertIn('보관 정보 비교 범위', ko)
        self.assertIn('최근 기억 500개까지만 비교했고, 그보다 오래된 기억은 확인하지 않았습니다', ko)
        self.assertIn('나간 내용마다 앞 1,000자까지만 비교했고, 그 뒤는 확인하지 않았습니다', ko)
        self.assertIn('Compared only with the newest 500 Memory items; older Memory was not checked', en)
        self.assertIn('Compared only the first 1,000 characters of each thing that went out', en)
        self.assertNotIn('other', ko + en)


if __name__ == '__main__':
    unittest.main()
