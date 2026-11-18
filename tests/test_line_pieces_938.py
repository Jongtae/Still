"""#938 (owner decisions 2026-11-17 and 2026-11-18): a line's own run schedules the next piece of its work.

Evidence class: model-free, fixture transports, as ``test_entrusted_lines_798``: a scripted
compatible-API model, a recording Telegram transport, a FixtureDecisionEngine and a fake clock;
the real QuickStore, ``AgentService`` and ``Preparations`` run unchanged.  No live model, Telegram
or provider is contacted, and none is claimed.
"""
from personal_agent import entrusted_lines as el
from personal_agent import preparations as prep
from personal_agent.agent_runtime import ToolError
from personal_agent.owner_forget import OwnerForget

from personal_agent import browser_session as bs
from personal_agent.messages import msg

from test_entrusted_lines_798 import _LineCase, tool_results
from test_payment_autonomy_662 import Mode
from test_preparations import call

HANDOFF = '1주 보유 중. 다음 확인: 손절선 아래로 내려갔는지 보고 그대로면 다음 확인을 예약.'


class _PieceCase(_LineCase):
    def owner_line(self, **kwargs):
        """An owner-set-up line whose announcement was sent, and its one work row."""
        brief, work = self.open_line(**kwargs)
        self.deliver_all()
        [row] = self.work_rows(brief['line_id'])
        return brief, row

    def run_scheduling(self, row, *calls):
        """Run ``row`` once; its worker makes the ``schedule_preparation`` calls in one step; returns (results, run).

        A call's ``due_in`` is seconds after the run starts.  ``run_line`` gives each step twice, so the
        repeated identical calls are refused as ``duplicate_call`` and left out of the results.
        """
        seen = []

        def step(body):
            made = []
            for index, args in enumerate(calls):
                args = dict(args)
                if 'due_in' in args:
                    args['due'] = self.due_iso(args.pop('due_in'))
                if 'until_in' in args:
                    args['until'] = self.due_iso(args.pop('until_in'))
                made.append(call(f'piece{index}', 'schedule_preparation', **args))
            return {'content': None, 'tool_calls': made}

        def answer(body):
            seen[:] = [item for item in tool_results(body, 'schedule_preparation')
                       if not (isinstance(item, dict) and item.get('code') == 'duplicate_call')]
            return {'content': '지금까지: 변동 없음.'}
        self.run_line(row, [step, answer])
        run = self.runs(row['id'])[-1]
        return seen, run

    def piece_args(self, seconds=3600, **extra):
        return {'kind': 'prepare', 'goal': HANDOFF, 'due_in': seconds, **extra}

    def pieces(self, line_id):
        return [row for row in self.service.preparations_service.line_rows(line_id)
                if row.get('origin') == prep.ORIGIN_LINE_PIECE]

    def running_slot(self, line_id):
        """A running owner-accepted row of the line; the slot key of its current run."""
        row = self.service.preparations.create(
            kind='prepare', goal=f'확인 {self.now} {len(self.rows())}', due_at=self.now, timezone='Asia/Seoul',
            recurrence=None, channel='web', created_from='settings', state=prep.STATE_SCHEDULED,
            accepted_by=prep.ACCEPTED_OWNER_SETTINGS, line_id=line_id)
        work_id = self.service.preparations.start(row, channel='web', chat_id=None, now=self.now)
        return prep.slot_key(self.store.job(work_id)['request_key'])

    def direct_piece(self, line_id, parent_slot, seconds=3600):
        return self.service.preparations.create(
            kind='prepare', goal=HANDOFF, due_at=self.now + seconds, timezone='Asia/Seoul', recurrence=None,
            channel='web', created_from='run', state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_LINE_PIECE,
            origin=prep.ORIGIN_LINE_PIECE, line_id=line_id, piece_of=parent_slot)


