"""A one-time phone page for inputs only a page can safely take (PHONE-INPUT-01 #1213).

The owner who is not at the Mac asks in conversation, confirms the draft, and
gets a temporary HTTPS link (ngrok, as the remote login's, #939, and the family
setup's, #897) in the paired Telegram chat.  The page does one of two things:

* ``google-client``: take the owner's own Google "Desktop app" client JSON
  (``AgentService.save_google_client``, #1172).

Google consent itself is done on the desktop only (owner decision
2026-10-09, #1225): a phone cannot reach this Mac's loopback callback, and
in-app browsers stall on it.

Boundaries, as #939's:
- a 32-byte code; the link expires in ``SESSION_SECONDS`` or less; it is sent
  only to the paired owner's Telegram chat;
- the first client whose page calls the API with the cookie the page set is
  bound; every later request must carry it, or it is answered 404;
- through the tunnel only ``PUBLIC_PATHS`` answer (``quickstart.phone_input_gate``);
- nothing a request carries is logged, stored by this module or shown to a model;
- one session at a time; done, expiry and a failed delivery stop the tunnel.
"""
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

LOG = logging.getLogger('personal_agent.phone_input')

SESSION_SECONDS = 10 * 60
PAGE_PATH = '/phone-input'
CLIENT_PATH = '/api/phone-input/google-client'
API_PATH = '/api/phone-input/api-slot'
API_SLOT = 'api-slot'
PUBLIC_PATHS = frozenset({PAGE_PATH, CLIENT_PATH, API_PATH})
COOKIE_NAME = 'agentos_phone_input'
GOOGLE_CLIENT = 'google-client'
#: Longest client JSON accepted.
MAX_CLIENT_CHARS = 20_000
ISSUED_KEPT = 8
DONE_DELAY_SECONDS = 0.3

LINK_TEXT = ('휴대폰에서 {label} 설정을 하는 링크예요. 이 링크를 연 휴대폰 한 대만 쓸 수 있고, 약 {minutes}분 뒤에 닫혀요. '
             '중간에 이 화면을 닫았다면 새 링크를 요청해 주세요.\n{link}')
NO_NGROK_TEXT = '휴대폰 링크를 만들 ngrok이 이 Mac에 없어요. Mac에서 설정해 주세요.'
NO_PORT_TEXT = '이 Mac의 Still 주소를 아직 알 수 없어 휴대폰 링크를 만들지 못했어요.'
BUSY_TEXT = '이미 휴대폰 링크가 열려 있어요. 그 링크가 닫힌 뒤에 다시 요청해 주세요.'
NOT_PAIRED_TEXT = '휴대폰 링크는 연결된 Telegram 대화로만 보내요. Telegram을 먼저 연결해 주세요.'
TUNNEL_FAILED_TEXT = '휴대폰 링크를 열지 못했어요. Mac에서 설정해 주세요.'
SENT_TEXT = '휴대폰 링크를 Telegram으로 보냈어요. 약 {minutes}분 동안 열려 있어요.'


class PhoneInputError(RuntimeError):
    """A session that could not start; the message is for the owner."""


def cookie_header(token):
    return f'{COOKIE_NAME}={token}; HttpOnly; Secure; SameSite=Strict; Path=/'


def _same(value, expected):
    return isinstance(value, str) and bool(value) and bool(expected) and hmac.compare_digest(value.encode(), str(expected).encode())


