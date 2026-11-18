"""One bounded chunk of an anonymous remote representation (RFC 9110)."""
import hashlib
import http.client
import re
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
    etag: str = field(repr=False)
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


class PublicDownload:
    """Reuse PublicPageReader's destination policy and pinned GET transport."""

    def __init__(self, reader):
        if reader.declared is not None:
            raise ValueError('public downloads use the public-only destination policy')
        self.reader = reader

    def read_range(self, url, offset, length, *, etag=None, total=None, source_digest=None,
                   address_check, before_send=None):
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

        ran = False

        def dispatch():
            nonlocal ran
            if before_send is not None:
                before_send()
            ran = True

        deadline = self.reader.clock() + MAX_PAGE_SECONDS
        try:
            current = self.reader._safe_url(url)
            for redirect in range(MAX_PAGE_REDIRECTS + 1):
                try:
                    addresses = self.reader._checked_hop(current, deadline, address_check)
                except ValueError:
                    raise PublicDownloadError('public_download_refused', ran) from None
                identity = _identifier(current)
                # Only the previously resolved representation receives its validator.
                # A redirect to a different representation cannot inherit this header.
                headers = {**_HEADERS, 'Range': f'bytes={offset}-{offset + length - 1}'}
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
                    if resumed and (identity != source_digest or response.status == 200):
                        raise PublicDownloadError('public_download_source_changed', ran)
                    return self._read_response(response, offset, length, etag, total, identity, deadline)
                finally:
                    self.reader._close_response(response)
        except PublicDownloadError:
            raise
        except (ValueError, ProviderError, OSError, http.client.HTTPException):
            raise PublicDownloadError('public_download_interrupted' if ran else 'public_download_refused', ran) from None
        raise PublicDownloadError('public_download_interrupted', ran)

    def _read_response(self, response, offset, length, etag, total, identity, deadline):
        if response.status != 206:
            raise PublicDownloadError('public_download_range_unavailable', True)
        content_range = _header(response, 'Content-Range', '')
        if not isinstance(content_range, str) or len(content_range) > 65:
            raise PublicDownloadError('public_download_invalid_response', True)
        match = _CONTENT_RANGE.fullmatch(content_range.strip())
        encoding = _header(response, 'Content-Encoding', '').strip().lower()
        received_etag = _header(response, 'ETag')
        media_type = _header(response, 'Content-Type', '').split(';', 1)[0].strip().lower()
        if (not match or encoding not in ('', 'identity') or not _strong_etag(received_etag)
                or media_type == 'multipart/byteranges'):
            raise PublicDownloadError('public_download_invalid_response', True)
        first, last, size = map(int, match.groups())
        if total is not None and (size != total or received_etag != etag):
            raise PublicDownloadError('public_download_source_changed', True)
        if first != offset or not offset < size <= _MAX_LENGTH or last != min(offset + length, size) - 1:
            raise PublicDownloadError('public_download_invalid_response', True)

        wanted = last - first + 1
        content_length = _header(response, 'Content-Length')
        if content_length is not None:
            if not isinstance(content_length, str) or len(content_length) > 19:
                raise PublicDownloadError('public_download_invalid_response', True)
            if not _NUMBER.fullmatch(content_length) or int(content_length) != wanted:
                raise PublicDownloadError('public_download_invalid_response', True)

        body = bytearray()
        while True:
            self.reader._set_response_deadline(response, deadline)
            with self.reader._deadline_guard(deadline, lambda: self.reader._interrupt_response(response)):
                chunk = response.read(min(64 * 1024, wanted - len(body) + 1))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > wanted:
                raise PublicDownloadError('public_download_invalid_response', True)
        if len(body) != wanted:
            raise PublicDownloadError('public_download_interrupted', True)
        return DownloadedRange(bytes(body), received_etag, size, identity, hashlib.sha256(body).hexdigest())
