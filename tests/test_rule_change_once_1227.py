"""#1227: rule changes from one owner Work reach the owner once, as what changed.

Evidence class: model-free, fixture transports (as ``test_entrusted_lines_798``): a scripted
compatible-API model, a recording Telegram transport, a FixtureDecisionEngine and a fake clock;
the real QuickStore, ``AgentService``, ``run_agent`` and ``Capabilities`` run unchanged.  No live
model, Telegram or provider is contacted, and none is claimed.
"""
from personal_agent import entrusted_lines as el
from personal_agent.messages import msg

import test_entrusted_lines_798 as lines798
from test_entrusted_lines_798 import TASK, _LineCase, tool_results
from test_preparations import call

RULE_A = '거래가 없어도 한 시간마다 보고하기'
RULE_B = '20시 전까지만 하기'
KEPT = '하루 10만원까지만'


class _OnceCase(_LineCase):
    def revise_calls(self, work_text, *calls):
        """One owner message in which the AI makes several ``entrusted_line`` calls; returns their results."""
        seen = []
        steps = [(lambda body, args=args: {'content': None, 'tool_calls': [call(f'line{index}', 'entrusted_line', **args)]})
                 for index, args in enumerate(calls)]

        def answer(body):
            seen.extend(tool_results(body, 'entrusted_line'))
            return {'content': '알겠습니다.'}

        self.script = [*steps, answer]
        work = self.receive(work_text)
        return work, seen

    def rule_notices(self):
        return [body for body in self.sends() if str(body.get('text', '')).startswith(msg('lines.rules_changed', title=TASK))]

    def message_id_of(self, body):
        return 9000 + next(index for index, (_method, item) in enumerate(self.telegram, 1) if item is body)

    def undo_data(self, body):
        [[button]] = body['reply_markup']['inline_keyboard']
        self.assertTrue(button['callback_data'].endswith(':undo'))
        return button['callback_data']

    def queued_rule_notices(self, work):
        with self.store.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM telegram_notifications WHERE job_id=? AND kind LIKE ?',
                                                    (work, el.NOTICE_RULE + '%'))]

    def clear_opening(self):
        self.deliver_all()
        self.telegram.clear()


