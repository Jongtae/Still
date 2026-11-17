"""#921 PR-1: real SQLite boundaries, deterministic clocks, no AI transport."""

import datetime
import json
import sqlite3

import pytest

from personal_agent import quickstart_store as store_module
from personal_agent.memory_terms import (
    ELIGIBLE_SQL, FAIL_LIMIT, MAX_TERMS, PROMPT_VERSION, TERM_CHARS, TERMS_CHARS,
    build_meta, cleared_meta, eligible, filter_terms, parse_meta,
)
from personal_agent.owner_forget import ForgetError, OwnerForget
from personal_agent.quickstart_store import QuickStore

OWNER = 'local-owner'
TODAY = '2026-11-17'
NOW = datetime.datetime(2026, 11, 17, 12, tzinfo=datetime.timezone.utc).timestamp()


class Clock:
    now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(store_module.time, 'time', clock)
    monkeypatch.setattr('personal_agent.current_context.owner_today',
                        lambda store: datetime.datetime.fromtimestamp(clock(), datetime.timezone.utc).date().isoformat())
    return clock


@pytest.fixture
def store(tmp_path, clock):
    return QuickStore(tmp_path / 'state')


def raw_row(store, memory_id):
    with store.db() as db:
        row = db.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
        return dict(row) if row else None


def meta(status='keyed', previous=None, **kwargs):
    return build_meta(status, previous=previous, at=NOW, **kwargs)


def save(store, key='profile.note', content='canonical words', terms='expansion', **kwargs):
    return store.save_memory(key, content, retrieval_terms=terms,
                             retrieval_terms_meta=kwargs.pop('retrieval_terms_meta', meta()), **kwargs)


def hits(store, term='expansion', **kwargs):
    kwargs.setdefault('holding_on', TODAY)
    return [row['id'] for row in store.search_memories(OWNER, [term], **kwargs)]


def set_v1(store):
    """An actual populated two-column index and its original triggers."""
    with store.db() as db:
        db.executescript('''
            DROP TRIGGER memories_search_ai;
            DROP TRIGGER memories_search_ad;
            DROP TRIGGER memories_search_au;
            DROP TABLE memories_search;
            CREATE VIRTUAL TABLE memories_search USING fts5(memory_key, content,
                content='memories', content_rowid='rowid', tokenize='unicode61');
            CREATE TRIGGER memories_search_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_search(rowid,memory_key,content) VALUES(new.rowid,new.memory_key,new.content);
            END;
            CREATE TRIGGER memories_search_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_search(memories_search,rowid,memory_key,content)
                    VALUES('delete',old.rowid,old.memory_key,old.content);
            END;
            CREATE TRIGGER memories_search_au AFTER UPDATE OF memory_key,content ON memories BEGIN
                INSERT INTO memories_search(memories_search,rowid,memory_key,content)
                    VALUES('delete',old.rowid,old.memory_key,old.content);
                INSERT INTO memories_search(rowid,memory_key,content) VALUES(new.rowid,new.memory_key,new.content);
            END;
            INSERT INTO memories_search(memories_search) VALUES('rebuild');
            UPDATE memory_search_meta SET version=1;
        ''')


def index_schema(store):
    with store.db() as db:
        return [tuple(row) for row in db.execute("SELECT name,sql FROM sqlite_master WHERE name='memories_search' OR name LIKE 'memories_search_a%' ORDER BY name")]


def spy_statements(monkeypatch, fail_rebuild=False):
    statements = []
    original = store_module._StoreConnection.execute

    def execute(db, sql, parameters=(), /):
        statements.append(sql)
        if fail_rebuild and sql == "INSERT INTO memories_search(memories_search) VALUES('rebuild')":
            raise sqlite3.OperationalError('injected rebuild failure')
        return original(db, sql, parameters)

    monkeypatch.setattr(store_module._StoreConnection, 'execute', execute)
    return statements


def test_fresh_v2_terms_and_trigger_updates(store):
    assert store.memory_search_available and store.memory_search_version == 2
    with store.db() as db:
        assert [row['name'] for row in db.execute('PRAGMA table_info(memories_search)')] == ['memory_key', 'content', 'retrieval_terms']
        assert db.execute('SELECT version FROM memory_search_meta').fetchone()[0] == 2
    row = save(store)
    assert hits(store) == [row['id']]
    with store.db() as db:
        db.execute('UPDATE memories SET retrieval_terms=? WHERE id=?', ('replacement', row['id']))
    assert hits(store) == []
    assert hits(store, 'replacement') == [row['id']]
    # Derived terms are not presented as canonical Memory content.
    assert 'retrieval_terms' not in store.memory(row['id'])


