"""BROWSE-07 (#939): sign in from the phone through a one-time link that drives the Mac's login window.

Evidence classes, named separately:

* unit (any platform): the worker's remote ops refuse ``not_shown`` while the
  window is parked (the ``Worker`` methods over a bare instance, no PyObjC);
  input validation; the session's code, binding and expiry rules;
* model-free local HTTP integration (fake login-window driver, fake ngrok
  process, temporary stores): the routes through a tunnel (forwarding
  headers), the cookie binding, 완료 saving the jar and closing, expiry
  closing, every other route refused through the tunnel, typed text reaching
  the driver and no log, and the Telegram button sending the link to the
  paired owner's chat only;
* unit (Node, fake DOM; skipped without Node): the phone page's keystrokes,
  composition, AutoFill and focus handling (#293);
* not here: the real WebKit snapshot, native press/key and ``insertText``
  paths (macOS + PyObjC, opt-in real-browser tests), and a live ngrok tunnel.
"""
import io
import json
import logging
import tempfile
import threading
import time
import unittest
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from personal_agent import browser_session as bs
from personal_agent import browser_worker
from personal_agent import remote_login
from personal_agent.browser_jar import JAR_NAME, CookieJar, MemoryKey
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart import make_handler
from personal_agent.quickstart_service import AgentService, BROWSER_LOGIN_PHONE_LABEL
from personal_agent.quickstart_store import QuickStore

CHAT, GENERATION = 42, 'gen-939'
SECRET_TEXT = 'hunter2-never-logged'


def wait_until(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


class WindowDriver:
    """A fake login-window driver: shows, answers frames, records remote inputs, exports a cookie once 'signed in'."""

    def __init__(self, log):
        self.log = log
        self.open = False
        self.sites = {}
        self.navigated = 0
        self.frames = 0
        self.remote = False
        #: A page dialog waiting for an answer, as the worker reports it in a frame.
        self.dialog = None
        #: #293: what kind of element has focus in the window, as the worker reports it in a frame.
        self.focus = None

    def alive(self):
        return True

    def remote_begin(self):
        if not self.open:
            raise bs.WorkerError('not_shown')
        self.remote = True
        self.log.append(('remote_begin',))

    def remote_end(self):
        self.remote = False
        self.dialog = None
        self.log.append(('remote_end',))

    def show(self, url, timeout):
        self.open = True
        self.log.append(('show', url))
        return url

    def is_open(self):
        return self.open

    def navigations(self):
        return self.navigated

    def frame(self):
        if not self.open:
            raise bs.WorkerError('not_shown')
        self.frames += 1
        return {'jpeg': 'AAAA', 'width': 1280.0, 'height': 900.0, 'dialog': self.dialog, 'focus': self.focus}

    def remote_input(self, kind, **fields):
        if not self.open:
            raise bs.WorkerError('not_shown')
        self.log.append(('remote_' + kind, fields))
        if kind == 'dialog':
            self.dialog = None
        if kind == 'key' and fields.get('key') == 'Enter':
            # The sign-in: the site now holds a session cookie.
            self.navigated += 1
            self.sites = {'fixture.test': [{'name': 'sid', 'value': 'session-' + str(time.time_ns()), 'domain': 'fixture.test',
                                            'path': '/', 'expires': None, 'secure': True, 'http_only': True, 'same_site': None}]}

    def cookies_export(self):
        return dict(self.sites), []

    def close(self):
        self.open = False
        self.log.append(('close',))


class FakeTunnel:
    """``subprocess.Popen`` for ngrok: reports one public address, records ``terminate``."""

    def __init__(self):
        self.processes = []

    def __call__(self, argv, **kwargs):
        tunnel = self

        class Process:
            stdout = iter([json.dumps({'msg': 'started tunnel', 'url': 'https://abc123.ngrok-free.app'}) + '\n'])
            terminated = False

            def terminate(inner):
                inner.terminated = True
        process = Process()
        process.argv = argv
        tunnel.processes.append(process)
        return process


def telegram_transport(calls):
    def transport(url, body=None, headers=None, timeout=60):
        method = url.rsplit('/', 1)[-1]
        calls.append((method, body))
        if method == 'sendMessage':
            return {'ok': True, 'result': {'message_id': 9000 + len(calls)}}
        if method == 'getMe':
            return {'ok': True, 'result': {'username': 'owner_test_bot'}}
        return {'ok': True, 'result': True}
    return transport


def ended_link_edits(calls):
    """#255: the ``editMessageText`` calls on a sent link message (both transports number a reply 9000 + its call count)."""
    links = {9000 + index + 1 for index, (method, body) in enumerate(list(calls))
             if method == 'sendMessage' and 'remote-login?code=' in str(body.get('text'))}
    return [body for method, body in list(calls) if method == 'editMessageText' and body.get('message_id') in links]


# ---------------------------------------------------------------- unit: the worker refuses while parked

class WorkerOpsWhileParked(unittest.TestCase):
    """The remote ops never touch WebKit while the window is parked: ``not_shown`` before anything else."""

    def worker(self):
        worker = browser_worker.Worker.__new__(browser_worker.Worker)
        worker.owner_visible = False
        worker.pending = {}
        worker.popups = []
        worker.replies = []
        worker.emit = worker.replies.append
        return worker

    def test_every_remote_op_answers_not_shown_while_the_window_is_parked(self):
        worker = self.worker()
        for op, command in (('frame', {}), ('remote_tap', {'x': 1, 'y': 1}), ('remote_text', {'text': 'x'}),
                            ('remote_key', {'key': 'Enter'}), ('remote_nav', {'action': 'back'}),
                            ('remote_begin', {}), ('remote_dialog', {'answer': 'ok'})):
            with self.subTest(op=op):
                worker.handle({'id': 7, 'op': op, **command})
                self.assertEqual(worker.replies[-1], {'id': 7, 'ok': False, 'error': 'not_shown'})

    def test_a_frame_while_a_dialog_is_held_is_the_last_frame_without_a_snapshot(self):
        """Live 2026-10-01: a page waiting on its own confirm() renders nothing, so a snapshot never completes."""
        worker = self.worker()
        worker.owner_visible = True
        worker.held_dialog = {'kind': 'confirm', 'text': 'delete?', 'handler': None, 'sheet': None}
        worker.last_frame = ('AAAA', 1280.0, 900.0)
        worker.front_view = lambda: self.fail('no snapshot while a dialog is held')
        worker.handle({'id': 9, 'op': 'frame'})
        self.assertEqual(worker.replies[-1], {'id': 9, 'ok': True, 'jpeg': 'AAAA', 'width': 1280.0, 'height': 900.0,
                                              'dialog': {'kind': 'confirm', 'text': 'delete?'}, 'focus': None})

    def test_a_page_dialog_is_held_for_the_phone_only_during_a_phone_session(self):
        """#936's rule stays in force without a phone session; with one, the phone (or the Mac's sheet) answers."""
        worker = self.worker()
        worker.owner_visible = True
        worker.dialog_step = None
        worker.held_dialog = None
        worker.remote = False
        worker.show_dialog_sheet = lambda kind, text: None   # the Mac's sheet needs AppKit; not here
        answers = []
        self.assertFalse(worker.hold_dialog('confirm', 'Sure?', answers.append), 'no phone session: #936 applies')
        worker.handle({'id': 1, 'op': 'remote_begin'})
        self.assertEqual(worker.replies[-1], {'id': 1, 'ok': True})
        self.assertTrue(worker.hold_dialog('confirm', '  삭제  하시겠습니까?  ', answers.append))
        self.assertEqual(worker.held_dialog['text'], '삭제 하시겠습니까?')
        self.assertEqual(answers, [], 'held, not answered')
        # A second dialog while one is held is cancelled at once.
        second = []
        self.assertTrue(worker.hold_dialog('alert', 'again', lambda: second.append('shown')))
        self.assertEqual(second, ['shown'])
        # The phone answers 확인: the page's confirm gets true, once.
        worker.handle({'id': 2, 'op': 'remote_dialog', 'answer': 'ok'})
        self.assertEqual(worker.replies[-1], {'id': 2, 'ok': True, 'answered': True})
        self.assertEqual(answers, [True])
        worker.handle({'id': 3, 'op': 'remote_dialog', 'answer': 'ok'})
        self.assertEqual(worker.replies[-1], {'id': 3, 'ok': True, 'answered': False}, 'nothing held now')
        worker.handle({'id': 4, 'op': 'remote_dialog', 'answer': 'maybe'})
        self.assertEqual(worker.replies[-1], {'id': 4, 'ok': False, 'error': 'bad_answer'})
        # Ending the session cancels a held dialog and stops holding.
        prompts = []
        self.assertTrue(worker.hold_dialog('prompt', 'name?', prompts.append))
        worker.handle({'id': 5, 'op': 'remote_end'})
        self.assertEqual(prompts, [None])
        self.assertFalse(worker.remote)
        self.assertFalse(worker.hold_dialog('confirm', 'Sure?', answers.append))
        self.assertEqual(answers, [True], 'back to the #936 path: not held, not answered here')

    def snapshot_worker(self, focus_answer):
        """A shown window whose snapshot and focus probe answer at once (no PyObjC): ``focus_answer`` is
        what ``REMOTE_FOCUS_SCRIPT`` returned, or an Exception for a failed script."""
        worker = self.worker()
        worker.owner_visible = True
        worker.held_dialog = None
        worker.deadline = lambda ident, seconds: worker.pending.__setitem__(ident, True)

        class Size:
            width, height = 390.0, 844.0

        class View:
            def frame(self):
                return type('Frame', (), {'size': Size()})()

            def takeSnapshotWithConfiguration_completionHandler_(self, config, done):
                done('image', None)

        class Config:
            def init(self):
                return self

            def setSnapshotWidth_(self, width):
                pass

            def setAfterScreenUpdates_(self, flag):
                pass

        worker.WebKit = type('WebKit', (), {'WKSnapshotConfiguration': type('C', (), {'alloc': staticmethod(Config)})})
        worker.AppKit = None
        worker.front_view = View
        worker.scripts = []

        def run(body, arguments, done, world=None, view=None):
            worker.scripts.append(body)
            if isinstance(focus_answer, Exception):
                return done(None, 'script_failed')
            done(focus_answer, None)
        worker.run = run
        return worker

    def test_a_frame_carries_the_kind_of_the_focused_element_and_nothing_else(self):
        """#293: the phone keyboard follows what has focus; an unknown or failed answer is no focus."""
        from unittest import mock
        with mock.patch.object(browser_worker, '_jpeg_bytes', return_value=b'jpg'):
            for answer, expected in (({'focus': 'text'}, 'text'), ({'focus': 'password'}, 'password'),
                                     ({'focus': 'frame'}, 'frame'), ({'focus': None}, None),
                                     ({'focus': 'hunter2'}, None), ({'focus': 'text', 'value': 'x'}, 'text'),
                                     (RuntimeError('blocked'), None)):
                with self.subTest(answer=answer):
                    worker = self.snapshot_worker(answer)
                    worker.handle({'id': 11, 'op': 'frame'})
                    self.assertEqual(worker.scripts, [browser_worker.REMOTE_FOCUS_SCRIPT])
                    self.assertEqual(worker.replies[-1], {'id': 11, 'ok': True, 'jpeg': 'anBn', 'width': 390.0, 'height': 844.0,
                                                          'dialog': None, 'focus': expected})

    def test_the_focus_probe_names_the_kind_through_shadow_roots_and_frames(self):
        """#293: the probe's own logic over a minimal DOM (Node; WebKit itself is the opt-in real-browser path)."""
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        harness = """
const el = (tag, extra = {}) => Object.assign({tagName: tag, getAttribute(name) { return this.attrs[name] ?? null; }, attrs: {}}, extra);
const body = el('BODY');
const cases = {
  none: body,
  text: el('INPUT', {attrs: {type: 'email'}}),
  untyped: el('INPUT'),
  password: el('INPUT', {attrs: {type: 'PASSWORD'}}),
  checkbox: el('INPUT', {attrs: {type: 'checkbox'}}),
  readonly: el('INPUT', {readOnly: true}),
  disabled: el('TEXTAREA', {disabled: true}),
  textarea: el('TEXTAREA'),
  editable: el('DIV', {isContentEditable: true}),
  button: el('BUTTON'),
  shadow: el('X-LOGIN', {shadowRoot: {activeElement: el('INPUT', {attrs: {type: 'password'}})}}),
  sameOrigin: el('IFRAME', {contentDocument: {body: {}, activeElement: el('INPUT')}}),
  crossOrigin: el('IFRAME', {contentDocument: null}),
  blocked: Object.defineProperty(el('IFRAME'), 'contentDocument', {get() { throw new Error('denied'); }}),
};
const probe = new Function('document', %s);
const out = {};
for (const [name, active] of Object.entries(cases)) out[name] = JSON.parse(probe({activeElement: active, body})).focus;
out.empty = JSON.parse(probe({activeElement: null})).focus;
console.log(JSON.stringify(out));
""" % json.dumps(browser_worker.REMOTE_FOCUS_SCRIPT)
        result = subprocess.run([node, '-e', harness], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            'none': None, 'text': 'text', 'untyped': 'text', 'password': 'password', 'checkbox': None, 'readonly': None,
            'disabled': None, 'textarea': 'text', 'editable': 'text', 'button': None, 'shadow': 'password',
            'sameOrigin': 'text', 'crossOrigin': 'frame', 'blocked': 'frame', 'empty': None})

    def test_the_focus_probe_reads_the_kind_never_a_value_or_the_page_text(self):
        script = browser_worker.REMOTE_FOCUS_SCRIPT
        for read in ('.value', 'innerText', 'textContent', 'innerHTML', 'outerHTML', 'getSelection', 'location'):
            self.assertNotIn(read, script)
        self.assertEqual(set(browser_worker.REMOTE_FOCUS_KINDS), set(bs.REMOTE_FOCUS_KINDS))

    def test_the_driver_passes_only_a_known_focus_kind(self):
        driver = bs.WebKitWorkerDriver.__new__(bs.WebKitWorkerDriver)
        for focus, expected in (('text', 'text'), ('password', 'password'), ('frame', 'frame'), (None, None),
                                ('secret', None), (['text'], None)):
            with self.subTest(focus=focus):
                driver._request_shown = lambda op, **kw: {'jpeg': 'AAAA', 'width': 1.0, 'height': 1.0, 'focus': focus}
                self.assertEqual(driver.frame()['focus'], expected)

    def test_the_keys_and_actions_the_phone_offers_are_the_ones_the_worker_knows(self):
        self.assertEqual(set(remote_login.KEYS), set(browser_worker.REMOTE_KEYS))
        self.assertEqual(set(remote_login.NAV_ACTIONS), set(browser_worker.REMOTE_NAV_ACTIONS))
        self.assertEqual(set(remote_login.INPUT_KINDS), set(bs.REMOTE_INPUT_KINDS))


