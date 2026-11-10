"""#780: the daily plan - the owner's AI prepares ahead without being asked.

Still sets up one daily ``prepare`` (origin ``day-plan``) while the look back is on.  Its run is the
only scheduled run that may call ``schedule_preparation``; what it sets up is marked
``ACCEPTED_DAY_PLAN``, is not announced, never pays and cannot schedule in turn.  The plan run is
shown the owner's recent conversation and Still's record of what earlier plan runs set up, including
in how many later Works each result was placed in the assistant's context.

Evidence class: model-free unit and service tests over a temporary store, a fake clock, a scripted
model transport and a recording Telegram transport.  No live model, calendar or Telegram is
contacted, and none is claimed.
"""
import json

from personal_agent import preparations as prep
from personal_agent.preparations_service import DAY_PLAN_GOAL

from test_preparations import _Case, call, finish


class _Plan(_Case):
    DAY_PLAN = True

    def plan(self):
        self.tick()
        [row] = [item for item in self.rows() if item.get('origin') == prep.ORIGIN_DAY_PLAN]
        return row

    def start_plan_run(self):
        """Advance to the plan's slot and claim its Work; the caller scripts the model, then ``run_one``."""
        row = self.plan()
        self.now = row['due_at'] + 60
        self.assertTrue(self.tick())
        return row

    def children(self):
        return [item for item in self.rows() if item.get('accepted_by') == prep.ACCEPTED_DAY_PLAN and not item.get('origin')]

    def schedule_call(self, ident='1', goal='오늘 점심 약속 근처 식당 후보를 준비해 줘', seconds=3600, **extra):
        return {'content': None, 'tool_calls': [call(ident, 'schedule_preparation', kind='prepare', goal=goal,
                                                     due=self.due_iso(seconds), delivery='keep', **extra)]}

    def tool_trace(self, work_id, tool='schedule_preparation'):
        return [event['trace'] for event in self.store.task_events(work_id) if event['tool'] == tool]


