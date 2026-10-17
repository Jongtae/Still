"""RFC 6749 client-credentials through oauthlib and the existing API transport.

oauthlib owns request construction and token response parsing. This thin adapter
keeps the owner-confirmed destination/scope, encrypted references, revision and
cache publication subordinate to Still. No issuer-specific protocol branches.
"""
import base64
import json
import math
import re
from urllib.parse import quote_plus

from oauthlib.oauth2 import BackendApplicationClient

from .api_credentials import ApiCredentials, CredentialError, fingerprint, credential_values

SCOPE = re.compile(r'[\x21\x23-\x5b\x5d-\x7e]{1,100}\Z')
MAX_TOKEN_SECONDS = 31 * 86400
EXPIRY_MARGIN = 5


def configuration(value):
    from .mcp_setup import remote_url
    if (not isinstance(value, dict) or set(value) != {'token_url', 'client_auth', 'scope'}
            or value['client_auth'] not in ('basic', 'body')
            or not isinstance(value['scope'], list) or len(value['scope']) > 16
            or any(not isinstance(s, str) or not SCOPE.fullmatch(s) for s in value['scope'])):
        raise ValueError('토큰 발급 주소, basic/body 인증 방식, 요청 범위를 확인하세요.')
    return {'token_url': remote_url(value['token_url']), 'client_auth': value['client_auth'],
            'scope': sorted(set(value['scope']))}


def _token_slot(slot, reference):
    return {**slot, 'credential_parent': slot['credential_ref'], 'credential_ref': reference,
            'token_expires_at': slot['token_cache']['expires_at']}


def _cache_value(credentials, name, slot, clock):
    cached = slot.get('token_cache') or {}
    expiry = cached.get('expires_at')
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not math.isfinite(expiry):
        return None
    if expiry <= clock() + EXPIRY_MARGIN:
        return None
    token = credentials.read(name, _token_slot(slot, cached.get('reference')))
    if not isinstance(token, str):
        raise CredentialError()
    return token


def _publish_cache(store, name, slot, cached):
    """Cache changes never widen a connection or bump its credential revision."""
    from .api_requests import SLOTS_KEY
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT value FROM config WHERE key=?', (SLOTS_KEY,)).fetchone()
        rows = json.loads(found['value']) if found else {}
        current = rows.get(name) or {}
        if (current.get('credential_ref') != slot['credential_ref'] or fingerprint(name, current) != fingerprint(name, slot)
                or current.get('token_cache') != slot.get('token_cache')):
            raise CredentialError()
        if cached is None:
            current.pop('token_cache', None)
        else:
            current['token_cache'] = cached
        db.execute('UPDATE config SET value=? WHERE key=?', (json.dumps(rows), SLOTS_KEY))


def resolve(store, name, slot, *, transport, clock, current, staged=False):
    """Resolve one bearer; retain encrypted retired tokens for secret redaction.

    For input readiness, the candidate receives its cache but no active slot is
    published. For ordinary calls, cache publication compares the current exact
    credential and cache; a concurrent winner is never overwritten.
    """
    from .api_requests import ApiError, TIMEOUT, MAX_RESPONSE_BYTES, MAX_BODY_BYTES
    credentials = ApiCredentials(store)
    current()
    secret = credentials.read(name, slot)
    if not isinstance(secret, dict):
        raise CredentialError()
    spec = configuration(slot['oauth'])
    token = _cache_value(credentials, name, slot, clock)
    if token is None:
        client = BackendApplicationClient(secret['client_id'], scope=spec['scope'])
        kwargs = {'include_client_id': True, 'client_secret': secret['client_secret']} if spec['client_auth'] == 'body' else {}
        try:
            url, headers, body = client.prepare_token_request(spec['token_url'], **kwargs)
            # oauthlib's form header map is shared; never mutate that map.
            headers = dict(headers)
            if spec['client_auth'] == 'basic':
                pair = quote_plus(secret['client_id']) + ':' + quote_plus(secret['client_secret'])
                headers['Authorization'] = 'Basic ' + base64.b64encode(pair.encode()).decode()
            if len(body.encode()) > MAX_BODY_BYTES:
                raise CredentialError()
            current()
            started = clock()
            status, _, raw = transport('POST', url, headers, body.encode(), TIMEOUT)
            current()
            if status != 200 or len(raw) > MAX_RESPONSE_BYTES:
                raise CredentialError()
            # Do not forward issuer bodies or exception messages. In particular
            # OAuth errors can include the original credential in descriptions.
            parsed = client.parse_request_body_response(raw.decode('utf-8'), scope=spec['scope'])
            duration = parsed.get('expires_in')
            token = parsed.get('access_token')
            if (str(parsed.get('token_type', '')).lower() != 'bearer'
                    or not isinstance(duration, (int, float)) or isinstance(duration, bool)
                    or not math.isfinite(duration) or not EXPIRY_MARGIN < duration <= MAX_TOKEN_SECONDS
                    or set(parsed.get('scope', spec['scope'])) != set(spec['scope'])):
                raise CredentialError()
            credential_values(token)
            if not isinstance(token, str) or started + duration <= clock() + EXPIRY_MARGIN:
                raise CredentialError()
        except Exception:
            raise ApiError('토큰 발급 또는 요청 범위를 확인하지 못했습니다. 인증 정보와 연결 방식을 확인하세요.',
                           'api_token_unavailable') from None
        reference = credentials.stage(name, {**slot, 'credential_parent': slot['credential_ref'],
                                             'token_expires_at': started + duration}, token)
        cached = {'reference': reference, 'expires_at': started + duration}
        try:
            current()
            if not staged:
                _publish_cache(store, name, slot, cached)
            slot['token_cache'] = cached
        except Exception:
            credentials.discard(reference)
            raise
    current()

    def finish(status):
        current()
        if status == 401 and not staged:
            _publish_cache(store, name, slot, None)
        current()

    return token, finish
