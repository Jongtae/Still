"""Development measurement of shipped judgments; stdlib, synthetic inputs only (#42).

Adapt: existing eval sandbox/report conventions and production qualification,
policy, callers and provider adapters. No evaluator dependency or runtime policy.
"""
import hashlib
import importlib
import json
import os
import re
import sys
import time
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from .paths import REPO, SRC, eval_home

HARNESS_VERSION = 'judgment-harness/1'
CASE_VERSION = 'judgment-validation/1'
DEFAULT_CASES = REPO / 'evals/judgments/cases-v1.json'
FLAGS = ('account_change', 'owner_situation', 'technical_work')
MODEL_FIELDS = ('route', 'engine', 'provider', 'requested_model', 'observed_model', 'model_policy')
INPUTS = {
    'followup_relation': ({'utterance', 'previous_intent', 'previous_status'},
                         {'previous_request', 'previous_reply', 'previous_request_at', 'previous_reply_at'}),
    'steers_running_work': ({'running_request', 'utterance'}, {'shown_plan'}),
    'answers_open_question': ({'earlier_request', 'earlier_reply', 'utterance'}, set()),
    'parked_work_withdrawn': ({'utterance'}, set()),
    'connector_effect': ({'tool_name', 'argument_names', 'owner_request'}, set()),
    'status_projection': ({'facts'}, set()),
    'owner_act_answer': ({'act', 'items'}, {'target'}),
    'goal_reached': ({'request', 'observations', 'answer'}, {'failed_steps', 'conversation'}),
    'orchestration_plan': ({'request'}, {'conversation'}),
    'orchestration_escalation': ({'request', 'answer', 'observations', 'failed'}, {'conversation'}),
}
_STILL = None


class CaseError(ValueError):
    pass


def load_still(src=SRC):
    """Select the measured checkout before importing any Still code.

    Refuse a mixed-checkout process: use a fresh CLI process for each baseline.
    """
    global _STILL
    src = Path(src).expanduser().resolve()
    loaded = sys.modules.get('personal_agent')
    if loaded and Path(loaded.__file__).resolve().parent.parent != src:
        raise CaseError('Still already imported from another src; use a fresh process')
    sys.path.insert(0, str(src))
    names = ('decision', 'conversation_handoff', 'orchestrator', 'decision_qualification')
    _STILL = SimpleNamespace(**{name: importlib.import_module('personal_agent.' + name) for name in names}, src=src)
    return _STILL


def still():
    return _STILL or load_still()


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise CaseError(f'Expected keys {sorted(required)}; optional {sorted(optional)}')


def load_cases(path=DEFAULT_CASES):
    try:
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
        _keys(raw, {'version', 'cases', 'catalogue'})
        if raw['version'] != CASE_VERSION or not isinstance(raw['cases'], list) or not raw['cases']:
            raise CaseError('Unknown or empty case set')
        _validate_catalogue(raw['catalogue'])
        ids = set()
        for case in raw['cases']:
            _keys(case, {'id', 'role', 'class', 'lang', 'category', 'input', 'expect'}, {'targets'})
            role = case['role']
            if role not in INPUTS or case['lang'] not in ('ko', 'en'):
                raise CaseError('Unknown role or language')
            if case['class'] != ('structured-plan' if role.startswith('orchestration_') else 'bounded'):
                raise CaseError('Incorrect case class')
            if not isinstance(case['id'], str) or not re.fullmatch('[a-z][a-z0-9-]*', case['id']) or case['id'] in ids:
                raise CaseError('Invalid or duplicate id')
            ids.add(case['id'])
            if not isinstance(case['category'], str) or not case['category']:
                raise CaseError('Missing category')
            if not isinstance(case.get('targets', []), list) or any(not isinstance(t, str) or not re.fullmatch(r'#\d+', t)
                                                                   for t in case.get('targets', [])):
                raise CaseError('Invalid targets')
            _keys(case['input'], *INPUTS[role])
            _validate_input(role, case['input'])
            expected = case['expect']
            allowed = (set(FLAGS) | {'fallback', 'tier'} if role == 'orchestration_plan' else
                       {'evaluation', 'redelegate', 'escalated'} if role == 'orchestration_escalation' else
                       {'item', 'answer'} if role == 'owner_act_answer' else
                       {'value', 'abstain'} if role in ('connector_effect', 'status_projection') else
                       {'outcome', 'value', 'abstain'} if role == 'followup_relation' else {'outcome'})
            _keys(expected, set(), allowed)
            if not expected:
                raise CaseError('Empty expectation')
            for key, value in expected.items():
                if key in (*FLAGS, 'fallback', 'redelegate', 'escalated', 'abstain') and type(value) is not bool:
                    raise CaseError('Expected boolean')
                if key in ('outcome', 'value', 'tier', 'evaluation', 'item', 'answer') and not isinstance(value, str):
                    raise CaseError('Expected label')
            if 'outcome' in expected and expected['outcome'] not in ('yes', 'no'):
                raise CaseError('Invalid outcome')
            if 'tier' in expected and expected['tier'] not in ('lowest', 'higher', 'any'):
                raise CaseError('Invalid tier')
            if expected.get('abstain') is not None and expected != {'abstain': True}:
                raise CaseError('Abstention must be the only expectation')
            if role == 'connector_effect' and 'value' in expected and expected['value'] not in ('read', 'mutate', 'payment'):
                raise CaseError('Invalid effect')
            if role == 'status_projection' and 'value' in expected and expected['value'] not in ('succeeded', 'partial', 'failed', 'unknown'):
                raise CaseError('Invalid projection')
            if role == 'owner_act_answer':
                _keys(expected, {'item', 'answer'})
                if expected['answer'] not in ('approve', 'decline', 'change', 'unrelated') or expected['item'] not in (
                        'none', *[item['id'] for item in case['input']['items']]):
                    raise CaseError('Invalid owner-act expectation')
        return raw
    except (KeyError, TypeError, ValueError) as exc:
        raise CaseError(str(exc)) from exc


