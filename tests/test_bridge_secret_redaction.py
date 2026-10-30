"""#369: the bridge's stored-secret redaction runs in the service, which can read the owner's key.

Live: under the CLI that starts it, the bridge could not read the Keychain key of encrypted API slot
values, so every redaction there failed closed: 1,069 recorded step hosts read ``[redacted]`` and all
168 ``method_gap`` notes were withheld as empty (``invalid_call``).
"""
import tempfile
import unittest
from pathlib import Path

from personal_agent.agent_runtime import ToolError
from personal_agent.cli_browser_relay import SECRET_REDACTION, BrowserRelay, RelayClient
from personal_agent.mcp_bridge import bridge_secret_redactor
from personal_agent.quickstart_store import QuickStore

SECRET = 'sk-synthetic-model-key-0123456789'


class RelayedRedaction(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')
        self.store.secret('model_key', SECRET)

    def relay(self, store):
        class Capabilities:
            tools, memo = {}, {}
        Capabilities.store = store

        class Tools:
            capabilities = Capabilities()

            def call(self, name, arguments):
                raise AssertionError('a redaction is not a tool call')
        relay = BrowserRelay(Tools())
        self.addCleanup(relay.close)
        return RelayClient(relay.address)

    def test_the_service_redacts_with_its_own_store(self):
        redact = bridge_secret_redactor(QuickStore(tempfile.mkdtemp()), self.relay(self.store))
        self.assertEqual(redact(f'host gmail.googleapis.com key {SECRET}'), 'host gmail.googleapis.com key [redacted]')

    def test_a_failure_in_the_service_raises_so_the_caller_withholds(self):
        class Broken:
            def secret(self, *args, **kwargs):
                raise RuntimeError('key unavailable')

            def secret_items(self, *args):
                raise RuntimeError('key unavailable')
        redact = bridge_secret_redactor(self.store, self.relay(Broken()))
        with self.assertRaises(ToolError):
            redact('anything')

    def test_capabilities_keep_text_when_it_works_and_withhold_when_it_does_not(self):
        from personal_agent.agent_runtime import Capabilities
        working = Capabilities(self.store, None, {}, '', 'job', lambda *a: None,
                               secret_redactor=bridge_secret_redactor(self.store, self.relay(self.store)))
        self.assertEqual(working.judgment_text('gmail.googleapis.com'), 'gmail.googleapis.com')

        class Down:
            def call(self, name, arguments):
                raise ToolError('relay down', 'browser_relay_unavailable')
        withheld = Capabilities(self.store, None, {}, '', 'job', lambda *a: None,
                                secret_redactor=bridge_secret_redactor(self.store, Down()))
        self.assertEqual(withheld.judgment_text('gmail.googleapis.com'), '[redacted]')

    def test_without_a_relay_it_runs_in_the_bridge(self):
        self.assertEqual(bridge_secret_redactor(self.store, None)(f'k {SECRET}'), 'k [redacted]')

    def test_a_request_without_text_or_key_is_refused(self):
        client = self.relay(self.store)
        with self.assertRaises(ValueError):  # a typed refusal (ExecutionError / ToolError), never a value
            client.call(SECRET_REDACTION, {'text': 3})
        wrong = RelayClient(client.directory)
        wrong._key = lambda: 'not-the-key'
        with self.assertRaises(ToolError):
            wrong.call(SECRET_REDACTION, {'text': SECRET})


if __name__ == '__main__':
    unittest.main()
