"""#407 follow-up: the eval's Bot API stand-in and the paired-chat turn (development tooling only).

Evidence class: model-free.  The stand-in is a real loopback HTTP server; the
redirect runs in a child interpreter with the sandbox import guard on
``PYTHONPATH``; the runner is driven by a scripted client.  No Telegram,
model or Still server is contacted.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import pytest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import runner, sandbox, scenarios, scoring  # noqa: E402
from agentos_eval.fake_telegram import TOKEN, FakeTelegram  # noqa: E402
from agentos_eval.paths import SANDBOX_SITE  # noqa: E402


def call(root, method, body=None, token=TOKEN):
    request = urllib.request.Request(f'{root}/bot{token}/{method}', data=json.dumps(body or {}).encode(),
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read())


class StandIn(unittest.TestCase):
    def test_the_owner_says_still_polls_and_every_sent_text_is_recorded(self):
        with FakeTelegram(chat_id=77) as fake:
            message_id = fake.say('부산 숙소 찾아줘')
            [update] = call(fake.root, 'getUpdates', {'offset': 0, 'timeout': 30})['result']
            self.assertEqual((update['message']['text'], update['message']['message_id']), ('부산 숙소 찾아줘', message_id))
            self.assertEqual(call(fake.root, 'getUpdates', {'offset': update['update_id'] + 1, 'timeout': 0})['result'], [])
            sent = call(fake.root, 'sendMessage', {'chat_id': 77, 'text': '네, 찾아볼게요.'})['result']
            self.assertIsInstance(sent['message_id'], int)
            call(fake.root, 'sendMessageDraft', {'chat_id': 77, 'draft_id': 1, 'text': '…'})
            self.assertEqual([item['text'] for item in fake.chat_since(0)], ['네, 찾아볼게요.'], 'a draft is not a message')

    def test_another_token_is_refused(self):
        with FakeTelegram(chat_id=77) as fake:
            self.assertEqual(call(fake.root, 'getMe', token='123:real')['error_code'], 401)


class Redirect(unittest.TestCase):
    def run_child(self, root):
        code = ('import personal_agent.conversation_handoff as h; print(h.TELEGRAM_API_ROOT)')
        return subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60,
                              env={'PYTHONPATH': os.pathsep.join([str(SANDBOX_SITE), str(ROOT / 'src')]),
                                   'PATH': '/usr/bin:/bin', **({'AGENTOS_EVAL_TELEGRAM_ROOT': root} if root else {})})

    def test_only_a_loopback_root_redirects_the_sandbox(self):
        self.assertEqual(self.run_child('http://127.0.0.1:19999').stdout.strip(), 'http://127.0.0.1:19999')
        self.assertEqual(self.run_child('').stdout.strip(), 'https://api.telegram.org')
        refused = self.run_child('https://evil.example')
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('127.0.0.1', refused.stderr)


class Pairing(unittest.TestCase):
    def test_the_seed_chat_is_re_enabled_with_the_made_up_token_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp) / 'private'
            private.mkdir()
            db = sqlite3.connect(private / 'quickstart.db')
            db.execute('CREATE TABLE config(key TEXT PRIMARY KEY, value TEXT)')
            db.execute("INSERT INTO config VALUES('telegram', ?)", (json.dumps({'enabled': False, 'user_id': 4242,
                                                                                'generation': 'g7', 'cursor': 99}),))
            db.commit()
            db.close()
            (private / 'connections.json').write_text(json.dumps({'other': 'kept'}))
            self.assertEqual(sandbox.pair_fake_telegram(tmp, TOKEN), 4242)
            config = json.loads(sqlite3.connect(private / 'quickstart.db').execute(
                "SELECT value FROM config WHERE key='telegram'").fetchone()[0])
            self.assertEqual(config, {'enabled': True, 'user_id': 4242, 'generation': 'g7', 'cursor': 0})
            self.assertEqual(json.loads((private / 'connections.json').read_text()),
                             {'other': 'kept', 'telegram_token': TOKEN})
            env = sandbox.sandbox_environment(tmp, base={'PATH': '/usr/bin'}, telegram_root='http://127.0.0.1:1')
            self.assertEqual(env['AGENTOS_EVAL_TELEGRAM_ROOT'], 'http://127.0.0.1:1')
            self.assertNotIn('AGENTOS_EVAL_TELEGRAM_ROOT', sandbox.sandbox_environment(tmp, base={'PATH': '/usr/bin'}))


class ScriptedClient:
    """Plays Still: the owner's chat message becomes Work w1, which sends a preview, then the reply."""

    def __init__(self, fake):
        self.fake = fake
        self.listed = set()

    def job_ids(self):
        return set(self.listed)

    def find_task(self, message, known, interval=1.0):
        call(self.fake.root, 'sendMessage', {'chat_id': 77, 'text': '네, 해운대 쪽으로 세 곳 비교해 볼게요.'})
        call(self.fake.root, 'sendMessage', {'chat_id': 77, 'text': '<b>세 곳</b>을 골랐어요: A, B, C'})
        return 'w1'

    def wait(self, task_id, timeout=900, interval=2.0):
        return {'status': 'succeeded', 'events': []}

    def answer(self, task_id):
        return {'response': '**세 곳**을 골랐어요: A, B, C', 'answer_withheld': False}


