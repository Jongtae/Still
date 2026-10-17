"""CONNECT-FIND-01: metadata discovery, never connection or execution authority.

Adapt the existing bounded no-redirect HTTP transport and local CLI config
reader. The official Registry searches server names, not semantic capability.
Its metadata is untrusted; registration says nothing about usable tools.
"""
from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import urlencode, urlsplit

from .api_requests import http_request
from .context_inbox import SENSITIVE

REGISTRY = 'https://registry.modelcontextprotocol.io/v0.1/servers'
MAX_BYTES = 256 * 1024
MAX_RESULTS = 10
TTL = 3600
NAME_QUERY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,79}\Z')


def _text(value, limit=200):
    return value[:limit] if isinstance(value, str) else ''


def _public_url(value):
    if not isinstance(value, str) or len(value) > 1000:
        return ''
    try:
        parts = urlsplit(value)
        if parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password and not parts.query and not parts.fragment:
            return value
    except ValueError:
        pass
    return ''


def _entry(row):
    if not isinstance(row, dict) or not isinstance(row.get('server'), dict):
        return None
    server = row['server']
    meta = row.get('_meta') if isinstance(row.get('_meta'), dict) else {}
    official = meta.get('io.modelcontextprotocol.registry/official', {})
    if not isinstance(official, dict) or official.get('status') in ('deleted', 'deprecated'):
        return None
    name, version = _text(server.get('name')), _text(server.get('version'), 80)
    if not name or not version:
        return None
    repository = server.get('repository') if isinstance(server.get('repository'), dict) else {}
    remotes = server.get('remotes') if isinstance(server.get('remotes'), list) else []
    packages = server.get('packages') if isinstance(server.get('packages'), list) else []
    return {'name': name, 'version': version, 'description': _text(server.get('description'), 600),
            'publisher_namespace': name.rpartition('/')[0] or None,
            'licence': 'unknown', 'repository': _public_url(repository.get('url')) or None,
            'remotes': [{'type': _text(item.get('type'), 40), 'url': _public_url(item.get('url'))}
                        for item in remotes[:4] if isinstance(item, dict) and _public_url(item.get('url'))],
            'packages': [{'registry': _text(item.get('registryType'), 30),
                          'identifier': _text(item.get('identifier')), 'version': _text(item.get('version'), 80)}
                         for item in packages[:4] if isinstance(item, dict)],
            'state': 'discovered', 'authenticated': 'unknown', 'tools': 'unknown',
            'scope': 'unknown', 'authority': 'none', 'untrusted': True}


class CapabilitySearch:
    """A bounded in-memory metadata cache; canonical owner state stays outside."""
    def __init__(self, transport=http_request, now=time.time):
        self.transport, self.now = transport, now
        self._cache = {}
        self._lock = threading.Lock()

    def registry(self, query, redact=lambda text: text):
        # A short public server-name token, never the request, document, URL,
        # email, credentials or a free-form semantic query.
        if not isinstance(query, str) or not NAME_QUERY.fullmatch(query) or SENSITIVE.search(query) or redact(query) != query:
            raise ValueError('공개 서버 이름만 검색하세요. 요청 본문이나 개인 정보는 넣지 마세요.')
        with self._lock:
            now = self.now()
            cached = self._cache.get(query)
            if cached and 0 <= now - cached['retrieved_at'] < (TTL if cached['state']=='observed' else 30):
                entries=[entry for entry in cached['results'] if redact(json.dumps(entry))==json.dumps(entry)]
                return {**cached, 'results': entries, 'contacted': False, 'cached': True,
                        'completeness':cached['completeness'] if len(entries)==len(cached['results']) else 'partial'}
            url = REGISTRY + '?' + urlencode({'search': query, 'limit': MAX_RESULTS, 'version': 'latest'})
            result = {'source': REGISTRY, 'query': query, 'retrieved_at': now, 'contacted': True,
                      'cached': False, 'results': [], 'state': 'unavailable', 'completeness': 'unknown',
                      'search_kind': 'server_name_substring', 'untrusted': True}
            try:
                status, _headers, payload = self.transport('GET', url, {'Accept': 'application/json'}, None, 10)
                if status != 200 or not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
                    raise ValueError('registry response')
                data = json.loads(payload)
                if not isinstance(data, dict) or not isinstance(data.get('servers'), list):
                    raise ValueError('registry shape')
                entries = [entry for row in data['servers'][:MAX_RESULTS] if (entry := _entry(row)) is not None]
                # Do not echo a poisoned response carrying a known credential.
                entries = [entry for entry in entries if not SENSITIVE.search(json.dumps(entry))
                           and redact(json.dumps(entry)) == json.dumps(entry)]
                metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
                result.update(results=entries, state='observed', completeness='partial' if
                              metadata.get('nextCursor') or len(entries) != len(data['servers']) else 'complete')
            except (OSError, TimeoutError, ValueError, TypeError, RecursionError):
                pass  # No arbitrary server error text or payload reaches a model/evidence.
            if len(self._cache) >= 32:
                self._cache.pop(next(iter(self._cache)))
            self._cache[query] = result
            return result
