"""#1543: a Telegram bot that stops answering leaves a reason in agentos.log."""
import tempfile
import unittest

from personal_agent.conversation_handoff import TelegramRejected
from personal_agent.providers import ProviderError
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

LOGGER = 'personal_agent.service'


class ReceivingFailureTests(unittest.TestCase):
    def test_each_cause_is_logged_once_and_409_names_another_agentos(self):
        with self.assertLogs(LOGGER, 'WARNING') as logs:
            cause = AgentService._log_receiving_failure(TelegramRejected(409, 'Conflict'), None)
            cause = AgentService._log_receiving_failure(TelegramRejected(409, 'Conflict'), cause)
            AgentService._log_receiving_failure(ProviderError('x', status='timeout'), cause)
        self.assertEqual(len(logs.output), 2)
        self.assertIn('TelegramRejected 409', logs.output[0])
        self.assertIn('another Still is receiving this bot', logs.output[0])
        self.assertIn('ProviderError timeout', logs.output[1])
        self.assertNotIn('Conflict', ''.join(logs.output), "Telegram's description text is never logged")


class TransientReceivingTests(unittest.TestCase):
    """#65: a connection reset the next poll recovers from is not a receiving failure."""

    RESET = ProviderError('x')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.service = AgentService(self.store)
        self.store.put('telegram', {'enabled': True, 'generation': 'g1', 'cursor': 0, 'user_id': 42})
        self.outcomes = []
        self.service.poll_telegram = self.poll

    def poll(self):
        outcome = self.outcomes.pop(0)
        if outcome:
            raise outcome

    def receive(self, outcome, now):
        self.outcomes.append(outcome)
        self.service.receive_telegram(now=now)

    def state(self):
        return (self.store.config('telegram_status') or {}).get('state')

    def test_one_reset_then_success_is_silent_and_stays_connected(self):
        with self.assertNoLogs(LOGGER, 'INFO'):
            self.receive(None, 0)
        with self.assertNoLogs(LOGGER, 'INFO'):
            self.receive(self.RESET, 1)
            self.assertEqual(self.state(), 'connected')
            self.receive(None, 1.5)
        self.assertEqual(self.state(), 'connected')

    def test_a_lasting_transport_failure_is_reported_once_and_its_recovery_told(self):
        with self.assertNoLogs(LOGGER, 'INFO'):
            self.receive(self.RESET, 0)
            self.receive(ProviderError('x', status='timeout'), 9.9)
        with self.assertLogs(LOGGER, 'INFO') as logs:
            self.receive(self.RESET, 10)
            self.assertEqual(self.state(), 'error')
            self.receive(self.RESET, 11)
            self.receive(None, 12)
        self.assertEqual([line.split(':', 2)[-1] for line in logs.output],
                         ['telegram: receiving failed (ProviderError)', 'telegram: receiving again'])
        self.assertEqual(self.state(), 'connected')
        with self.assertNoLogs(LOGGER, 'INFO'):
            self.receive(self.RESET, 20)
        self.assertEqual(self.state(), 'connected', 'the grace starts over after a recovery')

    def test_a_refusal_is_reported_on_the_first_failure(self):
        with self.assertLogs(LOGGER, 'WARNING') as logs:
            self.receive(TelegramRejected(409, 'Conflict'), 0)
        self.assertIn('TelegramRejected 409', logs.output[0])
        self.assertEqual(self.state(), 'error')


class UnpairedChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.service = AgentService(self.store)
        self.store.put('telegram', {'enabled': True, 'generation': 'g1', 'cursor': 0, 'user_id': 42})

    def message(self, update_id, sender, text='hello'):
        return {'update_id': update_id, 'message': {'from': {'id': sender}, 'chat': {'id': sender, 'type': 'private'},
                                                    'text': text}}

    def test_a_message_from_an_unpaired_chat_is_logged_without_its_text_or_sender(self):
        with self.assertLogs(LOGGER, 'INFO') as logs:
            self.service.ingest_update(self.message(1, 99, 'private words'), 'g1')
            self.service.ingest_update(self.message(2, 99, 'more words'), 'g1')
        unpaired = [line for line in logs.output if 'not paired' in line]
        self.assertEqual(len(unpaired), 1, 'at most one line every ten minutes')
        self.assertIn('pairing link', unpaired[0])
        self.assertNotIn('private words', unpaired[0])
        self.assertNotIn('99', unpaired[0])
        self.assertEqual(self.store.jobs(), [])

    def test_the_paired_owner_is_not_reported(self):
        with self.assertNoLogs(LOGGER, 'INFO'):
            self.service._unpaired_logged_at = 0
            self.service.ingest_update(self.message(1, 42), 'g1')
        self.assertTrue(self.store.jobs())


if __name__ == '__main__':
    unittest.main()
