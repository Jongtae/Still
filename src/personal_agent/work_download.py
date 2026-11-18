"""Work-scoped staging for an anonymous, validated public read."""
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from functools import partial
from types import SimpleNamespace

from .local_tools import normalize_public_url
from .messages import msg
from .public_download import MAX_RANGE_BYTES, PublicDownload

RANGE_BYTES = MAX_RANGE_BYTES
WORK_BYTES = 64 * RANGE_BYTES
STAGING_BYTES = 256 * RANGE_BYTES
PREVIEW_BYTES = 4096
INPUT_CHANGED = object()

# Every unbounded consuming repeat in SECRET_PATTERN uses these ASCII bytes.
# PEM, userinfo, and named/quoted values can also consume other bytes, but
# only in bounded portions. Allow for those portions and their assertions:
# even 8192 shell escapes at five UTF-8 bytes each fit with room for context.
_CREDENTIAL_RUN_BYTES = b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/+= \t\r\n\v\f'
_CREDENTIAL_CONTEXT_BYTES = 48 * 1024
_PREVIEW_SCAN_CHUNK = 16 * 1024
_LINE_BREAK = re.compile(rb'[\r\n]')
_PEM_BOUNDARY = re.compile(r'-----(BEGIN|END) (?:[A-Z]{1,20} )?PRIVATE KEY-----')

class WorkDownloadError(ValueError):
    def __init__(self, message, code, *, ran=False):
        super().__init__(message)
        self.code, self.ran = code, ran

def public_checkpoint(checkpoint):
    return {key: checkpoint[key] for key in ('artifact', 'received', 'total', 'revision')}


def checkpoint_note(checkpoint):
    if not checkpoint:
        return ''
    artifact, received, total, revision = (checkpoint.get(key) for key in ('artifact', 'received', 'total', 'revision'))
    if (not isinstance(artifact, str) or not artifact or len(artifact) > 128
            or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for char in artifact)
            or not all(type(value) is int and 0 < value < 2**63 for value in (received, total, revision))
            or received > total):
        return ''
    return (f'Still verified read progress on this same Work. Artifact reference {artifact}, '
            f'checkpoint revision {revision}: {received} of {total} bytes verified; '
            f'{total - received} bytes remain. This is a host observation, not a new owner instruction '
            'or proof of goal completion. Use the verified progress and current authority to decide what remains necessary.')


def checkpoint_snapshot(store, work_id, *, db=None):
    """The state used to build a judgment, independent of its writer's epoch."""
    if db is None:
        with store.db() as db:
            db.execute('BEGIN')
            return checkpoint_snapshot(store, work_id, db=db)
    checkpoint = store.read_checkpoint(work_id, db=db)
    if not checkpoint:
        return None
    from . import trust_record, work_mode
    from .file_workspace import FileWorkspace, active_file_roots
    roots = active_file_roots(store, db=db)
    workspace = FileWorkspace(store).active(db=db)
    # Use the trust registry's normalized authority from this transaction.
    hands = trust_record.hands(SimpleNamespace(config=partial(store.config, db=db)))
    authority = {key: {field: value for field, value in row.items() if field != 'history'}
                 for key, row in hands.items()}
    grants = (roots, workspace, authority)
    return {'checkpoint': public_checkpoint(checkpoint),
            'steers': [tuple(row) for row in db.execute(
                'SELECT source_job_id,content FROM work_steers WHERE job_id=? ORDER BY created,source_job_id', (work_id,))],
            'mode': store.config(work_mode.CONFIG_KEY, None, db=db),
            'grants': hashlib.sha256(json.dumps(grants, sort_keys=True).encode()).hexdigest()}


