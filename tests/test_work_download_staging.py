from contextlib import contextmanager
import hashlib
from unittest import mock

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
    assert result['content'] == '********'
    assert store.read_checkpoint(work_id)['received'] == 8


def test_uncommitted_tail_is_not_visible_and_is_replaced(download):
    store, work_id, _, caps, _ = download
    args = {'operation': 'fetch', 'url': URL, 'offset': 0}
    ref = caps.execute('public_download', args)
    path = store.private / 'work-downloads' / ref['artifact']
    with path.open('ab') as output:
        output.write(b'extra uncommitted bytes')
    assert caps.execute('public_download', {'operation': 'preview', 'offset': 0})['content'] == '****'
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


@pytest.mark.parametrize('retired', [False, True], ids=['stored', 'retired'])
def test_preview_masks_multiline_secret_prefix_at_incomplete_checkpoint(download, monkeypatch, retired):
    store, _, _, caps, _ = download
    secret = 'orchid river\nsilver cloud\nfinal sunset'
    store.secret('model_key', secret)
    if retired:
        assert store.remove_secret('model_key')
    header = '안내. Readable text\n'.encode() + b'More ordinary text. ' * 9
    verified_prefix = b'orchid river\nsilver'
    suffix = b' visible orchid'
    payload = header + secret.encode() + suffix
    received = len(header) + len(verified_prefix)
    # The ledger also marks the JSON-escaped value, which is longer by two bytes.
    literal_context = 4 * (len(secret) + 2) if retired else len(secret.encode())
    settled_end = received - literal_context
    assert 0 < settled_end < len(header)

    def read_range(reader, url, offset, length, **kwargs):
        end = received if offset == 0 else len(payload)
        part = payload[offset:end]
        return DownloadedRange(part, '"fixture"', len(payload), 'a' * 64, hashlib.sha256(part).hexdigest())

    monkeypatch.setattr(work_download.PublicDownload, 'read_range', read_range)
    fetch = {'operation': 'fetch', 'url': URL}
    offset = len('안내'.encode())
    preview = {'operation': 'preview', 'offset': offset}
    first = caps.execute('public_download', {**fetch, 'offset': 0})
    assert first['received'] == received < first['total']
    partial = caps.execute('public_download', preview)['content']
    assert partial == (header[offset:settled_end] + b'*' * literal_context).decode()

    complete = caps.execute('public_download', {**fetch, 'offset': received})
    assert complete['received'] == complete['total']
    assert caps.execute('public_download', preview)['content'] == (header[offset:] + b'*' * len(secret) + suffix).decode()


def stage_payload(caps, monkeypatch, payload, *, total=None):
    def read_range(reader, url, offset, length, **kwargs):
        part = payload[offset:offset + length]
        return DownloadedRange(part, '"fixture"', total or len(payload), 'a' * 64, hashlib.sha256(part).hexdigest())

    monkeypatch.setattr(work_download.PublicDownload, 'read_range', read_range)
    for offset in range(0, len(payload), work_download.RANGE_BYTES):
        caps.execute('public_download', {'operation': 'fetch', 'url': URL, 'offset': offset})


@pytest.mark.parametrize(('secret', 'offset'), [
    (b'-----BEGIN PRIVATE KEY-----\n' + b'YQ==' * 1600 + b'\n-----END PRIVATE KEY-----', 5000),
    (b'sk_live_' + b'A1_' * 12000, 20000),
    # Quoted-password alternatives can cross punctuation and UTF-8.
    (b'password="' + '한!,'.encode() * 2200 + b'"', 5001),
    (b'password="' + '한!,'.encode() * 2200 + b'"', 0),
], ids=['pem', 'unbounded-token', 'quoted-password-start', 'quoted-password-end'])
def test_completed_preview_masks_credentials_extending_far_beyond_window(download, monkeypatch, secret, offset):
    _, _, _, caps, _ = download
    suffix = b'. Visible'
    stage_payload(caps, monkeypatch, secret + suffix)
    result = caps.execute('public_download', {'operation': 'preview', 'offset': offset})
    assert result['offset'] == offset
    expected = b'*' * (len(secret) - offset) + suffix
    assert result['content'] == expected[:work_download.PREVIEW_BYTES].decode()


@pytest.mark.parametrize('line', [
    b'sk-ab',
    b'123456789:ABCDEFGHIJKLMNOPQRST',
    '한.글@예\u00a0시'.encode(),
    b'{"password":"orchid river silver',
    b': ' * (2 * work_download._CREDENTIAL_CONTEXT_BYTES),
], ids=['provider-prefix', 'numeric-token', 'utf8-and-punctuation', 'open-quoted-password', 'beyond-preview-context'])
def test_preview_masks_last_line_at_incomplete_checkpoint(download, monkeypatch, line):
    _, _, _, caps, _ = download
    prefix = '안내. Readable text\n'.encode()
    payload = prefix + line
    stage_payload(caps, monkeypatch, payload, total=len(payload) + 50)
    result = caps.execute('public_download', {'operation': 'preview', 'offset': 0})
    assert result['content'] == (prefix + b'*' * len(line))[:work_download.PREVIEW_BYTES].decode()


