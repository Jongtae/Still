"""Model-free regression: a saved task parameter must not become an owner statement.

Exercise real SQLite -> transcript projection -> API/CLI envelopes and the
related-exchange path. This verifies attribution, not a live model's judgment.
"""
import json
from datetime import datetime, timezone

from personal_agent import preparations as prep
from personal_agent.agent_runtime import render_turn_prompt, turn_context
from personal_agent.conversation_projection import context_message, earlier_turn_line
from test_preparations import _Case


class ScheduledRequestContext(_Case):
    GOAL = '이번 초안은 600자로 작성한다'
    CORRECTION = '다음 초안은 분량도 목적에 맞게 알아서 판단해 줘'

    def saved_run(self):
        row = self.scheduled(goal=self.GOAL)
        self.now += 120
        self.assertTrue(self.tick())
        self.assertTrue(self.service.run_one())
        [job] = self.runs(row['id'])
        return row, job

    def test_a_saved_parameter_is_context_instead_of_an_owner_instruction(self):
        row, job = self.saved_run()
        stored = next(m for m in self.store.history() if m['job_id'] == job['id'] and m['role'] == 'user')
        self.assertEqual(stored['content'], self.GOAL, 'original evidence stays unchanged')
        projected = context_message(stored)
        self.assertEqual(projected['role'], 'assistant', 'a generated goal is not the owner speaking')
        _label, source_json, goal = projected['content'].split('\n', 2)
        source = json.loads(source_json)
        self.assertEqual(source['kind'], 'scheduled_request')
        self.assertEqual(source['preparation_ref'], 'prep:' + row['id'])
        self.assertEqual(source['work_id'], job['id'])
        self.assertEqual(goal, self.GOAL)
        self.assertEqual(source['run_at'], datetime.fromtimestamp(stored['created'], timezone.utc).isoformat())
        for route in ('api', 'cli'):
            with self.subTest(route=route):
                context = turn_context([projected, {'role': 'user', 'content': self.CORRECTION}], route)
                self.assertEqual(context['request'], self.CORRECTION)
                self.assertEqual(context['conversation'][0]['role'], 'assistant')
                self.assertIn(job['id'], render_turn_prompt(context))
                judgment_line = earlier_turn_line(stored, context['conversation'][0])
                self.assertTrue(judgment_line.startswith('[assistant,'))

    def test_later_api_worker_receives_the_parameter_only_as_attributed_context(self):
        row, job = self.saved_run()
        self.model_calls.clear()
        self.receive(self.CORRECTION)
        sent = self.model_calls[0]['messages']
        historical = [m for m in sent[1:] if self.GOAL in m.get('content', '')]
        self.assertTrue(historical)
        self.assertTrue(all(m['role'] == 'assistant' for m in historical))
        self.assertIn('prep:' + row['id'], json.dumps(historical))
        self.assertEqual(sent[-1], {'role': 'user', 'content': self.CORRECTION})
        # The source remains a scheduled Work, not relabelled as owner-typed in storage.
        self.assertFalse(self.store.job(job['id'])['owner_typed'])

    def test_related_exchange_does_not_call_the_generated_goal_an_owner_statement(self):
        row, job = self.saved_run()
        current = self.store.enqueue(self.CORRECTION, 'correction', owner_typed=True)
        self.store.link_work_relation(current, job['id'], 'correction')
        continued = self.service.continued_exchange(self.store.job(current))
        self.assertFalse(continued.startswith('[owner]'))
        self.assertIn('prep:' + row['id'], continued)
        self.assertIn(self.GOAL, continued)

    def test_current_scheduled_work_still_executes_its_exact_accepted_goal(self):
        row, job = self.saved_run()
        sent = self.model_calls[0]['messages']
        self.assertEqual(sent[-1], {'role': 'user', 'content': self.GOAL})
        self.assertEqual(self.store.job(job['id'])['message'], self.GOAL)

    def test_a_real_owner_statement_or_an_unknown_source_is_not_reclassified(self):
        for source in ({'request_key': 'owner:1', 'owner_typed': 1}, {},
                       {'request_key': 'preparation:'}):
            with self.subTest(source=source):
                message = context_message({'role': 'user', 'content': self.GOAL, **source})
                self.assertEqual(message, {'role': 'user', 'content': self.GOAL})

    def test_attribution_keeps_literal_redaction_and_unknown_time(self):
        secret = 'synthetic-quote"slash\\value-123'
        self.store.secret('model_key', secret)
        row = {'role': 'user', 'content': self.GOAL + ' ' + secret,
               'request_key': prep.request_key('example', self.now) + '/loc:continuation'}
        projected = context_message(row)
        source = json.loads(projected['content'].split('\n', 2)[1])
        self.assertEqual(source['preparation_ref'], 'prep:example')
        self.assertIsNone(source['run_at'], 'unknown source time is never replaced by now')
        safe = self.service._redact_known_secrets(projected['content'])
        self.assertNotIn(secret, safe)
        self.assertNotIn('synthetic-quote', safe, 'escaping must not bypass literal secret removal')
