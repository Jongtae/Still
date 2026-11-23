"""#307 slice 2, model-free through Capabilities and the public reader's test opener."""
import json
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from personal_agent.agent_runtime import MEMORY_OWNER, evidence_summary, observed_part, worker_result
from personal_agent.api_requests import save_slot
from personal_agent.browser_jar import MemoryKey
from personal_agent.information_use import work_information_use
from personal_agent.local_tools import LocalTools, PublicPageReader
from personal_agent.owner_forget import OwnerForget
from personal_agent.portable_state import _portable_db
from personal_agent.providers import ProviderError
from personal_agent.quickstart_store import READ_RESULT_EXCERPT_CHARS, READ_RESULT_RETENTION_SECONDS, QuickStore

from test_public_page_reader import Response, Sequence, public_dns
from test_read_results_768 import SECRET, ReadResultsBase

ACTION = 'public_page_read'
URL = 'https://example.test/page'
REDIRECT = 'https://other.example.test/start'
BODY = 'Previously observed public page text.'
ETAG = '"revision-307"'
MODIFIED = 'Wed, 21 Oct 2015 07:28:00 GMT'
CONDITIONAL_HEADERS = {'if-none-match': ETAG, 'if-modified-since': MODIFIED}


class PageResponse(Response):
    def __init__(self, text=BODY, *, url=URL, status=200, headers=None):
        super().__init__(text.encode(), status=status, headers={
            'Content-Type': 'text/plain', 'ETag': ETAG, 'Last-Modified': MODIFIED,
            **(headers or {}),
        })
        self.url = url

    def close(self):
        self.body.close()


