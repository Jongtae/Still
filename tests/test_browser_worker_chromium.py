"""Model-free protocol/CDP contract tests; no Chromium, display or sockets needed."""
import ctypes
import io
import json
import time
from collections import deque
from unittest import mock

import pytest

from personal_agent import browser_worker as shared
from personal_agent import browser_worker_chromium as chromium

_native_relinquish_keyboard_focus = chromium.relinquish_keyboard_focus


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
            return {'targetInfo': {'targetId': self.page.ident, 'type': 'page'}}
        if method == 'Target.attachToTarget':
            return {'sessionId': 'legacy-' + params['targetId']}
        if method == 'Target.closeTarget':
            for page in self.browser.context.pages:
                if page.ident == params['targetId']:
                    if 'close' in page.handlers:
                        page.close()
                    else:
                        page.closed = True
        if method == 'Target.sendMessageToTarget':
            packet = json.loads(params['message'])
            self.browser.packets.append((params['sessionId'], packet))
            command, args = packet['method'], packet['params']
            result = {}
            if command == 'Page.getFrameTree':
                result = {'frameTree': {'frame': {'id': 'frame-' + params['sessionId']}}}
            elif command == 'Page.addScriptToEvaluateOnNewDocument':
                result = {'identifier': str(packet['id'])}
            elif command == 'Page.getNavigationHistory':
                result = getattr(self.browser, 'navigation_history', {})
            elif command == 'Page.createIsolatedWorld':
                result = {'executionContextId': 101}
            elif command == 'Runtime.callFunctionOn' and not args['functionDeclaration'].startswith('async function() {\nawait new Promise(resolve => requestAnimationFrame('):
                result = {'result': {'value': json.dumps(self.browser.script_results.popleft()
                                                        if self.browser.script_results else {})}}
            elif command == 'Page.captureScreenshot':
                result = {'data': '/9j/fakejpeg'}
            elif command == 'Page.getLayoutMetrics':
                result = getattr(self.browser, 'layout_metrics', {'cssVisualViewport': {'pageX': 0, 'pageY': 0}})
            elif command == 'Page.navigate':
                result = getattr(self.browser, 'navigate_result', {'frameId': 'f', 'loaderId': 'loader'})
            response = {'sessionId': params['sessionId'],
                        'message': json.dumps({'id': packet['id'], 'result': result})}
            if command == getattr(self.browser, 'hold_method', None):
                self.browser.held_responses.append(lambda: self.fire('Target.receivedMessageFromTarget', response))
            else:
                self.fire('Target.receivedMessageFromTarget', response)
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
        if 'close' in self.handlers:
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

    def new_page(self, reported=True):
        page = FakePage('page-' + str(len(self.pages)))
        self.pages.append(page)
        self.browser.root.fire('Target.targetCreated', {
            'targetInfo': {'type': 'page', 'targetId': page.ident}})
        if reported:
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


@pytest.fixture(autouse=True)
def no_native_display():
    with mock.patch.object(chromium, 'relinquish_keyboard_focus'):
        yield


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
         mock.patch.dict(chromium.os.environ, {'DISPLAY': ':0'}, clear=True), \
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
    assert options['has_touch'] is False
    assert options['viewport'] == {'width': int(shared.MOBILE_SIZE[0]), 'height': int(shared.MOBILE_SIZE[1])}
    assert options['user_agent'] == chromium.user_agent_for('mobile', worker.version)
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


def test_discovery_installs_guard_without_a_second_browser_autoattach(worker):
    root_calls = worker.engine.browser.root.calls
    assert ('Target.setDiscoverTargets', {'discover': True, 'filter': chromium.PAGE_FILTER}) in root_calls
    assert not any(m == 'Target.setAutoAttach' for m, _ in root_calls)
    methods = [p['method'] for _, p in worker.engine.browser.packets]
    assert methods.index('Fetch.enable') < methods.index('Runtime.runIfWaitingForDebugger')
    assert packets(worker, 'Fetch.enable')[0]['patterns'] == [
        {'urlPattern': '*', 'requestStage': 'Request', 'resourceType': 'Document'}]
    assert packets(worker, 'Target.setAutoAttach')[0]['filter'] == chromium.FRAME_FILTER
    assert not any(m == 'Target.detachFromTarget' for m, _ in root_calls)


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
    worker.frame_navigated(worker.main_target, {'id': worker.main_target.main_frame,
                                               'loaderId': 'loader', 'url': worker.page.url})
    worker.loaded(worker.main_target, {'frameId': worker.main_target.main_frame, 'loaderId': 'loader'})
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
    worker.navigation = (7, 'requested')
    worker.network_failed(worker.main_target, {'requestId': 'child', 'type': 'Document',
                                               'errorText': 'net::ERR_NAME_NOT_RESOLVED'})
    assert worker.navigation == (7, 'requested') and worker.messages == []
    worker.main_requests['main-request'] = 'requested'
    worker.network_failed(worker.main_target, {'requestId': 'main-request', 'type': 'Document',
                                               'errorText': 'net::ERR_NAME_NOT_RESOLVED'})
    assert worker.messages[-1]['error'] == 'host_not_found'


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
    worker.owner_visible = worker.guard_off = True
    popup = worker.context.new_page()
    worker.register_page(popup)
    target = worker.target_for(popup)
    target.ready = True
    route = FakeRoute()
    with mock.patch.object(worker, 'decide', side_effect=lambda url, main, done: done(False, 'blocked_destination')):
        worker.route_popup(route, FakeRequest(popup, 'http://127.0.0.1:8787/'))
    assert route.outcome == 'abort:aborted'
    assert popup.is_closed()
    assert worker.blocked == 0


def test_the_main_page_and_sub_resources_are_left_to_the_worker_own_fetch(worker):
    for request in (FakeRequest(worker.page, 'https://public.test/'), FakeRequest(worker.page, 'https://cdn.test/x.js',
                                                                                     navigation=False)):
        route = FakeRoute()
        worker.route_popup(route, request)
        assert route.outcome == 'continue'


def test_a_popup_that_cannot_be_identified_is_refused(worker, monkeypatch):
    worker.owner_visible = worker.guard_off = True
    monkeypatch.setattr(shared, 'WARM_SECONDS', 0)
    route = FakeRoute()
    stranger = mock.Mock(url='about:blank')
    with mock.patch.object(worker, 'popup_target', return_value=None):
        worker.route_popup(route, FakeRequest(stranger, 'https://public.test/'))
    assert route.outcome == 'abort:aborted'
    assert worker.blocked == 0


def test_a_reported_popup_uses_its_public_cdp_identity(worker):
    worker.owner_visible = worker.guard_off = True
    popup = worker.context.new_page()
    assert worker.popup_target(popup) is worker.targets[popup.ident]
    assert worker.pages[popup.ident] is popup


class UnreportedPopupRequest(FakeRequest):
    @property
    def frame(self):
        raise RuntimeError('Frame for this navigation request is not available')

    @frame.setter
    def frame(self, value):
        pass


