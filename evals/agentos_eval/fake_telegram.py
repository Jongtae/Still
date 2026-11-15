"""A loopback stand-in for the Telegram Bot API, so a sandbox runs the paired-chat path.

Some owner-facing behavior exists only for a Work from the paired Telegram chat:
the plan preview and the worker's interim messages (#407) are examples.  A
sandbox's Still reaches this server instead of ``api.telegram.org``: the
sandbox import guard (``sandbox_site/sitecustomize.py``) points
``conversation_handoff.TELEGRAM_API_ROOT`` here when
``AGENTOS_EVAL_TELEGRAM_ROOT`` is set.  The sandbox gets a made-up bot token
(``TOKEN``), never the owner's, so even a missed redirect reaches no chat.

The eval plays the owner: ``say`` queues an owner message that Still's own
``getUpdates`` poll receives.  Every Bot API call Still makes is recorded, so
a run can show the conversation as the owner would have read it.  Standard
library only; development tooling, never imported by ``src/personal_agent``.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: A made-up token in the Bot API shape; it authorizes nothing anywhere.
TOKEN = '100000001:STILL-EVAL-FAKE-TOKEN-not-a-real-bot'
BOT = {'id': 100000001, 'is_bot': True, 'first_name': 'Still eval', 'username': 'still_eval_bot'}
#: The longest a ``getUpdates`` call is held open here (Still asks for longer).
LONG_POLL_SECONDS = 1.0
#: Methods whose text the owner reads in the chat.
MESSAGE_METHODS = frozenset({'sendMessage', 'editMessageText'})


class FakeTelegram:
    def __init__(self, chat_id, clock=time.time):
        self.chat_id = int(chat_id)
        self.clock = clock
        self.calls = []
        self._updates = []
        self._next_update = 1
        self._next_message = 1
        self._lock = threading.Condition()
        self._server = None
        self._thread = None

    # -- the owner's side -------------------------------------------------------
    def say(self, text):
        """Queue one owner text message; returns its ``message_id``."""
        with self._lock:
            message_id = self._take_message_id()
            self._updates.append({'update_id': self._next_update, 'message': {
                'message_id': message_id, 'from': {'id': self.chat_id, 'is_bot': False, 'first_name': 'Owner'},
                'chat': {'id': self.chat_id, 'type': 'private'}, 'date': int(self.clock()), 'text': text}})
            self._next_update += 1
            self._lock.notify_all()
        return message_id

    def chat_since(self, since, *, after_sequence=None):
        """What the owner read after ``since``: each message Still sent, in order, with its time."""
        with self._lock:
            messages = []
            previous = {}
            for call in self.calls:
                result = call.get('result')
                if call['method'] not in MESSAGE_METHODS or not isinstance(result, dict):
                    continue
                text = result.get('text') or ''
                ident = (result.get('chat', {}).get('id'), result.get('message_id'))
                meaningful = text and previous.get(ident) != text
                previous[ident] = text
                included = call['sequence'] > after_sequence if after_sequence is not None else call['at'] >= since
                if included and meaningful:
                    messages.append({'at': call['at'], 'sequence': call['sequence'], 'method': call['method'],
                                     'chat_id': ident[0], 'message_id': ident[1], 'text': text,
                                     'delivery_id': f'{ident[0]}:{ident[1]}:{call["sequence"]}'})
            return messages

    def sequence(self):
        with self._lock:
            return len(self.calls)

    def calls_since(self, since, *, after_sequence=None):
        with self._lock:
            return [dict(call) for call in self.calls
                    if (call['sequence'] > after_sequence if after_sequence is not None else call['at'] >= since)]

    # -- the server ---------------------------------------------------------------
    @property
    def root(self):
        host, port = self._server.server_address[:2]
        return f'http://{host}:{port}'

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self._answer({})

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length) if length else b''
                try:
                    body = json.loads(raw) if raw and 'json' in (self.headers.get('Content-Type') or '') else {}
                except ValueError:
                    body = {}
                self._answer(body if isinstance(body, dict) else {})

            def _answer(self, body):
                token, _, method = self.path.lstrip('/').partition('/')
                if token != f'bot{TOKEN}':
                    status, reply = 401, {'ok': False, 'error_code': 401, 'description': 'Unauthorized'}
                else:
                    status, reply = 200, {'ok': True, 'result': fake._call(method.split('?', 1)[0], body)}
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_exc):
        self.stop()

    # -- Bot API methods ----------------------------------------------------------
    def _take_message_id(self):
        message_id = self._next_message
        self._next_message += 1
        return message_id

    def _call(self, method, body):
        if method == 'getUpdates':
            return self._get_updates(body)
        with self._lock:
            call = {'at': self.clock(), 'sequence': len(self.calls) + 1, 'method': method, 'body': dict(body)}
            if method == 'getMe':
                result = dict(BOT)
            elif method in ('sendMessage', 'sendPhoto', 'sendDocument', 'sendLocation'):
                result = {'message_id': self._take_message_id(), 'date': int(self.clock()),
                          'chat': {'id': body.get('chat_id', self.chat_id), 'type': 'private'},
                          **({'text': body['text']} if isinstance(body.get('text'), str) else {})}
            elif method == 'editMessageText':
                result = {'message_id': body.get('message_id'), 'date': int(self.clock()),
                          'chat': {'id': body.get('chat_id', self.chat_id), 'type': 'private'}, 'text': body.get('text')}
            elif method == 'getWebhookInfo':
                result = {'url': '', 'pending_update_count': 0}
            elif method == 'getChat':
                result = {'id': self.chat_id, 'type': 'private'}
            else:
                result = True
            call['result'] = result
            self.calls.append(call)
            return result

    def _get_updates(self, body):
        offset = int(body.get('offset') or 0)
        deadline = time.monotonic() + min(float(body.get('timeout') or 0), LONG_POLL_SECONDS)
        with self._lock:
            while True:
                pending = [update for update in self._updates if update['update_id'] >= offset]
                if pending or time.monotonic() >= deadline:
                    return pending
                self._lock.wait(max(0.0, deadline - time.monotonic()))
