"""#778: a run state's "plain" values are not secrets; everything else long stays redacted for good."""
import json
import unittest

from personal_agent import api_requests
from personal_agent.api_credentials import ApiCredentials
from personal_agent.current_context import redact_known_secrets
import test_code_run_secrets as secrets_case

PATH = '/v1/trading/order-cash-endpoint'
TOKEN = 'access-token-' + 'a' * 30
OLD_TOKEN = 'access-token-' + 'b' * 30


class PlainState(unittest.TestCase):
    setUp = secrets_case.Storage.setUp
    save = secrets_case.Storage.save

    def write(self, state):
        slot = api_requests.slots(self.store)['example-broker']
        self.assertTrue(ApiCredentials(self.store).write_state('example-broker', slot, json.dumps(state)))

    def shown(self, text):
        return redact_known_secrets(self.store, text)

    def test_a_plain_value_is_shown_and_a_token_outside_plain_is_not(self):
        self.save()
        self.write({'access_token': TOKEN, 'plain': {'order': PATH, 'log': [{'path': PATH}]}})
        self.assertEqual(self.shown(f'POST {PATH} with {TOKEN}'), f'POST {PATH} with [redacted]')

    def test_a_value_retired_by_an_earlier_state_is_shown_once_declared_plain(self):
        # The live sequence: a path saved outside plain, then rewritten away, stays redacted for good.
        self.save()
        self.write({'access_token': OLD_TOKEN, 'last_order': PATH})
        self.write({'access_token': TOKEN})
        self.assertEqual(self.shown(PATH), '[redacted]')
        self.write({'access_token': TOKEN, 'plain': {'order': PATH}})
        self.assertEqual(self.shown(f'{PATH} {TOKEN} {OLD_TOKEN}'), f'{PATH} [redacted] [redacted]')
        # Rewriting again keeps the plain value out of the ledger.
        self.write({'access_token': TOKEN})
        self.assertEqual(self.shown(PATH), PATH)

    def test_a_value_also_outside_plain_stays_redacted(self):
        self.save()
        self.write({'access_token': TOKEN, 'plain': {'copy': TOKEN}})
        self.assertEqual(self.shown(TOKEN), '[redacted]')

    def test_a_credential_value_in_plain_stays_redacted(self):
        self.save()
        secret = secrets_case.VALUES['APP_SECRET']
        self.write({'plain': {'key': secret}})
        self.assertEqual(self.shown(f'key {secret}'), 'key [redacted]')

    def test_declaring_plain_releases_only_its_own_marks(self):
        self.save()
        self.write({'access_token': OLD_TOKEN, 'last_order': PATH})
        self.write({'access_token': TOKEN, 'plain': {'order': PATH}})
        api_requests.remove_slot(self.store, 'example-broker')
        # The removed set's tokens stay retired; its plain value was never retired.
        self.assertEqual(self.shown(f'{OLD_TOKEN} {TOKEN} {PATH}'), f'[redacted] [redacted] {PATH}')

    def test_a_state_without_plain_keeps_the_length_rule(self):
        self.save()
        self.write({'access_token': TOKEN, 'expires': '2026-11-10 17:19:54', 'plain': 'not-an-object-' + 'c' * 20})
        self.assertEqual(self.shown(f'{TOKEN} 2026-11-10 17:19:54'), '[redacted] 2026-11-10 17:19:54')
        self.assertEqual(self.shown('not-an-object-' + 'c' * 20), 'not-an-object-' + 'c' * 20)


    def test_a_credential_copied_into_plain_stays_redacted_after_it_is_retired(self):
        # Codex P1 on #787: code copies an in-code credential into plain while it is active; the owner then
        # removes that credential (its value retired by the credential path), and the state is rewritten.
        self.save()
        copied = 'in-code-credential-' + 'd' * 20
        self.write({'plain': {'key': copied}})
        self.store.retire_secret_values([copied])
        self.write({'plain': {'key': copied}})
        self.assertEqual(self.shown(f'key {copied}'), 'key [redacted]')

    def test_a_value_a_state_and_a_credential_both_retired_is_not_released(self):
        self.save()
        self.write({'access_token': OLD_TOKEN, 'last_order': PATH})
        self.write({'access_token': TOKEN})
        self.store.retire_secret_values([PATH])
        self.write({'access_token': TOKEN, 'plain': {'order': PATH}})
        self.assertEqual(self.shown(PATH), '[redacted]')

    def test_a_mark_recorded_before_origins_were_kept_is_not_released(self):
        self.save()
        self.write({'access_token': OLD_TOKEN, 'last_order': PATH})
        self.write({'access_token': TOKEN})
        ledger = json.loads(self.store.retired_secrets_path.read_text())
        ledger.pop(self.store.STATE_MARKS)
        self.store.write_private(self.store.retired_secrets_path, json.dumps(ledger))
        self.write({'access_token': TOKEN, 'plain': {'order': PATH}})
        self.assertEqual(self.shown(PATH), '[redacted]')


if __name__ == '__main__':
    unittest.main()
