"""#620 (E4 of #614): one Work budget, its remaining part shown to the model, and the loop guard.

Evidence class: unit and model-free integration.  A scripted model transport,
a fake public network and the fixture ``FakeDriver`` replace only the
provider, the wire and the browser engine; ``run_agent``, ``Capabilities``,
``BrowserSession``, the CLI browser relay and the MCP bridge's result shaping
run unchanged.  Adopted patterns, named where they apply: OpenAI Agents SDK
``max_turns`` (a controlled final output at the run limit), LangGraph
``recursion_limit`` / ``RemainingSteps`` (the run sees what remains and winds
down), OpenHands ``StuckDetector`` (a loop is the same action meeting the same
observation, not merely the same path).
"""
import json
import tempfile
import unittest
from pathlib import Path

from personal_agent import browser_session as bs
from personal_agent.agent_runtime import (LAST_TURN_TEXT, WIND_DOWN_TEXT, WORK_JUDGMENT_RESERVE, Capabilities, LoopGuard,
                                          ToolError, WorkBudget, WorkLedger, loop_outcome, path_key, remaining_budget,
                                          run_agent)
from personal_agent.cli_browser_relay import BrowserRelay
from personal_agent.mcp_bridge import _with_remaining, tool_error_result
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore

from test_agency_loop import CFG, Network, Script, call, judgments
from test_browser_session import ORIGIN, Approvals, FakeDriver


def tool_replies(body):
    return {m['tool_call_id']: json.loads(m['content']) for m in body['messages'] if m.get('role') == 'tool'}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = QuickStore(Path(self.tmp.name) / 'data')
        self.events = []

    def record(self, tool, status, detail):
        self.events.append((tool, status, detail))

    def failed(self):
        return [json.loads(detail) for tool, status, detail in self.events if status == 'failed' and tool != 'model']

    def caps(self, transport, **kwargs):
        kwargs.setdefault('network', Network())
        return Capabilities(self.store, ModelAdapter(transport), CFG, '', 'job', self.record, **kwargs)


class OneStepCounter(Base):
    """Item 1: a browser step spends the Work's one attempt budget, once, and only when it runs."""

    def test_a_browser_step_through_execute_spends_exactly_one_attempt(self):
        budget = WorkBudget()
        caps = self.caps(Script(), browser=lambda: FakeDriver(), budget=budget)
        caps.execute('browser_open', {'url': ORIGIN + '/product', 'effect': 'read'})
        caps.execute('browser_read', {})
        caps.execute('list_notes', {})
        self.assertEqual(budget.attempts_used, 3, 'one counter: each call, a browser step included, spends one')
        caps.close_browser()

    def test_a_step_held_for_approval_spends_nothing(self):
        budget = WorkBudget()
        approvals = Approvals()
        caps = self.caps(Script(), browser=lambda: FakeDriver(), budget=budget, browser_approvals=approvals)
        self.addCleanup(caps.close_browser)
        caps.execute('browser_open', {'url': ORIGIN + '/checkout', 'effect': 'navigate'})
        self.assertEqual(budget.attempts_used, 1)
        with self.assertRaises(ToolError) as caught:
            caps.execute('browser_click', {'target': '결제하기', 'effect': 'mutate'})
        self.assertEqual(caught.exception.code, 'approval_required')
        self.assertEqual(len(approvals.requests), 1)
        self.assertEqual(budget.attempts_used, 1, 'a press held for the owner ran nothing and spends nothing')
        with self.assertRaises(ToolError):
            caps.execute('browser_click', {'target': '없는 버튼', 'effect': 'read'})
        self.assertEqual(budget.attempts_used, 1, 'a missing target ran nothing either')

    def test_a_busy_browser_spends_nothing(self):
        budget = WorkBudget()

        def busy():
            raise ToolError('busy', 'browser_busy')
        caps = self.caps(Script(), browser=busy, budget=budget)
        with self.assertRaises(ToolError) as caught:
            caps.execute('browser_open', {'url': ORIGIN + '/product', 'effect': 'read'})
        self.assertEqual(caught.exception.code, 'browser_busy')
        self.assertEqual(budget.attempts_used, 0)

    def test_a_stopped_work_refuses_the_step_before_its_snapshot(self):
        stopped = [False]
        driver = FakeDriver()
        sess = bs.BrowserSession(lambda: driver, work_id='w', budget=WorkBudget(stop=lambda: stopped[0]))
        sess.open({'url': ORIGIN + '/account', 'effect': 'read'})
        stopped[0] = True
        before = len(driver.log)
        with self.assertRaises(ToolError) as caught:
            sess.click({'target': '저장', 'effect': 'read'})
        self.assertEqual(caught.exception.code, 'stopped')
        self.assertEqual(len(driver.log), before)


