"""Encrypted, immutable API credential references (#1300).

Adapts the existing Fernet/KeychainKey/QuickStore secret boundary. Metadata stays
in the existing api_slots row; staging a credential grants no connection authority.
The key is never stored beside ciphertext. Tests inject browser_jar.MemoryKey.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets

from cryptography.fernet import Fernet

from .browser_jar import platform_key, store_account

PREFIX = 'api_credential:'
#: #1380: one code-run secret set's run state (for example an access token and its expiry),
#: encrypted with the same key and bound to the set's exact metadata.
STATE_PREFIX = 'api_credential_state:'
MAX_STATE_BYTES = 16 * 1024
#: #972: a condition check's own state between its runs (``CHECKSTATEFILE``), bound to the check's digest.
CHECK_STATE_PREFIX = STATE_PREFIX + 'check:'
#: #1126 review: every namespace this key encrypts.  A check state exists without any credential, so a
#: guard or reader that looks at credentials alone misses it; both read this one list.
ENCRYPTED_PREFIXES = (PREFIX, STATE_PREFIX)
#: State strings at least this long (tokens) are redacted; shorter ones (dates, times) are not.
STATE_REDACT_CHARS = 24
#: #778: the run state's top-level key for values the code declares not secret (paths, ids, a record of
#: what it did).  Still cannot tell a token from other long text, so everything else stays redacted.
STATE_PLAIN_KEY = 'plain'


def _strings(item, shortest):
    values, stack = [], [item]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str) and len(item) >= shortest:
            values.append(item)
    return values


def plain_state_values(text):
    """The strings under a run state's ``plain`` key, which are never redacted or retired (#778)."""
    try:
        state = json.loads(text or '{}')
    except ValueError:
        return []
    return _strings(state.get(STATE_PLAIN_KEY), 1) if isinstance(state, dict) else []


def secret_state_values(text, plain=()):
    """The same secret strings from either scratch state or decrypted saved state.

    #1265: ``plain`` are the strings a Work itself wrote into a check's plan (``update_check_state``).
    The Work's AI already holds them, so they are not secrets; a token the check code saved is not
    among them and stays redacted.
    """
    state = json.loads(text or '{}')
    if isinstance(state, dict):
        state = {key: value for key, value in state.items() if key != STATE_PLAIN_KEY}
    plain = set(plain or ())
    return [value for value in _strings(state, STATE_REDACT_CHARS) if value not in plain]


def work_plain_values(patch, text, earlier=()):
    """#1265: the Work-written strings still in ``text``: this patch's and those an earlier patch wrote."""
    present = set(_strings(json.loads(text or '{}'), 1))
    return sorted((set(_strings(patch, 1)) | set(earlier or ())) & present)


REFERENCE = re.compile(r'api_credential:[a-f0-9]{48}\Z')
KEY_SERVICE = 'personal-agentos.api-slots'


class CredentialError(ValueError):
    def __init__(self):
        super().__init__('API 인증 정보를 확인할 수 없어 사용하지 않았습니다.')


