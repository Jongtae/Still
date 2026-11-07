"""#42: model-free caller, transport, budget and report evidence only."""
import copy
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import cli, judgments as j

s = j.load_still(ROOT / 'src')
d = s.decision
from personal_agent.providers import ModelAdapter, ProviderError
from test_decision_routes import oracle, parse_prompt, schema_answer


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def planned(expect=None, model='model-standard', **extra):
    expect = expect or {}
    return dict(worker='default-worker', model=model, effort='', brief={'notes': '', 'lens': ''}, reason='fixture',
                **{k: expect.get(k, False) for k in j.FLAGS}, **extra)


def answers(case):
    e, role = case['expect'], case['role']
    if role == 'orchestration_plan':
        return [planned(e)]
    if role == 'orchestration_escalation':
        return [planned(), {'answer': False, 'gap': 'PRIVATE GAP', 'note': 'PRIVATE NOTE', 'retry_warranted': True},
                planned(model='model-advanced')]
    if role == 'owner_act_answer':
        return [{**e, 'rest': '', 'parts': []}]
    if role in ('followup_relation', 'connector_effect', 'status_projection'):
        return [{'choice': d.NO_CANDIDATE if e.get('abstain') or e.get('outcome') == 'no' else e['value']}]
    return [{'answer': e['outcome'] == 'yes', 'gap': 'PRIVATE GAP', 'note': 'PRIVATE NOTE'}]


class Transport:
    def __init__(self, scripted=(), clock=None, durations=(), mode='oracle'):
        self.scripted, self.clock, self.durations = list(scripted), clock or Clock(), list(durations)
        self.mode, self.calls = mode, []

    def __call__(self, url, body, headers=None, timeout=60):
        self.calls.append(body)
        self.clock.now += self.durations.pop(0) if self.durations else 0.25
        if self.mode == 'unavailable':
            raise ProviderError('fixture offline')
        if self.mode == 'timeout':
            raise ProviderError('fixture timeout', status='timeout')
        schema = body['tools'][0]['function']['parameters']
        purpose, facts = parse_prompt(body['messages'][-1]['content'])
        if self.mode == 'malformed':
            result = {'unexpected': 'bad'}
        elif self.scripted:
            result = self.scripted.pop(0)
        elif self.mode == 'careless':
            props = schema['properties']
            result = (planned() if 'worker' in props else {'item': 'draft-one', 'answer': 'approve', 'rest': '', 'parts': []}
                      if 'item' in props else {'choice': props['choice']['enum'][0]} if 'choice' in props else
                      {'answer': True, 'gap': '', 'note': ''})
        else:
            result = schema_answer(schema, purpose, facts, oracle)
            if 'answer' in result:
                result['gap'] = ''
        if self.mode == 'malformed':
            result = {'unexpected': 'bad'}
        result = {**result, 'confidence': 0.99}
        return {'model': 'gpt-4o-mini', 'choices': [{'message': {'role': 'assistant', 'tool_calls': [
            {'id': 'fixture-call', 'type': 'function', 'function': {'name': 'decide', 'arguments': json.dumps(result)}}]}}]}