class InputValidation(unittest.TestCase):
    def test_only_the_four_shapes_pass_and_bounded(self):
        self.assertEqual(remote_login.validate_input({'type': 'tap', 'x': 10, 'y': 20.5}), ('tap', {'x': 10.0, 'y': 20.5}))
        self.assertEqual(remote_login.validate_input({'type': 'text', 'text': 'abc'}), ('text', {'text': 'abc'}))
        self.assertEqual(remote_login.validate_input({'type': 'text', 'text': '한😀'}), ('text', {'text': '한😀'}))
        self.assertEqual(remote_login.validate_input({'type': 'key', 'key': 'Backspace'}), ('key', {'key': 'Backspace'}))
        self.assertEqual(remote_login.validate_input({'type': 'nav', 'action': 'reload'}), ('nav', {'action': 'reload'}))
        self.assertEqual(remote_login.validate_input({'type': 'dialog', 'answer': 'cancel'}), ('dialog', {'answer': 'cancel'}))
        for bad in ({'type': 'dialog', 'answer': 'yes'}, {'type': 'dialog', 'answer': True},{'type': 'tap', 'x': -1, 'y': 0}, {'type': 'tap', 'x': True, 'y': 1}, {'type': 'text', 'text': ''},
                    {'type': 'text', 'text': 'a\nb'}, {'type': 'text', 'text': 'x' * (remote_login.TEXT_LIMIT + 1)},
                    {'type': 'text', 'text': 'ab\ud83d'}, {'type': 'text', 'text': '\ude00'},
                    {'type': 'key', 'key': 'F5'}, {'type': 'nav', 'action': 'forward'}, {'type': 'script'}, [], None):
            with self.subTest(bad=bad):
                self.assertIsNone(remote_login.validate_input(bad))


# ---------------------------------------------------------------- the routes over a local server

