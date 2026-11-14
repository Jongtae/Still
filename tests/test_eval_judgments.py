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
from test_decision_routes import ServiceFixture, careless, oracle, parse_prompt, schema_answer


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def planned(expect=None, model='model-standard', **extra):
    expect = expect or {}
    return dict(worker='default-worker', model=model, effort='', brief={'notes': '', 'lens': ''}, reason='fixture', reply='',
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
                         {'wrong_choice': 1, 'false_positive': 1, 'unsafe_misroute': 3, 'false_completion': 2})
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

    def test_account_outcome_can_need_preparation_without_relaxing_routing(self):
        selected = subset(self.cases, 'plan-password', 'plan-forwarding', 'plan-subscription')
        for case in selected['cases']:
            with self.subTest(case=case['id']):
                for technical in (False, True):
                    reply = planned(case['expect'], model='model-advanced')
                    reply['technical_work'] = technical
                    rows, _, _ = self.measure(subset(selected, case['id']), scripted=[reply])
                    self.assertTrue(rows[0]['semantic_correct'])
                reply = planned(case['expect'], model='model-advanced')
                reply['account_change'] = False
                rows, _, _ = self.measure(subset(selected, case['id']), scripted=[reply])
                self.assertFalse(rows[0]['semantic_correct'])
                reply = planned(case['expect'])
                rows, _, _ = self.measure(subset(selected, case['id']), scripted=[reply])
                self.assertTrue(rows[0]['routing_correct'])
                self.assertEqual(rows[0]['observed']['tier'], 'higher')
                # The existing planner promotes a declared account change.
                # Omitting that flag must still fail even if it evades promotion.
                reply['account_change'] = False
                rows, _, _ = self.measure(subset(selected, case['id']), scripted=[reply])
                self.assertFalse(rows[0]['routing_correct'])
                self.assertEqual(rows[0]['error'], 'unsafe_misroute')

    def test_unavailable_route_is_not_run_and_accuracy_is_unmeasured(self):
        routed = d.RoutedDecisionEngine(lambda: d.UnavailableDecisionEngine())
        rows = j.run(self.cases, s.conversation_handoff.ConversationJudgments(routed), routed)
        self.assertTrue(all(r['outcome'] == 'not_run' for r in rows))
        self.assertTrue(all(r['semantic_correct'] is None and r['routing_correct'] is None for r in rows))
        result = j.report(rows, {}, {'active': {'available': False}})
        self.assertEqual(result['summary']['total']['calls'], 0)
        self.assertEqual(result['summary']['total']['answered'], 0)
        text = j.markdown(result)
        self.assertIn('Route unavailable', text)
        self.assertIn('Model calls: 0', text)
        self.assertIn('No answered cases', text)
        self.assertNotIn('0.0%', text)

    def test_failures_and_budget_refusals_do_not_dilute_answer_accuracy(self):
        cap = j.CallCap(1)
        rows, _, _ = self.measure(cap=cap)
        self.assertEqual(j.summarize(rows)['total']['semantic_accuracy'], 1)
        self.assertEqual(j.summarize(rows)['total']['answered'], 1)
        failed, _, _ = self.measure(mode='timeout')
        self.assertIsNone(j.summarize(failed)['total']['semantic_accuracy'])
        self.assertIsNone(j.summarize(failed)['total']['routing_accuracy'])
        self.assertEqual(j.compare(j.report(rows, {}, {}), j.report(failed, {}, {}))['changes'], [])

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

    def test_comparison_checks_case_content_and_effort(self):
        cases = subset(self.cases, 'goal-served')
        rows, _, _ = self.measure(cases)
        runtime = {'active': {'transport': 'fixture', 'effort': 'low'}}
        baseline = j.report(rows, {}, runtime, cases=cases)
        self.assertTrue(j.compare(baseline, copy.deepcopy(baseline))['same_conditions'])
        for field in ('input', 'expect', 'catalogue'):
            changed = copy.deepcopy(cases)
            if field == 'input':
                changed['cases'][0]['input']['request'] = 'CASE-PRIVATE-824'
            elif field == 'expect':
                changed['cases'][0]['expect']['outcome'] = 'no'
            else:
                changed['catalogue']['workers'][0]['name'] = 'CASE-PRIVATE-824'
            with self.subTest(field=field):
                head = j.report(rows, {}, runtime, cases=changed)
                comparison = j.compare(baseline, head)
                self.assertFalse(comparison['same_conditions'])
                self.assertIn('case_set_digest differs', comparison['reasons'])
                self.assertNotIn('CASE-PRIVATE-824', json.dumps(head))
        head = copy.deepcopy(baseline)
        head['runtime']['active']['effort'] = 'high'
        comparison = j.compare(baseline, head)
        self.assertFalse(comparison['same_conditions'])
        self.assertIn('runtime.effort differs', comparison['reasons'])
        self.assertIn('changes', comparison, 'configuration comparisons still expose result deltas')

    def test_case_digest_ignores_dictionary_key_order(self):
        cases = subset(self.cases, 'goal-served')
        # Reorder every JSON object's keys while preserving array order.
        reordered = json.loads(json.dumps(cases), object_pairs_hook=lambda pairs: dict(reversed(pairs)))
        rows, _, _ = self.measure(cases)
        runtime = {'active': {'transport': 'fixture', 'effort': None}}
        baseline = j.report(rows, {}, runtime, cases=cases)
        head = j.report(rows, {}, runtime, cases=reordered)
        self.assertEqual(baseline['case_set_digest'], head['case_set_digest'])
        self.assertTrue(j.compare(baseline, head)['same_conditions'])

    def test_qualification_identity_missing_or_unavailable_is_unverified(self):
        cases = subset(self.cases, 'goal-served')
        rows, _, _ = self.measure(cases)
        current = j.report(rows, {}, {'active': {'effort': None}}, cases=cases)
        legacy = copy.deepcopy(current)
        del legacy['qualification_identity']
        self.assertFalse(j.compare(legacy, current)['same_conditions'])
        with patch.object(j.inspect, 'getsource', side_effect=OSError('source unavailable')):
            unavailable = j.report(rows, {}, {'active': {'effort': None}}, cases=cases)
        comparison = j.compare(unavailable, unavailable)
        self.assertFalse(comparison['same_conditions'])
        self.assertIn('qualification identity missing; qualification equivalence is unverified', comparison['reasons'])
        changed = copy.deepcopy(current)
        changed['runtime']['suite_version'] = 'changed'
        self.assertIn('runtime.suite_version differs', j.compare(current, changed)['reasons'])

    def test_legacy_comparison_discloses_missing_conditions(self):
        cases = subset(self.cases, 'goal-served')
        rows, _, _ = self.measure(cases)
        current = j.report(rows, {}, {'active': {'effort': None}}, cases=cases)
        for missing in ('case_set_digest', 'effort', 'both'):
            legacy = copy.deepcopy(current)
            if missing in ('case_set_digest', 'both'):
                del legacy['case_set_digest']
            if missing in ('effort', 'both'):
                del legacy['runtime']['active']['effort']
            for before, after in ((legacy, current), (current, legacy), (legacy, legacy)):
                with self.subTest(missing=missing, before=before is legacy, after=after is legacy):
                    comparison = j.compare(before, after)
                    self.assertFalse(comparison['same_conditions'])
                    self.assertTrue(any('missing' in reason and 'unverified' in reason
                                        for reason in comparison['reasons']))
                    self.assertIn('unverified', j.compare_markdown(comparison))

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
            qualification_only = False

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
                transport = Transport([] if qualification_only else
                                      [a for c in self.cases['cases'] if c['role'] == 'goal_reached' for a in answers(c)])
                inner = d.ModelDecisionEngine(ModelAdapter(transport=transport),
                                             lambda: ({'provider': 'openai', 'endpoint': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'}, 'fixture-key'),
                                             audit=lambda record: service.record_decision(record), now=transport.clock)
                service.decision_engine = d.RoutedDecisionEngine(lambda: inner, budget=lambda: service.cap)
                service.decision_judge = s.conversation_handoff.ConversationJudgments(service.decision_engine)
                service.decision_routes = SimpleNamespace(status=lambda: {'active': {'transport': 'fixture'}})
                created.append((data, transport))
                return service
            with patch.dict(os.environ, {'AGENTOS_EVAL_HOME': str(root / 'home')}), patch.object(j, 'service_factory', factory), \
                    patch.object(j, 'pin_model') as pin, redirect_stdout(io.StringIO()) as out:
                cli.main(['judgments', 'run', '--seed', str(seed), '--model', 'fixture-model', '--requalify',
                          '--roles', 'goal_reached', '--out', str(root / 'reports')])
            pin.assert_called_once()
            self.assertEqual(pin.call_args.args[1], 'fixture-model')
            saved = json.loads(next((root / 'reports').glob('*.json')).read_text())
            expected = j.report(saved['rows'], {}, saved['runtime'], cases=self.cases)
            self.assertEqual(saved['case_set_digest'], expected['case_set_digest'])
            self.assertRegex(saved['case_set_digest'], r'^[0-9a-f]{64}$')
            self.assertIsNone(saved['runtime']['active']['effort'])
            self.assertTrue(j.compare(saved, copy.deepcopy(saved))['same_conditions'])
            self.assertEqual(saved['pinned_model'], 'fixture-model')
            self.assertIn('Pinned model: fixture-model', out.getvalue())
            self.assertEqual(saved['evidence_class'], 'fixture')
            self.assertTrue(all(r['semantic_correct'] for r in saved['rows']))
            self.assertEqual(saved['summary']['total']['calls'], 16)
            self.assertEqual(saved['requalification'], dict(attempted=False, calls=0))
            self.assertEqual(saved['call_budget']['admitted'], 16)
            self.assertFalse(created[0][0].exists())
            self.assertTrue(seed.exists())
            self.assertIn('report:', out.getvalue())
            qualification_only = True
            qualification_reports = []
            for variant, detail, expected in (('baseline', 'first', 'failed'),
                                              ('input', 'QUALIFICATION-PRIVATE-824', 'failed'),
                                              ('expectation', 'first', 'partial')):
                case = s.decision_qualification._choose(
                    'conversation-projection', {'observed_status': 'failed', 'observed_detail': detail},
                    ('failed', 'partial'), 'Report the observed status.', expected=expected)
                destination = root / variant
                with patch.dict(os.environ, {'AGENTOS_EVAL_HOME': str(root / 'home')}), \
                        patch.object(j, 'service_factory', factory), \
                        patch.object(s.decision_qualification, 'CASES', (('same-id', case),)), \
                        redirect_stdout(io.StringIO()):
                    cli.main(['judgments', 'run', '--seed', str(seed), '--roles', 'qualification-suite',
                              '--out', str(destination)])
                qualification_reports.append(json.loads(next(destination.glob('*.json')).read_text()))
            baseline = qualification_reports[0]
            self.assertTrue(j.compare(baseline, baseline)['same_conditions'])
            for changed in qualification_reports[1:]:
                self.assertEqual(baseline['case_set_digest'], changed['case_set_digest'])
                self.assertEqual(baseline['rows'][0]['id'], changed['rows'][0]['id'])
                comparison = j.compare(baseline, changed)
                self.assertFalse(comparison['same_conditions'])
                self.assertIn('qualification identity differs', comparison['reasons'])
                self.assertNotIn('QUALIFICATION-PRIVATE-824', json.dumps(changed))


class RequalificationTests(ServiceFixture):
    """Real update job and suite; fixture CLI transport, no sockets or models."""

    def stale_service(self):
        service = self.service()
        service.activate_decision_route({'transport': 'subscription_cli', 'engine': 'codex',
                                         'model_policy': 'explicit', 'model': 'gpt-5.6-terra'})
        route = service.decision_routes.active()
        # Same binary/version/suite/score, but the sandbox moved the store.
        route['strict_profile']['binding']['store'] = '/previous-owner-store'
        self.store.put('decision_route', route)
        self.assertTrue(service.decision_routes.status()['active']['requalification_needed'])
        self.assertEqual(route['qualification']['score'], 1)
        return service

    def model_calls(self):
        return sum('--output-schema' in c['argv'] for c in self.runner.calls)

    def test_pin_qualifies_only_the_requested_model_despite_a_follow_route(self):
        service = self.stale_service()
        route = service.decision_routes.active()
        route.update(mode='follow', main='codex', model_policy='fastest_qualified',
                     declared_candidates=['gpt-5.6-terra', 'gpt-5.6-luna'])
        self.store.put('decision_route', route)
        service.decision_routes.queue_qualification('codex')
        start = len(self.runner.calls)
        j.pin_model(service, 'gpt-5.6-luna')
        pinned = service.decision_routes.active()
        self.assertEqual(pinned['model_policy'], 'explicit')
        self.assertNotIn('mode', pinned)
        self.assertNotIn('qualification', pinned)
        self.assertFalse(j.effective_runtime(service)['active']['available'])
        result = j.requalify_route(service, j.CallCap(20))
        self.assertEqual(result['state'], 'passed')
        self.assertTrue(result['available'])
        active = service.decision_routes.active()
        self.assertEqual(active['qualification']['model'], 'gpt-5.6-luna')
        self.assertEqual(active['qualification']['score'], 1)
        cases = subset(j.load_cases(), 'projection-failed')
        rows = j.run(cases, service.decision_judge, service.decision_engine, roles=['status_projection'])
        self.assertTrue(all(r['semantic_correct'] for r in rows))
        calls = [c['argv'] for c in self.runner.calls[start:] if '--output-schema' in c['argv']]
        self.assertEqual(len(calls), len(s.decision_qualification.CASES) + 1)
        self.assertEqual({argv[argv.index('--model') + 1] for argv in calls}, {'gpt-5.6-luna'})

    def test_a_failed_pin_never_measures_or_falls_back_to_a_good_model(self):
        service = self.stale_service()
        self.runner.judges['gpt-5.6-luna'] = careless
        start = len(self.runner.calls)
        j.pin_model(service, 'gpt-5.6-luna')
        result = j.requalify_route(service, j.CallCap(20))
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(result['available'])
        runtime = j.effective_runtime(service)
        self.assertEqual(runtime['active']['requested_model'], 'gpt-5.6-luna')
        cases = subset(j.load_cases(), 'projection-failed')
        rows = j.run(cases, service.decision_judge, service.decision_engine, roles=['status_projection'])
        self.assertTrue(all(r['outcome'] == 'not_run' and not r['calls'] for r in rows))
        self.assertIsNone(j.summarize(rows)['total']['semantic_accuracy'])
        report = j.report(rows, {}, runtime)
        report.update(pinned_model='gpt-5.6-luna', requalification=result)
        self.assertIn('Requalification: failed.', j.markdown(report))
        self.assertIn('Route unavailable.', j.markdown(report))
        calls = [c['argv'] for c in self.runner.calls[start:] if '--output-schema' in c['argv']]
        self.assertEqual({argv[argv.index('--model') + 1] for argv in calls}, {'gpt-5.6-luna'})
        self.assertNotIn('qualification', service.decision_routes.active())

    def test_pin_without_requalification_cannot_reuse_copied_proof(self):
        service = self.stale_service()
        start = self.model_calls()
        j.pin_model(service, 'gpt-5.6-terra')
        cases = subset(j.load_cases(), 'projection-failed')
        rows = j.run(cases, service.decision_judge, service.decision_engine, roles=['status_projection'])
        self.assertEqual(self.model_calls(), start)
        self.assertTrue(all(r['outcome'] == 'not_run' for r in rows))
        with self.assertRaises(j.CaseError):
            j.pin_model(service, 'invalid model id')

    def test_shipped_update_job_requalifies_relocated_store_and_counts_calls(self):
        service = self.stale_service()
        start = self.model_calls()
        cap = j.CallCap(12)
        result = j.requalify_route(service, cap)
        self.assertEqual(result['state'], 'passed')
        self.assertTrue(result['available'])
        self.assertEqual(result['calls'], len(s.decision_qualification.CASES))
        self.assertEqual(cap.used, result['calls'])
        self.assertEqual(self.model_calls() - start, result['calls'])
        report = j.report([], {}, j.effective_runtime(service))
        report['requalification'] = result
        self.assertIn('Model calls: 11 (requalification: 11)', j.markdown(report))
        cases = subset(j.load_cases(), 'projection-failed')
        rows = j.run(cases, service.decision_judge, service.decision_engine,
                     roles=['qualification-suite'], cap=cap, scope=service.work_decision_scope)
        self.assertEqual(sum(len(r['calls']) for r in rows), 1)
        self.assertEqual(cap.used, 12)
        self.assertTrue(cap.refused)
        self.assertEqual(j.requalify_route(service, cap), dict(attempted=False, calls=0))

    def test_budget_exhaustion_cannot_qualify_or_launch_excess_calls(self):
        for limit in (0, 3):
            with self.subTest(limit=limit):
                service = self.stale_service()
                old = service.decision_routes.active()
                start = self.model_calls()
                cap = j.CallCap(limit)
                result = j.requalify_route(service, cap)
                self.assertEqual(result['state'], 'failed')
                self.assertFalse(result['available'])
                self.assertEqual(result['calls'], limit)
                self.assertEqual(self.model_calls() - start, limit)
                self.assertTrue(cap.refused)
                self.assertEqual(service.decision_routes.active(), old)


if __name__ == '__main__':
    unittest.main()
