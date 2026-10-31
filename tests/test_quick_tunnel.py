"""#523: a one-time phone link without an ngrok account, through a Cloudflare quick tunnel.

Model-free and network-free: an injected download, injected launcher and
locator, and fake processes that print what cloudflared 2026.10.7 prints
(its JSON log lines, observed once on the owner's Mac).  No live Cloudflare
or ngrok operation is observed here.
"""
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import family_setup, quick_tunnel
from personal_agent.quickstart_store import QuickStore

ADDRESS = 'https://charge-confirmed-claimed-angel.trycloudflare.com'
BOX = '|  ' + ADDRESS + '                                  |'


def cloudflared_lines(address=True, connected=True, error=None):
    lines = [{'level': 'info', 'message': 'Requesting new quick Tunnel on trycloudflare.com...'}]
    if error:
        lines.append({'level': 'error', 'message': error})
    if address:
        lines += [{'level': 'info', 'message': '+----+'}, {'level': 'info', 'message': BOX}]
    if connected:
        lines.append({'connIndex': 0, 'level': 'info', 'location': 'icn06', 'message': 'Registered tunnel connection'})
    return [json.dumps(line) + '\n' for line in lines]


class Launcher:
    """Records every spawn; each name prints its scripted lines and then ends."""

    def __init__(self, **outputs):
        self.outputs, self.spawned = outputs, []

    def __call__(self, argv, **kwargs):
        name = Path(argv[0]).name

        class Process:
            stdout = iter(self.outputs[name])
            terminated = False
            pid = 4000 + len(self.spawned)

            def terminate(inner):
                inner.terminated = True
        process = Process()
        self.spawned.append((name, argv, kwargs, process))
        return process


class TunnelCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = QuickStore(self.root / 'data')
        self.located = []

    def locate(self, **found):
        def locate(name):
            self.located.append(name)
            value = found.get(name)
            if isinstance(value, BaseException):
                raise value
            return value
        return locate