class SchedulingTests(_PieceCase):
    def test_a_line_run_schedules_its_next_piece_with_the_handoff_and_the_parent_delivery(self):
        brief, row = self.owner_line()
        seen, run = self.run_scheduling(row, self.piece_args(line=brief['line_id']))
        [result] = seen
        self.assertTrue(result['scheduled'] and result['piece'], result)
        self.assertFalse(result['replaced_piece'])
        [piece] = self.pieces(brief['line_id'])
        self.assertEqual((piece['origin'], piece['accepted_by'], piece['state'], piece['kind']),
                         (prep.ORIGIN_LINE_PIECE, prep.ACCEPTED_LINE_PIECE, prep.STATE_SCHEDULED, prep.KIND_PREPARE))
        self.assertEqual(piece['goal_text'], HANDOFF)
        self.assertEqual(piece['piece_of'], prep.slot_key(run['request_key']))
        self.assertEqual((piece['channel'], piece['delivery_mode']), (row['channel'], row['delivery_mode']))
        self.assertIsNone(piece['recurrence'])
        # No new notice type: a piece gets no scheduled-with-cancel message of its own.
        self.assertIsNone(self.store.notification_of(run['id'], 'preparation_scheduled'))

    def test_the_line_defaults_to_the_run_line_and_another_line_is_refused(self):
        brief, row = self.owner_line()
        seen, _run = self.run_scheduling(row, self.piece_args(line='0123456789abcdef'), self.piece_args())
        self.assertEqual(seen[0]['code'], 'line_mismatch')
        self.assertTrue(seen[1]['scheduled'])
        self.assertEqual(len(self.pieces(brief['line_id'])), 1)

    def test_a_second_call_of_the_same_run_replaces_its_pending_piece(self):
        brief, row = self.owner_line()
        seen, _run = self.run_scheduling(row, self.piece_args(3600), self.piece_args(7200, goal='다시 정리한 인계'))
        self.assertEqual(seen[0]['preparation_id'], seen[1]['preparation_id'])
        self.assertTrue(seen[1]['replaced_piece'])
        [piece] = self.pieces(brief['line_id'])
        self.assertEqual(piece['goal_text'], '다시 정리한 인계')
        self.assertEqual(prep.iso(piece['due_at'], piece['timezone']), seen[1]['due'])

    def test_shapes_other_than_one_one_off_prepare_of_the_line_are_refused(self):
        brief, row = self.owner_line()
        seen, _run = self.run_scheduling(
            row,
            {'kind': 'reminder', 'goal': '확인 알림', 'due_in': 3600},
            self.piece_args(recurrence='daily'),
            self.piece_args(every_minutes='30', until_in=4 * 3600),
            self.piece_args(replaces=row['id']))
        self.assertEqual([item['code'] for item in seen], ['piece_shape'] * 4, seen)
        self.assertEqual(self.pieces(brief['line_id']), [])

    def test_a_piece_sooner_than_the_lead_time_is_refused(self):
        brief, row = self.owner_line()
        seen, _run = self.run_scheduling(row, self.piece_args(prep.PIECE_LEAD_SECONDS - 60))
        self.assertEqual(seen[0]['code'], 'piece_too_soon')
        self.assertEqual(self.pieces(brief['line_id']), [])

    def test_a_piece_run_continues_the_chain_and_still_cannot_write_the_brief(self):
        brief, row = self.owner_line()
        self.run_scheduling(row, self.piece_args(3600))
        [piece] = self.pieces(brief['line_id'])
        seen, run = self.run_scheduling(piece, self.piece_args(3600, goal='세 번째 조각'))
        self.assertTrue(seen[0]['scheduled'], seen)
        pieces = self.pieces(brief['line_id'])
        self.assertEqual(len(pieces), 2)
        child = next(item for item in pieces if item['id'] != piece['id'])
        self.assertEqual(child['piece_of'], prep.slot_key(run['request_key']))
        # A piece is a self-set run: it never pays and never uses owner acceptance.
        job = self.store.job(run['id'])
        self.assertTrue(self.service.self_set_run(job))
        self.assertFalse(self.service.owner_accepted_run(job))
        with self.assertRaises(ToolError) as refused:
            self.service.line_handler(job)({'action': 'revise', 'line': brief['line_id'], 'expected_version': '1',
                                            'rule': '한도 없이 계속하기'})
        self.assertEqual(refused.exception.code, 'unattended_run')
        self.assertEqual(self.service.lines.current(brief['line_id'])['version'], 1)


