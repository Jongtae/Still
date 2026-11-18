"""#968: an owner steer makes a scheduled run owner-present for its own work; a line run revises its own plan.

Evidence class: model-free unit tests with the real QuickStore, ``AgentService``, preparations,
entrusted lines and settings tools, a FixtureDecisionEngine and a fake clock.  The tool handlers are
called directly, as a worker's call reaches them; no live model or messenger is contacted.
"""
import json

from personal_agent import entrusted_lines as el
from personal_agent import preparations as prep
from personal_agent.agent_runtime import ToolError
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from test_owner_settings_conversation import _Case

GOAL = 'Check the watched list; report a change above 3 percent.'
NEW_GOAL = 'Check the watched list; report a change above 5 percent.'
STEER = 'From now on report only changes above 5 percent.'


class _RunCase(_Case):
    def setUp(self):
        super().setUp()
        self.clock[0] = 1800000000.0
        self.preps = self.service.preparations
        self.preps.clock = lambda: self.clock[0]
        self.service.lines.clock = lambda: self.clock[0]
        self.asked, self.yes = [], True

        def judge(context, proposition):
            if context.purpose in ('explicit-line-change', 'explicit-preparation-request'):
                self.asked.append((context.purpose, dict(context.facts)))
                return BinaryDecision(OUTCOME_DECIDED, self.yes, fixture_confidence())
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(judge=judge))

    def line(self, open_matters='first check pending'):
        return self.service.lines.open('owner-opened', task='Watch the list each morning', report_promise='a short note each run',
                                       rules=['Never more than one note a run'], open_matters=open_matters)

    def running(self, *, line=None, accepted_by=prep.ACCEPTED_OWNER_REQUEST, origin=None, goal=GOAL):
        row = self.preps.create(kind='prepare', goal=goal, due_at=self.clock[0], timezone='UTC', recurrence='daily',
                                channel='web', created_from=None, state=prep.STATE_SCHEDULED, accepted_by=accepted_by,
                                line_id=line, origin=origin)
        work = self.preps.start(row, channel='web', chat_id=None, now=self.clock[0])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
        return self.preps.get(row['id']), self.store.job(work)

    def steer(self, run, text=STEER, *, owner_words=True, deliver=True):
        """The owner's message reaches the running Work as a steer (#999), handed to its worker."""
        source = self.store.enqueue(text, f'owner-{text}', 'telegram:g', '1', owner_typed=True, owner_words=owner_words)
        self.assertTrue(self.store.add_steer(run['id'], source, text))
        if deliver:
            self.assertEqual(self.store.claim_steers(run['id']), [text])
        return source

    def mark(self, run):
        self.store.mark_outside_content(run['id'], {'category': 'native-search', 'operation': 'web_search'})

    def rewrite(self, row, run, goal=NEW_GOAL, **extra):
        handler = self.service.settings_tools(run)
        return handler('settings_change', {'category': 'preparations', 'setting': 'edit',
                                           'value': json.dumps({'id': row['id'], 'goal': goal, **extra}),
                                           'reason': 'The run judged the threshold wrong.'})

    def line_call(self, run, **args):
        return self.service.line_handler(run)(args)

    def refused_line(self, run, **args):
        with self.assertRaises(ToolError) as refused:
            self.line_call(run, **args)
        return refused.exception.code

    def settle(self, row, run):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',delivery='none' WHERE id=?", (run['id'],))
        self.preps.settle(row, self.clock[0])
        return self.preps.get(row['id'])


