import hashlib

import pytest

from personal_agent import work_download
from personal_agent.agent_runtime import Capabilities, ToolError, WorkBudget, WorkLedger
from personal_agent.local_tools import LocalTools
from personal_agent.public_download import DownloadedRange
from personal_agent.quickstart_store import QuickStore


URL = 'https://example.org/fixture'
PAYLOAD = b'verified text'


@pytest.fixture
def download(tmp_path, monkeypatch):
    store = QuickStore(tmp_path)
    work_id = store.enqueue('Read the source', 'download-fixture')
    with store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (work_id,))
    ledger = WorkLedger(store, work_id, fresh=True)
    calls = []

    class FakeDownload:
        def __init__(self, reader):
            assert reader.declared is None

        def read_range(self, url, offset, length, *, etag, total, source_digest,
                       address_check, before_send):
            address_check(url)
            before_send()
            assert url == URL
            calls.append((offset, etag, total))
            part = PAYLOAD[offset:offset + 4]
            return DownloadedRange(part, '"fixture"', len(PAYLOAD), 'a' * 64,
                                   hashlib.sha256(part).hexdigest())

    monkeypatch.setattr(work_download, 'PublicDownload', FakeDownload)
    capabilities = Capabilities(store, None, {}, '', work_id, lambda *args: None,
                                network=LocalTools(), budget=WorkBudget(ledger=ledger))
    return store, work_id, ledger, capabilities, calls


def test_host_call_uses_verified_offset_and_projects_only_public_fields(download):
    store, work_id, _, caps, calls = download
    fetch = lambda offset: caps.execute('public_download', {'operation': 'fetch', 'url': URL, 'offset': offset})
    first = fetch(0)
    assert first['received'] == 4
    assert set(first) == {'artifact', 'received', 'total', 'revision'}
    with pytest.raises(ToolError, match='verified offset'):
        fetch(0)
    second = fetch(4)
    assert first['artifact'] == second['artifact']
    assert calls == [(0, None, None), (4, '"fixture"', len(PAYLOAD))]
    result = caps.execute('public_download', {'operation': 'preview', 'offset': 0})
    assert result['content'] == 'verified'
    assert store.read_checkpoint(work_id)['received'] == 8


def test_uncommitted_tail_is_not_visible_and_is_replaced(download):
    store, work_id, _, caps, _ = download
    args = {'operation': 'fetch', 'url': URL, 'offset': 0}
    ref = caps.execute('public_download', args)
    path = store.private / 'work-downloads' / ref['artifact']
    with path.open('ab') as output:
        output.write(b'extra uncommitted bytes')
    assert caps.execute('public_download', {'operation': 'preview', 'offset': 0})['content'] == 'veri'
    caps.execute('public_download', {**args, 'offset': 4})
    assert path.read_bytes() == b'verified'


def test_network_stop_is_rechecked_after_a_chunk_arrives(download, monkeypatch):
    store, work_id, _, caps, _ = download
    base_download = work_download.PublicDownload

    class StopAfterResponse(base_download):
        def read_range(self, *args, **kw):
            result = super().read_range(*args, **kw)
            caps.budget.stop = lambda: True
            return result

    monkeypatch.setattr(work_download, 'PublicDownload', StopAfterResponse)
    with pytest.raises(ToolError) as stopped:
        caps.execute('public_download', {'operation': 'fetch', 'url': URL, 'offset': 0})
    assert stopped.value.code == 'stopped'
    assert store.read_checkpoint(work_id) is None
    assert not (store.private / 'work-downloads').exists()
