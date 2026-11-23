"""#524 fixture evidence for the Telegram secret boundary and the pinned page."""
import base64
import hashlib
import json
import logging
import os
import re
import runpy
import shutil
import subprocess
import threading
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from personal_agent import phone_input, secret_miniapp, telegram_surface, work_mode
from personal_agent.api_credentials import ApiCredentials
from personal_agent.api_requests import slots
from personal_agent.browser_jar import MemoryKey
from personal_agent.messages import LANGUAGES, msg
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from fake_holdings_api import FakeHoldingsApi
from test_code_run_secrets import CODE_SET, VALUES
from test_connect_api_input import PUBLIC
from test_phone_input import CHAT

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'site/secret-input/v1'
MARKER = 'synthetic-fixture-marker-for-524'


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def seal(key, body, **overrides):
    public = serialization.load_der_public_key(unb64(key.spki_b64u()))
    header = {'alg': 'RSA-OAEP-256', 'enc': 'A256GCM', 'kid': key.kid, **overrides}
    protected = b64(json.dumps(header, separators=(',', ':')).encode())
    cek = AESGCM.generate_key(bit_length=256)
    iv = os.urandom(12)
    ciphertext = AESGCM(cek).encrypt(iv, json.dumps(body).encode(), protected.encode('ascii'))
    wrapped = public.encrypt(cek, padding.OAEP(padding.MGF1(hashes.SHA256()), hashes.SHA256(), None))
    return '.'.join([protected, *(b64(part) for part in (wrapped, iv, ciphertext[:-16], ciphertext[-16:]))])


def damaged(token, part):
    parts = token.split('.')
    data = unb64(parts[part])
    parts[part] = b64(bytes([data[0] ^ 1]) + data[1:])
    return '.'.join(parts)


def no_external_call(*args, **kwargs):
    raise AssertionError('unexpected external call')


@pytest.fixture
def rig(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1_800_000_000.0)
    clock.read = lambda: clock.now
    store = QuickStore(str(tmp_path / 'data'))
    store.api_slot_key = MemoryKey()
    calls, workers = [], []

    def transport(url, body, headers, *, timeout):
        method = url.rsplit('/', 1)[1]
        calls.append((method, body))
        # Deletion stays best effort even when Telegram refuses it.
        if method == 'deleteMessage':
            return {'ok': False, 'error_code': 400, 'description': 'fixture refused deletion'}
        return {'ok': True, 'result': {'message_id': 1}}

    def track_worker(*args, **kwargs):
        thread = threading.Thread(*args, **kwargs)
        workers.append(thread)
        return thread

    monkeypatch.setattr(telegram_surface, 'threading', SimpleNamespace(Thread=track_worker))
    monkeypatch.setattr(secret_miniapp, '_fetch', no_external_call)
    model = Mock(side_effect=no_external_call)
    service = AgentService(store, ModelAdapter(model), telegram_transport=transport)
    store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': 'g1', 'cursor': 0, 'owner_language': 'en'})
    service.local_server_port = 8787
    service.phone_input_popen = Mock(side_effect=no_external_call)
    service.phone_input_clock = clock.read
    service.secret_miniapp_available = Mock(return_value=True)
    service.api_input_clock = clock.read
    api = FakeHoldingsApi(MARKER, now=clock.read)
    service.api_input_transport = api
    job = store.job(store.enqueue('Read my requested records', 'original', 'telegram:g1', CHAT))
    settings = service.settings_orchestrator

    def confirm(metadata=PUBLIC):
        draft = service.settings_tools(job)('settings_change', {
            'category': 'api_connections', 'setting': 'connect',
            'value': json.dumps(metadata), 'continue_work': 'yes',
        })
        draft_id = draft['draft_id']
        with store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (job['id'],))
        result = settings.confirm(service.settings_owner(job), job['channel'], draft_id,
                                  settings._drafts()[draft_id]['digest'])
        assert result['state'] == 'awaiting-input'
        return result, service.phone_input_session()

    def drain():
        for thread in workers:
            thread.join(timeout=10)
            assert not thread.is_alive()

    def deliver(token, update_id=10, *, sender=CHAT, chat_type='private', chat_id=CHAT, wait=True):
        # Even a message carrying text and a location must bypass those paths.
        service.ingest_update({
            'update_id': update_id,
            'message': {'from': {'id': sender}, 'chat': {'id': chat_id, 'type': chat_type}, 'message_id': 99,
                        'web_app_data': {'data': token}, 'text': MARKER,
                        'location': {'latitude': 1, 'longitude': 2}},
        }, 'g1')
        if wait:
            drain()

    yield SimpleNamespace(clock=clock, store=store, calls=calls, service=service, api=api, job=job, settings=settings,
                          confirm=confirm, deliver=deliver, drain=drain)
    drain()
    service.end_tunnel_sessions()
    service.phone_input_popen.assert_not_called()
    model.assert_not_called()


