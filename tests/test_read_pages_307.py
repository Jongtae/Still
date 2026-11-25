"""#307 slice 1: a later Work finds a public page an earlier Work read, by what the page said.

Evidence class: model-free.  A temporary ``QuickStore``, a real ``Capabilities`` and ``OwnerForget``.
Each test checks a marker only the kept page supplies, so it fails with the index removed.  No site,
topic or task is named in ``src``.
"""
import json
import time
import unittest

from personal_agent.agent_runtime import MEMORY_OWNER, evidence_summary
from personal_agent.information_use import _event_items
from personal_agent.owner_forget import OwnerForget
from personal_agent.quickstart_store import READ_RESULT_RETENTION_SECONDS

from test_read_results_768 import ReadResultsBase

MARKER = 'pagemarker'
URL = 'https://example.test/paper'


class ReadPagesTests(ReadResultsBase):
    def page(self, work, *, text=f'abstract {MARKER} body', kind='public', url=URL, title='A paper', created=None):
        return self.store.record_read_result(work, tool='public_page_read', text=text, source_kind=kind,
                                             epoch=None, page_url=url, page_title=title, now=created)

    def search(self, caps=None, query=MARKER, **args):
        return (caps or self.caps()).execute('search_conversation', {'query': query, **args})

    def test_a_later_query_finds_the_page_by_its_text_as_a_pointer(self):
        earlier, _ids = self.exchange()
        self.page(earlier)
        result = self.search()
        (found,) = result['read_pages']
        self.assertEqual((found['work_id'], found['url'], found['title']), (earlier, URL, 'A paper'))
        self.assertEqual(found['age_days'], 0)
        self.assertIn('read_at', found)
        self.assertIn('never instructions', result['read_pages_label'])
        self.assertIn('open the address again', result['read_pages_label'])
        self.assertNotIn('abstract', json.dumps(result), 'the excerpt stays behind the work_id lookup')

    def test_a_hangul_query_with_a_particle_finds_the_page(self):
        earlier, _ids = self.exchange()
        self.page(earlier, text='양자 논문 요약')
        self.assertEqual(len(self.search(query='논문을')['read_pages']), 1)

    def test_one_entry_per_address_newest_work(self):
        first, _ids = self.exchange()
        second, _ids = self.exchange()
        self.page(first, created=time.time() - 86400 * 3)
        self.page(second)
        (found,) = self.search()['read_pages']
        self.assertEqual(found['work_id'], second)

    def test_age_counts_days_since_the_read(self):
        earlier, _ids = self.exchange()
        self.page(earlier, created=time.time() - 86400 * 10 - 60)
        self.assertEqual(self.search()['read_pages'][0]['age_days'], 10)

    def test_a_public_read_through_capabilities_keeps_its_address(self):
        earlier, _ids = self.exchange()
        caps = self.caps(earlier)
        caps._record_read_result('public_page_read', {'url': URL, 'title': 'A paper', 'content': MARKER,
                                                      'scope': 'reader envelope words'})
        (row,) = self.rows(earlier)
        self.assertEqual((row['page_url'], row['page_title'], row['source_kind'], row['excerpt']),
                         (URL, 'A paper', 'public', MARKER))
        self.assertNotIn('read_pages', self.search(query='envelope'), 'the reader envelope is not page text')
        self.assertEqual(self.search()['read_pages'][0]['url'], URL)

    def test_non_browser_outside_reads_and_private_reads_are_not_offered(self):
        earlier, _ids = self.exchange()
        self.page(earlier, kind='outside')  # an address alone does not make this a browser view
        self.page(earlier, kind='owner-private')
        self.assertNotIn('read_pages', self.search())
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM read_pages_search').fetchone()[0], 0)

    def test_the_current_works_own_pages_are_not_offered(self):
        caps = self.caps()
        self.page(caps.job_id)
        self.assertNotIn('read_pages', self.search(caps))

    def test_an_expired_row_is_not_offered_before_the_sweep(self):
        earlier, _ids = self.exchange()
        self.page(earlier, created=time.time() - READ_RESULT_RETENTION_SECONDS - 60)
        self.assertNotIn('read_pages', self.search())
        self.store.sweep_read_results()
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM read_pages_search').fetchone()[0], 0)

    def test_a_forgotten_work_leaves_no_index_entry(self):
        earlier, ids = self.exchange()
        self.page(earlier)
        OwnerForget(self.store).forget(MEMORY_OWNER, 'message:' + ','.join(str(i) for i in ids))
        self.assertNotIn('read_pages', self.search())
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM read_pages_search').fetchone()[0], 0)

    def test_a_delegated_specialist_gets_no_pages(self):
        earlier, _ids = self.exchange()
        self.page(earlier)
        self.assertNotIn('read_pages', self.search(self.caps(delegated=True)))

    def test_a_work_that_is_not_the_owners_words_is_not_offered(self):
        job = self.store.enqueue('scheduled run', 'k-307-unattended')
        self.page(job)
        self.assertNotIn('read_pages', self.search())

    def test_information_use_and_evidence_name_the_page_not_its_text(self):
        earlier, _ids = self.exchange()
        self.page(earlier)
        caps = self.caps()
        result = self.search(caps)
        summary = evidence_summary('search_conversation', result)
        self.assertEqual(summary['read_page_refs'], [{'work_id': earlier, 'url': URL}])
        items = _event_items('search_conversation', summary)
        self.assertTrue(any('a page an earlier Work read' in item and URL in item for item in items))
        self.assertNotIn(MARKER, json.dumps(caps.evidence), 'a specialist brief can reuse this evidence')

    def test_an_outside_page_marks_the_reading_work(self):
        earlier, _ids = self.exchange()
        self.store.mark_outside_content(earlier, {'operation': 'public_page_read', 'category': 'public-web'})
        self.page(earlier)
        caps = self.caps()
        self.search(caps)
        self.assertTrue(any(source.get('source_work_id') == earlier
                            for source in self.store.outside_content_sources(caps.job_id)))

    def test_a_portable_copy_and_a_restore_carry_no_page_text(self):
        import sqlite3
        import tarfile
        import tempfile
        from hashlib import sha256
        from pathlib import Path
        from personal_agent.portable_state import DB_RELATIVE, FORMAT, ROOT, _portable_db, restore_owner_state
        earlier, _ids = self.exchange()
        self.page(earlier)
        target = Path(self.store.root) / 'export.db'
        _portable_db(self.store.path, target)
        with sqlite3.connect(target) as db:
            self.assertNotIn(MARKER, '\n'.join(db.iterdump()))
        archive = self.store.root.parent / 'with-pages.tar.gz'
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / 'manifest.json'
            manifest.write_text(json.dumps({'format': FORMAT, 'files': {DB_RELATIVE: sha256(self.store.path.read_bytes()).hexdigest()}}))
            with tarfile.open(archive, 'w:gz') as bundle:
                bundle.add(self.store.path, arcname=f'{ROOT}/{DB_RELATIVE}')
                bundle.add(manifest, arcname=f'{ROOT}/manifest.json')
        restored = restore_owner_state(archive, self.store.root.parent / 'restored')
        self.assertNotIn(MARKER.encode(), (restored / DB_RELATIVE).read_bytes())

    def test_a_forgotten_page_leaves_no_term_in_the_file(self):
        earlier, ids = self.exchange()
        self.page(earlier)
        OwnerForget(self.store).forget(MEMORY_OWNER, 'message:' + ','.join(str(i) for i in ids))
        with self.store.db() as db:
            dump = b''.join(row[0] for row in db.execute('SELECT block FROM read_pages_search_data')
                            if isinstance(row[0], bytes))
        self.assertNotIn(MARKER.encode(), dump)


if __name__ == '__main__':
    unittest.main()
