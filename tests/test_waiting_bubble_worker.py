"""Owner direction 2026-10-31: the waiting bubble names the launched worker and model, not each tool step.

Evidence class: model-free, fixture transports (the real QuickStore, AgentService and run_one;
a scripted compatible-API model; a recording Telegram transport).  Nothing live is contacted.
"""
import unittest
from types import SimpleNamespace

from personal_agent.bounded_execution import live_progress_reader, live_sub_agent_steps
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


class SubAgentsInTheBubble(_Case):
    """#460: the worker's sub-agents, read from Claude Code's own stream, by the description it gave them."""

    def launch(self, ident, description):
        return {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': ident, 'name': 'Agent', 'input': {'description': description, 'prompt': 'p'}}]}}

    def done(self, ident):
        return {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': ident, 'content': 'r'}]}}

    def test_only_top_level_launches_count_and_codex_reports_none(self):
        self.assertEqual(live_sub_agent_steps('claude-code', self.launch('a1', '숙소 후보 찾기')),
                         [{'state': 'agent_running', 'id': 'a1', 'description': '숙소 후보 찾기'}])
        nested = {**self.launch('a2', 'inner'), 'parent_tool_use_id': 'a1'}
        self.assertEqual(live_sub_agent_steps('claude-code', nested), [], "a sub-agent's own calls are not a new one")
        self.assertEqual(live_sub_agent_steps('codex', self.launch('a1', 'x')), [])
        other = {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't', 'name': 'WebSearch', 'input': {}}]}}
        self.assertEqual(live_sub_agent_steps('claude-code', other), [])

    def test_the_bubble_names_them_while_they_run_redacted(self):
        import json
        from test_preparations import SECRET
        work = self.store.enqueue('부산 숙소', 'tg-sub', 'telegram:gen-1', 77)
        self.service.record_turn_worker(work, {'engine': 'claude-code', 'model': 'claude-opus-5-5'})
        on_line = live_progress_reader('claude-code', lambda step: self.service._observe_cli_step(work, step))
        line = lambda: self.service._draft_step_text(self.store.job(work), SimpleNamespace(scrubbed=None))
        on_line(json.dumps(self.launch('a1', '숙소 후보 찾기'), ensure_ascii=False))
        on_line(json.dumps(self.launch('a2', '교통편 알아보기'), ensure_ascii=False))
        self.assertEqual(line(), 'Claude Code · claude-opus-5-5 › 숙소 후보 찾기, 교통편 알아보기')
        on_line(json.dumps(self.done('a1')))
        self.assertEqual(line(), 'Claude Code · claude-opus-5-5 › 교통편 알아보기')
        on_line(json.dumps(self.launch('a3', f'토큰 {SECRET} 확인'), ensure_ascii=False))
        self.assertNotIn(SECRET, line())
        on_line(json.dumps(self.done('a2')))
        on_line(json.dumps(self.done('a3')))
        self.assertEqual(line(), 'Claude Code · claude-opus-5-5')


if __name__ == '__main__':
    unittest.main()
