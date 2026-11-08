"""#739: startup retires only the removed connection, without external calls."""
import json
from unittest.mock import patch

import pytest

from cryptography.fernet import Fernet

from personal_agent.messages import msg
from personal_agent.quickstart import configured_service
from personal_agent.quickstart_service import DRIVE_CONNECT_PATH
from personal_agent.quickstart_store import QuickStore

# Historical on-disk namespace, assembled here only for the retirement fixture.
LEGACY_PREFIX = '_'.join(('drive', 'web', 'oauth')) + '_'
CLIENT = json.dumps({'installed': {'client_id': '123-abcdefgh.apps.googleusercontent.com',
                                   'client_secret': 'fixture-client-secret'}})


def seed_removed_connection(store):
    for suffix, value in (('status', {'state': 'connected', 'owner': 42}),
                          ('selected_files', {'owner': 42, 'files': [{'id': 'old-file'}]}),
                          ('pending', {'state': 'old-state'})):
        store.put(LEGACY_PREFIX + suffix, value)
    for suffix in ('tokens', 'pending', 'picker_grant'):
        store.secret('encrypted:' + LEGACY_PREFIX + suffix, 'old-ciphertext-' + suffix)
    for name in ('drive-localhost-cert.pem', 'drive-localhost-key.pem'):
        store.write_private(store.private / name, 'old TLS identity')


def test_startup_retirement_removes_once_and_preserves_full_drive_and_owner_data(tmp_path):
    store = QuickStore(tmp_path)
    env = {'AGENTOS_GOOGLE_OAUTH_KEY': Fernet.generate_key().decode()}
    service = configured_service(store, env)
    service.save_google_client({'client_json': CLIENT})
    owner = service.connector_callback_owner('google-drive')
    offer = service.drive_oauth.begin_oauth(owner)
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(offer['authorization_url']).query)['state'][0]
    service.drive_oauth.complete_oauth(owner, {'state': state, 'code': 'fixture'}, lambda _: {
        'access_token': 'full-drive-access', 'refresh_token': 'full-drive-refresh',
        'expires_in': 3600, 'scope': 'https://www.googleapis.com/auth/drive.readonly'})
    full_secrets = json.loads(store.secret_path.read_text())
    full_registry = store.config('connector_contract_state')
    store.save_memory('retained-memory', 'owner memory')
    memory = store.notes()
    artifact = tmp_path / 'owner-artifact.txt'
    artifact.write_text('owner artifact')
    seed_removed_connection(store)

    restarted = QuickStore(tmp_path)
    assert json.loads(restarted.secret_path.read_text()) == full_secrets
    assert restarted.config('connector_contract_state') == full_registry
    assert restarted.notes() == memory
    assert artifact.read_text() == 'owner artifact'
    assert restarted.config(LEGACY_PREFIX + 'status') is None
    assert restarted.config(LEGACY_PREFIX + 'selected_files') is None
    assert restarted.config(LEGACY_PREFIX + 'pending') is None
    assert not restarted.secret_items('encrypted:' + LEGACY_PREFIX)
    assert not (restarted.private / 'drive-localhost-key.pem').exists()
    assert not (restarted.private / 'drive-localhost-cert.pem').exists()
    assert restarted.config('retired_drive_739')['status']['owner'] == 42
    assert configured_service(restarted, env).drive_oauth.credential_current(owner)

    # A second startup does not rewrite the shared credentials or retirement receipt.
    secret_before = restarted.secret_path.read_bytes()
    secret_mtime = restarted.secret_path.stat().st_mtime_ns
    receipt = restarted.config('retired_drive_739')
    with patch.object(QuickStore, 'remove_secret', side_effect=AssertionError('already removed')):
        again = QuickStore(tmp_path)
    assert again.secret_path.read_bytes() == secret_before
    assert again.secret_path.stat().st_mtime_ns == secret_mtime
    assert again.config('retired_drive_739') == receipt
    assert again.notes() == memory