def unreported_popup(worker):
    worker.owner_visible = worker.guard_off = True
    page = worker.context.new_page(reported=False)
    target = worker.targets[page.ident]
    assert target.ready
    assert target.ident not in worker.pages
    return page, target


def popup_fetch(target, url='https://public.test/', **fields):
    return {'requestId': 'first-fetch', 'frameId': target.main_frame,
            'request': {'url': url, 'method': 'GET'}, **fields}


def test_unreported_popup_refusal_aborts_reports_and_closes(worker):
    popup, target = unreported_popup(worker)
    route = FakeRoute()
    with mock.patch.object(worker, 'decide', side_effect=lambda url, main, done: done(False, 'blocked_destination')), \
         mock.patch.object(worker, 'navigation_refused', wraps=worker.navigation_refused) as refused:
        worker.route_popup(route, UnreportedPopupRequest(popup, 'http://127.0.0.1:8787/'))
    refused.assert_called_once_with(False, 'blocked_destination')
    assert route.outcome == 'abort:aborted'
    assert popup.is_closed() and worker.blocked == 0
    assert worker.page is not popup
    worker.AppHelper.pump()
    assert worker.deciding == 0


def test_owner_popup_is_released_without_replacing_main_page(worker):
    old = worker.page
    popup, target = unreported_popup(worker)
    route = FakeRoute()
    with mock.patch.object(worker, 'decide', side_effect=lambda url, counted, done: done(True)):
        worker.route_popup(route, UnreportedPopupRequest(popup, 'https://public.test/'))
    assert route.outcome == 'continue'
    worker.context.handlers['page'](popup)
    worker.AppHelper.pump()
    assert worker.page is old and not old.is_closed() and popup in worker.popups
    assert not packets(worker, 'Page.navigate')


@pytest.mark.parametrize('fetch_first', [False, True])
@pytest.mark.parametrize('allowed', [False, True])
def test_both_initial_interceptors_wait_for_one_policy_decision(worker, fetch_first, allowed):
    popup, target = unreported_popup(worker)
    route = FakeRoute()
    request = UnreportedPopupRequest(popup, 'https://public.test/')
    callbacks = []
    with mock.patch.object(worker, 'decide', side_effect=lambda url, counted, done: callbacks.append(done)) as decide:
        actions = [lambda: worker.route_popup(route, request),
                   lambda: worker.fetch_paused(target, popup_fetch(target))]
        for action in reversed(actions) if fetch_first else actions:
            action()
        assert route.outcome is None
        assert not packets(worker, 'Fetch.continueRequest') and not packets(worker, 'Fetch.failRequest')
        assert decide.call_count == 1
        callbacks[0](allowed, None if allowed else 'blocked_destination')
    assert route.outcome == ('continue' if allowed else 'abort:aborted')
    assert packets(worker, 'Fetch.continueRequest' if allowed else 'Fetch.failRequest')[-1]['requestId'] == 'first-fetch'
    assert popup.is_closed() is not allowed


def test_a_later_same_url_navigation_and_same_url_redirect_are_not_initial_request_passes(worker):
    popup, target = unreported_popup(worker)
    route = FakeRoute()
    with mock.patch.object(worker, 'decide', side_effect=lambda url, counted, done: done(True)) as decide:
        worker.route_popup(route, UnreportedPopupRequest(popup, 'https://public.test/'))
        worker.fetch_paused(target, popup_fetch(target))
        worker.fetch_paused(target, popup_fetch(target, requestId='redirect', redirectedRequestId='first-fetch'))
        worker.frame_navigated(target, {'id': target.main_frame, 'url': 'https://public.test/'})
        worker.fetch_paused(target, popup_fetch(target, requestId='next'))
    assert decide.call_count == 3


def test_initial_route_waits_for_the_fetch_acknowledgement_only(worker):
    worker.owner_visible = worker.guard_off = True
    """#34 (observed): while the popup's first request is held its renderer answers nothing, so the barrier is
    the browser-side Fetch interception (later hops checked), not the renderer's script acknowledgements."""
    worker.engine.browser.hold_method = 'Fetch.enable'
    worker.engine.browser.held_responses = []
    popup = worker.context.new_page(reported=False)
    target = worker.targets[popup.ident]
    assert not target.fetch_ready
    route = FakeRoute()
    with mock.patch.object(worker, 'decide', side_effect=lambda url, counted, done: done(True)) as decide:
        worker.route_popup(route, UnreportedPopupRequest(popup, 'https://public.test/'))
        assert route.outcome is None and not decide.called
        for response in worker.engine.browser.held_responses:
            response()
        assert target.fetch_ready
        # Run the scheduled readiness retry without sleeping on a real clock.
        _, _, retry, args = min(worker.AppHelper.items)
        retry(*args)
    assert route.outcome == 'continue'


def test_ambiguous_unreported_popups_fail_closed(worker, monkeypatch):
    first, first_target = unreported_popup(worker)
    second, second_target = unreported_popup(worker)
    monkeypatch.setattr(shared, 'WARM_SECONDS', 0)
    route = FakeRoute()
    with mock.patch.object(worker, 'decide') as decide:
        worker.route_popup(route, UnreportedPopupRequest(first, 'https://public.test/'))
    assert route.outcome == 'abort:aborted' and not decide.called
    assert first.is_closed() and second.is_closed()
    assert worker.blocked == 0


@pytest.mark.parametrize('fetch_first', [False, True])
def test_owner_popup_post_both_interceptors_do_not_replay(worker, fetch_first):
    popup, target = unreported_popup(worker)
    route = FakeRoute()
    request = UnreportedPopupRequest(popup, 'https://public.test/pay')
    request.method = 'POST'
    event = popup_fetch(target, request.url)
    event['request']['method'] = 'POST'
    worker.note_vetted({'vetted': {'method': 'post', 'action': request.url, 'payment': True}})
    with mock.patch.object(worker, 'decide', side_effect=lambda url, counted, done: done(True)) as decide:
        actions = [lambda: worker.route_popup(route, request), lambda: worker.fetch_paused(target, event)]
        for action in reversed(actions) if fetch_first else actions:
            action()
    assert decide.call_count == 1 and worker.posted_submits == 0
    assert route.outcome == 'continue'
    assert packets(worker, 'Fetch.continueRequest')[-1]['requestId'] == 'first-fetch'
    assert worker.take_vetted('POST', request.url)
    assert not packets(worker, 'Page.navigate')


def test_cancelled_initial_navigation_does_not_authorize_a_replacement(worker):
    popup, target = unreported_popup(worker)
    route = FakeRoute()
    with mock.patch.object(worker, 'decide', side_effect=lambda url, counted, done: done(True)):
        worker.route_popup(route, UnreportedPopupRequest(popup, 'https://public.test/'))
    worker.network_failed(target, {'requestId': 'original', 'type': 'Document', 'canceled': True})
    with mock.patch.object(worker, 'decide', side_effect=lambda url, counted, done: done(False, 'blocked_destination')) as decide:
        worker.fetch_paused(target, popup_fetch(target, requestId='replacement'))
    decide.assert_called_once()
    assert packets(worker, 'Fetch.failRequest')[-1]['requestId'] == 'replacement'
    assert popup.is_closed()


