"""#14: a failed step a later call made up for is not an unresolved failure.

Evidence class: model-free.  A scripted model transport, a fixture
DecisionEngine (the outcome judgment) and a fake CLI that records tool events
replace only the provider, the Judgment AI and the subscription process;
``run_agent``, ``Capabilities``, ``outcome_from_events``,
``AgentService.run_one`` and ``ConversationJudgments.goal_reached`` run
unchanged.  No request, site, provider or category is named in ``src``.
"""
import json
import tempfile
import unittest
from pathlib import Path

import test_agency_loop as loop
import test_orchestrator as orch

from personal_agent.agent_runtime import (GOAL_NOT_SHOWN, RECOVERED, ROUTED_AROUND, STEP_FACTS, UNKNOWN_EFFECT_STEP_FACT,
                                          Capabilities, ToolError, event_trail, failed_steps_text, outcome_from_events,
                                          run_agent, state_change_short, step_recovery, trail_steps)
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

RECOVERED_STEP_FACT = STEP_FACTS[RECOVERED]
ROUTED_AROUND_STEP_FACT = STEP_FACTS[ROUTED_AROUND]


def failed(action, code='tool_failed', effect='none', declared=None, **extra):
    detail = {'host_action': action, 'code': code, 'effect': effect, 'error': f'{action} {code}', **extra}
    if declared:detail['declared_effect'] = declared
    return (action, 'failed', json.dumps(detail))


def succeeded(action, **evidence):
    return (action, 'succeeded', json.dumps({'host_action': action, 'evidence': evidence}))


class RecoveredStepTests(unittest.TestCase):
    """The one projection both routes read: which failures a later call made up for."""

    TYPE_MISS = failed('browser_type', 'target_not_found', declared='mutate')
    READ = succeeded('browser_read', state='page')

    def marks(self, rows):
        return step_recovery(trail_steps(rows))

    def test_marked_only_where_the_state_change_guard_does_not_hold_the_failure(self):
        cases = [
            ('a failed read, then the same read succeeded', [failed('weather'), succeeded('weather')], {0: RECOVERED}),
            ('a failed read, then another call succeeded', [failed('weather'), succeeded('web_search')], {0: ROUTED_AROUND}),
            ('a step Still never ran, then something succeeded',
             [failed('browser_type', 'target_unavailable', declared='read'), self.READ], {0: ROUTED_AROUND}),
            ('a failed state change, then the same action succeeded',
             [self.TYPE_MISS, succeeded('browser_type', state='typed')], {0: RECOVERED}),
            ('a failed state change, then only a read', [self.TYPE_MISS, self.READ], {}),
            ('a read waiting for the owner', [failed('weather', 'needs_setup', requires='connection'),
                                              succeeded('web_search')], {}),
            ('an unknown effect, then the same action succeeded',
             [failed('browser_click', 'effect_unknown', effect='unknown', declared='mutate'),
              succeeded('browser_click', state='clicked')], {}),
            ('a budget-cut call', [failed('weather', 'deadline_exceeded'), succeeded('weather')], {}),
            ('a failure with nothing after it', [succeeded('web_search'), failed('weather')], {}),
            ('only internal bookkeeping after it', [failed('weather'), succeeded('propose_current_state')], {}),
        ]
        for label, rows, expected in cases:
            with self.subTest(label):
                self.assertEqual(self.marks(rows), expected)
                trail = event_trail(rows)[0]
                for index in expected:
                    # Never more lenient than #752's deterministic guard.
                    self.assertFalse(state_change_short(trail[index:]))

    def test_the_judgment_text_marks_each_call_and_lists_open_ones_first(self):
        rows = [failed('weather'), failed('web_search', 'provider_error'),
                failed('browser_click', 'effect_unknown', effect='unknown', declared='mutate'),
                self.TYPE_MISS, succeeded('weather')]
        text = failed_steps_text(trail_steps(rows))
        parts = text.split('; ')
        self.assertEqual(parts, ['browser_click: browser_click effect_unknown ' + UNKNOWN_EFFECT_STEP_FACT,
                                 'browser_type: browser_type target_not_found',
                                 'weather: weather tool_failed ' + RECOVERED_STEP_FACT,
                                 'web_search: web_search provider_error ' + ROUTED_AROUND_STEP_FACT])
        self.assertEqual(failed_steps_text(trail_steps([succeeded('web_search')])), '')

    def test_an_unfinished_work_names_only_the_failures_that_hold_it_down(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = QuickStore(Path(tmp.name) / 'data')
        service = AgentService(store)
        miss = ('browser_type', 'browser_type target_not_found')
        sign_in = ('browser_open', 'sign-in needed')
        weather = ('weather', 'weather tool_failed')
        for label, rows, refusals, expected in (
                ('made up for by the same action', [self.TYPE_MISS, succeeded('browser_type', state='typed')],
                 [miss, miss, sign_in], [sign_in]),
                ('only a read after it', [self.TYPE_MISS, self.READ], [miss, miss, sign_in], [miss, miss, sign_in]),
                # A call merely routed around may be why the Work fell short: it stays named.
                ('a read routed around', [failed('weather'), succeeded('web_search')], [weather], [weather]),
                ('the same failure once recovered and once not',
                 [self.TYPE_MISS, succeeded('browser_type', state='typed'), self.TYPE_MISS],
                 [miss, miss, sign_in], [miss, miss, sign_in])):
            with self.subTest(label):
                job = store.enqueue('x', f'k-{label}')
                with store.db() as db:
                    for tool, status, detail in rows:
                        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,1)',
                                   (job, tool, status, detail))
                # The record wrapper and the CLI outcome both name the failure; a sign-in wait is no tool failure.
                self.assertEqual(service.unrecovered_refusals(job, None, refusals), expected)
        # The CLI outcome rule itself is unchanged: its own events decide the status.
        self.assertEqual(outcome_from_events([self.TYPE_MISS, succeeded('browser_type', state='typed')])[0], 'partial')


