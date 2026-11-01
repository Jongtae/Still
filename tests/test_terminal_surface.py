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

    def interrupt(self):
        self.interrupted = getattr(self, 'interrupted', 0) + 1


class Converse(TerminalCase):
    def test_the_owner_talks_until_ctrl_d_and_the_terminal_is_released(self):
        chat = ScriptedChat(['요즘 고양이가 밥을 잘 안 먹어', '   '])
        first_run.converse(self.service, chat, clock=lambda: 10**10)
        self.assertEqual(chat.said[0].strip(), first_run.msg('terminal.ready'))
        self.assertEqual(chat.said[-1], first_run.msg('terminal.bye'))
        jobs = [job for job in self.store.jobs() if job['channel'].startswith('terminal:')]
        self.assertEqual([job['message'] for job in jobs], ['요즘 고양이가 밥을 잘 안 먹어'], 'a blank line is no request')
        self.assertEqual(chat.statuses[0], '', 'nothing is shown before a request')
        since = self.store.job(jobs[0]['id'])['created']
        self.assertEqual(chat.statuses[1], first_run.msg('terminal.working_for', seconds=int(10**10 - since)))
        self.assertIsNone(self.surface.pairing())

    def test_without_a_login_service_the_conversation_says_how_to_run_without_this_terminal(self):
        """#553: the opening and closing lines name `agentos service install` only while none is installed."""
        command = "agentos service install --data '/tmp/my still'"
        hint = first_run.msg('terminal.login_service_hint', command=command)
        for status, hinted in (('not_installed', True), ('running', False), ('stopped', False)):
            with self.subTest(status=status):
                chat = ScriptedChat([])
                first_run.converse(self.service, chat, login_service=lambda status=status: {'status': status},
                                   install_command=command)
                self.assertEqual(hint in chat.said, hinted)
                self.assertEqual(chat.said[-1], first_run.msg('terminal.bye_login_service', command=command)
                                 if hinted else first_run.msg('terminal.bye'))
        self.assertIn(command, hint, 'the hint names the command that keeps this data folder')
        chat = ScriptedChat([])
        first_run.converse(self.service, chat)
        self.assertFalse(any('agentos service install' in line for line in chat.said),
                         'no offer where the service cannot reproduce this start')
        self.assertEqual(chat.said[-1], first_run.msg('terminal.bye'))
        english = first_run.open_terminal('en')
        self.assertIn(f'run `{command}`', english.local(hint), 'an English terminal shows the hint translated, command intact')

    def test_the_working_line_counts_the_seconds_since_the_request_and_ends_with_it(self):
        """#512: measured time since the oldest open request of this conversation; nothing once it is answered."""
        self.surface.attach(lambda text: None)
        self.assertIsNone(self.surface.waiting_since())
        work_id = self.surface.say('요즘 고양이가 밥을 잘 안 먹어')
        since = self.surface.waiting_since()
        self.assertEqual(since, self.store.job(work_id)['created'])
        self.surface.say('하나 더')
        self.assertEqual(self.surface.waiting_since(), since, 'the oldest open request sets the time')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded', delivery='delivered' WHERE channel LIKE 'terminal:%'")
        self.assertIsNone(self.surface.waiting_since())
        self.assertFalse(self.surface.waiting())



TOKEN = '123456789:AAterminal-secret-token-value'


class TokenChat(ScriptedChat):
    def __init__(self, token):
        super().__init__([])
        self.token, self.opened = token, []

    def secret(self, prompt):
        return self.token

    def open(self, url):
        self.opened.append(url)