@pytest.mark.parametrize('replacement', ['commit', 'context_destroyed', 'contexts_cleared'])
def test_document_replacement_discards_all_form_passes(worker, replacement):
    target = worker.main_target
    target.contexts[target.main_frame] = 101
    for payment in (False, True):
        worker.note_vetted({'vetted': {'method': 'post', 'action': 'https://shop.test/pay', 'payment': payment}})
    if replacement == 'commit':
        target.receive({'method': 'Page.frameNavigated', 'params': {
            'frame': {'id': target.main_frame, 'url': 'https://shop.test/pay', 'loaderId': 'document-b'}}})
    elif replacement == 'context_destroyed':
        target.receive({'method': 'Runtime.executionContextDestroyed', 'params': {'executionContextId': 101}})
    elif replacement == 'contexts_cleared':
        target.receive({'method': 'Runtime.executionContextsCleared'})
    assert worker.vetted == []
    assert not worker.take_vetted('POST', 'https://shop.test/pay')
    # A queued notification from the destroyed document cannot replenish the cache.
    worker.binding_called(target, {'name': chromium.BINDING, 'executionContextId': 101,
                                  'payload': json.dumps({'vetted': {'method': 'post',
                                      'action': 'https://shop.test/pay', 'payment': False}})})
    assert not worker.take_vetted('POST', 'https://shop.test/pay')


def test_document_a_pass_cannot_release_document_b_payment_from_a_fresh_iframe(worker, monkeypatch):
    monkeypatch.setattr(chromium, 'VETTED_WAIT_SECONDS', 0)
    target = worker.main_target
    worker.note_vetted({'vetted': {'method': 'post', 'action': 'https://shop.test/pay', 'payment': False}})
    with mock.patch.object(worker, 'decide', side_effect=lambda url, main, done: done(True)):
        worker.fetch_paused(target, {'requestId': 'a-post', 'frameId': target.main_frame,
                                    'request': {'url': 'https://shop.test/pay', 'method': 'POST'}})
        target.receive({'method': 'Page.frameNavigated', 'params': {
            'frame': {'id': target.main_frame, 'url': 'https://shop.test/pay', 'loaderId': 'b'}}})
        worker.engine.browser.script_results.append({'cancelled': {'payment': True}})
        # Native submit borrowed from a new iframe bypasses the main document's wrappers/events.
        worker.fetch_paused(target, {'requestId': 'b-payment', 'frameId': target.main_frame,
                                    'request': {'url': 'https://shop.test/pay', 'method': 'POST'}})
    assert packets(worker, 'Fetch.continueRequest') == [{'requestId': 'a-post'}]
    assert packets(worker, 'Fetch.failRequest') == [{'requestId': 'b-payment', 'errorReason': 'Aborted'}]


def test_ordinary_pass_reusable_only_within_its_document_and_time_window(worker):
    with mock.patch.object(chromium.time, 'monotonic', return_value=100) as now:
        worker.note_vetted({'vetted': {'method': 'post', 'action': 'https://shop.test/pay', 'payment': False}})
        assert worker.take_vetted('POST', 'https://shop.test/pay')
        now.return_value = 100 + chromium.ORDINARY_VETTED_SECONDS
        assert worker.take_vetted('POST', 'https://shop.test/pay?again=1')
        now.return_value += .001
        assert not worker.take_vetted('POST', 'https://shop.test/pay')


def test_an_ordinary_pass_withdrawal_mirrors_a_shim_list_reset(worker):
    entry = {'method': 'post', 'action': 'https://shop.test/pay', 'payment': False}
    worker.note_vetted({'vetted': entry})
    worker.note_vetted({'unvetted': entry})
    assert not worker.take_vetted('POST', entry['action'])


def test_waiting_form_cannot_take_a_pass_from_a_replacement_document(worker):
    callbacks, answers = [], []
    with mock.patch.object(worker.AppHelper, 'callLater', side_effect=lambda delay, fn: callbacks.append(fn)):
        worker.check_form_navigation('https://shop.test/pay', 'POST', answers.append)
    worker.clear_vetted()
    worker.note_vetted({'vetted': {'method': 'post', 'action': 'https://shop.test/pay', 'payment': True}})
    callbacks[0]()
    assert answers == [False] and worker.deciding == 0
    assert worker.take_vetted('POST', 'https://shop.test/pay')


def test_form_policy_result_cannot_outlive_its_document(worker):
    target = worker.main_target
    worker.note_vetted({'vetted': {'method': 'post', 'action': 'https://shop.test/pay', 'payment': False}})
    decisions = []
    with mock.patch.object(worker, 'decide', side_effect=lambda url, main, done: decisions.append(done)):
        worker.request_paused(target, {'requestId': 'late', 'frameId': target.main_frame,
                                      'request': {'url': 'https://shop.test/pay', 'method': 'POST'}})
    worker.clear_vetted()
    decisions[0](True)
    assert not packets(worker, 'Fetch.continueRequest')
    assert packets(worker, 'Fetch.failRequest') == [{'requestId': 'late', 'errorReason': 'Aborted'}]


@pytest.mark.parametrize('reading', [False, True])
@pytest.mark.parametrize('url', ['blob:https://public.test/id', 'data:text/html,hello', 'file:///tmp/page.html'])
def test_non_network_main_frame_navigation_is_stopped_and_reported(worker, reading, url):
    target = worker.main_target
    worker.reading = reading
    old_url = worker.page.url
    with mock.patch.object(worker, 'decide') as decide:
        target.receive({'method': 'Page.frameRequestedNavigation', 'params': {
            'frameId': target.main_frame, 'url': url, 'reason': 'scriptInitiated'}})
    assert packets(worker, 'Page.stopLoading') == [{}]
    assert worker.blocked == 1 and worker.blocked_reason == 'unsupported_navigation'
    assert worker.read_navigations == int(reading)
    assert worker.page.url == old_url
    assert not packets(worker, 'Page.navigateToHistoryEntry')
    assert not decide.called


