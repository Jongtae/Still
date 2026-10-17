import socket
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

import personal_agent.limited_egress_proxy as proxy_module
from personal_agent.limited_egress_proxy import (
    LimitedEgressProxyServer,
    parse_allowlist,
    parse_connect_authority,
    relay_bidirectional,
)


class _ProxyFixture:
    def __init__(self, allowlist, *, max_relay_bytes=1024):
        self.connections = []
        self.upstream_peers = []

        def connector(address, timeout):
            proxy_side, fixture_side = socket.socketpair()
            self.connections.append((address, timeout))
            self.upstream_peers.append(fixture_side)
            return proxy_side

        def resolver(hostname, port, *, type):
            return [(socket.AF_INET, type, 6, "", ("8.8.8.8", port))]

        self.server = LimitedEgressProxyServer(
            ("127.0.0.1", 0),
            allowlist,
            connector=connector,
            resolver=resolver,
            idle_timeout=0.25,
            max_relay_bytes=max_relay_bytes,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def client(self):
        client = socket.create_connection(self.server.server_address, timeout=1)
        client.settimeout(1)
        return client

    def close(self):
        for peer in self.upstream_peers:
            peer.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


def _response(client):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = client.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


class LimitedEgressProxyTests(unittest.TestCase):
    def test_main_binds_container_port_and_passes_owner_allowlist_once(self):
        calls = []

        class FakeServer:
            def __init__(self, address, allowlist):
                calls.append((address, allowlist))

            def serve_forever(self):
                calls.append("served")

            def server_close(self):
                calls.append("closed")

        with patch.dict(
            "os.environ",
            {"AGENTOS_PROVIDER_EGRESS_ALLOWLIST": "api.example.test"},
        ), patch.object(proxy_module, "LimitedEgressProxyServer", FakeServer):
            self.assertEqual(proxy_module.main(), 0)
        self.assertEqual(
            calls,
            [(("0.0.0.0", 3128), "api.example.test"), "served", "closed"],
        )

    def test_allowlisted_connect_to_443_relays_in_both_directions(self):
        fixture = _ProxyFixture("api.example.test, login.example.test")
        self.addCleanup(fixture.close)
        client = fixture.client()
        self.addCleanup(client.close)
        client.sendall(
            b"CONNECT API.EXAMPLE.TEST:443 HTTP/1.1\r\n"
            b"Host: api.example.test:443\r\n"
            b"Authorization: secret-that-must-not-be-logged\r\n\r\n"
        )

        self.assertTrue(_response(client).startswith(b"HTTP/1.1 200"))
        self.assertEqual(fixture.connections, [(('8.8.8.8', 443), 10.0)])
        upstream = fixture.upstream_peers[0]
        upstream.settimeout(1)
        client.sendall(b"client TLS bytes")
        self.assertEqual(upstream.recv(64), b"client TLS bytes")
        upstream.sendall(b"provider TLS bytes")
        self.assertEqual(client.recv(64), b"provider TLS bytes")

    def test_request_headers_and_tunnel_payload_are_not_logged(self):
        fixture = _ProxyFixture("api.example.test")
        self.addCleanup(fixture.close)
        captured = StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            client = fixture.client()
            client.sendall(
                b"CONNECT api.example.test:443 HTTP/1.1\r\n"
                b"Authorization: top-secret-header\r\n\r\n"
            )
            self.assertTrue(_response(client).startswith(b"HTTP/1.1 200"))
            client.sendall(b"top-secret-payload")
            fixture.upstream_peers[0].settimeout(1)
            self.assertEqual(fixture.upstream_peers[0].recv(64), b"top-secret-payload")
            client.close()
        self.assertEqual(captured.getvalue(), "")

    def test_empty_unknown_ip_and_wrong_port_are_denied_without_connecting(self):
        cases = (
            ("", "api.example.test:443", b"403"),
            ("api.example.test", "other.example.test:443", b"403"),
            ("api.example.test", "127.0.0.1:443", b"400"),
            ("api.example.test", "api.example.test:80", b"400"),
        )
        for allowlist, authority, expected in cases:
            with self.subTest(authority=authority, allowlist=allowlist):
                fixture = _ProxyFixture(allowlist)
                try:
                    client = fixture.client()
                    client.sendall(f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode())
                    self.assertIn(expected, _response(client).split(b"\r\n", 1)[0])
                    self.assertEqual(fixture.connections, [])
                    client.close()
                finally:
                    fixture.close()

    def test_invalid_authorities_and_every_ordinary_http_method_are_rejected(self):
        fixture = _ProxyFixture("api.example.test")
        self.addCleanup(fixture.close)
        invalid = (
            "api.example.test",
            "api.example.test:443:443",
            "user@api.example.test:443",
            "[::1]:443",
            "api.example.test:+443",
            "api.example.test:0443",
        )
        for authority in invalid:
            with self.subTest(authority=authority):
                client = fixture.client()
                client.sendall(f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode())
                self.assertTrue(_response(client).startswith(b"HTTP/1.1 400"))
                client.close()

        for method in (
            "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "PROPFIND"
        ):
            with self.subTest(method=method):
                client = fixture.client()
                client.sendall(f"{method} / HTTP/1.1\r\nHost: api.example.test\r\n\r\n".encode())
                response = _response(client)
                self.assertTrue(response.startswith(b"HTTP/1.1 405"))
                self.assertIn(b"Allow: CONNECT", response)
                client.close()
        self.assertEqual(fixture.connections, [])

    def test_relay_enforces_one_aggregate_cap_across_both_directions(self):
        client, relay_client = socket.socketpair()
        relay_upstream, upstream = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(relay_client.close)
        self.addCleanup(relay_upstream.close)
        self.addCleanup(upstream.close)
        client.settimeout(1)
        upstream.settimeout(1)
        result = []
        thread = threading.Thread(
            target=lambda: result.append(
                relay_bidirectional(
                    relay_client, relay_upstream, max_bytes=7, idle_timeout=0.25, chunk_size=4
                )
            )
        )
        thread.start()
        client.sendall(b"abcd")
        self.assertEqual(upstream.recv(16), b"abcd")
        upstream.sendall(b"xyz-more")
        self.assertEqual(client.recv(16), b"xyz")
        thread.join(timeout=1)
        self.assertEqual(result, [7])

    def test_allowlist_parsing_is_exact_and_fails_closed_on_invalid_entries(self):
        self.assertEqual(parse_allowlist(None), frozenset())
        self.assertEqual(parse_allowlist(" , "), frozenset())
        self.assertEqual(
            parse_allowlist(" API.Example.Test.,login.example.test "),
            frozenset({"api.example.test", "login.example.test"}),
        )
        self.assertEqual(parse_connect_authority("API.Example.Test.:443"), ("api.example.test", 443))
        with self.assertRaises(ValueError):
            parse_connect_authority(" api.example.test:443")
        for config in ("*.example.test", "https://api.example.test", "127.0.0.1", "bad_name"):
            with self.subTest(config=config), self.assertRaises(ValueError):
                parse_allowlist(config)

    def test_private_loopback_link_local_and_rebinding_resolutions_are_denied(self):
        for address in ("127.0.0.1", "10.0.0.8", "169.254.1.1", "::1"):
            with self.subTest(address=address):
                calls = []
                def resolver(_host, port, *, type):
                    return [(socket.AF_INET6 if ":" in address else socket.AF_INET, type, 6, "", (address, port))]
                fixture = _ProxyFixture("api.example.test")
                fixture.server.resolver = resolver
                original = fixture.server.connector
                fixture.server.connector = lambda target, timeout: (calls.append((target, timeout)), original(target, timeout))[1]
                try:
                    client = fixture.client()
                    client.sendall(b"CONNECT api.example.test:443 HTTP/1.1\r\n\r\n")
                    self.assertTrue(_response(client).startswith(b"HTTP/1.1 403"))
                    self.assertEqual(calls, [])
                    client.close()
                finally:
                    fixture.close()

        # The connector receives a vetted numeric address, not the hostname;
        # it cannot cause a second DNS lookup with a rebinding result.
        fixture = _ProxyFixture("api.example.test")
        fixture.server.resolver = lambda _host, port, *, type: [(socket.AF_INET, type, 6, "", ("8.8.4.4", port))]
        try:
            client = fixture.client()
            client.sendall(b"CONNECT api.example.test:443 HTTP/1.1\r\n\r\n")
            self.assertTrue(_response(client).startswith(b"HTTP/1.1 200"))
            self.assertEqual(fixture.connections[0][0], ("8.8.4.4", 443))
            client.close()
        finally:
            fixture.close()


if __name__ == "__main__":
    unittest.main()