def _validate_catalogue(catalogue):
    _keys(catalogue, {'workers', 'routes', 'default'}, {'descriptions'})
    workers = catalogue['workers']
    if not isinstance(workers, list) or not workers or not isinstance(catalogue['routes'], dict):
        raise CaseError('Invalid catalogue')
    ids = []
    for worker in workers:
        _keys(worker, {'id', 'name', 'kind', 'available', 'default', 'default_model', 'models', 'model_tiers',
                       'tools', 'native_search', 'browser', 'cost', 'latency'})
        ids.append(worker['id'])
        for key, value in worker.items():
            if key in ('available', 'default', 'native_search', 'browser'):
                valid = type(value) is bool
            elif key in ('models', 'tools'):
                valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
            elif key == 'model_tiers':
                valid = isinstance(value, dict) and all(isinstance(k, str) and v in
                        ('lowest-cost', 'higher-cost', 'unranked') for k, v in value.items())
            else:
                valid = isinstance(value, str)
            if not valid:
                raise CaseError('Invalid worker field: ' + key)
        if worker['default_model'] not in worker['models'] or set(worker['model_tiers']) != set(worker['models']):
            raise CaseError('Undeclared model or tier')
    if len(set(ids)) != len(ids) or catalogue['default'] not in ids:
        raise CaseError('Invalid default or duplicate worker')


def _validate_input(role, values):
    for key, value in values.items():
        if key == 'argument_names':
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                raise CaseError('Invalid argument names')
        elif key == 'facts':
            if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
                raise CaseError('Invalid facts')
        elif key == 'items':
            if not isinstance(value, list) or not value:
                raise CaseError('Missing open items')
            for item in value:
                _keys(item, {'kind', 'id', 'shown_text', 'shown_at', 'expires_at', 'answers'})
                if (any(not isinstance(item[k], str) for k in item if k != 'answers')
                        or not isinstance(item['answers'], list) or any(not isinstance(v, str) for v in item['answers'])):
                    raise CaseError('Invalid open item')
        elif not isinstance(value, str):
            raise CaseError(f'Expected text: {key}')


def _entries(cases):
    entries = list(cases['cases'])
    for id, runner in still().decision_qualification.CASES:
        entries.append(dict(id='qualification-' + id, role='qualification-suite', **{'class': 'bounded'}, lang='ko',
                            category='qualification', targets=[], runner=runner))
    return entries


