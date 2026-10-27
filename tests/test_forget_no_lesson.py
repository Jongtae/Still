"""#380: no owner-model lesson from a Work that managed owner state; forget Evidence states undo and scope."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from personal_agent import owner_model as om
from personal_agent.agent_runtime import MEMORY_OWNER, Capabilities, evidence_summary
from personal_agent.owner_forget import OUTSIDE_SCOPE
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.subscription_engines import SubscriptionEngines  # noqa: F401 (store fixtures import order)


class NoLessonFromForget(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        self.service = AgentService(self.store)

    def work(self, message, *, related=None, relation=None, actions=()):
        job = self.store.enqueue(message, str(uuid.uuid4()), owner_typed=True, channel='web')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='답',related_job_id=?,relation_kind=? WHERE id=?",
                       (related, relation, job))
            for action in actions:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job, 'forget_record', 'succeeded',
                            json.dumps({'host_action': 'forget_record', 'evidence': {'action': action}}), 1.0))
        return job

    def run_upkeep(self, job):
        with mock.patch.object(self.service.owner_model, 'run', return_value=('done', 0, 'ok', {})) as run, \
                mock.patch.object(self.service.owner_model, 'run_review', return_value=('done', 0, 'ok', {})) as review, \
                mock.patch.object(self.service.owner_model, 'finish') as finish:
            for kind in (om.KIND_UPKEEP, om.KIND_SELF_REVIEW):
                self.service._owner_model_run({'job_id': job, 'expired': False, 'kind': kind}, 1000.0)
        return run.called or review.called, [call.args[4] for call in finish.call_args_list]

    def test_a_work_that_forgot_owner_state_teaches_nothing(self):
        for action in ('forget', 'delete', 'undo'):
            with self.subTest(action=action):
                ran, details = self.run_upkeep(self.work('어제 얘기 잊어줘', actions=('find', action)))
                self.assertFalse(ran)
                self.assertEqual(details, [{'reason': 'owner-state-management'}] * 2)

    def test_a_work_corrected_by_a_deletion_is_not_reviewed(self):
        """The observed case: "그거 완전히 지워줘" was linked as a correction of the forget Work."""
        first = self.work('어제 커피 얘기 잊어줘')
        self.work('그거 완전히 지워줘', related=first, relation='correction', actions=('list', 'delete'))
        ran, details = self.run_upkeep(first)
        self.assertFalse(ran)
        self.assertEqual(details, [{'reason': 'owner-state-management'}] * 2)

    def test_looking_only_is_not_managing_and_an_unrelated_work_still_runs(self):
        looked = self.work('잊은 게 뭐가 있지?', actions=('list', 'find'))
        plain = self.work('내일 날씨 어때?')
        self.work('다른 얘기', related=plain, relation='reference', actions=('delete',))
        for job in (looked, plain):
            with self.subTest(job=job):
                ran, _details = self.run_upkeep(job)
                self.assertTrue(ran, 'only forget/delete/undo by the Work or its correction/retry stops the upkeep')


class ForgetEvidence(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        self.job = self.store.enqueue('잊어줘', 'forget-ev')

    def test_the_evidence_states_undo_and_the_outside_scope_without_content(self):
        caps = Capabilities(self.store, None, {}, '', self.job, lambda *e: None)
        row = self.store.save_memory('city', '서울', MEMORY_OWNER, work_id='w-source')
        forgot = caps.execute('forget_record', {'action': 'forget', 'ref': 'memory:' + row['id']})
        summary = evidence_summary('forget_record', forgot)
        self.assertEqual((summary['can_undo'], summary['outside_scope'], summary['local_scope_only']), (True, OUTSIDE_SCOPE, True))
        self.assertNotIn('서울', json.dumps(summary, ensure_ascii=False))
        deleted = caps.execute('forget_record', {'action': 'delete', 'receipt': forgot['receipt']})
        self.assertIs(evidence_summary('forget_record', deleted)['can_undo'], False)
        listed = caps.execute('forget_record', {'action': 'list'})
        self.assertNotIn('can_undo', evidence_summary('forget_record', listed))


if __name__ == '__main__':
    unittest.main()
