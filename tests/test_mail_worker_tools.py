"""#150: the worker's mail_search / mail_read.

Evidence class: model-free unit tests.  The Gmail connector runs with a fake
transport returning Gmail API shapes (base64url bodies in MIME parts), and one
scripted worker run uses an injected model transport.  They prove the worker
receives decoded text and that records keep ids and counts only; they do not
prove how a live model words its answer.
"""
import base64
import json
import tempfile
import unittest
from pathlib import Path

from personal_agent.agent_runtime import DEFINITIONS, Capabilities, _evidence_detail, run_agent
from personal_agent.gmail import GMAIL_CONNECTOR_ID, MAIL_CONTENT_CHARS, GmailError, GmailWorkerReader
from personal_agent.information_use import CATEGORY_NAMES
from personal_agent.manifests import HOST_TOOLS
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore
import test_gmail



def b64(text):
    return base64.urlsafe_b64encode(text.encode()).rstrip(b'=').decode()


def message(message_id, html):
    """A Gmail ``format=full`` response whose only body is base64url HTML, as a sign-up notice often is."""
    return {'id': message_id, 'threadId': 't_' + message_id,
            'payload': {'mimeType': 'multipart/alternative',
                        'parts': [{'mimeType': 'text/html', 'body': {'data': b64(html)}}]}}


SIGNUP_HTML = ('<html><head><style>p{color:red}</style></head><body>'
               '<p>Welcome to the club.</p><p>Member number: <b>4401234</b></p></body></html>')


class GmailWorkerReaderTests(unittest.TestCase):
    # The connector fixture of the Gmail suite: real OAuth state, a fake transport, GET-only teardown.
    setUp = test_gmail.GmailConnectorTests.setUp
    tearDown = test_gmail.GmailConnectorTests.tearDown
    begin = test_gmail.GmailConnectorTests.begin
    connect = test_gmail.GmailConnectorTests.connect
    metadata = staticmethod(test_gmail.GmailConnectorTests.metadata)

    def reader(self):
        self.connect()
        return GmailWorkerReader(self.gmail, 'owner-a')

    def test_a_base64url_html_body_reaches_the_worker_as_text(self):
        reader = self.reader()
        self.responses.append(message('m_2', SIGNUP_HTML))
        result = reader.read('m_2')
        self.assertIn('Member number:', result['content'])
        self.assertIn('4401234', result['content'])
        self.assertNotIn('<b>', result['content'])
        self.assertNotIn('color:red', result['content'], 'style text is not body text')
        self.assertNotIn(b64(SIGNUP_HTML)[:24], json.dumps(result), 'the encoded form does not reach the worker')
        self.assertEqual((result['message_id'], result['thread_id'], result['truncated']), ('m_2', 't_m_2', False))
        self.assertEqual(self.calls[-1][2], {'format': 'full'})

    def test_a_long_body_is_bounded_and_says_so(self):
        reader = self.reader()
        self.responses.append({'id': 'm_3', 'threadId': 't_3',
                               'payload': {'mimeType': 'text/plain', 'body': {'data': b64('x' * (MAIL_CONTENT_CHARS + 10))}}})
        result = reader.read('m_3')
        self.assertEqual(len(result['content']), MAIL_CONTENT_CHARS)
        self.assertTrue(result['truncated'])

    def test_search_returns_every_match_with_what_tells_them_apart(self):
        reader = self.reader()
        self.responses.extend([{'messages': [{'id': 'm_1'}, {'id': 'm_2'}]},
                               self.metadata('m_1', 't_1'), self.metadata('m_2', 't_2')])
        result = reader.search('subject:club', limit=2)
        self.assertEqual([row['message_id'] for row in result['messages']], ['m_1', 'm_2'])
        self.assertEqual(set(result['messages'][0]), {'message_id', 'thread_id', 'subject', 'sender', 'date'})
        self.assertTrue(result['truncated'], 'a full page may not be every match')

    def test_an_unconnected_owner_is_a_typed_connector_error(self):
        with self.assertRaises(GmailError) as raised:
            GmailWorkerReader(self.gmail, 'owner-a').search('anything')
        self.assertEqual(raised.exception.reason, 'connection_required')


