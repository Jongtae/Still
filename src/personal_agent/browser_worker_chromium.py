"""Linux desktop Chromium worker for Still's JSON-lines browser protocol (#34).

Run with ``python -P -m personal_agent.browser_worker_chromium --profile ID
--chromium /pinned/chrome``. The main thread owns Playwright; stdin only queues
commands. No browser installation, persistent profile, permission grant, download,
or cookie file is created by this module. Chromium itself uses Playwright's
throwaway process directory; non-persistent context data is not a durable jar.

Reuse review (Adapt): Playwright's official Python sync API (requested version
1.64.1, Apache-2.0) owns launch, context and page lifetimes. CDP Page, Runtime,
Input, Fetch and Target supply isolated worlds, trusted input and redirect-hop
interception, including out-of-process frames. Context routing holds a popup's
first request in owner-login mode while Target discovery installs its CDP guard.
AI new windows instead adapt the macOS main-view policy using CDP windowOpen,
Target.closeTarget and Page.navigate; no popup is initialized or promoted. CDP
Page.pdl and Chromium FormSubmission::NotifyInspector / Frame::ScheduleFormSubmission
are the reference for event/method ordering. Playwright routing alone cannot
prevent initial blank-document execution or recover POST bodies. The adapter's
policy enforcement and bounded recovery are Still-owned glue (Build); no external
SDK can choose Still's authority policy. Existing Playwright/CDP APIs suffice,
with unchanged Apache-2.0/BSD provenance, platform fit, egress and dependency set.
No dependency or authority is added.
#640 reuse review (Adapt): CDP Page.navigate.loaderId, Page.lifecycleEvent and
Network.requestWillBeSent.loaderId supply document identity; Still only correlates
its pending reply. Xlib XSetInputFocus (X.Org's maintained platform API, MIT-style
licence) supplies native focus release via stdlib ctypes, without copied code or
an added package. Playwright/CDP have no native blur API; a second automation
framework would still need X11. The new native effect is limited to relinquishing
desktop keyboard focus when parking; data, network and guard policy are unchanged.
References: https://chromedevtools.github.io/devtools-protocol/tot/Page/
and https://www.x.org/releases/current/doc/libX11/libX11/libX11.html.
The stdlib queue/heapq provide the main-thread handoff and timer queue. Selenium
would still need the same CDP boundary; a second automation framework adds no
required API. Still's browser_worker.Worker supplies the existing protocol,
script constants, approval/press/submit state machine and bounded read loop;
browser_session.registrable_domain uses the installed publicsuffixlist's bundled
PSL (MIT), with no network update. No engine code or PSL implementation is copied.
The parent owns executable provenance/digest verification and dependency locking.
This adapter removes an engine/platform blocker for the owner's chosen AI; it
contains no task or destination-specific behavior.

Platform differences: Linux returns no QR links (Vision is macOS-only, and no
pure-Python decoder is a dependency). UA/platform identify Chromium on Linux or
Android, never Safari/iPhone. CDP emulates the phone viewport and scales its
rendering rather than using WKWebView.pageZoom. Chromium has no cancel-window-
close API: a closed owner window emits hidden and a fresh parked page is created
in the same in-memory context; cookies/storage survive, the closed DOM does not.
Owner dialogs remain pending for Chromium's native UI or remote_dialog;
there is no AppKit dialog sheet. Browser process crash
is a process failure, never a successful hide.
"""
import argparse
import contextlib
import ctypes
import heapq
import itertools
import json
import math
import os
import queue
import sys
import threading
import time
from urllib.parse import urlsplit

from . import browser_worker as shared
from .browser_session import registrable_domain

WORLD = 'still'

BINDING = '__still_guard_message'
PAGE_FILTER = [{'type': 'page'}, {'exclude': True}]
FRAME_FILTER = [{'type': 'iframe'}, {'exclude': True}]
#: Chromium's own launch-failure text when its sandbox cannot start (lower-cased).  'sandboxing failed'
#: is what Chrome for Testing 156 prints where user namespaces are refused (observed, #34).
SANDBOX_FAILURE_MARKERS = ('sandboxing failed', 'no usable sandbox', 'sandbox_host_linux', 'suid sandbox',
                           'running as root without --no-sandbox', 'failed to move to new namespace')
#: How long a paused form submission waits for the guard's own record of it (#34): the guard's message
#: is sent at the submit event, before the navigation, but may reach the worker after the paused request.
VETTED_WAIT_SECONDS = 0.5
#: The shared guard keeps an ordinary form's pass this long (``ORDINARY_MS`` in ``PRELUDE``).
ORDINARY_VETTED_SECONDS = 2.0
#: Chromium-only, in the isolated world after the shared guard: every submit the guard lets through
#: (``state().vetted``) is also told to the worker, because Chromium runs no page script while a form's
#: request is paused (observed, #34), so the shared ``FORM_NAVIGATION_SCRIPT`` cannot be asked then.
VETTED_SHIM = r"""(() => { const s = window.__agentos; if (!s) return;
  let list = s.vetted || [];
  const tell = (kind, entry) => { try { %(binding)s(JSON.stringify({[kind]: {method: String(entry.method || ''),
    action: String(entry.action || ''), payment: !!entry.payment}})); } catch (error) { /* the page check decides */ } };
  Object.defineProperty(s, 'vetted', {configurable: true, get: () => list, set: (value) => {
    const next = Array.isArray(value) ? value : [];
    // Withdraw first so replacing an entry with the same destination keeps the new pass.
    for (const entry of list) if (!next.includes(entry)) tell('unvetted', entry);
    for (const entry of next) if (!list.includes(entry)) tell('vetted', entry);
    list = next; }}); })();"""


def bare_url(value):
    """``origin + pathname`` of an http(s) URL as the guard's ``bare()`` reads it; '' otherwise."""
    try:
        parts = urlsplit(str(value or ''))
        port = parts.port
    except ValueError:
        return ''
    scheme, host = parts.scheme.lower(), (parts.hostname or '').lower()
    if scheme not in ('http', 'https') or not host:
        return ''
    if ':' in host:
        host = '[' + host + ']'
    default = {'http': 80, 'https': 443}[scheme]
    return f'{scheme}://{host}' + (f':{port}' if port and port != default else '') + (parts.path or '/')


LAUNCH_ARGS = [
    '--ozone-platform=x11', '--class=StillBrowser',
    f'--window-position={shared.PARK_ORIGIN},{shared.PARK_ORIGIN}',
    '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
    '--disable-background-timer-throttling', '--autoplay-policy=user-gesture-required',
    '--disable-dev-shm-usage', '--disable-crash-reporter',
]


#: The X11 window class of this worker's Chromium windows (``--class``), so focus is only ever taken
#: back from Still's own browser, never from the owner's other applications.
WINDOW_CLASS = 'StillBrowser'


class _ClassHint(ctypes.Structure):
    _fields_ = [('res_name', ctypes.c_void_p), ('res_class', ctypes.c_void_p)]


def relinquish_keyboard_focus():
    """Give the desktop keyboard back when a parked Still window holds it (Codex review on #640).

    Adopted: Xlib (X.Org, MIT) through stdlib ctypes; libX11 is what the X11 Chromium launch already
    needs.  Only when the focused window, or one of its ancestors, has ``WINDOW_CLASS`` is the focus
    set to PointerRoot (keys go to the window under the pointer, outside the off-screen window);
    otherwise the owner's focus is left alone.  Best effort: any failure leaves focus unchanged.
    Browser.Bounds has no focus property, a DOM blur does not release native focus, and minimizing
    would stop requestAnimationFrame for the parked page.
    """
    try:
        x11 = ctypes.CDLL('libX11.so.6')
    except OSError:
        return False
    x11.XOpenDisplay.argtypes, x11.XOpenDisplay.restype = [ctypes.c_char_p], ctypes.c_void_p
    x11.XGetInputFocus.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int)]
    x11.XGetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_ClassHint)]
    x11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                               ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p),
                               ctypes.POINTER(ctypes.c_uint)]
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(None)
    if not display:
        return False
    try:
        window, revert = ctypes.c_ulong(), ctypes.c_int()
        x11.XGetInputFocus(display, ctypes.byref(window), ctypes.byref(revert))
        current, ours = window.value, False
        for _ in range(32):
            if current in (0, 1):   # None, PointerRoot
                break
            hint = _ClassHint()
            if x11.XGetClassHint(display, current, ctypes.byref(hint)):
                names = [ctypes.string_at(p).decode('latin-1') for p in (hint.res_name, hint.res_class) if p]
                for pointer in (hint.res_name, hint.res_class):
                    if pointer:
                        x11.XFree(pointer)
                if WINDOW_CLASS in names:
                    ours = True
                    break
            root, parent, children, count = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p(), ctypes.c_uint()
            if not x11.XQueryTree(display, current, ctypes.byref(root), ctypes.byref(parent),
                                  ctypes.byref(children), ctypes.byref(count)):
                break
            if children.value:
                x11.XFree(children)
            if parent.value in (0, root.value):
                break
            current = parent.value
        if ours:
            # PointerRoot=1, RevertToPointerRoot=1, CurrentTime=0 (X11/X.h).
            x11.XSetInputFocus(display, 1, 1, 0)
            x11.XSync(display, False)
        return ours
    except Exception:
        return False
    finally:
        x11.XCloseDisplay(display)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='personal_agent.browser_worker_chromium')
    parser.add_argument('--profile', default='default')
    parser.add_argument('--chromium', required=True)
    parser.add_argument('--test-allow-origin', action='append', default=[])
    return parser.parse_args(argv)


