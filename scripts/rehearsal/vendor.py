"""A synthetic brokerage API for connection rehearsals (HARDEN-GENERIC-01 #1445).

Test asset, never product code. It reproduces generic shapes real vendor APIs
have, so the generic path (#1380) is exercised end to end before the owner:

* a documentation page the owner's AI must read;
* a non-standard JSON-body token exchange (not RFC 6749), on a non-default port;
* several secret headers on every call;
* an account list, and a balance per account keyed by a short product code;
* at most one token per minute (the vendor refuses more);
* one transient 502 on the first balance call.

``ParcelVendor`` (#1489) is a second, non-finance domain: parcel tracking with one API
key header and an invoice number the API takes as 12 digits without dashes.  Both count
documentation fetches, so a rehearsal can show that a second request used the saved
function library instead of re-reading the docs.

Nothing here is named after a real service.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

APP_KEY = 'rehearsal-app-key-7f3a9c'
APP_SECRET = 'rehearsal-app-secret-2b8e41d0'
ACCOUNTS = [{'account_no': '50123456', 'product_code': '01', 'kind': 'stock'},
            {'account_no': '50987654', 'product_code': '22', 'kind': 'pension'}]
BALANCES = {('50123456', '01'): {'cash': 1234567, 'holdings': [{'symbol': 'RHS1', 'name': 'Rehearsal Corp', 'quantity': 12,
                                                              'value': 345600}]},
            ('50987654', '22'): {'cash': 7654321, 'holdings': []}}
TOKEN_INTERVAL = 60

DOCS = """<html><body><h1>Rehearsal Securities Open API</h1>
<p>Base URL: <code>{base}</code>. All responses are JSON.</p>
<h2>1. Access token</h2>
<p><code>POST /oauth/issue</code> with JSON body
<code>{{"grant": "client", "app_key": "&lt;your app key&gt;", "app_secret": "&lt;your app secret&gt;"}}</code>.
Returns <code>{{"token": "...", "expires_in": 86400}}</code>. A token is valid for 24 hours.
At most one token may be issued per minute per app; reuse a valid token.</p>
<h2>2. Accounts</h2>
<p><code>GET /v2/accounts</code> with headers <code>X-Token: &lt;token&gt;</code>, <code>X-App-Key</code>,
<code>X-App-Secret</code>. Returns <code>{{"accounts": [{{"account_no": "...", "product_code": "..", "kind": "..."}}]}}</code>.</p>
<h2>3. Balance</h2>
<p><code>GET /v2/balance?account_no=..&amp;product_code=..</code> with the same three headers.
Returns cash and holdings for that account. The service may answer 502 during brief maintenance; retry.</p>
<h2>4. Reserved order</h2>
<p><code>POST /v2/order</code> with the same three headers and JSON body
<code>{{"account_no": "..", "product_code": "..", "symbol": "..", "side": "buy" or "sell", "quantity": 1, "price": 1000}}</code>.
The order is sent to the market at the next open. Returns <code>{{"order_id": "...", "status": "reserved"}}</code>.</p>
</body></html>"""


class Vendor:
    """The synthetic API's state and an HTTP server on ``127.0.0.1``."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.tokens = {}
        self.issued, self.refused_tokens, self.balance_calls, self.unauthorized = 0, 0, 0, 0
        self.docs_fetches = 0
        self.last_issue = None
        self.failed_once = False
        #: What each token request carried, without values: which keys, and whether each credential
        #: arrived as the real value, an unsubstituted sentinel, something else, or not at all.
        self.token_requests = []
        #: #1456: reserved orders received (values checked, never kept: whether the key arrived real).
        self.orders = []
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self._handler())
        self.port = self.server.server_port
        self.host = f'127.0.0.1:{self.port}'
        self.base = f'http://{self.host}'

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def stats(self):
        return {'tokens_issued': self.issued, 'tokens_refused': self.refused_tokens, 'docs_fetches': self.docs_fetches,
                'balance_calls': self.balance_calls, 'unauthorized': self.unauthorized,
                'token_requests': self.token_requests[-4:], 'orders': self.orders}

    @staticmethod
    def _seen(value, expected):
        if value is None:
            return 'missing'
        if value == expected:
            return 'real'
        return 'sentinel' if str(value).startswith('fake_value') else 'other'

    def _authorized(self, headers):
        token = headers.get('X-Token') or ''
        return (headers.get('X-App-Key') == APP_KEY and headers.get('X-App-Secret') == APP_SECRET
                and self.tokens.get(token, 0) > self.clock())

    def _handler(self):
        vendor = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body, kind='application/json'):
                data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                parts = urlsplit(self.path)
                if parts.path == '/docs':
                    vendor.docs_fetches += 1
                    return self.reply(200, DOCS.format(base=vendor.base), 'text/html; charset=utf-8')
                with vendor.lock:
                    if not vendor._authorized(self.headers):
                        vendor.unauthorized += 1
                        return self.reply(401, {'error': 'invalid token or app credentials'})
                    if parts.path == '/v2/accounts':
                        return self.reply(200, {'accounts': ACCOUNTS})
                    if parts.path == '/v2/balance':
                        vendor.balance_calls += 1
                        if not vendor.failed_once:
                            vendor.failed_once = True
                            return self.reply(502, {'error': 'maintenance, retry'})
                        query = parse_qs(parts.query)
                        key = (query.get('account_no', [''])[0], query.get('product_code', [''])[0])
                        if key not in BALANCES:
                            return self.reply(404, {'error': 'unknown account'})
                        return self.reply(200, {'account_no': key[0], **BALANCES[key]})
                return self.reply(404, {'error': 'not found'})

            def body(self):
                """The request body, also when sent chunked (a proxy that substitutes values may re-frame it)."""
                if 'chunked' in (self.headers.get('Transfer-Encoding') or '').lower():
                    data = b''
                    while True:
                        size = int(self.rfile.readline().split(b';')[0].strip() or b'0', 16)
                        if size == 0:
                            self.rfile.readline()
                            return data
                        data += self.rfile.read(size)
                        self.rfile.readline()
                return self.rfile.read(int(self.headers.get('Content-Length') or 0))

            def do_POST(self):
                try:
                    body = json.loads(self.body() or b'{}')
                except ValueError:
                    return self.reply(400, {'error': 'json body required'})
                if urlsplit(self.path).path == '/v2/order':
                    with vendor.lock:
                        if not vendor._authorized(self.headers):
                            vendor.unauthorized += 1
                            return self.reply(401, {'error': 'invalid token or app credentials'})
                        if not isinstance(body, dict) or body.get('side') not in ('buy', 'sell'):
                            return self.reply(400, {'error': 'side, symbol, quantity and price required'})
                        order_id = f'R{len(vendor.orders) + 1:04d}'
                        vendor.orders.append({'order_id': order_id, 'side': body.get('side'), 'symbol': body.get('symbol'),
                                              'quantity': body.get('quantity'), 'price': body.get('price')})
                        return self.reply(200, {'order_id': order_id, 'status': 'reserved'})
                if urlsplit(self.path).path != '/oauth/issue':
                    return self.reply(404, {'error': 'not found'})
                with vendor.lock:
                    vendor.token_requests.append({'keys': sorted(body) if isinstance(body, dict) else type(body).__name__,
                                                  'grant': body.get('grant') if isinstance(body, dict) else None,
                                                  'app_key': vendor._seen(body.get('app_key'), APP_KEY),
                                                  'app_secret': vendor._seen(body.get('app_secret'), APP_SECRET),
                                                  'content_type': self.headers.get('Content-Type')})
                    if body.get('grant') != 'client' or body.get('app_key') != APP_KEY or body.get('app_secret') != APP_SECRET:
                        vendor.unauthorized += 1
                        return self.reply(403, {'error': 'invalid app credentials'})
                    now = vendor.clock()
                    if vendor.last_issue is not None and now - vendor.last_issue < TOKEN_INTERVAL:
                        vendor.refused_tokens += 1
                        return self.reply(403, {'error': 'token already issued within the last minute; reuse it'})
                    vendor.last_issue, vendor.issued = now, vendor.issued + 1
                    token = f'rehearsal-token-{vendor.issued}-{int(now)}-' + 'z' * 24
                    vendor.tokens[token] = now + 86400
                    return self.reply(200, {'token': token, 'expires_in': 86400})

        return Handler