class MailCapabilityTests(unittest.TestCase):
    def capabilities(self, mail):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Capabilities(QuickStore(temp.name), None, {}, None, 'job-1', {}, mail=mail)

    def test_without_a_gmail_connection_a_read_is_setup_required(self):
        class Unconnected:
            def search(self, query):
                raise GmailError('connection_required')

        for mail, name, args in ((None, 'mail_read', {'message_id': 'm_1'}),
                                 (Unconnected(), 'mail_search', {'query': 'x'})):
            with self.subTest(name=name):
                result = self.capabilities(mail).execute(name, args)
                self.assertTrue(result['needs_setup'])
                self.assertEqual(result['requires'], GMAIL_CONNECTOR_ID)

    def test_a_read_is_owner_mail_provenance_and_its_record_keeps_no_mail_text(self):
        class Reader:
            def search(self, query):
                return {'messages': [{'message_id': 'm_1', 'thread_id': 't', 'subject': 'Private subject',
                                      'sender': 'shop@example.test', 'date': 'Fri, 17 Oct 2026'}], 'truncated': False}

            def read(self, message_id):
                return {'message_id': message_id, 'thread_id': 't', 'mime_type': 'text/html',
                        'content': 'Member number 4401234', 'truncated': False, 'sources': ['Gmail message m_1']}

        caps = self.capabilities(Reader())
        found = caps.execute('mail_search', {'query': 'club'})
        read = caps.execute('mail_read', {'message_id': 'm_1'})
        self.assertIn('owner-mail', caps.private_provenance)
        records = json.dumps([_evidence_detail('mail_search', found), _evidence_detail('mail_read', read)])
        for private in ('Private subject', 'shop@example.test', '4401234'):
            self.assertNotIn(private, records)
        self.assertIn('m_1', records)

    def test_the_tools_are_read_only_service_held_mail_reads(self):
        for name in ('mail_search', 'mail_read'):
            tool = HOST_TOOLS[name]
            self.assertEqual((tool.write, tool.read_only_surface, tool.service_held, tool.reads), (False, True, True, 'mail'))
        self.assertIn('mail', CATEGORY_NAMES)
        definition = next(tool for tool in DEFINITIONS if tool['function']['name'] == 'mail_read')
        self.assertEqual(definition['function']['parameters']['required'], ['message_id'])


class MailReadReachesTheWorkerTests(unittest.TestCase):
    """The decoded body reaches the worker and the outcome judgment through the same loop as any read."""

    def test_decoded_text_reaches_the_worker_and_the_judgment(self):
        import test_agency_loop as loop
        connector = test_gmail.GmailConnectorTests('test_search_is_bounded_metadata_only_with_exact_get_endpoints_and_parameters')
        connector.setUp()
        self.addCleanup(connector.tearDown)
        connector.connect()
        connector.responses.append(message('m_2', SIGNUP_HTML))
        store = QuickStore(Path(self.enterContext(tempfile.TemporaryDirectory())) / 'data')
        judged = []

        def judge(facts):
            judged.append(facts)
            return True
        script = loop.Script({'tool_calls': [loop.call('r', 'mail_read', message_id='m_2')]},
                             loop.finish('f', 'r', summary='member number'))
        caps = Capabilities(store, ModelAdapter(script), loop.CFG, '', 'job', lambda *event: None,
                            network=loop.Network(), judgments=loop.judgments(judge),
                            mail=GmailWorkerReader(connector.gmail, 'owner-a'))
        run_agent(caps.adapter, loop.CFG, '', [{'role': 'user', 'content': 'what is my member number?'}], '', caps, caps.record)
        shown = {m.get('tool_call_id'): json.loads(m['content']) for m in script.bodies[-1]['messages'] if m['role'] == 'tool'}
        self.assertIn('4401234', shown['r'].get('content', ''), shown)
        [facts] = judged
        self.assertIn('4401234', json.dumps(facts, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
