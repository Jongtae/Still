"""#1170: an owner's explicit change to an earlier plan is carried out, and a steer is never dropped silently.

Evidence class: model-free, fixture transports.  The real ``QuickStore``, ``AgentService`` (Telegram
ingress, ``run_one``, ``continuity_relation``), the worker turn assembly and the steer store run
unchanged; a scripted compatible-API model and a FixtureDecisionEngine replace only the providers.
Fixtures are neutral; no site, provider or task is named in ``src``.
"""
import time
import unittest

from personal_agent import work_status as ws
from personal_agent.agent_runtime import CORE_INSTRUCTIONS, turn_context
from personal_agent.conversation_handoff import FOLLOWUP_CORRECTION, FOLLOWUP_REFERENCE
from personal_agent.messages import msg
from personal_agent.quickstart_service import CORRECTION_NOTE, STEER_FOLDED_TEXT

import test_judged_continuity as continuity_cases
from test_steer_running_work import FIRST, REMARK, _SteerCase

CORRECTED = 'Drop the observe-only condition I set earlier; the checks may act now.'
REFERENCED = 'Why did you pick that time?'


class CorrectionReachesTheBrief(continuity_cases._Case):
    """Expected 1: the recorded ``correction`` relation reaches the worker, the plan and the judgment."""
    relations = continuity_cases.JudgedContinuityTests.relations
    earlier_work = continuity_cases.JudgedContinuityTests.earlier_work

    def test_only_a_correction_brief_says_the_owner_message_replaces_the_earlier_condition(self):
        earlier = self.earlier_work()
        self.relations({CORRECTED: FOLLOWUP_CORRECTION, REFERENCED: FOLLOWUP_REFERENCE})
        for message, relation in ((CORRECTED, FOLLOWUP_CORRECTION), (REFERENCED, FOLLOWUP_REFERENCE)):
            with self.subTest(relation=relation):
                start = len(self.model_calls)
                self.script = [{'content': 'Done.'}] * 2
                work = self.receive(message)
                job = self.store.job(work)
                self.assertEqual((job['relation_kind'], job['related_job_id']), (relation, earlier))
                host = '\n'.join(item['content'] for item in self.model_calls[start]['messages']
                                 if item['role'] == 'system')
                self.assertIn(f'Related Work {earlier}', host)
                continues = self.service.continued_exchange(job)
                if relation == FOLLOWUP_CORRECTION:
                    self.assertIn(CORRECTION_NOTE, host)
                    # Last, so the plan's and the judgment's end-kept bound keeps it.
                    self.assertTrue(continues.endswith('[Still] ' + CORRECTION_NOTE))
                else:
                    self.assertNotIn(CORRECTION_NOTE, host, 'a reference replaces nothing')
                    self.assertNotIn(CORRECTION_NOTE, continues)
                self.assertIn(continuity_cases.EARLIER, continues)

    def test_the_correction_line_survives_the_continues_bound(self):
        from personal_agent.orchestrator import CONTINUES_CHARS, Orchestration
        earlier = self.earlier_work()
        self.relations({CORRECTED: FOLLOWUP_CORRECTION})
        self.script = [{'content': 'Done.'}] * 2
        work = self.receive(CORRECTED)
        with self.store.db() as db:
            db.execute("UPDATE messages SET content=? WHERE job_id=? AND role='assistant'",
                       ('long earlier reply ' * 400, earlier))
        continues = self.service.continued_exchange(self.store.job(work))
        self.assertGreater(len(continues), CONTINUES_CHARS)
        orchestration = Orchestration(None, None, request=CORRECTED, continues=continues)
        self.assertIn(CORRECTION_NOTE, orchestration._continues_text())


class OwnerDecisionGuidance(unittest.TestCase):
    """Expected 2: every worker route is told the owner's decision within their grants is carried out."""

    RULE = 'When they change or drop it, or ask you to reassess a plan, their latest message is the instruction'

    def test_every_route_receives_the_rule_below_what_still_enforces(self):
        history = [{'role': 'user', 'content': 'go ahead'}]
        for route in ('api', 'cli'):
            with self.subTest(route=route):
                self.assertIn(self.RULE, turn_context(history, route)['instructions'])
        # It ranks below priority 1 (approvals, secrets, no invented effect), which it never overrides.
        self.assertLess(CORE_INSTRUCTIONS.index('happens only after the owner approves that exact action'),
                        CORE_INSTRUCTIONS.index(self.RULE))
        line = CORE_INSTRUCTIONS.split(self.RULE, 1)[1].split('\n', 1)[0]
        self.assertIn('an approval Still asks for', line, 'an approval still blocks; the rule never waives one')
        for word in ('trade', 'order', 'stock', 'price', 'buy', 'sell', 'market', 'broker'):
            self.assertNotIn(word, line.lower(), 'the rule names no domain or task (C16)')


class SteerJudgedShort(_SteerCase):
    """Expected 4: a steer folded into a Work judged not met is reported, never settled as applied."""

    def steered(self, status, goal_outcome):
        self.case = getattr(self, 'case', 0) + 1
        first = self.store.enqueue(FIRST, 'k1170-%d' % self.case)
        remark = self.store.enqueue(REMARK, 'r1170-%d' % self.case)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (first,))
        self.assertTrue(self.store.add_steer(first, remark, REMARK))
        claimed = time.time() - 1
        self.assertEqual(self.store.claim_unanswered_steers(first, claimed), [REMARK])
        with self.store.db() as db:
            db.execute('UPDATE jobs SET status=?,goal_outcome=? WHERE id=?', (status, goal_outcome, first))
            self.store.answer_steers(first, claimed, db)
        self.assertTrue(self.store.steer_answered(remark))
        return self.store.job(remark)

    def test_a_steer_whose_reply_was_judged_short_runs_on_its_own_and_says_why(self):
        remark = self.steered('partial', ws.GOAL_NOT_REACHED)
        self.assertFalse(self.service.fold_delivered_steer(remark))
        self.assertEqual(self.store.job(remark['id'])['status'], 'queued', 'not settled as applied')
        self.assertEqual(self.service.steer_disposition_notice(remark), msg('steer.not_applied.short'))

    def test_a_steer_whose_reply_served_the_request_is_still_folded(self):
        for status, goal in (('succeeded', ws.GOAL_REACHED), ('partial', ws.GOAL_OWNER_NEEDED)):
            with self.subTest(goal=goal):
                remark = self.steered(status, goal)
                self.assertTrue(self.service.fold_delivered_steer(remark))
                self.assertEqual(self.store.job(remark['id'])['response'], STEER_FOLDED_TEXT)


if __name__ == '__main__':
    unittest.main()
