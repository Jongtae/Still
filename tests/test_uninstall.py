"""#1545: `agentos uninstall` removes AgentOS in one place and keeps data unless told otherwise."""
import fcntl
import tempfile
import unittest
from pathlib import Path

from personal_agent import uninstall


class ScriptedTerminal:
    def __init__(self, asks=()):
        self.asks, self.lines = list(asks), []

    def say(self, text=''):
        self.lines.append(text)

    def ask(self, prompt):
        return self.asks.pop(0)

    def text(self):
        return '\n'.join(self.lines)


class Done:
    def __init__(self, code):
        self.returncode = code


class FakeKey:
    deleted = []

    def __init__(self, account, service):
        self.account, self.service = account, service

    def delete(self):
        FakeKey.deleted.append((self.account, self.service))
        return True


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        (agents / 'com.personal-agentos.plist').write_text('x')
        (agents / 'com.personal-agentos.spouse.plist').write_text('x')
        self.owner = self.home / '.local/share/agentos'
        self.family = self.home / '.local/share/agentos-instances/spouse'
        for folder in (self.owner, self.family):
            (folder / 'private').mkdir(parents=True)
            (folder / 'private' / 'quickstart.db').write_text('state')
        self.prefix = self.home / '.local/share/uv/tools/personal-agentos'
        (self.home / '.local/bin').mkdir(parents=True)
        (self.home / '.local/bin/uv').write_text('')
        self.actions, self.commands = [], []
        FakeKey.deleted = []

    def action(self, name, **options):
        self.actions.append((name, options.get('instance')))
        return {'ok': True}

    def runner(self, command, check=False):
        self.commands.append(command)
        return Done(0)

    def run_uninstall(self, term, **options):
        return uninstall.run(term, home=self.home, env={}, platform='darwin', prefix=self.prefix,
                             action=self.action, runner=self.runner, key_factory=FakeKey,
                             which=lambda name: None, **options)

    def test_cancel_changes_nothing(self):
        term = ScriptedTerminal(asks=[''])
        self.assertEqual(self.run_uninstall(term), 0)
        self.assertIn('취소했습니다', term.text())
        self.assertEqual((self.actions, self.commands), ([], []))
        self.assertTrue(self.owner.is_dir())

    def test_default_removes_services_and_program_and_keeps_data(self):
        term = ScriptedTerminal(asks=['y', ''])
        self.assertEqual(self.run_uninstall(term), 0)
        self.assertEqual(self.actions, [('uninstall', None), ('uninstall', 'spouse')])
        self.assertEqual(self.commands, [[str(self.home / '.local/bin/uv'), 'tool', 'uninstall', 'personal-agentos']])
        self.assertTrue(self.owner.is_dir() and self.family.is_dir())
        self.assertEqual(FakeKey.deleted, [], 'kept data keeps the keys that decrypt it')
        self.assertIn('/deletebot', term.text())
        self.assertIn('myaccount.google.com/permissions', term.text())

    def test_typing_delete_removes_data_and_its_keychain_keys(self):
        term = ScriptedTerminal(asks=['y', 'delete'])
        self.assertEqual(self.run_uninstall(term), 0)
        self.assertFalse(self.owner.exists() or self.family.exists())
        services = sorted({service for _, service in FakeKey.deleted})
        self.assertEqual(services, ['personal-agentos.api-slots', 'personal-agentos.browser-jar', 'personal-agentos.google-oauth'])
        self.assertEqual(len(FakeKey.deleted), 6)

    def test_anything_but_delete_keeps_the_data(self):
        self.run_uninstall(ScriptedTerminal(asks=['y', 'yes']))
        self.assertTrue(self.owner.is_dir())

    def test_a_running_instance_stops_before_data_or_program(self):
        lock = (self.owner / 'private' / 'instance.lock').open('a')
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        term = ScriptedTerminal(asks=['y', 'delete'])
        self.assertEqual(self.run_uninstall(term), 1)
        self.assertTrue(self.owner.is_dir() and self.family.is_dir())
        self.assertEqual(self.commands, [])
        self.assertIn('Ctrl-C', term.text())

    def test_yes_without_delete_data_keeps_data_and_asks_nothing(self):
        term = ScriptedTerminal()
        self.assertEqual(self.run_uninstall(term, assume_yes=True), 0)
        self.assertTrue(self.owner.is_dir())
        self.assertEqual(len(self.commands), 1)

    def test_yes_with_delete_data_deletes(self):
        self.assertEqual(self.run_uninstall(ScriptedTerminal(), assume_yes=True, delete_data=True), 0)
        self.assertFalse(self.owner.exists())

    def test_a_failed_service_or_program_step_is_reported(self):
        self.action = lambda name, **options: {'ok': False}
        self.runner = lambda command, check=False: Done(1)
        term = ScriptedTerminal(asks=['y', ''])
        self.assertEqual(self.run_uninstall(term), 1)
        self.assertIn('agentos service status', term.text())
        self.assertIn('uv tool uninstall personal-agentos', term.text())

    def test_unknown_install_method_prints_how_to_remove(self):
        term = ScriptedTerminal(asks=['y', ''])
        uninstall.run(term, home=self.home, env={}, platform='darwin', prefix='/opt/source-checkout/.venv',
                      action=self.action, runner=self.runner, key_factory=FakeKey, which=lambda name: None)
        self.assertEqual(self.commands, [])
        self.assertIn('pip uninstall personal-agentos', term.text())

    def test_homebrew_install_uses_brew(self):
        label, command = uninstall.program_remover('/opt/homebrew/Cellar/agentos/1.4.1/libexec', self.home,
                                                   which=lambda name: '/opt/homebrew/bin/brew' if name == 'brew' else None)
        self.assertEqual((label, command), ('Homebrew', ['/opt/homebrew/bin/brew', 'uninstall', 'agentos']))

    def test_linux_has_no_launchd_services_or_keychain(self):
        term = ScriptedTerminal(asks=['y', 'delete'])
        uninstall.run(term, home=self.home, env={}, platform='linux', prefix=self.prefix,
                      action=self.action, runner=self.runner, key_factory=FakeKey, which=lambda name: None)
        self.assertEqual(self.actions, [])
        self.assertEqual(FakeKey.deleted, [])
        self.assertFalse(self.owner.exists())

    def test_cli_refuses_delete_data_without_yes(self):
        with self.assertRaises(SystemExit):
            uninstall.main(['--delete-data'])


if __name__ == '__main__':
    unittest.main()
