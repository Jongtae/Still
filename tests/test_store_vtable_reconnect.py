"""#262: a statement that reaches an FTS5 table survives a concurrent schema change.

Evidence class: model-free unit tests over a temporary store.  A real schema change on another
connection while one connection prepares an insert into ``messages`` fails it with "vtable constructor
failed: messages_search" (observed in the parallel suite and in a local stress run); the timing cannot
be forced deterministically, so the failure is injected beneath the store connection here.
"""
import sqlite3
import tempfile
import unittest

from personal_agent.quickstart_store import VTABLE_RECONNECT_ATTEMPTS, QuickStore, _StoreConnection


class _Interrupted(sqlite3.Connection):
    """Fails the first ``failures`` statements the way FTS5 reports a concurrent schema change."""
    failures = 0
    message = 'vtable constructor failed: messages_search'

    def execute(self, sql, parameters=(), /):
        if _Interrupted.failures:
            _Interrupted.failures -= 1
            raise sqlite3.OperationalError(_Interrupted.message)
        return super().execute(sql, parameters)


class _InterruptedStore(_StoreConnection, _Interrupted):
    pass


class VtableReconnect(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(temp.name)
        self.addCleanup(setattr, _Interrupted, 'failures', 0)
        self.addCleanup(setattr, _Interrupted, 'message', _Interrupted.message)

    def connect(self):
        connection = sqlite3.connect(self.store.path, timeout=10, factory=_InterruptedStore)
        self.addCleanup(connection.close)
        return connection

    def messages(self):
        with self.store.db() as db:
            return [row['content'] for row in db.execute('SELECT content FROM messages')]

    def test_store_connections_prepare_again(self):
        with self.store.db() as db:
            self.assertIsInstance(db, _StoreConnection)

    def test_an_interrupted_insert_runs_once_and_is_searchable(self):
        _Interrupted.failures = VTABLE_RECONNECT_ATTEMPTS - 1
        with self.connect() as db:
            db.execute("INSERT INTO messages(role,content,channel,created) VALUES ('user','hello there','web',1.0)")
        self.assertEqual(self.messages(), ['hello there'])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages_search WHERE messages_search MATCH 'hello'").fetchone()[0], 1)

    def test_the_retry_is_bounded(self):
        _Interrupted.failures = VTABLE_RECONNECT_ATTEMPTS
        with self.assertRaisesRegex(sqlite3.OperationalError, 'vtable constructor failed'), self.connect() as db:
            db.execute("INSERT INTO messages(role,content,channel,created) VALUES ('user','hello','web',1.0)")
        self.assertEqual(self.messages(), [])

    def test_other_errors_are_not_retried(self):
        _Interrupted.failures, _Interrupted.message = 1, 'database is locked'
        with self.assertRaisesRegex(sqlite3.OperationalError, 'database is locked'), self.connect() as db:
            db.execute("INSERT INTO messages(role,content,channel,created) VALUES ('user','hello','web',1.0)")
        self.assertEqual(_Interrupted.failures, 0)
        self.assertEqual(self.messages(), [])


if __name__ == '__main__':
    unittest.main()