class OneNoticeTests(_OnceCase):
    def test_two_rule_changes_in_one_work_are_one_notice_listing_both_and_undo_reverts_both(self):
        brief, _work = self.open_line()
        line_id = brief['line_id']
        self.clear_opening()
        work, results = self.revise_calls('앞으로 거래 없어도 한 시간마다 보고하고, 20시 전까지만 해 줘',
                                          {'action': 'revise', 'line': line_id, 'expected_version': '1', 'rule': RULE_A},
                                          {'action': 'revise', 'line': line_id, 'expected_version': '2', 'rule': RULE_B})
        self.assertEqual([result['version'] for result in results], [2, 3])
        self.assertEqual(len(self.queued_rule_notices(work)), 1)
        self.deliver_all()
        [notice] = self.rule_notices()
        self.assertIn(msg('lines.rule_added', rule=RULE_A), notice['text'])
        self.assertIn(msg('lines.rule_added', rule=RULE_B), notice['text'])
        # No other message restates either rule.
        self.assertEqual([body for body in self.sends() if RULE_A in str(body.get('text'))], [notice])
        self.tap(self.undo_data(notice), self.message_id_of(notice))
        current = self.service.lines.current(line_id)
        # One undo puts back the brief from before the first change of the set, exactly.
        self.assertEqual((current['version'], current['change'], current['rules']), (4, 'undo', []))
        self.assertEqual(current['task'], self.service.lines.version(line_id, 1)['task'])
        self.assertIn(msg('lines.undone'), [str(body.get('text')) for method, body in self.telegram if method == 'editMessageText'])

    def test_a_change_set_undo_after_a_later_change_is_stale_and_changes_nothing(self):
        brief, _work = self.open_line()
        line_id = brief['line_id']
        self.clear_opening()
        self.revise_calls('두 가지 규칙 정해 줘',
                          {'action': 'revise', 'line': line_id, 'expected_version': '1', 'rule': RULE_A},
                          {'action': 'revise', 'line': line_id, 'expected_version': '2', 'rule': RULE_B})
        self.deliver_all()
        [notice] = self.rule_notices()
        # A later owner Work changes the line again before the owner taps Undo.
        self.revise_calls('보고는 30분마다로 해 줘',
                          {'action': 'revise', 'line': line_id, 'expected_version': '3', 'drop_rule': '1', 'rule': '30분마다 보고하기'})
        self.assertEqual(self.service.lines.current(line_id)['version'], 4)
        self.tap(self.undo_data(notice), self.message_id_of(notice))
        current = self.service.lines.current(line_id)
        self.assertEqual((current['version'], [rule['text'] for rule in current['rules']]), (4, [RULE_B, '30분마다 보고하기']))

    def test_a_change_the_notice_does_not_show_splits_the_set_so_undo_never_reverts_it(self):
        brief, _work = self.open_line()
        line_id = brief['line_id']
        self.clear_opening()
        self.revise_calls('규칙 하나 넣고 이름 바꾸고 하나 더 넣어 줘',
                          {'action': 'revise', 'line': line_id, 'expected_version': '1', 'rule': RULE_A},
                          {'action': 'revise', 'line': line_id, 'expected_version': '2', 'title': '아침 요약'},
                          {'action': 'revise', 'line': line_id, 'expected_version': '3', 'rule': RULE_B})
        self.deliver_all()
        notices = [body for body in self.sends() if str(body.get('text', '')).startswith(msg('lines.rules_changed', title='아침 요약'))]
        self.assertEqual(len(notices), 2)
        later = next(body for body in notices if RULE_B in body['text'])
        self.assertNotIn(RULE_A, later['text'])
        self.tap(self.undo_data(later), self.message_id_of(later))
        current = self.service.lines.current(line_id)
        # Only the later rule is undone; the title change, which no notice showed, stays.
        self.assertEqual((current['version'], current['title'], [rule['text'] for rule in current['rules']]),
                         (5, '아침 요약', [RULE_A]))

    def test_a_replacement_in_one_call_reads_before_and_after(self):
        brief, _work = self.open_line(rules=[RULE_A])
        line_id = brief['line_id']
        self.clear_opening()
        self.revise_calls('보고는 30분마다로 바꿔 줘',
                          {'action': 'revise', 'line': line_id, 'expected_version': '1', 'drop_rule': '1', 'rule': '30분마다 보고하기'})
        self.deliver_all()
        [notice] = self.rule_notices()
        self.assertIn(msg('lines.rule_replaced', before=RULE_A, after='30분마다 보고하기'), notice['text'])

    def test_a_rule_added_then_dropped_in_the_same_work_is_not_told(self):
        brief, _work = self.open_line()
        line_id = brief['line_id']
        self.clear_opening()
        self.revise_calls('그 규칙 넣었다가 다시 빼 줘',
                          {'action': 'revise', 'line': line_id, 'expected_version': '1', 'rule': RULE_A},
                          {'action': 'revise', 'line': line_id, 'expected_version': '2', 'drop_rule': '1'})
        self.deliver_all()
        self.assertEqual(self.rule_notices(), [])
        self.assertFalse([body for body in self.sends() if RULE_A in str(body.get('text'))])

    def test_a_rule_changed_then_undone_before_delivery_is_not_told(self):
        brief, _work = self.open_line()
        line_id = brief['line_id']
        self.clear_opening()
        self.revise_calls('앞으로 한 시간마다 보고해 줘',
                          {'action': 'revise', 'line': line_id, 'expected_version': '1', 'rule': RULE_A})
        # A second owner Work undoes it before the first Work's notice went out.
        self.revise_calls('아니 방금 거 되돌려 줘', {'action': 'undo', 'line': line_id, 'version': '2'})
        self.assertEqual(self.service.lines.current(line_id)['rules'], [])
        self.deliver_all()
        self.assertEqual(self.rule_notices(), [])
        self.assertFalse([body for body in self.sends() if RULE_A in str(body.get('text'))])

    def test_a_web_conversation_says_the_change_in_the_reply_and_queues_no_notice(self):
        brief, _work = self.open_line()
        line_id = brief['line_id']
        seen = []

        def step(body):
            return {'content': None, 'tool_calls': [call('line', 'entrusted_line', action='revise', line=line_id,
                                                         expected_version='1', rule=RULE_A)]}

        def answer(body):
            seen.extend(tool_results(body, 'entrusted_line'))
            return {'content': '알겠습니다.'}

        self.script = [step, answer]
        work = self.web_turn('앞으로 한 시간마다 보고해 줘', owner_typed=True)
        [result] = seen
        # The reply is the one place it is told: nothing is queued beside it.
        self.assertIn(msg('lines.rule_added', rule=RULE_A), result['next_step'])
        self.assertEqual(self.queued_rule_notices(work), [])


class MoneyOnceTests(_OnceCase):
    touch_money = lines798.MoneyTests.touch_money
    owner_turn = lines798.MoneyTests.owner_turn

    def test_a_money_line_notice_shows_the_changed_rule_with_the_owners_words_and_no_unchanged_rule(self):
        brief, _work = self.open_line(rules=[KEPT])
        self.touch_money(brief)
        self.owner_turn(True)
        self.clear_opening()
        self.revise_calls('앞으로는 20시 전까지만 해 줘',
                          {'action': 'revise', 'line': brief['line_id'], 'expected_version': '1', 'rule': RULE_B})
        self.deliver_all()
        [notice] = self.rule_notices()
        self.assertIn(msg('lines.rule_added', rule=RULE_B), notice['text'])
        self.assertIn('앞으로는 20시 전까지만 해 줘', notice['text'])
        # The unchanged rule and the words it came from are not restated.
        self.assertNotIn(KEPT, notice['text'])
        self.assertNotIn('앞으로 매일 아침 관심 목록 확인해서 정리해 줘', notice['text'])

    def test_dropping_a_money_line_rule_is_told_explicitly(self):
        brief, _work = self.open_line(rules=[KEPT, RULE_B])
        self.touch_money(brief)
        self.owner_turn(True)
        self.clear_opening()
        self.revise_calls('하루 한도는 없애 줘',
                          {'action': 'revise', 'line': brief['line_id'], 'expected_version': '1', 'drop_rule': '1'})
        self.deliver_all()
        [notice] = self.rule_notices()
        self.assertIn(msg('lines.rule_dropped', rule=KEPT), notice['text'])
        self.assertNotIn(RULE_B, notice['text'])
