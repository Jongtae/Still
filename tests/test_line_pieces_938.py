"""#938: a run of an entrusted line schedules the next piece of its work.

Evidence class: model-free, fixture transports, as ``test_entrusted_lines_798``: a scripted
compatible-API model, a recording Telegram transport, a FixtureDecisionEngine and a fake clock;
the real QuickStore, ``AgentService``, ``run_agent``, ``Capabilities``, ``ApiRequests`` and
``OwnerForget`` run unchanged.  No live model, Telegram, provider or payment is contacted, and none
is claimed.

Owner decision 2026-11-18 (#938, tracker #973): a piece of a line whose root row the owner accepted
follows the owner's payment autonomy as that row does; a piece of a row Still set up on its own
never pays.  Both are stamped from the parent row, never from the call.
"""
import json
import unittest
from unittest import mock

from personal_agent import entrusted_lines as el
from personal_agent import preparations as prep
from personal_agent.agent_runtime import ToolError
from personal_agent.api_requests import ApiRequests, save_slot
from personal_agent.messages import msg
from personal_agent.owner_forget import OwnerForget

from test_entrusted_lines_798 import PROMISE, TASK, _LineCase, tool_results
from test_payment_autonomy_662 import Mode
from test_preparations import call

OWNER_ROOTS = (prep.ACCEPTED_OWNER_REQUEST, prep.ACCEPTED_OWNER_BUTTON, prep.ACCEPTED_OWNER_SETTINGS,
               prep.ACCEPTED_OWNER_ANSWER, prep.ACCEPTED_OWNER_STEER)
PAY = {'slot': 'money', 'url': 'https://pay.test/pay', 'method': 'POST', 'body': '{"amount":"10"}', 'effect': 'payment'}


class _PieceCase(Mode, _LineCase):
    def setUp(self):
        super().setUp()
        self.line_id = self.service.lines.open('owner-work', task=TASK, report_promise=PROMISE)['line_id']

    def line_row(self, accepted_by=prep.ACCEPTED_OWNER_REQUEST, *, line=True, origin=None, kind='prepare',
             recurrence=None, channel='web', delivery_mode=None, goal='root'):
        """A row of the line, as the owner's own Work (or Settings, or the look back) left it."""
        return self.service.preparations.create(
            kind=kind, goal=goal, due_at=self.now + 60, timezone='Asia/Seoul', recurrence=recurrence, channel=channel,
            created_from='owner-work', state=prep.STATE_SCHEDULED, accepted_by=accepted_by, origin=origin,
            delivery_mode=delivery_mode, line_id=self.line_id if line is True else line or None)

    def running(self, row, suffix=''):
        """The current run of ``row`` (``suffix`` is a continuation of its slot, #774)."""
        key = prep.request_key(row['id'], row['due_at']) + suffix
        work = self.store.enqueue(row['goal_text'], key)
        with self.store.db() as db:
            db.execute('UPDATE preparations SET state=?,last_run_job_id=? WHERE id=?', (prep.STATE_RUNNING, work, row['id']))
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
        return self.store.job(work)

    def schedule(self, job, seconds=600, **args):
        args = {'kind': 'prepare', 'goal': '다음 단계: 확인한 상태에서 이어 보기', 'due': self.due_iso(seconds), **args}
        return self.service.preparation_scheduler(job, '')(args)

    def refused(self, job, code, **args):
        with self.assertRaises(ToolError) as refused:
            self.schedule(job, **args)
        self.assertEqual(refused.exception.code, code)
        return refused.exception

    def pieces(self, line_id=None):
        with self.store.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM preparations WHERE origin=? AND line_id=? ORDER BY created_at',
                                                    (prep.ORIGIN_LINE_PIECE, line_id or self.line_id))]

    def piece_run(self, piece):
        """Start the piece through the scheduler's own claim (``Preparations.start``)."""
        work = self.service.preparations.start(self.service.preparations.get(piece['id']), channel='web', chat_id=None,
                                               now=piece['due_at'])
        self.assertIsNotNone(work)
        return self.store.job(work)


