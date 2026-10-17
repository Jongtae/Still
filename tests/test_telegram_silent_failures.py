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
