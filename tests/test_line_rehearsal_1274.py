"""#1274: a line in rehearsal records its external changes instead of sending them; only the owner takes it live.

Owner decisions 2026-11-28: a line-level setting; only the owner switches a line to live (the AI may only
propose it); a rehearsed change is counted apart from sent ones, and a line in rehearsal says so first.
Real local code: the line store and its tool handler, the condition-check dispatch, the connector decision,
the browser step gate and the line's account, with fixture transports and a fixture decision engine.
"""
import json

import pytest

from personal_agent import entrusted_lines as el
from personal_agent.agent_runtime import EFFECT_REHEARSAL, ToolError, connector_change_refused, line_rehearsal
from personal_agent.browser_session import BrowserSession
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from personal_agent.messages import msg

from test_condition_check_orders_1194 import POST, run_due, switch
from test_condition_checks_972 import case, current, wake_job  # noqa: F401 - fixture
from test_connector_refused_1271 import ACTION, call, connect, permissions
from test_entrusted_lines_798 import _LineCase
from test_line_effects_1252 import line_with_check
from test_refused_effects_1250 import woken


def rehearse(c, line, on=True):
    brief = c.service.lines.current(line)
    c.service.lines.write(line, c.owner, brief['version'], 'revised', lambda b: {**b, 'rehearsal': on})


def test_a_check_in_rehearsal_sends_nothing_and_counts_as_rehearsed(case):
    line, row = line_with_check(case)
    rehearse(case, line)
    switch(case, True)
    sent = []
    assert not run_due(case, sent)['allow'] and sent == [], 'nothing reaches the service'
    effects = case.service.preparations_service.line_effects(line)
    assert (effects['rehearsed'], effects['sent'], effects['refused'], effects['unconfirmed']) == (1, 0, 0, 0)
    text = case.service.preparations_service.line_effects_text(effects, account=True)
    assert msg('lines.effects_rehearsed', count=1) in text


def test_the_same_check_sends_once_the_line_is_live(case):
    line, row = line_with_check(case)
    rehearse(case, line)
    rehearse(case, line, on=False)
    switch(case, True)
    sent = []
    assert run_due(case, sent)['allow'] and sent == [POST]
    assert case.service.preparations_service.line_effects(line)['rehearsed'] == 0


def test_a_connector_change_in_rehearsal_is_rehearsed_not_refused(case):
    line, row = line_with_check(case)
    rehearse(case, line)
    connect(case.store, case.service, operation='mutate')
    job = woken(case, row)
    assert call(case.service, job)['behavior'] == 'deny'
    (status, evidence), = permissions(case.store, job)
    assert status == 'denied' and evidence['refused'] == 'rehearsal' and evidence['rehearsed'] == 'mutate'
    assert not connector_change_refused(case.store, job['id']), 'a rehearsal does not hold the Work as refused'
    effects = case.service.preparations_service.line_effects(line)
    assert (effects['rehearsed'], effects['refused']) == (1, 0)


def test_rehearsal_is_read_for_the_lines_works_and_checks_only(case):
    line, row = line_with_check(case)
    rehearse(case, line)
    job = woken(case, row)
    with case.store.db() as db:
        assert line_rehearsal(db, job['id'])
        assert line_rehearsal(db, 'check:' + row['id'])
        assert not line_rehearsal(db, case.owner), "the owner's own Work is not a run of the line"
    rehearse(case, line, on=False)
    with case.store.db() as db:
        assert not line_rehearsal(db, job['id'])


@pytest.mark.parametrize('effect,refused', [('mutate', True), ('payment', True), ('read', False), ('navigate', False)])
def test_a_browser_step_declared_to_change_is_not_pressed_in_rehearsal(effect, refused):
    session = BrowserSession.__new__(BrowserSession)
    session.rehearsal = lambda: True
    if refused:
        with pytest.raises(ToolError) as held:
            session._refuse_in_rehearsal(effect)
        assert held.value.code == EFFECT_REHEARSAL
    else:
        session._refuse_in_rehearsal(effect)
    session.rehearsal = lambda: False
    session._refuse_in_rehearsal(effect)