class WhoMaySchedule(_PieceCase):
    def test_a_qualifying_run_schedules_one_piece_that_inherits_its_line_channel_delivery_and_standing(self):
        for accepted_by, standing in ([(value, prep.ACCEPTED_LINE_PIECE_OWNER) for value in OWNER_ROOTS]
                                      + [(prep.ACCEPTED_OWNER_WORK, prep.ACCEPTED_LINE_PIECE)]):
            with self.subTest(accepted_by=accepted_by):
                root = self.line_row(accepted_by, channel='telegram', delivery_mode=prep.DELIVERY_WHEN_NEEDED, goal='root ' + accepted_by)
                job = self.running(root)
                result = self.schedule(job, delivery='send', title='제목')
                piece = self.service.preparations.get(result['preparation_id'])
                self.assertEqual((piece['origin'], piece['accepted_by'], piece['state'], piece['kind'], piece['line_id']),
                                 (prep.ORIGIN_LINE_PIECE, standing, prep.STATE_SCHEDULED, prep.KIND_PREPARE, self.line_id))
                self.assertEqual((piece['channel'], piece['delivery_mode']), ('telegram', prep.DELIVERY_WHEN_NEEDED))
                self.assertEqual((piece['recurrence'], piece['piece_of'], piece['created_from']),
                                 (None, prep.slot_key(job['request_key']), job['id']))
                self.assertEqual((result['accepted_by'], result['line_id'], result['scheduled']), (standing, self.line_id, True))
                # The piece's own run continues the chain with the same standing.
                grand = self.service.preparations.get(self.schedule(self.running(piece), seconds=1200)['preparation_id'])
                self.assertEqual((grand['accepted_by'], grand['piece_of']), (standing, prep.request_key(piece['id'], piece['due_at'])))

    def test_other_unattended_runs_stay_refused(self):
        stood = self.service.lines.stand('look-back-anchor', {'task': '돌아보기 일', 'report_promise': PROMISE},
                                         {'change': 'stood', 'by': 'look-back', 'stand_key': 'look-back:a:b'})
        cases = {
            'ordinary scheduled run': self.line_row(prep.ACCEPTED_OWNER_SETTINGS, line=False, goal='ordinary'),
            'look-back row': self.line_row(prep.ACCEPTED_DREAM, origin=prep.ORIGIN_DREAM, goal='dream'),
            'daily-plan row': self.line_row(prep.ACCEPTED_DAY_PLAN, origin=prep.ORIGIN_DAY_PLAN, goal='day plan'),
            'row a daily plan set up': self.line_row(prep.ACCEPTED_DAY_PLAN, goal='set up by the plan'),
            'periodic account': self.line_row(prep.ACCEPTED_OWNER_WORK, origin=prep.ORIGIN_LINE_ACCOUNT, recurrence='daily', goal='account'),
            'look-back line': self.line_row(prep.ACCEPTED_OWNER_REQUEST, line=stood['line_id'], goal='stood line'),
            'no acceptance': self.line_row(None, goal='unknown acceptance'),
        }
        for name, row in cases.items():
            with self.subTest(name):
                # Naming the row's own line: a piece, never a schedule of another kind.
                self.refused(self.running(row), 'unattended_run', **({'line': row['line_id']} if row['line_id'] else {}))
        # Only the slot's current Work: an earlier run of the row, after the slot moved on, is refused.
        row = self.line_row(goal='moved on')
        earlier = self.running(row)
        self.running(row, suffix='/loc:1')
        self.refused(earlier, 'unattended_run')
        self.assertEqual(self.pieces(), [])
        self.assertEqual(self.pieces(stood['line_id']), [])

    def test_a_piece_is_only_one_prepare_of_the_same_line(self):
        other = self.service.lines.open('owner-work', task='다른 일', report_promise=PROMISE)['line_id']
        job = self.running(self.line_row())
        cases = {'reminder': {'kind': 'reminder'}, 'another line': {'line': other}, 'recurrence': {'recurrence': 'daily'},
                 'rrule': {'rrule': 'FREQ=DAILY;COUNT=2'}, 'window': {'every_minutes': '30', 'until': self.due_iso(7200)},
                 'max_runs': {'max_runs': '3'}, 'report': {'report_every_minutes': '60'},
                 'replaces': {'replaces': self.line_row(goal='owner row')['id']}}
        for name, args in cases.items():
            with self.subTest(name):
                self.refused(job, 'line_piece_invalid', **args)
        self.assertEqual(self.pieces(), [])
        self.assertEqual(self.pieces(other), [])
        # Without a line argument the piece is the run's own line, never a row outside any line.
        piece = self.service.preparations.get(self.schedule(job)['preparation_id'])
        self.assertEqual(piece['line_id'], self.line_id)

    def test_an_owner_typed_work_is_unchanged_and_sets_up_no_piece(self):
        def step(body):
            return {'content': None, 'tool_calls': [call('s', 'schedule_preparation', kind='prepare', goal='첫 확인',
                                                          due=self.due_iso(3600), line=self.line_id)]}
        self.script = [step, {'content': '예약했어요.'}]
        self.receive('이 일 한 시간 뒤에 확인해 줘')
        [row] = [row for row in self.work_rows(self.line_id) if row['goal_text'] == '첫 확인']
        self.assertIsNone(row['origin'])
        self.assertIsNone(row['piece_of'])
        self.assertIn(row['accepted_by'], (prep.ACCEPTED_OWNER_REQUEST, prep.ACCEPTED_OWNER_WORK))


