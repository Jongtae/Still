"""#798: entrusted lines - one continuing responsibility with an identity of its own.

Evidence class: model-free, fixture transports, as ``test_preparations``: a scripted
compatible-API model, a recording Telegram transport, a FixtureDecisionEngine and a fake
clock; the real QuickStore, ``AgentService``, ``run_agent`` and ``Capabilities`` run
unchanged.  No live model, Telegram or provider is contacted, and none is claimed.
"""
import json

from personal_agent import entrusted_lines as el
from personal_agent import preparations as prep
from personal_agent.agent_runtime import Capabilities, ToolError
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from personal_agent.messages import msg
from personal_agent.owner_forget import OwnerForget
from personal_agent.quickstart_store import QuickStore

from test_preparations import CHAT, _Case, call, engine

TASK = '매일 아침 관심 목록을 확인해 정리해 주기'
PROMISE = '매일 아침 9시에 정리한 내용을'
RULE = '거래가 없어도 한 시간마다 보고하기'


def tool_results(body, name=None):
    """The JSON tool results the model has been shown so far, oldest first."""
    calls = {item['id']: item['function']['name'] for message in body['messages'] if message.get('tool_calls')
             for item in message['tool_calls']}
    out = []
    for message in body['messages']:
        if message.get('role') == 'tool' and (name is None or calls.get(message['tool_call_id']) == name):
            try:
                out.append(json.loads(message['content']))
            except ValueError:
                out.append(message['content'])
    return out


def text_of(body):
    return json.dumps(body['messages'], ensure_ascii=False)


class _LineCase(_Case):
    def open_line(self, *, rules=None, schedule=True, recurrence='daily', delivery=None, extra=None):
        """The owner hands over continuing work; the AI opens a line and schedules its work."""
        args = {'action': 'open', 'task': TASK, 'report_promise': PROMISE}
        if rules:
            args['rules'] = '\n'.join(rules)
        steps = [{'content': None, 'tool_calls': [call('open', 'entrusted_line', **args)]}]
        if schedule:
            def schedule_step(body):
                [opened] = tool_results(body, 'entrusted_line')
                prepare = {'offer': '다음 점검 결과를 정리해 드려요.', 'kind': 'prepare', 'goal': '관심 목록 확인 후 정리', 'due': self.due_iso(3600),
                           'line': opened['line_id']}
                if recurrence:
                    prepare['recurrence'] = recurrence
                if delivery:
                    prepare['delivery'] = delivery
                prepare.update(extra or {})
                return {'content': None, 'tool_calls': [call('schedule', 'schedule_preparation', **prepare)]}
            steps.append(schedule_step)
        steps.append({'content': '앞으로 맡아서 할게요.'})
        self.script = steps
        work = self.receive('앞으로 매일 아침 관심 목록 확인해서 정리해 줘')
        # The newest active line is the one this Work opened.
        brief = self.service.lines.lines()[0]
        self.assertEqual(brief['written_in'], work)
        return brief, work

    def deliver_all(self):
        for _ in range(6):
            self.service.deliver_one()
        for _ in range(6):
            self.service.deliver_notification()

    def work_rows(self, line_id):
        return [row for row in self.service.preparations_service.line_rows(line_id)
                if row.get('origin') != prep.ORIGIN_LINE_ACCOUNT]

    def line_call(self, work_text, **args):
        """One owner message in which the AI makes one ``entrusted_line`` call; returns its result."""
        seen = []

        def step(body):
            return {'content': None, 'tool_calls': [call('line', 'entrusted_line', **args)]}

        def answer(body):
            seen.extend(tool_results(body, 'entrusted_line'))
            return {'content': '알겠습니다.'}

        self.script = [step, answer]
        self.receive(work_text)
        return seen[-1]

    def run_line(self, row, steps=None):
        """Run one due row of a line through the tick; returns the model bodies the run saw."""
        start = len(self.model_calls)
        # The plan call reads the script too; every step is given twice so the worker gets it.
        self.script = [step for step in (steps or [{'content': '오늘 정리: 변동 없음.'}]) for _ in range(2)]
        self.now = max(self.now, row['due_at']) + 1
        self.assertTrue(self.tick())
        self.assertTrue(self.service.run_one())
        # A run whose result is sent settles once its reply was delivered (earlier replies go first).
        self.deliver_all()
        self.assertTrue(self.tick())
        return self.model_calls[start:]


class OpeningTests(_LineCase):
    def test_announcement_is_sent_once_per_line_with_its_three_parts_and_a_stop_control(self):
        brief, _work = self.open_line()
        self.assertTrue(all(rule.get('basis') is None for rule in brief['rules']))
        self.assertEqual((brief['task'], brief['report_promise'], brief['state'], brief['version']),
                         (TASK, PROMISE, el.STATE_ACTIVE, 1))
        self.telegram.clear()
        for _ in range(3):
            self.service.deliver_notification()
        announcement = msg('lines.announced', task=TASK, promise=PROMISE)
        announced = [body for body in self.sends() if body.get('text') == announcement]
        self.assertEqual(len(announced), 1)
        [body] = announced
        self.assertIn(TASK, body['text'])
        self.assertIn(PROMISE, body['text'])
        self.assertIn('"그만"', body['text'])
        self.assertEqual(body['reply_markup']['inline_keyboard'][0][0]['callback_data'].split(':')[2], 'stop')
        # A second Work about the same line sends no second announcement.
        self.line_call('관심 목록 일 어떻게 되고 있어?', action='read', line=brief['line_id'])
        self.telegram.clear()
        for _ in range(3):
            self.service.deliver_notification()
        self.assertFalse([body for body in self.sends() if body.get('text') == announcement])

    def test_in_a_conversation_without_notices_the_reply_is_told_to_carry_the_announcement(self):
        seen = []

        def opening(body):
            return {'content': None, 'tool_calls': [call('open', 'entrusted_line', action='open', task=TASK, report_promise=PROMISE)]}

        def answer(body):
            seen.extend(tool_results(body, 'entrusted_line'))
            return {'content': '맡을게요.'}
        self.script = [opening, answer]
        self.web_turn('앞으로 매일 아침 관심 목록 정리해 줘', owner_typed=True)
        [opened] = seen
        self.assertIn(msg('lines.announced', task=TASK, promise=PROMISE), opened['next_step'])

    def test_the_lines_work_is_linked_and_its_periodic_account_is_scheduled(self):
        brief, _work = self.open_line()
        [work] = self.work_rows(brief['line_id'])
        self.assertEqual((work['line_id'], work['state'], work['recurrence']), (brief['line_id'], prep.STATE_SCHEDULED, 'daily'))
        [account] = [row for row in self.service.preparations_service.line_rows(brief['line_id'])
                     if row.get('origin') == prep.ORIGIN_LINE_ACCOUNT]
        self.assertEqual((account['state'], account['recurrence'], account['accepted_by']),
                         (prep.STATE_SCHEDULED, 'RRULE:FREQ=DAILY;INTERVAL=7', prep.ACCEPTED_OWNER_WORK))
        self.assertEqual(account['due_at'], self.now + 7 * 86400)