class LineRunGoalTests(_RunCase):
    def test_a_line_run_rewrites_its_own_goal_with_a_receipt_and_the_next_run_uses_it(self):
        brief = self.line()
        row, run = self.running(line=brief['line_id'])
        result = self.rewrite(row, run)
        self.assertTrue(result['applied'], result)
        self.assertEqual(self.preps.get(row['id'])['goal_text'], NEW_GOAL)
        [event] = [e for e in self.store.config('settings_audit', []) if e['terminal'] == 'own-run']
        self.assertEqual((event['target'], event['before'], event['after']),
                         ('preparations.goal', {'goal_text': GOAL}, {'goal_text': NEW_GOAL}))
        # The receipt shows where the brief changed, before and after.
        text = self.service.preparations_service.self_change_text(event)
        self.assertIn('3 percent', text)
        self.assertIn('5 percent', text)
        current = self.settle(self.preps.get(row['id']), run)
        self.clock[0] = current['due_at']
        following = self.preps.start(current, channel='web', chat_id=None, now=self.clock[0])
        self.assertEqual(self.store.job(following)['message'], NEW_GOAL)

    def test_the_owner_undoes_it_in_one_act_and_the_run_cannot_change_it_again(self):
        brief = self.line()
        row, run = self.running(line=brief['line_id'])
        self.assertTrue(self.rewrite(row, run)['applied'])
        draft = self.settings.propose('owner', 'http', 'preparations', 'restore', row['id'])
        self.assertEqual(self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])['state'], 'applied')
        self.assertEqual(self.preps.get(row['id'])['goal_text'], GOAL)
        self.assertFalse(self.rewrite(row, run, goal='Another threshold, 9 percent.')['applied'])
        self.assertEqual(self.preps.get(row['id'])['goal_text'], GOAL)

    def test_rows_that_do_not_qualify_keep_their_goal(self):
        brief = self.line()
        cases = {'no line': dict(line=None),
                 'look back': dict(line=brief['line_id'], accepted_by=prep.ACCEPTED_DREAM),
                 'daily plan': dict(line=brief['line_id'], accepted_by=prep.ACCEPTED_DAY_PLAN),
                 'periodic account': dict(line=brief['line_id'], origin=prep.ORIGIN_LINE_ACCOUNT)}
        for name, kwargs in cases.items():
            with self.subTest(name):
                row, run = self.running(goal=GOAL + ' ' + name, **kwargs)
                result = self.rewrite(row, run)
                self.assertFalse(result['applied'], result)
                self.assertEqual(self.preps.get(row['id'])['goal_text'], GOAL + ' ' + name)

    def test_a_marked_run_a_stopped_line_another_row_or_a_mixed_edit_is_refused(self):
        brief = self.line()
        row, run = self.running(line=brief['line_id'])
        other, _ = self.running(line=brief['line_id'], goal=GOAL + ' other')
        self.assertFalse(self.rewrite(other, run)['applied'])
        self.assertFalse(self.rewrite(row, run, max_runs=2)['applied'])
        self.mark(run)
        self.assertFalse(self.rewrite(row, run)['applied'])
        self.assertEqual(self.asked, [])
        stopped = self.line()
        row2, run2 = self.running(line=stopped['line_id'], goal=GOAL + ' stopped')
        self.service.lines.write(stopped['line_id'], 'owner', None, 'stopped', lambda b: {**b, 'state': el.STATE_STOPPED})
        self.assertFalse(self.rewrite(row2, run2)['applied'])
        for target, goal in ((row, GOAL), (other, GOAL + ' other'), (row2, GOAL + ' stopped')):
            self.assertEqual(self.preps.get(target['id'])['goal_text'], goal)


