"""BROWSER-DIALOG-01 (#174): the fixed payment floor plus a judgment that can only add the owner's approval.

Evidence class: unit tests over the fake page driver and a scripted judgment (no model, no network).
The fixed ``COMMIT_*`` floor is unchanged; a press the floor did not flag is judged once, whatever
effect the worker declared (#188), and only a confident yes adds the approval requirement.
"""
import tempfile
import types
import unittest

from personal_agent import browser_session as bs
from personal_agent.agent_runtime import WORK_MODEL_TURNS, Capabilities, ToolError, WorkBudget
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import (OUTCOME_DECIDED, BinaryDecision, DecisionConfidence, FixtureDecisionEngine,
                                     RoutedDecisionEngine, UnavailableDecisionEngine, fixture_confidence)
from personal_agent.quickstart_store import QuickStore

from test_browser_session import ORIGIN, PAGES, Approvals, FakeDriver, session

#: One-press labels the fixed floor does not name (#174 audit F5).
MISSED = ('예약하기', 'Book now', 'Reserve', 'Comprar', 'Zahlungspflichtig bestellen')


class ScriptedJudgments:
    """The ``press_commits_payment`` surface with a scripted outcome; records every question."""

    def __init__(self, outcome='yes', raises=False):
        self.outcome, self.raises, self.asked = outcome, raises, []

    def press_commits_payment(self, control, nearby_text, page_title='', work_id=None):
        self.asked.append({'control': control, 'nearby_text': nearby_text, 'page_title': page_title,
                           'work_id': work_id})
        if self.raises:
            raise RuntimeError('engine down')
        return types.SimpleNamespace(outcome=self.outcome)


class NearDriver(FakeDriver):
    """The fixture driver, with the page's ``near`` text reported for each control as the page script does."""

    near = '129,000원'

    def snapshot(self):
        page = super().snapshot()
        for element in page.get('elements') or ():
            element['context'] = self.near
        return page


def opened(judgments, approvals=None, **kwargs):
    browser, driver = session(judgments=judgments, approvals=approvals or Approvals(), **kwargs)
    browser.open({'url': ORIGIN + '/one-press', 'effect': 'navigate'})
    return browser, driver


class FloorTests(unittest.TestCase):
    def test_the_floor_misses_the_audited_labels_and_still_flags_its_own(self):
        for label in MISSED:
            self.assertFalse(bs.commit_name(label), label)
        self.assertTrue(bs.commit_name('Buy now'))


