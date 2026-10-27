"""SLACK-01 (#214): the owner's Slack direct message as a conversation surface, over Socket Mode.

Model-free: a fixture model, a scripted owner-act judgment, a fake Web API client and a fake Socket
Mode connection fed with payloads shaped like Slack's (Events API ``message.im`` / ``reaction_added``,
``block_actions``).  ``GuardedWebClient`` is also exercised against a local HTTP server, so the real
SDK request path (guard, error mapping, no retry) is not mocked away.  No Slack workspace was contacted.
"""
import json
import logging
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from personal_agent.conversation_surface import SurfaceCapabilities
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.owner_acts import KIND_SETTINGS, KIND_STEP
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart import configured_service, make_handler
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.slack_surface import (ACTION_APPROVE, ACTION_DECLINE, APP_SECRET, BOT_SECRET, CONFIG_KEY, MANIFEST,
                                          SlackError, SlackNotSent, markdown_parts, message_id, slack_ts, web_client)
from owner_act_script import with_owner_acts

BOT_TOKEN = 'xoxb-1111-2222-abcdefghijklmnop'
APP_TOKEN = 'xapp-1-A0001-3333-abcdef0123456789'
TEAM, OTHER_TEAM = 'T0TEAM01', 'T0OTHER9'
BOT, OWNER, INTRUDER = 'U0BOT0001', 'U0OWNER01', 'U0INTRUD1'
DM, INTRUDER_DM, PUBLIC = 'D0OWNERDM', 'D0INTRDM1', 'C0PUBLIC1'


class FakeSlack:
    """The Web API methods the surface calls, against in-memory messages; ``guard()`` is asked first, as in the SDK client."""

    def __init__(self):
        self.clock = time.time() + 1
        self.messages = {}
        self.calls = []
        self.fail = None        # None | 'not_sent' | 'lost' for the next chat_postMessage
        self.fail_after = 0
        self.refuse_markdown = None     # a Slack error code for every Markdown body (#372)

    def tick(self):
        self.clock += 0.007
        return f'{self.clock:.6f}'

    def client(self, token, guard):
        fake = self

        class Client:
            def _call(self, method, **kwargs):
                guard()
                fake.calls.append((method, token, kwargs))
                if token not in (BOT_TOKEN, APP_TOKEN) and not token.startswith(('xoxb-new', 'xapp-new')):
                    raise SlackError('invalid_auth', status=200)
                return getattr(fake, method)(**kwargs)

            def __getattr__(self, method):
                return lambda **kwargs: self._call(method, **kwargs)
        return Client()

    def auth_test(self):
        return {'ok': True, 'team_id': TEAM, 'team': 'Test workspace', 'user_id': BOT}

    def conversations_open(self, users):
        return {'ok': True, 'channel': {'id': DM if users == OWNER else 'D0ELSE001'}}

    def apps_connections_open(self, app_token):
        return {'ok': True, 'url': 'wss://wss-primary.slack.com/link/?ticket=x'}

    def chat_postMessage(self, channel, text=None, blocks=None, markdown_text=None):
        if markdown_text is not None and (text is not None or blocks is not None):
            raise SlackError('markdown_text_conflict', status=200, code='markdown_text_conflict')
        if self.refuse_markdown and (markdown_text is not None
                                     or any(block.get('type') == 'markdown' for block in blocks or ())):
            raise SlackError(self.refuse_markdown, status=200, code=self.refuse_markdown)
        if self.fail and self.fail_after:
            self.fail_after -= 1
        elif self.fail == 'not_sent':
            self.fail = None
            raise SlackNotSent('not sent')
        elif self.fail == 'lost':
            self.fail = None
            raise SlackError('lost')
        ts = self.tick()
        self.messages[ts] = {'channel': channel, 'text': markdown_text if text is None else text, 'blocks': blocks,
                             'markdown': markdown_text is not None}
        return {'ok': True, 'ts': ts, 'channel': channel}

    def chat_update(self, channel, ts, text, blocks=None):
        self.messages[ts].update(text=text, blocks=blocks)
        return {'ok': True}

    def reactions_add(self, channel, timestamp, name):
        return {'ok': True}

    def reactions_remove(self, channel, timestamp, name):
        return {'ok': True}

    def posted(self):
        return [message['text'] for _ts, message in sorted(self.messages.items())]