def test_populated_v1_upgrade_and_idempotent_reopen(store, monkeypatch):
    set_v1(store)
    rows = [save(store, f'profile.note.{i}', f'canonical token{i}', terms=f'expanded{i}') for i in range(4)]
    statements = spy_statements(monkeypatch)
    upgraded = QuickStore(store.root)
    assert upgraded.memory_search_version == 2
    for i, row in enumerate(rows):
        assert hits(upgraded, f'token{i}') == [row['id']]
        assert hits(upgraded, f'expanded{i}') == [row['id']]
    assert sum("VALUES('rebuild')" in sql and 'memories_search' in sql for sql in statements) == 1
    statements.clear()
    reopened = QuickStore(store.root)
    assert reopened.memory_search_version == 2
    assert not any("VALUES('rebuild')" in sql and 'memories_search' in sql for sql in statements)


def test_add_columns_before_legacy_index_migration(store):
    set_v1(store)
    row = store.save_memory('profile.legacy', 'legacy content')
    with store.db() as db:
        db.execute('ALTER TABLE memories DROP COLUMN retrieval_terms')
        db.execute('ALTER TABLE memories DROP COLUMN retrieval_terms_meta')
    upgraded = QuickStore(store.root)
    assert upgraded.memory_search_version == 2
    assert hits(upgraded, 'legacy') == [row['id']]
    assert raw_row(upgraded, row['id'])['retrieval_terms_meta'] is None


@pytest.mark.parametrize('existing', [True, False])
def test_probe_failure_uses_like_and_leaves_durable_index(store, monkeypatch, tmp_path, existing):
    schema = index_schema(store)

    def unavailable(db):
        raise sqlite3.OperationalError('no such module: fts5')

    monkeypatch.setattr(QuickStore, '_probe_memory_search', staticmethod(unavailable))
    reopened = QuickStore(store.root if existing else tmp_path / 'no-fts')
    assert not reopened.memory_search_available
    row = save(reopened)
    result = reopened.search_memories(OWNER, ['expansion'], include_mode=True, holding_on=TODAY)
    assert result['search_mode'] == 'bounded-like'
    assert [item['id'] for item in result['memories']] == [row['id']]
    assert index_schema(reopened) == (schema if existing else [])


def test_successful_probe_is_temp_and_does_not_rebuild(store, monkeypatch):
    row = save(store)
    schema = index_schema(store)
    statements = spy_statements(monkeypatch)
    reopened = QuickStore(store.root)
    assert index_schema(reopened) == schema
    assert hits(reopened) == [row['id']]
    assert 'CREATE VIRTUAL TABLE temp.memory_search_probe USING fts5(probe)' in statements
    with reopened.db() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_search_probe'").fetchone()
        assert not db.execute("SELECT 1 FROM sqlite_temp_master WHERE name='memory_search_probe'").fetchone()
    assert not any(sql.startswith('DROP TABLE IF EXISTS memories_search') for sql in statements)


@pytest.mark.parametrize('prior', ['v1', 'downgraded', 'none'])
def test_savepoint_fault_restores_actual_prior_mode_and_retries(store, monkeypatch, caplog, prior):
    row = save(store)
    if prior == 'v1':
        set_v1(store)
    elif prior == 'downgraded':
        with store.db() as db:
            db.execute('UPDATE memory_search_meta SET version=1')
    else:
        with store.db() as db:
            db.executescript('''DROP TRIGGER memories_search_ai; DROP TRIGGER memories_search_ad;
                DROP TRIGGER memories_search_au; DROP TABLE memories_search; DROP TABLE memory_search_meta;''')
    schema = index_schema(store)
    with monkeypatch.context() as patch:
        statements = spy_statements(patch, fail_rebuild=True)
        reopened = QuickStore(store.root)
        assert reopened.memory_search_available is (prior != 'none')
        assert reopened.memory_search_version == (0 if prior == 'none' else 1)
        assert index_schema(reopened) == schema
        assert 'ROLLBACK TO memory_search_v2' in statements
        assert 'RELEASE memory_search_v2' in statements
        # V1 still uses the unweighted rank and restored triggers support writes.
        assert hits(reopened, 'canonical') == [row['id']]
        newer = save(reopened, 'profile.new', 'searchable newest')
        assert hits(reopened, 'newest') == [newer['id']]
        if prior != 'none':
            assert any('ORDER BY bm25(memories_search),' in sql for sql in statements)
    assert 'memory-search-migration-failed' in caplog.text
    upgraded = QuickStore(store.root)
    assert upgraded.memory_search_available and upgraded.memory_search_version == 2
    assert set(hits(upgraded)) == {row['id'], newer['id']}


