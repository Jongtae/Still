"""Sign in to a site from the phone through a one-time link that drives the Mac's login window (BROWSE-07 #939).

An in-flow login (#709) or an explicit login request shows the Still login
window on the Mac.  The owner who is not at the Mac gets a temporary HTTPS
link (ngrok or a Cloudflare Quick Tunnel, #523, as the family setup's, #897) to a page that shows that window
as an image refreshed about twice a second; a tap on the image is pressed at
the same point in the window and opens the phone keyboard while an editable
element has focus there, each keystroke goes into that field (#293), and [완료]
closes the window, which saves the session to the jar exactly as closing it
on the Mac does.  Nothing is proxied: the site talks to the Mac's WebKit
only, and the phone sees pixels.

Boundaries:
- the link carries a 32-byte random code and expires in ``SESSION_SECONDS``
  (10 minutes) or less; it is sent only to the paired owner's Telegram chat;
- the first client whose page calls the API with the cookie the page set is
  bound to the session (an HttpOnly, Secure, SameSite=Strict cookie); every
  later request must carry it, or it is answered 404;
- through the tunnel only ``PUBLIC_PATHS`` answer, and only with the code;
  every other route is refused (``quickstart.remote_login_gate``);
- typed text and taps go into the worker's ``remote_*`` ops and nowhere
  else: never logged, stored or shown to a model; image frames pass through
  once and are never stored;
- the log records only ``open``, ``bound``, ``done``, ``expired`` or
  ``closed`` with the site name;
- one session at a time; done, expiry, a Telegram skip and a Mac close each
  stop the tunnel and the window, and rewrite the sent link message to say
  how it ended, without the link (#255).
"""
from .messages import msg
import hashlib
import hmac
import json
import logging
import secrets
import subprocess
import threading
import time
from html import escape
from urllib.parse import quote

from .family_setup import start_tunnel
from . import work_mode
from .work_mode import WorkModeRefused

LOG = logging.getLogger('personal_agent.remote_login')

SESSION_SECONDS = 10 * 60
PAGE_PATH = '/remote-login'
FRAME_PATH = '/api/remote-login/frame'
INPUT_PATH = '/api/remote-login/input'
DONE_PATH = '/api/remote-login/done'
PUBLIC_PATHS = frozenset({PAGE_PATH, FRAME_PATH, INPUT_PATH, DONE_PATH})
COOKIE_NAME = 'agentos_remote_login'
INPUT_KINDS = ('tap', 'text', 'key', 'nav', 'dialog')
DIALOG_ANSWERS = ('ok', 'cancel')
KEYS = ('Enter', 'Backspace', 'Tab')
NAV_ACTIONS = ('back', 'reload')
#: Characters one text input may carry (a page's field, never a document).
TEXT_LIMIT = 512
#: How often the page refreshes the image, in milliseconds.
REFRESH_MS = 500

#: #242: the Telegram message follows the owner's language; the phone page itself (``page``) is
#: still Korean, so the other languages name its finish button by place rather than by its Korean word.
LINK_TEXT = msg('remote_login.link')
#: #255: what the link message says, in place, once its session ended (``{site}``); never the link again.
LINK_ENDED_TEXT = {'done': msg('remote_login.link_ended_done'), 'expired': msg('remote_login.link_ended_expired'),
                   'closed': msg('remote_login.link_ended_closed')}
#: #255 (owner choice 2026-10-24): the in-flow prompt's last line while the phone link drives its window;
#: the prompt then has no buttons, so the phone page's [완료] is the one place to finish.
PROMPT_PHONE_ACTIVE_LINE = msg('remote_login.prompt_phone_active')
NO_NGROK_TEXT = msg('remote_login.no_ngrok')
NO_PORT_TEXT = msg('remote_login.no_local_address')
BUSY_TEXT = msg('remote_login.a_phone_login_link_is_already')
#: A link that could not open or be delivered leaves the Mac window and its prompt as they are (review P2-1).
TUNNEL_FAILED_TEXT = msg('remote_login.i_couldn_t_open_the_phone')
#: How many unbound page cookies are kept at once (review P3-1): a later page open never invalidates an earlier one.
ISSUED_KEPT = 8
#: Review P3-3: how long after 완료's reply the tunnel is stopped, so the phone gets the answer first.
DONE_DELAY_SECONDS = 0.3


class RemoteLoginError(RuntimeError):
    """A session that could not start; the message is for the owner."""


