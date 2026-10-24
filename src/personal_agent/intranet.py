"""Owner-declared intranet destinations (#209).

The owner names, in Settings on this Mac, the exact company hosts Still may
reach (``jira.corp.example``, ``gitlab.corp.example:8443``).  Such a host may
resolve to a private address that every destination guard otherwise refuses.
Everything the owner did not declare stays refused as before.

Still-owned boundary, nothing more (C16: the owner's AI does the work; this
module only says which destinations are reachable):

* **List.** One row (``CONFIG_KEY``), written only by ``add`` and ``remove``
  from the local Settings endpoint.  ``settings_change`` in conversation has
  no such setting and no Work tool writes it.  Every change is kept in
  ``AUDIT_KEY``.  A change applies from the next request: every guard reads
  the list again.
* **Entry.** An exact ASCII DNS name (an international name in its IDNA form)
  with an optional port.  No IP literal, no wildcard or suffix, never
  ``localhost``, a ``.localhost`` name or a metadata host name.  An entry with
  no port admits the default ports 443 and 80 only.
* **Address.** A declared host may resolve to a global address or to
  private-use space (``PRIVATE_USE``: RFC 1918, shared CGNAT space, IPv6
  unique-local).  It may never resolve to loopback, unspecified, link-local,
  multicast, broadcast, other special-purpose space (``SPECIAL_PURPOSE``) or a
  cloud metadata address (``METADATA``).  These are the classes the pinned
  ``@anthropic-ai/sandbox-runtime`` resolved-address guard refuses for an
  allow-listed name, so code runs and every other path judge an address the
  same way.  An IPv4 address carried in IPv6 is judged as that IPv4 address.
* **Authority.** Declaring grants reachability only: no credential, no write
  and no effect approval.  Page scope, API slot hosts, effect approvals and
  owner MCP decisions are unchanged.
* **Work mode.** While it is on, the declared hosts are the only places page
  reads, API calls and code runs may reach (``work_mode.refusal``).

Not covered (stated, never overclaimed): Python's standard library cannot list
this Mac's own interface addresses, so a declared name whose DNS answer is
this Mac's LAN address is not refused here (sandbox-runtime refuses it for
code runs).  A path that resolves the name again after the check is not
pinned to the checked address: MCP setup (the CLI resolves it, as
``mcp_setup`` already states) and the browser (WebKit resolves it, as before
#209).  Page reads and code runs connect to the checked address.
"""
from __future__ import annotations

import ipaddress
import time
from urllib.parse import urlsplit

from .limited_egress_proxy import _dns_hostname
from .messages import msg

CONFIG_KEY = 'intranet_destinations'
AUDIT_KEY = 'intranet_destinations_audit'
AUDIT_LIMIT = 100
MAX_ENTRIES = 32
DEFAULT_PORTS = (443, 80)

#: Private-use space a declared host may resolve to.
PRIVATE_USE = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', 'fc00::/7')
#: Cloud instance-metadata endpoints outside link-local space (sandbox-runtime 0.0.79's list).
METADATA = ('100.100.100.200', '168.63.129.16', '192.0.0.192', 'fd00:ec2::/32', 'fd20:ce::254',
            'fd00:c1::a9fe:a9fe', 'fd00:42::42', 'fd00:a9fe:a9fe::1', 'fd00:100::100:200')
#: Special-purpose space that is neither global nor private-use (RFC 6890): never reachable by name.
SPECIAL_PURPOSE = ('0.0.0.0/8', '192.0.0.0/24', '192.0.2.0/24', '198.18.0.0/15', '198.51.100.0/24',
                   '203.0.113.0/24', '240.0.0.0/4', '2001:db8::/32')
_PRIVATE_USE = tuple(ipaddress.ip_network(value) for value in PRIVATE_USE)
_METADATA = tuple(ipaddress.ip_network(value) for value in METADATA)
_SPECIAL_PURPOSE = tuple(ipaddress.ip_network(value) for value in SPECIAL_PURPOSE)
#: Names that never denote a company host, whatever the owner types.
NEVER_NAMES = frozenset({'localhost', 'metadata', 'metadata.google.internal', 'instance-data',
                         'instance-data.ec2.internal'})


def parse(text):
    """``(host, port | None)`` of one owner entry, or ``ValueError`` with an owner-facing reason."""
    if not isinstance(text, str) or not text.strip() or len(text) > 260:
        raise ValueError(msg('intranet.invalid'))
    value = text.strip()
    if '://' in value or '/' in value or '@' in value or '*' in value or value.startswith('['):
        raise ValueError(msg('intranet.invalid'))
    host, port = value, None
    if value.count(':') == 1:
        host, port_text = value.split(':')
        if not port_text.isascii() or not port_text.isdecimal() or not 1 <= int(port_text) <= 65535:
            raise ValueError(msg('intranet.invalid'))
        port = int(port_text)
    try:
        host = _dns_hostname(host)
    except ValueError:
        raise ValueError(msg('intranet.invalid')) from None
    if '.' not in host or host in NEVER_NAMES or host.endswith('.localhost'):
        raise ValueError(msg('intranet.invalid'))
    return host, port