def test_downgraded_three_column_index_is_rebuilt(store, monkeypatch):
    row = save(store)
    with store.db() as db:
        db.execute('UPDATE memory_search_meta SET version=1')
    statements = spy_statements(monkeypatch)
    reopened = QuickStore(store.root)
    assert reopened.memory_search_version == 2
    assert hits(reopened) == [row['id']]
    assert "INSERT INTO memories_search(memories_search) VALUES('rebuild')" in statements


def test_version_two_stamp_does_not_hide_a_two_column_table(store):
    set_v1(store)
    row = save(store)
    with store.db() as db:
        db.execute('UPDATE memory_search_meta SET version=2')
    upgraded = QuickStore(store.root)
    assert upgraded.memory_search_version == 2
    assert hits(upgraded) == [row['id']]


@pytest.mark.parametrize('change', ['supersede', 'correction', 'expiry', 'forget', 'retract', 'delete'])
def test_terms_do_not_match_after_lifecycle_change(store, clock, change):
    row = save(store, valid_until=TODAY)
    assert hits(store) == [row['id']]
    if change in ('supersede', 'correction'):
        new = store.save_memory('profile.note', 'new canonical value', corrected=change == 'correction')
        assert raw_row(store, row['id'])['retrieval_terms'] == 'expansion'
        assert raw_row(store, new['id'])['retrieval_terms'] is None
    elif change == 'expiry':
        assert hits(store, holding_on='2026-11-18') == []
        return
    elif change in ('forget', 'delete'):
        OwnerForget(store, clock).forget(OWNER, 'memory:' + row['id'], delete=change == 'delete')
    else:
        store.retract_memory(OWNER, row['id'], row['content_digest'], now=clock())
        actual = raw_row(store, row['id'])
        assert actual['retrieval_terms'] is None
        assert parse_meta(actual['retrieval_terms_meta'])['g'] == 1
        assert parse_meta(actual['retrieval_terms_meta'])['status'] == 'cleared'
    assert hits(store) == []
    if change in ('forget', 'retract', 'delete'):
        with store.db() as db:
            assert db.execute("SELECT count(*) FROM memories_search WHERE memories_search MATCH 'expansion'").fetchone()[0] == 0
    if change == 'delete':
        assert raw_row(store, row['id']) is None


def test_forget_chain_generations_survive_statuses_and_two_undos(store, clock):
    older = save(store, retrieval_terms_meta=meta(previous={'g': 6}))
    current = save(store, content='new canonical words')
    forget = OwnerForget(store, clock)
    for generation in (1, 2):
        receipt = forget.forget(OWNER, 'memory:' + current['id'])
        for row, expected in ((current, generation), (older, 6 + generation)):
            actual = raw_row(store, row['id'])
            assert actual['retrieval_terms'] is None
            assert parse_meta(actual['retrieval_terms_meta']) == parse_meta(cleared_meta({'g': expected - 1}, at=clock()))
        with pytest.raises(ForgetError, match='.') as error:
            forget.forget(OWNER, 'memory:' + current['id'])
        assert error.value.code == 'stale'
        forget.undo(OWNER, receipt['receipt'])
        assert eligible(raw_row(store, current['id'])['retrieval_terms_meta'])
        assert hits(store) == []
        for status in ('keyed', 'empty', 'failed'):
            previous = raw_row(store, current['id'])['retrieval_terms_meta']
            updated = meta(status, previous, n=1 if status == 'failed' else None)
            assert parse_meta(updated)['g'] == generation
            assert store.write_retrieval_terms(OWNER, current['id'], terms='', meta=updated,
                                              expected_meta=previous, content_digest=current['content_digest'], today=TODAY)


def test_retract_increments_an_existing_generation(store, clock):
    row = save(store, retrieval_terms_meta=meta(previous={'g': 8}))
    store.retract_memory(OWNER, row['id'], row['content_digest'], now=clock())
    actual = raw_row(store, row['id'])
    assert actual['retrieval_terms'] is None
    assert parse_meta(actual['retrieval_terms_meta'])['g'] == 9


