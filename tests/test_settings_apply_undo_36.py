"""#36: an owner-typed settings change applies once with a notice and an exact undo; an inferred one asks.

Evidence class: model-free unit tests over the real ``QuickStore``, ``AgentService`` setters and
``SettingsOrchestrator``.  ``owner_request`` stands in for the Judgment AI's reading of the owner's
own typed message (``AgentService.owner_settings_request``); no live model is contacted.
"""
from test_owner_settings_conversation import _Case

from personal_agent.settings_orchestrator import SettingsError


class ApplyAndUndo(_Case):
    def tools(self, asked=True, owner='owner', channel='http'):
        self.asks = []

        def owner_request(summary):
            self.asks.append(summary)
            return asked
        return self.settings.work_tools(owner, channel, None, owner_request=owner_request)

    def change(self, tools, **args):
        return tools('settings_change', args)

    def timezone(self):
        return self.context()['timezone']

    def terminals(self):
        return [row['terminal'] for row in self.store.config('settings_audit') or ()]

    def test_owner_typed_change_applies_once_with_notice_and_no_second_ask(self):
        tools = self.tools()
        before = self.timezone()
        result = self.change(tools, category='current_context', setting='timezone', value='Asia/Tokyo')
        self.assertEqual((result['state'], result['applied'], result['owner_request']), ('applied', True, True))
        self.assertEqual(self.timezone(), 'Asia/Tokyo')
        self.assertNotEqual(before, 'Asia/Tokyo')
        self.assertEqual(len(self.asks), 1, 'one judgment of the owner message, no second confirmation')
        self.assertTrue(result['undoable'])
        self.assertIn('되돌리고 싶으면', result['response'])
        self.assertIn(f'undo="{result["draft_id"]}"', result['next_step'])
        self.assertEqual(self.settings.pending_for_conversation('owner', 'http'), [], 'nothing waits')
        self.assertEqual(self.terminals().count('applied'), 1)
        with self.assertRaises(SettingsError):  # a repeated callback of the same draft applies nothing
            row = self.settings._drafts()[result['draft_id']]
            self.settings.confirm('owner', 'http', row['id'], row['digest'])
        self.assertEqual(self.terminals().count('applied'), 1)

    def test_inferred_change_asks_and_changes_nothing(self):
        tools = self.tools(asked=False)
        before = self.timezone()
        result = self.change(tools, category='current_context', setting='timezone', value='Asia/Tokyo',
                             reason='The owner mentioned a trip.')
        self.assertFalse(result['applied'])
        self.assertEqual(result['state'], 'awaiting-confirmation')
        self.assertNotIn('digest', result, 'the model never gets the confirmation digest')
        self.assertEqual(self.timezone(), before)
        self.assertEqual(len(self.settings.pending_for_conversation('owner', 'http')), 1)

    def test_undo_restores_the_exact_prior_value_once(self):
        tools = self.tools()
        before = self.timezone()
        applied = self.change(tools, category='current_context', setting='timezone', value='Asia/Tokyo')
        undone = self.change(tools, category='current_context', setting='timezone', value='Europe/Paris',
                             undo=applied['draft_id'])
        self.assertEqual((undone['state'], undone['applied']), ('applied', True))
        self.assertEqual(undone['after'], before, 'the exact prior value, never the value the model named')
        self.assertEqual(self.timezone(), before)
        self.assertIn('되돌리기', self.asks[-1], 'the judgment reads the undo preview')
        self.assertFalse(undone['undoable'], 'an undo is not offered for undo again')
        again = self.change(tools, category='current_context', setting='timezone', value=before,
                            undo=applied['draft_id'])
        self.assertEqual((again['state'], again['idempotent']), ('already_undone', True))
        self.assertEqual(self.timezone(), before)
        self.assertEqual(self.terminals().count('applied'), 2)

    def test_inferred_undo_waits_and_is_not_duplicated(self):
        applied = self.change(self.tools(), category='developer_mode', setting='enabled', value='on')
        self.assertTrue(applied['applied'])
        tools = self.tools(asked=False)
        first = self.change(tools, category='developer_mode', setting='enabled', value='off', undo=applied['draft_id'])
        second = self.change(tools, category='developer_mode', setting='enabled', value='off', undo=applied['draft_id'])
        self.assertEqual((first['state'], first['applied']), ('awaiting-confirmation', False))
        self.assertEqual(second['draft_id'], first['draft_id'], 'asking again returns the same waiting undo')
        self.assertEqual(len(self.settings.pending_for_conversation('owner', 'http')), 1)
        self.assertEqual(self.settings.read('owner', 'developer_mode')['settings']['developer_mode']['enabled']['value'], 'on')

    def test_undo_never_overwrites_a_later_change(self):
        tools = self.tools()
        first = self.change(tools, category='current_context', setting='timezone', value='Asia/Tokyo')
        self.change(tools, category='current_context', setting='timezone', value='America/New_York')
        with self.assertRaisesRegex(SettingsError, '다시 바뀌어서'):
            self.change(tools, category='current_context', setting='timezone', value='x', undo=first['draft_id'])
        self.assertEqual(self.timezone(), 'America/New_York')

    def test_a_waiting_undo_goes_stale_when_the_setting_changes_first(self):
        applied = self.change(self.tools(), category='current_context', setting='timezone', value='Asia/Tokyo')
        waiting = self.change(self.tools(asked=False), category='current_context', setting='timezone', value='',
                              undo=applied['draft_id'])
        self.change(self.tools(), category='current_context', setting='timezone', value='America/New_York')
        row = self.settings._drafts()[waiting['draft_id']]
        with self.assertRaises(SettingsError):
            self.settings.confirm('owner', 'http', row['id'], row['digest'])
        self.assertEqual(self.timezone(), 'America/New_York')

    def test_undo_is_bound_to_this_owner_and_conversation_and_undoable_kinds(self):
        applied = self.change(self.tools(), category='current_context', setting='timezone', value='Asia/Tokyo')
        with self.assertRaises(SettingsError):
            self.change(self.tools(owner='other'), category='current_context', setting='timezone', value='',
                        undo=applied['draft_id'])
        with self.assertRaises(SettingsError):
            self.change(self.tools(channel='telegram'), category='current_context', setting='timezone', value='',
                        undo=applied['draft_id'])
        waiting = self.change(self.tools(asked=False), category='current_context', setting='enabled', value='on')
        with self.assertRaises(SettingsError):  # not applied: nothing to undo
            self.change(self.tools(), category='current_context', setting='enabled', value='off',
                        undo=waiting['draft_id'])
        self.assertEqual(self.timezone(), 'Asia/Tokyo')

    def test_undoing_payment_autonomy_off_never_turns_it_on_without_confirmation(self):
        def autonomy():
            return self.settings.read('owner', 'payment_autonomy')['settings']['payment_autonomy']['enabled']['value']
        on = self.draft('payment_autonomy', 'enabled', 'on')
        self.settings.confirm('owner', 'http', on['draft_id'], on['digest'])
        self.assertEqual(autonomy(), 'on')
        off = self.change(self.tools(), category='payment_autonomy', setting='enabled', value='off')
        self.assertEqual((off['applied'], autonomy()), (True, 'off'))
        undo = self.change(self.tools(), category='payment_autonomy', setting='enabled', value='on', undo=off['draft_id'])
        self.assertEqual((undo['state'], undo['applied']), ('awaiting-confirmation', False),
                         'turning payment autonomy on always waits for the exact draft confirmation')
        self.assertEqual(autonomy(), 'off')

    def test_an_interrupted_undo_reports_only_what_the_value_shows(self):
        tools = self.tools(asked=False)
        applied = self.change(self.tools(), category='current_context', setting='timezone', value='Asia/Tokyo')
        waiting = self.change(tools, category='current_context', setting='timezone', value='', undo=applied['draft_id'])
        row = self.settings._drafts()[waiting['draft_id']]
        self.settings._admit(row, row['digest'])  # applying, then cut off before its setter ran
        repeat = self.change(tools, category='current_context', setting='timezone', value='', undo=applied['draft_id'])
        self.assertEqual((repeat['state'], repeat['applied']), ('applying', False))
        self.assertNotIn('이미 되돌렸어요', repeat['response'])
        self.settings._in_flight.clear()
        self.settings.reconcile()
        self.assertEqual(self.settings._drafts()[waiting['draft_id']]['state'], 'unknown')
        again = self.change(self.tools(), category='current_context', setting='timezone', value='', undo=applied['draft_id'])
        self.assertEqual((again['state'], again['applied']), ('applied', True), 'the value showed it had not landed')
        self.assertNotEqual(self.timezone(), 'Asia/Tokyo')

    def test_a_subscription_cli_default_model_is_a_choice_undo_can_restore(self):
        from unittest import mock
        status = {'current': 'claude-code', 'routes': [{'id': 'claude-code', 'name': 'Claude Code', 'ready': True,
                                                        'model': 'opus', 'model_selectable': True}]}
        with mock.patch.object(self.service.main_ai, 'status', return_value=status):
            options = self.settings._main_ai()['model']['options']
            self.assertEqual(options[0]['value'], '', 'the CLI default leads the model choices')
            value, _row = self.settings._normalized('main_ai', 'model', '')
        self.assertEqual(value, '')

    def test_owner_confirmed_change_offers_the_same_undo(self):
        draft = self.draft('current_context', 'enabled', 'on')
        applied = self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
        self.assertTrue(applied['undoable'])
        self.assertIn('되돌리고 싶으면', applied['response'])
        undone = self.change(self.tools(), category='current_context', setting='enabled', value='',
                             undo=draft['draft_id'])
        self.assertEqual(undone['after'], 'off')
        self.assertFalse(self.context()['enabled'])


if __name__ == '__main__':
    import unittest
    unittest.main()