class ContinuityTests(_LineCase):
    def run_input(self, bodies):
        """The request the run's worker read (the last user turn of its first model call)."""
        users = [message for message in bodies[0]['messages'] if message.get('role') == 'user']
        return users[-1]['content'] if isinstance(users[-1]['content'], str) else json.dumps(users[-1]['content'], ensure_ascii=False)

    def test_another_pending_work_suppresses_the_note_and_marker(self):
        brief, _ = self.open_line(recurrence=None)
        line = brief['line_id']
        [first] = self.work_rows(line)
        self.script = [
            {'content': None, 'tool_calls': [call('next', 'schedule_preparation', offer='다음 점검 결과를 정리해 드려요.', kind='prepare',
                                                  goal='다음 단계', due=self.due_iso(86400), line=line)]},
            {'content': '다음에도 이어서 확인할게요.'},
        ]
        self.receive('내일도 이어서 확인해 줘')
        [schedule] = tool_results(self.model_calls[-1], 'schedule_preparation')
        self.assertTrue(schedule['scheduled'])
        self.assertNotIn('line_schedule_note', schedule)
        bodies = self.run_line(first)
        self.assertNotIn(msg('lines.no_following_run'), self.run_input(bodies))
        lines = self.service.preparations_service
        listing = lines.lines_listing()
        self.assertNotIn('nothing_scheduled', listing['lines'][0])
        self.assertNotIn(msg('lines.blocked_unscheduled'), listing['text'])
        self.assertNotIn(msg('lines.blocked_unscheduled'), lines.lines_index({'id': 'probe'}))

    def test_a_stopped_line_has_no_note_or_marker(self):
        brief, work = self.open_line(recurrence=None)
        [row] = self.work_rows(brief['line_id'])
        lines = self.service.preparations_service
        self.line_call('이제 그만해 줘', action='stop', line=brief['line_id'], scope='line')
        listing = lines.lines_listing()
        self.assertEqual(listing['lines'][0]['state'], el.STATE_STOPPED)
        self.assertNotIn('nothing_scheduled', listing['lines'][0])
        self.assertNotIn(msg('lines.blocked_unscheduled'), listing['text'])
        self.assertEqual(lines.lines_index({'id': 'probe'}), '')
        self.assertNotIn(msg('lines.no_following_run'), lines.line_run_input({'id': work}, row))

    def test_a_periodic_account_does_not_prompt_the_ai_to_continue_a_finished_mission(self):
        brief, _ = self.open_line(recurrence=None)
        [row] = self.work_rows(brief['line_id'])
        self.run_line(row, [{'content': '필요한 확인을 모두 마쳤으므로 이 일은 끝났습니다.'}])
        account = self.service.preparations_service.line_account_row(brief['line_id'])
        seen = self.run_input(self.run_line(account))
        self.assertIn('이 일은 끝났습니다.', seen)
        self.assertNotIn(msg('lines.no_following_run'), seen)

    def test_a_recurring_row_gets_the_note_only_on_its_last_run(self):
        brief, _ = self.open_line(recurrence=None, extra={'rrule': 'FREQ=DAILY;COUNT=2'})
        [row] = self.work_rows(brief['line_id'])
        [schedule] = tool_results(self.model_calls[-1], 'schedule_preparation')
        self.assertNotIn('line_schedule_note', schedule)
        note = msg('lines.no_following_run')
        self.assertNotIn(note, self.run_input(self.run_line(row)))
        following = self.service.preparations.get(row['id'])
        self.assertEqual(following['state'], prep.STATE_SCHEDULED)
        self.assertIn(note, self.run_input(self.run_line(following)))

    def test_the_last_run_of_a_bounded_window_can_schedule_a_piece_for_another_day(self):
        brief, _ = self.open_line(recurrence=None, extra={'every_minutes': '5', 'until': self.due_iso(3900)})
        line = brief['line_id']
        [row] = self.work_rows(line)
        note = msg('lines.no_following_run')
        self.assertNotIn(note, self.run_input(self.run_line(row)))
        last = self.service.preparations.get(row['id'])

        def next_piece(_body):
            return {'content': None, 'tool_calls': [call('next', 'schedule_preparation', offer='다음 점검 결과를 정리해 드려요.', kind='prepare',
                                                          goal='다음 단계', due=self.due_iso(86400), line=line)]}

        bodies = self.run_line(last, [next_piece, {'content': '내일 이어서 확인할게요.'}])
        self.assertIn(note, self.run_input(bodies))
        [piece] = [item for item in self.work_rows(line) if item.get('origin') == prep.ORIGIN_LINE_PIECE]
        self.assertEqual(piece['state'], prep.STATE_SCHEDULED)
        self.assertGreaterEqual(piece['due_at'], last['due_at'] + 86400)

    def test_a_replaced_row_keeps_its_line_and_the_next_run_reads_earlier_outcomes_with_their_labels(self):
        brief, _work = self.open_line(delivery='send')
        [first] = self.work_rows(brief['line_id'])
        self.run_line(first, [{'content': '첫 정리: 항목 A가 올랐습니다.'}])

        def replace(body):
            return {'content': None, 'tool_calls': [call('replace', 'schedule_preparation', offer='다음 점검 결과를 정리해 드려요.', kind='prepare',
                                                          goal='관심 목록 확인 후 표로 정리', due=self.due_iso(7200),
                                                          recurrence='daily', delivery='keep', replaces=first['id'])]}
        self.script = [replace, {'content': '표로 바꿨어요.'}]
        self.receive('정리를 표로 바꿔 줘')
        rows = self.work_rows(brief['line_id'])
        [second] = [row for row in rows if row['state'] == prep.STATE_SCHEDULED]
        self.assertEqual(second['line_id'], brief['line_id'])
        self.assertEqual(self.service.preparations.get(first['id'])['state'], prep.STATE_CANCELLED)

        bodies = self.run_line(second, [{'content': '둘째 정리: 표.'}])
        seen = self.run_input(bodies)
        self.assertIn(TASK, seen)
        self.assertIn('첫 정리: 항목 A가 올랐습니다.', seen)
        self.assertIn(msg('lines.outcome_delivered', at='', text='').split(' - ')[0].split(': ')[1], seen)
        # The kept second run reads as kept, not delivered, to the run after it.
        outcomes = self.service.preparations_service.line_outcomes(brief['line_id'])
        self.assertEqual([item['outcome'] for item in outcomes[:2]], [el.OUTCOME_KEPT, el.OUTCOME_DELIVERED])

    def test_a_line_outlives_a_period_with_nothing_scheduled_and_an_owner_restart_continues_it(self):
        brief, _work = self.open_line(recurrence=None)
        [first] = self.work_rows(brief['line_id'])
        schedule = tool_results(self.model_calls[-1], 'schedule_preparation')[-1]
        note = msg('lines.no_following_run')
        self.assertEqual(schedule['line_schedule_note'], note)
        bodies = self.run_line(first, [{'content': '한 번 정리했습니다.'}])
        self.assertIn(note, self.run_input(bodies))
        self.assertNotIn(self.service.preparations.get(first['id'])['state'], prep.ACTIVE_STATES)
        self.assertEqual(self.service.lines.current(brief['line_id'])['state'], el.STATE_ACTIVE)
        lines = self.service.preparations_service
        listing = lines.lines_listing()
        [entry] = listing['lines']
        self.assertTrue(entry.get('next_is_account'))
        self.assertTrue(entry['nothing_scheduled'])
        self.assertIn(msg('lines.blocked_unscheduled'), listing['text'])
        self.assertIn(msg('lines.blocked_unscheduled'), lines.lines_index({'id': 'probe'}))

        # Restart Still, then the owner starts the same work again: the AI continues the line.
        self.store = QuickStore(self.root)
        self.service = self.make_service()

        def again(body):
            return {'content': None, 'tool_calls': [call('again', 'schedule_preparation', offer='다음 점검 결과를 정리해 드려요.', kind='prepare', goal='관심 목록 확인 후 정리',
                                                          due=self.due_iso(3600), line=brief['line_id'])]}
        self.script = [again, {'content': '다시 할게요.'}]
        self.receive('관심 목록 정리 다시 해 줘')
        rows = self.work_rows(brief['line_id'])
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['line_id'] for row in rows}, {brief['line_id']})
        self.assertEqual(len(self.service.lines.lines()), 1)

    def test_a_standing_correction_on_day_one_is_in_the_run_input_on_day_two_after_restart_and_replacement(self):
        brief, _work = self.open_line()
        result = self.line_call('앞으로는 거래가 없어도 한 시간마다 보고해 줘', action='revise', line=brief['line_id'],
                                expected_version='1', rule=RULE)
        self.assertEqual((result['version'], result['restated']), (2, True))
        self.now += 86400
        self.store = QuickStore(self.root)
        self.service = self.make_service()
        [first] = self.work_rows(brief['line_id'])

        def replace(body):
            return {'content': None, 'tool_calls': [call('replace', 'schedule_preparation', offer='다음 점검 결과를 정리해 드려요.', kind='prepare', goal='관심 목록 다시 정리',
                                                          due=self.due_iso(600), recurrence='daily', replaces=first['id'])]}
        self.script = [replace, {'content': '바꿨어요.'}]
        self.receive('정리 방식을 바꿔 줘')
        [second] = [row for row in self.work_rows(brief['line_id']) if row['state'] == prep.STATE_SCHEDULED]
        seen = self.run_input(self.run_line(second))
        self.assertIn(RULE, seen)

    def test_a_standing_correction_is_restated_once_and_undo_restores_the_prior_version(self):
        brief, _work = self.open_line()
        for _ in range(3):
            self.service.deliver_notification()
        self.telegram.clear()
        self.line_call('앞으로는 거래가 없어도 한 시간마다 보고해 줘', action='revise', line=brief['line_id'],
                       expected_version='1', rule=RULE)
        for _ in range(3):
            self.service.deliver_notification()
        restated = [body for body in self.sends() if RULE in str(body.get('text'))]
        self.assertEqual(len(restated), 1)
        [body] = restated
        self.assertEqual(body['text'], msg('lines.restated', rule=RULE))
        data = body['reply_markup']['inline_keyboard'][0][0]['callback_data']
        self.assertTrue(data.endswith(':undo'))
        message_id = 9000 + next(index for index, (method, item) in enumerate(self.telegram, 1) if item is body)
        self.tap(data, message_id)
        current = self.service.lines.current(brief['line_id'])
        self.assertEqual((current['version'], current['rules'], current['change']), (3, [], 'undo'))
        # A second tap changes nothing.
        self.tap(data, message_id)
        self.assertEqual(self.service.lines.current(brief['line_id'])['version'], 3)

    def test_a_today_only_correction_writes_no_version_and_is_absent_the_next_day(self):
        brief, _work = self.open_line()
        # The AI judged the correction to be for today: no entrusted_line write.
        self.script = [{'content': '오늘만 30분마다 볼게요.'}]
        self.receive('오늘만 30분마다 봐 줘')
        self.assertEqual(self.service.lines.current(brief['line_id'])['version'], 1)
        self.now += 86400
        [row] = self.work_rows(brief['line_id'])
        seen = self.run_input(self.run_line(self.service.preparations.get(row['id'])))
        self.assertNotIn('30분마다', seen)

    def test_the_run_records_the_version_it_read_and_a_stale_write_keeps_the_newer_revision(self):
        brief, _work = self.open_line()
        [row] = self.work_rows(brief['line_id'])
        self.run_line(row)
        [run] = self.runs(row['id'])
        self.assertEqual(self.service.preparations_service.line_version_read(run['id']), (brief['line_id'], 1))
        self.line_call('앞으로 한 시간마다 보고해', action='revise', line=brief['line_id'], expected_version='1', rule=RULE)
        seen = []

        def stale(body):
            return {'content': None, 'tool_calls': [call('stale', 'entrusted_line', action='revise', line=brief['line_id'],
                                                          expected_version='1', rule='다른 규칙')]}

        def answer(body):
            seen.extend(tool_results(body, 'entrusted_line'))
            return {'content': '바뀌어 있어요.'}
        self.script = [stale, answer]
        self.receive('앞으로 다른 규칙으로 해')
        [refused] = seen
        self.assertEqual(refused['code'], 'line_version_conflict')
        current = self.service.lines.current(brief['line_id'])
        self.assertEqual((current['version'], [rule['text'] for rule in current['rules']]), (2, [RULE]))


