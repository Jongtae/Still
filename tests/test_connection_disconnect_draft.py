"""#183: the worker drafts a connection disconnect; the owner's confirmation applies it exactly once.

Evidence class: model-free service tests.  Google's revocation endpoint is the injected fake of
``tests/test_connector_revocation.py`` and the Gmail connection is a fixture OAuth exchange; no
live Google request is made and no model is asked.
"""
import json

from personal_agent.agent_runtime import DEFINITIONS
from personal_agent.providers import ModelAdapter
from personal_agent.connector_contract import ConnectorState
from personal_agent.gmail import GMAIL_CONNECTOR_ID, GmailError
from personal_agent.settings_orchestrator import DISCONNECT_NOTE, RETIRED_CONTROL_MESSAGE, SettingsError

from test_conversation_connector_handoff import MODEL_ENDPOINT, OWNER
from test_connector_revocation import RevocationTestCase

CHANNEL = 'telegram:42'


class ConnectionDisconnectDraftTests(RevocationTestCase):
    def setUp(self):
        super().setUp()
        self.settings = self.service.settings_orchestrator
        self.tools = self.settings.work_tools(OWNER, CHANNEL, 'work-1')

    def propose(self, value=GMAIL_CONNECTOR_ID, setting='disconnect'):
        return self.tools('settings_change', {'category': 'connections', 'setting': setting, 'value': value,
                                              'reason': '소유자가 Gmail 연결을 끊어 달라고 했습니다.'})

    def draft_row(self, draft_id):
        return self.store.config('settings_change_drafts')[draft_id]

    def test_the_tool_offers_connections_disconnect(self):
        [schema] = [tool for tool in DEFINITIONS if tool['function']['name'] == 'settings_change']
        properties = schema['function']['parameters']['properties']
        self.assertIn('connections', properties['category']['enum'])
        self.assertIn('disconnect', properties['setting']['enum'])

    def test_settings_read_names_what_can_be_disconnected(self):
        self.connect_gmail()
        rows = self.tools('settings_read', {'category': 'connections'})['connections']
        gmail = next(row for row in rows if row['id'] == GMAIL_CONNECTOR_ID)
        self.assertTrue(gmail['disconnectable'])
        self.assertFalse(next(row for row in rows if row['id'] == 'telegram').get('disconnectable'))

    def test_a_draft_changes_nothing_until_the_owner_confirms_then_applies_once(self):
        self.connect_gmail()
        self.assertTrue(self.gmail.search(OWNER, 'budget'))
        draft = self.propose()
        self.assertEqual((draft['state'], draft['requires_owner_confirmation'], draft['applied']),
                         ('awaiting-confirmation', True, False))
        self.assertIn('Google Gmail', draft['summary'])
        self.assertIn('연결을 해제합니다', draft['summary'])
        self.assertEqual(draft['note'], DISCONNECT_NOTE)
        self.assertNotIn('digest', draft, 'the worker cannot confirm its own draft')
        # Nothing changed: still connected, still usable, nothing sent to Google.
        self.assertIs(self.gmail_state(), ConnectorState.CONNECTED)
        self.assertTrue(self.gmail.search(OWNER, 'budget'))
        self.assertEqual(self.google.requests, [])

        row = self.draft_row(draft['draft_id'])
        result = self.settings.confirm(OWNER, CHANNEL, row['id'], row['digest'])
        self.assertEqual(result['state'], 'applied')
        self.assertIn('Gmail 연결을 해제했습니다', result['response'])
        self.assertIs(self.gmail_state(), ConnectorState.DISCONNECTED)
        with self.assertRaises(GmailError):
            self.gmail.search(OWNER, 'budget')
        self.assertEqual(len(self.google.requests), 1, 'one revocation request')
        # Exactly once: a second confirmation applies nothing more.
        with self.assertRaises(SettingsError):
            self.settings.confirm(OWNER, CHANNEL, row['id'], row['digest'])
        self.assertEqual(len(self.google.requests), 1)
        audit = [event for event in self.store.config('settings_audit') if event.get('target') == 'connections.disconnect']
        self.assertEqual([event['terminal'] for event in audit], ['drafted', 'applied'])

    def test_the_owners_plain_yes_takes_the_same_path(self):
        self.connect_gmail()
        draft = self.propose()
        rows = self.settings.pending_for_conversation(OWNER, CHANNEL)
        self.assertEqual([row['id'] for row in rows], [draft['draft_id']])
        result = self.settings.settle_pending(OWNER, CHANNEL, rows, apply=True)
        self.assertEqual(result['state'], 'applied')
        self.assertIs(self.gmail_state(), ConnectorState.DISCONNECTED)

    def test_a_declined_expired_foreign_or_stale_draft_changes_nothing(self):
        self.connect_gmail()
        declined = self.propose()
        self.settings.settle_pending(OWNER, CHANNEL, self.settings.pending_for_conversation(OWNER, CHANNEL), apply=False)
        self.assertEqual(self.draft_row(declined['draft_id'])['state'], 'canceled')

        foreign = self.draft_row(self.propose()['draft_id'])
        with self.assertRaises(SettingsError):
            self.settings.confirm('telegram:99', CHANNEL, foreign['id'], foreign['digest'])
        with self.assertRaises(SettingsError):
            self.settings.confirm(OWNER, CHANNEL, foreign['id'], 'not-the-digest')
        self.settings.cancel(OWNER, CHANNEL, foreign['id'])

        expired = self.draft_row(self.propose()['draft_id'])
        drafts = self.store.config('settings_change_drafts')
        drafts[expired['id']]['expires_at'] = 0  # its ten minutes are over
        self.store.put('settings_change_drafts', drafts)
        with self.assertRaises(SettingsError):
            self.settings.confirm(OWNER, CHANNEL, expired['id'], expired['digest'])
        self.assertIs(self.gmail_state(), ConnectorState.CONNECTED)

        self.assertEqual(self.google.requests, [])
        # Disconnected in Settings, then reconnected (a new revision), after the draft: it is stale.
        stale = self.draft_row(self.propose()['draft_id'])
        self.disconnect()
        self.connect_gmail()
        with self.assertRaises(SettingsError):
            self.settings.confirm(OWNER, CHANNEL, stale['id'], stale['digest'])
        self.assertIs(self.gmail_state(), ConnectorState.CONNECTED)
        self.assertEqual(len(self.google.requests), 1, 'only the Settings disconnect reached Google')
        self.assertEqual(self.draft_row(stale['id'])['state'], 'failed')

    def test_a_reconnect_during_the_apply_is_never_disconnected_by_the_old_draft(self):
        # Review P1: the revoker's confirmation is minted at apply time; a reconnect between the stale
        # check and that mint must not let the draft disconnect the new credential.
        self.connect_gmail()
        row = self.draft_row(self.propose()['draft_id'])
        apply = self.service.apply_connection_disconnect
        raced = []

        def racing_apply(draft):
            # After the stale check passed and before the revoker's preview: reconnect.
            raced.append(True)
            self.disconnect()       # the Settings page, in between
            self.connect_gmail()    # and a fresh connection: a new revision
            return apply(draft)

        self.service.apply_connection_disconnect = racing_apply
        with self.assertRaises(SettingsError):
            self.settings.confirm(OWNER, CHANNEL, row['id'], row['digest'])
        self.service.apply_connection_disconnect = apply
        self.assertIs(self.gmail_state(), ConnectorState.CONNECTED, 'the new connection stays')
        self.assertTrue(self.gmail.search(OWNER, 'budget'))
        self.assertEqual(len(self.google.requests), 1, 'only the Settings disconnect reached Google')
        self.assertEqual(self.draft_row(row['id'])['state'], 'failed')
        self.assertEqual(raced, [True])

    def test_no_oauth_completion_can_commit_between_the_check_and_the_disconnect(self):
        # Review P1: the revision check and the disconnect hold the connector's lifecycle lock together.
        import threading
        self.connect_gmail()
        row = self.draft_row(self.propose()['draft_id'])
        build = self.service.google_revocation
        held = []

        def revocation():
            revoker = build()
            disconnect = revoker.disconnect

            def observed(*args):
                probe = []
                thread = threading.Thread(target=lambda: probe.append(self.gmail.oauth_lock.acquire(timeout=0)))
                thread.start(); thread.join()
                if probe[0]:
                    self.gmail.oauth_lock.release()
                held.append(not probe[0])
                return disconnect(*args)
            revoker.disconnect = observed
            return revoker

        self.service.google_revocation = revocation
        try:
            self.assertEqual(self.settings.confirm(OWNER, CHANNEL, row['id'], row['digest'])['state'], 'applied')
        finally:
            self.service.google_revocation = build
        self.assertEqual(held, [True], 'an OAuth completion is held off while the disconnect runs')

    def test_disconnect_keeps_records_and_is_not_forget(self):
        self.connect_gmail()
        note = self.store.save_memory('profile.mail.label', '예산 메일은 재무 라벨', work_id='source-work')
        row = self.draft_row(self.propose()['draft_id'])
        result = self.settings.confirm(OWNER, CHANNEL, row['id'], row['digest'])
        self.assertIn('저장된 결과물과 기록은 그대로', result['response'])
        self.assertEqual([item['id'] for item in self.store.memories()], [note['id']], 'disconnect forgets nothing')
        [event] = self.store.config('google_disconnect_audit')
        self.assertEqual((event['operation'], event['local_credentials'], event['retained_data']),
                         ('disconnect', 'deleted', 'preserved'))

    def test_pause_resume_and_unknown_connections_are_refused_without_a_draft(self):
        self.connect_gmail()
        for setting, value in (('pause', GMAIL_CONNECTOR_ID), ('resume', GMAIL_CONNECTOR_ID),
                               ('disconnect', 'telegram'), ('disconnect', 'google-unknown')):
            with self.subTest(setting=setting, value=value), self.assertRaises(SettingsError) as caught:
                self.propose(value, setting)
            if setting != 'disconnect':
                self.assertEqual(str(caught.exception), RETIRED_CONTROL_MESSAGE)
        self.assertEqual(self.settings.pending_for_conversation(OWNER, CHANNEL), [])
        self.assertIs(self.gmail_state(), ConnectorState.CONNECTED)

    def test_a_blocked_connection_is_not_offered(self):
        self.connect_gmail()
        build = self.service.google_revocation

        def blocked():
            revoker = build()
            revoker.connections[GMAIL_CONNECTOR_ID].state = lambda: 'blocked'
            return revoker
        self.service.google_revocation = blocked
        try:
            self.assertNotIn(GMAIL_CONNECTOR_ID, [row['id'] for row in self.service.connection_disconnect_options()])
            with self.assertRaises(SettingsError):
                self.propose()
        finally:
            self.service.google_revocation = build
        self.assertEqual(self.google.requests, [])

    def test_an_already_disconnected_connection_is_not_offered(self):
        with self.assertRaises(SettingsError):
            self.propose()
        self.assertEqual(self.google.requests, [])