def _label(host, port):
    return host if port is None else f'{host}:{port}'


def entries(store):
    """The declared ``[{'host', 'port'}]``; a malformed row is skipped, never repaired.

    No owner store is an empty list.  A store that cannot be read raises, so a
    caller's request fails instead of going out.
    """
    return [] if store is None else _entries(store.config(CONFIG_KEY, []))


def _entries(rows):
    out = []
    for row in rows if isinstance(rows, list) else ():
        port = row.get('port') if isinstance(row, dict) else None
        if not isinstance(row, dict) or not isinstance(row.get('host'), str) \
                or port is not None and (not isinstance(port, int) or isinstance(port, bool)):
            continue
        try:
            host, parsed = parse(_label(row['host'], port))
        except ValueError:
            continue
        entry = {'host': host, 'port': parsed}
        if parsed == port and entry not in out:
            out.append(entry)
    return out


def declared(store, host, port):
    """Whether ``host`` on ``port`` is a destination the owner declared."""
    if not isinstance(host, str) or not isinstance(port, int):
        return False
    name = host.strip().casefold().rstrip('.')
    return any(entry['host'] == name and (entry['port'] == port if entry['port'] is not None
                                          else port in DEFAULT_PORTS)
               for entry in entries(store))


def host_declared(store, host):
    """Whether ``host`` is declared on any port (a code run names hosts without ports)."""
    if not isinstance(host, str):
        return False
    name = host.strip().casefold().rstrip('.')
    return any(entry['host'] == name for entry in entries(store))


def origin_keys(store):
    """Every declared ``host:port`` (an entry with no port gives one key per default port)."""
    keys = set()
    for entry in entries(store):
        for port in (entry['port'],) if entry['port'] is not None else DEFAULT_PORTS:
            keys.add(f"{entry['host']}:{port}")
    return frozenset(keys)


def destination(url):
    """``(host, port)`` of an http(s) ``url`` with its default port filled in, or None."""
    try:
        parts = urlsplit(str(url or ''))
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return None
    return parts.hostname.casefold().rstrip('.'), port or (443 if parts.scheme == 'https' else 80)


def _embedded_ipv4(address):
    if address.version == 6:
        for embedded in (address.ipv4_mapped, address.sixtofour, (address.teredo or (None, None))[1]):
            if embedded is not None:
                return embedded
    return address


def address_admitted(address):
    """Whether a declared host may connect to ``address`` (an ``ipaddress`` address)."""
    address = _embedded_ipv4(address)
    if (address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified
            or any(address in network for network in _METADATA + _SPECIAL_PURPOSE)):
        return False
    if address.version == 4 and address == ipaddress.IPv4Address('255.255.255.255'):
        return False
    return address.is_global or any(address in network for network in _PRIVATE_USE)


def private_use(address):
    """Whether ``address`` is in the private-use space only a declared host may reach."""
    address = _embedded_ipv4(address)
    return any(address in network for network in _PRIVATE_USE)


def _change(store, text, add, surface, clock):
    host, port = parse(text)
    entry = {'host': host, 'port': port}
    now = clock()
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        current = _entries(store.config(CONFIG_KEY, [], db=db))
        if add:
            if entry in current:
                return status(store)
            if len(current) >= MAX_ENTRIES:
                raise ValueError(msg('intranet.too_many', limit=MAX_ENTRIES))
            after = [*current, entry]
        else:
            if entry not in current:
                raise ValueError(msg('intranet.not_declared', host=_label(host, port)))
            after = [row for row in current if row != entry]
        store.put(CONFIG_KEY, after, db=db)
        audit = store.config(AUDIT_KEY, [], db=db)
        audit = audit if isinstance(audit, list) else []
        store.put(AUDIT_KEY, [*audit, {'at': now, 'action': 'add' if add else 'remove', 'host': host, 'port': port,
                                       'surface': str(surface)}][-AUDIT_LIMIT:], db=db)
    return status(store)


def add(store, text, *, surface, clock=time.time):
    """The owner's Settings addition of one entry; returns the status."""
    return _change(store, text, True, surface, clock)


def remove(store, text, *, surface, clock=time.time):
    """The owner's Settings removal of one entry; returns the status.  It applies from the next request."""
    return _change(store, text, False, surface, clock)


def status(store):
    """The Settings read model: the declared entries and the last recorded change."""
    audit = store.config(AUDIT_KEY, [])
    last = audit[-1] if isinstance(audit, list) and audit and isinstance(audit[-1], dict) else None
    return {'entries': [{**entry, 'label': _label(entry['host'], entry['port'])} for entry in entries(store)],
            'limit': MAX_ENTRIES, 'last_change': last}