class DirectRouteJudgmentInputTests(unittest.TestCase):
    """``run_agent``: the outcome judgment reads which failed steps a later call made up for."""

    REQUEST = [{'role': 'user', 'content': '내일 성남 날씨 알려줘'}]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')
        self.events, self.seen = [], []

    def run_script(self, script, network, answer):
        def judge(facts):
            self.seen.append(facts)
            return answer(facts)
        caps = Capabilities(self.store, ModelAdapter(script), loop.CFG, '', 'job', lambda *event: self.events.append(event),
                            network=network, judgments=loop.judgments(judge))
        return run_agent(caps.adapter, loop.CFG, '', self.REQUEST, '', caps, caps.record)

    @staticmethod
    def strict(facts):
        """A judge that holds every failed step not marked recovered against the reply."""
        steps = [step for step in facts['failed_steps'].split('; ') if step and step != 'none']
        return all(RECOVERED_STEP_FACT in step for step in steps)

    @staticmethod
    def weather_on_second_try(network):
        calls = []
        def execute(plan, original=network.execute):
            calls.append(plan)
            if plan['tool'] == 'weather' and len(calls) > 1:return loop.FORECAST
            return original(plan)
        network.execute = execute
        return network

    def test_recovered_success_meets_the_goal(self):
        script = loop.Script({'tool_calls': [loop.call('1', 'weather', city='Seongnam', country='KR')]},
                             {'tool_calls': [loop.call('2', 'weather', city='Seongnam-si', country='KR')]},
                             loop.finish('f', '2', summary='내일 성남은 최저 14°C, 최고 22°C입니다.'))
        result = self.run_script(script, self.weather_on_second_try(loop.Network()), self.strict)
        self.assertEqual(result.outcome, 'succeeded')
        [facts] = self.seen
        self.assertEqual(facts['failed_steps'], f'weather: {loop.NOT_FOUND} {RECOVERED_STEP_FACT}')
        # The failed attempt stays in Evidence; nothing was run again to make it look clean.
        self.assertEqual([tool for tool, status, _d in self.events if status == 'failed'], ['weather'])
        self.assertEqual(result.report['unknown'], [])

    def test_an_unrecovered_failure_stays_an_open_step(self):
        network = loop.Network()
        def needs_owner(plan, original=network.execute):
            if plan['tool'] == 'weather':
                raise ToolError('날씨 연결이 필요합니다.', 'needs_setup', requires='weather-connection')
            return original(plan)
        network.execute = needs_owner
        script = loop.Script({'tool_calls': [loop.call('1', 'weather', city='Seongnam', country='KR')]},
                             {'tool_calls': [loop.call('2', 'web_search', query='Seongnam weather tomorrow')]},
                             loop.finish('f1', '2', summary='내일 22°C입니다.'),
                             loop.finish('f2', '2', summary='내일 22°C입니다.'))
        result = self.run_script(script, network, self.strict)
        self.assertEqual(result.outcome, 'partial')
        self.assertEqual(self.seen[0]['failed_steps'], 'weather: 날씨 연결이 필요합니다.')
        self.assertIn(['weather', '날씨 연결이 필요합니다.'], result.report['failed'])
        self.assertIn(GOAL_NOT_SHOWN, result.report['unknown'])

    def test_a_claim_without_sufficient_observation_stays_partial_and_says_why(self):
        shown = lambda facts: self.strict(facts) and '강수' in facts['observations']
        script = loop.Script({'tool_calls': [loop.call('1', 'weather', city='Seongnam', country='KR')]},
                             {'tool_calls': [loop.call('2', 'weather', city='Seongnam-si', country='KR')]},
                             loop.finish('f1', '2', summary='내일 성남에는 오후 3시에 비가 옵니다.'),
                             loop.finish('f2', '2', summary='내일 성남에는 오후 3시에 비가 옵니다.'))
        result = self.run_script(script, self.weather_on_second_try(loop.Network()), shown)
        self.assertEqual(result.outcome, 'partial')
        # The step was recovered; what is missing is the observation, and the report says so.
        self.assertTrue(all(RECOVERED_STEP_FACT in facts['failed_steps'] for facts in self.seen))
        self.assertEqual(result.report['unknown'], [GOAL_NOT_SHOWN])


