"""#523: the one-time phone link opens without an ngrok account (Cloudflare Quick Tunnel).

Model-free and network-free: fake tunnel processes, an injected program
lookup and an injected download.  No real ngrok, cloudflared or Cloudflare
edge is reached here; a live Quick Tunnel is not observed by these tests.
"""
import hashlib
import http.client
import io
import os
import pathlib
import tarfile
import tempfile
import unittest
from unittest import mock

from personal_agent import family_setup, quick_tunnel, work_mode
from personal_agent.quickstart import relayed_request
from personal_agent.quickstart_store import QuickStore

PORT = 8787
NGROK_LINE = '{"lvl":"info","msg":"started tunnel","url":"https://abc123.ngrok-free.app"}\n'
NGROK_NO_TOKEN = '{"lvl":"eror","err":"authentication failed: Usage of ngrok requires a verified account and authtoken"}\n'
#: The startup lines cloudflared 2026.10.7 logs (``quickTunnelStartupLines`` in cmd/cloudflared/tunnel/quick_tunnel.go).
CLOUDFLARED_LINES = (
    '2026-10-31T01:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...\n',
    '2026-10-31T01:00:01Z INF +--------------------------------------------------------------------------------------------+\n',
    '2026-10-31T01:00:01Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |\n',
    '2026-10-31T01:00:01Z INF |  https://quiet-river-bright-lamp.trycloudflare.com                                         |\n',
)
QUICK_URL = 'https://quiet-river-bright-lamp.trycloudflare.com'


class Process:
    def __init__(self, argv, lines):
        self.argv, self.stdout, self.terminated, self.pid = argv, iter(lines), False, 4321

    def terminate(self):
        self.terminated = True


class Popen:
    """Answers each program by its basename: ngrok and cloudflared lines, or missing."""
    def __init__(self, outputs):
        self.outputs, self.calls = outputs, []

    def __call__(self, argv, **kwargs):
        name = pathlib.Path(argv[0]).name
        self.calls.append(argv)
        if name not in self.outputs:
            raise FileNotFoundError(2, 'No such file or directory', name)
        process = Process(argv, self.outputs[name])
        return process


def finder(*present):
    return lambda command: f'/opt/homebrew/bin/{command}' if command in present else None


def archive(*members):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