class FakeSocket:
    """The SDK's Socket Mode client as the surface uses it."""

    made = []

    def __init__(self, app_token, client):
        self.app_token, self.client = app_token, client
        self.socket_mode_request_listeners = []
        self.acks, self.closed, self.connected = [], False, False
        FakeSocket.made.append(self)

    def connect(self):
        # The SDK asks the (guarded) Web API client for the URL first.
        self.client.apps_connections_open(app_token=self.app_token)
        self.connected = True

    def close(self):
        self.closed = True

    def send_socket_mode_response(self, response):
        self.acks.append(response.envelope_id)

    def deliver(self, kind, payload):
        from slack_sdk.socket_mode.request import SocketModeRequest
        request = SocketModeRequest(type=kind, envelope_id=f'env-{len(self.acks)}-{time.monotonic_ns()}', payload=payload)
        for listener in self.socket_mode_request_listeners:
            listener(self, request)


#: Every request body the fixture model was sent.
MODEL_REQUESTS = []


def model(url, body, headers=None, timeout=60):
    MODEL_REQUESTS.append(body)
    tools = [t.get('function', {}).get('name') or t.get('name') for t in body.get('tools', [])]
    if 'agentos_connection_probe' in tools:
        return {'message': {'content': '', 'tool_calls': [
            {'id': 'probe', 'function': {'name': 'agentos_connection_probe', 'arguments': {}}}]}}
    return {'message': {'content': '저녁은 순두부찌개 어때요?'}}


class SlackCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')
        self.fake = FakeSlack()
        FakeSocket.made = []
        self.service = AgentService(self.store, ModelAdapter(model), lambda *a, **k: {'ok': True, 'result': True})
        self.service.slack_web = self.fake.client
        self.service.slack_socket = FakeSocket
        self.service.steer_spawn = lambda target: target()
        self.service.save_model({'provider': 'ollama', 'endpoint': 'http://127.0.0.1:11434', 'model': 'test-model',
                                 'api_key': ''})
        self.assertTrue(self.service.test_model()['ok'])
        self.surface = self.service.slack_surface
        self.events, self.buttons_shown = 0, {}

    def connect(self, **extra):
        status = self.service.connect_slack({'bot_token': BOT_TOKEN, 'app_token': APP_TOKEN, 'owner_user_id': OWNER, **extra})
        self.surface.receive()
        return status

    @property
    def socket(self):
        return FakeSocket.made[-1]

    def event(self, event, team=TEAM, socket=None):
        self.events += 1
        (socket or self.socket).deliver('events_api', {'team_id': team, 'api_app_id': 'A0001', 'type': 'event_callback',
                                                        'event_id': f'Ev{self.events:06d}', 'event': event})

    def owner_says(self, text, thread=None, user=OWNER, channel=DM, **extra):
        ts = self.fake.tick()
        event = {'type': 'message', 'channel': channel, 'channel_type': 'im', 'user': user, 'text': text, 'ts': ts, **extra}
        if thread:
            event['thread_ts'] = thread
        self.event(event)
        return ts

    def press(self, ts, action, user=OWNER, team=TEAM, channel=DM, action_ts=None):
        message = self.fake.messages.get(ts, {})
        # The buttons as the owner saw them (a press can still arrive after Still removed them).
        shown = self.buttons_shown.setdefault(ts, message['blocks'])
        value = next(element['value'] for element in shown[-1]['elements'] if element['action_id'] == action)
        self.socket.deliver('interactive', {
            'type': 'block_actions', 'team': {'id': team}, 'user': {'id': user}, 'channel': {'id': channel},
            'container': {'type': 'message', 'message_ts': ts, 'channel_id': channel},
            'message': {'ts': ts, 'blocks': shown},
            'actions': [{'action_id': action, 'value': value, 'action_ts': action_ts or self.fake.tick()}]})

    def sl_jobs(self):
        return [job for job in self.store.jobs() if job['channel'].startswith('slack:')]


