"""#674: the outcome judgment sees this Work's recorded effects, and cannot deny one.

Evidence class: model-free service tests.  The CLI route runs through ``AgentService.run_one``
with a fake execution adapter whose turn calls the real ``settings_change`` host tool; the
direct route runs the real ``run_agent`` with a scripted model.  A scripted Judgment AI answers
the outcome judgment.  The settings orchestrator, preparations, tool events and the stored Work
row are real.  No live model, CLI or account is used.  All text is made up.
"""
import json
import time
import unittest
from unittest import mock

from personal_agent import preparations as prep
from personal_agent.agent_runtime import (NOT_DONE_OTHER, RECORDED_EFFECT_FACT, evidence_summary, record_contradictions,
                                          recorded_effects, recorded_effects_text)
from personal_agent.conversation_handoff import JUDGMENT_NO, JUDGMENT_YES, ConversationJudgments, Judgment
from personal_agent.conversation_projection import TERMINAL_UNCONFIRMED_NOTE
from personal_agent.decision import (OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, SchemaDecisionEngine,
                                     fixture_confidence)

from test_orchestrator import Harness, plan
from test_truthful_terminal_result import CHAT, GENERATION, TerminalResultTestCase

DENIAL_GAP = 'The example change still waits for confirmation.'
DENIAL_NOTE = 'The example change has not been applied yet.'
EARLIER_ANSWER = 'I prepared the example change. Press Apply to confirm it.'


def denying(calls, answer=False, not_done=('e1',), note=DENIAL_NOTE):
    """A goal judgment that reports ``not_done`` (by default: the first recorded effect)."""
    def judge(context, proposition):
        if context.purpose != 'goal-reached':
            return None
        calls.append(context)
        return BinaryDecision(OUTCOME_DECIDED, answer, fixture_confidence(), gap=DENIAL_GAP if not answer else '',
                              note=note, retry_warranted=False, not_done=list(not_done))
    return judge


class RecordedEffectsUnit(unittest.TestCase):
    STEPS = [('settings_read', 'settings_read', 'succeeded', {'evidence': {}}, None),
             ('settings_change', 'settings_change', 'succeeded',
              {'evidence': {'draft_id': 'd1', 'applied': True, 'requires_owner_confirmation': None}}, None),
             ('settings_change', 'settings_change', 'withheld',
              {'evidence': {'draft_id': 'd2', 'applied': False, 'requires_owner_confirmation': True}}, None),
             ('settings_change', 'settings_change', 'succeeded',
              {'evidence': {'draft_id': 'd3', 'applied': False, 'requires_owner_confirmation': True}}, None),
             ('browser_click', 'browser_click', 'failed', {'code': 'tool_failed'}, 'failed'),
             ('example_write', 'example_write', 'succeeded', {}, None)]

    def test_only_calls_recorded_as_having_made_their_change_are_listed(self):
        # Reads, drafts awaiting the owner (withheld, or a confirmed draft whose record says not applied)
        # and failures are not; an unknown action that succeeded is (the one effect rule).
        effects = recorded_effects(self.STEPS)
        self.assertEqual(effects, [('e1', 'settings_change'), ('e2', 'example_write')])
        self.assertEqual(recorded_effects_text(effects).splitlines(),
                         [f'e1 settings_change: {RECORDED_EFFECT_FACT}', f'e2 example_write: {RECORDED_EFFECT_FACT}'])
        self.assertEqual(recorded_effects([]), [])
        self.assertEqual(recorded_effects_text([]), '')

    def test_contradictions_are_typed_ids_never_wording(self):
        effects = [('e1', 'settings_change'), ('e2', 'example_write')]
        self.assertEqual(record_contradictions(effects, ['e1']), (('e1',), True))
        self.assertEqual(record_contradictions(effects, [' E2 ', 'e1', 'e1']), (('e2', 'e1'), True))
        self.assertEqual(record_contradictions(effects, ['e1', NOT_DONE_OTHER]), (('e1',), False))
        self.assertEqual(record_contradictions(effects, ['e9']), ((), False))
        for absent in (None, [], [NOT_DONE_OTHER], 'e1'):
            self.assertEqual(record_contradictions(effects, absent), ((), False))
        self.assertEqual(record_contradictions([], ['e1']), ((), False))


