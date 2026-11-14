"""#674: the outcome judgment sees this Work's completed effects, and a denial of one is re-judged once.

Evidence class: model-free service tests.  The CLI route runs through ``AgentService.run_one``
with a fake execution adapter whose turn calls the real ``settings_change`` host tool; the
direct route runs the real ``run_agent`` with a scripted model.  A scripted Judgment AI answers
the outcome judgment (and its one re-judgment).  The settings orchestrator, preparations, tool
events and the stored Work row are real.  No live model, CLI or account is used.  All text is made up.
"""
import json
import time
import unittest
from unittest import mock

from personal_agent import preparations as prep
from personal_agent.agent_runtime import (NOT_DONE_OTHER, RECORDED_EFFECT_FACT, evidence_summary, record_contradictions,
                                          recorded_effects, recorded_effects_text, second_judgment_allowed)
from personal_agent.conversation_handoff import (JUDGMENT_NO, JUDGMENT_UNAVAILABLE, JUDGMENT_YES, REJUDGED_DENIED_AGAIN,
                                                 REJUDGED_NOT_ALLOWED, REJUDGED_UNAVAILABLE, REJUDGED_YES,
                                                 ConversationJudgments, Judgment)
from personal_agent.decision import (OUTCOME_DECIDED, OUTCOME_UNAVAILABLE, BinaryDecision, FixtureDecisionEngine,
                                     SchemaDecisionEngine, fixture_confidence)

from test_orchestrator import Harness, plan
from test_truthful_terminal_result import CHAT, GENERATION, TerminalResultTestCase

DENIAL_GAP = 'The example change still waits for confirmation.'
DENIAL_NOTE = 'The example change has not been applied yet.'
SECOND_NOTE = 'The example report will not run again.'
EARLIER_ANSWER = 'I prepared the example change. Press Apply to confirm it.'
APPLIED_FACT = 'e1 settings_change: ' + RECORDED_EFFECT_FACT.format(receipt='applied')

DENY = {'answer': False, 'not_done': ['e1']}
YES = {'answer': True, 'not_done': [], 'note': SECOND_NOTE}
UNAVAILABLE = None


def scripted(calls, answers):
    """Goal judgments answered in order: a dict of fields, or None (unavailable)."""
    answers = list(answers)

    def judge(context, proposition):
        if context.purpose != 'goal-reached':
            return None
        calls.append(context)
        answer = answers.pop(0) if answers else None
        if answer is None:
            return BinaryDecision(OUTCOME_UNAVAILABLE)
        verdict = answer['answer']
        return BinaryDecision(OUTCOME_DECIDED, verdict, fixture_confidence(), gap='' if verdict else DENIAL_GAP,
                              note=answer.get('note', DENIAL_NOTE), retry_warranted=False, not_done=answer['not_done'])
    return judge


