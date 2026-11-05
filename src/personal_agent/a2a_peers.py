"""Owner decision #651: two-way A2A conversation; a counterpart is never the owner.

Adapt (C15): a2a-sdk 1.3.0 owns cards, protobuf and HTTP. QuickStore owns
atomic admission/Work/Evidence; EncryptedDriveSecretStore owns Fernet storage.
MP1 D-04 supplies minimum disclosure and terminal-state principles, not its
retired fixture peer. No peer name, skill or message decides authority here.
The owner's AI writes the answers; this module keeps identity and containment.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from .drive_web_oauth import EncryptedDriveSecretStore
from .messages import msg
from .work_mode import WorkModeRefused
from .telegram_locale import ai_text, owner_language

REGISTRY = 'a2a_peers'
SECTIONS = frozenset({'profile', 'memory', 'current_context', 'folders', 'accounts'})
READ_TOOLS = {'list_memory': 'memory', 'search_memory': 'memory',
              'list_roots': 'folders', 'find_files': 'folders', 'read_file': 'folders',
              'calendar_query': 'accounts', 'drive_search': 'accounts', 'drive_read': 'accounts',
              'mail_search': 'accounts', 'mail_read': 'accounts'}
TERMINAL = ('completed', 'failed', 'cancelled')


class PeerError(ValueError):
    """A bounded status code, never an SDK exception or credential payload."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def confirmation_digest(card, card_url=None):
    """Confirm the discovery source as well as the reviewed card's contents."""
    return digest({'card': card, 'card_url': card_url}) if card_url else digest(card)


def endpoint(url):
    """HTTPS to the registered address; plain HTTP only to literal local fixtures."""
    parsed = urlsplit(url)
    if (parsed.username or parsed.password or parsed.fragment or parsed.query or not parsed.hostname
            or parsed.scheme not in ('http', 'https')
            or parsed.scheme == 'http' and parsed.hostname != '127.0.0.1'):
        raise PeerError('invalid_peer_endpoint')
    return url


def disclosure(value):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'sections', 'tools'}:
        raise PeerError('invalid_disclosure_scope')
    sections, tools = value.get('sections', []), value.get('tools', [])
    if (not isinstance(sections, list) or not isinstance(tools, list)
            or any(not isinstance(item, str) or item not in SECTIONS for item in sections)
            or any(not isinstance(item, str) or READ_TOOLS.get(item) not in sections for item in tools)):
        raise PeerError('invalid_disclosure_scope')
    return {'sections': sorted(set(sections)), 'tools': sorted(set(tools))}


class PeerSecrets:
    """Adapt the existing encrypted store, with an independent Keychain item.

    The generic redaction ledger receives keyed marks at issuance, so even a
    credential quoted back by a peer is removed from prompts and exports. Raw
    credentials occur only in this encrypted store and the one-time response.
    """
    def __init__(self, store, key=None):
        self.store, self.key = store, key
        self._encrypted = None
        self.lock = threading.RLock()

    def encrypted(self, writing=False):
        with self.lock:
            if self._encrypted is None:
                key = self.key() if callable(self.key) else self.key
                if key is None:
                    from .browser_jar import KeychainKey, store_account
                    holder = KeychainKey(store_account(self.store.root), 'Still.A2A')
                    key = holder.get()
                    if key is None and writing:
                        # Never generate a replacement for an existing encrypted store.
                        with self.store.secret_lock:
                            values = json.loads(self.store.secret_path.read_text()) if self.store.secret_path.exists() else {}
                        if not any(name.startswith('encrypted:a2a:') for name in values):
                            key = holder.create()
                if not key:
                    raise PeerError('peer_secret_key_unavailable')
                self._encrypted = EncryptedDriveSecretStore(self.store, key)
            return self._encrypted

    def get(self, peer_id):
        return self.encrypted().secret('a2a:' + peer_id) or {}

    def set(self, peer_id, values):
        for value in values.values():
            if value:
                self.store.retire_secret_values([value])
        self.encrypted(writing=True).secret('a2a:' + peer_id, values)