class LineRunOpenMattersTests(_RunCase):
    def test_a_line_run_revises_only_its_own_lines_open_matters_and_reports_before_and_after(self):
        brief = self.line()
        row, run = self.running(line=brief['line_id'])
        result = self.line_call(run, action='revise', line=brief['line_id'], expected_version='1',
                                open_matters='second check pending')
        self.assertEqual((result['version'], result['open_matters'], result['open_matters_before']),
                         (2, 'second check pending', 'first check pending'))
        self.assertIn('2', result['next_step'])
        for args in ({'rule': 'Report every hour'}, {'task': 'Something else'}, {'report_promise': 'never'},
                     {'drop_rule': '1'}, {'account_every_days': '1'}):
            with self.subTest(args=args):
                self.assertEqual(self.refused_line(run, action='revise', line=brief['line_id'], expected_version='2', **args),
                                 'unattended_run')
        for action in ('open', 'stop', 'undo', 'restart'):
            with self.subTest(action=action):
                self.assertEqual(self.refused_line(run, action=action, line=brief['line_id'], version='2',
                                                   task='t', report_promise='p'), 'unattended_run')
        other = self.line()
        self.assertEqual(self.refused_line(run, action='revise', line=other['line_id'], expected_version='1',
                                           open_matters='x'), 'unattended_run')
        self.assertEqual(self.service.lines.current(brief['line_id'])['version'], 2)
        self.assertEqual(self.service.lines.current(other['line_id'])['version'], 1)
        # The owner undoes the version in one act from a Work they typed.
        self.service.preparations_service.undo_line_version(brief['line_id'], 2, 'owner-work')
        self.assertEqual(self.service.lines.current(brief['line_id'])['open_matters'], 'first check pending')

    def test_a_marked_or_unqualified_run_cannot(self):
        brief = self.line()
        row, run = self.running(line=brief['line_id'])
        self.mark(run)
        self.assertEqual(self.refused_line(run, action='revise', line=brief['line_id'], expected_version='1',
                                           open_matters='from a page'), 'unattended_run')
        _row, dream = self.running(line=brief['line_id'], accepted_by=prep.ACCEPTED_DREAM, goal=GOAL + ' dream')
        self.assertEqual(self.refused_line(dream, action='revise', line=brief['line_id'], expected_version='1',
                                           open_matters='x'), 'unattended_run')
        self.assertEqual(self.service.lines.current(brief['line_id'])['version'], 1)