class PhoneInput:
    """One phone input session: its link, binding and tunnel.

    ``kind`` is ``GOOGLE_CLIENT`` or a Google connector id; ``label`` is its
    owner word.  ``on_end(reason)`` runs once after the session ended.
    ``popen`` and ``clock`` are injectable for tests.
    """

    def __init__(self, kind, label, *, on_end=None, popen=subprocess.Popen, clock=time.time, seconds=SESSION_SECONDS):
        self.kind, self.label = str(kind), str(label)
        self._on_end, self._popen, self.clock = on_end, popen, clock
        self.code = secrets.token_urlsafe(32)
        self.created = clock()
        self.expires = self.created + max(1.0, min(float(seconds), SESSION_SECONDS))
        self.bound_client = None
        self._issued = []
        self.process = None
        self.link = None
        self.reason = None
        self._lock = threading.Lock()
        self._ended = threading.Event()

    def start(self, port):
        """Open the tunnel to ``port`` and return the one-time link; a watcher ends the session at expiry."""
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise PhoneInputError(NO_PORT_TEXT)
        try:
            self.process, public = start_tunnel(port, popen=self._popen)
        except (OSError, RuntimeError) as exc:
            raise PhoneInputError(TUNNEL_FAILED_TEXT + ' ' + str(exc)) from None
        self.link = f'{public}{PAGE_PATH}?code={quote(self.code)}'
        LOG.info('phone input open kind=%s', self.kind)
        threading.Thread(target=self._watch, name='agentos-phone-input', daemon=True).start()
        return self.link

    def _watch(self):
        while not self._ended.wait(0.5):
            if self.clock() >= self.expires:
                self.finish('expired')

    def start_local(self, port):
        """The same bounded page on this machine, without starting a tunnel."""
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise PhoneInputError(NO_PORT_TEXT)
        self.link = f'http://localhost:{port}{PAGE_PATH}?code={quote(self.code)}'
        threading.Thread(target=self._watch, name='agentos-local-input', daemon=True).start()
        return self.link

    def alive(self):
        return not self._ended.is_set()

    def expired(self, now=None):
        return (self.clock() if now is None else now) >= self.expires

    def finish(self, reason):
        """End the session once: stop the tunnel and run the end hook."""
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
        LOG.info('phone input %s kind=%s', reason, self.kind)
        if self._on_end is not None:
            try:
                self._on_end(reason)
            except Exception as exc:
                LOG.warning('phone input: end hook failed (%s)', type(exc).__name__)
        return True

    def finish_later(self, reason, delay=DONE_DELAY_SECONDS):
        """``finish`` after ``delay`` seconds, so the reply already written reaches the phone first."""
        timer = threading.Timer(delay, self.finish, args=(reason,))
        timer.daemon = True
        timer.start()
        return self.alive()

    def code_ok(self, code):
        return self.alive() and not self.expired() and _same(code, self.code)

    def _issued_match(self, cookie):
        return next((issued for issued in self._issued if _same(cookie, issued)), None)

    def open_page(self, cookie):
        """The cookie to set while unbound, None for a known client, or False for another client after binding."""
        with self._lock:
            if self.bound_client is None:
                if self._issued_match(cookie) is not None:
                    return None
                issued = secrets.token_urlsafe(32)
                self._issued = (self._issued + [issued])[-ISSUED_KEPT:]
                return issued
            return None if _same(cookie, self.bound_client) else False

    def client_ok(self, cookie):
        """Whether an API request comes from the bound client; the first API call with an issued cookie binds it."""
        with self._lock:
            if self.bound_client is None:
                issued = self._issued_match(cookie)
                if issued is None:
                    return False
                self.bound_client, self._issued = issued, []
                LOG.info('phone input bound kind=%s', self.kind)
                return True
            return _same(cookie, self.bound_client)

    def status(self):
        return {'kind': self.kind, 'state': self.reason or ('bound' if self.bound_client else 'open'),
                'expires_in': max(0, int(self.expires - self.clock()))}


# -- the page ----------------------------------------------------------------

