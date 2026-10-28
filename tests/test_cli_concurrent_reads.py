"""#386: independent effect-free reads overlap on the CLI route (#16 slice 1, ``test_concurrent_reads``, generalized); nothing else does."""
import json
import threading
import time
import unittest

from personal_agent.agent_runtime import CONCURRENT_READ_LIMIT, concurrent_read
from personal_agent.bounded_execution import incomplete_bridge_calls


class Timeline:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = []
        self.active = 0
        self.peak = 0

    def call(self, name, seconds=0.05):
        def run():
            with self.lock:
                self.events.append(('enter', name))
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(seconds)
            with self.lock:
                self.events.append(('exit', name))
                self.active -= 1
            return name
        return run


class CallOrderTest(unittest.TestCase):
    def run_calls(self, plan):
        """Start ``(name, read)`` calls in order, as the dispatcher spawns one task per request."""
        import anyio
        from personal_agent.mcp_bridge import _CallOrder
        timeline = Timeline()

        async def main():
            order = _CallOrder()
            async with anyio.create_task_group() as group:
                for name, read in plan:
                    group.start_soon(order.run, timeline.call(name), read)
                    await anyio.sleep(0.005)
        anyio.run(main)
        return timeline

    def test_reads_overlap(self):
        timeline = self.run_calls([('a', True), ('b', True)])
        self.assertEqual([kind for kind, _ in timeline.events], ['enter', 'enter', 'exit', 'exit'])

    def test_a_change_waits_for_earlier_reads_and_later_calls_wait_for_it(self):
        timeline = self.run_calls([('r1', True), ('r2', True), ('write', False), ('r3', True)])
        events = timeline.events
        write_in, write_out = events.index(('enter', 'write')), events.index(('exit', 'write'))
        self.assertEqual(write_out, write_in + 1, 'nothing overlaps the change')
        self.assertLess(events.index(('exit', 'r1')), write_in)
        self.assertLess(events.index(('exit', 'r2')), write_in)
        self.assertGreater(events.index(('enter', 'r3')), write_out, 'a later read does not overtake the change')

    def test_changes_run_one_at_a_time_in_arrival_order(self):
        timeline = self.run_calls([('w1', False), ('w2', False), ('w3', False)])
        self.assertEqual(timeline.events, [(kind, name) for name in ('w1', 'w2', 'w3') for kind in ('enter', 'exit')])

    def test_a_page_read_waits_for_the_searches_before_it(self):
        """#16's rule: a page read whose scope includes search results never overtakes a search."""
        import anyio
        from personal_agent.mcp_bridge import _CallOrder
        timeline = Timeline()

        async def main():
            order = _CallOrder()
            async with anyio.create_task_group() as group:
                group.start_soon(lambda: order.run(timeline.call('search'), True, search=True))
                await anyio.sleep(0.005)
                group.start_soon(lambda: order.run(timeline.call('page'), True, after_searches=True))
                await anyio.sleep(0.005)
                group.start_soon(lambda: order.run(timeline.call('other'), True))
        anyio.run(main)
        events = timeline.events
        self.assertLess(events.index(('exit', 'search')), events.index(('enter', 'page')))
        self.assertLess(events.index(('enter', 'other')), events.index(('exit', 'search')), 'other reads still overlap')

    def test_a_search_that_arrived_after_a_page_read_waits_for_it(self):
        """A later search cannot admit an earlier page read: the page runs, and ends, with what it arrived with."""
        import anyio
        from personal_agent.mcp_bridge import _CallOrder
        timeline = Timeline()

        async def main():
            order = _CallOrder()
            async with anyio.create_task_group() as group:
                group.start_soon(lambda: order.run(timeline.call('page', 0.01), True, after_searches=True))
                group.start_soon(lambda: order.run(timeline.call('search', 0.1), True, search=True))
        anyio.run(main)
        events = timeline.events
        self.assertLess(events.index(('exit', 'page')), events.index(('enter', 'search')))

    def test_at_most_the_concurrent_read_limit_run_together(self):
        timeline = self.run_calls([(f'r{index}', True) for index in range(CONCURRENT_READ_LIMIT + 3)])
        self.assertEqual(timeline.peak, CONCURRENT_READ_LIMIT)

    def test_a_cancelled_turn_waits_for_a_started_call_to_finish(self):
        import anyio
        from personal_agent.mcp_bridge import _CallOrder
        timeline = Timeline()

        async def main():
            order = _CallOrder()
            async with anyio.create_task_group() as group:
                group.start_soon(order.run, timeline.call('read', 0.1), True)
                await anyio.sleep(0.02)
                group.cancel_scope.cancel()
        anyio.run(main)
        self.assertEqual(timeline.events, [('enter', 'read'), ('exit', 'read')])


