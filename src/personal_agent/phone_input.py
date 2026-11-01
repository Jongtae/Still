"""A one-time phone page for inputs only a page can safely take (PHONE-INPUT-01 #1213).

The owner who is not at the Mac asks in conversation, confirms the draft, and
gets a temporary HTTPS link (``family_setup.start_tunnel``: ngrok when installed,
else a Cloudflare quick tunnel, #523; as the remote login's, #939, and the family
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
from . import work_mode
from .messages import msg
from .work_mode import WorkModeRefused

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

LINK_TEXT = msg('phone_input.link')
NO_TUNNEL_TEXT = msg('phone_input.no_tunnel')
NO_PORT_TEXT = msg('phone_input.no_port')
BUSY_TEXT = msg('phone_input.busy')
NOT_PAIRED_TEXT = msg('phone_input.not_paired')
TUNNEL_FAILED_TEXT = msg('phone_input.tunnel_failed')
SENT_TEXT = msg('phone_input.sent')


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

    def __init__(self, kind, label, *, store, on_end=None, popen=subprocess.Popen, clock=time.time, seconds=SESSION_SECONDS):
        self.kind, self.label = str(kind), str(label)
        self._on_end, self._popen, self.clock = on_end, popen, clock
        #: #208: the owner store whose work mode the tunnel start reads.
        self._store = store
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
            self.process, public = start_tunnel(port, popen=self._popen, store=self._store)
        except WorkModeRefused as exc:
            # #208: the owner enters it on this Mac instead (``start_local``, Settings).
            raise PhoneInputError(str(exc)) from None
        except FileNotFoundError:
            # #520: the owner's words, not "[Errno 2] No such file or directory: 'ngrok'".
            raise PhoneInputError(NO_TUNNEL_TEXT) from None
        except (OSError, RuntimeError) as exc:
            raise PhoneInputError(TUNNEL_FAILED_TEXT + ' ' + str(exc)) from None
        # #208 review: work mode switched on, or the session ended, while the tunnel was starting: the
        # switch's ``finish`` may already have run and found no process, so this end stops it.
        if work_mode.active(self._store) or self._ended.is_set():
            self._stop_unpublished_tunnel()
            raise PhoneInputError(msg('work_mode.refused.tunnel') if work_mode.active(self._store) else TUNNEL_FAILED_TEXT)
        self.link = f'{public}{PAGE_PATH}?code={quote(self.code)}'
        LOG.info('phone input open kind=%s', self.kind)
        threading.Thread(target=self._watch, name='agentos-phone-input', daemon=True).start()
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
    body = f'''<p class="note">{msg('phone_input.client_instructions')}</p>
<label class="note" for="file">{msg('phone_input.json_file')}</label><input id="file" type="file" accept=".json,application/json">
<label class="note" for="json">{msg('phone_input.json_contents')}</label><textarea id="json" rows="6" autocomplete="off" spellcheck="false" maxlength="{MAX_CLIENT_CHARS}"></textarea>
<button id="save" class="main">{msg('label.save')}</button>'''
    script = f'''$('file').onchange=async()=>{{const f=$('file').files[0];if(!f)return;if(f.size>{MAX_CLIENT_CHARS}){{say('{msg('phone_input.file_too_large')}');return}}$('json').value=await f.text()}};
$('save').onclick=async()=>{{const d=await post('{CLIENT_PATH}',{{client_json:$('json').value}});if(d&&d.ok)end('{msg('phone_input.client_saved')}');else say(d&&d.error||'{msg('phone_input.save_failed')}')}};'''
    if session.kind == API_SLOT:
        body = f'''<p class="note">{msg('phone_input.api_instructions')}</p>
<label class="note" for="secret">{msg('phone_input.api_key')}</label><input id="secret" type="password" autocomplete="off" spellcheck="false" maxlength="4096">
<button id="save" class="main">{msg('phone_input.check_connection')}</button>'''
        script = f'''$('save').onclick=async()=>{{const b=$('save');b.disabled=true;try{{const d=await post('{API_PATH}',{{secret:$('secret').value}});if(d&&d.ok){{$('secret').value='';end(d.response||'{msg('phone_input.connection_verified')}')}}else say(d&&d.error||'{msg('phone_input.connection_failed')}');}}finally{{b.disabled=false;}}}};'''
        draft = json.loads(session.api_draft['after'])
        if draft.get('fields'):
            # #1380: one masked input per named value of a code-run secret set. Labels come from the
            # AI's draft (escaped); names are validated UPPER_SNAKE identifiers.
            uses = {'header_or_body': msg('phone_input.use_at_send'),
                    'in_code': msg('phone_input.use_in_code')}
            # #1566: values a draft that only adds fields keeps are not asked again.
            kept = [item for item in draft['fields'] if item['name'] in getattr(session, 'api_kept', ())]
            asked = [item for item in draft['fields'] if item not in kept]
            inputs = '\n'.join(
                f'<label class="note" for="f_{item["name"]}">{escape(item["label"])} · {uses[item["use"]]}</label>'
                f'<input id="f_{item["name"]}" type="password" autocomplete="off" spellcheck="false" maxlength="4096">'
                for item in asked)
            hosts = escape(', '.join(draft['hosts']))
            kept_note = ('<p class="note">' + msg('phone_input.kept_values', labels=escape(", ".join(item["label"] for item in kept))) + '</p>\n') if kept else ''
            body = ('<p class="note">' + msg('phone_input.secret_values', hosts=hosts) + '</p>\n' + kept_note + inputs
                    + f'\n<button id="save" class="main">{msg('phone_input.save_continue')}</button>')
            names = json.dumps([item['name'] for item in asked])
            script = (f"$('save').onclick=async()=>{{const b=$('save');b.disabled=true;try{{const v={{}};"
                      f"for(const n of {names})v[n]=$('f_'+n).value;const d=await post('{API_PATH}',v);"
                      f"if(d&&d.ok){{for(const n of {names})$('f_'+n).value='';end(d.response||'{msg('phone_input.saved')}')}}"
                      f"else say(d&&d.error||'{msg('phone_input.save_failed')}');}}finally{{b.disabled=false;}}}};")
        elif draft.get('oauth'):
            body = f'''<p class="note">{msg('phone_input.oauth_instructions')}</p>
<label class="note" for="client">{msg('phone_input.client_id')}</label><input id="client" type="password" autocomplete="off" spellcheck="false" maxlength="4096">
<label class="note" for="secret">{msg('phone_input.client_secret')}</label><input id="secret" type="password" autocomplete="off" spellcheck="false" maxlength="4096">
<button id="save" class="main">{msg('phone_input.check_connection')}</button>'''
            script = f'''$('save').onclick=async()=>{{const b=$('save');b.disabled=true;try{{const d=await post('{API_PATH}',{{client_id:$('client').value,client_secret:$('secret').value}});if(d&&d.ok){{$('client').value='';$('secret').value='';end(d.response||'{msg('phone_input.connection_verified')}')}}else say(d&&d.error||'{msg('phone_input.connection_failed')}');}}finally{{b.disabled=false;}}}};'''

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
if(r.status===404){{end('{msg('phone_input.link_closed')}');return null}}return await r.json()}}catch(e){{return null}}}}
{script}
</script></body></html>'''


def page_csp(nonce):
    return (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; "
            "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