class RemoteLoginSurface(unittest.TestCase):
    """The owner's instance with an explicit login window open and a remote session for it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = QuickStore(Path(self.tmp.name) / 'data')
        self.calls, self.driver_log, self.drivers = [], [], []

        def launcher(profile_dir, headless):
            driver = WindowDriver(self.driver_log)
            self.drivers.append(driver)
            return driver
        profile_dir = Path(self.tmp.name) / 'profile'
        self.jar = CookieJar(profile_dir / JAR_NAME, MemoryKey())
        self.profile = bs.BrowserProfile(profile_dir, launcher=launcher, jar=self.jar)
        self.service = AgentService(self.store, ModelAdapter(lambda *a, **k: {}), telegram_transport(self.calls),
                                    browser_profile=self.profile)
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0})
        self.tunnel = FakeTunnel()
        self.service.remote_login_popen = self.tunnel
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.service))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.close_windows)
        self.service.local_server_port = self.server.server_port
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def close_windows(self):
        """End the session and every login window, and wait for their threads: the window's final save and
        ``on_closed`` write under the data directory, which the temporary directory removes right after."""
        session = self.service.remote_login_session()
        if session is not None:
            session.finish('closed')
        for driver in self.drivers:
            driver.open = False
        # #156: a window still starting has no driver in ``self.drivers`` yet; its own stop flag
        # closes it too (before, cleanup waited out both 5 s bounds and the window outlived the test).
        for record in list(self.profile._login_windows.values()):
            record['stop'].set()
        for record in list(self.profile._login_windows.values()):
            record['done'].wait(5)
        thread = self.profile._login_thread
        if thread is not None:
            thread.join(5)

    def request(self, path, method='GET', body=None, tunneled=True, cookie=None):
        headers = {}
        if tunneled:
            headers.update({'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Proto': 'https'})
        if cookie:
            headers['Cookie'] = f'{remote_login.COOKIE_NAME}={cookie}'
        data = json.dumps(body).encode() if body is not None else (b'' if method == 'POST' else None)
        if body is not None:
            headers['Content-Type'] = 'application/json'
        try:
            with urlopen(Request(self.base + path, data=data, method=method, headers=headers), timeout=10) as response:
                return response.status, response.read(), dict(response.headers)
        except HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def open_window(self, phone=True):
        """An explicit login request (the Settings path) with ``phone``: the window shows, the link is sent."""
        receipt = self.service.open_browser_for_login({'url': 'https://fixture.test/login', 'phone': phone})
        self.assertEqual(receipt['state'], 'opening')
        self.assertTrue(wait_until(lambda: self.service.remote_login_session() is not None and
                                   self.service.remote_login_session().link is not None
                                   and any(method == 'sendMessage' for method, _ in self.calls)), 'the session started and the link was sent')
        return self.service.remote_login_session()

    def code(self, session):
        return '?code=' + session.code

    def frame_url(self, session, full=True):
        """The frame route; ``full`` asks for the frame even when it did not change (as the page's first fetch does)."""
        return remote_login.FRAME_PATH + self.code(session) + ('&full=1' if full else '')

    def bound_cookie(self, session):
        """Open the page as the phone and make its first API call: the cookie the page set is now bound."""
        status, body, headers = self.request(remote_login.PAGE_PATH + self.code(session))
        self.assertEqual(status, 200)
        jar = SimpleCookie()
        jar.load(headers['Set-Cookie'])
        morsel = jar[remote_login.COOKIE_NAME]
        self.assertTrue(morsel['httponly'])
        self.assertTrue(morsel['secure'])
        self.assertEqual(morsel['samesite'], 'Strict')
        cookie = morsel.value
        self.assertEqual(self.request(self.frame_url(session), cookie=cookie)[0], 200)
        return cookie

    # -- the link ---------------------------------------------------------------
    def test_the_link_goes_to_the_owner_chat_only_without_a_preview_and_carries_the_code(self):
        session = self.open_window()
        sends = [body for method, body in self.calls if method == 'sendMessage']
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]['chat_id'], CHAT)
        self.assertIn(session.link, sends[0]['text'])
        self.assertEqual(sends[0]['link_preview_options'], {'is_disabled': True})
        self.assertTrue(session.link.startswith('https://abc123.ngrok-free.app/remote-login?code='))
        self.assertGreaterEqual(len(session.code), 43, 'a 32-byte urlsafe code')
        self.assertIn('--host-header=rewrite', self.tunnel.processes[0].argv)
        self.assertIn('--inspect=false', self.tunnel.processes[0].argv)
        self.assertLessEqual(session.expires - session.created, remote_login.SESSION_SECONDS)

    def test_a_wrong_or_missing_code_is_404_on_every_route(self):
        session = self.open_window()
        cookie = self.bound_cookie(session)
        for path, method in ((remote_login.PAGE_PATH, 'GET'), (remote_login.FRAME_PATH, 'GET'),
                             (remote_login.INPUT_PATH, 'POST'), (remote_login.DONE_PATH, 'POST')):
            for query in ('', '?code=', '?code=wrong', '?code=' + session.code[:-1] + 'x', '?code=%C3%A9'):
                with self.subTest(path=path, query=query):
                    body = {'type': 'key', 'key': 'Enter'} if method == 'POST' else None
                    self.assertEqual(self.request(path + query, method, body, cookie=cookie)[0], 404)
        self.assertTrue(session.alive(), 'guessing never closes the session')
        self.assertFalse([entry for entry in self.driver_log if entry[0].startswith('remote_') and entry[0] != 'remote_begin'],
                         'nothing reached the window')

    def test_a_second_client_without_the_bound_cookie_is_404(self):
        session = self.open_window()
        cookie = self.bound_cookie(session)
        for path, method in ((remote_login.PAGE_PATH, 'GET'), (remote_login.FRAME_PATH, 'GET'),
                             (remote_login.INPUT_PATH, 'POST'), (remote_login.DONE_PATH, 'POST')):
            with self.subTest(path=path, who='no cookie'):
                self.assertEqual(self.request(path + self.code(session), method, {} if method == 'POST' else None)[0], 404)
            with self.subTest(path=path, who='another cookie'):
                self.assertEqual(self.request(path + self.code(session), method, {} if method == 'POST' else None, cookie='x' * 43)[0], 404)
        # The bound phone still works, and reloading its page sets no new cookie.
        status, _body, headers = self.request(remote_login.PAGE_PATH + self.code(session), cookie=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn('Set-Cookie', headers)
        self.assertEqual(self.request(self.frame_url(session), cookie=cookie)[0], 200)
        self.assertTrue(session.alive())

    def test_a_link_preview_fetch_before_the_owner_opens_the_page_does_not_bind(self):
        """Telegram's preview fetcher (or any GET that never runs the page) must not lock the owner out."""
        session = self.open_window()
        status, _body, headers = self.request(remote_login.PAGE_PATH + self.code(session))
        self.assertEqual(status, 200)
        preview = SimpleCookie()
        preview.load(headers['Set-Cookie'])
        preview_cookie = preview[remote_login.COOKIE_NAME].value
        cookie = self.bound_cookie(session)   # the owner's page, opened later, binds on its first API call
        self.assertNotEqual(cookie, preview_cookie)
        self.assertEqual(self.request(self.frame_url(session), cookie=preview_cookie)[0], 404)
        self.assertEqual(self.request(remote_login.PAGE_PATH + self.code(session), cookie=preview_cookie)[0], 404)

    # -- the page -----------------------------------------------------------------
    def test_the_page_is_self_contained_korean_and_talks_only_to_its_own_api(self):
        session = self.open_window()
        status, body, headers = self.request(remote_login.PAGE_PATH + self.code(session))
        self.assertEqual(status, 200)
        page = body.decode()
        csp = headers['Content-Security-Policy']
        self.assertIn("default-src 'none'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("form-action 'none'", csp)
        self.assertIn('nonce-', csp)
        for label in ('입력', '엔터', '←지우기', '뒤로', '새로고침', '완료'):
            self.assertIn(label, page)
        self.assertIn('fixture.test', page)
        self.assertNotIn('http://', page.split('<script')[1], 'no external resource')
        self.assertNotIn('https://', page.split('<script')[1])
        self.assertIn(remote_login.INPUT_PATH, page)
        self.assertIn('setInterval(frame,500)', page)
        self.assertNotIn('<link', page)

    # -- driving the window ----------------------------------------------------------
    def test_the_page_reports_typing_or_claims_done_only_when_the_operation_said_ok(self):
        """Codex P2: a 200 with ``{ok: false}`` (focus lost, worker refused) must not pass as typed or show 완료."""
        session = self.open_window()
        cookie = self.bound_cookie(session)
        page = self.request(remote_login.PAGE_PATH + self.code(session), cookie=cookie)[1].decode()
        post = page.split('async function post')[1].split('\n')[1]
        self.assertIn('if(!r.ok)return false;const d=await r.json();return d&&d.ok===true', post)
        self.assertNotRegex(post, r'return r\.ok\}', 'the HTTP status alone never counts as success')
        self.assertIn("const ok=await post('/api/remote-login/input',body)", page)
        self.assertIn("else if(!ok)failed=true", page)
        self.assertIn("if(await post('/api/remote-login/done')){end(", page)
        # The server side of that contract: a refused input is a 200 whose ok is false.
        self.drivers[-1].open = False
        self.drivers[-1].remote_input = lambda kind, **fields: (_ for _ in ()).throw(bs.WorkerError('not_typable'))
        self.drivers[-1].open = True
        status, answer, _ = self.request(remote_login.INPUT_PATH + self.code(session), 'POST', {'type': 'text', 'text': 'x'}, cookie=cookie)
        self.assertEqual((status, json.loads(answer)), (200, {'ok': False}))

    def test_frames_come_from_the_window_each_time_and_inputs_reach_it_with_no_log(self):
        session = self.open_window()
        cookie = self.bound_cookie(session)
        driver = self.drivers[-1]
        frames_before = driver.frames
        with self.assertLogs('personal_agent', level='DEBUG') as logs:
            status, body, _ = self.request(self.frame_url(session), cookie=cookie)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {'jpeg': 'AAAA', 'width': 1280.0, 'height': 900.0, 'dialog': None, 'focus': None})
            self.assertEqual(driver.frames, frames_before + 1, 'read from the window now, never cached')
            for body in ({'type': 'tap', 'x': 640, 'y': 300}, {'type': 'text', 'text': SECRET_TEXT},
                         {'type': 'key', 'key': 'Backspace'}, {'type': 'nav', 'action': 'reload'}):
                status, answer, _ = self.request(remote_login.INPUT_PATH + self.code(session), 'POST', body, cookie=cookie)
                self.assertEqual((status, json.loads(answer)), (200, {'ok': True}))
            self.assertEqual(self.request(remote_login.INPUT_PATH + self.code(session), 'POST', {'type': 'key', 'key': 'F5'}, cookie=cookie)[1],
                             b'{"ok": false}')
            self.assertEqual(self.request(remote_login.INPUT_PATH + self.code(session), 'POST', {'type': 'text', 'text': ''}, cookie=cookie)[1],
                             b'{"ok": false}')
            # A log line exists (content-free), so assertLogs has something to inspect.
            logging.getLogger('personal_agent.remote_login').info('remote login probe site=%s', session.site)
        remote = [entry for entry in self.driver_log if entry[0].startswith('remote_') and entry[0] != 'remote_begin']
        self.assertEqual(remote, [('remote_tap', {'x': 640.0, 'y': 300.0}), ('remote_text', {'text': SECRET_TEXT}),
                                  ('remote_key', {'key': 'Backspace'}), ('remote_nav', {'action': 'reload'})])
        self.assertNotIn(SECRET_TEXT, '\n'.join(logs.output))
        self.assertNotIn('640', '\n'.join(logs.output))
        self.assertFalse([line for line in logs.output if 'AAAA' in line], 'no frame in a log')
        for line in logs.output:
            if 'remote login' in line:
                self.assertRegex(line, r'remote login \w+ site=fixture\.test$')
        # Nothing of the input is in the store either.
        with self.store.db() as db:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                for row in db.execute(f'SELECT * FROM {table}'):
                    self.assertNotIn(SECRET_TEXT, json.dumps([str(value) for value in row]))
        self.assertFalse(hasattr(session, 'frames') or hasattr(session, 'last_frame'), 'a session keeps no frame')

    def test_a_page_dialog_reaches_the_phone_and_its_answer_reaches_the_window(self):
        session = self.open_window()
        cookie = self.bound_cookie(session)
        driver = self.drivers[-1]
        self.assertTrue(driver.remote, 'the session told the worker to hold dialogs for the phone')
        driver.dialog = {'kind': 'confirm', 'text': '삭제하시겠습니까?'}
        status, body, _ = self.request(self.frame_url(session), cookie=cookie)
        self.assertEqual(json.loads(body)['dialog'], {'kind': 'confirm', 'text': '삭제하시겠습니까?'})
        page = self.request(remote_login.PAGE_PATH + self.code(session), cookie=cookie)[1].decode()
        self.assertIn('기다리고 있어요', page)
        self.assertIn('dialog-ok', page)
        status, answer, _ = self.request(remote_login.INPUT_PATH + self.code(session), 'POST', {'type': 'dialog', 'answer': 'ok'}, cookie=cookie)
        self.assertEqual((status, json.loads(answer)), (200, {'ok': True}))
        self.assertIn(('remote_dialog', {'answer': 'ok'}), self.driver_log)
        self.assertIsNone(json.loads(self.request(self.frame_url(session), cookie=cookie)[1])['dialog'])
        session.finish('done')
        self.assertIn(('remote_end',), self.driver_log, 'ending the session ends the hold')

    def test_a_received_share_refuses_the_window_and_the_phone(self):
        """#940 hook: a site this instance received (family share, #935) never gets a login window here."""
        refusals = []

        def refuse(site):
            refusals.append(site)
            return '이 사이트는 공유받은 로그인이라 여기서 다시 로그인할 수 없어요' if site == 'fixture.test' else None
        self.service.refuse_login = refuse
        for body in ({'url': 'https://www.fixture.test/login'}, {'url': 'https://fixture.test/login', 'phone': True}):
            with self.assertRaises(ValueError) as caught:
                self.service.open_browser_for_login(body)
            self.assertEqual(str(caught.exception), '이 사이트는 공유받은 로그인이라 여기서 다시 로그인할 수 없어요')
        self.assertEqual(refusals, ['fixture.test', 'fixture.test'], 'asked by registrable site')
        self.assertEqual(self.drivers, [], 'no window opened')
        self.assertEqual(self.tunnel.processes, [], 'no tunnel opened')
        with self.assertRaises(remote_login.RemoteLoginError):
            self.service.start_remote_login('w', 'fixture.test', lambda reason: None)
        # Another site is unaffected.
        receipt = self.service.open_browser_for_login({'url': 'https://other.test/login'})
        self.assertEqual(receipt['state'], 'opening')
        self.assertTrue(wait_until(lambda: self.drivers))

    def test_done_closes_the_window_saves_the_jar_and_stops_the_tunnel(self):
        session = self.open_window()
        cookie = self.bound_cookie(session)
        driver = self.drivers[-1]
        self.request(remote_login.INPUT_PATH + self.code(session), 'POST', {'type': 'key', 'key': 'Enter'}, cookie=cookie)
        self.assertTrue(driver.sites, 'the fake sign-in set a session cookie')
        status, body, _ = self.request(remote_login.DONE_PATH + self.code(session), 'POST', {}, cookie=cookie)
        self.assertEqual((status, json.loads(body)), (200, {'ok': True}))
        self.assertTrue(session.alive(), 'review P3-3: the answer left before the tunnel stops')
        self.assertFalse(self.tunnel.processes[0].terminated)
        self.assertTrue(wait_until(lambda: not session.alive()), 'then the session ends')
        self.assertTrue(wait_until(lambda: self.profile.login_window_outcome(session.window) is not None), 'the window closed')
        self.assertEqual(self.profile.login_window_outcome(session.window), ('closed', True), 'closed by request and saved')
        self.assertIn('fixture.test', {site for site in self.jar.import_rows()[0]}, 'the session is in the jar')
        self.assertTrue(self.tunnel.processes[0].terminated, 'the tunnel stopped')
        self.assertFalse(session.alive())
        self.assertEqual(session.reason, 'done')
        self.assertIsNone(self.service.remote_login_session())
        # Afterwards the link is dead on every route, and a second 완료 does nothing.
        for path, method in ((remote_login.PAGE_PATH, 'GET'), (remote_login.FRAME_PATH, 'GET'),
                             (remote_login.INPUT_PATH, 'POST'), (remote_login.DONE_PATH, 'POST')):
            self.assertEqual(self.request(path + self.code(session), method, {} if method == 'POST' else None, cookie=cookie)[0], 404)
        self.assertFalse(session.finish('done'))

    def ended_link_edits(self):
        """The edits that rewrote a link message (#255)."""
        return ended_link_edits(self.calls)

    def test_each_end_rewrites_the_link_message_in_place_without_the_link(self):
        """#255: a settled phone sign-in never leaves a live-looking link: done, expiry and a close each say so."""
        cases = (('done', remote_login.LINK_ENDED_TEXT['done']), ('expired', remote_login.LINK_ENDED_TEXT['expired']),
                 ('closed', remote_login.LINK_ENDED_TEXT['closed']))
        for reason, ended in cases:
            with self.subTest(reason=reason):
                self.calls.clear()
                session = self.open_window()
                self.assertEqual(self.ended_link_edits(), [], 'still live: nothing rewritten yet')
                if reason == 'expired':
                    session.expires = session.clock() - 1
                elif reason == 'closed':
                    self.drivers[-1].open = False
                else:
                    self.assertTrue(session.finish('done'))
                self.assertTrue(wait_until(lambda: self.ended_link_edits()), 'the link message was rewritten')
                edits = self.ended_link_edits()
                self.assertEqual(len(edits), 1, 'once')
                self.assertEqual(edits[0]['chat_id'], CHAT)
                self.assertEqual(edits[0]['text'], ended.format(site='fixture.test'))
                self.assertNotIn(session.link, edits[0]['text'])
                self.assertNotIn('remote-login?code=', edits[0]['text'])
                self.assertFalse(session.finish('closed'), 'a later end does nothing more')
                time.sleep(0.2)
                self.assertEqual(len(self.ended_link_edits()), 1)
                self.close_windows()
                self.assertTrue(wait_until(lambda: self.service.remote_login_session() is None))

    def test_a_link_whose_session_ended_while_it_was_sent_is_rewritten_at_once(self):
        """#255: the session may end before the send returns; the late message is still not left live."""
        service = self.service
        original = service.telegram.call

        def call(method, body, **kwargs):
            result = original(method, body, **kwargs)
            if method == 'sendMessage' and 'remote-login?code=' in str(body.get('text')):
                service.remote_login_session().finish('expired')
            return result
        service.telegram.call = call
        self.assertEqual(service.open_browser_for_login({'url': 'https://fixture.test/login', 'phone': True})['state'], 'opening')
        self.assertTrue(wait_until(lambda: self.ended_link_edits()), 'rewritten once the send returned')
        self.assertEqual(self.ended_link_edits()[0]['text'], remote_login.LINK_ENDED_TEXT['expired'].format(site='fixture.test'))

    def test_expiry_closes_the_window_and_the_tunnel(self):
        session = self.open_window()
        cookie = self.bound_cookie(session)
        session.expires = session.clock() - 1   # the fake clock moves: the watcher sees it within half a second
        self.assertTrue(wait_until(lambda: not session.alive()), 'the session ended')
        self.assertEqual(session.reason, 'expired')
        self.assertTrue(self.tunnel.processes[0].terminated)
        self.assertTrue(wait_until(lambda: self.profile.login_window_outcome(session.window) is not None), 'the window closed')
        self.assertEqual(self.profile.login_window_outcome(session.window)[0], 'closed')
        self.assertEqual(self.request(self.frame_url(session), cookie=cookie)[0], 404)
        self.assertEqual(self.request(remote_login.PAGE_PATH + self.code(session), cookie=cookie)[0], 404)

    def test_a_mac_close_ends_the_session_and_stops_the_tunnel(self):
        session = self.open_window()
        cookie = self.bound_cookie(session)
        self.drivers[-1].open = False   # the owner closed the window on the Mac
        self.assertTrue(wait_until(lambda: not session.alive()))
        self.assertEqual(session.reason, 'closed')
        self.assertTrue(self.tunnel.processes[0].terminated)
        self.assertEqual(self.request(self.frame_url(session), cookie=cookie)[0], 404)

    def test_one_session_at_a_time(self):
        self.open_window()
        with self.assertRaises(ValueError) as caught:
            self.service.open_browser_for_login({'url': 'https://fixture.test/login', 'phone': True})
        self.assertEqual(str(caught.exception), remote_login.BUSY_TEXT)
        self.assertEqual(len(self.tunnel.processes), 1)

    # -- the tunnel gate ---------------------------------------------------------------
    def test_through_the_tunnel_every_other_route_is_refused_while_and_after_the_session(self):
        session = self.open_window()
        others = ('/', '/api/status', '/api/settings', '/healthz', '/api/claim', '/family-setup', '/api/browser/login')
        for path in others:
            with self.subTest(path=path, when='open'):
                self.assertEqual(self.request(path)[0], 404)
                self.assertEqual(self.request(path, 'POST', {})[0], 404)
                self.assertEqual(self.request(path, 'DELETE')[0], 404)
        self.assertEqual(self.request(remote_login.PAGE_PATH + self.code(session))[0], 200)
        session.finish('done')
        for path in others:
            with self.subTest(path=path, when='after'):
                self.assertEqual(self.request(path)[0], 404, 'a tunnel left up reaches nothing')
        self.assertNotEqual(self.request('/healthz', tunneled=False)[0], 404, 'this Mac itself is unaffected')

    def test_before_any_session_the_tunnel_gate_is_open_as_today(self):
        self.assertFalse(self.service.remote_login_started())
        self.assertNotEqual(self.request('/healthz')[0], 404)
        self.assertEqual(self.request(remote_login.PAGE_PATH + '?code=x')[0], 404)

    # -- the explicit request's preconditions ------------------------------------------
    def test_phone_needs_a_paired_telegram(self):
        self.store.put('telegram', {})
        with self.assertRaises(ValueError):
            self.service.open_browser_for_login({'url': 'https://fixture.test/login', 'phone': True})
        self.assertEqual(self.drivers, [], 'no window was opened')

    def test_a_link_that_cannot_be_sent_closes_the_tunnel_and_keeps_the_window(self):
        """Review P2-1: nothing was wrong with the login itself; the Mac window stays for the owner."""
        self.calls.clear()
        failing = []

        def transport(url, body=None, headers=None, timeout=60):
            if url.endswith('/sendMessage'):
                failing.append(body)
                raise OSError('telegram down')
            return {'ok': True, 'result': True}
        self.service.telegram_transport = transport
        receipt = self.service.open_browser_for_login({'url': 'https://fixture.test/login', 'phone': True})
        self.assertEqual(receipt['state'], 'opening')
        self.assertTrue(wait_until(lambda: failing and self.tunnel.processes and self.tunnel.processes[0].terminated), 'closed')
        self.assertIsNone(self.service.remote_login_session())
        time.sleep(0.3)
        self.assertTrue(self.profile.status()['login_window_open'], 'the window stays open')
        self.assertTrue(self.drivers[-1].open)
        self.assertIn(('remote_end',), self.driver_log, 'dialogs go back to the Mac alone')

    def test_a_tunnel_that_does_not_open_keeps_the_window_and_tells_the_owner(self):
        """Review P2-1: a second ngrok session that exits with an error."""
        tunnel = self.tunnel

        def broken_popen(argv, **kwargs):
            class Process:
                stdout = iter([json.dumps({'lvl': 'eror', 'err': 'ERR_NGROK_108 limited to 1 simultaneous session'}) + '\n'])
                terminated = False

                def terminate(inner):
                    inner.terminated = True
            process = Process()
            tunnel.processes.append(process)
            return process
        self.service.remote_login_popen = broken_popen
        receipt = self.service.open_browser_for_login({'url': 'https://fixture.test/login', 'phone': True})
        self.assertEqual(receipt['state'], 'opening')
        self.assertTrue(wait_until(lambda: [body for method, body in self.calls if method == 'sendMessage']), 'the owner was told')
        notice = [body for method, body in self.calls if method == 'sendMessage'][-1]
        self.assertEqual(notice['chat_id'], CHAT)
        self.assertIn('Mac의 로그인 창에서 로그인해 주세요', notice['text'])
        self.assertIsNone(self.service.remote_login_session())
        self.assertTrue(self.drivers[-1].open, 'the window stays open')
        self.assertTrue(self.profile.status()['login_window_open'])

    def test_a_transient_frame_failure_is_retryable_not_the_end(self):
        """Review P3-2: only a dead session is 404; the page keeps polling on 503."""
        session = self.open_window()
        cookie = self.bound_cookie(session)
        driver = self.drivers[-1]
        original = driver.frame
        driver.frame = lambda: (_ for _ in ()).throw(bs.WorkerError('frame_failed'))
        status, body, _ = self.request(self.frame_url(session), cookie=cookie)
        self.assertEqual((status, json.loads(body)), (503, {'retry': True}))
        self.assertTrue(session.alive())
        driver.frame = original
        self.assertEqual(self.request(self.frame_url(session), cookie=cookie)[0], 200)
        page = self.request(remote_login.PAGE_PATH + self.code(session), cookie=cookie)[1].decode()
        self.assertIn("if(r.status===404){end('이 링크는 닫혔어요.');return}", page)
        self.assertNotIn('세션이 저장됐어요', page.split('r.status===404')[1].split(';')[0], 'the closed text never claims a save')

    def test_an_unchanged_frame_is_204_and_a_changed_one_is_sent(self):
        """Review P3-4: the same image is not sent twice; a page dialog or a changed image is."""
        session = self.open_window()
        cookie = self.bound_cookie(session)   # one full frame already went out
        self.assertEqual(self.request(self.frame_url(session, full=False), cookie=cookie)[0], 204)
        self.assertEqual(self.request(self.frame_url(session, full=False), cookie=cookie)[0], 204)
        self.assertEqual(self.request(self.frame_url(session), cookie=cookie)[0], 200, 'a page load asks for it anyway')
        self.drivers[-1].dialog = {'kind': 'alert', 'text': '안내'}
        status, body, _ = self.request(self.frame_url(session, full=False), cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['dialog'], {'kind': 'alert', 'text': '안내'})
        self.assertEqual(self.request(self.frame_url(session, full=False), cookie=cookie)[0], 204)
        self.assertFalse(hasattr(session, 'last_frame') or getattr(session, '_last_frame', None) == 'AAAA', 'a digest, never the frame')

    def test_a_change_of_focus_alone_is_a_changed_frame(self):
        """#293: the image may look the same when a field gains focus; the page still has to hear of it."""
        session = self.open_window()
        cookie = self.bound_cookie(session)
        self.assertEqual(self.request(self.frame_url(session, full=False), cookie=cookie)[0], 204)
        self.drivers[-1].focus = 'password'
        status, body, _ = self.request(self.frame_url(session, full=False), cookie=cookie)
        self.assertEqual((status, json.loads(body)['focus']), (200, 'password'))
        self.assertEqual(self.request(self.frame_url(session, full=False), cookie=cookie)[0], 204)

    def test_two_pages_opened_before_binding_both_keep_their_cookie(self):
        """Review P3-1: a later page open never invalidates an earlier one; whichever calls the API first binds."""
        session = self.open_window()
        cookies = []
        for _ in range(2):
            status, _body, headers = self.request(remote_login.PAGE_PATH + self.code(session))
            self.assertEqual(status, 200)
            jar = SimpleCookie()
            jar.load(headers['Set-Cookie'])
            cookies.append(jar[remote_login.COOKIE_NAME].value)
        first, second = cookies
        self.assertNotEqual(first, second)
        status, _body, headers = self.request(remote_login.PAGE_PATH + self.code(session), cookie=first)
        self.assertEqual(status, 200)
        self.assertNotIn('Set-Cookie', headers, 'a reload with a pending cookie keeps it')
        self.assertEqual(self.request(self.frame_url(session), cookie=first)[0], 200, 'the earlier page binds')
        self.assertEqual(self.request(self.frame_url(session), cookie=second)[0], 404)
        self.assertEqual(self.request(remote_login.PAGE_PATH + self.code(session), cookie=second)[0], 404)
        self.assertEqual(self.request(remote_login.PAGE_PATH + self.code(session))[0], 404)

    def test_a_family_instance_opens_a_phone_link_for_its_own_sign_in(self):
        """#949: a family member signs in to their own account from their phone (only a received
        share is refused, #940); the link goes to that instance's own paired chat."""
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0, 'pair_user_id': CHAT})
        receipt = self.service.open_browser_for_login({'url': 'https://fixture.test/login', 'phone': True})
        self.assertEqual(receipt['state'], 'opening')
        self.assertTrue(wait_until(lambda: self.tunnel.processes), 'the tunnel opens')

    def test_a_received_share_site_is_refused_by_default(self):
        """#940: the family-share rule applies without any hook set."""
        from personal_agent import family_share
        self.store.put(family_share.SHARED_KEY, {'fixture.test': {'from': 'owner', 'since': time.time()}})
        self.assertIsNone(self.service.refuse_login)
        for body in ({'url': 'https://www.fixture.test/login'}, {'url': 'https://fixture.test/login', 'phone': True}):
            with self.assertRaises(ValueError) as caught:
                self.service.open_browser_for_login(body)
            self.assertEqual(str(caught.exception), '이 사이트는 공유받은 로그인이라 여기서 다시 로그인할 수 없어요')
        with self.assertRaises(remote_login.RemoteLoginError) as caught:
            self.service.start_remote_login('w', 'fixture.test', lambda reason: None)
        self.assertEqual(str(caught.exception), family_share.LOGIN_REFUSED_TEXT)
        self.assertEqual(self.drivers, [])
        self.assertEqual(self.tunnel.processes, [])
        self.assertEqual(self.service.open_browser_for_login({'url': 'https://other.test/login'})['state'], 'opening')