def plan(cases):
    counts = Counter(c['role'] for c in _entries(cases))
    roles = {r: {'cases': n, 'max_calls': n * (3 if r == 'orchestration_escalation' else 1)}
             for r, n in sorted(counts.items())}
    total = sum(r['max_calls'] for r in roles.values())
    return {'roles': roles, 'cases': sum(counts.values()), 'max_calls': total,
            'max_calls_per_route': {'configured-judgment-route': total}}


class CallLimit(Exception):
    code = 'turn_budget'


class CallCap:
    peer_store_root = None

    def __init__(self, max_calls):
        if type(max_calls) is not int or max_calls < 0:
            raise ValueError('max_calls must be a nonnegative integer')
        self.max_calls, self.used, self.refused = max_calls, 0, False

    def spend_turn(self, reserve=False):
        if self.used >= self.max_calls:
            self.refused = True
            raise CallLimit()
        self.used += 1

    def interrupted(self):
        return None


def _attempt(attempt, catalogue):
    worker = catalogue.worker(attempt.worker)
    o = still().orchestrator
    model = attempt.model or worker['default_model']
    tier = 'lowest' if o.runs_lowest(worker, attempt.model) else (
        'higher' if worker['model_tiers'].get(model) == o.TIER_HIGHER else 'unranked')
    return dict(worker=attempt.worker, model=model, tier=tier, fallback=bool(attempt.fallback),
                fallback_code=attempt.fallback, **{flag: getattr(attempt, flag) for flag in FLAGS})


def _call(case, cases, judgments, engine, policy, keep_text):
    s = still()
    role, inputs = case['role'], dict(case.get('input', {}))
    if role == 'qualification-suite':
        passed, outcome = case['runner'](engine, policy)
        return {'passed': bool(passed), 'outcome': outcome}
    if role.startswith('orchestration_'):
        catalogue = s.orchestrator.Catalogue(**cases['catalogue'])
        orch = s.orchestrator.Orchestration(judgments, catalogue, request=inputs['request'],
                                           conversation=inputs.get('conversation', ''), policy=policy)
        attempt = orch.first()
        observed = _attempt(attempt, catalogue)
        if role == 'orchestration_escalation':
            evaluation = orch.evaluate_answer(inputs['answer'], inputs['observations'], inputs['failed'])
            following = orch.next(attempt, evaluation)
            after = _attempt(following, catalogue) if following else None
            observed.update(evaluation=evaluation, redelegate=following is not None,
                            escalated=bool(following and following.signature != attempt.signature and
                                           (after['tier'] == 'higher' or following.worker != attempt.worker)))
            if after:
                observed['next'] = after
        return observed
    if role == 'status_projection':
        decision = engine.choose(s.decision.DecisionContext('conversation-projection', inputs['facts']),
                                 ('succeeded', 'partial', 'failed', 'unknown'),
                                 s.decision_qualification._PROJECTION_QUESTION)
        return {'value': policy.selection(decision), 'outcome': decision.outcome}
    if role == 'parked_work_withdrawn':
        result = judgments.parked_work_withdrawn(inputs['utterance'], ('google-gmail-read',))
    else:
        result = getattr(judgments, role)(**inputs)
    if role == 'connector_effect':
        return {'value': result}
    if role == 'owner_act_answer':
        return {'item': result['item'], 'answer': result['answer']} if result else {'item': None, 'answer': None}
    observed = {'outcome': result.outcome}
    if role == 'followup_relation':
        observed['value'] = result.value
    if role == 'goal_reached':
        observed['note_returned'] = result.outcome == s.conversation_handoff.JUDGMENT_NO and result.note is not None
        if keep_text:
            observed.update(gap=result.value, note=result.note)
    return observed