class SteeredRunTests(_RunCase):
    def test_owner_steer_words_need_a_message_handed_to_the_run_that_is_the_owners_own(self):
        row, run = self.running()
        self.assertEqual(self.service.owner_steer_words(run), [])
        # Judged to steer, but not yet handed to the worker: the run has not received it.
        source = self.steer(run, 'not handed over yet', deliver=False)
        self.assertEqual(self.service.owner_steer_words(run), [])
        # A web message not recorded as the owner's own words (#423, #744) never counts.
        web = self.store.enqueue('typed on the web', 'owner-web', 'web', None, owner_typed=True, owner_words=False)
        self.store.add_steer(run['id'], web, 'typed on the web')
        self.assertEqual(self.store.claim_steers(run['id']), ['not handed over yet', 'typed on the web'])
        self.assertEqual(self.service.owner_steer_words(run), [(source, 'not handed over yet')])
        # A Work the owner typed is not a scheduled run, and another run did not receive this steer.
        self.assertEqual(self.service.owner_steer_words(self.store.job(source)), [])
        _other, elsewhere = self.running(goal=GOAL + ' other')
        self.assertEqual(self.service.owner_steer_words(elsewhere), [])

    def test_a_steered_run_rewrites_its_own_goal_and_an_unsteered_one_does_not(self):
        row, run = self.running()
        self.assertFalse(self.rewrite(row, run)['applied'])
        self.steer(run)
        self.assertTrue(self.rewrite(row, run)['applied'])
        self.assertEqual(self.preps.get(row['id'])['goal_text'], NEW_GOAL)

    def test_after_outside_content_only_what_the_steer_asked_for(self):
        row, run = self.running()
        self.steer(run)
        self.mark(run)
        self.yes = False
        self.assertFalse(self.rewrite(row, run)['applied'])
        self.assertEqual(self.preps.get(row['id'])['goal_text'], GOAL)
        [(purpose, facts)] = self.asked
        self.assertEqual(purpose, 'explicit-line-change')
        self.assertEqual(facts['owner_message'], STEER)
        self.assertIn(NEW_GOAL, facts['proposed_change'])
        self.yes = True
        self.assertTrue(self.rewrite(row, run)['applied'])

    def test_a_steered_run_replaces_its_own_preparation_and_nothing_else(self):
        brief = self.line()
        row, run = self.running(line=brief['line_id'])
        other, _ = self.running(goal=GOAL + ' other')
        schedule = self.service.preparation_scheduler(run, '')
        args = {'kind': 'prepare', 'goal': NEW_GOAL, 'due': prep.iso(self.clock[0] + 3600, 'UTC'), 'recurrence': 'daily'}
        with self.assertRaises(ToolError) as refused:
            schedule({**args, 'replaces': row['id']})
        self.assertEqual(refused.exception.code, 'unattended_run')
        self.steer(run)
        for extra in ({}, {'replaces': other['id']}):
            with self.subTest(extra=extra), self.assertRaises(ToolError) as refused:
                schedule({**args, **extra})
            self.assertEqual(refused.exception.code, 'unattended_run')
        result = schedule({**args, 'replaces': row['id']})
        self.assertTrue(result['scheduled'], result)
        new = self.preps.get(result['preparation_id'])
        self.assertEqual((new['goal_text'], new['line_id'], new['accepted_by']),
                         (NEW_GOAL, brief['line_id'], prep.ACCEPTED_OWNER_REQUEST))
        self.assertEqual(self.preps.get(row['id'])['state'], prep.STATE_CANCELLED)
        self.assertEqual(self.preps.get(other['id'])['state'], prep.STATE_RUNNING)
        [(purpose, facts)] = self.asked
        self.assertEqual((purpose, facts['owner_message']), ('explicit-preparation-request', STEER))

    def test_a_marked_steered_run_replaces_only_as_asked(self):
        row, run = self.running()
        self.steer(run)
        self.mark(run)
        self.yes = False
        schedule = self.service.preparation_scheduler(run, '')
        with self.assertRaises(ToolError) as refused:
            schedule({'kind': 'prepare', 'goal': NEW_GOAL, 'due': prep.iso(self.clock[0] + 3600, 'UTC'), 'replaces': row['id']})
        self.assertEqual(refused.exception.code, 'unattended_run')
        self.assertEqual(self.preps.get(row['id'])['state'], prep.STATE_RUNNING)
        self.assertEqual(len([r for r in self.preps.rows() if r['state'] in prep.ACTIVE_STATES]), 1)

    def test_a_steered_run_revises_or_stops_its_own_line_with_the_steer_as_the_rules_source(self):
        brief = self.line()
        other = self.line()
        row, run = self.running(line=brief['line_id'])
        source = self.steer(run)
        result = self.line_call(run, action='revise', line=brief['line_id'], expected_version='1', rule='Only above 5 percent')
        self.assertEqual(result['version'], 2)
        current = self.service.lines.current(brief['line_id'])
        self.assertEqual(current['rules'][-1]['from'], source)
        for action in ('open', 'undo', 'restart'):
            with self.subTest(action=action):
                self.assertEqual(self.refused_line(run, action=action, line=brief['line_id'], version='2',
                                                   task='t', report_promise='p'), 'unattended_run')
        self.assertEqual(self.refused_line(run, action='revise', line=other['line_id'], expected_version='1',
                                           rule='x'), 'unattended_run')
        self.assertEqual(self.refused_line(run, action='stop', line=other['line_id']), 'unattended_run')
        self.assertEqual(self.service.lines.current(other['line_id'])['state'], el.STATE_ACTIVE)
        self.line_call(run, action='stop', line=brief['line_id'])
        self.assertEqual(self.service.lines.current(brief['line_id'])['state'], el.STATE_STOPPED)

    def test_a_marked_steered_run_changes_its_line_only_as_asked(self):
        brief = self.line()
        row, run = self.running(line=brief['line_id'])
        self.steer(run)
        self.mark(run)
        self.yes = False
        self.assertEqual(self.refused_line(run, action='revise', line=brief['line_id'], expected_version='1',
                                           open_matters='as the page said'), 'line_change_needs_owner')
        self.assertEqual(self.service.lines.current(brief['line_id'])['version'], 1)
        self.assertEqual(self.asked[-1][1]['owner_message'], STEER)