def page(session, nonce):
    """The phone page: one mobile page, no external resources; it talks only to its own API."""
    label = escape(session.label)
    code = json.dumps(session.code)
    nonce = escape(nonce)
    body = f'''<p class="note">Google Cloud에서 만든 데스크톱 앱(Desktop app) client의 JSON 파일을 고르거나 내용을 붙여 넣고 저장하세요.</p>
<label class="note" for="file">JSON 파일</label><input id="file" type="file" accept=".json,application/json">
<label class="note" for="json">또는 JSON 내용</label><textarea id="json" rows="6" autocomplete="off" spellcheck="false" maxlength="{MAX_CLIENT_CHARS}"></textarea>
<button id="save" class="main">저장</button>'''
    script = f'''$('file').onchange=async()=>{{const f=$('file').files[0];if(!f)return;if(f.size>{MAX_CLIENT_CHARS}){{say('파일이 너무 커요.');return}}$('json').value=await f.text()}};
$('save').onclick=async()=>{{const d=await post('{CLIENT_PATH}',{{client_json:$('json').value}});if(d&&d.ok)end('저장했어요. 서비스 연결은 Mac의 설정 → 외부 연결에서 해 주세요.');else say(d&&d.error||'저장하지 못했어요.')}};'''
    if session.kind == API_SLOT:
        body = '''<p class="note">이 연결에 사용할 API 키를 입력하세요. 값은 대화에 보내지 않습니다. 필요한 읽기를 확인한 뒤 연결을 저장합니다.</p>
<label class="note" for="secret">API 키</label><input id="secret" type="password" autocomplete="off" spellcheck="false" maxlength="4096">
<button id="save" class="main">연결 확인</button>'''
        script = f'''$('save').onclick=async()=>{{const b=$('save');b.disabled=true;try{{const d=await post('{API_PATH}',{{secret:$('secret').value}});if(d&&d.ok){{$('secret').value='';end(d.response||'연결을 확인했어요.')}}else say(d&&d.error||'연결을 확인하지 못했어요.');}}finally{{b.disabled=false;}}}};'''
        draft = json.loads(session.api_draft['after'])
        if draft.get('fields'):
            # #1380: one masked input per named value of a code-run secret set. Labels come from the
            # AI's draft (escaped); names are validated UPPER_SNAKE identifiers.
            uses = {'header_or_body': '코드에는 가려져 있다가 보낼 때만 채워져요',
                    'in_code': '실행 코드 안에서 직접 쓰여요(출력에는 가려져요)'}
            # #1566: values a draft that only adds fields keeps are not asked again.
            kept = [item for item in draft['fields'] if item['name'] in getattr(session, 'api_kept', ())]
            asked = [item for item in draft['fields'] if item not in kept]
            inputs = '\n'.join(
                f'<label class="note" for="f_{item["name"]}">{escape(item["label"])} · {uses[item["use"]]}</label>'
                f'<input id="f_{item["name"]}" type="password" autocomplete="off" spellcheck="false" maxlength="4096">'
                for item in asked)
            hosts = escape(', '.join(draft['hosts']))
            kept_note = (f'<p class="note">이미 저장된 값({escape(", ".join(item["label"] for item in kept))})은 '
                         '그대로 써요. 새로 필요한 값만 입력하세요.</p>\n') if kept else ''
            body = (f'<p class="note">이 값들은 대화에 보내지 않고 암호화해 저장해요. {hosts}로 보내는 요청에만 쓰이고, '
                    '조회만 할 수 있어요. 저장하면 원래 작업을 이어갑니다.</p>\n' + kept_note + inputs
                    + '\n<button id="save" class="main">저장하고 이어가기</button>')
            names = json.dumps([item['name'] for item in asked])
            script = (f"$('save').onclick=async()=>{{const b=$('save');b.disabled=true;try{{const v={{}};"
                      f"for(const n of {names})v[n]=$('f_'+n).value;const d=await post('{API_PATH}',v);"
                      f"if(d&&d.ok){{for(const n of {names})$('f_'+n).value='';end(d.response||'저장했어요.')}}"
                      "else say(d&&d.error||'저장하지 못했어요.');}finally{b.disabled=false;}};")
        elif draft.get('oauth'):
            body = '''<p class="note">이 연결의 클라이언트 ID와 비밀값을 입력하세요. 토큰 발급과 필요한 읽기를 확인한 뒤 원래 작업을 이어갑니다.</p>
<label class="note" for="client">클라이언트 ID (8자 이상)</label><input id="client" type="password" autocomplete="off" spellcheck="false" maxlength="4096">
<label class="note" for="secret">클라이언트 비밀값 (8자 이상)</label><input id="secret" type="password" autocomplete="off" spellcheck="false" maxlength="4096">
<button id="save" class="main">연결 확인</button>'''
            script = f'''$('save').onclick=async()=>{{const b=$('save');b.disabled=true;try{{const d=await post('{API_PATH}',{{client_id:$('client').value,client_secret:$('secret').value}});if(d&&d.ok){{$('client').value='';$('secret').value='';end(d.response||'연결을 확인했어요.')}}else say(d&&d.error||'연결을 확인하지 못했어요.');}}finally{{b.disabled=false;}}}};'''

    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{label}</title>
<style nonce="{nonce}">body{{font:16px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:14px;background:#1c1c1e;color:#f2f2f7}}
h1{{font-size:17px;margin:0 0 10px}}.note{{display:block;font-size:14px;color:#c7c7cc;margin:10px 0 6px}}
textarea,input{{width:100%;box-sizing:border-box;font-size:15px;padding:10px;border-radius:10px;border:1px solid #48484a;background:#2c2c2e;color:#fff}}
button{{display:block;box-sizing:border-box;width:100%;font-size:17px;padding:14px;border-radius:10px;border:0;margin-top:10px;background:#3a3a3c;color:#fff}}button.main{{background:#0a84ff}}
#msg{{color:#ffd60a;font-size:14px;min-height:1em}}#end{{display:none;text-align:center;padding:30px 0;font-size:18px}}.hidden{{display:none}}</style></head><body>
<h1>{label}</h1><div id="live">{body}<p id="msg" role="status"></p></div><p id="end"></p>
<script nonce="{nonce}">
const code={code};const q='?code='+encodeURIComponent(code);const $=id=>document.getElementById(id);
function say(text){{$('msg').textContent=text}}
function end(text){{$('live').classList.add('hidden');$('end').style.display='block';$('end').textContent=text}}
async function post(path,body){{try{{const r=await fetch(path+q,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body||{{}}),credentials:'same-origin'}});
if(r.status===404){{end('이 링크는 닫혔어요.');return null}}return await r.json()}}catch(e){{return null}}}}
{script}
</script></body></html>'''


def page_csp(nonce):
    return (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; "
            "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
