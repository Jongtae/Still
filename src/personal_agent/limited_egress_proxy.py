"""Small, default-deny HTTP CONNECT proxy for isolated provider workers.

The proxy intentionally supports no ordinary HTTP methods.  A tunnel is
opened only when its authority is an exact DNS-name match in the configured
allowlist and its port is 443.  Request headers and tunneled bytes are never
logged.
"""

from __future__ import annotations

import ipaddress
import os
import re
import select
import socket
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def _dns_hostname(value: str) -> str:
    """Return a canonical DNS hostname or raise ``ValueError``.

    Only ASCII DNS names are accepted.  This keeps matching deterministic;
    callers that need an international name can configure its IDNA form.
    """

    if not isinstance(value, str):
        raise ValueError("hostname must be a string")
    hostname = value.strip().lower()
    if hostname.endswith("."):
        hostname = hostname[:-1]
    if not hostname or len(hostname) > 253 or ":" in hostname:
        raise ValueError("invalid DNS hostname")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("IP literals are not allowed")
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("hostname must use ASCII or IDNA form") from exc
    if any(not _DNS_LABEL.fullmatch(label) for label in hostname.split(".")):
        raise ValueError("invalid DNS hostname")
    return hostname


def parse_allowlist(config: str | None) -> frozenset[str]:
    """Parse a comma-separated hostname allowlist.

    Missing, blank, or comma-only configuration produces an empty set and
    therefore denies every destination.  Invalid non-empty entries fail
    configuration closed instead of being silently ignored.
    """

    if config is None or not isinstance(config, str):
        if config is None:
            return frozenset()
        raise ValueError("allowlist must be a comma-separated string")
    entries = [entry.strip() for entry in config.split(",")]
    return frozenset(_dns_hostname(entry) for entry in entries if entry)


def parse_connect_authority(authority: str) -> tuple[str, int]:
    """Validate a CONNECT request-target as ``dns-name:443``."""

    if not isinstance(authority, str) or authority.count(":") != 1:
        raise ValueError("invalid CONNECT authority")
    hostname_text, port_text = authority.rsplit(":", 1)
    if not port_text.isascii() or not port_text.isdecimal() or port_text != "443":
        raise ValueError("CONNECT is restricted to port 443")
    # Whitespace trimming is permitted in configuration, but never in a wire
    # authority: accepting it would make policy and HTTP parsing disagree.
    if hostname_text != hostname_text.strip():
        raise ValueError("invalid CONNECT authority")
    return _dns_hostname(hostname_text), 443