class Connect(SlackCase):
    def test_settings_connects_and_the_tokens_stay_in_the_secret_store(self):
        status = self.connect()
        self.assertEqual((status['connected'], status['owner_user_id'], status['team'], status['usable']),
                         (True, OWNER, 'Test workspace', True))
        cfg = self.store.config(CONFIG_KEY)
        self.assertEqual((cfg['team_id'], cfg['channel_id'], cfg['bot_user_id']), (TEAM, DM, BOT))
        for token in (BOT_TOKEN, APP_TOKEN):
            self.assertNotIn(token, json.dumps(cfg))
            self.assertNotIn(token, json.dumps(self.service.slack_status()))
        self.assertEqual((self.store.secret(BOT_SECRET), self.store.secret(APP_SECRET)), (BOT_TOKEN, APP_TOKEN))
        self.assertEqual(len(self.fake.posted()), 1, 'the bot says hello in the direct message')
        self.assertTrue(self.socket.connected and self.socket.app_token == APP_TOKEN, 'Socket Mode opens with the app token')

    def test_wrong_tokens_or_owner_connect_nothing(self):
        cases = [{'bot_token': 'xoxp-user-token-123456', 'app_token': APP_TOKEN, 'owner_user_id': OWNER},
                 {'bot_token': BOT_TOKEN, 'app_token': 'xoxb-not-an-app-token', 'owner_user_id': OWNER},
                 {'bot_token': BOT_TOKEN, 'app_token': APP_TOKEN, 'owner_user_id': 'owner'},
                 {'bot_token': BOT_TOKEN, 'app_token': APP_TOKEN, 'owner_user_id': BOT},
                 {'bot_token': 'xoxb-wrong-0000000000', 'app_token': APP_TOKEN, 'owner_user_id': OWNER}]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.service.connect_slack(body)
        self.assertFalse(self.surface.status()['connected'])
        self.assertIsNone(self.store.secret(BOT_SECRET) or None, 'no token is saved')

    def test_the_manifest_asks_for_direct_message_scopes_only(self):
        self.assertEqual(sorted(MANIFEST['oauth_config']['scopes']['bot']),
                         ['chat:write', 'im:history', 'im:write', 'reactions:read', 'reactions:write'])
        self.assertTrue(MANIFEST['settings']['socket_mode_enabled'])
        self.assertNotIn('request_url', json.dumps(MANIFEST), 'no public request URL')
        self.assertEqual(self.service.slack_status()['manifest'], MANIFEST)

    def test_settings_changes_only_on_this_mac(self):
        service = configured_service(self.store, {})
        service.slack_web = self.fake.client
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(service))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        session = self.store.local_session()
        headers = {'Content-Type': 'application/json', 'Cookie': f'agentos_session={session}', 'Origin': base,
                   'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Proto': 'https'}
        body = json.dumps({'bot_token': BOT_TOKEN, 'app_token': APP_TOKEN, 'owner_user_id': OWNER}).encode()
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(base + '/api/slack/connect', data=body, method='POST', headers=headers), timeout=10)
        self.assertEqual(caught.exception.code, 400)
        self.assertFalse(service.slack_surface.status()['connected'])
        self.assertEqual(self.fake.calls, [])


