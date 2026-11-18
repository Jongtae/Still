"""Work-scoped staging for an anonymous, validated public read."""
import hashlib
import json
import os
import stat
import uuid

from .local_tools import normalize_public_url
from .public_download import MAX_RANGE_BYTES, PublicDownload

RANGE_BYTES = MAX_RANGE_BYTES
WORK_BYTES = 64 * RANGE_BYTES
STAGING_BYTES = 256 * RANGE_BYTES
PREVIEW_BYTES = 4096

class WorkDownloadError(ValueError):
    def __init__(self, message, code, *, ran=False):
        super().__init__(message)
        self.code, self.ran = code, ran

def public_checkpoint(checkpoint):
    return {key: checkpoint[key] for key in ('artifact', 'received', 'total', 'revision')}

def _staged_file(store, artifact, flags):
    try:
        if uuid.UUID(hex=artifact).hex != artifact:
            raise ValueError
    except (ValueError, AttributeError):
        raise WorkDownloadError('The staged read reference is invalid.', 'read_checkpoint_invalid') from None
    directory = store.private / 'work-downloads'
    directory.mkdir(mode=0o700, exist_ok=True)
    root = os.open(directory, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(artifact, flags | os.O_NOFOLLOW, 0o600, dir_fd=root)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise WorkDownloadError('The staged read is unavailable.', 'read_checkpoint_invalid')
        return fd
    finally:
        os.close(root)

def _make_room(store, db, artifact, size):
    directory = store.private / 'work-downloads'
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
    protected = {json.loads(row['value'])['artifact'] for row in candidates
                 if row['status'] not in {'succeeded','cancelled'} and row['status'] is not None}
    for name, info in sorted(files.items(), key=lambda pair: pair[1].st_mtime):
        if name == artifact or name in protected or not stat.S_ISREG(info.st_mode):
            continue
        try:
            if uuid.UUID(hex=name).hex != name:
                continue
        except ValueError:
            continue
        (directory / name).unlink()
        used -= info.st_size
        if used + size <= STAGING_BYTES:
            return
    raise WorkDownloadError('The install reached its staged read limit.', 'read_checkpoint_limit', ran=True)

def receive(store, job_id, ledger, reader, url, offset, *, address_check, guard):
    """Read one range and publish only bytes whose Work checkpoint commits."""
    if ledger is None or ledger.epoch is None:
        raise WorkDownloadError('The Work has no active read checkpoint scope.', 'read_checkpoint_unavailable')
    canonical = normalize_public_url(url, reader.declared)
    identity = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    previous = store.read_checkpoint(job_id)
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
        address_check=address_check, before_send=guard,
    )
    guard()
    checkpoint = {
        'artifact': previous['artifact'] if previous else uuid.uuid4().hex,
        'received': offset + len(part.data), 'total': part.total,
        'revision': previous['revision'] + 1 if previous else 1,
        'etag': part.etag, 'source_digest': part.source_digest,
        'digest': part.digest, 'url_digest': identity,
    }
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not store.record_read_checkpoint(job_id, checkpoint, epoch=ledger.epoch, db=db):
            raise WorkDownloadError('The Work checkpoint changed before the range committed.', 'read_checkpoint_superseded', ran=True)
        fd = _staged_file(store, checkpoint['artifact'], os.O_RDWR | os.O_CREAT)
        with os.fdopen(fd, 'r+b') as output:
            if os.fstat(output.fileno()).st_size < offset:
                raise WorkDownloadError('Previously verified bytes are unavailable.', 'read_checkpoint_missing', ran=True)
            _make_room(store, db, checkpoint['artifact'], checkpoint['received'])
            output.seek(offset)
            output.truncate()
            output.write(part.data)
            output.flush()
            os.fsync(output.fileno())
    return public_checkpoint(checkpoint)

def preview(store, job_id, offset):
    checkpoint = store.read_checkpoint(job_id)
    if not checkpoint or not isinstance(offset, int) or not 0 <= offset < checkpoint['received']:
        raise WorkDownloadError('The requested position is outside the verified range.', 'read_checkpoint_missing')
    fd = _staged_file(store, checkpoint['artifact'], os.O_RDONLY)
    with os.fdopen(fd, 'rb') as source:
        source.seek(offset)
        size = min(PREVIEW_BYTES, checkpoint['received'] - offset)
        data = source.read(size)
    if len(data) != size:
        raise WorkDownloadError('Previously verified bytes are unavailable.', 'read_checkpoint_missing')
    return {**public_checkpoint(checkpoint), 'offset': offset, 'content': data.decode('utf-8', errors='replace')}
