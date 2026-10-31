"""Cloudflare Quick Tunnel: a one-time phone link without an ngrok account (#523).

``family_setup.start_tunnel`` stays the one place a public tunnel starts.  It
uses ngrok when ngrok is installed and configured; otherwise it runs
``cloudflared tunnel --url http://127.0.0.1:<port>`` here, which needs no
Cloudflare account (Cloudflare's Quick Tunnel documentation).

Getting ``cloudflared`` is adopted from the installer's uv pattern
(``scripts/install.sh``): one already on PATH or in an official install
directory (Homebrew) is used as is, with the same trust as any other tool the
owner installed.  Otherwise the pinned release below is downloaded from the
project's GitHub release, checked against its SHA-256 *before* anything is
unpacked or made executable, and kept in Still's own tool directory.  A
mismatch is refused and nothing is run.

Boundaries:
- ``--config`` names Still's own config file, so an owner's
  ``~/.cloudflared/config.yml`` ingress never routes this tunnel elsewhere;
- ``--no-autoupdate`` keeps the verified binary the one that runs;
- ``--http-host-header`` makes the instance see ``127.0.0.1:<port>``, as
  ngrok's ``--host-header=rewrite`` does; Cloudflare still adds ``CF-*`` and
  ``X-Forwarded-*`` headers, so every tunnelled request is relayed and never
  local (#594).
"""
import hashlib
import io
import os
import platform
import re
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

#: cloudflared 2026.10.7, Apache-2.0.  The digests are GitHub's own per-asset
#: SHA-256 for this release, re-computed from a download on 2026-10-31.  The
#: release notes list the same digests for the Linux binaries; their darwin
#: entries differ from the published (signed and notarized) archives, so the
#: asset digests are the ones pinned.
VERSION = '2026.10.7'
RELEASE = f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/'
ASSETS = {
    ('darwin', 'arm64'): ('cloudflared-darwin-arm64.tgz',
                          '3586ccf397e98e5f437e60e7c307ca6ac463e110580503af8bb8e6f9ebf831bd'),
    ('darwin', 'amd64'): ('cloudflared-darwin-amd64.tgz',
                          '02f3edfd87684dd5b705607688e4eb4b323f6e777b81e7b3a85af556aa356848'),
    ('linux', 'amd64'): ('cloudflared-linux-amd64',
                         '1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872'),
    ('linux', 'arm64'): ('cloudflared-linux-arm64',
                         '3df43aedc9ff291d1735b5cb576cb97e0b157488796db2e32b28aecabb445775'),
}
MACHINES = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64', 'amd64': 'amd64'}
MAX_BYTES = 128 * 1024 * 1024
DOWNLOAD_SECONDS = 120
#: Quick Tunnel hostnames are hyphenated words; ``api.trycloudflare.com`` is never one.
ADDRESS = re.compile(r'https://[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com(?![\w.-])')
CONFIG = 'no-autoupdate: true\n'


def asset(system=None, machine=None):
    """``(file name, sha256)`` of the pinned build for this computer, or None."""
    system = sys.platform if system is None else system
    machine = MACHINES.get(str(platform.machine() if machine is None else machine).lower())
    return ASSETS.get((system, machine))


def available(find=None):
    """Whether a link can be opened: ngrok or cloudflared is here, or a pinned build fits."""
    from .subscription_engines import find_cli
    find = find or find_cli
    return bool(find('ngrok') or find('cloudflared') or asset())


def tools_dir(store):
    return Path(store.root) / 'tools'


def config_file(store):
    """Still's own cloudflared config: no ingress rules, no self-update."""
    path = tools_dir(store) / 'cloudflared-quick-tunnel.yml'
    if not path.is_file() or path.read_text(encoding='utf-8') != CONFIG:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(CONFIG, encoding='utf-8')
    return str(path)


def argv(binary, port, config):
    port = int(port)
    # ``--config`` is last: ``family_setup._own_tunnel`` reads the words before it.
    return [binary, 'tunnel', '--url', f'http://127.0.0.1:{port}', '--http-host-header', f'127.0.0.1:{port}',
            '--no-autoupdate', '--config', config]


def download(url, timeout=DOWNLOAD_SECONDS):
    """The bytes at ``url`` (HTTPS GitHub release), at most ``MAX_BYTES``."""
    request = urllib.request.Request(url, headers={'User-Agent': f'Still cloudflared-fetch/{VERSION}'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise RuntimeError('the cloudflared download was larger than expected; nothing was run.')
    return data


def _unpack(data):
    """The ``cloudflared`` regular file from a release archive; no other member is read."""
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive.getmembers():
            if member.name in ('cloudflared', './cloudflared') and member.isfile():
                return archive.extractfile(member).read()
    raise RuntimeError('the cloudflared archive has no cloudflared program; nothing was run.')


def ensure(store, fetch=download, *, system=None, machine=None):
    """The path of Still's own pinned cloudflared, downloading and verifying it once."""
    directory = tools_dir(store) / f'cloudflared-{VERSION}'
    target = directory / 'cloudflared'
    if target.is_file() and os.access(target, os.X_OK):
        return str(target)
    picked = asset(system, machine)
    if picked is None:
        raise RuntimeError('no cloudflared build is pinned for this computer; install ngrok or cloudflared.')
    name, digest = picked
    try:
        data = fetch(RELEASE + name)
    except OSError as exc:
        raise RuntimeError(f'cloudflared {VERSION} could not be downloaded ({type(exc).__name__}).') from None
    if hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError(f'checksum mismatch for cloudflared {VERSION}; nothing was run.')
    program = _unpack(data) if name.endswith('.tgz') else data
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, partial = tempfile.mkstemp(prefix='.cloudflared-', dir=directory)
    try:
        with os.fdopen(handle, 'wb') as out:
            out.write(program)
        os.chmod(partial, 0o700)
        os.replace(partial, target)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise
    return str(target)


def read_line(line):
    """``(https address or None, error text or '')`` from one line of cloudflared's log."""
    found = ADDRESS.search(line)
    if found:
        return found.group(0), ''
    if ' ERR ' in line or line.startswith('ERR '):
        return None, line.split(' ERR ', 1)[-1].strip()[:200]
    return None, ''
