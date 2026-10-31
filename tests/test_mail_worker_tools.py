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
from personal_agent.gmail import GMAIL_CONNECTOR_ID, MAIL_CONTENT_CHARS, MESSAGES_ENDPOINT, PROFILE_ENDPOINT, GmailError, GmailReauthenticationRequired, GmailWorkerReader
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
        self.responses.extend([message('m_2', SIGNUP_HTML), {'emailAddress': 'owner+mail@example.test'}])
        result = reader.read('m_2')
        self.assertIn('Member number:', result['content'])
        self.assertIn('4401234', result['content'])
        self.assertNotIn('<b>', result['content'])
        self.assertNotIn('color:red', result['content'], 'style text is not body text')
        self.assertNotIn(b64(SIGNUP_HTML)[:24], json.dumps(result), 'the encoded form does not reach the worker')
        self.assertEqual((result['message_id'], result['thread_id'], result['truncated']), ('m_2', 't_m_2', False))
        self.assertEqual([call[1:3] for call in self.calls],
                         [(MESSAGES_ENDPOINT + '/m_2', {'format': 'full'}), (PROFILE_ENDPOINT, {})])
        link = 'https://mail.google.com/mail/u/owner%2Bmail@example.test/#all/m_2'
        self.assertEqual(result['link'], link)
        self.assertEqual(result['sources'], [link])

    def test_a_long_body_is_bounded_and_says_so(self):
        reader = self.reader()
        self.responses.append({'id': 'm_3', 'threadId': 't_3',
                               'payload': {'mimeType': 'text/plain', 'body': {'data': b64('x' * (MAIL_CONTENT_CHARS + 10))}}})
        self.responses.append({'emailAddress': 'owner@example.test'})
        result = reader.read('m_3')
        self.assertEqual(len(result['content']), MAIL_CONTENT_CHARS)
        self.assertTrue(result['truncated'])

    def test_search_returns_every_match_with_what_tells_them_apart(self):
        reader = self.reader()
        self.responses.extend([{'messages': [{'id': 'm_1'}, {'id': 'm_2'}]},
                               self.metadata('m_1', 't_1'), self.metadata('m_2', 't_2'),
                               {'emailAddress': 'owner@example.test'}])
        result = reader.search('subject:club', limit=2)
        self.assertEqual([row['message_id'] for row in result['messages']], ['m_1', 'm_2'])
        self.assertEqual(set(result['messages'][0]), {'message_id', 'thread_id', 'subject', 'sender', 'date', 'link'})
        self.assertEqual([row['link'] for row in result['messages']],
                         ['https://mail.google.com/mail/u/owner@example.test/#all/' + mid for mid in ('m_1', 'm_2')])
        self.assertEqual([call[1:3] for call in self.calls],
                         [(MESSAGES_ENDPOINT, {'q': 'subject:club', 'maxResults': 2, 'includeSpamTrash': False}),
                          (MESSAGES_ENDPOINT + '/m_1', {'format': 'metadata', 'metadataHeaders': ['Subject', 'From', 'Date']}),
                          (MESSAGES_ENDPOINT + '/m_2', {'format': 'metadata', 'metadataHeaders': ['Subject', 'From', 'Date']}),
                          (PROFILE_ENDPOINT, {})])
        self.assertTrue(result['truncated'], 'a full page may not be every match')

    def test_an_unconnected_owner_is_a_typed_connector_error(self):
        with self.assertRaises(GmailError) as raised:
            GmailWorkerReader(self.gmail, 'owner-a').search('anything')
        self.assertEqual(raised.exception.reason, 'connection_required')

    def test_profile_is_cached_across_tools_and_refetched_after_reconnect(self):
        reader = self.reader()
        self.responses.extend([{'messages': [{'id': 'm_1'}]}, self.metadata('m_1', 't_1'),
                               {'emailAddress': 'first@example.test'}, message('m_1', 'body')])
        before = {path: path.read_bytes() for path in Path(self.temp.name).rglob('*') if path.is_file()}
        self.assertEqual(reader.search('club')['messages'][0]['link'],
                         'https://mail.google.com/mail/u/first@example.test/#all/m_1')
        # A second reader shares the connector's in-memory cache.
        self.assertEqual(GmailWorkerReader(self.gmail, 'owner-a').read('m_1')['sources'],
                         ['https://mail.google.com/mail/u/first@example.test/#all/m_1'])
        self.assertEqual(sum(call[1] == PROFILE_ENDPOINT for call in self.calls), 1)
        self.assertEqual(before, {path: path.read_bytes() for path in Path(self.temp.name).rglob('*') if path.is_file()})
        revision = self.gmail.status('owner-a')['connection_revision']
        self.gmail.disconnect('owner-a', lambda _: None)
        self.connect(access_token='replacement')
        self.assertNotEqual(revision, self.gmail.status('owner-a')['connection_revision'])
        self.responses.extend([message('m_1', 'body'), {'emailAddress': 'second@example.test'}])
        self.assertEqual(reader.read('m_1')['link'], 'https://mail.google.com/mail/u/second@example.test/#all/m_1')
        self.assertEqual(sum(call[1] == PROFILE_ENDPOINT for call in self.calls), 2)

    def test_profile_cache_is_separate_for_each_owner(self):
        self.connect('owner-a')
        self.connect('owner-b')
        for owner in ('owner-a', 'owner-b'):
            self.responses.extend([message('m_1', 'body'), {'emailAddress': owner + '@example.test'}])
            result = GmailWorkerReader(self.gmail, owner).read('m_1')
            self.assertEqual(result['link'], 'https://mail.google.com/mail/u/' + owner + '@example.test/#all/m_1')
        self.assertEqual(sum(call[1] == PROFILE_ENDPOINT for call in self.calls), 2)

    def test_unknown_or_failed_profile_falls_back_without_failing_mail(self):
        profiles = [{}, None, {'status_code': 503}, {'emailAddress': None}]
        profiles += [{'emailAddress': value} for value in
                     ('', 'a@', '@b', 'a@@b', 'a b@example.test', 'a\t@example.test',
                      'a\n@example.test', 'a/b@example.test', 'a#b@example.test',
                      'a?b@example.test', 'a\x00@example.test', 'a' * 250 + '@b.test')]
        for profile in profiles:
            with self.subTest(profile=profile):
                reader = self.reader()
                self.responses.extend([message('m_1', 'body'), profile,
                                       {'messages': [{'id': 'm_1'}]}, self.metadata('m_1', 't_1')])
                result = reader.read('m_1')
                fallback = 'https://mail.google.com/mail/#all/m_1'
                self.assertEqual(result['link'], fallback)
                self.assertEqual(result['sources'], [fallback])
                self.assertEqual(reader.search('club')['messages'][0]['link'], fallback)
                self.assertEqual(self.responses, [])
                self.gmail.disconnect('owner-a', lambda _: None)

    def test_profile_transport_failure_is_asked_again(self):
        reader = self.reader()
        transport = self.gmail.transport

        def unavailable(method, endpoint, params, headers):
            if endpoint == PROFILE_ENDPOINT:
                self.calls.append((method, endpoint, params, headers))
                raise OSError('offline')
            return transport(method, endpoint, params, headers)

        self.gmail.transport = unavailable
        self.responses.extend([message('m_1', 'body'), message('m_1', 'body')])
        for _ in range(2):
            self.assertEqual(reader.read('m_1')['link'], 'https://mail.google.com/mail/#all/m_1')
        self.assertEqual(sum(call[1] == PROFILE_ENDPOINT for call in self.calls), 2)

    def test_profile_reauthentication_uses_the_existing_connector_error(self):
        reader = self.reader()
        self.responses.extend([message('m_1', 'body'), {'status_code': 401}])
        with self.assertRaises(GmailReauthenticationRequired):
            reader.read('m_1')
        self.assertEqual(self.gmail.status('owner-a')['state'], 'reauth_required')

    def test_link_encodes_path_parts(self):
        self.assertEqual(GmailWorkerReader._link('id/with?#%', 'owner+mail@example.test'),
                         'https://mail.google.com/mail/u/owner%2Bmail@example.test/#all/id%2Fwith%3F%23%25')


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
                        'content': 'Member number 4401234', 'truncated': False,
                        'link': 'https://mail.google.com/mail/u/private-owner@example.test/#all/m_1',
                        'sources': ['https://mail.google.com/mail/u/private-owner@example.test/#all/m_1']}

        caps = self.capabilities(Reader())
        found = caps.execute('mail_search', {'query': 'club'})
        read = caps.execute('mail_read', {'message_id': 'm_1'})
        self.assertIn('owner-mail', caps.private_provenance)
        records = json.dumps([_evidence_detail('mail_search', found), _evidence_detail('mail_read', read)])
        for private in ('Private subject', 'shop@example.test', '4401234', 'private-owner@example.test', 'https://mail.google.com'):
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
        connector.responses.extend([message('m_2', SIGNUP_HTML), {'emailAddress': 'owner@example.test'}])
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
        self.assertEqual(shown['r']['link'], 'https://mail.google.com/mail/u/owner@example.test/#all/m_2')
        self.assertEqual(shown['r']['sources'], [shown['r']['link']])
        [facts] = judged
        self.assertIn('4401234', json.dumps(facts, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
