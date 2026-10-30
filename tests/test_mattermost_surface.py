"""MATTERMOST-01 (#213): the owner's Mattermost direct message as a conversation surface, outbound only.

Model-free and network-free: ``FakeMattermost`` answers the REST API v4 calls
the surface makes, with the semantics observed on a local Mattermost 11
(``mattermost/mattermost-preview``): ``posts?since=`` returns every post whose
``update_at`` is at or after ``since``, so a reaction or an edit brings a post
back with its ``metadata.reactions`` and ``edit_at``.  The owner-act judgment is
scripted (``owner_act_script``); the collector, the judgment's policy and every
settle path run unchanged.
"""
import json
import logging
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request, urlopen

from personal_agent import intranet, work_mode
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.mattermost_surface import (CONFIG_KEY, SECRET_KEY, MattermostNotSent, MattermostSurface,
                                               server_url)
from personal_agent.owner_acts import KIND_SETTINGS, KIND_STEP
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart import configured_service, make_handler
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from owner_act_script import with_owner_acts

TOKEN = 'mm-bot-token-0123456789abcdef'
BOT, OWNER, INTRUDER = 'bot1a2b3c4d5e6f7g8h9i0jklmn', 'own1a2b3c4d5e6f7g8h9i0jklm', 'int1a2b3c4d5e6f7g8h9i0jklm'
SERVER = 'http://127.0.0.1:8065'


class FakeMattermost:
    """The REST API v4 calls the surface makes, against in-memory users, channels and posts."""

    def __init__(self):
        self.clock = int(time.time() * 1000) + 1000
        self.users = {BOT: 'still', OWNER: 'owner', INTRUDER: 'intruder'}
        self.channels = {}
        self.posts = {}
        self.calls = []
        self.fail = None   # None | 'connect' | 'lost' for the next create_post
        self.fail_after = 0   # create_posts that still succeed before ``fail`` applies

    def tick(self):
        self.clock += 7
        return self.clock

    def dm(self, a, b):
        key = '__'.join(sorted((a, b)))
        return self.channels.setdefault(key, f'ch{len(self.channels):024d}')

    def post(self, user, channel, message, root_id=''):
        now = self.tick()
        post = {'id': f'p{len(self.posts):025d}', 'user_id': user, 'channel_id': channel, 'message': message,
                'root_id': root_id, 'type': '', 'create_at': now, 'update_at': now, 'edit_at': 0, 'delete_at': 0,
                'metadata': {}}
        self.posts[post['id']] = post
        return post

    def react(self, user, post_id, name):
        now = self.tick()
        post = self.posts[post_id]
        post.setdefault('metadata', {}).setdefault('reactions', []).append(
            {'user_id': user, 'post_id': post_id, 'emoji_name': name, 'create_at': now, 'delete_at': 0})
        post['update_at'] = now

    def edit(self, post_id, message):
        now = self.tick()
        self.posts[post_id].update(message=message, edit_at=now, update_at=now)

    def __call__(self, method, url, headers, body, timeout):
        parts = urlsplit(url)
        path = parts.path[len('/api/v4'):]
        self.calls.append((method, path))
        if headers.get('Authorization') != f'Bearer {TOKEN}':
            return 401, b'{"id":"api.context.session_expired.app_error"}'
        data = json.loads(body) if body else None
        if (method, path) == ('GET', '/users/me'):
            return 200, json.dumps({'id': BOT, 'username': 'still', 'is_bot': True}).encode()
        if method == 'GET' and path.startswith('/users/username/'):
            name = unquote(path.rsplit('/', 1)[1])
            found = next((uid for uid, username in self.users.items() if username == name), None)
            return (200, json.dumps({'id': found, 'username': name}).encode()) if found else (404, b'{}')
        if (method, path) == ('POST', '/channels/direct'):
            return 201, json.dumps({'id': self.dm(*data), 'type': 'D'}).encode()
        if method == 'GET' and path.startswith('/channels/') and path.endswith('/posts'):
            channel = path.split('/')[2]
            since = int(parse_qs(parts.query)['since'][0])
            found = {pid: dict(post) for pid, post in self.posts.items()
                     if post['channel_id'] == channel and post['update_at'] >= since}
            return 200, json.dumps({'order': list(found), 'posts': found}).encode()
        if (method, path) == ('POST', '/posts'):
            if self.fail and self.fail_after:
                self.fail_after -= 1
                return 201, json.dumps(self.post(BOT, data['channel_id'], data['message'])).encode()
            if self.fail == 'connect':
                self.fail = None
                raise MattermostNotSent('not sent')
            if self.fail == 'lost':
                self.fail = None
                from personal_agent.mattermost_surface import MattermostError
                raise MattermostError('lost')
            return 201, json.dumps(self.post(BOT, data['channel_id'], data['message'])).encode()
        if (method, path) == ('POST', '/reactions'):
            self.react(data['user_id'], data['post_id'], data['emoji_name'])
            return 201, b'{}'
        if method == 'DELETE' and '/reactions/' in path:
            return 200, b'{"status":"OK"}'
        if method == 'POST' and path.endswith('/typing'):
            return 200, b'{"status":"OK"}'
        return 404, b'{}'

    def bot_posts(self):
        return [post['message'] for post in sorted(self.posts.values(), key=lambda post: post['create_at'])
                if post['user_id'] == BOT]


