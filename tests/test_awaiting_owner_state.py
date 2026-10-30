"""#240 (3) and (4): one typed "awaiting the owner" fact, and waiting is not a shortfall.

(4) The typed items (kind, shown, settled, state) are read from a Work's recorded tool events by one
rule (``owner_model.owner_decision_record``) and reach the plan call, the goal judgment on both
routes, and the self-review (``Upkeep.awaiting``, text unchanged).
(3) One service method (``work_waits_for_owner``) is the typed waiting state every surface reads:
Telegram's closing line, the task API and the web task view.  ``jobs.status`` stays ``partial``.

Evidence class: model-free unit and service tests with a real store, scripted judgments and a
fixture CLI; the web view runs app.js under node.  No live model was asked, so these prove which
facts reach the judgments and what each surface says, not how a live model words its verdict.
"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import owner_model as om
from personal_agent.conversation_handoff import GOAL_REACHED_PROPOSITION, ConversationJudgments
from personal_agent.conversation_projection import TERMINAL_UNCONFIRMED_NOTE
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.orchestrator import QUESTION, Orchestration
from personal_agent.quickstart_service import AgentService, awaiting_owner_rows
from personal_agent.quickstart_store import QuickStore

import test_orchestrator as orch
from test_refused_write_outcome import RefusedWriteTestCase

DRAFT = {'scope': 'subscription-mcp-bridge', 'call_id': 'c1', 'host_action': 'settings_change',
         'evidence': {'draft_id': 'set-1', 'category': 'current_context', 'setting': 'timezone',
                      'applied': False, 'requires_owner_confirmation': True}}
PROPOSAL = {'host_action': 'schedule_preparation',
            'evidence': {'preparation_id': 'p1', 'kind': 'reminder', 'state': 'proposed',
                         'requires_owner_acceptance': True}}
WAITING_TEXT = "settings_change: 1 shown for the owner's own approval, 0 later settled by the owner"


def record(store, job_id, tool, status, detail):
    with store.db() as db:
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (job_id, tool, status, json.dumps(detail), time.time()))


class StoreCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'state')
        self.job = self.store.job(self.store.enqueue('change my timezone to New York', 'k1', owner_typed=True))


class TypedItems(StoreCase):
    def test_an_item_waits_until_a_later_event_settles_it(self):
        self.assertEqual(om.work_awaiting_owner(self.store, self.job['id']), [])
        record(self.store, self.job['id'], 'settings_change', 'failed', DRAFT)
        record(self.store, self.job['id'], 'schedule_preparation', 'succeeded', PROPOSAL)
        self.assertEqual(om.work_awaiting_owner(self.store, self.job['id']),
                         [{'kind': 'settings_change', 'shown': 1, 'settled': 0, 'state': 'waiting'},
                          {'kind': 'schedule_preparation', 'shown': 1, 'settled': 0, 'state': 'waiting'}])
        later = self.store.enqueue('승인', 'k2', owner_typed=True)
        record(self.store, later, 'settings_change', 'succeeded',
               {**DRAFT, 'evidence': {**DRAFT['evidence'], 'applied': True, 'requires_owner_confirmation': False}})
        self.assertEqual(om.work_awaiting_owner(self.store, self.job['id']),
                         [{'kind': 'settings_change', 'shown': 1, 'settled': 1, 'state': 'settled'},
                          {'kind': 'schedule_preparation', 'shown': 1, 'settled': 0, 'state': 'waiting'}])

    def test_a_refused_write_an_applied_record_and_an_unfinished_call_are_not_items(self):
        refused = {'host_action': 'save_memory',
                   'evidence': {'requires_owner_approval': True, 'refused_because': 'third-party-memory-write'}}
        applied = {**DRAFT, 'evidence': {**DRAFT['evidence'], 'applied': True}}
        worded = {'host_action': 'web_search', 'evidence': {'note': 'requires_owner_approval'}}
        rows = [('save_memory', 'succeeded', refused), ('settings_change', 'succeeded', applied),
                ('web_search', 'succeeded', worded), ('settings_change', 'running', DRAFT)]
        self.assertEqual(om.awaiting_owner_items(rows), [])
        self.assertFalse(awaiting_owner_rows(rows), 'the attempt evaluation reads the same rule')
        self.assertTrue(awaiting_owner_rows([('settings_change', 'succeeded', DRAFT)]))

    def test_the_self_review_text_is_unchanged(self):
        upkeep = om.Upkeep(self.store)
        record(self.store, self.job['id'], 'settings_change', 'failed', DRAFT)
        record(self.store, self.job['id'], 'schedule_preparation', 'succeeded', {'requires_owner_acceptance': True})
        record(self.store, self.job['id'], 'settings_change', 'failed',
               {**DRAFT, 'evidence': {**DRAFT['evidence'], 'draft_id': 'set-2'}})
        record(self.store, self.store.enqueue('승인', 'k3', owner_typed=True), 'settings_change', 'succeeded',
               {'draft_id': 'set-2', 'applied': True})
        self.assertEqual(upkeep.awaiting(self.job['id']),
                         "settings_change: 2 shown for the owner's own approval, 1 later settled by the owner; "
                         "schedule_preparation: 1 shown for the owner's own approval, 0 later settled by the owner")


class PlanCall(unittest.TestCase):
    def catalogue(self):
        return orch.OrchestrationUnit.catalogue(self)

    def plan_facts(self, **kwargs):
        seen = []
        engine = FixtureDecisionEngine(structured=lambda context, question, schema: seen.append(context))
        Orchestration(ConversationJudgments(engine), self.catalogue(), request='r', **kwargs).first()
        return seen[0].facts

    def test_the_plan_reads_what_waits_in_the_conversation_and_what_this_work_left(self):
        facts = self.plan_facts(awaiting_conversation='settings-drafts (shown 5 minutes before this message, waiting)',
                                awaiting_work=lambda: WAITING_TEXT)
        self.assertEqual(facts['awaiting_owner'],
                         'in this conversation: settings-drafts (shown 5 minutes before this message, waiting)\n'
                         f'left by this Work: {WAITING_TEXT}')
        self.assertIn('awaiting_owner is Still\'s own record of what already waits', QUESTION)

    def test_nothing_waiting_is_none(self):
        self.assertEqual(self.plan_facts()['awaiting_owner'], 'none')
        self.assertEqual(self.plan_facts(awaiting_work=lambda: '')['awaiting_owner'], 'none')


class ServicePlanInputs(StoreCase):
    def test_the_work_orchestration_passes_the_conversation_items_and_this_works_events(self):
        service = AgentService(self.store)
        created = float(self.job['created'])
        items = [{'kind': 'settings-drafts', 'shown_text': 'PRIVATE PREVIEW TEXT', 'shown_at': created - 330},
                 {'kind': 'action-approval', 'shown_text': 'x', 'shown_at': created - 10}]
        with mock.patch('personal_agent.quickstart_service.worker_catalogue', return_value=mock.Mock(default='w')), \
                mock.patch('personal_agent.quickstart_service.Orchestration') as built, \
                mock.patch.object(service.owner_acts, 'open_items', return_value=items):
            service.work_orchestration(self.job, self.job['message'], [], {}, None)
        kwargs = built.call_args.kwargs
        self.assertEqual(kwargs['awaiting_conversation'],
                         'settings-drafts (shown 5 minutes before this message, waiting); '
                         'action-approval (shown just before this message, waiting)')
        self.assertNotIn('PRIVATE', kwargs['awaiting_conversation'], 'kinds and times only, never what was shown')
        self.assertEqual(kwargs['awaiting_work'](), '')
        record(self.store, self.job['id'], 'settings_change', 'failed', DRAFT)
        self.assertEqual(kwargs['awaiting_work'](), WAITING_TEXT, 'read at each plan, so a re-plan sees this Work\'s items')


class DirectRouteJudgment(StoreCase):
    def run_direct(self):
        import test_agency_loop as loop
        from personal_agent.agent_runtime import Capabilities, run_agent
        from personal_agent.providers import ModelAdapter
        seen = []

        def judge(facts):
            seen.append(facts)
            return True
        script = loop.Script({'tool_calls': [loop.call('1', 'web_search', query='New York timezone')]},
                             loop.finish('f', '1', summary='초안을 만들었어요. 승인하시면 반영돼요.'))
        caps = Capabilities(self.store, ModelAdapter(script), loop.CFG, '', self.job['id'], lambda *event: None,
                            network=loop.Network(), judgments=loop.judgments(judge))
        run_agent(caps.adapter, loop.CFG, '', [{'role': 'user', 'content': 'change my timezone to New York'}], '', caps,
                  caps.record)
        self.assertTrue(seen, 'the outcome judgment ran')
        return seen[0]

    def test_the_direct_route_judgment_reads_this_works_waiting_items(self):
        record(self.store, self.job['id'], 'settings_change', 'failed', DRAFT)
        self.assertEqual(self.run_direct()['awaiting_owner'], WAITING_TEXT)

    def test_nothing_waiting_is_none(self):
        self.assertEqual(self.run_direct()['awaiting_owner'], 'none')

    def test_the_proposition_says_a_wait_is_not_a_shortfall_and_not_an_approval(self):
        for rule in ("awaiting_owner is Still's own record of what this Work prepared and showed",
                     'does not fall short for that reason',
                     'an item is not approved or applied until a later event settles it'):
            self.assertIn(rule, GOAL_REACHED_PROPOSITION)
        self.assertIn('A step marked awaiting the owner prepared something', GOAL_REACHED_PROPOSITION,
                      'the failed-steps mark stays')


class OrchestratedRouteJudgment(orch.Harness):
    def test_the_plan_and_the_cli_goal_judgment_read_this_works_waiting_items(self):
        self.engine.answers = ['초안을 만들었어요. 승인하시면 반영돼요.']
        self.script([orch.plan('codex', 'Answer.')], goals=[True])
        job = self.store.enqueue('change my timezone to New York', 'awaiting-1')
        # An earlier run of this Work left a draft for the owner (a resumed Work).
        record(self.store, job, 'settings_change', 'failed', DRAFT)
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.asked_goals[-1].facts['awaiting_owner'], WAITING_TEXT)
        self.assertIn(f'left by this Work: {WAITING_TEXT}', self.asked_plans[-1][0].facts['awaiting_owner'])

    def test_nothing_waiting_is_none_for_both(self):
        self.engine.answers = ['답']
        self.script([orch.plan('codex', 'Answer.')], goals=[True])
        self.run_work('오늘 날씨')
        self.assertEqual(self.asked_goals[-1].facts['awaiting_owner'], 'none')
        self.assertEqual(self.asked_plans[-1][0].facts['awaiting_owner'], 'none')


class WaitingState(RefusedWriteTestCase):
    DRAFT = {'category': 'current_context', 'setting': 'timezone', 'value': 'America/New_York',
             'reason': '소유자가 시간대를 뉴욕으로 바꿔 달라고 했습니다.'}

    def task(self, job_id):
        return self.service.task_progress(job_id)['selected']

    def test_a_work_left_awaiting_approval_is_typed_waiting_and_stays_partial(self):
        self.plan = [('settings_change', self.DRAFT)]
        self.text = '시간대 변경 초안을 만들었어요.'
        job, bubble = self.ask('시간대를 뉴욕으로 바꿔줘')
        self.assertEqual(job['status'], 'partial', 'no status migration: the stored status is unchanged')
        self.assertEqual(self.service.work_waits_for_owner(job), ('settings-drafts',))
        self.assertTrue(self.service.work_awaits_owner_preview(job), 'the preview reading other surfaces call agrees')
        task = self.task(job['id'])
        self.assertEqual((task['waiting_for_owner'], task['waiting_for']), (True, ['settings-drafts']))
        self.assertNotIn(TERMINAL_UNCONFIRMED_NOTE, bubble)
        # The owner declines: nothing waits any more, and no surface keeps saying it does.
        [draft] = self.service.settings_orchestrator.pending_for_work(job['id'])
        self.service.settings_orchestrator.cancel(draft['owner'], draft['channel'], draft['id'])
        self.assertEqual(self.service.work_waits_for_owner(job), ())
        self.assertFalse(self.service.work_awaits_owner_preview(job))
        self.assertFalse(self.task(job['id'])['waiting_for_owner'])

    def test_a_note_left_beside_the_wait_keeps_the_web_from_calling_it_only_waiting(self):
        """#240 review: as on Telegram, a draft wait explains the outcome only when nothing else is left to
        say; with a recorded note the web keeps "partly done" and its notice, and still lists the wait."""
        self.plan = [('settings_change', self.DRAFT)]
        self.text = '시간대 변경 초안을 만들었어요.'
        job, _bubble = self.ask('시간대를 뉴욕으로 바꿔줘')
        with self.store.db() as db:
            db.execute('UPDATE jobs SET owner_note=? WHERE id=?', ('남은 일: 확인', job['id']))
        task = self.task(job['id'])
        self.assertEqual((task['waiting_for_owner'], task['waiting_for']), (False, ['settings-drafts']))
        with mock.patch.object(self.service, 'work_waits_for_owner', return_value=('action-approval',)):
            self.assertTrue(self.task(job['id'])['waiting_for_owner'], 'a step approval waits over a note')

    def test_a_work_with_nothing_waiting_is_unchanged(self):
        self.text = '안녕하세요.'
        job, _bubble = self.ask('안녕')
        self.assertEqual(self.service.work_waits_for_owner(job), ())
        task = self.task(job['id'])
        self.assertEqual((task['waiting_for_owner'], task['waiting_for']), (False, []))

    def test_a_preparation_proposal_waits_too(self):
        job = self.store.job(self.store.enqueue('매일 아침 날씨 알려줘', 'prep-1'))
        self.assertEqual(self.service.work_waits_for_owner(job), ())
        with mock.patch.object(self.service.preparations, 'proposed_from', return_value=[{'id': 'p1'}]):
            self.assertEqual(self.service.work_waits_for_owner(job), ('preparation-proposal',))


class TelegramClosingLine(RefusedWriteTestCase):
    def deliver(self, waits, note=None):
        job_id = self.store.enqueue('x', 'tg-close', channel='telegram:g1', chat_id=909)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial',response=?,owner_note=?,delivery='pending' WHERE id=?",
                       ('초안을 만들었어요.', note, job_id))
        before = len(self.sent)
        with mock.patch.object(self.service, 'work_waits_for_owner', return_value=waits) as read:
            self.service.deliver_one()
        read.assert_called()
        return self.sent[before]

    def test_the_closing_line_follows_the_typed_waiting_state(self):
        self.assertNotIn(TERMINAL_UNCONFIRMED_NOTE, self.deliver(('preparation-proposal',)))

    def test_a_work_with_nothing_waiting_keeps_its_closing_line(self):
        self.assertTrue(self.deliver(()).endswith(TERMINAL_UNCONFIRMED_NOTE))

    def test_a_note_is_still_said_unless_a_step_approval_waits(self):
        self.assertTrue(self.deliver(('settings-drafts',), note='남은 일: 확인').endswith('남은 일: 확인'))

    def test_a_step_approval_waits_over_a_note(self):
        self.assertFalse(self.deliver(('action-approval',), note='남은 일: 확인').endswith('남은 일: 확인'))


class WebView(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def run_view(self, body):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        app = self.ROOT / 'src' / 'personal_agent' / 'web' / 'app.js'
        script = "const ui=require(process.argv[1]);ui.setLanguage(process.argv[2]);" + body
        return {language: subprocess.run([node, '-e', script, str(app), language], capture_output=True, text=True,
                                         check=True).stdout
                for language in ('ko', 'en')}

    def test_a_partial_work_that_waits_shows_the_waiting_label_not_partly_done(self):
        out = self.run_view(r"""
