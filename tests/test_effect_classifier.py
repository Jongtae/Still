"""#1445 part 2: one effect classifier for every consumer.

A retry, a re-delegation, a resumption and a park read the same recorded calls; each used to
keep its own list of what changes state, and they disagreed (a code_run, an unrun click, a
refused API call and a refused settings draft were each misjudged by one of them).  These
tests pin one corpus of recorded call pairs to one class and check every consumer reads it
the same way.
"""
import unittest
from types import SimpleNamespace

from personal_agent.agent_runtime import (EFFECT_CHANGED, EFFECT_DRAFT, EFFECT_NONE, EFFECT_UNKNOWN, ToolError, call_effects,
                                          not_run, record_effect)
from personal_agent.quickstart_service import AgentService


def _pair(tool, terminal, status, *, host_action=None, call_id='c1'):
    base = {'scope': 'w1', 'call_id': call_id}
    if host_action:
        base['host_action'] = host_action
    running = {**base, **{key: value for key, value in terminal.items() if key == 'declared_effect'}}
    return [(tool, 'running', running), (tool, status, {**base, **terminal})]


#: name -> (rows, expected class)
CORPUS = {
    'code_run that read': (_pair('code_run', {'evidence': {'exit_code': 0}}, 'succeeded'), EFFECT_NONE),
    'click refused before running': (_pair('browser_click', {'declared_effect': 'read', 'effect': 'none',
                                                             'code': 'target_not_found'}, 'failed'), EFFECT_NONE),
    'click that ran': (_pair('browser_click', {'declared_effect': 'read'}, 'failed'), EFFECT_CHANGED),
    # #1472 (live): several elements matched, so nothing was pressed; it withheld an approved order.
    'click on an ambiguous target': (_pair('browser_click', {'declared_effect': 'navigate', 'effect': 'none',
                                                             'code': 'ambiguous_target'}, 'failed'), EFFECT_NONE),
    'api request refused before dispatch': (_pair('api_request', {'declared_effect': 'mutate',
                                                                  'code': 'approval_required'}, 'failed'), EFFECT_NONE),
    'api request that failed after dispatch': (_pair('api_request', {'declared_effect': 'mutate',
                                                                     'code': 'api_http_error'}, 'failed'), EFFECT_CHANGED),
    'api GET': (_pair('api_request', {'declared_effect': 'read'}, 'succeeded'), EFFECT_NONE),
    'settings draft waiting': (_pair('settings_change', {'evidence': {'applied': False,
                                                                      'requires_owner_confirmation': True}},
                                     'succeeded'), EFFECT_DRAFT),
    'settings draft refused and recorded failed': (_pair('settings_change', {'evidence': {
        'applied': False, 'requires_owner_confirmation': True}}, 'failed'), EFFECT_DRAFT),
    'settings refused with no effect': (_pair('settings_change', {'code': 'settings_refused', 'effect': 'none'},
                                              'failed'), EFFECT_NONE),
    'browser_open declared mutate': (_pair('browser_open', {'declared_effect': 'mutate'}, 'succeeded'), EFFECT_CHANGED),
    'browser_open declared navigate': (_pair('browser_open', {'declared_effect': 'navigate'}, 'succeeded'), EFFECT_NONE),
    'unknown package tool': (_pair('pkg_tool', {}, 'succeeded', host_action='acme.place_order'), EFFECT_CHANGED),
    'outcome unknown': (_pair('api_request', {'declared_effect': 'read', 'effect': 'unknown'}, 'failed'),
                        EFFECT_UNKNOWN),
    'web search': (_pair('web_search', {}, 'succeeded'), EFFECT_NONE),
    # #103: a press that reached the page with no click on its target: unknown whatever was declared,
    # even with no form post observed (which makes a confirmed ``read`` click a no-change).
    'click the page covered during the press': (_pair('browser_click', {
        'declared_effect': 'read', 'effect': 'unknown', 'code': 'effect_unknown',
        'observed_step': {'posted': False, 'approved': False}}, 'failed'), EFFECT_UNKNOWN),
    # #103: a press the early guard stopped before any page press handler ran is refused like an
    # unrun target: nothing for a ``read`` step; a ``mutate`` one keeps the conservative class.
    'click stopped before any handler': (_pair('browser_click', {'declared_effect': 'read', 'effect': 'none',
                                                                 'code': 'target_unavailable'}, 'failed'), EFFECT_NONE),
    'mutate click stopped before any handler': (_pair('browser_click', {'declared_effect': 'mutate', 'effect': 'none',
                                                                        'code': 'target_unavailable'}, 'failed'),
                                                EFFECT_CHANGED),
    # #1484 (live, Work 51cd331b): Still refused a repeated click before it ran.
    'click refused as a repeated path': (_pair('browser_click', {'declared_effect': 'mutate', 'effect': 'none',
                                                                'code': 'repeat_path', 'ran': False}, 'failed'), EFFECT_NONE),
    'api request refused by the attempt budget': (_pair('api_request', {'declared_effect': 'mutate', 'code': 'attempt_budget',
                                                                        'ran': False}, 'failed'), EFFECT_NONE),
    # Records without the mark keep the conservative class; a mark on a success changes nothing.
    'repeated click recorded without the mark': (_pair('browser_click', {'declared_effect': 'mutate', 'effect': 'none',
                                                                        'code': 'repeat_path'}, 'failed'), EFFECT_CHANGED),
    'package tool success marked not run': (_pair('pkg_tool', {'ran': False}, 'succeeded', host_action='acme.place_order'),
                                            EFFECT_CHANGED),
    # #1496 (live, Work 3cc7b7e4): a navigation click timed out; the worker saw it start no form post.
    'navigation click timed out, nothing posted': (_pair('browser_click', {
        'declared_effect': 'navigate', 'effect': 'none', 'code': 'browser_timeout',
        'observed_step': {'posted': False, 'approved': False}}, 'failed'), EFFECT_NONE),
    'navigation click timed out after a form post': (_pair('browser_click', {
        'declared_effect': 'navigate', 'effect': 'none', 'code': 'browser_timeout',
        'observed_step': {'posted': True, 'approved': False}}, 'failed'), EFFECT_CHANGED),
    'navigation click timed out, unobserved': (_pair('browser_click', {
        'declared_effect': 'navigate', 'effect': 'none', 'code': 'browser_timeout'}, 'failed'), EFFECT_CHANGED),
    'mutate typing timed out, nothing posted': (_pair('browser_type', {
        'declared_effect': 'mutate', 'effect': 'none', 'code': 'browser_timeout',
        'observed_step': {'posted': False, 'approved': False}}, 'failed'), EFFECT_CHANGED),
    'approved click timed out': (_pair('browser_click', {
        'declared_effect': 'navigate', 'effect': 'none', 'code': 'browser_timeout',
        'observed_step': {'posted': False, 'approved': True}}, 'failed'), EFFECT_CHANGED),
}