def read_offer(calls):
    method, body = calls[-1]
    assert method == 'sendMessage'
    assert body['chat_id'] == CHAT
    markup = body['reply_markup']
    assert markup['one_time_keyboard'] and markup['resize_keyboard']
    [row] = markup['keyboard']
    [button] = row
    assert button['text'] == msg('api_input.miniapp_button', lang='en')
    url = button['web_app']['url']
    assert url.partition('?')[0] == secret_miniapp.PAGE_URL
    params = parse_qs(urlsplit(url).query)
    assert set(params) == {'s'}
    [encoded] = params['s']
    assert re.fullmatch(r'[A-Za-z0-9_-]+', encoded)
    return json.loads(unb64(encoded))


def test_round_trip_uses_existing_storage_and_resumes_work(rig, caplog):
    caplog.set_level(logging.DEBUG)
    result, session = rig.confirm()
    assert len(rig.calls) == 1
    assert result['response'] == msg('api_input.link_sent', lang='en')
    key = session.miniapp_key
    spec = read_offer(rig.calls)
    assert spec == {'v': 1, 'kid': key.kid, 'k': key.spki_b64u(),
                    'title': msg('api_input.connection_label', lang='en', name=PUBLIC['name']), 'lang': 'en',
                    'fields': [{'name': 'secret', 'label': msg('phone_input.api_key', lang='en')}],
                    'too_long': msg('api_input.miniapp_too_long', lang='en'),
                    'no_bridge': msg('api_input.miniapp_open_from_button', lang='en'),
                    'save': msg('phone_input.check_connection', lang='en')}
    assert rig.store.job(rig.job['id'])['status'] == 'awaiting_connection'
    assert len(rig.store.jobs()) == 1
    pem = key._private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode('ascii')
    token = seal(key, {'secret': MARKER})
    rig.deliver(token)
    assert rig.settings._drafts()[result['draft_id']]['state'] == 'applied'
    assert rig.store.job(rig.job['id'])['status'] == 'queued'
    assert len(rig.api.calls) == 1
    slot = slots(rig.store)[PUBLIC['name']]
    assert ApiCredentials(rig.store).read(PUBLIC['name'], slot) == MARKER
    assert rig.calls[-2:] == [
        ('sendMessage', {'chat_id': CHAT, 'text': msg('api_input.read_verified', lang='en'),
                         'reply_markup': {'remove_keyboard': True}}),
        ('deleteMessage', {'chat_id': CHAT, 'message_id': 99}),
    ]
    with pytest.raises(secret_miniapp.SecretMiniAppError, match='^used$'):
        key.open(token)
    # Inspect every table, including conversation rows, trace/Evidence and durable receipts.
    with rig.store.db() as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
        dump = '\n'.join(db.iterdump())
    assert len(rig.store.jobs()) == 1
    assert MARKER not in rig.store.secret_path.read_text()
    for surface in (caplog.text, dump, repr(rig.calls)):
        for value in (MARKER, token, '-----BEGIN PRIVATE KEY-----', pem.splitlines()[1]):
            assert value not in surface


def test_duplicate_update_is_ignored_and_resubmission_is_refused(rig):
    result, session = rig.confirm()
    token = seal(session.miniapp_key, {'secret': MARKER})
    rig.deliver(token)
    rows, stored, sent = rig.settings._drafts(), slots(rig.store), list(rig.calls)
    rig.deliver(token)
    assert rig.calls == sent
    rig.deliver(token, update_id=11)
    assert rig.calls[-2][1]['text'] == msg('api_input.miniapp_closed', lang='en')
    assert rig.calls[-2][1]['reply_markup'] == {'remove_keyboard': True}
    assert rig.settings._drafts() == rows
    assert slots(rig.store) == stored
    assert rows[result['draft_id']]['state'] == 'applied'
    assert len(rig.api.calls) == 1