@pytest.mark.parametrize('reading', [False, True])
@pytest.mark.parametrize('requested', [False, True])
def test_unsupported_commit_restores_history_even_during_a_read(worker, reading, requested):
    target = worker.main_target
    worker.reading = reading
    url = 'blob:https://public.test/id'
    worker.engine.browser.navigation_history = {'currentIndex': 1, 'entries': [
        {'id': 10, 'url': 'https://public.test/previous'}, {'id': 11, 'url': url}]}
    if requested:
        target.receive({'method': 'Page.frameRequestedNavigation', 'params': {
            'frameId': target.main_frame, 'url': url, 'reason': 'scriptInitiated'}})
    target.receive({'method': 'Page.frameNavigated', 'params': {
        'frame': {'id': target.main_frame, 'url': url, 'loaderId': 'blob-document'}}})
    assert worker.blocked == 1 and worker.blocked_reason == 'unsupported_navigation'
    assert worker.read_navigations == int(reading)
    assert packets(worker, 'Page.navigateToHistoryEntry') == [{'entryId': 10}]
    # Returning to the held page must not itself be blocked by the read hold.
    with mock.patch.object(worker, 'decide', side_effect=lambda url, main, done: done(True)):
        worker.fetch_paused(target, {'requestId': 'restore', 'frameId': target.main_frame,
                                    'request': {'url': 'https://public.test/previous', 'method': 'GET'}})
    assert packets(worker, 'Fetch.continueRequest') == [{'requestId': 'restore'}]
    assert worker.read_navigations == int(reading) and worker.main_navigations == 0
    target.receive({'method': 'Page.frameNavigated', 'params': {
        'frame': {'id': target.main_frame, 'url': 'https://public.test/previous'}}})
    assert target.restoring_history is None


@pytest.mark.parametrize('reading', [False, True])
def test_subframe_blob_navigation_does_not_change_policy_or_main_document_passes(worker, reading):
    target = worker.main_target
    worker.reading = reading
    worker.note_vetted({'vetted': {'method': 'post', 'action': 'https://shop.test/pay', 'payment': False}})
    target.contexts['child'] = 202
    target.receive({'method': 'Runtime.executionContextDestroyed', 'params': {'executionContextId': 202}})
    target.receive({'method': 'Page.frameRequestedNavigation', 'params': {
        'frameId': 'child', 'url': 'blob:https://public.test/id', 'reason': 'scriptInitiated'}})
    target.receive({'method': 'Page.frameNavigated', 'params': {
        'frame': {'id': 'child', 'parentId': target.main_frame, 'url': 'blob:https://public.test/id'}}})
    assert worker.take_vetted('POST', 'https://shop.test/pay')
    assert not packets(worker, 'Page.stopLoading') and not packets(worker, 'Page.getNavigationHistory')
    assert worker.blocked == worker.read_navigations == 0


@pytest.mark.parametrize('url', ['https://public.test/', 'http://public.test/', 'about:blank'])
def test_supported_main_frame_requests_keep_existing_navigation_path(worker, url):
    target = worker.main_target
    target.receive({'method': 'Page.frameRequestedNavigation', 'params': {
        'frameId': target.main_frame, 'url': url, 'reason': 'scriptInitiated'}})
    assert not packets(worker, 'Page.stopLoading') and worker.blocked == 0
    assert worker.frame_requests[(target.ident, target.main_frame)]['url'] == url



def test_vetted_shim_mirrors_ordinary_resets_and_same_destination_replacements(worker):
    import shutil
    import subprocess

    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to execute the isolated-world shim')
    source = (
        'const notices = []; const window = {__agentos: {vetted: []}};'
        'const notice = text => notices.push(JSON.parse(text));'
        + chromium.VETTED_SHIM % {'binding': 'notice'} +
        "const first = {method: 'post', action: 'https://shop.test/pay', payment: false};"
        'window.__agentos.vetted = [first];'
        'window.__agentos.vetted = [{...first}];'
        'window.__agentos.vetted = [];'
        'console.log(JSON.stringify(notices));'
    )
    notices = json.loads(subprocess.run([node, '-e', source], check=True, capture_output=True,
                                        text=True, timeout=10).stdout)
    assert [next(iter(record)) for record in notices] == ['vetted', 'unvetted', 'vetted', 'unvetted']
    for record in notices[:3]:
        worker.note_vetted(record)
    assert worker.take_vetted('POST', 'https://shop.test/pay')
    worker.note_vetted(notices[3])
    assert not worker.take_vetted('POST', 'https://shop.test/pay')



def test_blank_navigation_is_also_held_during_a_read(worker):
    worker.reading = True
    target = worker.main_target
    target.receive({'method': 'Page.frameRequestedNavigation', 'params': {
        'frameId': target.main_frame, 'url': 'about:blank', 'reason': 'scriptInitiated'}})
    assert packets(worker, 'Page.stopLoading') == [{}]
    assert worker.read_navigations == 1 and worker.blocked == 0


def test_chromiums_own_error_page_is_not_an_unsupported_navigation():
    assert chromium.Worker.supported_document('chrome-error://chromewebdata/')
    for url in ('blob:https://public.test/1', 'data:text/html,x', 'file:///etc/passwd', 'javascript:1'):
        assert not chromium.Worker.supported_document(url), url


def window_open(worker, url, gesture=False):
    worker.main_target.receive({'method': 'Page.windowOpen', 'params': {
        'url': url, 'userGesture': gesture, 'windowName': '', 'windowFeatures': []}})


@pytest.mark.parametrize('allowed', [True, False])
@pytest.mark.parametrize('gesture', [True, False])
def test_ai_window_open_closes_target_and_uses_main_fetch_policy(worker, allowed, gesture):
    main, page = worker.main_target, worker.page
    url = 'https://public.test/' if allowed else 'http://127.0.0.1/'
    window_open(worker, url, gesture=gesture)
    popup = worker.context.new_page()
    worker.AppHelper.pump()
    assert popup.is_closed() and worker.page is page and not page.is_closed()
    assert popup.ident not in worker.targets
    assert not any(params.get('targetId') == popup.ident for method, params in worker.root_cdp.calls
                   if method == 'Target.attachToTarget')
    assert packets(worker, 'Page.navigate') == [{'url': url, 'transitionType': 'link' if gesture else 'other'}]
    with mock.patch.object(worker, 'decide', side_effect=lambda url, main, done:
                           done(allowed, None if allowed else 'blocked_destination')) as decide:
        worker.fetch_paused(main, popup_fetch(main, url))
    decide.assert_called_once_with(url, True, mock.ANY)
    assert worker.main_navigations == int(allowed)
    assert worker.blocked == int(not allowed)
    assert worker.deciding == 0
    assert packets(worker, 'Fetch.continueRequest' if allowed else 'Fetch.failRequest')[-1]['requestId'] == 'first-fetch'
    if allowed:
        worker.frame_navigated(main, {'id': main.main_frame, 'url': url})
        assert worker.landed == 1
    else:
        assert worker.blocked_reason == 'blocked_destination'
        worker.pending[8] = True
        worker.finish_input(8, blocked_before=0)
        assert worker.messages[-1]['error'] == 'blocked_destination'


@pytest.mark.parametrize('url', ['about:blank', 'blob:https://public.test/id', 'data:text/html,hello'])
@pytest.mark.parametrize('visible,guard_off', [(False, False), (True, False), (False, True)])
def test_ai_non_http_window_open_leaves_no_popup_or_navigation(worker, url, visible, guard_off):
    worker.owner_visible, worker.guard_off = visible, guard_off
    window_open(worker, url)
    popup = worker.context.new_page(reported=False)
    assert popup.is_closed()
    assert not packets(worker, 'Page.navigate')
    assert not packets(worker, 'Fetch.continueRequest')