class StartTunnel(TunnelCase):
    def test_without_ngrok_a_quick_tunnel_opens_once_the_edge_registered_it(self):
        launcher = Launcher(cloudflared=cloudflared_lines())
        with mock.patch.dict(os.environ, {'TUNNEL_LOGFILE': '/tmp/requests.log', 'TUNNEL_LOGLEVEL': 'debug'}):
            process, url = family_setup.start_tunnel(8787, popen=launcher, store=self.store, timeout=5,
                                                     locate=self.locate(cloudflared='/opt/homebrew/bin/cloudflared'))
        self.assertEqual(url, ADDRESS)
        self.assertEqual(self.located, ['ngrok', 'cloudflared'])
        [(name, argv, kwargs, spawned)] = launcher.spawned
        self.assertIs(process, spawned)
        self.assertFalse(process.terminated)
        self.assertEqual(argv[:2], ['/opt/homebrew/bin/cloudflared', 'tunnel'])
        self.assertEqual(argv[argv.index('--url') + 1], 'http://127.0.0.1:8787')
        self.assertEqual(argv[argv.index('--http-host-header') + 1], 'localhost:8787')
        self.assertEqual(argv[argv.index('--loglevel') + 1], 'info', 'request URLs carry the one-time code')
        self.assertIn('--no-autoupdate', argv)
        config = Path(argv[argv.index('--config') + 1])
        self.assertEqual(config.parent, self.store.root / 'tools')
        self.assertEqual(config.read_text(), '{}\n', "the owner's own cloudflared config is never read")
        self.assertFalse([key for key in kwargs['env'] if key.startswith('TUNNEL_')])

    def test_the_link_is_handed_out_only_after_the_new_hostname_had_time_to_resolve(self):
        waited = []
        launcher = Launcher(cloudflared=cloudflared_lines())
        with mock.patch.object(quick_tunnel, 'SETTLE_SECONDS', 6), mock.patch.object(family_setup.time, 'sleep', waited.append):
            family_setup.start_tunnel(8787, popen=launcher, store=self.store, timeout=5,
                                      locate=self.locate(cloudflared='cloudflared'))
        self.assertEqual(waited, [6])

    def test_the_address_alone_is_not_enough_while_the_connection_is_still_coming(self):
        lines = cloudflared_lines(connected=False) + [json.dumps({'level': 'info', 'message': 'Registered tunnel connection'}) + '\n']
        launcher = Launcher(cloudflared=lines)
        _process, url = family_setup.start_tunnel(8787, popen=launcher, store=self.store, timeout=5,
                                                  locate=self.locate(cloudflared='cloudflared'))
        self.assertEqual(url, ADDRESS)

    def test_a_quick_tunnel_that_ends_without_an_address_is_reported_and_stopped(self):
        launcher = Launcher(cloudflared=cloudflared_lines(address=False, connected=False, error='failed to request quick Tunnel'))
        with self.assertRaises(RuntimeError) as caught:
            family_setup.start_tunnel(8787, popen=launcher, store=self.store, timeout=5,
                                      locate=self.locate(cloudflared='cloudflared'))
        self.assertIn('cloudflared did not report', str(caught.exception))
        self.assertIn('failed to request quick Tunnel', str(caught.exception))
        self.assertTrue(launcher.spawned[0][3].terminated)

    def test_at_the_deadline_an_address_without_a_recognised_connection_line_stands(self):
        """An installed cloudflared of another version may word its connection line differently."""
        class Silent:
            """Prints the address, then stays up without another line."""
            terminated = False

            def __init__(self):
                import threading
                self.release = threading.Event()
                self.stdout = self.lines()

            def lines(self):
                yield from cloudflared_lines(connected=False)
                self.release.wait(5)

            def terminate(self):
                self.terminated = True
                self.release.set()
        process = Silent()
        self.addCleanup(process.release.set)
        got, url = family_setup.start_tunnel(8787, popen=lambda argv, **kw: process, store=self.store, timeout=0.3,
                                             locate=self.locate(cloudflared='cloudflared'))
        self.assertEqual((got, url, process.terminated), (process, ADDRESS, False))

    def test_ngrok_stays_first_when_it_opens(self):
        launcher = Launcher(ngrok=[json.dumps({'msg': 'started tunnel', 'url': 'https://abc123.ngrok-free.app'}) + '\n'])
        _process, url = family_setup.start_tunnel(8787, popen=launcher, store=self.store, timeout=5,
                                                  locate=self.locate(ngrok='/opt/homebrew/bin/ngrok', cloudflared='cloudflared'))
        self.assertEqual(url, 'https://abc123.ngrok-free.app')
        self.assertEqual(self.located, ['ngrok'], 'cloudflared is neither sought nor downloaded')
        self.assertEqual([name for name, *_ in launcher.spawned], ['ngrok'])

    def test_an_ngrok_without_an_account_token_falls_back_to_a_quick_tunnel(self):
        ngrok_error = json.dumps({'lvl': 'eror', 'err': 'authentication failed: ERR_NGROK_4018'}) + '\n'
        launcher = Launcher(ngrok=[ngrok_error], cloudflared=cloudflared_lines())
        _process, url = family_setup.start_tunnel(8787, popen=launcher, store=self.store, timeout=5,
                                                  locate=self.locate(ngrok='ngrok', cloudflared='cloudflared'))
        self.assertEqual(url, ADDRESS)
        self.assertEqual([name for name, *_ in launcher.spawned], ['ngrok', 'cloudflared'])
        self.assertTrue(launcher.spawned[0][3].terminated, 'the failed ngrok is stopped')

    def test_when_neither_opens_the_first_reason_is_kept(self):
        ngrok_error = json.dumps({'lvl': 'eror', 'err': 'authentication failed'}) + '\n'
        launcher = Launcher(ngrok=[ngrok_error])
        broken = RuntimeError('the cloudflared 2026.10.7 download did not match its pinned SHA-256; nothing was run.')
        with self.assertRaises(RuntimeError) as caught:
            family_setup.start_tunnel(8787, popen=launcher, store=self.store, timeout=5,
                                      locate=self.locate(ngrok='ngrok', cloudflared=broken))
        self.assertIn('authentication failed', str(caught.exception))
        with self.assertRaises(RuntimeError) as caught:
            family_setup.start_tunnel(8787, popen=launcher, store=self.store, timeout=5, locate=self.locate(cloudflared=broken))
        self.assertIs(caught.exception, broken)
        self.assertEqual([name for name, *_ in launcher.spawned], ['ngrok'], 'an unverified cloudflared is never run')

    def test_no_tool_at_all_is_a_missing_file_for_the_owner_facing_text(self):
        with self.assertRaises(FileNotFoundError):
            family_setup.start_tunnel(8787, popen=Launcher(), store=self.store, timeout=5, locate=self.locate())

    def test_the_real_locator_prefers_an_installed_cloudflared_and_else_the_pinned_download(self):
        with mock.patch('personal_agent.subscription_engines.find_cli', return_value='/opt/homebrew/bin/cloudflared'), \
                mock.patch.object(quick_tunnel, 'ensure', side_effect=AssertionError('downloaded')):
            self.assertEqual(family_setup._locator(self.store)('cloudflared'), '/opt/homebrew/bin/cloudflared')
        with mock.patch('personal_agent.subscription_engines.find_cli', return_value=None), \
                mock.patch.object(quick_tunnel, 'ensure', return_value='/pinned/cloudflared') as ensure:
            self.assertEqual(family_setup._locator(self.store)('cloudflared'), '/pinned/cloudflared')
            self.assertIsNone(family_setup._locator(self.store)('ngrok'), 'ngrok is never downloaded')
        ensure.assert_called_once_with(self.store.root / 'tools')

    def test_a_link_can_be_tried_with_either_tool_or_a_pin(self):
        with mock.patch.object(quick_tunnel, 'pin', return_value=None):
            self.assertFalse(family_setup.tunnel_available(find=lambda name: None))
            self.assertTrue(family_setup.tunnel_available(find={'cloudflared': '/x/cloudflared'}.get))
            self.assertTrue(family_setup.tunnel_available(find={'ngrok': '/x/ngrok'}.get))
        with mock.patch.object(quick_tunnel, 'pin', return_value=('a', 'b', 'c')):
            self.assertTrue(family_setup.tunnel_available(find=lambda name: None))


