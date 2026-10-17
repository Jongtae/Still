"""Owner-controlled local context inbox.

This module deliberately accepts only a *user submitted* item.  It does not
read the clipboard, browser, or any other host surface.  Captured text remains
in the owner database; callers must separately opt in a named assistant and a
particular sharing operation before a payload is returned.
"""
import hashlib
import ipaddress
import re
import time
import uuid
from urllib.parse import urlsplit


MAX_CONTENT = 12000
MAX_RETENTION = 90 * 24 * 60 * 60
DEFAULT_RETENTION = 7 * 24 * 60 * 60
SENSITIVE = re.compile(
    r"(?:\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|otp)\b\s*[:=]\s*\S+|"
    r"\b(?:\d[ -]*?){13,19}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.)", re.I)


def _safe_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.rstrip('.').lower()
        if host in ('localhost',) or host.endswith('.local'):
            return False
        try:
            address = ipaddress.ip_address(host)
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified:
                return False
        except ValueError:
            pass
        return True
    except (TypeError, ValueError):
        return False


class ContextInbox:
    def __init__(self, store):
        self.store = store

    def _enabled(self, source_kind):
        settings = self.store.config('context_inbox', {})
        return bool(settings.get('sources', {}).get(source_kind, False))

    def configure(self, body):
        sources = body.get('sources') if isinstance(body, dict) else None
        if not isinstance(sources, dict) or set(sources) - {'text', 'url'}:
            raise ValueError('텍스트와 URL 수집만 설정할 수 있습니다.')
        if any(not isinstance(value, bool) for value in sources.values()):
            raise ValueError('수집 설정은 켜기 또는 끄기여야 합니다.')
        current = self.store.config('context_inbox', {})
        self.store.put('context_inbox', {'sources': {'text': bool(sources.get('text', current.get('sources', {}).get('text', False))),
                                                   'url': bool(sources.get('url', current.get('sources', {}).get('url', False)))}})
        return self.status()

    def status(self):
        settings = self.store.config('context_inbox', {})
        return {'sources': {'text':bool(settings.get('sources', {}).get('text', False)),
                            'url':bool(settings.get('sources', {}).get('url', False))},
                'local_only': True,
                'sharing_requires_policy_and_per_request_approval': True,
                'items': self.list()}

    def capture(self, body):
        if not isinstance(body, dict): raise ValueError('수집할 내용을 확인하세요.')
        kind = body.get('source_kind')
        content = body.get('content')
        if kind not in ('text', 'url') or not isinstance(content, str):
            raise ValueError('텍스트 또는 URL만 수집할 수 있습니다.')
        content = content.strip()
        if not self._enabled(kind): raise ValueError('이 소스의 수집을 먼저 명시적으로 켜세요.')
        if not content or len(content) > MAX_CONTENT: raise ValueError('수집 내용은 1~12,000자여야 합니다.')
        if SENSITIVE.search(content): raise ValueError('민감한 정보로 보이는 내용은 저장하지 않았습니다.')
        if kind == 'url' and not _safe_url(content): raise ValueError('공용 HTTP/HTTPS URL만 저장할 수 있습니다.')
        retention = body.get('retention_seconds', DEFAULT_RETENTION)
        if not isinstance(retention, int) or not 60 <= retention <= MAX_RETENTION:
            raise ValueError('보존 기간은 1분에서 90일 사이여야 합니다.')
        now = time.time(); digest = hashlib.sha256((kind+'\0'+content).encode()).hexdigest()
        with self.store.db() as db:
            db.execute('DELETE FROM context_events WHERE expires_at<=?', (now,))
            old = db.execute('SELECT id,captured_at,expires_at FROM context_events WHERE content_hash=? AND expires_at>?', (digest, now)).fetchone()
            if old: return {'id':old['id'], 'duplicate':True, 'captured_at':old['captured_at'], 'expires_at':old['expires_at']}
            event_id = str(uuid.uuid4())
            db.execute('INSERT INTO context_events VALUES (?,?,?,?,?,?,?,?,?)',
                       (event_id, now, kind, content, digest, now + retention, 'local_only', body.get('source_app','') if isinstance(body.get('source_app',''),str) else '', body.get('source_domain','') if isinstance(body.get('source_domain',''),str) else ''))
        return {'id':event_id, 'duplicate':False, 'captured_at':now, 'expires_at':now+retention}

    def list(self):
        now=time.time()
        with self.store.db() as db:
            db.execute('DELETE FROM context_events WHERE expires_at<=?', (now,))
            rows=db.execute('SELECT id,captured_at,source_kind,expires_at,sharing_state,source_app,source_domain FROM context_events ORDER BY captured_at DESC LIMIT 100').fetchall()
        return [dict(row) for row in rows]

    def delete(self, event_id=None):
        with self.store.db() as db:
            if event_id is None:
                count=db.execute('DELETE FROM context_events').rowcount
                db.execute('DELETE FROM context_sharing_policies')
            else:
                if not isinstance(event_id,str) or not event_id: raise ValueError('삭제할 인박스 항목을 확인하세요.')
                count=db.execute('DELETE FROM context_events WHERE id=?',(event_id,)).rowcount
        return {'deleted':count}

    def set_policy(self, body):
        if not isinstance(body,dict) or not isinstance(body.get('assistant_id'),str) or not body['assistant_id']:
            raise ValueError('공유할 어시스턴트를 확인하세요.')
        if body.get('approved') is not True: raise ValueError('명시적인 공유 승인만 설정할 수 있습니다.')
        with self.store.db() as db:
            db.execute('INSERT INTO context_sharing_policies VALUES (?,?,?) ON CONFLICT(assistant_id) DO UPDATE SET approved=excluded.approved,approved_at=excluded.approved_at', (body['assistant_id'],1,time.time()))
        return {'assistant_id':body['assistant_id'],'approved':True}

    def policy_approved(self, assistant_id):
        with self.store.db() as db:
            row=db.execute('SELECT approved FROM context_sharing_policies WHERE assistant_id=?',(assistant_id,)).fetchone()
        return bool(row and row['approved'])

    def share(self, body):
        """Return payload only for an approved assistant and this approved request."""
        if not isinstance(body,dict) or body.get('approved') is not True: raise ValueError('각 공유 요청은 명시적으로 승인해야 합니다.')
        assistant=body.get('assistant_id'); ids=body.get('event_ids')
        if not isinstance(assistant,str) or not assistant or not isinstance(ids,list) or not ids or len(ids)>20 or any(not isinstance(i,str) for i in ids):
            raise ValueError('어시스턴트와 최대 20개의 인박스 항목을 확인하세요.')
        with self.store.db() as db:
            policy=db.execute('SELECT approved FROM context_sharing_policies WHERE assistant_id=?',(assistant,)).fetchone()
            if not policy or not policy['approved']: raise ValueError('이 어시스턴트의 공유 정책을 먼저 승인하세요.')
            marks=','.join('?' for _ in ids)
            rows=db.execute(f'SELECT id,captured_at,source_kind,content,expires_at,source_app,source_domain FROM context_events WHERE id IN ({marks}) AND expires_at>?', (*ids,time.time())).fetchall()
        if len(rows)!=len(set(ids)): raise ValueError('만료되었거나 없는 인박스 항목은 공유할 수 없습니다.')
        return {'assistant_id':assistant,'items':[dict(row) for row in rows], 'untrusted':True, 'local_copy_retained':True}
