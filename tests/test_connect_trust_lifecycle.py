"""Trust lifecycle at the real SQLite/settings boundary, without external accounts."""
import tempfile
import unittest
import json
from unittest import mock
from pathlib import Path

from personal_agent import trust_record as trust
from personal_agent.agent_runtime import MEMORY_OWNER
from personal_agent.owner_forget import OwnerForget, ForgetError, UNDO_SECONDS
from personal_agent.quickstart_store import QuickStore


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'state')
        self.target = trust.connection('codex', 'owner-mcp', 'notes')
        self.ref = 'trust:codex|owner-mcp|notes'
        self.now = 1000
        self.forget = OwnerForget(self.store, clock=lambda: self.now)
        trust.set_operation(self.store, self.target, 'list_notes', 'read')

    def test_undo_restores_money_and_rung_together_and_rejects_later_revision(self):
        trust.set_rung(self.store, self.target, 'mandate')
        marked = trust.set_money(self.store, self.target, True)
        change = marked['revision']
        restored = trust.undo(self.store, self.target, change)
        self.assertFalse(restored['money'])
        self.assertEqual(restored['rung'], 'mandate')
        self.assertNotEqual(restored['revision'], change)
        with self.assertRaises(ValueError):
            trust.undo(self.store, self.target, change)
        with self.assertRaises(ValueError):
            trust.undo(self.store, trust.connection('claude-code', 'owner-mcp', 'notes'), change)

    def test_success_history_rotation_does_not_erase_current_exact_undo(self):
        marked = trust.set_money(self.store, self.target, True)
        for index in range(trust.MAX_HISTORY + 2):
            trust.observe(self.store, self.target, 'list_notes', 'succeeded', f'work-{index}')
        record = trust.hand(self.store, self.target)
        self.assertLessEqual(len(record['history']), trust.MAX_HISTORY)
        restored = trust.undo(self.store, self.target, marked['revision'])
        self.assertFalse(restored['money'])
        self.assertEqual(restored['rung'], 'approve')

    def test_forget_survives_restart_and_never_falls_back_to_seed(self):
        receipt = self.forget.forget(MEMORY_OWNER, self.ref)
        restarted = QuickStore(self.store.root)
        self.assertFalse(trust.is_read(restarted, self.target, 'list_notes', {'list_notes'}))
        self.assertFalse(trust.readable(restarted, self.target, {'list_notes'}))
        self.assertEqual(trust.hand(restarted, self.target)['operations'], {})
        with self.assertRaises(ForgetError):
            self.forget.undo(MEMORY_OWNER, receipt['receipt'])
        with self.assertRaises(ForgetError):
            self.forget.undo('another-owner', receipt['receipt'], confirmed_authority=True)
        self.forget.undo(MEMORY_OWNER, receipt['receipt'], confirmed_authority=True)
        self.assertTrue(trust.is_read(restarted, self.target, 'list_notes'))

    def test_purge_removes_payload_but_retains_revocation(self):
        receipt = self.forget.forget(MEMORY_OWNER, self.ref)
        self.now += UNDO_SECONDS
        self.assertEqual(self.forget.purge_due(), 1)
        raw = self.store.config(trust.KEY)['hands']['codex|owner-mcp|notes']
        self.assertNotIn('operations', raw)
        self.assertNotIn('history', raw)
        self.assertFalse(trust.is_read(self.store, self.target, 'list_notes', {'list_notes'}))
        with self.assertRaises(ForgetError):
            self.forget.undo(MEMORY_OWNER, receipt['receipt'], confirmed_authority=True)

    def test_later_explicit_decision_does_not_revive_old_permissions_or_allow_restore(self):
        receipt = self.forget.forget(MEMORY_OWNER, self.ref)
        trust.set_operation(self.store, self.target, 'read_public', 'read')
        self.assertTrue(trust.is_read(self.store, self.target, 'read_public'))
        self.assertFalse(trust.is_read(self.store, self.target, 'list_notes', {'list_notes'}))
        with self.assertRaises(ForgetError):
            self.forget.undo(MEMORY_OWNER, receipt['receipt'], confirmed_authority=True)
        self.now += UNDO_SECONDS
        self.forget.purge_due()
        self.assertTrue(trust.is_read(self.store, self.target, 'read_public'))

    def test_observations_never_raise_or_reactivate_and_keep_no_payload(self):
        revision = trust.hand(self.store, self.target)['revision']
        trust.observe(self.store, self.target, 'list_notes', 'succeeded', 'work-1')
        self.assertEqual(trust.hand(self.store, self.target)['revision'], revision)
        failed = trust.observe(self.store, self.target, 'list_notes', 'failed', 'work-2')
        self.assertEqual(failed['rung'], 'propose')
        self.forget.forget(MEMORY_OWNER, self.ref)
        trust.observe(self.store, self.target, 'list_notes', 'succeeded', 'work-3')
        trust.lower(self.store, self.target, 'failure')
        self.assertFalse(trust.is_read(self.store, self.target, 'list_notes'))

    def test_undo_new_decision_after_forget_restores_revocation_not_seed_access(self):
        self.forget.forget(MEMORY_OWNER, self.ref)
        fresh = trust.set_operation(self.store, self.target, 'new_read', 'read')
        trust.undo(self.store, self.target, fresh['revision'])
        self.assertEqual(trust.hand(self.store, self.target)['state'], 'forgotten')
        self.assertFalse(trust.is_read(self.store, self.target, 'list_notes', {'list_notes'}))
        self.assertNotIn('operations', self.store.config(trust.KEY)['hands']['codex|owner-mcp|notes'])

    def service(self):
        from personal_agent.quickstart_service import AgentService
        service = AgentService(self.store)
        service.owner_mcp_available = lambda: {'codex': ['notes'], 'claude-code': []}
        return service

    def change(self, service, setting, value):
        settings = service.settings_orchestrator
        draft = settings.propose('local-owner', 'web', 'ai_connections', setting, value)
        return settings.confirm('local-owner', 'web', draft['draft_id'], draft['digest'])

    def test_settings_undo_shows_full_prior_state_and_confirms_once(self):
        service = self.service()
        self.change(service, 'rung', 'codex|owner-mcp|notes|mandate')
        self.change(service, 'money', 'codex|owner-mcp|notes|on')
        revision = trust.hand(self.store, self.target)['revision']
        settings = service.settings_orchestrator
        draft = settings.propose('local-owner', 'web', 'ai_connections', 'undo', 'codex|owner-mcp|notes|' + revision)
        self.assertIn('위임 범위', draft['summary'])
        self.assertTrue(trust.hand(self.store, self.target)['money'])
        settings.confirm('local-owner', 'web', draft['draft_id'], draft['digest'])
        self.assertFalse(trust.hand(self.store, self.target)['money'])
        self.assertEqual(trust.hand(self.store, self.target)['rung'], 'mandate')

    def test_forget_cancels_pending_approval_and_restore_does_not_revive_it(self):
        from personal_agent.settings_orchestrator import SettingsError
        service = self.service()
        settings = service.settings_orchestrator
        pending = settings.propose('local-owner', 'web', 'ai_connections', 'review', 'codex|owner-mcp|notes|read_secret|read')
        self.change(service, 'forget', 'codex|owner-mcp|notes')
        self.assertEqual(settings._drafts()[pending['draft_id']]['state'], 'cancelled')
        offered = settings.read('local-owner', 'ai_connections')['settings']['ai_connections']['restore']['options']
        self.change(service, 'restore', offered[0]['value'])
        with self.assertRaises(SettingsError):
            settings.confirm('local-owner', 'web', pending['draft_id'], pending['digest'])
        self.assertFalse(trust.is_read(self.store, self.target, 'read_secret'))
        self.assertTrue(trust.is_read(self.store, self.target, 'list_notes'))

    def test_authority_change_between_check_and_set_is_rejected_atomically(self):
        from personal_agent.settings_orchestrator import SettingsError
        service = self.service()
        original = trust.set_operation
        def raced(*args, **kwargs):
            trust.lower(self.store, self.target, 'failure')
            return original(*args, **kwargs)
        with mock.patch.object(trust, 'set_operation', side_effect=raced), self.assertRaises(SettingsError):
            self.change(service, 'review', 'codex|owner-mcp|notes|read_secret|read')
        self.assertFalse(trust.is_read(self.store, self.target, 'read_secret'))

    def test_other_read_grants_commute_but_failures_corrections_and_money_changes_do_not(self):
        record = trust.hand(self.store, self.target)
        trust.set_operation(self.store, self.target, 'other_read', 'read')
        accepted = trust.set_operation(self.store, self.target, 'new_read', 'read', expected_revision=record['revision'])
        self.assertEqual(accepted['operations']['new_read'], 'read')
        for change in (lambda: trust.lower(self.store, self.target, 'failure'),
                       lambda: trust.set_money(self.store, self.target, True),
                       lambda: trust.set_operation(self.store, self.target, 'other_read', 'refused')):
            before = trust.hand(self.store, self.target)['revision']
            change()
            with self.assertRaises(ValueError):
                trust.set_operation(self.store, self.target, 'late_read', 'read', expected_revision=before)

    def test_forget_then_restore_cannot_resume_an_already_approved_old_wait(self):
        from personal_agent import owner_mcp
        service = self.service()
        service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        job = self.store.job(self.store.enqueue('Read other notes', 'old-wait'))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
        service.connector_permission(job, {'engine': 'codex', 'tool_name': 'mcp__notes__read_secret', 'input': {}})
        draft = service.settings_tools(job)('settings_change', {'category': 'ai_connections', 'setting': 'review',
                                                               'value': 'codex|owner-mcp|notes|read_secret|read'})
        settings = service.settings_orchestrator
        pending = settings._drafts()[draft['draft_id']]
        settings.confirm(service.settings_owner(job), job['channel'], pending['id'], pending['digest'])
        self.assertEqual(settings._drafts()[pending['id']]['state'], 'applied')
        self.change(service, 'forget', 'codex|owner-mcp|notes')
        offered = settings.read('local-owner', 'ai_connections')['settings']['ai_connections']['restore']['options']
        self.change(service, 'restore', offered[0]['value'])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (job['id'],))
        service.resume_settings_work()
        self.assertEqual(self.store.job(job['id'])['status'], 'partial')
        self.assertEqual(settings._drafts()[pending['id']]['state'], 'cancelled')

    def test_owner_correction_lowers_tells_and_exact_undo_restores_decision_and_rung(self):
        service = self.service()
        result = self.change(service, 'review', 'codex|owner-mcp|notes|list_notes|refused')
        self.assertIn('교정', result['response'])
        corrected = trust.hand(self.store, self.target)
        self.assertEqual(corrected['rung'], 'propose')
        self.assertTrue(corrected['history'][-1]['owner_correction'])
        self.change(service, 'undo', 'codex|owner-mcp|notes|' + corrected['revision'])
        self.assertTrue(trust.is_read(self.store, self.target, 'list_notes'))
        self.assertEqual(trust.hand(self.store, self.target)['rung'], 'approve')

    def test_forget_during_apply_survives_late_settlement_and_stale_draft_writes(self):
        from personal_agent import owner_mcp
        service = self.service()
        service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        job = self.store.job(self.store.enqueue('Read other notes', 'in-flight'))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
        service.connector_permission(job, {'engine': 'codex', 'tool_name': 'mcp__notes__read_secret', 'input': {}})
        draft = service.settings_tools(job)('settings_change', {'category': 'ai_connections', 'setting': 'review',
                                                               'value': 'codex|owner-mcp|notes|read_secret|read'})
        settings = service.settings_orchestrator
        pending = settings._drafts()[draft['draft_id']]
        apply = service.apply_trust_setting
        def forget_before_settlement(*args, **kwargs):
            result = apply(*args, **kwargs)
            self.assertEqual(settings._drafts()[pending['id']]['state'], 'applying')
            stale = settings._drafts()
            OwnerForget(self.store).forget(MEMORY_OWNER, self.ref)
            settings._put_drafts(stale)
            return result
        with mock.patch.object(service, 'apply_trust_setting', side_effect=forget_before_settlement):
            result = settings.confirm(service.settings_owner(job), job['channel'], pending['id'], pending['digest'])
        self.assertEqual(result['state'], 'cancelled')
        offered = settings.read('local-owner', 'ai_connections')['settings']['ai_connections']['restore']['options']
        self.change(service, 'restore', offered[0]['value'])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (job['id'],))
        service.resume_settings_work()
        self.assertEqual(self.store.job(job['id'])['status'], 'partial')
        self.assertTrue(settings._drafts()[pending['id']]['trust_revoked'])

    def test_full_history_cannot_bypass_undecided_call_shutdown(self):
        from personal_agent import owner_mcp
        service = self.service()
        service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        for index in range(trust.MAX_HANDS - 1):
            trust.set_operation(self.store, trust.connection('codex', 'owner-mcp', f'hand_{index}'), 'read', 'read')
        job = self.store.job(self.store.enqueue('Read observations', 'full-history'))
        with mock.patch.object(service, 'reviewed_connector_reads', return_value={'other': {'read'}}):
            allowed = service.connector_permission(job, {'engine': 'codex', 'tool_name': 'mcp__claude_ai_other__read', 'input': {}})
        self.assertEqual(allowed['behavior'], 'allow')
        result = service.record_connector_calls(job['id'], [
            {'tool': 'mcp__claude_ai_other__read', 'status': 'succeeded'},
            {'tool': 'mcp__notes__unapproved_action', 'status': 'succeeded'}], engine='codex')
        self.assertEqual(len(result), 1)
        self.assertEqual(owner_mcp.confirmed(self.store, 'codex'), [])

    def test_forget_restore_before_draft_insert_does_not_satisfy_old_wait(self):
        from personal_agent import owner_mcp
        service = self.service()
        service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        job = self.store.job(self.store.enqueue('Read other notes', 'late-draft'))
        service.connector_permission(job, {'engine': 'codex', 'tool_name': 'mcp__notes__read_secret', 'input': {}})
        settings = service.settings_orchestrator
        put = settings._put_drafts
        def forget_before_insert(rows):
            # Another Work satisfied the shared prerequisite, then the owner revoked/restored it,
            # before this Work's already-proposed wait reached durable storage.
            trust.set_operation(self.store, self.target, 'read_secret', 'read')
            lifecycle = OwnerForget(self.store)
            receipt = lifecycle.forget(MEMORY_OWNER, self.ref)
            lifecycle.undo(MEMORY_OWNER, receipt['receipt'], confirmed_authority=True)
            return put(rows)
        with mock.patch.object(settings, '_put_drafts', side_effect=forget_before_insert):
            draft = service.settings_tools(job)('settings_change', {'category': 'ai_connections', 'setting': 'review',
                                                                   'value': 'codex|owner-mcp|notes|read_secret|read'})
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (job['id'],))
        service.resume_settings_work()
        self.assertNotEqual(self.store.job(job['id'])['status'], 'queued')
        self.assertEqual(settings._drafts()[draft['draft_id']]['state'], 'cancelled')

    def test_restore_listing_survives_newer_memory_and_stale_trust_receipts(self):
        service = self.service()
        self.change(service, 'forget', 'codex|owner-mcp|notes')
        lifecycle = OwnerForget(self.store)
        for index in range(20):
            memory = self.store.save_memory(f'key-{index}', f'value-{index}', MEMORY_OWNER, work_id='source')
            lifecycle.forget(MEMORY_OWNER, 'memory:' + memory['id'])
        other = trust.connection('codex', 'owner-mcp', 'other')
        for _index in range(35):
            trust.set_operation(self.store, other, 'read', 'read')
            lifecycle.forget(MEMORY_OWNER, 'trust:codex|owner-mcp|other')
        trust.set_operation(self.store, other, 'read', 'read')
        offered = service.settings_orchestrator.read('local-owner', 'ai_connections')['settings']['ai_connections']['restore']['options']
        self.assertEqual(len(offered), 1)
        self.change(service, 'restore', offered[0]['value'])
        self.assertTrue(trust.is_read(self.store, self.target, 'list_notes'))

    def test_delete_tool_receipt_discloses_retained_revocation_marker(self):
        from personal_agent.agent_runtime import Capabilities
        work = self.store.enqueue('Delete trust', 'delete-trust')
        caps = Capabilities(self.store, None, {}, '', work, lambda *args: None)
        result = caps.execute('forget_record', {'action': 'delete', 'ref': self.ref})
        self.assertEqual(result['state'], 'deleted')
        self.assertNotIn('완전히 지웠', result['message'])
        self.assertIn('표시', result['message'])
        self.assertFalse(trust.is_read(self.store, self.target, 'list_notes', {'list_notes'}))

    def test_observed_failure_is_told_from_work_evidence_without_result_payload(self):
        from personal_agent import owner_mcp
        from personal_agent.agent_runtime import work_disclosures
        service = self.service()
        service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        job = self.store.job(self.store.enqueue('Read notes', 'failure'))
        service.connector_permission(job, {'engine': 'codex', 'tool_name': 'mcp__notes__list_notes', 'input': {}})
        service.record_connector_calls(job['id'], [{'tool': 'mcp__notes__list_notes', 'status': 'failed',
                                                   'result': 'private-result-never-retained'}], engine='codex')
        with self.store.db() as db:
            events = db.execute('SELECT tool,status,detail FROM tool_events WHERE job_id=?', (job['id'],)).fetchall()
        self.assertTrue(any('신뢰 단계' in text for text in work_disclosures(events)))
        self.assertNotIn('private-result-never-retained', json.dumps(self.store.config(trust.KEY)))
        self.assertEqual(trust.hand(self.store, self.target)['history'][-1]['work_id'], job['id'])


if __name__ == '__main__':
    unittest.main()