@pytest.mark.parametrize('method', ['POST', 'GET'])
def test_ai_popup_first_route_never_continues_even_with_opener_pass(worker, method):
    popup = worker.context.new_page(reported=False)
    route = FakeRoute()
    request = UnreportedPopupRequest(popup, 'https://public.test/pay')
    request.method = method
    worker.note_vetted({'vetted': {'method': 'post', 'action': request.url, 'payment': False}})
    with mock.patch.object(worker, 'decide') as decide:
        worker.route_popup(route, request)
    assert route.outcome == 'abort:aborted' and popup.is_closed()
    assert not decide.called and not packets(worker, 'Fetch.continueRequest')
    assert worker.take_vetted('POST', request.url), 'the popup never borrows a pass'
    assert worker.refused_submits == int(method == 'POST')


def test_post_new_window_notification_after_window_open_is_refused_before_replay(worker):
    browser, target = worker.engine.browser, worker.main_target
    browser.hold_method, browser.held_responses = 'Runtime.evaluate', []
    window_open(worker, 'https://public.test/submit', gesture=True)
    assert worker.deciding == 1 and not packets(worker, 'Page.navigate')
    # Chromium's form creates its target first, then reports method and target
    # frame on the opener. The disposition can be currentTab in the new frame.
    target.receive({'method': 'Page.frameRequestedNavigation', 'params': {
        'frameId': 'new-window-frame', 'url': 'https://public.test/submit',
        'reason': 'formSubmissionPost', 'disposition': 'currentTab'}})
    popup = worker.context.new_page(reported=False)
    for response in browser.held_responses:
        response()
    assert popup.is_closed() and worker.refused_submits == 1
    assert worker.deciding == 0
    assert not packets(worker, 'Page.navigate') and not packets(worker, 'Fetch.continueRequest')
    worker.pending[8] = True
    worker.finish_input(8, blocked_before=0)
    assert worker.messages[-1] == {'id': 8, 'ok': False, 'error': 'submit_refused'}


def test_window_open_renderer_barrier_is_bounded_and_late_answer_cannot_replay(worker, monkeypatch):
    browser = worker.engine.browser
    browser.hold_method, browser.held_responses = 'Runtime.evaluate', []
    monkeypatch.setattr(shared, 'WARM_SECONDS', 0)
    window_open(worker, 'https://public.test/')
    worker.AppHelper.pump()
    assert worker.deciding == 0 and worker.blocked_reason == 'navigation_failed'
    for response in browser.held_responses:
        response()
    assert not packets(worker, 'Page.navigate')


@pytest.mark.parametrize('commit', [False, True])
@pytest.mark.parametrize('url', ['blob:https://public.test/id', 'data:text/html,bad'])
def test_owner_popup_unsupported_main_document_is_closed(worker, commit, url):
    popup, target = unreported_popup(worker)
    if commit:
        worker.frame_navigated(target, {'id': target.main_frame, 'url': url})
    else:
        worker.frame_requested(target, {'frameId': target.main_frame, 'url': url})
    assert popup.is_closed() and not worker.page.is_closed()
    assert not packets(worker, 'Page.navigateToHistoryEntry')


def test_unsupported_history_recovery_is_bounded_and_blank_parking_is_not_a_loop(worker):
    target = worker.main_target
    worker.reading = True
    worker.engine.browser.navigation_history = {'currentIndex': 1, 'entries': [
        {'id': 10, 'url': 'https://public.test/previous'}, {'id': 11, 'url': 'blob:bad'}]}
    for _ in range(3):
        worker.frame_navigated(target, {'id': target.main_frame, 'url': 'blob:bad'})
        worker.frame_navigated(target, {'id': target.main_frame,
                                      'url': getattr(target, 'restoring_history', 'about:blank')})
    assert packets(worker, 'Page.navigateToHistoryEntry') == [{'entryId': 10}]
    assert packets(worker, 'Page.navigate') == [{'url': 'about:blank'}, {'url': 'about:blank'}]
    assert worker.blocked == 3 and worker.blocked_reason == 'unsupported_navigation'
    assert worker.read_navigations == 3


def test_frame_waits_for_paint_in_recreated_world_before_capture_and_keeps_phone_zoom(worker):
    target, browser = worker.main_target, worker.engine.browser
    worker.owner_visible = worker.guard_off = True
    command(worker, 'remote_begin')
    target.contexts[target.main_frame] = 999
    worker.frame_navigated(target, {'id': target.main_frame, 'url': 'https://public.test/desktop-only'})
    assert target.main_frame not in target.contexts
    worker.set_page_zoom(.39)
    browser.hold_method, browser.held_responses = 'Runtime.callFunctionOn', []
    before = len(packets(worker, 'Page.captureScreenshot'))
    worker.handle({'id': 8, 'op': 'frame', 'timeout': 10})
    assert len(packets(worker, 'Page.captureScreenshot')) == before
    assert packets(worker, 'Page.createIsolatedWorld')[-1]['frameId'] == target.main_frame
    assert 'requestAnimationFrame' in packets(worker, 'Runtime.callFunctionOn')[-1]['functionDeclaration']
    browser.hold_method = None
    browser.script_results.extend([{'focus': 'text', 'fields': []}, {'page': 1000, 'inner': 1000}])
    for response in browser.held_responses:
        response()
    answer = worker.messages[-1]
    assert answer['id'] == 8 and answer['ok'] and answer['focus'] == 'text'
    assert answer['width'] == shared.MOBILE_SIZE[0]
    clip = packets(worker, 'Page.captureScreenshot')[-1]['clip']
    assert clip['width'] == pytest.approx(1000)
    assert clip['height'] == pytest.approx(shared.MOBILE_SIZE[1] / .39)
    assert target.contexts[target.main_frame] == 101


def test_owner_popup_pending_decision_cannot_continue_after_guard_is_armed(worker):
    popup, target = unreported_popup(worker)
    callbacks = []
    route = FakeRoute()
    with mock.patch.object(worker, 'decide', side_effect=lambda url, counted, done: callbacks.append(done)):
        worker.route_popup(route, UnreportedPopupRequest(popup, 'https://public.test/'))
    assert route.outcome is None
    worker.guard_off = False
    callbacks[0](True)
    assert route.outcome == 'abort:aborted' and popup.is_closed()
    assert not packets(worker, 'Fetch.continueRequest')
    assert worker.deciding == 0


