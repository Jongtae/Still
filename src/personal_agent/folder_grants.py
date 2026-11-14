"""Validation for owner folder grants (AI read folders and file-workspace folders).

One rule set for both entry points, so a folder refused in one place is refused in
the other, and stored grants that later fall outside the rules are blocked at use
time instead of silently widening access.

#820: the owner's folder choice is honoured.  A grant that *is* or lies *inside* a
credential, system or Still-data location is refused (``SENSITIVE``), and so are the
filesystem root and the folders above the home folder (``BROAD``).  The home folder,
and a folder inside it that *contains* a protected location, is granted once the
owner confirms it with what it includes (``ConfirmBroad``); the protected locations
inside it stay out of every read and listing (``excluded``).

Folders are compared by filesystem identity (device and inode of the folder and its
ancestors) as well as by case-folded path, so case-insensitive volumes, firmlinks and
alternative spellings cannot reach a protected folder. The sensitive list is
best-effort: it covers common credential, key, browser and system locations, and the
read-time guards (no dot-files, no symlinks, no escaping the root) remain the second
layer.
"""
import os
from pathlib import Path

# Credential, key and app-data stores under the owner's home. A grant may not be one
# of these or live inside one; a grant inside the home folder that contains one is
# granted after the owner's confirmation and never reads it (#820).
_HOME_SENSITIVE = (
    '.ssh', '.gnupg', '.aws', '.azure', '.kube', '.docker', '.password-store', '.gnome-keyring',
    '.terraform.d', '.mozilla', '.config/gcloud', '.config/gh', '.config/google-chrome',
    '.config/chromium', '.local/share/keyrings',
    'Library/Keychains', 'Library/Cookies', 'Library/Mail', 'Library/Messages', 'Library/Safari',
    'Library/Containers', 'Library/Group Containers', 'Library/Application Support',
)
# System configuration and binaries. Temporary folders stay allowed.
_SYSTEM_SENSITIVE = (
    '/etc', '/private/etc', '/System', '/usr', '/bin', '/sbin', '/dev', '/proc', '/sys',
    '/boot', '/root', '/var/root', '/private/var/root', '/Library/Keychains', '/var/db',
    '/private/var/db', '/var/lib',
)

#: #820: a broad folder is refused only where the owner cannot confirm it (``validate(broad_allowed=False)``).
BROAD = '전체 홈이나 시스템 루트 대신 작업용 하위 폴더를 선택하세요'
SENSITIVE = '인증 정보나 시스템 설정이 있는 폴더는 연결할 수 없습니다'
SYMLINK_CHANGED = '연결한 폴더가 심볼릭 링크로 바뀌어 다시 연결해야 합니다'
UNAVAILABLE = '연결한 폴더를 현재 사용할 수 없습니다'


class ConfirmBroad(ValueError):
    """A broad folder the owner has not confirmed yet (#820).

    ``path`` is the resolved folder and ``includes`` the owner-readable account of
    what granting it gives the AI; the caller asks once and passes the confirmation
    back (``validate(..., confirm_broad=...)``).  Nothing is granted by raising it.
    """
    reason = 'confirm_broad'

    def __init__(self, path):
        from .messages import msg
        self.path = path
        self.includes = msg('folder_grants.broad_includes', path=str(path))
        super().__init__(self.includes)


def _identity(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino)


def _chain(path):
    """Identities of an existing path and every ancestor."""
    return {identity for identity in map(_identity, (path, *path.parents)) if identity}


def _folded(path):
    return Path(os.path.normcase(str(path)).casefold())


def _case_insensitive(path):
    """Whether an existing path's volume aliases a component with changed case."""
    for candidate in (path, *path.parents):
        if not candidate.exists():
            continue
        for index, character in enumerate(candidate.name):
            if not character.isalpha():
                continue
            alternate_name = (candidate.name[:index] + character.swapcase() + candidate.name[index + 1:])
            alternate = candidate.with_name(alternate_name)
            try:
                if alternate.exists() and os.path.samefile(candidate, alternate):
                    return True
            except OSError:
                continue
    return False


def _relation(path, path_chain, other, case_insensitive):
    """``(inside, contains)``: whether path is other or lies inside it, and whether it contains other.

    Compared by identity (device and inode of each folder and its ancestors) and, on
    case-insensitive volumes, by case-folded name.
    """
    other_id = _identity(other)
    try:
        resolved_other = other.resolve()
    except (OSError, RuntimeError):
        # Malformed sensitive aliases must not break checks for every grant.
        # Their lexical path is still included below where case-insensitive
        # matching applies; direct grants to the symlink are blocked at use.
        resolved_other = other
    other_chain = _chain(other) | _chain(resolved_other)
    own = _identity(path)
    inside = bool(other_id and other_id in path_chain) or bool(own and own == _identity(resolved_other))
    contains = bool(own and own in other_chain)
    if case_insensitive:
        mine = _folded(path)
        forms = {_folded(other), _folded(resolved_other)}
        inside = inside or any(mine.is_relative_to(form) for form in forms)
        contains = contains or any(form.is_relative_to(mine) for form in forms)
    return inside, contains


def _sensitive_paths():
    home = Path.home()
    return [home / item for item in _HOME_SENSITIVE] + [Path(item) for item in _SYSTEM_SENSITIVE]


def _protected(store):
    """Locations no grant may be or reach into: credential and system stores, and Still's own private data."""
    return [*_sensitive_paths(), Path(store.private)]