def user_agent_for(layout, version):
    """Engine-honest UA, with the launched browser's version, not an OS guess."""
    device = 'Linux; Android 13; Mobile' if layout == 'mobile' else 'X11; Linux x86_64'
    return (f'Mozilla/5.0 ({device}) AppleWebKit/537.36 (KHTML, like Gecko) '
            f'Chrome/{version} ' + ('Mobile ' if layout == 'mobile' else '') +
            f'Safari/537.36 {shared.EMBEDDED_UA_TOKEN}')


def cookie_row(cookie):
    expires = cookie.get('expires', -1)
    return {'name': str(cookie['name']), 'value': str(cookie['value']), 'domain': str(cookie['domain']),
            'path': str(cookie.get('path') or '/'),
            'expires': float(expires) if isinstance(expires, (int, float)) and expires > 0 else None,
            'secure': bool(cookie.get('secure')), 'http_only': bool(cookie.get('httpOnly')),
            'same_site': cookie.get('sameSite') or None}


def cookie_object(row, now=None):
    """Translate a jar row, dropping unusable/expired rows as the WebKit worker does."""
    try:
        cookie = {key: str(row[key]) for key in ('name', 'value', 'domain')}
        if not cookie['name'] or not cookie['domain']:
            return None
        expires = row.get('expires')
        if isinstance(expires, (int, float)):
            if not math.isfinite(expires) or expires <= (time.time() if now is None else now):
                return None
            cookie['expires'] = float(expires)
        cookie.update(path=str(row.get('path') or '/'), secure=bool(row.get('secure')),
                      httpOnly=bool(row.get('http_only')))
        same_site = str(row.get('same_site') or '').lower()
        if same_site in ('strict', 'lax', 'none'):
            cookie['sameSite'] = same_site.capitalize()
        return cookie
    except (KeyError, TypeError, ValueError):
        return None


def navigation_error_code(error):
    text = str(error)
    if 'ERR_NAME_NOT_RESOLVED' in text or 'ERR_NAME_RESOLUTION_FAILED' in text:
        return 'host_not_found'
    if any(token in text for token in ('ERR_CONNECTION_', 'ERR_TIMED_OUT', 'ERR_ADDRESS_UNREACHABLE',
                                       'ERR_INTERNET_DISCONNECTED', 'ERR_CERT_', 'ERR_SSL_')):
        return 'host_unreachable'
    return 'navigation_failed'


class _Timers:
    """AppHelper's three scheduling operations, backed by a main-thread heap."""
    def __init__(self):
        self.items = []
        self.incoming = queue.SimpleQueue()
        self.ids = itertools.count()
        self.running = True

    def callAfter(self, fn, *args):
        # DNS completion is the only producer outside the main thread.
        self.incoming.put((fn, args))

    def callLater(self, seconds, fn, *args):
        heapq.heappush(self.items, (time.monotonic() + seconds, next(self.ids), fn, args))

    def stopEventLoop(self):
        self.running = False

    def pump(self):
        while not self.incoming.empty():
            fn, args = self.incoming.get()
            self.callLater(0, fn, *args)
        # Callbacks scheduled by a callback run on the next pump (no starvation).
        due = []
        now = time.monotonic()
        while self.items and self.items[0][0] <= now:
            due.append(heapq.heappop(self.items))
        for _, _, fn, args in due:
            fn(*args)


class _Target:
    """CDP Target.sendMessageToTarget envelope over Playwright's public CDPSession.

    Context routing holds new pages' first requests; related-target attachment
    pauses OOPIFs. A non-flattened session envelope
    provides asynchronous commands, so awaitPromise never
    blocks the stdin loop or a deadline, including while a dialog is held.
    This is only CDP framing; Playwright owns the actual transport.
    """
    def __init__(self, worker, parent, session_id, info):
        self.worker, self.parent, self.session_id = worker, parent, session_id
        self.ident, self.kind = info['targetId'], info['type']
        self.contexts, self.callbacks, self.scripts = {}, {}, []
        self.main_frame = None
        self.ready = False
        self.disposed = False
        #: Fetch interception acknowledged (browser-side): this target's redirect hops are checked from now on.
        self.fetch_ready = False
        self.initializing, self.setup_pending = True, set()
        self.ids = itertools.count(1)

    def post(self, method, params=None, done=None):
        if self.disposed:
            if done is not None:
                done({}, {'code': -1})
            return
        ident = next(self.ids)
        if self.initializing:
            self.setup_pending.add(ident)
            original_done = done

            def setup_done(result, error):
                self.setup_pending.discard(ident)
                if error:
                    self.worker.target_fault(self)
                elif original_done is not None:
                    original_done(result, error)
                self.finish_setup()
            done = setup_done
        if done is not None:
            self.callbacks[ident] = done
        packet = {'id': ident, 'method': method, 'params': params or {}}
        envelope = {'sessionId': self.session_id, 'message': json.dumps(packet, ensure_ascii=True)}
        if isinstance(self.parent, _Target):
            self.parent.post('Target.sendMessageToTarget', envelope)
        else:
            self.parent.send('Target.sendMessageToTarget', envelope)

    def finish_setup(self):
        if not self.disposed and not self.initializing and not self.setup_pending and not self.worker.fatal:
            self.ready = True

    def receive(self, message):
        if self.disposed:
            return
        if 'id' in message:
            callback = self.callbacks.pop(message['id'], None)
            if callback is not None:
                callback(message.get('result', {}), message.get('error'))
            elif 'error' in message:
                self.worker.target_fault(self)
            return
        method, params = message.get('method'), message.get('params', {})
        if method == 'Target.attachedToTarget':
            self.worker.attached(self, params)
        elif method == 'Target.receivedMessageFromTarget':
            self.worker.target_message(params)
        elif method == 'Target.detachedFromTarget':
            self.worker.detached(params)
        elif method == 'Runtime.executionContextCreated':
            context = params['context']
            if context.get('name') == WORLD:
                self.contexts[context['auxData']['frameId']] = context['id']
        elif method == 'Runtime.executionContextDestroyed':
            if self is self.worker.main_target and params.get('executionContextId') == self.contexts.get(self.main_frame):
                self.worker.clear_vetted()
            self.contexts = {k: v for k, v in self.contexts.items() if v != params.get('executionContextId')}
        elif method == 'Runtime.executionContextsCleared':
            if self is self.worker.main_target:
                self.worker.clear_vetted()
            self.contexts.clear()
        elif method == 'Runtime.bindingCalled':
            self.worker.binding_called(self, params)
        elif method == 'Fetch.requestPaused':
            self.worker.fetch_paused(self, params)
        elif method == 'Page.windowOpen':
            self.worker.window_open(self, params)
        elif method == 'Page.frameRequestedNavigation':
            self.worker.frame_requested(self, params)
        elif method == 'Page.frameNavigated':
            self.worker.frame_navigated(self, params['frame'])
        elif method == 'Page.frameDetached':
            frame_id = params['frameId']
            self.contexts.pop(frame_id, None)
            self.worker.loading.discard((self.ident, frame_id))
            self.worker.frame_origins.pop((self.ident, frame_id), None)
            self.worker.frame_requests.pop((self.ident, frame_id), None)
        elif method == 'Page.frameStartedLoading':
            self.worker.loading.add((self.ident, params['frameId']))
        elif method == 'Page.frameStoppedLoading':
            self.worker.loading.discard((self.ident, params['frameId']))
        elif method == 'Network.requestWillBeSent':
            # Only the main frame's document request is a navigation; its sub-resources share the frame id
            # (Codex review on #640).
            if self is self.worker.main_target and params.get('frameId') == self.main_frame \
                    and params.get('type') == 'Document':
                self.worker.main_requests[params['requestId']] = params.get('loaderId')
        elif method == 'Network.loadingFinished':
            self.worker.main_requests.pop(params.get('requestId'), None)
        elif method == 'Network.loadingFailed':
            self.worker.network_failed(self, params)
        elif method == 'Page.javascriptDialogClosed':
            self.worker.native_dialog_closed(self, params)
        elif method == 'Page.lifecycleEvent' and params.get('name') == 'load':
            self.worker.loaded(self, params)


