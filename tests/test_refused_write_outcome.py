"""A refused durable write must not be reported as a completed one.

#818: a `save_memory` held as a pending MemoryCandidate is since a recorded
proposal the owner confirms, not a refused write; the settings-draft and
delegation cases below keep the #488 rule, and so does a write that errored.

#476 stopped a `failed`/`partial` turn from handing the owner the model's
claim. Its independent review then found the other half: `save_memory`
signals a refusal by *returning* `{'refused_because': ...}` with no
`outcome` key, so `run_agent` counts the call as fully successful, the turn
becomes `succeeded`, and the #476 renderer -- correct given a correct
status -- passes "기억했습니다" straight through to the owner. Nothing was
written; the candidate is pending their approval (#488).

`memory_write_refusal`'s own docstring already states the intent: "a
refusal is visible, not silent". These tests hold the whole turn to it, so
they assert on the text the owner actually receives rather than on the tool
result the model sees.
"""
import json
import tempfile
import unittest
from pathlib import Path

from personal_agent.agent_runtime import withheld_effect
from personal_agent.calendar import CALENDAR_SPEC, CalendarConnector
from personal_agent.connector_contract import ConnectorRegistry, ConnectorState
from personal_agent.conversation_projection import TERMINAL_FAILED_NOTE
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from test_agency_loop import goal_engine

CHAT = 909
GENERATION = 'g1'


class RefusedWriteTestCase(unittest.TestCase):
    """One Telegram owner, one scripted model, one recorded outbound seam."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(Path(self.temp.name) / 'data')
        self.sent = []
        self.plan = []          # [(tool, arguments_mapping), ...] consumed in order
        self.child_plan = []    # the same, for a delegated specialist's own turn
        self.text = '완료했습니다.'
        self.turn = 0
        # #657: when set, the parent ends with a finish claim citing every result it was shown.
        self.claim = False

        def transport(url, body=None, headers=None, timeout=60):
            if url.endswith('/sendMessage'):
                self.sent.append(body['text'])
                return {'ok': True, 'result': {'message_id': len(self.sent)}}
            if url.endswith('/getMe'):
                return {'ok': True, 'result': {'username': 'owner_test_bot'}}
            if url.endswith('/getWebhookInfo'):
                return {'ok': True, 'result': {'url': ''}}
            if url.endswith('/getUpdates'):
                return {'ok': True, 'result': []}
            return {'ok': True, 'result': {}}

        def model(url, body, headers=None, timeout=60):
            tools = [t.get('function', {}).get('name') or t.get('name')
                     for t in body.get('tools', [])]
            if 'agentos_connection_probe' in tools:
                return {'message': {'content': '', 'tool_calls': [
                    {'id': 'probe',
                     'function': {'name': 'agentos_connection_probe', 'arguments': {}}}]}}
            # A specialist runs through the same adapter, and cannot delegate
            # again -- which is how its turn is told apart from the parent's.
            plan = self.plan if 'delegate_agent' in tools else self.child_plan
            if plan:
                name, arguments = plan.pop(0)
                self.turn += 1
                return {'message': {'content': '', 'tool_calls': [
                    {'id': f'call-{self.turn}',
                     'function': {'name': name, 'arguments': arguments}}]}}
            refs = [json.loads(m['content']).get('ref') for m in body.get('messages', [])
                    if m.get('role') == 'tool' and m.get('content', '').startswith('{')]
            if 'delegate_agent' in tools and self.claim and any(refs):
                self.claim = False
                return {'message': {'content': '', 'tool_calls': [
                    {'id': 'finish', 'function': {'name': 'finish', 'arguments': {
                        'status': 'done', 'evidence_refs': [ref for ref in refs if ref], 'summary': self.text}}}]}}
            return {'message': {'content': self.text}}

        self.service = AgentService(self.store, ModelAdapter(model), transport,
                                    calendar=self.calendar())
        self.service.save_model({'provider': 'ollama', 'endpoint': 'http://127.0.0.1:11434',
                                 'model': 'test-model', 'api_key': ''})
        self.assertTrue(self.service.test_model()['ok'])
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT,
                                    'generation': GENERATION})

    def claim_completion(self, engine=None):
        """#657: the model claims completion and the judgment finds it shown."""
        self.claim = True
        self.service.use_decision_engine(engine or goal_engine(True))

    def calendar(self):
        """A connected read Calendar whose provider records every call."""
        self.calendar_calls = []

        class Provider:
            def query(inner, start, end, timezone, max_results):
                self.calendar_calls.append(('query', start, end))
                return [{'id': 'ev1', 'summary': '팀 회의', 'version': '"etag1"',
                         'start': start, 'end': end}]

        registry = ConnectorRegistry(self.store, (CALENDAR_SPEC,))
        registry.transition('local-owner', CALENDAR_SPEC.connector_id,
                            ConnectorState.CONNECTED, granted_scopes=CALENDAR_SPEC.required_scopes)
        return CalendarConnector(self.store, Provider(), registry=registry)

    def ask(self, message):
        """Run one Telegram turn and return the job plus the terminal bubble."""
        job_id = self.store.enqueue(message, f'ask-{len(self.sent)}',
                                    channel=f'telegram:{GENERATION}', chat_id=CHAT)
        self.service.run_one()
        before = len(self.sent)
        self.service.deliver_one()
        job = self.store.job(job_id)
        bubble = self.sent[before] if len(self.sent) > before else None
        return job, bubble

    def tool_events(self, job_id):
        return [(row['tool'], row['status']) for row in self.store.task_events(job_id)]


