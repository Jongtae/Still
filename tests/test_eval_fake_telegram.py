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