class _View:
    """Only the engine-neutral worker's loading/stop/zoom queries, not a WebKit emulator."""
    def __init__(self, worker):
        self.worker = worker

    def isLoading(self):
        return any(target == self.worker.main_target.ident for target, _ in self.worker.loading)

    def stopLoading(self):
        self.worker.main_target.post('Page.stopLoading')

    def pageZoom(self):
        return self.worker.zoom


class Worker(shared.Worker):
    """Reuse the protocol/approval/read state machine; override engine operations."""
    def __init__(self, profile, emit, playwright, chromium, allowed_origins=()):
        self.profile, self.emit = profile, emit
        self.allowed_origins = frozenset(allowed_origins)
        self.AppHelper = _Timers()
        self.pending, self.expiry = {}, {}
        self.navigation = None
        self.blocked = self.refused_submits = self.step_refused = self.posted_submits = self.step_posted = 0
        self.reported_refused = self.main_navigations = self.landed = self.deciding = self.read_navigations = 0
        self.blocked_reason = None
        self.hosts, self.origins, self.loading, self.stopping = set(), set(), set(), set()
        self.main_requests = {}
        # Lifecycle/network events can arrive before the Page.navigate reply.
        self.navigation_events = {}
        self.reading = False
        self.document_generation, self.vetted = 0, []
        self.resolved, self.frame_requests, self.frame_origins = {}, {}, {}
        self.intranet = frozenset()
        self.popups, self.reported = [], []
        self.guard_off = self.owner_visible = self.remote = False
        self.cancelled = self.held = self.press_seen = self.dialog_step = self.held_dialog = None
        self.last_frame = self.qr_cache = self.mobile_restore = self.phone_fit = None
        self.layout, self.zoom = 'mobile', 1.0
        self.viewport_size = {'width': int(shared.MOBILE_SIZE[0]), 'height': int(shared.MOBILE_SIZE[1])}
        self.targets, self.sessions, self.pages, self.clients = {}, {}, {}, {}
        self.main_target = self.page = None
        self.closing = False
        self.fatal = False
        self.browser = playwright.chromium.launch(executable_path=chromium, headless=False,
                                                  chromium_sandbox=True, args=list(LAUNCH_ARGS))
        self.version = self.browser.version
        self.context = self.browser.new_context(
            accept_downloads=False, service_workers='block', permissions=[],
            viewport={'width': int(shared.MOBILE_SIZE[0]), 'height': int(shared.MOBILE_SIZE[1])},
            # #34: WKWebView parity: a phone-sized window lays pages out at its own width, so no mobile
            # viewport semantics (a page without a viewport tag would get a 980px layout instead).
            user_agent=user_agent_for('mobile', self.version), is_mobile=False, has_touch=False,
            device_scale_factor=1)
        self.context.on('page', self.page_created)
        # #34: a popup's first request, before this worker's CDP session on it exists (``route_popup``).
        self.context.route('**/*', self.route_popup)
        self.root_cdp = self.browser.new_browser_cdp_session()
        self.root_cdp.on('Target.targetCreated', self.target_created)
        self.root_cdp.on('Target.receivedMessageFromTarget', self.target_message)
        self.root_cdp.on('Target.detachedFromTarget', self.detached)
        # Do not compete with Playwright's auto-attach/debugger resume. A popup
        # may not have a public Page until its first navigation commits, so a
        # per-page public CDPSession cannot install the guard at this boundary.
        self.root_cdp.send('Target.setDiscoverTargets', {'discover': True, 'filter': PAGE_FILTER})
        self.page = self.context.new_page()
        self.main_target = self.target_for(self.page)
        self.view = _View(self)
        self.world = WORLD
        self.park()

    def target_for(self, page):
        client = self.clients.get(page)
        if client is None:
            client = self.context.new_cdp_session(page)
            self.clients[page] = client
        info = client.send('Target.getTargetInfo')['targetInfo']
        ident = info['targetId']
        if ident not in self.targets:
            self.target_created({'targetInfo': info})
        self.pages[ident] = page
        return self.targets[ident]

    def page_created(self, page):
        page.on('dialog', lambda dialog: self.dialog(dialog, page))
        page.on('close', lambda: self.page_closed(page))
        page.on('download', lambda download: download.cancel())
        # Initialization events arrive before Playwright reports the page.
        self.AppHelper.callAfter(self.register_page, page)

    def register_page(self, page):
        if not page.is_closed():
            if page is not self.page and self.page is not None and not self.owner_popups():
                page.close(run_before_unload=False)
                return
            self.target_for(page)
            if self.page is not None and page is not self.page and self.owner_popups() and page not in self.popups:
                self.popups.append(page)

    def target_created(self, event):
        info = event['targetInfo']
        if info['type'] != 'page' or info['targetId'] in self.targets:
            return
        if self.main_target is not None and not getattr(self, 'creating_page', False) and not self.owner_popups():
            self.root_cdp.send('Target.closeTarget', {'targetId': info['targetId']})
            return
        session_id = self.root_cdp.send('Target.attachToTarget', {
            'targetId': info['targetId'], 'flatten': False})['sessionId']
        self.attached(self.root_cdp, {'sessionId': session_id, 'targetInfo': info})

    def attached(self, parent, event):
        target = _Target(self, parent, event['sessionId'], event['targetInfo'])
        self.sessions[target.session_id] = self.targets[target.ident] = target
        page_target = target
        while page_target.kind == 'iframe' and isinstance(page_target.parent, _Target):
            page_target = page_target.parent
        if page_target is not target and page_target is not self.main_target and not self.owner_popups():
            self.root_cdp.send('Target.closeTarget', {'targetId': page_target.ident})
            return
        if target.kind == 'page' and self.main_target is not None and not getattr(self, 'creating_page', False):
            if not self.owner_popups():
                self.root_cdp.send('Target.closeTarget', {'targetId': target.ident})
                return
            target.popup_navigation = True
        target.post('Runtime.enable')
        target.post('Page.enable')
        target.post('Page.setLifecycleEventsEnabled', {'enabled': True})
        target.post('Network.enable')
        target.post('Page.getFrameTree', done=lambda result, error: setattr(
            target, 'main_frame', result.get('frameTree', {}).get('frame', {}).get('id')))
        target.post('Runtime.addBinding', {'name': BINDING, 'executionContextName': WORLD})
        self.install_scripts(target)
        # Every redirect has another requestPaused event, unlike context.route.
        target.post('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request',
                                                  'resourceType': 'Document'}]},
                    done=lambda result, error: setattr(target, 'fetch_ready', not error))
        target.post('Target.setAutoAttach', {'autoAttach': True, 'waitForDebuggerOnStart': True,
                                            'flatten': False, 'filter': FRAME_FILTER})
        self.emulate(target)

        target.post('Runtime.runIfWaitingForDebugger')
        target.initializing = False
        target.finish_setup()

    def target_message(self, event):
        target = self.sessions.get(event.get('sessionId'))
        if target is not None:
            try:
                target.receive(json.loads(event['message']))
            except Exception as exc:
                self.delegate_failed('cdp_event', exc, False)
                self.target_fault(target)

    def detached(self, event):
        target = self.sessions.pop(event.get('sessionId'), None)
        if target is not None:
            target.disposed = True
            target.ready = False
            self.targets.pop(target.ident, None)
            self.loading = {key for key in self.loading if key[0] != target.ident}
            self.frame_origins = {key: value for key, value in self.frame_origins.items() if key[0] != target.ident}
            if not self.closing:
                for done in list(target.callbacks.values()):
                    done({}, {'code': -1})
            target.callbacks.clear()

    def target_fault(self, target):
        if self.closing or target.disposed:
            return
        # A missing security domain/script is not permission to resume unguarded.
        self.fatal = True
        self.root_cdp.send('Target.closeTarget', {'targetId': target.ident})

    def install_scripts(self, target):
        for ident in target.scripts:
            target.post('Page.removeScriptToEvaluateOnNewDocument', {'identifier': ident})
        target.scripts = []
        shim = ('window.webkit = {messageHandlers: {' + json.dumps(shared.GUARD_HANDLER) +
                ': {postMessage: m => ' + BINDING + '(JSON.stringify(m))}}};\n')
        sources = [(shim + shared.client_guard_user_script(self.guard_off) + '\n' +
                    VETTED_SHIM % {'binding': BINDING}, WORLD, True)]
        if not self.guard_off:
            sources.append((shared.PAGE_WRAP_USER_SCRIPT, None, True))
        # Adapt the platform shim rather than claiming the macOS phone's platform.
        platform_name = 'Linux armv8l' if self.layout == 'mobile' else 'Linux x86_64'
        sources.append((shared.PHONE_PLATFORM_USER_SCRIPT.replace('iPhone', platform_name), None, False))
        for source, world, main_only in sources:
            if main_only:
                source = 'if (window === window.top) {\n' + source + '\n}'
            params = {'source': source, 'runImmediately': True}
            if world:
                params['worldName'] = world
            target.post('Page.addScriptToEvaluateOnNewDocument', params,
                        lambda result, error, t=target: t.scripts.append(result['identifier']) if not error else self.target_fault(t))

    def _install_guard_scripts(self):
        for target in list(self.targets.values()):
            self.install_scripts(target)

    def binding_called(self, target, event):
        if event.get('name') != BINDING or target is not self.main_target:
            return
        if event.get('executionContextId') != target.contexts.get(target.main_frame):
            return
        try:
            record = json.loads(event.get('payload', ''))
        except (ValueError, TypeError):
            return
        if isinstance(record, dict) and isinstance(record.get('vetted') or record.get('unvetted'), dict):
            return self.note_vetted(record)
        if shared._press_record(record) is not None:
            self.receive_press(record)
        else:
            self.receive_cancelled(record)

    def clear_vetted(self):
        # Invalidate both cached passes and form decisions still waiting on this document.
        self.document_generation += 1
        self.vetted = []

    def note_vetted(self, record):
        """Keep (or withdraw) the guard's record of a submit it let through, for the paused request (#34)."""
        withdrawn = 'unvetted' in record
        entry = record.get('unvetted') if withdrawn else record['vetted']
        key = (str(entry.get('method') or '').lower(), bare_url(entry.get('action')), bool(entry.get('payment')))
        now = time.monotonic()
        keep = shared.VETTED_SECONDS if key[2] else ORDINARY_VETTED_SECONDS
        self.vetted = [(k, t, doc) for k, t, doc in self.vetted if doc == self.document_generation and
                       now - t <= shared.VETTED_SECONDS and
                       not (withdrawn and k == key)]
        if not withdrawn and key[1]:
            self.vetted = (self.vetted + [(key, now, self.document_generation)])[-8:]
        self.vetted_keep = keep

    def take_vetted(self, method, url):
        """Whether the guard let this submission through just before; a payment pass is used once."""
        verb, place, now = str(method or 'get').lower(), bare_url(url), time.monotonic()
        for index, ((entry_verb, entry_place, payment), at, doc) in enumerate(self.vetted):
            limit = shared.VETTED_SECONDS if payment else ORDINARY_VETTED_SECONDS
            if doc == self.document_generation and entry_verb == verb and entry_place == place and place and now - at <= limit:
                if payment:
                    del self.vetted[index]
                return True
        return False

    def check_form_navigation(self, url, method, then, into_frame=False):
        """A paused form submission (#34; the shared check is ``browser_worker.Worker.check_form_navigation``).

        Chromium runs no page script while the submission's request is paused, so the page cannot be
        asked then.  A submission the guard let through (its own record, ``VETTED_SHIM``) proceeds to the
        destination check.  Any other is refused at once (nothing is sent) and the page is asked
        afterwards which payment form it was: that one is held for the owner's approval as on macOS,
        anything else is a typed ``submit_refused``.  Fail closed: a non-payment submission the guard
        could not see (a native submit from another realm) is refused here, where macOS lets it through.
        """
        began = time.monotonic()
        document = self.document_generation
        self.deciding += 1   # a click waits for this decision (#736)

        def look():
            if document != self.document_generation:
                self.deciding -= 1
                return then(False)
            if self.take_vetted(method, url):
                self.deciding -= 1
                return then(True)
            if time.monotonic() - began < VETTED_WAIT_SECONDS:
                return self.AppHelper.callLater(.02, look)
            then(False)   # aborted: nothing was sent; the page answers now that it runs again
            answered = []

            def answer(record=None):
                if answered:
                    return
                answered.append(True)
                self.deciding -= 1
                if isinstance(record, dict):
                    self.receive_cancelled(record)   # held: an approval of it can release it
                else:
                    self.refused_submits += 1

            def done(value, error):
                held = value.get('cancelled') if error is None and isinstance(value, dict) else None
                answer(held if isinstance(held, dict) else None)
            try:
                self.run(shared.FORM_NAVIGATION_SCRIPT,
                         {'url': url, 'method': str(method or 'GET'), 'frame': into_frame is True}, done)
            except Exception:
                answer()
                raise
            self.AppHelper.callLater(shared.FORM_CHECK_SECONDS, answer)
        look()

    def run(self, body, arguments, done, world=None, view=None):
        page = view if view is not None and view is not self.view else self.page
        target = self.main_target if page is self.page else self.target_for(page)
        args = arguments or {}

        def evaluated(result, error):
            try:
                if error or result.get('exceptionDetails'):
                    return done(None, 'script_failed')
                value = result.get('result', {}).get('value')
                decoded = json.loads(value) if value is not None else None
            except (ValueError, TypeError, UnicodeError):
                return done(None, 'script_failed')
            done(decoded, None)

        def execute(context_id):
            target.post('Runtime.callFunctionOn', {
                'functionDeclaration': 'async function(' + ','.join(args) + ') {\n' + body + '\n}',
                'executionContextId': context_id, 'arguments': [{'value': v} for v in args.values()],
                'awaitPromise': True, 'returnByValue': True}, evaluated)
        context_id = target.contexts.get(target.main_frame)
        if context_id is not None:
            return execute(context_id)

        def created(result, error):
            if error or 'executionContextId' not in result:
                return done(None, 'script_failed')
            context_id = result['executionContextId']
            target.contexts[target.main_frame] = context_id
            execute(context_id)
        target.post('Page.createIsolatedWorld', {'frameId': target.main_frame, 'worldName': WORLD}, created)

    def deadline(self, ident, seconds, on_timeout=None, observed=None):
        # Bound even the shared settle/load_more soft caps by the caller's deadline.
        cap = self.expiry.get(ident, time.monotonic() + max(.5, float(seconds)))
        self.pending[ident] = True

        def expire():
            if ident not in self.pending:
                return
            fields = {}
            if observed:
                try:
                    fields = observed()
                except Exception:
                    pass
            if on_timeout:
                try:
                    on_timeout()
                except Exception:
                    pass
            self.fail(ident, 'timeout', **fields)
        self.AppHelper.callLater(max(0, cap - time.monotonic()), expire)

    def reply(self, ident, ok=True, **fields):
        expiry = self.expiry.pop(ident, None)
        if expiry is not None and time.monotonic() >= expiry and fields.get('error') != 'timeout':
            fields = {'error': 'timeout', **(self.step_posted_field() if self.dialog_step is not None else {})}
            if self.dialog_step is not None:
                try:
                    self.end_step()
                except Exception:
                    self.dialog_step = None
            ok = False
        super().reply(ident, ok, **fields)

    def handle(self, command):
        ident = command.get('id')
        try:
            self.expiry[ident] = time.monotonic() + max(.5, float(command.get('timeout') or 20))
            super().handle(command)
        except Exception:
            self.fail(ident, 'worker_error')

    def request_paused(self, target, event, release=None, refuse=None):
        """Check *each* Document request, including redirectedRequestId hops.

        ``release``/``refuse`` answer a request held elsewhere (a popup's first request, held by
        ``route_popup``); by default the request is the one this target's Fetch paused.
        """
        request = event['request']
        release = release or (lambda: target.post('Fetch.continueRequest', {'requestId': event['requestId']}))
        # 'Aborted' (net::ERR_ABORTED) leaves the current document in place, as WebKit's cancelled
        # policy decision does; 'BlockedByClient' would commit Chromium's error page instead.
        refuse = refuse or (lambda: target.post('Fetch.failRequest', {'requestId': event['requestId'],
                                                                      'errorReason': 'Aborted'}))
        url, method = request['url'], request.get('method', 'GET')
        page_target = target
        while page_target.kind == 'iframe' and isinstance(page_target.parent, _Target):
            page_target = page_target.parent
        if page_target.kind == 'page' and page_target is not self.main_target and not self.owner_popups():
            refuse()
            self.root_cdp.send('Target.closeTarget', {'targetId': page_target.ident})
            if method.upper() != 'GET':
                self.refuse_popup_submit()
            return
        frame_id = event.get('frameId')
        main = target.kind == 'page' and frame_id == target.main_frame
        primary = target is self.main_target
        popup = target.kind == 'page' and not primary
        counted = primary and main
        if popup and main and not self.popup_document(url):
            refuse()
            self.root_cdp.send('Target.closeTarget', {'targetId': target.ident})
            return
        # Recovery returns to the held history entry; the read must not cancel its own recovery.
        restoring = primary and main and method.upper() == 'GET' and url == getattr(target, 'restoring_history', None)
        answered = []
        document, vetted_form = self.document_generation, False

        def decision(allowed, reason=None):
            if answered:
                return
            answered.append(True)
            if allowed and (vetted_form and document != self.document_generation or popup and not self.owner_popups()):
                allowed = False
            if allowed and counted and self.reading and not restoring:
                self.read_navigations += 1
                allowed = False
            def continue_navigation():
                if counted and not restoring:
                    self.main_navigations += 1
                    self.phone_navigated(committed=False)
                self.origins.add(self.origin(url))
                release()

            if not allowed:
                if reason:
                    self.navigation_refused(counted, reason)
                refuse()
                if popup or getattr(target, 'popup_navigation', False):
                    self.root_cdp.send('Target.closeTarget', {'targetId': target.ident})
            else:
                continue_navigation()

        if counted and self.reading and not restoring:
            self.read_navigations += 1
            return decision(False)
        info = self.frame_requests.pop((target.ident, frame_id), {})
        form = info.get('reason') in ('formSubmissionGet', 'formSubmissionPost') or method.upper() != 'GET'
        # A redirect continues a previously vetted submission; it is not another form submit.
        if form and not event.get('redirectedRequestId') and not self.guard_off and (primary or popup or target.kind == 'iframe'):
            if method.upper() != 'GET':
                self.posted_submits += 1
            if info.get('reason') == 'reload' and method.upper() != 'GET':
                self.refused_submits += 1
                return decision(False)
            vetted_form = True
            return self.check_form_navigation(url, method, lambda allowed: self.decide(url, counted, decision)
                                              if allowed else decision(False), into_frame=not main)
        self.decide(url, counted, decision)

    def fetch_paused(self, target, event):
        if target.kind == 'page' and target is not self.main_target and not self.owner_popups():
            return self.request_paused(target, event)
        # Context routing and our Fetch can both hold the initial request, in
        # either order. Share its decision, not a URL-based bypass for a later
        # request. Redirects ALWAYS get their own decision.
        release = lambda: target.post('Fetch.continueRequest', {'requestId': event['requestId']})
        refuse = lambda: target.post('Fetch.failRequest', {'requestId': event['requestId'], 'errorReason': 'Aborted'})
        if getattr(target, 'popup_navigation', False) and event.get('frameId') == target.main_frame \
                and not event.get('redirectedRequestId'):
            return self.initial_popup_request(target, event, 'fetch', release, refuse)
        self.request_paused(target, event, release, refuse)

    def initial_popup_request(self, target, event, source, release, refuse):
        if not self.owner_popups():
            refuse()
            self.root_cdp.send('Target.closeTarget', {'targetId': target.ident})
            return
        request = event['request']
        key = (request['url'], request.get('method', 'GET'))
        first = getattr(target, 'initial_request', None)
        if first is not None and first['key'] == key and source not in first['sources']:
            first['sources'].add(source)
            if first['allowed'] is None:
                first['waiters'].append((release, refuse))
            else:
                (release if first['allowed'] else refuse)()
            return
        first = {'key': key, 'sources': {source}, 'allowed': None, 'waiters': [(release, refuse)]}
        target.initial_request = first

        def settle(allowed):
            allowed = allowed and self.owner_popups()
            first['allowed'] = allowed
            for yes, no in first['waiters']:
                (yes if allowed else no)()
            first['waiters'].clear()

        self.request_paused(target, event, lambda: settle(True), lambda: settle(False))

    def route_popup(self, route, request):
        """A popup's first request (#34): held by Playwright, which resumes a new page before this worker's
        own CDP session can intercept it.  Decided like any paused request; let through only once this
        worker's interception is installed on the popup, so its redirect hops are checked too.  A popup
        that cannot be identified is refused (fail closed)."""
        page = None
        try:
            try:
                frame = request.frame
                page = frame.page
            except Exception:
                # Supported Request.frame raises for the first navigation of
                # a popup whose Page has not been reported yet (1.64.1).
                frame = None
            if not request.is_navigation_request():
                if page is not self.page and not self.owner_popups():
                    return self._settle_route(route, False)
                return route.continue_()
            if page is self.page or self.owner_popups() and frame is not None and frame.parent_frame is not None:
                return route.continue_()   # this worker's own Fetch decides (each hop)
        except Exception:
            self.navigation_refused(not self.owner_visible, 'navigation_failed')
            if page is not None and not page.is_closed():
                page.close(run_before_unload=False)
            return self._settle_route(route, False)
        if not self.owner_popups():
            if request.method.upper() != 'GET':
                self.refuse_popup_submit()
            if page is not None and not page.is_closed():
                page.close(run_before_unload=False)
            return self._settle_route(route, False)
        began = time.monotonic()
        self.deciding += 1
        settled = False

        def finish(allowed):
            nonlocal settled
            if settled:
                return
            settled = True
            self.deciding -= 1
            self._settle_route(route, allowed)

        def ready():
            target = self.popup_target(page)
            # #34 (observed): while its first request is held, the popup's renderer answers no CDP command,
            # so only the browser-side Fetch acknowledgement can be waited for; the isolated-world scripts
            # and binding are queued before it and reach the renderer before its first document runs.
            if target is None or not target.fetch_ready:
                if time.monotonic() - began < shared.WARM_SECONDS:
                    return self.AppHelper.callLater(.02, ready)
                # Fail closed, and say so: the step reports the refused navigation.
                self.navigation_refused(not self.owner_visible, 'navigation_failed')
                # No safe correlation: abort and close all still-unreported
                # popup candidates rather than guessing an opener/URL match.
                for candidate in list(self.targets.values()):
                    if getattr(candidate, 'popup_navigation', False) and candidate.ident not in self.pages:
                        self.root_cdp.send('Target.closeTarget', {'targetId': candidate.ident})
                if page is not None and not page.is_closed():
                    page.close(run_before_unload=False)
                return finish(False)
            self.initial_popup_request(target, {'request': {'url': request.url, 'method': request.method},
                                                'frameId': target.main_frame}, 'route',
                                       lambda: finish(True), lambda: finish(False))
        ready()

    def popup_target(self, page):
        """Use the public Page when available, otherwise only an unambiguous
        unreported popup. Never correlate concurrent popups by URL/opener order.
        """
        known = next((t for t in self.targets.values() if page is not None and self.pages.get(t.ident) is page), None)
        if known is not None:
            return known
        if page is not None:
            return self.target_for(page)
        unmapped = [t for t in self.targets.values()
                    if getattr(t, 'popup_navigation', False) and t.ident not in self.pages]
        if len(unmapped) != 1:
            return None
        return unmapped[0]

    @staticmethod
    def _settle_route(route, allowed):
        try:
            route.continue_() if allowed else route.abort('aborted')
        except Exception:
            pass

    def owner_popups(self):
        return self.owner_visible and self.guard_off

    def refuse_popup_submit(self):
        # Page.windowOpen has neither a method nor a body. Never recreate a POST as GET.
        self.refused_submits += 1

    def window_open(self, target, event):
        if target is not self.main_target or self.owner_popups():
            return
        url = event.get('url', '')
        if not self.origin(url):
            return
        pending = {'url': url, 'form': None, 'token': object()}
        opens = getattr(target, 'window_opens', [])
        target.window_opens = opens
        opens.append(pending)
        document = self.document_generation
        self.deciding += 1

        def replay(result, error):
            if pending not in opens:
                return
            opens.remove(pending)
            self.deciding -= 1
            if error or target is not self.main_target or document != self.document_generation:
                self.navigation_refused(True, 'navigation_failed')
                return
            form = pending['form']
            if form is not None:
                if form.get('reason') == 'formSubmissionPost' or form.get('url') != url:
                    self.refuse_popup_submit()
                    return
                self.frame_requests[(target.ident, target.main_frame)] = form
            # Use the opener's event, never the popup's URL or document authority.
            # Fetch handles destination policy, read holds and navigation accounting.
            target.post('Page.navigate', {'url': url, 'transitionType': 'link' if event.get('userGesture') else 'other'})

        # Chromium creates a form's window before NotifyInspector reports its
        # method (FormSubmission::Create / Frame::ScheduleFormSubmission). Wait
        # for an opener-renderer round trip, after that synchronous task, so a
        # POST is identified before any GET can be issued. No popup is resumed.
        self.AppHelper.callLater(shared.WARM_SECONDS, replay, {}, {'code': -1})
        target.post('Runtime.evaluate', {'expression': 'void 0'}, replay)

    @staticmethod
    def origin(url):
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            return ''
        host = '[' + parts.hostname + ']' if ':' in parts.hostname else parts.hostname
        return f'{parts.scheme}://{host}' + (f':{parts.port}' if parts.port else '')

    @staticmethod
    def supported_document(url):
        # Chromium's own error page is what a failed navigation commits (host_not_found and the like are
        # reported by that navigation itself); it is not a page navigating somewhere unsupported.
        return url in ('about:blank', 'chrome-error://chromewebdata/') or \
            str(url).lower().startswith(('http://', 'https://'))

    @staticmethod
    def popup_document(url):
        return url == 'about:blank' or bool(Worker.origin(url))

    def frame_requested(self, target, event):
        if event.get('reason') in ('formSubmissionGet', 'formSubmissionPost'):
            for pending in getattr(target, 'window_opens', []):
                # Ambiguous concurrent forms fail closed rather than guessing.
                pending['form'] = event if pending['form'] is None else {'reason': 'formSubmissionPost'}
        if event.get('disposition') in ('newWindow', 'newTab'):
            return
        self.frame_requests[(target.ident, event['frameId'])] = event
        if target.kind == 'page' and target is not self.main_target and event['frameId'] == target.main_frame:
            if not self.owner_popups() or not self.popup_document(event.get('url', '')):
                self.root_cdp.send('Target.closeTarget', {'targetId': target.ident})
            return
        if target is not self.main_target or event['frameId'] != target.main_frame:
            return
        target.refused_navigation = None
        url = event.get('url', '')
        unsupported = not self.supported_document(url)
        if unsupported or (self.reading and url == 'about:blank' and
                           url != getattr(target, 'restoring_history', None)):
            target.refused_navigation = url
            if self.reading:
                self.read_navigations += 1
            if unsupported:
                self.navigation_refused(True, 'unsupported_navigation')
            target.post('Page.stopLoading')

    def frame_navigated(self, target, frame):
        if target.kind == 'page' and target is not self.main_target and not frame.get('parentId'):
            if not self.owner_popups() or not self.popup_document(frame.get('url', '')):
                self.root_cdp.send('Target.closeTarget', {'targetId': target.ident})
                return
        origin = self.origin(frame.get('url', ''))
        if origin:
            self.origins.add(origin)
        self.frame_origins[(target.ident, frame['id'])] = origin
        if not frame.get('parentId'):
            target.main_frame = frame['id']
            if frame.get('url') not in ('', ':', 'about:blank', None):
                target.initial_request = None
                target.popup_navigation = False
        if target is self.main_target and frame['id'] == target.main_frame:
            # frameNavigated is a new document; same-document URL changes have their own CDP event.
            self.clear_vetted()
            target.committed_frame = frame
            target.contexts.pop(target.main_frame, None)
            url = frame.get('url', '')
            restoring = url == getattr(target, 'restoring_history', None)
            target.restoring_history = None
            unsupported = not self.supported_document(url)
            if unsupported or self.reading and url == 'about:blank' and not restoring:
                if getattr(target, 'refused_navigation', None) != url:
                    if self.reading:
                        self.read_navigations += 1
                    if unsupported:
                        self.navigation_refused(True, 'unsupported_navigation')
                target.refused_navigation = None
                target.post('Page.stopLoading')

                if getattr(target, 'history_recovered', False):
                    target.restoring_history = 'about:blank'
                    target.post('Page.navigate', {'url': 'about:blank'})
                    return
                target.history_recovered = True

                def restore(history, error):
                    if target is not self.main_target or target.committed_frame is not frame:
                        return
                    if error:
                        target.restoring_history = 'about:blank'
                        target.post('Page.navigate', {'url': 'about:blank'})
                        return
                    index = history.get('currentIndex', 0) - 1
                    entries = history.get('entries', [])
                    if 0 <= index < len(entries):
                        entry = entries[index]
                        target.restoring_history = entry['url']
                        if self.origin(entry['url']):
                            target.post('Page.navigateToHistoryEntry', {'entryId': entry['id']})
                            return
                    target.restoring_history = 'about:blank'
                    target.post('Page.navigate', {'url': 'about:blank'})
                target.post('Page.getNavigationHistory', done=restore)
                return
            target.refused_navigation = None
            self.landed += 1
            host = shared._host(frame.get('url'))
            if host:
                self.hosts.add(host)
            self.phone_navigated()
            self.finish_navigation()

    def network_failed(self, target, event):
        if event.get('type') == 'Document':
            # A cancelled/replaced navigation cannot lend its first-request
            # decision (or a consumed form pass) to the replacement.
            target.initial_request = None
        if target is not self.main_target or event.get('requestId') not in self.main_requests:
            return
        loader = self.main_requests.pop(event['requestId'])
        if event.get('canceled'):
            return
        self.landed += 1
        if self.navigation is not None and loader:
            self.navigation_events[loader] = navigation_error_code(event.get('errorText', ''))
            self.finish_navigation()

    def loaded(self, target, event):
        if target is self.main_target and event.get('frameId') == target.main_frame \
                and event.get('loaderId') and self.navigation is not None:
            self.navigation_events[event['loaderId']] = 'load'
            self.finish_navigation()

    def finish_navigation(self):
        if self.navigation is None:
            return
        ident, loader = self.navigation
        outcome = self.navigation_events.get(loader) if loader else None
        if outcome is None:
            return
        frame = getattr(self.main_target, 'committed_frame', {})
        if outcome == 'load' and frame.get('loaderId') != loader:
            return  # A load must describe the requested committed document.
        self.navigation = None
        self.navigation_events.clear()
        if ident in self.pending:
            if outcome == 'load':
                self.reply(ident, url=frame['url'], layout=self.layout)
            else:
                self.fail(ident, outcome)

    def emulate(self, target):
        mobile = self.layout == 'mobile'
        width, height = self.viewport_size['width'], self.viewport_size['height']
        target.post('Emulation.setUserAgentOverride', {'userAgent': user_agent_for(self.layout, self.version),
                                                      'platform': 'Linux armv8l' if mobile else 'Linux x86_64'})
        if target.kind != 'page':
            return  # OOPIF metrics inherit the top-level frame; CDP forbids overriding them.
        # Disabled touch exposes navigator.maxTouchPoints=0. Do not pass 0 as
        # maxTouchPoints: CDP only accepts 1..16 for that optional parameter.
        target.post('Emulation.setTouchEmulationEnabled', {'enabled': False})
        target.post('Emulation.setDeviceMetricsOverride', {
            'width': round(width / self.zoom), 'height': round(height / self.zoom),
            'deviceScaleFactor': 1, 'mobile': False, 'scale': self.zoom,
            'screenWidth': int(width), 'screenHeight': int(height)})

    def op_navigate(self, ident, command, timeout):
        layout = command.get('layout')
        if layout is None:
            layout = self.layout
        if layout not in shared.LAYOUTS:
            return self.fail(ident, 'bad_layout')
        url = str(command.get('url') or '')
        if urlsplit(url).scheme not in ('http', 'https'):
            return self.fail(ident, 'bad_url')
        if layout != self.layout:
            self.layout = layout
            if self.mobile_restore is None:
                self.resize(shared.DESKTOP_SIZE if layout == 'desktop' else shared.MOBILE_SIZE)
            self.emulate(self.main_target)
            self._install_guard_scripts()
        self.deadline(ident, timeout, self.view.stopLoading)
        self.main_target.history_recovered = False
        self.navigation = (ident, None)
        self.navigation_events.clear()

        def navigated(result, error):
            if ident not in self.pending or self.navigation is None or self.navigation[0] != ident:
                return
            if error or result.get('errorText'):
                self.navigation = None
                self.fail(ident, navigation_error_code(result.get('errorText', '')))
            elif not result.get('loaderId') and self.navigation is not None and self.navigation[0] == ident:
                # A same-document navigation (only the fragment changed) loads nothing and fires no load
                # event; CDP answers it without a loaderId (Codex review on #640).
                self.navigation = None
                self.reply(ident, url=url, layout=self.layout)
            else:
                self.navigation = (ident, result['loaderId'])
                self.finish_navigation()
        self.main_target.post('Page.navigate', {'url': url}, navigated)

    def press(self, x, y, view=None):
        page = view if view is not None and view is not self.view else self.page
        target = self.main_target if page is self.page else self.target_for(page)
        # LOCATE supplies viewport CSS pixels. Remote taps convert view points.
        x, y = float(x), float(y)
        target.post('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': y})
        for kind in ('mousePressed', 'mouseReleased'):
            target.post('Input.dispatchMouseEvent', {'type': kind, 'x': x, 'y': y,
                                                    'button': 'left', 'clickCount': 1})

    def key(self, name, view=None):
        page = view if view is not None and view is not self.view else self.page
        target = self.main_target if page is self.page else self.target_for(page)
        code = {'Enter': 13, 'Backspace': 8, 'Tab': 9}[name]
        for kind in ('keyDown', 'keyUp'):
            params = {'type': kind, 'key': name, 'code': name, 'windowsVirtualKeyCode': code,
                      'nativeVirtualKeyCode': code}
            if kind == 'keyDown' and name in ('Enter', 'Tab'):
                params['text'] = '\r' if name == 'Enter' else '\t'
            target.post('Input.dispatchKeyEvent', params)

    def bounds(self, page=None, **bounds):
        page = page or self.page
        client = self.clients.get(page)
        if client is None:
            self.target_for(page)
            client = self.clients[page]
        info = client.send('Browser.getWindowForTarget')
        client.send('Browser.setWindowBounds', {'windowId': info['windowId'], 'bounds': bounds})

    def park(self):
        self.bounds(left=shared.PARK_ORIGIN, top=shared.PARK_ORIGIN, windowState='normal')
        relinquish_keyboard_focus()

    def op_show(self, ident, command, timeout):
        self.owner_visible = True
        self.set_guard_off(True)
        self.bounds(left=80, top=80, windowState='normal')
        self.main_target.post('Page.bringToFront')
        if command.get('url'):
            return self.op_navigate(ident, command, timeout)
        self.reply(ident)

    def front_view(self):
        return self.popups[-1] if self.popups else self.view

    def close_popup(self, view=None, window=None):
        for page in list(self.popups):
            if view is None or page is view:
                self.popups.remove(page)
                page.close(run_before_unload=False)

    def page_closed(self, page):
        if page in self.popups:
            self.popups.remove(page)
        self.clients.pop(page, None)
        self.pages = {ident: candidate for ident, candidate in self.pages.items() if candidate is not page}
        if page is self.page and not self.closing:
            visible = self.owner_visible
            self.owner_visible = False
            self.end_remote()
            self.close_popup()
            if visible:
                self.emit({'event': 'hidden'})
            self.AppHelper.callAfter(self.replace_closed_page)

    def replace_closed_page(self):
        if self.closing or not self.browser.is_connected():
            return
        self.creating_page = True
        try:
            self.page = self.context.new_page()
            self.main_target = self.target_for(self.page)
        finally:
            self.creating_page = False
        self.set_guard_off(False)
        self.park()

    def poll_windows(self):
        # CDP exposes minimization, but no native window-hide notification.
        if self.owner_visible and self.page is not None and not self.page.is_closed():
            client = self.clients[self.page]
            info = client.send('Browser.getWindowForTarget')
            if info['bounds'].get('windowState') == 'minimized':
                self.window_closed_by_owner()
        for ident, page in list(self.pages.items()):
            if page is self.page or page.is_closed() or page in self.popups:
                continue
            if self.owner_popups():
                self.popups.append(page)
            else:
                page.close(run_before_unload=False)

    def window_closed_by_owner(self):
        self.end_remote()
        self.close_popup()
        self.owner_visible = False
        self.park()
        self.set_guard_off(False)
        self.emit({'event': 'hidden'})

    def dialog(self, dialog, page=None):
        kind = dialog.type
        if kind == 'beforeunload':
            kind = 'confirm'
        if self.owner_visible:
            if self.held_dialog is not None:
                dialog.dismiss()
            else:
                self.held_dialog = {'kind': kind, 'text': ' '.join(dialog.message.split())[:shared.DIALOG_TEXT_LIMIT],
                                    'dialog': dialog,
                                    'target': self.main_target if page is None or page is self.page else self.target_for(page)}
            return
        answer = self.js_dialog(kind, dialog.message)
        if kind == 'alert' or answer is True:
            dialog.accept()
        else:
            dialog.dismiss()

    def native_dialog_closed(self, target, event):
        if self.held_dialog is None or self.held_dialog.get('target') is not target:
            return
        held, self.held_dialog = self.held_dialog, None
        if held and self.dialog_step is not None and len(self.dialog_step['dialogs']) < shared.DIALOG_RECORDS:
            self.dialog_step['dialogs'].append({'kind': held['kind'], 'message': held['text'], 'outcome': 'owner'})

    def answer_dialog(self, ok, from_sheet=False):
        held, self.held_dialog = self.held_dialog, None
        if held is None:
            return False
        if self.dialog_step is not None and len(self.dialog_step['dialogs']) < shared.DIALOG_RECORDS:
            self.dialog_step['dialogs'].append({'kind': held['kind'], 'message': held['text'], 'outcome': 'owner'})
        dialog = held['dialog']
        try:
            if held['kind'] == 'alert' or ok and held['kind'] == 'confirm':
                dialog.accept()
            else:
                dialog.dismiss()
        except Exception:
            pass  # A closing page may have already disposed of the dialog.
        return True

    def resize(self, size):
        self.viewport_size = {'width': int(size[0]), 'height': int(size[1])}
        self.page.set_viewport_size(self.viewport_size)

    def enter_mobile(self):
        if self.mobile_restore is None:
            self.mobile_restore = dict(self.viewport_size)
            self.phone_fit = None
            self.resize(shared.MOBILE_SIZE)
            self.emulate(self.main_target)

    def leave_mobile(self):
        if self.mobile_restore is not None:
            restore, self.mobile_restore = self.mobile_restore, None
            self.resize((restore['width'], restore['height']))
            self.phone_fit = None
            self.zoom = 1.0
            self.emulate(self.main_target)

    def set_page_zoom(self, zoom):
        self.zoom = float(zoom)
        self.emulate(self.main_target)

    def op_frame(self, ident, command, timeout):
        if not self._remote_allowed(ident):
            return
        held, last = self.held_dialog, self.last_frame
        if held and last:
            return self.reply(ident, jpeg=last[0], width=last[1], height=last[2],
                              dialog={'kind': held['kind'], 'text': held['text']}, focus=None, fields=[], links=[])
        if held:
            return self.fail(ident, 'frame_failed')
        self.deadline(ident, timeout)
        view = self.front_view()
        target = self.main_target if view is self.view else self.target_for(view)
        width, height = self.viewport_size['width'], self.viewport_size['height']
        zoom = self.zoom if target is self.main_target else 1.0

        def captured(result, error):
            if ident not in self.pending:
                return
            if error or not result.get('data'):
                return self.fail(ident, 'frame_failed')
            encoded = result['data']
            self.last_frame = (encoded, width, height, [])

            def focused(value, error):
                if ident not in self.pending:
                    return
                value = value if isinstance(value, dict) and not error else {}
                held = self.held_dialog
                self.reply(ident, jpeg=encoded, width=width, height=height,
                           dialog={'kind': held['kind'], 'text': held['text']} if held else None,
                           focus=value.get('focus') if value.get('focus') in shared.REMOTE_FOCUS_KINDS else None,
                           fields=shared.remote_fields(value.get('fields')), links=[])
                self.fit_phone_page()
            if self.held_dialog:
                focused(None, 'held')
            else:
                self.run(shared.REMOTE_FOCUS_SCRIPT, {}, focused, view=view)
        # Capture at up to the CSS layout width, preserving the phone aspect ratio.
        pixel_width = min(shared.FRAME_MAX_WIDTH, width / zoom)

        # A load event (or a recreated isolated world after commit) does not
        # mean the compositor has a surface. Give the document a painted frame
        # before asking for pixels, including after remote_begin resizes/zooms.
        def paint_ready(value, error):
            if ident not in self.pending:
                return
            if error:
                return self.fail(ident, 'frame_failed')
            capture()

        def capture():
            target.post('Page.captureScreenshot', {'format': 'jpeg', 'quality': int(shared.FRAME_JPEG_QUALITY * 100),
                                                 'captureBeyondViewport': False,
                                                 'clip': {'x': 0, 'y': 0, 'width': width / zoom, 'height': height / zoom,
                                                          'scale': pixel_width / (width / zoom)}}, captured)

        self.run('await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));'
                 + 'return JSON.stringify(null);', {}, paint_ready, view=view)

    def op_remote_tap(self, ident, command, timeout):
        if not self._remote_allowed(ident):
            return
        try:
            x, y = float(command.get('x')), float(command.get('y'))
        except (TypeError, ValueError):
            return self.fail(ident, 'bad_point')
        width, height = self.viewport_size['width'], self.viewport_size['height']
        if not (0 <= x <= width and 0 <= y <= height):
            return self.fail(ident, 'bad_point')
        zoom = self.zoom if self.front_view() is self.view else 1.0
        self.press(x / zoom, y / zoom, self.front_view())
        self.reply(ident)

    def op_remote_text(self, ident, command, timeout):
        if not self._remote_allowed(ident):
            return
        text = command.get('text')
        if not isinstance(text, str) or not text:
            return self.fail(ident, 'bad_text')
        self.deadline(ident, timeout)
        view = self.front_view()
        target = self.main_target if view is self.view else self.target_for(view)

        def focused(value, error):
            if ident not in self.pending:
                return
            kind = (value or {}).get('focus') if not error else None
            if kind not in shared.REMOTE_FOCUS_KINDS:
                return self.fail(ident, 'not_typable')

            def insert(result, error):
                if ident in self.pending:
                    if error:
                        self.fail(ident, 'not_typable')
                    else:
                        self.reply(ident)
            if command.get('replace') is True and kind != 'frame':
                # Selection is engine-independent, then the native insert path.
                def selected(value, error):
                    if ident in self.pending:
                        if error:
                            return self.fail(ident, 'not_typable')
                        target.post('Input.insertText', {'text': text}, insert)
                self.run("const el = document.activeElement; if (typeof el.select === 'function') el.select(); "
                         "else document.execCommand('selectAll', false, null); return JSON.stringify({ok:true});",
                         {}, selected, view=view)
            else:
                target.post('Input.insertText', {'text': text}, insert)
        self.run(shared.REMOTE_FOCUS_SCRIPT, {}, focused, view=view)

    def op_remote_nav(self, ident, command, timeout):
        if not self._remote_allowed(ident):
            return
        action = command.get('action')
        if action not in shared.REMOTE_NAV_ACTIONS:
            return self.fail(ident, 'bad_action')
        target = self.main_target if self.front_view() is self.view else self.target_for(self.front_view())
        if action == 'reload':
            target.post('Page.reload')
        else:
            def history(result, error):
                index = result.get('currentIndex', 0)
                if not error and index > 0:
                    target.post('Page.navigateToHistoryEntry', {'entryId': result['entries'][index - 1]['id']})
            target.post('Page.getNavigationHistory', done=history)
        self.reply(ident)

    def frame_links(self, data):
        return []

    def op_cookies_export(self, ident, command, timeout):
        self.deadline(ident, timeout)
        rows = [cookie_row(c) for c in self.context.cookies()]
        sites = {registrable_domain(row['domain'].lstrip('.')) for row in rows}
        grouped = {}
        for row in rows:
            grouped.setdefault(shared.site_of(row['domain'], sites), []).append(row)
        if ident in self.pending:
            self.reply(ident, sites=grouped, hosts=sorted(self.hosts))

    def op_cookies_import(self, ident, command, timeout):
        self.deadline(ident, timeout)
        cookies = [cookie for row in command.get('cookies') or [] if isinstance(row, dict)
                   if (cookie := cookie_object(row)) is not None]
        if cookies:
            self.context.add_cookies(cookies)
        self.reply(ident, imported=len(cookies))

    def clear_storage(self, origins, done):
        jobs = []
        for origin in origins:
            jobs.append((self.main_target, 'Storage.clearDataForOrigin',
                         {'origin': origin, 'storageTypes': 'all'}))
        for (ident, frame_id), origin in self.frame_origins.items():
            target = self.targets.get(ident)
            if target is not None and origin in origins:
                jobs.append((target, 'session', frame_id))
        if not jobs:
            return done(None)
        remaining, failed = [len(jobs)], [False]

        def finished(result, error):
            failed[0] = failed[0] or bool(error) or bool(result.get('exceptionDetails'))
            remaining[0] -= 1
            if not remaining[0]:
                done('worker_error' if failed[0] else None)

        for target, method, params in jobs:
            if method != 'session':
                target.post(method, params, finished)
                continue

            def created(result, error, target=target):
                if error:
                    return finished({}, error)
                target.post('Runtime.callFunctionOn', {
                    'executionContextId': result['executionContextId'],
                    'functionDeclaration': 'function () { sessionStorage.clear(); }',
                    'awaitPromise': True, 'returnByValue': True}, finished)
            target.post('Page.createIsolatedWorld', {'frameId': params, 'worldName': WORLD}, created)

    def op_cookies_delete(self, ident, command, timeout):
        site = str(command.get('site') or '').lower()
        if not site:
            return self.fail(ident, 'bad_site')
        self.deadline(ident, timeout)
        rows = self.context.cookies()
        hits = [c for c in rows if shared.site_of(c['domain'], {site}) == site]
        origins = {o for o in self.origins if shared.site_of(shared._host(o), {site}) == site}

        def cleared(error):
            if ident not in self.pending:
                return
            if error:
                return self.fail(ident, error)
            for cookie in hits:
                self.context.clear_cookies(name=cookie['name'], domain=cookie['domain'], path=cookie['path'])
            self.reply(ident, deleted=len(origins) + len(hits))
        self.clear_storage(origins, cleared)

    def op_cookies_clear(self, ident, command, timeout):
        self.deadline(ident, timeout)

        def cleared(error):
            if ident not in self.pending:
                return
            if error:
                return self.fail(ident, error)
            self.context.clear_cookies()
            self.reply(ident)
        self.clear_storage(set(self.origins), cleared)

    def op_quit(self, ident, command, timeout):
        self.reply(ident)
        self.AppHelper.stopEventLoop()

    def close(self):
        self.closing = True
        self.context.close()
        self.browser.close()


