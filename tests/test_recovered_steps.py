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
#: A click's recorded arguments carry no masking mark, so equal arguments are the same invocation.
SAVE = {'target': 'save', 'effect': 'mutate'}
CANCEL = {'target': 'cancel', 'effect': 'mutate'}


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


def pressed(call_id, arguments, ok):
    """One recorded browser_click call: its running record, then its terminal record (no arguments)."""
    terminal = (succeeded('browser_click', {'state': 'clicked'}, scope='main', call_id=call_id) if ok else
                failed('browser_click', 'target_not_found', declared='mutate', scope='main', call_id=call_id))
    return [running('browser_click', call_id, arguments), terminal]


class StepRecoveryTests(unittest.TestCase):
    """The one projection both routes read: which failures a later call made up for."""

    TYPE_MISS = failed('browser_type', 'target_not_found', declared='mutate')
    READ = succeeded('browser_read', {'state': 'page'})

    def marks(self, rows):
        return step_recovery(trail_steps(rows))

    def test_marked_only_for_the_same_invocation_or_a_call_that_changed_nothing(self):
        cases = [
            ('the same state change on the same target succeeded',
             pressed('1', SAVE, False) + pressed('2', SAVE, True), {0: RECOVERED}),
            # #630 review P1: redacted text records only its length; equal records are no evidence.
            ('typed text recorded alike by its length', typed('1', NAME, False) + typed('2', NAME, True), {}),
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
                steps = trail_steps(rows)
                for index in expected:
                    # Never more lenient than #752's deterministic guard.
                    self.assertFalse(state_change_short(steps[index:]))

    def test_the_judgment_text_marks_each_call_and_lists_open_ones_first(self):
        rows = [failed('weather'), failed('browser_click', 'effect_unknown', effect='unknown', declared='mutate'),
                self.TYPE_MISS, *pressed('1', SAVE, False), *pressed('2', SAVE, True)]
        self.assertEqual(failed_steps_text(trail_steps(rows)).split('; '),
                         ['browser_click: browser_click effect_unknown ' + UNKNOWN_EFFECT_STEP_FACT,
                          'browser_type: browser_type target_not_found',
                          'weather: weather tool_failed ' + ROUTED_AROUND_STEP_FACT,
                          'browser_click: browser_click target_not_found ' + RECOVERED_STEP_FACT])
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
        press = ('browser_click', 'browser_click target_not_found')
        weather = ('weather', 'weather tool_failed')
        sign_in = ('browser_open', 'sign-in needed')
        for label, rows, refusals, expected in (
                ('the same target succeeded later', pressed('1', SAVE, False) + pressed('2', SAVE, True),
                 [press, press, sign_in], [sign_in]),
                ('typed text recorded alike by its length', typed('1', NAME, False) + typed('2', NAME, True),
                 [miss, miss, sign_in], [miss, miss, sign_in]),
                ('another target succeeded later', typed('1', NAME, False) + typed('2', EMAIL, True),
                 [miss, miss, sign_in], [miss, miss, sign_in]),
                ('only a read after it', [self.TYPE_MISS, self.READ], [miss, sign_in], [miss, sign_in]),
                # A call merely routed around may be why the Work fell short: it stays named.
                ('a read routed around', [failed('weather'), succeeded('web_search')], [weather], [weather]),
                ('the same failure once recovered and once not',
                 pressed('1', SAVE, False) + pressed('2', SAVE, True) + pressed('3', CANCEL, False),
                 [press, press, sign_in], [press, press, sign_in])):
            with self.subTest(label):
                job = store.enqueue('x', f'k-{label}')
                with store.db() as db:
                    for tool, status, detail in rows:
                        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,1)',
                                   (job, tool, status, detail))
                # The record wrapper and the CLI outcome both name the failure; a sign-in wait is no tool failure.
                self.assertEqual(service.unrecovered_refusals(job, None, refusals), expected)
        # The CLI outcome rule itself is unchanged: its own events decide the status.
        self.assertEqual(outcome_from_events(pressed('1', SAVE, False) + pressed('2', SAVE, True))[0], 'partial')