class RunTests(_LineCase):
    def test_a_correction_during_a_run_reaches_that_runs_next_step(self):
        brief, _work = self.open_line()
        [row] = self.work_rows(brief['line_id'])

        def first_step(body):
            # The owner's standing correction lands (in another Work) while this run works.
            self.service.lines.write(brief['line_id'], 'owner-work', 1, 'rule',
                                     lambda current: {**current, 'rules': [{'text': RULE, 'from': 'owner-work', 'at': self.now}]})
            return {'content': None, 'tool_calls': [call('notes', 'list_notes')]}
        bodies = self.run_line(row, [first_step, {'content': '규칙대로 정리했습니다.'}])
        later = [body for body in bodies if msg('lines.changed_during_run') in text_of(body)]
        self.assertTrue(later)
        self.assertIn(RULE, text_of(later[0]))
        [run] = self.runs(row['id'])
        self.assertEqual(self.service.preparations_service.line_version_read(run['id']), (brief['line_id'], 2))

    def test_a_stop_during_a_run_ends_it_at_its_next_step_and_its_reply_is_not_sent(self):
        self.check_stop_during_run(end_line=True)

    def test_stopping_current_work_ends_the_run_at_its_next_step_and_suppresses_its_reply(self):
        self.check_stop_during_run(end_line=False)

    def check_stop_during_run(self, *, end_line):
        brief, _work = self.open_line(delivery='send')
        [row] = self.work_rows(brief['line_id'])
        self.deliver_all()
        self.telegram.clear()
        calls = []

        def first_step(body):
            self.service.preparations_service.stop_line(brief['line_id'], 'owner-work', end_line=end_line)
            return {'content': None, 'tool_calls': [call('notes', 'list_notes')]}

        def after(body):
            calls.append(body)
            return {'content': '계속합니다.'}
        start = len(self.model_calls)
        self.script = [first_step, first_step, after]
        self.now = row['due_at'] + 1
        self.assertTrue(self.tick())
        self.service.run_one()
        [run] = self.runs(row['id'])
        self.assertIn(self.store.job(run['id'])['status'], ('cancelled', 'failed', 'interrupted'))
        self.assertFalse(calls, 'no model step after the stop')
        self.assertLessEqual(len(self.model_calls) - start, 2)
        self.deliver_all()
        self.assertFalse(self.sends(), 'the stopped run reports nothing')
        # Nothing of the line runs again.
        self.now += 2 * 86400
        self.tick()
        self.assertEqual(len(self.runs(row['id'])), 1)

    def test_the_quiet_judgment_of_a_lines_run_is_given_its_report_promise(self):
        seen = []

        def judge(context, proposition):
            if context.purpose == 'watch-notification':
                seen.append(dict(context.facts))
                return BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence())
            if context.purpose in ('goal-reached', 'explicit-preparation-request'):
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None
        self.judge = FixtureDecisionEngine(judge=judge)
        self.service.use_decision_engine(self.judge)
        brief, _work = self.open_line(delivery='when_needed')
        [row] = self.work_rows(brief['line_id'])
        self.run_line(row, [{'content': '변동 없음.'}])
        [facts] = seen
        self.assertEqual(facts['report_promise'], PROMISE)

    def test_the_periodic_account_runs_at_its_interval_with_the_full_rules_and_is_delivered_when_nothing_changed(self):
        brief, _work = self.open_line(rules=[RULE, '20시까지만 하기'])
        [account] = [row for row in self.service.preparations_service.line_rows(brief['line_id'])
                     if row.get('origin') == prep.ORIGIN_LINE_ACCOUNT]
        self.assertEqual(account['channel'], 'telegram')
        # Only the account is due in this test.
        [work] = self.work_rows(brief['line_id'])
        self.service.cancel_preparation(work['id'])
        self.deliver_all()
        self.telegram.clear()
        bodies = self.run_line(account, [{'content': '지난 7일 동안 바뀐 것은 없습니다. 규칙 두 가지를 지키고 있습니다.'}])
        seen = text_of(bodies[-1])
        self.assertIn(msg('lines.account_input'), seen)
        self.assertIn(RULE, seen)
        self.assertIn('20시까지만 하기', seen)
        self.assertTrue(any('바뀐 것은 없습니다' in str(body.get('text')) for body in self.sends()),
                        'the account reaches the owner although nothing changed')
        settled = self.service.preparations.get(account['id'])
        self.assertEqual((settled['state'], settled['due_at']), (prep.STATE_SCHEDULED, account['due_at'] + 7 * 86400))

    def test_the_owner_changes_the_account_interval(self):
        brief, _work = self.open_line()
        self.line_call('정기 보고는 3일마다 해 줘', action='revise', line=brief['line_id'], expected_version='1',
                       account_every_days='3')
        account = self.service.preparations_service.line_account_row(brief['line_id'])
        self.assertEqual(account['recurrence'], 'RRULE:FREQ=DAILY;INTERVAL=3')
        self.assertEqual(self.service.lines.current(brief['line_id'])['account_every_days'], 3)


