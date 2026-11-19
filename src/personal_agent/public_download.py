"""Bounded anonymous retrieval of a remote representation (RFC 9110)."""
import hashlib
import http.client
import re
from copy import copy
from dataclasses import dataclass, field

from .local_tools import MAX_PAGE_REDIRECTS, MAX_PAGE_SECONDS
from .providers import ProviderError


MAX_RANGE_BYTES = 1024 * 1024
_MAX_LENGTH = (1 << 63) - 1
# RFC 9110 §8.8.3. Weak validators cannot be used with If-Range.
_ETAG = re.compile(r'"[\x21\x23-\x7e\x80-\xff]{0,512}"')
_CONTENT_RANGE = re.compile(r'(?i:bytes) ([0-9]{1,19})-([0-9]{1,19})/([0-9]{1,19})')
_NUMBER = re.compile(r'[0-9]{1,19}')
_DIGEST = re.compile(r'[0-9a-f]{64}')
_HEADERS = {'User-Agent': 'Still public-download/1.0', 'Accept-Encoding': 'identity'}


class PublicDownloadError(ValueError):
    """A typed failure without untrusted headers, addresses or body text."""

    def __init__(self, code, ran):
        super().__init__('공개 파일의 요청 범위를 확인하지 못했어요.')
        self.code = code
        self.ran = ran


@dataclass(frozen=True)
class DownloadedRange:
    """Host-internal receipt. Evidence only needs digests, size and progress."""

    data: bytes = field(repr=False)
    etag: str | None = field(repr=False)
    total: int
    source_digest: str
    digest: str