def refusal(path, store, case_insensitive=None):
    """Why an existing, resolved directory may not be granted at all, or None.

    #820: refused are Still's own data folder; a folder that is, or lies inside, a
    credential, system or Still-data location (``SENSITIVE``); the filesystem root
    and the folders above the home folder, which hold other accounts' files the
    location list does not cover (``BROAD``); and, outside the home folder, a
    folder that contains a protected location (``SENSITIVE``, as before).  The home
    folder itself and a folder inside it that contains protected locations are
    not refused: they are granted once the owner confirms them (``broad``).
    """
    chain = _chain(path)
    own = _identity(path)
    if own is None:
        return None
    case_insensitive = _case_insensitive(path) if case_insensitive is None else case_insensitive
    if own == _identity(store.root):
        return SENSITIVE
    relations = [_relation(path, chain, item, case_insensitive) for item in _protected(store)]
    if any(inside for inside, _contains in relations):
        return SENSITIVE
    home_id = _identity(Path.home().resolve())
    if own in _chain(Path.home().resolve()) and own != home_id:
        return BROAD
    if not (home_id and home_id in chain) and any(contains for _inside, contains in relations):
        return SENSITIVE
    return None


def broad(path, store, case_insensitive=None):
    """Whether granting an allowed ``path`` needs the owner's one confirmation (#820).

    The home folder itself, and a folder inside it that contains a protected
    location (credential, system or Still data).  ``refusal`` has already refused
    anything wider.
    """
    chain = _chain(path)
    own = _identity(path)
    if own is None:
        return False
    home_id = _identity(Path.home().resolve())
    if own == home_id:
        return True
    if not (home_id and home_id in chain):
        return False
    case_insensitive = _case_insensitive(path) if case_insensitive is None else case_insensitive
    return any(_relation(path, chain, item, case_insensitive)[1] for item in [*_protected(store), Path(store.root)])


def excluded(path, store, base=None):
    """Whether ``path`` (an existing, resolved file or folder) is a protected location or lies in one (#820).

    The read-time half of a broad grant: what the grant contains but no read or
    listing may reach.  ``path`` is the resolved target, so a symlink inside the
    grant that points into a protected location is caught too.  Compared by
    identity, so case and firmlink spellings of a protected folder are covered.
    ``base`` is the granted folder: Still's whole data folder is left out of a
    grant that contains it, while a grant inside it keeps what it granted.
    """
    chain = _chain(Path(path))
    return bool(chain) and not chain.isdisjoint(protected_identities(store, base))


def protected_identities(store, base=None):
    """The identities of the protected locations that exist now, for a walk that checks many folders.

    Credential and system stores and Still's private data always; Still's data
    folder itself unless the grant ``base`` lies inside it.
    """
    items = list(_protected(store))
    root = _identity(store.root)
    if root and not (base is not None and root in _chain(Path(base))):
        items.append(Path(store.root))
    found = set()
    for item in items:
        for form in {item, _safe_resolve(item)}:
            identity = _identity(form)
            if identity:
                found.add(identity)
    return found


def identity(path):
    """The (device, inode) of an existing path, or None: what ``protected_identities`` holds."""
    return _identity(path)


def _safe_resolve(path):
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def _confirmed(confirm_broad, text, path):
    if confirm_broad is True:
        return True
    if not confirm_broad or isinstance(confirm_broad, (str, bytes)):
        return False
    try:
        return text in confirm_broad or str(path) in confirm_broad
    except TypeError:
        return False


def validate(value, store, confirm_broad=False, broad_allowed=True):
    """Return the resolved folder for an owner-supplied path or raise ValueError.

    ``confirm_broad`` is True, or the owner-confirmed paths (as supplied or
    resolved): a broad folder not among them raises ``ConfirmBroad`` (#820).
    ``broad_allowed=False`` keeps the older refusal (``BROAD``) for a grant the
    owner cannot confirm where it is made: the result-write folder and a folder a
    Work asked for (#505).
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError('폴더 경로를 입력하세요.')
    text = value.strip()
    try:
        supplied = Path(text).expanduser()
    except (RuntimeError, OSError):
        raise ValueError(f'폴더 경로를 확인할 수 없습니다: {text}') from None
    if not supplied.is_absolute():
        raise ValueError(f'/로 시작하는 전체 경로를 입력하세요: {text}')
    if supplied.is_symlink():
        raise ValueError(f'심볼릭 링크 대신 실제 폴더 경로를 입력하세요: {text}')
    if not supplied.exists():
        raise ValueError(f'폴더를 찾을 수 없습니다: {text}')
    path = supplied.resolve()
    if not path.is_dir():
        raise ValueError(f'폴더가 아닙니다: {text}')
    case_insensitive = _case_insensitive(path)
    reason = refusal(path, store, case_insensitive)
    if reason:
        raise ValueError(f'{reason}: {text}')
    if broad(path, store, case_insensitive):
        if not broad_allowed:
            raise ValueError(f'{BROAD}: {text}')
        if not _confirmed(confirm_broad, text, path):
            raise ConfirmBroad(path)
    return path


def blocked(stored_path, store):
    """Reason a previously stored grant must not be used now, or None.

    Re-check the *stored path* before resolving it. A directory that was
    originally granted and later replaced by a symlink must not inherit the
    old grant for its new target, even when that target is otherwise allowed.
    """
    try:
        supplied = Path(stored_path).expanduser()
        if not supplied.is_absolute():
            return UNAVAILABLE
        if any(part.is_symlink() for part in (supplied, *supplied.parents)):
            return SYMLINK_CHANGED
        if not supplied.exists() or not supplied.is_dir():
            return UNAVAILABLE
        path = supplied.resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return UNAVAILABLE
    return refusal(path, store)