def _score(case, observed, outcome):
    role, expected = case['role'], case.get('expect', {})
    routing = role in ('followup_relation', 'steers_running_work', 'answers_open_question',
                       'parked_work_withdrawn', 'connector_effect', 'owner_act_answer') or role.startswith('orchestration_')
    if outcome != 'decided':
        error = 'budget_refused' if outcome == 'budget_refused' else (
            'invalid_plan' if role.startswith('orchestration_') and outcome == 'malformed' else 'engine_failure')
        return False, False if routing else None, error
    if role == 'qualification-suite':
        ok = observed['passed']
        return ok, None, 'none' if ok else 'wrong_choice'
    if expected.get('abstain'):
        ok = observed.get('value') is None and observed.get('outcome') in (None, 'no', 'unavailable', 'decided')
        return ok, ok if routing else None, 'none' if ok else 'guessed'
    if role.startswith('orchestration_'):
        if observed.get('fallback_code') == still().orchestrator.FALLBACK_UNCONFIDENT:
            return False, False, 'abstained'
        ok = all(observed.get(k) == v for k, v in expected.items() if not (k == 'tier' and v == 'any'))
        error = 'none' if ok else 'wrong_choice'
        if observed.get('fallback_code') in (still().orchestrator.FALLBACK_MALFORMED, still().orchestrator.FALLBACK_INVALID):
            ok, error = False, 'invalid_plan'
        elif any(expected.get(flag) for flag in FLAGS) and observed.get('tier') == 'lowest':
            ok, error = False, 'unsafe_misroute'
        return ok, ok, error
    abstained = (observed.get('outcome') == 'unavailable' or
                 role in ('connector_effect', 'status_projection') and observed.get('value') is None or
                 role == 'owner_act_answer' and observed.get('item') is None)
    if abstained:
        return False, False if routing else None, 'abstained'
    ok = all(observed.get(k) == v for k, v in expected.items())
    error = 'none' if ok else 'wrong_choice'
    if not ok:
        if expected.get('outcome') == 'no' and observed.get('outcome') == 'yes':
            error = 'false_completion' if role == 'goal_reached' else 'false_positive'
        elif expected.get('outcome') == 'yes' and observed.get('outcome') == 'no':
            error = 'false_negative'
        elif role == 'status_projection' and expected.get('value') != 'succeeded' and observed.get('value') == 'succeeded':
            error = 'false_completion'
        elif role == 'connector_effect' and ((expected.get('value') == 'payment' and observed.get('value') in ('read', 'mutate'))
                                             or expected.get('value') == 'mutate' and observed.get('value') == 'read'):
            error = 'unsafe_misroute'
        elif role == 'owner_act_answer' and (expected.get('answer') in ('decline', 'none') or expected.get('item') == 'none') and observed.get('answer') == 'approve':
            error = 'unsafe_misroute'
    return ok, ok if routing else None, error


def _models(records):
    return sorted({tuple(record.get(k, '') for k in MODEL_FIELDS) for record in records})


def _outcome(records, observed):
    failures = [r.get('outcome') for r in records if r.get('outcome') != 'decided']
    if any(r.get('failure') == 'turn_budget' for r in records):
        return 'budget_refused'
    if failures:
        return failures[0] if failures[0] in ('provider_unavailable', 'timeout', 'malformed') or str(failures[0]).startswith('error:') else 'provider_unavailable'
    if observed.get('fallback_code') == still().orchestrator.FALLBACK_UNAVAILABLE:
        return 'provider_unavailable'
    if observed.get('fallback_code') == still().orchestrator.FALLBACK_BUDGET:
        return 'budget_refused'
    if observed.get('fallback_code') == still().orchestrator.FALLBACK_MALFORMED:
        return 'malformed'
    if observed.get('outcome') in ('provider_unavailable', 'timeout', 'malformed'):
        return observed['outcome']
    return 'decided'


class _MeasuredEngine:
    """Observe typed returns without replacing the shipped engine or policy."""

    def __init__(self, engine, audit):
        self.engine, self.audit = engine, audit

    def __getattr__(self, name):
        method = getattr(self.engine, name)
        if name not in ('judge', 'judge_explained', 'choose', 'choose_many', 'structured', 'score'):
            return method

        def measured(context, *args, **kwargs):
            index = len(self.audit)
            try:
                decision = method(context, *args, **kwargs)
            except Exception as exc:
                self.audit.append({'outcome': 'error:' + type(exc).__name__, 'sent': False})
                raise
            confidence = decision.confidence
            if len(self.audit) == index:
                # Fixture engines do not emit audits. Reuse the production shape;
                # fixture calls are simulated admissions, never live evidence.
                kind = 'judge' if name == 'judge_explained' else name
                record = still().decision.audit_record(context, kind, decision.outcome, {}, confidence, 0,
                                                        failure=context.refused or '')
                self.audit.append(record)
            for record in self.audit[index:]:
                record['sent'] = (confidence.sent is True or confidence.sent is None and decision.decided)
            return decision
        return measured


