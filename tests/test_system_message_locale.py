"""STEADY-LOCALE-01 (#10): connection failures and other fixed system lines in the owner's language.

One catalog (``messages.json``) feeds Telegram (``telegram_locale``), the
terminal (``first_run.Terminal``) and now the browser's error replies.  AI
answers and unknown text are never translated as templates; Korean owners see
no change.  No network: every HTTP exchange is injected.
"""
import io
import json
import pathlib
import re
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from personal_agent import conversation_handoff, providers
from personal_agent.first_run import Terminal
from personal_agent.messages import msg
from personal_agent.providers import ProviderError
from personal_agent.telegram_locale import language, localize_body, translate

LANGS = ('en', 'ja', 'zh')


def provider_error(raised):
    """The ProviderError ``request_json`` raises when its HTTP open raises ``raised`` (or returns it)."""
    class Opener:
        def open(self, request, timeout=None):
            if isinstance(raised, BaseException):
                raise raised
            return raised

    with mock.patch.object(providers, 'build_opener', lambda *handlers: Opener()):
        try:
            providers.request_json('https://model.example.test/v1/chat', {'x': 1})
        except ProviderError as exc:
            return exc
    raise AssertionError('no ProviderError')


class Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ConnectionFailureFixtures(unittest.TestCase):
    """Each connection failure, in English, Japanese and Chinese, from the catalog line it was raised with."""

    def cases(self):
        return (
            (provider_error(HTTPError('u', 401, 'x', {}, io.BytesIO(b'{}'))), 'providers.http_error', {'status': 401}),
            (provider_error(HTTPError('u', 429, 'x', {}, io.BytesIO(b'{}'))), 'providers.free_model_rate_limited', {}),
            (provider_error(URLError('down')), 'providers.unreachable', {}),
            (provider_error(TimeoutError()), 'providers.unreachable', {}),
            (provider_error(Body(b'not json')), 'providers.invalid_json', {}),
            (provider_error(Body(b'x' * 2_000_001)), 'providers.response_too_large', {}),
        )

    def test_provider_failures_are_catalog_lines_in_every_language(self):
        for error, message_id, values in self.cases():
            with self.subTest(message_id=message_id):
                self.assertEqual(str(error), msg(message_id, **values))
                for lang in LANGS:
                    self.assertEqual(translate(str(error), lang), msg(message_id, lang, **values))
                self.assertEqual(translate(str(error), None), str(error), 'Korean owners see no change')

    def test_the_terminal_shows_the_same_line(self):
        error = provider_error(URLError('down'))
        for lang in LANGS:
            self.assertEqual(Terminal(lang).local(str(error)), msg('providers.unreachable', lang))
        self.assertEqual(Terminal(None).local(str(error)), str(error))

    def test_telegram_and_model_http_errors_share_one_line(self):
        def exchange(*args, **kwargs):
            return 503, b'upstream down'

        with mock.patch.object(conversation_handoff, '_telegram_exchange', exchange):
            with self.assertRaises(ProviderError) as telegram:
                conversation_handoff.telegram_request_json('https://api.telegram.org/botX/getMe', None)
        model = provider_error(HTTPError('u', 503, 'x', {}, io.BytesIO(b'{}')))
        self.assertEqual(str(telegram.exception), str(model))
        self.assertEqual(translate(str(telegram.exception), 'en'), msg('providers.http_error', 'en', status=503))

    def test_telegram_photo_failures(self):
        def unreached(*args, **kwargs):
            raise conversation_handoff._Unreached(timed_out=True)

        with mock.patch.object(conversation_handoff, '_telegram_exchange', unreached):
            with self.assertRaises(ProviderError) as caught:
                conversation_handoff.telegram_request_file('https://api.telegram.org/file/botX/a.jpg')
        for lang in LANGS:
            self.assertEqual(translate(str(caught.exception), lang),
                             msg('conversation_handoff.telegram_photo_download_failed', lang))
        self.assertEqual(conversation_handoff.TELEGRAM_FAILURE_TEXT, msg('conversation_handoff.telegram_request_failed'))

    def test_the_main_ai_check_failure_is_one_line_with_the_provider_error_inside(self):
        """Codex review on #146: the browser's 'check and use' failure wraps the provider error."""
        from cryptography.fernet import Fernet
        from personal_agent.quickstart import configured_service
        from personal_agent.quickstart_store import QuickStore
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = configured_service(QuickStore(str(pathlib.Path(temp.name) / 'data')),
                                     {'AGENTOS_GOOGLE_OAUTH_KEY': Fernet.generate_key().decode()})
        service.save_main_ai_key({'provider': 'openai', 'key': 'sk-made-up-0001'})
        failure = str(provider_error(HTTPError('u', 401, 'x', {}, io.BytesIO(b'{}'))))
        with mock.patch.object(service, 'probe_model', return_value={'ok': False, 'error': failure}):
            with self.assertRaises(ValueError) as caught:
                service.activate_main_ai({'route': 'openai'})
        text = str(caught.exception)
        self.assertEqual(text, msg('main_ai.api_check_failed', name='OpenAI', error=failure))
        for lang in LANGS:
            self.assertEqual(translate(text, lang), msg('main_ai.api_check_failed', lang, name='OpenAI',
                                                        error=msg('providers.http_error', lang, status=401)))

    def test_model_setup_validation(self):
        for config, message_id in (({'provider': 'x'}, 'providers.choose_connection_method'),
                                   ({'provider': 'openai', 'endpoint': 'https://other.test/v1'},
                                    'providers.use_openai_address'),
                                   ({'provider': 'compatible', 'endpoint': 'https://m.test/v1'},
                                    'providers.enter_model_name')):
            with self.assertRaises(ValueError) as caught:
                providers.validate_model(config)
            self.assertEqual(translate(str(caught.exception), 'ja'), msg(message_id, 'ja'))