class SuccessFloorTests(unittest.TestCase):
    """#630: ``state_change_short`` reads each call's own record and recovers only the same invocation."""

    READ = succeeded('browser_read', {'state': 'page'})
    NAVIGATED = {'posted': False, 'approved': False}

    def short(self, rows):
        return state_change_short(trail_steps(rows))

    def test_only_the_same_invocation_recovers_a_failed_write(self):
        self.assertFalse(self.short(pressed('1', SAVE, False) + pressed('2', SAVE, True)))
        self.assertTrue(self.short(pressed('1', SAVE, False) + pressed('2', CANCEL, True)), 'another target')
        self.assertTrue(self.short(typed('1', NAME, False) + typed('2', EMAIL, True)), 'another field')
        # #630 review P1: two different values of one length record alike; only the same recorded call counts.
        self.assertTrue(self.short(typed('1', NAME, False) + typed('2', NAME, True)), 'typed text of one length')
        self.assertTrue(self.short([failed('api_request', 'api_http_error', declared='mutate', arguments={'body': '[가림: 12자]'}),
                                    succeeded('api_request', {'status': 200}, arguments={'body': '[가림: 12자]'})]),
                        'an API body of one length')
        self.assertFalse(self.short([failed('browser_type', 'transient_failure', declared='mutate', scope='main', call_id='5'),
                                     succeeded('browser_type', {'state': 'typed'}, scope='main', call_id='5')]),
                         'the same recorded call')
        self.assertTrue(self.short([failed('browser_type', 'target_not_found', declared='mutate'),
                                    succeeded('browser_type', {'state': 'typed'})]), 'no recorded arguments')

    def test_changed_unknown_withheld_and_incomplete_calls_still_hold(self):
        held = {
            'effect none on a failed write': failed('save_note', 'tool_failed'),
            'a navigate-declared click with no observation': failed('browser_click', 'press_unconfirmed', declared='navigate'),
            'a navigation that submitted': failed('browser_click', 'press_unconfirmed', declared='navigate',
                                                  observed_step={'posted': True, 'approved': False}),
            'a code run that sent a change': failed('code_run', 'tool_failed', evidence={'effects': [{'method': 'POST'}]}),
            'a browser_open declared to change something': failed('browser_open', 'tool_failed', declared='mutate'),
            'a call without evidence': ('browser_click', 'failed', json.dumps({'host_action': 'browser_click'})),
            'withheld': succeeded('settings_change', {'draft_id': 'd9', 'applied': False, 'requires_owner_confirmation': True}),
            'withheld though declared a read': succeeded('browser_click', {'refused_because': 'owner-approval',
                                                                           'observed_step': self.NAVIGATED}, declared_effect='read'),
            'incomplete': succeeded('save_note', {'qualifiers': ['partial']}),
        }
        for label, row in held.items():
            with self.subTest(label):
                self.assertTrue(self.short([row, self.READ]))
                self.assertTrue(self.short([row, self.READ, succeeded(row[0], {'state': 'done'})]),
                                'a later success of the action without the same invocation')

    def test_a_later_success_that_wrote_nothing_does_not_recover_a_failed_write(self):
        """#630 review P1: a write that returned normally yet says it wrote nothing (``saved: false`` and
        nothing saved before) did not do what it was asked: it is incomplete, never a recovery."""
        from personal_agent.agent_runtime import evidence_summary
        args = {'title': 'Notes', 'content': '[가림: 20자]'}
        miss = failed('save_result', 'tool_failed', scope='main', call_id='9', arguments=args)
        for label, result, short in (
                ('the earlier result is detached', {'saved': False, 'already_saved': False, 'id': 'r1', 'path': 'a.md'}, True),
                ('the result was written', {'saved': True, 'id': 'r1', 'path': 'a.md'}, False),
                ('one result per Work, already written', {'saved': False, 'already_saved': True, 'id': 'r1', 'path': 'a.md'}, False)):
            with self.subTest(label):
                later = ('save_result', 'succeeded', json.dumps({'host_action': 'save_result', 'scope': 'main', 'call_id': '9',
                                                                 'evidence': evidence_summary('save_result', result)}))
                self.assertEqual(trail_steps([later])[0][2], 'incomplete' if short else 'succeeded')
                self.assertEqual(self.short([miss, later]), short)

    def test_an_unknown_effect_holds_even_when_the_same_call_succeeded_later(self):
        rows = [running('browser_click', '3', {'target': 'send', 'effect': 'mutate'}),
                failed('browser_click', 'effect_unknown', effect='unknown', declared='mutate', scope='main', call_id='3'),
                succeeded('browser_click', {'state': 'clicked'}, scope='main', call_id='3')]
        self.assertTrue(self.short(rows))

    def test_calls_still_records_show_changed_nothing_do_not_hold(self):
        cleared = {
            'a read-declared click refused by a guard': failed('browser_click', 'approval_required', declared='read', ran=False),
            'an owner-local write': failed('save_result', 'tool_failed', ran=False),
            'refused by the service': failed('api_request', 'api_http_error', declared='mutate', executed=False),
            'refused before it ran': failed('browser_click', 'approval_required', declared='mutate', ran=False),
            'a navigation click Still observed submitting nothing': failed('browser_click', 'press_unconfirmed', declared='navigate',
                                                                           observed_step=self.NAVIGATED),
            'a read-declared step whose target Still could not act on': failed('browser_click', 'target_unavailable', declared='read'),
            'a read Still made by its method': failed('api_request', 'api_http_error', declared='read'),
            'a failed effect-free read': failed('weather'),
            'a page load': failed('browser_open', 'tool_failed', declared='navigate'),
            'an incomplete read': succeeded('web_search', {'qualifiers': ['partial']}),
            'a delegation': failed('delegate_agent', 'tool_failed'),
        }
        for label, row in cleared.items():
            with self.subTest(label):
                self.assertFalse(self.short([row, self.READ]))


