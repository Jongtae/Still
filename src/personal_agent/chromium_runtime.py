"""The Chromium the Linux browser worker drives (#34), downloaded once and checked.

Adopted, not built: Still obtains Chromium the way ``node_runtime`` obtains
Node.js.  The build is Google's official **Chrome for Testing** release that
Playwright 1.64.1 is made for (``playwright/driver/package/browsers.json``:
chromium revision 1248, browser version 156.0.8078.4); Playwright's own
mirror (``cdn.playwright.dev/builds/cft/...``) serves byte-identical archives.
The archive is downloaded from ``storage.googleapis.com/chrome-for-testing-public``
into Still's own tool directory, kept only after its pinned SHA-256 matches,
and the ``chrome`` binary's pinned SHA-256 is checked after extraction and
again before each launch.  Nothing is installed system-wide; ``playwright
install`` is never run.

The archive digests were computed on 2026-11-04 from the Google download and
compared with Playwright's mirror (equal); the ``chrome`` digests were read
from those archives.  Chrome for Testing is Chromium under its BSD-style
licence plus bundled third-party licences (``ABOUT`` / ``LICENSE`` in the archive).

The renderer sandbox is required (owner decision 2026-11-04).  Ubuntu 23.10
and later restrict unprivileged user namespaces with AppArmor
(``kernel.apparmor_restrict_unprivileged_userns``), which Chromium's sandbox
needs; a binary outside the distribution has no profile allowing them.
``apparmor_setup`` gives the owner the one command that installs a profile
for this exact binary path.  Nothing here runs ``sudo`` or falls back to
``--no-sandbox``.  Nothing here knows what a page is for.
"""
import hashlib
import os
import re
import platform
import shlex
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = '156.0.8078.4'
DIST = 'https://storage.googleapis.com/chrome-for-testing-public'
#: ``(system, machine) -> (release platform, archive SHA-256, chrome SHA-256)`` for ``chrome-<platform>.zip``.
PINS = {
    ('Linux', 'x86_64'): ('linux64',
                          '43fd31766c7533a02dda2ded5ad50c2f861cd5400be7509c0abad71129f79d79',
                          '377a9fc07b495c3b8d0ab8ec1fdfd153641759f9756fea34d8dfce4de82f5025'),
    ('Linux', 'arm64'): ('linux-arm64',
                         '910a12fff6170202195f72ef3d353801c7063d301ab80b1f51b77d791fda60ce',
                         '2b3a2bb9c4308ce74cb680dcd50305cfae1f79589305dc92b993f876b5a94f65'),
}
#: The release archives are about 200 MB; anything far larger is not the pinned file.
MAX_BYTES = 320 * 1024 * 1024
#: Unpacked about 420 MB; a zip claiming far more is refused before extraction.
MAX_UNPACKED_BYTES = 640 * 1024 * 1024
DOWNLOAD_SECONDS = 600
#: The sysctl Ubuntu uses to restrict unprivileged user namespaces to AppArmor-profiled programs.
USERNS_RESTRICTION = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
#: The AppArmor profile name and file this module's setup command writes.
APPARMOR_PROFILE = 'still-chromium'
APPARMOR_FILE = Path('/etc/apparmor.d') / APPARMOR_PROFILE
#: A path the profile can name literally: no AppArmor pattern (``* ? [ ] { } ^``), variable (``@``),
#: escape, quote or alternation character, so the attachment is exactly that one file.
_LITERAL_PATH = re.compile(r'/[A-Za-z0-9._+ /-]*')


def pin(system=None, machine=None):
    """This platform's pin, or None where Chrome for Testing publishes no matching build."""
    machine = str(machine or platform.machine()).lower()
    machine = {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(machine, machine)
    return PINS.get((system or platform.system(), machine))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _fetch(url, destination):
    """Stream ``url`` into ``destination``; the SHA-256 of what was written."""
    digest, size = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=DOWNLOAD_SECONDS) as response, open(destination, 'wb') as out:
        for block in iter(lambda: response.read(1 << 20), b''):
            size += len(block)
            if size > MAX_BYTES:
                raise RuntimeError('the Chromium download was larger than expected; nothing was kept.')
            digest.update(block)
            out.write(block)
    return digest.hexdigest()


def _target(directory, chosen):
    return Path(directory) / 'chromium' / VERSION / f'chrome-{chosen[0]}'


def executable(directory, chosen=None):
    """Where the pinned ``chrome`` is (or would be) under ``directory``; None without a pin."""
    chosen = chosen or pin()
    return None if chosen is None else _target(directory, chosen) / 'chrome'


def installed(directory, *, chosen=None):
    """The path of the pinned ``chrome`` already in ``directory`` with its digest intact, else None."""
    chosen = chosen or pin()
    chrome = executable(directory, chosen)
    if chrome is None or not chrome.is_file():
        return None
    return str(chrome) if _sha256(chrome) == chosen[2] else None


