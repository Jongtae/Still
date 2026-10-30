import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

from personal_agent import first_run
from personal_agent.quickstart_store import QuickStore

PASSWORD = 'correct horse battery'


class ScriptedTerminal:
    def __init__(self, asks=(), secrets=(), on_open=None):
        self.asks, self.secrets = list(asks), list(secrets)
        self.lines, self.commands, self.opened = [], [], []
        self.on_open = on_open

    def say(self, text=''):
        self.lines.append(text)

    def ask(self, prompt):
        answer = self.asks.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def secret(self, prompt):
        answer = self.secrets.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def run(self, argv):
        self.commands.append(argv)
        return 0

    def open(self, url):
        self.opened.append(url)
        if self.on_open:
            self.on_open(url)

    def banner(self, version=''):
        self.lines.append(f'Still {version}')

    def title(self, text):
        self.lines.append(text)

    def mark(self, mark, text):
        self.lines.append(f'{mark} {text}')

    def choose(self, prompt, options, default=None):
        self.choices = getattr(self, 'choices', []) + [(options, default)]
        return self.ask(prompt) or default or ''

    def text(self):
        return '\n'.join(self.lines)


class FakeFinder:
    def __init__(self, found):
        self.found = set(found)

    def __call__(self, command):
        return f'/usr/local/bin/{command}' if command in self.found else None


class FakeEngines:
    def __init__(self, found):
        self.finder = FakeFinder(found)


class FakeService:
    def __init__(self, found=('codex', 'claude')):
        self.subscription_engines = FakeEngines(found)
        self.engine = ''
        self.model_ready = False
        self.calls = []
        self.model_ok = True
        self.connect_error = None
        self.logins = {}
        self.main_ai = SimpleNamespace(current=lambda: self.engine or ('openai' if self.model_ready else ''))
        self.judgment = {'effective': {'state': 'attention'}, 'qualification': None}
        self.decision_routes = SimpleNamespace(
            mode=lambda: 'follow_main', status=lambda: self.judgment,
            follow_main_switched=Mock(side_effect=self.queue_judgment))

    def queue_judgment(self, main):
        self.judgment = {'effective': {'state': 'checking'}, 'qualification': {'state': 'queued'}}

    def check_engine_login(self, engine_id):
        return {'state': self.logins.get(engine_id, 'signed-out')}

    def onboarding(self):
        return {'steps': [{'id': 'engine', 'state': 'ready', 'message': 'ok'}],
                'subscription_engine': self.engine, 'model_ready': self.model_ready}

    def connect_subscription_engine(self, body, *, require_signed_in=False):
        self.calls.append(('connect', body['engine'], require_signed_in))
        if self.connect_error:
            raise ValueError(self.connect_error)
        self.engine = body['engine']

    def save_engine_credential(self, body):
        self.calls.append(('credential', body['engine'], body['token']))

    def save_model(self, body):
        self.calls.append(('save_model', body['provider'], body['endpoint'], body['model'], body['api_key']))

    def test_model(self):
        self.calls.append(('test_model',))
        return {'ok': self.model_ok, 'error': '' if self.model_ok else 'timeout.'}

    def select_ai_route(self, body):
        self.calls.append(('select_route', body['route']))
        self.model_ready = True

    def connect_telegram(self, body):
        self.calls.append(('telegram', body['token']))
        return {'url': 'https://t.me/bot?start=abc'}


class FirstRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = QuickStore(Path(self.temp.name) / 'data')

    def tearDown(self):
        self.temp.cleanup()

    def run_wizard(self, service, term):
        return first_run.run(service, self.store, term)

    # asks start with the password question and end with the Telegram question (Enter = skip).

    def test_skipping_every_step_claims_locally_and_finishes(self):
        service = FakeService()
        term = ScriptedTerminal(asks=['', '4', ''])
        self.assertTrue(self.run_wizard(service, term))
        self.assertTrue(self.store.claimed())
        self.assertTrue(first_run.finished(self.store))
        self.assertEqual(self.store.config('local_access'), True)
        self.assertEqual(service.calls, [])

    def test_the_password_step_says_it_is_not_the_mac_password(self):
        term = ScriptedTerminal(asks=['', '4', ''])
        self.run_wizard(FakeService(), term)
        self.assertIn('Mac 로그인 암호가 아니라', term.text())

    def test_a_chosen_password_is_rechecked_and_confirmed(self):
        term = ScriptedTerminal(asks=['y', '4', ''], secrets=['short', PASSWORD, 'typo ' + PASSWORD, PASSWORD, PASSWORD])
        self.assertTrue(self.run_wizard(FakeService(), term))
        self.assertIn('12자 이상으로 입력하세요.', term.text())
        self.assertIn('두 비밀번호가 다릅니다', term.text())
        self.assertEqual(self.store.config('local_access'), False)
        self.assertTrue(self.store.login(PASSWORD))

    def test_an_empty_new_password_falls_back_to_local_access(self):
        term = ScriptedTerminal(asks=['y', '4', ''], secrets=[''])
        self.assertTrue(self.run_wizard(FakeService(), term))
        self.assertEqual(self.store.config('local_access'), True)

    def test_codex_login_runs_official_cli_then_connects_signed_in(self):
        service = FakeService()
        term = ScriptedTerminal(asks=['', '1', '', ''])
        self.assertTrue(self.run_wizard(service, term))
        self.assertEqual(term.commands, [['/usr/local/bin/codex', 'login']])
        self.assertIn(('connect', 'codex', True), service.calls)
        service.decision_routes.follow_main_switched.assert_called_once_with('codex')
        self.assertIn(first_run.msg('first_run.judgment_checking'), term.text())
        self.assertEqual(term.opened, [])

    def test_signed_in_codex_connects_without_running_login(self):
        service = FakeService()
        service.logins['codex'] = 'signed-in'
        term = ScriptedTerminal(asks=['', '1', ''])
        self.assertTrue(self.run_wizard(service, term))
        self.assertEqual(term.commands, [])
        self.assertIn(('connect', 'codex', True), service.calls)

    def test_missing_codex_opens_its_install_page_then_logs_in(self):
        service = FakeService(found=())
        term = ScriptedTerminal(asks=['', '1', '', '', ''],
                                on_open=lambda url: service.subscription_engines.finder.found.add('codex'))
        self.assertTrue(self.run_wizard(service, term))
        self.assertEqual(term.opened, ['https://github.com/openai/codex'])
        self.assertIn('curl -fsSL https://chatgpt.com/codex/install.sh | sh', term.text())
        self.assertEqual(term.commands, [['/usr/local/bin/codex', 'login']])
        self.assertIn(('connect', 'codex', True), service.calls)

    def test_missing_claude_installs_then_asks_for_its_token(self):
        service = FakeService(found=())
        token = 'sk-ant-oat-' + 'x' * 30
        term = ScriptedTerminal(asks=['', '2', '', ''], secrets=[token],
                                on_open=lambda url: service.subscription_engines.finder.found.add('claude'))
        self.assertTrue(self.run_wizard(service, term))
        self.assertEqual(term.opened, ['https://code.claude.com/docs/en/setup'])
        self.assertIn('curl -fsSL https://claude.ai/install.sh | bash', term.text())
        self.assertEqual(term.commands, [['/usr/local/bin/claude', 'setup-token']])
        self.assertIn(('credential', 'claude-code', token), service.calls)
        self.assertIn(('connect', 'claude-code', True), service.calls)

    def test_a_still_missing_cli_waits_and_b_returns_to_the_menu(self):
        service = FakeService(found=())
        term = ScriptedTerminal(asks=['', '1', '', 'b', '4', ''])
        self.assertTrue(self.run_wizard(service, term))
        self.assertIn('Codex', term.text())
        self.assertIn('아직 설치된 Codex CLI가 보이지 않습니다', term.text())
        self.assertEqual(term.commands, [])
        self.assertEqual(service.calls, [])

    def test_codex_signed_out_shows_service_message_and_returns_to_menu(self):
        service = FakeService()
        service.connect_error = 'Codex에 로그인되어 있지 않아 전환하지 않았습니다.'
        term = ScriptedTerminal(asks=['', '1', 'n', '4', ''])
        self.assertTrue(self.run_wizard(service, term))
        self.assertIn('로그인되어 있지 않아', term.text())
        self.assertEqual(term.commands, [])
        self.assertEqual(service.engine, '')

    def test_claude_token_is_saved_through_engine_credential_then_connected(self):
        service = FakeService()
        token = 'sk-ant-oat-' + 'x' * 30
        term = ScriptedTerminal(asks=['', '2', ''], secrets=[token])
        self.assertTrue(self.run_wizard(service, term))
        self.assertEqual(term.commands, [['/usr/local/bin/claude', 'setup-token']])
        self.assertIn(('credential', 'claude-code', token), service.calls)
        self.assertIn(('connect', 'claude-code', True), service.calls)

    def test_signed_in_claude_connects_without_asking_for_a_token(self):
        service = FakeService()
        service.logins['claude-code'] = 'signed-in'
        term = ScriptedTerminal(asks=['', '2', ''])
        self.assertTrue(self.run_wizard(service, term))
        self.assertEqual(term.commands, [])
        self.assertEqual(term.secrets, [])
        self.assertIn(('connect', 'claude-code', True), service.calls)
        self.assertFalse(any(call[0] == 'credential' for call in service.calls))

    def test_api_key_is_explained_and_its_page_opened_with_the_default_model(self):
        service = FakeService()
        term = ScriptedTerminal(asks=['', '3', '1', '', '', ''], secrets=['sk-key'])
        self.assertTrue(self.run_wizard(service, term))
        self.assertIn('쓴 만큼 요금이 나옵니다', term.text())
        self.assertEqual(term.opened, ['https://platform.openai.com/api-keys'])
        self.assertIn(('save_model', 'openai', 'https://api.openai.com/v1', 'gpt-4o-mini', 'sk-key'), service.calls)
        self.assertIn(('select_route', 'direct-api'), service.calls)
        service.decision_routes.follow_main_switched.assert_called_once_with('openai')
        self.assertNotIn('sk-key', term.text())

    def test_an_owner_with_a_key_skips_the_key_page_and_names_a_model(self):
        service = FakeService()
        term = ScriptedTerminal(asks=['', '3', '1', 'gpt-test', 'n', ''], secrets=['sk-key'])
        self.assertTrue(self.run_wizard(service, term))
        self.assertEqual(term.opened, [])
        self.assertIn(('save_model', 'openai', 'https://api.openai.com/v1', 'gpt-test', 'sk-key'), service.calls)

    def test_ollama_needs_no_key_and_no_key_page(self):
        service = FakeService()
        term = ScriptedTerminal(asks=['', '3', '4', '', 'llama3', ''])
        self.assertTrue(self.run_wizard(service, term))
        self.assertEqual(term.opened, [])
        self.assertIn(('save_model', 'ollama', 'http://127.0.0.1:11434', 'llama3', ''), service.calls)

    def test_failed_api_test_keeps_route_and_returns_to_menu(self):
        service = FakeService()
        service.model_ok = False
        term = ScriptedTerminal(asks=['', '3', '1', '', 'n', '4', ''], secrets=['k'])
        self.assertTrue(self.run_wizard(service, term))
        self.assertIn('연결 확인 실패', term.text())
        self.assertNotIn('..', term.text())
        self.assertNotIn(('select_route', 'direct-api'), service.calls)

    def test_telegram_token_prints_pairing_link(self):
        service = FakeService()
        term = ScriptedTerminal(asks=['', '4', 'y'], secrets=['bot-token-123'])
        self.assertTrue(self.run_wizard(service, term))
        self.assertIn('/newbot', term.text())
        self.assertIn('telegram.org', term.text())
        self.assertEqual(term.opened, ['https://t.me/BotFather', 'https://t.me/bot?start=abc'])
        self.assertIn(('telegram', 'bot-token-123'), service.calls)
        self.assertIn('https://t.me/bot?start=abc', term.text())
        self.assertNotIn('bot-token-123', term.text())

    def test_saying_yes_to_telegram_but_leaving_the_token_empty_connects_nothing(self):
        service = FakeService()
        term = ScriptedTerminal(asks=['', '4', 'y'], secrets=[''])
        self.assertTrue(self.run_wizard(service, term))
        self.assertEqual(term.opened, ['https://t.me/BotFather'])
        self.assertEqual(service.calls, [])

    def test_stopping_midway_keeps_claim_and_resumes_on_next_run(self):
        service = FakeService()
        term = ScriptedTerminal(asks=['', KeyboardInterrupt()])
        self.assertFalse(self.run_wizard(service, term))
        self.assertTrue(self.store.claimed())
        self.assertFalse(first_run.finished(self.store))
        term = ScriptedTerminal(asks=['4', ''])
        self.assertTrue(self.run_wizard(service, term))
        self.assertTrue(first_run.finished(self.store))
        self.assertEqual(term.asks, [])

    def test_configured_ai_and_telegram_are_not_asked_again(self):
        service = FakeService()
        service.engine = 'codex'
        self.store.claim(self.store.bootstrap.read_text(), PASSWORD)
        self.store.put('telegram', {'enabled': True})
        term = ScriptedTerminal()
        self.assertTrue(self.run_wizard(service, term))
        self.assertEqual(term.asks, [])
        self.assertEqual(term.secrets, [])
        self.assertEqual(service.calls, [])
        service.decision_routes.follow_main_switched.assert_called_once_with('codex')

    def test_an_install_with_nothing_to_ask_skips_the_setup_intro(self):
        """#90: claimed, an AI route and Telegram already there: no "let's set up", only the summary."""
        service = FakeService()
        service.engine = 'codex'
        self.store.claim(self.store.bootstrap.read_text(), PASSWORD)
        self.store.put('telegram', {'enabled': True})
        term = ScriptedTerminal()
        self.assertTrue(self.run_wizard(service, term))
        self.assertNotIn(first_run.msg('first_run.intro'), term.text())
        self.assertFalse(term.text().startswith('Still'))
        self.assertIn(first_run.msg('first_run.finished'), term.text())
        self.assertTrue(first_run.finished(self.store))

    def test_an_install_with_a_step_left_still_shows_the_intro(self):
        service = FakeService()
        term = ScriptedTerminal(asks=['', '4', ''])
        self.assertTrue(self.run_wizard(service, term))
        self.assertTrue(term.lines[0].startswith('Still'))
        self.assertEqual(term.lines[1], first_run.msg('first_run.intro'))


