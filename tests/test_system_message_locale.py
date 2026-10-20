"""STEADY-LOCALE-01 (#10): connection failures and other fixed system lines in the owner's language.

One catalog (``messages.json``) feeds Telegram (``telegram_locale``), the
terminal (``first_run.Terminal``) and now the browser's error replies.  AI
answers and unknown text are never translated as templates; Korean owners see
no change.  No network: every HTTP exchange is injected.
"""
import io
import json
import pathlib
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from personal_agent import calendar_conversation, conversation_handoff, family_share, providers, remote_login
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
    """The calendar, sign-in handoff and family-sharing lines moved into the catalog."""

    def test_calendar_conversation_lines(self):
        lines = {
            calendar_conversation.OTHER_CHANNEL: 'calendar_conversation.draft_from_other_channel',
            calendar_conversation.FAILED_RECONNECT: 'calendar_conversation.not_applied_reconnect',
            calendar_conversation.FAILED_RETRY: 'calendar_conversation.not_applied_retry',
            calendar_conversation.NOT_APPLIED_REST: 'calendar_conversation.rest_not_applied',
            calendar_conversation.REPLACED_NOTICE: 'calendar_conversation.previous_draft_cancelled',
        }
        for text, message_id in lines.items():
            for lang in LANGS:
                self.assertEqual(translate(text, lang), msg(message_id, lang))
        refused = calendar_conversation.UNVERIFIED_TARGET.format(count=2)
        self.assertEqual(translate(refused, 'en'), msg('calendar_conversation.unverified_target', 'en', count=2))
        # A reply of several fixed lines is translated line by line.
        reply = calendar_conversation.FAILED_RETRY + '\n' + calendar_conversation.NOT_APPLIED_REST
        self.assertEqual(translate(reply, 'zh'), msg('calendar_conversation.not_applied_retry', 'zh') + '\n'
                         + msg('calendar_conversation.rest_not_applied', 'zh'))

    def test_sign_in_handoff_and_family_lines(self):
        self.assertEqual(translate(remote_login.NO_NGROK_TEXT, 'en'), msg('remote_login.no_ngrok', 'en'))
        self.assertEqual(translate(remote_login.NO_PORT_TEXT, 'ja'), msg('remote_login.no_local_address', 'ja'))
        self.assertEqual(translate(family_share.PAYMENT_REFUSED_TEXT, 'zh'), msg('family_share.payment_refused', 'zh'))
        receipt = family_share.RECEIVER_SIGNED_IN_RECEIPT.format(label='Mom', site='example.com')
        self.assertEqual(translate(receipt, 'en'),
                         msg('family_share.receiver_signed_in_receipt', 'en', label='Mom', site='example.com'))
        self.assertEqual(translate(family_share.NOT_INSTALLED_TEXT.format(instance='kid'), 'ja'),
                         msg('family_share.not_installed', 'ja', instance='kid'))

    def test_telegram_buttons_and_bodies_use_the_same_lines(self):
        body = {'chat_id': 1, 'text': msg('providers.unreachable')}
        self.assertEqual(localize_body('sendMessage', body, 'en')['text'], msg('providers.unreachable', 'en'))


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