class SetUpTests(_Plan):
    def test_one_daily_plan_is_set_up_as_a_method_and_never_twice(self):
        row = self.plan()
        self.assertEqual((row['kind'], row['state'], row['recurrence'], row['accepted_by'], row['channel']),
                         ('prepare', 'scheduled', 'daily', prep.ACCEPTED_DAY_PLAN, prep.CHANNEL_WEB))
        self.assertEqual(row['goal_text'], DAY_PLAN_GOAL)
        local = prep.local_text(row['due_at'], row['timezone'])
        self.assertIn(f'{prep.DAY_PLAN_HOUR:02d}:{prep.DAY_PLAN_MINUTE:02d}', local)
        self.assertGreater(row['due_at'], self.now)
        self.tick()
        self.tick(self.make_service())  # a restart
        self.assertEqual(len([item for item in self.rows() if item.get('origin') == prep.ORIGIN_DAY_PLAN]), 1)
        self.assertEqual(self.sends(), [], 'setting the plan up tells the owner nothing')

    def test_a_cancelled_plan_is_not_set_up_again(self):
        row = self.plan()
        self.service.preparation_request({'operation': 'cancel', 'id': row['id']})
        self.now += 3 * 86400
        self.tick()
        [kept] = [item for item in self.rows() if item.get('origin') == prep.ORIGIN_DAY_PLAN]
        self.assertEqual((kept['id'], kept['state']), (row['id'], 'cancelled'))
        self.assertEqual(self.store.jobs(), [], 'a cancelled plan never runs')

    def test_a_deleted_plan_is_not_set_up_again(self):
        """Removing the row from the list, cancelled first or not, is the owner's no and is kept."""
        row = self.plan()
        self.service.preparation_request({'operation': 'cancel', 'id': row['id']})
        self.service.preparation_request({'operation': 'delete', 'id': row['id']})
        self.now += 3 * 86400
        self.tick()
        self.tick(self.make_service())
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.store.jobs(), [])

    def test_a_plan_deleted_without_a_tick_in_between_is_not_set_up_again(self):
        row = self.plan()
        with self.store.db() as db:
            db.execute('DELETE FROM preparations WHERE id=?', (row['id'],))
        self.tick()
        self.assertEqual(self.rows(), [])

    def test_turning_the_look_back_on_again_ends_the_opt_out(self):
        row = self.plan()
        self.service.preparation_request({'operation': 'cancel', 'id': row['id']})
        self.tick()
        self.service.owner_model_request({'operation': 'set', 'daily_calls': 5})
        self.tick()
        self.assertEqual([item['state'] for item in self.rows()], ['cancelled'], 'another control does not opt in')
        self.service.owner_model_request({'operation': 'set', 'dream': True})
        self.tick()
        self.assertEqual(sorted(item['state'] for item in self.rows()), ['cancelled', 'scheduled'])

    def test_an_opt_in_before_the_next_tick_is_not_overridden_by_it(self):
        """Cancelled or deleted, then the look back turned on again, with no tick in between."""
        for remove in (False, True):
            with self.subTest(deleted=remove):
                [row] = [item for item in self.rows() if item['state'] == 'scheduled'] or [self.plan()]
                self.service.preparation_request({'operation': 'cancel', 'id': row['id']})
                if remove:
                    self.service.preparation_request({'operation': 'delete', 'id': row['id']})
                self.service.owner_model_request({'operation': 'set', 'dream': True})
                self.tick()
                [active] = [item for item in self.rows() if item['state'] == 'scheduled']
                self.assertNotEqual(active['id'], row['id'])
                self.assertEqual(active['origin'], prep.ORIGIN_DAY_PLAN)

    def test_while_the_look_back_is_off_an_existing_plan_does_not_run(self):
        row = self.plan()
        self.service.owner_model.set_controls({'dream': False})
        self.now = row['due_at'] + 60
        self.tick()
        self.assertEqual(self.store.jobs(), [], 'the due slot passes unrun')
        moved = self.service.preparations.get(row['id'])
        self.assertEqual(moved['state'], 'scheduled')
        self.assertGreater(moved['due_at'], self.now)
        self.service.owner_model.set_controls({'dream': True})
        self.now = moved['due_at'] + 60
        self.assertTrue(self.tick())
        self.assertEqual(len(self.runs(row['id'])), 1, 'it runs again once the look back is on; the passed slot is not replayed')

    def test_no_plan_while_the_look_back_is_off(self):
        self.service.owner_model.set_controls({'dream': False})
        self.tick()
        self.assertEqual(self.rows(), [])

    def test_the_plan_names_no_topic(self):
        """C16: the brief is a method.  The words a topic-specific brief would need are absent."""
        lowered = DAY_PLAN_GOAL.lower()
        for word in ('stock', 'market', 'lunch', 'restaurant', 'weather', 'news', 'trade', 'shopping'):
            self.assertNotIn(word, lowered)