@pytest.mark.parametrize('sender,chat_id,chat_type', [
    (CHAT + 1, CHAT, 'private'), (CHAT + 1, CHAT + 1, 'private'), (CHAT, CHAT, 'group'),
])
def test_foreign_chat_advances_cursor_without_decrypt_or_reply(rig, monkeypatch, sender, chat_id, chat_type):
    _, session = rig.confirm()
    spy = Mock(wraps=session.miniapp_key.open)
    monkeypatch.setattr(session.miniapp_key, 'open', spy)
    rig.deliver('unused-marker', sender=sender, chat_id=chat_id, chat_type=chat_type)
    spy.assert_not_called()
    assert len(rig.calls) == 1
    assert rig.store.config('telegram')['cursor'] == 11
    assert len(rig.store.jobs()) == 1


def test_expired_session_is_closed_before_decrypting(rig, monkeypatch):
    result, session = rig.confirm()
    key = session.miniapp_key
    with monkeypatch.context() as patch:
        spy = Mock(wraps=key.open)
        patch.setattr(key, 'open', spy)
        rig.clock.now += phone_input.SESSION_SECONDS + 1
        rig.deliver('unused-marker')
        spy.assert_not_called()
    assert rig.settings._drafts()[result['draft_id']]['state'] == 'expired'
    assert rig.calls[-2][1]['text'] == msg('api_input.miniapp_closed', lang='en')
    assert not session.alive()
    with pytest.raises(secret_miniapp.SecretMiniAppError, match='^used$'):
        key.open('unused-marker')


@pytest.mark.parametrize('make_token,reason', [
    (lambda key: seal(key, {}, kid='another-id'), 'kid'),
    (lambda key: seal(key, {}, alg='RSA-OAEP'), 'header'),
    (lambda key: seal(key, {}, enc='A128GCM'), 'header'),
    (lambda key: seal(key, {}, extra=True), 'header'),
    (lambda key: 'é' * 2049, 'size'),
    (lambda key: 'x' * 4097, 'size'),
    (lambda key: None, 'malformed'),
    (lambda key: 'x.y.z', 'malformed'),
    (lambda key: seal(key, {}) + '=', 'malformed'),
    (lambda key: seal(key, {'secret': 12}), 'malformed'),
    (lambda key: damaged(seal(key, {'secret': MARKER}), 3), 'decrypt'),
    (lambda key: damaged(seal(key, {'secret': MARKER}), 1), 'decrypt'),
])
def test_invalid_token_consumes_the_key(make_token, reason):
    key = secret_miniapp.OneTimeKey()
    token = make_token(key)
    with pytest.raises(secret_miniapp.SecretMiniAppError, match=f'^{reason}$'):
        key.open(token)
    with pytest.raises(secret_miniapp.SecretMiniAppError, match='^used$'):
        key.open(token)


def test_probe_runs_off_poll_with_service_lock_free_and_can_retry(rig):
    result, session = rig.confirm()
    key = session.miniapp_key
    original_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    thread_state = []
    rig.api.fault = 'no_auth'

    def probe(*args, **kwargs):
        unlocked = rig.service.lock.acquire(blocking=False)
        if unlocked:
            rig.service.lock.release()
        thread_state.append((threading.get_ident(), unlocked))
        entered.set()
        assert release.wait(5)
        return rig.api(*args, **kwargs)

    rig.service.api_input_transport = probe
    try:
        rig.deliver(seal(key, {'secret': MARKER}), wait=False)
        assert entered.wait(5)
        [(probe_thread, unlocked)] = thread_state
        assert probe_thread != original_thread
        assert unlocked
    finally:
        release.set()
        rig.drain()
    assert rig.settings._drafts()[result['draft_id']]['state'] == 'awaiting-input'
    assert rig.calls[-3][1]['reply_markup'] == {'remove_keyboard': True}
    new_key = session.miniapp_key
    assert new_key.kid != key.kid
    assert read_offer(rig.calls)['kid'] == new_key.kid
    assert session.miniapp_rearms == 1
    assert session.expires - session.created == phone_input.SESSION_SECONDS
    rig.api.fault = None
    rig.deliver(seal(new_key, {'secret': MARKER}), update_id=11)
    assert rig.store.job(rig.job['id'])['status'] == 'queued'