PARCEL_KEY = 'rehearsal-parcel-key-91c4e2a7'
#: Invoice (as the owner writes it) -> what the tracking API returns. The codes are what a right answer carries.
PARCELS = {'6100-2233-4455': {'status': 'delivered', 'location': 'front door', 'signed_by': 'RH-7731',
                              'events': [{'at': '2026-10-12T09:10', 'what': 'out for delivery'},
                                         {'at': '2026-10-12T14:05', 'what': 'delivered'}]},
           '6100-9988-7766': {'status': 'in_transit', 'location': 'hub RH-HUB-0942', 'signed_by': None,
                              'events': [{'at': '2026-10-13T21:40', 'what': 'arrived at hub RH-HUB-0942'}]}}

PARCEL_DOCS = """<html><body><h1>Rehearsal Parcel Tracking API</h1>
<p>Base URL: <code>{base}</code>. All responses are JSON.</p>
<h2>Authentication</h2>
<p>Send <code>Authorization: ApiKey &lt;your api key&gt;</code> on every call.</p>
<h2>Tracking</h2>
<p><code>GET /api/v3/tracking?invoice=&lt;invoice&gt;</code>. The invoice is the 12-digit number printed on the
label, sent <b>without dashes</b>. Returns <code>{{"invoice": "...", "status": "delivered" or "in_transit",
"location": "...", "signed_by": "...", "events": [{{"at": "...", "what": "..."}}]}}</code>.</p>
<p>Errors: 401 bad key; 400 invoice must be 12 digits; 404 unknown invoice.</p>
</body></html>"""


