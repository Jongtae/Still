"""#705: Still's own options are visible to the owner's AI and listed in /help.

Evidence class: model-free unit tests on the real ``IntentClassifier``,
``SettingsOrchestrator`` and ``AgentService`` setters with a temporary store.
"""
import unittest

from personal_agent import still_profile
from personal_agent.agent_runtime import SETTINGS_CATEGORIES, SETTINGS_CHANGE_ARGUMENTS, SETTINGS_READ_ARGUMENTS
from personal_agent.conversation_handoff import (INTENT_DEVELOPER_MODE, OWNER_COMMANDS, IntentClassifier,
                                                 owner_commands)
from personal_agent.messages import msg
from personal_agent.settings_orchestrator import SETTINGS, SettingsError, direct_request_setting

from test_owner_settings_conversation import _Case


class OwnerCommandList(unittest.TestCase):
    def test_every_listed_command_is_one_the_parser_takes(self):
        handoff = IntentClassifier()
        for command, _message_id in OWNER_COMMANDS:
            with self.subTest(command=command):
                decision = handoff.explicit(command) or handoff.explicit(command + ' x')
                self.assertIsNotNone(decision, f'{command} is listed but the parser does not take it')
        self.assertEqual(handoff.explicit('/dev on').intent, INTENT_DEVELOPER_MODE)

    def test_help_lists_every_command_in_the_owner_language(self):
        for lang, code in (('ko', 'ko'), ('en', 'en-US'), ('ja', 'ja'), ('zh', 'zh-hans'), ('en', 'de')):
            text = still_profile.welcome(code, commands=True)
            with self.subTest(code=code):
                self.assertIn(msg('owner_commands.heading', lang), text)
                for row in owner_commands(lang):
                    self.assertIn(row['usage'], text)
                closing = still_profile.WELCOME[lang].rsplit('\n\n', 1)[1]
                self.assertTrue(text.endswith(closing), 'the closing question stays last')
                self.assertEqual(still_profile.welcome(code), still_profile.WELCOME[lang], '/start stays the plain welcome')
        classifier = IntentClassifier()
        self.assertEqual(classifier.explicit('/help').argument, 'commands')
        self.assertIsNone(classifier.explicit('/start').argument)


class DeveloperModeSetting(_Case):
    def test_the_ai_reads_developer_mode_and_the_owner_commands(self):
        self.assertIn('developer_mode', SETTINGS_CATEGORIES)
        self.assertIn('developer_mode', SETTINGS_READ_ARGUMENTS['category'])
        self.assertIn('developer_mode enabled', SETTINGS_CHANGE_ARGUMENTS['setting'])
        read = self.settings.read('owner')
        row = read['settings']['developer_mode']['enabled']
        self.assertEqual((row['value'], [o['value'] for o in row['options']]), ('off', ['on', 'off']))
        self.assertIn('/dev on', row['note'])
        self.assertEqual([item['command'] for item in read['owner_commands']], [c for c, _ in OWNER_COMMANDS])
        self.assertNotIn('owner_commands', self.settings.read('owner', 'developer_mode'),
                         'a category read stays that category')

    def test_a_confirmed_change_and_the_dev_command_use_one_setter(self):
        draft = self.draft('developer_mode', 'enabled', 'on')
        self.assertEqual((draft['before'], draft['after']), ('off', 'on'))
        self.assertFalse(self.service.developer_mode(), 'a draft changes nothing')
        self.assertEqual(self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])['state'], 'applied')
        self.assertTrue(self.service.developer_mode())
        self.assertEqual(self.settings.read('owner', 'developer_mode')['settings']['developer_mode']['enabled']['value'], 'on')
        self.service.set_developer_mode(False)
        self.assertEqual(self.settings.read('owner', 'developer_mode')['settings']['developer_mode']['enabled']['value'], 'off')

    def test_developer_mode_applies_on_the_owner_direct_request_only_as_on_or_off(self):
        self.assertEqual(SETTINGS['developer_mode'], ('enabled',))
        self.assertTrue(direct_request_setting('developer_mode', 'enabled', 'on'))
        with self.assertRaises(SettingsError):
            self.draft('developer_mode', 'enabled', 'loud')
        self.assertFalse(self.service.developer_mode())


if __name__ == '__main__':
    unittest.main()