def _events(rows):
    return [{'tool': tool, 'status': status, 'trace': detail} for tool, status, detail in rows]


def _names(rows):
    owner = SimpleNamespace(LOCAL_NON_TOOL_EVENTS=AgentService.LOCAL_NON_TOOL_EVENTS)
    return AgentService.effect_call_names(owner, _events(rows))


class OneClassifier(unittest.TestCase):
    def test_every_recorded_call_has_one_class(self):
        for name, (rows, expected) in CORPUS.items():
            with self.subTest(name):
                self.assertEqual([effect for _action, effect, _status in call_effects(rows)], [expected])

    def test_the_terminal_record_decides_and_a_running_one_is_classified_as_it_runs(self):
        rows = CORPUS['click refused before running'][0]
        self.assertEqual(len(call_effects(rows)), 1, 'a running and a terminal record are one call')
        self.assertEqual(call_effects(rows[:1]), [('browser_click', EFFECT_NONE, 'running')],
                         'a read click still running is classified on its declaration')
        self.assertEqual(call_effects(CORPUS['unknown package tool'][0][:1])[0][1], EFFECT_CHANGED)

    def test_retry_names_and_single_event_reading_agree_with_the_class(self):
        for name, (rows, expected) in CORPUS.items():
            with self.subTest(name):
                action = rows[-1][2].get('host_action') or rows[-1][0]
                changed = expected != EFFECT_NONE
                self.assertEqual(action in _names(rows), changed)
                self.assertEqual(bool(AgentService.effect_calls(_events(rows)[-1])), changed)

    def test_calls_without_an_id_are_read_one_by_one(self):
        rows = [('web_search', 'succeeded', {}), ('save_note', 'succeeded', {})]
        self.assertEqual([effect for _a, effect, _s in call_effects(rows)], [EFFECT_NONE, EFFECT_CHANGED])

    def test_an_unreadable_detail_is_a_change(self):
        self.assertEqual(record_effect('mystery', None, 'succeeded'), EFFECT_CHANGED)