def cookie_header(token):
    return f'{COOKIE_NAME}={token}; HttpOnly; Secure; SameSite=Strict; Path=/'


def _same(value, expected):
    return isinstance(value, str) and bool(value) and bool(expected) and hmac.compare_digest(value.encode(), str(expected).encode())


def _encodable(text):
    """#293: no lone surrogate: the worker pipe is UTF-8, and one that cannot be encoded ends the worker."""
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


def validate_input(body):
    """``(kind, fields)`` of one phone input, or None when it is not one.

    ``tap`` carries ``x``/``y`` (numbers), ``text`` a bounded string, ``key``
    one of ``KEYS``, ``nav`` one of ``NAV_ACTIONS`` and ``dialog`` (the
    answer to a page's own dialog) one of ``DIALOG_ANSWERS``.  Nothing else passes.
    """
    if not isinstance(body, dict):
        return None
    kind = body.get('type')
    if kind == 'tap':
        x, y = body.get('x'), body.get('y')
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in (x, y)):
            return 'tap', {'x': float(x), 'y': float(y)}
    elif kind == 'text':
        text = body.get('text')
        if isinstance(text, str) and 0 < len(text) <= TEXT_LIMIT and '\n' not in text and '\r' not in text \
                and _encodable(text):
            return 'text', {'text': text}
    elif kind == 'key':
        if body.get('key') in KEYS:
            return 'key', {'key': body['key']}
    elif kind == 'nav':
        if body.get('action') in NAV_ACTIONS:
            return 'nav', {'action': body['action']}
    elif kind == 'dialog':
        if body.get('answer') in DIALOG_ANSWERS:
            return 'dialog', {'answer': body['answer']}
    return None


