"""#182: the worker reads and searches the file workspace and saves a result, inside its grant.

Evidence class: model-free unit tests over a temporary store and temporary folders.  They prove what
``find_files`` / ``read_file`` / ``save_result`` may reach and write through ``Capabilities.execute``
(the path every route calls); they do not prove how a live model chooses these tools.
"""
import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from personal_agent.agent_runtime import (EFFECT_NONE, SAVED_RESULTS_ROOT, WORKSPACE_REFERENCE_PREFIX, Capabilities,
                                          _evidence_detail, record_effect)
from personal_agent.file_workspace import FileWorkspace
from personal_agent.information_use import CATEGORY_NAMES
from personal_agent.manifests import HOST_TOOLS, WRITE_ACTIONS
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class WorkerFileWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.store = QuickStore(str(self.base / 'data'))
        self.reference = self.base / 'reference'
        self.workspace = self.base / 'workspace'
        self.reference.mkdir()
        self.workspace.mkdir()
        self.trip = self.reference / 'trip.md'
        self.trip.write_text('출장 계획 초안: 9월 3일 출발, 숙소는 역 앞.', encoding='utf-8')
        (self.reference / 'budget.md').write_text('출장 계획 예산: 40만원.', encoding='utf-8')
        self.outside = self.base / 'outside.md'
        self.outside.write_text('출장 계획 outside secret', encoding='utf-8')
        FileWorkspace(self.store).configure([str(self.reference)], str(self.workspace))
        self.root = WORKSPACE_REFERENCE_PREFIX + FileWorkspace(self.store).status()['references'][0]['id']
        self.originals = {path.name: path.read_bytes() for path in self.reference.iterdir()}

    def caps(self, job='job-1', **kwargs):
        return Capabilities(self.store, None, {}, None, job, {}, **kwargs)

    def assertOriginalsUnchanged(self):
        self.assertEqual({path.name: path.read_bytes() for path in self.reference.iterdir()}, self.originals)

    def test_the_tools_are_declared_reads_and_one_bounded_write(self):
        self.assertNotIn('find_files', WRITE_ACTIONS)
        self.assertNotIn('read_file', WRITE_ACTIONS)
        self.assertIn('save_result', WRITE_ACTIONS)
        self.assertEqual(HOST_TOOLS['save_result'].recorded_by_length, 'content', 'the saved text is recorded by length')
        self.assertIn(HOST_TOOLS['save_result'].reads, CATEGORY_NAMES)

    def test_the_worker_finds_reads_and_saves_a_result_citing_what_it_read(self):
        caps = self.caps()
        roots = caps.execute('list_roots', {})['roots']
        self.assertEqual(roots, [{'id': self.root, 'name': 'reference', 'read_only': True}])
        found = caps.execute('find_files', {'query': '출장 계획'})['files']
        self.assertEqual(sorted(item['path'] for item in found), ['budget.md', 'trip.md'])
        self.assertEqual({item['root_id'] for item in found}, {self.root})
        read = caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        self.assertIn('9월 3일 출발', read['content'])
        self.assertTrue(read['read_only'])
        self.assertIn('connected-document', caps.private_provenance)
        saved = caps.execute('save_result', {'title': '9월 출장 정리', 'content': '요약: 9월 3일 출발.'})
        self.assertEqual((saved['saved'], saved['path'], saved['cited']), (True, '9월 출장 정리.md', ['trip.md']))
        body = (self.workspace / '9월 출장 정리.md').read_text(encoding='utf-8')
        self.assertIn('요약: 9월 3일 출발.', body)
        # Only the file the worker read is cited, not budget.md that find_files' scan opened.
        self.assertIn('- trip.md @ ', body)
        self.assertNotIn('budget.md', body)
        self.assertEqual([item['path'] for item in self.store.task_artifacts('job-1')], ['9월 출장 정리.md'])
        self.assertOriginalsUnchanged()
        # The record names the file and its sources, never the saved text.
        record = json.dumps(_evidence_detail('save_result', saved), ensure_ascii=False)
        self.assertIn('9월 출장 정리.md', record)
        self.assertNotIn('요약', record)
        from personal_agent.information_use import _event_items
        self.assertEqual(_event_items('save_result', _evidence_detail('save_result', saved)),
                         ['결과 저장: 9월 출장 정리.md (원본: trip.md)'])

    def test_a_saved_result_is_found_and_read_later_and_a_stale_one_is_not(self):
        first = self.caps('job-1')
        first.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        first.execute('save_result', {'title': '9월 출장 정리', 'content': '요약: 역 앞 숙소.'})
        later = self.caps('job-2')
        self.assertIn({'id': SAVED_RESULTS_ROOT, 'name': 'saved results', 'read_only': True},
                      later.execute('list_roots', {})['roots'])
        hits = [item for item in later.execute('find_files', {'query': '9월 출장 정리'})['files']
                if item['root_id'] == SAVED_RESULTS_ROOT]
        self.assertEqual([(item['path'], item['match']) for item in hits], [('9월 출장 정리.md', 'filename')])
        read = later.execute('read_file', {'root_id': SAVED_RESULTS_ROOT, 'path': '9월 출장 정리.md'})
        self.assertIn('요약: 역 앞 숙소.', read['content'])
        self.assertIn('trip.md', read['content'])
        # The original changed: the result no longer matches its source and is not offered.
        self.trip.write_text('changed', encoding='utf-8')
        self.assertEqual([item for item in later.execute('find_files', {'query': '역 앞 숙소'})['files']
                          if item['root_id'] == SAVED_RESULTS_ROOT], [])
        with self.assertRaises(ValueError):
            later.execute('read_file', {'root_id': SAVED_RESULTS_ROOT, 'path': '9월 출장 정리.md'})

    def test_an_answer_saved_into_a_project_is_readable(self):
        workspace_id = self.store.create_workspace('출장')['id']
        result_id = str(uuid.uuid4())
        with self.store.db() as db:
            db.execute('INSERT INTO workspace_results VALUES (?,?,?,?,?,?)',
                       (result_id, workspace_id, 'old-job', '지난 출장 정리: 숙소 예약 완료', '', time.time()))
        caps = self.caps()
        hits = [item for item in caps.execute('find_files', {'query': '숙소 예약'})['files']
                if item['root_id'] == SAVED_RESULTS_ROOT]
        self.assertEqual([item['path'] for item in hits], ['project/' + result_id])
        read = caps.execute('read_file', {'root_id': SAVED_RESULTS_ROOT, 'path': 'project/' + result_id})
        self.assertEqual((read['title'], read['content']), ('출장', '지난 출장 정리: 숙소 예약 완료'))

    def test_no_read_outside_the_grant(self):
        caps = self.caps()
        (self.reference / 'linked.md').symlink_to(self.outside)
        for root_id, path in ((self.root, '../outside.md'), (self.root, str(self.outside)), (self.root, 'linked.md'),
                              (self.root, '.hidden.md'),
                              # Not a connected reference: an invented id and the result folder's own id.
                              (WORKSPACE_REFERENCE_PREFIX + 'deadbeef', 'trip.md'),
                              (WORKSPACE_REFERENCE_PREFIX + FileWorkspace(self.store).status()['workspace_id'], 'x.md'),
                              # A file in the result folder that is not a saved result.
                              (SAVED_RESULTS_ROOT, 'owner.md'), (SAVED_RESULTS_ROOT, '../reference/trip.md')):
            with self.subTest(root_id=root_id, path=path), self.assertRaises(ValueError):
                caps.execute('read_file', {'root_id': root_id, 'path': path})
        (self.workspace / 'owner.md').write_text('owner private note 출장 계획', encoding='utf-8')
        found = caps.execute('find_files', {'query': '출장 계획'})['files']
        self.assertEqual(sorted(item['path'] for item in found), ['budget.md', 'trip.md'],
                         'neither the symlink target nor an unrecorded result-folder file is searched')
        self.assertEqual(caps.workspace_reads, {})

    def test_an_oversized_reference_is_refused_before_it_is_read(self):
        from unittest.mock import patch
        caps = self.caps()
        with patch('personal_agent.file_workspace.MAX_FILE_BYTES', 20), \
                patch('personal_agent.file_workspace.Path.read_text', side_effect=AssertionError('read')):
            with self.assertRaises(ValueError):
                caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
            self.assertEqual(caps.execute('find_files', {'query': '출장 계획'})['files'], [])
        self.assertEqual(caps.workspace_reads, {})

    def test_saved_result_checks_are_bounded(self):
        from unittest.mock import patch
        for index in range(3):
            caps = self.caps(f'job-{index}')
            caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
            caps.execute('save_result', {'title': f'Result {index}', 'content': f'summary {index}'})
        later = self.caps('job-later')
        checked = []
        original = FileWorkspace._fresh
        with patch.object(FileWorkspace, '_fresh', lambda self, record, deadline=None: checked.append(record['path']) or original(self, record, deadline)):
            later.execute('list_roots', {})
            self.assertEqual(checked, [], 'listing roots checks no saved result')
            later.execute('read_file', {'root_id': SAVED_RESULTS_ROOT, 'path': 'Result 1.md'})
            self.assertEqual(checked, ['Result 1.md'], 'reading one result checks only that one')
            checked.clear()
            self.assertEqual(later.saved_results(deadline=0), [])  # find_files passes its scan deadline
            self.assertEqual(checked, [], 'a passed deadline stops the checks')

    def test_saved_results_leave_search_capacity_for_the_folders(self):
        for index in range(12):
            caps = self.caps(f'job-{index}')
            caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
            caps.execute('save_result', {'title': f'출장 계획 {index}', 'content': 'summary'})
        found = self.caps('job-later').execute('find_files', {'query': '출장 계획'})
        saved = [item for item in found['files'] if item['root_id'] == SAVED_RESULTS_ROOT]
        self.assertEqual(len(saved), 10)
        self.assertTrue(found['truncated'])
        self.assertEqual(sorted(item['path'] for item in found['files'] if item['root_id'] == self.root),
                         ['budget.md', 'trip.md'])

    def test_one_root_can_be_searched_by_its_id(self):
        other = self.base / 'docs'
        other.mkdir()
        (other / 'plan.md').write_text('출장 계획 별도 문서', encoding='utf-8')
        self.store.put('file_roots', [{'id': 'bbbbbbbbbbbb', 'path': str(other)}])
        caps = self.caps()
        self.assertEqual([(item['root_id'], item['path'])
                          for item in caps.execute('find_files', {'query': '출장 계획', 'root_id': 'bbbbbbbbbbbb'})['files']],
                         [('bbbbbbbbbbbb', 'plan.md')])
        self.assertEqual({item['root_id'] for item in caps.execute('find_files', {'query': '출장 계획', 'root_id': self.root})['files']},
                         {self.root})
        with self.assertRaises(ValueError):
            caps.execute('find_files', {'query': '출장 계획', 'root_id': 'not-a-root'})

    def test_a_saved_results_search_uses_the_whole_budget_and_says_when_it_stopped(self):
        from unittest.mock import patch
        for index in range(12):
            caps = self.caps(f'job-{index}')
            caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
            caps.execute('save_result', {'title': f'출장 계획 {index}', 'content': 'summary'})
        later = self.caps('job-later')
        found = later.execute('find_files', {'query': '출장 계획', 'root_id': SAVED_RESULTS_ROOT})
        self.assertEqual((len(found['files']), found['truncated']), (12, False))
        # Freshness checks cut short by the deadline: the search says it is incomplete.
        original = FileWorkspace.current_results
        with patch.object(FileWorkspace, 'current_results',
                          lambda self, needle=None, latest=False, path=None, deadline=None:
                          original(self, needle, latest, path, 0)):
            stopped = later.execute('find_files', {'query': '출장 계획', 'root_id': SAVED_RESULTS_ROOT})
        self.assertEqual((stopped['files'], stopped['truncated']), ([], True))

    def test_the_deadline_also_bounds_one_results_source_checks(self):
        from unittest.mock import patch
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        caps.execute('read_file', {'root_id': self.root, 'path': 'budget.md'})
        caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        workspace = FileWorkspace(self.store)
        ticks = iter([0.0, 0.0, 10.0])  # the record starts in time; the second source is past the deadline
        with patch('personal_agent.file_workspace.time.monotonic', side_effect=lambda: next(ticks, 10.0)):
            self.assertEqual(workspace.current_results(deadline=5.0), [])
        self.assertTrue(workspace.stopped_early)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM file_workspace_results').fetchone()[0], 'current',
                             'a cut-short check never marks the result stale')

    def record_like_the_bridge(self, job, name, result):
        from personal_agent.agent_runtime import evidence_summary
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job, name, 'succeeded', json.dumps({'host_action': name, 'evidence': evidence_summary(name, result)}),
                        time.time()))

    def test_a_restarted_bridge_still_cites_what_the_work_read(self):
        first = self.caps('job-1')
        read = first.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        self.record_like_the_bridge('job-1', 'read_file', read)
        restarted = self.caps('job-1')  # a new bridge process of the same Work
        saved = restarted.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        self.assertEqual((saved['saved'], saved['cited']), (True, ['trip.md']))
        # Another Work's reads are never cited.
        with self.assertRaises(ValueError):
            self.caps('job-2').execute('save_result', {'title': 'Other', 'content': 'summary'})

    def test_an_unavailable_result_folder_is_setup_required(self):
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        caps.execute('save_result', {'title': 'Notes', 'content': 'RESULT-ONLY-TEXT'})
        self.store.put('file_workspace', {**FileWorkspace(self.store).status(), 'workspace': None, 'workspace_id': None})
        later = self.caps('job-later')
        found = later.execute('find_files', {'query': 'RESULT-ONLY-TEXT'})
        self.assertEqual((found['files'], found['needs_setup'], found['requires']), ([], True, 'local-result-write'))
        read = later.execute('read_file', {'root_id': SAVED_RESULTS_ROOT, 'path': 'Notes.md'})
        self.assertEqual((read['needs_setup'], read['requires'], read['content']), (True, 'local-result-write', ''))

    def test_reading_one_saved_result_is_bounded_too(self):
        from unittest.mock import patch
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        with patch.object(FileWorkspace, '_fresh', lambda self, record, deadline=None: FileWorkspace.UNCHECKED):
            with self.assertRaises(ValueError):
                self.caps('job-later').execute('read_file', {'root_id': SAVED_RESULTS_ROOT, 'path': 'Notes.md'})

    def test_a_package_alias_of_read_file_marks_the_work_as_document_history(self):
        service = AgentService(self.store)
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       ('job-1', 'pkg_reader', 'succeeded',
                        json.dumps({'host_action': 'read_file', 'evidence': {'root_id': self.root, 'path': 'trip.md'}}), time.time()))
        self.assertTrue(service.read_file_workspace('job-1'))
        self.assertFalse(service.read_file_workspace('job-2'))

    def test_an_interrupted_save_is_settled_before_a_retry_counts_as_a_replay(self):
        from unittest.mock import patch
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        with patch('personal_agent.file_workspace.os.link', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM file_workspace_results').fetchone()[0], 'pending')
        retried = caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        self.assertEqual((retried['saved'], retried['path']), (True, 'Notes.md'))
        self.assertTrue((self.workspace / 'Notes.md').is_file())

    def test_a_setup_only_read_is_not_document_history(self):
        service = AgentService(self.store)
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       ('job-1', 'read_file', 'succeeded', json.dumps({'host_action': 'read_file', 'evidence': {
                           'root_id': SAVED_RESULTS_ROOT, 'path': 'Notes.md', 'qualifiers': ['setup-required']}}), time.time()))
        self.assertFalse(service.read_file_workspace('job-1'))

    def test_a_detached_reservation_is_not_reported_as_saved(self):
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        with self.store.db() as db:
            db.execute("UPDATE file_workspace_results SET state='detached'")
        again = caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        self.assertEqual((again['saved'], again['already_saved']), (False, False))

    def test_a_setup_only_read_labels_no_document_use(self):
        from personal_agent.agent_runtime import recorded_private_sources
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        self.store.put('file_workspace', {**FileWorkspace(self.store).status(), 'workspace': None, 'workspace_id': None})
        later = self.caps('job-later')
        read = later.execute('read_file', {'root_id': SAVED_RESULTS_ROOT, 'path': 'Notes.md'})
        self.assertTrue(read['needs_setup'])
        self.assertNotIn('connected-document', later.private_provenance)
        self.record_like_the_bridge('job-later', 'read_file', read)
        self.assertEqual(recorded_private_sources(self.store, 'job-later'), set())

    def test_an_empty_saved_results_search_asks_for_no_folder(self):
        self.store.put('file_workspace', {'references': [], 'workspace': None})
        caps = self.caps()
        self.assertEqual(caps.execute('find_files', {'query': 'notes', 'root_id': SAVED_RESULTS_ROOT}),
                         {'files': [], 'truncated': False})

    def test_a_setup_only_search_labels_no_document_use(self):
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        caps.execute('save_result', {'title': 'Notes', 'content': 'RESULT-ONLY-TEXT'})
        self.store.put('file_workspace', {**FileWorkspace(self.store).status(), 'workspace': None, 'workspace_id': None})
        later = self.caps('job-later')
        found = later.execute('find_files', {'query': 'RESULT-ONLY-TEXT'})
        self.assertTrue(found['needs_setup'])
        self.assertNotIn('connected-document', later.private_provenance)

    def test_a_result_in_a_replaced_result_folder_is_no_replay(self):
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        other = self.base / 'other-workspace'
        other.mkdir()
        FileWorkspace(self.store).configure([str(self.reference)], str(other))
        again = caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        self.assertEqual((again['saved'], again['already_saved']), (False, False))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM file_workspace_results').fetchone()[0], 'detached')

    def test_more_saved_results_than_the_cap_is_reported_as_truncated(self):
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        with self.store.db() as db:
            [record] = [dict(row) for row in db.execute('SELECT * FROM file_workspace_results')]
            for index in range(100):
                db.execute('INSERT INTO file_workspace_results(id,request_id,path,content_hash,sources,created,workspace_id,state) '
                           'VALUES (?,?,?,?,?,?,?,?)', (f'r{index}', f'q{index}', f'missing-{index}.md', 'x', record['sources'],
                                                       record['created'] + 1 + index, record['workspace_id'], 'current'))
        found = self.caps('job-later').execute('find_files', {'query': 'summary', 'root_id': SAVED_RESULTS_ROOT})
        self.assertEqual((found['files'], found['truncated']), ([], True))

    def test_an_edited_or_oversized_result_file_is_not_returned(self):
        from unittest.mock import patch
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})
        later = self.caps('job-later')
        # A limit the source fits under and the result file does not.
        limit = self.trip.stat().st_size + 1
        self.assertGreater((self.workspace / 'Notes.md').stat().st_size, limit)
        with patch('personal_agent.file_workspace.MAX_FILE_BYTES', limit), \
                patch('personal_agent.file_workspace.Path.read_bytes', side_effect=AssertionError('read')):
            with self.assertRaises(ValueError):
                later.execute('read_file', {'root_id': SAVED_RESULTS_ROOT, 'path': 'Notes.md'})
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM file_workspace_results').fetchone()[0], 'current')
        (self.workspace / 'Notes.md').write_text('EDITED TEXT', encoding='utf-8')
        with self.assertRaises(ValueError):
            later.execute('read_file', {'root_id': SAVED_RESULTS_ROOT, 'path': 'Notes.md'})
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM file_workspace_results').fetchone()[0], 'stale')

    def test_saved_text_stays_in_the_private_write_set(self):
        caps = self.caps()
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        caps.execute('save_result', {'title': 'Notes', 'content': 'RESULT-TEXT-123'})
        self.assertIn('RESULT-TEXT-123', caps.written_private)

    def test_a_reference_also_granted_as_a_connected_folder_is_found_as_the_reference(self):
        # Review: overlapping grants.  The reference folder is also a generic connected folder.
        self.store.put('file_roots', [{'id': 'aaaaaaaaaaaa', 'path': str(self.reference)}])
        caps = self.caps()
        self.assertEqual(len(caps.roots()), 1, 'the generic grant is usable here')
        found = caps.execute('find_files', {'query': '출장 계획'})['files']
        trip = [item for item in found if item['path'].endswith('trip.md')]
        self.assertEqual([(item['root_id'], item['path']) for item in trip], [(self.root, 'trip.md')],
                         'found once, under the reference root, before the hit limit')
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        self.assertTrue(caps.execute('save_result', {'title': 'Notes', 'content': 'summary'})['saved'])

    def test_a_removed_reference_is_no_longer_read(self):
        caps = self.caps()
        other = self.base / 'other'
        other.mkdir()
        FileWorkspace(self.store).configure([str(other)], str(self.workspace))
        with self.assertRaises(ValueError):
            caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})

    def test_a_save_writes_only_a_new_file_never_an_original_or_an_earlier_file(self):
        owner_file = self.workspace / '9월 출장 정리.md'
        owner_file.write_text('owner content', encoding='utf-8')
        caps = self.caps()
        with self.assertRaises(ValueError):
            caps.execute('save_result', {'title': 'x', 'content': 'nothing read yet'})
        self.assertEqual(sorted(path.name for path in self.workspace.iterdir()), ['9월 출장 정리.md'])
        caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        saved = caps.execute('save_result', {'title': '9월 출장 정리', 'content': 'summary'})
        self.assertEqual(saved['path'], '9월 출장 정리-2.md')
        self.assertEqual(owner_file.read_text(encoding='utf-8'), 'owner content')
        # One Work saves one result: a second save returns the first and writes nothing.
        again = caps.execute('save_result', {'title': 'trip', 'content': 'other summary'})
        self.assertEqual((again['saved'], again['already_saved'], again['id'], again['path']),
                         (False, True, saved['id'], saved['path']))
        self.assertNotIn('other summary', caps.written_private)
        self.assertEqual(sorted(path.name for path in self.workspace.iterdir()), ['9월 출장 정리-2.md', '9월 출장 정리.md'])
        # A title cannot name a path outside the result folder.
        traversal = self.caps('job-3')
        traversal.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        escaped = traversal.execute('save_result', {'title': '../reference/trip', 'content': 'overwrite?'})
        self.assertTrue((self.workspace / escaped['path']).is_file())
        self.assertNotIn('/', escaped['path'])
        self.assertOriginalsUnchanged()

    def test_a_missing_folder_is_setup_required_and_wrote_nothing(self):
        caps = self.caps()
        self.store.put('file_workspace', {'references': [], 'workspace': None})
        result = caps.execute('save_result', {'title': 'x', 'content': 'y'})
        self.assertEqual((result['saved'], result['needs_setup'], result['requires']), (False, True, 'local-reference-read'))
        detail = {'host_action': 'save_result', 'evidence': _evidence_detail('save_result', result)}
        from personal_agent.information_use import _event_items
        self.assertEqual(_event_items('save_result', detail['evidence']), ['결과 저장 안 함: 폴더 연결 필요'])
        self.assertEqual(record_effect('save_result', detail, 'succeeded'), EFFECT_NONE)
        # Nor does it label the Work as holding owner documents.
        from personal_agent.agent_runtime import recorded_private_sources
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       ('job-1', 'save_result', 'succeeded', json.dumps(detail), time.time()))
        self.assertEqual(recorded_private_sources(self.store, 'job-1'), set())
        self.assertEqual(caps.execute('find_files', {'query': 'trip'})['requires'], 'local-folder-read')

    def test_without_document_sharing_nothing_is_read(self):
        caps = self.caps(document_access=False)
        with self.assertRaises(ValueError):
            caps.execute('find_files', {'query': '출장 계획'})
        with self.assertRaises(ValueError):
            caps.execute('read_file', {'root_id': self.root, 'path': 'trip.md'})
        self.assertEqual(caps.workspace_reads, {})



