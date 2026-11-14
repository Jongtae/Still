"""#820 B2 second verifier regressions, deterministic fixture evidence only."""
import json
import sqlite3
from unittest import mock

import pytest
from personal_agent import information_use as audit
from personal_agent.owner_forget import OwnerForget
from personal_agent.quickstart_store import QuickStore
from test_deferred_information_use import store, memory, call, view, MEMBER, PHONE
from test_deferred_information_use_remediation import marked


def legacy(store, job, key='member', destination='recorded.test'):
    call(store, job, evidence={'outbound_comparison': None, 'outbound_matches': [
        {'source': 'memory', 'key': key, 'destination': destination, 'tool': 'web_search'}]})


def test_unrelated_memory_deletion_preserves_recorded_legacy_match(store):
    memory(store, 'member', MEMBER, 10)
    other = memory(store, 'other', PHONE, 11)
    memory(store, 'last', 'Q9999-0000', 12)
    job = store.enqueue('find', 'test')
    legacy(store, job)
    before = view(store, job)
    assert len(before['held_value_matches']) == 1
    store.delete_personal_space_item('memories', other['id'])
    after = view(store, job)
    assert after['held_value_matches'] == before['held_value_matches']
    assert 'legacy_match_withheld' not in {row['kind'] for row in after.get('held_value_comparison_cut', [])}


@pytest.mark.parametrize('change', ['forget', 'retract', 'delete', 'supersede'])
def test_withheld_legacy_memory_match_always_has_value_free_cut(store, change):
    a = memory(store, 'member', MEMBER, 10)
    if change == 'retract':
        b = memory(store, 'member', PHONE, 12)
        store.retract_memory('local-owner', b['id'], b['content_digest'], now=13)
    job = store.enqueue('find', 'test')
    legacy(store, job)
    if change == 'forget': OwnerForget(store, clock=lambda: 26).forget('local-owner', 'memory:' + a['id'])
    elif change == 'delete': store.delete_personal_space_item('memories', a['id'])
    elif change == 'supersede': memory(store, 'member', PHONE, 30)
    result = view(store, job)
    assert result['held_value_matches'] == []
    assert {'kind': 'legacy_match_withheld', 'limit': 0} in result['held_value_comparison_cut']
    assert MEMBER not in json.dumps(result['held_value_comparison_cut'])
    assert 'member' not in json.dumps(result['held_value_comparison_cut'])
    assert audit.msg('information_use.comparison_cut_legacy_match_withheld') in audit.render_korean({'sent_to': result})


def test_deadline_truncated_comparison_is_retried_without_database_write(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    marked(store, job)
    original = audit.deferred_comparison
    def exhausted(*args, **kwargs):
        kwargs['deadline'] = 0
        return original(*args, **kwargs)
    with mock.patch.object(audit, 'deferred_comparison', side_effect=exhausted):
        first = view(store, job)
    assert {'kind': 'comparison_time', 'limit': 1500} in first['held_value_comparison_cut']
    with mock.patch.object(audit, 'deferred_comparison', wraps=original) as compare:
        second = view(store, job)
    assert compare.call_count == 1
    assert second['held_value_matches'][0]['key'] == 'member'
    assert 'comparison_time' not in {row['kind'] for row in second['held_value_comparison_cut']}


def test_legacy_retention_checks_share_deadline_and_name_unchecked_labels(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    for i in range(8): legacy(store, job, destination='host-' + str(i))
    now = [0.0]
    original = store.comparison_legacy_kept
    def slow(*args, **kwargs):
        now[0] += 0.6
        return original(*args, **kwargs)
    with mock.patch.object(store, 'comparison_legacy_kept', side_effect=slow) as check:
        result = audit.work_information_use(store, job, clock=lambda: now[0])['sent_to']
    assert check.call_count <= 3
    assert {'kind': 'comparison_time', 'limit': 1500} in result['held_value_comparison_cut']
    assert {'kind': 'legacy_match_withheld', 'limit': 0} in result['held_value_comparison_cut']


def test_legacy_retention_checks_reuse_one_connection_without_table_count(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    for i in range(3): legacy(store, job, destination='host-' + str(i))
    original = store.comparison_legacy_kept
    queries = []
    def checked(*args, **kwargs):
        db = kwargs.get('db')
        if db is not None: db.set_trace_callback(queries.append)
        return original(*args, **kwargs)
    with mock.patch.object(store, 'comparison_legacy_kept', side_effect=checked) as check:
        assert len(view(store, job)['held_value_matches']) == 3
    connections = [c.kwargs.get('db') for c in check.call_args_list]
    assert connections and all(db is not None for db in connections)
    assert len({id(db) for db in connections}) == 1
    assert not any('COUNT(' in query.upper() for query in queries)
    with pytest.raises(sqlite3.ProgrammingError): connections[0].execute('SELECT 1')


def test_legacy_validation_obeys_display_bound(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    for i in range(audit.MAX_ITEMS + 1): legacy(store, job, destination='host-' + str(i))
    with mock.patch.object(store, 'comparison_legacy_kept', wraps=store.comparison_legacy_kept) as check:
        result = view(store, job)
    assert check.call_count == audit.MAX_ITEMS
    assert len(result['held_value_matches']) == audit.MAX_ITEMS
    assert {'kind': 'legacy_display', 'limit': audit.MAX_ITEMS} in result['held_value_comparison_cut']
    assert 'calls' not in {row['kind'] for row in result['held_value_comparison_cut']}


def test_current_export_restore_preserves_memory_deletion_gap(store, tmp_path):
    """Operating-path check, not a general SQLite ROWID stability guarantee."""
    from personal_agent.portable_state import export_owner_state, restore_owner_state
    memory(store, 'member', MEMBER, 10)
    middle = memory(store, 'member', PHONE, 15)
    memory(store, 'member', 'Q9999-0000', 30)
    store.delete_personal_space_item('memories', middle['id'])
    with store.db() as db:
        before = [tuple(row) for row in db.execute('SELECT rowid,id FROM memories ORDER BY rowid')]
    archive = tmp_path / 'owner.tar.gz'
    export_owner_state(store.root, archive)
    target = tmp_path / 'restored'
    restore_owner_state(archive, target)
    restored = QuickStore(target)
    with restored.db() as db:
        after = [tuple(row) for row in db.execute('SELECT rowid,id FROM memories ORDER BY rowid')]
    assert after == before
    rows = restored.comparison_memories('local-owner', 20, 501)
    assert len(rows) == 1 and rows[0]['comparison'] == 'later_memory'