class RemainingBudget(Base):
    """Item 2: the remaining budget rides on tool results (LangGraph ``RemainingSteps``)."""

    def test_tool_results_carry_turns_steps_and_seconds(self):
        script = Script({'tool_calls': [call('1', 'list_notes')]}, {'content': '없습니다.'})
        caps = self.caps(script, budget=WorkBudget(turns=10, attempts=50))
        run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': '메모'}], '', caps, self.record)
        remaining = tool_replies(script.bodies[1])['1']['remaining']
        self.assertEqual((remaining['turns'], remaining['steps']), (10 - WORK_JUDGMENT_RESERVE - 1, 49))
        self.assertGreater(remaining['seconds'], 0)
        self.assertNotIn('note', remaining)

    def test_little_left_adds_the_wind_down_note(self):
        budget = WorkBudget(attempts=12)
        self.assertNotIn('note', remaining_budget(budget))
        budget.spend_attempt(); budget.spend_attempt()
        self.assertEqual(remaining_budget(budget)['note'], WIND_DOWN_TEXT)
        self.assertNotIn('turns', remaining_budget(budget, turns=False))

    def test_the_shared_ledger_is_what_both_views_report(self):
        with self.store.db() as db:
            db.execute("INSERT INTO jobs(id,request_key,message,status,created) VALUES ('j','k','x','running',1)")
        host = WorkBudget(attempts=20, ledger=WorkLedger(self.store, 'j'))
        bridge = WorkBudget(attempts=20, ledger=WorkLedger(self.store, 'j'))
        host.spend_attempt(); host.spend_attempt()
        self.assertEqual(bridge.left()['steps'], 18, 'the bridge reads the attempts the host spent')

    def test_the_bridge_adds_remaining_to_results_and_failures(self):
        budget = WorkBudget(attempts=30)
        ok = _with_remaining({'content': [{'type': 'text', 'text': '{}'}]}, budget)
        shown = json.loads(ok['content'][-1]['text'])['remaining']
        self.assertEqual(sorted(shown), ['seconds', 'steps'])
        self.assertEqual(shown['steps'], 30)
        failed, typed = tool_error_result(ToolError('no', 'tool_failed'), 'list_notes')
        _with_remaining(failed, budget)
        self.assertEqual(failed['structuredContent']['remaining']['steps'], 30)
        self.assertNotIn('remaining', typed, 'the recorded event keeps its own shape')
        self.assertNotIn('turns', failed['structuredContent']['remaining'], "the CLI's own turns are not Still's")

    def test_the_last_turn_is_told_so_and_its_plain_reply_concludes(self):
        # turns=3: two worker turns, the third held for the outcome judgment (#1418).
        script = Script({'tool_calls': [call('1', 'list_notes')]}, {'content': '메모가 없습니다. 남은 일은 없습니다.'})
        caps = self.caps(script, budget=WorkBudget(turns=3), judgments=judgments(True))
        result = run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': '메모 보여줘'}], '', caps, self.record)
        self.assertEqual(len(script.bodies), 2)
        self.assertNotIn(LAST_TURN_TEXT, [m.get('content') for m in script.bodies[0]['messages']])
        self.assertEqual(script.bodies[1]['messages'][-1], {'role': 'system', 'content': LAST_TURN_TEXT})
        # No completion-check turn follows: the model's own final answer is the run's answer, judged once.
        self.assertIn('메모가 없습니다', result.content)
        self.assertEqual(result.outcome, 'succeeded')

    def test_a_run_with_no_observation_is_never_told_to_answer_without_tools(self):
        # One worker turn only: telling it "no tools" would turn a needed lookup into plain conversation.
        script = Script({'tool_calls': [call('1', 'list_notes')]}, {'content': 'unused'})
        caps = self.caps(script, budget=WorkBudget(turns=2))
        result = run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': '메모 보여줘'}], '', caps, self.record)
        self.assertNotIn(LAST_TURN_TEXT, [m.get('content') for m in script.bodies[0]['messages']])
        self.assertEqual(len(script.bodies), 1)
        self.assertEqual(result.outcome, 'partial', 'the turn limit ends it with the true reason, never as succeeded')