class ReadmeFileJourneyThroughTheWorker(unittest.TestCase):
    """README scenes 1 and 6 (summarize and save; find the saved result) reach the worker whole (#182).

    No rule reads the words: a scripted worker calls ``find_files`` / ``read_file`` / ``save_result``
    on the direct-API route, and a later Work finds and reads the saved result.
    """

    def run_scene(self, locale, source_name, source_text, result_title):
        import build_readme_visuals as visuals
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        store = QuickStore(str(base / 'data'))
        reference, workspace = base / 'reference', base / 'workspace'
        reference.mkdir()
        workspace.mkdir()
        original = reference / source_name
        original.write_text(source_text, encoding='utf-8')
        before = original.read_bytes()
        plan = []
        worker_prompts = []

        def transport(url, body, headers=None, timeout=60):
            names = {tool.get('function', {}).get('name') for tool in body.get('tools', [])}
            if 'agentos_connection_probe' in names:
                return {'choices': [{'message': {'tool_calls': [
                    {'id': 'probe', 'function': {'name': 'agentos_connection_probe', 'arguments': '{}'}}]}}]}
            if 'save_result' in names:
                messages = body['messages']
                worker_prompts.extend(m['content'] for m in messages if m.get('role') == 'user')
                done = sum(1 for m in messages if m.get('role') == 'tool')
                if done < len(plan):
                    name, arguments = plan[done]
                    return {'choices': [{'message': {'tool_calls': [{'id': f'call-{done}', 'function': {
                        'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}}]}
                refs = [json.loads(m['content']).get('ref') for m in messages
                        if m.get('role') == 'tool' and str(m.get('content', '')).startswith('{')]
                if any(refs) and not any((call.get('function') or {}).get('name') == 'finish'
                                         for m in messages for call in (m.get('tool_calls') or [])):
                    return {'choices': [{'message': {'tool_calls': [{'id': 'finish', 'function': {'name': 'finish',
                        'arguments': json.dumps({'status': 'done', 'evidence_refs': [r for r in refs if r],
                                                 'summary': 'done'})}}]}}]}
            return {'choices': [{'message': {'content': 'Done.'}}]}

        service = AgentService(store, ModelAdapter(transport), transport)
        service.use_decision_engine(FixtureDecisionEngine(
            judge=lambda context, proposition: BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            if context.purpose == 'goal-reached' else None))
        service.save_model({'provider': 'compatible', 'endpoint': 'http://127.0.0.1:11434/v1', 'model': 'm', 'api_key': ''})
        self.assertTrue(service.test_model()['ok'])
        service.configure_file_workspace({'references': [str(reference)], 'workspace': str(workspace)})
        root = WORKSPACE_REFERENCE_PREFIX + FileWorkspace(store).status()['references'][0]['id']
        scenes = visuals.SCENES[locale]
        save_ask, find_ask = scenes[0][0].replace('\n', ' '), scenes[5][0].replace('\n', ' ')

        plan[:] = [('find_files', {'query': source_name.rsplit('.', 1)[0]}),
                   ('read_file', {'root_id': root, 'path': source_name}),
                   ('save_result', {'title': result_title, 'content': 'Summary: ship October 12.'})]
        saved = store.enqueue(save_ask, 'scene-1')
        service.run_one()
        job = store.job(saved)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual([path.name for path in workspace.glob('*.md')], [result_title + '.md'])
        self.assertIn(source_name, (workspace / (result_title + '.md')).read_text(encoding='utf-8'))
        self.assertIn('저장됨: ' + result_title + '.md', job['response'])
        self.assertEqual(original.read_bytes(), before)
        self.assertIn(save_ask, worker_prompts[0], 'the owner words reach the worker whole')

        plan[:] = [('find_files', {'query': result_title}),
                   ('read_file', {'root_id': SAVED_RESULTS_ROOT, 'path': result_title + '.md'})]
        worker_prompts.clear()
        found = store.enqueue(find_ask, 'scene-6')
        service.run_one()
        self.assertEqual(store.job(found)['status'], 'succeeded')
        self.assertTrue(any(find_ask in prompt for prompt in worker_prompts))
        self.assertFalse(any('[Saved result:' in prompt for prompt in worker_prompts), 'no rule attached the result')
        [read] = [event['trace']['evidence'] for event in store.task_events(found)
                  if event['tool'] == 'read_file' and event['status'] == 'succeeded']
        self.assertEqual((read['root_id'], read['path']), (SAVED_RESULTS_ROOT, result_title + '.md'))

    def test_a_save_without_folders_parks_the_work_for_the_folder(self):
        """The direct route: the worker's setup-required save parks the Work for one folder grant (#505)."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = QuickStore(str(Path(temp.name) / 'data'))

        def transport(url, body, headers=None, timeout=60):
            names = {tool.get('function', {}).get('name') for tool in body.get('tools', [])}
            if 'agentos_connection_probe' in names:
                return {'choices': [{'message': {'tool_calls': [
                    {'id': 'probe', 'function': {'name': 'agentos_connection_probe', 'arguments': '{}'}}]}}]}
            if 'save_result' in names and not any(m.get('role') == 'tool' for m in body['messages']):
                return {'choices': [{'message': {'tool_calls': [{'id': 'save', 'function': {
                    'name': 'save_result', 'arguments': json.dumps({'title': 'Notes', 'content': 'summary'})}}]}}]}
            return {'choices': [{'message': {'content': 'A folder is needed.'}}]}

        service = AgentService(store, ModelAdapter(transport), transport)
        service.save_model({'provider': 'compatible', 'endpoint': 'http://127.0.0.1:11434/v1', 'model': 'm', 'api_key': ''})
        self.assertTrue(service.test_model()['ok'])
        job_id = store.enqueue('Summarize “Launch review” and save it as “Launch notes”.', 'no-folder')
        service.run_one()
        self.assertEqual(store.job(job_id)['status'], 'awaiting_connection')
        [request] = [event for event in store.task_events(job_id) if event['tool'] == 'local_authority']
        self.assertEqual(request['trace']['scopes'], ['folder:read'])

    def test_an_unpublished_save_is_not_reported_as_saved(self):
        from unittest.mock import patch
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        store = QuickStore(str(base / 'data'))
        (base / 'reference').mkdir()
        (base / 'workspace').mkdir()
        (base / 'reference' / 'trip.md').write_text('trip', encoding='utf-8')
        plan = []

        def transport(url, body, headers=None, timeout=60):
            names = {tool.get('function', {}).get('name') for tool in body.get('tools', [])}
            if 'agentos_connection_probe' in names:
                return {'choices': [{'message': {'tool_calls': [
                    {'id': 'probe', 'function': {'name': 'agentos_connection_probe', 'arguments': '{}'}}]}}]}
            done = sum(1 for m in body.get('messages', []) if m.get('role') == 'tool')
            if 'save_result' in names and done < len(plan):
                name, arguments = plan[done]
                return {'choices': [{'message': {'tool_calls': [{'id': f'c{done}', 'function': {
                    'name': name, 'arguments': json.dumps(arguments)}}]}}]}
            return {'choices': [{'message': {'content': 'Done.'}}]}

        service = AgentService(store, ModelAdapter(transport), transport)
        service.save_model({'provider': 'compatible', 'endpoint': 'http://127.0.0.1:11434/v1', 'model': 'm', 'api_key': ''})
        self.assertTrue(service.test_model()['ok'])
        service.configure_file_workspace({'references': [str(base / 'reference')], 'workspace': str(base / 'workspace')})
        root = WORKSPACE_REFERENCE_PREFIX + FileWorkspace(store).status()['references'][0]['id']
        plan[:] = [('read_file', {'root_id': root, 'path': 'trip.md'}),
                   ('save_result', {'title': 'Notes', 'content': 'summary'})]
        job_id = store.enqueue('save a summary of trip', 'unpublished')
        # The reservation is never marked published (an interruption between publish and settle).
        with patch.object(FileWorkspace, '_mark_current', lambda self, record: None):
            service.run_one()
        self.assertNotIn('저장됨', store.job(job_id)['response'] or '')

    def test_english_scenes(self):
        self.run_scene('en', 'Launch review.md', 'Launch review: ship October 12.', 'Launch notes')

    def test_korean_scenes(self):
        self.run_scene('ko', '출시 검토.md', '출시 검토: 10월 12일 출시.', '출시 메모')


if __name__ == '__main__':
    unittest.main()
