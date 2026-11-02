"""The Node.js that ``code_run``'s sandbox runtime runs on, when the host has none (#593).

Adopted, not built: Still obtains Node the way ``quick_tunnel`` obtains
cloudflared.  A Node.js 18 or newer already on the host (Homebrew's ``node``,
which the formula now depends on) is used as is, with the same trust as any
other tool the owner installed.  Otherwise the pinned official release below
is downloaded once from nodejs.org into Still's own tool directory.  Only
``bin/node`` and npm are extracted (tarfile's ``data`` filter), and only after
the archive's pinned SHA-256 matches.  ``bin/node``'s pinned SHA-256 is checked
after extraction and again before each use.

The archive digests are the release's ``SHASUMS256.txt`` lines; that file's
signature was verified against the Node.js release keys
(github.com/nodejs/release-keys) on 2026-11-02.  The ``bin/node`` digests were
read from those verified archives.  Node.js is MIT-licensed; npm is
Artistic-2.0.  Nothing here knows what a run is for.
"""
import hashlib
import io
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = '26.13.0'
DIST = 'https://nodejs.org/dist'
#: ``(system, machine) -> (release platform, archive SHA-256, bin/node SHA-256)`` for ``node-v<VERSION>-<platform>.tar.gz``.
PINS = {
    ('Darwin', 'arm64'): ('darwin-arm64',
                          'd38bd5e5a2ddce6bbb5ce45e46e208337da4d51fffe611e567fd32073d6439f6',
                          '533bd644c5ae0e0ea3a80e433aad16b817447ccba1e1eea701875eac80cb75d4'),
    ('Darwin', 'x86_64'): ('darwin-x64',
                           '525c9b29bb0263d687de60a4cec0e2a6db876a8eb032369e5795eca71e7c358e',
                           'd5891b4336441138426c7cb3920d3b140f4dde1c3dfb3d7152873941e65a3b05'),
    ('Linux', 'x86_64'): ('linux-x64',
                          '5848553868b402d3fabb5b09270649977780a390f832155721025a08b5104455',
                          '0adde40d1b7dfa57c8bc83cbc319758c3ec6ebba21363bb2fa752ea6b267ad1b'),
    ('Linux', 'arm64'): ('linux-arm64',
                         '439e9c438acdf6a6ffe47418da709153d66c4bed812e30a473faacb107dd2791',
                         '66f4a883c880ef336276e7730c01cbae2b92fb8e6a51f7371f920599cac824b7'),
}
#: The release archives are about 55-60 MB; anything far larger is not the pinned file.
MAX_BYTES = 96 * 1024 * 1024
DOWNLOAD_SECONDS = 300


def pin(system=None, machine=None):
    """This platform's pin, or None where nodejs.org publishes no matching build."""
    machine = str(machine or platform.machine()).lower()
    machine = {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(machine, machine)
    return PINS.get((system or platform.system(), machine))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _fetch(url):
    with urllib.request.urlopen(url, timeout=DOWNLOAD_SECONDS) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise RuntimeError('the Node.js download was larger than expected; nothing was run.')
    return data


def _target(directory):
    return Path(directory) / f'node-v{VERSION}'


def installed(directory, *, chosen=None):
    """The path of the pinned ``bin/node`` already in ``directory`` with its digest intact, else None."""
    chosen = chosen or pin()
    node = _target(directory) / 'bin' / 'node'
    if chosen is None or not node.is_file():
        return None
    return str(node) if _sha256(node) == chosen[2] else None


def _wanted(name, prefix):
    """Whether a release member is ``bin/node``, ``bin/npm`` or part of npm's own package."""
    if not name.startswith(prefix):
        return False
    rest = name[len(prefix):]
    return rest in ('bin/node', 'bin/npm') or rest.startswith('lib/node_modules/npm/')


def ensure(directory, *, fetch=None, chosen=None):
    """The pinned ``bin/node`` in ``directory``, downloaded and checked first when needed.

    Raises ``FileNotFoundError`` on a platform without a pin and ``RuntimeError`` (nothing
    kept or run) when the download does not match its pinned digests.
    """
    chosen = chosen or pin()
    if chosen is None:
        raise FileNotFoundError(2, 'No such file or directory', 'node')
    found = installed(directory, chosen=chosen)
    if found:
        return found
    release, archive_sha, node_sha = chosen
    name = f'node-v{VERSION}-{release}'
    data = (fetch or _fetch)(f'{DIST}/v{VERSION}/{name}.tar.gz')
    if hashlib.sha256(data).hexdigest() != archive_sha:
        raise RuntimeError(f'the Node.js {VERSION} download did not match its pinned SHA-256; nothing was run.')
    target = _target(directory)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix='.node-'))
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            members = [member for member in archive if _wanted(member.name, name + '/')]
            archive.extractall(staging, members=members, filter='data')
        unpacked = staging / name
        node = unpacked / 'bin' / 'node'
        if not node.is_file() or _sha256(node) != node_sha:
            raise RuntimeError(f'the Node.js {VERSION} binary did not match its pinned SHA-256; nothing was run.')
        if target.exists():
            # An earlier, damaged copy (its digest did not match above), or one a concurrent Work just finished.
            if installed(directory, chosen=chosen):
                return str(target / 'bin' / 'node')
            shutil.rmtree(target)
        unpacked.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return str(target / 'bin' / 'node')