class ListAndStopTests(_LineCase):
    def test_the_list_shows_each_active_line_with_its_four_parts_a_blocked_one_and_the_stopped_ones_marked(self):
        self.assertEqual(self.service.preparations_service.lines_listing()['text'], msg('lines.list_none'))
        delivered, _work = self.open_line(delivery='send')
        [row] = self.work_rows(delivered['line_id'])
        self.run_line(row, [{'content': '오늘 정리했습니다.'}])
        blocked, _work = self.open_line()
        [waiting] = self.work_rows(blocked['line_id'])
        self.now = waiting['due_at'] + 1
        self.tick()
        [run] = self.runs(waiting['id'])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='awaiting_connection' WHERE id=?", (run['id'],))
        stopped, _work = self.open_line()
        self.service.preparations_service.stop_line(stopped['line_id'])

        listing = self.service.preparations_service.lines_listing()
        entries = {entry['line_id']: entry for entry in listing['lines']}
        first = entries[delivered['line_id']]
        self.assertEqual((first['task'], first['report_promise']), (TASK, PROMISE))
        self.assertIsNotNone(first['last_report'])
        self.assertIsNotNone(first.get('next'))
        self.assertEqual(entries[blocked['line_id']]['blocked'], msg('lines.blocked_connection'))
        self.assertEqual(entries[stopped['line_id']]['state'], el.STATE_STOPPED)
        lines = listing['text'].splitlines()
        self.assertEqual(len(lines), 3)
        # Active lines first (newest first), then the stopped one, marked.
        self.assertEqual([entry['line_id'] for entry in listing['lines']],
                         [blocked['line_id'], delivered['line_id'], stopped['line_id']])
        self.assertIn(msg('lines.list_blocked', reason=msg('lines.blocked_connection')), lines[0])
        self.assertIn(first['last_report'], lines[1])
        self.assertEqual(lines[2], msg('lines.list_stopped', task=TASK))

    def test_stop_cancels_everything_under_the_line_and_sends_only_the_confirmation(self):
        brief, _work = self.open_line(delivery='send')
        self.deliver_all()
        self.telegram.clear()
        result = self.line_call('맡은 일 자체를 끝내 줘', action='stop', line=brief['line_id'], scope='line')
        self.assertEqual((result['state'], result['changed'], result['scope']), (el.STATE_STOPPED, True, 'line'))
        rows = self.service.preparations_service.line_rows(brief['line_id'])
        self.assertTrue(rows and all(row['state'] == prep.STATE_CANCELLED for row in rows))
        self.deliver_all()
        confirmation = msg('lines.stopped', task=TASK)
        self.assertEqual(sum(body.get('text') == confirmation for body in self.sends()), 1)
        self.assertNotIn(msg('lines.work_stopped', task=TASK), self.texts())
        self.assertNotIn('되돌', confirmation)
        self.telegram.clear()
        self.now += 30 * 86400
        self.tick()
        self.assertFalse(any(self.runs(row['id']) for row in rows))
        self.deliver_all()
        self.assertFalse(self.sends(), 'nothing after the confirmation')
        # Only the owner restarts it; a stopped line takes no new schedule.
        for explicitly_requested in (False, True):
            with self.subTest(explicitly_requested=explicitly_requested):
                self.service.use_decision_engine(engine(preparation=explicitly_requested))
                result, body = self.schedule_again(brief['line_id'])
                self.assertEqual(result['code'], 'line_stopped')
                self.assertNotIn(el.REF_PREFIX + brief['line_id'], text_of(body))

    def read_then_schedule(self, line_id, **extra):
        seen = []
        def read(body):
            return {'content': None, 'tool_calls': [call('r', 'entrusted_line', action='read', line=line_id)]}
        def schedule(body):
            return {'content': None, 'tool_calls': [call('s', 'schedule_preparation', offer='다음 점검 결과를 정리해 드려요.', kind='prepare', goal='내일 이어서',
                                                          due=self.due_iso(600), **extra)]}
        def retry(body):
            seen.append(tool_results(body, 'schedule_preparation')[-1])
            return {'content': None, 'tool_calls': [call('s2', 'schedule_preparation', offer='다음 점검 결과를 정리해 드려요.', kind='prepare', goal='내일 이어서',
                                                          due=self.due_iso(900), line=extra.get('retry_line') or line_id)]}
        def done(body):
            seen.append(tool_results(body, 'schedule_preparation')[-1])
            return {'content': '알겠습니다.'}
        self.script = [read, schedule, retry, done]
        self.receive('내일도 이어서 해 줘')
        return seen

    def test_1169_a_work_that_read_a_line_must_say_whether_a_schedule_continues_it(self):
        brief, _work = self.open_line()
        before = len(self.service.preparations_service.line_rows(brief['line_id']))
        refused, ok = self.read_then_schedule(brief['line_id'])
        self.assertEqual(refused['code'], 'line_unnamed')
        self.assertEqual(len(self.service.preparations_service.line_rows(brief['line_id'])), before + 1)
        self.assertNotIn('code', ok)

    def test_1169_separate_work_names_none_and_joins_no_line(self):
        brief, _work = self.open_line()
        before = len(self.service.preparations_service.line_rows(brief['line_id']))
        seen = []
        def read(body):
            return {'content': None, 'tool_calls': [call('r', 'entrusted_line', action='read', line=brief['line_id'])]}
        def schedule(body):
            return {'content': None, 'tool_calls': [call('s', 'schedule_preparation', offer='별개의 점검이에요.', kind='prepare', goal='별개',
                                                          due=self.due_iso(600), line='none')]}
        def done(body):
            seen.append(tool_results(body, 'schedule_preparation')[-1])
            return {'content': '알겠습니다.'}
        self.script = [read, schedule, done]
        self.receive('따로 하나 더 예약해 줘')
        self.assertNotIn('code', seen[-1])
        self.assertEqual(len(self.service.preparations_service.line_rows(brief['line_id'])), before)

    def test_1169_a_work_that_read_no_line_schedules_without_naming_one(self):
        seen = []
        def schedule(body):
            return {'content': None, 'tool_calls': [call('s', 'schedule_preparation', offer='한 번만 점검해요.', kind='prepare', goal='한 번',
                                                          due=self.due_iso(600))]}
        def done(body):
            seen.append(tool_results(body, 'schedule_preparation')[-1])
            return {'content': '알겠습니다.'}
        self.script = [schedule, done]
        self.receive('한 번만 점검해 줘')
        self.assertNotIn('code', seen[-1])

    def schedule_again(self, line_id):
        seen = []
        def schedule(body):
            return {'content': None, 'tool_calls': [call('again', 'schedule_preparation', offer='다음 점검 결과를 정리해 드려요.', kind='prepare', goal='다시',
                                                          due=self.due_iso(600), line=line_id)]}

        def answer(body):
            seen.append((tool_results(body, 'schedule_preparation')[-1], body))
            return {'content': '알겠습니다.'}
        self.script = [schedule, answer]
        self.receive('관심 목록 정리 예약해')
        return seen[-1]

    def test_default_scope_stops_only_current_work(self):
        brief, _work = self.open_line()
        result = self.line_call('그만해', action='stop', line=brief['line_id'])
        self.assertEqual((result['state'], result['changed'], result['scope']), (el.STATE_ACTIVE, True, 'work'))
        self.assertEqual(self.service.lines.current(brief['line_id']), brief)
        self.assertTrue(all(row['state'] == prep.STATE_CANCELLED
                            for row in self.service.preparations_service.line_rows(brief['line_id'])))

    def test_work_stop_keeps_the_same_brief_and_allows_new_schedules(self):
        brief, _work = self.open_line(delivery='send')
        line_id = brief['line_id']
        [row] = self.work_rows(line_id)
        self.now = row['due_at'] + 1
        self.assertTrue(self.tick())
        self.assertTrue(self.service.run_one())
        [run] = self.runs(row['id'])
        self.assertEqual(self.store.job(run['id'])['delivery'], 'pending')

        self.telegram.clear()
        result = self.line_call('지금 하던 건 그만해', action='stop', line=line_id, scope='work')
        self.assertEqual((result['state'], result['changed'], result['scope']), (el.STATE_ACTIVE, True, 'work'))
        self.assertEqual(self.service.lines.current(line_id), brief)
        self.assertTrue(all(row['state'] == prep.STATE_CANCELLED
                            for row in self.service.preparations_service.line_rows(line_id)))
        self.assertEqual(self.store.job(run['id'])['delivery'], 'cancelled')
        self.deliver_all()
        self.assertEqual(self.texts().count(msg('lines.work_stopped', task=TASK)), 1)
        self.assertNotIn(msg('lines.stopped', task=TASK), self.texts())

        for explicitly_requested, accepted_by in ((False, prep.ACCEPTED_OWNER_WORK),
                                                  (True, prep.ACCEPTED_OWNER_REQUEST)):
            self.service.use_decision_engine(engine(preparation=explicitly_requested))
            scheduled, body = self.schedule_again(line_id)
            self.assertIn(el.REF_PREFIX + line_id, text_of(body))
            self.assertTrue(scheduled['scheduled'], scheduled)
            replacement = self.service.preparations.get(scheduled['preparation_id'])
            self.assertEqual((replacement['line_id'], replacement['accepted_by']), (line_id, accepted_by))
        # A subsequent run the owner accepted may continue its mission in pieces.
        run_id = self.service.preparations.start(replacement, channel='web', chat_id=None, now=replacement['due_at'])
        self.assertIsNotNone(run_id)
        piece = self.service.preparation_scheduler(self.store.job(run_id), '')(
            {'offer': '다음 점검 결과를 정리해 드려요.', 'kind': 'prepare', 'goal': '다음 확인', 'due': self.due_iso(1200), 'line': line_id})
        self.assertEqual(piece['accepted_by'], prep.ACCEPTED_LINE_PIECE_OWNER)

    def test_invalid_stop_scope_changes_nothing(self):
        brief, _work = self.open_line()
        result = self.line_call('그만해', action='stop', line=brief['line_id'], scope='invalid')
        self.assertEqual(result['code'], 'invalid_arguments')
        self.assertEqual(self.service.lines.current(brief['line_id']), brief)
        self.assertTrue(all(row['state'] == prep.STATE_SCHEDULED
                            for row in self.service.preparations_service.line_rows(brief['line_id'])))

    def test_the_announcements_stop_control_stops_the_line(self):
        brief, _work = self.open_line()
        self.deliver_all()
        announcement = msg('lines.announced', task=TASK, promise=PROMISE)
        index = next(index for index, (method, body) in enumerate(self.telegram, 1)
                     if method == 'sendMessage' and body.get('text') == announcement)
        data = self.telegram[index - 1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        self.tap(data, 9000 + index)
        self.assertEqual(self.service.lines.current(brief['line_id'])['state'], el.STATE_STOPPED)
        self.assertTrue(all(row['state'] == prep.STATE_CANCELLED
                            for row in self.service.preparations_service.line_rows(brief['line_id'])))
        edits = [body for method, body in self.telegram if method == 'editMessageText']
        self.assertEqual(edits[-1]['text'], msg('lines.stopped', task=TASK))


class AnswerTests(_LineCase):
    def test_a_related_answer_is_given_the_brief_and_latest_outcomes_and_an_unrelated_one_neither(self):
        brief, _work = self.open_line(rules=[RULE])
        [row] = self.work_rows(brief['line_id'])
        self.run_line(row, [{'content': '첫 정리: 항목 A가 올랐습니다.'}])
        self.deliver_all()

        start = len(self.model_calls)
        self.script = [{'content': '오늘 저녁은 국수 어때요?'}] * 2
        self.receive('오늘 저녁 뭐 먹지?')
        unrelated = ''.join(text_of(body) for body in self.model_calls[start:])
        # Only the pointer to the line: its id and task sentence, never its brief or its outcomes record.
        # (A run's own reply stays an assistant turn of the conversation, as for every preparation.)
        self.assertIn(brief['line_id'], unrelated)
        self.assertNotIn(RULE, unrelated)
        self.assertNotIn(msg('lines.outcomes_heading'), unrelated)
        self.assertNotIn(msg('lines.brief', task='', promise='', rules='', open='', version='', days='').split(':')[0], unrelated)

        result = self.line_call('관심 목록 요즘 어때?', action='read', line=brief['line_id'])
        self.assertEqual(result['rules'], [RULE])
        self.assertIn(RULE, result['text'])
        self.assertIn('항목 A가 올랐습니다', result['text'])
        self.assertEqual(result['ref'], 'line:' + brief['line_id'])
        self.assertEqual(result['outcomes'][0]['outcome'], el.OUTCOME_KEPT)


class FormatTests(_LineCase):
    def test_the_brief_has_no_authority_rung_amount_or_subject_field_and_an_unknown_field_is_refused(self):
        self.assertEqual(el.FIELDS, ('task', 'rules', 'report_promise', 'open_matters', 'state', 'account_every_days'))
        self.assertEqual(el.RULE_FIELDS, ('text', 'from', 'at', 'basis'))
        legacy = el.validate({'task': TASK, 'report_promise': PROMISE,
                              'rules': [{'text': RULE, 'from': 'owner-work', 'at': self.now}]})
        self.assertIsNone(legacy['rules'][0]['basis'])
        valid = {'task': TASK, 'report_promise': PROMISE, 'rules': [], 'state': el.STATE_ACTIVE}
        el.validate(valid)
        for field in ('authority', 'rung', 'amount', 'subject', 'limit', 'grant'):
            with self.subTest(field=field), self.assertRaises(el.LineRefusal) as refused:
                el.validate({**valid, field: 'x'})
            self.assertEqual(refused.exception.code, 'line_unknown_field')
        with self.assertRaises(el.LineRefusal):
            el.validate({**valid, 'rules': [{'text': RULE, 'amount': 10}]})
        from personal_agent.agent_runtime import DEFINITIONS
        [definition] = [item for item in DEFINITIONS if item['function']['name'] == 'entrusted_line']
        parameters = definition['function']['parameters']
        self.assertFalse(set(parameters['properties'])
                         & {'authority', 'rung', 'amount', 'subject', 'grant', 'payment'})
        self.assertEqual(parameters['properties']['scope']['enum'], ['work', 'line'])
        self.assertNotIn('scope', parameters['required'])

    def test_a_stored_brief_that_cannot_be_read_stops_the_lines_run(self):
        brief, _work = self.open_line()
        [row] = self.work_rows(brief['line_id'])
        with self.store.db() as db:
            db.execute("UPDATE procedure_versions SET source='{not json' WHERE slot=? AND state='current'", (el.slot(brief['line_id']),))
        self.now = row['due_at'] + 1
        self.tick()
        self.assertEqual(self.runs(row['id']), [])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_FAILED)

    def test_no_brief_field_prompt_or_catalogue_entry_names_a_subject(self):
        from personal_agent.messages import catalog
        from personal_agent.prompt_files import prompt
        texts = [prompt(name) for name in ('line_account', 'owner_model_dream_lines', 'day_plan_lines')] + [
            entry['en'] for key, entry in catalog().items()
            if key.startswith('lines.') or key.startswith('button.line_')]
        named = ('trade', 'stock', 'invest', 'shop', 'cart', 'flight', 'hotel', 'calendar', 'mail', 'price', 'news', 'weather')
        for text in texts:
            with self.subTest(text=text[:40]):
                self.assertFalse([word for word in named if word in text.lower()])


