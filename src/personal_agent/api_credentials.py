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

from .browser_jar import KeychainKey, store_account

PREFIX = 'api_credential:'
#: #1380: one code-run secret set's run state (for example an access token and its expiry),
#: encrypted with the same key and bound to the set's exact metadata.
STATE_PREFIX = 'api_credential_state:'
MAX_STATE_BYTES = 16 * 1024
#: State strings at least this long (tokens) are redacted; shorter ones (dates, times) are not.
STATE_REDACT_CHARS = 24
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
    for key in ('oauth', 'credential_parent', 'token_expires_at', 'kind', 'fields', 'reads'):
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
        self.key = getattr(store, 'api_slot_key', None) or KeychainKey(store_account(store.root), KEY_SERVICE)

    def _cipher(self, create=False):
        value = self.key.get()
        if value is None and create:
            # stage calls this inside QuickStore's secret-file lock. Existing
            # ciphertext belongs to a recoverable key, never a replacement key.
            if self.store.secret_items(PREFIX):
                raise CredentialError()
            value = self.key.create()
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

    def values(self, records):
        """Redaction includes staged/retired ciphertext, not only active slots.

        These values are never used as authentication: only read/resolve with
        current metadata can release a credential for that purpose.
        """
        try:
            cipher = self._cipher()
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
        token = self.store.secret(STATE_PREFIX + name)
        if not token:
            return ''
        try:
            payload = json.loads(self._cipher().decrypt(token.encode()))
        except Exception:
            return ''
        if not isinstance(payload, dict) or payload.get('version') != 1 or payload.get('binding') != fingerprint(name, slot):
            return ''
        state = payload.get('state')
        return state if isinstance(state, str) else ''

    def _state_strings(self, token):
        """The redacted string values of one saved run-state ciphertext (``state_values``' rule)."""
        state = json.loads(json.loads(self._cipher().decrypt(token.encode())).get('state') or '{}')
        values, stack = [], [state]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str) and len(item) >= STATE_REDACT_CHARS:
                values.append(item)
        return values

    def discard_state(self, name):
        """Remove the set's run state after retiring its values, in one locked step (#258).

        Raises when the saved state cannot be read, so its ciphertext is never dropped unretired.
        """
        return self.store.swap_secret(STATE_PREFIX + name, None, self._state_strings)

    def write_state(self, name, slot, text):
        """Save the run state (a JSON object of at most ``MAX_STATE_BYTES``); False when refused."""
        if not isinstance(text, str) or not text.strip():
            try:
                self.discard_state(name)
            except Exception:
                return False  # Codex review on #259: an unreadable state is kept, never dropped unretired
            return True
        try:
            if len(text.encode()) > MAX_STATE_BYTES or not isinstance(json.loads(text), dict):
                return False
            self.assert_current(name, slot)
            payload = {'version': 1, 'binding': fingerprint(name, slot), 'state': text}
            encoded = json.dumps(payload, separators=(',', ':')).encode()
            # The old state's values are retired and the new state written under one lock (Codex review).
            self.store.swap_secret(STATE_PREFIX + name, self._cipher(create=True).encrypt(encoded).decode(),
                                   self._state_strings)
        except Exception:
            return False
        return True

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
