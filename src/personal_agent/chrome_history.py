"""Owner-granted Chrome History reads. SQLite opens only the private copy."""

import json
import math
import os
import shutil
import sqlite3
import stat
import sys
import time
import uuid
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .current_context import redact_known_secrets
from .messages import msg

GRANTS_KEY = 'chrome_history_grants'
PROVENANCE = 'owner-chrome-history'
SNAPSHOTS = 'chrome-history-snapshots'
SOURCE_FILES = ('History', 'History-journal', 'History-wal')
LOCAL_STATE_LIMIT = 4 * 1024 * 1024
ROW_LIMIT = 20
WINDOW_SECONDS = 31 * 86400
QUERY_CHARS = 200
ATTEMPTS = 3
CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
UNIX_OFFSET = 11644473600
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class HistoryError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(msg(f'chrome_history.{reason}'))


class HistoryBusy(HistoryError):
    def __init__(self):
        super().__init__('busy')


def _component(name):
    return (isinstance(name, str) and bool(name) and name not in ('.', '..')
            and '/' not in name and '\x00' not in name)


def _identity(info):
    return info.st_dev, info.st_ino


def _stamp(info):
    return (*_identity(info), info.st_size, info.st_mtime_ns)


@contextmanager
def _directory(path, *, parent=None):
    fd = os.open(path, DIR_FLAGS, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def _open_regular(parent, name):
    # Check the directory entry and the opened file. NONBLOCK also avoids a
    # blocking open if a file is replaced by a FIFO between those operations.
    expected = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
        raise HistoryError('unavailable')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or _identity(info) != _identity(expected)):
            raise HistoryError('unavailable')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def default_chrome_roots():
    if sys.platform == 'darwin':
        return (Path.home() / 'Library/Application Support/Google/Chrome',)
    if sys.platform.startswith('linux'):
        config = Path.home() / '.config'
        return config / 'google-chrome', config / 'chromium'
    return ()


def list_profiles(chrome_root):
    """Read profile names from Local State, with no profile-directory scan."""
    try:
        with _directory(chrome_root) as root, _open_regular(root, 'Local State') as source:
            data = source.read(LOCAL_STATE_LIMIT + 1)
        if len(data) > LOCAL_STATE_LIMIT:
            return []
        cache = json.loads(data)['profile']['info_cache']
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return []
    if not isinstance(cache, dict):
        return []
    return [{'profile_dir': name, 'display_name': row['name'][:120]}
            for name, row in cache.items()
            if _component(name) and isinstance(row, dict)
            and isinstance(row.get('name'), str) and row['name'].strip()]


def _profile_entries(root, name):
    info = os.stat(name, dir_fd=root, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise HistoryError('unavailable')
    return info


def grants(store, *, db=None):
    return store.config(GRANTS_KEY, [], db=db)


def grant_profile(store, chrome_root, profile_dir):
    if not _component(profile_dir):
        raise HistoryError('profile_required')
    profile = next((item for item in list_profiles(chrome_root) if item['profile_dir'] == profile_dir), None)
    if profile is None:
        raise HistoryError('profile_required')
    try:
        with _directory(chrome_root) as root:
            device, inode = _identity(_profile_entries(root, profile_dir))
    except OSError:
        raise HistoryError('unavailable') from None
    record = {**profile, 'device': device, 'inode': inode, 'granted_at': time.time()}
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        entries = [item for item in grants(store, db=db) if item['profile_dir'] != profile_dir]
        store.put(GRANTS_KEY, [*entries, record], db=db)


def revoke_profile(store, profile_dir):
    if not _component(profile_dir):
        raise HistoryError('profile_required')
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        entries = [item for item in grants(store, db=db) if item['profile_dir'] != profile_dir]
        store.put(GRANTS_KEY, entries, db=db)


@contextmanager
def _profile_directory(roots, record):
    name = record['profile_dir']
    if not _component(name):
        raise HistoryError('profile_required')
    identity = record['device'], record['inode']
    with ExitStack() as stack:
        for path in roots:
            try:
                root = stack.enter_context(_directory(path))
                if _identity(_profile_entries(root, name)) != identity:
                    continue
                profile = stack.enter_context(_directory(name, parent=root))
                if _identity(os.fstat(profile)) != identity:
                    continue
            except (OSError, HistoryError):
                continue
            yield root, profile
            return
    raise HistoryError('unavailable')


def _sources(profile, stack):
    sources = {}
    for name in SOURCE_FILES:
        try:
            sources[name] = stack.enter_context(_open_regular(profile, name))
        except FileNotFoundError:
            if name == 'History':
                raise HistoryError('unavailable') from None
        except OSError:
            raise HistoryError('unavailable') from None
    return sources


def _source_stamps(profile):
    stamps = {}
    for name in SOURCE_FILES:
        try:
            stamps[name] = _stamp(os.stat(name, dir_fd=profile, follow_symlinks=False))
        except FileNotFoundError:
            continue
    return stamps


@contextmanager
def _snapshot(store):
    parent = store.private / SNAPSHOTS
    parent.mkdir(mode=0o700, exist_ok=True)
    with _directory(parent) as directory:
        name = uuid.uuid4().hex
        os.mkdir(name, mode=0o700, dir_fd=directory)
        try:
            with _directory(name, parent=directory) as destination:
                yield parent / name, destination
        finally:
            shutil.rmtree(name, dir_fd=directory)


def sweep_snapshots(store):
    """Remove crash leftovers at service startup, without following symlinks."""
    try:
        with _directory(store.private / SNAPSHOTS) as directory:
            for name in os.listdir(directory):
                if (len(name) == 32 and all(char in '0123456789abcdef' for char in name)
                        and stat.S_ISDIR(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode)):
                    shutil.rmtree(name, dir_fd=directory)
    except FileNotFoundError:
        pass


def _copy_source(source, directory, name, remaining):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(name, flags, 0o600, dir_fd=directory), 'wb') as target:
        while remaining and (chunk := source.read(min(remaining, 1024 * 1024))):
            target.write(chunk)
            remaining -= len(chunk)
    return remaining == 0