# ---------------------------------------------------------------- the in-flow prompt's button (#709 + #939)

def _flow_harness():
    from test_flow_login import LoginHarness
    return LoginHarness


class PhoneButton(_flow_harness()):
    """The Telegram login prompt's 휴대폰에서 로그인 starts the session for that Work's window."""

    def setUp(self):
        super().setUp()
        # Most cases exercise the button path. The automatic-link case opts in
        # explicitly; the service default now starts that flow with the prompt.
        self.service.AUTO_PHONE_LOGIN = False
        self.tunnel = FakeTunnel()
        self.service.remote_login_popen = self.tunnel
        self.service.local_server_port = 8787
        self.addCleanup(self.end_session)

    def end_session(self):
        session = self.service.remote_login_session()
        if session is not None:
            session.finish('closed')

    def link_messages(self):
        return [body for method, body in self.calls if method == 'sendMessage' and 'remote-login?code=' in str(body.get('text'))]

    def test_the_link_follows_the_prompt_without_a_tap(self):
        """#953 (owner 2026-10-01): the phone link is sent with the login prompt, no extra tap."""
        from test_flow_login import CHAT as FLOW_CHAT
        self.service.AUTO_PHONE_LOGIN = True
        job_id, prompt, buttons, notification = self.login_work()
        self.assertTrue(wait_until(lambda: self.link_messages()), 'the link was sent with the prompt')
        self.assertEqual([body['chat_id'] for body in self.link_messages()], [FLOW_CHAT])
        self.assertEqual(len(self.tunnel.processes), 1)
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'])
        time.sleep(0.3)
        self.assertEqual(len(self.tunnel.processes), 1, 'a tap while the link is open starts no second session')

    def test_the_prompt_offers_the_phone_and_the_link_goes_to_the_owner_chat_only(self):
        from test_flow_login import CHAT as FLOW_CHAT
        job_id, prompt, buttons, notification = self.login_work()
        rows = prompt['reply_markup']['inline_keyboard']
        self.assertEqual(rows[1], [{'text': BROWSER_LOGIN_PHONE_LABEL, 'callback_data': f"p7l:{notification['id']}:phone"}])
        # A stranger's tap, and a tap on another message: nothing starts, nothing is sent.
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'], sender=777)
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'] + 1)
        self.assertEqual(self.link_messages(), [])
        self.assertIsNone(self.service.remote_login_session())
        self.assertEqual(self.tunnel.processes, [])
        # The owner's tap on the prompt itself.
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'])
        self.assertTrue(wait_until(lambda: self.link_messages()), 'the link was sent')
        links = self.link_messages()
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['chat_id'], FLOW_CHAT)
        self.assertEqual({body['chat_id'] for method, body in self.calls if method == 'sendMessage'}, {FLOW_CHAT},
                         'every message of this flow went to the paired owner chat')
        session = self.service.remote_login_session()
        self.assertIsNotNone(session)
        self.assertEqual(session.window, (self.service._browser_login(job_id) or {}).get('window'))
        self.assertEqual(session.site, 'fixture.test')
        self.assertEqual(self.state(job_id), 'offered', 'the login is still offered; the phone drives its window')
        # A second tap while the session runs: refused, no second tunnel.
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'])
        self.assertEqual(len(self.tunnel.processes), 1)
        self.assertEqual(len(self.link_messages()), 1)

    def test_done_from_the_phone_settles_the_login_as_login_complete(self):
        job_id, prompt, buttons, notification = self.login_work()
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'])
        self.assertTrue(wait_until(lambda: self.service.remote_login_session() is not None and self.service.remote_login_session().link))
        session = self.service.remote_login_session()
        self.window().login()   # the owner signed in through the phone
        self.assertTrue(session.finish('done'))
        self.assertEqual(self.settle(job_id), 'resumed')
        self.assertTrue(self.tunnel.processes[0].terminated)
        self.assertEqual(self.store.job(job_id)['status'], 'queued', 'the Work continues once')

    def test_an_in_flow_login_for_a_received_share_shows_no_window_and_no_prompt(self):
        """#940 hook on the in-flow path: the row is unavailable; no window, no Telegram prompt."""
        self.service.refuse_login = lambda site: '이 사이트는 공유받은 로그인이라 여기서 다시 로그인할 수 없어요' if site == 'fixture.test' else None
        self.scripts = self.login_script()
        job_id = self.receive('계정 페이지 확인해줘')
        self.assertTrue(self.service.run_one())
        self.assertTrue(wait_until(lambda: self.state(job_id) in (None, 'unavailable')), self.state(job_id))  # #954 review: refused before any row
        self.assertEqual(len(self.drivers), 1, 'only the Work\'s own driver; no login window')
        self.service.deliver_one()
        self.assertEqual(self.prompts(), [])

    def test_an_in_flow_login_for_a_received_share_is_refused_by_default(self):
        """#940 on the in-flow path without a hook: the received site gets no window and no prompt."""
        from personal_agent import family_share
        self.store.put(family_share.SHARED_KEY, {'fixture.test': {'from': 'owner', 'since': time.time()}})
        self.scripts = self.login_script()
        job_id = self.receive('계정 페이지 확인해줘')
        self.assertTrue(self.service.run_one())
        self.assertTrue(wait_until(lambda: self.state(job_id) in (None, 'unavailable')), self.state(job_id))  # #954 review: refused before any row
        self.service.deliver_one()
        self.assertEqual(self.prompts(), [])

    def test_a_tunnel_failure_keeps_the_in_flow_window_and_prompt(self):
        """Review P2-1: the login is still offered; the owner is told to use the Mac window."""
        job_id, prompt, buttons, notification = self.login_work()

        def broken_popen(argv, **kwargs):
            class Process:
                stdout = iter([json.dumps({'lvl': 'eror', 'err': 'limited to 1 simultaneous session'}) + '\n'])

                def terminate(inner):
                    pass
            self.tunnel.processes.append(Process())
            return Process()
        self.service.remote_login_popen = broken_popen
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'])
        self.assertTrue(wait_until(lambda: [b for m, b in self.calls if m == 'sendMessage' and 'Mac의 로그인 창에서' in b.get('text', '')]),
                        'the owner was told')
        time.sleep(0.3)
        self.assertEqual(self.state(job_id), 'offered', 'the login is still offered')
        self.assertTrue(self.window().is_open(), 'the window stays')
        self.assertIsNone(self.service.remote_login_session())
        self.assertEqual([m for m, b in self.calls if m == 'editMessageText'], [], 'the prompt was not edited')
        # The owner can still finish on the Mac.
        self.owner_closes(job_id)
        self.assertEqual(self.settle(job_id), 'resumed')

    def test_a_skip_on_telegram_ends_the_phone_session(self):
        job_id, prompt, buttons, notification = self.login_work()
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'])
        self.assertTrue(wait_until(lambda: self.service.remote_login_session() is not None and self.service.remote_login_session().link))
        session = self.service.remote_login_session()
        self.tap(f"p7l:{notification['id']}:skip", notification['message_id'])
        self.assertEqual(self.settle(job_id), 'skipped')
        self.assertTrue(wait_until(lambda: not session.alive()))
        self.assertEqual(session.reason, 'closed')
        self.assertTrue(self.tunnel.processes[0].terminated)
        # #255: below the prompt's skip text, the link no longer offers a live sign-in.
        self.assertTrue(wait_until(lambda: ended_link_edits(self.calls)), 'the link message was rewritten')
        self.assertEqual([edit['text'] for edit in ended_link_edits(self.calls)],
                         [remote_login.LINK_ENDED_TEXT['closed'].format(site='fixture.test')])


    def prompt_edits(self, notification):
        return [body for method, body in self.calls if method == 'editMessageText'
                and body.get('message_id') == notification['message_id']]

    def phone_prompt(self, notification):
        """#255: the prompt's edit while the phone link runs (no buttons of its own)."""
        return [body for body in self.prompt_edits(notification) if remote_login.PROMPT_PHONE_ACTIVE_LINE in body['text']]

    def test_while_the_phone_link_runs_the_prompt_has_no_buttons_of_its_own(self):
        """#255 (owner choice 2026-10-24): one place to finish, the phone page; the prompt keeps the site lines."""
        from personal_agent.quickstart_service import BROWSER_LOGIN_RESULT_TEXT
        job_id, prompt, buttons, notification = self.login_work()
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'])
        self.assertTrue(wait_until(lambda: self.phone_prompt(notification)), 'the prompt was rewritten')
        edit = self.phone_prompt(notification)[-1]
        self.assertEqual(edit['reply_markup'], {'inline_keyboard': []}, 'no 로그인 완료, 건너뛰기 or 휴대폰 button')
        self.assertTrue(edit['text'].startswith('로그인 요청 사이트: fixture.test\n'), 'the site still leads')
        self.assertNotIn('로그인 완료', edit['text'])
        self.assertNotIn('건너뛰', edit['text'])
        self.assertEqual(self.state(job_id), 'offered', 'the login is still offered')
        # 완료 on the phone settles it, and the outcome is the prompt's last word.
        session = self.service.remote_login_session()
        self.window().login()
        self.assertTrue(session.finish('done'))
        self.assertEqual(self.settle(job_id), 'resumed')
        self.assertEqual(self.prompt_edits(notification)[-1]['text'], BROWSER_LOGIN_RESULT_TEXT['resumed'])

    def test_a_link_that_never_opened_leaves_the_prompt_and_its_buttons(self):
        """#255: only a delivered link withdraws the prompt's buttons; without one the Mac path stays."""
        job_id, prompt, buttons, notification = self.login_work()

        def broken_popen(argv, **kwargs):
            class Process:
                stdout = iter([json.dumps({'lvl': 'eror', 'err': 'limited to 1 simultaneous session'}) + '\n'])

                def terminate(inner):
                    pass
            return Process()
        self.service.remote_login_popen = broken_popen
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'])
        self.assertTrue(wait_until(lambda: [b for m, b in self.calls if m == 'sendMessage' and 'Mac의 로그인 창에서' in b.get('text', '')]))
        time.sleep(0.3)
        self.assertEqual(self.prompt_edits(notification), [])

    def test_a_login_settled_while_the_prompt_was_rewritten_keeps_its_outcome_last(self):
        """#255: a settle racing the phone rewrite never leaves "signing in on the phone" as the last word."""
        from personal_agent.quickstart_service import BROWSER_LOGIN_RESULT_TEXT
        job_id, prompt, buttons, notification = self.login_work()
        service, original = self.service, self.service.telegram.edit_message_text
        nonce = self.service._browser_login(job_id)['nonce']

        def edit(chat_id, message_id, text, reply_markup=None):
            if remote_login.PROMPT_PHONE_ACTIVE_LINE in text:
                # The owner's skip lands and settles first; this rewrite is the late one.
                service._request_login_decision(job_id, nonce, 'skip')
                self.settle(job_id)
            return original(chat_id, message_id, text, reply_markup)
        service.telegram.edit_message_text = edit
        self.tap(f"p7l:{notification['id']}:phone", notification['message_id'])
        self.assertTrue(wait_until(lambda: self.phone_prompt(notification)))
        self.assertTrue(wait_until(lambda: self.prompt_edits(notification)[-1]['text'] == BROWSER_LOGIN_RESULT_TEXT['skipped']),
                        [body['text'][:40] for body in self.prompt_edits(notification)])