class AuthorityTests(_LineCase):
    def test_a_brief_write_from_an_unattended_run_is_refused_and_the_revision_is_unchanged(self):
        brief, _work = self.open_line()
        [row] = self.work_rows(brief['line_id'])
        seen = []

        def widen(body):
            return {'content': None, 'tool_calls': [call('widen', 'entrusted_line', action='revise', line=brief['line_id'],
                                                          expected_version='1', rule='한도 없이 계속하기')]}

        def answer(body):
            seen.extend(tool_results(body, 'entrusted_line'))
            return {'content': '바꾸지 않았습니다.'}
        self.run_line(row, [widen, answer])
        self.assertTrue(seen)
        self.assertEqual(seen[0]['code'], 'unattended_run')
        self.assertEqual(self.service.lines.current(brief['line_id'])['version'], 1)
        [run] = self.runs(row['id'])
        for action in ('open', 'stop', 'restart', 'undo'):
            with self.subTest(action=action), self.assertRaises(ToolError) as refused:
                self.service.line_handler(self.store.job(run['id']))({'action': action, 'line': brief['line_id'],
                                                                      'task': TASK, 'report_promise': PROMISE, 'version': '1'})
            self.assertEqual(refused.exception.code, 'unattended_run')
        self.assertEqual(self.service.lines.current(brief['line_id'])['version'], 1)

    def test_payment_autonomy_reads_no_brief_and_a_continuing_row_keeps_its_own_kind_and_payment_rule(self):
        from personal_agent import browser_session as bs
        from test_payment_autonomy_662 import Mode
        Mode.switch(self, True)
        brief, _work = self.open_line()
        self.line_call('앞으로는 필요하면 결제까지 해', action='revise', line=brief['line_id'], expected_version='1',
                       rule='필요하면 결제까지 하기')

        def run_of(row):
            work = self.store.enqueue('pay', prep.request_key(row['id'], row['due_at']))
            with self.store.db() as db:
                db.execute('UPDATE preparations SET state=?,last_run_job_id=? WHERE id=?', (prep.STATE_RUNNING, work, row['id']))
                # Authorization is checked while this probe is running.
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
            return self.store.job(work)

        def authorized(job):
            binding = bs.step_binding(job['id'], 'api_request', 'https://pay.test/', 'pay 10', argument='', state='s')
            try:
                result = self.service._authorize_payment_autonomy(job, binding, 'pay 10')
            finally:
                # The probe is finished. Leaving four synthetic running rows
                # would fill slice 2's three lanes before the reminder turn.
                with self.store.db() as db:
                    db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (job['id'],))
            return result and result['authorized_by']

        for accepted_by in (prep.ACCEPTED_OWNER_WORK, prep.ACCEPTED_OWNER_REQUEST):
            with self.subTest(accepted_by=accepted_by):
                rows = [self.service.preparations.create(kind='prepare', goal='pay ' + accepted_by, due_at=self.now + 60 + index,
                                                         timezone='Asia/Seoul', recurrence=None, channel='web',
                                                         created_from='settings', state=prep.STATE_SCHEDULED,
                                                         accepted_by=accepted_by, line_id=line)
                        for index, line in enumerate((brief['line_id'], None))]
                with_line, without = (authorized(run_of(row)) for row in rows)
                self.assertEqual(with_line, without)
                self.assertEqual(with_line, None if accepted_by == prep.ACCEPTED_OWNER_WORK else 'payment-autonomy')

        # A row the owner's AI adds to the line later keeps its own kind and who set it up.
        def reminder(body):
            return {'content': None, 'tool_calls': [call('remind', 'schedule_preparation', kind='reminder', goal='목록 확인 알림',
                                                          due=self.due_iso(600), line=brief['line_id'])]}
        self.judge = FixtureDecisionEngine(judge=lambda context, proposition: BinaryDecision(
            OUTCOME_DECIDED, context.purpose == 'goal-reached', fixture_confidence()))
        self.service.use_decision_engine(self.judge)
        self.script = [reminder, {'content': '알림도 걸었어요.'}]
        self.receive('관심 목록 확인할 때 알림도 줘')
        [added] = [row for row in self.work_rows(brief['line_id']) if row['kind'] == prep.KIND_REMINDER]
        self.assertEqual((added['accepted_by'], added['kind']), (prep.ACCEPTED_OWNER_WORK, prep.KIND_REMINDER))
        [first] = [row for row in self.work_rows(brief['line_id']) if row['created_from'] == _work]
        self.assertEqual((first['accepted_by'], first['kind']), (prep.ACCEPTED_OWNER_REQUEST, prep.KIND_PREPARE))


