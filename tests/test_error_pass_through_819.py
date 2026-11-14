"""#819: a tool's error reaches the owner's worker with its text, after the stored-secret pass."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from personal_agent.agent_runtime import Capabilities, run_agent
from personal_agent.current_context import redact_known_secrets
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore

SECRET = 'telegram-token-0123456789'


def call(call_id, name, **arguments):
    return {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}


class DirectRouteToolError(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        self.store.secret('telegram_token', SECRET)
        with self.store.db() as db:
            db.execute("INSERT INTO jobs(id,request_key,message,status,created,owner_typed) VALUES ('job','k-job',?,'running',?,1)",
                       ('노트 정리해 줘', time.time()))

    def run_with(self, redactor, error=None):
        caps = Capabilities(self.store, ModelAdapter(lambda *a, **k: {}), {}, '', 'job', lambda *event: None,
                            secret_redactor=redactor)

        def failing(name, arguments, *rest, **kwargs):
            raise error or ValueError(f'could not open /Users/owner/notes/a.md with {SECRET}')
        caps.execute = failing
        bodies = []
        replies = [
            {'choices': [{'message': {'content': None, 'tool_calls': [call('1', 'list_notes')]}}]},
            {'choices': [{'message': {'content': '열지 못했어요.'}}]},
            {'choices': [{'message': {'content': '열지 못했어요.'}}]},
        ]

        def transport(*args, **kwargs):
            bodies.append(json.dumps([*args, kwargs], ensure_ascii=False, default=str))
            return replies.pop(0)
        run_agent(ModelAdapter(transport), {'provider': 'compatible', 'endpoint': 'http://127.0.0.1:9', 'model': 'm'},
                  '', [{'role': 'user', 'content': '노트 정리해 줘'}], '', caps, lambda *event: None)
        return bodies[1]

    def test_the_worker_reads_the_real_error_without_the_stored_secret(self):
        sent = self.run_with(lambda text: redact_known_secrets(self.store, text))
        self.assertIn('could not open /Users/owner/notes/a.md with [redacted]', sent)
        self.assertNotIn(SECRET, sent)

    def test_a_transient_os_error_reaches_the_worker_with_its_message(self):
        sent = self.run_with(lambda text: redact_known_secrets(self.store, text),
                             OSError(f'connection reset while reading /Users/owner/notes/a.md ({SECRET})'))
        self.assertIn('connection reset while reading /Users/owner/notes/a.md ([redacted])', sent)
        self.assertNotIn(SECRET, sent)

    def test_a_failing_stored_secret_pass_withholds_the_error(self):
        def broken(text):
            raise OSError('ledger unreadable')
        sent = self.run_with(broken)
        self.assertNotIn(SECRET, sent)
        self.assertNotIn('/Users/owner/notes/a.md', sent)


    def test_an_unreadable_stored_value_withholds_the_error(self):
        """Codex review on #839: a stored value that cannot be read never lets the error text through."""
        from unittest import mock
        real = QuickStore.secret

        def failing(store, key, *args, **kwargs):
            if key == 'telegram_token':
                raise OSError('secrets file busy')
            return real(store, key, *args, **kwargs)
        self.store._known_secret_material = None
        with mock.patch.object(QuickStore, 'secret', failing):
            sent = self.run_with(lambda text: redact_known_secrets(self.store, text))
        self.assertNotIn(SECRET, sent)
        self.assertNotIn('/Users/owner/notes/a.md', sent)


if __name__ == '__main__':
    unittest.main()
