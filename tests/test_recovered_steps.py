"""#14: a failed step a later call made up for is not an unresolved failure.

Evidence class: model-free.  A scripted model transport, a fixture
DecisionEngine (the outcome judgment), a fake browser behind ``Capabilities``
and a fake CLI that records tool events replace only the provider, the
Judgment AI, the browser and the subscription process; ``run_agent``,
``outcome_from_events``, ``AgentService.run_one`` and
``ConversationJudgments.goal_reached`` run unchanged.  No request, site,
provider or category is named in ``src``.
"""
import json
import tempfile
import unittest
from pathlib import Path

import test_agency_loop as loop
import test_orchestrator as orch

from personal_agent.agent_runtime import (FAILED_STEPS_CHARS, GOAL_NOT_SHOWN, RECOVERED, ROUTED_AROUND, STEP_FACTS,
                                          UNKNOWN_EFFECT_STEP_FACT, Capabilities, ToolError, event_trail, failed_steps_text,
                                          outcome_from_events, run_agent, state_change_short, step_recovery, trail_steps)
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

RECOVERED_STEP_FACT = STEP_FACTS[RECOVERED]
ROUTED_AROUND_STEP_FACT = STEP_FACTS[ROUTED_AROUND]
NAME = {'target': 'name', 'text': '[가림: 3자]', 'effect': 'mutate'}
EMAIL = {'target': 'email', 'text': '[가림: 9자]', 'effect': 'mutate'}


def failed(action, code='tool_failed', effect='none', declared=None, **extra):
    detail = {'host_action': action, 'code': code, 'effect': effect, 'error': f'{action} {code}', **extra}
    if declared:detail['declared_effect'] = declared
    return (action, 'failed', json.dumps(detail))


def succeeded(action, evidence=None, **extra):
    return (action, 'succeeded', json.dumps({'host_action': action, 'evidence': evidence or {}, **extra}))


def running(action, call_id, arguments):
    return (action, 'running', json.dumps({'scope': 'main', 'call_id': call_id, 'host_action': action, 'arguments': arguments}))


def typed(call_id, arguments, ok):
    """One recorded browser_type call: its running record, then its terminal record (no arguments)."""
    terminal = (succeeded('browser_type', {'state': 'typed'}, scope='main', call_id=call_id) if ok else
                failed('browser_type', 'target_not_found', declared='mutate', scope='main', call_id=call_id))
    return [running('browser_type', call_id, arguments), terminal]