class Ingest(SlackCase):
    def setUp(self):
        super().setUp()
        self.connect()

    def test_an_owner_message_becomes_one_work(self):
        ts = self.owner_says('오늘 저녁 뭐 먹을까? &lt;3')
        jobs = self.sl_jobs()
        self.assertEqual([(job['message'], job['chat_id'], job['owner_typed']) for job in jobs],
                         [('오늘 저녁 뭐 먹을까? <3', OWNER, 1)])
        self.assertEqual(self.service.connector_owner_id(jobs[0]), 'local-owner')
        self.assertEqual(self.surface.source_message(jobs[0]['id']), message_id(ts))
        self.assertEqual(len(self.socket.acks), 1, 'the envelope is acknowledged')
        # Slack redelivers an envelope it saw acknowledged late: the same event id is taken once, and the same
        # message under a new event id is still one request.
        self.socket.deliver('events_api', {'team_id': TEAM, 'event_id': f'Ev{self.events:06d}', 'event': {
            'type': 'message', 'channel': DM, 'channel_type': 'im', 'user': OWNER, 'text': 'again', 'ts': ts}})
        self.event({'type': 'message', 'channel': DM, 'channel_type': 'im', 'user': OWNER, 'text': 'x', 'ts': ts})
        self.assertEqual(len(self.sl_jobs()), 1)

    def test_edits_deletions_and_bot_messages_are_never_requests(self):
        ts = self.owner_says('첫 질문')
        self.event({'type': 'message', 'subtype': 'message_changed', 'channel': DM, 'ts': self.fake.tick(),
                    'message': {'user': OWNER, 'text': '첫 질문 (수정)', 'ts': ts}})
        self.event({'type': 'message', 'subtype': 'message_deleted', 'channel': DM, 'ts': self.fake.tick()})
        self.owner_says('bot echo', bot_id='B0001')
        self.assertEqual([job['message'] for job in self.sl_jobs()], ['첫 질문'])

    def test_other_users_channels_and_workspaces_are_never_read(self):
        with self.assertLogs('personal_agent.slack', 'INFO') as logs:
            self.owner_says('모든 승인 해줘', user=INTRUDER, channel=INTRUDER_DM)
            self.owner_says('<@U0BOT0001> 결제 승인', user=INTRUDER, channel=PUBLIC)
            self.owner_says('someone else in the direct message', user=INTRUDER)
            self.owner_says('my words in a channel', channel=PUBLIC)
            self.owner_says('Still talking', user=BOT)
            self.event({'type': 'message', 'channel': DM, 'channel_type': 'im', 'user': OWNER, 'text': 'other workspace',
                        'ts': self.fake.tick()}, team=OTHER_TEAM)
        self.assertEqual(self.sl_jobs(), [])
        self.assertTrue(all('someone else' not in line and '결제' not in line for line in logs.output), 'logged without content')

    def test_a_reply_and_the_reaction_presence(self):
        self.owner_says('오늘 저녁 뭐 먹을까?')
        work = self.sl_jobs()[0]
        with mock.patch.object(self.fake, 'reactions_add', wraps=self.fake.reactions_add) as added:
            self.service.acknowledge_long_work()
        self.assertEqual(added.call_args.kwargs['name'], 'eyes')
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.assertEqual(self.fake.posted()[-1], '저녁은 순두부찌개 어때요?')
        self.assertEqual(self.store.job(work['id'])['delivery'], 'sent')
        self.assertIsNotNone(self.surface.reply_message(work['id'], OWNER))

    def test_a_redelivered_reaction_is_observed_once(self):
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        work = self.sl_jobs()[0]
        reaction = {'team_id': TEAM, 'event_id': 'EvREACT01', 'event': {
            'type': 'reaction_added', 'user': OWNER, 'reaction': '+1',
            'item': {'type': 'message', 'channel': DM, 'ts': slack_ts(self.surface.reply_message(work['id'], OWNER))}}}
        self.socket.deliver('events_api', reaction)
        self.socket.deliver('events_api', reaction)   # Slack redelivers an envelope acknowledged late
        with self.store.db() as db:
            events = [json.loads(row[0]) for row in db.execute(
                "SELECT detail FROM tool_events WHERE job_id=? AND tool='owner_reaction'", (work['id'],))]
        self.assertEqual(events, [{'emoji': ['👍'], 'on': 'answer'}])

    def test_a_message_that_failed_to_be_taken_is_not_acknowledged_and_its_redelivery_is_taken(self):
        import sqlite3
        original = self.surface._ingest_owner_message
        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError('database is locked')
            return original(*args, **kwargs)
        payload = {'team_id': TEAM, 'event_id': 'EvFLAKY001', 'event': {
            'type': 'message', 'channel': DM, 'channel_type': 'im', 'user': OWNER, 'text': '사라지면 안 되는 질문',
            'ts': self.fake.tick()}}
        acks = len(self.socket.acks)
        with mock.patch.object(self.surface, '_ingest_owner_message', flaky):
            self.socket.deliver('events_api', payload)
            self.assertEqual((len(self.socket.acks), self.sl_jobs()), (acks, []), 'not acknowledged, nothing taken')
            self.socket.deliver('events_api', payload)   # Slack delivers the unacknowledged envelope again
        self.assertEqual([job['message'] for job in self.sl_jobs()], ['사라지면 안 되는 질문'])
        self.assertEqual(len(self.socket.acks), acks + 1)

    def test_a_reaction_that_failed_to_be_taken_is_read_again(self):
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        work = self.sl_jobs()[0]
        reaction = {'team_id': TEAM, 'event_id': 'EvREACT02', 'event': {
            'type': 'reaction_added', 'user': OWNER, 'reaction': '+1',
            'item': {'type': 'message', 'channel': DM, 'ts': slack_ts(self.surface.reply_message(work['id'], OWNER))}}}
        original, calls = self.surface.ingest_reaction, []

        def flaky(*args):
            calls.append(1)
            if len(calls) == 1:
                raise OSError('disk')
            return original(*args)
        with mock.patch.object(self.surface, 'ingest_reaction', flaky):
            self.socket.deliver('events_api', reaction)
            self.socket.deliver('events_api', reaction)
        with self.store.db() as db:
            events = db.execute("SELECT COUNT(*) FROM tool_events WHERE job_id=? AND tool='owner_reaction'",
                                (work['id'],)).fetchone()[0]
        self.assertEqual(events, 1)

    def test_a_thread_reply_names_the_message_it_answers(self):
        root = self.fake.posted() and sorted(self.fake.messages)[0]
        self.owner_says('이거요', thread=root)
        self.assertEqual(self.surface.reply_to(self.sl_jobs()[0]['id']), message_id(root))