class RecordedEffectsUnit(unittest.TestCase):
    STEPS = [('settings_read', 'settings_read', 'succeeded', {'evidence': {}}, None),
             ('settings_change', 'settings_change', 'succeeded',
              {'evidence': {'draft_id': 'd1', 'applied': True, 'requires_owner_confirmation': None}}, None),
             ('settings_change', 'settings_change', 'withheld',
              {'evidence': {'draft_id': 'd2', 'applied': False, 'requires_owner_confirmation': True}}, None),
             ('settings_change', 'settings_change', 'succeeded',
              {'evidence': {'draft_id': 'd3', 'applied': False, 'requires_owner_confirmation': True}}, None),
             # Codex P1 on #676: the CLI route records an unaccepted proposal as a plain success.
             ('schedule_preparation', 'schedule_preparation', 'succeeded',
              {'evidence': {'preparation_id': 'p1', 'state': 'proposed', 'scheduled': False,
                            'requires_owner_acceptance': True}}, None),
             ('schedule_preparation', 'schedule_preparation', 'succeeded',
              {'evidence': {'preparation_id': 'p2', 'state': 'proposed', 'scheduled': True,
                            'requires_owner_acceptance': True}}, None),
             ('browser_click', 'browser_click', 'failed', {'code': 'tool_failed'}, 'failed'),
             # The classifier's ``changed`` fallback is no completion receipt.
             ('example_write', 'example_write', 'succeeded', {}, None),
             ('example_write', 'example_write', 'succeeded', {'applied': True, 'effect': 'unknown'}, None),
             # The direct route's steps carry the result itself.
             ('schedule_preparation', 'schedule_preparation', 'succeeded',
              {'preparation_id': 'p3', 'state': 'scheduled', 'scheduled': True}, None)]

    def test_only_calls_with_a_positive_completion_receipt_are_listed(self):
        effects = recorded_effects(self.STEPS)
        self.assertEqual(effects, [('e1', 'settings_change', 'applied'), ('e2', 'schedule_preparation', 'scheduled')])
        self.assertEqual(recorded_effects_text(effects).splitlines(),
                         [APPLIED_FACT, 'e2 schedule_preparation: ' + RECORDED_EFFECT_FACT.format(receipt='scheduled')])
        self.assertEqual(recorded_effects([]), [])
        self.assertEqual(recorded_effects_text([]), '')

    def test_contradictions_are_typed_ids_never_wording(self):
        effects = [('e1', 'settings_change', 'applied'), ('e2', 'save_note', 'saved')]
        self.assertEqual(record_contradictions(effects, ['e1']), ('e1',))
        self.assertEqual(record_contradictions(effects, [' E2 ', 'e1', 'e1']), ('e2', 'e1'))
        self.assertEqual(record_contradictions(effects, ['e1', NOT_DONE_OTHER]), ('e1',))
        for absent in (None, [], [NOT_DONE_OTHER], ['e9'], 'e1'):
            self.assertEqual(record_contradictions(effects, absent), ())
        self.assertEqual(record_contradictions([], ['e1']), ())

    def test_the_re_judgment_keeps_to_the_works_judgment_budget(self):
        class Budget:
            def __init__(self, interrupted=None, remaining=60.0):
                self.stop, self.left, self.spent = interrupted, remaining, 0
            def interrupted(self):
                return self.stop
            def remaining(self):
                return self.left
            def spend_turn(self, reserve=False):
                self.spent += 1
        self.assertTrue(second_judgment_allowed(None, None))
        budget = Budget()
        self.assertTrue(second_judgment_allowed(None, budget))
        self.assertEqual(budget.spent, 1, 'its turn is spent when the engine does not spend it')
        carried = Budget()
        judgments = type('J', (), {'engine': type('E', (), {'work_budget': carried})()})()
        self.assertTrue(second_judgment_allowed(judgments, carried))
        self.assertEqual(carried.spent, 0, 'the engine spends it')
        self.assertFalse(second_judgment_allowed(None, Budget(interrupted='stopped')))
        self.assertFalse(second_judgment_allowed(None, Budget(remaining=0)))


class ScriptedSchemaEngine(SchemaDecisionEngine):
    """The real explained-judgment schema; answers in order (None: unavailable)."""

    def __init__(self, *answers):
        super().__init__()
        self.answers, self.asked = list(answers), []

    def _ask(self, context, kind, question, schema, valid):
        self.asked.append((context, question, schema))
        data = self.answers.pop(0)
        if data is None:
            return OUTCOME_UNAVAILABLE, {}, None
        data = {'gap': DENIAL_GAP if data['answer'] is False else '', 'note': DENIAL_NOTE, 'confidence': 0.9,
                'retry_warranted': True, **data}
        assert valid(data), data
        return OUTCOME_DECIDED, data, fixture_confidence()