def run(cases, judgments, engine, *, policy=None, timer=time.monotonic, audit=None, cap=None, roles=None,
        trials=1, keep_text=False, scope=None):
    """Trials are independent measurements (one row per trial), never best-of-N scoring.

    ``audit`` is the caller-owned append-only list. A service scope installs the
    cap on its shipped routed engine; fixture callers can pass that same seam.
    """
    if type(trials) is not int or trials < 1:
        raise ValueError('trials must be positive')
    roles = set(roles.split(',') if isinstance(roles, str) else roles or [])
    if roles - (set(INPUTS) | {'qualification-suite'}):
        raise CaseError('Unknown role filter')
    policy = policy or still().decision.DecisionPolicy()
    audit = [] if audit is None else audit
    measured = _MeasuredEngine(engine, audit)
    rows = []
    for case in _entries(cases):
        if roles and case['role'] not in roles:
            continue
        for trial in range(trials):
            row = {k: case.get(k, []) for k in ('id', 'role', 'class', 'lang', 'category', 'targets')}
            row.update(trial=trial + 1, retries=int(trial > 0), calls=[], timeouts=0, models=[],
                       decision_seconds=0, end_to_end_seconds=0, startup_seconds=None, observed={},
                       semantic_correct=False, routing_correct=None, error='none', schema_valid=False)
            if cap and cap.refused:
                row.update(outcome='not_run', semantic_correct=None)
                rows.append(row)
                continue
            start, index = timer(), len(audit)
            observed = {}
            try:
                original_engine = judgments.engine
                try:
                    judgments.engine = measured
                    with scope(None, cap) if scope else nullcontext():
                        observed = _call(case, cases, judgments, measured, policy, keep_text)
                finally:
                    judgments.engine = original_engine
                outcome = _outcome(audit[index:], observed)
            except Exception as exc:
                outcome = 'budget_refused' if getattr(exc, 'code', None) == 'turn_budget' else 'error:' + type(exc).__name__
            row['end_to_end_seconds'] = round(timer() - start, 6)
            records = audit[index:]
            if cap and cap.refused:
                outcome = 'budget_refused'
            ok, routing, error = _score(case, observed, outcome)
            sent = [dict(r) for r in records if r.get('sent') is True]
            timings = [r['cli_timing'] for r in records if isinstance(r.get('cli_timing'), dict)]
            startup = [t.get('first_event_s') if t.get('first_event_s') is not None else t.get('spawn_s')
                       for t in timings]
            startup = [v for v in startup if isinstance(v, (int, float)) and not isinstance(v, bool)]
            row.update(outcome=outcome, observed=observed, semantic_correct=ok, routing_correct=routing, error=error,
                       schema_valid=outcome == 'decided' and error != 'invalid_plan', calls=sent,
                       timeouts=sum(r.get('outcome') == 'timeout' for r in records),
                       decision_seconds=round(sum(r.get('elapsed_seconds') or 0 for r in records), 6),
                       startup_seconds=startup[0] if startup else None, models=_models(records))
            rows.append(row)
    return rows


def effective_runtime(service, audit=()):
    status = service.decision_routes.status()
    allowed = ('transport', 'source', 'engine', 'provider', 'requested_model', 'model_policy', 'effort',
               'destination', 'available', 'requalification_needed', 'cli_version')
    active = {k: status['active'][k] for k in allowed if k in status.get('active', {})
              and isinstance(status['active'][k], (str, bool, int, float, type(None)))}
    qualification = status.get('active', {}).get('qualification', {})
    if isinstance(qualification, dict):
        active['qualification'] = {k: qualification[k] for k in ('suite_version', 'model', 'score', 'cli_version',
                                                               'p50_seconds', 'p95_seconds') if k in qualification
                                   and isinstance(qualification[k], (str, bool, int, float, type(None)))}
    return dict(active=active, effective={k: status.get('effective', {})[k] for k in ('state', 'reason')
                                         if k in status.get('effective', {})},
                suite_version=status.get('suite_version', still().decision_qualification.SUITE_VERSION),
                observed_models=_models(audit), unavailable_reasons=dict(Counter(r['failure'] for r in audit if r.get('failure'))))


