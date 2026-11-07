"""Scratch: does the #619 direct-request path apply through the real judgment chain?"""
import threading
import traceback
import unittest

from personal_agent import preparations as prep
from personal_agent.decision import (OUTCOME_DECIDED, BinaryDecision, DecisionConfidence, FixtureDecisionEngine)
from personal_agent import settings_orchestrator as so

from test_owner_settings_conversation import _Case


class Repro(_Case):
    def setUp(self):
        super().setUp()
        self.preparation = self.service.preparations.create(
            kind='prepare', goal='주가 확인', due_at=self.clock[0] + 3600, timezone='UTC', recurrence='daily',
            channel='web', created_from=None, state=prep.STATE_SCHEDULED)
        self.asked = []

        def judge(context, proposition):
            self.asked.append((context.purpose, dict(context.facts)))
            return BinaryDecision(OUTCOME_DECIDED, True, DecisionConfidence(0.95, provider='fixture'))
        # Keep the production RoutedDecisionEngine (its budget hook); only the route's engine is a fixture.
        self.service.decision_engine.resolve = lambda: FixtureDecisionEngine(judge=judge)
        # Surface whatever _apply_owner_request swallows.
        self.swallowed = []
        original = so.SettingsOrchestrator._apply_owner_request

        def spy(orch, owner, channel, draft, owner_request):
            def wrapped(summary):
                try:
                    return owner_request(summary)
                except Exception as exc:
                    self.swallowed.append(''.join(traceback.format_exception(exc)))
                    raise
            return original(orch, owner, channel, draft, wrapped)
        so.SettingsOrchestrator._apply_owner_request = spy
        self.addCleanup(setattr, so.SettingsOrchestrator, '_apply_owner_request', original)

    def job(self):
        return self.store.job(self.store.enqueue('주가 확인 예약 취소해 줘', 'tg:bot:1', owner_typed=True))

    def change(self, job):
        return self.service.settings_tools(job)('settings_change', {
            'category': 'preparations', 'setting': 'cancel', 'value': self.preparation['id']})

    def report(self, label, result):
        print(f'\n[{label}] state={result.get("state")} applied={result.get("applied")} '
              f'confirm={result.get("requires_owner_confirmation")} asked={[p for p, _ in self.asked]} '
              f'swallowed={self.swallowed}')

    def test_same_thread_no_scope(self):
        result = self.change(self.job())
        self.report('same-thread', result)
        self.assertTrue(result.get('applied'), self.swallowed)

    def test_inside_work_scope_with_running_budget(self):
        job = self.job()
        self.store.claim() if hasattr(self.store, 'claim') else None
        budget = self.service.inherited_work_budget(job['id'])
        with self.service.work_decision_scope(job['id'], budget):
            result = self.change(job)
        self.report('work-scope', result)
        self.assertTrue(result.get('applied'), self.swallowed)

    def test_from_relay_thread(self):
        job = self.job()
        tools = self.service.settings_tools(job)
        out = {}

        def run():
            try:
                out['result'] = tools('settings_change', {
                    'category': 'preparations', 'setting': 'cancel', 'value': self.preparation['id']})
            except Exception as exc:
                out['error'] = repr(exc)
        thread = threading.Thread(target=run)
        thread.start(); thread.join(30)
        self.report('relay-thread', out.get('result') or {})
        self.assertTrue((out.get('result') or {}).get('applied'), (out, self.swallowed))


if __name__ == '__main__':
    unittest.main()