class Case(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(pathlib.Path(temp.name) / 'data')
        self.fetched = []

    def no_fetch(self, url):
        raise AssertionError(f'nothing is downloaded here: {url}')

    def start(self, popen, find, fetch=None, timeout=5):
        return family_setup.start_tunnel(PORT, popen=popen, timeout=timeout, store=self.store, find=find,
                                         fetch=fetch or self.no_fetch)


class WhichTunnel(Case):
    def test_a_configured_ngrok_is_used_unchanged(self):
        popen = Popen({'ngrok': [NGROK_LINE], 'cloudflared': list(CLOUDFLARED_LINES)})
        process, url = self.start(popen, finder('ngrok', 'cloudflared'))
        self.assertEqual(url, 'https://abc123.ngrok-free.app')
        self.assertEqual(popen.calls, [['/opt/homebrew/bin/ngrok', 'http', f'127.0.0.1:{PORT}', '--host-header=rewrite',
                                        '--inspect=false', '--log', 'stdout', '--log-format', 'json']])
        self.assertFalse(process.terminated)

    def test_without_ngrok_an_installed_cloudflared_opens_a_quick_tunnel(self):
        popen = Popen({'cloudflared': list(CLOUDFLARED_LINES)})
        process, url = self.start(popen, finder('cloudflared'))
        self.assertEqual(url, QUICK_URL)
        config = str(quick_tunnel.tools_dir(self.store) / 'cloudflared-quick-tunnel.yml')
        self.assertEqual(popen.calls, [['/opt/homebrew/bin/cloudflared', 'tunnel', '--url', f'http://127.0.0.1:{PORT}',
                                        '--http-host-header', f'127.0.0.1:{PORT}', '--no-autoupdate', '--config', config]])
        # Still's own config: an owner's ~/.cloudflared/config.yml ingress never applies here.
        self.assertEqual(pathlib.Path(config).read_text(encoding='utf-8'), 'no-autoupdate: true\n')
        self.assertFalse(process.terminated)

    def test_an_ngrok_without_an_authtoken_falls_back_to_a_quick_tunnel(self):
        popen = Popen({'ngrok': [NGROK_NO_TOKEN], 'cloudflared': list(CLOUDFLARED_LINES)})
        _process, url = self.start(popen, finder('ngrok', 'cloudflared'))
        self.assertEqual(url, QUICK_URL)
        self.assertEqual([pathlib.Path(argv[0]).name for argv in popen.calls], ['ngrok', 'cloudflared'])

    def test_when_both_fail_both_reasons_are_given_and_both_processes_end(self):
        popen = Popen({'ngrok': [NGROK_NO_TOKEN],
                       'cloudflared': ['2026-10-31T01:00:00Z ERR failed to request quick Tunnel: 429 Too Many Requests\n']})
        processes = []
        with mock.patch.object(Process, 'terminate', lambda self: processes.append(self.argv[0])):
            with self.assertRaises(RuntimeError) as caught:
                self.start(popen, finder('ngrok', 'cloudflared'))
        self.assertIn('authtoken', str(caught.exception))
        self.assertIn('429 Too Many Requests', str(caught.exception))
        self.assertEqual(processes, ['/opt/homebrew/bin/ngrok', '/opt/homebrew/bin/cloudflared'])

    def test_a_quick_tunnel_that_reports_no_address_in_time_is_terminated(self):
        class Silent(Process):
            def __init__(self, argv):
                super().__init__(argv, [])
                self.stdout = self

            def __iter__(self):
                return self

            def __next__(self):
                import time
                time.sleep(0.05)
                if self.terminated:
                    raise StopIteration
                return '2026-10-31T01:00:00Z INF waiting\n'
        spawned = []
        with self.assertRaises(RuntimeError) as caught:
            self.start(lambda argv, **kwargs: spawned.append(Silent(argv)) or spawned[-1], finder('cloudflared'), timeout=0.3)
        self.assertIn('cloudflared did not report', str(caught.exception))
        self.assertTrue(spawned[0].terminated)

    def test_the_address_is_the_quick_tunnel_hostname_only(self):
        for line, expected in (
                ('INF Requesting new quick Tunnel on trycloudflare.com...', None),
                ('INF quick-service https://api.trycloudflare.com', None),
                ('INF |  https://evil.example/https://a-b.trycloudflare.com.evil.example  |', None),
                ('INF |  https://a-b-c.trycloudflare.com   |', 'https://a-b-c.trycloudflare.com')):
            with self.subTest(line=line):
                self.assertEqual(quick_tunnel.read_line(line)[0], expected)
        self.assertEqual(quick_tunnel.read_line('2026-10-31T01:00:00Z ERR failed to request quick Tunnel'),
                         (None, 'failed to request quick Tunnel'))


class PinnedDownload(Case):
    def pin(self, data, name='cloudflared-darwin-arm64.tgz'):
        digest = hashlib.sha256(data).hexdigest()
        return mock.patch.dict(quick_tunnel.ASSETS, {('darwin', 'arm64'): (name, digest)}, clear=True)

    def fetch_returning(self, data):
        def fetch(url):
            self.fetched.append(url)
            return data
        return fetch

    def test_without_any_cloudflared_the_pinned_release_is_verified_kept_and_reused(self):
        data = archive(('cloudflared', b'#!/bin/sh\necho pinned\n'))
        popen = Popen({'cloudflared': list(CLOUDFLARED_LINES)})
        with self.pin(data), mock.patch.object(quick_tunnel.sys, 'platform', 'darwin'), \
                mock.patch.object(quick_tunnel.platform, 'machine', return_value='arm64'):
            _process, url = self.start(popen, finder(), fetch=self.fetch_returning(data))
            again = self.start(popen, finder(), fetch=self.no_fetch)[1]
        binary = quick_tunnel.tools_dir(self.store) / f'cloudflared-{quick_tunnel.VERSION}' / 'cloudflared'
        self.assertEqual((url, again), (QUICK_URL, QUICK_URL))
        self.assertEqual(self.fetched, [quick_tunnel.RELEASE + 'cloudflared-darwin-arm64.tgz'])
        self.assertTrue(self.fetched[0].startswith('https://github.com/cloudflare/cloudflared/releases/download/2026.10.7/'))
        self.assertEqual([argv[0] for argv in popen.calls], [str(binary), str(binary)])
        self.assertEqual(binary.read_bytes(), b'#!/bin/sh\necho pinned\n')
        self.assertTrue(os.access(binary, os.X_OK))
        self.assertEqual(sorted(path.name for path in binary.parent.iterdir()), ['cloudflared'], 'no partial file is left')

    def test_a_raw_linux_binary_is_verified_and_kept_as_is(self):
        data = b'\x7fELF pinned'
        with self.pin(data, 'cloudflared-linux-amd64'):
            path = quick_tunnel.ensure(self.store, self.fetch_returning(data), system='darwin', machine='arm64')
        self.assertEqual(pathlib.Path(path).read_bytes(), data)

    def test_a_checksum_mismatch_is_refused_and_nothing_is_run_or_kept(self):
        data = archive(('cloudflared', b'pinned'))
        popen = Popen({'cloudflared': list(CLOUDFLARED_LINES)})
        with self.pin(data), mock.patch.object(quick_tunnel.sys, 'platform', 'darwin'), \
                mock.patch.object(quick_tunnel.platform, 'machine', return_value='arm64'):
            with self.assertRaises(RuntimeError) as caught:
                self.start(popen, finder(), fetch=self.fetch_returning(archive(('cloudflared', b'tampered'))))
        self.assertIn('checksum mismatch', str(caught.exception))
        self.assertEqual(popen.calls, [], 'an unverified program is never run')
        self.assertFalse((quick_tunnel.tools_dir(self.store) / f'cloudflared-{quick_tunnel.VERSION}').exists())

    def test_an_archive_without_the_program_is_refused_and_no_other_member_is_written(self):
        data = archive(('../../escape', b'x'), ('cloudflared/cloudflared', b'x'))
        with self.pin(data), self.assertRaises(RuntimeError) as caught:
            quick_tunnel.ensure(self.store, self.fetch_returning(data), system='darwin', machine='arm64')
        self.assertIn('no cloudflared program', str(caught.exception))
        self.assertFalse((quick_tunnel.tools_dir(self.store) / f'cloudflared-{quick_tunnel.VERSION}').exists())
        self.assertFalse((quick_tunnel.tools_dir(self.store).parent.parent / 'escape').exists())

    def test_a_failed_download_names_the_version_and_runs_nothing(self):
        def offline(url):
            raise OSError('offline')
        popen = Popen({'cloudflared': list(CLOUDFLARED_LINES)})
        with mock.patch.object(quick_tunnel.sys, 'platform', 'darwin'), \
                mock.patch.object(quick_tunnel.platform, 'machine', return_value='arm64'):
            with self.assertRaises(RuntimeError) as caught:
                self.start(popen, finder(), fetch=offline)
        self.assertIn(f'cloudflared {quick_tunnel.VERSION} could not be downloaded', str(caught.exception))
        self.assertEqual(popen.calls, [])

    def test_the_pins_cover_both_macs_and_name_exact_release_assets(self):
        self.assertEqual(quick_tunnel.asset('darwin', 'arm64')[0], 'cloudflared-darwin-arm64.tgz')
        self.assertEqual(quick_tunnel.asset('darwin', 'x86_64')[0], 'cloudflared-darwin-amd64.tgz')
        self.assertEqual(quick_tunnel.asset('linux', 'aarch64')[0], 'cloudflared-linux-arm64')
        self.assertIsNone(quick_tunnel.asset('win32', 'amd64'))
        for name, digest in quick_tunnel.ASSETS.values():
            self.assertRegex(digest, r'\A[0-9a-f]{64}\Z', name)
        self.assertNotIn('latest', quick_tunnel.RELEASE)

    def test_available_only_when_a_tool_or_a_pinned_build_exists(self):
        with mock.patch.object(quick_tunnel, 'asset', return_value=None):
            self.assertFalse(quick_tunnel.available(finder()))
            self.assertTrue(quick_tunnel.available(finder('ngrok')))
            self.assertTrue(quick_tunnel.available(finder('cloudflared')))
        with mock.patch.object(quick_tunnel, 'asset', return_value=('cloudflared-linux-amd64', '0' * 64)):
            self.assertTrue(quick_tunnel.available(finder()))


class WorkModeRefusesBoth(Case):
    def on(self):
        work_mode.set_enabled(self.store, True, surface='settings')

    def test_work_mode_refuses_before_any_lookup_download_or_spawn(self):
        self.on()
        looked = []
        popen = Popen({'ngrok': [NGROK_LINE], 'cloudflared': list(CLOUDFLARED_LINES)})
        for find in (finder('ngrok', 'cloudflared'), finder()):
            with self.subTest(find=find), self.assertRaises(work_mode.WorkModeRefused):
                self.start(popen, lambda command: looked.append(command) or find(command))
        self.assertEqual((popen.calls, looked, self.fetched), ([], [], []))
        self.assertFalse(quick_tunnel.tools_dir(self.store).exists())

    def test_switched_on_while_ngrok_failed_no_quick_tunnel_follows(self):
        outer = self

        class Refusing(Popen):
            def __call__(self, argv, **kwargs):
                process = super().__call__(argv, **kwargs)
                outer.on()  # the owner switches work mode on while ngrok is failing
                return process
        popen = Refusing({'ngrok': [NGROK_NO_TOKEN], 'cloudflared': list(CLOUDFLARED_LINES)})
        with self.assertRaises(work_mode.WorkModeRefused):
            self.start(popen, finder('ngrok', 'cloudflared'))
        self.assertEqual([pathlib.Path(argv[0]).name for argv in popen.calls], ['ngrok'])

    def test_switched_on_during_the_download_the_downloaded_cloudflared_is_not_started(self):
        """Codex P1 on #531: a first download takes a while; the switch may land meanwhile."""
        data = archive(('cloudflared', b'pinned'))

        def fetch(url):
            self.on()  # the owner switches work mode on while cloudflared downloads
            return data
        popen = Popen({'cloudflared': list(CLOUDFLARED_LINES)})
        digest = hashlib.sha256(data).hexdigest()
        with mock.patch.dict(quick_tunnel.ASSETS, {('darwin', 'arm64'): ('cloudflared-darwin-arm64.tgz', digest)}, clear=True), \
                mock.patch.object(quick_tunnel.sys, 'platform', 'darwin'), \
                mock.patch.object(quick_tunnel.platform, 'machine', return_value='arm64'):
            with self.assertRaises(work_mode.WorkModeRefused):
                self.start(popen, finder(), fetch=fetch)
        self.assertEqual(popen.calls, [], 'no tunnel process is started after the switch')


class OwnTunnelProcess(unittest.TestCase):
    """#929, #1445: only Still's own tunnel to this port is ever signalled, cloudflared's included."""

    def test_the_cloudflared_command_line_is_recognised_even_under_a_path_with_spaces(self):
        config = '/Users/o/Library/Application Support/Still/tools/cloudflared-quick-tunnel.yml'
        own = ' '.join(quick_tunnel.argv('/Users/o/Library/Application Support/Still/tools/cloudflared-2026.10.7/cloudflared',
                                         PORT, config))
        self.assertTrue(family_setup._own_tunnel(own, PORT))
        self.assertTrue(family_setup._own_tunnel(' '.join(quick_tunnel.argv('cloudflared', PORT, config)), PORT))
        for other in (own.replace(f'127.0.0.1:{PORT}', '127.0.0.1:9999'),
                      ' '.join(quick_tunnel.argv('/usr/bin/cloudflared', 87870, config)),
                      f'/usr/bin/cloudflared tunnel run my-named-tunnel --url http://127.0.0.1:{PORT}',
                      f'/usr/bin/python3 notcloudflared tunnel --url http://127.0.0.1:{PORT}'):
            with self.subTest(other=other):
                self.assertFalse(family_setup._own_tunnel(other, PORT))

    def test_an_orphaned_quick_tunnel_is_reclaimed_and_an_owned_one_is_not(self):
        command = ' '.join(quick_tunnel.argv('/opt/homebrew/bin/cloudflared', PORT, '/tmp/c.yml'))
        table = f'  201     1 {command}\n  202   555 {command}\n'
        killed = []
        ended = family_setup.reclaim_orphan_tunnels(
            PORT, run=lambda *a, **k: mock.Mock(returncode=0, stdout=table), kill=lambda pid, sig: killed.append(pid))
        self.assertEqual((ended, killed), (1, [201]))


class CloudflareRelayedIsNeverLocal(unittest.TestCase):
    """#594: what Cloudflare adds on the way to the loopback port marks the request relayed."""

    #: Headers a Quick Tunnel request carries at the origin (Cloudflare edge + cloudflared).
    CLOUDFLARE_HEADERS = (('CF-Connecting-IP', '198.51.100.7'), ('CF-IPCountry', 'KR'), ('CF-Ray', '8a1b2c3d4e5f-ICN'),
                          ('CF-Visitor', '{"scheme":"https"}'), ('Cf-Warp-Tag-Id', '0f1e2d3c'),
                          ('CDN-Loop', 'cloudflare'), ('X-Forwarded-For', '198.51.100.7'), ('X-Forwarded-Proto', 'https'))

    def parse(self, pairs):
        raw = ''.join(f'{name}: {value}\r\n' for name, value in pairs) + '\r\n'
        return http.client.parse_headers(io.BytesIO(raw.encode()))

    def test_each_cloudflare_header_alone_marks_the_request_relayed(self):
        host = ('Host', f'127.0.0.1:{PORT}')  # what --http-host-header makes the instance see
        self.assertFalse(relayed_request(self.parse([host])))
        for header in self.CLOUDFLARE_HEADERS:
            with self.subTest(header=header[0]):
                self.assertTrue(relayed_request(self.parse([host, header])))
        self.assertTrue(relayed_request(self.parse([host, *self.CLOUDFLARE_HEADERS])))


if __name__ == '__main__':
    unittest.main()
