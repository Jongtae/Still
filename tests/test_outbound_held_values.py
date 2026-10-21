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


if __name__ == '__main__':
    unittest.main()
