"""CONNECT-TRUST-01 (#1297): the owner's trust record replaces the developer-reviewed list as the authority.

Model-free: the real store, service and settings orchestrator; nothing reaches a model or a service.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import owner_mcp, trust_record
from personal_agent.quickstart_store import QuickStore
from personal_agent.settings_orchestrator import SettingsError

LIST = 'mcp__notes__list_notes'
SAVE = 'mcp__notes__save_note'
DRIVE_SEARCH = 'mcp__claude_ai_Google_Drive__search_files'


class TrustRecordTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'state')

    def test_starting_rungs_follow_the_money_marking(self):
        self.assertEqual(trust_record.hand(self.store, 'notes')['rung'], 'approve')
        broker = trust_record.connection('codex', 'owner-mcp', 'broker')
        trust_record.set_money(self.store, broker, True)
        self.assertEqual(trust_record.hand(self.store, broker)['rung'], 'report')
        with self.assertRaises(ValueError):
            trust_record.set_rung(self.store, broker, 'mandate')
        self.store.put(trust_record.KEY, {'hands': {'broker': {'money': True, 'rung': 'mandate'}}})
        self.assertEqual(trust_record.hand(self.store, 'broker')['rung'], 'approve', 'a stored mandate on a money hand is capped')

    def test_the_owner_decision_wins_over_the_seed_and_a_money_hand_ignores_the_seed(self):
        seed = frozenset({'search_files'})
        self.assertTrue(trust_record.is_read(self.store, 'Google Drive', 'search_files', seed))
        trust_record.set_operation(self.store, 'Google Drive', 'search_files', 'refused')
        self.assertFalse(trust_record.is_read(self.store, 'Google Drive', 'search_files', seed))
        broker = trust_record.connection('codex', 'owner-mcp', 'broker')
        trust_record.set_money(self.store, broker, True)
        self.assertFalse(trust_record.is_read(self.store, broker, 'balance', frozenset({'balance'})),
                         'on a money-capable hand only an owner-reviewed read is a read')
        trust_record.set_operation(self.store, broker, 'balance', 'read')
        self.assertTrue(trust_record.is_read(self.store, broker, 'balance'))
        self.assertTrue(trust_record.readable(self.store, broker))
        self.assertFalse(trust_record.readable(self.store, 'other'))

    def test_lowering_is_one_rung_and_recorded(self):
        trust_record.set_rung(self.store, 'notes', 'mandate')
        trust_record.lower(self.store, 'notes', 'owner correction')
        row = trust_record.hand(self.store, 'notes')
        self.assertEqual(row['rung'], 'approve')
        self.assertEqual(row['history'][-1]['change'], 'lowered')
        trust_record.set_rung(self.store, 'notes', 'report')
        self.assertEqual(trust_record.lower(self.store, 'notes', 'x')['rung'], 'report', 'nothing below report')

    def test_values_are_validated(self):
        for name, operation, cls in (('a|b', 'op', 'read'), ('ok', 'bad op', 'read'), ('ok', 'op', 'maybe'), ('', 'op', 'read')):
            with self.assertRaises(ValueError):
                trust_record.set_operation(self.store, name, operation, cls)
        self.store.put(trust_record.KEY, {'hands': {'notes': {'operations': {'list_notes': 'read', 'x y': 'read', 'z': 'admin'}}}})
        self.assertEqual(trust_record.hand(self.store, 'notes')['operations'], {'list_notes': 'read'})

    def test_same_display_name_does_not_cross_engine_or_connection_kind(self):
        codex_server = trust_record.connection('codex', 'owner-mcp', 'notes', 'notes')
        claude_server = trust_record.connection('claude-code', 'owner-mcp', 'notes', 'notes')
        claude_connector = trust_record.connection('claude-code', 'ai-connection', 'notes', 'notes')
        trust_record.set_operation(self.store, codex_server, 'list_notes', 'read')
        self.assertTrue(trust_record.is_read(self.store, codex_server, 'list_notes'))
        self.assertFalse(trust_record.is_read(self.store, claude_server, 'list_notes'))
        self.assertFalse(trust_record.is_read(self.store, claude_connector, 'list_notes'))

    def test_concurrent_authority_changes_do_not_restore_an_older_snapshot(self):
        from concurrent.futures import ThreadPoolExecutor
        target = trust_record.connection('codex', 'owner-mcp', 'notes')
        trust_record.set_operation(self.store, target, 'private_read', 'refused')
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda index: trust_record.set_operation(self.store, target, f'read_{index}', 'read'), range(24)))
        record = trust_record.hand(self.store, target)
        self.assertEqual(len(record['operations']), 25)
        self.assertEqual(record['operations']['private_read'], 'refused')


class DecisionTests(unittest.TestCase):
    def setUp(self):
        from personal_agent.quickstart_service import AgentService
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'state')
        self.service = AgentService(self.store)
        self.job = {'id': self.store.enqueue('notes', 'k1')}
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['notes', 'broker'])
        patcher = mock.patch.object(type(self.service), 'reviewed_connector_reads',
                                    staticmethod(lambda: {'Google Drive': frozenset({'search_files'}),
                                                          'broker': frozenset({'balance'})}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def decide(self, tool):
        engine = 'claude-code' if tool.startswith('mcp__claude_ai_') else 'codex'
        return self.service.connector_permission(self.job, {'tool_name': tool, 'input': {}, 'engine': engine})

    def test_an_undecided_operation_is_introduced_with_the_exact_value_to_propose(self):
        denied = self.decide(LIST)
        self.assertEqual(denied['behavior'], 'deny')
        self.assertIn('codex|owner-mcp|notes|list_notes|read', denied['message'])
        self.assertIn('소개', denied['message'])
        notes = trust_record.connection('codex', 'owner-mcp', 'notes')
        trust_record.set_operation(self.store, notes, 'list_notes', 'read')
        self.assertEqual(self.decide(LIST)['behavior'], 'allow', 'decided once, not asked again')
        trust_record.set_operation(self.store, notes, 'save_note', 'mutate')
        allowed = self.decide(SAVE)
        self.assertEqual(allowed['behavior'], 'allow', 'reviewed non-payment changes use pilot authority')
        self.assertIsNone(self.service._browser_request(self.job['id']))

    def test_the_seed_still_answers_for_a_non_money_hand_only(self):
        self.assertEqual(self.decide(DRIVE_SEARCH)['behavior'], 'allow')
        self.assertEqual(self.decide('mcp__broker__balance')['behavior'], 'allow')
        broker = trust_record.connection('codex', 'owner-mcp', 'broker')
        trust_record.set_money(self.store, broker, True)
        self.assertEqual(self.decide('mcp__broker__balance')['behavior'], 'deny')

    def test_a_call_that_ran_undecided_lowers_the_rung(self):
        notes = trust_record.connection('codex', 'owner-mcp', 'notes')
        trust_record.set_rung(self.store, notes, 'mandate')
        self.service.record_turn_worker(self.job['id'], {'engine': 'codex'})
        self.service.record_turn_provenance(self.job['id'], connector_reads=[{'tool': SAVE, 'status': 'succeeded'}])
        self.assertEqual(trust_record.hand(self.store, notes)['rung'], 'approve')


class ConversationTests(unittest.TestCase):
    def setUp(self):
        from personal_agent.quickstart_service import AgentService
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'state')
        self.service = AgentService(self.store)
        self.service.owner_mcp_available = lambda: {'claude-code': [], 'codex': ['notes', 'broker']}
        self.orchestrator = self.service.settings_orchestrator

    def change(self, setting, value):
        draft = self.orchestrator.propose('local-owner', 'web', 'ai_connections', setting, value)
        self.assertFalse(draft['applied'], 'a draft changes nothing')
        return draft, self.orchestrator.confirm('local-owner', 'web', draft['draft_id'], draft['digest'])

    def test_servers_are_confirmed_only_from_what_the_owner_configured(self):
        draft, _done = self.change('servers', 'codex|notes')
        self.assertIn('notes', draft['summary'])
        self.assertEqual(owner_mcp.confirmed(self.store, 'codex'), ['notes'])
        for bad in ('codex|unknown', 'other|notes', 'codex|claude_ai_x', 'notes'):
            with self.assertRaises(SettingsError):
                self.orchestrator.propose('local-owner', 'web', 'ai_connections', 'servers', bad)
        with self.assertRaises(SettingsError):
            self.orchestrator.propose('local-owner', 'web', 'ai_connections', 'servers', 'codex|notes')
        self.change('servers', 'codex|')
        self.assertEqual(owner_mcp.confirmed(self.store, 'codex'), [])

    def test_a_review_is_confirmed_once_and_an_undo_is_the_reverse_draft(self):
        draft, _done = self.change('review', 'codex|owner-mcp|notes|list_notes|read')
        self.assertIn("'읽기'", draft['summary'])
        notes = trust_record.connection('codex', 'owner-mcp', 'notes')
        self.assertEqual(trust_record.hand(self.store, notes)['operations'], {'list_notes': 'read'})
        with self.assertRaises(SettingsError):
            self.orchestrator.propose('local-owner', 'web', 'ai_connections', 'review', 'codex|owner-mcp|notes|list_notes|read')
        self.change('review', 'codex|owner-mcp|notes|list_notes|refused')
        self.assertFalse(trust_record.is_read(self.store, notes, 'list_notes'))
        read = self.service.conversation_settings_request({'operation': 'read', 'category': 'ai_connections'})
        self.assertIn('list_notes(막음)', read['response'])

    def test_conflicting_review_drafts_keep_the_actual_before_state(self):
        first = self.orchestrator.propose('local-owner', 'web', 'ai_connections', 'review',
                                          'codex|owner-mcp|notes|list_notes|read')
        second = self.orchestrator.propose('local-owner', 'web', 'ai_connections', 'review',
                                           'codex|owner-mcp|notes|list_notes|refused')
        self.orchestrator.confirm('local-owner', 'web', second['draft_id'], second['digest'])
        with self.assertRaises(SettingsError):
            self.orchestrator.confirm('local-owner', 'web', first['draft_id'], first['digest'])
        notes = trust_record.connection('codex', 'owner-mcp', 'notes')
        self.assertEqual(trust_record.hand(self.store, notes)['operations'], {'list_notes': 'refused'})

    def test_server_draft_cannot_overwrite_a_later_owner_choice(self):
        first = self.orchestrator.propose('local-owner', 'web', 'ai_connections', 'servers', 'codex|notes')
        second = self.orchestrator.propose('local-owner', 'web', 'ai_connections', 'servers', 'codex|broker')
        self.orchestrator.confirm('local-owner', 'web', second['draft_id'], second['digest'])
        with self.assertRaises(SettingsError):
            self.orchestrator.confirm('local-owner', 'web', first['draft_id'], first['digest'])
        self.assertEqual(owner_mcp.confirmed(self.store, 'codex'), ['broker'])

    def test_money_marking_and_rungs_are_owner_decisions(self):
        self.change('money', 'codex|owner-mcp|broker|on')
        broker = trust_record.connection('codex', 'owner-mcp', 'broker')
        self.assertEqual(trust_record.hand(self.store, broker)['rung'], 'report')
        with self.assertRaises(SettingsError):
            self.orchestrator.propose('local-owner', 'web', 'ai_connections', 'rung', 'codex|owner-mcp|broker|mandate')
        self.change('rung', 'codex|owner-mcp|broker|approve')
        self.assertEqual(trust_record.hand(self.store, broker)['rung'], 'approve')
        for bad in ('codex|owner-mcp|broker|maybe', 'broker', 'a|b|c'):
            with self.assertRaises(SettingsError):
                self.orchestrator.propose('local-owner', 'web', 'ai_connections', 'rung', bad)


class ContinuationTests(ConversationTests):
    """Actual draft / permission / jobs boundary, no model or remote service."""
    def blocked(self, key='original', operation='list_notes'):
        work = self.store.enqueue('Compare the notes and give me a summary', key)
        job = self.store.job(work)
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['notes'])
        self.service.connector_permission(job, {'tool_name': f'mcp__notes__{operation}', 'engine': 'codex', 'input': {}})
        draft = self.service.settings_tools(job)('settings_change', {
            'category': 'ai_connections', 'setting': 'review',
            'value': f'codex|owner-mcp|notes|{operation}|read'})
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (work,))
        return job, draft

    def confirm(self, job, draft):
        row = self.orchestrator._drafts()[draft['draft_id']]
        return self.orchestrator.confirm(self.service.settings_owner(job), job['channel'], row['id'], row['digest'])

    def test_confirmation_continues_original_work_once_without_another_request(self):
        job, draft = self.blocked()
        self.confirm(job, draft)
        self.assertEqual(self.store.job(job['id'])['status'], 'queued')
        self.service.resume_settings_work()
        events = [e for e in self.store.task_events(job['id']) if e['tool'] == 'settings_continuation']
        self.assertEqual(len(events), 1)
        self.assertEqual(self.store.job(job['id'])['message'], job['message'])
        self.assertEqual(self.service.connector_permission(job, {'tool_name': LIST, 'engine': 'codex'})['behavior'], 'allow')

    def test_setup_only_draft_has_no_continuation(self):
        work = self.store.enqueue('Allow notes reads', 'setup')
        job = self.store.job(work)
        draft = self.service.settings_tools(job)('settings_change', {'category': 'ai_connections', 'setting': 'review',
                    'value': 'codex|owner-mcp|notes|list_notes|read'})
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (work,))
        self.confirm(job, draft)
        self.assertEqual(self.store.job(work)['status'], 'succeeded')

    def test_confirmation_during_run_waits_until_run_settles(self):
        job, draft = self.blocked()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
        self.confirm(job, draft)
        self.assertEqual(self.store.job(job['id'])['status'], 'running')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (job['id'],))
        self.service.resume_settings_work()
        self.assertEqual(self.store.job(job['id'])['status'], 'queued')

    def test_applied_draft_is_recovered_after_process_restart(self):
        job, draft = self.blocked()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(job, draft)
        from personal_agent.quickstart_service import AgentService
        restarted = AgentService(self.store)
        restarted.resume_settings_work()
        self.assertEqual(self.store.job(job['id'])['status'], 'queued')

    def test_stop_and_revocation_do_not_resume(self):
        for state in ('cancelled', 'revoked'):
            with self.subTest(state=state):
                job, draft = self.blocked(state, state)
                with mock.patch.object(self.service, 'resume_settings_work'):
                    self.confirm(job, draft)
                if state == 'cancelled':
                    with self.store.db() as db:
                        db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job['id'],))
                else:
                    owner_mcp.set_confirmed(self.store, 'codex', [])
                self.service.resume_settings_work()
                self.assertNotEqual(self.store.job(job['id'])['status'], 'queued')

    def test_effect_is_not_replayed_after_read_review(self):
        job, draft = self.blocked()
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job['id'], 'browser_click', 'succeeded', '{}', 1))
        self.confirm(job, draft)
        self.assertNotEqual(self.store.job(job['id'])['status'], 'queued')

    def test_binding_tamper_cannot_move_resume_to_another_work(self):
        job, draft = self.blocked()
        other = self.store.enqueue('Unrelated work', 'other')
        rows = self.orchestrator._drafts()
        rows[draft['draft_id']]['work_id'] = other
        self.orchestrator._put_drafts(rows)
        with self.assertRaises(SettingsError):
            self.confirm(job, draft)

    def test_tool_schema_offers_the_trust_settings(self):
        from personal_agent.agent_runtime import DEFINITIONS
        definition = next(d for d in DEFINITIONS if d['function']['name'] == 'settings_change')
        self.assertTrue({'servers', 'review', 'money', 'rung'} <=
                        set(definition['function']['parameters']['properties']['setting']['enum']))

    def test_two_requests_for_same_connection_resume_independently(self):
        first, draft = self.blocked('first')
        second, _other = self.blocked('second')
        self.confirm(first, draft)
        self.assertEqual([self.store.job(job['id'])['status'] for job in (first, second)], ['queued', 'queued'])

    def test_cancel_and_expiry_settle_wait_without_resuming(self):
        for outcome in ('cancel', 'expire'):
            with self.subTest(outcome=outcome):
                job, draft = self.blocked(outcome, outcome)
                self.service.resume_settings_work()
                self.assertEqual(self.store.job(job['id'])['status'], 'awaiting_connection')
                if outcome == 'cancel':
                    self.orchestrator.cancel(self.service.settings_owner(job), job['channel'], draft['draft_id'])
                else:
                    self.orchestrator.now = lambda: float('inf')
                self.service.resume_settings_work()
                self.assertEqual(self.store.job(job['id'])['status'], 'partial')

    def test_multiple_prerequisites_wait_for_all_and_cancel_wins(self):
        job, first = self.blocked()
        self.service.connector_permission(job, {'tool_name': 'mcp__notes__read_note', 'engine': 'codex'})
        other = self.service.settings_tools(job)('settings_change', {'category': 'ai_connections', 'setting': 'review',
                         'value': 'codex|owner-mcp|notes|read_note|read'})
        self.confirm(job, first)
        self.assertEqual(self.store.job(job['id'])['status'], 'awaiting_connection')
        self.orchestrator.cancel(self.service.settings_owner(job), job['channel'], other['draft_id'])
        self.service.resume_settings_work()
        self.assertEqual(self.store.job(job['id'])['status'], 'partial')

    def test_multiple_read_prerequisites_on_one_hand_confirm_without_reasking(self):
        job, first = self.blocked()
        self.service.connector_permission(job, {'tool_name': 'mcp__notes__read_note', 'engine': 'codex'})
        other = self.service.settings_tools(job)('settings_change', {'category': 'ai_connections', 'setting': 'review',
                         'value': 'codex|owner-mcp|notes|read_note|read'})
        self.confirm(job, first)
        self.assertEqual(self.store.job(job['id'])['status'], 'awaiting_connection')
        self.confirm(job, other)
        self.assertEqual(self.store.job(job['id'])['status'], 'queued')

    def test_expired_independent_reads_renew_and_continue_without_reasking(self):
        job, _first = self.blocked()
        self.service.connector_permission(job, {'tool_name': 'mcp__notes__read_note', 'engine': 'codex'})
        self.service.settings_tools(job)('settings_change', {'category': 'ai_connections', 'setting': 'review',
                         'value': 'codex|owner-mcp|notes|read_note|read'})
        now = self.orchestrator.now() + self.orchestrator.TTL_SECONDS + 1
        self.orchestrator.now = lambda: now
        self.service.work_settings_draft(job['id'], {'action': 'confirm'})
        self.assertEqual(self.store.job(job['id'])['status'], 'queued')
        target = trust_record.connection('codex', 'owner-mcp', 'notes')
        self.assertTrue(trust_record.is_read(self.store, target, 'list_notes'))
        self.assertTrue(trust_record.is_read(self.store, target, 'read_note'))

    def test_owner_renewal_after_expiry_receipt_continues_once(self):
        job, _draft = self.blocked()
        now = self.orchestrator.now() + self.orchestrator.TTL_SECONDS + 1
        self.orchestrator.now = lambda: now
        self.service.resume_settings_work()
        self.assertEqual(self.store.job(job['id'])['status'], 'partial')
        self.service.work_settings_draft(job['id'], {'action': 'confirm'})
        self.assertEqual(self.store.job(job['id'])['status'], 'queued')
        self.service.resume_settings_work()
        receipts = [e['trace'] for e in self.store.task_events(job['id']) if e['tool'] == 'settings_continuation']
        self.assertEqual(sum(r['resumed'] is True for r in receipts), 1)

    def test_sequential_prerequisites_have_a_cumulative_bound(self):
        job, draft = self.blocked()
        self.confirm(job, draft)
        for index in range(1, 10):
            operation = f'read_{index}'
            self.service.connector_permission(job, {'tool_name': f'mcp__notes__{operation}', 'engine': 'codex'})
            draft = self.service.settings_tools(job)('settings_change', {'category': 'ai_connections', 'setting': 'review',
                           'value': f'codex|owner-mcp|notes|{operation}|read'})
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='partial' WHERE id=?", (job['id'],))
            self.confirm(job, draft)
        receipts = [e['trace'] for e in self.store.task_events(job['id']) if e['tool'] == 'settings_continuation']
        self.assertEqual(sum(r['resumed'] for r in receipts), 8)
        self.assertEqual(receipts[-1]['reason'], 'continuation-budget')

    def test_duplicate_consumers_claim_one_queue_entry(self):
        from concurrent.futures import ThreadPoolExecutor
        from personal_agent.quickstart_service import AgentService
        job, draft = self.blocked()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(job, draft)
        services = [AgentService(self.store), AgentService(self.store)]
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda service: service.resume_settings_work(), services))
        self.assertEqual(len([e for e in self.store.task_events(job['id']) if e['tool'] == 'settings_continuation']), 1)

    def test_an_incomplete_or_mutating_cli_turn_cannot_resume(self):
        for meta in ({}, {'tool_calls': [], 'stream_tail': ['item.completed']},
                     {'tool_calls': [{'type': 'command_execution', 'name': 'shell'}], 'stream_tail': ['turn.completed']}):
            self.assertFalse(self.service.connector_turn_read_only('missing', 'codex', meta))

    def test_conversation_cancellation_closes_actual_trust_wait(self):
        job, draft = self.blocked()
        self.service.resume_settings_work()
        parked = self.store.job(job['id'])
        canceled, _text = self.service.cancel_focused_work(parked, self.service.connector_owner_id(parked))
        self.assertTrue(canceled)
        with self.assertRaises(SettingsError):
            self.confirm(job, draft)
        self.service.resume_settings_work()
        self.assertEqual(self.store.job(job['id'])['status'], 'cancelled')

    def test_confirmation_during_run_does_not_repeat_completed_original(self):
        job, draft = self.blocked()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
        self.confirm(job, draft)
        self.service.connector_permission(job, {'tool_name': LIST, 'engine': 'codex'})
        self.service.record_connector_calls(job['id'], [{'tool': LIST, 'status': 'succeeded'}], engine='codex')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='The comparison is complete.' WHERE id=?", (job['id'],))
        self.service.resume_settings_work()
        self.assertEqual(self.store.job(job['id'])['status'], 'succeeded')
        receipt = [e for e in self.store.task_events(job['id']) if e['tool'] == 'settings_continuation'][-1]
        self.assertEqual(receipt['trace']['reason'], 'original-completed')

    def test_long_operation_cannot_inherit_the_prefix_permission(self):
        job, _draft = self.blocked()
        target = trust_record.connection('codex', 'owner-mcp', 'notes')
        operation = 'a' * 60
        trust_record.set_operation(self.store, target, operation, 'read')
        self.assertEqual(self.service.connector_permission(job, {'tool_name': f'mcp__notes__{operation}', 'engine': 'codex'})['behavior'], 'allow')
        self.assertEqual(self.service.connector_permission(job, {'tool_name': f'mcp__notes__{operation}delete', 'engine': 'codex'})['behavior'], 'deny')

    def test_prior_attempt_permission_cannot_certify_a_later_call(self):
        job, draft = self.blocked()
        self.confirm(job, draft)
        self.service.connector_permission(job, {'tool_name': LIST, 'engine': 'codex'})
        target = trust_record.connection('codex', 'owner-mcp', 'notes')
        trust_record.set_operation(self.store, target, 'list_notes', 'payment')
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job['id'], 'subscription_engine', 'running', '{}', 1))
        meta = {'tool_calls': [{'type': 'mcp_tool_call', 'server': 'notes', 'name': 'list_notes', 'status': 'completed'}],
                'connector_reads': [{'tool': LIST, 'status': 'succeeded'}], 'stream_tail': ['turn.completed']}
        self.assertFalse(self.service.connector_turn_read_only(job['id'], 'codex', meta))
        self.assertTrue(self.service.record_connector_calls(job['id'], meta['connector_reads'], engine='codex'))
        self.assertFalse(self.service.settings_resume_safe(job['id']))

    def test_each_reported_invocation_needs_its_own_current_decision(self):
        job, draft = self.blocked()
        self.confirm(job, draft)
        self.service.connector_permission(job, {'tool_name': LIST, 'engine': 'codex'})
        calls = [{'type': 'mcp_tool_call', 'server': 'notes', 'name': 'list_notes', 'status': 'completed'}] * 2
        reads = [{'tool': LIST, 'status': 'succeeded'}] * 2
        self.assertFalse(self.service.connector_turn_read_only(job['id'], 'codex', {'tool_calls': calls, 'connector_reads': reads, 'stream_tail': ['turn.completed']}))
        self.assertEqual(len(self.service.record_connector_calls(job['id'], reads, engine='codex')), 1)


class AvailableTests(unittest.TestCase):
    def test_names_come_from_each_cli_configuration_only(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        home = Path(temp.name)
        (home / '.claude.json').write_text(json.dumps({'mcpServers': {'files': {'command': 'x', 'env': {'K': 'secret'}},
                                                                      'claude_ai_x': {}, 'agentos': {}}}))
        (home / 'codex').mkdir()
        (home / 'codex' / 'config.toml').write_text('[mcp_servers.notes]\ncommand = "n"\n[mcp_servers.figma]\nurl = "https://x"\n[mcp_servers.off]\nenabled = false\n')
        found = owner_mcp.available(home=home, codex_home=home / 'codex')
        self.assertEqual(found, {'claude-code': ['files'], 'codex': ['figma', 'notes']})
        self.assertNotIn('secret', json.dumps(found))
        self.assertEqual(owner_mcp.available(home=home / 'missing', codex_home=home / 'missing'), {'claude-code': [], 'codex': []})


if __name__ == '__main__':
    unittest.main()