def test_session_can_rearm_only_three_times(rig):
    result, session = rig.confirm()
    # A body with the wrong fields fails before the probe and leaves the input draft open.
    for index in range(4):
        key = session.miniapp_key
        rig.deliver(seal(key, {'unexpected': MARKER}), update_id=10 + index)
    offers = [body for method, body in rig.calls if 'keyboard' in body.get('reply_markup', {})]
    assert len(offers) == 4
    assert session.miniapp_rearms == 3
    assert rig.settings._drafts()[result['draft_id']]['state'] == 'expired'
    assert rig.api.calls == []


def test_code_run_names_round_trip(rig):
    _, session = rig.confirm(CODE_SET)
    spec = read_offer(rig.calls)
    assert [field['name'] for field in spec['fields']] == list(VALUES)
    rig.deliver(seal(session.miniapp_key, VALUES))
    slot = slots(rig.store)[CODE_SET['name']]
    assert ApiCredentials(rig.store).read(CODE_SET['name'], slot) == VALUES
    assert not rig.api.calls


def test_work_mode_uses_mac_page(rig):
    work_mode.set_enabled(rig.store, True, surface='settings')
    result, session = rig.confirm(CODE_SET)
    assert session.link.startswith('http://localhost:8787/phone-input?code=')
    assert session.link in result['response']
    rig.service.secret_miniapp_available.assert_not_called()
    assert not rig.calls


def served_assets():
    return {secret_miniapp.PAGE_URL + ('' if path.name == 'index.html' else path.name): path.read_bytes()
            for path in PAGE.iterdir()}


@pytest.mark.parametrize('fail', ['fetch_error', 'asset_mismatch'])
def test_page_unavailable_falls_back_to_mac(rig, fail):
    assets = served_assets()

    def fetch(url, *, timeout):
        if fail == 'fetch_error':
            raise OSError('fixture absent')
        return assets[url] + (b'changed' if url.endswith('seal.js') else b'')

    rig.service.secret_miniapp_available = lambda: secret_miniapp.available(fetch=fetch)
    result, session = rig.confirm()
    assert session.link.startswith('http://localhost:8787/phone-input?code=')
    assert session.link in result['response']
    assert msg('api_input.miniapp_unavailable', lang='en') in result['response']
    assert session.miniapp_key is None
    assert rig.calls == []