class Standing(_PieceCase):
    def test_a_piece_cannot_raise_its_standing_or_mark_itself_owner_accepted(self):
        root = self.line_row(prep.ACCEPTED_OWNER_WORK)
        job = self.running(root)
        result = self.schedule(job, accepted_by=prep.ACCEPTED_OWNER_REQUEST, origin=None, state='scheduled')
        piece = self.service.preparations.get(result['preparation_id'])
        self.assertEqual(piece['accepted_by'], prep.ACCEPTED_LINE_PIECE)
        piece_job = self.running(piece)
        self.assertTrue(self.service.self_set_run(piece_job))
        self.assertFalse(self.service.owner_accepted_run(piece_job))
        # Down the chain it stays self-set.
        grand = self.service.preparations.get(self.schedule(piece_job, seconds=1200, accepted_by='owner-settings')['preparation_id'])
        self.assertEqual(grand['accepted_by'], prep.ACCEPTED_LINE_PIECE)
        # Standing is inherited only: the look back, the daily plan or an unknown value give none.
        for parent in (prep.ACCEPTED_DREAM, prep.ACCEPTED_DAY_PLAN, None, 'owner', 'line-piece-owner '):
            with self.subTest(parent=parent):
                self.assertIsNone(prep.piece_standing(parent))
        # A piece exists only with the run it was scheduled from, and only as a scheduled prepare of its line.
        with self.assertRaises(ValueError):
            self.line_row(prep.ACCEPTED_LINE_PIECE_OWNER, origin=prep.ORIGIN_LINE_PIECE, goal='forged')
        with self.assertRaises(ValueError):
            self.service.preparations.create(kind='prepare', goal='forged', due_at=self.now + 600, timezone='Asia/Seoul',
                                             recurrence=None, channel='web', created_from='x', state=prep.STATE_SCHEDULED,
                                             accepted_by=prep.ACCEPTED_OWNER_REQUEST, origin=prep.ORIGIN_LINE_PIECE,
                                             line_id=self.line_id, piece_of='preparation:x:1')

    def test_a_piece_never_takes_up_another_works_waiting_proposal(self):
        due = self.now + 900
        proposal = self.service.preparations.create(kind='prepare', goal='같은 목표', due_at=due, timezone='Asia/Seoul',
                                                    recurrence=None, channel='web', created_from='other-work',
                                                    state=prep.STATE_PROPOSED, line_id=self.line_id)
        job = self.running(self.line_row())
        result = self.schedule(job, seconds=900, goal='같은 목표')
        self.assertNotEqual(result['preparation_id'], proposal['id'])
        self.assertEqual(self.service.preparations.get(proposal['id'])['state'], prep.STATE_PROPOSED)

    def test_a_piece_cannot_write_the_brief(self):
        piece = self.service.preparations.get(self.schedule(self.running(self.line_row()))['preparation_id'])
        job = self.running(piece)
        for action in ('revise', 'open', 'stop', 'restart', 'undo'):
            with self.subTest(action=action), self.assertRaises(ToolError) as refused:
                self.service.line_handler(job)({'action': action, 'line': self.line_id, 'expected_version': '1',
                                                'rule': '한도 없이', 'task': TASK, 'report_promise': PROMISE, 'version': '1'})
            self.assertEqual(refused.exception.code, 'unattended_run')
        self.assertEqual(self.service.lines.current(self.line_id)['version'], 1)


class Payment(_PieceCase):
    def piece_job(self, root_accepted_by):
        piece = self.service.preparations.get(self.schedule(self.running(self.line_row(root_accepted_by, goal='root ' + str(root_accepted_by))))['preparation_id'])
        return self.running(piece)

    def paid(self, job):
        sent = []
        api = ApiRequests(self.store, job['id'], approvals=self.service.browser_approvals_for(job),
                          transport=lambda *a, **k: sent.append(a) or (200, {}, b'{}'))
        try:
            api.call(PAY)
            code = None
        except ToolError as exc:
            code = exc.code
        return len(sent), code

    def test_a_piece_of_an_owner_accepted_line_follows_payment_autonomy(self):
        save_slot(self.store, 'money', ['pay.test'], 'secret-key-value-123456789', money=True)
        self.switch(True)
        job = self.piece_job(prep.ACCEPTED_OWNER_REQUEST)
        self.assertFalse(self.service.self_set_run(job))
        self.assertEqual(self.paid(job), (1, None))
        self.assert_receipt(job['id'], 'api_request')

    def test_a_piece_of_an_owner_steer_row_follows_payment_autonomy_like_other_owner_accepted_roots(self):
        # #984: work a steered run started is owner-accepted (``owner-steer``); its pieces are too.
        self.assertEqual(prep.piece_standing(prep.ACCEPTED_OWNER_STEER), prep.ACCEPTED_LINE_PIECE_OWNER)
        save_slot(self.store, 'money', ['pay.test'], 'secret-key-value-123456789', money=True)
        job = self.piece_job(prep.ACCEPTED_OWNER_STEER)
        self.assertFalse(self.service.self_set_run(job))
        self.assertEqual(self.paid(job), (0, 'unattended_run'))  # autonomy off by default
        self.switch(True)
        self.assertEqual(self.paid(job), (1, None))

    def test_with_autonomy_off_an_owner_accepted_lines_piece_cannot_pay_and_asks_no_approval(self):
        save_slot(self.store, 'money', ['pay.test'], 'secret-key-value-123456789', money=True)
        # Payment autonomy is off until the owner turns it on.
        job = self.piece_job(prep.ACCEPTED_OWNER_REQUEST)
        self.assertEqual(self.paid(job), (0, 'unattended_run'))
        self.assertEqual(self.receipts(job['id']), [])
        self.assertIsNone(self.service._browser_request(job['id']))

    def test_a_piece_of_a_row_still_set_up_on_its_own_never_pays_even_with_autonomy_on(self):
        save_slot(self.store, 'money', ['pay.test'], 'secret-key-value-123456789', money=True)
        self.switch(True)
        job = self.piece_job(prep.ACCEPTED_OWNER_WORK)
        self.assertTrue(self.service.self_set_run(job))
        self.assertIsNone(self.service._authorize_payment_autonomy(job, None, 'pay'))
        self.assertEqual(self.paid(job), (0, 'unattended_run'))
        # Its own piece in turn: still self-set.
        grand = self.service.preparations.get(self.schedule(job, seconds=1200)['preparation_id'])
        grand_job = self.running(grand)
        self.assertEqual(self.paid(grand_job), (0, 'unattended_run'))
        self.assertEqual(self.receipts(job['id']) + self.receipts(grand_job['id']), [])
        # The look back's and the daily plan's rows schedule no piece at all, so none of theirs can pay.
        for accepted_by, origin in ((prep.ACCEPTED_DREAM, prep.ORIGIN_DREAM), (prep.ACCEPTED_DAY_PLAN, prep.ORIGIN_DAY_PLAN),
                                    (prep.ACCEPTED_DAY_PLAN, None)):
            with self.subTest(accepted_by=accepted_by, origin=origin):
                self.refused(self.running(self.line_row(accepted_by, origin=origin, goal=f'{accepted_by} {origin}')), 'unattended_run',
                             line=self.line_id)
        self.assertEqual([piece['accepted_by'] for piece in self.pieces()], [prep.ACCEPTED_LINE_PIECE, prep.ACCEPTED_LINE_PIECE])