class FakeBrowserCapabilities(Capabilities):
    """``Capabilities`` with a fake browser: the first ``misses`` typing or click calls miss, then they run."""

    def __init__(self, *args, misses=1, **kwargs):
        super().__init__(*args, browser=lambda: object(), **kwargs)
        self.misses, self.tries = misses, 0

    def execute(self, name, args):
        if name not in ('browser_type', 'browser_click'):
            return super().execute(name, args)
        self.tries += 1
        if self.tries <= self.misses:
            raise ToolError('입력할 칸을 찾지 못했습니다.' if name == 'browser_type' else '누를 항목을 찾지 못했습니다.', 'target_not_found')
        return {'state': 'typed' if name == 'browser_type' else 'clicked', 'target': args.get('target')}


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
    def type_into(ident, target, text='Kim'):
        return {'tool_calls': [loop.call(ident, 'browser_type', target=target, text=text, effect='mutate')]}

    @staticmethod
    def press(ident, target):
        return {'tool_calls': [loop.call(ident, 'browser_click', target=target, effect='mutate')]}

    def test_recovered_success_meets_the_goal(self):
        script = loop.Script(self.press('1', 'save'), self.press('2', 'save'),
                             loop.finish('f', '2', summary='저장을 눌렀습니다.'))
        result = self.run_script(script, self.strict)
        self.assertEqual(result.outcome, 'succeeded')
        [facts] = self.seen
        self.assertEqual(facts['failed_steps'], f'browser_click: 누를 항목을 찾지 못했습니다. {RECOVERED_STEP_FACT}')
        # The failed attempt stays in Evidence; nothing was run again to make it look clean.
        self.assertEqual([tool for tool, status, _d in self.events if status == 'failed'], ['browser_click'])
        self.assertEqual(result.report['unknown'], [])

    def test_typed_text_recorded_alike_by_its_length_is_not_a_recovery(self):
        """#630 review P1: 'Kim' and 'Lee' both record as a three-character placeholder; a judged yes
        does not store the Work as succeeded on that."""
        script = loop.Script(self.type_into('1', 'name', 'Kim'), self.type_into('2', 'name', 'Lee'),
                             loop.finish('f', '2', summary='이름 칸에 Kim을 입력했습니다.'))
        result = self.run_script(script, lambda facts: True)
        self.assertEqual(len(self.seen), 1, 'the judgment was asked and said yes')
        self.assertEqual(result.outcome, 'partial')
        self.assertNotIn(RECOVERED_STEP_FACT, self.seen[0]['failed_steps'])

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

    def test_a_judged_yes_does_not_lift_the_floor_over_another_target(self):
        """#630: the direct route's conclusion applies the CLI route's floor: typing into another field
        does not make up for the field that failed, whatever the judgment says."""
        script = loop.Script(self.type_into('1', 'name'), self.type_into('2', 'email'),
                             loop.finish('f', '2', summary='이름 칸에 Kim을 입력했습니다.'))
        result = self.run_script(script, lambda facts: True)
        self.assertEqual(len(self.seen), 1, 'the judgment was asked and said yes')
        self.assertEqual(result.outcome, 'partial')
        self.assertIn(['browser_type', '입력할 칸을 찾지 못했습니다.'], result.report['failed'])
        # The same target succeeding later still concludes on the judgment.
        script = loop.Script(self.press('3', 'save'), self.press('4', 'save'), loop.finish('g', '4', summary='저장을 눌렀습니다.'))
        self.assertEqual(self.run_script(script, lambda facts: True).outcome, 'succeeded')

    def test_a_judged_yes_stands_over_a_navigation_still_observed_submitting_nothing(self):
        """#630: a click declared ``navigate`` whose failure carries Still's observation that it posted
        nothing and spent no approval changed nothing, so it does not hold a reached goal down."""
        class Navigating(FakeBrowserCapabilities):
            posted = False

            def execute(self, name, args):
                if name == 'browser_click':
                    exc = ToolError('누른 뒤 페이지가 확인되지 않았습니다.', 'press_unconfirmed')
                    exc.observed_step = {'posted': self.posted, 'approved': False}
                    raise exc
                return super().execute(name, args)
        for posted, expected in ((False, 'succeeded'), (True, 'partial')):
            with self.subTest(posted=posted):
                script = loop.Script({'tool_calls': [loop.call('1', 'browser_click', target='more', effect='navigate')]},
                                     self.type_into('2', 'name'),
                                     loop.finish('f', '2', summary='이름 칸에 Kim을 입력했습니다.'))
                caps = Navigating(self.store, ModelAdapter(script), loop.CFG, '', 'job', lambda *event: self.events.append(event),
                                  network=loop.Network(), judgments=loop.judgments(lambda facts: True), misses=0)
                caps.posted = posted
                result = run_agent(caps.adapter, loop.CFG, '', self.REQUEST, '', caps, caps.record)
                self.assertEqual(result.outcome, expected)

    def test_a_write_that_returned_without_writing_is_not_succeeded(self):
        """#630 review P1 (direct-result shape): a save that says it wrote nothing and nothing was saved
        before is incomplete, so a judged yes does not store the Work as succeeded."""
        class Saving(Capabilities):
            answer = {}

            def execute(self, name, args):
                if name == 'save_result':return dict(self.answer)
                return super().execute(name, args)
        for answer, expected in (({'saved': False, 'already_saved': False, 'id': 'r1', 'path': 'a.md'}, 'partial'),
                                 ({'saved': True, 'id': 'r1', 'path': 'a.md'}, 'succeeded')):
            with self.subTest(answer=answer):
                script = loop.Script({'tool_calls': [loop.call('1', 'save_result', title='Notes', content='Some notes.')]},
                                     loop.finish('f', '1', summary='결과를 저장했습니다.'))
                caps = Saving(self.store, ModelAdapter(script), loop.CFG, '', 'job', lambda *event: self.events.append(event),
                              network=loop.Network(), judgments=loop.judgments(lambda facts: True))
                caps.answer = answer
                result = run_agent(caps.adapter, loop.CFG, '', self.REQUEST, '', caps, caps.record)
                self.assertEqual(result.outcome, expected)

    def test_a_claim_without_sufficient_observation_stays_partial_and_says_why(self):
        shown = lambda facts: self.strict(facts) and 'submitted' in facts['observations']
        script = loop.Script(self.press('1', 'save'), self.press('2', 'save'),
                             loop.finish('f1', '2', summary='저장하고 제출까지 마쳤습니다.'),
                             loop.finish('f2', '2', summary='저장하고 제출까지 마쳤습니다.'))
        result = self.run_script(script, shown)
        self.assertEqual(result.outcome, 'partial')
        # The step was recovered; what is missing is the observation, and the report says so.
        self.assertTrue(all(RECOVERED_STEP_FACT in facts['failed_steps'] for facts in self.seen))
        self.assertEqual(result.report['unknown'], [GOAL_NOT_SHOWN])

    def test_reached_completion_uses_the_failed_calls_effect_class(self):
        """#656: the same completion floor applies to plain replies and finish claims."""
        from personal_agent.agent_runtime import record_effect

        class Browser(Capabilities):
            def execute(self, name, args):
                if name == 'browser_read':
                    raise ToolError('No page open.', 'no_page')
                if name == 'browser_type':
                    exc = ToolError('Element is not typable.', 'target_unavailable', ran=False if self.effect == 'none' else None)
                    if self.effect == 'unknown':exc.effect = 'unknown'
                    raise exc
                return super().execute(name, args)

        for effect in ('none', 'changed', 'unknown'):
            for reached in (True, False):
                for explicit in (True, False):
                    with self.subTest(effect=effect, reached=reached, explicit=explicit):
                        events = []
                        end = loop.finish('f', '3', summary='Found it.') if explicit else {'content': 'Found it.'}
                        script = loop.Script(
                            {'tool_calls': [loop.call('1', 'browser_read')]},
                            self.type_into('2', 'name'),
                            {'tool_calls': [loop.call('3', 'web_search', query='example')]}, end,
                            {'content': 'Found it.'})
                        caps = Browser(self.store, ModelAdapter(script), loop.CFG, '', 'job',
                                       lambda *event: events.append(event), browser=lambda: object(),
                                       network=loop.Network(), judgments=loop.judgments(reached))
                        caps.effect = effect
                        result = run_agent(caps.adapter, loop.CFG, '', self.REQUEST, '', caps, caps.record)
                        self.assertEqual(result.outcome, 'succeeded' if reached and effect == 'none' else 'partial')
                        failures = [(tool, json.loads(detail)) for tool, status, detail in events if status == 'failed']
                        self.assertEqual([tool for tool, _ in failures], ['browser_read', 'browser_type'])
                        self.assertEqual([record_effect(tool, detail, 'failed') for tool, detail in failures], ['none', effect])
                        self.assertEqual([tool for tool, _reason in result.report['failed']], ['browser_read', 'browser_type'])


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
                self.assertIn('일부 단계는 끝내지 못했어요', row['owner_note'])
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

    def test_reached_effect_free_failures_succeed_and_keep_the_evidence(self):
        rows = [failed('browser_read', 'no_page'),
                failed('browser_type', 'target_unavailable', declared='mutate', ran=False,
                       error='Element is not typable.'),
                succeeded('web_search')]
        for reached, expected in ((True, 'succeeded'), (False, 'partial')):
            with self.subTest(reached=reached):
                self.attempt(*rows)
                self.script([orch.plan('codex', 'Look it up.')], goals=[reached])
                job, row = self.run_work('Find the information.')
                self.assertEqual(row['status'], expected)
                if reached:self.assertIsNone(row['owner_note'])
                with self.store.db() as db:
                    kept = db.execute("SELECT tool,detail FROM tool_events WHERE job_id=? AND status='failed' ORDER BY id", (job,)).fetchall()
                self.assertEqual([item['tool'] for item in kept], ['browser_read', 'browser_type'])
                self.assertEqual([json.loads(item['detail'])['code'] for item in kept], ['no_page', 'target_unavailable'])

    def test_recovered_success_is_succeeded_without_naming_the_earlier_step(self):
        self.attempt(*pressed('1', SAVE, False), *pressed('2', SAVE, True))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[True])
        job, row = self.run_work('이름 칸에 Kim 넣어줘')
        self.assertEqual(row['status'], 'succeeded')
        self.assertIsNone(row['owner_note'])
        [goal] = self.asked_goals
        self.assertEqual(goal.facts['failed_steps'], f'browser_click: browser_click target_not_found {RECOVERED_STEP_FACT}')
        self.assertEqual(len(self.engine.turns), 1, 'nothing was run again after the effect')

    def test_a_shortfall_after_a_recovered_step_names_the_judgment_not_the_step(self):
        self.attempt(*pressed('1', SAVE, False), *pressed('2', SAVE, True))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[False])
        job, row = self.run_work('이름 칸에 Kim 넣고 제출해줘')
        self.assertEqual(row['status'], 'partial')
        # Before #14 the note counted the recovered step ('일부 단계는 끝내지 못했어요') and the cause named it.
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
        self.assertIn('일부 단계는 끝내지 못했어요', row['owner_note'])
        self.assertIn(GOAL_NOT_SHOWN, row['owner_cause'])

    def test_an_unrecovered_state_change_keeps_the_work_partial_and_named(self):
        self.attempt(*typed('1', NAME, False), succeeded('browser_read', {'state': 'page'}))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[True])
        job, row = self.run_work('이름 칸에 Kim 넣어줘')
        # #752: a reached verdict never outranks a state change that did not happen.
        self.assertEqual(row['status'], 'partial')
        self.assertIn('일부 단계는 끝내지 못했어요', row['owner_note'])
        self.assertIn('target_not_found', row['error'])
        [goal] = self.asked_goals
        self.assertNotIn(RECOVERED_STEP_FACT, goal.facts['failed_steps'])

    def test_another_target_of_the_same_action_does_not_lift_the_floor(self):
        """#630: a reached verdict does not upgrade a Work whose failed write only another target's success follows."""
        self.attempt(*typed('1', NAME, False), *typed('2', EMAIL, True))
        self.script([orch.plan('codex', 'Fill it in.')], goals=[True])
        job, row = self.run_work('이름 칸에 Kim 넣어줘')
        self.assertEqual(row['status'], 'partial')
        self.assertTrue(self.asked_goals, 'the judgment was asked')

    def test_a_navigation_still_observed_submitting_nothing_does_not_hold_a_reached_goal(self):
        """#630: a refused navigation-only click with no effect keeps no complete answer partial; a click
        whose failure Still did not observe, or saw submit, still does."""
        for observed, expected in (({'posted': False, 'approved': False}, 'succeeded'),
                                   ({'posted': True, 'approved': False}, 'partial'), (None, 'partial')):
            with self.subTest(observed=observed):
                extra = {'observed_step': observed} if observed else {}
                self.attempt(failed('browser_click', 'press_unconfirmed', declared='navigate', **extra),
                             succeeded('browser_read', {'state': 'page'}))
                self.script([orch.plan('codex', 'Look it up.')], goals=[True])
                job, row = self.run_work('그 페이지 내용 알려줘')
                self.assertEqual(row['status'], expected)

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