class SchedulingTests(_Plan):
    def test_the_plan_run_schedules_at_once_quietly(self):
        plan = self.start_plan_run()
        before = len(self.judge.asked)
        self.script = [self.schedule_call(), finish('f', '1', summary='식당 후보 준비를 잡았습니다.')]
        self.assertTrue(self.service.run_one())
        [child] = self.children()
        self.assertEqual((child['state'], child['accepted_by'], child['origin'], child['channel']),
                         ('scheduled', prep.ACCEPTED_DAY_PLAN, None, prep.CHANNEL_WEB))
        [run] = self.runs(plan['id'])
        self.assertEqual(child['created_from'], run['id'])
        [evidence] = [item['evidence'] for item in self.tool_trace(run['id']) if item.get('evidence')]
        self.assertEqual((evidence['scheduled'], evidence['requires_owner_acceptance'], evidence['accepted_by']),
                         (True, False, prep.ACCEPTED_DAY_PLAN))
        self.assertFalse(any(item[1].purpose == 'explicit-preparation-request' for item in self.judge.asked[before:]),
                         'who asked is not judged: nobody did')
        self.tick()
        self.assertEqual(self.sends(), [], 'what the plan sets up is not announced one by one')

    def test_what_the_plan_set_up_cannot_schedule_in_turn(self):
        self.start_plan_run()
        self.script = [self.schedule_call(seconds=600), finish('f', '1')]
        self.assertTrue(self.service.run_one())
        [child] = self.children()
        self.tick()  # settle the plan run
        self.now = child['due_at'] + 60
        self.assertTrue(self.tick())
        self.script = [self.schedule_call(goal='또 다른 준비', seconds=7200), {'content': '후보: 국수집'}]
        self.assertTrue(self.service.run_one())
        self.assertEqual([item['id'] for item in self.children()], [child['id']])
        [run] = self.runs(child['id'])
        refused = self.tool_trace(run['id'])
        self.assertTrue(refused and refused[-1].get('code') == 'unattended_run', refused)

    def test_a_plan_run_cannot_schedule_once_the_look_back_is_turned_off(self):
        plan = self.start_plan_run()
        self.service.owner_model.set_controls({'dream': False})
        self.script = [self.schedule_call(), finish('f', '1')]
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.children(), [])
        [run] = self.runs(plan['id'])
        self.assertEqual(self.tool_trace(run['id'])[-1].get('code'), 'unattended_run')

    def test_an_ordinary_scheduled_run_still_cannot_schedule(self):
        self.plan()
        row = self.scheduled(goal='내일도 점심 후보 준비해 둬', seconds=60)
        self.now += 120
        self.assertTrue(self.tick())
        self.script = [self.schedule_call(), {'content': '제안했습니다.'}]
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.children(), [])
        [run] = self.runs(row['id'])
        self.assertEqual(self.tool_trace(run['id'])[-1].get('code'), 'unattended_run')

    def test_the_plan_run_cannot_replace_what_exists(self):
        existing = self.scheduled(goal='주인이 잡아 둔 준비', seconds=86400 * 3)
        plan = self.start_plan_run()
        self.script = [self.schedule_call(replaces=existing['id']), finish('f', '1')]
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.children(), [])
        self.assertEqual(self.service.preparations.get(existing['id'])['state'], 'scheduled')
        [run] = self.runs(plan['id'])
        self.assertEqual(self.tool_trace(run['id'])[-1].get('code'), 'replace_not_allowed')

    def test_the_plan_and_what_it_sets_up_are_self_set_so_they_never_pay(self):
        plan = self.start_plan_run()
        self.script = [self.schedule_call(seconds=600), finish('f', '1')]
        self.assertTrue(self.service.run_one())
        [child] = self.children()
        [plan_run] = self.runs(plan['id'])
        self.assertTrue(self.service.self_set_run(plan_run))
        self.tick()
        self.now = child['due_at'] + 60
        self.assertTrue(self.tick())
        [child_run] = self.runs(child['id'])
        self.assertTrue(self.service.self_set_run(child_run))
        self.assertTrue(self.service.unattended_work(plan_run) and self.service.unattended_work(child_run))

    def test_a_cancelled_plan_is_not_a_declined_goal(self):
        row = self.plan()
        self.service.preparation_request({'operation': 'cancel', 'id': row['id']})
        self.assertNotIn(DAY_PLAN_GOAL, self.service.preparations.declined_goals(self.now + 1))


class ContextTests(_Plan):
    RECENT = '금요일 발표 자료 초안 RECENT-MARK'

    def test_the_plan_run_reads_the_recent_conversation_and_not_other_runs(self):
        self.script = [{'content': '초안 구조를 잡아 봤어요.'}]
        self.web_turn(self.RECENT, owner_typed=True)
        other = self.scheduled(goal='다른 준비 OTHER-RUN-MARK', seconds=60)
        self.now += 120
        self.assertTrue(self.tick())
        self.script = [{'content': '다른 준비 결과'}]
        self.assertTrue(self.service.run_one())
        self.tick()
        self.assertEqual(self.service.preparations.get(other['id'])['last_outcome'], 'delivered')

        self.start_plan_run()
        seen = []
        self.script = [lambda body: seen.append(json.loads(json.dumps(body['messages']))) or {'content': '오늘은 준비할 것이 없습니다.'}]
        self.assertTrue(self.service.run_one())
        sent = json.dumps(seen[0][1:], ensure_ascii=False)
        self.assertIn('RECENT-MARK', sent)
        self.assertNotIn('OTHER-RUN-MARK', sent)
        self.assertIn(DAY_PLAN_GOAL.split('\n', 1)[0], seen[0][-1]['content'])

    def test_an_ordinary_run_is_still_shown_only_its_own_origin(self):
        self.plan()
        self.script = [{'content': '초안 구조를 잡아 봤어요.'}]
        self.web_turn(self.RECENT, owner_typed=True)
        self.scheduled(goal='점심 후보 3곳 준비해 줘', seconds=60)
        self.now += 120
        self.assertTrue(self.tick())
        seen = []
        self.script = [lambda body: seen.append(json.loads(json.dumps(body['messages']))) or {'content': '후보: 국수집'}]
        self.assertTrue(self.service.run_one())
        self.assertNotIn('RECENT-MARK', json.dumps(seen[0][1:], ensure_ascii=False))