class OutsideText(_PieceCase):
    def test_a_child_of_a_marked_run_starts_marked_and_down_the_chain(self):
        root = self.line_row()
        job = self.running(root)
        piece = self.service.preparations.get(self.schedule(job)['preparation_id'])
        # The page is read after the piece was scheduled; the piece starts with the mark all the same.
        self.store.mark_outside_content(job['id'], {'category': 'outside-content', 'operation': 'public_page_read'})
        piece_job = self.piece_run(piece)
        [source] = self.store.outside_content_sources(piece_job['id'])
        self.assertEqual((source['operation'], source['source_work_id']), ('line-piece', job['id']))
        grand = self.service.preparations.get(self.schedule(piece_job, seconds=1200)['preparation_id'])
        self.assertTrue(self.store.outside_content_sources(self.piece_run(grand)['id']))

    def test_a_continuations_mark_reaches_the_child_and_an_unmarked_run_gives_an_unmarked_child(self):
        root = self.line_row()
        first = self.running(root)
        continuation = self.running(root, suffix='/loc:1')
        self.store.mark_outside_content(continuation['id'], {'category': 'outside-content', 'operation': 'web_search'})
        piece = self.service.preparations.get(self.schedule(continuation)['preparation_id'])
        self.assertEqual(piece['piece_of'], prep.slot_key(first['request_key']))
        self.assertTrue(self.store.outside_content_sources(self.piece_run(piece)['id']))
        clean_root = self.line_row(goal='clean')
        clean = self.service.preparations.get(self.schedule(self.running(clean_root))['preparation_id'])
        self.assertEqual(self.store.outside_content_sources(self.piece_run(clean)['id']), [])

    def test_a_marked_run_cannot_widen_what_it_schedules(self):
        other = self.service.lines.open('owner-work', task='다른 일', report_promise=PROMISE)['line_id']
        owner_row = self.line_row(goal='owner row')
        job = self.running(self.line_row(prep.ACCEPTED_OWNER_WORK))
        self.store.mark_outside_content(job['id'], {'category': 'outside-content', 'operation': 'public_page_read'})
        for args in ({'line': other}, {'replaces': owner_row['id']}, {'recurrence': 'daily'}, {'accepted_by': 'owner-request'}):
            with self.subTest(args=args):
                if 'accepted_by' in args:
                    piece = self.service.preparations.get(self.schedule(job, **args)['preparation_id'])
                    self.assertEqual(piece['accepted_by'], prep.ACCEPTED_LINE_PIECE)
                else:
                    self.refused(job, 'line_piece_invalid', **args)
        self.assertEqual(self.service.preparations.get(owner_row['id'])['state'], prep.STATE_SCHEDULED)
        self.assertEqual(self.pieces(other), [])


class Bounds(_PieceCase):
    def test_one_child_per_run_a_second_call_or_its_continuation_replaces_the_pending_child(self):
        root = self.line_row()
        job = self.running(root)
        first = self.schedule(job, seconds=600, goal='첫 인계')
        again = self.schedule(job, seconds=1800, goal='고친 인계')
        self.assertEqual(again['preparation_id'], first['preparation_id'])
        self.assertTrue(again['replaced_pending'])
        continuation = self.running(root, suffix='/loc:1')
        later = self.schedule(continuation, seconds=2400, goal='이어서 고친 인계')
        self.assertEqual(later['preparation_id'], first['preparation_id'])
        [piece] = self.pieces()
        self.assertEqual((piece['goal_text'], piece['due_at']), ('이어서 고친 인계', self.now + 2400))
        # Once it started, or the owner paused or cancelled it, the run schedules nothing more.
        self.piece_run(piece)
        self.refused(continuation, 'line_piece_exists')
        for change in ('pause', 'cancel'):
            with self.subTest(change=change):
                other = self.line_row(goal='another ' + change)
                run = self.running(other)
                child = self.schedule(run)['preparation_id']
                getattr(self.service.preparations, change)(child)
                self.refused(run, 'line_piece_exists')
        self.assertEqual(len(self.pieces()), 3)

    def test_a_due_time_inside_the_lead_time_is_refused(self):
        job = self.running(self.line_row())
        self.refused(job, 'line_piece_too_soon', seconds=prep.PIECE_LEAD_SECONDS - 1)
        self.refused(job, 'line_piece_too_soon', seconds=0)
        self.assertEqual(self.pieces(), [])
        self.assertTrue(self.schedule(job, seconds=prep.PIECE_LEAD_SECONDS)['scheduled'])

    def finished_pieces(self, count, line_id=None):
        """``count`` pieces of the line scheduled today by other runs, each already run."""
        for index in range(count):
            row = self.service.preparations.create(
                kind='prepare', goal=f'piece {index}', due_at=self.now + 600, timezone='Asia/Seoul', recurrence=None,
                channel='web', created_from='run', state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_LINE_PIECE,
                origin=prep.ORIGIN_LINE_PIECE, line_id=line_id or self.line_id, piece_of=f'preparation:p{index}:1')
            with self.store.db() as db:
                db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_DELIVERED, row['id']))

    def test_the_25th_piece_of_a_line_in_one_owner_day_is_refused_even_with_the_shared_budget_off(self):
        self.service.preparations.set_budget(0)
        self.finished_pieces(prep.LINE_PIECES_PER_DAY)
        job = self.running(self.line_row())
        self.refused(job, 'line_pieces_daily')
        self.assertEqual(len(self.pieces()), prep.LINE_PIECES_PER_DAY)
        # Another line has its own count.
        other = self.service.lines.open('owner-work', task='다른 일', report_promise=PROMISE)['line_id']
        self.assertTrue(self.schedule(self.running(self.line_row(line=other, goal='other')))['scheduled'])
        # The next owner-local day counts afresh.
        self.now += 86400
        self.assertTrue(self.schedule(job)['scheduled'])

    def test_the_11th_pending_piece_in_the_store_is_refused(self):
        for index in range(prep.MAX_PENDING_PIECES):
            self.schedule(self.running(self.line_row(goal=f'root {index}')))
        self.assertEqual(len(self.pieces()), prep.MAX_PENDING_PIECES)
        self.refused(self.running(self.line_row(goal='one more')), 'line_pieces_limit')
        self.assertEqual(len(self.pieces()), prep.MAX_PENDING_PIECES)

    def test_every_piece_run_is_charged_to_the_shared_daily_budget(self):
        piece = self.service.preparations.get(self.schedule(self.running(self.line_row()))['preparation_id'])
        job = self.piece_run(piece)
        day = self.service.preparations.budget_day(piece['due_at'])
        self.assertEqual(self.store.config(prep.SCHEDULED_USAGE_PREFIX + day), 1)
        self.assertTrue([event for event in self.store.task_events(job['id']) if event['tool'] == prep.SCHEDULED_BUDGET_KEY])


