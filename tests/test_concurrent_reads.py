"""#16 slice 1: overlap by a call's effect class (concurrent_read), as on the CLI route.

The real loop and Capabilities execute against gated clients/store methods.
Results, records, Evidence, budgets and interruption retain call-order semantics.
Evidence class: model-free; no live model or network is contacted.
"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from types import SimpleNamespace

from personal_agent.agent_runtime import CONCURRENT_READ_LIMIT, Capabilities, ReadOverlap, WorkBudget, LoopGuard, run_agent
from personal_agent.providers import ModelAdapter, ProviderError
from personal_agent.quickstart_store import QuickStore
from test_agency_loop import CFG, Script, call, finish, judgments


def search_result(query):
    return {'tool': 'web_search', 'query': query, 'retrieved_at': 1,
            'results': [{'title': query, 'url': f'https://{query}.example/', 'snippet': query}],
            'sources': [f'https://{query}.example/']}


class Wire:
    """The public network.  Logs request start/end; optional barrier and delays per query."""

    def __init__(self, barrier=None, delays=None, transient=(), gate=None):
        self.log, self.lock = [], threading.Lock()
        self.barrier, self.delays = barrier, delays or {}
        self.transient = set(transient)
        self.active = 0
        self.peak = 0
        self.gate = gate

    def execute(self, plan):
        key = plan.get('query') or plan.get('url') or plan.get('city')
        with self.lock:
            self.log.append(('start', key))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.gate is not None:
                self.gate(key)
            if key in self.transient:
                self.transient.discard(key)
                raise TimeoutError('slow wire')
            if self.barrier is not None:
                self.barrier.wait()
            time.sleep(self.delays.get(key, 0))
            if plan['tool'] == 'public_page_read':
                return {'tool': 'public_page_read', 'url': plan['url'], 'title': 'page', 'text': 'page text',
                        'sources': [plan['url']]}
            return search_result(key)
        finally:
            with self.lock:
                self.active -= 1
                self.log.append(('end', key))


class ConcurrentReadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')
        self.events = []

    def record(self, tool, status, detail):
        self.events.append((tool, status, json.loads(detail) if isinstance(detail, str) else detail))

    def run_turn(self, calls, wire=None, budget=None, after=None, **kwargs):
        script = Script({'tool_calls': calls}, *(after or [finish('f', *[c['id'] for c in calls], summary='끝')]))
        caps = Capabilities(self.store, ModelAdapter(script), CFG, '', 'job', self.record, network=wire,
                            judgments=kwargs.pop('judgments', judgments(True)), **kwargs, **({'budget': budget} if budget is not None else {}))
        result = run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': '찾아줘'}], '', caps, self.record)
        return result, script, caps

    def tool_events(self):
        return [(tool, status, detail.get('call_id'), detail.get('scope')) for tool, status, detail in self.events
                if tool != 'model']

    def test_independent_searches_overlap(self):
        # Two requests can pass this barrier only together; one by one, the first would time out.
        wire = Wire(barrier=threading.Barrier(2, timeout=5))
        result, _script, _caps = self.run_turn([call('1', 'web_search', query='alpha'),
                                                call('2', 'web_search', query='beta')], wire)
        self.assertEqual(wire.peak, 2)
        self.assertEqual(result.outcome, 'succeeded')

    def test_records_and_results_keep_the_turns_order_when_the_second_read_finishes_first(self):
        wire = Wire(barrier=threading.Barrier(2, timeout=5), delays={'alpha': 0.2})
        _result, script, _caps = self.run_turn([call('1', 'web_search', query='alpha'),
                                                call('2', 'web_search', query='beta')], wire)
        self.assertLess(wire.log.index(('end', 'beta')), wire.log.index(('end', 'alpha')), 'beta finished first')
        self.assertEqual(self.tool_events(), [('web_search', 'running', '1', 'main'), ('web_search', 'succeeded', '1', 'main'),
                                              ('web_search', 'running', '2', 'main'), ('web_search', 'succeeded', '2', 'main')])
        shown = [json.loads(m['content']) for m in script.bodies[1]['messages'] if m.get('role') == 'tool']
        self.assertEqual([(row['ref'], row.get('query')) for row in shown], [('1', 'alpha'), ('2', 'beta')],
                         'each call reads its own result')

    def test_a_transient_retry_is_recorded_in_its_calls_place(self):
        wire = Wire(transient={'beta'})
        budget = WorkBudget()
        self.run_turn([call('1', 'web_search', query='alpha'), call('2', 'web_search', query='beta')], wire, budget=budget)
        self.assertEqual(self.tool_events(), [('web_search', 'running', '1', 'main'), ('web_search', 'succeeded', '1', 'main'),
                                              ('web_search', 'running', '2', 'main'),
                                              ('web_search', 'failed', None, 'transient-retry'),
                                              ('web_search', 'succeeded', '2', 'main')])
        self.assertEqual(budget.attempts_used, 3, 'two reads and one retry, as one by one')

    def test_six_reads_respect_the_thread_cap_and_keep_call_order(self):
        # Every request waits until four are on the wire, then briefly for a fifth:
        # an uncapped pool lets the fifth in (peak 5+); with four workers it cannot
        # enter until one of the first wave returns.
        condition = threading.Condition()
        entered = []

        def gate(key):
            with condition:
                entered.append(key)
                condition.notify_all()
                if not condition.wait_for(lambda: len(entered) >= CONCURRENT_READ_LIMIT, timeout=5):
                    raise AssertionError('four reads never overlapped')
                condition.wait_for(lambda: len(entered) > CONCURRENT_READ_LIMIT, timeout=0.2)

        queries = ['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta']
        calls = [call(str(i), 'web_search', query=q) for i, q in enumerate(queries)]
        wire = Wire(gate=gate)
        result, script, _caps = self.run_turn(calls, wire, budget=WorkBudget(turns=30, attempts=20))
        self.assertEqual(result.outcome, 'succeeded')
        self.assertEqual(wire.peak, CONCURRENT_READ_LIMIT)
        self.assertCountEqual([q for event, q in wire.log if event == 'start'], queries)
        self.assertEqual(self.tool_events(), [event for i in range(6) for event in
                         [('web_search', 'running', str(i), 'main'),
                          ('web_search', 'succeeded', str(i), 'main')]])
        shown = [json.loads(m['content']) for m in script.bodies[1]['messages'] if m.get('role') == 'tool']
        self.assertEqual([(row['ref'], row['query']) for row in shown], list(zip(map(str, range(6)), queries)))

    def _interrupt_in_flight_group(self, reason, private=False):
        queries = ['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta']
        action = 'drive_search' if private else 'web_search'
        calls = [call(str(i), action, query=q) for i, q in enumerate(queries)]
        stopped = threading.Event()
        wall = [0.0]
        budget = WorkBudget(turns=30, attempts=20, seconds=10, clock=lambda: wall[0], stop=stopped.is_set)
        condition = threading.Condition()
        entered = []
        release = threading.Event()
        read_threads = []

        def gate(key):
            with condition:
                entered.append(key)
                read_threads.append(threading.current_thread())
                condition.notify_all()
            if not release.wait(5):
                raise AssertionError('test did not release the in-flight reads')

        wire = Wire(gate=gate)

        def run_and_snapshot():
            result = self.run_turn(calls, wire, budget, **({'drive': SimpleNamespace(search=lambda query: wire.execute({'tool': 'drive_search', 'query': query}))} if private else {}))
            return result, list(self.events)

        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(run_and_snapshot)
            try:
                with condition:
                    self.assertTrue(condition.wait_for(lambda: len(entered) >= 4, timeout=5))
                self.assertEqual(wire.active, 4, 'the interrupt must arrive on the wire, mid-group')
                if reason == 'stopped':
                    stopped.set()
                else:
                    wall[0] = budget.deadline
            finally:
                release.set()
            (result, script, caps), returned_records = future.result(timeout=5)

        for thread in read_threads:
            thread.join(5)
            self.assertFalse(thread.is_alive(), 'the read pool must terminate')
        self.assertEqual(self.events, returned_records, 'no read records arrive after run_agent returns')

        self.assertEqual(result.outcome, 'partial', 'already-sent reads remain observed after interruption')
        self.assertEqual(len(script.bodies), 1, 'no further model turn after interruption')
        self.assertCountEqual([q for event, q in wire.log if event == 'start'], queries[:4])
        self.assertCountEqual([q for event, q in wire.log if event == 'end'], queries[:4])
        self.assertEqual(self.tool_events(), [event for i in range(6) for event in
                         [(action, 'running', str(i), 'main'),
                          (action, 'succeeded' if i < 4 else 'failed', str(i), 'main')]])
        refused = [detail['code'] for tool, status, detail in self.events if tool != 'model' and status == 'failed']
        self.assertEqual(refused, [reason, reason])
        self.assertEqual([(status, detail.get('code')) for tool, status, detail in self.events
                          if tool == 'model' and status == 'stopped'], [('stopped', reason)])
        self.assertEqual(wire.active, 0, 'all in-flight requests settled before returning')
        self.assertEqual(caps.record, self.record, 'settle restored the record sink')
        records = list(self.events)
        caps.record('probe', 'succeeded', {})
        self.assertEqual(self.events, records + [('probe', 'succeeded', {})])

    def test_stop_mid_group_finishes_sent_reads_and_refuses_queued_reads(self):
        self._interrupt_in_flight_group('stopped')

    def test_deadline_mid_group_finishes_sent_reads_and_refuses_queued_reads(self):
        self._interrupt_in_flight_group('deadline_exceeded')

    def test_an_effect_between_reads_keeps_them_apart(self):
        wire = Wire()
        self.run_turn([call('1', 'web_search', query='alpha'), call('2', 'save_note', content='메모'),
                       call('3', 'web_search', query='beta')], wire)
        self.assertEqual(wire.peak, 1)
        self.assertEqual(wire.log, [('start', 'alpha'), ('end', 'alpha'), ('start', 'beta'), ('end', 'beta')])
        order = [(tool, status) for tool, status, _id, _scope in self.tool_events()]
        self.assertEqual(order, [('web_search', 'running'), ('web_search', 'succeeded'), ('save_note', 'running'),
                                 ('save_note', 'succeeded'), ('web_search', 'running'), ('web_search', 'succeeded')])

    def test_a_page_read_after_a_search_in_the_same_turn_waits_for_it(self):
        # The search's result is what admits the page; it must have run first.
        wire = Wire()
        result, _script, _caps = self.run_turn([call('1', 'web_search', query='alpha'),
                                                call('2', 'public_page_read', url='https://alpha.example/')], wire)
        self.assertEqual(wire.log, [('start', 'alpha'), ('end', 'alpha'),
                                    ('start', 'https://alpha.example/'), ('end', 'https://alpha.example/')])
        self.assertEqual([status for tool, status, _id, _scope in self.tool_events() if tool == 'public_page_read'],
                         ['running', 'succeeded'])

    def test_a_duplicate_read_is_never_sent(self):
        wire = Wire()
        self.run_turn([call('1', 'web_search', query='alpha'), call('2', 'web_search', query='alpha')], wire)
        self.assertEqual([entry for entry in wire.log if entry[0] == 'start'], [('start', 'alpha')])
        refused = [detail for tool, status, detail in self.events if status == 'failed' and tool != 'model']
        self.assertEqual([row['code'] for row in refused], ['repeat_path'])

    def test_a_budget_that_cannot_cover_every_retry_runs_the_turn_one_by_one(self):
        wire = Wire()
        budget = WorkBudget(attempts=3)
        self.run_turn([call('1', 'web_search', query='alpha'), call('2', 'web_search', query='beta')], wire, budget=budget)
        self.assertEqual(wire.peak, 1)
        self.assertEqual(budget.attempts_used, 2)

    def test_native_searches_need_room_for_a_retry_turn_each(self):
        # A native search provider spends a model turn per request and another on a transient
        # retry; a group runs together only when the turn allowance covers both for every search.
        caps = Capabilities(self.store, None, CFG, '', 'job', self.record, network=Wire(),
                            budget=WorkBudget(turns=20, attempts=20))
        overlap = ReadOverlap(caps, {})
        searches = [('1', 'web_search', {'query': 'a'}, 'web_search'), ('2', 'web_search', {'query': 'b'}, 'web_search')]
        left = caps.budget.turn_limit()
        caps.budget.turns_used = left - 3
        self.assertFalse(overlap.covered(searches), 'three turns cannot cover two searches and their retries')
        caps.budget.turns_used = left - 4
        self.assertTrue(overlap.covered(searches))

    def test_a_stopped_work_sends_nothing_and_refuses_in_order(self):
        wire = Wire()
        stopped = [False]
        budget = WorkBudget(stop=lambda: stopped[0])
        script = Script({'tool_calls': [call('1', 'web_search', query='alpha'), call('2', 'web_search', query='beta')]})
        caps = Capabilities(self.store, ModelAdapter(script), CFG, '', 'job', self.record, network=wire,
                            judgments=judgments(True), budget=budget)
        original = script.__call__

        def stop_after_the_turn(url, body, headers=None, timeout=60):
            answer = original(url, body, headers, timeout)
            stopped[0] = True
            return answer
        caps.adapter = ModelAdapter(stop_after_the_turn)
        try:
            run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': '찾아줘'}], '', caps, self.record)
        except ProviderError:
            pass  # the stopped run ends with nothing executed, as before
        self.assertEqual(wire.log, [])
        refused = [(detail.get('call_id'), detail.get('code')) for tool, status, detail in self.events
                   if status == 'failed' and tool != 'model']
        self.assertEqual(refused, [('1', 'stopped'), ('2', 'stopped')])

    def test_reads_after_a_completion_claim_are_not_started_early(self):
        wire = Wire()
        calls = [call('1', 'web_search', query='alpha'),
                 {'id': 'f', 'function': {'name': 'finish', 'arguments': json.dumps(
                     {'status': 'done', 'evidence_refs': [], 'summary': '아직'})}},
                 call('2', 'web_search', query='beta')]
        self.run_turn(calls, wire, after=[finish('g', '1', '2', summary='끝')])
        self.assertEqual(wire.peak, 1)
        self.assertEqual(wire.log, [('start', 'alpha'), ('end', 'alpha'), ('start', 'beta'), ('end', 'beta')])

    def test_the_record_sink_is_restored_after_the_turn(self):
        wire = Wire()
        _result, _script, caps = self.run_turn([call('1', 'web_search', query='alpha'),
                                                call('2', 'web_search', query='beta')], wire)
        self.assertEqual(caps.record, self.record)

    def test_private_reads_and_mixed_reads_overlap(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                self.events.clear()
                barrier = threading.Barrier(2, timeout=5)
                def read(query):
                    barrier.wait()
                    return {'files': [], 'messages': [], 'query': query}
                wire = Wire(barrier=barrier)
                calls = [call('1', 'drive_search', query='alpha'),
                         call('2', 'web_search' if mixed else 'mail_search', query='beta')]
                result, _, _ = self.run_turn(calls, wire, drive=SimpleNamespace(search=read),
                                             mail=SimpleNamespace(search=read))
                self.assertEqual(result.outcome, 'succeeded')

    def test_private_effect_boundaries_and_api_effect_floor(self):
        caps = Capabilities(self.store, None, CFG, '', 'job', self.record)
        specs = {d['function']['name']: d['function']['parameters'] for d in caps.definitions()}
        # The API definition is normally hidden without a registered slot.
        from personal_agent.agent_runtime import DEFINITIONS
        specs.update({d['function']['name']: d['function']['parameters'] for d in DEFINITIONS
                      if d['function']['name'] == 'api_request'})
        overlap = ReadOverlap(caps, specs)
        for middle in (call('2', 'save_note', content='note'),
                       call('2', 'api_request', slot='test', method='POST', url='https://example.com/', effect='read')):
            calls = [call('1', 'drive_search', query='alpha'), middle,
                     call('3', 'mail_search', query='beta')]
            self.assertEqual([row[0] for row in overlap.group(calls, 0, {}, LoopGuard())], ['1'])
        calls[1] = call('2', 'api_request', slot='test', method='GET', url='https://example.com/', effect='read')
        self.assertEqual([row[0] for row in overlap.group(calls, 0, {}, LoopGuard())], ['1', '2', '3'])

    def test_private_evidence_and_records_replay_in_one_order(self):
        from personal_agent.agent_runtime import EvidenceLog
        second_done = threading.Event()
        original = self.store.memories
        original_search = self.store.search_memories
        observed = []
        caps_box = []
        def memories(*args, **kwargs):
            self.assertTrue(second_done.wait(5))
            caps_box[0].record('inside', 'succeeded', {'first': True})
            return original(*args, **kwargs)
        def search(*args, **kwargs):
            caps_box[0].record('inside', 'succeeded', {'first': False})
            return original_search(*args, **kwargs)
        original_append = EvidenceLog.append
        def append(log, item):
            held = getattr(log._hold, 'held', None)
            original_append(log, item)
            if held is None:
                observed.append(('evidence', item['tool']))
        original_start = ReadOverlap.start
        def start(overlap, *args):
            original_start(overlap, *args)
            if '2' in overlap.started:
                overlap.started['2'][0].add_done_callback(lambda _: second_done.set())
        def record(tool, status, detail):
            self.record(tool, status, detail)
            if tool == 'inside':
                observed.append(('record', detail['first']))
        script = Script({'tool_calls': [call('1', 'list_memory'), call('2', 'search_memory', query='coffee')]},
                        finish('f', '1', '2', summary='done'))
        caps = Capabilities(self.store, ModelAdapter(script), CFG, '', 'job', record, judgments=judgments(True))
        caps_box.append(caps)
        with patch.object(self.store, 'memories', memories), patch.object(self.store, 'search_memories', search), patch.object(EvidenceLog, 'append', append), patch.object(ReadOverlap, 'start', start):
            run_agent(caps.adapter, CFG, '', [], '', caps, record)
        tools = [item['tool'] for item in caps.evidence]
        self.assertLess(tools.index('list_memory'), tools.index('search_memory'))
        self.assertEqual(observed[0], ('record', True))
        self.assertLess(observed.index(('evidence', 'list_memory')), observed.index(('record', False)))
        self.assertIsNone(caps.evidence._hold)
        self.assertIn('owner-memory', caps.private_provenance)

    def test_private_failure_keeps_other_result_and_call_order(self):
        from personal_agent.google_drive_read import DriveHTTPError
        barrier = threading.Barrier(2, timeout=5)
        def drive(query):
            barrier.wait()
            raise DriveHTTPError(500)
        def mail(query):
            barrier.wait()
            return {'messages': [], 'query': query}
        facts = []
        result, script, _ = self.run_turn([call('1', 'drive_search', query='alpha'),
                                          call('2', 'mail_search', query='beta')],
                                         drive=SimpleNamespace(search=drive), mail=SimpleNamespace(search=mail),
                                         judgments=judgments(lambda f: facts.append(f) or True))
        self.assertIn('drive_search', facts[0]['failed_steps'])
        self.assertEqual([(t, s, i) for t, s, i, _ in self.tool_events()],
                         [('drive_search', 'running', '1'), ('drive_search', 'failed', '1'),
                          ('mail_search', 'running', '2'), ('mail_search', 'succeeded', '2')])
        self.assertIn('drive_unavailable', json.dumps(self.events))
        shown = [json.loads(m['content']) for m in script.bodies[1]['messages'] if m.get('role') == 'tool']
        self.assertEqual(shown[1]['query'], 'beta')

    def test_private_attempt_budget_needs_no_retry_reserve(self):
        caps = Capabilities(self.store, None, CFG, '', 'job', self.record, budget=WorkBudget(attempts=2))
        overlap = ReadOverlap(caps, {})
        private = [('1', 'drive_search', {}, 'drive_search'), ('2', 'mail_search', {}, 'mail_search')]
        self.assertTrue(overlap.covered(private))
        self.assertFalse(overlap.covered([private[0], ('2', 'web_search', {}, 'web_search')]))
        barrier = threading.Barrier(2, timeout=5)
        def read(query):
            barrier.wait()
            return {'files': [], 'messages': []}
        self.run_turn([call('1', 'drive_search', query='alpha'), call('2', 'mail_search', query='beta')],
                      budget=caps.budget, drive=SimpleNamespace(search=read), mail=SimpleNamespace(search=read))
        self.assertEqual(caps.budget.attempts_used, 2)

    def test_private_stop_mid_group(self):
        self._interrupt_in_flight_group('stopped', private=True)

    def test_conversation_budget_is_shared_while_evidence_is_held(self):
        # A transport boundary gate on a third read proves the two recall calls
        # are in an overlapping group; recall's own lock serializes its budget.
        from test_conversation_recall import ConversationRecall
        for query in ('alpha', 'beta'):
            for i in range(3):
                ConversationRecall.exchange(self, owner=f'{query} coffee {i}')
        ConversationRecall.age(self)
        entered = threading.Event()
        seen_limits = []
        original = self.store.search_conversation
        def search(query, **kwargs):
            self.assertTrue(entered.wait(5))
            seen_limits.append(kwargs['limit'])
            return original(query, **kwargs)
        def drive(query):
            entered.set()
            return {'files': []}
        with patch.object(self.store, 'search_conversation', search):
            _, _, caps = self.run_turn([call('1', 'search_conversation', query='alpha', limit=2),
                                       call('2', 'search_conversation', query='beta', limit=2),
                                       call('3', 'drive_search', query='gamma')], drive=SimpleNamespace(search=drive))
        rows = [item for item in caps.evidence if item['tool'] == 'search_conversation']
        self.assertEqual(sum(len(item['result']['exchanges']) for item in rows), 3)
        self.assertEqual(seen_limits, [2, 1])

    def test_non_evidence_log_keeps_reads_sequential(self):
        caps = Capabilities(self.store, None, CFG, '', 'job', self.record)
        caps.evidence = []
        specs = {d['function']['name']: d['function']['parameters'] for d in caps.definitions()}
        overlap = ReadOverlap(caps, specs)
        overlap.start([call('1', 'list_memory'), call('2', 'list_notes')], 0, {}, LoopGuard())
        self.assertEqual(overlap.started, {})
        self.assertEqual(caps.record, self.record)

    def test_api_reads_overlap_at_the_transport(self):
        from personal_agent.api_requests import ApiRequests, save_slot
        save_slot(self.store, 'test', ['example.com'], 'test-only-key')
        barrier = threading.Barrier(2, timeout=5)
        def transport(method, url, headers, body, timeout):
            barrier.wait()
            return 200, {}, json.dumps({'url': url}).encode()
        script = Script({'tool_calls': [call(str(i), 'api_request', slot='test', method='GET',
                                             url=f'https://example.com/{i}', effect='read') for i in range(2)]},
                        finish('f', '0', '1', summary='done'))
        caps = Capabilities(self.store, ModelAdapter(script), CFG, '', 'job', self.record, judgments=judgments(True))
        caps._api = ApiRequests(self.store, 'job', transport=transport)
        run_agent(caps.adapter, CFG, '', [], '', caps, self.record)
        self.assertEqual([item['result']['data']['url'] for item in caps.evidence],
                         ['https://example.com/0', 'https://example.com/1'])

    def test_saved_result_setup_status_does_not_leak_between_reads(self):
        from personal_agent.file_workspace import FileWorkspace
        failed_done = threading.Event()
        original_start = ReadOverlap.start
        def start(overlap, *args):
            original_start(overlap, *args)
            if '2' in overlap.started:
                overlap.started['2'][0].add_done_callback(lambda _: failed_done.set())
        def current_results(workspace, *, path=None, **kwargs):
            if path == 'unavailable':
                raise OSError('folder unavailable')
            self.assertTrue(failed_done.wait(5))
            return [{'path': path, 'content': 'saved content'}]
        calls = [call('1', 'read_file', root_id='saved-results', path='available'),
                 call('2', 'read_file', root_id='saved-results', path='unavailable')]
        with patch.object(FileWorkspace, 'current_results', current_results), \
             patch.object(FileWorkspace, 'has_file_results', return_value=True), \
             patch.object(ReadOverlap, 'start', start):
            _, script, _ = self.run_turn(calls)
        shown = [json.loads(m['content']) for m in script.bodies[1]['messages'] if m.get('role') == 'tool']
        self.assertEqual(shown[0]['content'], 'saved content')
        self.assertTrue(shown[1]['needs_setup'])


if __name__ == '__main__':
    unittest.main()