def test_preview_keeps_long_line_with_a_later_break_at_incomplete_checkpoint(download, monkeypatch):
    _, _, _, caps, _ = download
    line = b': ' * (2 * work_download._CREDENTIAL_CONTEXT_BYTES)
    payload = line + b'\nUnsettled text'
    stage_payload(caps, monkeypatch, payload, total=len(payload) + 50)
    result = caps.execute('public_download', {'operation': 'preview', 'offset': 0})
    assert result['content'] == line[:work_download.PREVIEW_BYTES].decode()


@pytest.mark.parametrize('line_break', [b'\n', b'\r'], ids=['lf', 'cr'])
def test_preview_masks_password_value_on_last_line(download, monkeypatch, line_break):
    _, _, _, caps, _ = download
    prefix = b'password:' + line_break
    value = b'  orchid'
    payload = prefix + value
    stage_payload(caps, monkeypatch, payload, total=len(payload) + 50)
    result = caps.execute('public_download', {'operation': 'preview', 'offset': 0})
    assert result['content'] == prefix.decode() + '*' * len(value)


def test_preview_masks_open_pem_across_lines_at_incomplete_checkpoint(download, monkeypatch):
    _, _, _, caps, _ = download
    prefix = '안내. Readable text\n'.encode()
    pem = b'-----BEGIN PRIVATE KEY-----\nYWJj!\nZGVm'
    payload = prefix + pem
    stage_payload(caps, monkeypatch, payload, total=len(payload) + 50)
    result = caps.execute('public_download', {'operation': 'preview', 'offset': 0})
    assert result['content'] == prefix.decode() + '*' * len(pem)
    offset = len(payload) - len(b'!\nZGVm')
    result = caps.execute('public_download', {'operation': 'preview', 'offset': offset})
    assert result['content'] == '*' * (len(payload) - offset)


def test_preview_clamps_literal_boundary_when_total_is_unknown(download, monkeypatch):
    store, work_id, _, caps, _ = download
    store.secret('model_key', 'a stored value longer than the verified bytes')
    payload = b'one line\nsecond line'
    stage_payload(caps, monkeypatch, payload)
    checkpoint = store.read_checkpoint(work_id)
    monkeypatch.setattr(store, 'read_checkpoint', lambda *args, **kwargs: {**checkpoint, 'total': None})
    result = caps.execute('public_download', {'operation': 'preview', 'offset': 0})
    assert result['total'] is None
    assert result['content'] == '*' * len(payload)


def test_preview_reveals_ordinary_last_line_when_checkpoint_completes(download, monkeypatch):
    _, _, _, caps, _ = download
    partial = b'Ordinary text'
    payload = partial + b' continues here'
    stage_payload(caps, monkeypatch, partial, total=len(payload))
    preview = {'operation': 'preview', 'offset': 0}
    assert caps.execute('public_download', preview)['content'] == '*' * len(partial)

    def read_range(reader, url, offset, length, **kwargs):
        part = payload[offset:offset + length]
        return DownloadedRange(part, '"fixture"', len(payload), 'a' * 64, hashlib.sha256(part).hexdigest())

    monkeypatch.setattr(work_download.PublicDownload, 'read_range', read_range)
    complete = caps.execute('public_download', {'operation': 'fetch', 'url': URL, 'offset': len(partial)})
    assert complete['received'] == complete['total'] == len(payload)
    assert caps.execute('public_download', preview)['content'] == payload.decode()


def test_preview_reads_bounded_redaction_context_in_large_prose(download, monkeypatch):
    _, _, _, caps, _ = download
    line = b'First sentence. Second sentence! Items: one, two.\n'
    payload = line * (8 * work_download.RANGE_BYTES // len(line))
    stage_payload(caps, monkeypatch, payload)
    offset = len(payload) // 2
    read_sizes = []
    fdopen = work_download.os.fdopen

    @contextmanager
    def measured_reader(*args, **kwargs):
        with fdopen(*args, **kwargs) as source:
            def read(size):
                block = source.read(size)
                read_sizes.append(len(block))
                return block

            yield mock.Mock(wraps=source, read=read)

    monkeypatch.setattr(work_download.os, 'fdopen', measured_reader)
    result = caps.execute('public_download', {'operation': 'preview', 'offset': offset})
    assert result['content'] == payload[offset:offset + work_download.PREVIEW_BYTES].decode()
    # Digest validation already reads the verified file once. Context search
    # and redaction together should read well under another full pass.
    assert 0 < sum(read_sizes) - len(payload) < len(payload) // 3


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