if __name__ == '__main__':
    unittest.main()


class MobileUserAgent(unittest.TestCase):
    """#955: the phone session's user agent is a phone browser's and keeps Still's embedded marker."""

    def test_the_mobile_agent_is_a_phone_browser_with_the_marker(self):
        from personal_agent.browser_worker import EMBEDDED_UA_TOKEN, MOBILE_SIZE, mobile_user_agent
        agent = mobile_user_agent()
        self.assertIn('iPhone', agent)
        self.assertIn('Mobile/', agent)
        self.assertTrue(agent.endswith(EMBEDDED_UA_TOKEN), 'Still still refuses its own pages to this browser (#680)')
        self.assertEqual(MOBILE_SIZE, (390.0, 844.0))


    def test_the_os_token_is_one_a_real_iphone_sends(self):
        """#13: Safari 26+ on iOS freezes the OS token (Apple Safari 26.0/26.2 release notes; WebKit 298717, 298473)."""
        from unittest import mock
        from personal_agent import browser_worker as bw
        for version, token in (('26.0', '18_6'), ('26.0.1', '18_6'), ('26.1', '18_6_2'), ('26.2', '18_6_2'),
                               ('26.3.1', '18_6_2'), ('27.0.1', '18_6_2'), ('18.6', '18_6'), ('18.3.1', '18_3'),
                               ('17', '17_0'), ('', '18_6_2'), ('x.y', '18_6_2')):
            self.assertEqual(bw.ios_os_token(version), token, version)
        with mock.patch.object(bw, 'safari_application_name', return_value=f'Version/27.0.1 Safari/605.1.15 {bw.EMBEDDED_UA_TOKEN}'):
            agent = bw.mobile_user_agent()
        self.assertIn('CPU iPhone OS 18_6_2 like Mac OS X', agent)
        self.assertIn('Version/27.0.1 Mobile/15E148', agent, 'the Safari version itself is reported as it is')
        self.assertNotIn('27_0', agent)


