"""#657: startup retires legacy sharing authority without deleting owner data."""
import json
from pathlib import Path

from personal_agent.quickstart_store import QuickStore


def test_startup_retires_only_family_state_and_is_idempotent(tmp_path, caplog):
    store = QuickStore(tmp_path)
    legacy = {'family_setups': [{'instance': 'family-1'}],
              'family_shared_sites': [{'site': 'shop.test', 'instance': 'family-1'}],
              'shared_sites': {'shop.test': {'from': 'owner'}}}
    for key, value in legacy.items():
        store.put(key, value)
    owner = {'enabled': True, 'username': 'owner_bot', 'user_id': 123, 'generation': 'g'}
    family_draft = {'id': 'old', 'category': 'family', 'state': 'awaiting-confirmation'}
    owner_draft = {'id': 'own', 'category': 'main_ai', 'state': 'awaiting-confirmation'}
    store.put('settings_change_drafts', {'old': family_draft, 'own': owner_draft})
    store.put('telegram', {**owner, 'pair_user_id': 456})
    store.secret('telegram_token', 'owner-secret-kept')
    store.secret('family_handoff.family-1', 'retired-handoff-secret')
    store.put('browser_owner_signins', {'shop.test': {'at': 123}})
    original = tmp_path / 'original.txt'
    original.write_text('owner original')
    work_id = store.enqueue('owner work', 'retirement-owner-work')
    with store.db() as db:
        db.execute("INSERT INTO messages(role,content,channel,created) VALUES ('user','owner message','web',1)")
        db.execute("INSERT INTO notes VALUES ('kept-note','owner note',1)")
    for name in ('family-setup.json', 'owner-link.secret'):
        (store.private / name).write_text('legacy-secret')
    with caplog.at_level('INFO', logger='personal_agent.quickstart_store'):
        upgraded = QuickStore(tmp_path)
    assert 'family sharing retired (#657)' in caplog.text
    assert 'retired-handoff-secret' not in caplog.text
    assert 'legacy-secret' not in caplog.text
    archive = upgraded.config('retired_family_657')
    for key, value in legacy.items():
        assert upgraded.config(key) is None
        assert json.loads(archive[key]) == value
    assert upgraded.config('settings_change_drafts') == {'own': owner_draft}
    assert archive['settings_change_drafts'] == {'old': family_draft}
    assert upgraded.config('telegram') == owner
    assert upgraded.secret('telegram_token') == 'owner-secret-kept'
    assert not upgraded.secret_items('family_handoff.')
    assert upgraded.config('browser_owner_signins') == {'shop.test': {'at': 123}}
    assert original.read_text() == 'owner original'
    assert upgraded.job(work_id)['message'] == 'owner work'
    with upgraded.db() as db:
        assert db.execute("SELECT content FROM messages").fetchone()['content'] == 'owner message'
        assert db.execute("SELECT content FROM notes WHERE id='kept-note'").fetchone()['content'] == 'owner note'
    for name in ('family-setup.json', 'owner-link.secret'):
        assert not (store.private / name).exists()
        assert (store.private / (name + '.retired-657')).read_text() == 'legacy-secret'
    caplog.clear()
    QuickStore(tmp_path)
    assert not caplog.records
    assert upgraded.config('retired_family_657') == archive


def test_removed_settings_and_tool_surface(tmp_path):
    import pytest
    from personal_agent.agent_runtime import DEFINITIONS, SETTINGS_CATEGORIES, model_facing_text
    from personal_agent.settings_orchestrator import SettingsError, SettingsOrchestrator
    settings = SettingsOrchestrator(QuickStore(tmp_path))
    assert 'family' not in SETTINGS_CATEGORIES
    for operation in ('add', 'share_site', 'unshare_site'):
        with pytest.raises(SettingsError):
            settings.propose('owner', 'web', 'family', operation, 'legacy')
    for definition in DEFINITIONS:
        if definition['function']['name'] in ('settings_read', 'settings_change'):
            assert 'family' not in model_facing_text(definition['function'])
            assert 'share_site' not in model_facing_text(definition['function'])


def test_interrupted_file_retirement_and_archive_conflict_preserve_contents(tmp_path, caplog):
    import os
    store = QuickStore(tmp_path)
    path = store.private / 'family-setup.json'
    archive = store.private / 'family-setup.json.retired-657'
    path.write_text('old setup')
    os.link(path, archive)
    QuickStore(tmp_path)
    assert not path.exists()
    assert archive.read_text() == 'old setup'
    path.write_text('another setup')
    QuickStore(tmp_path)
    assert path.read_text() == 'another setup'
    assert archive.read_text() == 'old setup'
    assert 'archive conflict' in caplog.text
