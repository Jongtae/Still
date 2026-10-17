"""The selected local grant and original continuation share one SQLite commit."""
from unittest.mock import patch

from test_contextual_capability_handoff import HandoffTestCase, GENERATION
from personal_agent.conversation_handoff import (
    LOCAL_FOLDER_READ, LOCAL_REFERENCE_READ, LOCAL_RESULT_WRITE,
    LOCAL_PENDING_KEY, ConversationHandoffError,
)
from personal_agent.quickstart_service import LOCAL_RESUMED_KEY
from personal_agent.agent_runtime import WORK_STOP_KEY
from personal_agent.file_workspace import FileWorkspace


class LocalApprovalCommit(HandoffTestCase):
    def selected(self, key=LOCAL_FOLDER_READ):
        work=self.store.enqueue('Read the selected material',key,f'telegram:{GENERATION}',505)
        self.service.park_for_local_authority(self.store.job(work),key)
        row=self.service.resume_index.record(key,work_id=work)
        folder=self.folder(key)
        self.service.select_local_folder({'handoff_id':row['handoff_id'],'path':str(folder)})
        return work,row,folder

    def assert_no_grant(self):
        self.assertEqual(self.store.config('file_roots',[]),[])
        self.assertEqual(self.store.config('file_workspace',{}),{})

    def test_failure_after_grant_before_marker_rolls_back_every_kind(self):
        for key in (LOCAL_FOLDER_READ,LOCAL_REFERENCE_READ,LOCAL_RESULT_WRITE):
            with self.subTest(key=key):
                work,row,_=self.selected(key)
                self.store.put('document_sharing',{'original':True})
                with patch.object(self.service,'_remember_work',side_effect=RuntimeError('crash before commit')):
                    with self.assertRaises(RuntimeError):
                        self.service.approve_local_folder({'handoff_id':row['handoff_id']})
                self.assert_no_grant()
                self.assertEqual(self.store.config('document_sharing'),{'original':True})
                self.assertEqual(self.store.job(work)['status'],'awaiting_connection')
                self.assertNotIn(work,self.store.config(LOCAL_RESUMED_KEY,[]))
                self.assertIsNotNone(self.service.resume_index.record(key,work_id=work))

    def test_failure_at_queue_does_not_publish_a_grant(self):
        work,row,_=self.selected()
        with patch.object(self.service,'_schedule_resumed_work',side_effect=RuntimeError('crash at queue')):
            with self.assertRaises(RuntimeError):
                self.service.approve_local_folder({'handoff_id':row['handoff_id']})
        self.assert_no_grant()
        self.assertEqual(self.store.job(work)['status'],'awaiting_connection')

    def test_committed_queue_survives_lost_claim_completion_for_all_kinds(self):
        for key in (LOCAL_FOLDER_READ,LOCAL_REFERENCE_READ,LOCAL_RESULT_WRITE):
            with self.subTest(key=key):
                work,row,folder=self.selected(key)
                with patch.object(self.service.local_handoff.pending,'complete',side_effect=RuntimeError('lost completion')):
                    with self.assertRaises(RuntimeError):
                        self.service.approve_local_folder({'handoff_id':row['handoff_id']})
                self.service=self.make_service()
                self.assertEqual(self.store.job(work)['status'],'queued')
                self.assertIn(work,self.store.config(LOCAL_RESUMED_KEY,[]))
                if key==LOCAL_FOLDER_READ:self.assertIn(str(folder.resolve()),self.roots())
                elif key==LOCAL_REFERENCE_READ:
                    self.assertIn(str(folder.resolve()),[r['path'] for r in self.store.config('file_workspace')['references']])
                else:self.assertEqual(self.store.config('file_workspace')['workspace'],str(folder.resolve()))
                # Terminal cleanup finishes the retained claim without scheduling again.
                with self.store.db() as db:
                    db.execute("UPDATE jobs SET status='succeeded',response='Original result' WHERE id=?",(work,))
                self.service.settle_native_handoffs()
                self.assertIsNone(self.service.resume_index.record(key,work_id=work))
        self.assertEqual({r['state'] for r in self.store.secret(LOCAL_PENDING_KEY).values()},{'completed'})

    def test_lost_completion_still_reaches_original_substantive_result_after_restart(self):
        work,_=self.park_read()
        folder=self.folder('contracts',{'renewal.txt':'계약서 갱신일: 10월 1일'})
        row=self.pending()[0]
        self.service.select_local_folder({'handoff_id':row['handoff_id'],'path':str(folder)})
        with patch.object(self.service.local_handoff.pending,'complete',side_effect=RuntimeError('lost completion')):
            with self.assertRaises(RuntimeError):self.service.approve_local_folder({'handoff_id':row['handoff_id']})
        self.service=self.make_service()
        self.plan=[('find_files',{'query':'계약서'})]
        self.claim_completion()
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(work)['status'],'succeeded')
        self.assertEqual(self.store.job(work)['response'],self.text)
        self.assertFalse(self.service.run_one())
        self.assertEqual(len(self.store.jobs()),1)
        self.assertIsNone(self.service.resume_index.record(LOCAL_FOLDER_READ,work_id=work))

    def test_cancellation_winning_queue_cas_cannot_publish_a_grant(self):
        work,row,_=self.selected()
        original=self.service.local_handoff.pending.claim
        def cancel_then_claim(*args,**kwargs):
            reference=original(*args,**kwargs)
            with self.store.db() as db:db.execute("UPDATE jobs SET status='cancelled' WHERE id=?",(work,))
            return reference
        with patch.object(self.service.local_handoff.pending,'claim',side_effect=cancel_then_claim):
            with self.assertRaises(ConversationHandoffError) as error:
                self.service.approve_local_folder({'handoff_id':row['handoff_id']})
        self.assertEqual(error.exception.reason,'work_already_completed')
        self.assertFalse(any('허용했습니다' in text for text in self.sent))
        self.assertEqual(self.store.job(work)['status'],'cancelled')
        self.assert_no_grant()
        self.assertNotIn(work,self.store.config(LOCAL_RESUMED_KEY,[]))

    def test_pairing_change_after_claim_cannot_publish_a_grant(self):
        work,row,_=self.selected()
        original=self.service.local_handoff.pending.claim
        def replace_then_claim(*args,**kwargs):
            reference=original(*args,**kwargs)
            self.store.put('telegram',{'enabled':True,'user_id':505,'generation':'replacement'})
            return reference
        with patch.object(self.service.local_handoff.pending,'claim',side_effect=replace_then_claim):
            with self.assertRaises(ConversationHandoffError):
                self.service.approve_local_folder({'handoff_id':row['handoff_id']})
        self.assert_no_grant()
        self.assertEqual(self.store.job(work)['status'],'failed')
        self.assertEqual(self.sent,[])

    def test_stop_after_claim_cannot_publish_a_grant(self):
        work,row,_=self.selected()
        original=self.service.local_handoff.pending.claim
        def stop_then_claim(*args,**kwargs):
            reference=original(*args,**kwargs)
            self.store.put(WORK_STOP_KEY,[work])
            return reference
        with patch.object(self.service.local_handoff.pending,'claim',side_effect=stop_then_claim):
            with self.assertRaises(ConversationHandoffError):
                self.service.approve_local_folder({'handoff_id':row['handoff_id']})
        self.assert_no_grant()
        self.assertEqual(self.store.job(work)['status'],'failed')
        self.assertIsNone(self.service.resume_index.record(LOCAL_FOLDER_READ,work_id=work))

    def test_exact_approval_never_queues_a_sibling_wait(self):
        work,row,_=self.selected()
        sibling=self.store.enqueue('Separate request','sibling',f'telegram:{GENERATION}',505)
        self.service.park_for_local_authority(self.store.job(sibling),LOCAL_FOLDER_READ)
        result=self.service.approve_local_folder({'handoff_id':row['handoff_id']})
        self.assertTrue(result['scheduled'])
        self.assertEqual(self.store.job(work)['status'],'queued')
        self.assertEqual(self.store.job(sibling)['status'],'awaiting_connection')
        self.assertEqual(self.store.config(LOCAL_RESUMED_KEY),[work])
        with self.assertRaises(ConversationHandoffError):
            self.service.approve_local_folder({'handoff_id':row['handoff_id']})

    def test_result_folder_selected_in_settings_during_claim_is_kept(self):
        work,row,selected=self.selected(LOCAL_RESULT_WRITE)
        current=self.folder('settings-result')
        original=self.service.local_handoff.pending.claim
        def set_then_claim(*args,**kwargs):
            reference=original(*args,**kwargs)
            FileWorkspace(self.store).plan_grant('workspace',str(current))[1]()
            return reference
        with patch.object(self.service.local_handoff.pending,'claim',side_effect=set_then_claim):
            result=self.service.approve_local_folder({'handoff_id':row['handoff_id']})
        self.assertTrue(result['kept_existing'])
        self.assertEqual(self.store.config('file_workspace')['workspace'],str(current.resolve()))
        self.assertNotEqual(str(current.resolve()),str(selected.resolve()))
        self.assertEqual(self.store.job(work)['status'],'queued')

    def test_reference_overlap_introduced_during_claim_rolls_back_queue(self):
        work,row,folder=self.selected(LOCAL_REFERENCE_READ)
        original=self.service.local_handoff.pending.claim
        def set_then_claim(*args,**kwargs):
            reference=original(*args,**kwargs)
            FileWorkspace(self.store).plan_grant('workspace',str(folder))[1]()
            return reference
        with patch.object(self.service.local_handoff.pending,'claim',side_effect=set_then_claim):
            with self.assertRaises(ValueError):
                self.service.approve_local_folder({'handoff_id':row['handoff_id']})
        self.assertEqual(self.store.config('file_workspace')['references'],[])
        self.assertEqual(self.store.job(work)['status'],'awaiting_connection')
        self.assertNotIn(work,self.store.config(LOCAL_RESUMED_KEY,[]))