@pytest.mark.parametrize('expected', [None, meta(), cleared_meta(None, at=NOW)])
def test_guarded_matching_write_is_one_statement(store, monkeypatch, expected):
    row = save(store, retrieval_terms_meta=expected)
    statements = spy_statements(monkeypatch)
    assert store.write_retrieval_terms(OWNER, row['id'], terms='added', meta=meta(previous=expected),
                                      expected_meta=expected, content_digest=row['content_digest'], today=TODAY)
    assert len(statements) == 1 and statements[0].startswith('UPDATE memories SET')
    assert hits(store, 'added') == [row['id']]


@pytest.mark.parametrize('change', ['supersede', 'correction', 'forget', 'forget-undo', 'retract',
                                    'delete', 'content-edit', 'date-passed', 'newer-meta', 'other-owner'])
@pytest.mark.parametrize('initial_meta', [None, meta()])
def test_stale_guarded_write_never_changes_or_recreates_row(store, clock, change, initial_meta):
    row = save(store, retrieval_terms_meta=initial_meta, valid_until=TODAY)
    snapshot = raw_row(store, row['id'])
    forget = OwnerForget(store, clock)
    owner = OWNER
    if change in ('supersede', 'correction'):
        store.save_memory('profile.note', 'new canonical value', corrected=change == 'correction')
    elif change.startswith('forget'):
        receipt = forget.forget(OWNER, 'memory:' + row['id'])
        if change == 'forget-undo':
            forget.undo(OWNER, receipt['receipt'])
    elif change == 'retract':
        store.retract_memory(OWNER, row['id'], row['content_digest'], now=clock())
    elif change == 'delete':
        forget.forget(OWNER, 'memory:' + row['id'], delete=True)
    elif change == 'content-edit':
        with store.db() as db:
            db.execute('UPDATE memories SET content=?,content_digest=? WHERE id=?',
                       ('edited', store.memory_digest('profile.note', 'edited'), row['id']))
    elif change == 'date-passed':
        clock.now += 86400
    elif change == 'newer-meta':
        assert store.write_retrieval_terms(OWNER, row['id'], terms='newrevision', meta=meta('empty', run='new'),
                                          expected_meta=initial_meta, content_digest=row['content_digest'], today=TODAY)
    else:
        owner = 'another-owner'
    before = raw_row(store, row['id'])
    today = datetime.datetime.fromtimestamp(clock(), datetime.timezone.utc).date().isoformat()
    assert not store.write_retrieval_terms(owner, row['id'], terms='staleresult', meta=meta('keyed', run='old'),
                                          expected_meta=snapshot['retrieval_terms_meta'],
                                          content_digest=snapshot['content_digest'], today=today)
    assert raw_row(store, row['id']) == before
    assert hits(store, 'staleresult', holding_on=today) == []


@pytest.mark.parametrize('value', [None, '', '{broken', '[]', 'null', '5'])
def test_parse_absent_and_invalid_meta(value):
    assert parse_meta(value) == {'g': 0}


@pytest.mark.parametrize('value,expected', [
    (None, True), ('bad json', False), ('{}', False), ('[]', False),
    (meta('keyed'), False), (meta('empty'), False), (meta('oversize'), False),
    (meta('cleared'), True), (meta('failed', n=0), True), (meta('failed', n=2), True),
    (meta('failed', n=FAIL_LIMIT), False), (meta('failed'), False),
    (meta('failed', n='2'), False), (meta('failed', n=True), False),
])
def test_eligibility_python_and_json1_agree(store, value, expected):
    row = save(store, retrieval_terms_meta=value)
    with store.db() as db:
        selected = db.execute(f'SELECT id FROM memories WHERE id=? AND {ELIGIBLE_SQL}', (row['id'],)).fetchone()
    assert eligible(value) is expected
    assert bool(selected) is expected