class StopAndForget(_PieceCase):
    def test_stop_cancels_the_pending_piece(self):
        piece = self.schedule(self.running(self.line_row()))
        self.service.preparations_service.stop_line(self.line_id)
        self.assertEqual(self.service.preparations.get(piece['preparation_id'])['state'], prep.STATE_CANCELLED)

    def test_a_piece_scheduled_while_its_line_stops_is_refused(self):
        root = self.line_row()
        job = self.running(root)
        fields = self.service.preparations_service.preparation_fields

        def stop_between(args, now, *rest):
            result = fields(args, now, *rest)
            self.service.preparations_service.stop_line(self.line_id)
            return result
        with mock.patch.object(self.service, 'preparation_fields', side_effect=stop_between):
            self.refused(job, 'line_stopped')
        self.assertEqual(self.pieces(), [])
        # A stopped brief alone (before its rows are cancelled) already refuses.
        other = self.service.lines.open('owner-work', task='다른 일', report_promise=PROMISE)['line_id']
        run = self.running(self.line_row(line=other, goal='other'))
        self.service.lines.write(other, None, None, 'stopped', lambda brief: {**brief, 'state': el.STATE_STOPPED})
        self.refused(run, 'line_stopped')
        self.assertEqual(self.pieces(other), [])

    def test_current_work_stop_prevents_a_piece_from_being_created_after_the_stop(self):
        root = self.line_row()
        job = self.running(root)
        fields = self.service.preparation_fields

        def stop_between(args, now):
            result = fields(args, now)
            self.service.preparations_service.stop_line(self.line_id, end_line=False)
            return result
        with mock.patch.object(self.service, 'preparation_fields', side_effect=stop_between):
            self.refused(job, 'unattended_run')
        self.assertEqual(self.pieces(), [])
        self.assertEqual(self.service.lines.current(self.line_id)['state'], el.STATE_ACTIVE)

    def test_forget_cancels_pending_pieces_and_undo_restores_only_the_brief(self):
        root = self.line_row()
        piece = self.schedule(self.running(root))
        receipt = OwnerForget(self.store).forget('local-owner', 'line:' + self.line_id)
        self.assertEqual(self.service.preparations.get(piece['preparation_id'])['state'], prep.STATE_CANCELLED)
        # A running piece of the forgotten line schedules nothing more.
        self.refused(self.running(root, suffix='/loc:1'), 'line_stopped')
        OwnerForget(self.store).undo('local-owner', receipt['receipt'])
        self.assertEqual(self.service.lines.current(self.line_id)['state'], el.STATE_ACTIVE)
        self.assertEqual(self.service.preparations.get(piece['preparation_id'])['state'], prep.STATE_CANCELLED)

    def test_a_cancelled_piece_is_not_a_declined_proposal(self):
        piece = self.schedule(self.running(self.line_row()), goal='끝난 인계')
        self.service.preparations_service.stop_line(self.line_id)
        self.assertEqual(self.service.preparations.get(piece['preparation_id'])['state'], prep.STATE_CANCELLED)
        self.assertNotIn('끝난 인계', self.service.preparations.declined_goals(self.now))