class ConcurrentReadRule(unittest.TestCase):
    def test_keyed_on_the_effect_not_the_tool(self):
        self.assertTrue(concurrent_read('mail_read', {}))
        self.assertTrue(concurrent_read('read_file', {}))
        self.assertTrue(concurrent_read('api_request', {'method': 'GET', 'effect': 'read'}))
        for action, args in (('api_request', {'method': 'POST', 'effect': 'read'}),
                             ('api_request', {'method': 'GET'}),  # undeclared: Still's floor is mutate
                             ('api_request', {'method': 'GET', 'effect': 'mutate'}),
                             ('browser_open', {}), ('browser_click', {}), ('code_run', {}),
                             ('skill_load', {}), ('method_gap', {}), ('save_note', {}), ('unknown', {})):
            with self.subTest(action=action, args=args):
                self.assertFalse(concurrent_read(action, args))


class IncompleteCallsPairByCallId(unittest.TestCase):
    def test_an_overlapping_read_left_open_is_the_one_reported(self):
        row = lambda status, ref: ('api_request', status, json.dumps({'scope': 'subscription-mcp-bridge', 'call_id': ref,
                                                                      'host_action': 'api_request'}))
        rows = [row('running', 'a'), row('running', 'b'), row('succeeded', 'b')]
        self.assertEqual(incomplete_bridge_calls(rows), [('api_request', 'api_request')])
        self.assertEqual(incomplete_bridge_calls(rows + [row('succeeded', 'a')]), [])


class RelayOverlap(unittest.TestCase):
    def relay(self, actions):
        from personal_agent.cli_browser_relay import BrowserRelay
        timeline = Timeline()

        class Capabilities:
            tools = {name: {'host_action': action} for name, action in actions.items()}
            memo = {}

            def keep_read_result(self, *args):
                pass

        class Tools:
            capabilities = Capabilities()

            def call(self, name, arguments):
                return timeline.call(name)()
        relay = BrowserRelay(Tools())
        self.addCleanup(relay.close)
        return relay, timeline

    def send(self, relay, calls):
        from personal_agent.cli_browser_relay import RelayClient
        threads = [threading.Thread(target=RelayClient(relay.address).call, args=(name, arguments)) for name, arguments in calls]
        for thread in threads:
            thread.start()
            time.sleep(0.005)
        for thread in threads:
            thread.join(5)

    def test_reads_overlap_and_browser_steps_do_not(self):
        relay, timeline = self.relay({'mail_read': 'mail_read', 'api_request': 'api_request'})
        self.send(relay, [('mail_read', {'id': '1'}), ('api_request', {'method': 'GET', 'effect': 'read', 'url': 'u'})])
        self.assertEqual([kind for kind, _ in timeline.events], ['enter', 'enter', 'exit', 'exit'])
        relay, timeline = self.relay({'browser_open': 'browser_open', 'browser_read': 'browser_read'})
        self.send(relay, [('browser_open', {'url': 'https://a.example/'}), ('browser_read', {})])
        self.assertEqual([kind for kind, _ in timeline.events], ['enter', 'exit', 'enter', 'exit'])

    def test_a_relayed_change_waits_its_turn(self):
        relay, timeline = self.relay({'api_request': 'api_request'})
        self.send(relay, [('api_request', {'method': 'POST', 'effect': 'mutate', 'url': 'u'}),
                          ('api_request', {'method': 'PATCH', 'effect': 'mutate', 'url': 'v'})])
        self.assertEqual([kind for kind, _ in timeline.events], ['enter', 'exit', 'enter', 'exit'])


if __name__ == '__main__':
    unittest.main()