class LoopGuardUnit(unittest.TestCase):
    """Item 4: OpenHands-style stuck detection."""

    def test_success_and_permanent_failure_refuse_a_transient_failure_allows_one_retry(self):
        guard = LoopGuard()
        for outcome, refused_after in (('seen', True), ('failed', True)):
            key = outcome
            guard.admit(key)
            self.assertTrue(guard.refused(key), 'in flight: the same call is not sent twice')
            guard.settle(outcome, key)
            self.assertEqual(guard.refused(key), refused_after)
        guard.admit('t'); guard.settle('transient', 't')
        self.assertFalse(guard.refused('t'), 'one retry after a transient failure')
        guard.admit('t'); guard.settle('transient', 't')
        self.assertTrue(guard.refused('t'), 'not a second')

    def test_a_read_after_a_write_is_a_new_observation(self):
        guard = LoopGuard()
        key = guard.scoped('calendar', True)
        guard.admit(key); guard.settle('seen', key)
        self.assertTrue(guard.refused(guard.scoped('calendar', True)))
        guard.wrote()
        self.assertFalse(guard.refused(guard.scoped('calendar', True)))
        self.assertEqual(guard.scoped('page-step', False), 'page-step', 'a page step is keyed on its page instead')

    def test_only_a_retry_that_cannot_duplicate_an_effect_is_transient(self):
        timeout = {'code': 'browser_timeout', 'effect': 'none'}
        self.assertEqual(loop_outcome('browser_click', {'effect': 'navigate'}, timeout), 'transient')
        self.assertEqual(loop_outcome('browser_click', {'effect': 'mutate'}, timeout), 'failed')
        self.assertEqual(loop_outcome('browser_click', {'effect': 'mutate'}, {**timeout, 'ran': False}), 'transient')
        self.assertEqual(loop_outcome('web_search', {}, {'code': 'transient_failure', 'effect': 'none'}), 'transient')
        self.assertEqual(loop_outcome('web_search', {}, {'code': 'transient_failure', 'effect': 'unknown'}), 'failed')
        self.assertEqual(loop_outcome('web_search', {}, {'code': 'tool_failed', 'effect': 'none'}), 'failed')
        for code in ('browser_busy', 'destination_check_timeout', 'destination_check_failed'):
            self.assertEqual(loop_outcome('browser_open', {'effect': 'read'}, {'code': code, 'effect': 'none'}), 'transient')

    def test_the_declared_effect_is_part_of_a_page_step(self):
        self.assertNotEqual(path_key('browser_click', {'target': '1', 'effect': 'read'}, 'p'),
                            path_key('browser_click', {'target': '1', 'effect': 'payment'}, 'p'))


