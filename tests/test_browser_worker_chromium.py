"""Model-free protocol/CDP contract tests; no Chromium, display or sockets needed."""
import io
import json
import time
from collections import deque
from unittest import mock

import pytest

from personal_agent import browser_worker as shared
from personal_agent import browser_worker_chromium as chromium


class FakeCDP:
    def __init__(self, browser, page=None):
        self.browser, self.page = browser, page
        self.handlers = {}
        self.calls = []

    def on(self, event, fn):
        self.handlers[event] = fn

    def fire(self, event, params):
        handler = self.handlers.get(event)
        if handler:
            handler(params)

    def send(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == 'Target.getTargetInfo':
            return {'targetInfo': {'targetId': self.page.ident}}
        if method == 'Target.attachToTarget':
            return {'sessionId': 'legacy-' + params['targetId']}
        if method == 'Target.sendMessageToTarget':
            packet = json.loads(params['message'])
            self.browser.packets.append((params['sessionId'], packet))
            command, args = packet['method'], packet['params']
            result = {}
            if command == 'Page.getFrameTree':
                result = {'frameTree': {'frame': {'id': 'frame-' + params['sessionId']}}}
            elif command == 'Page.addScriptToEvaluateOnNewDocument':
                result = {'identifier': str(packet['id'])}
            elif command == 'Page.createIsolatedWorld':
                result = {'executionContextId': 101}
            elif command == 'Runtime.callFunctionOn':
                result = {'result': {'value': json.dumps(self.browser.script_results.popleft()
                                                        if self.browser.script_results else {})}}
            elif command == 'Page.captureScreenshot':
                result = {'data': '/9j/fakejpeg'}
            self.fire('Target.receivedMessageFromTarget', {
                'sessionId': params['sessionId'], 'message': json.dumps({'id': packet['id'], 'result': result})})
        if method == 'Browser.getWindowForTarget':
            return {'windowId': 1, 'bounds': {'windowState': self.browser.window_state}}
        return {}


class FakePage:
    def __init__(self, ident):
        self.ident, self.url = ident, 'about:blank'
        self.handlers = {}
        self.closed = False
        self.frames = []

    def on(self, event, fn):
        self.handlers[event] = fn

    def set_viewport_size(self, size):
        self.viewport_size = size

    def is_closed(self):
        return self.closed

    def close(self, **kwargs):
        self.closed = True
        self.handlers['close']()


class FakeContext:
    def __init__(self, browser):
        self.browser = browser
        self.handlers = {}
        self.pages = []
        self.jar = []

    def on(self, event, fn):
        self.handlers[event] = fn

    def route(self, pattern, handler):
        self.routes = getattr(self, 'routes', []) + [(pattern, handler)]

    def new_page(self):
        page = FakePage('page-' + str(len(self.pages)))
        self.pages.append(page)
        self.browser.root.fire('Target.attachedToTarget', {
            'sessionId': 'flat-' + page.ident, 'waitingForDebugger': True,
            'targetInfo': {'type': 'page', 'targetId': page.ident}})
        self.handlers['page'](page)
        return page

    def new_cdp_session(self, page):
        return FakeCDP(self.browser, page)

    def cookies(self):
        return list(self.jar)

    def add_cookies(self, cookies):
        self.jar.extend(cookies)

    def clear_cookies(self, **match):
        self.jar = [c for c in self.jar if not all(c.get(k) == v for k, v in match.items())] if match else []

    def close(self):
        pass


class FakeBrowser:
    version = '140.0.7339.16'

    def __init__(self):
        self.packets = []
        self.script_results = deque()
        self.window_state = 'normal'
        self.root = FakeCDP(self)
        self.context = FakeContext(self)

    def new_context(self, **kwargs):
        self.context_options = kwargs
        return self.context

    def new_browser_cdp_session(self):
        return self.root

    def is_connected(self):
        return True

    def close(self):
        pass


class FakePlaywright:
    def __init__(self):
        self.chromium = self
        self.browser = FakeBrowser()

    def launch(self, **kwargs):
        self.launch_options = kwargs
        return self.browser


@pytest.fixture
def worker():
    engine = FakePlaywright()
    messages = []
    result = chromium.Worker('fixture', messages.append, engine, '/pinned/chrome', ('fixture.test:80',))
    result.messages = messages
    result.engine = engine
    return result


def packets(worker, method):
    return [p['params'] for _, p in worker.engine.browser.packets if p['method'] == method]


def command(worker, op, **kwargs):
    worker.handle({'id': 7, 'op': op, 'timeout': 10, **kwargs})
    return worker.messages[-1] if worker.messages else None


def test_arguments_require_explicit_chromium_and_accumulate_exact_test_origins():
    args = chromium.parse_args(['--profile', 'owner', '--chromium', '/bin/chrome',
                                '--test-allow-origin', '127.0.0.1:87', '--test-allow-origin', 'fixture.test:80'])
    assert (args.profile, args.chromium) == ('owner', '/bin/chrome')
    assert args.test_allow_origin == ['127.0.0.1:87', 'fixture.test:80']
    assert chromium.parse_args(['--chromium', '/bin/chrome']).profile == 'default'
    with pytest.raises(SystemExit):
        chromium.parse_args([])


@pytest.mark.parametrize('platform,dependency,display,expected', [
    ('darwin', True, True, 'unsupported_platform'),
    ('linux', False, True, 'missing_dependency'),
    ('linux', True, False, 'no_display'),
])
def test_unavailable_events(platform, dependency, display, expected):
    output = io.StringIO()
    with mock.patch.object(chromium.sys, 'platform', platform), \
         mock.patch.object(chromium.sys, 'stdout', output), \
         mock.patch.dict(chromium.os.environ, {'DISPLAY': ':8'} if display else {}, clear=True), \
         mock.patch.object(chromium, '_playwright_factory', **(
             {'return_value': mock.Mock()} if dependency else {'side_effect': ImportError('private text')})):
        assert chromium.main(['--chromium', '/pinned/chrome']) == 2
    assert json.loads(output.getvalue()) == {'event': 'unavailable', 'error': expected}


@pytest.mark.parametrize('error,expected', [
    ('No usable sandbox! secret', 'sandbox_unavailable'),
    ('Running as root without --no-sandbox is not supported', 'sandbox_unavailable'),
    ('Failed to move to new namespace: Operation not permitted', 'sandbox_unavailable'),
    ('executable failed: private text', 'chromium_failed'),
])
def test_launch_failures_never_emit_library_text(error, expected):
    output = io.StringIO()
    factory = mock.MagicMock()
    factory.return_value.__enter__.side_effect = RuntimeError(error)
    with mock.patch.object(chromium.sys, 'platform', 'linux'), \
         mock.patch.object(chromium.sys, 'stdout', output), \
         mock.patch.dict(chromium.os.environ, {'WAYLAND_DISPLAY': 'wayland-0'}, clear=True), \
         mock.patch.object(chromium, '_playwright_factory', return_value=factory):
        assert chromium.main(['--chromium', '/pinned/chrome']) == 2
    assert json.loads(output.getvalue()) == {'event': 'unavailable', 'error': expected}


def test_launch_and_single_context_security_contract(worker):
    launch = worker.engine.launch_options
    assert launch['executable_path'] == '/pinned/chrome'
    assert launch['headless'] is False
    assert launch['chromium_sandbox'] is True
    assert '--no-sandbox' not in launch['args']
    assert '--ozone-platform=x11' in launch['args']
    assert f'--window-position={shared.PARK_ORIGIN},{shared.PARK_ORIGIN}' in launch['args']
    for arg in ('--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
                '--disable-background-timer-throttling', '--autoplay-policy=user-gesture-required'):
        assert arg in launch['args']
    options = worker.engine.browser.context_options
    assert options['accept_downloads'] is False
    assert options['service_workers'] == 'block'
    assert options['permissions'] == []
    assert options['has_touch'] is True
    assert 'user_data_dir' not in options


def test_agents_identify_actual_engine_version_and_selected_layout():
    desktop = chromium.user_agent_for('desktop', '140.2.3.4')
    mobile = chromium.user_agent_for('mobile', '140.2.3.4')
    for ua in (desktop, mobile):
        assert 'Chrome/140.2.3.4' in ua
        assert ua.endswith(shared.EMBEDDED_UA_TOKEN)
        assert 'Version/' not in ua and 'iPhone' not in ua
    assert 'X11; Linux' in desktop and 'Android' not in desktop
    assert 'Android' in mobile and 'Mobile' in mobile


def test_cookie_conversion_maps_flags_and_session_expiry():
    row = {'name': 'session', 'value': 'private', 'domain': '.example.test', 'path': '/a',
           'expires': 2000.25, 'secure': True, 'http_only': True, 'same_site': 'strict'}
    cookie = chromium.cookie_object(row, now=1000)
    assert cookie == {'name': 'session', 'value': 'private', 'domain': '.example.test', 'path': '/a',
                      'expires': 2000.25, 'secure': True, 'httpOnly': True, 'sameSite': 'Strict'}
    assert chromium.cookie_row(cookie) == {**row, 'same_site': 'Strict'}
    session = chromium.cookie_object({**row, 'expires': None, 'same_site': None})
    assert 'expires' not in session and 'sameSite' not in session
    assert chromium.cookie_row({**session, 'expires': -1})['expires'] is None
    for value in (0, 999.9, float('nan'), float('inf')):
        assert chromium.cookie_object({**row, 'expires': value}, now=1000) is None
    assert chromium.cookie_object({'name': 'missing value'}) is None


@pytest.mark.parametrize('name', ['Strict', 'Lax', 'None'])
def test_cookie_samesite_round_trip(name):
    row = {'name': 'n', 'value': 'v', 'domain': 'example.test', 'same_site': name.lower()}
    assert chromium.cookie_row(chromium.cookie_object(row))['same_site'] == name


def test_cookie_ops_group_by_psl_and_delete_site_storage(worker):
    worker.context.jar = [
        {'name': 'a', 'value': 'one', 'domain': '.accounts.example.co.uk', 'path': '/'},
        {'name': 'b', 'value': 'two', 'domain': 'shop.example.co.uk', 'path': '/'},
        {'name': 'c', 'value': 'three', 'domain': 'other.test', 'path': '/'},
    ]
    worker.hosts = {'accounts.example.co.uk', 'other.test'}
    answer = command(worker, 'cookies_export')
    assert set(answer['sites']) == {'example.co.uk', 'other.test'}
    assert len(answer['sites']['example.co.uk']) == 2
    assert answer['hosts'] == sorted(worker.hosts)
    worker.origins = {'https://accounts.example.co.uk', 'https://shop.example.co.uk', 'https://other.test'}
    command(worker, 'cookies_delete', site='example.co.uk')
    assert [c['name'] for c in worker.context.jar] == ['c']
    assert {p['origin'] for p in packets(worker, 'Storage.clearDataForOrigin')} == {
        'https://accounts.example.co.uk', 'https://shop.example.co.uk'}
    command(worker, 'cookies_clear')
    assert worker.context.jar == []


def test_cookie_import_drops_expired_and_returns_accepted_count(worker):
    result = command(worker, 'cookies_import', cookies=[
        {'name': 'n', 'value': 'v', 'domain': 'example.test', 'http_only': True},
        {'name': 'expired', 'value': 'v', 'domain': 'example.test', 'expires': 1}, None])
    assert result['imported'] == 1
    assert worker.context.jar[0]['httpOnly'] is True


def test_binding_is_scoped_to_one_isolated_world_and_never_page_world(worker):
    bindings = packets(worker, 'Runtime.addBinding')
    assert bindings == [{'name': chromium.BINDING, 'executionContextName': 'still'}]
    scripts = packets(worker, 'Page.addScriptToEvaluateOnNewDocument')
    isolated = [p for p in scripts if p.get('worldName') == 'still']
    page_world = [p for p in scripts if 'worldName' not in p]
    assert len(isolated) == 1
    assert chromium.BINDING in isolated[0]['source']
    assert shared.GUARD_HANDLER in isolated[0]['source']
    assert 'window === window.top' in isolated[0]['source']
    assert all(chromium.BINDING not in p['source'] for p in page_world)
    assert any(shared.PAGE_WRAP_USER_SCRIPT in p['source'] for p in page_world)
    assert any('Linux armv8l' in p['source'] and 'window === window.top' not in p['source'] for p in page_world)
    assert all(p['runImmediately'] for p in scripts)
    worker.guard_off = True
    worker._install_guard_scripts()
    new_scripts = packets(worker, 'Page.addScriptToEvaluateOnNewDocument')[len(scripts):]
    assert not any(shared.PAGE_WRAP_USER_SCRIPT in p['source'] for p in new_scripts)


def test_forged_binding_and_subframe_context_are_ignored(worker):
    target = worker.main_target
    target.contexts[target.main_frame] = 101
    payload = json.dumps({'press': 'nonce', 'hit': True})
    for name, context in ((chromium.BINDING, 999), ('pageBinding', 101)):
        worker.binding_called(target, {'name': name, 'executionContextId': context, 'payload': payload})
        assert worker.press_seen is None
    worker.binding_called(target, {'name': chromium.BINDING, 'executionContextId': 101, 'payload': payload})
    assert worker.press_seen == {'nonce': 'nonce', 'hit': True}


def test_autoattachment_installs_fetch_before_resuming_popup_and_oopif(worker):
    root_calls = worker.engine.browser.root.calls
    auto = next(p for m, p in root_calls if m == 'Target.setAutoAttach')
    assert auto['flatten'] is True and auto['waitForDebuggerOnStart'] is True
    assert auto['filter'] == chromium.PAGE_FILTER
    methods = [p['method'] for _, p in worker.engine.browser.packets]
    assert methods.index('Fetch.enable') < methods.index('Runtime.runIfWaitingForDebugger')
    assert packets(worker, 'Fetch.enable')[0]['patterns'] == [
        {'urlPattern': '*', 'requestStage': 'Request', 'resourceType': 'Document'}]
    assert packets(worker, 'Target.setAutoAttach')[0]['filter'] == chromium.FRAME_FILTER
    assert any(m == 'Target.detachFromTarget' and p['sessionId'].startswith('flat-') for m, p in root_calls)


def test_redirect_hop_is_checked_before_network_continues(worker):
    target = worker.main_target
    event = {'requestId': 'redirect-request', 'redirectedRequestId': 'initial-request',
             'frameId': target.main_frame, 'resourceType': 'Document',
             'request': {'url': 'http://127.0.0.1:99/private', 'method': 'GET'}}
    with mock.patch.object(chromium.shared, 'destination_refusal', return_value='blocked_destination') as check:
        # The inherited resolver only posts its result back to the main thread.
        worker.request_paused(target, event)
        end = time.monotonic() + 1
        while not packets(worker, 'Fetch.failRequest') and time.monotonic() < end:
            worker.AppHelper.pump()
            time.sleep(.001)
    check.assert_called_once_with(event['request']['url'], worker.allowed_origins, intranet=worker.intranet)
    assert packets(worker, 'Fetch.failRequest')[-1] == {
        'requestId': 'redirect-request', 'errorReason': 'Aborted'}
    assert not packets(worker, 'Fetch.continueRequest')
    assert worker.blocked == 1 and worker.blocked_reason == 'blocked_destination'


def test_allowed_redirect_continues_once_without_rechecking_form_allowance(worker):
    target = worker.main_target
    event = {'requestId': 'next', 'redirectedRequestId': 'previous', 'frameId': target.main_frame,
             'request': {'url': 'https://public.test/next', 'method': 'POST'}}
    with mock.patch.object(worker, 'decide', side_effect=lambda url, main, done: done(True)), \
         mock.patch.object(worker, 'check_form_navigation') as form:
        worker.request_paused(target, event)
    form.assert_not_called()
    assert packets(worker, 'Fetch.continueRequest')[-1] == {'requestId': 'next'}
    assert worker.posted_submits == 0


def test_native_form_navigation_is_checked_and_cancelled_record_is_preserved(worker, monkeypatch):
    # #34: a submission the guard never vetted is aborted first (Chromium runs no page script while it is
    # paused), then the page names the payment form it was, which is held for the owner's approval.
    monkeypatch.setattr(chromium, 'VETTED_WAIT_SECONDS', 0)
    target = worker.main_target
    worker.engine.browser.script_results.append({'allow': False, 'cancelled': {
        'id': 'hold', 'dom': 0, 'method': 'post', 'action': 'https://public.test/submit',
        'page': 'https://public.test/page', 'state': 'payload'}})
    worker.request_paused(target, {'requestId': 'post', 'frameId': target.main_frame,
                                  'request': {'url': 'https://public.test/submit', 'method': 'POST'}})
    assert worker.posted_submits == 1
    assert worker.cancelled['id'] == 'hold'
    assert packets(worker, 'Fetch.failRequest')[-1]['requestId'] == 'post'
    assert not packets(worker, 'Fetch.continueRequest')
    assert worker.deciding == 0


def test_load_more_hold_refuses_navigation_without_leaving_page(worker):
    worker.reading = True
    worker.request_paused(worker.main_target, {'requestId': 'read-nav', 'frameId': worker.main_target.main_frame,
                                              'request': {'url': 'https://public.test/', 'method': 'GET'}})
    assert worker.read_navigations == 1
    assert not packets(worker, 'Fetch.continueRequest')
    assert packets(worker, 'Fetch.failRequest')[-1]['requestId'] == 'read-nav'


def test_context_destruction_recreates_named_world_and_awaits_async_scripts(worker):
    target = worker.main_target
    target.contexts[target.main_frame] = 101
    target.receive({'method': 'Runtime.executionContextDestroyed', 'params': {'executionContextId': 101}})
    assert target.contexts == {}
    worker.engine.browser.script_results.append({'ok': True})
    answers = []
    worker.run('await Promise.resolve(); return JSON.stringify({ok: true});', {'nonce': 'abc'},
               lambda value, error: answers.append((value, error)))
    assert packets(worker, 'Page.createIsolatedWorld')[-1] == {'frameId': target.main_frame, 'worldName': 'still'}
    call = packets(worker, 'Runtime.callFunctionOn')[-1]
    assert call['awaitPromise'] is True and call['returnByValue'] is True
    assert call['executionContextId'] == 101 and call['arguments'] == [{'value': 'abc'}]
    assert answers == [({'ok': True}, None)]


def test_native_input_is_single_press_and_remote_keys_use_cdp(worker):
    worker.press(20, 30)
    mouse = packets(worker, 'Input.dispatchMouseEvent')
    assert [p['type'] for p in mouse] == ['mouseMoved', 'mousePressed', 'mouseReleased']
    assert all(p['x'] == 20 and p['y'] == 30 for p in mouse)
    worker.key('Enter')
    keys = packets(worker, 'Input.dispatchKeyEvent')
    assert [p['type'] for p in keys] == ['keyDown', 'keyUp']
    assert keys[0]['key'] == 'Enter' and keys[0]['text'] == '\r'


def test_timed_out_press_keeps_posted_and_ends_step(worker):
    worker.pending[7] = True
    worker.expiry[7] = time.monotonic() - 1
    worker.posted_submits, worker.step_posted = 1, 0
    worker.dialog_step = {'approved': False, 'dialogs': []}
    worker.deadline(7, 10, worker.end_step, worker.step_posted_field)
    worker.AppHelper.pump()
    assert worker.messages[-1] == {'id': 7, 'ok': False, 'error': 'timeout', 'posted': True}
    assert worker.dialog_step is None and 7 not in worker.pending


def test_synchronous_completion_after_deadline_returns_timeout(worker):
    worker.expiry[7] = time.monotonic() - 1
    worker.reply(7, imported=1)
    assert worker.messages[-1] == {'id': 7, 'ok': False, 'error': 'timeout'}


@pytest.mark.parametrize('error,code', [
    ('net::ERR_NAME_NOT_RESOLVED', 'host_not_found'),
    ('net::ERR_CONNECTION_REFUSED', 'host_unreachable'),
    ('net::ERR_CERT_AUTHORITY_INVALID', 'host_unreachable'),
    ('net::ERR_ABORTED', 'navigation_failed'),
])
def test_navigation_error_mapping(error, code):
    assert chromium.navigation_error_code(error) == code


def test_layout_change_and_window_show_hide(worker):
    result = command(worker, 'navigate', url='https://public.test/', layout='desktop')
    assert result is None
    assert worker.layout == 'desktop'
    assert packets(worker, 'Emulation.setDeviceMetricsOverride')[-1]['width'] == 1280
    assert 'X11; Linux' in packets(worker, 'Emulation.setUserAgentOverride')[-1]['userAgent']
    worker.page.url = 'https://public.test/'
    worker.loaded(worker.main_target)
    assert worker.messages[-1]['layout'] == 'desktop'
    command(worker, 'show')
    assert worker.owner_visible and worker.guard_off
    command(worker, 'hide')
    assert not worker.owner_visible and not worker.guard_off
    assert any(m == 'Browser.setWindowBounds' and p['bounds'].get('left') == shared.PARK_ORIGIN
               for m, p in worker.clients[worker.page].calls)


def test_minimizing_owner_window_emits_hidden(worker):
    worker.owner_visible = True
    worker.engine.browser.window_state = 'minimized'
    worker.poll_windows()
    assert not worker.owner_visible and not worker.guard_off
    assert worker.messages[-1] == {'event': 'hidden'}


def test_closed_window_replaces_page_in_same_context_and_reports_hidden(worker):
    context = worker.context
    worker.owner_visible = True
    worker.page.close()
    worker.AppHelper.pump()
    assert worker.context is context and not worker.page.is_closed()
    assert worker.messages[-1] == {'event': 'hidden'}


def test_remote_text_refuses_noneditable_focus_and_uses_native_insert(worker):
    worker.owner_visible = True
    worker.engine.browser.script_results.append({'focus': None})
    assert command(worker, 'remote_text', text='private')['error'] == 'not_typable'
    assert not packets(worker, 'Input.insertText')
    worker.engine.browser.script_results.append({'focus': 'password'})
    result = command(worker, 'remote_text', text='private')
    assert result == {'id': 7, 'ok': True}
    assert packets(worker, 'Input.insertText')[-1] == {'text': 'private'}
    assert 'private' not in json.dumps(worker.messages)


def test_frame_reports_focus_and_no_qr_links(worker):
    assert command(worker, 'frame')['error'] == 'not_shown'
    worker.owner_visible = True
    worker.engine.browser.script_results.append({'focus': 'text', 'fields': []})
    answer = command(worker, 'frame')
    assert answer['jpeg'] == '/9j/fakejpeg' and answer['links'] == []
    assert answer['width'] == shared.MOBILE_SIZE[0]
    assert answer['focus'] == 'text' and answer['dialog'] is None
    assert packets(worker, 'Page.captureScreenshot')[-1]['format'] == 'jpeg'


def test_payment_dialog_decision_and_remote_answer(worker):
    worker.dialog_step = {'approved': False, 'confirm_ok': False, 'dialogs': []}
    dialog = mock.Mock(type='confirm', message='Confirm?')
    worker.dialog(dialog)
    dialog.dismiss.assert_called_once()
    assert worker.dialog_step['dialogs'][-1]['outcome'] == 'declined_payment'
    worker.owner_visible = worker.remote = True
    remote = mock.Mock(type='confirm', message='Owner question')
    worker.dialog(remote)
    assert worker.held_dialog['text'] == 'Owner question'
    assert command(worker, 'remote_dialog', answer='ok')['answered'] is True
    remote.accept.assert_called_once()
    assert worker.held_dialog is None


def test_every_shared_dispatch_op_has_an_implementation_and_unknown_is_typed(worker):
    ops = {name for name in vars(shared.Worker) if name.startswith('op_')}
    assert {name.removeprefix('op_') for name in ops} == {
        'navigate', 'settle', 'snapshot', 'click', 'type', 'release_submit',
        'load_more', 'load_more_stop', 'show', 'hide', 'state', 'frame',
        'remote_begin', 'remote_end', 'remote_tap', 'remote_text', 'remote_key', 'remote_nav', 'remote_dialog',
        'cookies_export', 'cookies_import', 'cookies_delete', 'cookies_clear', 'quit'}
    for name in ops:
        assert callable(getattr(worker, name))
    # These execute the *same* state machine, not a second Linux copy.
    for name in ('op_click', 'op_type', 'op_release_submit', 'op_snapshot', 'op_settle',
                 'op_load_more', 'op_load_more_stop', 'op_state', 'op_remote_key', 'op_remote_begin',
                 'op_remote_end', 'op_remote_dialog', 'op_hide'):
        assert getattr(chromium.Worker, name) is getattr(shared.Worker, name)
    assert command(worker, 'invented')['error'] == 'unknown_op'


def test_unconfirmed_click_returns_pressed_without_replaying(worker):
    worker.engine.browser.script_results.extend([
        {'x': 20, 'y': 30, 'editable': False},
        {'error': 'press_no_click', 'pressed': True},
        {},
    ])
    answer = command(worker, 'click', index=0, expect={})
    assert answer == {'id': 7, 'ok': False, 'error': 'press_no_click', 'pressed': True, 'posted': False}
    assert [p['type'] for p in packets(worker, 'Input.dispatchMouseEvent')] == [
        'mouseMoved', 'mousePressed', 'mouseReleased']
    scripts = packets(worker, 'Runtime.callFunctionOn')
    assert shared.LOCATE_SCRIPT in scripts[0]['functionDeclaration']
    assert shared.VERIFY_CLICK_SCRIPT in scripts[1]['functionDeclaration']
    assert shared.STEP_END_SCRIPT in scripts[2]['functionDeclaration']
    assert len(packets(worker, 'Page.createIsolatedWorld')) == 1
    assert len({p['executionContextId'] for p in scripts}) == 1


def test_session_storage_is_cleared_in_isolated_frame_world(worker):
    target = worker.main_target
    worker.origins = {'https://public.test'}
    worker.frame_origins[(target.ident, 'child')] = 'https://public.test'
    result = command(worker, 'cookies_clear')
    assert result == {'id': 7, 'ok': True}
    assert packets(worker, 'Page.createIsolatedWorld')[-1] == {'frameId': 'child', 'worldName': 'still'}
    assert 'sessionStorage.clear()' in packets(worker, 'Runtime.callFunctionOn')[-1]['functionDeclaration']


def test_subframe_network_failure_does_not_fail_main_navigation(worker):
    worker.pending[7] = True
    worker.navigation = (7, None)
    worker.network_failed(worker.main_target, {'requestId': 'child', 'type': 'Document',
                                               'errorText': 'net::ERR_NAME_NOT_RESOLVED'})
    assert worker.navigation == (7, None) and worker.messages == []
    worker.main_requests.add('main-request')
    worker.network_failed(worker.main_target, {'requestId': 'main-request', 'type': 'Document',
                                               'errorText': 'net::ERR_NAME_NOT_RESOLVED'})
    assert worker.messages[-1]['error'] == 'host_not_found'


def test_ai_popup_post_is_adopted_without_replaying_the_request(worker):
    old_page = worker.page
    popup = worker.context.new_page()
    worker.register_page(popup)
    target = worker.target_for(popup)
    # #34: the guard let this submit through (its own record), so the paused request proceeds.
    worker.note_vetted({'vetted': {'method': 'post', 'action': 'https://public.test/submit', 'payment': False}})
    with mock.patch.object(worker, 'decide', side_effect=lambda url, main, done: done(True)):
        worker.request_paused(target, {'requestId': 'original-post', 'frameId': target.main_frame,
                                      'request': {'url': 'https://public.test/submit', 'method': 'POST'}})
    assert worker.page is popup and old_page.is_closed()
    assert worker.posted_submits == 1
    assert packets(worker, 'Fetch.continueRequest')[-1] == {'requestId': 'original-post'}
    assert not packets(worker, 'Page.navigate'), 'never recreate a native request or its body'


@pytest.mark.parametrize('layout', ['', False, 0, 'unknown'])
def test_invalid_layout_is_rejected_without_navigation(worker, layout):
    assert command(worker, 'navigate', url='https://public.test/', layout=layout)['error'] == 'bad_layout'
    assert not packets(worker, 'Page.navigate')


def test_oopif_attachment_uses_recursive_target_envelope_and_never_overrides_frame_metrics(worker):
    parent = worker.main_target
    # A child target must be initialized through its parent's CDP envelope,
    # not through an unrelated page's context or an unpaused navigation event.
    worker.attached(parent, {'sessionId': 'oopif', 'waitingForDebugger': True,
                             'targetInfo': {'type': 'iframe', 'targetId': 'child-target'}})
    envelopes = [p['params'] for _, p in worker.engine.browser.packets
                 if p['method'] == 'Target.sendMessageToTarget' and p['params']['sessionId'] == 'oopif']
    messages = [json.loads(p['message']) for p in envelopes]
    methods = [p['method'] for p in messages]
    assert 'Fetch.enable' in methods and 'Runtime.addBinding' in methods
    assert methods.index('Fetch.enable') < methods.index('Runtime.runIfWaitingForDebugger')
    assert 'Emulation.setDeviceMetricsOverride' not in methods
    binding = next(p['params'] for p in messages if p['method'] == 'Runtime.addBinding')
    assert binding['executionContextName'] == 'still'
    assert 'Emulation.setUserAgentOverride' in methods


def test_isolated_world_initialization_failure_closes_target_instead_of_continuing(worker):
    target = worker.main_target
    target.receive({'id': 99999, 'error': {'code': -1, 'message': 'binding setup failed'}})
    assert worker.fatal
    assert worker.root_cdp.calls[-1] == ('Target.closeTarget', {'targetId': target.ident})


def test_dialog_in_another_target_cannot_clear_the_held_owner_dialog(worker):
    worker.owner_visible = True
    dialog = mock.Mock(type='confirm', message='Owner question')
    worker.dialog(dialog)
    worker.native_dialog_closed(object(), {'result': False})
    assert worker.held_dialog is not None
    worker.native_dialog_closed(worker.main_target, {'result': False})
    assert worker.held_dialog is None



def test_deadline_still_answers_if_cancellation_transport_has_already_closed(worker):
    worker.expiry[7] = time.monotonic() - 1
    worker.deadline(7, 1, mock.Mock(side_effect=RuntimeError('closed transport')))
    worker.AppHelper.pump()
    assert worker.messages[-1] == {'id': 7, 'ok': False, 'error': 'timeout'}


def test_main_emits_one_ready_and_processes_pipe_commands_on_main_thread(capsys):
    import threading

    engine = FakePlaywright()
    stream = io.StringIO()
    pipe = io.StringIO('{"id": 1, "op": "state"}\n{"id": 2, "op": "quit"}\n')
    manager = mock.MagicMock()

    def entered():
        print('third-party diagnostic must go to stderr')
        return engine

    manager.__enter__.side_effect = entered
    owner_thread = threading.get_ident()
    called_threads = []
    original_send = FakeCDP.send

    def send(session, method, params=None):
        called_threads.append(threading.get_ident())
        return original_send(session, method, params)

    # The fake event pump yields to the actual stdin thread, with no browser.
    with mock.patch.object(chromium.sys, 'platform', 'linux'), \
         mock.patch.object(chromium.sys, 'stdout', stream), \
         mock.patch.object(chromium.sys, 'stdin', pipe), \
         mock.patch.dict(chromium.os.environ, {'DISPLAY': ':8'}, clear=True), \
         mock.patch.object(chromium, '_playwright_factory', return_value=lambda: manager), \
         mock.patch.object(FakePage, 'wait_for_timeout', lambda self, ms: time.sleep(.001), create=True), \
         mock.patch.object(FakeCDP, 'send', send):
        assert chromium.main(['--profile', 'owner', '--chromium', '/pinned/chrome']) == 0
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert rows[0] == {'event': 'ready', 'profile': 'owner', 'pid': chromium.os.getpid()}
    assert rows[1:] == [{'id': 1, 'ok': True, 'visible': False, 'navigations': 0}, {'id': 2, 'ok': True}]
    assert called_threads and set(called_threads) == {owner_thread}
    assert 'third-party diagnostic' in capsys.readouterr().err


def test_bare_url_reads_the_place_a_submission_goes_as_the_guard_does():
    assert chromium.bare_url('HTTPS://Shop.Test:443/pay?x=1#f') == 'https://shop.test/pay'
    assert chromium.bare_url('http://shop.test:8080') == 'http://shop.test:8080/'
    assert chromium.bare_url('javascript:alert(1)') == ''


def test_a_vetted_submission_proceeds_and_a_payment_pass_is_used_once(worker):
    worker.note_vetted({'vetted': {'method': 'post', 'action': 'https://shop.test/pay', 'payment': True}})
    assert worker.take_vetted('POST', 'https://shop.test/pay?step=2')
    assert not worker.take_vetted('POST', 'https://shop.test/pay'), 'a payment pass is spent once'
    worker.note_vetted({'vetted': {'method': 'post', 'action': 'https://shop.test/search', 'payment': False}})
    assert worker.take_vetted('POST', 'https://shop.test/search')
    assert not worker.take_vetted('GET', 'https://shop.test/search'), 'the method must match'
    assert not worker.take_vetted('POST', 'https://other.test/search'), 'the place must match'


def test_a_payment_pass_the_page_withdrew_vouches_for_nothing(worker):
    entry = {'method': 'post', 'action': 'https://shop.test/pay', 'payment': True}
    worker.note_vetted({'vetted': entry})
    worker.note_vetted({'unvetted': entry})
    assert not worker.take_vetted('POST', 'https://shop.test/pay')


def test_an_unvetted_ordinary_submission_is_never_sent(worker, monkeypatch):
    """Fail closed (#34): the page says it is no payment form, but the request was already refused."""
    monkeypatch.setattr(chromium, 'VETTED_WAIT_SECONDS', 0)
    target = worker.main_target
    worker.engine.browser.script_results.append({'allow': True})
    worker.request_paused(target, {'requestId': 'post', 'frameId': target.main_frame,
                                  'request': {'url': 'https://public.test/submit', 'method': 'POST'}})
    assert packets(worker, 'Fetch.failRequest')[-1]['requestId'] == 'post'
    assert not packets(worker, 'Fetch.continueRequest')
    assert worker.refused_submits == 1 and worker.cancelled is None
    assert worker.deciding == 0


def test_the_vetted_notice_runs_only_in_the_isolated_world(worker):
    sources = packets(worker, 'Page.addScriptToEvaluateOnNewDocument')
    with_notice = [source for source in sources if "Object.defineProperty(s, 'vetted'" in source['source']]
    assert with_notice and all(source.get('worldName') == chromium.WORLD for source in with_notice)


class FakeRoute:
    def __init__(self):
        self.outcome = None

    def continue_(self):
        self.outcome = 'continue'

    def abort(self, reason):
        self.outcome = 'abort:' + reason


class FakeRequest:
    def __init__(self, page, url, navigation=True, child=False):
        self.url, self.method = url, 'GET'
        self.frame = mock.Mock(page=page, parent_frame=object() if child else None)
        self._navigation = navigation

    def is_navigation_request(self):
        return self._navigation


def test_every_request_of_the_context_passes_the_popup_gate(worker):
    """#34: Playwright resumes a new page before this worker's CDP session exists; its route holds the
    popup's first request so it is decided like any other."""
    assert [pattern for pattern, _ in worker.context.routes] == ['**/*']


def test_a_popups_first_request_to_a_refused_destination_is_aborted(worker):
    popup = worker.context.new_page()
    worker.register_page(popup)
    target = worker.target_for(popup)
    target.ready = True
    route = FakeRoute()
    with mock.patch.object(worker, 'decide', side_effect=lambda url, main, done: done(False, 'blocked_destination')):
        worker.route_popup(route, FakeRequest(popup, 'http://127.0.0.1:8787/'))
    assert route.outcome == 'abort:aborted'
    assert worker.blocked_reason == 'blocked_destination'


def test_the_main_page_and_sub_resources_are_left_to_the_worker_own_fetch(worker):
    for request in (FakeRequest(worker.page, 'https://public.test/'), FakeRequest(object(), 'https://cdn.test/x.js',
                                                                                     navigation=False)):
        route = FakeRoute()
        worker.route_popup(route, request)
        assert route.outcome == 'continue'


def test_a_popup_that_cannot_be_identified_is_refused(worker, monkeypatch):
    monkeypatch.setattr(shared, 'WARM_SECONDS', 0)
    route = FakeRoute()
    stranger = mock.Mock(url='about:blank')
    with mock.patch.object(worker, 'popup_target', return_value=None):
        worker.route_popup(route, FakeRequest(stranger, 'https://public.test/'))
    assert route.outcome == 'abort:aborted'
    assert worker.blocked_reason == 'navigation_failed', 'the step is told its navigation was refused'


def test_an_unmapped_popup_is_matched_to_the_one_new_page_target(worker):
    popup = mock.Mock(url='about:blank')
    worker.attached(worker.root_cdp, {'sessionId': 'flat-popup', 'waitingForDebugger': True,
                                      'targetInfo': {'type': 'page', 'targetId': 'popup-target'}})
    target = worker.popup_target(popup)
    assert target is not None and target.ident == 'popup-target'
    assert worker.popup_target(popup) is target, 'mapped once, found again'