class Handoff(_PieceCase):
    def test_only_a_run_that_may_continue_in_pieces_is_told_how(self):
        guidance = msg('lines.piece_guidance', line=self.line_id, minutes=prep.PIECE_LEAD_SECONDS // 60)
        root = self.line_row()
        self.assertIn(guidance, self.service.preparations_service.line_run_input(self.running(root), root))
        account = self.line_row(prep.ACCEPTED_OWNER_WORK, origin=prep.ORIGIN_LINE_ACCOUNT, recurrence='daily', goal='account')
        self.assertNotIn(guidance, self.service.preparations_service.line_run_input(self.running(account), account))

    def test_a_line_with_many_rows_still_reads_its_outcomes(self):
        with self.store.db() as db:
            for index in range(1100):
                db.execute('INSERT INTO preparations(id,owner,kind,goal_text,due_at,timezone,channel,state,created_at,updated_at,line_id) '
                           'VALUES (?,?,?,?,?,?,?,?,?,?,?)', (f'r{index:015d}', 'local-owner', 'prepare', 'g', self.now, 'Asia/Seoul',
                                                              'web', prep.STATE_DELIVERED, self.now, self.now, self.line_id))
            work = self.store.enqueue('g', prep.request_key('r000000000000007', self.now), db=db)
            db.execute("UPDATE jobs SET status='succeeded',response='찾은 상태',delivery='none' WHERE id=?", (work,))
        [outcome] = self.service.preparations_service.line_outcomes(self.line_id)
        self.assertEqual((outcome['work_id'], outcome['text']), (work, '찾은 상태'))
        # The next run's history reads that earlier report too.
        self.now += 60
        run = self.running(self.line_row(goal='next'))
        history = self.service.preparations_service.preparation_history(run)
        self.assertIn('찾은 상태', json.dumps(history, ensure_ascii=False))

    def test_a_line_continues_in_pieces_through_the_worker_and_each_piece_reads_its_handoff(self):
        self.now += 1  # the line this Work opens is the newest
        brief, _work = self.open_line(recurrence=None)
        [row] = self.work_rows(brief['line_id'])
        handoff = '한 일: 첫 확인. 찾은 상태: 대기 중. 다음 조각이 확인할 것: 상태 변화.'
        seen = []

        def schedule_next(body):
            return {'content': None, 'tool_calls': [call('n', 'schedule_preparation', kind='prepare', goal=handoff,
                                                          due=self.due_iso(1800), line=brief['line_id'])]}

        def answer(body):
            seen.extend(tool_results(body, 'schedule_preparation'))
            return {'content': '첫 단계 완료, 30분 뒤 이어서 확인해요.'}
        bodies = self.run_line(row, [schedule_next, answer])
        self.assertIn(msg('lines.piece_guidance', line=brief['line_id'], minutes=5), json.dumps(bodies, ensure_ascii=False))
        self.assertTrue(seen and seen[0].get('scheduled'), seen)
        [piece] = [item for item in self.work_rows(brief['line_id']) if item.get('origin') == prep.ORIGIN_LINE_PIECE]
        self.assertEqual((piece['goal_text'], piece['line_id']), (handoff, brief['line_id']))
        bodies = self.run_line(piece, [{'content': '상태 변화 없음.'}])
        self.assertIn(handoff, json.dumps(bodies, ensure_ascii=False))
        self.assertEqual(self.service.preparations.get(piece['id'])['state'], prep.STATE_DELIVERED)


class OwnerView(_PieceCase):
    """Slice 3 (D4): the owner sees the pending piece and how many pieces ran, through existing views."""

    def piece_row(self, state, due_at=None):
        """A piece row of the line as a chain of runs left it (written directly: its parent run is over)."""
        self.serial = getattr(self, 'serial', 0) + 1
        piece_id = f'piece{self.serial:011d}'
        due_at = due_at or self.now + 3600
        with self.store.db() as db:
            db.execute('INSERT INTO preparations(id,owner,kind,goal_text,due_at,timezone,channel,state,created_at,updated_at,'
                       'line_id,origin,accepted_by,piece_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (piece_id, 'local-owner', 'prepare', '조각', due_at, 'Asia/Seoul', 'web', state, self.now, self.now,
                        self.line_id, prep.ORIGIN_LINE_PIECE, prep.ACCEPTED_LINE_PIECE_OWNER, f'preparation:parent:{self.serial}'))
        return self.service.preparations.get(piece_id)

    def piece_with_run(self, status, created, *, delivery='none', state=prep.STATE_DELIVERED):
        """A piece row of the line and one run of it, created at ``created``."""
        piece = self.piece_row(state, due_at=created)
        work = self.store.enqueue('조각', prep.request_key(piece['id'], piece['due_at']))
        with self.store.db() as db:
            db.execute('UPDATE jobs SET status=?,delivery=?,response=?,created=? WHERE id=?',
                       (status, delivery, '조각 결과', created, work))
            db.execute('UPDATE preparations SET last_run_job_id=? WHERE id=?', (work, piece['id']))
        return piece

    def account(self):
        return self.line_row(prep.ACCEPTED_OWNER_WORK, origin=prep.ORIGIN_LINE_ACCOUNT, recurrence='daily', goal='account')

    def test_the_pending_piece_shows_as_the_lines_next_run(self):
        root = self.line_row()
        job = self.running(root)
        self.schedule(job, seconds=1800)
        [piece] = self.pieces()
        with self.store.db() as db:
            db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_DELIVERED, root['id']))
        entry = self.service.preparations_service.line_entry(self.service.lines.current(self.line_id))
        self.assertEqual(entry['next'], prep.local_text(piece['due_at'], piece['timezone']))
        self.assertNotIn('next_is_account', entry)

    def test_the_account_counts_the_pieces_of_its_period_and_names_the_next_one(self):
        account = self.account()
        # The account's previous run opens the period; a piece run before it belongs to the last account.
        period = self.now + 100
        with self.store.db() as db:
            earlier = self.store.enqueue('account', prep.request_key(account['id'], period), db=db)
            db.execute("UPDATE jobs SET status='succeeded',created=? WHERE id=?", (period, earlier))
        self.piece_with_run('succeeded', period - 50, delivery='sent')
        self.piece_with_run('succeeded', period + 10, delivery='sent')
        self.piece_with_run('succeeded', period + 20)
        self.piece_with_run('failed', period + 30, state=prep.STATE_FAILED)
        self.now = period + 86400
        pending = self.piece_row(prep.STATE_SCHEDULED)
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=? WHERE id=?', (self.now, account['id']))
        account = self.service.preparations.get(account['id'])
        text = self.service.preparations_service.line_run_input(self.running(account), account)
        self.assertIn(msg('lines.account_pieces', count=3, delivered=1, kept=1, failed=1, open=0), text)
        self.assertIn(msg('lines.account_next_piece', at=prep.local_text(pending['due_at'], pending['timezone'])), text)

    def test_an_account_of_a_line_without_pieces_and_an_ordinary_run_get_no_count(self):
        account = self.account()
        self.assertEqual(self.service.preparations_service.line_pieces_account(account, self.running(account)), '')
        self.piece_with_run('succeeded', self.now, delivery='sent')
        root = self.line_row(goal='ordinary')
        text = self.service.preparations_service.line_run_input(self.running(root), root)
        self.assertNotIn(msg('lines.account_pieces', count=1, delivered=1, kept=0, failed=0, open=0), text)

    def test_a_paused_piece_is_not_named_as_due_and_does_not_hide_a_scheduled_one(self):
        account = self.account()
        paused = self.piece_row(prep.STATE_PAUSED, due_at=self.now + 600)
        text = self.service.preparations_service.line_pieces_account(account, self.running(account))
        self.assertIn(msg('lines.account_piece_paused'), text)
        self.assertNotIn(msg('lines.account_next_piece', at=prep.local_text(paused['due_at'], paused['timezone'])), text)
        scheduled = self.piece_row(prep.STATE_SCHEDULED, due_at=self.now + 7200)
        text = self.service.preparations_service.line_pieces_account(account, self.running(account, '/1'))
        self.assertIn(msg('lines.account_next_piece', at=prep.local_text(scheduled['due_at'], scheduled['timezone'])), text)
        self.assertNotIn(msg('lines.account_piece_paused'), text)

    def test_a_late_account_and_the_next_one_count_each_piece_run_once(self):
        account = self.account()
        slot = self.now + 100
        late = slot + 20  # the account due at ``slot`` started 20 seconds late
        with self.store.db() as db:
            previous = self.store.enqueue('account', prep.request_key(account['id'], slot), db=db)
            db.execute("UPDATE jobs SET status='succeeded',created=? WHERE id=?", (late, previous))
        self.piece_with_run('succeeded', slot + 10, delivery='sent')  # the previous account already counted it
        self.piece_with_run('succeeded', late + 10, delivery='sent')
        self.now = slot + 86400
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=? WHERE id=?', (self.now, account['id']))
        account = self.service.preparations.get(account['id'])
        job = self.running(account)
        with self.store.db() as db:
            db.execute('UPDATE jobs SET created=? WHERE id=?', (self.now + 5, job['id']))
        self.piece_with_run('succeeded', self.now + 30, delivery='sent')  # after this account started: the next one's
        text = self.service.preparations_service.line_pieces_account(account, self.store.job(job['id']))
        self.assertIn(msg('lines.account_pieces', count=1, delivered=1, kept=0, failed=0, open=0), text)


