"""Production dispatch/judgment path with injected worker and transport."""
import hashlib
from unittest.mock import patch

import pytest

import test_contextual_capability_handoff as handoff
from personal_agent import work_download
from personal_agent.public_download import DownloadedRange
from personal_agent.quickstart_store import QuickStore

URL = 'https://example.org/reference'
DATA = b'abc-def'


class TestReadWorkRestart(handoff.HandoffTestCase):
    def test_new_worker_finishes_same_work_from_verified_range(self):
        calls = []
        stop_at_boundary = True

        class FixtureDownload:
            def __init__(self, reader):
                pass

            def read_range(self, url, offset, length, *, etag, total,
                           source_digest, address_check, before_send, on_request, deadline, full_limit):
                if stop_at_boundary and offset == 4:
                    raise SystemExit('fixture stopped after the first committed range')
                address_check(url)
                before_send()
                on_request(url)
                assert url == URL
                calls.append(offset)
                chunk = DATA[offset:offset + length]
                return DownloadedRange(chunk, '"ref"', len(DATA), 'b' * 64,
                                       hashlib.sha256(chunk).hexdigest())

        def fetch(offset):
            return ('public_download', {'operation': 'fetch', 'url': URL, 'offset': offset})

        request = 'Read all bytes of the reference and report its first and last three characters.'
        job = self.store.enqueue(request, 'read-restart-e2e', owner_typed=True)
        self.plan = [fetch(0)]
        self.claim_completion()

        with (patch.object(work_download, 'PublicDownload', FixtureDownload),
              patch.object(work_download, 'RANGE_BYTES', 4)):
            with pytest.raises(SystemExit, match='first committed range'):
                self.service.run_one()
            assert self.store.job(job)['status'] == 'running'
            first = self.store.read_checkpoint(job)
            assert first['received'] == 4
            assert first['digest'] == hashlib.sha256(DATA[:4]).hexdigest()
            assert calls == [0]
            stop_at_boundary = False
            self.store = QuickStore(self.store.root)
            self.service = self.make_service()
            self.service.recover_interrupted_work()
            assert self.store.job(job)['status'] == 'queued'
            self.plan = [('public_download', {'operation': 'fetch', 'offset': 4}), ('public_download', {'operation': 'preview', 'offset': 0})]
            self.text = 'The source starts with abc and ends in def.'
            self.claim_completion()
            self.assertTrue(self.service.run_one())
        assert calls == [0, 4]
        assert self.store.job(job)['status'] == 'succeeded'
        completed = self.store.read_checkpoint(job)
        assert completed['received'] == len(DATA)
        assert completed['artifact'] == first['artifact']
        assert completed['digest'] == hashlib.sha256(DATA).hexdigest()
        with self.store.db() as db:
            rows = db.execute('SELECT role,content FROM messages WHERE job_id=?', (job,)).fetchall()
        assert sum(row['role'] == 'user' and row['content'] == request for row in rows) == 1
        assert sum(row['role'] == 'assistant' and 'ends in def' in row['content'] for row in rows) == 1