def test_owner_popup_fetch_post_preserves_original_body_and_redirect_policy(worker):
    popup, target = unreported_popup(worker)
    decisions = []

    def decide(url, counted, done):
        decisions.append((url, counted))
        done(len(decisions) == 1, 'blocked_destination')

    with mock.patch.object(worker, 'decide', side_effect=decide):
        worker.fetch_paused(target, {**popup_fetch(target), 'request': {
            'url': 'https://public.test/', 'method': 'POST', 'postData': 'original=body'}})
        worker.fetch_paused(target, popup_fetch(target, 'http://127.0.0.1/',
                                              requestId='redirect', redirectedRequestId='first-fetch'))
    assert decisions == [('https://public.test/', False), ('http://127.0.0.1/', False)]
    assert packets(worker, 'Fetch.continueRequest')[-1] == {'requestId': 'first-fetch'}
    assert packets(worker, 'Fetch.failRequest')[-1]['requestId'] == 'redirect'
    assert popup.is_closed() and not packets(worker, 'Page.navigate')


def test_popup_child_request_after_guard_is_armed_is_never_continued(worker):
    popup, target = unreported_popup(worker)
    worker.guard_off = False
    route = FakeRoute()
    worker.route_popup(route, FakeRequest(popup, 'https://public.test/child', child=True))
    assert route.outcome == 'abort:aborted'
    # A related OOPIF arriving after the mode change is closed without resuming.
    worker.attached(target, {'sessionId': 'popup-child', 'targetInfo': {
        'type': 'iframe', 'targetId': 'popup-child'}})
    assert popup.is_closed()
    assert not any(session == 'popup-child' for session, _ in worker.engine.browser.packets)


def test_a_same_document_navigation_answers_without_a_load_event(worker):
    """Codex review on #640: a fragment-only navigation has no loaderId and fires no load event."""
    worker.engine.browser.navigate_result = {'frameId': 'f'}
    worker.handle({'id': 41, 'op': 'navigate', 'timeout': 10, 'url': 'https://public.test/page#part'})
    answer = next((m for m in worker.messages if m.get('id') == 41), None)
    assert answer is not None and answer.get('ok') is True, worker.messages


def test_a_bare_wayland_session_is_no_display_for_the_x11_worker():
    """Codex review on #640: the worker parks its window with --ozone-platform=x11."""
    output = io.StringIO()
    with mock.patch.object(chromium.sys, 'platform', 'linux'), \
         mock.patch.object(chromium.sys, 'stdout', output), \
         mock.patch.dict(chromium.os.environ, {'WAYLAND_DISPLAY': 'wayland-0'}, clear=True), \
         mock.patch.object(chromium, '_playwright_factory', return_value=mock.MagicMock()):
        assert chromium.main(['--chromium', '/pinned/chrome']) == 2
    assert json.loads(output.getvalue()) == {'event': 'unavailable', 'error': 'no_display'}


def test_a_failed_sub_resource_is_not_a_failed_navigation(worker):
    """Codex review on #640: an image or script of the main frame failing never fails the navigation."""
    target = worker.main_target
    worker.navigation = (9, None)
    worker.pending[9] = True
    for kind in ('Image', 'Script', 'Stylesheet'):
        target.receive({'method': 'Network.requestWillBeSent',
                        'params': {'requestId': kind, 'frameId': target.main_frame, 'type': kind}})
        target.receive({'method': 'Network.loadingFailed', 'params': {'requestId': kind, 'errorText': 'net::ERR_FAILED'}})
    assert worker.navigation == (9, None) and 9 in worker.pending
    target.receive({'method': 'Network.requestWillBeSent',
                    'params': {'requestId': 'doc', 'frameId': target.main_frame, 'type': 'Document'}})
    assert 'doc' in worker.main_requests


@pytest.mark.parametrize('layout', ['mobile', 'desktop'])
def test_mouse_only_emulation_keeps_layout_and_ua(worker, layout):
    worker.layout = layout
    worker.emulate(worker.main_target)
    assert packets(worker, 'Emulation.setTouchEmulationEnabled')[-1] == {'enabled': False}
    assert packets(worker, 'Emulation.setDeviceMetricsOverride')[-1]['mobile'] is False
    assert packets(worker, 'Emulation.setUserAgentOverride')[-1]['userAgent'] == chromium.user_agent_for(
        layout, worker.version)
    worker.press(10, 20)
    assert len(packets(worker, 'Input.dispatchMouseEvent')) == 3
    assert not packets(worker, 'Input.dispatchTouchEvent')


def test_parking_releases_native_focus_after_moving_without_minimizing(worker):
    calls = []
    with mock.patch.object(worker, 'bounds', side_effect=lambda **kw: calls.append(kw)), \
         mock.patch.object(chromium, 'relinquish_keyboard_focus', side_effect=lambda: calls.append('blur')):
        worker.park()
    assert calls == [{'left': shared.PARK_ORIGIN, 'top': shared.PARK_ORIGIN, 'windowState': 'normal'}, 'blur']


@pytest.mark.parametrize('kind', ['iframe', 'page'])
@pytest.mark.parametrize('detach', [True, False])
def test_target_initialization_teardown_is_not_guard_failure(worker, kind, detach):
    target = chromium._Target(worker, worker.root_cdp, 'initializing',
                              {'targetId': 'initializing', 'type': kind})
    worker.sessions[target.session_id] = target
    worker.targets[target.ident] = target
    worker.engine.browser.hold_method = 'Page.addScriptToEvaluateOnNewDocument'
    worker.engine.browser.held_responses = []
    worker.install_scripts(target)
    assert target.callbacks and not target.ready
    ident = next(iter(target.callbacks))
    if detach:
        worker.detached({'sessionId': target.session_id})
        assert target.disposed and not target.callbacks and not target.ready
        # A late guard reply cannot revive the target or kill the worker.
        target.receive({'id': ident, 'error': {'code': -1}})
        assert not worker.fatal
    else:
        target.receive({'id': ident, 'error': {'code': -1}})
        assert worker.fatal and not target.disposed
        assert ('Target.closeTarget', {'targetId': target.ident}) in worker.root_cdp.calls


def navigation_load(target, loader, url='https://new.test/'):
    target.receive({'method': 'Page.frameNavigated', 'params': {'frame': {
        'id': target.main_frame, 'loaderId': loader, 'url': url}}})
    target.receive({'method': 'Page.lifecycleEvent', 'params': {
        'name': 'load', 'frameId': target.main_frame, 'loaderId': loader}})


@pytest.mark.parametrize('defer_response', [False, True])
def test_old_document_load_cannot_answer_new_navigation(worker, defer_response):
    browser, target = worker.engine.browser, worker.main_target
    browser.navigate_result = {'frameId': target.main_frame, 'loaderId': 'new'}
    browser.held_responses = []
    if defer_response:
        browser.hold_method = 'Page.navigate'
    worker.handle({'id': 41, 'op': 'navigate', 'timeout': 10, 'url': 'https://new.test/'})
    worker.page.url = 'https://old.test/'
    navigation_load(target, 'old', worker.page.url)
    target.receive({'method': 'Page.loadEventFired', 'params': {'timestamp': 1}})
    assert worker.messages == [] and 41 in worker.pending
    # Subframe lifecycle events also cannot complete it.
    target.receive({'method': 'Page.lifecycleEvent', 'params': {
        'name': 'load', 'frameId': 'child', 'loaderId': 'new'}})
    assert worker.messages == []
    navigation_load(target, 'new', 'https://redirect.test/final')
    for respond in browser.held_responses:
        respond()
    assert worker.messages == [{'id': 41, 'ok': True, 'url': 'https://redirect.test/final', 'layout': 'mobile'}]


