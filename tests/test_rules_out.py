"""RULES-OUT-01 (#173, slice A): keyword intent rules no longer answer the owner.

Each request below was once claimed by a kernel cue table and answered from a
rule result, with no AI involved: a retry returned an old saved workspace
file, a spending question returned raw note excerpts, a meeting summary was
saved as a workspace file under an invented title, and a status-and-disconnect
request returned a settings read and lost the disconnect.  Now each reaches
the owner's chosen AI (the worker) with the owner's words verbatim, and the
material a rule would have found is the worker's to find with its own tools.

Evidence class: model-free, fixture transports (``test_preparations._Case``).
A scripted compatible-API model, a recording Telegram transport and a
FixtureDecisionEngine replace only the providers; ``AgentService`` (Telegram
ingress, ``run_one``), ``run_agent`` and ``Capabilities`` run unchanged.  No
live model, Telegram or Google account is contacted, and none is claimed.
"""
import unittest

from personal_agent.conversation_handoff import INTENT_CONVERSATION
from personal_agent.file_workspace import FileWorkspace
from test_preparations import _Case

RETRY = '아까 결과 다시 해줘'
SPENDING = '내 기록에서 지난달 지출 합계 알려줘'
MEETING_NOTE = '회의 내용 정리해서 노트로 저장해줘'
GMAIL_STATUS_AND_DISCONNECT = 'Gmail 연결 상태 보여주고 끊어줘'
ANSWER = '확인해 볼게요.'


class FormerRuleRequestsReachTheWorker(_Case):
    def setUp(self):
        super().setUp()
        # Material each retired rule would have answered from.
        base = self.root.parent
        self.reference, self.workspace = base / 'reference', base / 'workspace'
        self.reference.mkdir()
        self.workspace.mkdir()
        (self.reference / '회의록.md').write_text('회의 결정: 10월 12일 출시', encoding='utf-8')
        state = self.service.configure_file_workspace({'references': [str(self.reference)],
                                                       'workspace': str(self.workspace)})
        files = FileWorkspace(self.store)
        source = files.read(state['references'][0]['id'], '회의록.md')
        files.save('earlier-work', '지난 결과', 'OLD-SAVED-RESULT 회의 결정 요약', [source])
        with self.store.db() as db:
            db.execute('INSERT INTO notes VALUES (?,?,?)', ('n1', '지난달 지출 메모: 커피 12000원', 1.0))
        self.saved_files = sorted(path.name for path in self.workspace.glob('*.md'))
        self.notes = [row['content'] for row in self.store.notes()]

    def turn(self, text):
        self.script = [{'content': ANSWER}] * 2
        job = self.store.job(self.receive(text))
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        return job

    def worker_message(self):
        return self.model_calls[0]['messages'][-1]['content']

    def assertNoRuleAnswered(self, job):
        self.assertEqual(job['response'], ANSWER, 'the worker answered, not a rule')
        self.assertEqual(self.service.conversation_focus.current()['intent'], INTENT_CONVERSATION)
        self.assertEqual(self.store.config('personal_knowledge_audit', []), [], 'no personal-space rule search ran')
        self.assertEqual(sorted(path.name for path in self.workspace.glob('*.md')), self.saved_files,
                         'no rule saved a workspace file')
        self.assertEqual([row['content'] for row in self.store.notes()], self.notes, 'no rule wrote a note')
        self.assertEqual(self.store.config('settings_change_drafts', {}), {}, 'no rule drafted a setting')

    def test_a_retry_request_reaches_the_worker_not_an_old_saved_file(self):
        job = self.turn(RETRY)
        self.assertEqual(self.worker_message(), RETRY)
        self.assertNotIn('OLD-SAVED-RESULT', job['response'])
        self.assertNoRuleAnswered(job)

    def test_a_question_about_my_records_reaches_the_worker_with_its_own_tools(self):
        job = self.turn(SPENDING)
        self.assertEqual(self.worker_message(), SPENDING)
        tools = {tool['function']['name'] for tool in self.model_calls[0].get('tools', [])}
        self.assertTrue({'search_memory', 'list_notes'} <= tools, tools)
        self.assertNoRuleAnswered(job)

    def test_a_meeting_summary_note_reaches_the_worker_and_no_file_is_invented(self):
        job = self.turn(MEETING_NOTE)
        self.assertEqual(self.worker_message(), MEETING_NOTE)
        self.assertIn('save_note', {tool['function']['name'] for tool in self.model_calls[0].get('tools', [])})
        self.assertFalse((self.workspace / '회의 결과 브리프.md').exists())
        self.assertNoRuleAnswered(job)

    def test_a_status_and_disconnect_request_reaches_the_worker_whole(self):
        job = self.turn(GMAIL_STATUS_AND_DISCONNECT)
        self.assertEqual(self.worker_message(), GMAIL_STATUS_AND_DISCONNECT)
        self.assertIn('settings_read', {tool['function']['name'] for tool in self.model_calls[0].get('tools', [])})
        self.assertNoRuleAnswered(job)

    def test_retired_natural_language_commands_reach_the_worker(self):
        for text in ('무엇이 연결되어 있어?', '메모 목록', '메모: 오로라 예산'):
            with self.subTest(text=text):
                self.model_calls.clear()
                job = self.turn(text)
                self.assertEqual(self.worker_message(), text)
                self.assertNoRuleAnswered(job)

    def test_slash_commands_stay_exact_commands_without_the_worker(self):
        for text, expected in (('/notes', '지난달 지출 메모'), ('/knowledge 지출', '지난달 지출 메모'),
                               ('/workspace-search OLD-SAVED-RESULT', 'OLD-SAVED-RESULT')):
            with self.subTest(text=text):
                self.model_calls.clear()
                job = self.store.job(self.receive(text))
                self.assertEqual(job['status'], 'succeeded', job.get('error'))
                self.assertIn(expected, job['response'])
                self.assertEqual(self.model_calls, [], 'an explicit command needs no worker')


if __name__ == '__main__':
    unittest.main()