class OtherFlowsTest(unittest.TestCase):
    """The calendar, sign-in handoff and family-sharing lines moved into the catalog.

    #242: what the owner is sent on those flows is read off the real send paths in
    ``test_handoff_locale`` (``CalendarDraftLines``, ``PhoneSignInLines``, ``FamilySharingLines``).
    """

    def test_the_preview_footer_asks_for_no_exact_word(self):
        """#172: approval is judged, not matched, so no language is told to send one exact word."""
        import re
        quoted = re.compile(r'"([^"]+)"|「([^」]+)」|“([^”]+)”')
        for lang in ('ko', *LANGS):
            with self.subTest(lang=lang):
                self.assertEqual(quoted.findall(msg('calendar_conversation.reply_approve_to_apply_it_as', lang)), [])

    def test_telegram_buttons_and_bodies_use_the_same_lines(self):
        body = {'chat_id': 1, 'text': msg('providers.unreachable')}
        self.assertEqual(localize_body('sendMessage', body, 'en')['text'], msg('providers.unreachable', 'en'))


class MultiLineEntriesTest(unittest.TestCase):
    """#235: a catalog entry that spans several lines is translated as one block."""

    @staticmethod
    def rendered(message_id, lang='ko'):
        from personal_agent.messages import catalog
        names = re.findall(r'\{([a-z_]+)\}', catalog()[message_id]['ko'])
        return msg(message_id, lang, **{name: f'sample-{name}' for name in names})

    def multi_line_ids(self):
        from personal_agent.messages import catalog
        ids = [key for key, entry in catalog().items() if '\n' in entry['ko']]
        self.assertIn('working_notes.saved', ids)
        return ids

    def test_every_multi_line_entry_reaches_every_language(self):
        from personal_agent.messages import catalog
        for message_id in self.multi_line_ids():
            source = self.rendered(message_id)
            for lang in LANGS:
                if not catalog()[message_id].get(lang):
                    continue
                with self.subTest(message_id=message_id, lang=lang):
                    expected = self.rendered(message_id, lang)
                    self.assertEqual(translate(source, lang), expected)
                    self.assertEqual(localize_body('sendMessage', {'chat_id': 1, 'text': source}, lang)['text'], expected)
                    # Inside a longer message, between a catalog line and a model-written line.
                    self.assertEqual(translate(f"{msg('providers.unreachable')}\n{source}\nAn AI-written line.", lang),
                                     f"{msg('providers.unreachable', lang)}\n{expected}\nAn AI-written line.")
                    self.assertEqual(translate(source, None), source, 'Korean owners see no change')

    def test_a_placeholder_value_may_span_lines(self):
        text = msg('working_notes.saved', change='first line\nsecond line')
        self.assertEqual(translate(text, 'en'), msg('working_notes.saved', 'en', change='first line\nsecond line'))

    def test_only_part_of_a_multi_line_entry_is_not_translated(self):
        first, second = msg('working_notes.saved', change='x').split('\n')
        for text in (first, second, f'{first}\nAn AI-written second line.'):
            for lang in LANGS:
                self.assertEqual(translate(text, lang), text)


class NotTranslatedTest(unittest.TestCase):
    def test_ai_answers_and_unknown_text_pass_unchanged(self):
        for text in ('The HTTP 401 means your key expired; reconnect in Settings.',
                     '연결 대상이 바뀌어서 다시 확인해 볼게요.',  # model-written Korean, not a catalog line
                     '모델의 텍스트 응답이 없습니다. 그런데 이건 AI가 덧붙인 문장이에요.'):
            for lang in LANGS:
                self.assertEqual(translate(text, lang), text)


class BrowserErrorRepliesTest(unittest.TestCase):
    """The browser sends its screen language; Still's own error lines come back in it."""

    def setUp(self):
        from http.server import ThreadingHTTPServer
        from cryptography.fernet import Fernet
        from personal_agent.quickstart import configured_service, make_handler
        from personal_agent.quickstart_store import QuickStore
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = QuickStore(str(pathlib.Path(temp.name) / 'data'))
        service = configured_service(store, {'AGENTOS_GOOGLE_OAUTH_KEY': Fernet.generate_key().decode()})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(service))
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), thread.join(), self.server.server_close()))
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def error(self, lang):
        from urllib.request import Request, urlopen
        headers = {'X-Still-Language': lang} if lang else {}
        try:
            urlopen(Request(self.url + '/api/state', headers=headers), timeout=5)
        except HTTPError as exc:
            return exc.code, json.load(exc)['error']
        raise AssertionError('expected an error reply')

    def test_a_sign_in_error_follows_the_screen_language(self):
        status, korean = self.error(None)
        self.assertEqual((status, korean), (401, msg('quickstart.a_sign_in_is_needed')))
        self.assertEqual(self.error('ko')[1], korean)
        self.assertEqual(self.error('en')[1], msg('quickstart.a_sign_in_is_needed', 'en'))
        self.assertEqual(self.error('ja')[1], msg('quickstart.a_sign_in_is_needed', 'ja'))
        self.assertEqual(self.error('zh-CN')[1], msg('quickstart.a_sign_in_is_needed', 'zh'))
        self.assertEqual(language('zh-CN'), 'zh')

    def test_the_browser_sends_its_language(self):
        app = (pathlib.Path(providers.__file__).parent / 'web' / 'app.js').read_text(encoding='utf-8')
        start = app.index('async function api(path,body,method){')
        self.assertIn("'X-Still-Language':currentLanguage", app[start:start + 600])


if __name__ == '__main__':
    unittest.main()