@pytest.mark.parametrize('defer_response', [False, True])
def test_old_document_failure_cannot_fail_new_navigation(worker, defer_response):
    browser, target = worker.engine.browser, worker.main_target
    browser.navigate_result = {'frameId': target.main_frame, 'loaderId': 'new'}
    browser.held_responses = []
    if defer_response:
        browser.hold_method = 'Page.navigate'
    worker.handle({'id': 41, 'op': 'navigate', 'timeout': 10, 'url': 'https://new.test/'})
    for loader in ['old', 'new']:
        target.receive({'method': 'Network.requestWillBeSent', 'params': {
            'requestId': loader + '-request', 'frameId': target.main_frame, 'type': 'Document', 'loaderId': loader}})
        target.receive({'method': 'Network.loadingFailed', 'params': {
            'requestId': loader + '-request', 'errorText': 'net::ERR_NAME_NOT_RESOLVED', 'type': 'Document'}})
        if loader == 'old':
            assert worker.messages == [] and 41 in worker.pending
    for respond in browser.held_responses:
        respond()
    assert worker.messages == [{'id': 41, 'ok': False, 'error': 'host_not_found'}]


def test_matching_load_waits_for_matching_committed_document(worker):
    target = worker.main_target
    worker.engine.browser.navigate_result = {'loaderId': 'new'}
    worker.handle({'id': 41, 'op': 'navigate', 'timeout': 10, 'url': 'https://new.test/'})
    target.receive({'method': 'Page.lifecycleEvent', 'params': {
        'name': 'load', 'frameId': target.main_frame, 'loaderId': 'new'}})
    assert worker.messages == []
    target.receive({'method': 'Page.frameNavigated', 'params': {'frame': {
        'id': target.main_frame, 'loaderId': 'old', 'url': 'https://old.test/'}}})
    assert worker.messages == []
    target.receive({'method': 'Page.frameNavigated', 'params': {'frame': {
        'id': target.main_frame, 'loaderId': 'new', 'url': 'https://new.test/'}}})
    assert worker.messages[-1] == {'id': 41, 'ok': True, 'url': 'https://new.test/', 'layout': 'mobile'}


class FakeX11:
    """libX11 through ctypes: one focused window, its class, and its parent chain."""

    def __init__(self, focused, classes, parents, display=1):
        self.focused, self.classes, self.parents, self.display = focused, classes, parents, display
        self.set_focus, self.closed, self.freed = [], 0, 0
        self.handler = chromium._XErrorHandler(lambda display, event: 0)
        self.original_handler = self.handler
        for name in ('XOpenDisplay', 'XGetInputFocus', 'XGetClassHint', 'XQueryTree', 'XFree', 'XSetInputFocus',
                     'XSync', 'XSetErrorHandler', 'XCloseDisplay'):
            setattr(self, name, mock.MagicMock(side_effect=getattr(self, '_' + name)))
        self._strings = []

    def _XOpenDisplay(self, name):
        return self.display

    def _XGetInputFocus(self, display, window, revert):
        window._obj.value = self.focused

    def _XGetClassHint(self, display, window, hint):
        names = self.classes.get(window)
        if not names:
            return 0
        buffers = [ctypes.create_string_buffer(n.encode()) for n in names]
        self._strings += buffers
        hint._obj.res_name, hint._obj.res_class = (ctypes.addressof(b) for b in buffers)
        return 1

    def _XQueryTree(self, display, window, root, parent, children, count):
        root._obj.value, parent._obj.value, children._obj.value = 100, self.parents.get(window, 100), None
        return 1

    def _XFree(self, pointer):
        self.freed += 1

    def _XSetInputFocus(self, display, window, revert, time_):
        self.set_focus.append((window, revert, time_))

    def _XSync(self, display, discard):
        pass

    def _XSetErrorHandler(self, handler):
        previous, self.handler = self.handler, handler
        return previous

    def _XCloseDisplay(self, display):
        self.closed += 1


def test_focus_is_released_only_from_stills_own_parked_window(monkeypatch):
    """Codex review on #640: a parked window must not keep the desktop keyboard, and the owner's focus
    elsewhere is never taken."""
    ours = FakeX11(focused=7, classes={5: ('stillbrowser', chromium.WINDOW_CLASS)}, parents={7: 5})
    monkeypatch.setattr(chromium.ctypes, 'CDLL', lambda name: ours)
    assert _native_relinquish_keyboard_focus() is True
    assert ours.set_focus == [(1, 1, 0)] and ours.closed == 1
    theirs = FakeX11(focused=9, classes={9: ('gnome-terminal', 'Gnome-terminal')}, parents={})
    monkeypatch.setattr(chromium.ctypes, 'CDLL', lambda name: theirs)
    assert _native_relinquish_keyboard_focus() is False
    assert theirs.set_focus == [] and theirs.closed == 1


def test_focus_release_is_best_effort(monkeypatch):
    monkeypatch.setattr(chromium.ctypes, 'CDLL', mock.Mock(side_effect=OSError('no libX11')))
    assert _native_relinquish_keyboard_focus() is False
    closed = FakeX11(focused=7, classes={}, parents={}, display=None)
    monkeypatch.setattr(chromium.ctypes, 'CDLL', lambda name: closed)
    assert _native_relinquish_keyboard_focus() is False


def test_native_x_error_during_class_lookup_is_contained(monkeypatch):
    x11 = FakeX11(focused=7, classes={7: ('stillbrowser', chromium.WINDOW_CLASS)}, parents={})
    monkeypatch.setattr(chromium.ctypes, 'CDLL', lambda name: x11)

    def bad_window(display, window, hint):
        assert isinstance(x11.handler, chromium._XErrorHandler)
        assert x11.handler(display, ctypes.pointer(chromium._XErrorEvent(error_code=3))) == 0
        return 0

    x11.XGetClassHint.side_effect = bad_window
    assert _native_relinquish_keyboard_focus() is False
    assert x11.set_focus == [] and x11.closed == 1
    assert x11.handler is x11.original_handler
    assert x11.XSetErrorHandler.argtypes == [chromium._XErrorHandler]
    assert x11.XSetErrorHandler.restype is chromium._XErrorHandler
    x11.XSync.assert_called_with(x11.display, False)
    assert x11.XSetErrorHandler.call_args_list[-1] == mock.call(x11.original_handler)


