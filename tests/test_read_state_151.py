"""#151: an answer that says the asked value was not confirmed does not pass while material stayed unread.

Owner report 2026-10-20: asked for a member number in a sign-up mail, the worker answered that the
number could not be confirmed and linked one mail; the outcome judgment recorded ``reached``.  The
judgment now reads a structural ``read_state`` fact - which listed items this attempt opened and
read through, from Still's recorded evidence - and its proposition says what that fact means.

Evidence class: model-free unit tests with a real store, injected judgment doubles and a fake
Decision engine.  They prove which facts reach the one outcome judgment and how its verdict is
recorded; they do not prove how a live model words its verdict.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from personal_agent.agent_runtime import evidence_summary, goal_judgment, read_state_text, steps_read_state
from personal_agent.conversation_handoff import GOAL_REACHED_PROPOSITION, ConversationJudgments
from personal_agent.orchestrator import NOT_REACHED, REACHED, Orchestration
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

LISTED = {'message_count': 2, 'message_ids': ['m1', 'm2'], 'truncated': False, 'needs_setup': False}


def read(message_id, characters=900, truncated=False):
    return {'message_id': message_id, 'mime_type': 'text/plain', 'characters': characters,
            'truncated': truncated, 'needs_setup': False}


class ReadState(unittest.TestCase):
    def test_a_listed_item_no_read_opened_is_named(self):
        text = read_state_text([('mail_search', LISTED), ('mail_read', read('m1'))])
        self.assertIn('listed but never opened: 1 of 2 listed (m2)', text)
        self.assertNotIn('read through (', text)

    def test_partial_and_empty_reads_and_a_cut_listing_are_named(self):
        text = read_state_text([('mail_search', {**LISTED, 'truncated': True}), ('mail_read', read('m1', truncated=True)),
                                ('mail_read', read('m2', characters=0))])
        self.assertIn('opened but returned only in part (truncated): m1', text)
        self.assertIn('opened but returned no readable text: m2', text)
        self.assertIn('listing cut short, more items matched than were listed: mail_search', text)
        self.assertNotIn('never opened', text)

    def test_every_listed_item_read_through_is_said_plainly(self):
        text = read_state_text([('mail_search', LISTED), ('mail_read', read('m1')), ('mail_read', read('m2'))])
        self.assertTrue(text.endswith('every listed item was opened and read through (2 opened)'), text)

    def test_the_fact_is_structural_across_item_kinds_and_absent_without_items(self):
        files = {'file_count': 2, 'files': [{'root_id': 'r', 'path': 'a.txt'}, {'root_id': 'r', 'path': 'b.txt'}]}
        opened = {'root_id': 'r', 'path': 'a.txt', 'characters': 40, 'truncated': False}
        self.assertIn('never opened: 1 of 2 listed (r:b.txt)', read_state_text([('find_files', files), ('read_file', opened)]))
        self.assertEqual(read_state_text([('web_search', {'sources': ['https://s.test'], 'result_count': 3}),
                                          ('browser_open', {'characters': 0})]), '',
                         'a search or a page without an item id carries no read state')

    def test_the_direct_route_reads_the_same_fact_from_its_steps(self):
        steps = [('mail_search', 'mail_search', 'succeeded', {'messages': [{'message_id': 'm1'}, {'message_id': 'm2'}]}, None),
                 ('mail_read', 'mail_read', 'succeeded', {'message_id': 'm1', 'content': 'hello'}, None),
                 ('mail_read', 'mail_read', 'failed', {'message_id': 'm2'}, 'boom')]
        self.assertEqual(evidence_summary('mail_read', steps[1][3])['characters'], 5)
        self.assertIn('listed but never opened: 1 of 2 listed (m2)', steps_read_state(steps))
        seen = {}
        judgments = SimpleNamespace(goal_reached=lambda goal, observed, failed, **kw: seen.update(observed=observed)
                                    or SimpleNamespace(outcome='no', value='m2 was not read', note=None))
        verdict = goal_judgment(judgments, 'my member number?', {'evidence_refs': [], 'summary': 'not confirmed'}, {},
                                '', read_state=steps_read_state(steps))
        self.assertEqual((verdict, verdict.gap), ('no', 'm2 was not read'))
        self.assertTrue(seen['observed'].startswith('read_state'), seen['observed'])


class TheJudgmentReadsIt(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'state')
        self.service = AgentService(self.store)
        self.job = self.store.job(self.store.enqueue('what is my member number in the sign-up mail?', 'k1', owner_typed=True))

    def step(self, *evidences):
        with self.store.db() as db:
            for tool, evidence in evidences:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (self.job['id'], tool, 'succeeded',
                            json.dumps({'scope': 'subscription-mcp-bridge', 'host_action': tool, 'evidence': evidence}), 0.0))
        orchestration = SimpleNamespace(orchestrated=True, _redact=lambda text, **_: text, next=Mock(return_value=None),
                                        evaluate_answer=Mock(return_value='not_reached'), may_judge=lambda: True,
                                        budget_allows=lambda **_: True, budget=None)
        self.service.orchestration_step(orchestration, object(), self.job['id'], 0,
                                        answer='The number itself could not be confirmed.', outcome='partial')
        return orchestration.evaluate_answer.call_args.args[1]

    def test_the_cli_attempts_observations_carry_the_read_state_last(self):
        observations = self.step(('mail_search', LISTED), ('mail_read', read('m1')))
        self.assertTrue(observations.splitlines()[-1].startswith('- read_state'), observations)
        self.assertIn('never opened: 1 of 2 listed (m2)', observations)

    def test_the_proposition_says_what_the_fact_means(self):
        for rule in ('A read_state line in the observations is Still\'s own record',
                     'could not be confirmed does not serve the message: that material was not read',
                     'shows every listed item opened and read through is a statement about what was read'):
            self.assertIn(rule, GOAL_REACHED_PROPOSITION)

    def evaluate(self, observations, verdict):
        """The one judgment over ``observations``, with a fake engine whose verdict is ``verdict``."""
        seen = []

        class Engine:
            def judge_explained(self, context, proposition):
                seen.append((context.facts, proposition))
                from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, DecisionConfidence
                return BinaryDecision(OUTCOME_DECIDED, verdict == 'yes', DecisionConfidence(0.95, provider='fake'),
                                      gap='m2 was listed but never opened')

        judgments = ConversationJudgments(Engine())
        orchestration = Orchestration(judgments, SimpleNamespace(available=lambda: [], default='w', worker=lambda _w: None),
                                      request='what is my member number in the sign-up mail?')
        outcome = orchestration.evaluate_answer('The number itself could not be confirmed.', observations)
        return outcome, orchestration.gap, seen

    def test_unread_material_and_a_not_confirmed_answer_is_not_reached_with_a_gap(self):
        observations = '- mail_search: {}\n- ' + read_state_text([('mail_search', LISTED), ('mail_read', read('m1'))])
        outcome, gap, seen = self.evaluate(observations, 'no')
        self.assertEqual((outcome, gap), (NOT_REACHED, 'm2 was listed but never opened'))
        [(facts, proposition)] = seen
        self.assertIn('never opened: 1 of 2 listed (m2)', facts['observations'])
        self.assertIs(proposition, GOAL_REACHED_PROPOSITION)

    def test_an_absence_after_reading_every_source_stays_reached_in_one_call(self):
        observations = '- ' + read_state_text([('mail_search', LISTED), ('mail_read', read('m1')), ('mail_read', read('m2'))])
        outcome, gap, seen = self.evaluate(observations, 'yes')
        self.assertEqual((outcome, gap), (REACHED, ''))
        self.assertEqual(len(seen), 1, 'no extra model call beyond the one outcome judgment')


if __name__ == '__main__':
    unittest.main()
