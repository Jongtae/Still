"""#827: model-free multi-day responsibility traces, including explicit gaps.

Reuse review: N/A; fixture content and a driver over existing test helpers only.
No runtime component, dependency, policy case or external operation is added.
rows_active counts work rows; the automatic periodic-account row is separate.
Today-only scope is a scripted AI choice, not evidence of model judgment quality.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from personal_agent import preparations as prep
from personal_agent.quickstart_store import QuickStore
import test_entrusted_lines_798 as helpers
from test_entrusted_lines_798 import _LineCase, call, tool_results
from test_preparations import watch_engine


FIXTURE = Path(__file__).resolve().parents[1] / 'evals/continuity/line-traces-v1.json'
KINDS = frozenset(('open_line', 'standing_correction', 'today_only_correction',
                   'replace_preparation', 'edit_schedule', 'restart', 'run_due',
                   'finish_run', 'stop_line', 'cancel_row', 'observe'))
EXPECT = frozenset(('same_line_id', 'line_version', 'rule_present', 'rule_absent',
                    'rows_active', 'run_started', 'run_read_version', 'reply_sent',
                    'report_reason'))
# Deliberately restricted to the two known, locally specified issues. No network lookup.
GAPS = frozenset((813, 814))


def validate(document):
    if document.get('version') != 'line-traces/1':
        raise ValueError('unknown trace version')
    traces = document['traces']
    ids, splits = set(), set()
    for trace in traces:
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
            if 'gap' in step and (type(step['gap']) is not int or step['gap'] not in GAPS):
                raise ValueError('unknown gap issue')
            at = step['at_hours']
            if isinstance(at, bool) or not isinstance(at, (int, float)) or at < previous:
                raise ValueError('offsets must be nonnegative and ordered')
            previous = at
    if splits != {'dev', 'heldout'}:
        raise ValueError('both splits required')
    return traces


DOCUMENT = json.loads(FIXTURE.read_text())


class ValidationTests(unittest.TestCase):
    def test_fixture_and_rejected_vocabulary(self):
        traces = validate(DOCUMENT)
        self.assertEqual(len(traces), 5)
        self.assertEqual([t['split'] for t in traces].count('dev'), 3)
        self.assertEqual([t['split'] for t in traces].count('heldout'), 2)
        self.assertEqual({s['gap'] for t in traces for s in t['steps'] if 'gap' in s}, GAPS)
        for mutation in ('kind', 'expect', 'split', 'gap', 'one_split'):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(DOCUMENT)
                trace, step = bad['traces'][0], bad['traces'][0]['steps'][0]
                if mutation == 'kind':
                    step['do'] = 'invented'
                elif mutation == 'expect':
                    step['expect']['invented'] = True
                elif mutation == 'split':
                    del trace['split']
                elif mutation == 'gap':
                    step['gap'] = 999999
                else:
                    for trace in bad['traces']:
                        trace['split'] = 'dev'
                with self.assertRaises(ValueError):
                    validate(bad)


class LineTraceTests(_LineCase):
    def current_row(self):
        return self.service.preparations.get(self.row_id)

    def open_trace_line(self, args):
        # open_line hard-codes its task/promise constants. Scope overrides to this call;
        # keep its real scripted tool flow, and vary the preparation goal via extra.
        extra = dict(args.get('extra', {}), goal=args['task'])
        if 'window_hours' in args:
            extra.update(every_minutes='10', until=self.due_iso(3600 + args['window_hours'] * 3600),
                         report_every_minutes='20')
            self.judge = watch_engine([False] * 12, preparation=True)
            self.service.use_decision_engine(self.judge)
        with patch.object(helpers, 'TASK', args['task']), patch.object(helpers, 'PROMISE', args['promise']):
            brief, _ = self.open_line(delivery=args.get('delivery', 'keep'), extra=extra)
        self.line_id = brief['line_id']
        [row] = self.work_rows(self.line_id)
        self.row_id = row['id']

    def standing_correction(self, args):
        brief = self.service.lines.current(self.line_id)
        result = self.line_call(args['owner_text'], action='revise', line=self.line_id,
                                expected_version=str(brief['version']), rule=args['rule'])
        self.assertEqual(result['version'], brief['version'] + 1)

    def today_only_correction(self, args):
        self.script = [{'content': args['rule']}]
        self.receive(args['owner_text'])  # AI deliberately makes no standing-brief write.

    def replace_preparation(self, args):
        request = dict(kind='prepare', goal=args['goal'], due=self.due_iso(args.get('due_in_hours', 1) * 3600),
                       recurrence='daily', delivery='keep', replaces=self.row_id)
        if 'window_hours' in args:
            # The cap probe is a single long window, so it must not trip the
            # unrelated same-local-day restriction on recurring windows.
            request.pop('recurrence')
            request.update(every_minutes='5', until=self.due_iso((1 + args['window_hours']) * 3600),
                           delivery='when_needed')
        self.script = [{'content': None, 'tool_calls': [call('replace', 'schedule_preparation', **request)]},
                       {'content': 'Updated the preparation.'}]
        self.receive(args['goal'])
        result = tool_results(self.model_calls[-1], 'schedule_preparation')[-1]
        if 'refusal_code' in args:
            self.assertEqual(result['code'], args['refusal_code'])
        else:
            self.assertTrue(result['scheduled'], result)
            old = self.current_row()
            self.assertEqual(old['state'], prep.STATE_CANCELLED)
            self.row_id = result['preparation_id']

    def restart(self, args):
        self.store = QuickStore(self.root)
        self.service = self.make_service()

    def run_due(self, args):
        if not args.get('complete', True) or self.service.lines.current(self.line_id)['state'] == 'stopped':
            self.tick()
            if args.get('complete', True):
                self.script = [{'content': 'This reply must be withheld.'}] * 2
                self.service.run_one()
                self.deliver_all()
            return
        bodies = self.run_line(self.current_row(), [{'content': args.get('answer', 'No changes.')}])
        # run_line delivers ordinary replies before settling; watch notifications
        # are queued by that settlement and need the following delivery pass.
        self.deliver_all()
        self.capture_input(bodies)

    def capture_input(self, bodies):
        users = [m for m in bodies[0]['messages'] if m.get('role') == 'user']
        self.run_input = users[-1]['content']

    def finish_run(self, args):
        start = len(self.model_calls)
        self.script = [{'content': args.get('answer', 'No changes.')}]*2
        self.assertTrue(self.service.run_one())
        self.deliver_all()
        self.assertTrue(self.tick())
        self.deliver_all()
        self.capture_input(self.model_calls[start:])

    def stop_trace_line(self, args):
        self.service.preparations_service.stop_line(self.line_id)
        rows = self.service.preparations_service.line_rows(self.line_id)
        self.assertTrue(rows)
        self.assertTrue(all(row['state'] == prep.STATE_CANCELLED for row in rows))

    def execute(self, step):
        args = step['args']
        handlers = {
            'open_line': self.open_trace_line, 'standing_correction': self.standing_correction,
            'today_only_correction': self.today_only_correction,
            'replace_preparation': self.replace_preparation, 'restart': self.restart,
            'edit_schedule': lambda a: self.service.preparations.edit(
                self.row_id, due=self.due_iso(a['due_in_hours'] * 3600)),
            'run_due': self.run_due, 'finish_run': self.finish_run,
            'stop_line': self.stop_trace_line,
            'cancel_row': lambda a: self.service.preparations.cancel(self.row_id),
            'observe': lambda a: None,
        }
        before = {r['id'] for row in self.work_rows(self.line_id) for r in self.runs(row['id'])} if hasattr(self, 'line_id') else set()
        handlers[step['do']](args)
        rows = self.work_rows(self.line_id)
        runs = [r for row in rows for r in self.runs(row['id'])]
        brief = self.service.lines.current(self.line_id)
        run_id = self.current_row().get('last_run_job_id')
        read = self.service.preparations_service.line_version_read(run_id) if run_id else None
        observed = {
            'same_line_id': brief['line_id'] == self.line_id and all(row['line_id'] == self.line_id for row in rows)
                            and len(self.service.lines.lines()) == 1 and (not read or read[0] == self.line_id),
            'line_version': brief['version'],
            'rows_active': sum(row['state'] in prep.ACTIVE_STATES for row in rows),
            'run_started': bool({r['id'] for r in runs} - before),
            'run_read_version': read[1] if read else None,
            'reply_sent': bool(self.sends()),
            'report_reason': self.current_row().get('last_decision_reason'),
        }
        for key, expected in step['expect'].items():
            if key in ('rule_present', 'rule_absent'):
                self.assertEqual(expected in self.run_input, key == 'rule_present')
            else:
                self.assertEqual(observed[key], expected, key)


def trace_test(trace):
    def test(self):
        for number, step in enumerate(trace['steps']):
            with self.subTest(trace=trace['id'], split=trace['split'], step=number, kind=step['do']):
                if 'gap' in step:
                    self.skipTest(f"gap #{step['gap']}: {step['args']['gap_reason']}")
                self.now = self.START + step['at_hours'] * 3600
                # Flush owner acknowledgments before measuring this step's deliveries.
                self.deliver_all()
                self.telegram.clear()
                self.execute(step)
    return test


for _trace in validate(DOCUMENT):
    setattr(LineTraceTests, 'test_' + _trace['id'], trace_test(_trace))