class HitRecordTests(_Plan):
    def prepared_child(self):
        self.start_plan_run()
        self.script = [self.schedule_call(seconds=600), finish('f', '1')]
        self.assertTrue(self.service.run_one())
        [child] = self.children()
        self.tick()
        self.now = child['due_at'] + 60
        self.assertTrue(self.tick())
        self.script = [{'content': '후보: 국수집, 덮밥집'}]
        self.assertTrue(self.service.run_one())
        self.tick()
        return child

    def offered_events(self):
        with self.store.db() as db:
            return [(row['job_id'], json.loads(row['detail'])) for row in
                    db.execute('SELECT job_id,detail FROM tool_events WHERE tool=? ORDER BY id', (prep.OFFERED_EVENT_TOOL,))]

    def test_placing_a_result_in_a_works_context_is_recorded_once(self):
        child = self.prepared_child()
        self.assertEqual(self.offered_events(), [])
        self.now += 600
        self.script = [{'content': '준비해 둔 후보입니다.'}]
        asked = self.web_turn('점심 뭐 먹지?')
        self.assertEqual(self.offered_events(), [(asked, {'preparation_ids': [child['id']]})])
        [listed] = self.service.preparations.set_by_day_plan(self.now - 86400)
        self.assertEqual((listed['id'], listed['offered_works']), (child['id'], 1))
        self.assertFalse(self.service.preparations.record_offered(asked, [child['id']]))
        self.assertEqual(len(self.offered_events()), 1)

    def test_nothing_is_recorded_when_nothing_was_placed(self):
        self.plan()
        self.script = [{'content': '안녕하세요.'}]
        self.web_turn('안녕')
        self.assertEqual(self.offered_events(), [])

    def test_the_next_plan_run_is_shown_what_earlier_runs_set_up_and_how_often_it_was_placed(self):
        child = self.prepared_child()
        self.now += 600
        self.script = [{'content': '준비해 둔 후보입니다.'}]
        self.web_turn('점심 뭐 먹지?')
        plan = [item for item in self.rows() if item.get('origin') == prep.ORIGIN_DAY_PLAN][0]
        self.now = self.service.preparations.get(plan['id'])['due_at'] + 60
        self.assertTrue(self.tick())
        seen = []
        self.script = [lambda body: seen.append(json.loads(json.dumps(body['messages']))) or {'content': '어제 준비는 쓰였습니다.'}]
        self.assertTrue(self.service.run_one())
        last = seen[0][-1]['content']
        self.assertIn('earlier_plans: ', last)
        [item] = json.loads(last.split('earlier_plans: ', 1)[1].split('\n', 1)[0])
        self.assertEqual((item['placed_in_later_requests'], item['last_outcome']), (1, 'delivered'))
        self.assertEqual(item['title'], prep.owner_title(child))

    def test_the_first_plan_run_has_no_record(self):
        self.start_plan_run()
        seen = []
        self.script = [lambda body: seen.append(json.loads(json.dumps(body['messages']))) or {'content': '없음'}]
        self.assertTrue(self.service.run_one())
        self.assertNotIn('earlier_plans: [', seen[0][-1]['content'])
