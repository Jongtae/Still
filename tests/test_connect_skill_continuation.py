"""#1299: real skill acquisition/settings/queue with fake GitHub and model wires."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from personal_agent import owner_mcp
from personal_agent.agent_runtime import DEFINITIONS
from personal_agent.quickstart_service import AgentService
from personal_agent.settings_orchestrator import SettingsError
from personal_agent.skills import SkillError, SkillLibrary
from test_owner_settings_conversation import _Case, call
from test_agency_loop import finish, judgments
from test_skill_supply import ADDRESS, COMMIT, FakeGitHub, UPSTREAM, tarball, upstream_files


class SkillContinuation(_Case):
    def setUp(self):
        super().setUp()
        self.github = FakeGitHub({COMMIT: tarball(UPSTREAM, COMMIT, upstream_files())})
        self.library = SkillLibrary(self.store, transport=self.github)
        self.service.skill_library = lambda: self.library
        self.job = self.store.job(self.store.enqueue('Prepare the requested project update', 'original'))

    def status(self, state):
        with self.store.db() as db:
            db.execute('UPDATE jobs SET status=? WHERE id=?', (state, self.job['id']))

    def propose(self, setting='add', value=ADDRESS, **kwargs):
        if setting == 'enabled' and value == 'on' and 'required_skill' not in kwargs:
            entry = self.library.bundled()[0]
            kwargs['required_skill'] = entry['skill'] + '@' + entry['digest']
        result = self.service.settings_tools(self.job)('settings_change', {
            'category': 'skills', 'setting': setting, 'value': value, 'continue_work': 'yes', **kwargs})
        self.status('partial')
        return result

    def confirm(self, result):
        row = self.settings._drafts()[result['draft_id']]
        return self.settings.confirm(self.service.settings_owner(self.job), self.job['channel'], row['id'], row['digest'])

    def receipts(self):
        return [e['trace'] for e in self.store.task_events(self.job['id']) if e['tool'] == 'settings_continuation']

    def test_install_is_confirmed_and_resumes_once_with_fresh_binding(self):
        self.library.set_enabled(True)
        original = self.service.skill_binding()
        before = original.refs()
        draft = self.propose()
        self.assertEqual(self.github.urls, [], 'pin already exact; no acquisition before owner confirmation')
        self.assertTrue(draft['continues_work'])
        self.assertIn('원래 작업', draft['note'])
        self.confirm(draft)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        current = self.service.skill_binding()
        self.assertEqual(original.refs(), before, 'a running snapshot never changes')
        self.assertGreater(len(current.refs()), len(before))
        added = next(key for key in current.entries if 'internal-comms' in key)
        with self.assertRaises(SkillError):
            original.load(added)
        self.assertFalse(current.loaded, 'readiness never injects skill text')
        self.assertIn('instructions', current.load(added))
        self.service.resume_settings_work()
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(self.store.job(self.job['id'])['message'], self.job['message'])

    def test_setup_only_does_not_resume(self):
        draft = self.propose(continue_work="no")
        self.status('succeeded')
        self.confirm(draft)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'succeeded')
        self.assertFalse(self.receipts())

    def test_enabled_existing_skills_and_restart_resume_without_acquisition(self):
        draft = self.propose('enabled', 'on')
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(draft)
        restarted = AgentService(self.store)
        restarted.resume_settings_work()
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(self.github.urls, [])

    def test_apply_during_run_waits_for_original_attempt_to_settle(self):
        draft = self.propose()
        self.status('running')
        self.confirm(draft)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'running')
        self.status('partial')
        self.service.resume_settings_work()
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')

    def test_enabled_wait_needs_exact_required_skill_in_bounded_catalogue(self):
        self.library.install(ADDRESS)
        entry = next(row for row in self.library.summary() if 'internal-comms' in row['skill'])
        required = entry['skill'] + '@' + entry['digest']
        draft = self.propose('enabled', 'on', required_skill=required)
        # Installed but no longer offered in the fresh bounded view. Bundled entries still exist.
        with mock.patch('personal_agent.skills.CATALOGUE_ENTRIES', len(self.library.bundled())):
            self.confirm(draft)
        self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertFalse(self.receipts()[-1]['resumed'])

    def test_enabled_continuation_requires_current_exact_reference(self):
        for value in (None, 'not-present@' + 'a' * 64):
            with self.subTest(value=value), self.assertRaises(SettingsError):
                self.propose('enabled', 'on', required_skill=value)

    def test_failed_acquisition_never_means_ready(self):
        draft = self.propose()
        self.github.offline = True
        with self.assertRaises(SettingsError):
            self.confirm(draft)
        self.service.resume_settings_work()
        self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertFalse(self.library.installed())

    def test_pending_cancel_and_expiry_are_terminal(self):
        for action in ('cancel', 'expire'):
            with self.subTest(action=action):
                draft = self.propose()
                self.service.resume_settings_work()
                self.assertEqual(self.store.job(self.job['id'])['status'], 'awaiting_connection')
                if action == 'cancel':
                    self.settings.cancel(self.service.settings_owner(self.job), self.job['channel'], draft['draft_id'])
                else:
                    self.clock[0] += self.settings.TTL_SECONDS + 1
                self.service.resume_settings_work()
                self.assertEqual(self.store.job(self.job['id'])['status'], 'partial')
                self.assertFalse(self.library.installed())

    def test_owner_explicit_renewal_preserves_original_continuation(self):
        self.propose()
        self.clock[0] += self.settings.TTL_SECONDS + 1
        self.service.resume_settings_work()
        self.service.work_settings_draft(self.job['id'], {'action': 'confirm'})
        # The owner web callback supplies a notifier, so slow acquisition runs in the existing worker.
        import time
        end = time.monotonic() + 5
        while self.store.job(self.job['id'])['status'] != 'queued' and time.monotonic() < end:
            time.sleep(.01)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(sum(row['resumed'] for row in self.receipts()), 1)

    def test_disabled_removed_or_tampered_content_is_not_ready(self):
        draft = self.propose()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(draft)
        row = self.settings._drafts()[draft['draft_id']]
        self.assertTrue(self.service.settings_skill_ready(row))
        self.library.set_enabled(False)
        self.assertFalse(self.service.settings_skill_ready(row))
        self.library.set_enabled(True)
        entry = next(e for e in self.library.catalogue() if e['name'] == 'internal-comms')
        (self.library.folder(entry) / 'SKILL.md').write_text('tampered')
        self.assertFalse(self.service.settings_skill_ready(row))
        self.library.remove(entry['package'])
        self.assertFalse(self.service.settings_skill_ready(row))
        self.service.resume_settings_work()
        self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')

    def test_cancelled_work_does_not_resume(self):
        draft = self.propose()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(draft)
        self.status('cancelled')
        self.service.resume_settings_work()
        self.assertEqual(self.store.job(self.job['id'])['status'], 'cancelled')
        self.assertFalse(self.receipts()[-1]['resumed'])

    def test_unknown_effect_and_unsettled_settings_call_block_resume(self):
        for detail, tool in (({'effect':'unknown'}, 'connector_read'),
                             ({'host_action':'settings_change','scope':'main','call_id':'unsettled','attempt':1}, 'settings_change')):
            with self.subTest(tool=tool):
                self.job = self.store.job(self.store.enqueue('Continue the requested result', 'unknown-' + tool))
                draft = self.propose('enabled', 'on')
                with mock.patch.object(self.service, 'resume_settings_work'):
                    self.confirm(draft)
                with self.store.db() as db:
                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,0)',
                               (self.job['id'], tool, 'running', json.dumps(detail)))
                self.service.resume_settings_work()
                self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')
                self.library.set_enabled(False)

    def test_mixed_prerequisites_wait_for_both(self):
        draft = self.propose()
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        self.service.connector_permission(self.job, {'tool_name': 'mcp__notes__list_notes', 'engine': 'codex'})
        other = self.service.settings_tools(self.job)('settings_change', {
            'category': 'ai_connections', 'setting': 'review', 'value': 'codex|owner-mcp|notes|list_notes|read'})
        self.confirm(draft)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'awaiting_connection')
        self.confirm(other)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(len(self.receipts()[0]['draft_ids']), 2)

    def test_duplicate_process_callbacks_enqueue_once(self):
        draft = self.propose()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(draft)
        services = [AgentService(self.store) for _ in range(3)]
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda s: s.resume_settings_work(), services))
        self.assertEqual(sum(row['resumed'] for row in self.receipts()), 1)

    def test_cancellation_winning_admission_cannot_be_overwritten(self):
        draft = self.propose()
        rival = AgentService(self.store).settings_orchestrator
        settle = self.settings._settle
        def cancel_first(ref, state, *args, **kwargs):
            if state == 'applying':
                rival.cancel(self.service.settings_owner(self.job), self.job['channel'], ref)
            return settle(ref, state, *args, **kwargs)
        with mock.patch.object(self.settings, '_settle', side_effect=cancel_first):
            with self.assertRaises(SettingsError):
                self.confirm(draft)
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'canceled')
        self.assertFalse(self.library.installed())
        self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')

    def test_confirmation_winning_cancel_cannot_be_reported_as_nothing_changed(self):
        draft = self.propose()
        rival_service = AgentService(self.store)
        rival_service.skill_library = lambda: self.library
        rival = rival_service.settings_orchestrator
        rival.now = self.settings.now
        settle = self.settings._settle
        def confirm_first(ref, state, *args, **kwargs):
            if state == 'canceled':
                row = rival._drafts()[ref]
                rival.confirm(row['owner'], row['channel'], ref, row['digest'])
            return settle(ref, state, *args, **kwargs)
        with mock.patch.object(self.settings, '_settle', side_effect=confirm_first):
            with self.assertRaises(SettingsError):
                self.settings.cancel(self.service.settings_owner(self.job), self.job['channel'], draft['draft_id'])
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'applied')
        self.assertTrue(self.library.installed())
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(sum(row['resumed'] for row in self.receipts()), 1)

    def test_new_draft_does_not_restore_another_stale_pending_snapshot(self):
        first = self.propose()
        rival = AgentService(self.store).settings_orchestrator
        put = self.settings._put_drafts
        def cancel_first(rows):
            rival.cancel(self.service.settings_owner(self.job), self.job['channel'], first['draft_id'])
            return put(rows)
        with mock.patch.object(self.settings, '_put_drafts', side_effect=cancel_first):
            self.propose('enabled', 'on')
        self.assertEqual(self.settings._drafts()[first['draft_id']]['state'], 'canceled')

    def test_flag_is_scoped_and_bound_to_the_exact_owner_draft(self):
        for setting, value, flag in (('remove', 'internal-comms', 'yes'), ('enabled', 'off', 'yes'), ('enabled', 'on', True)):
            with self.subTest(setting=setting, value=value), self.assertRaises(SettingsError):
                self.propose(setting, value, continue_work=flag)
        draft = self.propose()
        rows = self.settings._drafts()
        rows[draft['draft_id']]['continue_work'] = False
        self.settings._put_drafts(rows)
        with self.assertRaises(SettingsError):
            self.confirm(draft)
        schema = next(d['function']['parameters'] for d in DEFINITIONS if d['function']['name'] == 'settings_change')
        self.assertEqual(schema['properties']['continue_work']['enum'], ['yes', 'no'])

    def test_resumed_worker_delivers_substantive_original_result(self):
        self.service.save_model({'provider': 'compatible', 'endpoint': 'https://example.test/v1', 'model': 'test-model', 'api_key': 'k'})
        self.assertTrue(self.service.test_model()['ok'])
        self.script = [{'tool_calls': [call('draft', 'settings_change', category='skills', setting='add', value=ADDRESS, continue_work="yes")]},
                       {'content': 'I need your confirmation to add the writing method before preparing the update.'}]
        self.assertTrue(self.service.run_one())
        [row] = self.settings.pending_for_work(self.job['id'])
        self.confirm({'draft_id': row['id']})
        binding = self.service.skill_binding()
        skill = next(key for key in binding.entries if 'internal-comms' in key)
        self.service.decision_judge = judgments(True)
        self.script = [{'tool_calls': [call('load', 'skill_load', skill=skill)]},
                       finish('done', 'load', summary='Project update: the release checklist is ready. The remaining dependency is approval of the draft schedule.')]
        self.assertTrue(self.service.run_one())
        result = self.store.job(self.job['id'])
        self.assertEqual(result['status'], 'succeeded')
        self.assertIn('remaining dependency', result['response'])
        self.assertEqual(len(self.store.jobs()), 1, 'no second owner request')
        events = self.store.task_events(self.job['id'])
        self.assertTrue(any(e['tool'] == 'skill_load' and e['status'] == 'succeeded' for e in events))