class ReadPageRevalidationTests(ReadResultsBase):
    def setUp(self):
        super().setUp()
        self.earlier, self.message_ids = self.exchange()

    def page(self, *, job=None, url=URL, created=None):
        return self.store.record_read_result(
            job or self.earlier, tool=ACTION, text=BODY, source_kind='public', page_url=url,
            page_etag=ETAG, page_last_modified=MODIFIED, now=created,
        )

    def reader(self, *responses):
        opener = Sequence(*responses)
        return PublicPageReader(opener=opener, resolver=public_dns), opener

    def fetch(self, response, *, job=None, url=URL, **caps_kwargs):
        reader, opener = self.reader(response)
        caps = self.caps(job, network=LocalTools(page_reader=reader), **caps_kwargs)
        return caps, caps.execute(ACTION, {'url': url}), opener

    def assert_conditionals(self, opener, expected, *, hop=0):
        request, _timeout = opener.requests[hop]
        conditional = {key.lower(): value for key, value in request.header_items()
                       if key.lower() in CONDITIONAL_HEADERS}
        self.assertEqual(conditional, expected)

    def test_a_200_through_capabilities_keeps_both_validators(self):
        _caps, result, opener = self.fetch(PageResponse(), job=self.earlier)
        self.assert_conditionals(opener, {})
        self.assertEqual((result['etag'], result['last_modified']), (ETAG, MODIFIED))
        (row,) = self.rows(self.earlier)
        self.assertEqual((row['excerpt'], row['page_etag'], row['page_last_modified']), (BODY, ETAG, MODIFIED))

    def test_a_304_reuses_the_kept_text_and_writes_a_new_row(self):
        _caps, first_result, _opener = self.fetch(PageResponse(), job=self.earlier)
        (old,) = self.rows(self.earlier)
        later, _ids = self.exchange()
        response = PageResponse('', status=304)
        caps, result, opener = self.fetch(response, job=later, url='https://EXAMPLE.test:443/page#section')
        self.assert_conditionals(opener, CONDITIONAL_HEADERS)
        self.assertTrue(response.body.closed)
        worker = observed_part(worker_result(ACTION, result))
        self.assertEqual(worker['content'], first_result['content'])
        self.assertEqual(worker['scope'], first_result['scope'])
        self.assertEqual(worker['sources'], [URL])
        self.assertIs(worker['content_truncated'], False)
        self.assertNotIn('title', worker)
        self.assertEqual(worker['kept_from_work'], self.earlier)
        self.assertEqual(worker['unchanged_since'], datetime.fromtimestamp(old['created'], timezone.utc).isoformat())
        (new,) = self.rows(caps.job_id)
        self.assertGreater(new['id'], old['id'])
        self.assertEqual((new['excerpt'], new['page_etag'], new['page_last_modified']), (BODY, ETAG, MODIFIED))
        self.assertEqual(self.store.revalidation_row(URL)['id'], new['id'])

    def test_an_incomplete_or_redacted_page_is_read_again_without_validators(self):
        self.store.api_slot_key = MemoryKey()
        save_slot(self.store, 'fixture', ['example.test'], SECRET, encrypted=True)
        cases = {
            'store-cut': 'word ' * READ_RESULT_EXCERPT_CHARS,
            'reader-cut': 'Short sentence. ' + 'word ' * 6000,
            'redacted': f'{BODY} {SECRET}',
        }
        for path, text in cases.items():
            with self.subTest(path=path):
                url = f'{URL}/{path}'
                self.fetch(PageResponse(text, url=url), job=self.earlier, url=url)
                row = self.rows(self.earlier)[-1]
                self.assertEqual((row['page_etag'], row['page_last_modified']), (None, None))
                self.assertIsNone(self.store.revalidation_row(url))
                _caps, _result, opener = self.fetch(PageResponse(url=url), url=url)
                self.assert_conditionals(opener, {})

    def test_validators_are_sent_only_on_the_matching_redirect_hop(self):
        self.page()
        row = self.store.revalidation_row(URL)
        validators = {'url': row['page_url'], 'etag': row['page_etag'], 'last_modified': row['page_last_modified']}
        reader, opener = self.reader(
            PageResponse('', url=REDIRECT, status=302, headers={'Location': URL}),
            PageResponse('', status=304),
        )
        self.assertIs(reader.read(REDIRECT, validators=validators)['not_modified'], True)
        self.assert_conditionals(opener, {}, hop=0)
        self.assert_conditionals(opener, CONDITIONAL_HEADERS, hop=1)

        reader, opener = self.reader(
            PageResponse('', status=302, headers={'Location': REDIRECT}),
            PageResponse(url=REDIRECT),
        )
        reader.read(URL, validators=validators)
        self.assert_conditionals(opener, CONDITIONAL_HEADERS, hop=0)
        self.assert_conditionals(opener, {}, hop=1)

    def test_a_304_without_validators_is_a_failed_read_and_releases_the_response(self):
        response = PageResponse('', status=304)
        reader, opener = self.reader(response)
        with self.assertRaises(ProviderError):
            reader.read(URL)
        self.assert_conditionals(opener, {})
        self.assertTrue(response.body.closed)

    def test_capabilities_rejects_a_304_without_a_kept_row(self):
        network = SimpleNamespace(execute=lambda plan: {'url': URL, 'not_modified': True})
        caps = self.caps(network=network)
        with self.assertRaises(ProviderError):
            caps.execute(ACTION, {'url': URL})
        self.assertEqual(self.rows(caps.job_id), [])

    def test_ineligible_rows_are_not_used_as_validators(self):
        unattended = self.store.enqueue('scheduled request', 'revalidation-unattended')
        document, _ids = self.exchange()
        self.store.put('file_workspace_document_jobs', [document])
        self.page(url=f'{URL}/expired', created=time.time() - READ_RESULT_RETENTION_SECONDS - 60)
        self.page(url=f'{URL}/unattended', job=unattended)
        self.page(url=f'{URL}/document', job=document)
        for path in ('expired', 'unattended', 'document'):
            with self.subTest(path=path):
                url = f'{URL}/{path}'
                _caps, _result, opener = self.fetch(PageResponse(url=url), url=url)
                self.assert_conditionals(opener, {})
        self.page()
        OwnerForget(self.store).forget(MEMORY_OWNER, 'message:' + ','.join(map(str, self.message_ids)))
        self.assertEqual(self.rows(self.earlier), [])
        _caps, _result, opener = self.fetch(PageResponse())
        self.assert_conditionals(opener, {})

    def test_delegated_and_readonly_works_do_not_use_validators(self):
        self.page()
        self.assertIsNotNone(self.store.revalidation_row(URL))
        for kwargs in ({'delegated': True}, {'readonly': True}):
            with self.subTest(kwargs=kwargs):
                _caps, _result, opener = self.fetch(PageResponse(), **kwargs)
                self.assert_conditionals(opener, {})

    def test_304_evidence_names_the_earlier_work_and_inherits_outside_content(self):
        self.fetch(PageResponse(), job=self.earlier)
        caps, result, _opener = self.fetch(PageResponse('', status=304))
        evidence = evidence_summary(ACTION, result)
        self.assertEqual(evidence['read_page_refs'], [{'work_id': self.earlier, 'url': URL}])
        trace = json.dumps({'host_action': ACTION, 'evidence': evidence})
        self.assertNotIn(BODY, trace)
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (caps.job_id, ACTION, 'succeeded', trace, time.time()))
        audit = work_information_use(self.store, caps.job_id, compare_held_values=False)
        items = [item for group in audit['used'] for item in group['items']]
        self.assertIn(f'a page an earlier Work read {self.earlier}: {URL}', items)
        self.assertTrue(any(source.get('source_work_id') == self.earlier
                            for source in self.store.outside_content_sources(caps.job_id)))

    def test_unsafe_validators_are_neither_kept_nor_sent(self):
        invalid = {'etag': '\r\nInjected: value', 'last_modified': 'x' * 257}
        self.fetch(PageResponse(headers={'ETag': invalid['etag'], 'Last-Modified': invalid['last_modified']}),
                   job=self.earlier)
        (row,) = self.rows(self.earlier)
        self.assertEqual((row['page_etag'], row['page_last_modified']), (None, None))
        reader, opener = self.reader(PageResponse())
        reader.read(URL, validators={'url': URL, **invalid})
        self.assert_conditionals(opener, {})

    def test_validator_echoing_a_held_secret_is_withheld(self):
        self.store.api_slot_key = MemoryKey()
        save_slot(self.store, 'fixture', ['example.test'], SECRET, encrypted=True)
        _caps, result, _opener = self.fetch(PageResponse(headers={'ETag': f'"{SECRET}"'}), job=self.earlier)
        self.assertIsNone(result['etag'])
        (row,) = self.rows(self.earlier)
        self.assertEqual((row['page_etag'], row['page_last_modified']), (None, MODIFIED))

    def test_a_url_only_reader_keeps_working_when_validators_are_available(self):
        self.page()
        reader = SimpleNamespace(read=lambda url: {'url': url, 'content': BODY})
        caps = self.caps(network=LocalTools(page_reader=reader))
        self.assertEqual(caps.execute(ACTION, {'url': URL})['content'], BODY)

    def test_portable_copy_carries_no_validator_rows(self):
        self.page()
        target = self.store.root / 'export.db'
        _portable_db(self.store.path, target)
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM work_read_results').fetchone()[0], 0)

    def test_an_old_store_gains_nullable_validator_columns_without_losing_rows(self):
        row_id = self.page()
        with self.store.db() as db:
            db.execute('ALTER TABLE work_read_results DROP COLUMN page_etag')
            db.execute('ALTER TABLE work_read_results DROP COLUMN page_last_modified')
        self.store = QuickStore(self.store.root)
        (old,) = self.rows(self.earlier)
        self.assertEqual((old['id'], old['excerpt'], old['page_etag'], old['page_last_modified']),
                         (row_id, BODY, None, None))
        self.assertIsNone(self.store.revalidation_row(URL))
        new_id = self.page()
        self.assertEqual(self.store.revalidation_row(URL)['id'], new_id)