class SettingsGroups(_PieceCase):
    """#1005: Settings lists a line's finished pieces as one entry, and they take up no list room."""

    piece_row, piece_with_run = OwnerView.piece_row, OwnerView.piece_with_run

    def status(self):
        return self.service.preparations_service.preparations_status()

    def test_finished_pieces_leave_the_list_and_come_back_once_per_line(self):
        root = self.line_row()
        pending = self.piece_row(prep.STATE_SCHEDULED)
        self.now += 10
        self.piece_with_run('completed', self.now, state=prep.STATE_DELIVERED)
        self.now += 10
        newest = self.piece_with_run('failed', self.now, state=prep.STATE_FAILED)
        self.now += 10
        self.piece_row(prep.STATE_CANCELLED)
        status = self.status()
        listed = {row['id'] for row in status['preparations']}
        self.assertIn(root['id'], listed)
        self.assertIn(pending['id'], listed, 'a piece that still runs keeps its own row and actions')
        self.assertFalse(listed & {piece['id'] for piece in self.pieces() if piece['state'] not in prep.ACTIVE_STATES})
        [group] = status['piece_groups']
        self.assertEqual((group['line_id'], group['line_task'], group['count']), (self.line_id, TASK, 3))
        self.assertEqual(group['states'], {prep.STATE_DELIVERED: 1, prep.STATE_FAILED: 1, prep.STATE_CANCELLED: 1})
        self.assertEqual([piece['state'] for piece in group['pieces']], [prep.STATE_CANCELLED, prep.STATE_FAILED, prep.STATE_DELIVERED])
        self.assertEqual(group['pieces'][1]['id'], newest['id'])
        self.assertEqual(group['pieces'][1]['last_result'], '조각 결과')
        self.assertEqual(status['management']['pieces_finished']['ko'], msg('preparations.pieces_finished', 'ko'))

    def test_finished_pieces_take_up_no_room_in_the_list_and_show_the_newest_few(self):
        for _ in range(prep.FINISHED_PIECES_SHOWN + 3):
            self.now += 1
            self.piece_row(prep.STATE_DELIVERED, due_at=self.now - 60)
        other = self.service.preparations.create(kind='reminder', goal='다른 알림', due_at=self.now + 60, timezone='Asia/Seoul',
                                                 recurrence=None, channel='web', created_from=None, state=prep.STATE_SCHEDULED,
                                                 accepted_by=prep.ACCEPTED_OWNER_REQUEST)
        with self.store.db() as db:
            db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_DELIVERED, other['id']))
        limited = self.service.preparations.rows(limit=2, finished_pieces=False)
        self.assertEqual([row['id'] for row in limited], [other['id']])
        self.assertEqual(len(self.service.preparations.rows(limit=200)), prep.FINISHED_PIECES_SHOWN + 4, 'the default read is unchanged')
        [group] = self.status()['piece_groups']
        self.assertEqual(group['count'], prep.FINISHED_PIECES_SHOWN + 3)
        self.assertEqual(len(group['pieces']), prep.FINISHED_PIECES_SHOWN)

    def test_no_group_without_finished_pieces(self):
        self.line_row()
        self.piece_row(prep.STATE_SCHEDULED)
        self.assertEqual(self.status()['piece_groups'], [])