class ParcelVendor:
    """#1489: a non-finance synthetic API (parcel tracking) on ``127.0.0.1``."""

    def __init__(self):
        self.docs_fetches, self.lookups, self.unauthorized, self.rejected = 0, [], 0, 0
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self._handler())
        self.port = self.server.server_port
        self.host = f'127.0.0.1:{self.port}'
        self.base = f'http://{self.host}'

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def stats(self):
        return {'docs_fetches': self.docs_fetches, 'lookups': list(self.lookups), 'unauthorized': self.unauthorized,
                'rejected': self.rejected}

    def _handler(self):
        vendor = self
        by_digits = {invoice.replace('-', ''): row for invoice, row in PARCELS.items()}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body, kind='application/json'):
                data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                parts = urlsplit(self.path)
                if parts.path == '/docs':
                    vendor.docs_fetches += 1
                    return self.reply(200, PARCEL_DOCS.format(base=vendor.base), 'text/html; charset=utf-8')
                # As real APIs do, the refusal says what is wrong (never the key).
                scheme, _, key = (self.headers.get('Authorization') or '').partition(' ')
                if not scheme:
                    vendor.unauthorized += 1
                    return self.reply(401, {'error': 'missing Authorization header; send Authorization: ApiKey <api key>'})
                if scheme != 'ApiKey':
                    vendor.unauthorized += 1
                    return self.reply(401, {'error': f'unsupported Authorization scheme {scheme[:20]!r}; use ApiKey'})
                if key != PARCEL_KEY:
                    vendor.unauthorized += 1
                    return self.reply(401, {'error': 'invalid api key'})
                if parts.path != '/api/v3/tracking':
                    return self.reply(404, {'error': 'not found'})
                invoice = parse_qs(parts.query).get('invoice', [''])[0]
                if not (len(invoice) == 12 and invoice.isdigit()):
                    vendor.rejected += 1
                    return self.reply(400, {'error': 'invoice must be 12 digits without dashes'})
                vendor.lookups.append(invoice)
                if invoice not in by_digits:
                    return self.reply(404, {'error': 'unknown invoice'})
                return self.reply(200, {'invoice': invoice, **by_digits[invoice]})

        return Handler