def checkpoint_judgment(store, ledger, purpose, facts, note, call, redact, *, snapshot=None, eligible=None):
    """Admit once per input/epoch; only the input's still-current writer may commit."""
    from .direct_message_surface import redact_provenance_values

    work_id = ledger.job_id

    def current_checkpoint(db):
        checkpoint = store.read_checkpoint(work_id, db=db)
        if (note and checkpoint and not ledger.lapse(db=db) and not checkpoint['resume_pending']
                and checkpoint['epoch'] == ledger.epoch and not store._read_checkpoint_stopped(db, work_id)
                and store.read_checkpoint_can_settle(work_id, ledger.epoch, db=db)):
            return checkpoint

    def discard(db):
        # This row belongs to the dispatch even after its Work scope is fenced.
        # Only its own pending registration may be removed.
        return db.cursor().execute('DELETE FROM work_read_judgments '
                                   'WHERE job_id=? AND input_digest=? AND epoch=? AND result IS NULL',
                                   (work_id, key, ledger.epoch)).rowcount

    try:
        # Freeze the key before dispatch too: call() may mutate its input objects.
        key = hashlib.sha256(json.dumps((purpose, snapshot, facts), sort_keys=True,
                                       ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        checkpoint = current_checkpoint(db)
        if not checkpoint or snapshot is None:
            return None
        if not store._read_checkpoint_ready(db, work_id, checkpoint, eligible or (lambda *_: True),
                                            expected_epoch=ledger.epoch):
            return None
        if checkpoint_snapshot(store, work_id, db=db) != snapshot:
            return None
        row = db.execute('SELECT epoch,result FROM work_read_judgments WHERE job_id=? AND input_digest=?',
                         (work_id, key)).fetchone()
        if row and row['result'] is not None:
            return redact_provenance_values(json.loads(row['result']), redact)
        if row and row['epoch'] == ledger.epoch:
            return None
        db.execute('INSERT INTO work_read_judgments(job_id,input_digest,epoch) VALUES (?,?,?) '
                   'ON CONFLICT(job_id,input_digest) DO UPDATE SET epoch=excluded.epoch',
                   (work_id, key, ledger.epoch))
    try:
        result = call()
        if result is None or (purpose == 'goal-reached' and result.get('outcome') not in ('yes', 'no')):
            encoded = None
        else:
            result = redact_provenance_values(result, redact)
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except Exception:
        encoded = None
    # Process interruptions propagate with the pending registration intact.
    if encoded is None:
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            discard(db)
        return None
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not current_checkpoint(db):
            return None
        if checkpoint_snapshot(store, work_id, db=db) != snapshot:
            return INPUT_CHANGED if discard(db) else None
        committed = db.execute('UPDATE work_read_judgments SET result=? '
                               'WHERE job_id=? AND input_digest=? AND epoch=? AND result IS NULL',
                               (encoded, work_id, key, ledger.epoch)).rowcount
    return result if committed else None


@contextmanager
def _staging_directory(store, *, create=False):
    directory = store.private / 'work-downloads'
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(directory, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)

def _staged_file(directory, artifact, flags):
    try:
        if uuid.UUID(hex=artifact).hex != artifact:
            raise ValueError
    except (ValueError, AttributeError):
        raise WorkDownloadError('The staged read reference is invalid.', 'read_checkpoint_invalid') from None
    fd = os.open(artifact, flags | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise WorkDownloadError('The staged read is unavailable.', 'read_checkpoint_invalid')
    return fd

def _verified_digest(source, size, expected=None):
    content = hashlib.sha256()
    overdue = size
    while overdue:
        block = source.read(min(RANGE_BYTES, overdue))
        if not block:
            raise WorkDownloadError('Previously verified bytes are unavailable.', 'read_checkpoint_missing', ran=True)
        content.update(block)
        overdue -= len(block)
    if expected is not None and content.hexdigest() != expected:
        raise WorkDownloadError('The staged bytes no longer match their checkpoint.', 'read_checkpoint_corrupt', ran=True)
    return content

def staged_checkpoint_available(store, checkpoint):
    if not checkpoint.get('source_url') or not checkpoint.get('digest'):
        return False
    try:
        with _staging_directory(store) as directory:
            fd = _staged_file(directory, checkpoint['artifact'], os.O_RDONLY)
            with os.fdopen(fd, 'rb') as source:
                _verified_digest(source, checkpoint['received'], checkpoint['digest'])
            return True
    except (OSError, WorkDownloadError):
        return False

def _make_room(db, directory, artifact, size):
    with os.scandir(directory) as entries:
        files = {row.name: row.stat(follow_symlinks=False) for row in entries}
    used = sum(info.st_size for name, info in files.items() if name != artifact)
    if used + size <= STAGING_BYTES:
        return
    # This scratch cache is only readable in the original active Work.
    # Pending and interrupted Works retain their bytes until their status settles.
    candidates = db.execute("""
        SELECT c.value,j.status FROM work_read_checkpoints c
        LEFT JOIN jobs j ON j.id=c.job_id
    """)
    protected = set()
    for row in candidates:
        if row['status'] in {'queued', 'running', 'awaiting_connection', 'awaiting_approval', 'awaiting_context'}:
            value = json.loads(row['value'])
            if not isinstance(value, dict) or not isinstance(value.get('artifact'), str):
                raise WorkDownloadError('The staging index could not be verified.', 'read_checkpoint_invalid', ran=True)
            protected.add(value['artifact'])
    for name, info in sorted(files.items(), key=lambda pair: pair[1].st_mtime):
        if name == artifact or name in protected or not stat.S_ISREG(info.st_mode):
            continue
        try:
            if uuid.UUID(hex=name).hex != name:
                continue
        except ValueError:
            continue
        os.unlink(name, dir_fd=directory)
        used -= info.st_size
        if used + size <= STAGING_BYTES:
            return
    raise WorkDownloadError('The install reached its staged read limit.', 'read_checkpoint_limit', ran=True)

def receive(store, job_id, ledger, reader, url, offset, *, address_check, before_send, guard, on_request):
    """Read one range and publish only bytes whose Work checkpoint commits."""
    if ledger is None or ledger.epoch is None:
        raise WorkDownloadError('The Work has no active read checkpoint scope.', 'read_checkpoint_unavailable')
    previous = store.read_checkpoint(job_id)
    if url is None and previous:
        url = previous.get('source_url')
    if not isinstance(url, str) or len(url) > 8192:
        raise WorkDownloadError('Use the public file address for a new fetch.', 'read_checkpoint_source')
    canonical = normalize_public_url(url, reader.declared, preserve_query=True)
    identity = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    if previous and previous.get('url_digest') != identity:
        raise WorkDownloadError('This Work has a checkpoint for a different source.', 'read_checkpoint_source')
    verified = previous['received'] if previous else 0
    if type(offset) is not int or offset != verified:
        raise WorkDownloadError('Use the verified offset from the current Work checkpoint.', 'read_checkpoint_position')
    if previous and offset == previous['total']:
        return public_checkpoint(previous)
    if offset >= WORK_BYTES:
        raise WorkDownloadError('The Work reached its staged read limit.', 'read_checkpoint_limit')
    length = min(RANGE_BYTES, WORK_BYTES - offset)
    part = PublicDownload(reader).read_range(
        canonical, offset, length, etag=previous.get('etag') if previous else None,
        total=previous.get('total') if previous else None,
        source_digest=previous.get('source_digest') if previous else None,
        address_check=address_check, before_send=before_send, on_request=on_request,
    )
    guard()
    checkpoint = {
        'artifact': previous['artifact'] if previous else uuid.uuid4().hex,
        'received': offset + len(part.data), 'total': part.total,
        'revision': previous['revision'] + 1 if previous else 1,
        'etag': part.etag, 'source_digest': part.source_digest,
        'digest': part.digest, 'url_digest': identity, 'source_url': canonical,
    }
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if store.read_checkpoint(job_id, db=db) != previous:
            raise WorkDownloadError('The Work checkpoint changed before the range committed.', 'read_checkpoint_superseded', ran=True)
        with _staging_directory(store, create=True) as directory:
            fd = _staged_file(directory, checkpoint['artifact'], os.O_RDWR | os.O_CREAT)
            with os.fdopen(fd, 'r+b') as output:
                digest = _verified_digest(output, offset, previous['digest'] if previous else None)
                digest.update(part.data)
                checkpoint['digest'] = digest.hexdigest()
                if not store.record_read_checkpoint(job_id, checkpoint, epoch=ledger.epoch, db=db):
                    raise WorkDownloadError('The Work checkpoint changed before the range committed.', 'read_checkpoint_superseded', ran=True)
                _make_room(db, directory, checkpoint['artifact'], checkpoint['received'])
                output.seek(offset)
                output.truncate()
                output.write(part.data)
                output.flush()
                os.fsync(output.fileno())
            os.fsync(directory)
    return public_checkpoint(checkpoint)

def _read_preview_bytes(source, size):
    data = source.read(size)
    if len(data) != size:
        raise WorkDownloadError('Previously verified bytes are unavailable.', 'read_checkpoint_missing')
    return data


def _preview_context_edge(source, position, limit):
    """Find enough separators to rule out a credential spanning the cut."""
    remaining = _CREDENTIAL_CONTEXT_BYTES
    while position != limit and remaining > 0:
        adjacent = (min(limit, position + _PREVIEW_SCAN_CHUNK) if limit > position
                    else max(limit, position - _PREVIEW_SCAN_CHUNK))
        start, end = sorted((position, adjacent))
        source.seek(start)
        block = _read_preview_bytes(source, end - start)
        remaining -= len(block.translate(None, _CREDENTIAL_RUN_BYTES))
        position = adjacent
    return position


def _open_pem_start(data):
    # A PEM body can contain punctuation. Its bounded character window fits
    # inside the credential context even at four UTF-8 bytes per character.
    max_body, max_marker = 8192, 50
    window = max_body + 2 * max_marker
    start = max(0, len(data) - 4 * window)
    text = data[start:].decode('utf-8', errors='surrogateescape')
    boundary = None
    for match in _PEM_BOUNDARY.finditer(text, max(0, len(text) - window)):
        boundary = match
    if boundary and boundary[1] == 'BEGIN' and len(text) - boundary.end() <= max_body + max_marker:
        return start + len(text[:boundary.start()].encode('utf-8', errors='surrogateescape'))
    return len(data)


def _mask_preview(data, offset, size, values, ledger, *, settled_end):
    from .bounded_execution import SECRET_PATTERN
    from .quickstart_store import QuickStore

    # Keep byte positions even for invalid UTF-8, but match against printable text.
    decoded = data.decode('utf-8', errors='surrogateescape')
    text = re.sub(r'[\udc80-\udcff]', '\ufffd', decoded)
    spans = QuickStore._retired_spans(text, ledger)
    spans.extend(match.span() for match in SECRET_PATTERN.finditer(text))
    for value in values:
        start = text.find(value)
        while start != -1:
            spans.append((start, start + len(value)))
            start = text.find(value, start + 1)

    content = bytearray(data[offset:offset + size])
    if spans and content:
        # Keep offsets only for characters touching the raw preview window.
        byte_offsets = []
        position = 0
        for index, char in enumerate(decoded):
            next_position = position + len(char.encode('utf-8', errors='surrogateescape'))
            if next_position > offset:
                if not byte_offsets:
                    first_char = index
                    byte_offsets.append(position)
                byte_offsets.append(next_position)
                if next_position >= offset + size:
                    break
            position = next_position
        last_char = first_char + len(byte_offsets) - 1
        for start, end in spans:
            start, end = max(start, first_char), min(end, last_char)
            if start < end:
                left = max(0, byte_offsets[start - first_char] - offset)
                right = min(size, byte_offsets[end - first_char] - offset)
                content[left:right] = b'*' * (right - left)
    unsettled = max(0, settled_end - offset)
    if unsettled < len(content):
        content[unsettled:] = b'*' * (len(content) - unsettled)
    return content.decode('utf-8', errors='replace')


def preview(store, job_id, offset):
    from .current_context import redaction_material

    checkpoint = store.read_checkpoint(job_id)
    if not checkpoint or not isinstance(offset, int) or not 0 <= offset < checkpoint['received']:
        raise WorkDownloadError('The requested position is outside the verified range.', 'read_checkpoint_missing')
    try:
        values, ledger = redaction_material(store)
        stored_bytes = max((len(value.encode('utf-8')) for value in values), default=0)
        retired_bytes = 4 * max((int(mark[0]) for mark in (ledger or {}).get('marks', ())), default=0)
        literal_context = max(stored_bytes, retired_bytes)
    except Exception:
        raise WorkDownloadError(msg('agent_runtime.redaction_unavailable'), 'redaction_unavailable') from None
    received = checkpoint['received']
    total = checkpoint['total']
    incomplete = total is None or received < total
    size = min(PREVIEW_BYTES, received - offset)
    settled_end = received
    with _staging_directory(store) as directory:
        fd = _staged_file(directory, checkpoint['artifact'], os.O_RDONLY)
        with os.fdopen(fd, 'rb') as source:
            _verified_digest(source, received, checkpoint['digest'])
            # The backward scan covers any open PEM that could touch the page.
            # The extra literal lookbehind includes the literal cutoff if it
            # falls before the page.
            start = max(0, _preview_context_edge(source, offset, 0) - literal_context)
            end = min(received, _preview_context_edge(source, offset + size, received) + literal_context)
            source.seek(start)
            data = _read_preview_bytes(source, end - start)
            if incomplete:
                settled_end = max(0, received - literal_context)
                # If the context stops before the checkpoint, its forward scan
                # has already covered more bytes than an open PEM can span.
                if end == received:
                    settled_end = min(settled_end, start + _open_pem_start(data))
                line_start = start + 1 + max(data.rfind(b'\n'), data.rfind(b'\r'))
                if line_start < min(settled_end, offset + size):
                    # A later break puts the last line beyond this preview.
                    position = end
                    while position < received:
                        block = _read_preview_bytes(source, min(_PREVIEW_SCAN_CHUNK, received - position))
                        if _LINE_BREAK.search(block):
                            break
                        position += len(block)
                    if position == received:
                        settled_end = line_start
    try:
        content = _mask_preview(data, offset - start, size, values, ledger, settled_end=settled_end - start)
    except Exception:
        raise WorkDownloadError(msg('agent_runtime.redaction_unavailable'), 'redaction_unavailable') from None
    return {**public_checkpoint(checkpoint), 'offset': offset, 'content': content}