def _extract(archive_path, staging):
    """Unpack the zip into ``staging``: regular files and folders only, inside it, permission bits kept."""
    root = Path(staging).resolve()
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if sum(member.file_size for member in members) > MAX_UNPACKED_BYTES:
            raise RuntimeError('the Chromium archive unpacks larger than expected; nothing was kept.')
        for member in members:
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                continue   # the release has none; a link could point outside the folder
            destination = (root / member.filename).resolve()
            if root not in destination.parents and destination != root:
                raise RuntimeError('the Chromium archive names a path outside its folder; nothing was kept.')
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(destination, 'wb') as out:
                shutil.copyfileobj(source, out, 1 << 20)
            # Owner-only write; execute bits as released; never setuid/setgid.
            os.chmod(destination, (mode & 0o755) or 0o644)


def _read_only(root):
    """Drop write permission from the checked build (a guard against accidental change, not a same-user
    attacker, who can change permissions back; such a change is caught by the digest check before each start)."""
    for folder, folders, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            if not os.path.islink(path):
                os.chmod(path, os.stat(path).st_mode & ~0o222)
    for folder, folders, files in os.walk(root, topdown=False):
        os.chmod(folder, 0o555)


def _writable(root):
    for folder, folders, files in os.walk(root):
        os.chmod(folder, 0o755)


def ensure(directory, *, fetch=None, chosen=None):
    """The pinned ``chrome`` in ``directory``, downloaded and checked first when needed.

    Raises ``FileNotFoundError`` on a platform without a pin and ``RuntimeError`` (nothing
    kept or run) when the download does not match its pinned digests.
    """
    chosen = chosen or pin()
    if chosen is None:
        raise FileNotFoundError(2, 'No such file or directory', 'chrome')
    found = installed(directory, chosen=chosen)
    if found:
        return found
    release, archive_sha, chrome_sha = chosen
    name = f'chrome-{release}'
    target = _target(directory, chosen)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix='.chromium-'))
    try:
        archive = staging / f'{name}.zip'
        digest = (fetch or _fetch)(f'{DIST}/{VERSION}/{release}/{name}.zip', archive)
        if digest != archive_sha or _sha256(archive) != archive_sha:
            raise RuntimeError(f'the Chromium {VERSION} download did not match its pinned SHA-256; nothing was run.')
        _extract(archive, staging)
        archive.unlink()
        unpacked = staging / name
        chrome = unpacked / 'chrome'
        if not chrome.is_file() or _sha256(chrome) != chrome_sha:
            raise RuntimeError(f'the Chromium {VERSION} binary did not match its pinned SHA-256; nothing was run.')
        if target.exists():
            # An earlier, damaged copy (its digest did not match above), or one a concurrent start just finished.
            if installed(directory, chosen=chosen):
                return str(target / 'chrome')
            _writable(target)
            shutil.rmtree(target)
        unpacked.rename(target)
        _read_only(target)
    finally:
        if staging.exists():
            _writable(staging)
        shutil.rmtree(staging, ignore_errors=True)
    return str(target / 'chrome')


def userns_restricted(path=USERNS_RESTRICTION):
    """Whether this kernel restricts unprivileged user namespaces to AppArmor-profiled programs."""
    try:
        return Path(path).read_text().strip() == '1'
    except OSError:
        return False


def apparmor_profile(chrome):
    """The AppArmor profile that lets exactly ``chrome`` create the user namespaces its sandbox needs.

    The pattern Ubuntu documents for programs that need unprivileged user
    namespaces: unconfined apart from the ``userns`` permission.  It names one
    absolute path; a different Chromium version needs the command again.
    """
    chrome = str(chrome)
    if not _LITERAL_PATH.fullmatch(chrome) or '//' in chrome or '/../' in chrome or chrome.endswith('/..'):
        raise ValueError('the Chromium path cannot be written into an AppArmor profile literally')
    return ('abi <abi/4.0>,\n'
            'include <tunables/global>\n\n'
            f'profile {APPARMOR_PROFILE} "{chrome}" flags=(unconfined) {{\n'
            '  userns,\n'
            f'  include if exists <local/{APPARMOR_PROFILE}>\n'
            '}\n')


def apparmor_installed(chrome, path=APPARMOR_FILE):
    """Whether the profile file names exactly this ``chrome`` (readable without privileges)."""
    try:
        return Path(path).read_text() == apparmor_profile(chrome)
    except (OSError, ValueError):
        return False


def apparmor_setup(chrome):
    """The one shell command (run by the owner, with sudo) that installs and loads ``apparmor_profile``."""
    profile = apparmor_profile(chrome)
    return (f"printf '%s' {shlex.quote(profile)} | sudo tee {APPARMOR_FILE} >/dev/null"
            f' && sudo apparmor_parser -r {APPARMOR_FILE}')