class DirectRouteServiceTests(unittest.TestCase):
    """``AgentService.run_one`` on the web and Telegram channels: status, note and cause agree."""

    CHANNELS = loop.OwnerEntryPointTests.CHANNELS
    service = loop.OwnerEntryPointTests.service
    run_turn = loop.OwnerEntryPointTests.run_turn

    def test_a_shortfall_after_a_recovered_step_names_the_judgment_not_the_step(self):
        for route in self.CHANNELS:
            with self.subTest(route=route[0]):
                summary = '내일 성남에는 오후 3시에 비가 옵니다.'
                script = loop.Script({'tool_calls': [loop.call('1', 'weather', city='Seongnam', country='KR')]},
                                     {'tool_calls': [loop.call('2', 'weather', city='Seongnam-si', country='KR')]},
                                     loop.finish_observed('f1', summary=summary), loop.finish_observed('f2', summary=summary))
                network = DirectRouteJudgmentInputTests.weather_on_second_try(loop.Network())
                seen = []
                def judge(facts):
                    seen.append(facts['failed_steps'])
                    return False
                row, failed = self.run_turn('내일 성남 날씨 알려줘', script, network, route, engine=loop.goal_engine(judge))
                self.assertEqual(row['status'], 'partial')
                self.assertTrue(seen and all(RECOVERED_STEP_FACT in item for item in seen))
                # Before #14 the note counted the recovered read and the cause named it.
                self.assertIsNone(row['owner_note'])
                self.assertIsNone(row['error'])
                self.assertIn(GOAL_NOT_SHOWN, row['owner_cause'])
                self.assertEqual([event['tool'] for event in failed], ['weather'], 'the failed attempt stays in Evidence')

    def test_an_unrecovered_failure_is_still_named(self):
        for route in self.CHANNELS:
            with self.subTest(route=route[0]):
                script = loop.Script({'tool_calls': [loop.call('1', 'weather', city='Nowhere', country='KR')]},
                                     {'tool_calls': [loop.call('2', 'weather', city='Nowhere-si', country='KR')]},
                                     {'content': '그 지역을 찾지 못했습니다.'})
                row, failed = self.run_turn('내일 Nowhere 날씨 알려줘', script, loop.Network(), route)
                self.assertEqual(row['status'], 'failed')
                self.assertIn(loop.NOT_FOUND, row['error'])