class ChatTurn(unittest.TestCase):
    def test_what_the_owner_read_before_the_answer_reaches_the_judge(self):
        with FakeTelegram(chat_id=77) as fake:
            scenario = scenarios.validate({'id': 'chat-one', 'channel': 'telegram', 'rubric': ['conversational'],
                                           'turns': ['부산 숙소 세 곳 비교해줘']})
            [turn] = runner.run_turns(ScriptedClient(fake), scenario, poll_interval=0, telegram=fake)
        self.assertEqual([item['text'] for item in turn['before_answer']], ['네, 해운대 쪽으로 세 곳 비교해 볼게요.'])
        prompt = scoring.judge_prompt(scenario, {'turns': [turn]})
        self.assertIn('before the answer', prompt)
        self.assertIn('네, 해운대 쪽으로 세 곳 비교해 볼게요.', prompt)

    def test_a_web_scenario_is_unchanged_and_conversational_is_opt_in(self):
        web = scenarios.validate({'id': 'web-one', 'turns': ['안녕']})
        self.assertEqual(web['channel'], 'web')
        self.assertNotIn('conversational', web['rubric'])
        with self.assertRaises(scenarios.ScenarioError):
            scenarios.validate({'id': 'bad-one', 'channel': 'sms', 'turns': ['안녕']})


if __name__ == '__main__':
    unittest.main()


def test_response_identity_round_trip_and_meaningful_edits():
    tg=FakeTelegram(42,clock=lambda:100)
    first=tg._call('sendMessage',{'chat_id':42,'text':'same words'})
    second=tg._call('sendMessage',{'chat_id':42,'text':'same words'})
    assert first['message_id'] != second['message_id']
    tg._call('editMessageText',{'chat_id':42,'message_id':first['message_id'],'text':'same words','reply_markup':{}})
    changed=tg._call('editMessageText',{'chat_id':42,'message_id':first['message_id'],'text':'meaningful new words'})
    tg._call('sendChatAction',{'chat_id':42,'action':'typing'})
    messages=tg.chat_since(100)
    assert [m['text'] for m in messages]==['same words','same words','meaningful new words']
    assert [m['sequence'] for m in messages]==[1,2,4]
    assert [m['message_id'] for m in messages]==[first['message_id'],second['message_id'],changed['message_id']]
    assert len({m['delivery_id'] for m in messages})==3
    assert tg.calls_since(100)[0]['result']==first
    assert tg.chat_since(101)==[]
    assert tg.chat_since(100)==messages  # stateless collector; consumer owns its cursor


def test_unknown_or_unacknowledged_call_is_not_delivery():
    tg=FakeTelegram(42,clock=lambda:100)
    tg._call('sendDocument',{'chat_id':42})
    tg.calls.append({'at':100,'sequence':2,'method':'sendMessage','body':{'text':'unacknowledged'}})
    assert tg.chat_since(100)==[]


def test_sequence_collection_survives_backward_clock():
    now=[100]
    tg=FakeTelegram(42,clock=lambda:now[0])
    tg._call('sendMessage',{'chat_id':42,'text':'before sample'})
    cursor=tg.sequence()
    now[0]=1
    tg._call('sendMessage',{'chat_id':42,'text':'after clock jumped'})
    assert tg.chat_since(100,after_sequence=cursor)[0]['text']=='after clock jumped'
    assert tg.calls_since(100,after_sequence=cursor)[0]['sequence']==cursor+1