def _unix_time(value):
    if value is None:
        return None
    if type(value) not in (float, int) or not -UNIX_OFFSET <= value <= 253402300799:
        raise HistoryError('invalid_query')
    return value


def _filters(args):
    query = args.get('query', '')
    profile = args.get('profile')
    limit = args.get('limit', ROW_LIMIT)
    after, before = _unix_time(args.get('after')), _unix_time(args.get('before'))
    if (not isinstance(query, str) or len(query) > QUERY_CHARS or '\x00' in query
            or (profile is not None and (not isinstance(profile, str) or not profile.strip()
                                         or profile in ('.', '..') or '\x00' in profile))
            or type(limit) is not int or limit < 1
            or (after is not None and before is not None and after >= before)):
        raise HistoryError('invalid_query')
    words = query.split()
    if not words and (after is None or before is None or before - after > WINDOW_SECONDS):
        raise HistoryError('invalid_query')
    return words, after, before, min(limit, ROW_LIMIT)


def _like(word):
    return '%' + word.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'


_READ_COLUMNS = {
    'urls': {'id', 'url', 'title', 'visit_count', 'last_visit_time'},
    'visits': {'url', 'visit_time'},
}


def _authorize_query(action, table, column, database, source):
    allowed = (action == sqlite3.SQLITE_SELECT or
               (action == sqlite3.SQLITE_READ and database == 'main' and source is None
                and column in _READ_COLUMNS.get(table, ())) or
               (action == sqlite3.SQLITE_FUNCTION and column == 'like'))
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def _query_snapshot(db, words, after, before, limit):
    clauses, params = [], []
    for word in words:
        clauses.append("(u.title LIKE ? ESCAPE '!' OR u.url LIKE ? ESCAPE '!')")
        params.extend((_like(word), _like(word)))
    window = []
    for value, condition in ((after, 'v.visit_time >= ?'), (before, 'v.visit_time < ?')):
        if value is not None:
            window.append(condition)
            params.append(math.ceil(value * 1_000_000) + UNIX_OFFSET * 1_000_000)
    if window:
        # Semi-join on the visit-time range. Repeated visits do not duplicate an address.
        clauses.append(f"u.id IN (SELECT v.url FROM visits v WHERE {' AND '.join(window)})")
    sql = ('SELECT u.url, u.title, u.visit_count, u.last_visit_time FROM urls u WHERE '
           + ' AND '.join(clauses) + ' ORDER BY u.last_visit_time DESC, u.id DESC LIMIT ?')
    db.set_authorizer(_authorize_query)
    return db.execute(sql, [*params, limit]).fetchall()


