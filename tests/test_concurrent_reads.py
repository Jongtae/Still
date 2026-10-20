"""#16 slice 1: independent public reads of one model turn run at the same time.

``run_agent`` still admits, records, judges and answers a turn's calls one by
one, in order.  Only the network requests of consecutive public reads
(``NETWORK_READS``) that the loop would send are started together.  Effects,
private reads, a page read that may depend on a search in the same turn, and
anything the Work budget could not cover stay sequential.

Evidence class: model-free.  A scripted model transport and a fake public
network (with a barrier that only concurrent requests can pass) replace only
the provider and the wire; ``run_agent`` and ``Capabilities`` run unchanged.
No live model or network is contacted.
"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from personal_agent.agent_runtime import Capabilities, WorkBudget, run_agent
from personal_agent.providers import ModelAdapter, ProviderError
from personal_agent.quickstart_store import QuickStore
from test_agency_loop import CFG, Script, call, finish, judgments


def search_result(query):
    return {'tool': 'web_search', 'query': query, 'retrieved_at': 1,
            'results': [{'title': query, 'url': f'https://{query}.example/', 'snippet': query}],
            'sources': [f'https://{query}.example/']}


class Wire:
    """The public network.  Logs request start/end; optional barrier and delays per query."""

    def __init__(self, barrier=None, delays=None, transient=()):
        self.log, self.lock = [], threading.Lock()
        self.barrier, self.delays = barrier, delays or {}
        self.transient = set(transient)
        self.active = 0
        self.peak = 0

    def execute(self, plan):
        key = plan.get('query') or plan.get('url') or plan.get('city')
        with self.lock:
            self.log.append(('start', key))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
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

    def run_turn(self, calls, wire, budget=None, after=None):
        script = Script({'tool_calls': calls}, *(after or [finish('f', *[c['id'] for c in calls], summary='끝')]))
        caps = Capabilities(self.store, ModelAdapter(script), CFG, '', 'job', self.record, network=wire,
                            judgments=judgments(True), **({'budget': budget} if budget is not None else {}))
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


if __name__ == '__main__':
    unittest.main()