class RemoteLogin:
    """One remote login session: its link, binding, tunnel and the login window it drives.

    ``profile`` is the ``BrowserProfile`` whose login window ``window`` is
    open; ``close(reason)`` closes that window the way its opener closes it
    (an in-flow login records a decision, an explicit one asks the window to
    close); ``on_end(reason)`` runs once after the session ended.  ``popen``
    and ``clock`` are injectable for tests.
    """

    def __init__(self, profile, window, site, close, *, store, on_end=None, popen=subprocess.Popen, clock=time.time,
                 seconds=SESSION_SECONDS):
        self.profile, self.window, self.site = profile, window, str(site or '')
        self._close, self._on_end = close, on_end
        self._popen, self.clock = popen, clock
        #: #208: the owner store whose work mode the tunnel start reads.
        self._store = store
        self.code = secrets.token_urlsafe(32)
        self.created = clock()
        self.expires = self.created + max(1.0, min(float(seconds), SESSION_SECONDS))
        #: The cookie value the bound client carries; None until a page's first API call binds it.
        self.bound_client = None
        #: The cookie values pages were given while unbound (newest last, at most ``ISSUED_KEPT``).
        self._issued = []
        #: The digest of the last frame sent (review P3-4); never the frame itself.
        self._last_frame = None
        self.process = None
        self.link = None
        self.reason = None
        #: #255: ``(chat_id, message_id)`` of the sent link message, until its end is said there.
        self._link_message = None
        self._lock = threading.Lock()
        self._ended = threading.Event()

    # -- lifecycle -------------------------------------------------------------
    def start(self, port):
        """Open the tunnel to ``port`` and return the one-time link; the watcher then runs until the end."""
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise RemoteLoginError(NO_PORT_TEXT)
        try:
            self.process, public = start_tunnel(port, popen=self._popen, store=self._store)
        except WorkModeRefused as exc:
            # #208: the owner signs in at the Mac instead; the window there stays open.
            raise RemoteLoginError(str(exc)) from None
        except (OSError, RuntimeError) as exc:
            raise RemoteLoginError(TUNNEL_FAILED_TEXT + ' ' + str(exc)) from None
        # #208 review: work mode switched on, or the session ended, while ngrok was starting: the
        # switch's ``finish`` may already have run and found no process, so this end stops it.
        if work_mode.active(self._store) or self._ended.is_set():
            self._stop_unpublished_tunnel()
            raise RemoteLoginError(msg('work_mode.refused.tunnel') if work_mode.active(self._store) else TUNNEL_FAILED_TEXT)
        self.link = f'{public}{PAGE_PATH}?code={quote(self.code)}'
        # Page dialogs (#936) are now held for the phone's answer and shown on the Mac as a sheet.
        self.profile.login_window_remote(self.window, True)
        LOG.info('remote login open site=%s', self.site)
        threading.Thread(target=self._watch, name='agentos-remote-login', daemon=True).start()
        return self.link

    def _stop_unpublished_tunnel(self):
        """Terminate a tunnel whose link was never given out (#208 review)."""
        with self._lock:
            process, self.process = self.process, None
        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass

    def _watch(self):
        """End the session at its expiry, or as soon as its window closed on its own."""
        while not self._ended.wait(0.5):
            if self.clock() >= self.expires:
                self.finish('expired')
            elif self.profile.login_window_outcome(self.window) is not None:
                self.finish('closed')

    def alive(self):
        return not self._ended.is_set()

    def expired(self, now=None):
        return (self.clock() if now is None else now) >= self.expires

    def finish(self, reason):
        """End the session once: stop the tunnel, close the window its way, tell the owner's side.

        ``closed``: the window already closed on its own, nothing more to close.
        ``failed``: the link never opened or was never delivered (review P2-1):
        the tunnel stops but the window and its prompt stay exactly as they
        were, for the owner to use on the Mac.
        """
        with self._lock:
            if self._ended.is_set():
                return False
            self.reason = reason
            self._ended.set()
        process, self.process = self.process, None
        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass
        LOG.info('remote login %s site=%s', reason, self.site)
        if reason != 'closed':
            self.profile.login_window_remote(self.window, False)
        if reason not in ('closed', 'failed'):
            try:
                self._close(reason)
            except Exception as exc:
                LOG.warning('remote login: window close failed (%s)', type(exc).__name__)
        if self._on_end is not None:
            try:
                self._on_end(reason)
            except Exception as exc:
                LOG.warning('remote login: end hook failed (%s)', type(exc).__name__)
        return True

    def link_sent(self, chat_id, message_id):
        """Remember the link message; False when the session already ended (its end is then said at once)."""
        with self._lock:
            self._link_message = (chat_id, message_id)
            return not self._ended.is_set()

    def take_link_message(self):
        """The sent link message, once: whoever takes it (the end hook or a late send) rewrites it (#255)."""
        with self._lock:
            sent, self._link_message = self._link_message, None
        return sent

    def finish_later(self, reason, delay=DONE_DELAY_SECONDS):
        """``finish`` after ``delay`` seconds, so a reply already written reaches the phone before the tunnel stops (review P3-3)."""
        timer = threading.Timer(delay, self.finish, args=(reason,))
        timer.daemon = True
        timer.start()
        return self.alive()

    # -- the link and its client -----------------------------------------------
    def code_ok(self, code):
        return self.alive() and not self.expired() and _same(code, self.code)

    def _issued_match(self, cookie):
        return next((issued for issued in self._issued if _same(cookie, issued)), None)

    def open_page(self, cookie):
        """The page was requested: the cookie to set (a fresh value while unbound, unless the request already
        carries one issued here, review P3-1), None when a known client reloads, or False when another
        client asked after binding."""
        with self._lock:
            if self.bound_client is None:
                if self._issued_match(cookie) is not None:
                    return None
                issued = secrets.token_urlsafe(32)
                self._issued = (self._issued + [issued])[-ISSUED_KEPT:]
                return issued
            return None if _same(cookie, self.bound_client) else False

    def client_ok(self, cookie):
        """Whether an API request's cookie is the bound client's; the first API call carrying any cookie a
        page was issued binds that client (a link preview fetcher never calls the API, so it never binds,
        and a later page open never invalidates an earlier one, review P3-1)."""
        with self._lock:
            if self.bound_client is None:
                issued = self._issued_match(cookie)
                if issued is None:
                    return False
                self.bound_client, self._issued = issued, []
                LOG.info('remote login bound site=%s', self.site)
                return True
            return _same(cookie, self.bound_client)

    # -- the window ----------------------------------------------------------------
    def frame(self, full=False):
        """The window's current image (``{'jpeg', 'width', 'height', 'dialog', 'focus'}``), ``{'unchanged': True}``
        when nothing changed since the last one sent (review P3-4; ``full`` sends it anyway), or None when
        the window could not answer now.  ``focus`` (#293) is what kind of element has focus, so the page
        knows when to keep the phone keyboard up; a change of it alone is a change."""
        if not self.alive():
            return None
        frame = self.profile.login_window_frame(self.window)
        if not isinstance(frame, dict):
            return None
        digest = hashlib.sha256(json.dumps([frame.get('jpeg'), frame.get('width'), frame.get('height'), frame.get('dialog'),
                                            frame.get('focus')], sort_keys=True).encode()).hexdigest()
        if not full and digest == self._last_frame:
            return {'unchanged': True}
        self._last_frame = digest
        return frame

    def input(self, body):
        """One phone input (``validate_input``) into the window; True when the worker accepted it."""
        if not self.alive():
            return False
        checked = validate_input(body)
        if checked is None:
            return False
        kind, fields = checked
        return bool(self.profile.login_window_input(self.window, kind, **fields))

    def status(self):
        return {'site': self.site, 'state': self.reason or ('bound' if self.bound_client else 'open'),
                'expires_in': max(0, int(self.expires - self.clock()))}


