"""#819: a Work's failure text shows the owner the real path; the portable export still masks it."""
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path

from personal_agent.portable_state import DB_RELATIVE, ROOT, export_owner_state
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore


class ExportMasksOwnerPaths(unittest.TestCase):
    def test_failure_texts_keep_paths_locally_and_lose_them_in_the_export(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data = Path(tmp.name) / 'state'
        store = QuickStore(data)
        reason = AgentService._redact_reason('could not read /Users/owner/Documents/x.txt: risk-free plan')
        self.assertEqual(reason, 'could not read /Users/owner/Documents/x.txt: risk-free plan')
        job = store.enqueue('정리해 줘', 'k-819')
        with store.db() as db:
            db.execute("UPDATE jobs SET status='failed',error=?,owner_cause=?,owner_note=? WHERE id=?",
                       (reason, reason, f'kept under {data}/results/a.md', job))
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job, 'read_file', 'failed', '{"error": "no such file /Users/owner/Documents/x.txt"}', 1))
        store.put('browser_step_requests', [{'id': 'r1', 'label': 'open /Users/owner/Downloads/form.pdf', 'state': 'done'}])
        self.assertIn('/Users/owner/Documents/x.txt', store.job(job)['owner_cause'])
        archive = Path(tmp.name) / 'export.tar.gz'
        export_owner_state(data, archive)
        out = Path(tmp.name) / 'unpacked'
        with tarfile.open(archive) as bundle:
            bundle.extractall(out, filter='data')
        with sqlite3.connect(out / ROOT / DB_RELATIVE) as db:
            row = db.execute('SELECT error, owner_cause, owner_note, message FROM jobs WHERE id=?', (job,)).fetchone()
            detail = db.execute('SELECT detail FROM tool_events WHERE job_id=?', (job,)).fetchone()[0]
            steps = db.execute("SELECT value FROM config WHERE key='browser_step_requests'").fetchone()
        exported = ' '.join(str(value) for value in (*row, detail, steps[0] if steps else ''))
        self.assertNotIn('/Users/owner', exported)
        self.assertNotIn(str(data.resolve()), exported)
        self.assertIn('could not read [owner path]', exported)
        self.assertIn('risk-free plan', exported)
        self.assertEqual(row[3], '정리해 줘')


if __name__ == '__main__':
    unittest.main()
