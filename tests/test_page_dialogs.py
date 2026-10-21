"""#936: a page's own alert/confirm/prompt during a step."""
import types
import unittest

from personal_agent import browser_session as bs
from personal_agent import browser_worker as bw

WORKER = next(value for value in vars(bw).values() if isinstance(value, type) and hasattr(value, 'js_dialog'))


def worker(step=None, owner_visible=False):
    return types.SimpleNamespace(owner_visible=owner_visible, dialog_step=step)


def answer(state, kind, message):
    return WORKER.js_dialog(state, kind, message)


class PageDialogRule(unittest.TestCase):
    def test_a_confirmation_during_a_step_declared_mutate_is_accepted(self):
        state = worker({'approved': False, 'confirm_ok': True, 'effect': 'mutate', 'dialogs': []})
        self.assertIs(answer(state, 'confirm', '해당 상품을 삭제하시겠습니까?'), True)
        self.assertIsNone(answer(state, 'alert', '장바구니 상품이 삭제되었습니다'))
        self.assertEqual([row['outcome'] for row in state.dialog_step['dialogs']], ['accepted', 'shown'])

    def test_a_confirmation_after_a_step_declared_to_read_or_navigate_is_declined_and_recorded(self):
        """#174: the step's own declared effect answers the question; the AI reads it in its result."""
        for effect in ('read', 'navigate', None, 'MUTATE', 'anything'):
            for message in ('정말 탈퇴하시겠습니까?', '예약을 취소하시겠습니까?', 'Are you sure?'):
                state = worker({'approved': False, 'confirm_ok': True, 'effect': effect, 'dialogs': []})
                self.assertIs(answer(state, 'confirm', message), False, (effect, message))
                self.assertEqual(state.dialog_step['dialogs'], [{'kind': 'confirm', 'message': message,
                                                                 'outcome': 'declined_effect'}])

    def test_an_approved_payment_step_confirms_a_plain_question(self):
        """A step declared payment runs only with the owner's approval; its non-payment question is confirmed."""
        state = worker({'approved': False, 'confirm_ok': True, 'effect': 'payment', 'dialogs': []})
        self.assertIs(answer(state, 'confirm', 'Are you sure?'), True)
        self.assertIs(answer(state, 'confirm', '결제하시겠습니까?'), False, 'a typing approval never confirms a payment')

    def test_a_payment_confirmation_needs_an_approved_step(self):
        for effect in ('mutate', 'navigate', 'payment'):
            for message in ('결제하시겠습니까?', '주문을 진행할까요?', 'Proceed to checkout?', '12,000원을 송금합니다. 계속할까요?'):
                state = worker({'approved': False, 'confirm_ok': True, 'effect': effect, 'dialogs': []})
                self.assertIs(answer(state, 'confirm', message), False, message)
                self.assertEqual(state.dialog_step['dialogs'][0]['outcome'], 'declined_payment')
        approved = worker({'approved': True, 'dialogs': []})
        self.assertIs(answer(approved, 'confirm', '결제하시겠습니까?'), True)

    def test_a_plain_question_on_a_payment_page_is_not_confirmed(self):
        """#937 review: "Are you sure?" after an unapproved press on a page with a card field."""
        state = worker({'approved': False, 'confirm_ok': False, 'effect': 'mutate', 'dialogs': []})
        self.assertIs(answer(state, 'confirm', 'Are you sure?'), False)
        self.assertEqual(state.dialog_step['dialogs'][0]['outcome'], 'declined_payment')

    def test_a_step_without_the_new_fields_confirms_nothing(self):
        self.assertIs(answer(worker({'approved': False, 'dialogs': []}), 'confirm', '삭제할까요?'), False)

    def test_nothing_is_confirmed_outside_a_step(self):
        self.assertIs(answer(worker(None), 'confirm', '이 페이지를 떠나시겠습니까?'), False)

    def test_a_prompt_is_dismissed_and_records_are_bounded(self):
        state = worker({'approved': False, 'dialogs': []})
        self.assertIsNone(answer(state, 'prompt', '이름을 입력하세요'))
        for _ in range(10):
            answer(state, 'alert', 'x' * 500)
        self.assertEqual(len(state.dialog_step['dialogs']), bw.DIALOG_RECORDS)
        self.assertTrue(all(len(row['message']) <= bw.DIALOG_TEXT_LIMIT for row in state.dialog_step['dialogs']))


class StepWindow(unittest.TestCase):
    def test_ending_a_step_ends_its_dialog_rule(self):
        """#937 review P2: a timed-out step must not leave its approval for later dialogs."""
        state = types.SimpleNamespace(dialog_step={'approved': True, 'dialogs': []}, run=lambda *args, **kwargs: None)
        WORKER.end_step(state)
        self.assertIsNone(state.dialog_step)


class StepDialogsMediation(unittest.TestCase):
    def test_only_known_shapes_reach_the_model(self):
        rows = bs.step_dialogs({'dialogs': [{'kind': 'confirm', 'message': 'ok?', 'outcome': 'accepted'},
                                            {'kind': 'eval', 'message': 'x', 'outcome': 'accepted'},
                                            {'kind': 'alert', 'message': 'y', 'outcome': 'pwned'}, 'junk']})
        self.assertEqual(rows, [{'kind': 'confirm', 'message': 'ok?', 'outcome': 'accepted'}])
        self.assertEqual(bs.step_dialogs(None), [])

    def test_a_declined_question_reaches_the_step_result_with_what_to_do(self):
        """#174: the worker AI reads the page's question and that its step's declaration declined it."""
        from test_browser_session import ORIGIN, session, FakeDriver
        row = {'kind': 'confirm', 'message': '정말 탈퇴하시겠습니까?', 'outcome': 'declined_effect'}
        self.assertEqual(bs.step_dialogs({'dialogs': [row]}), [row])
        seen = []

        class Asking(FakeDriver):
            def click(inner, index, timeout, approved=False, confirm_ok=False, effect=None):
                seen.append(effect)
                FakeDriver.click(inner, index, timeout, approved, confirm_ok)
                return {'navigated': False, 'dialogs': bs.step_dialogs({'dialogs': [row]})}
        browser, _driver = session(Asking())
        browser.open({'url': ORIGIN + '/product', 'effect': 'read'})
        result = browser.click({'target': '내 계정', 'effect': 'navigate'})
        self.assertEqual(seen, ['navigate'], 'the declared effect crosses to the worker')
        self.assertEqual(result['dialogs'], [{'kind': 'confirm', 'message': '정말 탈퇴하시겠습니까?',
                                              'answer': bs.DIALOG_OUTCOMES['declined_effect']}])
        self.assertIn('mutate', result['dialogs'][0]['answer'])

    def test_commit_question(self):
        self.assertTrue(bs.commit_question('결제하시겠습니까?'))
        self.assertFalse(bs.commit_question('해당 상품을 삭제하시겠습니까?'))


if __name__ == '__main__':
    unittest.main()
