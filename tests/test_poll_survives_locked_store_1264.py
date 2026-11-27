"""#1264: a store briefly locked by another reader never ends a surface's poll loop."""
import io
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from rich.console import Console

from personal_agent import first_run
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

LOGGER = 'personal_agent.service'


class PollSurvivesLockedStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.service = AgentService(self.store)
        self.service.LOOP_FAILURE_BACKOFF_SECONDS = 0.01
        self.store.put('telegram', {'enabled': True, 'generation': 'g1', 'cursor': 0, 'user_id': 42})
        self.store.secret('telegram_token', '123456:TEST_TOKEN')

    def run_loop(self, step, interval=0.01):
        worker = threading.Thread(target=self.service._keep_looping, args=('telegram poll', step, interval), daemon=True)
        worker.start()
        worker.join(timeout=5)
        self.service.stop.set()
        self.assertFalse(worker.is_alive(), 'the loop ended when asked to stop')

    def test_a_locked_store_in_the_poll_is_logged_and_the_next_update_is_still_ingested(self):
        updates = [[{'update_id': 1}], [{'update_id': 2}]]
        ingested = []
        self.service.telegram = SimpleNamespace(get_updates=lambda cursor, timeout: updates.pop(0) if updates else [])

        def ingest(update, generation):
            ingested.append(update['update_id'])
        self.service.ingest_update = ingest
        real_settle = self.store.telegram.settle_albums
        calls = []

        def settle(cutoff):
            calls.append(cutoff)
            if len(calls) == 1:
                raise sqlite3.OperationalError('database is locked')
            return real_settle(cutoff)
        done = threading.Event()
        with mock.patch.object(self.store.telegram, 'settle_albums', side_effect=settle), \
                mock.patch.object(AgentService, 'acknowledge_long_work', return_value=[]), \
                self.assertLogs(LOGGER, 'WARNING') as logs:
            # The service's own poll thread, as started for the owner.
            self.service.start()
            try:
                for _ in range(200):
                    if len(ingested) == 2:break
                    done.wait(0.025)
            finally:
                self.service.stop.set()
                for worker in self.service.threads:worker.join(timeout=5)
        self.assertEqual(ingested, [1, 2], 'the poll went on after the locked store')
        self.assertIn('telegram poll tick failed kind=OperationalError', '\n'.join(logs.output))
        self.assertNotIn('database is locked', '\n'.join(logs.output), 'only the error kind is logged')

    def test_any_error_ends_one_tick_not_the_loop(self):
        ticks = []

        def step():
            ticks.append(1)
            if len(ticks) == 1:
                raise RuntimeError('boom')
            self.service.stop.set()
        with self.assertLogs(LOGGER, 'WARNING'):
            self.run_loop(step)
        self.assertEqual(len(ticks), 2)


class TerminalLineSurvivesLockedStoreTests(unittest.TestCase):
    def test_a_failed_working_line_read_keeps_the_last_line_and_raises_nothing(self):
        """The toolbar runs inside prompt_toolkit's loop; an error there stopped on "Press ENTER to continue..."."""
        toolbars = []
        term = first_run.RichTerminal('en', Console(file=io.StringIO()),
                                      reader=lambda prompt, **options: toolbars.append(options) or 'typed',
                                      chooser=lambda *a, **k: None)
        reads = [first_run.msg('terminal.working_for', seconds=3), sqlite3.OperationalError('database is locked')]

        def status():
            value = reads.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        with mock.patch('prompt_toolkit.application.current.get_app'):
            term.message(status)
        toolbar = toolbars[0]['bottom_toolbar']
        first = toolbar()
        with self.assertLogs('personal_agent.first_run', 'WARNING') as logs:
            second = toolbar()
        self.assertIn('kind=OperationalError', logs.output[0])
        self.assertTrue(first and second)
        self.assertEqual(first.split(' ', 1)[1], second.split(' ', 1)[1], 'the last line stays shown')


if __name__ == '__main__':
    unittest.main()