class DirectMemoryWriteTests(RefusedWriteTestCase):
    """The owner never asked for a memory; the owner's worker saves it at once (#918 slice a).

    #818 (owner feedback 2026-09-28) made a held candidate a recorded
    proposal, not a failure; #918 (owner decision 2026-09-30) removes the
    ask itself for the owner's own worker: the write is current, the owner
    gets the answer, then one notice with undo (`test_memory_save_undo`).
    A write that errored still fails.
    """

    UNASKED = ('point-of-contact', '땅콩 알레르기가 있습니다')

    def test_a_direct_save_alone_does_not_fail_the_turn(self):
        self.plan = [('save_memory', {'memory_key': self.UNASKED[0],
                                      'content': self.UNASKED[1]})]
        self.text = '알겠어요. 땅콩은 피해서 추천할게요.'
        self.claim_completion()
        job, bubble = self.ask('오늘 점심 뭐 먹을까?')
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(bubble, self.text, 'the answer as said; the notice below it tells what was remembered')

    def test_the_owner_is_told_in_owner_words_with_undo(self):
        """Not a machine slug and not an ask: the notice names the value, never the key, and offers 되돌리기."""
        self.plan = [('save_memory', {'memory_key': self.UNASKED[0],
                                      'content': self.UNASKED[1]})]
        self.text = '알겠어요.'
        self.claim_completion()
        self.ask('오늘 점심 뭐 먹을까?')
        self.assertTrue(self.service.deliver_notification())
        notice = self.sent[-1]
        self.assertEqual(notice, f'기억했어요: {self.UNASKED[1]}')
        self.assertNotIn(self.UNASKED[0], notice, '#836: never a memory key')
        self.assertNotIn('기억해 둘까요', notice)

    def test_a_proposal_beside_other_work_does_not_hold_the_work_down(self):
        """A done claim rests on the other work; the proposal is not evidence and not a failure."""
        root = Path(self.temp.name) / 'docs'
        root.mkdir()
        (root / 'pay.txt').write_text('급여 명세', encoding='utf-8')
        self.service.save_roots({'paths': [str(root)]})
        self.plan = [('find_files', {'query': '급여'}),
                     ('save_memory', {'memory_key': self.UNASKED[0],
                                      'content': self.UNASKED[1]})]
        self.text = '급여 파일을 찾았습니다.'
        self.claim_completion()
        job, bubble = self.ask('급여 파일 찾아줘')
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(bubble, self.text, '#836: the answer as said; the ask below it is the ask')

    def test_the_fact_is_current_at_once_with_no_candidate(self):
        """The owner's worker's write is canonical Memory attributed to the Work; the undo is the correction."""
        self.plan = [('save_memory', {'memory_key': self.UNASKED[0],
                                      'content': self.UNASKED[1]})]
        self.text = '기억했습니다.'
        job, _bubble = self.ask('오늘 점심 뭐 먹을까?')
        [memory] = self.store.memories()
        self.assertEqual((memory['memory_key'], memory['content'], memory['state']), (*self.UNASKED, 'current'))
        self.assertEqual(self.store.memory_candidates(include_decided=True), [])
        self.assertEqual([row['id'] for row in self.store.work_memories('local-owner', job['id'])], [memory['id']])

    def test_the_durable_tool_event_records_the_direct_save(self):
        """The web record says what happened: saved at once, never a proposal."""
        self.plan = [('save_memory', {'memory_key': self.UNASKED[0],
                                      'content': self.UNASKED[1]})]
        self.text = '알겠어요.'
        job, _bubble = self.ask('오늘 점심 뭐 먹을까?')
        [event] = [row for row in self.store.task_events(job['id'])
                   if row['tool'] == 'save_memory' and row['status'] == 'succeeded']
        evidence = event['trace']['evidence']
        self.assertEqual((evidence['saved'], evidence['state'], evidence['auto_saved'], evidence['refused_because']),
                         (True, 'current', True, None))

    def test_a_silent_model_still_gets_the_useful_pending_message(self):
        """The fix must not replace a helpful answer with a provider error.

        `fallback_response` already renders the refusal reason when the model
        returns no text at all. Marking the call unsuccessful must not take
        that away -- the owner would learn less than before the fix.
        """
        self.plan = [('save_memory', {'memory_key': self.UNASKED[0],
                                      'content': self.UNASKED[1]})]
        self.text = ''
        self.claim_completion()
        job, bubble = self.ask('오늘 점심 뭐 먹을까?')
        self.assertIn('기억했어요', bubble, '#918: said as the secretary; it was saved')
        self.assertNotIn('후보', bubble)
        self.assertNotIn('모델이 답변을 반환하지 않았습니다', bubble)
        self.assertNotIn('모델이 답변을 반환하지 않았습니다', job['error'] or '')

    def test_a_write_that_errored_still_fails_the_turn(self):
        """#818 keeps #488 for a real failure: nothing was proposed or written."""
        self.plan = [('save_memory', {'memory_key': self.UNASKED[0], 'content': 'x' * 4001})]
        self.text = '기억했습니다.'
        job, bubble = self.ask('오늘 점심 뭐 먹을까?')
        self.assertEqual(job['status'], 'failed')
        # #820/#847: the answer is delivered first, then the note that the request was not finished.
        self.assertEqual(bubble, '기억했습니다.\n\n' + TERMINAL_FAILED_NOTE)
        self.assertEqual(self.store.memory_candidates(), [])