def _summary(rows):
    measured = [r for r in rows if r['outcome'] != 'not_run']
    routing = [r for r in measured if r['routing_correct'] is not None]
    latency = still().decision_qualification.latency
    usage = [r['usage'] for row in measured for r in row['calls'] if type(r.get('usage')) in (int, float)]
    return dict(n=len(rows), ran=len(measured), semantic_accuracy=sum(r['semantic_correct'] for r in measured) / len(measured) if measured else None,
                routing_accuracy=sum(r['routing_correct'] for r in routing) / len(routing) if routing else None,
                errors=dict(Counter(r['error'] for r in measured)), schema_valid_rate=sum(r['schema_valid'] for r in measured) / len(measured) if measured else None,
                abstentions=sum(r['error'] == 'abstained' for r in measured), calls=sum(len(r['calls']) for r in measured),
                timeouts=sum(r['timeouts'] for r in measured), retries=sum(r['retries'] for r in measured),
                decision_latency=latency([r['decision_seconds'] for r in measured]),
                end_to_end_latency=latency([r['end_to_end_seconds'] for r in measured]),
                startup_latency=latency([r['startup_seconds'] for r in measured if r['startup_seconds'] is not None]),
                usage=sum(usage) if usage else 'not reported', cost='not reported')


def summarize(rows):
    return dict(total=_summary(rows),
                roles={k: _summary([r for r in rows if r['role'] == k]) for k in sorted({r['role'] for r in rows})},
                classes={k: _summary([r for r in rows if r['class'] == k]) for k in sorted({r['class'] for r in rows})},
                matrix={f'{role} / {cls}': _summary([r for r in rows if r['role'] == role and r['class'] == cls])
                        for role, cls in sorted({(r['role'], r['class']) for r in rows})},
                targets={tag: {'n': sum(tag in r['targets'] for r in rows),
                               'passed': sum(bool(r['semantic_correct']) and r['routing_correct'] is not False
                                             for r in rows if tag in r['targets'])}
                         for tag in sorted({t for r in rows for t in r['targets']})})


def report(rows, run_meta, runtime):
    src = Path(run_meta.get('src', still().src))
    evidence = run_meta.get('evidence_class', 'fixture')
    if evidence != 'fixture' and not evidence.startswith('live-route:'):
        raise ValueError('Unknown evidence class')
    return dict(harness_version=HARNESS_VERSION, case_set_version=run_meta.get('case_set_version', CASE_VERSION),
                evidence_class=evidence, git_head=run_meta.get('git_head'), src=str(src),
                prompt_digests={str(p.relative_to(src)): hashlib.sha256(p.read_bytes()).hexdigest()[:12]
                                for p in sorted((src / 'personal_agent/prompts').glob('*.md'))},
                runtime=runtime, summary=summarize(rows), rows=rows)


def markdown(result):
    lines = [f"# Judgment validation — {result['evidence_class']}", '',
             f"Cases: {result['case_set_version']}; head: {result['git_head']}", '',
             '| Role / class | n | semantic | routing | FP/FN/abstained/invalid/false-completion/unsafe | decision p50/p95 | end-to-end p50/p95 | calls |',
             '|---|---:|---:|---:|---|---|---|---:|']
    def percent(v):
        return '—' if v is None else f'{v:.1%}'
    def timing(v):
        return '—' if v is None else f"{v['p50']}/{v['p95']}"
    for label, s in result['summary']['matrix'].items():
        errors = '/'.join(str(s['errors'].get(k, 0)) for k in ('false_positive', 'false_negative', 'abstained', 'invalid_plan', 'false_completion', 'unsafe_misroute'))
        lines.append(f"| {label} | {s['n']} | {percent(s['semantic_accuracy'])} | {percent(s['routing_accuracy'])} | {errors} | {timing(s['decision_latency'])} | {timing(s['end_to_end_latency'])} | {s['calls']} |")
    lines += ['', 'Runtime:', '```json', json.dumps(result['runtime'], ensure_ascii=False, indent=2), '```',
              '', 'Usage/cost: ' + str(result['summary']['total']['usage']) + ' / not reported', '']
    return '\n'.join(lines)


def write_report(result, folder=None, id=None):
    from .sandbox import run_id
    folder = Path(folder or eval_home() / 'reports')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = 'judgments-' + (id or run_id())
    paths = [folder / (stem + suffix) for suffix in ('.json', '.md')]
    for path, content in zip(paths, [json.dumps(result, ensure_ascii=False, indent=2) + '\n', markdown(result)]):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
    return tuple(paths)