def test_meta_is_canonical_and_attributable():
    value = build_meta('keyed', previous='{"g":7}', route='judgment', model='active',
                       observed_model='actual', policy='policy', run='memory-terms:1', at=NOW)
    parsed = parse_meta(value)
    assert parsed == dict(v=PROMPT_VERSION, g=7, status='keyed', route='judgment', model='active',
                          observed_model='actual', policy='policy', run='memory-terms:1', at=NOW)
    assert value == json.dumps(parsed, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    assert parse_meta(meta())['observed_model'] == 'not reported'
    assert parse_meta('{"status":"keyed"}')['g'] == 0


@pytest.mark.parametrize('secret', ['LiteralStoredSecretValue', 'CaseSensitiveAbCdEf'])
def test_raw_stored_secrets_drop_all_terms(store, secret):
    store.secret('model_key', secret)
    assert filter_terms(store, 'profile.note', 'canonical', 'safe ' + secret + ' expansion') == ('', 'secret')


def test_punctuation_credential_shape_checked_before_tokenization(store):
    assert filter_terms(store, 'profile.note', 'canonical', 'safe sk-abcdefghijklmnopqrstuv expansion') == ('', 'secret')
    assert filter_terms(store, 'profile.account.password', 'canonical', 'hunter2') == ('', 'secret')


def test_filter_casefold_dedup_content_key_and_number_rules(store):
    assert filter_terms(store, 'profile.Favourite_place', 'Seoul café 2026',
                        'SEOUL café PROFILE favourite PLACE travel TRAVEL 여행 2027 abc9') == ('travel 여행', None)
    # A present digit token passes the number condition but is still dropped
    # by the brief's earlier content-duplicate rule (its test bullet conflicts).
    assert filter_terms(store, 'profile.note', '2026', '2026 2027') == ('', None)
    assert filter_terms(store, 'profile.note', 'canonical', '____!!!') == ('', None)


def test_filter_term_count_length_and_total_bounds(store):
    words = ['word' + chr(97 + i // 26) + chr(97 + i % 26) for i in range(30)]
    assert filter_terms(store, 'profile.note', '', ' '.join(words))[0].split() == words[:MAX_TERMS]
    longest = 'a' * TERM_CHARS
    assert filter_terms(store, 'profile.note', '', longest + ' ' + 'b' * (TERM_CHARS + 1)) == (longest, None)
    words = [chr(97 + i) * TERM_CHARS for i in range(10)]
    bounded = filter_terms(store, 'profile.note', '', ' '.join(words))[0]
    assert len(bounded) <= TERMS_CHARS
    assert bounded == ' '.join(words[:6])
    exact = ' '.join(words[:6]) + ' ' + 'z' * 10
    assert len(exact) == TERMS_CHARS
    assert filter_terms(store, 'profile.note', '', exact + ' extra')[0] == exact


def test_misleading_term_is_indexed_but_content_match_ranks_first(store):
    terms, reason = filter_terms(store, 'profile.false', 'quiet green fields', 'volcano')
    assert reason is None and terms == 'volcano'  # Not a relevance filter.
    expanded = save(store, 'profile.false', 'quiet green fields', terms=terms)
    content = save(store, 'profile.right', 'active volcano nearby', terms='')
    assert hits(store, 'volcano') == [content['id'], expanded['id']]


@pytest.mark.parametrize('fts', [True, False])
def test_terms_owner_prefix_holding_and_state_filters_before_limit(store, fts):
    wanted = save(store, 'profile.wanted', valid_until=TODAY)
    save(store, 'profile.foreign', owner_id='other-owner')
    save(store, 'record.other')
    save(store, 'profile.expired', valid_until='2026-11-16')
    gone = save(store, 'profile.gone')
    store.save_memory('profile.gone', 'superseding canonical')
    store.memory_search_available = fts
    assert hits(store, key_prefix='profile.', limit=1) == [wanted['id']]
    assert gone['id'] not in hits(store)
    assert hits(store, '" OR *') == []


def test_status_counts_current_holding_profile_rows_only(store, clock):
    for i, value in enumerate([None, meta('cleared'), meta('failed', n=2), meta('failed', n=3),
                               meta('keyed'), meta('empty'), meta('oversize')]):
        save(store, f'profile.count.{i}', retrieval_terms_meta=value)
    save(store, 'profile.expired', valid_until='2026-11-16')
    save(store, 'profile.foreign', owner_id='other-owner')
    save(store, 'record.note')
    old = save(store, 'profile.forgotten')
    OwnerForget(store, clock).forget(OWNER, 'memory:' + old['id'])
    counts = store.memory_status_counts(OWNER)
    assert counts['retrieval_terms'] == {'processed': 4, 'keyed': 1, 'eligible': 3, 'oversize': 1}
    assert counts['current_memory_count'] == 9
    assert counts['candidate_counts'] == {'pending': 0, 'accepted': 0, 'rejected': 0}