class GoalReachedBound(unittest.TestCase):
    """The check lives in the one outcome judgment, so both routes share it."""

    EFFECTS = (('e1', 'settings_change', 'applied'),)

    def judge(self, *answers, allowed=True):
        engine = ScriptedSchemaEngine(*answers)
        judged = ConversationJudgments(engine).goal_reached('Change the example.', 'Example result.', answer='Changed.',
                                                            recorded_effects=self.EFFECTS, may_rejudge=lambda: allowed)
        return engine, judged

    def test_the_schema_asks_not_done_and_the_facts_carry_the_record(self):
        engine, _judged = self.judge({'answer': False, 'not_done': []})
        context, question, schema = engine.asked[0]
        self.assertIn('not_done', schema['properties'])
        self.assertIn('not_done', schema['required'])
        self.assertIn('not_done:', question)
        self.assertEqual(context.facts['recorded_effects'], APPLIED_FACT)
        self.assertNotIn('record_contradiction', context.facts)

    def test_a_denial_is_judged_once_more_with_the_record_as_a_fact(self):
        engine, judged = self.judge(DENY, YES)
        self.assertEqual(len(engine.asked), 2)
        second = engine.asked[1][0].facts['record_contradiction']
        self.assertIn('e1 settings_change recorded applied: true', second)
        self.assertEqual((judged.outcome, judged.note, judged.record_check),
                         (JUDGMENT_YES, SECOND_NOTE, {'contradicted': ['e1'], 'rejudged': REJUDGED_YES}))

    def test_a_mislabelled_denial_never_succeeds_unless_the_second_judgment_says_yes(self):
        # The first reading tagged a wrong result as e1; the second, given the record, names it other.
        engine, judged = self.judge(DENY, {'answer': False, 'not_done': [NOT_DONE_OTHER], 'note': SECOND_NOTE})
        self.assertEqual((judged.outcome, judged.value, judged.note), (JUDGMENT_NO, DENIAL_GAP, SECOND_NOTE))
        self.assertEqual(judged.record_check['rejudged'], 'no')

    def test_without_a_usable_second_answer_the_first_verdict_stands_without_its_line(self):
        for answers, allowed, rejudged, asked in (((DENY, DENY), True, REJUDGED_DENIED_AGAIN, 2),
                                                 ((DENY, UNAVAILABLE), True, REJUDGED_UNAVAILABLE, 2),
                                                 ((DENY,), False, REJUDGED_NOT_ALLOWED, 1)):
            with self.subTest(rejudged=rejudged):
                engine, judged = self.judge(*answers, allowed=allowed)
                self.assertEqual(len(engine.asked), asked)
                self.assertEqual((judged.outcome, judged.value, judged.note, judged.retry_warranted),
                                 (JUDGMENT_NO, None, None, True), 'no upgrade, no contradicting line')
                self.assertEqual(judged.record_check, {'contradicted': ['e1'], 'rejudged': rejudged})

    def test_no_denial_asks_once_and_leaves_the_judgment_as_it_was(self):
        for not_done in ([NOT_DONE_OTHER], [], ['e9']):
            with self.subTest(not_done=not_done):
                engine, judged = self.judge({'answer': False, 'not_done': not_done})
                self.assertEqual(len(engine.asked), 1)
                self.assertEqual((judged.outcome, judged.value, judged.note, judged.record_check),
                                 (JUDGMENT_NO, DENIAL_GAP, DENIAL_NOTE, None))

    def test_an_unavailable_first_judgment_is_unchanged(self):
        engine, judged = self.judge(UNAVAILABLE)
        self.assertEqual((len(engine.asked), judged.outcome, judged.record_check), (1, JUDGMENT_UNAVAILABLE, None))