class PressJudgmentTests(unittest.TestCase):
    def test_a_mutate_press_the_floor_misses_needs_approval_when_the_judgment_says_yes(self):
        for label in MISSED:
            with self.subTest(label=label):
                judgments, approvals = ScriptedJudgments('yes'), Approvals()
                browser, driver = opened(judgments, approvals)
                with self.assertRaises(ToolError) as caught:
                    browser.click({'target': label, 'effect': 'mutate'})
                self.assertEqual((caught.exception.code, caught.exception.requires),
                                 ('approval_required', 'browser-step-approval'))
                self.assertFalse([entry for entry in driver.log if entry[0] == 'click'], 'nothing was pressed')
                self.assertEqual(len(approvals.requests), 1)
                self.assertIn(label, approvals.requests[0][1])
                self.assertTrue(approvals.requests[0][1].startswith('결제 가능성'), approvals.requests[0][1])
                self.assertEqual(len(judgments.asked), 1)
                self.assertEqual(judgments.asked[0]['control'], f'button: {label}')
                self.assertEqual(judgments.asked[0]['page_title'], '한 번에')
                self.assertEqual(judgments.asked[0]['work_id'], 'work-1')

    def test_the_owner_approving_that_press_lets_it_through_without_asking_again(self):
        judgments, approvals = ScriptedJudgments('yes'), Approvals()
        browser, driver = opened(judgments, approvals)
        with self.assertRaises(ToolError):
            browser.click({'target': '예약하기', 'effect': 'mutate'})
        approvals.issue(approvals.requests[0][0])
        browser.click({'target': '예약하기', 'effect': 'mutate'})
        self.assertEqual(driver.approved, [True])
        self.assertEqual(len(judgments.asked), 1, 'one press is judged at most once')

    def test_no_unavailable_or_a_failing_judgment_leaves_the_floor_answer(self):
        for judgments in (ScriptedJudgments('no'), ScriptedJudgments('unavailable'), ScriptedJudgments(raises=True)):
            with self.subTest(outcome=judgments.outcome, raises=judgments.raises):
                approvals = Approvals()
                browser, driver = opened(judgments, approvals)
                browser.click({'target': 'Book now', 'effect': 'mutate'})
                self.assertEqual(driver.approved, [False])
                self.assertEqual(approvals.requests, [])
                self.assertEqual(len(judgments.asked), 1)

    def test_without_judgments_the_press_is_the_floor_answer(self):
        browser, driver = opened(None)
        browser.click({'target': '예약하기', 'effect': 'mutate'})
        self.assertEqual(driver.approved, [False])

    def test_the_declared_effect_cannot_skip_the_payment_judgment(self):
        """#188 (Codex on PR #186): a press declared read or navigate is the same press, so it is judged too."""
        for effect in ('read', 'navigate'):
            with self.subTest(effect=effect):
                judgments, approvals = ScriptedJudgments('yes'), Approvals()
                browser, driver = opened(judgments, approvals)
                with self.assertRaises(ToolError) as caught:
                    browser.click({'target': '예약하기', 'effect': effect})
                self.assertEqual((caught.exception.code, caught.exception.requires),
                                 ('approval_required', 'browser-step-approval'))
                self.assertFalse([entry for entry in driver.log if entry[0] == 'click'], 'nothing was pressed')
                self.assertEqual(len(judgments.asked), 1)
                self.assertEqual(len(approvals.requests), 1)
                approvals.issue(approvals.requests[0][0])
                browser.click({'target': '예약하기', 'effect': effect})
                self.assertEqual(driver.approved, [True], 'only the approved press goes through')

    def test_a_read_or_navigate_press_judged_no_goes_through_unapproved(self):
        for effect in ('read', 'navigate'):
            with self.subTest(effect=effect):
                judgments, approvals = ScriptedJudgments('no'), Approvals()
                browser, driver = opened(judgments, approvals)
                browser.click({'target': '예약하기', 'effect': effect})
                self.assertEqual((driver.approved, approvals.requests, len(judgments.asked)), ([False], [], 1))

    def in_form(self, judgments, approvals=None):
        """A one-press control inside a form whose price and title sit outside that form."""
        pages = dict(PAGES)
        pages['/in-form'] = ('<html><head><title>객실 1박</title></head><body><p>129,000원</p>'
                             '<form action="/order" method="post"><button type="button">예약하기</button></form>'
                             '</body></html>')
        driver = NearDriver(pages)
        browser, driver = session(driver, judgments=judgments, approvals=approvals or Approvals())
        browser.open({'url': ORIGIN + '/in-form', 'effect': 'navigate'})
        return browser, driver

    def test_a_changed_price_or_title_is_judged_again_after_a_no(self):
        """#188 (Codex on #186's diff): the judged facts are in the binding, so a cached no cannot cover new ones."""
        judgments = ScriptedJudgments('no')
        browser, driver = self.in_form(judgments)
        browser.click({'target': '예약하기', 'effect': 'mutate'})
        browser.click({'target': '예약하기', 'effect': 'mutate'})
        self.assertEqual(len(judgments.asked), 1, 'the same facts are judged once')
        driver.near = '1,290,000원'  # the sibling price changed; the form's own state did not
        judgments.outcome = 'yes'
        with self.assertRaises(ToolError) as caught:
            browser.click({'target': '예약하기', 'effect': 'mutate'})
        self.assertEqual(caught.exception.code, 'approval_required')
        self.assertEqual(len(judgments.asked), 2)
        self.assertIn('1,290,000원', judgments.asked[-1]['nearby_text'])
        self.assertEqual(driver.approved, [False, False], 'the changed press did not run')

    def test_an_approval_for_the_old_facts_is_not_spent_on_new_ones(self):
        judgments, approvals = ScriptedJudgments('yes'), Approvals()
        browser, driver = self.in_form(judgments, approvals)
        with self.assertRaises(ToolError):
            browser.click({'target': '예약하기', 'effect': 'mutate'})
        approvals.issue(approvals.requests[0][0])
        driver.pages['/in-form'] = driver.pages['/in-form'].replace('<title>객실 1박</title>', '<title>객실 7박</title>')
        with self.assertRaises(ToolError) as caught:
            browser.click({'target': '예약하기', 'effect': 'mutate'})
        self.assertEqual(caught.exception.code, 'approval_required')
        self.assertEqual(len(approvals.requests), 2, 'the new facts ask the owner again')
        self.assertNotEqual(approvals.requests[0][0]['state_digest'], approvals.requests[1][0]['state_digest'])
        self.assertFalse([entry for entry in driver.log if entry[0] == 'click'], 'nothing was pressed')

    def test_a_press_the_floor_already_flags_is_never_judged_and_the_judgment_cannot_clear_it(self):
        for effect in ('mutate', 'navigate', 'read'):
            with self.subTest(effect=effect):
                judgments, approvals = ScriptedJudgments('no'), Approvals()
                browser, driver = opened(judgments, approvals)
                with self.assertRaises(ToolError) as caught:
                    browser.click({'target': 'Buy now', 'effect': effect})
                self.assertEqual(caught.exception.code, 'approval_required')
                self.assertEqual(judgments.asked, [], 'the floor decided; nothing was asked')
        judgments = ScriptedJudgments('no')
        browser, _driver = opened(judgments)
        with self.assertRaises(ToolError) as caught:
            browser.click({'target': '메모 저장', 'effect': 'payment'})
        self.assertEqual(caught.exception.code, 'approval_required', "the model's payment label still adds it")
        self.assertEqual(judgments.asked, [])

    def test_a_typing_step_is_never_judged(self):
        judgments = ScriptedJudgments('yes')
        browser, _driver = session(judgments=judgments, approvals=Approvals())
        browser.open({'url': ORIGIN + '/product', 'effect': 'navigate'})
        browser.type({'target': '검색어', 'text': '세제', 'effect': 'mutate'})
        self.assertEqual(judgments.asked, [])

    def test_the_judgment_spends_one_turn_of_the_work_budget_or_leaves_the_floor(self):
        """The routed engine admits the judgment against the Work's turns, as every Work judgment."""
        asked = []

        def judge(context, proposition):
            asked.append(context.purpose)
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())

        for turns, expected in ((WORK_MODEL_TURNS, 'approval_required'), (0, None)):
            with self.subTest(turns=turns):
                asked.clear()
                budget = WorkBudget(turns=turns)
                engine = RoutedDecisionEngine(lambda: FixtureDecisionEngine(judge=judge), budget=lambda: budget)
                browser, driver = opened(ConversationJudgments(engine), budget=budget)
                if expected:
                    with self.assertRaises(ToolError) as caught:
                        browser.click({'target': 'Reserve', 'effect': 'mutate'})
                    self.assertEqual(caught.exception.code, expected)
                    self.assertEqual((asked, budget.turns_used), (['browser-press-payment'], 1))
                else:
                    browser.click({'target': 'Reserve', 'effect': 'mutate'})
                    self.assertEqual(asked, [], 'no turn left: the engine is not contacted, the floor answers')
                    self.assertEqual(driver.approved, [False])

    def test_an_engine_that_cannot_answer_costs_no_turn(self):
        budget = WorkBudget()
        engine = RoutedDecisionEngine(UnavailableDecisionEngine, budget=lambda: budget)
        browser, driver = opened(ConversationJudgments(engine), budget=budget)
        browser.click({'target': 'Reserve', 'effect': 'mutate'})
        self.assertEqual(driver.approved, [False], 'no decision provider: the floor answers')
        self.assertEqual(budget.turns_used, 0)

    def test_the_work_hands_its_judgments_to_the_browser_session(self):
        judgments = ScriptedJudgments()
        capabilities = Capabilities(QuickStore(tempfile.mkdtemp()), None, {}, '', 'w', lambda *a: None,
                                    browser=lambda: FakeDriver(), judgments=judgments)
        self.assertIs(capabilities.browser_session().judgments, judgments)


