"""#1307: a skill folder is extracted from a streamed repository archive.

The archive of a large repository (Still's own is about 64 MB) used to be read
into memory under the 32 MB general download cap, so no Still reference skill
could be installed. ``fetch_github`` now reads the archive as a stream
(``tarfile`` mode ``r|gz``), counts the bytes it receives against
``MAX_ARCHIVE_BYTES`` and still writes only the pinned folder and the root
licence files, bounded by the package limits. The commit pin and the digest
check are unchanged.
"""
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import skills
from personal_agent.plugins import PluginRegistry
from personal_agent.quickstart_store import QuickStore
from personal_agent.skills import SkillError, SkillLibrary, fetch_github, parse_source

REPO = 'example/big'
COMMIT = 'c' * 40
SKILL = '---\nname: small\ndescription: A small skill.\nlicense: MIT\n---\nBody.\n'


def archive(files, revision=COMMIT):
    """A codeload-shaped ``tar.gz`` with ``<repo>-<revision>/`` members, in order."""
    top = f"{REPO.split('/')[1]}-{revision}"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for path, data in files.items():
            data = data.encode() if isinstance(data, str) else data
            info = tarfile.TarInfo(f'{top}/{path}')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class Transport:
    """A bytes-returning GitHub transport, like every existing test fake."""

    def __init__(self, data):
        self.data, self.calls = data, []

    def __call__(self, url, limit=None, timeout=None):
        self.calls.append((url, limit))
        return COMMIT.encode() if url.startswith('https://api.github.com/') else self.data