def test_picker_only_install_offers_normal_disconnected_drive_connection(tmp_path):
    store = QuickStore(tmp_path)
    env = {'AGENTOS_GOOGLE_OAUTH_KEY': Fernet.generate_key().decode()}
    # A configured client is not a full-Drive connection: only the removed connector was connected.
    configured_service(store, env).save_google_client({'client_json': CLIENT})
    seed_removed_connection(store)
    restarted = QuickStore(tmp_path)
    service = configured_service(restarted, env)
    row = next(row for row in service.settings()['connectors'] if row['connector_id'] == 'google-drive')
    assert row['state'] == 'disconnected'
    assert row['connect_path'] == DRIVE_CONNECT_PATH
    assert row['required_scopes'] == ['https://www.googleapis.com/auth/drive.readonly']
    assert service.drive_oauth.credential_current(service.connector_callback_owner('google-drive')) is False
    assert not any(row['connector_id'] == 'google-drive-read' for row in service.settings()['connectors'])


def test_retirement_unlinks_only_its_tls_symlink_not_the_owner_target(tmp_path):
    store = QuickStore(tmp_path)
    original = tmp_path / 'owner-original.txt'
    original.write_text('owner original')
    link = store.private / 'drive-localhost-key.pem'
    link.symlink_to(original)
    QuickStore(tmp_path)
    assert not link.is_symlink()
    assert original.read_text() == 'owner original'


def test_picker_only_without_normal_client_offers_existing_connection_setup(tmp_path):
    store = QuickStore(tmp_path)
    seed_removed_connection(store)
    service = configured_service(QuickStore(tmp_path), {})
    assert service.drive_oauth is None
    assert service.google_client_status()['configured'] is False
    response = service.conversation_settings_request({'operation': 'read', 'category': 'connections'})['response']
    assert '자체 Google client · 설정 안 됨' in response
    assert '설정 > 외부 연결 > 자체 Google client' in response
    assert not any(row['state'] == 'connected' for row in service.settings()['connectors'])


@pytest.mark.parametrize('legacy_state', ['connection-offered', 'connected', 'already-retired'])
def test_retirement_settles_only_legacy_drive_waits_once(tmp_path, legacy_state):
    store = QuickStore(tmp_path)
    workspace = store.create_workspace('owner workspace')
    waiting = [store.enqueue('original request', 'old-drive-' + str(index), channel='telegram:fixture',
                             chat_id=42 + index, workspace_id=workspace['id']) for index in range(2)]
    full_drive_wait = store.enqueue('normal Drive request', 'full-drive-wait')
    completed = store.enqueue('finished request', 'completed-work')
    with store.db() as db:
        for work_id, delivery in zip(waiting, ('pending', 'none')):
            db.execute("UPDATE jobs SET status='awaiting_drive',delivery=? WHERE id=?", (delivery, work_id))
        db.execute("UPDATE jobs SET status='awaiting_connection',delivery='pending' WHERE id=?", (full_drive_wait,))
        db.execute("UPDATE jobs SET status='succeeded',response='retained result' WHERE id=?", (completed,))
    untouched = [store.job(full_drive_wait), store.job(completed)]
    if legacy_state == 'already-retired':
        # The previous retirement removed its handoff but left these Works waiting.
        store.put('retired_drive_739', {'status': {'owner': 42, 'pending_job_id': waiting[0]}})
    else:
        seed_removed_connection(store)
        # Only the latest handoff was indexed; an older waiting Work must also settle.
        store.put(LEGACY_PREFIX + 'status', {'state': legacy_state, 'owner': 42, 'pending_job_id': waiting[0]})

    restarted = QuickStore(tmp_path)
    reason = msg('quickstart_store.drive_connection_retired')
    assert '설정 > 외부 연결에서 Google Drive' in reason
    assert [restarted.job(work_id) for work_id in (full_drive_wait, completed)] == untouched
    for work_id, delivery in zip(waiting, ('cancelled', 'none')):
        job = restarted.job(work_id)
        assert (job['status'], job['error'], job['response'], job['delivery']) == ('failed', reason, reason, delivery)
        assert job['message'] == 'original request'
        reply = restarted.last_reply(work_id)
        assert reply is not None and reply['content'] == reason
        with restarted.db() as db:
            rows = db.execute("SELECT channel,workspace_id FROM messages WHERE job_id=? AND role='assistant'",
                              (work_id,)).fetchall()
        assert len(rows) == 1
        assert tuple(rows[0]) == ('telegram:fixture', workspace['id'])
    before = restarted.jobs(), restarted.history()
    again = QuickStore(tmp_path)
    assert (again.jobs(), again.history()) == before
