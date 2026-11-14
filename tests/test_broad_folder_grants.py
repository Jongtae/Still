"""#820: a broad folder (the home folder) is granted after one owner confirmation.

What the confirmation covers and what it does not: one folder per confirmation,
asked once (a stored grant is not asked again), only from Settings on this Mac,
never for the result-write folder or a folder a Work asked for, and the
protected locations inside it (credential, system and Still data) stay out of
every read and listing.
"""
import os
import unittest
from pathlib import Path
from unittest import mock

import test_contextual_capability_handoff as handoff
from personal_agent import folder_grants
from personal_agent.agent_runtime import Capabilities
from personal_agent.conversation_handoff import LOCAL_FOLDER_READ
from personal_agent.file_workspace import FileWorkspace


class BroadFolderGrantTests(handoff.HandoffTestCase):
    serve = handoff.HttpSurfaceTests.serve
    call = handoff.HttpSurfaceTests.call
    decode = staticmethod(handoff.HttpSurfaceTests.decode)

    def setUp(self):
        super().setUp()
        self.home = (self.root / 'home' / 'owner').resolve()
        (self.home / 'Documents').mkdir(parents=True)
        (self.home / 'Documents' / 'plan.txt').write_text('launch plan', encoding='utf-8')
        (self.home / 'Library' / 'Keychains').mkdir(parents=True)
        (self.home / 'Library' / 'Keychains' / 'login.txt').write_text('keychain secret', encoding='utf-8')
        (self.home / 'Projects').mkdir()
        for patch in (mock.patch.object(Path, 'home', return_value=self.home),
                      mock.patch.dict(os.environ, {'HOME': str(self.home)})):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_settings_route_asks_once_and_grants_on_the_confirmation(self):
        base, session = self.serve()
        status, reply = self.call(base, '/api/files/roots', session, {'paths': [str(self.home)]})
        self.assertEqual((status, reply.get('reason'), reply.get('path')), (409, 'confirm_broad', str(self.home)), reply)
        self.assertIn('Library/Keychains', reply['error'], 'the owner is told what stays excluded')
        self.assertEqual(self.roots(), [], 'nothing is granted before the confirmation')
        status, reply = self.call(base, '/api/files/roots', session, {'paths': [str(self.home)], 'confirm_broad': [str(self.home)]})
        self.assertEqual(status, 200, reply)
        self.assertEqual(self.roots(), [str(self.home)])
        self.assertIsNone(reply['roots'][0]['blocked'])
        # Asked once: a later save keeping the stored grant does not ask again.
        status, reply = self.call(base, '/api/files/roots', session, {'paths': [str(self.home), str(self.home / 'Projects')]})
        self.assertEqual(status, 200, reply)
        self.assertEqual(self.roots(), [str(self.home), str(self.home / 'Projects')])

    def test_a_confirmation_covers_only_the_folder_it_names(self):
        other = self.root / 'other-home'
        (other / '.ssh').mkdir(parents=True)
        (other / 'Library' / 'Keychains').mkdir(parents=True)
        with self.assertRaises(folder_grants.ConfirmBroad) as raised:
            self.service.save_roots({'paths': [str(self.home), str(self.home / 'Library')], 'confirm_broad': [str(self.home)]})
        self.assertEqual(raised.exception.path, self.home / 'Library')
        self.assertEqual(self.roots(), [])

    def test_off_this_mac_a_confirmation_cannot_add_a_broad_folder(self):
        base, session = self.serve()
        host = base.split('//', 1)[1]
        for label, extra in handoff.HttpSurfaceTests.OFF_MAC:
            with self.subTest(via=label):
                status, reply = self.call(base, '/api/files/roots', {**session, 'Host': host, **extra},
                                          {'paths': [str(self.home)], 'confirm_broad': [str(self.home)]})
                self.assertEqual((status, reply.get('reason')), (403, 'owner_local_surface'), reply)
                self.assertEqual(self.roots(), [])

    def test_a_folder_a_work_asked_for_and_the_result_folder_are_never_broad(self):
        with self.assertRaisesRegex(ValueError, folder_grants.BROAD):
            self.service._plan_local_grant(LOCAL_FOLDER_READ, str(self.home))
        with self.assertRaisesRegex(ValueError, folder_grants.BROAD):
            FileWorkspace(self.store).plan_grant('reference', str(self.home))
        with self.assertRaisesRegex(ValueError, folder_grants.BROAD):
            self.service.configure_file_workspace({'references': [str(self.home / 'Projects')], 'workspace': str(self.home),
                                                   'confirm_broad': [str(self.home)]})
        self.assertEqual(self.roots(), [])
        # A broad reference folder is granted on the same one confirmation as a read folder.
        out = self.root / 'results'
        out.mkdir()
        status = self.service.configure_file_workspace({'references': [str(self.home)], 'workspace': str(out),
                                                        'confirm_broad': [str(self.home)]})
        self.assertEqual([ref['path'] for ref in status['references']], [str(self.home)])

    def test_protected_locations_inside_a_home_grant_are_never_read_or_listed(self):
        # Still's data folder placed under the home folder by a custom data path.
        custom = self.home / 'StillData'
        custom.mkdir()
        (custom / 'notes.txt').write_text('launch plan copy kept by Still', encoding='utf-8')
        (self.home / 'Documents' / 'keys.txt').symlink_to(self.home / 'Library' / 'Keychains' / 'login.txt')
        roots = self.service.save_roots({'paths': [str(self.home)], 'confirm_broad': [str(self.home)]})['roots']
        caps = Capabilities(self.store, None, {}, '', 'job', lambda *args: None)
        with mock.patch.object(self.store, 'root', custom), mock.patch.object(self.store, 'private', custom / 'private'):
            self.assertIn('launch plan', caps.read_file(roots[0]['id'], 'Documents/plan.txt')['content'])
            for path in ('Library/Keychains/login.txt', 'Documents/keys.txt', 'StillData/notes.txt'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    caps.read_file(roots[0]['id'], path)
            found = {hit['path'] for hit in caps.find_files('launch')['files']} | {hit['path'] for hit in caps.find_files('keychain')['files']}
        self.assertEqual(found, {'Documents/plan.txt'})

    def test_protected_locations_inside_a_broad_reference_folder_are_never_read(self):
        out = self.root / 'results'
        out.mkdir()
        files = FileWorkspace(self.store)
        status = files.configure([str(self.home)], str(out), confirm_broad=[str(self.home)])
        ref = status['references'][0]['id']
        self.assertIn('launch plan', files.read(ref, 'Documents/plan.txt')['content'])
        with self.assertRaises(ValueError):
            files.read(ref, 'Library/Keychains/login.txt')
        self.assertEqual({source['path'] for source in files.find_references('secret')}, set())
        self.assertEqual({source['path'] for source in files.find_references('launch')}, {'Documents/plan.txt'})


if __name__ == '__main__':
    unittest.main()