def _search_profile(store, roots, record, filters):
    # Adapted from purarue/sqlite_backup's copy_all_files algorithm (MIT):
    # copy sidecars with the database, verify source stamps, and let SQLite
    # recover the copy. Never acquire a SQLite lock on Chrome's files.
    for _ in range(ATTEMPTS):
        if record not in grants(store):
            raise HistoryError('not_granted')
        with _profile_directory(roots, record) as (root, profile), ExitStack() as stack:
            sources = _sources(profile, stack)
            before = {name: _stamp(os.fstat(source.fileno())) for name, source in sources.items()}
            if before != _source_stamps(profile):
                continue
            with _snapshot(store) as (path, destination):
                copied = all(_copy_source(source, destination, name, before[name][2])
                             for name, source in sources.items())
                if not copied or before != _source_stamps(profile):
                    continue
                try:
                    current = _profile_entries(root, record['profile_dir'])
                except OSError:
                    raise HistoryError('unavailable') from None
                if _identity(current) != (record['device'], record['inode']):
                    raise HistoryError('unavailable')
                try:
                    with closing(sqlite3.connect(path / 'History', timeout=0)) as db:
                        db.execute('PRAGMA trusted_schema=OFF')
                        db.execute('PRAGMA temp_store=MEMORY')
                        if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
                            continue
                        rows = _query_snapshot(db, *filters)
                except sqlite3.DatabaseError:
                    continue
                if record not in grants(store):
                    raise HistoryError('not_granted')
                return rows
    raise HistoryBusy()


def _iso(micros):
    try:
        return (CHROME_EPOCH + timedelta(microseconds=micros)).isoformat()
    except (TypeError, OverflowError):
        return None


class ChromeHistory:
    """Settings and Work share this service-held reader. Every call reloads grants."""

    def __init__(self, store, chrome_root=None):
        self.store = store
        self.chrome_root = Path(chrome_root) if chrome_root is not None else None

    def roots(self):
        return (self.chrome_root,) if self.chrome_root is not None else default_chrome_roots()

    def _settings_profiles(self):
        # On Linux prefer Chrome when both variants have profiles.
        for root in self.roots():
            profiles = list_profiles(root)
            if profiles:
                return root, profiles
        return None, []

    def settings(self):
        _, profiles = self._settings_profiles()
        connected = {row['profile_dir']: row['display_name'] for row in grants(self.store)}
        listed = {row['profile_dir'] for row in profiles}
        return {'profiles': [
            *({**row, 'granted': row['profile_dir'] in connected} for row in profiles),
            *({'profile_dir': name, 'display_name': display, 'granted': True, 'missing': True}
              for name, display in connected.items() if name not in listed),
        ]}

    def grant(self, profile_dir):
        root, _ = self._settings_profiles()
        if root is None:
            raise HistoryError('profile_required')
        grant_profile(self.store, root, profile_dir)

    def revoke(self, profile_dir):
        revoke_profile(self.store, profile_dir)

    def search(self, args):
        records = grants(self.store)
        selector = args.get('profile')
        if not records and selector is None:
            return {'rows': [], 'message': msg('chrome_history.no_grant')}
        filters = words, after, before, limit = _filters(args)
        if selector is not None:
            exact = [row for row in records if row['profile_dir'] == selector]
            records = exact or [row for row in records if row['display_name'] == selector]
            if len(records) != 1:
                raise HistoryError('not_granted')
        redact = lambda value: redact_known_secrets(self.store, value)
        matches, reports = [], []
        for index, record in enumerate(records):
            report = {'profile': redact(record['display_name']), 'row_count': 0}
            reports.append(report)
            try:
                matches.extend((row, index) for row in _search_profile(self.store, self.roots(), record, filters))
            except HistoryError as exc:
                if len(records) == 1:
                    raise
                report['status'] = exc.reason
                report['message'] = str(exc)
        rows = []
        for (url, title, count, last_visit), index in sorted(
                matches, key=lambda item: item[0][3] or 0, reverse=True)[:limit]:
            report = reports[index]
            report['row_count'] += 1
            rows.append({'profile': report['profile'], 'url': redact(url)[:512], 'title': redact(title)[:200],
                         'visit_count': count, 'last_visit_time': _iso(last_visit)})
        return {'rows': rows, 'profiles': reports, 'query': redact(' '.join(words)),
                'after': after, 'before': before,
                'content_note': msg('chrome_history.outside_content')}