def fingerprint(name, slot):
    """Bind an encrypted value to the exact authority-bearing slot metadata."""
    from .api_requests import DEFAULT_MAX_AGE
    bound = {'name': name, 'hosts': list(slot['hosts']), 'header': slot['header'],
             'scheme': slot['scheme'], 'subject': slot.get('subject'),
             'money': slot.get('money', False), 'revision': slot['revision'],
             'max_age_seconds': slot.get('max_age_seconds', DEFAULT_MAX_AGE)}
    # #355: a connection's current write boundary participates in the dispatch guard.
    if slot.get('connection'):
        bound.update({key: slot.get(key) for key in ('connection', 'paths', 'read_only', 'write_paths')})
        # #432: fixed write parameters are part of the boundary only where a slot has them.
        for key in ('write_query', 'write_body_refuse', 'write_grant_revisions'):
            if slot.get(key):
                bound[key] = slot[key]
    # #1380: a code-run secret set binds its named fields and owner-reviewed reads too.
    for key in ('oauth', 'credential_parent', 'token_expires_at', 'kind', 'fields', 'reads', 'registration_id'):
        if key in slot:
            bound[key] = slot[key]
    return hashlib.sha256(json.dumps(bound, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


#: #1380: the name of one value in a code-run secret set (also its environment-variable suffix).
FIELD = re.compile(r'[A-Z][A-Z0-9_]{0,39}')  # matched whole (``fullmatch``)
MAX_FIELDS = 8
#: The longest secret value of any shape.
MAX_VALUE_CHARS = 4096


def named_fields(value):
    """Whether ``value`` is a code-run secret set's ``{FIELD: value}`` mapping (#1380)."""
    return (isinstance(value, dict) and 1 <= len(value) <= MAX_FIELDS
            and all(isinstance(key, str) and FIELD.fullmatch(key) for key in value))


def credential_values(value):
    """The supported secret shapes, also shared by the redaction boundary."""
    named = isinstance(value, dict) and set(value) != {'client_id', 'client_secret'} and named_fields(value)
    values = ([value] if isinstance(value, str)
              else list(value.values()) if isinstance(value, dict) and (set(value) == {'client_id', 'client_secret'} or named)
              else [])
    # #1380 live: a code-run set's value may be short (an account product code such as "01");
    # every other credential keeps the 8-character floor. Redaction skips short values.
    shortest = 1 if named else 8
    if not values or any(not isinstance(item, str) or not shortest <= len(item) <= MAX_VALUE_CHARS
                         or any(ord(ch) < 32 or ord(ch) == 127 for ch in item) for item in values):
        raise CredentialError()
    return tuple(values)


class ApiCredentials:
    def __init__(self, store):
        self.store = store
        # Only wiring/tests may inject a key provider, never a tool argument.
        self.key = getattr(store, 'api_slot_key', None) or platform_key(store_account(store.root), KEY_SERVICE)

    def _cipher(self, create=False):
        try:
            value = self.key.get()
            if value is None and create:
                # stage calls this inside QuickStore's secret-file lock. Existing
                # ciphertext belongs to a recoverable key, never a replacement key.
                if any(self.store.secret_items(prefix) for prefix in ENCRYPTED_PREFIXES):
                    raise CredentialError()
                value = self.key.create()
        except CredentialError:
            raise
        except Exception:
            # #1126 review: a locked or failing keyring is an unavailable key to every caller.
            raise CredentialError() from None
        if value is None:
            raise CredentialError()
        return Fernet(value)

    def stage(self, name, slot, secret):
        """Write a fresh encrypted reference, without publishing slot metadata.

        QuickStore.secret(create=...) holds its process lock and file lock while
        obtaining/creating the Keychain key and writing the ciphertext. Concurrent
        first writers therefore cannot overwrite each other's initial key.
        """
        reference = PREFIX + secrets.token_hex(24)
        try:
            credential_values(secret)
            payload = {'version': 1, 'binding': fingerprint(name, slot), 'secret': secret}
            encoded = json.dumps(payload, separators=(',', ':')).encode()
            self.store.secret(reference, create=lambda: self._cipher(create=True).encrypt(encoded).decode())
        except Exception:
            raise CredentialError() from None
        return reference

    def read(self, name, slot):
        reference = slot.get('credential_ref')
        if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
            raise CredentialError()
        try:
            token = self.store.secret(reference)
            payload = json.loads(self._cipher().decrypt(token.encode()))
            if (not isinstance(payload, dict) or payload.get('version') != 1
                    or payload.get('binding') != fingerprint(name, slot)):
                raise CredentialError()
            credential_values(payload.get('secret'))
            return payload['secret']
        except Exception:
            raise CredentialError() from None

    def discard(self, reference):
        if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
            raise CredentialError()
        # #221 (Codex review): the plaintext this removes is remembered only as a keyed digest, so an
        # older message holding it is still kept from a model after the credential is gone.
        token = self.store.secret_items(reference).get(reference)
        if token:
            # Codex review on #259: the ciphertext stays until its plaintext is retired; an unreadable
            # credential (owner-held key unavailable) is not removed, so redaction never loses it.
            self.store.retire_secret_values(self.values({reference: token}))
        self.store.remove_secret(reference)

    def values(self, records, cipher=None):
        """Redaction includes staged/retired ciphertext, not only active slots.

        These values are never used as authentication: only read/resolve with
        current metadata can release a credential for that purpose.
        """
        try:
            cipher = cipher if cipher is not None else self._cipher()
            result = []
            for reference, token in records.items():
                if not REFERENCE.fullmatch(reference):
                    raise CredentialError()
                payload = json.loads(cipher.decrypt(token.encode()))
                value = payload.get('secret')
                if payload.get('version') != 1:
                    raise CredentialError()
                result.extend(credential_values(value))
            return tuple(result)
        except Exception:
            raise CredentialError() from None

    def assert_current(self, name, slot):
        from .api_requests import slots
        current = slots(self.store).get(name)
        if (current is None or current.get('credential_ref') != slot.get('credential_ref')
                or fingerprint(name, current) != fingerprint(name, slot)):
            raise CredentialError()

    def resolve(self, name, slot):
        self.assert_current(name, slot)
        value = self.read(name, slot)
        self.assert_current(name, slot)
        return value, lambda _status: self.assert_current(name, slot)

    # -- #1380: a code-run secret set's state between runs ---------------------------------

    def read_state(self, name, slot):
        """The set's saved run state text, or '' when none, unreadable or saved for other metadata."""
        return self._read_bound(STATE_PREFIX + name, fingerprint(name, slot))

    def check_work_plain(self, check_id):
        """#1265: the Work-written plan strings recorded with the check's saved state."""
        token = self.store.secret(CHECK_STATE_PREFIX + check_id)
        if not token:
            return []
        payload = json.loads(self._cipher().decrypt(token.encode()))
        return list(payload.get('work_plain') or ()) if isinstance(payload, dict) else []

    def _read_bound(self, key, binding, *, strict=False):
        token = self.store.secret(key)
        if not token:
            return ''
        try:
            payload = json.loads(self._cipher().decrypt(token.encode()))
        except Exception:
            if strict:
                raise CredentialError() from None
            return ''
        if isinstance(payload, dict) and payload.get('version') == 1:
            if payload.get('binding') != binding:
                return ''
            if isinstance(payload.get('state'), str):
                return payload['state']
        if strict:
            raise CredentialError()
        return ''

    def _state_strings(self, token, cipher=None):
        """The redacted string values of one saved run-state ciphertext (``state_values``' rule).

        #778: every long string except those under ``plain``; a plain value that also occurs
        elsewhere in the state is still redacted.
        """
        cipher = cipher if cipher is not None else self._cipher()
        payload = json.loads(cipher.decrypt(token.encode()))
        return secret_state_values(payload.get('state'), payload.get('work_plain'))

    def discard_state(self, name):
        """Remove the set's run state after retiring its values, in one locked step (#258).

        Raises when the saved state cannot be read, so its ciphertext is never dropped unretired.
        """
        return self._discard_bound(STATE_PREFIX + name)

    def _discard_bound(self, key):
        return self.store.swap_secret(key, None, self._state_strings, origin='state')

    def write_state(self, name, slot, text):
        """Save the run state (a JSON object of at most ``MAX_STATE_BYTES``); False when refused."""
        return self._write_bound(STATE_PREFIX + name, lambda: fingerprint(name, slot), text,
                                 lambda: self.assert_current(name, slot))

    def _write_bound(self, key, binding, text, current=lambda: None):
        try:
            return self.store_sealed(self._seal(key, binding, text, current))
        except Exception:
            return False

    def _seal(self, key, binding, text, current=lambda: None, work_plain=None):
        """#1126 review: everything a state save needs from the key, done before the save itself.

        Checks the state (empty discards it; otherwise a JSON object of at most ``MAX_STATE_BYTES``, else
        ``ValueError``) and encrypts it.  ``CredentialError`` when the key is unavailable, so a caller can
        report that before it relies on the save.  ``store_sealed`` then needs no key lookup.
        """
        if not isinstance(text, str) or not text.strip():
            try:
                missing = self.key.get() is None
            except Exception:
                raise CredentialError() from None
            if missing:
                if key in self.store.secret_items(key):
                    raise CredentialError()  # An old state that cannot be retired is kept, never dropped.
                return {'key': key, 'value': None, 'plain': [], 'cipher': None}
            return {'key': key, 'value': None, 'plain': [], 'cipher': self._cipher()}
        if len(text.encode()) > MAX_STATE_BYTES or not isinstance(json.loads(text), dict):
            raise ValueError('run state must be a JSON object of at most 16 KB')
        current()
        payload = {'version': 1, 'binding': binding(), 'state': text}
        cipher = self._cipher(create=True)
        if key.startswith(CHECK_STATE_PREFIX):
            # #1265: a check's own save keeps the Work-written strings it still holds as plain.
            if work_plain is None:
                old = self.store.secret(key)
                try:
                    earlier = json.loads(cipher.decrypt(old.encode())).get('work_plain') if old else ()
                except Exception:
                    earlier = ()
                work_plain = work_plain_values({}, text, earlier)
            if work_plain:
                payload['work_plain'] = sorted(work_plain)
        encoded = json.dumps(payload, separators=(',', ':')).encode()
        return {'key': key, 'value': cipher.encrypt(encoded).decode(),
                'plain': sorted(set(plain_state_values(text)) | set(payload.get('work_plain') or ())),
                'cipher': cipher}

    def store_sealed(self, sealed):
        """Write a ``_seal`` result; False when the store refused it (an unreadable old state is kept, never
        dropped unretired: Codex review on #259)."""
        cipher, plain = sealed['cipher'], set(sealed['plain'])

        def retire(old):
            if cipher is None:
                raise CredentialError()
            return [value for value in self._state_strings(old, cipher) if value not in plain]
        try:
            # The old state's values are retired and the new state written under one lock (Codex review).
            # #778: a value the new state declares plain is not retired, and its earlier mark is dropped.
            if sealed['value'] is None:
                self.store.swap_secret(sealed['key'], None, retire, origin='state')
            else:
                self.store.swap_secret(sealed['key'], sealed['value'], retire, release=sealed['plain'], origin='state')
        except Exception:
            return False
        return True

    # -- #972: a condition check's own state between its runs -----------------------------
    # Kept under the run-state namespace, so the store-wide redaction pass covers it like a set's state.
    # A set name never contains ':' (``api_requests.NAME``), so the two cannot collide.

    def read_check_state(self, check_id, digest, *, strict=False):
        """The check's saved state, or '' when none, unreadable or saved for other check code."""
        return self._read_bound(CHECK_STATE_PREFIX + check_id, {'check': check_id, 'digest': digest}, strict=strict)

    def write_check_state(self, check_id, digest, text):
        """Save the check's state under the same rules as a set's run state; False when refused."""
        return self._write_bound(CHECK_STATE_PREFIX + check_id, lambda: {'check': check_id, 'digest': digest}, text)

    def seal_check_state(self, check_id, digest, text, work_plain=None):
        """The check's state, checked and encrypted for ``store_sealed``; raises like ``_seal``."""
        return self._seal(CHECK_STATE_PREFIX + check_id, lambda: {'check': check_id, 'digest': digest}, text,
                          work_plain=work_plain)

    def discard_check_state(self, check_id, *, keep_digest=None):
        """Remove the check's state after retiring its values; raises when it cannot be read."""
        key = CHECK_STATE_PREFIX + check_id
        if keep_digest is None:
            return self._discard_bound(key)

        class CurrentState(Exception):
            pass

        def retire(token):
            payload = json.loads(self._cipher().decrypt(token.encode()))
            if payload.get('binding') == {'check': check_id, 'digest': keep_digest}:
                raise CurrentState
            return secret_state_values(payload.get('state'), payload.get('work_plain'))

        # Check the binding under the same lock that retires and removes the state.
        # Undo can restore a digest after its state has been swept. It then starts from {}.
        try:
            return self.store.swap_secret(key, None, retire, origin='state')
        except CurrentState:
            return False

    def check_state_ids(self):
        """The checks that have a saved state."""
        records = self.store.secret_items(CHECK_STATE_PREFIX) if callable(getattr(self.store, 'secret_items', None)) else {}
        return [key[len(CHECK_STATE_PREFIX):] for key in records]

    def state_values(self):
        """String values in every saved run state, for the redaction boundary (tokens, keys)."""
        values = []
        records = self.store.secret_items(STATE_PREFIX) if callable(getattr(self.store, 'secret_items', None)) else {}
        if not records:
            return ()
        for token in records.values():
            try:
                values.extend(self._state_strings(token))
            except Exception:
                continue
        return tuple(values)