def test_successful_asset_verification_expires_and_failures_are_not_cached(monkeypatch):
    assets = served_assets()
    fetch = Mock(side_effect=lambda url, *, timeout: assets[url])
    clock = SimpleNamespace(now=10.0)
    # Patch the module reference without changing the process-wide monotonic clock.
    monkeypatch.setattr(secret_miniapp, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(secret_miniapp, '_verified_until', 0.0)
    monkeypatch.setattr(secret_miniapp, '_fetch', fetch)
    assert secret_miniapp.available()
    assert {call.args[0] for call in fetch.call_args_list} == assets.keys()
    assert all(0 < call.kwargs['timeout'] <= 3 for call in fetch.call_args_list)
    fetch.reset_mock()
    assert secret_miniapp.available()
    fetch.assert_not_called()
    clock.now += 301
    original = assets[secret_miniapp.PAGE_URL]
    assets[secret_miniapp.PAGE_URL] = b'changed'
    assert not secret_miniapp.available()
    assets[secret_miniapp.PAGE_URL] = original
    assert secret_miniapp.available()


def test_asset_verification_stops_when_work_mode_switches_on(rig):
    assets = served_assets()

    def fetch(url, *, timeout):
        work_mode.set_enabled(rig.store, True, surface='settings')
        return assets[url]

    read = Mock(side_effect=fetch)
    assert not secret_miniapp.available(fetch=read, store=rig.store)
    read.assert_called_once()


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is not installed')
@pytest.mark.parametrize('bridge', ['native', 'notify', 'web', 'none', 'oversize'])
def test_node_page_bridge_and_webcrypto(bridge):
    key = secret_miniapp.OneTimeKey()
    label = '<literal label>'
    spec = {'v': 1, 'kid': key.kid, 'k': key.spki_b64u(), 'lang': 'en', 'title': 'Fixture',
            'fields': [{'name': 'secret', 'label': label}], 'save': 'Save',
            'no_bridge': 'Open from the button', 'too_long': 'Use the Mac'}
    value = '入力-标记' + (MARKER if bridge != 'oversize' else 'a' * 3000)
    result = subprocess.check_output([
        'node', '-e',
        """const fs = require('node:fs'), vm = require('node:vm');
        const [dir, spec, bridge, value] = process.argv.slice(1);
        const elements = {};
        const element = () => ({ children: [], append(child) { this.children.push(child); } });
        const document = { documentElement: {}, createElement: element,
            getElementById: id => elements[id] ??= element() };
        const events = [];
        const window = { location: { search: '?s=' + spec } };
        window.parent = window;
        if (bridge === 'native' || bridge === 'oversize') {
            window.TelegramWebviewProxy = {
                postEvent(eventType, data) { events.push({ eventType, eventData: JSON.parse(data) }); }
            };
        } else if (bridge === 'notify') {
            window.external = { notify(data) { events.push(JSON.parse(data)); } };
        } else if (bridge === 'web') {
            window.parent = { postMessage(data, origin) { events.push({ ...JSON.parse(data), origin }); } };
        }
        const context = vm.createContext({ window, document, URLSearchParams, TextEncoder, TextDecoder,
            atob, btoa, crypto: require('node:crypto').webcrypto });
        for (const name of ['seal.js', 'page.js']) {
            vm.runInContext(fs.readFileSync(dir + '/' + name, 'utf8'), context);
        }
        (async () => {
            const row = elements.fields?.children[0], input = row?.children[0];
            if (input) {
                input.value = value;
                await elements.save.onclick();
            }
            console.log(JSON.stringify({ events, status: elements.status.textContent,
                label: row?.textContent, type: input?.type, autocomplete: input?.autocomplete,
                cleared: input?.value === '' }));
        })().catch(error => { console.error(error.message); process.exitCode = 1; });""",
        str(PAGE), b64(json.dumps(spec).encode()), bridge, value,
    ], text=True, timeout=10)
    page = json.loads(result)
    events = page['events']
    if bridge == 'none':
        assert page['status'] == spec['no_bridge']
        assert events == []
        return
    assert (page['label'], page['type'], page['autocomplete']) == (label, 'password', 'off')
    assert [event['eventType'] for event in events[:2]] == ['web_app_ready', 'web_app_expand']
    if bridge == 'oversize':
        assert page['status'] == spec['too_long']
        assert len(events) == 2
        return
    assert len(events) == 3
    assert events[2]['eventType'] == 'web_app_data_send'
    if bridge == 'web':
        assert {event['origin'] for event in events} == {'https://web.telegram.org'}
    assert page['cleared']
    assert json.loads(key.open(events[2]['eventData']['data'])) == {'secret': value}


def test_reviewed_assets_csp_integrity_and_release_stamp(tmp_path):
    assets = {path.name: path.read_bytes() for path in PAGE.iterdir()}
    assert {name: hashlib.sha256(data).hexdigest() for name, data in assets.items()} == secret_miniapp.PAGE_SHA256
    html = assets['index.html'].decode()
    assert not re.search(r'https?://', html)
    parsed = []

    class Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            parsed.append((tag, dict(attrs)))

    Parser().feed(html)
    [csp] = [attrs['content'] for tag, attrs in parsed
             if tag == 'meta' and attrs.get('http-equiv') == 'Content-Security-Policy']

    def integrity(data):
        return 'sha256-' + base64.b64encode(hashlib.sha256(data).digest()).decode()

    css = re.search(r'<style>(.*?)</style>', html, flags=re.S)[1]
    assert set(csp.split('; ')) == {
        "default-src 'none'", "script-src 'self'", f"style-src '{integrity(css.encode())}'",
        "connect-src 'none'", "img-src 'none'", "base-uri 'none'", "form-action 'none'",
    }
    assert {attrs['src']: attrs['integrity'] for tag, attrs in parsed if tag == 'script'} == {
        name: integrity(data) for name, data in assets.items() if name.endswith('.js')
    }
    assert all(key not in attrs for _, attrs in parsed for key in ('href', 'action'))
    stamp = runpy.run_path(str(ROOT / 'scripts/stamp_site_release.py'))['stamp']
    copied = Path(shutil.copytree(PAGE, tmp_path / 'site'))
    assert stamp(copied, 'a' * 40, '123.45.67') == []
    assert {path.name: path.read_bytes() for path in copied.iterdir()} == assets


def test_all_new_messages_have_all_locales():
    from personal_agent.messages import catalog
    entries = {key: translations for key, translations in catalog().items() if key.startswith('api_input.miniapp_')}
    assert entries
    for translations in entries.values():
        assert set(translations) == set(LANGUAGES)
        assert all(translations.values())