@pytest.mark.parametrize('permanently_locked', [False, True])
def test_chat_turn_busy_receipt_read_retries_without_losing_messages(tmp_path, monkeypatch, permanently_locked):
    from test_eval_come_due import Clock
    clock = Clock()
    fake = FakeTelegram(77, clock=clock.wall)
    path = tmp_path / 'private'
    path.mkdir()
    with sqlite3.connect(path / 'quickstart.db') as db:
        db.execute('CREATE TABLE telegram_turns(job_id TEXT PRIMARY KEY,chat_id INTEGER NOT NULL, '
                   'source_message_id INTEGER,reply_message_id INTEGER,created REAL NOT NULL,reply_to_message_id INTEGER)')
    class Client:
        def job_ids(self): return set()
        def find_task(self, *a, **kw):
            fake._call('sendMessage', {'chat_id': 77, 'text': 'preview'})
            reply = fake._call('sendMessage', {'chat_id': 77, 'text': 'the reply'})
            with sqlite3.connect(path / 'quickstart.db') as db:
                db.execute('INSERT INTO telegram_turns(job_id,chat_id,reply_message_id,created) VALUES (?,?,?,?)',
                           ('owner',77,reply['message_id'],clock.wall()))
            return 'owner'
        def wait(self, *a, **kw): return {'status': 'succeeded'}
        def answer(self, *a, **kw): return {'response': 'the reply'}
    connect = sqlite3.connect
    attempts = []
    class BusyRead:
        def __init__(self, db): self.db = db
        def execute(self, sql, *args):
            attempts.append(sql)
            if permanently_locked or len(attempts) <= 2:
                raise sqlite3.OperationalError('database is locked')
            return self.db.execute(sql, *args)
        def close(self): self.db.close()
    def sometimes_locked(*a, **kw):
        db = connect(*a, **kw)
        return BusyRead(db) if kw.get('uri') else db
    monkeypatch.setattr(runner.sqlite3, 'connect', sometimes_locked)
    task, detail, before, delivered = runner.chat_turn(Client(), fake, 'owner text', 20, 2,
                                                      wall=clock.wall,sleep=clock.sleep,data=tmp_path)
    assert task == 'owner' and detail['status'] == 'succeeded'
    if permanently_locked:
        assert delivered is None and [m['text'] for m in before] == ['preview', 'the reply']
        assert clock.now == runner.DELIVERY_WAIT_SECONDS
    else:
        assert [m['text'] for m in before] == ['preview'] and delivered['text'] == 'the reply'
    assert all('FROM telegram_turns' in query for query in attempts)
    assert attempts and clock.now <= runner.DELIVERY_WAIT_SECONDS


@pytest.mark.parametrize('seeded', [False, True])
def test_sandbox_pairs_real_store_before_server_start_and_readiness(tmp_path, monkeypatch, seeded):
    """Real schema/bootstrap subprocess; no server, model, socket or Bot API calls."""
    from types import SimpleNamespace
    from unittest.mock import Mock
    from personal_agent.quickstart_store import QuickStore

    seed = tmp_path / 'seed' if seeded else None
    if seed:
        store = QuickStore(seed)
        store.put('telegram', {'enabled': False, 'user_id': 4242, 'generation': 'g7', 'cursor': 99})
        store.put('telegram_status', {'state': 'error'})
        store.secret('other', 'kept')
        original_config = store.config('telegram')
        original_secrets = store.secret_path.read_bytes()
    telegram = SimpleNamespace(chat_id=0, root='http://127.0.0.1:1')
    process = Mock(poll=Mock(return_value=0), returncode=0)
    reservation = Mock(port=18900)
    monkeypatch.setattr(sandbox, 'reserve_port', lambda **kwargs: reservation)
    box = sandbox.Sandbox(tmp_path / 'boxes', seed=seed, slot=0, python=sys.executable,
                          src=ROOT / 'src', telegram=telegram, keep=True)
    expected_chat = 4242 if seeded else 77
    bootstrap = []
    server = []
    popen = subprocess.Popen

    def assert_pairing():
        with sqlite3.connect(box.data / 'private' / 'quickstart.db') as db:
            config = json.loads(db.execute("SELECT value FROM config WHERE key='telegram'").fetchone()[0])
            assert config == {'enabled': True, 'user_id': expected_chat, 'cursor': 0,
                              'generation': 'g7' if seeded else 'eval'}
            assert db.execute("SELECT 1 FROM config WHERE key='telegram_status'").fetchone() is None
            # Genuine migrations exist; no hand-built config-only store.
            assert 'created_from' in {row[1] for row in db.execute('PRAGMA table_info(preparations)')}
            assert 'sent_at' in {row[1] for row in db.execute('PRAGMA table_info(telegram_notifications)')}
        secrets = box.data / 'private' / 'connections.json'
        assert json.loads(secrets.read_text())['telegram_token'] == TOKEN
        if seeded:
            assert json.loads(secrets.read_text())['other'] == 'kept'
        assert secrets.stat().st_mode & 0o777 == 0o600
        assert telegram.chat_id == expected_chat

    def spawn(argv, *args, **kwargs):
        assert argv[0] == box.python
        if argv[1:3] == ['-m', 'personal_agent.quickstart']:
            # Pairing must be complete before the service can begin polling.
            assert_pairing()
            server.append(argv)
            return process
        bootstrap.append(argv)
        assert 'from personal_agent.quickstart_store import QuickStore' in argv[2]
        assert argv[-1] == str(box.data)
        assert str(box.src) in kwargs['env']['PYTHONPATH'].split(os.pathsep)
        return popen(argv, *args, **kwargs)

    monkeypatch.setattr(sandbox.subprocess, 'Popen', spawn)
    try:
        box.start()
        assert len(bootstrap) == (0 if seeded else 1)
        assert len(server) == 1
        assert_pairing()
        process.poll.return_value = None
        class Client:
            def healthy(self):
                assert_pairing()
                return True
        assert box.wait_healthy(Client()) is True
        if seed:
            assert store.config('telegram') == original_config
            assert store.secret_path.read_bytes() == original_secrets
    finally:
        process.poll.return_value = 0
        box.stop()
