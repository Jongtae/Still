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

from test_preparations import CHAT, _Case, call

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
                prepare = {'kind': 'prepare', 'goal': '관심 목록 확인 후 정리', 'due': self.due_iso(3600),
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
        [brief] = self.service.lines.lines()
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

    def test_a_replaced_row_keeps_its_line_and_the_next_run_reads_earlier_outcomes_with_their_labels(self):
        brief, _work = self.open_line(delivery='send')
        [first] = self.work_rows(brief['line_id'])
        self.run_line(first, [{'content': '첫 정리: 항목 A가 올랐습니다.'}])

        def replace(body):
            return {'content': None, 'tool_calls': [call('replace', 'schedule_preparation', kind='prepare',
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
        self.run_line(first, [{'content': '한 번 정리했습니다.'}])
        self.assertNotIn(self.service.preparations.get(first['id'])['state'], prep.ACTIVE_STATES)
        self.assertEqual(self.service.lines.current(brief['line_id'])['state'], el.STATE_ACTIVE)
        [entry] = self.service.preparations_service.lines_listing()['lines']
        self.assertTrue(entry.get('next_is_account'))

        # Restart Still, then the owner starts the same work again: the AI continues the line.
        self.store = QuickStore(self.root)
        self.service = self.make_service()

        def again(body):
            return {'content': None, 'tool_calls': [call('again', 'schedule_preparation', kind='prepare', goal='관심 목록 확인 후 정리',
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
            return {'content': None, 'tool_calls': [call('replace', 'schedule_preparation', kind='prepare', goal='관심 목록 다시 정리',
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