class LoopGuardInTheLoop(Base):
    def test_a_search_that_failed_transiently_may_be_sent_once_more(self):
        network = Network(search=False)
        same = {'query': 'Seongnam weather'}
        script = Script({'tool_calls': [call('1', 'web_search', **same)]}, {'tool_calls': [call('2', 'web_search', **same)]},
                        {'tool_calls': [call('3', 'web_search', **same)]}, {'content': '검색하지 못했습니다.'})
        caps = self.caps(script, network=network)
        run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': '날씨'}], '', caps, self.record)
        codes = [row['code'] for row in self.failed() if row.get('scope') != 'transient-retry']
        self.assertEqual(codes, ['transient_failure', 'transient_failure', 'repeat_path'])
        # Each model call reached the wire with its one internal retry; the third was refused before it.
        self.assertEqual(len(network.plans), 4)

    def test_a_read_after_an_intervening_write_runs_again(self):
        script = Script({'tool_calls': [call('1', 'list_notes')]}, {'tool_calls': [call('2', 'save_note', content='공항 3시')]},
                        {'tool_calls': [call('3', 'list_notes')]}, {'tool_calls': [call('4', 'list_notes')]},
                        {'content': '저장하고 확인했습니다.'})
        caps = self.caps(script)
        run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': '메모 저장하고 다시 확인해줘'}], '', caps, self.record)
        replies = tool_replies(script.bodies[-1])
        self.assertIn('공항 3시', json.dumps(replies['3'], ensure_ascii=False), 'the re-read sees the write, not a stale memo')
        self.assertEqual([row['code'] for row in self.failed()], ['duplicate_call'],
                         'only the second re-read with nothing changed in between is refused')

    def test_the_same_write_is_still_never_sent_twice(self):
        script = Script({'tool_calls': [call('1', 'save_note', content='x')]}, {'tool_calls': [call('2', 'save_note', content='x')]},
                        {'content': '저장했습니다.'})
        caps = self.caps(script)
        run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': '저장'}], '', caps, self.record)
        self.assertEqual([row['code'] for row in self.failed()], ['duplicate_call'])

    def test_a_guard_refusal_earns_no_nudge_but_a_failed_path_does(self):
        script = Script({'tool_calls': [call('1', 'list_notes')]}, {'tool_calls': [call('2', 'list_notes')]},
                        {'tool_calls': [call('3', 'weather', city='Nowhere', country='KR')]}, {'content': '없습니다.'})
        caps = self.caps(script)
        run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': '메모와 날씨'}], '', caps, self.record)
        nudge = lambda body: [m for m in body['messages'] if m['role'] == 'system' and m['content'].startswith('Path check')]
        self.assertEqual(nudge(script.bodies[2]), [], 'duplicate_call is the guard: no nudge')
        self.assertEqual(len(nudge(script.bodies[3])), 1, 'a failed path still earns one')


class RelayLoopGuard(unittest.TestCase):
    """The CLI route's relay applies the same guard (``cli_browser_relay``)."""

    def relay(self, outcomes):
        class Tools:
            calls = []

            class capabilities:
                tools = {'browser_click': {'host_action': 'browser_click'}}

            def call(self, name, arguments):
                Tools.calls.append(name)
                outcome = outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        relay = BrowserRelay(Tools())
        self.addCleanup(relay.close)
        relay._page = 'page-digest'
        return relay, Tools.calls

    def request(self, relay, effect):
        line = json.dumps({'key': relay._key, 'name': 'browser_click',
                           'arguments': {'target': '3', 'effect': effect}}).encode()
        return relay._reply(line)

    def test_a_navigate_step_that_timed_out_may_run_once_more(self):
        timeout = ToolError('slow', 'browser_timeout')
        relay, calls = self.relay([timeout, ToolError('slow', 'browser_timeout')])
        self.assertEqual(self.request(relay, 'navigate')['error']['code'], 'browser_timeout')
        self.assertEqual(self.request(relay, 'navigate')['error']['code'], 'browser_timeout')
        self.assertEqual(self.request(relay, 'navigate')['error']['code'], 'repeat_path')
        self.assertEqual(len(calls), 2)

    def test_a_mutate_step_that_timed_out_is_not_pressed_again(self):
        relay, calls = self.relay([ToolError('slow', 'browser_timeout')])
        self.request(relay, 'mutate')
        self.assertEqual(self.request(relay, 'mutate')['error']['code'], 'repeat_path')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
