"""#121: the Telegram Bot API transport over a real local TLS server.

Evidence class: model-free local integration.  The production client
(``telegram_http_client``: httpx2 pool + connect retry) talks to a TLS server
on 127.0.0.1 that resets chosen handshakes or requests.  Only the server
certificate is test-made; no Telegram or other network host is contacted.
"""
import datetime
import ipaddress
import socket
import ssl
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from personal_agent import conversation_handoff
from personal_agent.conversation_handoff import TELEGRAM_CONNECT_RETRIES, telegram_http_client, telegram_request_json
from personal_agent.providers import ProviderError


def _certificate(directory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = Path(directory) / 'cert.pem', Path(directory) / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


def _reset(conn):
    """Close with RST, as the owner's network does to some fresh connections."""
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
    conn.close()


class _Server:
    """A keep-alive HTTPS server that resets the first handshakes or requests it is told to."""

    def __init__(self, cert_path, key_path, reset_handshakes=0, reset_requests=0):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert_path, key_path)
        self.reset_handshakes, self.reset_requests = reset_handshakes, reset_requests
        self.connections, self.requests = 0, []
        self.listener = socket.create_server(('127.0.0.1', 0))
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self):
        self.listener.close()

    def _serve(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            self.connections += 1
            if self.connections <= self.reset_handshakes:
                _reset(conn)
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            tls = self.context.wrap_socket(conn, server_side=True)
        except OSError:
            return
        buffer = b''
        while True:
            while b'\r\n\r\n' not in buffer:
                chunk = tls.recv(65536)
                if not chunk:
                    tls.close()
                    return
                buffer += chunk
            head, buffer = buffer.split(b'\r\n\r\n', 1)
            length = 0
            for line in head.split(b'\r\n')[1:]:
                key, _, value = line.partition(b':')
                if key.strip().lower() == b'content-length':
                    length = int(value)
            while len(buffer) < length:
                buffer += tls.recv(65536)
            body, buffer = buffer[:length], buffer[length:]
            self.requests.append((head.split(b'\r\n', 1)[0], body))
            if len(self.requests) <= self.reset_requests:
                _reset(tls)
                return
            payload = b'{"ok": true, "result": {"message_id": %d}}' % len(self.requests)
            tls.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n'
                        b'Connection: keep-alive\r\n\r\n' % len(payload) + payload)


class TelegramTransportOverTlsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cert_path, self.key_path = _certificate(temp.name)

    def serve(self, **resets):
        server = _Server(self.cert_path, self.key_path, **resets)
        self.addCleanup(server.close)
        client = telegram_http_client(verify=ssl.create_default_context(cafile=str(self.cert_path)))
        self.addCleanup(client.close)
        patcher = mock.patch.object(conversation_handoff, '_telegram_http', return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)
        return server, f'https://127.0.0.1:{server.port}/botTOKEN/sendMessage'

    def test_a_reset_handshake_is_tried_again_and_the_message_is_sent_once(self):
        server, url = self.serve(reset_handshakes=TELEGRAM_CONNECT_RETRIES)
        result = telegram_request_json(url, {'chat_id': 1, 'text': '알림: 진스키야키 웨이팅 걸 시간이에요.'})
        self.assertEqual(result, {'ok': True, 'result': {'message_id': 1}})
        self.assertEqual(server.connections, TELEGRAM_CONNECT_RETRIES + 1)
        self.assertEqual(len(server.requests), 1, 'exactly one request reached the server')

    def test_handshakes_reset_past_the_retries_fail_with_nothing_sent(self):
        server, url = self.serve(reset_handshakes=TELEGRAM_CONNECT_RETRIES + 1)
        with self.assertRaises(ProviderError) as caught:
            telegram_request_json(url, {'chat_id': 1, 'text': 'x'})
        self.assertIsNone(caught.exception.status)
        self.assertEqual(server.connections, TELEGRAM_CONNECT_RETRIES + 1)
        self.assertEqual(server.requests, [])

    def test_a_reset_after_the_request_was_written_is_never_sent_again(self):
        server, url = self.serve(reset_requests=1)
        with self.assertRaises(ProviderError):
            telegram_request_json(url, {'chat_id': 1, 'text': 'x'})
        self.assertEqual(len(server.requests), 1, 'an uncertain send is not repeated')

    def test_consecutive_calls_reuse_one_connection(self):
        server, url = self.serve()
        for _ in range(3):
            self.assertTrue(telegram_request_json(url, {'chat_id': 1, 'text': 'x'})['ok'])
        self.assertEqual((server.connections, len(server.requests)), (1, 3))


if __name__ == '__main__':
    unittest.main()