def release(binary=b'\x7fELF cloudflared', tgz=True):
    """A fake release asset and the pin that matches it."""
    if tgz:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            info = tarfile.TarInfo('cloudflared')
            info.size, info.mode = len(binary), 0o755
            archive.addfile(info, io.BytesIO(binary))
        data, asset = buffer.getvalue(), 'cloudflared-darwin-arm64.tgz'
    else:
        data, asset = binary, 'cloudflared-linux-amd64'
    return data, (asset, hashlib.sha256(data).hexdigest(), hashlib.sha256(binary).hexdigest())


class PinnedDownload(TunnelCase):
    def fetch(self, data):
        self.fetched = []

        def fetch(url):
            self.fetched.append(url)
            return data
        return fetch

    def test_the_pinned_release_is_downloaded_once_checked_and_reused(self):
        data, chosen = release()
        tools = self.root / 'tools'
        path = Path(quick_tunnel.ensure(tools, fetch=self.fetch(data), chosen=chosen))
        self.assertEqual(path, tools / f'cloudflared-{quick_tunnel.VERSION}' / 'cloudflared')
        self.assertEqual(path.read_bytes(), b'\x7fELF cloudflared')
        self.assertTrue(os.access(path, os.X_OK))
        self.assertEqual(self.fetched, [f'{quick_tunnel.RELEASES}/{quick_tunnel.VERSION}/cloudflared-darwin-arm64.tgz'])
        self.assertEqual(quick_tunnel.ensure(tools, fetch=self.fetch(b'unused'), chosen=chosen), str(path))
        self.assertEqual(self.fetched, [], 'a verified binary is not downloaded again')
        path.write_bytes(b'changed')
        self.assertEqual(quick_tunnel.ensure(tools, fetch=self.fetch(data), chosen=chosen), str(path))
        self.assertEqual((len(self.fetched), path.read_bytes()), (1, b'\x7fELF cloudflared'), 'a changed binary is replaced')

    def test_a_download_that_does_not_match_its_pins_is_never_written_or_run(self):
        data, (asset, asset_sha, binary_sha) = release()
        tools = self.root / 'tools'
        for chosen, tampered in (((asset, asset_sha, binary_sha), data + b'x'),          # the archive changed
                                 ((asset, hashlib.sha256(data).hexdigest(), '0' * 64), data)):  # the binary inside
            with self.subTest(chosen=chosen[1:]):
                with self.assertRaises(RuntimeError) as caught:
                    quick_tunnel.ensure(tools, fetch=self.fetch(tampered), chosen=chosen)
                self.assertIn('nothing was run', str(caught.exception))
                self.assertFalse(list(tools.rglob('*cloudflared*')))

    def test_a_raw_linux_binary_is_written_as_is(self):
        data, chosen = release(b'linux binary', tgz=False)
        path = quick_tunnel.ensure(self.root / 'tools', fetch=self.fetch(data), chosen=chosen)
        self.assertEqual(Path(path).read_bytes(), b'linux binary')

    def test_a_platform_without_a_pin_downloads_nothing(self):
        with mock.patch.object(quick_tunnel, 'pin', return_value=None), self.assertRaises(FileNotFoundError):
            quick_tunnel.ensure(self.root / 'tools', fetch=self.fetch(b'x'))
        self.assertEqual(self.fetched, [])

    def test_an_archive_entry_is_read_in_memory_and_never_extracted_by_its_path(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            for name in ('../cloudflared', 'bin/cloudflared'):
                info = tarfile.TarInfo(name)
                info.size = 1
                archive.addfile(info, io.BytesIO(b'x'))
        with self.assertRaises(RuntimeError):
            quick_tunnel._unpack(buffer.getvalue())
        self.assertFalse((self.root.parent / 'cloudflared').exists())

    def test_tests_never_download(self):
        with self.assertRaises(OSError):
            quick_tunnel._fetch('https://github.com/cloudflare/cloudflared/releases')

    def test_the_pins_cover_the_supported_platforms(self):
        self.assertEqual(quick_tunnel.pin('Darwin', 'arm64')[0], 'cloudflared-darwin-arm64.tgz')
        self.assertEqual(quick_tunnel.pin('Darwin', 'x86_64')[0], 'cloudflared-darwin-amd64.tgz')
        self.assertEqual(quick_tunnel.pin('Linux', 'aarch64')[0], 'cloudflared-linux-arm64')
        self.assertEqual(quick_tunnel.pin('Linux', 'AMD64')[0], 'cloudflared-linux-amd64')
        self.assertIsNone(quick_tunnel.pin('Windows', 'AMD64'))
        for asset, asset_sha, binary_sha in quick_tunnel.PINS.values():
            self.assertRegex(asset_sha + binary_sha, r'\A[0-9a-f]{128}\Z')
            self.assertEqual(asset_sha == binary_sha, not asset.endswith('.tgz'))


class OwnTunnels(unittest.TestCase):
    def test_a_quick_tunnel_left_by_a_killed_server_is_reclaimed_and_nothing_else(self):
        import signal
        table = ('  201     1 /Users/o/.local/share/agentos/tools/cloudflared-2026.10.7/cloudflared tunnel --config '
                 '/Users/o/.local/share/agentos/tools/cloudflared-quick-tunnel.yml --no-autoupdate --url http://127.0.0.1:8787\n'
                 '  202     1 /Users/o/My Data/tools/cloudflared-2026.10.7/cloudflared tunnel --url http://127.0.0.1:8787\n'
                 '  203     1 /opt/homebrew/bin/cloudflared tunnel run my-named-tunnel\n'
                 '  204     1 /opt/homebrew/bin/cloudflared tunnel --url http://127.0.0.1:8797\n'
                 '  205   555 /opt/homebrew/bin/cloudflared tunnel --url http://127.0.0.1:8787\n'
                 '  206     1 /usr/bin/python3 cloudflared --url http://127.0.0.1:8787\n'
                 '  207     1 /usr/bin/python3 cloudflared tunnel --url http://127.0.0.1:8787\n')
        killed = []
        run = lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, table, '')
        family_setup.reclaim_orphan_tunnels(8787, run=run, kill=lambda pid, sig: killed.append((pid, sig)))
        self.assertEqual(killed, [(201, signal.SIGTERM), (202, signal.SIGTERM)])


if __name__ == '__main__':
    unittest.main()