class Socket(SlackCase):
    def test_the_connection_follows_the_pairing_and_work_mode(self):
        self.connect()
        first = self.socket
        self.service.set_work_mode({'enabled': True})
        self.assertTrue(first.closed, 'switching work mode on closes the connection at once')
        calls = len(self.fake.calls)
        self.surface.receive()
        self.assertEqual((len(FakeSocket.made), self.fake.calls[calls:]), (1, []), 'no reconnect, no request')
        self.service.set_slack_company_approved({'approved': True})
        self.surface.receive()
        self.assertEqual(len(FakeSocket.made), 2, 'a company-approved workspace reconnects')
        self.service.disconnect_slack()
        self.assertTrue(self.socket.closed)
        self.assertIsNone(self.store.secret(BOT_SECRET) or None)

    def test_a_reconnect_replaces_the_connection_and_the_old_one_submits_nothing(self):
        self.connect()
        old = self.socket
        self.connect()
        self.assertTrue(old.closed)
        self.event({'type': 'message', 'channel': DM, 'channel_type': 'im', 'user': OWNER, 'text': 'late on the old one',
                    'ts': self.fake.tick()}, socket=old)
        self.assertEqual(self.sl_jobs(), [])

    def test_a_failed_connection_waits_longer_each_time(self):
        self.connect()
        self.service.disconnect_slack()

        class Failing(FakeSocket):
            def connect(inner):
                raise OSError('refused')
        self.service.slack_socket = Failing
        self.service.connect_slack({'bot_token': BOT_TOKEN, 'app_token': APP_TOKEN, 'owner_user_id': OWNER})
        made = len(FakeSocket.made)
        self.surface.receive(now=1000)
        self.surface.receive(now=1001)
        self.assertEqual(len(FakeSocket.made), made + 1, 'no second attempt within the pause')
        self.assertEqual(self.surface.status()['state'], 'error')
        self.surface.receive(now=1003)
        self.assertEqual(len(FakeSocket.made), made + 2)
        self.assertTrue(FakeSocket.made[-1].closed)


class Delivery(SlackCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.owner_says('질문')
        self.work = self.sl_jobs()[0]
        self.assertTrue(self.service.run_one())

    def test_a_reply_that_never_left_waits_and_is_sent_once(self):
        self.fake.fail = 'not_sent'
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'pending')
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'sent')
        self.assertEqual(self.fake.posted().count('저녁은 순두부찌개 어때요?'), 1)

    def test_an_uncertain_send_is_never_replayed(self):
        self.fake.fail = 'lost'
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'unknown')
        self.service.deliver_one()
        self.assertNotIn('저녁은 순두부찌개 어때요?', self.fake.posted())

    def test_a_long_reply_partly_sent_is_never_posted_again(self):
        with mock.patch('personal_agent.slack_surface.POST_CHARS', 4):
            self.fake.fail, self.fake.fail_after = 'not_sent', 1
            self.service.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'unknown')

    def test_a_reply_is_sent_as_markdown(self):
        # #372: the model's own Markdown, laid out by Slack, instead of raw ``**`` and ``|---|`` in mrkdwn text.
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'sent')
        _method, _token, kwargs = [call for call in self.fake.calls if call[0] == 'chat_postMessage'][-1]
        self.assertEqual(kwargs, {'channel': DM, 'markdown_text': '저녁은 순두부찌개 어때요?'})

    def test_a_refused_markdown_body_is_sent_once_as_plain_text(self):
        # Slack answered ok:false, so nothing was posted: one plain resend, as Telegram's plain resend (#581).
        self.fake.refuse_markdown = 'invalid_blocks'
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'sent')
        self.assertEqual(self.fake.posted().count('저녁은 순두부찌개 어때요?'), 1)
        self.assertEqual([message['markdown'] for message in self.fake.messages.values()][-1], False)

    def test_another_refusal_is_not_resent_as_plain_text(self):
        self.fake.refuse_markdown = 'channel_not_found'
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'unknown')
        self.assertNotIn('저녁은 순두부찌개 어때요?', self.fake.posted())

    def test_the_worker_is_told_it_answers_in_a_slack_chat(self):
        # #375: the surface fact of the Work's own channel (#1380), as on Telegram; it was Telegram-only.
        MODEL_REQUESTS.clear()
        self.owner_says('다른 질문')
        self.assertTrue(self.service.run_one())
        system = '\n'.join(str(message.get('content')) for body in MODEL_REQUESTS for message in body.get('messages', ())
                           if message.get('role') == 'system')
        self.assertIn('# Where the owner is writing\nIn their paired Slack direct message.', system)
        self.assertIn('read as a chat message', system)

    def test_a_token_is_used_only_with_its_own_pairing(self):
        self.store.secret(BOT_SECRET, 'xoxb-new-other-workspace-1')   # a reconnect stored new tokens, not yet its pairing
        calls = len(self.fake.calls)
        self.assertFalse(self.surface.send_owner('x'))
        self.service.deliver_one()
        self.assertEqual(self.fake.calls[calls:], [], 'neither token is sent with the other pairing')
        self.assertEqual(self.store.job(self.work['id'])['delivery'], 'pending')