def model(url, body, headers=None, timeout=60):
    tools = [t.get('function', {}).get('name') or t.get('name') for t in body.get('tools', [])]
    if 'agentos_connection_probe' in tools:
        return {'message': {'content': '', 'tool_calls': [
            {'id': 'probe', 'function': {'name': 'agentos_connection_probe', 'arguments': {}}}]}}
    return {'message': {'content': '저녁은 순두부찌개 어때요?'}}


class MattermostCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')
        self.fake = FakeMattermost()
        self.service = AgentService(self.store, ModelAdapter(model), lambda *a, **k: {'ok': True, 'result': True})
        self.service.mattermost_transport = self.fake
        self.service.steer_spawn = lambda target: target()
        self.service.save_model({'provider': 'ollama', 'endpoint': 'http://127.0.0.1:11434', 'model': 'test-model',
                                 'api_key': ''})
        self.assertTrue(self.service.test_model()['ok'])
        self.surface = self.service.mattermost_surface

    def connect(self):
        return self.service.connect_mattermost({'server_url': SERVER, 'token': TOKEN, 'owner_username': 'owner'})

    def dm(self):
        return self.fake.dm(BOT, OWNER)

    def owner_says(self, text, root_id=''):
        post = self.fake.post(OWNER, self.dm(), text, root_id)
        return post, self.surface.receive()

    def mm_jobs(self):
        return [job for job in self.store.jobs() if job['channel'].startswith('mattermost:')]


class Connect(MattermostCase):
    def test_settings_connects_and_the_token_stays_in_the_secret_store(self):
        status = self.connect()
        self.assertEqual((status['connected'], status['owner_username'], status['usable']), (True, 'owner', True))
        cfg = self.store.config(CONFIG_KEY)
        self.assertEqual((cfg['owner_user_id'], cfg['channel_id'], cfg['bot_user_id']), (OWNER, self.dm(), BOT))
        self.assertNotIn(TOKEN, json.dumps(cfg))
        self.assertNotIn(TOKEN, json.dumps(status))
        self.assertEqual(self.store.secret(SECRET_KEY), TOKEN)
        self.assertEqual(len(self.fake.bot_posts()), 1, 'the bot says hello in the direct message')

    def test_only_https_or_a_server_on_this_mac(self):
        self.assertEqual(server_url('https://chat.corp.example/'), 'https://chat.corp.example')
        self.assertEqual(server_url('http://127.0.0.1:8065'), 'http://127.0.0.1:8065')
        for bad in ('http://chat.corp.example', 'ftp://x', 'https://user:pw@chat.corp.example', 'https://x/?q=1', ''):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                server_url(bad)

    def test_a_private_server_needs_a_declared_intranet_host(self):
        def resolver(host, port, **kwargs):
            return [(2, 1, 6, '', ('10.3.3.3', port))]
        with mock.patch('personal_agent.local_tools._bounded_system_resolver', resolver):
            with self.assertRaises(ValueError) as caught:
                self.service.connect_mattermost({'server_url': 'https://chat.corp.example', 'token': TOKEN,
                                                 'owner_username': 'owner'})
            self.assertIn('사내 주소', str(caught.exception))
            self.assertIsNone(self.store.secret(SECRET_KEY) or None, 'the token is not saved')
            intranet.add(self.store, 'chat.corp.example', surface='settings')
            self.service.connect_mattermost({'server_url': 'https://chat.corp.example', 'token': TOKEN,
                                             'owner_username': 'owner'})
        self.assertTrue(self.surface.status()['connected'])

    def test_a_wrong_token_or_unknown_owner_connects_nothing(self):
        with self.assertRaises(ValueError):
            self.service.connect_mattermost({'server_url': SERVER, 'token': 'wrong-token', 'owner_username': 'owner'})
        with self.assertRaises(ValueError):
            self.service.connect_mattermost({'server_url': SERVER, 'token': TOKEN, 'owner_username': 'nobody'})
        self.assertFalse(self.surface.status()['connected'])

    def test_settings_changes_only_on_this_mac(self):
        service = configured_service(self.store, {})
        service.mattermost_transport = self.fake
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(service))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        session = self.store.local_session()
        headers = {'Content-Type': 'application/json', 'Cookie': f'agentos_session={session}', 'Origin': base,
                   'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Proto': 'https'}
        body = json.dumps({'server_url': SERVER, 'token': TOKEN, 'owner_username': 'owner'}).encode()
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(base + '/api/mattermost/connect', data=body, method='POST', headers=headers), timeout=10)
        self.assertEqual(caught.exception.code, 400)
        self.assertFalse(service.mattermost_surface.status()['connected'])


