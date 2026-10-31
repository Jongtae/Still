"""Cloudflare Quick Tunnel: a one-time phone link without an ngrok account (#523).

Adopted, not built: ``cloudflared tunnel --url http://127.0.0.1:<port>`` is
Cloudflare's own way to put one local port behind a random
``https://*.trycloudflare.com`` address, with no Cloudflare account or domain
(https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
Its stated limits (testing use, no uptime guarantee, 200 in-flight requests,
no Server-Sent Events, a new hostname each time) fit Still's links: each is
one-time, short-lived and uses no SSE.  ``family_setup.start_tunnel`` stays
the one place a tunnel starts; this module only finds the binary and builds
its command.

The binary is obtained the way the installer obtains uv
(``scripts/install.sh``): one already installed (Homebrew) is used as is,
with the same trust as any other tool the owner installed; otherwise the
pinned release below is downloaded once into Still's own tool directory and
run only after both pinned SHA-256 digests match, and its digest is checked
again before each start.  cloudflared is Apache-2.0 (``LICENSE`` at tag
``2026.10.7``).
"""
import hashlib
import io
import os
import platform
import re
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = '2026.10.7'
RELEASES = 'https://github.com/cloudflare/cloudflared/releases/download'
#: ``(system, machine) -> (asset, asset SHA-256, binary SHA-256)``.  The asset digest is the one
#: GitHub publishes for the release asset; the binary digest is the one Cloudflare's release notes
#: list (for a ``.tgz`` it is the ``cloudflared`` inside).  Both were checked against a download.
PINS = {
    ('Darwin', 'arm64'): ('cloudflared-darwin-arm64.tgz',
                          '3586ccf397e98e5f437e60e7c307ca6ac463e110580503af8bb8e6f9ebf831bd',
                          '2136431d5eb467bf447d2ee514b713e192719b143aec1d9e09bf7dc290d5bb12'),
    ('Darwin', 'x86_64'): ('cloudflared-darwin-amd64.tgz',
                           '02f3edfd87684dd5b705607688e4eb4b323f6e777b81e7b3a85af556aa356848',
                           '5c55e77da7012cebca19170357e196176c48c9647cc2ead404702fa125ddbd86'),
    ('Linux', 'x86_64'): ('cloudflared-linux-amd64',
                          '1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872',
                          '1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872'),
    ('Linux', 'arm64'): ('cloudflared-linux-arm64',
                         '3df43aedc9ff291d1735b5cb576cb97e0b157488796db2e32b28aecabb445775',
                         '3df43aedc9ff291d1735b5cb576cb97e0b157488796db2e32b28aecabb445775'),
}
#: The release binaries are about 40 MB; anything far larger is not the pinned file.
MAX_BYTES = 96 * 1024 * 1024
DOWNLOAD_SECONDS = 120
#: Where cloudflared prints the quick tunnel's address.
ADDRESS = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com')
#: The log line that says the edge accepted a connection.
CONNECTED = 'Registered tunnel connection'
#: The new hostname resolves about 3 seconds after that line (observed 2026-10-31), and a resolver
#: that asked earlier keeps its "no such host" for the zone's 60-second negative TTL; so the link is
#: handed out only after this wait, before anyone can open it.
SETTLE_SECONDS = 6


def pin(system=None, machine=None):
    """This platform's pin, or None where Cloudflare publishes no matching binary."""
    machine = str(machine or platform.machine()).lower()
    machine = {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(machine, machine)
    return PINS.get((system or platform.system(), machine))


def tool_dir(store):
    """Still's own tool directory beside the owner store."""
    return Path(store.root) / 'tools'


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
        raise RuntimeError('the cloudflared download was larger than expected; nothing was run.')
    return data


def _unpack(data):
    """The ``cloudflared`` file inside a release ``.tgz``, read in memory and never extracted by its path."""
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive:
            if member.isfile() and member.name in ('cloudflared', './cloudflared') and member.size <= MAX_BYTES:
                return archive.extractfile(member).read()
    raise RuntimeError('the cloudflared download did not contain cloudflared; nothing was run.')


def ensure(directory, *, fetch=None, chosen=None):
    """The pinned cloudflared in ``directory``, downloaded and checked first when needed.

    Raises ``FileNotFoundError`` on a platform without a pin and ``RuntimeError`` (nothing
    written or run) when a download does not match its pinned digests.
    """
    chosen = chosen or pin()
    if chosen is None:
        raise FileNotFoundError(2, 'No such file or directory', 'cloudflared')
    asset, asset_sha, binary_sha = chosen
    target = Path(directory) / f'cloudflared-{VERSION}' / 'cloudflared'
    if target.is_file() and _sha256(target) == binary_sha:
        return str(target)
    data = (fetch or _fetch)(f'{RELEASES}/{VERSION}/{asset}')
    if hashlib.sha256(data).hexdigest() != asset_sha:
        raise RuntimeError(f'the cloudflared {VERSION} download did not match its pinned SHA-256; nothing was run.')
    binary = _unpack(data) if asset.endswith('.tgz') else data
    if hashlib.sha256(binary).hexdigest() != binary_sha:
        raise RuntimeError(f'the cloudflared {VERSION} binary did not match its pinned SHA-256; nothing was run.')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix='.cloudflared-')
    try:
        with os.fdopen(handle, 'wb') as out:
            out.write(binary)
        os.chmod(temporary, 0o755)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return str(target)


def config_file(directory):
    """An empty configuration, so an owner's own ``~/.cloudflared/config.yml`` (a named tunnel,
    its credentials) is never read into a quick tunnel."""
    path = Path(directory) / 'cloudflared-quick-tunnel.yml'
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text('{}\n')
    return str(path)


def command(binary, port, config):
    """The quick tunnel for one loopback port.

    ``--no-autoupdate`` keeps the pinned binary unchanged; ``--loglevel info`` keeps request
    URLs (which carry the one-time code) out of its output; ``--http-host-header`` makes the
    instance see ``localhost:<port>`` as with ngrok's ``--host-header=rewrite``, while the
    ``Cf-*`` and ``X-Forwarded-*`` headers still mark every request as relayed (#594).
    """
    port = int(port)
    return [binary, 'tunnel', '--config', str(config), '--no-autoupdate', '--loglevel', 'info', '--output', 'json',
            '--url', f'http://127.0.0.1:{port}', '--http-host-header', f'localhost:{port}']


def environment(environ=None):
    """The environment without ``TUNNEL_*`` settings, which could add a log file or change the origin."""
    environ = os.environ if environ is None else environ
    return {key: value for key, value in environ.items() if not key.upper().startswith('TUNNEL_')}


def is_own(words, port):
    """Whether the split command line ``words`` is the quick tunnel Still starts for ``port``.

    ``ps`` joins argv with spaces, so a tool directory with a space splits the binary's path:
    the binary is the first word named ``cloudflared`` followed by ``tunnel``, and a later word
    is still a path (``python3 cloudflared tunnel`` is not this tunnel).
    """
    start = next((i for i in range(len(words) - 1) if Path(words[i]).name == 'cloudflared' and words[i + 1] == 'tunnel'), None)
    if start is None or start and '/' not in words[start]:
        return False
    rest = words[start + 2:]
    return any(rest[i] == '--url' and rest[i + 1] == f'http://127.0.0.1:{port}' for i in range(len(rest) - 1))