class QualifyingRunTests(_PieceCase):
    """``piece_parent``: only the row's current slot run (or its continuation) of an owner-set-up line."""

    def started(self):
        brief, row = self.owner_line()
        self.now = row['due_at'] + 1
        work_id = self.service.preparations.start(self.service.preparations.get(row['id']), channel='web',
                                                  chat_id=None, now=self.now)
        return brief, self.service.preparations.get(row['id']), self.store.job(work_id)

    def test_the_current_run_and_its_continuation_qualify(self):
        _brief, row, job = self.started()
        self.assertEqual(self.service.preparations_service.piece_parent(job)['id'], row['id'])
        continuation = {**job, 'id': 'cont', 'request_key': job['request_key'] + prep.CONTINUATION_SEPARATOR + 'loc:1'}
        self.assertEqual(self.service.preparations_service.piece_parent(continuation)['id'], row['id'])

    def test_a_stale_slot_a_cancelled_row_and_a_look_back_line_do_not(self):
        brief, row, job = self.started()
        stale = {**job, 'id': 'old', 'request_key': prep.request_key(row['id'], row['due_at'] - 86400)}
        self.assertIsNone(self.service.preparations_service.piece_parent(stale))
        lines = self.service.lines
        provenance = lines.provenance
        lines.provenance = lambda line: {'by': 'look-back'}
        try:
            self.assertIsNone(self.service.preparations_service.piece_parent(job))
        finally:
            lines.provenance = provenance
        self.service.cancel_preparation(row['id'])
        self.assertIsNone(self.service.preparations_service.piece_parent(job))
        with self.assertRaises(ToolError) as refused:
            self.service.preparation_scheduler(job, '')({'kind': 'prepare', 'goal': HANDOFF, 'due': self.due_iso(3600),
                                                         'line': brief['line_id']})
        self.assertEqual(refused.exception.code, 'unattended_run')


class StillRefusedTests(_PieceCase):
    """Every unattended run that is not a qualifying line row's stays refused with ``unattended_run``."""

    def running(self, row):
        self.now = max(self.now, row['due_at']) + 1
        work_id = self.service.preparations.start(self.service.preparations.get(row['id']), channel='web',
                                                  chat_id=None, now=self.now)
        self.assertTrue(work_id)
        return self.store.job(work_id)

    def refused(self, job, **args):
        with self.assertRaises(ToolError) as refused:
            self.service.preparation_scheduler(job, '')({'kind': 'prepare', 'goal': HANDOFF,
                                                         'due': self.due_iso(3600), **args})
        return refused.exception.code

    def test_ordinary_look_back_day_plan_and_account_rows(self):
        brief, _row = self.owner_line()
        base = dict(kind='prepare', goal='확인', due_at=self.now + 60, timezone='Asia/Seoul', recurrence=None,
                    channel='web', created_from='settings', state=prep.STATE_SCHEDULED)
        cases = {
            'outside a line': self.service.preparations.create(**base, accepted_by=prep.ACCEPTED_OWNER_SETTINGS),
            'look-back': self.service.preparations.create(**{**base, 'goal': '되돌아보기'}, accepted_by=prep.ACCEPTED_DREAM,
                                                          line_id=brief['line_id']),
            'day plan': self.service.preparations.create(**{**base, 'goal': '계획'}, accepted_by=prep.ACCEPTED_DAY_PLAN,
                                                         line_id=brief['line_id']),
            'periodic account': next(row for row in self.service.preparations_service.line_rows(brief['line_id'])
                                     if row.get('origin') == prep.ORIGIN_LINE_ACCOUNT),
        }
        for name, row in cases.items():
            with self.subTest(row=name):
                job = self.running(row)
                self.assertIsNone(self.service.preparations_service.piece_parent(job))
                self.assertEqual(self.refused(job, line=brief['line_id']), 'unattended_run')
        self.assertEqual(self.pieces(brief['line_id']), [])


