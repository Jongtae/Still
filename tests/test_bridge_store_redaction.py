"""#369 follow-up: the bridge's own store-level redaction also runs in the service.

Live 2026-11-17 14:52 onwards: every ``public_page_read`` from a Codex Work failed with
"API 인증 정보를 확인할 수 없어 사용하지 않았습니다." (48 failures, no success). #916 records an
outside-content mark before a result reaches the worker, and the mark's text passes
``redact_known_secrets(store, ...)`` in the bridge process. With an encrypted API slot stored,
that read needs the owner's Keychain key, which the process the CLI starts cannot read, so the
read failed closed before its result was returned.
"""
import json
import tempfile
import unittest
from pathlib import Path

from personal_agent.api_credentials import CredentialError
from personal_agent.api_requests import save_slot
from personal_agent.browser_jar import MemoryKey
from personal_agent.cli_browser_relay import BrowserRelay, RelayClient
from personal_agent.current_context import redact_known_secrets
from personal_agent.mcp_bridge import relay_store_redaction
from personal_agent.quickstart_store import QuickStore

SECRET = 'synthetic-encrypted-slot-value-0123456789'


class UnreadableKey(MemoryKey):
    """The owner's key as the CLI-started bridge sees it: present, but not readable."""

    def get(self):
        raise PermissionError('keychain not readable from this process')


class BridgeStoreRedaction(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / 'data'
        self.service_store = QuickStore(root)
        self.service_store.api_slot_key = MemoryKey()
        save_slot(self.service_store, 'holdings', ['api.example.test'], SECRET, encrypted=True)
        self.job = self.service_store.enqueue('fixture request', 'bridge-store-redaction')
        self.bridge_store = QuickStore(root)
        self.bridge_store.api_slot_key = UnreadableKey()

    def relay(self):
        class Capabilities:
            tools, memo = {}, {}
        Capabilities.store = self.service_store

        class Tools:
            capabilities = Capabilities()

            def call(self, name, arguments):
                raise AssertionError('a redaction is not a tool call')
        relay = BrowserRelay(Tools())
        self.addCleanup(relay.close)
        return RelayClient(relay.address)

    def test_without_the_relay_the_bridge_store_fails_closed(self):
        with self.assertRaises(CredentialError):
            redact_known_secrets(self.bridge_store, 'anything')
        with self.assertRaises(CredentialError):
            self.bridge_store.mark_outside_content(self.job, {'operation': 'public_page_read',
                                                              'url': 'https://www.example.test/notice'})

    def test_with_the_relay_a_public_read_is_marked_and_secrets_stay_out(self):
        relay_store_redaction(self.bridge_store, self.relay())
        url = 'https://www.example.test/notice?leak=' + SECRET
        self.bridge_store.mark_outside_content(self.job, {'operation': 'public_page_read', 'url': url})
        (source,) = self.bridge_store.outside_content_sources(self.job)
        self.assertEqual(source['operation'], 'public_page_read')
        self.assertTrue(source['url'].startswith('https://www.example.test/notice'))
        self.assertNotIn(SECRET, json.dumps(source))

    def test_a_relay_failure_still_withholds(self):
        class Down:
            def call(self, name, arguments):
                raise RuntimeError('relay down')
        relay_store_redaction(self.bridge_store, Down())
        with self.assertRaises(RuntimeError):
            self.bridge_store.mark_outside_content(self.job, {'operation': 'public_page_read',
                                                              'url': 'https://www.example.test/'})
        self.assertEqual(self.bridge_store.outside_content_sources(self.job), [])

    def test_without_a_relay_nothing_changes(self):
        relay_store_redaction(self.bridge_store, None)
        with self.assertRaises(CredentialError):
            redact_known_secrets(self.bridge_store, 'anything')
        self.assertEqual(redact_known_secrets(self.service_store, 'k ' + SECRET), 'k [redacted]')


if __name__ == '__main__':
    unittest.main()