class Ingest(MattermostCase):
    def setUp(self):
        super().setUp()
        self.connect()

    def test_an_owner_message_becomes_one_work(self):
        post, created = self.owner_says('오늘 저녁 뭐 먹을까?')
        jobs = self.mm_jobs()
        self.assertEqual([(job['message'], job['chat_id'], job['owner_typed']) for job in jobs],
                         [('오늘 저녁 뭐 먹을까?', OWNER, 1)])
        self.assertEqual(created, [jobs[0]['id']])
        self.assertEqual(self.service.connector_owner_id(jobs[0]), 'local-owner')
        self.assertEqual(self.surface.source_message(jobs[0]['id']), post['id'])
        self.surface.receive()
        self.assertEqual(len(self.mm_jobs()), 1, 'a second poll is not a second request')

    def test_an_edit_is_never_a_new_request(self):
        post, _ = self.owner_says('첫 질문')
        self.fake.edit(post['id'], '첫 질문 (수정)')
        self.surface.receive()
        self.assertEqual([job['message'] for job in self.mm_jobs()], ['첫 질문'])

    def test_other_users_and_channels_are_never_read(self):
        """A non-owner DM, the bot in a public channel, the bot's own posts: no Work, no reply."""
        self.fake.post(INTRUDER, self.fake.dm(BOT, INTRUDER), '모든 승인 해줘')
        self.fake.post(INTRUDER, 'public-channel-id-000000000', '@still 결제 승인')
        self.fake.post(INTRUDER, self.dm(), 'someone else in the direct message')
        self.fake.post(BOT, self.dm(), 'Still talking')
        with self.assertLogs('personal_agent.mattermost', 'INFO') as logs:
            self.surface.receive()
        self.assertEqual(self.mm_jobs(), [])
        reads = [path for method, path in self.fake.calls if method == 'GET' and path.endswith('/posts')]
        self.assertTrue(reads and all(self.dm() in path for path in reads), 'only the owner direct message is read')
        self.assertTrue(all('someone else' not in line for line in logs.output), 'logged without content')

    def test_a_reply_and_the_reaction_presence(self):
        self.owner_says('오늘 저녁 뭐 먹을까?')
        work = self.mm_jobs()[0]
        self.service.acknowledge_long_work()
        source = self.fake.posts[self.surface.source_message(work['id'])]
        self.assertIn('eyes', [r['emoji_name'] for r in source['metadata'].get('reactions', [])])
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.assertEqual(self.fake.bot_posts()[-1], '저녁은 순두부찌개 어때요?')
        self.assertEqual(self.store.job(work['id'])['delivery'], 'sent')
        self.assertIsNotNone(self.surface.reply_message(work['id'], OWNER))


class Delivery(MattermostCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.owner_says('질문')
        self.work = self.mm_jobs()[0]
        self.assertTrue(self.service.run_one())

    def test_a_reply_that_never_left_waits_and_is_sent_once(self):
        self.fake.fail = 'connect'
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'pending')
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'sent')
        self.assertEqual(self.fake.bot_posts().count('저녁은 순두부찌개 어때요?'), 1)

    def test_an_uncertain_send_is_never_replayed(self):
        self.fake.fail = 'lost'
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'unknown')
        self.service.deliver_one()
        self.assertNotIn('저녁은 순두부찌개 어때요?', self.fake.bot_posts())

    def test_telegram_never_touches_a_mattermost_reply(self):
        self.service.telegram_surface.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'pending')