class PhonePageAutofill(unittest.TestCase):
    """#1170: the phone page offers iPhone password AutoFill; what it fills goes out as typed text and is cleared."""

    def page(self):
        class Session:
            site, code, expires = 'shop.test', 'c0de', 1000.0

            @staticmethod
            def clock():
                return 400.0
        return remote_login.page(Session(), 'n0nce')

    def test_an_id_and_a_password_field_the_phone_can_autofill(self):
        html = self.page()
        self.assertIn('name="username" type="text" autocomplete="username"', html)
        self.assertIn('name="password" type="password" autocomplete="current-password"', html)
        self.assertIn('<form id="cred" autocomplete="on">', html)

    def test_the_fill_sends_id_tab_password_through_the_input_route_and_keeps_nothing(self):
        html = self.page()
        script = html[html.index("$('cred').addEventListener"):html.index("window.addEventListener('pagehide'")]
        self.assertIn('e.preventDefault()', script, 'the form itself never submits anywhere')
        self.assertLess(script.index('clearCred()'), script.index("type:'text',text:u"), 'cleared before anything is sent')
        self.assertLess(script.index("type:'text',text:u"), script.index("key:'Tab'"))
        self.assertLess(script.index("key:'Tab'"), script.index("type:'text',text:p"))
        self.assertEqual(script.count(remote_login.INPUT_PATH), 3, 'only the existing input route')
        self.assertIn("window.addEventListener('pagehide',clearCred)", html)
        self.assertEqual(remote_login.validate_input({'type': 'key', 'key': 'Tab'}), ('key', {'key': 'Tab'}))