GROUP_UI_CHECK = r"""
ctx.api=async()=>({preparations:ctx.rows,piece_groups:ctx.groups,management:JSON.parse(process.argv[2])});
const lines=node=>descendants(node).filter(n=>n.className==='settings-technical-line').map(n=>n.textContent);
(async()=>{
 const line={id:'r1',kind:'prepare',goal:'환불 지켜보기',state:'scheduled',due_local:'D1',recurrence:'daily',delivery:'send',last_result:'',line_id:'L1',line_task:'환불 지켜보기'};
 const other={id:'r2',kind:'reminder',goal:'치과',state:'scheduled',due_local:'D2',recurrence:null,delivery:'send',last_result:''};
 const piece={id:'r3',kind:'prepare',goal:'다음 조각',state:'scheduled',due_local:'D3',recurrence:null,delivery:'send',last_result:'',line_id:'L1',line_task:'환불 지켜보기'};
 ctx.rows=[line,other,piece];
 ctx.groups=[{line_id:'L1',line_task:'환불 지켜보기',count:3,states:{delivered:2,failed:1},
  pieces:[{id:'g1',state:'failed',due_local:'T1',last_decision:'quiet',last_decision_reason:'run-did-not-finish',last_result:''},
          {id:'g2',state:'delivered',due_local:'T2',last_decision:null,last_result:'환불 처리 중'}]},
  {line_id:'L2',line_task:'신청 열림 확인',count:1,states:{delivered:1},pieces:[{id:'g3',state:'delivered',due_local:'T3',last_result:'열렸어요'}]}];
 await ctx.loadPreparations();
 const listed=rows();
 assert.deepEqual(listed.map(n=>texts(n,'settings-row-title')[0]),['환불 지켜보기','치과','다음 조각','환불 지켜보기','신청 열림 확인'],
  'each group follows its line\'s last listed row once; a line with only finished pieces comes at the end');
 const group=listed[3];
 assert.equal(texts(group,'settings-row-description')[0],'이 일의 끝난 조각 3개 · 완료 2, 실패 1 · T1 · 마지막 실행: 조용히 넘어감 (결과를 얻지 못함)');
 assert.deepEqual(texts(group,'settings-state danger'),['실패']);
 assert.deepEqual(buttons(group),[],'a finished piece has nothing to run, pause or cancel');
 assert.deepEqual(lines(group),['실패 · T1 · 마지막 실행: 조용히 넘어감 (결과를 얻지 못함)','완료 · T2 · 환불 처리 중']);
 assert.equal(descendants(group).find(n=>n.tag==='summary').textContent,'가장 늦게 끝난 조각 2개 보기');
 const details=descendants(group).find(n=>n.tag==='details');details.open=true;details.ontoggle();
 ctx.groups=[...ctx.groups];ctx.groups[0]={...ctx.groups[0],count:4};await ctx.loadPreparations();
 assert.equal(descendants(rows()[3]).find(n=>n.tag==='details').open,true,'an opened group stays open on refresh');
 vm.runInContext("setLanguage('en')",ctx);ctx.renderPreparations();
 assert(texts(rows()[3],'settings-row-description')[0].startsWith('4 finished pieces of this work'));
 ctx.rows=[];ctx.groups=[ctx.groups[1]];await ctx.loadPreparations();
 assert.equal(rows().length,1,'finished pieces alone are not an empty list');
 console.log('ok');
})().catch(error=>{console.error(error);process.exit(1);});
"""


class SettingsGroupsUi(unittest.TestCase):
    def test_settings_lists_a_lines_finished_pieces_as_one_entry(self):
        import shutil
        import subprocess
        from pathlib import Path
        from test_preparations import UI_CHECK
        node = shutil.which('node')
        if node is None:
            raise unittest.SkipTest('Node is needed for the DOM-stub check')
        app = Path(__file__).resolve().parents[1] / 'src' / 'personal_agent' / 'web' / 'app.js'
        labels = self.service_labels()
        check = UI_CHECK.split('(async()=>{')[0] + GROUP_UI_CHECK
        result = subprocess.run([node, '-e', check, str(app), json.dumps(labels)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], 'ok')

    @staticmethod
    def service_labels():
        keys = {'pieces_finished': 'preparations.pieces_finished', 'pieces_newest': 'preparations.pieces_newest',
                **{'delivery_' + mode: 'preparations.delivery_' + mode for mode in ('send', 'keep', 'when_needed')}}
        return {key: {lang: msg(message_id, lang) for lang in ('ko', 'en', 'ja', 'zh')} for key, message_id in keys.items()}
