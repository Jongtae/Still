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

    def test_worker_error_text_preserves_paths_and_removes_stored_credentials(self):
        from personal_agent.mcp_bridge import tool_error_result, unexpected_error_result
        redact = bridge_secret_redactor(self.store, None)
        self.store.secret('model_key', 'ordinarycredential')
        self.store.retire_secret_values(['retiredcredential'])
        for make in (tool_error_result, unexpected_error_result):
            for message in ('file /Users/o/a.txt not found', 'risk-free failure'):
                with self.subTest(make=make.__name__, message=message):
                    result, typed = make(RuntimeError(message), 'list_notes', redact)
                    self.assertEqual(result['content'][0]['text'], message)
                    self.assertEqual(result['structuredContent'], typed)
            result, _ = make(RuntimeError('ordinarycredential retiredcredential password=hunter2'), 'list_notes', redact)
            text = result['content'][0]['text']
            for value in ('ordinarycredential', 'retiredcredential', 'hunter2'):
                self.assertNotIn(value, text)
            result, _ = make(RuntimeError(), 'list_notes', redact)
            self.assertEqual(result['content'][0]['text'], 'RuntimeError')

    def test_worker_errors_fail_closed_without_the_stored_secret_pass(self):
        from personal_agent.mcp_bridge import tool_error_result, unexpected_error_result, TOOL_FAILED_TEXT
        def broken(text):
            raise RuntimeError('redactor unavailable')
        for make in (tool_error_result, unexpected_error_result):
            for redact in (None, broken, lambda text: None):
                with self.subTest(make=make.__name__, redact=redact):
                    result, _ = make(RuntimeError(SECRET), 'list_notes', redact)
                    self.assertEqual(result['content'][0]['text'], TOOL_FAILED_TEXT)

    def test_owner_reason_and_progress_keep_paths_and_ordinary_words(self):
        from personal_agent.quickstart_service import AgentService
        text = 'risk-free file /Users/owner/Documents/x.txt not found'
        self.assertEqual(AgentService._redact_reason(text), text)
        self.assertEqual(AgentService._progress_title(text, 'job'), text)

class BridgeErrorPassThrough(unittest.TestCase):
    def test_raising_tool_reaches_worker_through_actual_bridge_dispatch(self):
        from unittest import mock
        from personal_agent import mcp_bridge
        from personal_agent.bounded_execution import AgentOSMcpTools, STRICT_PROFILE
        with tempfile.TemporaryDirectory() as folder:
            store = QuickStore(Path(folder) / 'state')
            store.secret('model_key', 'storedcredential')
            job = store.enqueue('fixture request', 'error-pass-through')
            with store.db() as db:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
            messages = ['file /Users/o/a.txt not found', 'storedcredential password=hunter2', '']
            def dispatch(handle, _tools):
                for message in messages:
                    for kind in (RuntimeError, ValueError):
                        with self.subTest(message=message, kind=kind):
                            with mock.patch.object(AgentOSMcpTools, 'call', side_effect=kind(message)):
                                result = handle('tools/call', {'name': 'list_notes', 'arguments': {}})
                            text = result['content'][0]['text']
                            self.assertTrue(result['isError'])
                            self.assertEqual(store.task_events(job)[-1]['trace']['error'], text)
                            if message == messages[0]:
                                self.assertEqual(text, message)
                            elif message:
                                self.assertNotIn('storedcredential', text)
                                self.assertNotIn('hunter2', text)
                            else:
                                self.assertEqual(text, kind.__name__)
            with mock.patch.object(mcp_bridge, '_serve_stdio', side_effect=dispatch):
                mcp_bridge.serve(store.root, job, profile=STRICT_PROFILE)


if __name__ == '__main__':
    unittest.main()
