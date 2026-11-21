"""#782: a Work the payment mode allows repeatedly keeps one updating notice and closes with a summary.

Model-free: the real service, store and Telegram client over the fake Bot API transport of
``test_owner_settings_conversation._Case``; no model call is made.
"""
import json
import time
import unittest

from personal_agent import browser_session as bs
from personal_agent.messages import msg
from personal_agent.quickstart_service import payment_autonomy_notice
from test_owner_settings_conversation import CHAT, GENERATION
import test_payment_autonomy_662 as mode


class StatusNotice(mode.Mode, mode._Case):
    def setUp(self):
        super().setUp()
        self.refuse_edits = False
        self.switch(True)
        self.job = self.store.job(self.store.enqueue('pay', 'telegram-782', channel='telegram:' + GENERATION,
                                                     chat_id=CHAT, owner_typed=True))
        self.telegram.clear()

    def _telegram(self, url, body=None, headers=None, timeout=60):
        if url.endswith('/editMessageText') and self.refuse_edits:
            self.telegram.append(('editMessageText', body))
            return {'ok': False, 'error_code': 400, 'description': 'Bad Request: message to edit not found'}
        return super()._telegram(url, body, headers, timeout)

    def allow(self, n):
        binding = bs.step_binding(self.job['id'], 'browser_click', 'https://pay.test/', f'pay-{n}', f'pay-{n}', 'exact')
        return self.service.browser_approvals_for(self.job).payment_autonomy(binding, f'Pay exact amount {n}', dispatch=True)

    def calls(self, method):
        return [body for name, body in self.telegram if name == method]

    def notice_rows(self):
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,content FROM messages WHERE job_id=? AND role='assistant' ORDER BY id", (self.job['id'],))]

    def test_repeated_requests_edit_one_message_and_one_row(self):
        first = self.allow(1)
        self.allow(2)
        third = self.allow(3)
        sends, edits = self.calls('sendMessage'), self.calls('editMessageText')
        self.assertEqual(len(sends), 1)
        self.assertEqual(len(edits), 2)
        self.assertEqual({edit['message_id'] for edit in edits}, {9001})
        self.assertEqual(sends[0]['text'], payment_autonomy_notice(first))
        latest = payment_autonomy_notice(third, count=3)
        self.assertEqual(edits[-1]['text'], latest)
        self.assertIn('Pay exact amount 3', latest)
        self.assertNotIn('Pay exact amount 1', latest)
        # One transcript row, rewritten in place; every request keeps its own receipt.
        self.assertEqual([row['content'] for row in self.notice_rows()], [latest])
        self.assertEqual(len(self.receipts(self.job['id'])), 3)

    def test_an_edit_failure_falls_back_to_a_new_message(self):
        self.allow(1)
        self.refuse_edits = True
        self.allow(2)
        sends = self.calls('sendMessage')
        self.assertEqual(len(sends), 2)
        self.assertEqual(len(self.calls('editMessageText')), 1)
        self.assertEqual(sends[1]['text'], self.notice_rows()[0]['content'])
        # The new message is the one later requests edit.
        self.refuse_edits = False
        self.allow(3)
        held = self.store.config(self.service.PAYMENT_STATUS_KEY + self.job['id'])
        second_id = [i for i, (name, _body) in enumerate(self.telegram, 1) if name == 'sendMessage'][1] + 9000
        self.assertEqual(held['shown']['message_id'], second_id)
        self.assertEqual(self.calls('editMessageText')[-1]['message_id'], second_id)

    def test_a_forgotten_row_is_not_rewritten(self):
        self.allow(1)
        row = self.notice_rows()[0]['id']
        with self.store.db() as db:
            db.execute("UPDATE messages SET content='' WHERE id=?", (row,))
            db.execute('INSERT INTO forgotten_messages(message_id,receipt_id,created) VALUES (?,?,?)', (row, 'r', time.time()))
        self.allow(2)
        rows = self.notice_rows()
        self.assertEqual(rows[0]['content'], '')
        self.assertEqual(len(rows), 2)
        # A new row is a new bubble, not an edit of the forgotten one.
        self.assertEqual(len(self.calls('sendMessage')), 2)
        self.assertEqual(self.calls('editMessageText'), [])


class ClosingLine(mode.Mode, mode._Case):
    def setUp(self):
        super().setUp()
        self.switch(True)
        self.job = self.store.job(self.store.enqueue('pay', 'closing-782', owner_typed=True))

    def event(self, tool, status, detail):
        with self.store.db() as db:
            return db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                              (self.job['id'], tool, status, json.dumps(detail), time.time())).lastrowid

    def allow(self, n):
        binding = bs.step_binding(self.job['id'], 'browser_click', 'https://pay.test/', f'pay-{n}', f'pay-{n}', 'exact')
        return self.service.browser_approvals_for(self.job).payment_autonomy(binding, f'Pay {n}', dispatch=True)

    def test_no_line_without_a_payment_mode_receipt(self):
        self.event('browser_click', 'succeeded', {'host_action': 'browser_click', 'call_id': 'c0', 'scope': 'browser'})
        self.assertIsNone(self.service.payment_autonomy_closing(self.job['id']))
        self.assertEqual(self.service._with_payment_autonomy_closing(self.job['id'], 'answer'), 'answer')

    def test_the_line_counts_observed_failed_unknown_and_stopped_from_records_only(self):
        for n in range(4):self.allow(n)
        released = self.allow(9)
        self.service._release_payment_autonomy(self.job, released)
        call = {'host_action': 'browser_click', 'scope': 'browser'}
        # Observed: a running record and its success are one call.
        self.event('browser_click', 'running', {**call, 'call_id': 'ok'})
        self.event('browser_click', 'succeeded', {**call, 'call_id': 'ok'})
        self.event('browser_click', 'failed', {**call, 'call_id': 'bad'})
        self.event('browser_click', 'failed', {**call, 'call_id': 'lost', 'effect': 'unknown'})
        self.event('browser_click', 'running', {**call, 'call_id': 'cut'})
        # Refused before any hand-off: no effect, so not counted.
        self.event('browser_click', 'failed', {**call, 'call_id': 'refused', 'ran': False})
        line = self.service.payment_autonomy_closing(self.job['id'])
        self.assertEqual(line, msg('payment_autonomy.closing', allowed=4, observed=1, failed=1, unknown=2)
                         + ' ' + msg('payment_autonomy.closing_stopped', stopped=1))
        self.assertEqual(self.service._with_payment_autonomy_closing(self.job['id'], 'answer'), 'answer\n\n' + line)

    def test_no_effect_recorded_is_said_as_none_not_as_success(self):
        self.allow(1)
        self.assertEqual(self.service.payment_autonomy_closing(self.job['id']),
                         msg('payment_autonomy.closing', allowed=1, observed=0, failed=0, unknown=0))


if __name__ == '__main__':
    unittest.main()