class MoneyTests(_LineCase):
    def touch_money(self, brief):
        [row] = self.work_rows(brief['line_id'])
        work = self.store.enqueue('run', prep.request_key(row['id'], row['due_at']))
        with self.store.db() as db:
            # An earlier, finished run of the line that took a payment step.
            db.execute("UPDATE jobs SET status='succeeded',response='done' WHERE id=?", (work,))
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work, 'api_request', 'succeeded', json.dumps({'declared_effect': 'payment'}), self.now))
        self.assertTrue(self.service.preparations_service.line_touches_money(brief['line_id']))

    def owner_turn(self, yes):
        def judge(context, proposition):
            if context.purpose == 'explicit-line-change':
                self.asked.append(dict(context.facts))
                return BinaryDecision(OUTCOME_DECIDED, yes, fixture_confidence())
            if context.purpose in ('goal-reached', 'explicit-preparation-request'):
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None
        self.asked = []
        self.judge = FixtureDecisionEngine(judge=judge)
        self.service.use_decision_engine(self.judge)

    def test_a_widened_limit_without_an_owner_turn_is_refused(self):
        brief, _work = self.open_line(rules=['하루 10만원까지만'])
        self.touch_money(brief)
        self.owner_turn(False)
        result = self.line_call('요즘 어때?', action='revise', line=brief['line_id'], expected_version='1',
                                drop_rule='1', rule='하루 100만원까지')
        self.assertEqual(result['code'], 'line_change_needs_owner')
        [facts] = self.asked
        self.assertIn('하루 10만원까지만', facts['owner_rules'])
        self.assertIn('앞으로 매일 아침 관심 목록 확인해서 정리해 줘', facts['owner_rules'])
        current = self.service.lines.current(brief['line_id'])
        self.assertEqual((current['version'], [rule['text'] for rule in current['rules']]), (1, ['하루 10만원까지만']))

    def test_a_rule_change_the_owner_asked_for_shows_their_original_words_beside_the_full_rules(self):
        brief, _work = self.open_line(rules=['하루 10만원까지만'])
        self.touch_money(brief)
        self.owner_turn(True)
        self.deliver_all()
        self.telegram.clear()
        result = self.line_call('앞으로는 20시 전까지만 해 줘', action='revise', line=brief['line_id'],
                                expected_version='1', rule='20시 전까지만 하기')
        self.assertEqual((result['version'], result['money_rules']), (2, True))
        self.deliver_all()
        [notice] = [body for body in self.sends() if '20시 전까지만 하기' in str(body.get('text'))]
        self.assertIn(msg('lines.restated', rule='20시 전까지만 하기'), notice['text'])
        self.assertIn(msg('lines.money_rules'), notice['text'])
        self.assertIn('하루 10만원까지만', notice['text'])
        self.assertIn('앞으로 매일 아침 관심 목록 확인해서 정리해 줘', notice['text'])
        self.assertIn('앞으로는 20시 전까지만 해 줘', notice['text'])