class CliRouteRecordedEffects(Harness):
    """The live shape of #674: an applied change, with an older pending draft for the same target."""

    def setUp(self):
        super().setUp()
        self.preparation = self.service.preparations.create(
            kind='prepare', goal='Example hourly report', due_at=time.time() + 3600, timezone='UTC',
            recurrence='daily', channel='web', created_from=None, state=prep.STATE_SCHEDULED)
        # An earlier Work drafted a change to the same preparation and asked the owner to press Apply.
        earlier = self.store.enqueue('Stop the example report.', 'earlier-674')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (earlier,))
            for role, text in (('user', 'Stop the example report.'), ('assistant', EARLIER_ANSWER)):
                db.execute('INSERT INTO messages(role,content,job_id,created) VALUES (?,?,?,?)', (role, text, earlier, 1))
        self.service.settings_orchestrator.propose(
            self.service.settings_owner(self.store.job(earlier)), 'web', 'preparations', 'cancel', self.preparation['id'],
            work_id=earlier)
        self.results = []

    def bridge_record(self, tools, action, value):
        """The record the MCP bridge writes for one call (``mcp_bridge``: its redacted evidence summary)."""
        tools.capabilities.record(action, 'succeeded', json.dumps({
            'scope': 'subscription-mcp-bridge', 'call_id': f'call-{len(self.results)}', 'host_action': action,
            'evidence': evidence_summary(action, value)}))

    def change(self, owner_request):
        def call(tools):
            with mock.patch.object(self.service.decision_judge, 'explicit_settings_request',
                                   return_value=Judgment(owner_request)):
                value = tools.call('settings_change', {
                    'category': 'preparations', 'setting': 'cancel', 'value': self.preparation['id']})
            self.results.append(value)
            self.bridge_record(tools, 'settings_change', value)
        return call

    def run_judged(self, work, answers):
        self.engine.before = work
        self.engine.answers = ['Done: the example report is cancelled.']
        engine = self.script([plan('codex', 'Answer.')])
        calls = []
        engine._judge = scripted(calls, answers)
        job, row = self.run_work('Cancel the example report.', owner_words=True)
        return job, row, calls

    def bubble(self, row):
        return self.service.telegram_result_text(row['response'], row.get('owner_cause') or row['error'], row['status'],
                                                 verified=row.get('owner_verified'), note=row.get('owner_note'))

    def test_an_applied_change_denied_by_the_judgment_ends_on_the_re_judgment(self):
        job, row, calls = self.run_judged(self.change(JUDGMENT_YES), [DENY, YES])
        self.assertTrue(self.results[0]['applied'])
        # The judgment's input carries this Work's applied change, beside the earlier "press Apply" answer.
        self.assertEqual(calls[0].facts['recorded_effects'], APPLIED_FACT)
        self.assertIn(EARLIER_ANSWER, calls[0].facts['recent_conversation'])
        self.assertEqual(len(calls), 2, 'asked once more, with the record stated')
        self.assertIn('recorded applied: true', calls[1].facts['record_contradiction'])
        self.assertEqual(row['status'], 'succeeded')
        self.assertNotIn(DENIAL_NOTE, self.bubble(row))
        evaluated = self.events(job, 'evaluated')[-1][1]
        self.assertEqual((evaluated['outcome'], evaluated['record_check']),
                         ('reached', {'contradicted': ['e1'], 'rejudged': REJUDGED_YES}))

    def test_without_a_usable_re_judgment_there_is_no_upgrade_and_no_false_line(self):
        for second, rejudged in ((UNAVAILABLE, REJUDGED_UNAVAILABLE), (DENY, REJUDGED_DENIED_AGAIN)):
            with self.subTest(rejudged=rejudged):
                self.results.clear()
                self.preparation = self.service.preparations.create(
                    kind='prepare', goal='Example hourly report', due_at=time.time() + 3600, timezone='UTC',
                    recurrence='daily', channel='web', created_from=None, state=prep.STATE_SCHEDULED)
                job, row, calls = self.run_judged(self.change(JUDGMENT_YES), [DENY, second])
                self.assertEqual(len(calls), 2)
                self.assertNotEqual(row['status'], 'succeeded')
                self.assertIsNone(row['owner_note'])
                bubble = self.bubble(row)
                self.assertEqual(bubble, row['response'])
                for denial in (DENIAL_NOTE, DENIAL_GAP):
                    self.assertNotIn(denial, bubble)
                    self.assertNotIn(denial, row.get('owner_cause') or '')
                self.assertEqual(self.events(job, 'evaluated')[-1][1]['record_check']['rejudged'], rejudged)

    def test_an_unaccepted_proposal_is_never_a_recorded_effect_and_its_no_stands(self):
        # Codex P1 on #676: the bridge records a proposal awaiting acceptance as succeeded.
        def propose(tools):
            self.results.append(None)
            self.bridge_record(tools, 'schedule_preparation', {
                'preparation_id': 'p-example', 'kind': 'reminder', 'state': 'proposed', 'scheduled': False,
                'requires_owner_acceptance': True})
        job, row, calls = self.run_judged(propose, [DENY, YES])
        self.assertEqual(calls[0].facts['recorded_effects'], 'none')
        self.assertEqual(len(calls), 1, 'nothing to re-judge')
        # Its no stands: the stop is the owner's, and the judgment recorded is the first one, unchanged.
        evaluated = self.events(job, 'evaluated')[-1][1]
        self.assertEqual((evaluated['outcome'], evaluated['judged'], evaluated['gap']), ('owner_needed', 'not_reached', DENIAL_GAP))
        self.assertNotIn('record_check', evaluated)

    def test_a_change_that_only_drafted_is_judged_as_before(self):
        job, row, calls = self.run_judged(self.change(JUDGMENT_NO), [DENY, YES])
        self.assertTrue(self.results[-1]['requires_owner_confirmation'])
        self.assertEqual(calls[0].facts['recorded_effects'], 'none')
        self.assertEqual(len(calls), 1)
        self.assertEqual(row['owner_note'], DENIAL_NOTE)
        self.assertNotEqual(row['status'], 'succeeded')
        self.assertNotIn('record_check', self.events(job, 'evaluated')[-1][1])