class BoundTests(_PieceCase):
    def test_the_twenty_fifth_piece_of_a_line_in_one_day_is_refused(self):
        # The cap is read in ``create``, apart from the shared scheduled-work budget (which charges runs).
        brief, _row = self.owner_line()
        for number in range(prep.LINE_PIECES_DAILY):
            piece = self.direct_piece(brief['line_id'], self.running_slot(brief['line_id']))
            # Each finishes, so the store-wide pending allowance is not what refuses the next.
            with self.store.db() as db:
                db.execute("UPDATE preparations SET state='delivered' WHERE id=?", (piece['id'],))
        with self.assertRaises(prep.PreparationRefusal) as refused:
            self.direct_piece(brief['line_id'], self.running_slot(brief['line_id']))
        self.assertEqual(refused.exception.code, 'line_pieces_daily')
        # The next owner-local day starts a new count.
        self.now += 86400
        self.assertTrue(self.direct_piece(brief['line_id'], self.running_slot(brief['line_id']))['id'])

    def test_the_eleventh_pending_piece_in_the_store_is_refused(self):
        brief, _row = self.owner_line()
        for number in range(prep.MAX_PENDING_PIECES):
            self.direct_piece(brief['line_id'], self.running_slot(brief['line_id']))
        with self.assertRaises(prep.PreparationRefusal) as refused:
            self.direct_piece(brief['line_id'], self.running_slot(brief['line_id']))
        self.assertEqual(refused.exception.code, 'pieces_limit')

    def test_a_started_piece_cannot_be_replaced_or_followed_by_a_second_child(self):
        brief, _row = self.owner_line()
        slot = self.running_slot(brief['line_id'])
        piece = self.direct_piece(brief['line_id'], slot)
        with self.store.db() as db:
            db.execute("UPDATE preparations SET state='running' WHERE id=?", (piece['id'],))
        with self.assertRaises(prep.PreparationRefusal) as refused:
            self.direct_piece(brief['line_id'], slot, seconds=7200)
        self.assertEqual(refused.exception.code, 'piece_started')

    def test_a_run_whose_piece_was_cancelled_cannot_schedule_it_again(self):
        brief, _row = self.owner_line()
        slot = self.running_slot(brief['line_id'])
        piece = self.direct_piece(brief['line_id'], slot)
        self.service.cancel_preparation(piece['id'])
        with self.assertRaises(prep.PreparationRefusal) as refused:
            self.direct_piece(brief['line_id'], slot, seconds=7200)
        self.assertEqual(refused.exception.code, 'piece_cancelled')

    def test_a_piece_scheduled_as_its_line_stops_is_refused_inside_the_transaction(self):
        brief, _row = self.owner_line()
        slot = self.running_slot(brief['line_id'])
        self.service.preparations_service.stop_line(brief['line_id'])
        with self.assertRaises(prep.PreparationRefusal) as refused:
            self.direct_piece(brief['line_id'], slot)
        self.assertEqual(refused.exception.code, 'line_stopped')
        self.assertEqual(self.pieces(brief['line_id']), [])

    def test_a_parent_cancelled_before_the_piece_is_created_refuses_it(self):
        brief, _row = self.owner_line()
        slot = self.running_slot(brief['line_id'])
        self.service.cancel_preparation(prep.preparation_of(slot))
        with self.assertRaises(prep.PreparationRefusal) as refused:
            self.direct_piece(brief['line_id'], slot)
        self.assertEqual(refused.exception.code, 'piece_parent_ended')
        self.assertEqual(self.pieces(brief['line_id']), [])

    def test_a_piece_needs_its_parent_run(self):
        with self.assertRaises(ValueError):
            self.service.preparations.create(kind='prepare', goal='x', due_at=self.now + 3600, timezone='Asia/Seoul',
                                             recurrence=None, channel='web', created_from='run',
                                             state=prep.STATE_SCHEDULED, origin=prep.ORIGIN_LINE_PIECE, line_id='l')


class StopTests(_PieceCase):
    def test_cancelling_the_work_row_ends_its_chain(self):
        brief, row = self.owner_line()
        self.run_scheduling(row, self.piece_args())
        self.service.cancel_preparation(row['id'])
        [piece] = self.pieces(brief['line_id'])
        self.assertEqual(piece['state'], prep.STATE_CANCELLED)
        self.assertEqual(self.service.lines.current(brief['line_id'])['state'], el.STATE_ACTIVE)

    def test_stop_cancels_the_pending_piece(self):
        brief, row = self.owner_line()
        self.run_scheduling(row, self.piece_args())
        self.service.preparations_service.stop_line(brief['line_id'])
        [piece] = self.pieces(brief['line_id'])
        self.assertEqual(piece['state'], prep.STATE_CANCELLED)

    def test_forget_cancels_the_pending_piece_and_undo_leaves_it_cancelled(self):
        brief, row = self.owner_line()
        self.run_scheduling(row, self.piece_args())
        [pending] = self.pieces(brief['line_id'])
        forget = OwnerForget(self.store)
        receipt = forget.forget('local-owner', 'line:' + brief['line_id'])
        piece = self.service.preparations.get(pending['id'])
        self.assertEqual(piece['state'], prep.STATE_CANCELLED)
        forget.undo('local-owner', receipt['receipt'])
        self.assertEqual(self.service.preparations.get(piece['id'])['state'], prep.STATE_CANCELLED)
        # A piece cancelled with its line is not a goal the owner declined.
        self.assertNotIn(HANDOFF, self.service.preparations.declined_goals(self.now))