class PeerWorkTool:
    """The owner's Work-bound callback and its current public tool options."""
    def __init__(self, peers, work_id):
        self.peers, self.work_id = peers, work_id

    def options(self):
        clean = self.peers.service._redact_known_secrets
        return [{'id': row['id'], 'name': clean(row['card']['name'])[:100],
                 'sections': row['scope']['sections'], 'tools': row['scope']['tools']}
                for row in self.peers.rows().values() if not row['blocked'] and not row['removed']]

    def __call__(self, args):
        return self.peers.tool(self.work_id, args)


class A2APeers:
    def __init__(self, service, *, secret_key=None, clock=time.time):
        self.service, self.store, self.clock = service, service.store, clock
        self.secrets = PeerSecrets(self.store, secret_key)
        self._initialized = False
        self.lock = threading.RLock()
        self.server = self.thread = None

    def initialize(self):
        with self.lock:
            if self._initialized:
                return
            with self.store.db() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS a2a_turns(
                    id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, work_id TEXT NOT NULL,
                    direction TEXT NOT NULL, message_id TEXT NOT NULL, task_id TEXT,
                    question TEXT NOT NULL, answer TEXT, state TEXT NOT NULL,
                    revision TEXT NOT NULL, created REAL NOT NULL,
                    UNIQUE(peer_id,direction,message_id), UNIQUE(peer_id,direction,task_id))''')
                db.execute('''CREATE TABLE IF NOT EXISTS a2a_owner_items(
                    id TEXT PRIMARY KEY, work_id TEXT NOT NULL, peer_id TEXT NOT NULL,
                    request TEXT NOT NULL, state TEXT NOT NULL)''')
            self._initialized = True

    def rows(self, db=None):
        return self.store.config(REGISTRY, {}, db=db) or {}

    def peer(self, peer_id, db=None):
        row = self.rows(db).get(peer_id)
        if not row or row.get('blocked') or row.get('removed'):
            raise PeerError('peer_unavailable')
        return row

    def guard(self, peer_id, revision=None, db=None):
        row = self.peer(peer_id, db)
        if revision is not None and row['revision'] != revision:
            raise PeerError('peer_revision_changed')
        return row

    def status(self):
        # No dependency import, secret read or listener start on a Settings read.
        return {'peers': list(self.rows().values()), 'listener': '127.0.0.1',
                'running': bool(self.server and self.server.started and self.thread and self.thread.is_alive())}

    def transport(self):
        try:
            from . import a2a_transport
            a2a_transport.protect_sdk_logs(self.service)
            return a2a_transport
        except ImportError:
            raise PeerError('optional_a2a_extra_required') from None

    def prepare_credential(self, credential):
        # RFC 6750 syntax and Still's existing eight-character redaction floor.
        if (not isinstance(credential, str) or len(credential) > 4096 or credential and len(credential) < 8
                or credential and re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', credential) is None):
            raise PeerError('invalid_peer_credential')
        if credential:
            self.store.retire_secret_values([credential])
        return credential

    def reviewed_card(self, value):
        card = self.transport().parse_card(value)
        serialized = json.dumps(card)
        if self.service._redact_known_secrets(serialized) != serialized:
            raise PeerError('agent_card_contains_credential')
        return card

    def card_url(self, value):
        url = endpoint(value)
        if self.service._redact_known_secrets(url) != url:
            raise PeerError('peer_card_url_contains_credential')
        return url

    def register(self, body):
        """Called only by the authenticated, local owner Settings API."""
        self.initialize()
        body = body or {}
        credential = self.prepare_credential(body.get('outbound_credential', ''))
        public_card = self.reviewed_card(body.get('card'))
        card_url = self.card_url(body['card_url']) if body.get('card_url') else None
        if card_url and body.get('confirm_digest') != confirmation_digest(public_card, card_url):
            raise PeerError('card_confirmation_required')
        scope = disclosure(body.get('scope'))
        budget, rate = body.get('budget', 100), body.get('rate', 10)
        if any(type(n) is not int or not 1 <= n <= 10000 for n in (budget, rate)):
            raise PeerError('invalid_peer_limits')
        peer_id, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        self.secrets.set(peer_id, {'inbound': token, 'outbound': credential})
        row = {'id': peer_id, 'card': public_card, 'card_digest': digest(public_card),
               'scope': scope, 'budget': budget, 'rate': rate, 'used': 0,
               'revision': secrets.token_hex(16), 'blocked': False, 'removed': False,
               'card_url': card_url}
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = self.rows(db)
            rows[peer_id] = row
            self.store.put(REGISTRY, rows, db=db)
        return {'peer': row, 'inbound_credential': token}

    def update(self, body):
        """Exact digest confirmation, scope and limits all revoke in-flight authority."""
        self.initialize()
        peer_id = body.get('peer_id')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = self.rows(db)
            row = dict(self.peer(peer_id, db))
            if 'card' in body:
                card = self.reviewed_card(body['card'])
                if body.get('confirm_digest') != digest(card):
                    raise PeerError('card_confirmation_required')
                row.update(card=card, card_digest=digest(card))
            if 'scope' in body:
                row['scope'] = disclosure(body['scope'])
            for field in ('budget', 'rate'):
                if field in body:
                    if type(body[field]) is not int or not 1 <= body[field] <= 10000:
                        raise PeerError('invalid_peer_limits')
                    row[field] = body[field]
            row['revision'] = secrets.token_hex(16)
            rows[peer_id] = row
            self.store.put(REGISTRY, rows, db=db)
        return row

    def block(self, peer_id, remove=False):
        self.initialize()
        # Registry revocation commits first. Retained ciphertext is never authority.
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = self.rows(db)
            row = rows.get(peer_id)
            if not row:
                raise PeerError('peer_unavailable')
            row.update(blocked=True, removed=bool(remove), revision=secrets.token_hex(16))
            self.store.put(REGISTRY, rows, db=db)
            db.execute("UPDATE jobs SET status='cancelled',error='peer_revoked' WHERE id IN "
                       "(SELECT work_id FROM a2a_turns WHERE peer_id=? AND state='working')", (peer_id,))
            db.execute("UPDATE a2a_turns SET state='cancelled' WHERE peer_id=? AND state='working'", (peer_id,))
            db.execute("UPDATE a2a_owner_items SET state='cancelled' WHERE peer_id=? AND state='pending'", (peer_id,))
        try:
            self.secrets.set(peer_id, {})
            cleanup = 'complete'
        except (ValueError, OSError):
            # The committed registry revocation is already effective. Missing
            # keys must not undo it or imply a still-authorized credential.
            cleanup = 'pending'
        return {'blocked': True, 'removed': bool(remove), 'credential_cleanup': cleanup}

    def authenticate(self, authorization):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
            raise PeerError('unauthenticated_peer')
        token = authorization[7:]
        for peer_id, row in self.rows().items():
            if row.get('blocked') or row.get('removed'):
                continue
            expected = self.secrets.get(peer_id).get('inbound', '')
            if expected and hmac.compare_digest(expected, token):
                return peer_id
        raise PeerError('unauthenticated_peer')

    def conversations(self, peer_id=None):
        self.initialize()
        with self.store.db() as db:
            turns = [dict(row) for row in db.execute('SELECT * FROM a2a_turns WHERE (? IS NULL OR peer_id=?) ORDER BY created',
                                                     (peer_id, peer_id))]
            items = [dict(row) for row in db.execute('SELECT * FROM a2a_owner_items WHERE (? IS NULL OR peer_id=?)',
                                                     (peer_id, peer_id))]
        return {'turns': turns, 'owner_items': items}

    def admit(self, peer_id, message_id, task_id, question, direction='inbound', work_id=None):
        self.initialize()
        if direction == 'inbound' and self.server and self.server.should_exit:
            raise PeerError('peer_listener_stopping')
        if (not isinstance(message_id, str) or not 1 <= len(message_id) <= 256
                or task_id is not None and (not isinstance(task_id, str) or not 1 <= len(task_id) <= 256)
                or not isinstance(question, str) or not 1 <= len(question.strip()) <= 12000):
            raise PeerError('invalid_peer_message')
        question = self.service._redact_known_secrets(question)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.guard(peer_id, db=db)
            old = db.execute('SELECT * FROM a2a_turns WHERE peer_id=? AND direction=? AND (message_id=? OR task_id=?)',
                             (peer_id, direction, message_id, task_id)).fetchone()
            if old:
                if old['revision'] != row['revision']:
                    raise PeerError('peer_revision_changed')
                if old['question'] != question:
                    raise PeerError('duplicate_peer_request_conflict')
                return dict(old), False
            refusal = self.admission_refusal(peer_id, row, db)
            if refusal:
                raise PeerError(refusal)
            turn_id = secrets.token_hex(16)
            if direction == 'inbound':
                # A different state as well as channel: the owner worker cannot claim it.
                work_id = self.store.enqueue(question, 'a2a:' + turn_id, 'a2a-peer:' + peer_id,
                                             db=db, owner_typed=False)
                db.execute("UPDATE jobs SET status='peer-running' WHERE id=?", (work_id,))
            else:
                work = db.execute('SELECT * FROM jobs WHERE id=?', (work_id,)).fetchone()
                if not work or work['channel'].startswith('a2a-peer:') or work['status'] != 'running':
                    raise PeerError('owner_work_required')
            db.execute('INSERT INTO a2a_turns VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (turn_id, peer_id, work_id, direction, message_id, task_id, question, None, 'working',
                        row['revision'], self.clock()))
            result = dict(db.execute('SELECT * FROM a2a_turns WHERE id=?', (turn_id,)).fetchone())
        return result, True

    def admission_refusal(self, peer_id, row, db):
        if row['used'] >= row['budget']:
            return 'peer_budget_exhausted'
        recent = db.execute('SELECT count(*) FROM a2a_turns WHERE peer_id=? AND created>?',
                            (peer_id, self.clock() - 60)).fetchone()[0]
        return 'peer_rate_exceeded' if recent >= row['rate'] else None

    def spend(self, peer_id, revision):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.guard(peer_id, revision, db)
            if row['used'] >= row['budget']:
                raise PeerError('peer_budget_exhausted')
            rows = self.rows(db)
            rows[peer_id]['used'] += 1
            self.store.put(REGISTRY, rows, db=db)

    def record(self, work_id, status, detail):
        payload = self.service._redact_known_secrets(json.dumps(detail, ensure_ascii=False))
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work_id, 'a2a_message', status, payload, self.clock()))

    def finish(self, turn, answer, state='completed'):
        answer = self.service._redact_known_secrets(str(answer))[:24000]
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.guard(turn['peer_id'], turn['revision'], db)
            current = db.execute('SELECT state FROM a2a_turns WHERE id=?', (turn['id'],)).fetchone()
            if not current or current['state'] != 'working':
                raise PeerError('peer_task_not_running')
            db.execute('UPDATE a2a_turns SET answer=?,state=? WHERE id=?', (answer, state, turn['id']))
            if turn['direction'] == 'inbound':
                db.execute('UPDATE jobs SET status=?,response=? WHERE id=?',
                           ('succeeded' if state == 'completed' else 'failed', answer, turn['work_id']))
        return answer

    def fail(self, turn, code):
        with self.store.db() as db:
            db.execute("UPDATE a2a_turns SET state='failed',answer=? WHERE id=? AND state='working'", (code, turn['id']))
            if turn['direction'] == 'inbound':
                db.execute("UPDATE jobs SET status='failed',error=? WHERE id=? AND status='peer-running'", (code, turn['work_id']))

    def receive(self, peer_id, message_id, task_id, text):
        turn, fresh = self.admit(peer_id, message_id, task_id, text)
        if not fresh:
            return turn
        try:
            from .a2a_work import answer_peer
            answer = answer_peer(self, turn)
            answer = self.finish(turn, answer)
            provenance = self.store.turn_provenance(turn['work_id']) or {}
            used_sections = provenance.get('peer_context_sections') or []
            used_tools = sorted({event['trace'].get('host_action') or event['tool']
                for event in self.store.task_events(turn['work_id']) if event['status'] == 'succeeded'
                and (event['trace'].get('host_action') or event['tool']) in self.peer(peer_id)['scope']['tools']})
            self.record(turn['work_id'], 'succeeded', {'peer_id': peer_id, 'direction': 'inbound',
                        'destination': self.peer(peer_id)['card']['supportedInterfaces'][0]['url'],
                        'sections': used_sections, 'tools': used_tools, 'state': 'response-returned'})
            return {**turn, 'answer': answer, 'state': 'completed'}
        except Exception as exc:
            code = str(exc) if isinstance(exc, PeerError) else 'peer_work_failed'
            self.fail(turn, code)
            with self.store.db() as db:
                current = db.execute('SELECT state FROM a2a_turns WHERE id=?', (turn['id'],)).fetchone()
            state = 'cancelled' if current and current['state'] == 'cancelled' else 'failed'
            return {**turn, 'answer': code, 'state': state}

    def propose_owner_item(self, turn, text):
        text = self.service._redact_known_secrets(str(text))[:12000]
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.guard(turn['peer_id'], turn['revision'], db)
            work = db.execute('SELECT status FROM jobs WHERE id=?', (turn['work_id'],)).fetchone()
            if not work or work['status'] != 'peer-running':
                raise PeerError('peer_task_not_running')
            old = db.execute('SELECT id FROM a2a_owner_items WHERE work_id=? AND request=?',
                             (turn['work_id'], text)).fetchone()
            item_id = old['id'] if old else secrets.token_hex(16)
            if not old:
                db.execute('INSERT INTO a2a_owner_items VALUES (?,?,?,?,?)',
                           (item_id, turn['work_id'], turn['peer_id'], text, 'pending'))
        if not old:
            for surface in self.service.surfaces:
                pair = surface.pairing()
                if pair:
                    # Reuse the language decision over the latest owner-typed Work
                    # on this paired conversation, never over the peer's words.
                    with self.store.db() as db:
                        latest = db.execute('SELECT id FROM jobs WHERE channel=? AND chat_id=? '
                                            'AND owner_typed=1 ORDER BY created DESC LIMIT 1',
                                            (pair.channel, pair.owner)).fetchone()
                    lang = owner_language(self.store, latest['id'] if latest else None,
                                          default='en' if latest else 'ko') or 'ko'
                    name = self.service._redact_known_secrets(self.peer(turn['peer_id'])['card']['name'])
                    notice = msg('a2a.owner_review', lang, name=name, request=text)
                    # Already localized. Protect the quoted third-party prose and
                    # display name from Telegram's fixed-line template matcher.
                    # No notification/open-item binding: this is never an effect approval.
                    surface.send_owner(ai_text(notice), owner=pair.owner)
        return {'state': 'awaiting_owner', 'item_id': item_id, 'effect': 'none',
                'response': 'The request was recorded for the owner. No action was performed.'}

    def settle_owner_item(self, body):
        """Local owner acknowledgment/refusal; neither is executable authority.

        A natural-language request has no exact operation/parameter binding.
        Treating its acknowledgment as a queued privileged Work would launder
        peer text into pilot mutation authority and current Memory. The owner
        may separately instruct their own AI on their own conversation surface;
        that Work retains all existing exact action/payment gates.
        """
        self.initialize()
        if body.get('decision') not in ('acknowledge', 'decline'):
            raise PeerError('invalid_owner_decision')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM a2a_owner_items WHERE id=?', (body.get('item_id'),)).fetchone()
            if not row or row['state'] != 'pending':
                raise PeerError('owner_item_not_pending')
            self.peer(row['peer_id'], db)
            db.execute('UPDATE a2a_owner_items SET state=? WHERE id=?', (body['decision'], row['id']))
        return {'state': body['decision'], 'effect': 'none'}

    def recover(self):
        """An interrupted exchange ends explicitly, never retries an AI or send."""
        self.initialize()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed',error='peer_interrupted' WHERE status='peer-running'")
            db.execute("UPDATE a2a_turns SET state='failed',answer='peer_interrupted' WHERE state='working'")

    def owner_outcomes(self, peer_id, work_id=None):
        """Observed owner decisions only, never an action approval or owner data."""
        with self.store.db() as db:
            return [dict(row) for row in db.execute('SELECT id,state FROM a2a_owner_items '
                'WHERE peer_id=? AND (? IS NULL OR work_id=?)', (peer_id, work_id, work_id))]

    async def send(self, peer_id, work_id, text, message_id, *, http=None):
        transport = self.transport()
        fetch_card, send_message = transport.fetch_card, transport.send_message
        row = self.peer(peer_id)
        sent_sections = self.outbound_scope(work_id, row)
        if row.get('card_url'):
            url = row['card']['supportedInterfaces'][0]['url']
            same_origin = ((urlsplit(row['card_url']).scheme, urlsplit(row['card_url']).netloc)
                           == (urlsplit(url).scheme, urlsplit(url).netloc))
            # An explicitly registered public card source may be separate from
            # the peer endpoint. Its revalidation never gets endpoint credentials.
            credential = self.secrets.get(peer_id).get('outbound', '') if same_origin else ''
            card = await fetch_card(row['card_url'], store=self.store, credential=credential, http=http)
            if digest(card) != row['card_digest']:
                raise PeerError('card_confirmation_required')
        else:
            # File cards cannot be refreshed remotely. Resolve the same registered
            # endpoint's public card before every call, rather than trust old claims.
            url = row['card']['supportedInterfaces'][0]['url']
            parsed = urlsplit(url)
            card = await fetch_card(f'{parsed.scheme}://{parsed.netloc}/.well-known/agent-card.json',
                                    store=self.store, credential=self.secrets.get(peer_id).get('outbound', ''), http=http)
            if digest(card) != row['card_digest']:
                raise PeerError('card_confirmation_required')
        turn, fresh = self.admit(peer_id, message_id, None, text, 'outbound', work_id)
        if not fresh:
            if turn['state'] != 'completed':
                raise PeerError('peer_outcome_unknown' if turn['state'] == 'working' else 'peer_exchange_failed')
            return {'answer': turn['answer'], 'origin': 'third-party', 'peer_id': peer_id}
        try:
            # Recheck the exact scope/card revision captured before card retrieval.
            if turn['revision'] != row['revision']:
                raise PeerError('peer_revision_changed')
            self.guard(peer_id, row['revision'])
            self.spend(peer_id, row['revision'])
            credential = self.secrets.get(peer_id).get('outbound', '')
            self.record(work_id, 'requested', {'peer_id': peer_id, 'direction': 'outbound',
                        'destination': row['card']['supportedInterfaces'][0]['url'], 'sections': sent_sections, 'state': 'dispatching'})
            answer = await send_message(row['card'], turn['question'], message_id, credential, store=self.store, http=http,
                                        guard=lambda: self.guard(peer_id, row['revision']),
                                        redact=self.service._redact_known_secrets)
            answer = self.finish(turn, answer)
            self.record(work_id, 'succeeded', {'peer_id': peer_id, 'direction': 'outbound',
                        'destination': row['card']['supportedInterfaces'][0]['url'], 'sections': sent_sections, 'state': 'response-observed'})
            return {'answer': answer, 'origin': 'third-party', 'peer_id': peer_id}
        except WorkModeRefused:
            self.fail(turn, 'work_mode')
            raise
        except Exception:
            self.record(work_id, 'failed', {'peer_id': peer_id, 'direction': 'outbound',
                        'destination': row['card']['supportedInterfaces'][0]['url'],
                        'sections': sent_sections, 'state': 'outcome-unknown'})
            self.fail(turn, 'peer_outcome_unknown')
            raise PeerError('peer_outcome_unknown') from None

    def outbound_scope(self, work_id, peer):
        """Conservative whole-Work taint, not a classifier over the AI's words.

        Once a model has seen out-of-scope context it cannot compose an outbound
        peer message. This includes prior conversation and unknown/native reads;
        a prompt instruction or literal-value scan cannot contain paraphrases.
        """
        from .manifests import HOST_TOOLS
        record = self.store.turn_provenance(work_id)
        if not isinstance(record, dict) or 'owner_information' not in record:
            raise PeerError('peer_source_scope_unknown')
        refs = record.get('owner_information') or {}
        allowed = set(peer['scope']['sections'])
        required = set()
        if refs.get('profile_keys'):
            required.add('profile')
        if refs.get('current_context'):
            required.add('current_context')
        if (refs.get('prepared') or refs.get('spliced') or refs.get('earlier_exchanges')
                or record.get('context_messages') or record.get('cli_native_tools')
                or record.get('route') == 'subscription'):
            raise PeerError('peer_source_scope_unknown')
        source_scope = {'owner-profile': 'profile', 'owner-current-context': 'current_context',
                        'owner-memory': 'memory', 'owner-folder-names': 'folders',
                        'connected-document': 'folders', 'owner-calendar': 'accounts',
                        'owner-mail': 'accounts', 'connected-drive-file': 'accounts'}
        for source in set(record.get('egress_taint') or []) | set(record.get('prompt_withheld') or []):
            if source not in source_scope:
                raise PeerError('peer_source_scope_unknown')
            required.add(source_scope[source])
        category_scope = {'memory': 'memory', 'files': 'folders', 'calendar': 'accounts',
                          'drive': 'accounts', 'mail': 'accounts', 'current_context': 'current_context'}
        for event in self.store.task_events(work_id):
            action = event['trace'].get('host_action') or event['tool']
            if (action == 'a2a_message' and event['trace'].get('peer_id')
                    and event['trace']['peer_id'] != peer['id']):
                raise PeerError('peer_cross_conversation_disclosure')
            declaration = HOST_TOOLS.get(action)
            if declaration and declaration.reads and event['status'] in ('running', 'succeeded'):
                category = category_scope.get(declaration.reads)
                if category is None:
                    raise PeerError('peer_source_scope_unknown')
                required.add(category)
        if required - allowed:
            raise PeerError('peer_disclosure_scope_exceeded')
        return sorted(required)

    def tool(self, work_id, args, *, http=None):
        # Calls from the synchronous model loop run outside the ASGI event loop.
        text = str(args.get('message', ''))
        peer_id = args.get('peer_id')
        # A per-Work stable dispatch identity, also preserved across retries.
        message_id = digest([work_id, peer_id, text])
        return asyncio.run(self.send(peer_id, work_id, text, message_id, http=http))

    def start(self, host='127.0.0.1', port=0):
        if host != '127.0.0.1':
            raise PeerError('peer_listener_must_be_127_0_0_1')
        if type(port) is not int or not 0 <= port <= 65535:
            raise PeerError('invalid_peer_port')
        if not self.rows():
            raise PeerError('no_registered_peers')
        if self.thread and self.thread.is_alive():
            raise PeerError('peer_listener_already_running')
        application = self.transport().application
        import socket
        import uvicorn
        sock = socket.socket()
        try:
            sock.bind((host, port))
            sock.listen()
            bound_port = sock.getsockname()[1]
            self.server = uvicorn.Server(uvicorn.Config(application(self, f'http://127.0.0.1:{bound_port}/'),
                                         host=host, port=bound_port, log_level='critical', access_log=False, lifespan='off',
                                         limit_concurrency=16, timeout_graceful_shutdown=2))
            self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [sock]}, daemon=True)
            self.thread.start()
            deadline = time.monotonic() + 5
            while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            if not self.server.started:
                raise PeerError('peer_listener_start_failed')
            return {'host': host, 'port': bound_port}
        except OSError:
            sock.close()
            raise PeerError('peer_listener_bind_failed') from None
        except Exception:
            sock.close()
            raise

    def stop(self):
        if self.server:
            self.server.should_exit = True
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE jobs SET status='cancelled',error='peer_listener_stopped' WHERE status='peer-running'")
                db.execute("UPDATE a2a_turns SET state='cancelled' WHERE direction='inbound' AND state='working'")
        if self.thread:
            self.thread.join(timeout=10)
        return {'running': bool(self.thread and self.thread.is_alive())}