class OwnerActs(MattermostCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.service.use_decision_engine(with_owner_acts(FixtureDecisionEngine()))

    def settings_notice(self):
        self.owner_says('시간대를 도쿄로 바꿔줘')
        job = self.mm_jobs()[0]
        settings = self.service.settings_orchestrator
        draft = settings.propose(self.service.settings_owner(job), job['channel'], 'current_context', 'timezone',
                                 'Asia/Tokyo', work_id=job['id'])
        self.service.queue_settings_confirmation(job)
        while self.service.deliver_notification():
            pass
        notice = self.store.notification_of(job['id'], 'settings_change_proposed')
        return job, draft['draft_id'], notice

    def test_a_thumbs_up_on_a_settings_proposal_applies_it(self):
        job, draft_id, notice = self.settings_notice()
        self.assertEqual((notice['state'], notice['surface']), ('sent', 'mattermost'))
        self.assertIn(notice['message_id'], self.fake.posts)
        items = self.service.owner_acts.open_items(job, target=notice['message_id'])
        self.assertEqual([item['kind'] for item in items], [KIND_SETTINGS])
        self.fake.react(OWNER, notice['message_id'], '+1')
        self.surface.receive()
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'applied')

    def test_a_reaction_is_settled_at_most_once(self):
        job, draft_id, notice = self.settings_notice()
        self.fake.react(OWNER, notice['message_id'], '+1')
        with mock.patch.object(self.service.owner_acts, 'answer_reaction', wraps=self.service.owner_acts.answer_reaction) as answered:
            self.surface.receive()
            self.surface.receive()
            self.fake.edit(notice['message_id'], notice['message_id'])   # the post comes back again
            self.surface.receive()
        self.assertEqual(answered.call_count, 1)

    def test_an_action_approval_is_never_answered_here(self):
        """Owner decision 2026-10-25: an exact action approval is given at the Mac, never on Mattermost."""
        self.owner_says('결제해줘')
        job = self.mm_jobs()[0]
        row = self.store.queue_notification(job['id'], OWNER, self.surface.owner_generation(), 'browser_approval_needed',
                                            'offer-1', surface='mattermost')
        with mock.patch.object(type(self.service), '_browser_request',
                               lambda service, work_id: {'state': 'requested', 'offer_digest': 'offer-1',
                                                         'requested_at': time.time()}), \
                mock.patch.object(type(self.service), 'browser_step_prompt', lambda service, work_id: '결제 승인 요청'):
            self.service.deliver_notification()
            sent = self.store.notification(row['id'])
            self.assertIn('Mac의 Still', self.fake.posts[sent['message_id']]['message'])
            items = self.service.owner_acts.open_items(job, target=sent['message_id'])
        self.assertNotIn(KIND_STEP, [item['kind'] for item in items])

    def test_a_reaction_on_an_answer_is_an_observation(self):
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        work = self.mm_jobs()[0]
        reply = self.surface.reply_message(work['id'], OWNER)
        self.fake.react(OWNER, reply, '+1')
        self.surface.receive()
        with self.store.db() as db:
            events = [json.loads(row[0]) for row in db.execute(
                "SELECT detail FROM tool_events WHERE job_id=? AND tool='owner_reaction'", (work['id'],))]
        self.assertEqual(events, [{'emoji': ['👍'], 'on': 'answer'}])