def _integer_in(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _strong_etag(tag):
    return isinstance(tag, str) and _ETAG.fullmatch(tag) is not None


def _identifier(url):
    return hashlib.sha256(url.encode('utf-8')).hexdigest()


def _header(response, name, default=None):
    all_values = getattr(response.headers, 'get_all', None)
    if callable(all_values) and len(all_values(name, [])) > 1:
        raise PublicDownloadError('public_download_invalid_response', True)
    return response.headers.get(name, default)


class _HostGuardRefusal(Exception):
    pass


def _run_guard(callback, *args):
    try:
        return callback(*args)
    except ValueError as exc:
        if isinstance(getattr(exc, 'code', None), str):
            raise _HostGuardRefusal(exc) from None
        raise


class PublicDownload:
    """Reuse PublicPageReader's destination policy and pinned GET transport."""

    def __init__(self, reader):
        if reader.declared is not None:
            raise ValueError('public downloads use the public-only destination policy')
        self.reader = copy(reader)
        self.reader.preserve_query = True

    def read_range(self, url, offset, length, *, etag=None, total=None, source_digest=None,
                   address_check, before_send=None, on_request=None, deadline=None,
                   full_limit=MAX_RANGE_BYTES):
        """Return only a complete, validated chunk, without publishing any files."""
        if not callable(address_check):
            raise TypeError('public download requires a host address check')
        if not (_integer_in(offset, 0, _MAX_LENGTH - 1)
                and _integer_in(length, 1, min(MAX_RANGE_BYTES, _MAX_LENGTH - offset))):
            raise PublicDownloadError('public_download_invalid_range', False)
        resumed = any(value is not None for value in (etag, total, source_digest))
        if (offset or resumed) and not (
            _strong_etag(etag) and _integer_in(total, offset + 1, _MAX_LENGTH)
            and isinstance(source_digest, str) and _DIGEST.fullmatch(source_digest)
        ):
            raise PublicDownloadError('public_download_invalid_checkpoint', False)

        request_deadline = self.reader.clock() + MAX_PAGE_SECONDS
        deadline = request_deadline if deadline is None else min(deadline, request_deadline)
        try:
            return self._read(url, offset, length, etag, total, source_digest, deadline,
                              full_limit, address_check, before_send, on_request)
        except PublicDownloadError as exc:
            if exc.code != 'public_download_needs_full':
                raise
        # Without a strong validator a partial 206 cannot be stitched to
        # another response. Discard it and request the whole representation.
        return self._read(url, 0, None, None, None, None, deadline,
                          full_limit, address_check, before_send, on_request)

    def _read(self, url, offset, length, etag, total, source_digest, deadline,
              full_limit, address_check, before_send, on_request):
        ran = False

        def dispatch():
            nonlocal ran
            if before_send is not None:
                _run_guard(before_send)
            if on_request is not None:
                on_request(current)
            ran = True

        try:
            current = self.reader._safe_url(url)
            for redirect in range(MAX_PAGE_REDIRECTS + 1):
                try:
                    addresses = self.reader._checked_hop(current, deadline, lambda url: _run_guard(address_check, url))
                except ValueError:
                    raise PublicDownloadError('public_download_refused', ran) from None
                identity = _identifier(current)
                # Only the previously resolved representation receives its validator.
                # A redirect to a different representation cannot inherit this header.
                headers = dict(_HEADERS)
                if length is not None:
                    headers['Range'] = f'bytes={offset}-{offset + length - 1}'
                if etag is not None and identity == source_digest:
                    headers['If-Range'] = etag
                response = self.reader._get_response(
                    current, addresses, deadline, headers=headers, before_send=dispatch)
                try:
                    self.reader._remaining(deadline)
                    location = _header(response, 'Location')
                    if response.status in (301, 302, 303, 307, 308):
                        current = self.reader._redirect_target(current, location, redirect)
                        continue
                    if etag is not None and (identity != source_digest or response.status == 200):
                        raise PublicDownloadError('public_download_source_changed', ran)
                    return self._read_response(response, offset, length, etag, total, identity,
                                               deadline, full_limit)
                finally:
                    self.reader._close_response(response)
        except _HostGuardRefusal as exc:
            raise exc.args[0] from None
        except PublicDownloadError:
            raise
        except (ValueError, ProviderError, OSError, http.client.HTTPException):
            raise PublicDownloadError('public_download_interrupted' if ran else 'public_download_refused', ran) from None
        raise PublicDownloadError('public_download_interrupted', ran)

    def _read_response(self, response, offset, length, etag, total, identity, deadline, full_limit):
        if response.status != 200 and (response.status != 206 or length is None):
            raise PublicDownloadError('public_download_range_unavailable', True)
        content_range = _header(response, 'Content-Range')
        encoding = _header(response, 'Content-Encoding', '').strip().lower()
        received_etag = _header(response, 'ETag')
        media_type = _header(response, 'Content-Type', '').split(';', 1)[0].strip().lower()
        if encoding not in ('', 'identity') or media_type == 'multipart/byteranges':
            raise PublicDownloadError('public_download_invalid_response', True)

        declared = _header(response, 'Content-Length')
        if declared is not None:
            if not isinstance(declared, str) or not _NUMBER.fullmatch(declared):
                raise PublicDownloadError('public_download_invalid_response', True)
            declared = int(declared)
        if response.status == 200:
            if content_range is not None:
                raise PublicDownloadError('public_download_invalid_response', True)
            if declared is not None and declared > full_limit:
                raise PublicDownloadError('public_download_size_limit', True)
            wanted, limit, size = declared, full_limit if declared is None else declared, None
        else:
            match = (_CONTENT_RANGE.fullmatch(content_range.strip())
                     if isinstance(content_range, str) and len(content_range) <= 65 else None)
            if not match:
                raise PublicDownloadError('public_download_invalid_response', True)
            first, last, size = map(int, match.groups())
            if total is not None and (size != total or received_etag != etag):
                raise PublicDownloadError('public_download_source_changed', True)
            if first != offset or not offset < size <= _MAX_LENGTH or last != min(offset + length, size) - 1:
                raise PublicDownloadError('public_download_invalid_response', True)
            wanted = limit = last - first + 1
            if declared is not None and declared != wanted:
                raise PublicDownloadError('public_download_invalid_response', True)
            if not _strong_etag(received_etag) and wanted != size:
                raise PublicDownloadError('public_download_needs_full', True)

        body = bytearray()
        while True:
            self.reader._set_response_deadline(response, deadline)
            with self.reader._deadline_guard(deadline, lambda: self.reader._interrupt_response(response)):
                chunk = response.read(min(64 * 1024, limit - len(body) + 1))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > limit:
                raise PublicDownloadError('public_download_size_limit' if wanted is None
                                          else 'public_download_invalid_response', True)
        if wanted is not None and len(body) != wanted:
            raise PublicDownloadError('public_download_interrupted', True)
        return DownloadedRange(bytes(body), received_etag if _strong_etag(received_etag) else None,
                               len(body) if size is None else size, identity, hashlib.sha256(body).hexdigest())
