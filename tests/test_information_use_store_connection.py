"""#1304: the information-use comparison cache must not break the store's writes.

On Apple's libsqlite3 (``ENABLE_SETLK_TIMEOUT``, ``ENABLE_LOCKING_STYLE=1``), any
connection that opens the Still database read-only (``?mode=ro``) in the same process
makes later ``BEGIN IMMEDIATE`` writes fail with ``disk I/O error``
(``SQLITE_IOERR_LOCK``/``EBADF``), whether it stays open or not. No live operation.
"""
import ast
import pathlib
import sqlite3
import threading
import time
import uuid

from personal_agent import information_use as audit
from test_deferred_information_use import store, memory, call, view, MEMBER  # noqa: F401

SRC = pathlib.Path(audit.__file__).resolve().parent


def _cache(store):
    state, _version, _value = audit._cached_comparison(store, ('job', 'key'))
    return state


def test_comparison_cache_connection_is_read_write_opened_and_query_only(store):
    state = _cache(store)
    assert state.db.execute('PRAGMA query_only').fetchone()[0] == 1
    # The cache never writes, but its handle is opened like the store's own writers.
    try:
        state.db.execute('CREATE TABLE should_not_exist(x)')
    except sqlite3.OperationalError as exc:
        assert 'readonly' in str(exc) or 'read-only' in str(exc) or 'query_only' in str(exc)
    else:  # pragma: no cover - the assertion below names the failure
        raise AssertionError('comparison cache connection accepted a write')
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='should_not_exist'").fetchone()[0] == 0


def test_no_server_code_opens_a_sqlite_database_read_only():
    """Structural cause, on every platform: no ``mode=ro`` SQLite URI in ``src``."""
    offenders = []
    for path in SRC.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and 'mode=ro' in node.value:
                offenders.append(f'{path.relative_to(SRC)}:{node.lineno}')
    assert offenders == []


def test_comparison_cache_still_hits_and_invalidates_on_a_write(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    call(store, job, evidence={'outbound_comparison': 'deferred'})
    first = view(store, job)
    state = audit._COMPARISON_CACHE[store]
    assert state.results
    assert view(store, job) == first
    version = state.version
    with store.db() as db:
        db.execute('INSERT INTO notes VALUES (?,?,?)', (job, 'x', 25))
    view(store, job)
    assert state.version != version


def test_store_writes_survive_the_comparison_cache_beside_them(store):
    """The #1304 reproduction against Still's own store and cache.

    Fails on the old ``mode=ro`` cache on an Apple-linked Python; Linux SQLite never
    reproduced it, so there it only holds the behavior.
    """
    _cache(store)
    errors, ok = [], [0]
    end = time.monotonic() + 3

    def writer():
        while time.monotonic() < end:
            try:
                with store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('INSERT INTO notes VALUES (?,?,?)', ('w-' + uuid.uuid4().hex, 'x', 1))
                ok[0] += 1
            except sqlite3.Error as exc:
                errors.append(str(exc))

    def reader():
        while time.monotonic() < end:
            try:
                _cache(store)
            except sqlite3.Error as exc:
                errors.append('reader ' + str(exc))
            time.sleep(0.001)

    threads = [threading.Thread(target=writer) for _ in range(4)] + [threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert ok[0] > 0
    assert errors == []
