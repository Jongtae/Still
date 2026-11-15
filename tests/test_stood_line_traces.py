"""#799 L1: model-free multi-day traces of a line the nightly look back stands.

Evidence class: fixture. The real store, preparations service, delivery and run path run over a
temporary store with a fake owner clock, a scripted judgment and an injected Telegram transport.
Reuse review: N/A; fixture content and a driver over existing test helpers only.
Every ``expect`` key is asserted; a step that omits one asserts nothing about it.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from personal_agent import entrusted_lines as el
from personal_agent import preparations as prep
from personal_agent import browser_session as bs
from personal_agent.agent_runtime import ToolError
from personal_agent.quickstart_service import UNATTENDED_APPROVAL_TEXT
from test_preparations import _Case
import test_lines_from_conversation_799 as base

FIXTURE = Path(__file__).resolve().parents[1] / 'evals/continuity/stood-line-traces-v1.json'
KINDS = frozenset(('owner_turn', 'look_back', 'announce', 'run_due', 'owner_rule', 'owner_stop',
                   'owner_restart', 'enable_payment_autonomy', 'attempt_payment', 'observe', 'report', 'flush', 'restart'))
EXPECT = frozenset((
    'line_count', 'line_state', 'line_version', 'same_line_id', 'announced', 'notice_count', 'rows_active',
    'rows_total', 'rows_all_cancelled', 'accepted_by_dream', 'run_started', 'run_read_version',
    'rule_present', 'rule_absent', 'brief_rule_present', 'inferred_rule_present', 'outcome_stood',
    'outcome_amended', 'refused_reason', 'provenance_days', 'owner_written', 'self_set_run',
    'payment_authorized', 'approval_refused', 'approval_raised', 'authority_unchanged',
    'bundles_today', 'parts', 'parts_once'))


def validate(document):
    if document.get('version') != 'stood-line-traces/1':
        raise ValueError('unknown trace version')
    ids, splits = set(), set()
    for trace in document['traces']:
        if trace.get('split') not in ('dev', 'heldout'):
            raise ValueError('missing or unknown split')
        splits.add(trace['split'])
        if trace['id'] in ids:
            raise ValueError('duplicate trace id')
        ids.add(trace['id'])
        previous = -1
        for step in trace['steps']:
            if step['do'] not in KINDS:
                raise ValueError('unknown step kind')
            if set(step['expect']) - EXPECT:
                raise ValueError('unknown expect key')
            at = step['at_hours']
            if isinstance(at, bool) or not isinstance(at, (int, float)) or at < previous:
                raise ValueError('offsets must be nonnegative and ordered')
            previous = at
    if splits != {'dev', 'heldout'}:
        raise ValueError('both splits required')
    return document['traces']


DOCUMENT = json.loads(FIXTURE.read_text())


class ValidationTests(unittest.TestCase):
    def test_fixture_and_rejected_vocabulary(self):
        traces = validate(DOCUMENT)
        self.assertEqual(len(traces), 7)
        self.assertEqual([t['split'] for t in traces].count('dev'), 3)
        self.assertEqual([t['split'] for t in traces].count('heldout'), 4)
        for mutation in ('kind', 'expect', 'split', 'order', 'one_split', 'version'):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(DOCUMENT)
                trace, step = bad['traces'][0], bad['traces'][0]['steps'][0]
                if mutation == 'kind':
                    step['do'] = 'invented'
                elif mutation == 'expect':
                    step['expect']['invented'] = True
                elif mutation == 'split':
                    del trace['split']
                elif mutation == 'order':
                    trace['steps'][1]['at_hours'] = -1
                elif mutation == 'version':
                    bad['version'] = 'x'
                else:
                    for trace in bad['traces']:
                        trace['split'] = 'dev'
                with self.assertRaises(ValueError):
                    validate(bad)


class StoodLineTraceTests(base.Lines):
    START = _Case.START

    def setUp(self):
        _Case.setUp(self)
        self.helper = self.service.preparations_service
        from personal_agent.current_context import TIMEZONE_CONFIG_KEY
        self.store.put(TIMEZONE_CONFIG_KEY, 'Asia/Seoul')  # owner-local days, not the test host's zone
        self.service.owner_model.clock = lambda: self.now
        self.service.owner_model.set_controls({'enabled': True, 'dream': True})
        self.counter = 0
        self.a = self.b = 'unused'  # item() defaults; every trace names its own sources
        self.turns, self.line_id, self.authority0 = {}, None, None
        self.run_input, self.last_outcome, self.last_approval = '', None, None

    # --- observation ---------------------------------------------------------------------------

    def brief(self):
        return self.service.lines.current(self.line_id) if self.line_id else None

    def notices(self):
        if not self.line_id:
            with self.store.db() as db:
                return db.execute('SELECT count(*) FROM telegram_notifications WHERE kind LIKE ?',
                                  (el.NOTICE_OPENED + '%',)).fetchone()[0]
        with self.store.db() as db:
            return db.execute('SELECT count(*) FROM telegram_notifications WHERE kind LIKE ?',
                              (el.NOTICE_OPENED + self.line_id + ':%',)).fetchone()[0]

    def all_rows(self):
        return self.helper.line_rows(self.line_id) if self.line_id else []

    def runs_of(self, rows):
        return {r['id'] for row in rows for r in self.runs(row['id'])}

    def last_run(self):
        rows = self.work_rows(self.brief()) if self.line_id else []
        ids = [row['last_run_job_id'] for row in rows if row.get('last_run_job_id')]
        return ids[0] if ids else None

    def observed(self, before_runs):
        brief = self.brief()
        rows = self.all_rows()
        work = [row for row in rows if row['origin'] != prep.ORIGIN_LINE_ACCOUNT]
        run_id = self.last_run()
        read = self.helper.line_version_read(run_id) if run_id else None
        runs_now = self.runs_of(rows)
        lines = self.service.lines.lines()
        from personal_agent.current_context import owner_today
        with self.store.db() as db:
            bundles=db.execute("SELECT created FROM jobs WHERE model='line-bundle'").fetchall()
            parts=db.execute("SELECT job_id FROM tool_events WHERE tool='line_bundled'").fetchall()
        return {
            'bundles_today': sum(owner_today(self.store,b['created'])==owner_today(self.store,self.now) for b in bundles),
            'parts': len(parts), 'parts_once':len(parts)==len({p['job_id'] for p in parts}),
            'line_count': len(lines),
            'line_state': brief['state'] if brief else None,
            'line_version': brief['version'] if brief else None,
            'same_line_id': bool(brief) and len(lines) == 1 and all(r['line_id'] == self.line_id for r in rows)
                            and (read is None or read[0] == self.line_id),
            'announced': self.helper.line_announced(self.line_id) if self.line_id else False,
            'notice_count': self.notices(),
            'rows_active': sum(row['state'] in prep.ACTIVE_STATES for row in work),
            'rows_total': len(rows),
            'rows_all_cancelled': bool(rows) and all(row['state'] == prep.STATE_CANCELLED for row in rows),
            'accepted_by_dream': bool(rows) and all(row['accepted_by'] == prep.ACCEPTED_DREAM for row in rows),
            'run_started': bool(runs_now - before_runs),
            'run_read_version': read[1] if read else None,
            'brief_rules': [rule['text'] for rule in (brief or {}).get('rules', [])],
            'inferred_rules': [rule['text'] for rule in (brief or {}).get('rules', []) if rule.get('basis') == 'inferred'],
            'provenance_days': (self.service.lines.provenance(self.line_id) or {}).get('evidence', {}).get('days') if brief else None,
            'owner_written': self.service.lines.owner_written(self.line_id) if brief else None,
            'authority_unchanged': self.authority_diff() == [],
        }

    def assert_expectations(self, step, observed):
        for key, expected in step['expect'].items():
            if key in ('rule_present', 'rule_absent'):
                self.assertEqual(expected in self.run_input, key == 'rule_present', key)
            elif key == 'brief_rule_present':
                self.assertIn(expected, observed['brief_rules'])
            elif key == 'inferred_rule_present':
                self.assertIn(expected, observed['inferred_rules'])
            elif key == 'outcome_stood':
                self.assertEqual(expected, 'line_id' in (self.last_outcome or {}) and 'reason' not in self.last_outcome, self.last_outcome)
            elif key == 'outcome_amended':
                self.assertEqual(expected, 'version' in (self.last_outcome or {}) and 'reason' not in self.last_outcome, self.last_outcome)
            elif key == 'refused_reason':
                self.assertEqual((self.last_outcome or {}).get('reason'), expected)
            elif key in ('self_set_run', 'payment_authorized', 'approval_refused', 'approval_raised'):
                self.assertEqual(getattr(self, 'probe', {}).get(key), expected, key)
            else:
                self.assertEqual(observed[key], expected, key)

    def authority_diff(self):
        now = self.authority_state()
        # Run bookkeeping (focus, budget, provenance, orchestration) and the host-probed model-list
        # cache are written by any Work, so they are not authority; every other config row still counts.
        noise = ('codex_bundled_models', 'conversation_focus', 'orchestration_state', 'work_',
                 prep.SCHEDULED_USAGE_PREFIX, prep.SCHEDULED_SKIPPED_PREFIX)
        for state in (now, self.authority0):
            state['config_bytes'] = [row for row in state['config_bytes'] if not str(row[0]).startswith(noise)]
        return [key for key in now if now[key] != self.authority0.get(key)]

    # --- step handlers -----------------------------------------------------------------------

    def tag_ids(self, tags):
        return [self.turns[tag] for tag in tags]

    def do_owner_turn(self, args):
        self.turns[args['tag']] = self.owner(args['text'], self.now)

    def do_look_back(self, args):
        last = self.last_turn_job()['id']
        sources = self.tag_ids(args['sources']) if 'sources' in args else [last]
        action = args['action']
        if action == 'stand':
            item = self.item(task=args['task'], sources=sources)
        else:
            brief = self.brief()
            version = brief['version'] if args.get('expected_version') == 'current' else args['expected_version']
            item = self.item(action='amend', line=self.line_id, expected_version=version, first=[], task='',
                             report_promise='', sources=sources[:1] or [last],
                             **{k: args[k] for k in ('drop_rules', 'rules') if k in args})
        outcomes, _ = self.helper.apply_line_actions(last, [item], self.service.decision_judge)
        self.last_outcome = outcomes[0]
        if 'line_id' in self.last_outcome and self.line_id is None:
            self.line_id = self.last_outcome['line_id']
        elif 'line_id' in self.last_outcome:
            self.assertEqual(self.last_outcome['line_id'], self.line_id)

    def do_announce(self, args):
        self.helper.line_tick(self.now)
        if args.get('reoffer'):
            # Fixture reconciliation through delivery's API; the line tick never requeues a notice.
            with self.store.db() as db:
                rows=db.execute("SELECT id FROM telegram_notifications WHERE state='unknown' AND kind LIKE ?",
                                (el.NOTICE_OPENED+'%',)).fetchall()
                for row in rows:self.store.telegram.reoffer_unknown(row['id'],db)
        if args['delivery'] == 'sent':
            with patch('personal_agent.telegram_store.time.time',return_value=self.now):
                self.service.deliver_notification()
        else:
            with self.store.db() as db:
                db.execute("UPDATE telegram_notifications SET state=? WHERE kind LIKE ? AND state='queued'",
                           (args['delivery'], el.NOTICE_OPENED + '%'))
        self.helper.line_tick(self.now)

    def deliver_all(self):
        for _ in range(6):
            self.service.deliver_one()
        for _ in range(6):
            self.service.deliver_notification()

    def do_run_due(self, args):
        rows = self.work_rows(self.brief()) if self.line_id else []
        start = len(self.model_calls)
        self.script = [{'content': 'No changes.'}] * 4
        due = max([row['due_at'] for row in rows if row['state'] in prep.ACTIVE_STATES] or [0])
        self.now = max(self.now, due) + 1 if rows and due else self.now
        self.service.run_due_preparation()
        self.service.run_one()
        self.deliver_all()
        self.service.run_due_preparation()
        users = [m for body in self.model_calls[start:] for m in body['messages'] if m.get('role') == 'user']
        if users:
            self.run_input = users[-1]['content']

    def do_report(self, args):
        row=self.work_rows(self.brief())[0]
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=?,state=? WHERE id=?',(self.now,prep.STATE_SCHEDULED,row['id']))
        self.script=[{'content':args['text']}]*4
        self.service.run_due_preparation()
        self.assertTrue(self.service.run_one())
        self.service.run_due_preparation()

    def do_flush(self, args):
        self.helper.line_tick(self.now)
        self.deliver_all()

    def do_restart(self, args):
        self.service=self.make_service()
        self.helper=self.service.preparations_service

    def do_owner_rule(self, args):
        brief = self.brief()
        # The latest owner turn is the typed Work that carries the revision.
        result = self.helper.line_handler(self.last_turn_job())(
            {'action': 'revise', 'line': self.line_id, 'expected_version': str(brief['version']), 'rule': args['rule']})
        self.assertEqual(result['version'], brief['version'] + 1)

    def last_turn_job(self):
        last = max(self.turns.values(), key=lambda work: self.store.job(work)['created'])
        return self.store.job(last)

    def do_owner_stop(self, args):
        self.helper.line_handler(self.last_turn_job())({'action': 'stop', 'line': self.line_id})

    def do_owner_restart(self, args):
        self.helper.line_handler(self.last_turn_job())({'action': 'restart', 'line': self.line_id})

    def do_enable_payment_autonomy(self, args):
        from test_payment_autonomy_662 import Mode
        Mode.switch(self, True)
        self.authority0 = self.authority_state()

    def do_attempt_payment(self, args):
        row = self.work_rows(self.brief())[0]
        work = self.store.enqueue('run', prep.request_key(row['id'], row['due_at']))
        job = self.store.job(work)
        pay = bs.step_binding(work, 'api_request', 'https://example.test/', 'pay', argument='', state='s')
        charge = bs.step_binding(work, 'api_request', 'https://example.test/charge', 'POST /charge',
                                 argument='{}', state='api-payment-v2|slot@1|payment')
        refused = None
        try:
            self.service.browser_approvals_for(job).request(charge, 'POST /charge')
        except ToolError as exc:
            refused = exc.code if str(exc) == UNATTENDED_APPROVAL_TEXT else str(exc)
        self.probe = {
            'self_set_run': bool(self.service.self_set_run(job)),
            'payment_authorized': self.service._authorize_payment_autonomy(job, pay, 'pay') is not None,
            'approval_refused': refused,
            'approval_raised': self.service._browser_request(work) is not None
                               or self.store.notification_of(work, 'browser_approval_needed') is not None,
        }

    def execute(self, step):
        if step['do'] == 'run_due':
            self.probe = {}
        before = self.runs_of(self.all_rows())
        if self.authority0 is None and step['do'] != 'enable_payment_autonomy':
            self.authority0 = self.authority_state()
        handler = {'owner_turn': self.do_owner_turn, 'look_back': self.do_look_back, 'announce': self.do_announce,
                   'run_due': self.do_run_due, 'owner_rule': self.do_owner_rule, 'owner_stop': self.do_owner_stop,
                   'owner_restart': self.do_owner_restart, 'enable_payment_autonomy': self.do_enable_payment_autonomy,
                   'attempt_payment': self.do_attempt_payment, 'observe': lambda a: None,
                   'report':self.do_report, 'flush':self.do_flush, 'restart':self.do_restart}[step['do']]
        handler(step['args'])
        if step['do'] == 'run_due' and 'self_set_run' in step['expect']:
            run = self.last_run()
            self.probe = {'self_set_run': bool(run and self.service.self_set_run(self.store.job(run)))}
        self.assert_expectations(step, self.observed(before))


def trace_test(trace):
    def test(self):
        for number, step in enumerate(trace['steps']):
            with self.subTest(trace=trace['id'], split=trace['split'], step=number, kind=step['do']):
                self.now = self.START + step['at_hours'] * 3600
                self.last_outcome = None
                self.execute(step)
    return test


# The inherited #799 cases are run by their own module; only the traces belong here.
for _name in [n for n in dir(base.Lines) if n.startswith('test_')]:
    setattr(StoodLineTraceTests, _name, None)
for _trace in validate(DOCUMENT):
    setattr(StoodLineTraceTests, 'test_' + _trace['id'], trace_test(_trace))
