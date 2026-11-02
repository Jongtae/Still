"""#593: the pinned official Node.js Still downloads for code_run when the host has none.

Model-free and network-free: an injected download of a small release-shaped
archive.  No live nodejs.org download is observed here.
"""
import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import node_runtime

NAME = f'node-v{node_runtime.VERSION}-darwin-arm64'
NODE = b'\x7fELF pinned node'


def release(node=NODE, extra=()):
    """A ``.tar.gz`` shaped like a nodejs.org release: node, npm, headers and docs."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        def add(name, data=b'', mode=0o644, link=None):
            info = tarfile.TarInfo(f'{NAME}/{name}')
            if link is not None:
                info.type, info.linkname = tarfile.SYMTYPE, link
            else:
                info.size, info.mode = len(data), mode
            archive.addfile(info, io.BytesIO(data) if link is None else None)
        add('bin/node', node, 0o755)
        add('bin/npm', link='../lib/node_modules/npm/bin/npm-cli.js')
        add('bin/corepack', link='../lib/node_modules/corepack/dist/corepack.js')
        add('lib/node_modules/npm/bin/npm-cli.js', b'#!/usr/bin/env node\n', 0o755)
        add('lib/node_modules/corepack/dist/corepack.js', b'// corepack\n')
        add('include/node/node.h', b'// header\n')
        add('share/doc/node/README.md', b'docs\n')
        for name, kwargs in extra:
            add(name, **kwargs)
    return buffer.getvalue()


def pin_for(data, node=NODE):
    return ('darwin-arm64', hashlib.sha256(data).hexdigest(), hashlib.sha256(node).hexdigest())


class Fetch:
    def __init__(self, data):
        self.data, self.urls = data, []

    def __call__(self, url):
        self.urls.append(url)
        return self.data


class Ensure(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())

    def test_downloads_once_and_keeps_only_node_and_npm(self):
        data = release()
        fetch = Fetch(data)
        node = node_runtime.ensure(self.directory, fetch=fetch, chosen=pin_for(data))
        self.assertEqual(fetch.urls, [f'https://nodejs.org/dist/v{node_runtime.VERSION}/{NAME}.tar.gz'])
        self.assertEqual(Path(node).read_bytes(), NODE)
        self.assertTrue(os.access(node, os.X_OK))
        root = Path(node).parent.parent
        self.assertTrue((root / 'bin' / 'npm').is_file(), 'npm resolves to its own package')
        self.assertFalse((root / 'bin' / 'corepack').exists())
        self.assertFalse((root / 'include').exists())
        self.assertFalse((root / 'share').exists())
        self.assertEqual(node_runtime.ensure(self.directory, fetch=fetch, chosen=pin_for(data)), node)
        self.assertEqual(len(fetch.urls), 1, 'a verified copy is reused')
        self.assertEqual(node_runtime.installed(self.directory, chosen=pin_for(data)), node)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), [f'node-v{node_runtime.VERSION}'])

    def test_an_archive_that_does_not_match_its_pin_keeps_nothing(self):
        data = release()
        chosen = ('darwin-arm64', 'f' * 64, hashlib.sha256(NODE).hexdigest())
        with self.assertRaisesRegex(RuntimeError, 'did not match its pinned SHA-256; nothing was run'):
            node_runtime.ensure(self.directory, fetch=Fetch(data), chosen=chosen)
        self.assertEqual(list(self.directory.iterdir()) if self.directory.exists() else [], [])

    def test_a_node_binary_that_does_not_match_its_pin_keeps_nothing(self):
        data = release(node=b'something else')
        with self.assertRaisesRegex(RuntimeError, 'binary did not match'):
            node_runtime.ensure(self.directory, fetch=Fetch(data), chosen=pin_for(data, node=NODE))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_a_changed_copy_is_not_used_and_is_replaced(self):
        data = release()
        node = node_runtime.ensure(self.directory, fetch=Fetch(data), chosen=pin_for(data))
        Path(node).write_bytes(b'tampered')
        self.assertIsNone(node_runtime.installed(self.directory, chosen=pin_for(data)))
        fetch = Fetch(data)
        self.assertEqual(node_runtime.ensure(self.directory, fetch=fetch, chosen=pin_for(data)), node)
        self.assertEqual(len(fetch.urls), 1)
        self.assertEqual(Path(node).read_bytes(), NODE)

    def test_a_link_out_of_the_tool_directory_is_refused(self):
        data = release(extra=[('lib/node_modules/npm/escape', {'link': '/etc/passwd'})])
        with self.assertRaises(tarfile.TarError):
            node_runtime.ensure(self.directory, fetch=Fetch(data), chosen=pin_for(data))
        self.assertEqual(list(self.directory.iterdir()), [])


class Pins(unittest.TestCase):
    def test_platform_names(self):
        self.assertEqual(node_runtime.pin('Darwin', 'arm64')[0], 'darwin-arm64')
        self.assertEqual(node_runtime.pin('Linux', 'aarch64')[0], 'linux-arm64')
        self.assertEqual(node_runtime.pin('Linux', 'AMD64')[0], 'linux-x64')
        self.assertIsNone(node_runtime.pin('Windows', 'AMD64'))
        with mock.patch.object(node_runtime, 'pin', return_value=None), self.assertRaises(FileNotFoundError):
            node_runtime.ensure(tempfile.mkdtemp(), fetch=lambda url: self.fail('nothing to fetch'))

    def test_every_pin_is_two_sha256_digests(self):
        for (system, machine), (release_name, archive_sha, node_sha) in node_runtime.PINS.items():
            with self.subTest(platform=release_name):
                self.assertRegex(archive_sha, r'^[0-9a-f]{64}$')
                self.assertRegex(node_sha, r'^[0-9a-f]{64}$')
                self.assertNotEqual(archive_sha, node_sha)


if __name__ == '__main__':
    unittest.main()