class ForgetTests(_LineCase):
    def test_forgetting_a_line_removes_its_brief_versions_from_every_later_context(self):
        brief, _work = self.open_line(rules=[RULE])
        self.line_call('앞으로 20시까지만 해', action='revise', line=brief['line_id'], expected_version='1', rule='20시까지만')
        receipt = OwnerForget(self.store).forget('local-owner', 'line:' + brief['line_id'])
        self.assertEqual((receipt['kind'], receipt['items']), ('line', 2))
        self.assertIsNone(self.service.lines.readable(brief['line_id']))
        self.assertEqual(self.service.lines.lines(), [])
        start = len(self.model_calls)
        self.script = [{'content': '네.'}] * 2
        self.receive('관심 목록 일은?')
        later = ''.join(text_of(body) for body in self.model_calls[start:])
        self.assertNotIn(brief['line_id'], later)
        self.assertNotIn(RULE, later)
        # Its runs stop: a brief that is gone runs nothing.
        [row] = self.work_rows(brief['line_id'])
        self.now = row['due_at'] + 1
        self.tick()
        self.assertEqual(self.runs(row['id']), [])
        # Undo puts every version back.
        OwnerForget(self.store).undo('local-owner', receipt['receipt'])
        self.assertEqual(self.service.lines.current(brief['line_id'])['version'], 2)