def engine(transport, audit, cap=None):
    inner = d.ModelDecisionEngine(ModelAdapter(transport=transport),
                                 lambda: ({'provider': 'openai', 'endpoint': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'}, 'fixture-key'),
                                 audit=audit.append, now=transport.clock)
    return d.RoutedDecisionEngine(lambda: inner, budget=lambda: cap)


def subset(cases, *ids):
    return {**cases, 'cases': [c for c in cases['cases'] if c['id'] in ids]}


class JudgmentTests(unittest.TestCase):
    def setUp(self):
        self.cases = j.load_cases()

    def measure(self, cases=None, mode='oracle', scripted=None, cap=None, durations=(), trials=1, keep_text=False):
        cases = cases or self.cases
        if scripted is None:
            scripted = [a for c in cases['cases'] for a in answers(c)] if mode == 'oracle' else []
        transport = Transport(scripted, durations=durations, mode=mode)
        audit = []
        routed = engine(transport, audit, cap)
        rows = j.run(cases, s.conversation_handoff.ConversationJudgments(routed), routed, audit=audit, cap=cap,
                     timer=transport.clock, trials=trials, keep_text=keep_text)
        return rows, transport, audit

    def test_cases_and_plan(self):
        raw = json.dumps(self.cases, ensure_ascii=False)
        self.assertNotIn('@', raw)
        self.assertNotRegex(raw, r'https?://|\d{7,}')
        self.assertEqual({c['lang'] for c in self.cases['cases']}, {'ko', 'en'})
        self.assertEqual({c['class'] for c in self.cases['cases']}, {'bounded', 'structured-plan'})
        categories = {c['category'] for c in self.cases['cases']}
        self.assertEqual(categories, set('follow-up correction topic-change short-reference ambiguous-reference abstention steer open-question withdrawal effect-class status-projection completion false-completion partial unknown-effect account-change owner-situation technical-work escalation owner-act'.split()))
        plan = j.plan(self.cases)
        self.assertEqual(plan['cases'], len(self.cases['cases']) + len(s.decision_qualification.CASES))
        self.assertEqual(plan['max_calls'], plan['cases'] + 4)
        self.assertEqual(plan['roles']['qualification-suite']['cases'], 11)
        for mutate in [lambda r: r.update(extra=True), lambda r: r['cases'][0].update(extra=True),
                       lambda r: r['cases'][0].update(role='invented'), lambda r: r['cases'][0].update(lang='fr'),
                       lambda r: r['cases'][0].update(**{'class': 'other'}),
                       lambda r: r['cases'][0]['input'].update(recorded_effects=[]),
                       lambda r: r['cases'][0]['expect'].update(extra=True),
                       lambda r: r['cases'].append(r['cases'][0]), lambda r: r.update(version=1),
                       lambda r: r['catalogue']['workers'][0].update(unknown='bad'),
                       lambda r: r['catalogue']['workers'][0].update(model_tiers={}),
                       lambda r: r['cases'][0]['expect'].update(abstain=True)]:
            with tempfile.TemporaryDirectory() as folder:
                bad = copy.deepcopy(self.cases)
                mutate(bad)
                path = Path(folder) / 'bad.json'
                path.write_text(json.dumps(bad))
                with self.assertRaises(j.CaseError):
                    j.load_cases(path)

    def test_oracle_scores_every_case_through_real_model_adapter(self):
        rows, transport, audit = self.measure()
        failures = [(r['id'], r['outcome'], r['error'], r['observed']) for r in rows if not r['semantic_correct'] or r['routing_correct'] is False]
        self.assertEqual(failures, [])
        self.assertEqual(len(transport.calls), j.plan(self.cases)['max_calls'])
        self.assertEqual(sum(len(r['calls']) for r in rows), len(transport.calls))
        self.assertTrue(all(r['schema_valid'] for r in rows))
        self.assertEqual(j.summarize(rows)['total']['semantic_accuracy'], 1)
        self.assertTrue(all(r['observed']['note_returned'] for r in rows if r['role'] == 'goal_reached' and r['observed']['outcome'] == 'no'))
        self.assertNotIn('PRIVATE', json.dumps(rows))
        self.assertTrue(audit)

    def test_careless_error_counts_are_hand_computed(self):
        selected = subset(self.cases, 'steer-unrelated-en', 'goal-failed', 'projection-unknown', 'effect-payment',
                          'owner-act-decline', 'followup-ambiguous-en', 'plan-password')
        rows, _, _ = self.measure(selected, mode='careless')
        selected_rows = rows[:7]
        self.assertEqual(j.summarize(selected_rows)['total']['errors'],
                         {'guessed': 1, 'false_positive': 1, 'unsafe_misroute': 3, 'false_completion': 2})
        # A confident no on a positive binary is a false negative, not abstention.
        rows, _, _ = self.measure(subset(self.cases, 'steer-correction-en'), scripted=[{'answer': False, 'gap': ''}])
        self.assertEqual(rows[0]['error'], 'false_negative')

    def test_malformed_unavailable_timeout_and_unconfidence(self):
        selected = subset(self.cases, 'plan-password', 'goal-failed', 'followup-ambiguous-en')
        rows, _, _ = self.measure(selected, mode='malformed')
        self.assertTrue(all(not r['schema_valid'] and not r['semantic_correct'] for r in rows))
        self.assertEqual(next(r for r in rows if r['role'] == 'orchestration_plan')['error'], 'invalid_plan')
        for mode in ('unavailable', 'timeout'):
            rows, _, _ = self.measure(selected, mode=mode)
            self.assertTrue(all(not r['semantic_correct'] for r in rows))
            self.assertTrue(all(r['error'] == 'engine_failure' for r in rows))
            if mode == 'timeout':
                self.assertTrue(all(r['timeouts'] == 1 for r in rows))
        fixture = d.FixtureDecisionEngine(judge=lambda *_: d.BinaryDecision('decided', None, d.fixture_confidence(0.1)))
        routed = d.RoutedDecisionEngine(lambda: fixture)
        rows = j.run(subset(self.cases, 'goal-served'), s.conversation_handoff.ConversationJudgments(routed), routed,
                     roles=['goal_reached'])
        self.assertEqual(rows[0]['error'], 'abstained')
        unavailable = d.RoutedDecisionEngine(lambda: d.UnavailableDecisionEngine())
        rows = j.run(selected, s.conversation_handoff.ConversationJudgments(unavailable), unavailable)
        self.assertFalse(any(r['semantic_correct'] for r in rows))

    def test_exception_is_never_pass(self):
        def fail(*_):
            raise RuntimeError('PRIVATE exception')
        routed = d.RoutedDecisionEngine(lambda: d.FixtureDecisionEngine(choose=fail))
        rows = j.run(subset(self.cases, 'followup-ambiguous-en'), s.conversation_handoff.ConversationJudgments(routed), routed,
                     roles=['followup_relation'])
        self.assertEqual(rows[0]['outcome'], 'error:RuntimeError')
        self.assertFalse(rows[0]['semantic_correct'])
        self.assertNotIn('PRIVATE', json.dumps(rows))

    def test_invalid_plan_and_swallowed_engine_exception_are_failures(self):
        invalid = planned()
        invalid['worker'] = 'undeclared-worker'
        rows, _, _ = self.measure(subset(self.cases, 'plan-plain-en'), scripted=[invalid])
        self.assertEqual(rows[0]['error'], 'invalid_plan')
        self.assertFalse(rows[0]['schema_valid'])
        self.assertEqual(rows[0]['observed']['fallback_code'], s.orchestrator.FALLBACK_INVALID)
        def fail(*_):
            raise RuntimeError('PRIVATE exception')
        routed = d.RoutedDecisionEngine(lambda: d.FixtureDecisionEngine(structured=fail))
        rows = j.run(subset(self.cases, 'plan-plain-en'), s.conversation_handoff.ConversationJudgments(routed), routed,
                     roles=['orchestration_plan'])
        self.assertEqual(rows[0]['outcome'], 'error:RuntimeError')
        self.assertEqual(rows[0]['error'], 'engine_failure')

    def test_startup_timing_and_numeric_usage_are_observed(self):
        selected = subset(self.cases, 'goal-served')
        transport = Transport(answers(selected['cases'][0]))
        audit = []
        routed = engine(transport, audit)
        inner = routed.resolve()
        def capture(record):
            audit.append({**record, 'cli_timing': {'first_event_s': None, 'spawn_s': 0.125}, 'usage': 17})
        inner.audit = capture
        routed.resolve = lambda: inner
        rows = j.run(selected, s.conversation_handoff.ConversationJudgments(routed), routed, audit=audit,
                     roles=['goal_reached'], timer=transport.clock)
        self.assertEqual(rows[0]['startup_seconds'], 0.125)
        summary = j.summarize(rows)['total']
        self.assertEqual(summary['startup_latency']['p50'], 0.125)
        self.assertEqual(summary['usage'], 17)
        self.assertEqual(summary['cost'], 'not reported')

    def test_latency_and_trials(self):
        selected = subset(self.cases, 'goal-served', 'goal-legitimate-question')
        rows, _, _ = self.measure(selected, durations=[1, 3])
        goals = [r for r in rows if r['role'] == 'goal_reached']
        self.assertEqual([r['decision_seconds'] for r in goals], [1, 3])
        self.assertEqual([r['end_to_end_seconds'] for r in goals], [1, 3])
        summary = j.summarize(goals)['total']
        self.assertEqual(summary['decision_latency'], {'p50': 2, 'p95': 3, 'samples': 2})
        self.assertEqual(summary['decision_latency'], summary['end_to_end_latency'])
        self.assertEqual(summary['usage'], 'not reported')
        rows, _, _ = self.measure(subset(self.cases, 'goal-served'), scripted=[{'answer': True, 'gap': ''}] * 2, trials=2)
        goals = [r for r in rows if r['role'] == 'goal_reached']
        self.assertEqual(len(goals), 2)
        self.assertEqual(sum(r['retries'] for r in goals), 1)

    def test_budget_through_shipped_admission(self):
        cap = j.CallCap(2)
        rows, transport, _ = self.measure(cap=cap)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual([r['outcome'] for r in rows[:4]], ['decided', 'decided', 'budget_refused', 'not_run'])
        self.assertEqual(rows[2]['error'], 'budget_refused')
        self.assertEqual(rows[2]['calls'], [])
        self.assertIsNone(rows[3]['semantic_correct'])
        self.assertEqual(j.summarize(rows)['total']['ran'], 3)

    def test_runtime_whitelist(self):
        status = dict(active=dict(transport='fixture', requested_model='fixture', available=True,
                                 api_key='SECRET', fingerprint='FINGERPRINT', binary='/private/BINARY',
                                 qualification=dict(model='fixture', score=1, key='SECRET')),
                      effective=dict(state='ready', reason='configured', token='SECRET'), suite_version='fixture-suite')
        service = SimpleNamespace(decision_routes=SimpleNamespace(status=lambda: status))
        runtime = j.effective_runtime(service, [{'failure': 'auth', 'observed_model': 'fixture'}])
        text = json.dumps(runtime)
        for forbidden in ('SECRET', 'FINGERPRINT', 'BINARY', 'api_key', 'fingerprint', 'binary'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(runtime['active']['requested_model'], 'fixture')
        self.assertEqual(runtime['unavailable_reasons'], {'auth': 1})

    def test_report_compare_and_permissions(self):
        rows, _, _ = self.measure(subset(self.cases, 'goal-served', 'goal-failed'))
        baseline = j.report(rows, {'git_head': 'fixture', 'src': ROOT / 'src'}, {'active': {'transport': 'fixture'}})
        self.assertTrue(baseline['prompt_digests'])
        self.assertEqual(baseline['evidence_class'], 'fixture')
        self.assertIn('fixture', j.markdown(baseline))
        self.assertNotIn('PRIVATE', json.dumps(baseline) + j.markdown(baseline))
        kept, _, _ = self.measure(subset(self.cases, 'goal-failed'), keep_text=True)
        self.assertIn('PRIVATE NOTE', json.dumps(kept))
        head = copy.deepcopy(baseline)
        baseline['rows'][0]['semantic_correct'] = False
        head['rows'][1]['semantic_correct'] = False
        baseline['rows'][2]['semantic_correct'] = False
        head['rows'][2]['semantic_correct'] = False
        head['runtime']['active']['transport'] = 'changed'
        head['prompt_digests']['new.md'] = 'digest'
        head['summary'] = j.summarize(head['rows'])
        delta = j.compare(baseline, head)
        self.assertFalse(delta['same_conditions'])
        self.assertEqual([c['change'] for c in delta['changes'][:2]], ['regressed', 'fixed'])
        self.assertIn('unchanged-fail', [c['change'] for c in delta['changes']])
        self.assertEqual(delta['changed_prompt_digests'], ['new.md'])
        self.assertIn('same_conditions', j.compare_markdown(delta))
        head['case_set_version'] = 'different'
        with self.assertRaises(j.CaseError):
            j.compare(baseline, head)
        with tempfile.TemporaryDirectory() as folder:
            paths = j.write_report(baseline, folder, 'fixture')
            for path in paths:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cli_plan_missing_seed_and_fake_live_run(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(['judgments', 'plan']), 0)
        self.assertEqual(json.loads(out.getvalue()), j.plan(self.cases))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['judgments', 'run'])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            seed = root / 'seed'
            (seed / 'private').mkdir(parents=True)
            (seed / 'private/quickstart.db').write_bytes(b'synthetic seed')
            created = []

            def factory(data):
                self.assertNotEqual(data, seed)
                self.assertEqual((data / 'private/quickstart.db').read_bytes(), b'synthetic seed')
                service = SimpleNamespace(record_decision=lambda record: None, cap=None)
                @contextmanager
                def scope(work, cap):
                    self.assertIsNone(work)
                    service.cap = cap
                    try:
                        yield
                    finally:
                        service.cap = None
                service.work_decision_scope = scope
                transport = Transport([a for c in self.cases['cases'] if c['role'] == 'goal_reached' for a in answers(c)])
                inner = d.ModelDecisionEngine(ModelAdapter(transport=transport),
                                             lambda: ({'provider': 'openai', 'endpoint': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'}, 'fixture-key'),
                                             audit=lambda record: service.record_decision(record), now=transport.clock)
                service.decision_engine = d.RoutedDecisionEngine(lambda: inner, budget=lambda: service.cap)
                service.decision_judge = s.conversation_handoff.ConversationJudgments(service.decision_engine)
                service.decision_routes = SimpleNamespace(status=lambda: {'active': {'transport': 'fixture'}})
                created.append((data, transport))
                return service
            with patch.dict(os.environ, {'AGENTOS_EVAL_HOME': str(root / 'home')}), patch.object(j, 'service_factory', factory), redirect_stdout(io.StringIO()) as out:
                cli.main(['judgments', 'run', '--seed', str(seed), '--roles', 'goal_reached', '--out', str(root / 'reports')])
            saved = json.loads(next((root / 'reports').glob('*.json')).read_text())
            self.assertEqual(saved['evidence_class'], 'fixture')
            self.assertTrue(all(r['semantic_correct'] for r in saved['rows']))
            self.assertEqual(saved['summary']['total']['calls'], 16)
            self.assertFalse(created[0][0].exists())
            self.assertTrue(seed.exists())
            self.assertIn('report:', out.getvalue())


if __name__ == '__main__':
    unittest.main()
