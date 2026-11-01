"""#514: phone page and API replies use the paired owner's language, without sockets."""
import hashlib
import io
import json
import re
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest

from personal_agent import phone_input
from personal_agent.messages import msg
from personal_agent.quickstart import make_handler
from personal_agent.telegram_locale import translate


DRAFTS = [None, {}, {'oauth': True}, {
    'fields': [{'name': 'KEY', 'label': 'Key', 'use': 'header_or_body'},
               {'name': 'OLD', 'label': 'Old', 'use': 'in_code'}],
    'hosts': ['example.org'],
}]
# Renderings of the pre-change page with the fixed code, nonce and drafts below.
KOREAN_HASHES = [
    '16bde030a0f5ec7aeddb62fa54b212214debb802751aaee91850ef63aaba6384',
    '617b4b57845ab80eeb108c48079c89eb4dcab098012d288771cbb4b83822590c',
    '267f4eecd2ee5a9d3108980f32205d4900a483d26a51c21bdbf96226c4d9dbd4',
    '776227212f6a4dd3f0bf576e48a89a09dac3bf74fe3f3dccea3ea0617d61b6a1',
]


def store(code):
    return SimpleNamespace(config=lambda key, default: {'owner_language': code})


def session(draft=None, code=None):
    return SimpleNamespace(
        label=msg('quickstart.own_google_client'), code='fixed-code',
        kind=phone_input.GOOGLE_CLIENT if draft is None else phone_input.API_SLOT,
        api_draft={'after': json.dumps(draft)}, api_kept=('OLD',),
        _store=store(code) if code is not None else None,
    )


@pytest.mark.parametrize('draft,digest', list(zip(DRAFTS, KOREAN_HASHES)))
def test_korean_page_is_byte_identical(draft, digest):
    baseline = phone_input.page(session(draft), 'fixed-nonce').encode()
    assert hashlib.sha256(baseline).hexdigest() == digest
    for code in (None, 'ko', 'ko-KR'):
        assert phone_input.page(session(draft, code), 'fixed-nonce').encode() == baseline


@pytest.mark.parametrize('draft', DRAFTS)
@pytest.mark.parametrize('code,lang', [('en-US', 'en'), ('ja', 'ja'), ('zh-CN', 'zh')])
def test_page_uses_owner_language(draft, code, lang):
    html = phone_input.page(session(draft, code), 'fixed-nonce')
    assert f'<html lang="{lang}">' in html
    assert f'<title>{msg("quickstart.own_google_client", lang=lang)}</title>' in html
    assert msg('phone_input.json_file' if draft is None else 'phone_input.save_continue'
               if draft.get('fields') else 'phone_input.check_connection', lang=lang) in html
    assert not re.search('[\uac00-\ud7a3]', html)


def test_inline_script_escapes_apostrophes_and_script_delimiters(monkeypatch):
    original = phone_input.msg
    text = "Can't save </script>\\file\r\nTry again.\u2028\u2029"
    monkeypatch.setattr(phone_input, 'msg', lambda key, **kw:
                        text if key == 'phone_input.save_failed' else original(key, **kw))
    escaped = r"Can\'t save \u003c/script\u003e\\file\r\nTry again.\u2028\u2029"
    assert phone_input._js_string(text) == escaped
    assert f"say(d&&d.error||'{escaped}')" in phone_input.page(session(code='en'), 'nonce')
    assert phone_input._js_string(original('phone_input.save_failed')) == original('phone_input.save_failed')


def route(code, path, *, result=None, error=None, body=b'{}', content_type='application/json'):
    current = session(code=code)
    current.code_ok = lambda code: True
    current.client_ok = lambda cookie: True
    service = SimpleNamespace(store=store(code), phone_input_session=lambda: current,
                              phone_input_client=Mock(return_value=result, side_effect=error))
    handler_class = make_handler(service)
    handler = handler_class.__new__(handler_class)
    handler.headers = {'Content-Type': content_type, 'Content-Length': str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler.phone_input_cookie = lambda: 'bound-cookie'
    handler.reply = lambda status, body, *args, **kwargs: (status, body)
    return handler.phone_input_route('POST', urlsplit(path))


@pytest.mark.parametrize('code', [None, 'ko', 'ko-KR', 'en'])
@pytest.mark.parametrize('message_id', ['phone_input.save_failed', 'quickstart.try_again_later'])
def test_phone_post_service_error_is_translated(code, message_id):
    text = msg(message_id)
    status, body = route(code, phone_input.CLIENT_PATH, error=ValueError(text))
    assert status == 400
    assert body == {'ok': False, 'error': translate(text, phone_input.owner_language(store(code)))}


@pytest.mark.parametrize('path', [phone_input.CLIENT_PATH, phone_input.API_PATH])
def test_phone_post_success_response_is_translated(path, monkeypatch):
    result = {'ok': True, 'response': msg('phone_input.saved'), 'other': 'unchanged'}
    monkeypatch.setattr('personal_agent.api_input.submit', lambda *args: result)
    assert route('en', path, result=result) == (200, {**result, 'response': msg('phone_input.saved', lang='en')})
    assert result['response'] == msg('phone_input.saved')


@pytest.mark.parametrize('code', [None, 'en'])
@pytest.mark.parametrize('kwargs,status,message_id', [
    ({'content_type': 'text/plain'}, 415, 'quickstart.json_required'),
    ({'body': b'invalid'}, 400, 'quickstart.invalid_request'),
    ({'error': OSError('private details')}, 502, 'quickstart.try_again_later'),
])
def test_phone_post_handler_errors_use_catalog(code, kwargs, status, message_id):
    actual_status, body = route(code, phone_input.CLIENT_PATH, **kwargs)
    assert actual_status == status
    assert body['error'] == msg(message_id, lang=code)