def _playwright_factory():
    from playwright.sync_api import sync_playwright
    return sync_playwright


def main(argv=None):
    args = parse_args(argv)
    emit = shared.make_emit(sys.stdout)
    # Third-party diagnostics can never corrupt the protocol stream.
    with contextlib.redirect_stdout(sys.stderr):
        return _main(args, emit)


def _main(args, emit):
    if sys.platform != 'linux':
        emit({'event': 'unavailable', 'error': 'unsupported_platform'})
        return 2
    try:
        factory = _playwright_factory()
    except ImportError:
        emit({'event': 'unavailable', 'error': 'missing_dependency'})
        return 2
    if not os.environ.get('DISPLAY'):   # X11 (or XWayland): the window is parked with --ozone-platform=x11
        emit({'event': 'unavailable', 'error': 'no_display'})
        return 2
    worker = None
    ready = False
    try:
        with factory() as playwright:
            worker = Worker(args.profile, emit, playwright, args.chromium, args.test_allow_origin)
            deadline = time.monotonic() + shared.WARM_SECONDS
            while not worker.main_target.ready and time.monotonic() < deadline and not worker.fatal:
                worker.page.wait_for_timeout(10)
            if not worker.main_target.ready or worker.fatal:
                raise RuntimeError('initialization failed')
            emit({'event': 'ready', 'profile': args.profile, 'pid': os.getpid()})
            ready = True
            commands = queue.SimpleQueue()

            def reader():
                try:
                    for line in sys.stdin:
                        try:
                            value = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(value, dict):
                            commands.put(value)
                finally:
                    commands.put(None)
            threading.Thread(target=reader, name='still-chromium-stdin', daemon=True).start()
            next_window_poll = time.monotonic()
            deferred = []
            while worker.AppHelper.running and worker.browser.is_connected() and not worker.fatal:
                worker.AppHelper.pump()
                while not commands.empty():
                    command = commands.get()
                    if command is None:
                        worker.AppHelper.stopEventLoop()
                        break
                    # Only stop/dialog/end/quit can interrupt the current op.
                    if not worker.pending or command.get('op') in ('load_more_stop', 'remote_dialog', 'remote_end', 'quit'):
                        worker.handle(command)
                    else:
                        deferred.append(command)
                if not worker.pending and deferred:
                    worker.handle(deferred.pop(0))
                if time.monotonic() >= next_window_poll:
                    worker.poll_windows()
                    next_window_poll = time.monotonic() + .25
                if not worker.AppHelper.running:
                    break
                # Playwright dispatches callbacks only while one of its APIs runs.
                if worker.page.is_closed():
                    worker.root_cdp.send('Browser.getVersion')
                else:
                    worker.page.wait_for_timeout(10)
            worker.close()
    except Exception as exc:
        if not ready:
            text = str(exc).lower()
            sandbox = any(marker in text for marker in SANDBOX_FAILURE_MARKERS)
            emit({'event': 'unavailable', 'error': 'sandbox_unavailable' if sandbox else 'chromium_failed'})
        if worker is not None:
            try:
                worker.close()
            except Exception:
                pass
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