# -- the page ----------------------------------------------------------------

def page(session, nonce):
    """The phone page: one mobile page, no external resources; it talks only to its own API."""
    site = escape(session.site or '로그인')
    code = json.dumps(session.code)
    nonce = escape(nonce)
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1"><title>{site} 로그인</title>
<style nonce="{nonce}">body{{font:16px/1.4 -apple-system,system-ui,sans-serif;margin:0;padding:10px;background:#1c1c1e;color:#f2f2f7}}
h1{{font-size:16px;margin:0 0 8px;font-weight:600}}.note{{font-size:13px;color:#aeaeb2;margin:6px 0}}
#shot{{display:block;width:100%;background:#000;border-radius:8px;touch-action:manipulation;-webkit-user-select:none;user-select:none}}
.row{{display:flex;gap:6px;margin:8px 0}}input{{flex:1;min-width:0;font-size:16px;padding:12px;border-radius:10px;border:1px solid #48484a;background:#2c2c2e;color:#fff}}
button{{font-size:15px;padding:12px 10px;border-radius:10px;border:0;background:#3a3a3c;color:#fff}}button.main{{background:#0a84ff}}
button.wide{{flex:1}}#done{{width:100%;font-size:19px;padding:16px;background:#30d158;color:#000;font-weight:600;margin-top:10px}}
#end{{display:none;text-align:center;padding:30px 0;font-size:18px}}.hidden{{display:none}}
#dialog{{background:#2c2c2e;border:1px solid #ffd60a;border-radius:10px;padding:12px;margin:8px 0}}#dialog p{{margin:0 0 8px;white-space:pre-wrap}}
#screen{{position:relative}}#keys{{position:absolute;left:0;top:0;width:1px;height:1px;min-width:0;flex:none;padding:0;margin:0;border:0;
opacity:0;font-size:16px;color:transparent;background:transparent;caret-color:transparent}}
details{{margin:8px 0}}summary{{font-size:14px;color:#aeaeb2;padding:6px 0}}</style></head><body>
<h1>{site} 로그인</h1>
<div id="live"><div id="dialog" class="hidden"><p class="note">Mac의 로그인 창이 이 질문에 답하기를 기다리고 있어요.</p><p id="dialog-text"></p>
<div class="row"><button id="dialog-ok" class="main wide">확인</button><button id="dialog-cancel" class="wide">취소</button></div></div>
<div id="screen"><img id="shot" alt="Mac의 로그인 창"><input id="keys" type="password" autocomplete="current-password" autocapitalize="off" autocorrect="off" spellcheck="false" aria-label="Mac 창의 선택한 칸에 바로 입력"></div>
<p class="note">화면을 누르면 Mac의 로그인 창에서 같은 자리가 눌려요. 입력할 칸을 누르면 키보드가 열리고, 치는 글자가 그 칸에 바로 들어가요.</p>
<p class="note hidden" id="typing-failed">글자가 Mac 창에 들어가지 않았어요. 입력할 칸을 다시 누르고 쳐 주세요.</p>
<div class="row"><button id="enter" class="wide">엔터</button><button id="tab" class="wide">다음 칸</button><button id="back" class="wide">←지우기</button><button id="nav-back" class="wide">뒤로</button><button id="reload" class="wide">새로고침</button></div>
<details id="autofill"><summary>iPhone 암호로 채우기</summary>
<form id="cred" autocomplete="on"><p class="note">위 화면에서 아이디 칸을 누른 뒤, 아래 칸을 눌러 iPhone 암호 자동 채우기(열쇠 아이콘)로 계정을 고르고 [넣기]를 누르세요. 아이디, Tab, 비밀번호 순으로 들어가요.</p>
<div class="row"><input id="user" name="username" type="text" autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="아이디" maxlength="{TEXT_LIMIT}"></div>
<div class="row"><input id="pass" name="password" type="password" autocomplete="current-password" placeholder="비밀번호" maxlength="{TEXT_LIMIT}"><button id="fill" type="submit" class="main">넣기</button></div></form></details>
<button id="done">완료</button>
<p class="note" id="expire"></p></div>
<p id="end"></p>
<script nonce="{nonce}">
const code={code};const q='?code='+encodeURIComponent(code);const $=id=>document.getElementById(id);
let W=0,H=0,busy=false,ended=false,full=true,F=null,checkAfter=0,moveSeq=0,composing=false,sending=false;const sendQ=[],idle=[];
function end(text){{ended=true;$('keys').blur();$('live').classList.add('hidden');$('end').style.display='block';$('end').textContent=text}}
async function post(path,body){{try{{const r=await fetch(path+q,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body||{{}}),credentials:'same-origin'}});
if(r.status===404){{end('이 링크는 닫혔어요.');return false}}if(!r.ok)return false;const d=await r.json();return d&&d.ok===true}}catch(e){{return false}}}}
async function frame(){{if(ended||busy)return;busy=true;const t0=Date.now();try{{const r=await fetch('{FRAME_PATH}'+q+(full?'&full=1':''),{{cache:'no-store',credentials:'same-origin'}});
if(r.status===404){{end('이 링크는 닫혔어요.');return}}
if(r.status===204){{full=false;if(t0>=checkAfter)applyFocus();return}}
if(r.ok){{const d=await r.json();full=false;W=d.width;H=d.height;$('shot').src='data:image/jpeg;base64,'+d.jpeg;dialog(d.dialog);
F=d.focus||null;if(t0>=checkAfter)applyFocus()}}}}catch(e){{}}finally{{busy=false}}}}
// #293: typing goes straight into the Mac window.  A tap focuses the invisible #keys over the image in
// the same gesture (iOS opens the keyboard only then); each change to it is read against a sentinel
// value (noVNC's hidden keyboard input) and sent in order as text, Backspace or Enter, and it is reset
// at once, so the page keeps nothing typed.  A frame's focus says what has focus in the window: the
// keyboard stays up only for an editable element, as a password input unless it is plain text, so the
// phone keyboard neither learns nor suggests what goes into a password field.  Anything that can move
// the window's focus (a tap, Tab, Enter, 뒤로, 새로고침: moving()) starts a password input again, and only
// frames fetched after it was sent decide (checkAfter): an older one still shows the focus from before.
// Only the latest such input reopens the gate (moveSeq): an earlier one's would let a frame of its field
// decide.
// Enter while an IME composes confirms the composition (a Japanese or Chinese candidate): it is left to
// the IME, and only an Enter outside composition goes to the window.
const S='__________';
function resetKeys(){{const k=$('keys');k.value=S;try{{k.setSelectionRange(S.length,S.length)}}catch(e){{}}}}
function keyType(){{return F==='text'?'text':'password'}}
function setKeyType(t){{const k=$('keys');t=t||keyType();if(k.type===t)return;k.type=t;
k.setAttribute('autocomplete',t==='password'?'current-password':'username');resetKeys()}}
function applyFocus(){{const k=$('keys');if(document.activeElement!==k)return;if(!F){{k.blur();return}}setKeyType()}}
// Text is split only between code points (for..of), never inside a surrogate pair.
function addText(part){{const last=sendQ[sendQ.length-1];
if(last&&last.type==='text'&&!last.sent&&last.text.length+part.length<={TEXT_LIMIT})last.text+=part;else sendQ.push({{type:'text',text:part}})}}
function enqueue(item){{if(item.type==='text'){{let part='';for(const ch of item.text.replace(/[\\r\\n]+/g,'')){{
if(part.length+ch.length>{TEXT_LIMIT}){{addText(part);part=''}}part+=ch}}if(part)addText(part)}}
else sendQ.push(item);pump()}}
async function pump(){{if(sending)return;sending=true;let failed=false;
while(sendQ.length&&!ended){{const item=sendQ[0];item.sent=true;const body=Object.assign({{}},item);delete body.sent;delete body.seq;
const ok=await post('{INPUT_PATH}',body);sendQ.shift();if(item.seq!==undefined&&item.seq===moveSeq)checkAfter=Date.now();if(item.type!=='tap'&&!ok)failed=true}}
sending=false;idle.splice(0).forEach(done=>done());$('typing-failed').classList.toggle('hidden',!failed);setTimeout(frame,150)}}
function moving(item){{setKeyType('password');checkAfter=Infinity;item.seq=++moveSeq;enqueue(item)}}
// 완료 closes the window: everything typed before it (the sign-in Enter included) reaches the window first.
function drained(){{return sending||sendQ.length?new Promise(done=>idle.push(done)):Promise.resolve()}}
// A whole-value replacement (AutoFill: insertReplacementText, or an input event with no inputType) is
// the value itself, sent intact: a leading '_' in it is never taken for the sentinel.
function flush(whole){{const k=$('keys'),v=k.value;resetKeys();if(whole){{if(v)enqueue({{type:'text',text:v}});return}}if(v===S)return;
let p=0;while(p<S.length&&p<v.length&&v[p]===S[p])p++;const added=v.slice(p);
if(added){{enqueue({{type:'text',text:added}});return}}for(let i=p;i<S.length;i++)enqueue({{type:'key',key:'Backspace'}})}}
resetKeys();
$('keys').addEventListener('compositionstart',()=>{{composing=true}});
$('keys').addEventListener('compositionend',()=>{{composing=false;flush()}});
$('keys').addEventListener('input',e=>{{if(composing||e.isComposing)return;flush(!e.inputType||e.inputType==='insertReplacementText')}});
$('keys').addEventListener('keydown',e=>{{if(e.key!=='Enter'||composing||e.isComposing||e.keyCode===229)return;e.preventDefault();flush();moving({{type:'key',key:'Enter'}})}});
function dialog(d){{const box=$('dialog');if(!d){{box.classList.add('hidden');return}}$('dialog-text').textContent=d.text||'';$('dialog-cancel').classList.toggle('hidden',d.kind!=='confirm');box.classList.remove('hidden')}}
$('dialog-ok').onclick=()=>post('{INPUT_PATH}',{{type:'dialog',answer:'ok'}}).then(()=>setTimeout(frame,150));
$('dialog-cancel').onclick=()=>post('{INPUT_PATH}',{{type:'dialog',answer:'cancel'}}).then(()=>setTimeout(frame,150));
setInterval(frame,{REFRESH_MS});frame();
$('shot').addEventListener('click',e=>{{if(!W||!H)return;const r=e.currentTarget.getBoundingClientRect();
const x=Math.max(0,Math.min(W,(e.clientX-r.left)/r.width*W)),y=Math.max(0,Math.min(H,(e.clientY-r.top)/r.height*H));
const k=$('keys');k.style.top=Math.round(e.clientY-r.top)+'px';resetKeys();k.focus();
moving({{type:'tap',x:x,y:y}})}});
// #1170: the phone's own AutoFill fills these two; they go out like typed text (ID, Tab, password) and are
// cleared at once: the page keeps neither.
function clearCred(){{$('user').value='';$('pass').value=''}}
$('cred').addEventListener('submit',async e=>{{e.preventDefault();const u=$('user').value,p=$('pass').value;clearCred();await drained();
if(u&&!(await post('{INPUT_PATH}',{{type:'text',text:u}})))return;
if(u&&p&&!(await post('{INPUT_PATH}',{{type:'key',key:'Tab'}})))return;
if(p)await post('{INPUT_PATH}',{{type:'text',text:p}});setTimeout(frame,150)}});
window.addEventListener('pagehide',clearCred);
$('enter').onclick=()=>moving({{type:'key',key:'Enter'}});
$('tab').onclick=()=>moving({{type:'key',key:'Tab'}});
$('back').onclick=()=>enqueue({{type:'key',key:'Backspace'}});
// Navigation goes behind what is still being typed, so no keystroke lands in the next page.
$('nav-back').onclick=()=>moving({{type:'nav',action:'back'}});
$('reload').onclick=()=>moving({{type:'nav',action:'reload'}});
$('done').onclick=async()=>{{const b=$('done');if(b.disabled)return;b.disabled=true;await drained();b.disabled=false;
if(await post('{DONE_PATH}')){{end('완료했어요. 로그인 창을 닫고 세션을 저장했어요. 이 페이지는 닫아도 돼요.')}}}};
$('expire').textContent='이 링크는 약 {max(1, int(round((session.expires - session.clock()) / 60)))}분 뒤에 닫혀요.';
</script></body></html>'''


def page_csp(nonce):
    return (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; "
            "img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