def compare(baseline, head):
    if baseline['case_set_version'] != head['case_set_version']:
        raise CaseError('Cannot compare different case set versions')
    reasons = [f'runtime.{k} differs' for k in ('transport', 'engine', 'requested_model', 'model_policy')
               if baseline['runtime'].get('active', {}).get(k) != head['runtime'].get('active', {}).get(k)]
    if baseline['evidence_class'] != head['evidence_class']:
        reasons.append('evidence_class differs')
    def verdict(row):
        return row['outcome'] != 'not_run' and row['semantic_correct'] and row['routing_correct'] is not False
    def indexed(result):
        return {(r['id'], r.get('trial', 1)): r for r in result['rows']}
    before, after = indexed(baseline), indexed(head)
    if set(before) != set(after):
        reasons.append('case/trial selections differ')
    changes = []
    for key in sorted(set(before) & set(after)):
        a, b = before[key], after[key]
        if a['outcome'] == 'not_run' or b['outcome'] == 'not_run':
            continue
        va, vb = bool(verdict(a)), bool(verdict(b))
        change = 'fixed' if vb and not va else 'regressed' if va and not vb else 'unchanged-fail' if not vb else None
        if change:
            changes.append(dict(id=key[0], trial=key[1], change=change))
    deltas = {}
    for role in sorted(set(baseline['summary']['roles']) & set(head['summary']['roles'])):
        a, b = baseline['summary']['roles'][role], head['summary']['roles'][role]
        delta = {k: b[k] - a[k] if a[k] is not None and b[k] is not None else None
                 for k in ('semantic_accuracy', 'routing_accuracy')}
        for metric in ('decision_latency', 'end_to_end_latency'):
            delta[metric] = {k: round(b[metric][k] - a[metric][k], 6) for k in ('p50', 'p95')} if a[metric] and b[metric] else None
        deltas[role] = delta
    prompts = sorted(k for k in set(baseline['prompt_digests']) | set(head['prompt_digests'])
                     if baseline['prompt_digests'].get(k) != head['prompt_digests'].get(k))
    return dict(same_conditions=not reasons, reasons=reasons, changes=changes, roles=deltas, changed_prompt_digests=prompts)


def compare_markdown(result):
    return '# Judgment comparison\n\n```json\n' + json.dumps(result, ensure_ascii=False, indent=2) + '\n```\n'


def service_factory(data):
    # Production construction, only reached by an explicit seeded live run.
    load_still(still().src)
    service = importlib.import_module('personal_agent.quickstart_service')
    store = importlib.import_module('personal_agent.quickstart_store')
    return service.AgentService(store.QuickStore(data))


_production_service_factory = service_factory

def live_run(args, factory=None):
    from .cli import git_head, resolve_seed
    from .sandbox import Sandbox
    if not args.seed:
        raise SystemExit('judgments run requires --seed (a sanitized snapshot)')
    load_still(args.src)
    cases = load_cases(args.cases)
    selected = args.roles.split(',') if args.roles else None
    maximum = sum(r['max_calls'] for role, r in plan(cases)['roles'].items() if not selected or role in selected) * args.trials
    cap = CallCap(maximum if args.max_calls is None else args.max_calls)
    sandbox = Sandbox(eval_home() / 'sandboxes', seed=resolve_seed(args.seed), src=args.src)
    audit = []
    try:
        data = sandbox.prepare()
        service = (factory or service_factory)(data)
        original = service.record_decision

        def capture(record):
            original(record)
            audit.append(dict(record))

        service.record_decision = capture
        rows = run(cases, service.decision_judge, service.decision_engine, audit=audit, cap=cap,
                   scope=service.work_decision_scope, roles=selected, trials=args.trials, keep_text=args.keep_text)
        runtime = effective_runtime(service, audit)
        measured_head = git_head(Path(args.src).parent)
        meta = dict(src=args.src, git_head=measured_head, case_set_version=cases['version'],
                    evidence_class=('live-route:' + runtime['active'].get('transport', 'unknown')
                                    if (factory or service_factory) is _production_service_factory else 'fixture'))
        result = report(rows, meta, runtime)
        paths = write_report(result, args.out)
        print(markdown(result))
        print(f'report: {paths[0]}')
        return result
    finally:
        sandbox.stop()
