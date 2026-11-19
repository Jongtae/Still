"""Model-free day-plan offers, owner acts, and payment through the normal brokers (#1027)."""
import json
import sqlite3

from personal_agent import entrusted_lines as el, preparations as prep
from personal_agent.agent_runtime import ToolError
from personal_agent.api_requests import ApiRequests, save_slot
from personal_agent.owner_forget import OwnerForget

from test_day_plan_780 import _Plan
from test_line_pieces_938 import PAY
from test_owner_acts import OwnerActCase
from test_payment_autonomy_662 import Mode
from test_preparations import call, finish


class ResumeOffers(Mode, _Plan, OwnerActCase):
    def line(self, stop=None, pending=False):
        owner = self.receive('이 일을 맡아 줘')
        brief = self.service.lines.open(owner, task='변동 확인', report_promise='바뀌면 알려 줘',
                                        rules=['정한 범위 안에서만'], open_matters='이전 결과와 비교')
        line_id = brief['line_id']
        if pending or stop == 'work':
            self.service.preparations.create(kind='prepare', goal='변동 확인', due_at=self.now + 86400,
                                             timezone='Asia/Seoul', recurrence=None, channel='web',
                                             created_from=owner, state=prep.STATE_SCHEDULED,
                                             accepted_by=prep.ACCEPTED_OWNER_REQUEST, line_id=line_id)
        if stop:
            brief, changed = self.service.preparations_service.stop_line(line_id, owner, end_line=stop == 'line')
            self.assertTrue(changed)
        return brief

    def resume_call(self, line_id, ident='1', goal='오늘 변동을 정한 범위 안에서 확인', **extra):
        args = {'kind': 'prepare', 'goal': goal, 'due': self.due_iso(3600), 'delivery': 'keep',
                'line': line_id, 'resume_line': True, 'title': '오늘 변동 확인',
                'offer': '오늘 이어서 확인할까요?\n정한 범위 안에서 이전 결과와 비교할게요.', **extra}
        return call(ident, 'schedule_preparation', **args)

    def offers(self):
        return [row for row in self.rows() if row['origin'] == prep.ORIGIN_LINE_RESUME]

    def plan_calls(self, make_calls):
        plan = self.start_plan_run()
        calls = make_calls()
        self.script = [{'content': None, 'tool_calls': list(calls)}, finish('f', calls[0]['id'])]
        self.assertTrue(self.service.run_one())
        [work] = self.runs(plan['id'])
        return work

    def offer(self, brief):
        work = self.plan_calls(lambda: [self.resume_call(brief['line_id'])])
        [row] = self.offers()
        self.assertEqual((row['state'], row['accepted_by'], row['line_id']),
                         (prep.STATE_PROPOSED, None, brief['line_id']))
        self.assertEqual(self.service.lines.current(brief['line_id']), brief)
        return work, row

    def notice(self, work):
        self.assertTrue(self.service.deliver_notification())
        notice = self.store.notification_of(work['id'], 'preparation_proposed')
        self.assertEqual(notice['state'], 'sent')
        self.assertIn('오늘 이어서 확인할까요?', self.sends()[-1]['text'])
        return notice

    def click(self, notice, action='accept'):
        self.tap(f"p7p:{notice['id']}:{action}", notice['message_id'])

    def assert_no_run(self, row):
        self.now = row['due_at'] + 1
        self.tick()
        self.assertEqual(self.runs(row['id']), [])

    def assert_accepted(self, brief, row, by, owner=None):
        current = self.service.lines.current(brief['line_id'])
        kept = self.service.preparations.get(row['id'])
        self.assertEqual((kept['state'], kept['accepted_by'], kept['line_id']),
                         (prep.STATE_SCHEDULED, by, brief['line_id']))
        if brief['state'] == el.STATE_STOPPED:
            self.assertEqual((current['state'], current['change'], current['version'], current['written_in']),
                             (el.STATE_ACTIVE, 'restarted', brief['version'] + 1, owner))
        else:
            self.assertEqual(current, brief)
        self.assertEqual(current['rules'], brief['rules'])

    def test_owner_button_restarts_and_owner_accepts_the_row(self):
        brief = self.line(stop='line')
        work, row = self.offer(brief)
        self.click(self.notice(work))
        self.assert_accepted(brief, row, prep.ACCEPTED_OWNER_BUTTON)

    def test_accepting_a_stopped_line_restores_its_self_set_account(self):
        brief = self.line()
        line_id = brief['line_id']
        lines = self.service.preparations_service
        owner = self.store.job(brief['written_in'])
        original = lines.ensure_line_account(owner, brief)
        stopped, _ = lines.stop_line(line_id, owner['id'])
        self.assertIsNone(lines.line_account_row(line_id))

        work, row = self.offer(stopped)
        self.click(self.notice(work))

        self.assert_accepted(stopped, row, prep.ACCEPTED_OWNER_BUTTON)
        account = lines.line_account_row(line_id)
        self.assertIsNotNone(account)
        self.assertEqual(account['state'], prep.STATE_SCHEDULED)
        self.assertIn(account['accepted_by'], prep.SELF_SET)
        self.assertEqual((account['recurrence'], account['channel']), (original['recurrence'], original['channel']))
        self.assertEqual(self.service.preparations.get(original['id'])['state'], prep.STATE_CANCELLED)

    def test_judged_owner_answer_restarts_and_records_the_owner_work(self):
        brief = self.line(stop='line')
        work, row = self.offer(brief)
        notice = self.notice(work)
        with self.store.db() as db:
            db.execute('UPDATE telegram_notifications SET created=?,sent_at=? WHERE id=?', (self.now, self.now, notice['id']))
        self.rule = lambda act, items: (items[0]['id'], 'approve', '') if act == '그렇게 해 줘' else None
        owner = self.receive_reply('그렇게 해 줘', notice['message_id'])
        self.assert_accepted(brief, row, prep.ACCEPTED_OWNER_ANSWER, owner)

    def test_work_only_stop_keeps_the_brief_active_and_allows_an_offer(self):
        brief = self.line(stop='work')
        self.assertEqual(brief['state'], el.STATE_ACTIVE)
        work, row = self.offer(brief)
        self.click(self.notice(work))
        self.assert_accepted(brief, row, prep.ACCEPTED_OWNER_BUTTON)

    def test_web_accept_requires_the_current_fingerprint(self):
        brief = self.line(stop='line')
        _, row = self.offer(brief)
        request = {'operation': 'accept', 'id': row['id']}
        with self.assertRaisesRegex(prep.PreparationRefusal, '이 제안이'):
            self.service.preparation_request(request)
        self.assertEqual(self.service.lines.current(brief['line_id']), brief)
        [view] = [item for item in self.service.preparations_status()['preparations'] if item['id'] == row['id']]
        self.service.preparation_request({**request, 'fingerprint': view['resume_offer_fingerprint']})
        self.assert_accepted(brief, row, prep.ACCEPTED_OWNER_BUTTON)

    def test_decline_preserves_the_stop_and_is_shown_to_the_next_plan(self):
        brief = self.line(stop='line')
        work, row = self.offer(brief)
        notice = self.notice(work)
        self.click(notice, 'deny')
        self.click(notice)
        kept = self.service.preparations.get(row['id'])
        self.assertEqual((kept['state'], kept['accepted_by'], kept['last_outcome']),
                         (prep.STATE_CANCELLED, None, 'declined'))
        with self.assertRaises(prep.PreparationRefusal):
            self.service.preparations.restore(row['id'])
        self.assert_no_run(row)
        self.assertEqual(self.service.lines.current(brief['line_id']), brief)

        next_plan = self.start_plan_run()
        next_work = self.store.job(self.service.preparations.get(next_plan['id'])['last_run_job_id'])
        history = self.service.preparation_history(next_work)[-1]['content']
        [prior] = json.loads(history.split('earlier_plans: ', 1)[1].split('\n\n', 1)[0])
        self.assertEqual((prior['line_id'], prior['resume_offer'], prior['declined']), (brief['line_id'], True, True))
        offered = self.service.preparation_scheduler(next_work, '')(json.loads(self.resume_call(brief['line_id'])['function']['arguments']))
        self.assertEqual(offered['state'], prep.STATE_PROPOSED, 'the limit reopens on the next local day')

    def test_ignored_offer_never_runs_even_on_an_active_line(self):
        brief = self.line()
        work, row = self.offer(brief)
        self.notice(work)
        with self.assertRaises(prep.PreparationRefusal):
            self.service.preparations.run_now(row['id'], channel='web', chat_id=None)
        self.assert_no_run(row)
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_PROPOSED)
        self.now += 86400
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_EXPIRED)
        self.assertEqual(self.service.lines.current(brief['line_id']), brief)

    def test_second_offer_same_line_local_day_is_refused_even_after_delete(self):
        brief = self.line()
        line_id = brief['line_id']
        plan = self.start_plan_run()
        work = self.store.job(self.service.preparations.get(plan['id'])['last_run_job_id'])
        schedule = self.service.preparation_scheduler(work, '')
        args = json.loads(self.resume_call(line_id)['function']['arguments'])
        first = schedule(args)
        self.service.preparations.delete(first['preparation_id'])
        with self.assertRaises(ToolError) as refusal:
            schedule({**args, 'goal': '조금 다른 목표'})
        self.assertEqual(refusal.exception.code, 'line_resume_already_offered')
        self.assertEqual(self.offers(), [])

    def test_plan_cannot_restart_accept_its_own_proposal_or_schedule_a_stopped_line(self):
        brief = self.line(stop='line')
        line_id = brief['line_id']
        work = self.plan_calls(lambda: [self.resume_call(line_id), self.resume_call(line_id, '2', resume_line=False),
                                        call('3', 'entrusted_line', action='restart', line=line_id)])
        [row] = self.offers()
        self.assertEqual([other['id'] for other in self.rows() if other['line_id'] == line_id], [row['id']])
        self.assertEqual([trace['code'] for trace in self.tool_trace(work['id']) if 'code' in trace], ['unattended_run'])
        [trace] = [trace for trace in self.tool_trace(work['id'], 'entrusted_line') if 'code' in trace]
        self.assertEqual(trace['code'], 'unattended_run')
        with self.assertRaises(prep.PreparationRefusal):
            self.service.preparations.accept(row['id'], prep.ACCEPTED_DAY_PLAN, offer_digest=prep.digest([row]))
        self.assertEqual(self.service.lines.current(line_id), brief)
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_PROPOSED)

    def test_ineligible_lines_are_refused_even_with_a_direct_id(self):
        active = self.line(pending=True)
        forgotten = self.line(stop='line')
        OwnerForget(self.store).forget('local-owner', 'line:' + forgotten['line_id'])
        base = {key: active[key] for key in el.FIELDS}
        look_back = self.service.lines.stand(None, base, {'by': 'look-back', 'stand_key': 'inferred:missing'})
        with self.store.db() as db:
            db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)",
                       (active['written_in'], 'line_restarted', 'succeeded',
                        json.dumps({'line_id': look_back['line_id'], 'version': look_back['version']}), self.now))
        self.assertTrue(self.service.preparations_service.line_announced(look_back['line_id']))
        plan_line = self.service.lines.stand(None, base, {'by': 'day-plan', 'stand_key': 'plan'})
        targets = [active, forgotten, look_back, plan_line]
        work = self.plan_calls(lambda: [self.resume_call(brief['line_id'], str(index + 1)) for index, brief in enumerate(targets)])
        self.assertEqual(self.offers(), [])
        self.assertEqual([trace['code'] for trace in self.tool_trace(work['id']) if 'code' in trace],
                         ['line_resume_unavailable'] * len(targets))

    def test_stale_button_after_goal_change_is_refused(self):
        brief = self.line(stop='line')
        work, row = self.offer(brief)
        notice = self.notice(work)
        with self.store.db() as db:
            db.execute('UPDATE preparations SET goal_text=? WHERE id=?', ('다른 목표', row['id']))
        self.click(notice)
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_PROPOSED)
        self.assertEqual(self.service.lines.current(brief['line_id']), brief)

    def test_changed_line_rules_make_the_old_offer_stale(self):
        brief = self.line(stop='line')
        work, row = self.offer(brief)
        notice = self.notice(work)
        _, revised = self.service.lines.write(brief['line_id'], None, None, 'revised',
                                              lambda value: {**value, 'open_matters': '새로 확인할 내용'})
        self.click(notice)
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_PROPOSED)
        self.assertEqual(self.service.lines.current(brief['line_id']), revised)

    def test_failed_acceptance_rolls_back_the_restart(self):
        brief = self.line(stop='line')
        _, row = self.offer(brief)
        with self.store.db() as db:
            db.execute("CREATE TRIGGER reject_offer BEFORE UPDATE ON preparations WHEN NEW.origin='line-resume' "
                       "BEGIN SELECT RAISE(ABORT, 'fixture rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.preparations.accept(row['id'], prep.ACCEPTED_OWNER_BUTTON, offer_digest=prep.digest([row]))
        self.assertEqual(self.service.lines.current(brief['line_id']), brief)
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_PROPOSED)
        self.assertIsNone(self.service.preparations_service.line_account_row(brief['line_id']))

    def test_resume_and_piece_follow_current_autonomy_and_never_ask_for_approval(self):
        brief = self.line(stop='line')
        work, row = self.offer(brief)
        self.click(self.notice(work))
        save_slot(self.store, 'money', ['pay.test'], 'secret-key-value-123456789', money=True)
        self.now = row['due_at']
        self.tick()
        [root] = self.runs(row['id'])
        child = self.service.preparation_scheduler(root, '')({
            'kind': 'prepare', 'goal': '이어서 확인', 'due': self.due_iso(600)})
        self.assertEqual(child['accepted_by'], prep.ACCEPTED_LINE_PIECE_OWNER)
        for run in (root, self.start_piece(child['preparation_id'])):
            self.assertFalse(self.service.payment_autonomy_settings()['enabled'])
            sent = []
            api = ApiRequests(self.store, run['id'], approvals=self.service.browser_approvals_for(run),
                              transport=lambda *args, **kwargs: sent.append(args) or (200, {}, b'{}'))
            with self.assertRaises(ToolError) as refusal:
                api.call(PAY)
            self.assertEqual(refusal.exception.code, 'unattended_run')
            self.assertEqual(sent, [])
            self.assertEqual(self.receipts(run['id']), [])
            self.assertIsNone(self.service._browser_request(run['id']))
            self.switch(True)
            api.call(PAY)
            self.assertEqual(len(sent), 1)
            self.assertEqual(self.receipts(run['id'])[0]['authorized_by'], 'payment-autonomy')
            self.switch(False)

    def start_piece(self, piece_id):
        piece = self.service.preparations.get(piece_id)
        work = self.service.preparations.start(piece, channel='web', chat_id=None, now=piece['due_at'])
        self.assertIsNotNone(work)
        return self.store.job(work)
