"""Owner direction 2026-10-31: the waiting bubble names the launched worker and model, not each tool step.

Evidence class: model-free, fixture transports (the real QuickStore, AgentService and run_one;
a scripted compatible-API model; a recording Telegram transport).  Nothing live is contacted.
"""
import unittest
from types import SimpleNamespace

from personal_agent.orchestrator import DEFAULT_MODEL_LABEL
from personal_agent.quickstart_service import worker_line

from test_preparations import _Case, call, finish


class WorkerLine(unittest.TestCase):
    def test_the_line_is_what_was_launched_and_never_a_guess(self):
        self.assertEqual(worker_line({'engine': 'claude-code', 'model': 'claude-opus-5-5'}), 'Claude Code · claude-opus-5-5')
        self.assertEqual(worker_line({'engine': 'codex', 'model': None}), f'Codex · {DEFAULT_MODEL_LABEL}',
                         'no model passed is the CLI default, said as such')
        self.assertEqual(worker_line({'provider': 'openai', 'model': 'gpt-6.1-terra'}, 'high'), 'OpenAI API · gpt-6.1-terra · high')
        self.assertEqual(worker_line({'provider': 'compatible', 'model': 'm'}), 'compatible · m', 'an unnamed route keeps its id')
        self.assertEqual(worker_line({'route': 'direct-api'}), '')


class DraftShowsTheWorker(_Case):
    def test_while_the_worker_runs_the_bubble_names_it_then_the_line_ends_with_the_work(self):
        seen = []

        def step(body):
            work = self.work_id()
            seen.append(self.service._draft_step_text(self.store.job(work), SimpleNamespace(scrubbed=None)))
            return {'content': None, 'tool_calls': [call('1', 'list_notes')]}
        self.script = [step, finish('f', '1', summary='끝났어요.')]
        work = self.receive('메모 정리해줘')
        self.assertEqual(seen, ['compatible · test-model'], 'the launched route and model, not a tool step line')
        self.assertNotIn(work, self.service.live_workers)

    def work_id(self):
        with self.store.db() as db:
            return db.execute("SELECT id FROM jobs WHERE status='running' ORDER BY created DESC LIMIT 1").fetchone()['id']


if __name__ == '__main__':
    unittest.main()