def test_asynchronous_focus_error_is_delivered_before_handler_restoration(monkeypatch):
    x11 = FakeX11(focused=7, classes={7: ('stillbrowser', chromium.WINDOW_CLASS)}, parents={})
    monkeypatch.setattr(chromium.ctypes, 'CDLL', lambda name: x11)

    def sync(display, discard):
        if x11.set_focus:
            assert x11.handler is chromium._focus_error_handler
            x11.handler(display, ctypes.pointer(chromium._XErrorEvent(error_code=3)))

    x11.XSync.side_effect = sync
    assert _native_relinquish_keyboard_focus() is False
    assert x11.handler is x11.original_handler and x11.closed == 1


@pytest.mark.parametrize('zoom', [1.0, .39])
def test_phone_frame_uses_scrolled_visual_viewport_and_taps_stay_viewport_relative(worker, zoom):
    worker.owner_visible = worker.guard_off = True
    command(worker, 'remote_begin')
    worker.set_page_zoom(zoom)
    worker.engine.browser.layout_metrics = {
        'cssVisualViewport': {'pageX': 23.5, 'pageY': 811.25, 'scale': zoom},
        'visualViewport': {'pageX': 9999, 'pageY': 9999}}
    command(worker, 'frame')
    clip = packets(worker, 'Page.captureScreenshot')[-1]['clip']
    assert clip['x'] == 23.5 and clip['y'] == 811.25
    assert clip['width'] == pytest.approx(shared.MOBILE_SIZE[0] / zoom)
    assert clip['height'] == pytest.approx(shared.MOBILE_SIZE[1] / zoom)
    assert clip['width'] * clip['scale'] == pytest.approx(min(shared.FRAME_MAX_WIDTH,
                                                            shared.MOBILE_SIZE[0] / zoom))
    worker.handle({'id': 8, 'op': 'remote_tap', 'x': 100, 'y': 200})
    mouse = packets(worker, 'Input.dispatchMouseEvent')[-3:]
    assert all(p['x'] == pytest.approx(100 / zoom) and p['y'] == pytest.approx(200 / zoom) for p in mouse)


@pytest.mark.parametrize('entry', ['request_paused', 'fetch_paused'])
@pytest.mark.parametrize('allowed', [False, True])
@pytest.mark.parametrize('initializing', [False, True])
def test_cancelled_fetch_settlement_does_not_fault_target(worker, entry, allowed, initializing):
    target, browser = worker.main_target, worker.engine.browser
    target.initializing = initializing
    method = 'Fetch.continueRequest' if allowed else 'Fetch.failRequest'
    browser.hold_method, browser.held_responses = method, []
    event = {'requestId': 'cancelled', 'frameId': target.main_frame, 'resourceType': 'Document',
             'request': {'url': 'https://public.test/', 'method': 'GET'}}
    with mock.patch.object(worker, 'decide', side_effect=lambda url, counted, done: done(allowed, 'blocked_destination')):
        getattr(worker, entry)(target, event)
    packet = next(p for _, p in reversed(browser.packets) if p['method'] == method)
    assert packet['id'] not in target.setup_pending
    target.receive({'id': packet['id'], 'error': {'code': -32602, 'message': 'Invalid InterceptionId'}})
    assert not worker.fatal and not worker.page.closed
    assert packet['id'] not in target.callbacks


@pytest.mark.parametrize('allowed', [False, True])
def test_disposed_request_settlement_is_best_effort(worker, allowed):
    worker.main_target.disposed = True
    worker.main_target.settle_request('disposed', allowed)
    assert not worker.fatal


@pytest.mark.parametrize('main_page', [False, True])
@pytest.mark.parametrize('navigation', [False, True])
def test_route_continuation_cancellation_does_not_close_page(worker, main_page, navigation):
    worker.owner_visible = worker.guard_off = True
    page = worker.page if main_page else worker.context.new_page()
    request = FakeRequest(page, 'https://public.test/', navigation=navigation, child=not main_page)
    route = mock.Mock()
    route.continue_.side_effect = RuntimeError('Target page, context or browser has been closed')
    worker.route_popup(route, request)
    route.continue_.assert_called_once_with()
    assert not page.closed and not worker.fatal and worker.blocked == 0


def test_navigation_timeout_clears_record_and_late_events_cannot_revive_it(worker):
    target = worker.main_target
    browser = worker.engine.browser
    browser.hold_method, browser.held_responses = 'Page.navigate', []
    with mock.patch.object(chromium.time, 'monotonic', return_value=100) as clock:
        worker.handle({'id': 41, 'op': 'navigate', 'timeout': 1, 'url': 'https://new.test/'})
        navigation_load(target, 'unrelated')
        assert worker.navigation_events == {'unrelated': 'load'}
        clock.return_value = 102
        worker.AppHelper.pump()
    assert worker.messages == [{'id': 41, 'ok': False, 'error': 'timeout'}]
    assert worker.navigation is None and worker.navigation_events == {} and 41 not in worker.pending
    assert packets(worker, 'Page.stopLoading')
    navigation_load(target, 'later')
    for respond in browser.held_responses:
        respond()
    assert worker.navigation is None and worker.navigation_events == {}
    assert worker.messages == [{'id': 41, 'ok': False, 'error': 'timeout'}]


def test_old_navigation_timeout_leaves_current_navigation_and_events_alone(worker):
    target = worker.main_target
    with mock.patch.object(chromium.time, 'monotonic', return_value=100) as clock:
        worker.handle({'id': 41, 'op': 'navigate', 'timeout': 1, 'url': 'https://old.test/'})
        worker.engine.browser.navigate_result = {'loaderId': 'replacement'}
        worker.handle({'id': 42, 'op': 'navigate', 'timeout': 10, 'url': 'https://new.test/'})
        navigation_load(target, 'unrelated')
        clock.return_value = 102
        worker.AppHelper.pump()
        assert worker.navigation == (42, 'replacement')
        assert worker.navigation_events == {'unrelated': 'load'}
        assert not packets(worker, 'Page.stopLoading')
        navigation_load(target, 'replacement')
    assert [row['id'] for row in worker.messages] == [41, 42]
    assert worker.messages[0]['error'] == 'timeout' and worker.messages[1]['ok']


def test_the_worker_windows_carry_stills_window_class():
    assert '--class=' + chromium.WINDOW_CLASS in chromium.LAUNCH_ARGS


def test_a_partitioned_cookie_keeps_its_partition_through_the_jar():
    """Codex review on #640: CHIPS cookies are never replayed outside their partition."""
    row = chromium.cookie_row({'name': 'sid', 'value': 'v', 'domain': 'widget.test', 'path': '/', 'expires': -1,
                               'secure': True, 'httpOnly': True, 'sameSite': 'None', 'partitionKey': 'https://shop.test'})
    assert row['partition_key'] == 'https://shop.test'
    assert chromium.cookie_object(row)['partitionKey'] == 'https://shop.test'
    plain = chromium.cookie_row({'name': 'a', 'value': 'b', 'domain': 'shop.test'})
    assert 'partition_key' not in plain and 'partitionKey' not in chromium.cookie_object(plain)