class Buttons(SlackCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.service.use_decision_engine(with_owner_acts(FixtureDecisionEngine()))

    def settings_notice(self):
        self.owner_says('시간대를 도쿄로 바꿔줘')
        job = self.sl_jobs()[0]
        settings = self.service.settings_orchestrator
        draft = settings.propose(self.service.settings_owner(job), job['channel'], 'current_context', 'timezone',
                                 'Asia/Tokyo', work_id=job['id'])
        self.service.queue_settings_confirmation(job)
        while self.service.deliver_notification():
            pass
        notice = self.store.notification_of(job['id'], 'settings_change_proposed')
        return job, draft['draft_id'], notice

    def test_a_settings_proposal_is_applied_by_its_button_once(self):
        job, draft_id, notice = self.settings_notice()
        self.assertEqual((notice['state'], notice['surface']), ('sent', 'slack'))
        blocks = self.fake.messages[slack_ts(notice['message_id'])]['blocks']
        self.assertEqual([element['action_id'] for element in blocks[-1]['elements']], [ACTION_APPROVE, ACTION_DECLINE])
        self.assertEqual([item['kind'] for item in self.service.owner_acts.open_items(job, target=notice['message_id'])],
                         [KIND_SETTINGS])
        with mock.patch.object(self.service.settings_orchestrator, 'settle_pending',
                               wraps=self.service.settings_orchestrator.settle_pending) as settled:
            self.press(slack_ts(notice['message_id']), ACTION_APPROVE)
            self.press(slack_ts(notice['message_id']), ACTION_APPROVE)   # a second press of the same button
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'applied')
        self.assertEqual(settled.call_count, 1, 'settled at most once')
        self.assertIn('이미 처리됐거나', self.fake.posted()[-1])
        self.assertNotIn('actions', [block['type'] for block in self.fake.messages[slack_ts(notice['message_id'])]['blocks']],
                         'the answered message loses its buttons')

    def test_a_notice_shows_its_markdown_beside_the_buttons(self):
        _job, _draft_id, notice = self.settings_notice()
        message = self.fake.messages[slack_ts(notice['message_id'])]
        self.assertEqual([block['type'] for block in message['blocks']], ['markdown', 'actions'])
        self.assertEqual(message['blocks'][0]['text'][:200], message['text'][:200], 'the fallback text is the same notice')

    def test_a_refused_markdown_notice_keeps_its_buttons_as_plain_text(self):
        self.fake.refuse_markdown = 'invalid_blocks'
        _job, _draft_id, notice = self.settings_notice()
        self.assertEqual(notice['state'], 'sent')
        blocks = self.fake.messages[slack_ts(notice['message_id'])]['blocks']
        self.assertEqual({block['type'] for block in blocks[:-1]}, {'section'})
        self.assertEqual(blocks[-1]['type'], 'actions')

    def test_a_replayed_press_is_taken_once(self):
        _job, _draft_id, notice = self.settings_notice()
        with mock.patch.object(self.service.owner_acts, 'answer_exact', wraps=self.service.owner_acts.answer_exact) as answered:
            self.press(slack_ts(notice['message_id']), ACTION_DECLINE, action_ts='1700000000.000100')
            self.press(slack_ts(notice['message_id']), ACTION_DECLINE, action_ts='1700000000.000100')
        self.assertEqual(answered.call_count, 1)

    def test_a_press_by_anyone_else_settles_nothing(self):
        _job, draft_id, notice = self.settings_notice()
        for kwargs in ({'user': INTRUDER}, {'team': OTHER_TEAM}, {'channel': INTRUDER_DM}):
            with self.subTest(**kwargs):
                self.press(slack_ts(notice['message_id']), ACTION_APPROVE, **kwargs)
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'awaiting-confirmation')

    def test_a_press_on_another_message_settles_nothing(self):
        _job, draft_id, notice = self.settings_notice()
        hello = sorted(self.fake.messages)[0]
        self.fake.messages[hello]['blocks'] = self.fake.messages[slack_ts(notice['message_id'])]['blocks']
        self.press(hello, ACTION_APPROVE)
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'awaiting-confirmation')

    def test_an_action_approval_is_answered_by_its_button(self):
        """Owner decision 2026-10-25 on #214: exact action approvals may be given on Slack."""
        self.owner_says('결제해줘')
        job = self.sl_jobs()[0]
        row = self.store.queue_notification(job['id'], OWNER, self.surface.owner_generation(), 'browser_approval_needed',
                                            'offer-1', surface='slack')
        answered = []
        with mock.patch.object(type(self.service), '_browser_request',
                               lambda service, work_id: {'state': 'requested', 'offer_digest': 'offer-1',
                                                         'requested_at': time.time()}), \
                mock.patch.object(type(self.service), 'browser_step_prompt', lambda service, work_id: '결제 승인 요청'), \
                mock.patch.object(type(self.service), 'answer_step_approval',
                                  lambda service, notification, approve: answered.append(approve) or '진행합니다'):
            self.service.deliver_notification()
            sent = self.store.notification(row['id'])
            self.assertNotIn('Mac의 Still', self.fake.messages[slack_ts(sent['message_id'])]['text'])
            self.assertIn(KIND_STEP, [item['kind'] for item in self.service.owner_acts.open_items(job, target=sent['message_id'])])
            self.press(slack_ts(sent['message_id']), ACTION_APPROVE)
        self.assertEqual(answered, [True])
        self.assertEqual(self.fake.posted()[-1], '진행합니다')

    def test_a_thumbs_up_on_a_settings_proposal_applies_it(self):
        _job, draft_id, notice = self.settings_notice()
        self.event({'type': 'reaction_added', 'user': OWNER, 'reaction': '+1::skin-tone-2',
                    'item': {'type': 'message', 'channel': DM, 'ts': slack_ts(notice['message_id'])}})
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'applied')

    def test_a_reaction_by_anyone_else_is_no_act(self):
        _job, draft_id, notice = self.settings_notice()
        self.event({'type': 'reaction_added', 'user': INTRUDER, 'reaction': '+1',
                    'item': {'type': 'message', 'channel': DM, 'ts': slack_ts(notice['message_id'])}})
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'awaiting-confirmation')


