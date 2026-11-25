"""#307 slice 3, model-free with the real store, Capabilities and browser driver's test worker."""

import json
import sqlite3
import sys
import tarfile
import time
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from personal_agent.agent_runtime import MEMORY_OWNER, ToolError, check_arguments, evidence_summary
from personal_agent.api_requests import save_slot
from personal_agent.browser_jar import CookieJar, MemoryKey
from personal_agent.browser_session import WebKitWorkerDriver
from personal_agent.information_use import work_information_use
from personal_agent.owner_forget import OwnerForget
from personal_agent.portable_state import DB_RELATIVE, FORMAT, ROOT, _portable_db, restore_owner_state
from personal_agent.quickstart_store import READ_RESULT_RETENTION_SECONDS, QuickStore

from test_read_results_768 import SECRET, TOKEN, ReadResultsBase

ACTION = 'browser_open'
URL = 'https://account.example.test/landed'
BODY = 'landed page'
MARKER = 'browserrecallmarker'


class BrowserReadPagesTests(ReadResultsBase):
    def setUp(self):
        super().setUp()
        self.earlier, self.ids = self.exchange()

    def driver(self, *, seed=None, flags=()):
        worker = Path(__file__).with_name('browser_fake_worker.py')
        command = [sys.executable, str(worker), str(self.store.root / 'worker.jsonl'), *flags]
        driver = WebKitWorkerDriver(command=command, seed=seed)
        self.addCleanup(driver.close)
        return driver

    def jar(self):
        jar = CookieJar(self.store.root / 'browser-jar', MemoryKey())
        jar.save_export({'example.test': [{'domain': '.example.test', 'path': '/', 'name': 'sid',
                                         'value': 'fixture-cookie', 'expires': None}]})
        return jar

    def page(self, *, job=None, text=MARKER, url=URL, signed_in=True, created=None,
             tool=ACTION, kind='outside', etag=None, title='Earlier page'):
        return self.store.record_read_result(
            job or self.earlier, tool=tool, text=text, page_url=url, page_title=title,
            source_kind=kind, page_signed_in=signed_in, now=created, page_etag=etag,
        )

    def search(self, caps=None, query=MARKER):
        return (caps or self.caps()).execute('search_conversation', {'query': query})

    def index_ids(self):
        with self.store.db() as db:
            return [row['rowid'] for row in db.execute('SELECT rowid FROM read_pages_search ORDER BY rowid')]

    def test_a_confirmed_cookie_import_marks_only_the_landed_site(self):
        jar = self.jar()
        driver = self.driver(seed=lambda: jar.import_rows()[1])
        caps = self.caps(self.earlier, browser=lambda: driver)
        self.assertIs(caps.execute(ACTION, {'url': URL, 'effect': 'read'})['signed_in'], True)
        anonymous = 'https://other.test/opened'
        self.assertIs(caps.execute(ACTION, {'url': anonymous, 'effect': 'read'})['signed_in'], False)
        self.assertIs(caps.execute('browser_read', {'load_more': '1'})['signed_in'], False)
        with_jar, without_jar, held = self.rows(self.earlier)
        self.assertEqual([row['page_signed_in'] for row in (with_jar, without_jar, held)], [1, 0, 0])
        self.assertTrue(all(row['source_kind'] == 'outside' for row in (with_jar, without_jar, held)))

        result = self.search(query=BODY)
        found = {page['url']: page for page in result['read_pages']}
        self.assertEqual(set(found), {URL, anonymous})
        for page in found.values():
            self.assertEqual(set(page), {'work_id', 'url', 'title', 'read_at', 'age_days', 'signed_in'})
            self.assertEqual((page['work_id'], page['age_days']), (self.earlier, 0))
        self.assertIs(found[URL]['signed_in'], True)
        self.assertIs(found[anonymous]['signed_in'], False)
        self.assertNotIn(BODY, json.dumps(result))

    def test_unavailable_session_provenance_is_conservatively_signed_in(self):
        driver = self.driver(seed=lambda: None)
        caps = self.caps(self.earlier, browser=lambda: driver)
        self.assertIs(caps.execute(ACTION, {'url': URL, 'effect': 'read'})['signed_in'], True)
        self.assertIs(self.search(query=BODY)['read_pages'][0]['signed_in'], True)

    def test_an_unrecorded_sign_in_can_establish_a_session_in_the_worker(self):
        rows = [{'domain': 'idp.test', 'name': 'session', 'value': 'owner-session', 'path': '/'}]
        driver = self.driver(seed=lambda: rows)
        caps = self.caps(self.earlier, browser=lambda: driver)
        start = 'https://shop.test/unrecorded-sign-in'
        self.assertIs(caps.execute(ACTION, {'url': start, 'effect': 'read'})['signed_in'], False)
        caps.execute('browser_click', {'target': 'Go', 'effect': 'navigate'})
        self.assertIn('shop.test', driver.cookies_export()[0], 'the worker acquired a new session')
        page = caps.execute('browser_read', {})
        self.assertEqual(page['url'], 'https://shop.test/landed')
        self.assertIs(page['signed_in'], True)
        self.assertEqual(self.rows(self.earlier)[-1]['page_signed_in'], 1)
        self.assertIs(self.search(query=BODY)['read_pages'][0]['signed_in'], True)

    def test_an_import_timeout_leaves_live_worker_session_provenance_unknown(self):
        rows = []
        driver = self.driver(seed=lambda: rows, flags=('--cookie-import', 'timeout'))
        rows.extend(self.jar().import_rows()[1])
        driver._kill()
        with self.assertRaises(TimeoutError):
            driver.goto(URL, 5)
        self.assertTrue(driver.alive(), 'the worker reported a timeout and stayed alive')
        self.assertIn('example.test', driver.cookies_export()[0])
        caps = self.caps(self.earlier, browser=lambda: driver)
        # Even a site with an empty cookie export cannot be called anonymous
        # while the worker's cookie imports are unconfirmed.
        page = caps.execute(ACTION, {'url': 'https://other.test/landed', 'effect': 'read'})
        self.assertIs(page['signed_in'], True)
        self.assertEqual(self.rows(self.earlier)[-1]['page_signed_in'], 1)

    def test_browser_find_keeps_an_anonymous_views_label(self):
        driver = self.driver()
        caps = self.caps(self.earlier, browser=lambda: driver)
        self.assertIs(caps.execute(ACTION, {'url': URL, 'effect': 'read'})['signed_in'], False)
        found = caps.execute('browser_find', {'text': BODY})
        self.assertIs(found['signed_in'], False)
        self.assertEqual(self.rows(self.earlier)[-1]['page_signed_in'], 0)
        self.assertIs(self.search(query=BODY)['read_pages'][0]['signed_in'], False)

    def test_the_landed_address_owns_the_label_after_a_redirect(self):
        jar = self.jar()
        driver = self.driver(seed=lambda: jar.import_rows()[1])
        caps = self.caps(self.earlier, browser=lambda: driver)
        goto = driver.goto
        with patch.object(driver, 'goto', side_effect=lambda _url, timeout, **kw: goto(URL, timeout, **kw)):
            page = caps.execute(ACTION, {'url': 'https://other.test/redirect', 'effect': 'read'})
        self.assertEqual(page['url'], URL)
        self.assertIs(page['signed_in'], True)
        self.assertIs(self.search(query=BODY)['read_pages'][0]['signed_in'], True)

    def test_model_arguments_cannot_supply_the_session_label(self):
        driver = self.driver()
        caps = self.caps(self.earlier, browser=lambda: driver)
        definition = next(item['function'] for item in caps.definitions() if item['function']['name'] == ACTION)
        forged = {'url': URL, 'effect': 'read', 'signed_in': True}
        with self.assertRaises(ToolError) as error:
            check_arguments(definition['parameters'], forged)
        self.assertEqual(error.exception.code, 'invalid_arguments')
        # The low-level dispatcher also derives the label from the driver.
        self.assertIs(caps.execute(ACTION, forged)['signed_in'], False)
        self.assertIs(self.search(query=BODY)['read_pages'][0]['signed_in'], False)

    def test_only_the_owner_receives_pointers_and_evidence_has_no_page_text(self):
        driver = self.driver(seed=lambda: self.jar().import_rows()[1])
        previous = self.caps(self.earlier, browser=lambda: driver)
        previous.execute(ACTION, {'url': URL, 'effect': 'read'})
        self.assertNotIn('read_pages', self.search(self.caps(delegated=True), query=BODY))
        caps = self.caps()
        result = self.search(caps, query=BODY)
        self.assertNotIn(URL, json.dumps(caps.evidence), 'a specialist brief can reuse this evidence')
        self.assertTrue(any(source.get('source_work_id') == self.earlier
                            for source in self.store.outside_content_sources(caps.job_id)))
        summary = evidence_summary('search_conversation', result)
        self.assertEqual(summary['read_page_refs'], [{'work_id': self.earlier, 'url': URL, 'signed_in': True}])
        self.assertNotIn(BODY, json.dumps(summary))
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (caps.job_id, 'search_conversation', 'succeeded',
                        json.dumps({'host_action': 'search_conversation', 'evidence': summary}), time.time()))
        audit = work_information_use(self.store, caps.job_id, compare_held_values=False)
        items = [item for group in audit['used'] for item in group['items']]
        self.assertIn(f'a page an earlier Work read signed in {self.earlier}: {URL}', items)

    def test_an_address_has_one_pointer_from_the_newest_matching_read(self):
        self.page(text=f'{MARKER} ' * 30, created=time.time() - 86400)
        newer, _ids = self.exchange()
        self.page(job=newer, text=f'{MARKER} ' + 'more ' * 100, signed_in=False)
        (pointer,) = self.search()['read_pages']
        self.assertEqual(pointer['work_id'], newer)
        self.assertIs(pointer['signed_in'], False)

    def test_only_http_browser_views_are_indexed(self):
        for url in ('action:123', 'about:blank', 'data:text/html,test', 'file:///tmp/page', 'ftp://example.test',
                    'https://', 'https:///no-host', 'http://:123', 'https://?no-host'):
            self.page(url=url)
        self.page(kind='owner-private')
        self.page(tool='code_run')
        self.page(tool='browser_sign_in')
        self.page(tool='browser_click')
        self.assertNotIn('read_pages', self.search())
        self.assertEqual(self.index_ids(), [])

    def test_query_secrets_and_credential_shapes_are_redacted_before_indexing(self):
        self.store.api_slot_key = MemoryKey()
        save_slot(self.store, 'fixture', ['example.test'], SECRET, encrypted=True)
        self.page(text=f'{MARKER} {SECRET} {TOKEN}', url=f'{URL}?q={SECRET}&key={TOKEN}', title=f'{SECRET} {TOKEN}')
        (row,) = self.rows(self.earlier)
        with self.store.db() as db:
            index = dict(db.execute('SELECT page_url,page_title,excerpt FROM read_pages_search').fetchone())
        for fields in (row, index):
            self.assertNotIn(SECRET, json.dumps(fields))
            self.assertNotIn(TOKEN, json.dumps(fields))
        self.assertEqual(index['page_url'], f'{URL}?q=[redacted]&key=[redacted]')
        pointer = self.search()['read_pages'][0]
        self.assertEqual(pointer['url'], index['page_url'])
        self.assertNotIn('read_pages', self.search(query='synthetic'))

    def test_legacy_browser_rows_are_backfilled_once_with_conservative_labels(self):
        with self.store.db() as db:
            db.execute('DROP TRIGGER read_pages_search_ai')
            db.execute("""
                CREATE TRIGGER read_pages_search_ai AFTER INSERT ON work_read_results
                WHEN new.source_kind='public' AND new.page_url IS NOT NULL BEGIN
                    INSERT INTO read_pages_search(rowid,page_url,page_title,excerpt)
                    VALUES(new.id,new.page_url,new.page_title,new.excerpt);
                END
            """)
            db.execute('DROP TABLE read_pages_search_meta')
        public = self.page(kind='public', tool='public_page_read', url='https://public.test/')
        browser = self.page(tool='browser_read')
        previous_writer = self.page(kind='owner-private', url='https://browser.test/')
        self.page(url='about:blank')
        self.page(kind='owner-private', tool='read_file')
        with self.store.db() as db:
            db.execute('ALTER TABLE work_read_results DROP COLUMN page_signed_in')
        self.assertEqual(self.index_ids(), [public])
        self.store = QuickStore(self.store.root)
        self.assertEqual(self.index_ids(), [public, browser, previous_writer])
        rows = {row['id']: row for row in self.rows(self.earlier)}
        for row_id in (browser, previous_writer):
            self.assertEqual(rows[row_id]['source_kind'], 'outside')
            self.assertIsNone(rows[row_id]['page_signed_in'])
        found = {page['url']: page for page in self.search()['read_pages']}
        self.assertIs(found[URL]['signed_in'], True)
        self.assertIs(found['https://browser.test/']['signed_in'], True)
        added = self.page(url='https://new.test/')
        self.store = QuickStore(self.store.root)
        self.assertEqual(self.index_ids(), [public, browser, previous_writer, added])

    def test_revalidation_ignores_a_browser_result_even_with_validators(self):
        self.page(etag='"browser"')
        self.assertIsNone(self.store.revalidation_row(URL))
        public = self.page(kind='public', tool='public_page_read', etag='"public"')
        self.page(tool='browser_read', etag='"new browser"')
        self.assertEqual(self.store.revalidation_row(URL)['id'], public)

    def test_expiry_hides_the_pointer_before_sweeping_then_removes_the_index_entry(self):
        expired = self.page(created=time.time() - READ_RESULT_RETENTION_SECONDS - 60)
        self.assertEqual(self.index_ids(), [expired])
        self.assertNotIn('read_pages', self.search())
        self.store.sweep_read_results()
        self.assertEqual(self.rows(self.earlier), [])
        self.assertEqual(self.index_ids(), [])

    def test_forget_removes_the_row_the_index_entry_and_its_terms(self):
        self.page()
        OwnerForget(self.store).forget(MEMORY_OWNER, 'message:' + ','.join(map(str, self.ids)))
        self.assertEqual(self.rows(self.earlier), [])
        self.assertEqual(self.index_ids(), [])
        self.assertNotIn('read_pages', self.search())
        with self.store.db() as db:
            blocks = db.execute('SELECT block FROM read_pages_search_data')
            self.assertFalse(any(MARKER.encode() in block for (block,) in blocks))

    def test_portable_export_and_restore_omit_browser_rows_and_index_entries(self):
        self.page()
        target = self.store.root / 'export.db'
        _portable_db(self.store.path, target)

        archive = self.store.root.parent / 'with-browser-reads.tar.gz'
        manifest = self.store.root.parent / 'manifest.json'
        manifest.write_text(json.dumps({'format': FORMAT, 'files': {
            DB_RELATIVE: sha256(self.store.path.read_bytes()).hexdigest(),
        }}))
        with tarfile.open(archive, 'w:gz') as bundle:
            bundle.add(self.store.path, arcname=f'{ROOT}/{DB_RELATIVE}')
            bundle.add(manifest, arcname=f'{ROOT}/manifest.json')
        restored = restore_owner_state(archive, self.store.root.parent / 'restored') / DB_RELATIVE

        for database in (target, restored):
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM work_read_results').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT count(*) FROM read_pages_search').fetchone()[0], 0)
            self.assertNotIn(MARKER.encode(), database.read_bytes())