class Done:
    def __init__(self, stdout=''):
        self.stdout = stdout


class LanguageTest(unittest.TestCase):
    def test_locale_variables_pick_the_language(self):
        self.assertIsNone(first_run.terminal_language({'LANG': 'ko_KR.UTF-8'}, 'linux'))
        self.assertEqual(first_run.terminal_language({'LANG': 'ja_JP.UTF-8'}, 'linux'), 'ja')
        self.assertEqual(first_run.terminal_language({'LC_ALL': 'zh_CN.UTF-8', 'LANG': 'ko_KR.UTF-8'}, 'linux'), 'zh')
        self.assertEqual(first_run.terminal_language({'LANG': 'de_DE.UTF-8'}, 'linux'), 'en')

    def test_an_unset_locale_uses_the_mac_language_or_english(self):
        self.assertIsNone(first_run.terminal_language({'LANG': 'C.UTF-8'}, 'darwin', lambda *a, **k: Done('ko_KR\n')))
        self.assertEqual(first_run.terminal_language({}, 'darwin', lambda *a, **k: Done('en_KR\n')), 'en')
        self.assertEqual(first_run.terminal_language({}, 'linux'), 'en')

    def test_english_terminal_translates_every_wizard_line(self):
        term = first_run.Terminal('en')
        self.assertEqual(term.local('   ' + first_run.msg('first_run.access_prompt') + ' '), '   Set a password? [y/N] ')
        self.assertEqual(term.local('\n' + first_run.msg('first_run.ai_title')), '\nAI connection')
        self.assertEqual(term.local('   ' + first_run.msg('first_run.cli_missing', name='Codex')),
                         '   Codex needs to be installed. Opening its official install page in the browser:')
        self.assertEqual(term.local('   ' + first_run.msg('first_run.model_prompt_default', model='gpt-4o-mini') + ' '),
                         '   Model name [gpt-4o-mini]: ')
        step = first_run.msg('service.onboarding_engine_selected', engine='codex')
        self.assertEqual(term.local(f'   ✓ {step}'), '   ✓ The official codex sign-in is selected.')
        self.assertEqual(term.local('https://t.me/bot?start=abc'), 'https://t.me/bot?start=abc')

    def test_korean_terminal_keeps_the_source_lines(self):
        line = '   ' + first_run.msg('first_run.access_prompt') + ' '
        self.assertEqual(first_run.Terminal(None).local(line), line)

    def test_a_whole_english_run_shows_no_korean(self):
        store = QuickStore(Path(tempfile.mkdtemp()) / 'data')
        lines = []
        english = first_run.Terminal('en')

        class English(ScriptedTerminal):
            def say(self, text=''):
                lines.append(english.local(text))

            def ask(self, prompt):
                lines.append(english.local(prompt))
                return super().ask(prompt)

            def secret(self, prompt):
                lines.append(english.local(prompt))
                return super().secret(prompt)

        service = FakeService(found=())
        service.model_ok = False
        term = English(asks=['y', '1', '', 'b', '3', '1', '', 'n', '4', 'y'],
                       secrets=['short', PASSWORD, 'other ' + PASSWORD, '', 'k', ''])
        self.assertTrue(first_run.run(service, store, term))
        self.assertTrue(lines)
        for line in lines:
            self.assertNotRegex(line, '[가-힣]', line)


