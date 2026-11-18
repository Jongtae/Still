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
    work_id = store.enqueue('Read the source', 'download-fixture', owner_typed=True)
    with store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (work_id,))
    ledger = WorkLedger(store, work_id, fresh=True)
    calls = []

    class FakeDownload:
        def __init__(self, reader):
            assert reader.declared is None

        def read_range(self, url, offset, length, *, etag, total, source_digest,
                       address_check, before_send, on_request):
            address_check(url)
            before_send()
            on_request(url)
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
    assert set(first) == {'artifact', 'received', 'total', 'revision', 'destinations'}
    assert first['destinations'] == ['example.org']
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


def test_preview_masks_stored_secret_fragments_at_byte_boundaries(download, monkeypatch):
    store, _, _, caps, _ = download
    left, right = 'hidden word 한'.encode(), '글 other text'.encode()
    second = 'quiet words 한 from a second fixture'.encode()
    store.secret('model_key', (left + right).decode())
    store.secret('telegram_token', second.decode())
    boundary = work_download.PREVIEW_BYTES
    prefix = b'.' * (boundary - len(left))
    suffix = b' visible'
    payload = prefix + left + right + b'\n' + second + suffix

    def read_range(*args, **kwargs):
        return DownloadedRange(payload, '"fixture"', len(payload), 'a' * 64, hashlib.sha256(payload).hexdigest())

    monkeypatch.setattr(work_download.PublicDownload, 'read_range', read_range)
    caps.execute('public_download', {'operation': 'fetch', 'url': URL, 'offset': 0})
    inside_index = len(b'quiet words ') + 1  # Start inside a multibyte character in the second secret.
    offset_inside = boundary + len(right) + 1 + inside_index
    previews = [caps.execute('public_download', {'operation': 'preview', 'offset': offset})
                for offset in (0, boundary, offset_inside)]
    assert [item['offset'] for item in previews] == [0, boundary, offset_inside]
    first, next_page, inside = (item['content'] for item in previews)
    assert first == prefix.decode() + '*' * len(left)
    assert next_page == '*' * len(right) + '\n' + '*' * len(second) + suffix.decode()
    assert inside == '*' * (len(second) - inside_index) + suffix.decode()
    assert (left + right).decode() not in first + next_page


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


def test_restarted_work_consumes_remaining_ranges_once(download):
    store, work_id, old_ledger, caps, calls = download
    previous = caps.execute('public_download', {'operation': 'fetch', 'url': URL, 'offset': 0})
    from personal_agent.quickstart_service import AgentService

    restarted_store = QuickStore(store.root)
    service = AgentService(restarted_store)
    service.recover_interrupted_work()
    assert restarted_store.job(work_id)['status'] == 'queued'
    claimed = service._claim_queued_work()
    assert claimed['id'] == work_id
    replacement = service.work_budget(work_id)
    assert replacement.ledger.epoch != old_ledger.epoch
    assert f'4 of {len(PAYLOAD)} bytes verified' in service.read_checkpoint_note(claimed)
    new_caps = Capabilities(restarted_store, None, {}, '', work_id, lambda *args: None,
                            network=LocalTools(), budget=replacement)
    offset = previous['received']
    while offset < len(PAYLOAD):
        current = new_caps.execute('public_download', {
            'operation': 'fetch', 'url': URL, 'offset': offset})
        offset = current['received']
    assert new_caps.execute('public_download', {'operation': 'preview', 'offset': 0})['content'] == PAYLOAD.decode()
    assert [row[0] for row in calls] == [0, 4, 8, 12]
    assert not service._claim_queued_work()


def test_same_length_modified_staging_cannot_recover_or_reach_model(download):
    store, work_id, _, caps, _ = download
    receipt = caps.execute('public_download', {'operation': 'fetch', 'url': URL, 'offset': 0})
    stage = store.private / 'work-downloads' / receipt['artifact']
    stage.write_bytes(b'XXXX')
    assert not work_download.staged_checkpoint_available(store, store.read_checkpoint(work_id))
    with pytest.raises(ToolError, match='no longer match'):
        caps.execute('public_download', {'operation': 'preview', 'offset': 0})
