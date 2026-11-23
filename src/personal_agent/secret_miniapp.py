"""Pinned static input page and its single-use RFC 7516 decryption key."""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from urllib.request import build_opener

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import work_mode
from .providers import NoRedirect

PAGE_URL = 'https://jongtae.github.io/Still/secret-input/v1/'
PAGE_SHA256 = {
    'index.html': '4e737713557fe63f05fcc27f37910e337f9fa5d4929fb9b3390b1ae6231573b5',
    'seal.js': '6b64966827c05dbeca3865a061e9319cae404cb61e3f569edbfc897ac953a3b3',
    'page.js': '49f6f6953499d2cb4b026d167b1ce885125a086d3c2bcda74ce7e7fb3540f099',
}
MAX_DATA_BYTES = 4096
_CACHE_SECONDS = 180
_FETCH_TIMEOUT = 2
_MAX_ASSET_BYTES = 64 * 1024
_B64URL = re.compile(r'[A-Za-z0-9_-]+')
_verified_until = 0.0


def _fetch(url, *, timeout):
    with build_opener(NoRedirect()).open(url, timeout=timeout) as response:
        return response.read(_MAX_ASSET_BYTES + 1)


def available(fetch=None, *, store=None):
    """Check the exact served assets. Only production GETs share the positive cache."""
    global _verified_until
    read = fetch if fetch is not None else _fetch
    try:
        if work_mode.active(store):
            return False
        if fetch is None and time.monotonic() < _verified_until:
            return True
        for name, expected in PAGE_SHA256.items():
            work_mode.refuse(store, 'public_read')
            url = PAGE_URL if name == 'index.html' else PAGE_URL + name
            data = read(url, timeout=_FETCH_TIMEOUT)
            if hashlib.sha256(data).hexdigest() != expected:
                return False
    except Exception:
        return False
    if fetch is None:
        _verified_until = time.monotonic() + _CACHE_SECONDS
    return True


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _decode(part):
    if not _B64URL.fullmatch(part):
        raise SecretMiniAppError('malformed')
    data = base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))
    if b64u(data) != part:
        raise SecretMiniAppError('malformed')
    return data


def _object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError
    return result


class SecretMiniAppError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class OneTimeKey:
    def __init__(self):
        self.kid = secrets.token_urlsafe(16)
        self._lock = threading.Lock()
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)

    def spki_b64u(self):
        with self._lock:
            if self._private_key is None:
                raise SecretMiniAppError('used')
            public = self._private_key.public_key()
        return b64u(public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))

    def destroy(self):
        with self._lock:
            self._private_key = None

    def open(self, token):
        """Consume the private key on the first attempt, including invalid input."""
        # Keep disposal and decryption serialized with finish() and other attempts.
        with self._lock:
            key, self._private_key = self._private_key, None
            if key is None:
                raise SecretMiniAppError('used')
            try:
                return self._open(key, token)
            except SecretMiniAppError as exc:
                reason = exc.reason
            finally:
                del key
        raise SecretMiniAppError(reason) from None

    def _open(self, key, token):
        if not isinstance(token, str):
            raise SecretMiniAppError('malformed')
        if len(token) > MAX_DATA_BYTES:
            raise SecretMiniAppError('size')
        try:
            if len(token.encode('utf-8')) > MAX_DATA_BYTES:
                raise SecretMiniAppError('size')
        except UnicodeError:
            raise SecretMiniAppError('malformed') from None
        parts = token.split('.')
        if len(parts) != 5:
            raise SecretMiniAppError('malformed')
        try:
            protected, wrapped, iv, ciphertext, tag = map(_decode, parts)
        except SecretMiniAppError:
            raise
        except ValueError:
            raise SecretMiniAppError('malformed') from None
        try:
            header = json.loads(protected.decode('utf-8'), object_pairs_hook=_object)
        except (ValueError, RecursionError):
            raise SecretMiniAppError('header') from None
        if not isinstance(header, dict) or header.keys() != {'alg', 'enc', 'kid'}:
            raise SecretMiniAppError('header')
        if (header['alg'], header['enc']) != ('RSA-OAEP-256', 'A256GCM'):
            raise SecretMiniAppError('header')
        if header['kid'] != self.kid:
            raise SecretMiniAppError('kid')
        if len(wrapped) != key.key_size // 8 or len(iv) != 12 or len(tag) != 16:
            raise SecretMiniAppError('malformed')
        try:
            cek = key.decrypt(wrapped, padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
            if len(cek) != 32:
                raise SecretMiniAppError('decrypt')
            plaintext = AESGCM(cek).decrypt(iv, ciphertext + tag, parts[0].encode('ascii'))
        except (ValueError, InvalidTag):
            raise SecretMiniAppError('decrypt') from None
        try:
            body = json.loads(plaintext.decode('utf-8'), object_pairs_hook=_object)
        except (ValueError, RecursionError):
            raise SecretMiniAppError('malformed') from None
        if not isinstance(body, dict) or any(not isinstance(value, str) for value in body.values()):
            raise SecretMiniAppError('malformed')
        return plaintext
