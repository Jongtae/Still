"""#6: a browser that refreshes or leaves mid-response is not an error.

The handler is driven on in-memory streams so each disconnect point is
deterministic: no socket, thread or timing is involved.
"""
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from personal_agent.quickstart import make_handler


class Disconnecting(io.RawIOBase):
    """A client stream that fails on the given write (1-based) with ``error``."""

    def __init__(self, fail_on=None, error=BrokenPipeError):
        self.fail_on = fail_on
        self.error = error
        self.writes = []

    def writable(self):
        return True

    def write(self, data):
        if self.fail_on is not None and len(self.writes) + 1 >= self.fail_on:
            raise self.error('client went away')
        self.writes.append(bytes(data))
        return len(data)


def service():
    fake = mock.MagicMock()
    fake.store.session.return_value = True
    fake.remote_login_started.return_value = False
    fake.phone_input_started.return_value = False
    fake.settings_snapshot.return_value = {'route': 'before'}
    fake.select_ai_route.return_value = {'ok': True}
    fake.settings_web_change_notice.return_value = 'AI route changed'
    return fake


def post(path, body, *, keep_alive=False):
    data = json.dumps(body).encode()
    head = (f'POST {path} HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nContent-Type: application/json\r\n'
            f'Content-Length: {len(data)}\r\nCookie: agentos_session=t\r\n')
    if keep_alive:
        head += 'Connection: keep-alive\r\n'
    return head.encode() + b'\r\n' + data


def run(fake, request, wfile):
    """Serve every request on one connection, as ``BaseHTTPRequestHandler.handle`` does.

    HTTP/1.1 is spoken so a connection could be kept open and reused; the
    disconnect must close it.
    """
    handler_class = type('Handler', (make_handler(fake),), {'protocol_version': 'HTTP/1.1'})
    handler = handler_class.__new__(handler_class)
    handler.server = SimpleNamespace(server_address=('127.0.0.1', 8765), server_port=8765)
    handler.client_address = ('127.0.0.1', 50000)
    handler.rfile = io.BufferedReader(io.BytesIO(request))
    handler.wfile = wfile
    handler.handle()
    return handler


class ReplyDisconnectTests(unittest.TestCase):
    def test_successful_reply_is_unchanged(self):
        fake, out = service(), Disconnecting()
        run(fake, post('/api/ai-route', {'route': 'x'}), out)
        raw = b''.join(out.writes)
        head, _, body = raw.partition(b'\r\n\r\n')
        self.assertTrue(head.startswith(b'HTTP/1.1 200'))
        self.assertIn(b'Content-Type: application/json; charset=utf-8', head)
        self.assertIn(b'Cache-Control: no-store', head)
        self.assertEqual(json.loads(body), {'ok': True})
        fake.record_conversation_notice.assert_called_once_with('AI route changed')
        fake.deliver_conversation_notice.assert_called_once_with('AI route changed')

    def test_disconnect_while_sending_headers_closes_quietly(self):
        fake = service()
        handler = run(fake, post('/api/ai-route', {'route': 'x'}), Disconnecting(fail_on=1))
        self.assertTrue(handler.close_connection)
        fake.select_ai_route.assert_called_once_with({'route': 'x'})

    def test_disconnect_while_sending_body_closes_quietly(self):
        fake, out = service(), Disconnecting(fail_on=2, error=ConnectionResetError)
        handler = run(fake, post('/api/ai-route', {'route': 'x'}), out)
        self.assertTrue(handler.close_connection)
        self.assertEqual(len(out.writes), 1)
        self.assertTrue(out.writes[0].startswith(b'HTTP/1.1 200'))

    def test_recorded_notice_survives_the_disconnect(self):
        for fail_on in (1, 2):
            with self.subTest(fail_on=fail_on):
                fake = service()
                run(fake, post('/api/ai-route', {'route': 'x'}), Disconnecting(fail_on=fail_on))
                fake.record_conversation_notice.assert_called_once_with('AI route changed')
                fake.deliver_conversation_notice.assert_called_once_with('AI route changed')

    def test_disconnected_request_is_not_replayed(self):
        # A second request pipelined on the same connection would run if the
        # connection were reused; neither it nor the first may run again.
        fake = service()
        request = post('/api/ai-route', {'route': 'x'}, keep_alive=True) + post('/api/ai-route', {'route': 'y'})
        handler = run(fake, request, Disconnecting(fail_on=1))
        self.assertTrue(handler.close_connection)
        fake.select_ai_route.assert_called_once_with({'route': 'x'})
        fake.record_conversation_notice.assert_called_once()

    def test_kept_alive_connection_serves_the_next_request_without_a_disconnect(self):
        # Control for the test above: the pipelined request does run when the
        # client stays, so its absence there comes from the disconnect.
        fake = service()
        request = post('/api/ai-route', {'route': 'x'}, keep_alive=True) + post('/api/ai-route', {'route': 'y'})
        run(fake, request, Disconnecting())
        self.assertEqual(fake.select_ai_route.call_args_list, [mock.call({'route': 'x'}), mock.call({'route': 'y'})])

    def test_redirect_disconnect_closes_quietly(self):
        handler_class = make_handler(service())
        handler = handler_class.__new__(handler_class)
        handler.request_version, handler.requestline = 'HTTP/1.1', 'GET / HTTP/1.1'
        handler.close_connection = False
        handler.wfile = Disconnecting(fail_on=1)
        handler.redirect('/')
        self.assertTrue(handler.close_connection)

    def test_unrelated_io_error_still_raises(self):
        fake = service()
        with self.assertRaises(OSError) as raised:
            run(fake, post('/api/ai-route', {'route': 'x'}), Disconnecting(fail_on=2, error=OSError))
        self.assertNotIsInstance(raised.exception, (BrokenPipeError, ConnectionResetError))
        fake.deliver_conversation_notice.assert_not_called()


if __name__ == '__main__':
    unittest.main()