const waiting={status:'partial',status_kind:'attention',waiting_for_owner:true,response:'초안',error:'e',
  qualifier:{outcome:'partial',verified:false}};
const short={status:'partial',status_kind:'attention',waiting_for_owner:false,response:'초안',error:'e',
  qualifier:{outcome:'partial',verified:false}};
const merged=ui.mergeTaskProgress({tasks:[waiting,short].map((task,i)=>({...task,id:'w'+i}))},null,[]).tasks;
console.log(JSON.stringify({waiting:ui.statusText(waiting),short:ui.statusText(short),
  outcome:ui.taskOutcome(waiting),shortOutcome:ui.taskOutcome(short).title,
  notice:ui.unverifiedAnswer(waiting),shortNotice:!!ui.unverifiedAnswer(short),
  errors:merged.map(task=>task.error??null)}));
""")
        ko, en = json.loads(out['ko']), json.loads(out['en'])
        self.assertEqual((ko['waiting'], ko['short']), ('승인 대기', '일부 완료'))
        self.assertEqual((en['waiting'], en['short']), ('Waiting for approval', 'Partly done'))
        self.assertEqual(ko['outcome'], {'title': '승인 대기', 'text': '초안', 'kind': 'attention'},
                         'the answer is shown, with no "not completed" line')
        self.assertEqual(ko['shortOutcome'], '일부 결과')
        self.assertEqual((ko['notice'], ko['shortNotice']), (None, True))
        self.assertEqual(ko['errors'], [None, 'e'], 'a waiting Work is not in the failed list')


if __name__ == '__main__':
    unittest.main()