def test_a_brief_without_the_field_reads_as_live():
    brief = el.validate({'task': 'Keep a watch', 'report_promise': 'Report at the end'})
    assert brief['rehearsal'] is False
    with pytest.raises(el.LineRefusal):
        el.validate({'task': 'Keep a watch', 'report_promise': 'Report at the end', 'rehearsal': 'yes'})


class RehearsalAuthorityTests(_LineCase):
    def judged(self, answer):
        seen = []

        def judge(context, proposition):
            if context.purpose == 'explicit-line-change':
                seen.append(context.facts['proposed_change'])
                return BinaryDecision(OUTCOME_DECIDED, answer, fixture_confidence())
            if context.purpose in ('goal-reached', 'explicit-preparation-request'):
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
        return seen

    def open_rehearsal(self):
        result = self.line_call('먼저 리허설로 돌려 봐', action='open', task='관심 목록 확인', report_promise='끝나면 정리',
                                rehearsal=True)
        self.assertIs(result['rehearsal'], True)
        brief = self.service.lines.current(result['line_id'])
        self.assertTrue(el.brief_text(brief).startswith(msg('lines.rehearsal_mode')), 'it says so first')
        return result['line_id']

    def test_the_owner_takes_a_line_live_when_their_words_ask_for_exactly_that(self):
        line = self.open_rehearsal()
        seen = self.judged(True)
        result = self.line_call('이제 실제로 운용해', action='revise', line=line, expected_version='1', rehearsal=False)
        self.assertIs(result['rehearsal'], False)
        [change] = seen
        self.assertIn('goes live', change)

    def test_without_the_owners_words_for_it_the_line_stays_in_rehearsal(self):
        line = self.open_rehearsal()
        self.judged(False)
        refused = self.line_call('규칙 좀 정리해 줘', action='revise', line=line, expected_version='1', rehearsal=False)
        self.assertIn('line_rehearsal_owner_only', json.dumps(refused, ensure_ascii=False))
        self.assertTrue(self.service.lines.current(line)['rehearsal'])

    def test_a_run_of_the_line_cannot_take_it_live(self):
        line = self.open_rehearsal()
        owner = self.service.store.job(self.service.lines.current(line)['written_in'])
        for authority in ('steer', 'self'):
            with self.assertRaises(el.LineRefusal) as held:
                self.service.preparations_service._line_revise(
                    owner, {'line': line, 'expected_version': '1', 'rehearsal': False}, authority)
            self.assertEqual(held.exception.code, 'line_rehearsal_owner_only')
        self.assertTrue(self.service.lines.current(line)['rehearsal'])

    def test_an_undo_never_takes_a_line_live(self):
        result = self.line_call('관심 목록 확인 맡아 줘', action='open', task='관심 목록 확인', report_promise='끝나면 정리')
        line = result['line_id']
        on = self.line_call('일단 리허설로 바꿔 줘', action='revise', line=line, expected_version='1', rehearsal=True)
        self.assertIs(on['rehearsal'], True)
        self.line_call('방금 바꾼 거 되돌려 줘', action='undo', line=line, version='2')
        self.assertTrue(self.service.lines.current(line)['rehearsal'])


def test_an_api_change_of_a_line_run_in_rehearsal_is_refused_before_anything_is_asked(case):
    from personal_agent.api_requests import ApiError, ApiRequests, save_slot
    line, row = line_with_check(case)
    save_slot(case.store, 'plain', ['plain.example'], 'plain-transport-key')
    rehearse(case, line)
    job = woken(case, row)
    with pytest.raises(ApiError) as held:
        ApiRequests(case.store, job['id'], transport=lambda *a, **k: pytest.fail('nothing is sent')).call(
            {'slot': 'plain', 'url': 'https://plain.example/orders', 'method': 'POST', 'effect': 'mutate'})
    assert held.value.code == EFFECT_REHEARSAL and held.value.ran is False