class ConversationJudgmentTests(unittest.TestCase):
    class Engine:
        def __init__(self, answer, probability):
            self.answer, self.probability, self.contexts, self.propositions = answer, probability, [], []

        def judge(self, context, proposition):
            self.contexts.append(context)
            self.propositions.append(proposition)
            return BinaryDecision(OUTCOME_DECIDED, answer=self.answer,
                                  confidence=DecisionConfidence(self.probability, provider='scripted'))

    def test_only_a_confident_answer_counts(self):
        for answer, probability, expected in ((True, 0.9, 'yes'), (False, 0.9, 'no'), (True, 0.5, 'unavailable')):
            engine = self.Engine(answer, probability)
            judged = ConversationJudgments(engine).press_commits_payment('button: 예약하기', '129,000원', '객실')
            self.assertEqual(judged.outcome, expected, (answer, probability))
        unavailable = ConversationJudgments(UnavailableDecisionEngine()).press_commits_payment('button: Reserve', '')
        self.assertEqual(unavailable.outcome, 'unavailable')

    def test_the_facts_are_redacted_and_the_question_names_no_site_or_category(self):
        engine = self.Engine(True, 0.9)
        judgments = ConversationJudgments(engine, redactor=lambda text, private=True: text.replace('M12345678', '[가림]'))
        judgments.press_commits_payment('button: Book now', '보관 위치 M12345678', 'Page', work_id='work-1')
        context = engine.contexts[0]
        self.assertEqual(context.purpose, 'browser-press-payment')
        self.assertEqual(context.work_id, 'work-1')
        self.assertEqual(context.facts['nearby_text'], '보관 위치 [가림]')
        proposition = engine.propositions[0].lower()
        for token in ('book', 'cart', 'kyobo', 'coupang', 'naver', 'hotel', 'flight'):
            self.assertNotIn(token, proposition)


if __name__ == '__main__':
    unittest.main()