class ScriptedSchemaEngine(SchemaDecisionEngine):
    def __init__(self, data):
        super().__init__()
        self.data, self.asked = data, []

    def _ask(self, context, kind, question, schema, valid):
        self.asked.append((context, question, schema))
        assert valid(self.data), self.data
        return OUTCOME_DECIDED, dict(self.data), fixture_confidence()


class GoalReachedBound(unittest.TestCase):
    """The bound lives in the one outcome judgment, so both routes share it."""

    EFFECTS = (('e1', 'settings_change'),)

    def judge(self, **fields):
        engine = ScriptedSchemaEngine({'answer': False, 'gap': DENIAL_GAP, 'note': DENIAL_NOTE, 'confidence': 0.9,
                                       'retry_warranted': True, **fields})
        judged = ConversationJudgments(engine).goal_reached('Change the example.', 'Example result.',
                                                            answer='Changed.', recorded_effects=self.EFFECTS)
        return engine, judged

    def test_the_schema_asks_not_done_and_the_facts_carry_the_record(self):
        engine, _judged = self.judge(not_done=[])
        context, question, schema = engine.asked[0]
        self.assertIn('not_done', schema['properties'])
        self.assertIn('not_done', schema['required'])
        self.assertIn('not_done:', question)
        self.assertEqual(context.facts['recorded_effects'], f'e1 settings_change: {RECORDED_EFFECT_FACT}')

    def test_a_denial_of_every_stated_shortfall_is_answered_by_the_record(self):
        _engine, judged = self.judge(not_done=['e1'])
        self.assertEqual((judged.outcome, judged.value, judged.note, judged.retry_warranted, judged.contradicted),
                         (JUDGMENT_YES, None, None, False, ('e1',)))

    def test_a_denial_beside_another_shortfall_keeps_the_no_but_not_its_line(self):
        _engine, judged = self.judge(not_done=['e1', NOT_DONE_OTHER])
        self.assertEqual((judged.outcome, judged.value, judged.note, judged.contradicted),
                         (JUDGMENT_NO, None, None, ('e1',)))

    def test_no_denial_leaves_the_judgment_as_it_was(self):
        for fields in ({'not_done': [NOT_DONE_OTHER]}, {'not_done': []}, {}):
            with self.subTest(fields=fields):
                _engine, judged = self.judge(**fields)
                self.assertEqual((judged.outcome, judged.value, judged.note, judged.retry_warranted, judged.contradicted),
                                 (JUDGMENT_NO, DENIAL_GAP, DENIAL_NOTE, True, ()))

    def test_a_yes_whose_line_denies_a_recorded_effect_loses_only_that_line(self):
        _engine, judged = self.judge(answer=True, gap='', not_done=['e1'])
        self.assertEqual((judged.outcome, judged.note, judged.contradicted), (JUDGMENT_YES, None, ('e1',)))


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
        self.old = self.service.settings_orchestrator.propose(
            self.service.settings_owner(self.store.job(earlier)), 'web', 'preparations', 'cancel', self.preparation['id'],
            work_id=earlier)
        self.results = []

    def work(self, owner_request):
        def call(tools):
            with mock.patch.object(self.service.decision_judge, 'explicit_settings_request',
                                   return_value=Judgment(owner_request)):
                value = tools.call('settings_change', {
                    'category': 'preparations', 'setting': 'cancel', 'value': self.preparation['id']})
            self.results.append(value)
            # The record the MCP bridge writes for this call (``mcp_bridge``: its redacted evidence summary).
            tools.capabilities.record('settings_change', 'succeeded', json.dumps({
                'scope': 'subscription-mcp-bridge', 'call_id': f'call-{len(self.results)}', 'host_action': 'settings_change',
                'evidence': evidence_summary('settings_change', value)}))
        return call

    def run_denied(self, owner_request, not_done=('e1',)):
        self.engine.before = self.work(owner_request)
        self.engine.answers = ['Done: the example report is cancelled.']
        engine = self.script([plan('codex', 'Answer.')])
        calls = []
        engine._judge = denying(calls, not_done=not_done)
        job, row = self.run_work('Cancel the example report.', owner_words=True)
        self.assertEqual(len(calls), 1, 'one outcome judgment')
        return job, row, calls[0]

    def test_an_applied_change_is_a_fact_the_judgment_cannot_deny(self):
        job, row, context = self.run_denied(JUDGMENT_YES)
        self.assertIn('"applied": true', json.dumps(self.results), self.results)
        # (a) The judgment's input carries this Work's applied change as a typed fact, beside the
        # earlier Work's answer that asked the owner to press Apply.
        self.assertEqual(context.facts['recorded_effects'], f'e1 settings_change: {RECORDED_EFFECT_FACT}')
        self.assertIn(EARLIER_ANSWER, context.facts['recent_conversation'])
        self.assertEqual(context.facts['awaiting_owner'], 'none')
        # (b) Its denial is not the closing line and does not make the Work partial.
        self.assertEqual(row['status'], 'succeeded')
        self.assertNotEqual(row['owner_note'], DENIAL_NOTE)
        self.assertFalse(row['owner_note'])
        evaluated = self.events(job, 'evaluated')[-1][1]
        self.assertEqual((evaluated['outcome'], evaluated['stop'], evaluated.get('record_won')), ('reached', 'reached', ['e1']))
        self.assertNotIn(DENIAL_GAP, evaluated['text'])

    def test_a_denial_beside_another_shortfall_stays_partial_with_the_floor_not_the_denial(self):
        _job, row, _context = self.run_denied(JUDGMENT_YES, not_done=('e1', NOT_DONE_OTHER))
        self.assertEqual(row['status'], 'partial')
        # Neither the judgment's line nor its gap is said; the truth floor closes the answer.
        self.assertIsNone(row['owner_note'])
        bubble = self.service.telegram_result_text(row['response'], row.get('owner_cause') or row['error'], row['status'],
                                                   verified=row.get('owner_verified'), note=row.get('owner_note'))
        self.assertEqual(bubble, row['response'] + '\n\n' + TERMINAL_UNCONFIRMED_NOTE)
        for denial in (DENIAL_NOTE, DENIAL_GAP):
            self.assertNotIn(denial, bubble)
            self.assertNotIn(denial, row.get('owner_cause') or '')

    def test_a_change_that_only_drafted_is_judged_as_before(self):
        job, row, context = self.run_denied(JUDGMENT_NO)
        self.assertTrue(self.results[-1]['requires_owner_confirmation'])
        self.assertEqual(context.facts['recorded_effects'], 'none')
        # The judgment's line about the waiting draft stays the closing line; no record overrides it.
        self.assertEqual(row['owner_note'], DENIAL_NOTE)
        self.assertNotEqual(row['status'], 'succeeded')
        self.assertNotIn('record_won', self.events(job, 'evaluated')[-1][1])


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

    def run_denied(self, owner_request):
        calls = []
        self.service.use_decision_engine(FixtureDecisionEngine(judge=denying(calls)))
        self.plan = [('settings_change', {'category': 'preparations', 'setting': 'cancel', 'value': self.preparation['id']})]
        self.text = 'Done: the example report is cancelled.'
        job_id = self.store.enqueue('Cancel the example report.', f'direct-{owner_request}',
                                    channel=f'telegram:{GENERATION}', chat_id=CHAT, owner_typed=True, owner_words=True)
        with mock.patch.object(self.service.decision_judge, 'explicit_settings_request',
                               return_value=Judgment(owner_request)):
            self.service.run_one()
        self.service.deliver_one()
        self.assertEqual(len(calls), 1, 'one outcome judgment')
        return self.store.job(job_id), calls[0]

    def test_an_applied_change_is_a_fact_the_judgment_cannot_deny(self):
        row, context = self.run_denied(JUDGMENT_YES)
        self.assertEqual(self.service.preparations.get(self.preparation['id'])['state'], prep.STATE_CANCELLED)
        self.assertEqual(context.facts['recorded_effects'], f'e1 settings_change: {RECORDED_EFFECT_FACT}')
        self.assertEqual(row['status'], 'succeeded')
        self.assertFalse(row['owner_note'])
        self.assertNotIn(DENIAL_NOTE, self.sent[-1])

    def test_a_change_that_only_drafted_is_judged_as_before(self):
        row, context = self.run_denied(JUDGMENT_NO)
        self.assertEqual(self.service.preparations.get(self.preparation['id'])['state'], prep.STATE_SCHEDULED)
        self.assertEqual(context.facts['recorded_effects'], 'none')
        self.assertNotEqual(row['status'], 'succeeded')
        self.assertEqual(row['owner_note'], DENIAL_NOTE)


if __name__ == '__main__':
    unittest.main()
