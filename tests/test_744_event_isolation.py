"""Intake authority is durable state, not a tool call or an authentication URL log."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from personal_agent.agent_runtime import Capabilities
from personal_agent.quickstart_store import QuickStore
from test_744_effects import host
from test_browser_session import FakeDriver, ORIGIN


@pytest.mark.parametrize('action', ['browser_open', 'browser_sign_in'])
def test_browser_intake_persists_reference_not_ephemeral_login_address(host, action):
    store, service, job = host
    driver = FakeDriver()
    caps = Capabilities(store, None, {}, '', job['id'], lambda *args: None,
                        browser=lambda: driver, browser_approvals=service.browser_approvals_for(job))
    url = ORIGIN + '/login?continue=private-return-744#private-fragment-744'
    result = caps.execute(action, {'url': url, 'effect': 'navigate'})
    assert result['state'] == 'login_required'
    assert store.outside_content_sources(job['id']) == [{
        'operation': action, 'category': 'owner-browser-session', 'url': ORIGIN + '/login'}]
    # The raw URL is available only to the window; every stored source is safe.
    reopened = QuickStore(store.root)
    assert reopened.outside_content_sources(job['id']) == store.outside_content_sources(job['id'])
    assert 'private-return-744' not in json.dumps(service._browser_login(job['id']))


def test_marks_never_appear_as_calls_in_event_or_effect_views(host):
    store, service, job = host
    with store.db() as db:
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (job['id'], 'list_roots', 'succeeded', '{}', 1))
    events = store.task_events(job['id'])
    recent = store.recent_tool_events()
    summary = store.evidence_summary(job['id'])
    continuation = service.may_continue(job['id'], 'resume')
    source = {'operation': 'web_search', 'category': 'public-web'}
    store.mark_outside_content(job['id'], source)
    assert store.outside_content_sources(job['id']) == [source]
    assert store.task_events(job['id']) == events
    assert store.recent_tool_events() == recent
    assert store.evidence_summary(job['id']) == summary
    assert service.may_continue(job['id'], 'resume') == continuation
    # Covers direct SQL consumers too (SDK call pairing, turn summaries, audit).
    with store.db() as db:
        assert [r['tool'] for r in db.execute('SELECT tool FROM tool_events WHERE job_id=?',
                                            (job['id'],))] == ['list_roots']


def test_existing_event_marks_migrate_atomically_without_losing_authority(host):
    store, _, job = host
    source = {'operation': 'api_request', 'category': 'owner-api', 'url': 'https://example.test/read'}
    browser = {'operation': 'browser_open', 'category': 'owner-browser-session',
               'url': 'https://example.test/login?continue=private-return#private-fragment'}
    with store.db() as db:
        # Recreate the pre-upgrade schema, not just a current store with old rows.
        db.execute('DROP TABLE IF EXISTS work_intake_sources')
        for item in (source, source, browser):
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job['id'], 'outside_content', 'observed', json.dumps(item), 1))
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (job['id'], 'api_request', 'failed', '{"code":"provider_error"}', 2))
    migrated = QuickStore(store.root)
    expected = [source, {**browser, 'url': 'https://example.test/login'}]
    assert migrated.outside_content_sources(job['id']) == expected
    assert [r['tool'] for r in migrated.task_events(job['id'])] == ['api_request']
    migrated.mark_outside_content(job['id'], source)
    assert QuickStore(store.root).outside_content_sources(job['id']) == expected
    with migrated.db() as db:
        assert db.execute("SELECT COUNT(*) FROM tool_events WHERE tool='outside_content'").fetchone()[0] == 0


def test_concurrent_store_openers_preserve_each_unique_mark(host):
    store, _, job = host
    sources = [{'category': 'native-search', 'operation': 'web_search', 'query': str(n)} for n in range(4)]
    def mark(source):
        other = QuickStore(store.root)
        other.mark_outside_content(job['id'], source)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(mark, sources * 2))
    assert sorted(store.outside_content_sources(job['id']), key=lambda row: row['query']) == sources
    assert store.task_events(job['id']) == []


def test_failed_migration_keeps_legacy_authority_and_no_partial_copy(host):
    store, _, job = host
    with store.db() as db:
        for detail in ('{"operation":"read_file"}', 'unreadable legacy mark'):
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job['id'], 'outside_content', 'observed', detail, 1))
    with pytest.raises(json.JSONDecodeError):
        QuickStore(store.root)
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM tool_events WHERE tool='outside_content'").fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM work_intake_sources').fetchone()[0] == 0


def test_portable_export_masks_intake_paths_but_keeps_work_authority(host, tmp_path):
    from personal_agent.portable_state import export_owner_state, restore_owner_state
    from personal_agent.self_set_writes import authorize
    from personal_agent.browser_session import step_binding
    from personal_agent.agent_runtime import ToolError
    from personal_agent.quickstart_service import AgentService
    store, _, job = host
    sources = [{'operation': 'read_file', 'category': 'connected-document',
                'path': path} for path in ('/Users/owner/Documents/private.txt',
                                           '/Users/owner/Documents/another.txt',
                                           str(store.root / 'results/private.md'))]
    for source in sources:
        store.mark_outside_content(job['id'], source)
    archive = tmp_path / 'export.tar.gz'
    export_owner_state(store.root, archive)
    restored_dir = tmp_path / 'restored'
    restore_owner_state(archive, restored_dir)
    restored = QuickStore(restored_dir)
    exported = restored.outside_content_sources(job['id'])
    assert len(exported) == 3
    assert all(row['operation'] == 'read_file' and row['category'] == 'connected-document' for row in exported)
    assert [row['path'] for row in exported] == ['[owner path]', '[owner path]', '[Still data]/results/private.md']
    assert '/Users/owner' not in json.dumps(exported) and str(store.root) not in json.dumps(exported)
    assert store.outside_content_sources(job['id']) == sources, 'local owner-visible references stay intact'
    service = AgentService(restored)
    binding = step_binding(job['id'], 'api_request', 'https://example.test/write', 'slot|POST')
    with pytest.raises(ToolError) as held:
        authorize(service, restored.job(job['id']), binding, 'slot:test', 'POST', 'https://example.test/write',
                  effect='irreversible', values={'value': 'unsupported'})
    assert held.value.code == 'approval_required', 'portable authority is retained, never reset to unmarked'


@pytest.mark.parametrize('delete', [False, True], ids=['forget', 'delete'])
def test_transcript_forgetting_preserves_work_intake_like_work_events(host, delete):
    from personal_agent.owner_forget import OwnerForget, UNDO_SECONDS
    from personal_agent.agent_runtime import MEMORY_OWNER
    store, _, job = host
    source = {'operation': 'read_file', 'category': 'connected-document', 'path': 'private.txt'}
    store.mark_outside_content(job['id'], source)
    with store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded', response='Outside answer' WHERE id=?", (job['id'],))
        message = db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                             ('assistant', 'Outside answer', 'web', 1, job['id'])).lastrowid
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (job['id'], 'read_file', 'succeeded', '{}', 1))
    forgotten = OwnerForget(store, clock=lambda: 100)
    receipt = forgotten.forget(MEMORY_OWNER, f'message:{message}', delete=delete)
    assert store.find_messages('Outside answer') == []
    assert store.outside_content_sources(job['id']) == [source]
    assert [r['tool'] for r in store.task_events(job['id'])] == ['read_file']
    assert forgotten.purge_due(100 + UNDO_SECONDS + 1) == 0
    assert QuickStore(store.root).outside_content_sources(job['id']) == [source]
    if not delete:
        forgotten.undo(MEMORY_OWNER, receipt['receipt'])
        assert store.find_messages('Outside answer')
        assert store.outside_content_sources(job['id']) == [source]