PAGE_TYPING_CHECKS = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const script=fs.readFileSync(process.argv[1],'utf8');
const ids=new Map();let active=null;
class Element{
 constructor(id){this.id=id;this.value='';this.type='text';this.attrs={};this.style={};this.listeners={};this._cls=new Set();this.textContent='';ids.set(id,this);}
 get classList(){const c=this._cls;return {add:n=>c.add(n),remove:n=>c.delete(n),toggle:(n,on)=>on?c.add(n):c.delete(n),contains:n=>c.has(n)};}
 addEventListener(name,fn){(this.listeners[name]=this.listeners[name]||[]).push(fn);}
 fire(name,event={}){event.currentTarget=this;for(const fn of this.listeners[name]||[])fn(event);return event;}
 setAttribute(k,v){this.attrs[k]=v;} focus(){active=this;} blur(){if(active===this)active=null;}
 setSelectionRange(){} getBoundingClientRect(){return {left:0,top:0,width:390,height:844};}
}
for(const id of ['live','end','keys','shot','dialog','dialog-text','dialog-ok','dialog-cancel','typing-failed','enter','tab','back',
 'nav-back','reload','done','expire','cred','user','pass'])new Element(id);
ids.get('keys').type='password';
const document={getElementById:id=>ids.get(id),get activeElement(){return active;}};
const posts=[];let frameAnswer={status:200,body:{jpeg:'AAAA',width:390,height:844,dialog:null,focus:null}};
let inputAnswer=()=>({ok:true});const holds=[];
const respond=(status,body)=>({status,ok:status>=200&&status<300,json:async()=>body});
async function fetch(url,opts={}){
 if(url.startsWith('/api/remote-login/frame'))return respond(frameAnswer.status,frameAnswer.body);
 const body=JSON.parse(opts.body||'{}');posts.push(body);
 if(holds.length&&body.type===holds[0].type){const gate=holds.shift();await gate.wait;}
 return respond(200,inputAnswer(body));
}
const timers=[];
const ctx={document,fetch,console,Date,Math,JSON,Object,Promise,encodeURIComponent,
 setTimeout:fn=>timers.push(fn),setInterval:()=>0,window:{addEventListener(){}}};