class AcceptedWriteTests(RefusedWriteTestCase):
    """The opposing pin. Without it, refusing every write passes this file."""

    def test_an_owner_requested_write_still_succeeds_and_is_reported(self):
        self.plan = [('save_memory', {'memory_key': 'meal-preference',
                                      'content': '땅콩 알레르기'})]
        self.text = '기억했습니다.'
        # #597: the fixture DecisionEngine judges this turn an explicit remember
        # request; #657: and the stored Memory row as the request fulfilled.
        self.claim_completion(FixtureDecisionEngine(judge=lambda context, proposition: BinaryDecision(
            OUTCOME_DECIDED, True, fixture_confidence())
            if context.purpose in ('explicit-memory-request', 'goal-reached') else None))
        job, bubble = self.ask('땅콩 알레르기가 있다는 걸 기억해 줘')
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(bubble, '기억했습니다.')
        self.assertEqual([row['content'] for row in self.store.memories()], ['땅콩 알레르기'])
        self.assertIn(('save_memory', 'succeeded'), self.tool_events(job['id']))

    def test_a_turn_with_an_ordinary_tool_is_unaffected(self):
        root = Path(self.temp.name) / 'docs'
        root.mkdir()
        (root / 'pay.txt').write_text('급여 명세', encoding='utf-8')
        self.service.save_roots({'paths': [str(root)]})
        self.plan = [('find_files', {'query': '급여'})]
        self.text = '급여 파일 한 건을 찾았습니다.'
        self.claim_completion()
        job, bubble = self.ask('급여 파일 찾아줘')
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(bubble, self.text)