class WorkMode(MattermostCase):
    def setUp(self):
        super().setUp()
        self.connect()

    def test_an_unapproved_surface_is_paused_and_sends_nothing_later(self):
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        before = len(self.fake.bot_posts())
        self.service.set_work_mode({'enabled': True})
        self.assertIsNone(self.surface.pairing())
        self.fake.post(OWNER, self.dm(), '회사에서 보낸 질문')
        self.assertIsNone(self.surface.receive())
        self.service.deliver_one()
        self.service.set_work_mode({'enabled': False})
        self.service.deliver_one()
        self.assertEqual(len(self.fake.bot_posts()), before, 'nothing queued while it was on is sent later')
        self.assertEqual(len(self.mm_jobs()), 1)

    def test_a_company_approved_surface_keeps_working(self):
        self.service.set_mattermost_company_approved({'approved': True})
        self.service.set_work_mode({'enabled': True})
        self.assertIsNotNone(self.surface.pairing())
        self.owner_says('회사 질문')
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.assertEqual(self.fake.bot_posts()[-1], '저녁은 순두부찌개 어때요?')

    def test_company_approval_is_a_real_bool(self):
        for value in ('yes', 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.set_mattermost_company_approved({'approved': value})


class Secrets(MattermostCase):
    def test_the_token_is_redacted_everywhere_and_after_rotation(self):
        from personal_agent.current_context import redact_known_secrets
        self.connect()
        self.assertNotIn(TOKEN, redact_known_secrets(self.store, f'my token is {TOKEN} ok'))
        self.store.secret(SECRET_KEY, 'mm-bot-token-rotated-9876543210')
        self.assertNotIn(TOKEN, redact_known_secrets(self.store, f'old {TOKEN}'), 'a retired token stays redacted')

    def test_the_token_never_reaches_a_log_or_a_record(self):
        with self.assertLogs('personal_agent', level=logging.DEBUG) as logs:
            self.connect()
            self.owner_says('질문')
            self.service.run_one()
            self.service.deliver_one()
            self.fake.fail = 'lost'
            self.surface.send_owner('x')
            logging.getLogger('personal_agent.mattermost').info('probe')
        self.assertTrue(all(TOKEN not in line for line in logs.output))
        with self.store.db() as db:
            dumped = ' '.join(str(tuple(row)) for table in ('tool_events', 'jobs', 'config')
                              for row in db.execute(f'SELECT * FROM {table}'))
        self.assertNotIn(TOKEN, dumped)


class ReviewRegressions(MattermostCase):
    """#306 review: revocation, batch watermarks and partial delivery."""

    def setUp(self):
        super().setUp()
        self.connect()

    def test_a_reconnect_never_sends_the_new_token_to_the_old_server(self):
        new_token, new_server = 'mm-new-token-fedcba9876543210', 'http://localhost:8065'
        seen = []

        def transport(method, url, headers, body, timeout):
            seen.append((urlsplit(url).netloc, headers.get('Authorization')))
            return self.fake(method, url, {**headers, 'Authorization': f'Bearer {TOKEN}'}, body, timeout)

        self.service.mattermost_transport = transport
        store_secret = self.store.secret

        def secret(key, value=None, create=None):
            result = store_secret(key, value, create)
            if key == SECRET_KEY and value == new_token:
                # A delivery runs between the new token being stored and its pairing being published.
                self.assertFalse(self.surface.send_owner('x'))
            return result

        with mock.patch.object(self.store, 'secret', secret):
            self.service.connect_mattermost({'server_url': new_server, 'token': new_token, 'owner_username': 'owner'})
        self.assertNotIn(('127.0.0.1:8065', f'Bearer {new_token}'), seen, 'the old server never gets the new token')
        self.assertTrue(self.surface.send_owner('y'), 'the new pairing works once it is published')
        self.assertEqual(seen[-1], ('localhost:8065', f'Bearer {new_token}'))

    def test_removing_the_declared_host_stops_polling_and_sending(self):
        def resolver(host, port, **kwargs):
            return [(2, 1, 6, '', ('10.3.3.3', port))]
        self.fake.dm(BOT, OWNER)
        intranet.add(self.store, 'chat.corp.example', surface='settings')
        self.surface._resolver = resolver
        with mock.patch('personal_agent.local_tools._bounded_system_resolver', resolver):
            self.service.connect_mattermost({'server_url': 'https://chat.corp.example', 'token': TOKEN,
                                             'owner_username': 'owner'})
        self.assertTrue(self.store.config(CONFIG_KEY)['private_server'])
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        intranet.remove(self.store, 'chat.corp.example', surface='settings')
        calls = len(self.fake.calls)
        self.fake.post(OWNER, self.dm(), '또 질문')
        self.assertIsNone(self.surface.receive())
        self.service.deliver_one()
        self.assertFalse(self.surface.send_owner('x'))
        self.assertEqual(self.fake.calls[calls:], [], 'nothing reaches the server once the host is removed')
        self.assertEqual(self.store.job(self.mm_jobs()[0]['id'])['delivery'], 'pending', 'definitely not sent')

    def test_a_post_edited_before_the_poll_is_still_taken(self):
        first = self.fake.post(OWNER, self.dm(), 'A')
        self.fake.post(OWNER, self.dm(), 'B')
        self.fake.edit(first['id'], 'A (edited)')   # A now sorts after B in posts?since=
        self.surface.receive()
        self.assertEqual(sorted(job['message'] for job in self.mm_jobs()), ['A (edited)', 'B'])
        self.surface.receive()
        self.assertEqual(len(self.mm_jobs()), 2)

    def test_posts_created_in_the_same_millisecond_are_all_taken(self):
        self.fake.post(OWNER, self.dm(), 'one')
        self.fake.clock -= 7
        self.fake.post(OWNER, self.dm(), 'two')
        self.surface.receive()
        self.assertEqual(sorted(job['message'] for job in self.mm_jobs()), ['one', 'two'])

    def test_every_reaction_of_a_batch_is_read_once(self):
        x = self.fake.post(BOT, self.dm(), 'notice x')
        y = self.fake.post(BOT, self.dm(), 'notice y')
        self.fake.react(OWNER, x['id'], '+1')       # the older reaction
        self.fake.react(OWNER, y['id'], '-1')       # the newer one
        self.fake.edit(x['id'], 'notice x (edited)')   # x now sorts after y
        with mock.patch.object(self.surface, 'ingest_reaction') as read:
            self.surface.receive()
            self.surface.receive()
        self.assertEqual(sorted((call.args[0], call.args[1]) for call in read.call_args_list),
                         sorted([(x['id'], '+1'), (y['id'], '-1')]))

    def test_the_work_keeps_when_the_owner_wrote_it(self):
        self.fake.post(OWNER, self.dm(), '네')
        wrote = time.time()
        with mock.patch('time.time', return_value=wrote + 30):   # the poll comes 30 s late
            self.surface.receive()
        job = self.mm_jobs()[0]
        self.assertLess(abs(job['source_at'] - wrote), 2, 'when the owner wrote it, on this Mac\'s clock')
        self.assertLess(job['source_at'], job['created'] - 20, 'not when it was fetched')

    def test_a_server_clock_behind_ours_does_not_put_a_reply_before_what_it_answers(self):
        self.surface.disconnect()
        self.fake.clock = int(time.time() * 1000) - 30_000   # the server is 30 s behind this Mac
        self.connect()
        shown = time.time()   # an item shown to the owner, recorded on this Mac's clock
        self.owner_says('네')
        self.assertGreaterEqual(self.mm_jobs()[0]['source_at'], shown - 1, 'the reply is after what it answers')

    def test_an_interrupted_work_gets_its_one_reply_after_a_restart(self):
        self.owner_says('긴 작업')
        work = self.mm_jobs()[0]
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work['id'],))
        self.assertEqual(self.service.recover_interrupted_work(), [work['id']])
        self.assertEqual(self.store.job(work['id'])['delivery'], 'pending')
        self.service.deliver_one()
        self.assertEqual(self.store.job(work['id'])['delivery'], 'sent')
        self.assertEqual(len([job for job in self.store.jobs() if job['channel'].startswith('mattermost:')]), 1,
                         'nothing is rerun')

    def test_connecting_in_work_mode_sends_nothing_until_approved(self):
        self.service.disconnect_mattermost()
        self.service.set_work_mode({'enabled': True})
        calls = len(self.fake.calls)
        with self.assertRaises(ValueError) as caught:
            self.connect()
        self.assertIn('회사 모드', str(caught.exception))
        self.assertEqual(self.fake.calls[calls:], [], 'no request, no token sent while unapproved')
        self.service.set_mattermost_company_approved({'approved': True})
        self.assertTrue(self.connect()['usable'])

    def test_a_reminder_asked_here_is_delivered_here(self):
        from personal_agent import preparations as prep
        self.owner_says('내일 9시에 회의 알려줘')
        job = self.mm_jobs()[0]
        result = self.service.preparation_scheduler(job, job['message'])(
            {'kind': prep.KIND_REMINDER, 'goal': '회의', 'timezone': 'Asia/Seoul',
             'due': time.strftime('%Y-%m-%dT%H:%M', time.localtime(time.time() + 86400))})
        row = self.service.preparations.get(result['preparation_id'])
        self.assertEqual((row['channel'], result['delivery']), ('mattermost', 'send'))
        with self.store.db() as db:
            db.execute("UPDATE preparations SET state=?, due_at=? WHERE id=?",
                       (prep.STATE_SCHEDULED, time.time() - 1, row['id']))
        self.service.run_due_preparation()
        started = [job for job in self.store.jobs() if prep.preparation_of(job.get('request_key'))]
        self.assertEqual([(job['channel'], job['chat_id']) for job in started],
                         [(f"mattermost:{self.surface.owner_generation()}", OWNER)])

    def test_a_parked_mattermost_work_is_found_by_its_owner(self):
        from personal_agent.connector_contract import _owner_key
        self.assertEqual(self.service._local_handoff_owner({'owner': _owner_key(f'mattermost:{OWNER}')}),
                         f'mattermost:{OWNER}')
        self.assertIn(f'mattermost:{OWNER}', self.service._connector_owner_candidates())

    def test_a_server_that_moves_to_a_private_address_is_refused(self):
        """Public at connect, private later (a VPN): an undeclared private address is refused at the next request."""
        self.surface.disconnect()
        answers = {'address': '93.184.216.34'}

        def resolver(host, port, **kwargs):
            return [(2, 1, 6, '', (answers['address'], port))]
        self.surface._resolver = resolver
        with mock.patch('personal_agent.local_tools._bounded_system_resolver', resolver):
            self.service.connect_mattermost({'server_url': 'https://chat.example.com', 'token': TOKEN,
                                             'owner_username': 'owner'})
        self.assertFalse(self.store.config(CONFIG_KEY)['private_server'])
        self.assertTrue(self.surface.send_owner('while still public'))
        answers['address'] = '10.8.8.8'   # the very next request, seconds later
        calls = len(self.fake.calls)
        self.assertFalse(self.surface.send_owner('to a private address'))
        self.assertIsNone(self.surface.receive())
        self.assertEqual(self.fake.calls[calls:], [], 'no token or message reaches the private address')

    def test_work_mode_switched_on_between_parts_stops_the_rest(self):
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        work = self.mm_jobs()[0]
        original = self.fake.post

        def post(user, channel, message, root_id=''):
            made = original(user, channel, message, root_id)
            if user == BOT:
                work_mode.set_enabled(self.store, True, surface='settings')   # lands after the first part
            return made
        self.fake.post = post
        with mock.patch.object(type(self.service), 'telegram_result_text', staticmethod(lambda *a, **k: '가' * 20000)):
            self.service.deliver_one()
        self.fake.post = original
        self.assertEqual(self.store.job(work['id'])['delivery'], 'unknown', 'one part left; the rest never did')
        self.assertEqual(sum(1 for text in self.fake.bot_posts() if text.startswith('가')), 1)

    def test_watermarks_follow_the_servers_clock(self):
        """A server 30 s behind this Mac: a message sent just after connecting is still taken."""
        self.surface.disconnect()
        self.fake.clock = int(time.time() * 1000) - 30_000
        self.connect()
        self.owner_says('연결 직후 질문')
        self.assertEqual([job['message'] for job in self.mm_jobs()], ['연결 직후 질문'])

    def test_work_mode_switched_on_while_connecting_stops_the_setup(self):
        self.surface.disconnect()
        original = self.fake.__call__

        class Switching:
            def __init__(inner):
                inner.seen = []

            def __call__(inner, method, url, headers, body, timeout):
                inner.seen.append(url.split('/api/v4', 1)[1])
                answer = original(method, url, headers, body, timeout)
                if url.endswith('/users/me'):
                    work_mode.set_enabled(self.store, True, surface='settings')
                return answer
        switching = Switching()
        self.service.mattermost_transport = switching
        with self.assertRaises(ValueError) as caught:
            self.connect()
        self.assertIn('회사 모드', str(caught.exception))
        self.assertEqual(switching.seen, ['/users/me'], 'no owner lookup, DM or greeting after the switch')
        self.assertFalse(self.surface.status()['connected'])

    def test_a_poll_answered_after_a_disconnect_submits_nothing(self):
        self.fake.post(OWNER, self.dm(), '연결 해제 직전 질문')
        original = self.fake.__call__

        def transport(method, url, headers, body, timeout):
            answer = original(method, url, headers, body, timeout)
            if method == 'GET' and url.split('?')[0].endswith('/posts'):
                self.service.disconnect_mattermost()   # lands while the poll is in flight
            return answer
        self.service.mattermost_transport = transport
        self.surface.receive()
        self.assertEqual(self.mm_jobs(), [], 'a revoked conversation submits no request')

    def test_saved_knowledge_is_searchable_from_here(self):
        from personal_agent.personal_knowledge import PersonalKnowledgeOrchestrator
        query = PersonalKnowledgeOrchestrator._request('channel:mattermost:g1:owner', 'mattermost:g1', '회의 메모')
        self.assertEqual(query, '회의 메모')
        with self.assertRaises(Exception):
            PersonalKnowledgeOrchestrator._request('x', 'slackish:g1', '회의 메모')

    def test_working_notes_and_saved_memory_notices_reach_this_surface(self):
        from personal_agent import owner_model as om
        self.owner_says('기억해줘')
        work = self.mm_jobs()[0]
        points = ['01 회의는 오전에 잡기']
        om.WorkingNotes(self.store).save({'points': points, 'text': om.notes_text(points), 'change': '회의 시간'}, work['id'])
        self.store.save_memory('profile.meeting', '회의는 오전', 'local-owner', work_id=work['id'], notice=True)
        with self.store.db() as db:
            rows = [tuple(row) for row in db.execute(
                'SELECT kind, surface, chat_id FROM telegram_notifications WHERE job_id=? ORDER BY kind', (work['id'],))]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(surface == 'mattermost' and chat == OWNER for _kind, surface, chat in rows), rows)

    def test_a_reconnect_between_parts_stops_the_rest(self):
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        work = self.mm_jobs()[0]
        original = self.fake.post

        def post(user, channel, message, root_id=''):
            made = original(user, channel, message, root_id)
            if user == BOT and message.startswith('가'):
                cfg = self.store.config(CONFIG_KEY)
                self.store.put(CONFIG_KEY, {**cfg, 'generation': 'reconnected'})   # Settings reconnects meanwhile
            return made
        self.fake.post = post
        with mock.patch.object(type(self.service), 'telegram_result_text', staticmethod(lambda *a, **k: '가' * 20000)):
            self.service.deliver_one()
        self.fake.post = original
        self.assertEqual(self.store.job(work['id'])['delivery'], 'unknown')
        self.assertEqual(sum(1 for text in self.fake.bot_posts() if text.startswith('가')), 1, 'no part after the reconnect')

    def test_a_delivered_watch_notice_is_recorded_as_told(self):
        from personal_agent import preparations as prep
        self.owner_says('가격 바뀌면 알려줘')
        job = self.mm_jobs()[0]
        row = self.store.queue_notification(job['id'], OWNER, self.surface.owner_generation(), prep.NOTIFY_KIND,
                                            'digest-1', surface='mattermost')
        with mock.patch.object(type(self.service), 'watch_notification_text', lambda service, n: '가격이 바뀌었어요'), \
                mock.patch.object(self.service.preparations, 'record_notified') as recorded:
            self.service.deliver_notification()
        self.assertEqual(self.store.notification(row['id'])['state'], 'sent')
        self.assertEqual([call.args[:2] for call in recorded.call_args_list], [(job['id'], 'digest-1')])

    def test_a_notice_of_a_work_that_ran_in_work_mode_is_never_sent_later(self):
        self.owner_says('질문')
        job = self.mm_jobs()[0]
        self.service.set_work_mode({'enabled': True})
        self.service.set_work_mode({'enabled': False})
        # A notice that binds nothing, so only the work-mode rule can hold it back.
        row = self.store.queue_notification(job['id'], OWNER, self.surface.owner_generation(), 'location_not_continued',
                                            surface='mattermost')
        before = len(self.fake.bot_posts())
        self.service.deliver_notification()
        self.assertEqual(self.store.notification(row['id'])['state'], 'cancelled')
        self.assertEqual(len(self.fake.bot_posts()), before)
        later = self.fake.post(OWNER, self.dm(), '회사 모드 끈 뒤 질문')
        self.surface.receive()
        fresh = next(job for job in self.mm_jobs() if job['message'] == later['message'])
        fresh_row = self.store.queue_notification(fresh['id'], OWNER, self.surface.owner_generation(),
                                                  'location_not_continued', surface='mattermost')
        self.service.deliver_notification()
        self.assertEqual(self.store.notification(fresh_row['id'])['state'], 'sent', 'a later Work is told as usual')

    def test_a_long_reply_partly_sent_is_never_posted_again(self):
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        work = self.mm_jobs()[0]
        before = len(self.fake.bot_posts())
        self.fake.fail, self.fake.fail_after = 'connect', 1
        with mock.patch.object(type(self.service), 'telegram_result_text', staticmethod(lambda *a, **k: '가' * 20000)):
            self.service.deliver_one()
        self.assertEqual(self.store.job(work['id'])['delivery'], 'unknown')
        self.service.deliver_one()
        self.assertEqual(len(self.fake.bot_posts()), before + 1, 'the delivered first part only, never again')


if __name__ == '__main__':
    unittest.main()
