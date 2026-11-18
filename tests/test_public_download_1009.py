import hashlib
import io
import unittest
from unittest.mock import Mock, patch
from email.message import Message

from personal_agent.local_tools import PublicPageReader
from personal_agent.public_download import MAX_RANGE_BYTES, PublicDownload, PublicDownloadError


URL = 'https://example.com/dataset.gz'
TAG = '"source-v1"'


class Response:
    def __init__(self, body=b'abcd', *, status=206, headers=None):
        self.status = status
        self.headers = Message()
        for key, value in (headers if headers is not None else
                           [('Content-Range', 'bytes 0-3/6'), ('ETag', TAG)]):
            self.headers[key] = value
        self.body = io.BytesIO(body)
        self.closed = False

    def read(self, size):
        return self.body.read(size)

    def close(self):
        self.closed = True
        self.body.close()


class Opener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.get_method(), dict(request.header_items())))
        return next(self.responses)


class PublicDownloadTests(unittest.TestCase):
    def make(self, *responses, private_host=None, guard=None):
        opener = Opener(responses)

        def resolve(host, port, **kwargs):
            address = '192.168.1.2' if host == private_host else '93.184.216.34'
            return [(None, None, None, None, (address, port))]

        return PublicDownload(PublicPageReader(opener=opener, resolver=resolve, guard=guard)), opener

    def read(self, download, *args, **kwargs):
        return download.read_range(*args, address_check=lambda url: None, **kwargs)

    def assert_error(self, code, ran, function, *args, **kwargs):
        with self.assertRaises(PublicDownloadError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertIs(caught.exception.ran, ran)
        self.assertNotIn('example.com', str(caught.exception))
        self.assertNotIn('source-', str(caught.exception))

    def test_exact_ranges_advance_and_final_range_is_clipped(self):
        first_response = Response()
        last_response = Response(b'ef', headers=[('Content-Range', 'bytes 4-5/6'),
                                                  ('ETag', TAG), ('Content-Length', '2')])
        download, opener = self.make(first_response, last_response)
        first = self.read(download, URL, 0, 4)
        last = self.read(download, URL, 4, 4, etag=first.etag, total=first.total,
                         source_digest=first.source_digest)
        self.assertEqual(first.data + last.data, b'abcdef')
        self.assertEqual(first.total, last.total)
        self.assertEqual(first.digest, hashlib.sha256(b'abcd').hexdigest())
        self.assertEqual(last.digest, hashlib.sha256(b'ef').hexdigest())
        self.assertEqual(first.source_digest, hashlib.sha256(URL.encode()).hexdigest())
        self.assertNotIn(TAG, repr(first))
        self.assertNotIn('abcd', repr(first))
        self.assertEqual([c[1] for c in opener.calls], ['GET', 'GET'])
        self.assertEqual(opener.calls[0][2]['Accept-encoding'], 'identity')
        self.assertEqual(opener.calls[0][2]['Range'], 'bytes=0-3')
        self.assertNotIn('If-range', opener.calls[0][2])
        self.assertEqual(opener.calls[1][2]['Range'], 'bytes=4-7')
        self.assertEqual(opener.calls[1][2]['If-range'], TAG)
        self.assertTrue(first_response.closed and last_response.closed)

    def test_non_resumable_and_changed_representations_return_no_chunk(self):
        original = {'etag': TAG, 'total': 6, 'source_digest': hashlib.sha256(URL.encode()).hexdigest()}
        responses = [
            ('source_changed', Response(status=200)),
            ('range_unavailable', Response(status=416, headers=[('Content-Range', 'bytes */6')])),
            ('source_changed', Response(b'ef', headers=[('Content-Range', 'bytes 4-5/6'), ('ETag', '"source-v2"')])),
            ('source_changed', Response(b'efgh', headers=[('Content-Range', 'bytes 4-7/8'), ('ETag', TAG)])),
        ]
        for code, response in responses:
            with self.subTest(code=code, status=response.status):
                download, _ = self.make(response)
                self.assert_error('public_download_' + code, True, self.read,
                                  download, URL, 4, 4, **original)
                self.assertTrue(response.closed)

    def test_wrong_offsets_encodings_validators_and_lengths_fail(self):
        bad_headers = (
            ('Content-Range', 'bytes 1-4/6'), ('Content-Range', 'bytes 0-2/6'),
            ('Content-Range', 'bytes 0-3/*'), ('Content-Range', 'bytes 0-3/4, bytes 2-4/6'),
            ('Content-Range', 'bytes 0-3/9223372036854775808'),
            ('ETag', 'W/"weak"'), ('ETag', '"tag with spaces"'),
            ('Content-Length', '3'), ('Content-Encoding', 'gzip'),
            ('Content-Type', 'multipart/byteranges; boundary=test'),
        )
        defaults = {'Content-Range': 'bytes 0-3/6', 'ETag': TAG}
        for name, value in bad_headers:
            with self.subTest(name=name, value=value):
                response = Response(headers=list({**defaults, name: value}.items()))
                download, _ = self.make(response)
                self.assert_error('public_download_invalid_response', True, self.read, download, URL, 0, 4)
                self.assertTrue(response.closed)
        response = Response(headers=[*defaults.items(), ('ETag', '"duplicate"')])
        download, _ = self.make(response)
        self.assert_error('public_download_invalid_response', True, self.read, download, URL, 0, 4)

    def test_truncation_and_overlong_body_never_return_bytes(self):
        for body, code in ((b'abc', 'interrupted'), (b'abcde', 'invalid_response')):
            with self.subTest(body=body):
                response = Response(body)
                download, _ = self.make(response)
                self.assert_error('public_download_' + code, True, self.read, download, URL, 0, 4)
                self.assertTrue(response.closed)

    def test_invalid_ranges_and_missing_checkpoint_are_refused_before_dispatch(self):
        download, opener = self.make()
        for offset, length in ((-1, 4), (0, MAX_RANGE_BYTES + 1), (True, 4), (0, 0)):
            self.assert_error('public_download_invalid_range', False, self.read, download, URL, offset, length)
        self.assert_error('public_download_invalid_checkpoint', False, self.read, download, URL, 4, 4)
        self.assertEqual(opener.calls, [])

    def test_all_hops_use_address_and_work_guards_before_dispatch(self):
        for blocked in ('dns', 'secret', 'work'):
            with self.subTest(blocked=blocked):
                first = Response(b'', status=302, headers=[('Location', 'https://other.example/path')])
                checked, guarded, dispatched = [], [], []

                def check(url):
                    checked.append(url)
                    if blocked == 'secret' and 'other' in url:
                        raise ValueError('source-secret hidden from the owner summary')

                def guard(url):
                    guarded.append(url)
                    return 'Work stopped' if blocked == 'work' and 'other' in url else None

                download, opener = self.make(first, guard=guard,
                                             private_host='other.example' if blocked == 'dns' else None)
                self.assert_error('public_download_refused', True, download.read_range, URL, 0, 4,
                                  address_check=check, before_send=lambda: dispatched.append(True))
                self.assertEqual(len(checked), 2)
                self.assertEqual(len(guarded), 1 if blocked == 'secret' else 2)
                self.assertEqual(dispatched, [True])
                self.assertEqual(len(opener.calls), 1)
                self.assertTrue(first.closed)

    def test_if_range_follows_only_the_verified_representation_url(self):
        final = 'https://cdn.example/data'
        responses = (Response(b'', status=302, headers=[('Location', final)]),
                     Response(b'ef', headers=[('Content-Range', 'bytes 4-5/6'), ('ETag', TAG)]))
        download, opener = self.make(*responses)
        self.read(download, URL, 4, 4, etag=TAG, total=6,
                  source_digest=hashlib.sha256(final.encode()).hexdigest())
        self.assertNotIn('If-range', opener.calls[0][2])
        self.assertEqual(opener.calls[1][2]['If-range'], TAG)

        download, opener = self.make(Response(b'ef', headers=[('Content-Range', 'bytes 4-5/6'), ('ETag', TAG)]))
        self.assert_error('public_download_source_changed', True, self.read, download, URL, 4, 4,
                          etag=TAG, total=6, source_digest=hashlib.sha256(final.encode()).hexdigest())
        self.assertNotIn('If-range', opener.calls[0][2])

    def test_start_rejects_intranet_capable_reader(self):
        with self.assertRaises(ValueError):
            PublicDownload(PublicPageReader(declared=lambda host, port: True))

    def test_download_shares_absolute_body_deadline(self):
        now = [0]

        class DelayedResponse(Response):
            def read(self, size):
                now[0] += 8
                return super().read(min(size, 2))

        response = DelayedResponse()
        opener = Opener([response])
        reader = PublicPageReader(opener=opener, clock=lambda: now[0],
                                  resolver=lambda *args, **kwargs: [(None, None, None, None, ('93.184.216.34', 443))])
        self.assert_error('public_download_interrupted', True,
                          self.read, PublicDownload(reader), URL, 0, 4)
        self.assertTrue(response.closed)

    def test_production_transport_sends_get_over_only_the_pinned_address(self):
        url = 'http://example.com/dataset.gz?view=raw'
        socket = Mock()
        connection = Mock()
        connection.getresponse.return_value = Response()
        reader = PublicPageReader(
            resolver=lambda *args, **kwargs: [(None, None, None, None, ('93.184.216.34', 80))])
        sent = Mock()
        with patch('personal_agent.local_tools.socket.create_connection', return_value=socket) as connect, \
             patch('personal_agent.local_tools.http.client.HTTPConnection', return_value=connection):
            result = self.read(PublicDownload(reader), url, 0, 4, before_send=sent)
        self.assertEqual(result.data, b'abcd')
        self.assertEqual(connect.call_args.args[0], ('93.184.216.34', 80))
        self.assertEqual(connection.request.call_args.args, ('GET', '/dataset.gz?view=raw'))
        self.assertEqual(connection.request.call_args.kwargs['headers']['Range'], 'bytes=0-3')
        sent.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_stopped_work_cannot_dispatch_even_after_address_is_validated(self):
        download, opener = self.make()

        def stopped():
            raise ValueError('Work stopped')

        self.assert_error('public_download_refused', False, self.read,
                          download, URL, 0, 4, before_send=stopped)
        self.assertEqual(opener.calls, [])
