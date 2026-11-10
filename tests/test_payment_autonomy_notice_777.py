"""#777: a payment-mode notice reads like the approval prompt, not a raw request dump."""
import unittest

from personal_agent.code_run import (APPROVAL_CONTENT_LABEL, APPROVAL_EXACT_LABEL, APPROVAL_FIELDS_LABEL,
                                     APPROVAL_MONEY_LINE, approval_field_lines, parse_approval_fields)
from personal_agent.messages import msg
from personal_agent.quickstart_service import AgentService, payment_autonomy_notice
import test_payment_autonomy_662 as mode

BODY = '{"ACCOUNT": "{{ACCOUNT}}", "ITEM": "A100", "QTY": "1", "PRICE": "69100", "KIND": "01"}'
NOTE = 'Sell the one held share at the current bid.'


def code_label(fields):
    """A money label shaped exactly as ``CodeRunner._state_change`` writes one."""
    lines = [NOTE, APPROVAL_MONEY_LINE]
    if fields:
        shown, check = approval_field_lines(parse_approval_fields(fields), BODY, '')
        lines.append('\n'.join([APPROVAL_FIELDS_LABEL, *shown, check]))
    lines.append(f'{APPROVAL_EXACT_LABEL} POST broker.test/v1/order\n{APPROVAL_CONTENT_LABEL} {BODY}')
    return '\n\n'.join(lines)


class Rendering(unittest.TestCase):
    def notice(self, label, action='code_run'):
        return payment_autonomy_notice({'action': action, 'label': label})

    def test_checked_fields_are_shown_and_the_raw_request_is_not(self):
        text = self.notice(code_label('item=ITEM; quantity=QTY; price=PRICE; kind=KIND=sell; account=ACCOUNT'))
        self.assertTrue(text.startswith(msg('payment_autonomy.notice', label='').strip()))
        for shown in (NOTE, 'item A100', 'quantity 1', 'price 69100', 'kind 01 (sell)'):
            self.assertIn(shown, text)
        for hidden in (APPROVAL_EXACT_LABEL, APPROVAL_CONTENT_LABEL, '"PRICE"', 'broker.test', APPROVAL_MONEY_LINE):
            self.assertNotIn(hidden, text)

    def test_a_field_left_unnamed_shows_the_exact_request(self):
        text = self.notice(code_label('price=PRICE'))
        self.assertIn('price 69100', text)
        self.assertIn(f'{APPROVAL_EXACT_LABEL} POST broker.test/v1/order', text)
        self.assertIn('"KIND": "01"', text)

    def test_a_label_without_fields_keeps_the_exact_request(self):
        text = self.notice(code_label(''))
        self.assertIn(NOTE, text)
        self.assertIn('"PRICE": "69100"', text)

    def test_a_browser_step_label_is_shown_as_is(self):
        self.assertIn('Pay exact amount', self.notice('Pay exact amount', action='browser_click'))

    def test_the_notice_states_authorization_not_an_instruction(self):
        for locale in ('ko', 'en', 'ja', 'zh'):
            text = msg('payment_autonomy.notice', locale, label='')
            self.assertNotIn('확인하세요', text)
            self.assertNotIn('Check the', text)


class ThroughTheCodeRun(mode.Mode, unittest.TestCase):
    BODY = mode.prior.CodeRunMoneyOnly.BODY
    setUp = mode.prior.CodeRunMoneyOnly.setUp

    def test_the_mode_notice_of_a_checked_code_run_hides_the_raw_body(self):
        self.service = AgentService(self.store)
        self.switch(True)
        self.save(money=True)
        approvals = self.service.browser_approvals_for(self.store.job(self.work))
        from personal_agent.code_run import CodeRunner
        process = mode.prior.FakeProcess(
            requests=[{'method': 'POST', 'host': mode.prior.HOST, 'path': '/v1/records/1', 'query': '',
                       'bodyText': self.BODY}], result={'stdout': 'ok', 'stderr': ''})
        runner = CodeRunner(self.store, self.work, popen=lambda *a, **k: process, node='/usr/bin/node',
                            install=lambda store, node: store.root, approvals=approvals)
        result = runner.run({'code': 'print(1)', 'hosts': '', 'secrets': 'example-broker',
                             'approval_note': 'Rename the record', 'approval_fields': 'note=note'})
        self.assertTrue(result['effects'])
        receipt = self.assert_receipt(self.work, 'code_run')
        self.assertIn(APPROVAL_EXACT_LABEL, receipt['label'])
        with self.store.db() as db:
            notice = db.execute("SELECT content FROM messages WHERE job_id=? AND role='assistant'",
                                (self.work,)).fetchone()['content']
        self.assertIn('Rename the record', notice)
        self.assertIn('note renamed', notice)
        self.assertNotIn(APPROVAL_EXACT_LABEL, notice)
        self.assertNotIn(APPROVAL_CONTENT_LABEL, notice)


if __name__ == '__main__':
    unittest.main()