class SetupLookTest(unittest.TestCase):
    """#438: the AI step offers a signed-in CLI first, and an unconnected AI is not reported as broken."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = QuickStore(Path(self.temp.name) / 'data')

    def tearDown(self):
        self.temp.cleanup()

    def test_enter_takes_the_signed_in_claude_code(self):
        service = FakeService()
        service.logins['claude-code'] = 'signed-in'
        term = ScriptedTerminal(asks=['', '', ''])
        self.assertTrue(first_run.run(service, self.store, term))
        self.assertEqual(term.choices[0][1], '2')
        self.assertIn(first_run.msg('first_run.ai_detected', name='Claude Code'), term.text())
        self.assertIn(('connect', 'claude-code', True), service.calls)
        self.assertEqual(term.commands, [])

    def test_without_a_signed_in_cli_enter_asks_again(self):
        service = FakeService()
        term = ScriptedTerminal(asks=['', '', '4', ''])
        self.assertTrue(first_run.run(service, self.store, term))
        self.assertIsNone(term.choices[0][1])
        self.assertIn(first_run.msg('first_run.choose_one_to_four'), term.text())
        self.assertNotIn(first_run.msg('first_run.ai_detected', name='Codex'), term.text())
        self.assertEqual(service.calls, [])

    def test_connect_later_is_not_shown_as_a_failure(self):
        term = ScriptedTerminal(asks=['', '4', ''])
        self.assertTrue(first_run.run(FakeService(), self.store, term))
        self.assertIn(first_run.msg('first_run.finished_without_ai'), term.text())
        self.assertNotIn(first_run.msg('first_run.finished'), term.text())
        self.assertFalse([line for line in term.lines if line.startswith('! ')])
        self.assertNotIn(first_run.msg('first_run.judgment_attention'), term.text())

    def test_a_connected_ai_whose_judgment_fails_is_still_flagged(self):
        service = FakeService()
        service.decision_routes.follow_main_switched = Mock()
        service.logins['codex'] = 'signed-in'
        self.assertTrue(first_run.run(service, self.store, ScriptedTerminal(asks=['', '1', ''])))
        term = ScriptedTerminal()
        first_run._summary(service, term)
        self.assertIn('! ' + first_run.msg('first_run.judgment_attention'), term.lines)

    def test_a_conversation_to_follow_offers_telegram_there_instead(self):
        service = FakeService()
        service.logins['codex'] = 'signed-in'
        term = ScriptedTerminal(asks=['', ''])  # a Telegram question would find no answer left
        self.assertTrue(first_run.run(service, self.store, term, conversation=True))
        self.assertNotIn(first_run.msg('first_run.telegram_title'), term.text())

    def test_without_an_ai_the_telegram_step_stays(self):
        term = ScriptedTerminal(asks=['', '4', ''])
        self.assertTrue(first_run.run(FakeService(), self.store, term, conversation=True))
        self.assertIn(first_run.msg('first_run.telegram_title'), term.text())

    def test_moving_to_the_login_service_asks_about_telegram_after_all(self):
        service = FakeService()
        service.logins['codex'] = 'signed-in'
        term = ScriptedTerminal(asks=['', '', 'y', ''])
        self.assertTrue(first_run.run(service, self.store, term, login_service=lambda: {'status': 'not_installed'},
                                      conversation=True))
        text = term.text()
        self.assertLess(text.index(first_run.msg('first_run.login_title')), text.index(first_run.msg('first_run.telegram_title')))


class RichTerminalTest(unittest.TestCase):
    """#438: Rich draws and prompt_toolkit reads; the catalog lines and the secret path stay the same."""

    def terminal(self, lang=None, answers=(), choice=None):
        import io
        from rich.console import Console
        self.out = io.StringIO()
        self.reads, self.picks = [], []
        answers = list(answers)

        def reader(prompt, **options):
            self.reads.append((prompt, options))
            return answers.pop(0)

        def chooser(message, **options):
            self.picks.append(options)
            return choice
        return first_run.RichTerminal(lang, Console(file=self.out, width=120, color_system=None),
                                      reader=reader, chooser=chooser)

    def test_banner_title_and_marks_are_drawn_in_the_terminal_language(self):
        term = self.terminal('en')
        term.banner('0.3.0')
        term.title(first_run.msg('first_run.ai_title'))
        term.mark('✓', first_run.msg('first_run.judgment_active'))
        text = self.out.getvalue()
        self.assertIn('/___/ /_/ /___/____/____/', text)
        self.assertIn(first_run.msg('first_run.tagline', 'en'), text)
        self.assertIn('v0.3.0', text)
        self.assertIn(first_run.msg('first_run.ai_title', 'en'), text)
        self.assertIn('✓ ' + first_run.msg('first_run.judgment_active', 'en'), text)
        self.assertNotRegex(text, '[가-힣]')

    def test_a_secret_is_read_masked_and_never_drawn(self):
        term = self.terminal(None, answers=['  123456789:AAsecret  '])
        self.assertEqual(term.secret(first_run.msg('first_run.bot_token_prompt')), '123456789:AAsecret')
        self.assertEqual(self.reads[0][1], {'is_password': True})
        self.assertNotIn('AAsecret', self.out.getvalue())

    def test_the_arrow_menu_numbers_its_own_rows_and_keeps_the_default(self):
        term = self.terminal('en', choice='2')
        options = [('1', first_run.msg('first_run.ai_codex')), ('2', first_run.msg('first_run.ai_claude'))]
        self.assertEqual(term.choose(first_run.msg('first_run.choice_prompt'), options, '2'), '2')
        self.assertEqual(self.picks[0]['options'], [('1', 'ChatGPT subscription (Codex)'),
                                                    ('2', 'Claude subscription (Claude Code)')])
        self.assertEqual(self.picks[0]['default'], '2')
        term.choose('', options, 'missing')
        self.assertIsNone(self.picks[1]['default'])

    def test_plain_lines_when_the_terminal_is_redirected_or_dumb(self):
        tty, pipe = SimpleNamespace(isatty=lambda: True), SimpleNamespace(isatty=lambda: False)
        self.assertIs(type(first_run.open_terminal('en', tty, pipe, {})), first_run.Terminal)
        self.assertIs(type(first_run.open_terminal('en', pipe, tty, {})), first_run.Terminal)
        self.assertIs(type(first_run.open_terminal('en', tty, tty, {'TERM': 'dumb'})), first_run.Terminal)
        self.assertIs(type(first_run.open_terminal('en', tty, tty, {'TERM': 'xterm-256color'})), first_run.RichTerminal)

    def test_plain_choice_lists_the_numbered_lines_and_takes_the_default_on_enter(self):
        term = first_run.Terminal(None)
        lines = []
        term.say = lines.append
        term.ask = lambda prompt: ''
        self.assertEqual(term.choose('번호:', [('1', first_run.msg('first_run.ai_codex'))], '1'), '1')
        self.assertEqual(lines, ['   ' + first_run.msg('first_run.ai_codex')])


if __name__ == '__main__':
    unittest.main()