class OutsideContentTests(_PieceCase):
    def test_a_piece_of_a_run_that_read_outside_content_starts_marked(self):
        brief, row = self.owner_line()
        _seen, run = self.run_scheduling(row, self.piece_args())
        self.store.mark_outside_content(run['id'], {'category': 'web-page', 'operation': 'browser_read'})
        [piece] = self.pieces(brief['line_id'])
        self.now = piece['due_at'] + 1
        work_id = self.service.preparations.start(self.service.preparations.get(piece['id']), channel='web',
                                                  chat_id=None, now=self.now)
        parent = {item['category'] for item in self.store.outside_content_sources(run['id'])}
        self.assertIn('web-page', parent)
        sources = self.store.outside_content_sources(work_id)
        # Every mark of the parent run, recorded as carried by the piece from that run.
        self.assertEqual({item['category'] for item in sources}, parent)
        self.assertEqual({(item['operation'], item['source_work_id']) for item in sources},
                         {(prep.ORIGIN_LINE_PIECE, run['id'])})

    def test_a_run_shown_a_marked_outcome_of_its_line_is_marked(self):
        # A piece reads a page; the line's next run of the work row is shown that piece's outcome.
        brief, row = self.owner_line()
        self.run_scheduling(row, self.piece_args())
        [piece] = self.pieces(brief['line_id'])
        _seen, piece_run = self.run_scheduling(piece)
        self.store.mark_outside_content(piece_run['id'], {'category': 'web-page', 'operation': 'browser_read'})
        _seen, later = self.run_scheduling(self.service.preparations.get(row['id']))
        carried = [item for item in self.store.outside_content_sources(later['id'])
                   if item.get('source_work_id') == piece_run['id']]
        self.assertIn('web-page', {item['category'] for item in carried})

    def test_a_piece_run_is_not_told_its_title_is_what_the_owner_accepted(self):
        brief, row = self.owner_line()
        self.run_scheduling(row, self.piece_args(title='손절선 확인', offer='손절선 아래면 알려 드려요.'))
        [piece] = self.pieces(brief['line_id'])
        self.assertTrue(prep.offered(piece))
        self.now = piece['due_at'] + 1
        work_id = self.service.preparations.start(piece, channel='web', chat_id=None, now=self.now)
        text = '\n'.join(item['content'] for item in self.service.preparation_history(self.store.job(work_id)))
        self.assertNotIn(msg('preparations.accepted_offer_label'), text)
        self.assertIn(HANDOFF, text)

    def test_a_piece_of_an_unmarked_run_starts_unmarked(self):
        brief, row = self.owner_line()
        _seen, run = self.run_scheduling(row, self.piece_args())
        with self.store.db() as db:  # a parent run that read nothing from outside
            db.execute('DELETE FROM work_intake_sources WHERE job_id=?', (run['id'],))
        [piece] = self.pieces(brief['line_id'])
        self.now = piece['due_at'] + 1
        work_id = self.service.preparations.start(self.service.preparations.get(piece['id']), channel='web',
                                                  chat_id=None, now=self.now)
        self.assertEqual(self.store.outside_content_sources(work_id), [])




class NotificationHistoryTests(_PieceCase):
    def test_a_piece_starts_with_the_chains_last_notification(self):
        brief, _row = self.owner_line()
        slot = self.running_slot(brief['line_id'])
        parent = prep.preparation_of(slot)
        with self.store.db() as db:
            db.execute("UPDATE preparations SET last_decision='notified',last_decision_reason='changed',"
                       "last_notified_digest='d1',last_notified_text='손절선 위',last_notified_at=? WHERE id=?",
                       (self.now, parent))
        piece = self.direct_piece(brief['line_id'], slot)
        self.now = piece['due_at'] + 1
        self.service.preparations.start(self.service.preparations.get(piece['id']), channel='web', chat_id=None, now=self.now)
        started = self.service.preparations.get(piece['id'])
        self.assertEqual((started['last_notified_digest'], started['last_notified_text'], started['last_decision']),
                         ('d1', '손절선 위', 'notified'))


class PaymentTests(Mode, _PieceCase):
    def test_a_piece_run_never_pays_with_payment_autonomy_on(self):
        self.switch(True)
        brief, row = self.owner_line()
        self.run_scheduling(row, self.piece_args())
        [piece] = self.pieces(brief['line_id'])
        self.now = piece['due_at'] + 1
        job = self.store.job(self.service.preparations.start(piece, channel='web', chat_id=None, now=self.now))
        binding = bs.step_binding(job['id'], 'api_request', 'https://pay.test/', 'pay 10', argument='', state='s')
        self.assertTrue(self.service.self_set_run(job))
        self.assertFalse(self.service.owner_accepted_run(job))
        self.assertIsNone(self.service._authorize_payment_autonomy(job, None, 'pay'))
        self.assertIsNone(self.service._authorize_payment_autonomy(job, binding, 'pay 10', dispatch=True))
        self.assertEqual(self.receipts(job['id']), [])


if __name__ == '__main__':
    import unittest
    unittest.main()
