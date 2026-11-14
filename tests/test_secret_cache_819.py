"""#819: the stored-secret pass reads secrets once per secrets revision.

Real ``QuickStore`` in a temporary directory; file reads and lock calls are counted
by wrapping the standard-library functions the store uses.
"""
import fcntl
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import current_context
from personal_agent.current_context import redact_known_secrets
from personal_agent.quickstart_store import QuickStore

TEXT = ('An ordinary line of owner text. ' * 60)


class SecretsReadOncePerRevision(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = QuickStore(self.root)
        self.value = 'telegram-token-0123456789'
        self.store.secret('telegram_token', self.value)
        # A replaced value leaves a retired mark in the ledger.
        self.store.secret('telegram_token', 'telegram-token-replaced-99')
        self.store.secret('telegram_token', self.value)

    def counted(self, calls):
        reads, locks = [], []
        real_read_text, real_read_bytes, real_flock = Path.read_text, Path.read_bytes, fcntl.flock
        with mock.patch.object(Path, 'read_text', lambda path, *a, **k: reads.append(path.name) or real_read_text(path, *a, **k)), \
             mock.patch.object(Path, 'read_bytes', lambda path, *a, **k: reads.append(path.name) or real_read_bytes(path, *a, **k)), \
             mock.patch.object(fcntl, 'flock', lambda fd, op: locks.append(op) or real_flock(fd, op)):
            for _ in range(calls):
                out = redact_known_secrets(self.store, TEXT + self.value + ' telegram-token-replaced-99')
        return out, reads, locks

    def settle(self):
        """Age the secrets files past the racy window, as any file not written in the last seconds is."""
        old = os.stat(self.store.secret_path).st_mtime - 60
        for path in (self.store.secret_path, self.store.retired_secrets_path):
            if path.exists():
                os.utime(path, (old, old))

    def test_many_calls_read_the_secrets_and_the_ledger_once_and_take_no_lock(self):
        self.settle()
        redact_known_secrets(self.store, 'warm up')
        out, reads, locks = self.counted(200)
        self.assertNotIn(self.value, out)
        self.assertNotIn('telegram-token-replaced-99', out)
        self.assertEqual(out.count('[redacted]'), 2)
        self.assertEqual(reads, [])
        self.assertEqual(locks, [])

    def test_a_cold_cache_reads_once_then_not_again(self):
        self.settle()
        _out, cold, _locks = self.counted(1)
        self.assertIn('connections.json', cold)
        self.assertIn('retired-secret-marks.json', cold)
        _out, warm, warm_locks = self.counted(50)
        self.assertEqual((warm, warm_locks), ([], []))

    def test_a_just_written_file_is_compared_by_content_not_trusted_by_metadata(self):
        # Modified within the racy window: its bytes are compared on each call, without the lock.
        redact_known_secrets(self.store, 'warm up')
        _out, reads, locks = self.counted(10)
        self.assertEqual(locks, [])
        self.assertTrue(set(reads) <= {'connections.json', 'retired-secret-marks.json'})
        self.assertLessEqual(len(reads), 20)

    def test_a_new_value_is_replaced_at_once(self):
        self.settle()
        redact_known_secrets(self.store, 'warm up')
        self.store.secret('mattermost_token', 'mattermost-token-ABCDEFGHIJ')
        self.assertEqual(redact_known_secrets(self.store, 'x mattermost-token-ABCDEFGHIJ y'), 'x [redacted] y')

    def test_a_rotation_replaces_the_new_and_the_retired_value(self):
        self.settle()
        redact_known_secrets(self.store, 'warm up')
        rotated = 'telegram-token-ROTATED-0001'
        self.store.secret('telegram_token', rotated)
        self.assertEqual(redact_known_secrets(self.store, rotated + ' ' + self.value),
                         '[redacted] [redacted]')

    def test_a_removed_value_stays_replaced_by_its_retired_mark(self):
        self.settle()
        redact_known_secrets(self.store, 'warm up')
        self.assertTrue(self.store.remove_secret('telegram_token'))
        self.assertEqual(redact_known_secrets(self.store, 'a ' + self.value), 'a [redacted]')

    def test_a_retirement_alone_is_seen(self):
        self.settle()
        redact_known_secrets(self.store, 'warm up')
        self.store.retire_secret_values(['decrypted-api-credential-77'])
        self.assertEqual(redact_known_secrets(self.store, 'b decrypted-api-credential-77'), 'b [redacted]')

    def test_another_process_writing_the_same_size_value_is_seen(self):
        # A second store over the same files stands in for the CLI's MCP bridge process.
        self.settle()
        redact_known_secrets(self.store, 'warm up')
        same_size = 'telegram-token-9876543210'
        self.assertEqual(len(same_size), len(self.value))
        QuickStore(self.root).secret('telegram_token', same_size)
        self.assertEqual(redact_known_secrets(self.store, same_size), '[redacted]')

    def test_same_metadata_within_the_racy_window_still_compares_content(self):
        # A write that reused the inode with the same size and timestamp tick is caught by content.
        redact_known_secrets(self.store, 'warm up')
        stat = current_context._file_stat
        frozen = {path: stat(path) for path in (self.store.secret_path, self.store.retired_secrets_path)}
        QuickStore(self.root).secret('telegram_token', 'telegram-token-9876543210')
        with mock.patch.object(current_context, '_file_stat', lambda path: frozen[path]):
            self.assertEqual(redact_known_secrets(self.store, 'telegram-token-9876543210'), '[redacted]')

    def test_an_unreadable_ledger_still_withholds_after_a_cached_read(self):
        self.settle()
        redact_known_secrets(self.store, 'warm up')
        self.store.write_private(self.store.retired_secrets_path, '{not json')
        with self.assertRaises(ValueError):
            redact_known_secrets(self.store, 'any text')

    def test_the_literal_match_minimum_is_unchanged(self):
        self.store.secret('mattermost_token', 'short01')
        self.assertEqual(current_context.MIN_REDACTED_CHARS, 8)
        self.assertEqual(redact_known_secrets(self.store, 'code short01'), 'code short01')


    def test_a_repeated_text_is_scanned_once_per_revision(self):
        import hmac
        self.settle()
        redact_known_secrets(self.store, 'warm up')
        real = hmac.digest
        digests = []
        with mock.patch.object(hmac, 'digest', lambda *a: digests.append(1) or real(*a)):
            first = redact_known_secrets(self.store, TEXT + self.value)
            scanned = len(digests)
            for _ in range(20):
                self.assertEqual(redact_known_secrets(self.store, TEXT + self.value), first)
        self.assertGreater(scanned, 0)
        self.assertEqual(len(digests), scanned)
        self.store.secret('telegram_token', 'telegram-token-AFTER-0002')
        self.assertEqual(redact_known_secrets(self.store, 'telegram-token-AFTER-0002'), '[redacted]')


if __name__ == '__main__':
    unittest.main()

    def test_threads_redacting_during_rotations_never_fail_or_keep_a_stale_value(self):
        import threading
        errors, stop = [], threading.Event()

        def redact():
            while not stop.is_set():
                try:
                    redact_known_secrets(self.store, TEXT[:200] + ' ' + str(threading.get_ident()))
                except Exception as exc:  # pragma: no cover - the assertion reports it
                    errors.append(exc)
        workers = [threading.Thread(target=redact) for _ in range(6)]
        for worker in workers:
            worker.start()
        rotated = []
        for index in range(15):
            value = f'telegram-token-ROTATE-{index:04d}'
            self.store.secret('telegram_token', value)
            rotated.append(value)
            self.assertEqual(redact_known_secrets(self.store, value), '[redacted]')
        stop.set()
        for worker in workers:
            worker.join(timeout=10)
        self.assertEqual(errors, [])
        for value in rotated:
            self.assertEqual(redact_known_secrets(self.store, value), '[redacted]')

    def test_a_read_in_a_new_thread_works(self):
        """The partial-read flag is per thread: a thread that never set it reads it as unset."""
        import threading
        out = []
        self.store._known_secret_material = None
        thread = threading.Thread(target=lambda: out.append(redact_known_secrets(self.store, self.value)))
        thread.start()
        thread.join(timeout=10)
        self.assertEqual(out, ['[redacted]'])


class EnvelopeRedactedOnce(unittest.TestCase):
    def test_the_prompt_envelope_passes_the_stored_secret_pass_once(self):
        from personal_agent.quickstart_service import AgentService
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = QuickStore(Path(tmp.name))
        store.secret('telegram_token', 'telegram-token-0123456789')
        service = AgentService(store)
        job = store.enqueue('정리해 줘', 'k-envelope')
        envelope = 'Owner request: 정리해 줘 with telegram-token-0123456789 at /Users/owner/a.txt'
        seen = []
        real = current_context.redact_known_secrets
        with mock.patch('personal_agent.quickstart_service.redact_known_secrets',
                        lambda store_, text: seen.append(text) or real(store_, text)):
            service.record_turn_sent(job, sent=envelope, instructions='', instructions_channel='prompt')
        self.assertEqual(seen.count(envelope), 1)
        kept = store.turn_provenance(job)['prompt_envelope']
        self.assertNotIn('telegram-token-0123456789', kept)
        self.assertIn('/Users/owner/a.txt', kept, 'a local record keeps the real path')
