"""#438: the terminal where the owner started Still, as a conversation surface.

Model-free: a fake model adapter answers, the real queue, worker and delivery
run (``run_one`` / ``deliver_one``), and the terminal is a list of printed answers.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from personal_agent import first_run
from personal_agent.conversation_surface import registered_surfaces
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

ANSWER = '고양이가 밥을 잘 안 먹으면 먼저 물을 잘 마시는지 보세요.'


def model(url, body, headers=None, timeout=60):
    tools = [t.get('function', {}).get('name') or t.get('name') for t in body.get('tools', [])]
    if 'agentos_connection_probe' in tools:
        return {'message': {'content': '', 'tool_calls': [
            {'id': 'probe', 'function': {'name': 'agentos_connection_probe', 'arguments': {}}}]}}
    return {'message': {'content': ANSWER}}


class TerminalCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')
        self.service = AgentService(self.store, ModelAdapter(model), lambda *a, **k: {'ok': True, 'result': True})
        self.service.steer_spawn = lambda target: target()
        self.service.save_model({'provider': 'ollama', 'endpoint': 'http://127.0.0.1:11434', 'model': 'test-model',
                                 'api_key': ''})
        self.assertTrue(self.service.test_model()['ok'])
        self.surface = self.service.terminal_surface
        self.printed = []

    def turn(self, text):
        work_id = self.surface.say(text)
        self.service.run_one()
        self.service.deliver_one()
        return work_id


class Conversation(TerminalCase):
    def test_a_typed_line_is_answered_in_the_terminal(self):
        generation = self.surface.attach(self.printed.append)
        work_id = self.turn('요즘 고양이가 밥을 잘 안 먹어')
        job = self.store.job(work_id)
        self.assertEqual((job['channel'], job['chat_id']), (f'terminal:{generation}', 'owner'))
        self.assertEqual(job['delivery'], 'sent')
        self.assertEqual(len(self.printed), 1)
        self.assertIn(ANSWER, self.printed[0])
        self.assertIs(self.service.surface_of(job), self.surface)

    def test_waiting_until_the_answer_is_printed(self):
        self.surface.attach(self.printed.append)
        self.surface.say('안녕')
        self.assertTrue(self.surface.waiting())
        self.service.run_one()
        self.service.deliver_one()
        self.assertFalse(self.surface.waiting())

    def test_nothing_is_taken_without_an_attached_terminal(self):
        self.assertIsNone(self.surface.pairing())
        self.assertIsNone(self.surface.say('안녕'))
        self.assertEqual(self.store.jobs(), [])

    def test_an_answer_finished_after_the_terminal_closed_is_not_shown_in_the_next_one(self):
        first = self.surface.attach(self.printed.append)
        work_id = self.surface.say('안녕')
        self.surface.detach()
        self.service.run_one()
        self.service.deliver_one()
        self.assertEqual(self.store.job(work_id)['delivery'], 'cancelled')
        second = self.surface.attach(self.printed.append)
        self.assertNotEqual(first, second)
        self.service.deliver_one()
        self.assertEqual(self.printed, [])

    def test_a_terminal_killed_without_closing_pairs_nothing_in_the_next_start(self):
        """A crash leaves the stored row enabled; a later start without a terminal neither pairs nor prints."""
        self.surface.attach(self.printed.append)
        work_id = self.surface.say('안녕')
        later = AgentService(self.store, ModelAdapter(model), lambda *a, **k: {'ok': True, 'result': True})
        self.assertTrue(later.terminal_surface.config().get('enabled'))
        self.assertIsNone(later.terminal_surface.pairing())
        later.run_one()
        later.deliver_one()
        self.assertEqual(self.store.job(work_id)['delivery'], 'cancelled')
        self.assertEqual(self.printed, [])

    def test_the_terminal_is_the_local_owner_scope(self):
        """This Mac's console: the local connector identity, and no paired source that a restart would change."""
        self.surface.attach(self.printed.append)
        job = self.store.job(self.surface.say('안녕'))
        self.assertEqual(self.surface.connector_owner(job), 'local-owner')
        self.assertIsNone(self.surface.paired_connector_owner())
        reader = SimpleNamespace(store=SimpleNamespace(config=self.store.config))
        self.assertEqual([surface.paired_connector_owner() for surface in registered_surfaces(reader)
                          if surface.name == 'terminal'], [None])


class ScriptedChat:
    """A terminal conversation: typed lines, then Ctrl-D."""

    def __init__(self, lines):
        self.lines, self.said, self.statuses = list(lines), [], []

    def say(self, text=''):
        self.said.append(text)

    def conversation(self):
        import contextlib
        return contextlib.nullcontext()

    def message(self, status):
        self.statuses.append(status())
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)

    def answer(self, text):
        self.said.append(text)


class Converse(TerminalCase):
    def test_the_owner_talks_until_ctrl_d_and_the_terminal_is_released(self):
        chat = ScriptedChat(['요즘 고양이가 밥을 잘 안 먹어', '   '])
        first_run.converse(self.service, chat)
        self.assertEqual(chat.said[0].strip(), first_run.msg('terminal.ready'))
        self.assertEqual(chat.said[-1], first_run.msg('terminal.bye'))
        jobs = [job for job in self.store.jobs() if job['channel'].startswith('terminal:')]
        self.assertEqual([job['message'] for job in jobs], ['요즘 고양이가 밥을 잘 안 먹어'], 'a blank line is no request')
        self.assertEqual(chat.statuses[1], first_run.msg('terminal.working'))
        self.assertIsNone(self.surface.pairing())


if __name__ == '__main__':
    unittest.main()
