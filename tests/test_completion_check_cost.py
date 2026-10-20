"""#16 slices 2-3: pinned cost of the completion checks per request shape (measurement only).

``scripts/dev/measure_completion_checks.py`` runs the real ``run_agent``,
``Capabilities``, ``ConversationJudgments`` and ``ModelDecisionEngine`` with
scripted worker and judgment transports and a fake public wire, and attributes
every model request to the check that caused it.  This test pins those counts,
so a later change to the execution check (``checked_direct``), the completion
turn or the goal judgment shows its effect here.  It asserts current behavior;
it does not choose one.

Evidence class: model-free fixture measurement.  No live model or network.
"""
import unittest

from scripts.dev.measure_completion_checks import SHAPES, measure, table

#: shape -> (base worker requests, +checked_direct worker requests, +completion-turn worker requests,
#:           +goal-judgment worker requests, judgment requests, decision-audit records,
#:           Work budget turns spent, outcome, judgment)
EXPECTED = {
    'plain_reply': (1, 1, 0, 0, 0, 0, 2, 'succeeded', None),
    'plain_draft_then_tool': (1, 2, 0, 0, 1, 1, 4, 'succeeded', 'yes'),
    'tool_then_finish': (2, 0, 0, 0, 1, 1, 3, 'succeeded', 'yes'),
    'tool_then_plain_reply': (2, 0, 1, 0, 1, 1, 4, 'succeeded', 'yes'),
    'tool_then_plain_reply_twice': (2, 0, 1, 0, 1, 1, 4, 'succeeded', 'yes'),
    'failed_step_then_reply': (2, 0, 0, 0, 1, 1, 3, 'failed', 'no'),
    'judgment_rejects_once': (2, 0, 0, 2, 2, 2, 6, 'succeeded', 'yes'),
    # No judgment route: no request is sent, yet the reserve turn is spent and the
    # not-configured judgment is audited; the Work cannot succeed.
    'tool_then_finish_no_judgment_route': (2, 0, 0, 0, 0, 1, 3, 'partial', 'unavailable'),
}

#: shape -> the Work's model events in order (one ``responded`` per worker request).
EVENTS = {
    'plain_reply': ['responded', 'responded'],
    'plain_draft_then_tool': ['responded', 'responded', 'responded', 'concluded'],
    'tool_then_finish': ['responded', 'responded', 'concluded'],
    'tool_then_plain_reply': ['responded', 'responded', 'responded', 'concluded'],
    'tool_then_plain_reply_twice': ['responded', 'responded', 'responded', 'concluded'],
    'failed_step_then_reply': ['responded', 'responded', 'concluded'],
    'judgment_rejects_once': ['responded', 'responded', 'claim_rejected', 'responded', 'responded', 'concluded'],
    'tool_then_finish_no_judgment_route': ['responded', 'responded', 'concluded'],
}


#: shape -> {check: Work events recorded on the path that check added} (empty checks left out).
ADDED_EVENTS = {
    'plain_reply': {'checked_direct': ['model/responded']},
    'plain_draft_then_tool': {'checked_direct': ['model/responded', 'save_note/running', 'save_note/succeeded',
                                                 'model/responded', 'model/concluded']},
    'tool_then_finish': {},
    'tool_then_plain_reply': {'completion_turn': ['model/responded']},
    'tool_then_plain_reply_twice': {'completion_turn': ['model/responded']},
    'failed_step_then_reply': {},
    'judgment_rejects_once': {'goal_judgment': ['model/claim_rejected', 'model/responded', 'web_search/running',
                                                'web_search/succeeded', 'model/responded']},
    'tool_then_finish_no_judgment_route': {},
}


class CompletionCheckCostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = {row['shape']: row for row in measure()}

    def counts(self, row):
        added = row['added']
        return (row['base_worker_calls'], added['checked_direct']['worker_calls'],
                added['completion_turn']['worker_calls'], added['goal_judgment']['worker_calls'],
                row['judgment_calls'], added['goal_judgment']['audit_records'], row['budget_turns'],
                row['outcome'], row['judgment'])

    def test_every_shape_is_measured(self):
        self.assertEqual(set(self.rows), set(SHAPES))
        self.assertEqual(set(EXPECTED), set(SHAPES))

    def test_added_requests_per_check_and_shape(self):
        for shape, expected in EXPECTED.items():
            with self.subTest(shape=shape):
                self.assertEqual(self.counts(self.rows[shape]), expected)

    def test_recorded_model_events_per_shape(self):
        for shape, expected in EVENTS.items():
            with self.subTest(shape=shape):
                self.assertEqual(self.rows[shape]['model_events'], expected)

    def test_work_events_each_check_added(self):
        for shape, expected in ADDED_EVENTS.items():
            with self.subTest(shape=shape):
                added = {check: part['events'] for check, part in self.rows[shape]['added'].items() if part['events']}
                self.assertEqual(added, expected)

    def test_attribution_accounts_for_every_request_and_event(self):
        for shape, row in self.rows.items():
            with self.subTest(shape=shape):
                added = row['added']
                self.assertEqual(row['base_worker_calls'] + sum(part['worker_calls'] for part in added.values()),
                                 row['worker_calls'])
                model = [event for event in row['base_events'] + [e for part in added.values() for e in part['events']]
                         if event.startswith('model/')]
                self.assertEqual(sorted(model), sorted(f'model/{status}' for status in row['model_events']))
                self.assertEqual(added['goal_judgment']['judgment_calls'], row['judgment_calls'])
                responded = row['model_events'].count('responded')
                self.assertEqual(responded, row['worker_calls'], 'one responded event per worker request')

    def test_the_execution_check_is_what_runs_the_tool_a_draft_only_claimed(self):
        # Without the check the run would end at the first reply ("Saved.") with nothing saved.
        row = self.rows['plain_draft_then_tool']
        self.assertEqual(row['base_worker_calls'], 1)
        self.assertEqual(row['succeeded_tools'], ['save_note'])

    def test_an_added_worker_request_resends_the_whole_conversation(self):
        # Sizes are fixture characters, not tokens; only their relation is pinned.
        row = self.rows['plain_reply']
        self.assertGreater(row['added']['checked_direct']['request_chars'], row['base_request_chars'])
        row = self.rows['tool_then_plain_reply']
        self.assertGreater(row['added']['completion_turn']['request_chars'], row['base_request_chars'] / 2)

    def test_the_table_renders_every_shape(self):
        rendered = table(list(self.rows.values()))
        for shape in SHAPES:
            self.assertIn(f'`{shape}`', rendered)


if __name__ == '__main__':
    unittest.main()