class WorkMode(SlackCase):
    def setUp(self):
        super().setUp()
        self.connect()

    def test_an_unapproved_surface_is_paused_and_sends_nothing_later(self):
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        socket, before = self.socket, len(self.fake.posted())
        self.service.set_work_mode({'enabled': True})
        self.assertIsNone(self.surface.pairing())
        self.event({'type': 'message', 'channel': DM, 'channel_type': 'im', 'user': OWNER, 'text': '회사에서 보낸 질문',
                    'ts': self.fake.tick()}, socket=socket)
        self.service.deliver_one()
        self.service.set_work_mode({'enabled': False})
        self.service.deliver_one()
        self.assertEqual(len(self.fake.posted()), before, 'nothing queued while it was on is sent later')
        self.assertEqual(len(self.sl_jobs()), 1)

    def test_connecting_in_work_mode_sends_nothing_until_approved(self):
        self.service.disconnect_slack()
        self.service.set_work_mode({'enabled': True})
        calls = len(self.fake.calls)
        with self.assertRaises(ValueError) as caught:
            self.connect()
        self.assertIn('회사 모드', str(caught.exception))
        self.assertEqual(self.fake.calls[calls:], [], 'no request and no token sent while unapproved')
        self.service.set_slack_company_approved({'approved': True})
        self.assertTrue(self.connect()['usable'])

    def test_a_company_approved_surface_keeps_working(self):
        self.service.set_slack_company_approved({'approved': True})
        self.service.set_work_mode({'enabled': True})
        self.surface.receive()
        self.owner_says('회사 질문')
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.assertEqual(self.fake.posted()[-1], '저녁은 순두부찌개 어때요?')

    def test_company_approval_is_a_real_bool(self):
        for value in ('yes', 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.set_slack_company_approved({'approved': value})


class Secrets(SlackCase):
    def test_the_tokens_are_redacted_everywhere(self):
        from personal_agent.bounded_execution import SECRET_PATTERN
        from personal_agent.current_context import redact_known_secrets
        self.connect()
        for token in (BOT_TOKEN, APP_TOKEN):
            with self.subTest(token=token[:5]):
                self.assertNotIn(token, redact_known_secrets(self.store, f'my token is {token} ok'))
                self.assertNotIn(token, SECRET_PATTERN.sub('[redacted]', f'pasted {token} here'),
                                 'a Slack token is recognised by its shape too')

    def test_the_tokens_never_reach_a_log_or_a_record(self):
        with self.assertLogs('personal_agent', level=logging.DEBUG) as logs:
            self.connect()
            self.owner_says('질문')
            self.service.run_one()
            self.service.deliver_one()
            self.fake.fail = 'lost'
            self.surface.send_owner('x')
            logging.getLogger('personal_agent.slack').info('probe')
        self.assertTrue(all(BOT_TOKEN not in line and APP_TOKEN not in line for line in logs.output))
        with self.store.db() as db:
            dumped = ' '.join(str(tuple(row)) for table in ('tool_events', 'jobs', 'config')
                              for row in db.execute(f'SELECT * FROM {table}'))
        self.assertNotIn(BOT_TOKEN, dumped)
        self.assertNotIn(APP_TOKEN, dumped)


class GuardedClient(unittest.TestCase):
    """The SDK's own request path: the guard runs before the network, errors become Still's, nothing is retried."""

    def serve(self, answer):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(inner):
                length = int(inner.headers.get('Content-Length') or 0)
                seen.append((inner.path, inner.headers.get('Authorization'), inner.rfile.read(length)))
                status, body = answer(inner.path)
                if status is None:
                    inner.connection.close()   # the request went out; the answer never comes
                    return
                inner.send_response(status)
                inner.send_header('Content-Type', 'application/json')
                inner.end_headers()
                inner.wfile.write(json.dumps(body).encode())

            def log_message(inner, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/api/', seen

    def client(self, base, guard=lambda: None):
        client = web_client(BOT_TOKEN, guard)
        client.base_url = base
        return client

    def test_a_refused_guard_sends_nothing(self):
        base, seen = self.serve(lambda path: (200, {'ok': True, 'ts': '1.0'}))

        def guard():
            raise SlackNotSent('work mode')
        with self.assertRaises(SlackNotSent):
            self.client(base, guard).chat_postMessage(channel=DM, text='x')
        self.assertEqual(seen, [])

    def test_a_post_and_a_refusal(self):
        base, seen = self.serve(lambda path: (200, {'ok': True, 'ts': '1.5'} if path.endswith('chat.postMessage')
                                              else {'ok': False, 'error': 'not_in_channel'}))
        client = self.client(base)
        self.assertEqual(client.chat_postMessage(channel=DM, text='hello').get('ts'), '1.5')
        self.assertEqual(seen[0][1], f'Bearer {BOT_TOKEN}')
        with self.assertRaises(SlackError) as caught:
            client.reactions_add(channel=DM, timestamp='1.5', name='eyes')
        self.assertNotIsInstance(caught.exception, SlackNotSent)
        self.assertNotIn(BOT_TOKEN, str(caught.exception))

    def test_markdown_reaches_slack_and_a_refusal_keeps_its_code(self):
        base, seen = self.serve(lambda path: (200, {'ok': False, 'error': 'invalid_blocks'}))
        with self.assertRaises(SlackError) as caught:
            self.client(base).chat_postMessage(channel=DM, markdown_text='**굵게**')
        self.assertEqual(caught.exception.code, 'invalid_blocks')
        self.assertIn(b'markdown_text', seen[0][2])

    def test_a_server_that_cannot_be_reached_is_not_sent(self):
        with self.assertRaises(SlackNotSent):
            self.client('http://127.0.0.1:9/api/').chat_postMessage(channel=DM, text='x')

    def test_a_lost_answer_is_unknown_and_never_retried(self):
        base, seen = self.serve(lambda path: (None, None))
        with self.assertRaises(SlackError) as caught:
            self.client(base).chat_postMessage(channel=DM, text='x')
        self.assertNotIsInstance(caught.exception, SlackNotSent)
        self.assertIsNone(caught.exception.code, 'no answer was read, so it is never taken for a refusal')
        self.assertEqual(len(seen), 1, 'the request is never sent twice')


class MarkdownParts(unittest.TestCase):
    def test_short_text_is_one_part(self):
        self.assertEqual(markdown_parts('짧은 답', 100), ['짧은 답'])
        self.assertEqual(markdown_parts('', 100), [''])

    def test_parts_are_cut_at_a_blank_line_and_keep_every_character(self):
        text = '## 요약\n첫 줄\n\n## 상세\n' + '가' * 30
        parts = markdown_parts(text, 25)
        self.assertEqual(''.join(parts), text)
        self.assertEqual(parts[0], '## 요약\n첫 줄\n\n')
        self.assertTrue(all(len(part) <= 25 for part in parts))

    def test_a_code_fence_is_not_cut_when_a_line_end_outside_it_exists(self):
        text = '앞\n```\na\nb\nc\n```\n뒤'
        parts = markdown_parts(text, 14)
        self.assertEqual(''.join(parts), text)
        self.assertEqual(parts[0], '앞\n')

    def test_text_without_line_ends_is_cut_hard(self):
        self.assertEqual(markdown_parts('abcdefghij', 4), ['abcd', 'efgh', 'ij'])


class Shape(unittest.TestCase):
    def test_capabilities(self):
        from personal_agent.slack_surface import SlackSurface
        self.assertEqual(SlackSurface.capabilities, SurfaceCapabilities(reactions=True, buttons=True, action_approvals=True))


if __name__ == '__main__':
    unittest.main()