class DirectRouteRecordedEffects(TerminalResultTestCase):
    def setUp(self):
        super().setUp()
        self.preparation = self.service.preparations.create(
            kind='prepare', goal='Example hourly report', due_at=time.time() + 3600, timezone='UTC',
            recurrence='daily', channel=f'telegram:{GENERATION}', created_from=None, state=prep.STATE_SCHEDULED)
        earlier = self.store.enqueue('Stop the example report.', 'earlier-direct-674',
                                     channel=f'telegram:{GENERATION}', chat_id=CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (earlier,))
            for role, text in (('user', 'Stop the example report.'), ('assistant', EARLIER_ANSWER)):
                db.execute('INSERT INTO messages(role,content,job_id,created) VALUES (?,?,?,?)', (role, text, earlier, 1))
        self.service.settings_orchestrator.propose(self.service.settings_owner(self.store.job(earlier)),
                                                   f'telegram:{GENERATION}', 'preparations', 'cancel',
                                                   self.preparation['id'], work_id=earlier)

    def run_judged(self, owner_request, answers):
        calls = []
        self.service.use_decision_engine(FixtureDecisionEngine(judge=scripted(calls, answers)))
        self.plan = [('settings_change', {'category': 'preparations', 'setting': 'cancel', 'value': self.preparation['id']})]
        self.text = 'Done: the example report is cancelled.'
        job_id = self.store.enqueue('Cancel the example report.', f'direct-{owner_request}-{len(answers)}-{answers[-1]}',
                                    channel=f'telegram:{GENERATION}', chat_id=CHAT, owner_typed=True, owner_words=True)
        with mock.patch.object(self.service.decision_judge, 'explicit_settings_request',
                               return_value=Judgment(owner_request)):
            self.service.run_one()
        before = len(self.sent)
        self.service.deliver_one()
        return self.store.job(job_id), calls, self.sent[before:]

    def test_an_applied_change_denied_by_the_judgment_ends_on_the_re_judgment(self):
        row, calls, _sent = self.run_judged(JUDGMENT_YES, [DENY, YES])
        self.assertEqual(self.service.preparations.get(self.preparation['id'])['state'], prep.STATE_CANCELLED)
        self.assertEqual(calls[0].facts['recorded_effects'], APPLIED_FACT)
        self.assertEqual(len(calls), 2)
        self.assertEqual(row['status'], 'succeeded')

    def test_without_a_usable_re_judgment_there_is_no_upgrade_and_no_false_line(self):
        row, calls, sent = self.run_judged(JUDGMENT_YES, [DENY, UNAVAILABLE])
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(row['status'], 'succeeded')
        self.assertNotEqual(row['owner_note'], DENIAL_NOTE)
        for text in sent:
            self.assertNotIn(DENIAL_NOTE, text)
            self.assertNotIn(DENIAL_GAP, text)

    def test_a_change_that_only_drafted_is_judged_as_before(self):
        row, calls, _sent = self.run_judged(JUDGMENT_NO, [DENY, YES])
        self.assertEqual(self.service.preparations.get(self.preparation['id'])['state'], prep.STATE_SCHEDULED)
        self.assertEqual(calls[0].facts['recorded_effects'], 'none')
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(row['status'], 'succeeded')
        self.assertEqual(row['owner_note'], DENIAL_NOTE)


if __name__ == '__main__':
    unittest.main()