class StepRecoveryTests(unittest.TestCase):
    """The one projection both routes read: which failures a later call made up for."""

    TYPE_MISS = failed('browser_type', 'target_not_found', declared='mutate')
    READ = succeeded('browser_read', {'state': 'page'})

    def marks(self, rows):
        return step_recovery(trail_steps(rows))

    def test_marked_only_for_the_same_invocation_or_a_call_that_changed_nothing(self):
        cases = [
            ('the same state change on the same target succeeded',
             typed('1', NAME, False) + typed('2', NAME, True), {0: RECOVERED}),
            # Review P1: another target of the same action recovers nothing.
            ('the same action on another target', typed('1', NAME, False) + typed('2', EMAIL, True), {}),
            ('the same action with no recorded arguments', [self.TYPE_MISS, succeeded('browser_type')], {}),
            ('the same recorded call', [failed('weather', 'transient_failure', scope='main', call_id='7'),
                                        succeeded('weather', scope='main', call_id='7')], {0: RECOVERED}),
            ('a failed read, then another call succeeded', [failed('weather'), succeeded('web_search')], {0: ROUTED_AROUND}),
            ('a step Still never ran, then something succeeded',
             [failed('browser_type', 'target_unavailable', declared='read'), self.READ], {0: ROUTED_AROUND}),
            ('a failed state change, then only a read', [self.TYPE_MISS, self.READ], {}),
            ('a read waiting for the owner', [failed('weather', 'needs_setup', requires='connection'),
                                              succeeded('web_search')], {}),
            ('an unknown effect, then the same call succeeded',
             [failed('browser_click', 'effect_unknown', effect='unknown', declared='mutate', scope='main', call_id='3'),
              succeeded('browser_click', {'state': 'clicked'}, scope='main', call_id='3')], {}),
            ('a budget-cut call', [failed('weather', 'deadline_exceeded'), succeeded('web_search')], {}),
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
        rows = [failed('weather'), failed('browser_click', 'effect_unknown', effect='unknown', declared='mutate'),
                self.TYPE_MISS, *typed('1', NAME, False), *typed('2', NAME, True)]
        self.assertEqual(failed_steps_text(trail_steps(rows)).split('; '),
                         ['browser_click: browser_click effect_unknown ' + UNKNOWN_EFFECT_STEP_FACT,
                          'browser_type: browser_type target_not_found',
                          'weather: weather tool_failed ' + ROUTED_AROUND_STEP_FACT,
                          'browser_type: browser_type target_not_found ' + RECOVERED_STEP_FACT])
        self.assertEqual(failed_steps_text(trail_steps([succeeded('web_search')])), '')

    def test_no_bound_ever_leaves_a_call_without_its_mark(self):
        """Review P2: entries are kept whole inside the judgment's bound; the rest are counted."""
        long = 'x' * 900
        rows = [*(failed('browser_click', 'tool_failed', declared='mutate', error=f'{n} {long}') for n in range(6)),
                failed('weather', error=long), succeeded('weather', scope='s', call_id='c'), succeeded('web_search')]
        text = failed_steps_text(trail_steps(rows))
        self.assertLessEqual(len(text), FAILED_STEPS_CHARS)
        entries = text.split('; ')
        self.assertRegex(entries[-1], r'^\d+ more failed calls not shown$')
        for entry in entries[:-1]:
            self.assertLessEqual(len(entry), 160 + len('browser_click: '))
        # A recovered call with a long reason keeps its mark.
        alone = failed_steps_text(trail_steps([failed('weather', error=long, scope='s', call_id='c'),
                                                succeeded('weather', scope='s', call_id='c')]))
        self.assertTrue(alone.endswith(RECOVERED_STEP_FACT))

    def test_an_unfinished_work_names_only_the_failures_that_hold_it_down(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = QuickStore(Path(tmp.name) / 'data')
        service = AgentService(store)
        miss = ('browser_type', 'browser_type target_not_found')
        weather = ('weather', 'weather tool_failed')
        sign_in = ('browser_open', 'sign-in needed')
        for label, rows, refusals, expected in (
                ('the same target succeeded later', typed('1', NAME, False) + typed('2', NAME, True),
                 [miss, miss, sign_in], [sign_in]),
                ('another target succeeded later', typed('1', NAME, False) + typed('2', EMAIL, True),
                 [miss, miss, sign_in], [miss, miss, sign_in]),
                ('only a read after it', [self.TYPE_MISS, self.READ], [miss, sign_in], [miss, sign_in]),
                # A call merely routed around may be why the Work fell short: it stays named.
                ('a read routed around', [failed('weather'), succeeded('web_search')], [weather], [weather]),
                ('the same failure once recovered and once not',
                 typed('1', NAME, False) + typed('2', NAME, True) + typed('3', EMAIL, False),
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
        self.assertEqual(outcome_from_events(typed('1', NAME, False) + typed('2', NAME, True))[0], 'partial')


class FakeBrowserCapabilities(Capabilities):
    """``Capabilities`` with a fake browser: the first ``misses`` ``browser_type`` calls miss, then it types."""

    def __init__(self, *args, misses=1, **kwargs):
        super().__init__(*args, browser=lambda: object(), **kwargs)
        self.misses, self.tries = misses, 0

    def execute(self, name, args):
        if name != 'browser_type':
            return super().execute(name, args)
        self.tries += 1
        if self.tries <= self.misses:
            raise ToolError('입력할 칸을 찾지 못했습니다.', 'target_not_found')
        return {'state': 'typed', 'target': args.get('target')}


class DirectRouteJudgmentInputTests(unittest.TestCase):
    """``run_agent``: the outcome judgment reads which failed steps a later call made up for."""

    REQUEST = [{'role': 'user', 'content': '이름 칸에 Kim 넣어줘'}]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')
        self.events, self.seen = [], []

    def run_script(self, script, answer, network=None, browser=True):
        def judge(facts):
            self.seen.append(facts)
            return answer(facts)
        kind = FakeBrowserCapabilities if browser else Capabilities
        caps = kind(self.store, ModelAdapter(script), loop.CFG, '', 'job', lambda *event: self.events.append(event),
                    network=network or loop.Network(), judgments=loop.judgments(judge))
        return run_agent(caps.adapter, loop.CFG, '', self.REQUEST, '', caps, caps.record)

    @staticmethod
    def strict(facts):
        """A judge that holds every failed step not marked recovered against the reply."""
        steps = [step for step in facts['failed_steps'].split('; ') if step and step != 'none']
        return all(RECOVERED_STEP_FACT in step for step in steps)

    @staticmethod
    def type_into(ident, target):
        return {'tool_calls': [loop.call(ident, 'browser_type', target=target, text='Kim', effect='mutate')]}

    def test_recovered_success_meets_the_goal(self):
        script = loop.Script(self.type_into('1', 'name'), self.type_into('2', 'name'),
                             loop.finish('f', '2', summary='이름 칸에 Kim을 입력했습니다.'))
        result = self.run_script(script, self.strict)
        self.assertEqual(result.outcome, 'succeeded')
        [facts] = self.seen
        self.assertEqual(facts['failed_steps'], f'browser_type: 입력할 칸을 찾지 못했습니다. {RECOVERED_STEP_FACT}')
        # The failed attempt stays in Evidence; nothing was run again to make it look clean.
        self.assertEqual([tool for tool, status, _d in self.events if status == 'failed'], ['browser_type'])
        self.assertEqual(result.report['unknown'], [])

    def test_another_target_of_the_same_action_is_not_a_recovery(self):
        """Review P1: typing into another field does not make up for the field that failed."""
        script = loop.Script(self.type_into('1', 'name'), self.type_into('2', 'email'),
                             loop.finish('f1', '2', summary='이름 칸에 Kim을 입력했습니다.'),
                             loop.finish('f2', '2', summary='이름 칸에 Kim을 입력했습니다.'))
        result = self.run_script(script, self.strict)
        self.assertEqual(result.outcome, 'partial')
        self.assertEqual(self.seen[0]['failed_steps'], 'browser_type: 입력할 칸을 찾지 못했습니다.')
        self.assertIn(GOAL_NOT_SHOWN, result.report['unknown'])

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
        result = self.run_script(script, self.strict, network=network, browser=False)
        self.assertEqual(result.outcome, 'partial')
        self.assertEqual(self.seen[0]['failed_steps'], 'weather: 날씨 연결이 필요합니다.')
        self.assertIn(['weather', '날씨 연결이 필요합니다.'], result.report['failed'])
        self.assertIn(GOAL_NOT_SHOWN, result.report['unknown'])

    def test_a_read_routed_around_is_marked_for_the_judgment(self):
        script = loop.Script({'tool_calls': [loop.call('1', 'weather', city='Seongnam', country='KR')]},
                             {'tool_calls': [loop.call('2', 'web_search', query='Seongnam weather tomorrow')]},
                             loop.finish('f', '2', summary='내일 성남은 22°C로 보입니다.'))
        result = self.run_script(script, lambda facts: ROUTED_AROUND_STEP_FACT in facts['failed_steps'], browser=False)
        self.assertEqual(result.outcome, 'succeeded')
        self.assertEqual(self.seen[0]['failed_steps'], f'weather: {loop.NOT_FOUND} {ROUTED_AROUND_STEP_FACT}')

    def test_a_claim_without_sufficient_observation_stays_partial_and_says_why(self):
        shown = lambda facts: self.strict(facts) and 'submitted' in facts['observations']
        script = loop.Script(self.type_into('1', 'name'), self.type_into('2', 'name'),
                             loop.finish('f1', '2', summary='이름을 넣고 제출까지 마쳤습니다.'),
                             loop.finish('f2', '2', summary='이름을 넣고 제출까지 마쳤습니다.'))
        result = self.run_script(script, shown)
        self.assertEqual(result.outcome, 'partial')
        # The step was recovered; what is missing is the observation, and the report says so.
        self.assertTrue(all(RECOVERED_STEP_FACT in facts['failed_steps'] for facts in self.seen))
        self.assertEqual(result.report['unknown'], [GOAL_NOT_SHOWN])


class DirectRouteServiceTests(unittest.TestCase):
    """``AgentService.run_one`` on the web and Telegram channels."""

    CHANNELS = loop.OwnerEntryPointTests.CHANNELS
    service = loop.OwnerEntryPointTests.service
    run_turn = loop.OwnerEntryPointTests.run_turn

    def test_a_read_routed_around_stays_named_when_the_work_falls_short(self):
        for route in self.CHANNELS:
            with self.subTest(route=route[0]):
                summary = '내일 성남에는 오후 3시에 비가 옵니다.'
                script = loop.Script({'tool_calls': [loop.call('1', 'weather', city='Seongnam', country='KR')]},
                                     {'tool_calls': [loop.call('2', 'web_search', query='Seongnam rain tomorrow')]},
                                     loop.finish_observed('f1', summary=summary), loop.finish_observed('f2', summary=summary))
                seen = []
                def judge(facts):
                    seen.append(facts['failed_steps'])
                    return False
                row, failed = self.run_turn('내일 성남 비 와?', script, loop.Network(), route, engine=loop.goal_engine(judge))
                self.assertEqual(row['status'], 'partial')
                self.assertTrue(seen and all(ROUTED_AROUND_STEP_FACT in item for item in seen))
                # Another path may not have served the failed read's purpose: the owner still hears of it.
                self.assertIn('한 단계는 끝내지 못했어요', row['owner_note'])
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
    """The service closeout over recorded tool events: status, judgment input and the owner's note agree.

    The events are recorded the way the direct route records them (a ``running`` record carrying the
    call's redacted arguments).  The CLI bridge records no arguments, so on that route a later call of
    the same action is never taken as the same invocation (``test_without_recorded_arguments_...``).
    """

    def attempt(self, *rows):
        def record(tools):
            for tool, status, detail in rows:
                tools.capabilities.record(tool, status, detail)
        self.engine.before = record

    def test_recovered_success_is_succeeded_without_naming_the_earlier_step(self):
        self.attempt(*typed('1', NAME, False), *typed('2', NAME, True))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[True])
        job, row = self.run_work('이름 칸에 Kim 넣어줘')
        self.assertEqual(row['status'], 'succeeded')
        self.assertIsNone(row['owner_note'])
        [goal] = self.asked_goals
        self.assertEqual(goal.facts['failed_steps'], f'browser_type: browser_type target_not_found {RECOVERED_STEP_FACT}')
        self.assertEqual(len(self.engine.turns), 1, 'nothing was run again after the effect')

    def test_a_shortfall_after_a_recovered_step_names_the_judgment_not_the_step(self):
        self.attempt(*typed('1', NAME, False), *typed('2', NAME, True))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[False])
        job, row = self.run_work('이름 칸에 Kim 넣고 제출해줘')
        self.assertEqual(row['status'], 'partial')
        # Before #14 the note counted the recovered step ('한 단계는 끝내지 못했어요') and the cause named it.
        self.assertIsNone(row['owner_note'])
        self.assertIsNone(row['error'])
        self.assertIn(GOAL_NOT_SHOWN, row['owner_cause'])
        self.assertNotIn('target_not_found', row['owner_cause'])

    def test_without_recorded_arguments_a_later_call_of_the_action_is_not_a_recovery(self):
        miss = failed('browser_type', 'target_not_found', declared='mutate')
        self.attempt(miss, succeeded('browser_type', {'state': 'typed'}))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[False])
        job, row = self.run_work('이름 칸에 Kim 넣고 제출해줘')
        self.assertEqual(row['status'], 'partial')
        [goal] = self.asked_goals
        self.assertEqual(goal.facts['failed_steps'], 'browser_type: browser_type target_not_found')
        # The step stays named, and the judgment's reason now follows it on the CLI route too.
        self.assertIn('한 단계는 끝내지 못했어요', row['owner_note'])
        self.assertIn(GOAL_NOT_SHOWN, row['owner_cause'])

    def test_an_unrecovered_state_change_keeps_the_work_partial_and_named(self):
        self.attempt(*typed('1', NAME, False), succeeded('browser_read', {'state': 'page'}))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[True])
        job, row = self.run_work('이름 칸에 Kim 넣어줘')
        # #752: a reached verdict never outranks a state change that did not happen.
        self.assertEqual(row['status'], 'partial')
        self.assertIn('한 단계는 끝내지 못했어요', row['owner_note'])
        self.assertIn('target_not_found', row['error'])
        [goal] = self.asked_goals
        self.assertNotIn(RECOVERED_STEP_FACT, goal.facts['failed_steps'])

    def test_an_unknown_external_effect_is_unknown_and_never_judged_or_replayed(self):
        self.attempt(running('browser_click', '1', {'target': 'submit', 'effect': 'mutate'}),
                     failed('browser_click', 'effect_unknown', effect='unknown', declared='mutate', scope='main', call_id='1'),
                     running('browser_click', '2', {'target': 'submit', 'effect': 'mutate'}),
                     succeeded('browser_click', {'state': 'clicked'}, scope='main', call_id='2'))
        self.script([orch.plan('codex', 'Submit it.')], goals=[True])
        job, row = self.run_work('제출해줘')
        self.assertEqual(row['status'], 'unknown')
        self.assertEqual(self.asked_goals, [], 'no judgment can call an unknown effect reached')
        self.assertEqual(len(self.engine.turns), 1, 'the possible effect is not replayed')


if __name__ == '__main__':
    unittest.main()