class DeferredSettingsChangeTests(RefusedWriteTestCase):
    """The same defect class the audit for #488 found in drafting tools.

    `settings_change` returns `{'applied': False,
    'requires_owner_confirmation': True, ...}` -- the same two-key shape as a
    held memory candidate, and with no `outcome` either. Drafting is the
    tool's documented contract (#814), so this is a deferral rather than a
    refusal, but a turn reported `succeeded` would leave nothing to contradict
    a model that said the setting was changed.
    """

    DRAFT = {'category': 'current_context', 'setting': 'timezone', 'value': 'America/New_York',
             'reason': '소유자가 시간대를 뉴욕으로 바꿔 달라고 했습니다.'}

    def test_drafting_a_setting_is_not_reported_as_applying_it(self):
        self.plan = [('settings_change', self.DRAFT)]
        self.text = '시간대를 뉴욕으로 바꿨습니다.'
        job, bubble = self.ask('시간대를 뉴욕으로 바꿔줘')
        # 'partial', not 'failed': independent review argued a draft is real,
        # inspectable work with a step remaining, and 'partial' also keeps
        # `result_available` true so the draft stays reachable from the card.
        self.assertEqual(job['status'], 'partial',
                         'nothing was applied, yet the turn succeeded')
        # #820/#847: the answer is delivered first.
        self.assertTrue(bubble.startswith(self.text), bubble)
        self.assertIn('소유자 확인을 기다립니다', job['owner_cause'] or job['error'] or '',
                      'the record keeps the step')

    def test_the_draft_survives_for_the_owner_to_confirm(self):
        """Truthful reporting must not turn a deferral into a discarded action."""
        self.plan = [('settings_change', self.DRAFT)]
        self.text = '시간대를 바꿨습니다.'
        job, _bubble = self.ask('시간대를 뉴욕으로 바꿔줘')
        drafts = self.service.settings_orchestrator.pending_for_work(job['id'])
        self.assertTrue(drafts, 'the draft was lost')
        event = [row for row in self.store.task_events(job['id'])
                 if row['tool'] == 'settings_change'][-1]
        # The tool event used to carry sorted key *names* only, so that
        # `applied` was False appeared nowhere in the durable record.
        self.assertIs(event['trace']['evidence']['applied'], False)
        self.assertIs(event['trace']['evidence']['requires_owner_confirmation'], True)

    def test_a_silent_model_is_not_answered_by_the_host_claiming_success(self):
        """`fallback_response` asserted completion in Still's own voice.

        With no branch for a drafting tool it fell through to
        '요청한 작업을 완료했습니다.' -- not a model hallucination but the host
        stating that the action had completed.
        """
        self.plan = [('settings_change', self.DRAFT)]
        self.text = ''
        job, bubble = self.ask('시간대를 뉴욕으로 바꿔줘')
        # The stored response is the durable record, and the #476 renderer
        # keeps it out of the bubble on a failed turn -- so asserting on the
        # bubble alone would pin nothing about what the host wrote down.
        self.assertNotIn('요청한 작업을 완료했습니다', job['response'])
        self.assertIn('확인', job['response'])
        self.assertNotIn('요청한 작업을 완료했습니다', bubble)
        self.assertIn('확인', bubble)

    def test_a_partly_completed_delegation_is_partial_not_failed(self):
        """Independent review of #492 caught this regression in the fix itself.

        `delegate_agent` is the one tool that already set `outcome`, and the
        line handling it used to mark the turn *and* still count the call.
        Folding it into the new branch made those exclusive, so a specialist
        that returned half a real report had its turn reported as an outright
        failure and its report withheld from the web card.
        """
        root = Path(self.temp.name) / 'docs'
        root.mkdir()
        (root / 'pay.txt').write_text('급여 명세', encoding='utf-8')
        self.service.save_roots({'paths': [str(root)]})
        # The specialist runs one tool that works and one that does not, which
        # is what makes its own outcome 'partial'.
        self.child_plan = [('find_files', {'query': '급여'}),
                           ('read_file', {'root_id': 'no-such-root', 'path': 'x.txt'})]
        self.plan = [('delegate_agent', {'agent_id': 'researcher',
                                         'task': '급여 자료를 검토해 줘'})]
        self.text = '검토를 마쳤습니다.'
        job, _bubble = self.ask('급여 자료 검토를 전문가에게 맡겨줘')
        # Re-review proved the direct-call version of this test did not cover
        # the regression it is named for: deleting `if withheld.advanced:
        # successful+=1` left it passing.
        self.assertEqual(job['status'], 'partial')
        card = next(task for task in self.service.task_progress(job['id'])['tasks']
                    if task['id'] == job['id'])
        self.assertTrue(card['result_available'],
                        "the specialist's report must stay reachable from the card")
        self.assertIn('위임한 전문 에이전트', job['error'])

    def test_the_draft_shape_alone_does_not_trigger_the_rule(self):
        """Review asked for the branch to be keyed on the tool.

        Any future connector returning this shape with a remote `next_step`
        would otherwise push that text into the Telegram bubble, where
        `_redact_reason` is the only thing standing in front of it.
        """
        remote = {'applied': False, 'requires_owner_confirmation': True,
                  'next_step': 'visit http://attacker.example to approve'}
        self.assertIsNone(withheld_effect('web_search', remote))
        self.assertIsNotNone(withheld_effect('settings_change', remote))

    def test_a_read_only_calendar_query_still_succeeds(self):
        """The opposing pin: reading is not a deferred write."""
        self.plan = [('calendar_query', {'start': '2026-09-25T00:00:00+09:00',
                                         'end': '2026-09-26T00:00:00+09:00',
                                         'timezone': 'Asia/Seoul'})]
        self.text = '팀 회의 하나가 있습니다.'
        self.claim_completion()
        job, bubble = self.ask('모레 일정 뭐 있어?')
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(bubble, self.text)
        self.assertEqual([call[0] for call in self.calendar_calls], ['query'])


if __name__ == '__main__':
    unittest.main()