class CliRouteOutcomeTests(orch.Harness):
    """The CLI route: the judgment input, the stored status and the owner's note agree."""

    TYPE_MISS = {'host_action': 'browser_type', 'code': 'target_not_found', 'effect': 'none',
                 'declared_effect': 'mutate', 'error': '입력할 칸을 찾지 못했습니다.'}

    def attempt(self, *events):
        def record(tools):
            for tool, status, detail in events:
                tools.capabilities.record(tool, status, json.dumps(detail))
        self.engine.before = record

    def test_recovered_success_is_succeeded_without_naming_the_earlier_step(self):
        self.attempt(('browser_type', 'failed', self.TYPE_MISS),
                     ('browser_type', 'succeeded', {'host_action': 'browser_type', 'evidence': {'state': 'typed'}}))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[True])
        job, row = self.run_work('그 칸에 이름 넣어줘')
        self.assertEqual(row['status'], 'succeeded')
        self.assertIsNone(row['owner_note'])
        [goal] = self.asked_goals
        self.assertEqual(goal.facts['failed_steps'], f"browser_type: {self.TYPE_MISS['error']} {RECOVERED_STEP_FACT}")
        self.assertEqual(len(self.engine.turns), 1, 'nothing was run again after the effect')

    def test_a_shortfall_after_a_recovered_step_names_the_judgment_not_the_step(self):
        self.attempt(('browser_type', 'failed', self.TYPE_MISS),
                     ('browser_type', 'succeeded', {'host_action': 'browser_type', 'evidence': {'state': 'typed'}}))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[False])
        job, row = self.run_work('그 칸에 이름 넣고 제출해줘')
        self.assertEqual(row['status'], 'partial')
        # Before #14 the note counted the recovered step ('한 단계는 끝내지 못했어요') and the cause named it.
        self.assertIsNone(row['owner_note'])
        self.assertIsNone(row['error'])
        self.assertIn(GOAL_NOT_SHOWN, row['owner_cause'])
        self.assertNotIn(self.TYPE_MISS['error'], row['owner_cause'])

    def test_an_unrecovered_state_change_keeps_the_work_partial_and_named(self):
        self.attempt(('browser_type', 'failed', self.TYPE_MISS),
                     ('browser_read', 'succeeded', {'host_action': 'browser_read', 'evidence': {'state': 'page'}}))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[True])
        job, row = self.run_work('그 칸에 이름 넣어줘')
        # #752: a reached verdict never outranks a state change that did not happen.
        self.assertEqual(row['status'], 'partial')
        self.assertIn('한 단계는 끝내지 못했어요', row['owner_note'])
        self.assertIn(self.TYPE_MISS['error'], row['error'])
        [goal] = self.asked_goals
        self.assertNotIn(RECOVERED_STEP_FACT, goal.facts['failed_steps'])

    def test_an_unknown_external_effect_is_unknown_and_never_judged_or_replayed(self):
        self.attempt(('browser_click', 'failed', {'host_action': 'browser_click', 'code': 'effect_unknown',
                                                  'effect': 'unknown', 'declared_effect': 'mutate', 'error': '응답이 끊겼습니다.'}),
                     ('browser_click', 'succeeded', {'host_action': 'browser_click', 'evidence': {'state': 'clicked'}}))
        self.script([orch.plan('codex', 'Submit it.')], goals=[True])
        job, row = self.run_work('제출해줘')
        self.assertEqual(row['status'], 'unknown')
        self.assertEqual(self.asked_goals, [], 'no judgment can call an unknown effect reached')
        self.assertEqual(len(self.engine.turns), 1, 'the possible effect is not replayed')


if __name__ == '__main__':
    unittest.main()