vm.createContext(ctx);vm.runInContext(script,ctx);
const $=id=>ids.get(id),keys=$('keys');
const settle=async()=>{for(let i=0;i<20;i++){await new Promise(r=>setImmediate(r));}};
const tick=async()=>{await settle();while(timers.length){timers.shift()();await settle();}};
const S='__________';
const sent=()=>posts.splice(0);
const tap=(x,y)=>$('shot').fire('click',{clientX:x,clientY:y});
const type=(value,event={})=>{keys.value=value;keys.fire('input',event);};
(async()=>{
 await tick();assert.equal(keys.value,S,'the sentinel is set at load');
 // A tap presses the point and opens the keyboard in the same gesture, as a password input until a frame says text.
 tap(195,422);
 assert.equal(active,keys,'focused inside the tap');assert.equal(keys.type,'password');assert.equal(keys.style.top,'422px');
 frameAnswer.body={...frameAnswer.body,focus:'text'};await tick();
 assert.deepEqual(sent(),[{type:'tap',x:195,y:422}]);
 assert.equal(keys.type,'text','a plain text field gets a text input (the Korean keyboard)');
 assert.equal(keys.attrs.autocomplete,'username');
 // Each keystroke goes out in order and the page keeps nothing.
 type(S+'a',{inputType:'insertText'});type(S+'b',{inputType:'insertText'});await tick();
 assert.equal(sent().filter(p=>p.type==='text').map(p=>p.text).join(''),'ab');assert.equal(keys.value,S);
 type(S.slice(0,-1),{inputType:'deleteContentBackward'});await tick();
 assert.deepEqual(sent(),[{type:'key',key:'Backspace'}]);
 // Korean: nothing while a syllable is composed; the committed syllable once.
 keys.fire('compositionstart');type(S+'ㅎ',{isComposing:true});type(S+'하',{isComposing:true});type(S+'한',{isComposing:true});
 await tick();assert.deepEqual(sent(),[]);
 keys.fire('compositionend');await tick();
 assert.deepEqual(sent(),[{type:'text',text:'한'}]);assert.equal(keys.value,S);
 // AutoFill replaces the value: what it filled is text, never a run of Backspaces.
 type('pw-filled',{inputType:'insertReplacementText'});await tick();
 assert.deepEqual(sent(),[{type:'text',text:'pw-filled'}]);
 // ...intact, even when it starts with the sentinel's character or equals the sentinel.
 type('_secret',{inputType:'insertReplacementText'});type(S,{inputType:'insertReplacementText'});await tick();
 assert.equal(sent().map(p=>p.text).join('|'),'_secret|'+S);
 // Safari's AutoFill input event carries no inputType: also the whole value.
 type('__init',{});await tick();assert.deepEqual(sent(),[{type:'text',text:'__init'}]);assert.equal(keys.value,S);
 // Typed underscores are text like any other.
 type(S+'_',{inputType:'insertText'});await tick();assert.deepEqual(sent(),[{type:'text',text:'_'}]);
 // Return is the window's Enter, not a form submit here.
 let prevented=false;keys.fire('keydown',{key:'Enter',preventDefault(){prevented=true;}});await tick();
 assert(prevented);assert.deepEqual(sent(),[{type:'key',key:'Enter'}]);
 // A password field (or a frame it cannot see into) gets a password input.
 frameAnswer.body={...frameAnswer.body,focus:'password'};await ctx.frame();
 assert.equal(keys.type,'password');assert.equal(keys.attrs.autocomplete,'current-password');assert.equal(active,keys);
 frameAnswer.body={...frameAnswer.body,focus:'frame'};frameAnswer.body.jpeg='BBBB';await ctx.frame();assert.equal(keys.type,'password');
 // Nothing editable has focus: the keyboard closes.
 frameAnswer.body={...frameAnswer.body,focus:null};await ctx.frame();assert.equal(active,null,'blurred');
 // A frame fetched before the tap was pressed never decides: it still shows the focus from before.
 let release;holds.push({type:'tap',wait:new Promise(r=>{release=r;})});
 tap(50,60);assert.equal(active,keys);await settle();
 await ctx.frame();assert.equal(active,keys,'a stale frame does not close the keyboard');
 frameAnswer.body={...frameAnswer.body,focus:'text'};release();await tick();
 assert.equal(active,keys);assert.equal(keys.type,'text');sent();
 // A new tap after a text field starts as a password input: the new field may be the password,
 // and what is typed before a post-tap frame arrives must not go through a text input.
 let release2;holds.push({type:'tap',wait:new Promise(r=>{release2=r;})});
 tap(195,211);assert.equal(keys.type,'password','password until a post-tap frame says text');
 assert.equal(keys.attrs.autocomplete,'current-password');await settle();
 await ctx.frame();assert.equal(keys.type,'password','a stale text frame does not switch it back');
 release2();await tick();assert.equal(keys.type,'text','a post-tap frame confirming text switches it');sent();
 // Two quick taps (ID, then password): the first one's press must not reopen the gate for a frame of its field.
 let first,second;holds.push({type:'tap',wait:new Promise(r=>{first=r;})},{type:'tap',wait:new Promise(r=>{second=r;})});
 tap(195,211);tap(195,422);assert.equal(keys.type,'password');await settle();
 first();await settle();assert.equal(keys.type,'password');
 await ctx.frame();assert.equal(keys.type,'password','a frame after the first tap only does not decide');
 second();await tick();assert.equal(keys.type,'text','the latest tap pressed: its frame decides');
 assert.deepEqual(sent().filter(p=>p.type==='tap'),[{type:'tap',x:195,y:211},{type:'tap',x:195,y:422}],'no seq leaves the page');
 // Enter that confirms an IME candidate stays with the IME: no early text, no Enter to the window.
 keys.fire('compositionstart');type(S+'にほん',{isComposing:true});
 let kept=true;keys.fire('keydown',{key:'Enter',isComposing:true,keyCode:229,preventDefault(){kept=false;}});await tick();
 assert(kept,'the IME keeps its Enter');assert.deepEqual(sent(),[]);
 type(S+'日本',{isComposing:true});keys.fire('compositionend');await tick();
 assert.deepEqual(sent(),[{type:'text',text:'日本'}]);
 // A paste over the limit goes in bounded pieces; a refused input says so on the page.
 type(S+'x'.repeat(600),{inputType:'insertFromPaste'});await tick();
 assert.deepEqual(sent().map(p=>p.text.length),[512,88]);
 // An astral character across the boundary stays whole: no chunk ends or starts inside a surrogate pair.
 const lone=/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/;
 type(S+'x'.repeat(511)+'😀'+'y',{inputType:'insertFromPaste'});await tick();
 const chunks=sent().map(p=>p.text);assert.equal(chunks.join(''),'x'.repeat(511)+'😀'+'y');
 assert(chunks.every(c=>!lone.test(c)&&c.length<=512),'whole code points only');assert.deepEqual(chunks.map(c=>c.length),[511,3]);
 assert($('typing-failed').classList.contains('hidden'));
 inputAnswer=()=>({ok:false});type(S+'z',{inputType:'insertText'});await tick();
 assert(!$('typing-failed').classList.contains('hidden'),'the owner is told the text did not go in');
 inputAnswer=()=>({ok:true});type(S+'y',{inputType:'insertText'});await tick();
 assert($('typing-failed').classList.contains('hidden'));
 // The buttons share the same queue.
 sent();$('tab').onclick();$('back').onclick();$('enter').onclick();await tick();
 assert.deepEqual(sent(),[{type:'key',key:'Tab'},{type:'key',key:'Backspace'},{type:'key',key:'Enter'}]);
 console.log('ok');
})().catch(error=>{console.error(error);process.exit(1);});
"""


class PhonePageTyping(unittest.TestCase):
    """#293: typing on the phone keyboard goes straight into the window; run in Node against a fake DOM."""

    def test_the_page_sends_each_keystroke_in_order_and_follows_the_window_focus(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node is needed to run the phone page script')

        class Session:
            site, code, expires = 'shop.test', 'c0de', 1000.0

            @staticmethod
            def clock():
                return 400.0
        html = remote_login.page(Session(), 'n0nce')
        script = html.split('<script nonce="n0nce">')[1].split('</script>')[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'page.js'
            path.write_text(script, encoding='utf-8')
            result = subprocess.run([node, '-e', PAGE_TYPING_CHECKS, str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('ok', result.stdout)

    def test_the_separate_text_box_is_gone_and_autofill_stays_folded(self):
        class Session:
            site, code, expires = 'shop.test', 'c0de', 1000.0

            @staticmethod
            def clock():
                return 400.0
        html = remote_login.page(Session(), 'n0nce')
        self.assertNotIn('id="text"', html)
        self.assertNotIn('id="send"', html)
        self.assertIn('<input id="keys" type="password"', html, 'a password input until the window says plain text')
        self.assertIn('<details id="autofill">', html)