def resolve_public_address(
    hostname: str,
    port: int,
    *,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
) -> tuple[str, int]:
    """Resolve once and select a globally routable numeric destination.

    The CONNECT policy is name-based, but the socket is opened using the
    selected numeric address.  That prevents a second resolver call from
    turning an allowlisted name into a loopback/private destination.
    """
    try:
        records = resolver(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        raise ValueError("provider hostname could not be resolved") from exc
    for _family, _kind, _protocol, _canonical, sockaddr in records:
        address = sockaddr[0]
        try:
            if ipaddress.ip_address(address).is_global:
                return address, port
        except ValueError:
            continue
    raise ValueError("provider hostname resolved to a non-global address")


def relay_bidirectional(
    client: socket.socket,
    upstream: socket.socket,
    *,
    max_bytes: int,
    idle_timeout: float,
    chunk_size: int = 16_384,
) -> int:
    """Relay both directions until EOF, timeout, or the aggregate byte cap.

    The cap includes bytes in both directions.  No read is larger than the
    remaining allowance, so the function can never forward more than the
    configured limit.  The returned value is the number of bytes forwarded.
    """

    if max_bytes <= 0 or idle_timeout <= 0 or chunk_size <= 0:
        raise ValueError("relay bounds must be positive")
    peers = {client: upstream, upstream: client}
    total = 0
    while total < max_bytes:
        try:
            readable, _, exceptional = select.select(
                tuple(peers), (), tuple(peers), idle_timeout
            )
        except (OSError, ValueError):
            break
        if exceptional or not readable:
            break
        for source in readable:
            remaining = max_bytes - total
            if remaining <= 0:
                break
            try:
                data = source.recv(min(chunk_size, remaining))
                if not data:
                    return total
                peers[source].sendall(data)
            except (OSError, TimeoutError):
                return total
            total += len(data)
    return total


class LimitedEgressProxyHandler(BaseHTTPRequestHandler):
    """HTTP handler implementing the narrow CONNECT-only policy."""

    protocol_version = "HTTP/1.1"
    # Avoid buffering bytes that immediately follow the CONNECT headers; those
    # bytes belong to the tunnel and must remain available on ``connection``.
    rbufsize = 0
    wbufsize = 0

    def log_message(self, _format: str, *_args: object) -> None:
        # In particular, never log the request line, headers, or tunnel data.
        return

    def __getattr__(self, name: str):
        # BaseHTTPRequestHandler dispatches verbs by looking up ``do_<VERB>``.
        # Catch extension and invented verbs too, so no non-CONNECT request can
        # fall through to its default 501 response contract.
        if name.startswith("do_"):
            return self._method_not_allowed
        raise AttributeError(name)

    def _reject(self, status: int) -> None:
        reason = {400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed"}[status]
        headers = [
            f"HTTP/1.1 {status} {reason}\r\n",
            "Content-Length: 0\r\n",
            "Connection: close\r\n",
        ]
        if status == 405:
            headers.append("Allow: CONNECT\r\n")
        headers.append("\r\n")
        try:
            self.connection.sendall("".join(headers).encode("ascii"))
        except OSError:
            pass
        self.close_connection = True

    def do_CONNECT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            hostname, port = parse_connect_authority(self.path)
        except ValueError:
            self._reject(400)
            return
        if hostname not in self.server.allowed_hosts:  # type: ignore[attr-defined]
            self._reject(403)
            return

        try:
            address = resolve_public_address(
                hostname, port, resolver=self.server.resolver  # type: ignore[attr-defined]
            )
            upstream = self.server.connector(  # type: ignore[attr-defined]
                address, self.server.connect_timeout  # type: ignore[attr-defined]
            )
        except (OSError, TimeoutError, ValueError):
            # Do not expose resolver or connection details to the worker.
            self._reject(403)
            return

        try:
            self.connection.settimeout(self.server.idle_timeout)  # type: ignore[attr-defined]
            upstream.settimeout(self.server.idle_timeout)  # type: ignore[attr-defined]
            self.connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            relay_bidirectional(
                self.connection,
                upstream,
                max_bytes=self.server.max_relay_bytes,  # type: ignore[attr-defined]
                idle_timeout=self.server.idle_timeout,  # type: ignore[attr-defined]
            )
        finally:
            try:
                upstream.close()
            except OSError:
                pass
            self.close_connection = True

    def _method_not_allowed(self) -> None:
        self._reject(405)

    do_DELETE = _method_not_allowed
    do_GET = _method_not_allowed
    do_HEAD = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_TRACE = _method_not_allowed


class LimitedEgressProxyServer(ThreadingHTTPServer):
    """Threaded CONNECT proxy with immutable destination policy."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        allowlist_config: str | None = None,
        *,
        connector: Callable[[tuple[str, int], float], socket.socket] = socket.create_connection,
        resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
        connect_timeout: float = 10.0,
        idle_timeout: float = 30.0,
        max_relay_bytes: int = 8 * 1024 * 1024,
    ):
        if connect_timeout <= 0 or idle_timeout <= 0 or max_relay_bytes <= 0:
            raise ValueError("proxy bounds must be positive")
        self.allowed_hosts = parse_allowlist(allowlist_config)
        self.connector = connector
        self.resolver = resolver
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.max_relay_bytes = max_relay_bytes
        super().__init__(server_address, LimitedEgressProxyHandler)


def main() -> int:
    """Serve the policy proxy with an immutable startup-time allowlist."""

    server = LimitedEgressProxyServer(
        ("0.0.0.0", 3128),
        os.environ.get("AGENTOS_PROVIDER_EGRESS_ALLOWLIST"),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