class WorkerDraftsTheDisconnect(RevocationTestCase):
    """'Gmail 연결 상태 보여주고 끊어줘' reaches the worker, which drafts the disconnect (#173 example)."""

    REQUEST = 'Gmail 연결 상태 보여주고 끊어줘'

    def test_the_worker_drafts_and_the_owners_yes_applies_it(self):
        plan = [('settings_read', {'category': 'connections'}),
                ('settings_change', {'category': 'connections', 'setting': 'disconnect', 'value': GMAIL_CONNECTOR_ID,
                                     'reason': '소유자가 Gmail 연결을 끊어 달라고 했습니다.'})]
        telegram = self.service.telegram_transport

        def model(url, body, headers=None, timeout=60):
            if not url.startswith(MODEL_ENDPOINT):
                return telegram(url, body, headers, timeout)
            names = [tool.get('function', {}).get('name') for tool in body.get('tools', [])]
            if 'agentos_connection_probe' in names:
                return {'choices': [{'message': {'tool_calls': [{'id': 'p', 'type': 'function', 'function': {
                    'name': 'agentos_connection_probe', 'arguments': '{}'}}]}}]}
            done = sum(1 for message in body.get('messages', []) if message.get('role') == 'tool')
            if 'settings_change' in names and done < len(plan):
                name, arguments = plan[done]
                return {'choices': [{'message': {'content': None, 'tool_calls': [{'id': f's{done}', 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}}]}
            return {'choices': [{'message': {'content': 'Gmail은 연결되어 있어요. 해제 확인을 요청했어요.'}}]}

        self.service.adapter = ModelAdapter(model)
        self.enable_worker()
        self.connect_gmail()
        job_id = self.enqueue(self.REQUEST)
        self.assertTrue(self.service.run_one())
        [draft] = [row for row in self.store.config('settings_change_drafts').values()
                   if row.get('target') == 'connections.disconnect']
        self.assertEqual((draft['work_id'], draft['state'], draft['after']), (job_id, 'awaiting-confirmation', GMAIL_CONNECTOR_ID))
        # The worker's turn changed nothing; the owner has not answered yet.
        self.assertIs(self.gmail_state(), ConnectorState.CONNECTED)
        self.assertEqual(self.google.requests, [])
        settings = self.service.settings_orchestrator
        result = settings.settle_pending(draft['owner'], draft['channel'],
                                         settings.pending_for_conversation(draft['owner'], draft['channel']), apply=True)
        self.assertEqual(result['state'], 'applied')
        self.assertIs(self.gmail_state(), ConnectorState.DISCONNECTED)
        self.assertEqual(len(self.google.requests), 1)


if __name__ == '__main__':
    import unittest
    unittest.main()
