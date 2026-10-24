"""#170 JUDGE-CONTEXT-01: the judgments read this Work's own events and what it awaits.

Live 2026-10-21: a Work that correctly left calendar drafts for the owner's approval was judged
"already approved but not added" (an approval from an earlier Work, read from unlabelled history),
its stop text said an irreversible action ran, and the self-review learned "apply without
reconfirming" from that verdict and a reaction.

Evidence class: model-free unit tests with a real store and injected orchestration doubles. They
prove which facts reach the judgments and which evaluation is recorded; they do not prove how a
live model words its verdict.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from personal_agent.agent_runtime import AWAITING_OWNER_STEP_FACT, failed_steps_text, trail_steps
from personal_agent.conversation_handoff import GOAL_REACHED_PROPOSITION, ConversationJudgments
from personal_agent import owner_model as om
from personal_agent.quickstart_service import AgentService, awaiting_owner_rows, earlier_turn_line
from personal_agent.quickstart_store import QuickStore

DRAFT = {'scope': 'subscription-mcp-bridge', 'call_id': 'c1', 'host_action': 'calendar_draft_create',
         'evidence': {'draft_id': 'draft-1', 'action': 'create', 'applied': False, 'requires_owner_approval': True}}


class Case(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'state')
        self.service = AgentService(self.store)
        self.job = self.store.job(self.store.enqueue('add these to my calendar', 'k1', owner_typed=True))

    def record(self, job_id, tool, status, detail):
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job_id, tool, status, json.dumps(detail), 0.0))

    def step(self, *rows):
        with self.store.db() as db:
            since = db.execute('SELECT COALESCE(MAX(id),0) FROM tool_events').fetchone()[0]
        for tool, status, detail in rows:
            self.record(self.job['id'], tool, status, detail)
        orchestration = SimpleNamespace(orchestrated=True, _redact=lambda text: text, next=Mock(return_value=None),
                                        evaluate_answer=Mock(return_value='not_reached'), may_judge=lambda: True,
                                        budget_allows=lambda **_: True, budget=None)
        self.service.orchestration_step(orchestration, object(), self.job['id'], since, answer='prepared six events',
                                        outcome='partial')
        return orchestration


class AwaitingTheOwner(Case):
    def test_a_draft_left_for_the_owner_stops_for_the_owner_without_a_goal_judgment(self):
        orchestration = self.step(('calendar_draft_create', 'succeeded', DRAFT))
        orchestration.evaluate_answer.assert_not_called()
        self.assertEqual(orchestration.next.call_args.args[1], 'owner_needed')

    def test_an_applied_change_or_a_refused_candidate_is_still_judged(self):
        applied = {**DRAFT, 'evidence': {**DRAFT['evidence'], 'applied': True}}
        candidate = {'scope': 'subscription-mcp-bridge', 'call_id': 'c2', 'host_action': 'save_memory',
                     'evidence': {'requires_owner_approval': True, 'refused_because': 'third-party-memory-write'}}
        for row in (applied, candidate):
            with self.subTest(row=row['host_action']):
                orchestration = self.step((row['host_action'], 'succeeded', row))
                orchestration.evaluate_answer.assert_called_once()

    def test_the_typed_flags_decide_never_the_wording(self):
        self.assertTrue(awaiting_owner_rows([('settings_change', 'succeeded',
                                              {'evidence': {'applied': False, 'requires_owner_confirmation': True}})]))
        self.assertTrue(awaiting_owner_rows([('schedule_preparation', 'succeeded', {'requires_owner_acceptance': True})]))
        self.assertFalse(awaiting_owner_rows([('web_search', 'succeeded', {'evidence': {'note': 'requires_owner_approval'}})]))
        self.assertFalse(awaiting_owner_rows([('calendar_draft_create', 'running', DRAFT)]))

    def test_a_waiting_step_reaches_the_judgment_as_awaiting_not_failed(self):
        text = failed_steps_text(trail_steps([('calendar_draft_create', 'succeeded', json.dumps(DRAFT))]))
        self.assertEqual(text, f'calendar_draft_create: {AWAITING_OWNER_STEP_FACT}')
        self.assertNotIn('failed', text)
        self.assertIn('A step marked awaiting the owner prepared something Still now shows the owner', GOAL_REACHED_PROPOSITION)


class EarlierWorks(Case):
    def test_each_history_row_is_an_earlier_work_with_its_time(self):
        from datetime import timezone
        line = earlier_turn_line({'created': 0}, {'role': 'user', 'content': '승인'}, timezone.utc)
        self.assertEqual(line, '[owner, earlier Work 1970-01-01 00:00] 승인')
        self.assertEqual(earlier_turn_line({}, {'role': 'assistant', 'content': 'ok'}, None), '[assistant, earlier Work] ok',
                         'no time is guessed when the zone is unknown')
        self.assertIn('Each recent_conversation row is an earlier Work', GOAL_REACHED_PROPOSITION)
        self.assertIn("only observations and failed_steps are this Work's own events", GOAL_REACHED_PROPOSITION)

    def test_the_plan_and_the_goal_judgment_get_labelled_rows(self):
        rows = [{'role': 'user', 'content': '승인', 'created': 1.0, 'job_id': 'earlier'},
                {'role': 'assistant', 'content': '반영했어요', 'created': 2.0, 'job_id': 'earlier'},
                {'role': 'user', 'content': 'add these to my calendar', 'created': 3.0, 'job_id': self.job['id']}]
        from unittest import mock
        with mock.patch('personal_agent.quickstart_service.worker_catalogue', return_value=SimpleNamespace(default='w')), \
                mock.patch('personal_agent.quickstart_service.Orchestration') as built:
            self.service.work_orchestration(self.job, self.job['message'], rows, {}, None)
        lines = built.call_args.kwargs['conversation']  # #275: one line per earlier turn
        self.assertIsInstance(lines, list)
        self.assertEqual(len(lines), 2, 'the current request is not part of the history')
        self.assertTrue(all(line.startswith(('[owner, earlier Work', '[assistant, earlier Work')) for line in lines), lines)
        self.assertEqual(built.call_args.kwargs['sections']['history'], 2)


class SelfReviewFacts(Case):
    def test_the_review_is_told_what_waited_for_the_owner_and_what_became_of_it(self):
        upkeep = om.Upkeep(self.store)
        self.assertEqual(upkeep.awaiting(self.job['id']), '')
        self.record(self.job['id'], 'calendar_draft_create', 'succeeded', DRAFT)
        self.assertEqual(upkeep.awaiting(self.job['id']),
                         "calendar_draft_create: 1 shown for the owner's own approval, 0 later settled by the owner")
        later = self.store.enqueue('승인', 'k2', owner_typed=True)
        self.record(later, 'calendar_create', 'succeeded', {'draft_id': 'draft-1', 'event_id': 'e1', 'effect': 'observed'})
        self.assertEqual(upkeep.awaiting(self.job['id']),
                         "calendar_draft_create: 1 shown for the owner's own approval, 1 later settled by the owner")

    def test_the_fact_and_the_rule_reach_the_review_question(self):
        seen = []

        class Engine:
            def structured(self, context, question, schema, shape):
                seen.append((context, question))
                from personal_agent.decision import OUTCOME_UNAVAILABLE, StructuredDecision
                return StructuredDecision(OUTCOME_UNAVAILABLE)

        ConversationJudgments(Engine()).self_review('r', 'a', 'partial', 'v', '👍', '', 'reaction',
                                                    awaiting_owner='calendar_draft_create: 1 shown')
        [(context, question)] = seen
        self.assertIn('calendar_draft_create: 1 shown', json.dumps(context.facts, ensure_ascii=False))
        for rule in ('evaluation is a model\'s reading of the Work, not the owner\'s words',
                     "such a wait is the software's own gate, never an owner habit",
                     'a reaction on a Work that was waiting may be the owner\'s answer to that wait rather than a lesson'):
            self.assertIn(rule, question)


if __name__ == '__main__':
    unittest.main()