class NotRunMark(unittest.TestCase):
    """#1484: Still marks only its own refusals before a tool ran, and the mark survives the relay."""

    def test_the_relay_carries_the_mark_and_never_invents_it(self):
        from personal_agent.bounded_execution import ExecutionError
        from personal_agent.cli_browser_relay import _error_payload, relay_exception
        refused = relay_exception(_error_payload(ToolError('x', 'repeat_path', ran=False)))
        self.assertEqual((refused.code, refused.ran), ('repeat_path', False))
        self.assertIs(relay_exception(_error_payload(not_run(ExecutionError('bad')))).ran, False)
        self.assertIs(relay_exception(_error_payload(not_run(ValueError('refused')))).ran, False)
        # A failure raised once the tool started carries no mark on either side.
        self.assertNotIn('ran', _error_payload(ToolError('x', 'tool_failed')))
        self.assertIsNone(getattr(relay_exception(_error_payload(ToolError('x', 'tool_failed'))), 'ran', None))
        self.assertIsNone(getattr(relay_exception({'kind': 'transient', 'message': ''}), 'ran', None))

    def test_the_relay_carries_an_unknown_effect_and_never_invents_it(self):
        # #103: a press of unknown effect stays unknown on the bridge's record, and is not retried.
        from personal_agent.agent_runtime import _error_observation
        from personal_agent.cli_browser_relay import _error_payload, relay_exception
        pressed = ToolError('x', 'press_unconfirmed')
        pressed.effect, pressed.observed_step = 'unknown', {'posted': False, 'approved': False}
        back = relay_exception(_error_payload(pressed))
        self.assertEqual((back.code, back.effect), ('press_unconfirmed', 'unknown'))
        seen = _error_observation(back, True, 'browser_click')
        self.assertEqual({key: seen[key] for key in ('code', 'retry', 'effect', 'observed_step')},
                         {'code': 'effect_unknown', 'retry': 'never', 'effect': 'unknown',
                          'observed_step': {'posted': False, 'approved': False}})
        self.assertNotIn('effect', _error_payload(ToolError('x', 'target_unavailable')))
        self.assertIsNone(getattr(relay_exception(_error_payload(ToolError('x', 'target_unavailable'))), 'effect', None))

    def test_the_relay_carries_a_timed_out_steps_observation(self):
        # #1496: what the browser worker saw of a step that timed out reaches the bridge's record.
        from personal_agent.cli_browser_relay import _error_payload, relay_exception
        timeout = ToolError('x', 'browser_timeout')
        timeout.observed_step = {'posted': False, 'approved': False}
        back = relay_exception(_error_payload(timeout))
        self.assertEqual((back.code, back.observed_step), ('browser_timeout', {'posted': False, 'approved': False}))
        self.assertIsNone(getattr(relay_exception({'kind': 'tool', 'message': 'x', 'code': 'browser_timeout',
                                                    'observed_step': {'posted': 'no'}}), 'observed_step', None))


if __name__ == '__main__':
    unittest.main()