class StreamedResponse(io.RawIOBase):
    """An HTTP response body served in chunks; records how much was read."""

    def __init__(self, data, chunk=64 * 1024):
        self.body, self.chunk, self.served, self.closed_by_caller = io.BytesIO(data), chunk, 0, False

    def readable(self):
        return True

    def readinto(self, buffer):
        part = self.body.read(min(len(buffer), self.chunk))
        buffer[:len(part)] = part
        self.served += len(part)
        return len(part)

    def close(self):
        self.closed_by_caller = True
        super().close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _Case(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.store = QuickStore(self.root / 'data')
        PluginRegistry(self.store.root)
        patcher = mock.patch('personal_agent.skills.reference_skills', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def source(self, transport, path='skills/small'):
        return parse_source(f'https://github.com/{REPO}/tree/{COMMIT}/{path}', transport)


class LargeArchiveSmallFolder(_Case):
    def test_an_archive_over_the_old_cap_installs_its_small_folder_and_checks_the_digest(self):
        # Incompressible filler ahead of and after the skill: the archive itself is over 32 MB.
        filler = os.urandom(17 * 1024 * 1024)
        data = archive({'LICENSE': 'MIT License\n', 'assets/a.bin': filler,
                        'skills/small/SKILL.md': SKILL, 'assets/b.bin': filler})
        self.assertGreater(len(data), 32 * 1024 * 1024)
        transport = Transport(data)
        library = SkillLibrary(self.store, transport=transport)
        expected = skills.inspect_skill(self._folder({'SKILL.md': SKILL}))['digest']
        manifest = library.install(f'https://github.com/{REPO}/tree/{COMMIT}/skills/small', expected_digest=expected)
        self.assertEqual(manifest['skills'][0]['digest'], expected)
        self.assertEqual(manifest['source']['revision'], COMMIT)
        archive_calls = [limit for url, limit in transport.calls if url.startswith('https://codeload.github.com/')]
        self.assertEqual(archive_calls, [skills.MAX_ARCHIVE_BYTES])

    def test_a_digest_mismatch_is_still_refused_and_nothing_is_declared(self):
        library = SkillLibrary(self.store, transport=Transport(archive({'skills/small/SKILL.md': SKILL})))
        with self.assertRaises(SkillError) as refused:
            library.install(f'https://github.com/{REPO}/tree/{COMMIT}/skills/small', expected_digest='0' * 64)
        self.assertEqual(refused.exception.code, 'pin_mismatch')
        self.assertEqual(library.installed(), [])

    def _folder(self, files):
        folder = self.root / 'expected' / 'small'
        for rel, text in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(text)
        return folder


class Refusals(_Case):
    def test_an_oversized_folder_is_refused_by_the_package_limits(self):
        big = 'x' * (skills.MAX_FILE_BYTES + 1)
        transport = Transport(archive({'skills/small/SKILL.md': SKILL, 'skills/small/big.md': big}))
        with self.assertRaises(SkillError) as refused:
            fetch_github(self.source(transport), self.root / 'stage' / 'small', transport)
        self.assertEqual(refused.exception.code, 'invalid_package')
        self.assertEqual(str(refused.exception), skills.msg('skills.file_limit'))

    def test_too_many_files_in_the_folder_are_refused(self):
        files = {'skills/small/SKILL.md': SKILL}
        files.update({f'skills/small/n/{i}.md': 'n' for i in range(skills.MAX_FILES + 1)})
        transport = Transport(archive(files))
        with self.assertRaises(SkillError) as refused:
            fetch_github(self.source(transport), self.root / 'stage' / 'small', transport)
        self.assertEqual(str(refused.exception), skills.msg('skills.file_limit'))

    def test_an_archive_over_the_archive_cap_is_refused(self):
        data = archive({'assets/a.bin': os.urandom(2 * 1024 * 1024), 'skills/small/SKILL.md': SKILL})
        transport = Transport(data)
        with mock.patch.object(skills, 'MAX_ARCHIVE_BYTES', 1024 * 1024), self.assertRaises(SkillError) as refused:
            fetch_github(self.source(transport), self.root / 'stage' / 'small', transport)
        self.assertEqual((refused.exception.code, str(refused.exception)),
                         ('invalid_package', skills.msg('skills.download_limit')))
        self.assertFalse((self.root / 'stage' / 'small' / 'SKILL.md').exists())

    def test_the_archive_cap_is_larger_than_the_general_cap_and_finite(self):
        self.assertGreater(skills.MAX_ARCHIVE_BYTES, 64 * 1024 * 1024)
        self.assertLessEqual(skills.MAX_ARCHIVE_BYTES, 1024 * 1024 * 1024)
        self.assertEqual(skills.MAX_DOWNLOAD_BYTES, 32 * 1024 * 1024)


class RealTransportStreams(_Case):
    """The default ``http_get`` streams a codeload archive instead of reading it whole."""

    def opener(self, response):
        opener = mock.Mock()
        opener.open.return_value = response
        return mock.patch('urllib.request.build_opener', return_value=opener)

    def test_the_default_transport_streams_the_archive_into_the_extraction(self):
        filler = os.urandom(3 * 1024 * 1024)
        response = StreamedResponse(archive({'skills/small/SKILL.md': SKILL, 'assets/a.bin': filler}))
        source = {'kind': 'github', 'repo': REPO, 'revision': COMMIT, 'path': 'skills/small'}
        with self.opener(response):
            fetch_github(source, self.root / 'stage' / 'small', skills.http_get)
        self.assertEqual((self.root / 'stage' / 'small' / 'SKILL.md').read_text(), SKILL)
        self.assertTrue(response.closed_by_caller)

    def test_the_default_transport_stops_reading_at_the_archive_cap(self):
        data = archive({'assets/a.bin': os.urandom(4 * 1024 * 1024), 'skills/small/SKILL.md': SKILL})
        response = StreamedResponse(data)
        source = {'kind': 'github', 'repo': REPO, 'revision': COMMIT, 'path': 'skills/small'}
        with self.opener(response), mock.patch.object(skills, 'MAX_ARCHIVE_BYTES', 1024 * 1024), \
                self.assertRaises(SkillError) as refused:
            fetch_github(source, self.root / 'stage' / 'small', skills.http_get)
        self.assertEqual(str(refused.exception), skills.msg('skills.download_limit'))
        # It read no more than the cap plus one read's worth, never the whole body.
        self.assertLess(response.served, 1024 * 1024 + 2 * 64 * 1024 + 1)
        self.assertTrue(response.closed_by_caller)

    def test_api_answers_are_still_bounded_bytes(self):
        response = StreamedResponse(COMMIT.encode())
        with self.opener(response):
            got = skills.http_get(f'https://api.github.com/repos/{REPO}/commits/main', limit=4096)
        self.assertEqual(got, COMMIT.encode())
        with self.opener(StreamedResponse(b'x' * 5000)), self.assertRaises(SkillError) as refused:
            skills.http_get(f'https://api.github.com/repos/{REPO}/commits/main', limit=4096)
        self.assertEqual(str(refused.exception), skills.msg('skills.download_limit'))

    def test_a_host_outside_github_is_still_refused_before_any_request(self):
        with mock.patch('urllib.request.build_opener') as build, self.assertRaises(SkillError) as refused:
            skills.http_get('https://example.com/x.tar.gz')
        self.assertEqual(refused.exception.code, 'invalid_source')
        build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
