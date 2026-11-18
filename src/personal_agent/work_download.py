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

    def writer_is_current(db):
        checkpoint = store.read_checkpoint(work_id, db=db)
        if (not note or not checkpoint or ledger.lapse(db=db) or checkpoint['resume_pending']
                or checkpoint['epoch'] != ledger.epoch or store._read_checkpoint_stopped(db, work_id)
                or not store.read_checkpoint_can_settle(work_id, ledger.epoch, db=db)):
            return False
        return store._read_checkpoint_ready(db, work_id, checkpoint, eligible or (lambda *_: True),
                                            expected_epoch=ledger.epoch)

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
        if not writer_is_current(db) or snapshot is None or checkpoint_snapshot(store, work_id, db=db) != snapshot:
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
        if not writer_is_current(db):
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

def _mask_preview(data, offset, size, values, ledger):
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
    if spans:
        byte_offsets = [0]
        for char in decoded:
            byte_offsets.append(byte_offsets[-1] + len(char.encode('utf-8', errors='surrogateescape')))
        for start, end in spans:
            left = max(0, byte_offsets[start] - offset)
            right = min(size, byte_offsets[end] - offset)
            if left < right:
                content[left:right] = b'*' * (right - left)
    return content.decode('utf-8', errors='replace')


def preview(store, job_id, offset):
    from .current_context import redaction_material

    checkpoint = store.read_checkpoint(job_id)
    if not checkpoint or not isinstance(offset, int) or not 0 <= offset < checkpoint['received']:
        raise WorkDownloadError('The requested position is outside the verified range.', 'read_checkpoint_missing')
    try:
        values, ledger = redaction_material(store)
        margin = max(PREVIEW_BYTES, max((len(value.encode('utf-8')) for value in values), default=0))
    except Exception:
        raise WorkDownloadError(msg('agent_runtime.redaction_unavailable'), 'redaction_unavailable') from None
    received = checkpoint['received']
    size = min(PREVIEW_BYTES, received - offset)
    start = max(0, offset - margin)
    end = min(received, offset + size + margin)
    with _staging_directory(store) as directory:
        fd = _staged_file(directory, checkpoint['artifact'], os.O_RDONLY)
        with os.fdopen(fd, 'rb') as source:
            _verified_digest(source, received, checkpoint['digest'])
            source.seek(start)
            data = source.read(end - start)
    if len(data) != end - start:
        raise WorkDownloadError('Previously verified bytes are unavailable.', 'read_checkpoint_missing')
    try:
        content = _mask_preview(data, offset - start, size, values, ledger)
    except Exception:
        raise WorkDownloadError(msg('agent_runtime.redaction_unavailable'), 'redaction_unavailable') from None
    return {**public_checkpoint(checkpoint), 'offset': offset, 'content': content}