class TelegramFromConversation(TerminalCase):
    """#438 B2: the owner's AI may ask to connect Telegram; the token is typed in the terminal, hidden."""

    def setUp(self):
        super().setUp()
        self.settings = self.service.settings_orchestrator
        self.interrupts = []
        self.generation = self.surface.attach(self.printed.append, lambda: self.interrupts.append(1))
        self.channel = f'terminal:{self.generation}'
        self.owner = AgentService.settings_owner({'channel': self.channel, 'chat_id': 'owner'})

    def propose(self, channel=None):
        return self.settings.propose(self.owner, channel or self.channel, 'connections', 'connect_chat', 'telegram')

    def test_a_confirmed_draft_opens_the_hidden_prompt_and_the_token_reaches_only_the_service(self):
        draft = self.propose()
        self.assertIn('봇 토큰 입력 칸', draft['response'])
        self.assertIsNone(self.surface.take_request(), 'a draft asks for nothing until the owner confirms it')
        applied = self.settings.confirm(self.owner, self.channel, draft['draft_id'], draft['digest'])
        self.assertEqual(applied['state'], 'requested')
        self.assertEqual(self.interrupts, [1], 'the line being typed ends so the prompt opens at once')
        self.assertEqual(self.surface.take_request(), 'telegram')
        connected = []
        self.service.connect_telegram = lambda body: connected.append(body) or {'url': 'https://t.me/still_bot?start=c'}
        chat = TokenChat(TOKEN)
        first_run._requested_input(self.service, chat, 'telegram')
        self.assertEqual(connected, [{'token': TOKEN}])
        self.assertEqual(chat.opened[-1], 'https://t.me/still_bot?start=c')
        self.assertNotIn(TOKEN, '\n'.join(chat.said + self.printed))
        with self.store.db() as db:
            for table in ('jobs', 'terminal_turns'):
                for row in db.execute(f'SELECT * FROM {table}').fetchall():
                    self.assertNotIn(TOKEN, repr(tuple(row)))

    def offered(self):
        """A Work that asked for the change, its draft, and the notice's choice as the terminal received it."""
        work_id = self.surface.say('휴대폰에서도 대화하고 싶어')
        self.settings.propose(self.owner, self.channel, 'connections', 'connect_chat', 'telegram', work_id=work_id)
        self.service.queue_settings_confirmation(self.store.job(work_id))
        self.assertTrue(self.service.deliver_notification())
        return self.surface.take_request()

    def test_the_draft_notice_is_answered_with_a_choice_and_no_judgment(self):
        """On a fresh install the judgment AI may still be qualifying; a choice is an exact act, like a button."""
        choice = self.offered()
        self.assertEqual(choice['labels'], ('button.apply', 'button.keep'))
        self.assertIn('Telegram을 연결해', choice['text'])
        self.assertFalse([line for line in self.printed if 'Telegram을 연결해' in line],
                         'the notice is drawn with its menu, not printed apart from it')
        self.service.decision_routes.engine = lambda *a, **k: self.fail('a choice needs no judgment')
        self.assertEqual(self.surface.answer_choice(choice, True), 'settings-drafts')
        self.assertEqual(self.surface.take_request(), 'telegram')

    def test_each_menu_shows_the_notice_it_settles(self):
        """Review P1 on #456: with two notices waiting, each menu carries its own preview."""
        first = self.offered()
        work_id = self.surface.say('주말 예약 하나 취소해 줘')
        self.settings.propose(self.owner, self.channel, 'current_context', 'timezone', 'Europe/Paris', work_id=work_id)
        self.service.queue_settings_confirmation(self.store.job(work_id))
        self.assertTrue(self.service.deliver_notification())
        second = self.surface.take_request()
        self.assertIn('Telegram을 연결해', first['text'])
        self.assertIn('Europe/Paris', second['text'])
        self.assertNotIn('Europe/Paris', first['text'])
        shown = []

        class Menu(TokenChat):
            def local(self, text):
                return text

            def choose(self, prompt, options, default=None, preview=None):
                shown.append(preview)
                return '2'
        for choice in (first, second):
            first_run._requested_input(self.service, Menu(TOKEN), choice)
        self.assertEqual(shown, [first['text'], second['text']])

    def test_an_english_terminal_shows_the_draft_and_its_reply_in_english(self):
        """#511: the notice drawn with its menu and Still's own reply follow the terminal language."""
        import io
        from rich.console import Console
        work_id = self.surface.say('I want to talk from my phone too')
        self.settings.propose(self.owner, self.channel, 'connections', 'connect_chat', 'telegram',
                              reason='Connect Telegram so you can chat with Still there.', work_id=work_id)
        self.service.queue_settings_confirmation(self.store.job(work_id))
        self.assertTrue(self.service.deliver_notification())
        choice = self.surface.take_request()
        out, menus = io.StringIO(), []
        term = first_run.RichTerminal('en', Console(file=out, width=400, color_system=None),
                                      reader=lambda prompt, **options: '',
                                      chooser=lambda message, **options: menus.append(message) or '1')
        first_run._requested_input(self.service, term, choice)
        self.service.deliver_one()
        for text in self.printed:  # what the surface printed, through the terminal's own writer
            term.answer(text)
        shown = ''.join(text for _style, text in menus[0].__pt_formatted_text__())
        self.assertIn(first_run.msg('settings_orchestrator.connect_chat_summary', 'en'), shown)
        self.assertIn('Reason: Connect Telegram so you can chat with Still there.', shown)
        self.assertNotRegex(shown, '[가-힣]')
        self.assertIn(first_run.msg('settings_orchestrator.connect_chat_opened', 'en'), out.getvalue())
        self.assertNotRegex(out.getvalue(), '[가-힣]')
        term.answer('연결 대상이 바뀌어서 다시 확인해 볼게요.')
        self.assertIn('연결 대상이 바뀌어서 다시 확인해 볼게요.', out.getvalue(), "a model's own words pass unchanged")

    def test_keeping_the_setting_asks_for_nothing(self):
        choice = self.offered()
        self.surface.answer_choice(choice, False)
        self.assertIsNone(self.surface.take_request())
        self.assertEqual(self.settings.pending_for_work(choice['ref']['work_id']), [])

    def test_a_second_answer_to_the_same_notice_changes_nothing(self):
        choice = self.offered()
        self.surface.answer_choice(choice, True)
        self.surface.take_request()
        self.assertIsNone(self.surface.answer_choice(choice, True))
        self.assertIsNone(self.surface.take_request())
        self.assertIn(first_run.msg('slack.button_stale'), self.printed[-1])

    def test_ctrl_c_at_the_token_prompt_changes_nothing_and_keeps_the_conversation(self):
        chat = TokenChat(TOKEN)
        chat.secret = lambda prompt: (_ for _ in ()).throw(KeyboardInterrupt)
        self.service.connect_telegram = lambda body: self.fail('nothing is connected')
        first_run._requested_input(self.service, chat, 'telegram')
        self.assertIn(first_run.msg('terminal.input_stopped'), '\n'.join(chat.said))

    def test_only_the_attached_terminal_conversation_may_ask(self):
        from personal_agent.settings_orchestrator import CONNECT_CHAT_TERMINAL_ONLY, SettingsError
        for channel in ('http', 'telegram:g1', f'terminal:{int(self.generation) + 1}'):
            with self.subTest(channel=channel), self.assertRaises(SettingsError) as raised:
                self.propose(channel)
            self.assertEqual(str(raised.exception), CONNECT_CHAT_TERMINAL_ONLY)
        self.surface.detach()
        with self.assertRaises(SettingsError):
            self.propose()

    def test_nothing_to_connect_when_telegram_is_connected(self):
        from personal_agent.settings_orchestrator import SettingsError
        self.store.put('telegram', {'enabled': True, 'user_id': 7, 'generation': 'g1'})
        with self.assertRaises(SettingsError):
            self.propose()

    def test_a_draft_confirmed_after_the_terminal_closed_asks_for_nothing(self):
        from personal_agent.settings_orchestrator import SettingsError
        draft = self.propose()
        self.surface.detach()
        with self.assertRaises(SettingsError):
            self.settings.confirm(self.owner, self.channel, draft['draft_id'], draft['digest'])
        self.assertIsNone(self.surface.take_request())
        self.assertEqual(self.interrupts, [])


if __name__ == '__main__':
    unittest.main()
