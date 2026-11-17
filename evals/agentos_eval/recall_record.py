"""Explicit opt-in, budgeted two-phase recorder. Never used by default replay.

Adopt judgments.service_factory/requalify_route/CallCap, RoutedDecisionEngine,
Sandbox.prepare and synthetic_seed marker. No second qualification/route path.
"""
import copy
import json
import re
from time import time as real_time
from collections import Counter
from pathlib import Path
from contextlib import nullcontext
from unittest import mock

from . import recall_pairs as pairs
from .recall_regression import (FLAGS, TERMS_VERSION, PLAN_VERSION, Executor, fixture_scope,
    raw_row, digest, filter_digest, terms_prompt_digest, safe_catalogue, prompt_digest, layer, seed_fixture,
    apply_terms, validate_terms, validate_plans, terms_status_counts)


class RealClockEngine:
    """Keep fixture facts/receipts fixed; restore wall time only in the caller.

    This proxy sits INSIDE RoutedDecisionEngine, retaining its admission callback.
    Both terms and plan use the structured API; qualification precedes fixture_scope.
    """
    def __init__(self, engine):
        self.engine = engine

    def structured(self, context, question, schema, valid=None):
        with mock.patch('time.time', real_time):
            return self.engine.structured(context, question, schema, valid)


class PurposeRouter:
    """All non-plan purposes use the SAME unavailable/goal-yes fixtures in both modes."""
    def __init__(self, *, plan=None, live=None, cap=None, service=None, execution=None, audit=None):
        from personal_agent.decision import FixtureDecisionEngine, BinaryDecision, OUTCOME_DECIDED, fixture_confidence
        self.plan, self.live, self.cap = plan, live, cap
        self.service, self.execution = service, execution
        self.audit = audit if audit is not None else []
        self.plan_outcome, self.plan_failure = None, ''
        self.calls, self.error, self.entry = Counter(), None, None
        self.engine = FixtureDecisionEngine(structured=self.structured,
            judge=lambda c,p: BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
                  if c.purpose == 'goal-reached' else None)

    def forward(self, context, question, schema):
        from personal_agent.orchestrator import PURPOSE
        if context.purpose != PURPOSE:
            self.error = 'Non-plan purpose would go live: ' + context.purpose
            raise RuntimeError(self.error)
        if self.calls[context.purpose]:
            self.error = 'Second live plan call in one Work'
            raise RuntimeError(self.error)
        self.calls[context.purpose] += 1
        # Route construction and the shipped qualification guard consult the
        # service execution adapter even though they use the tool-less caller.
        # Restore it ONLY inside this admitted plan boundary; workers remain
        # fixtures and the real worker execute method remains guarded.
        scope = mock.patch.object(self.service,'execution_adapter',self.execution) if self.service else nullcontext()
        before = len(self.audit)
        try:
            with scope:
                answer = self.live.structured(context, question, schema)
        except Exception as exc:
            self.plan_outcome = 'exception'
            self.plan_failure = type(exc).__name__ + ': ' + str(exc)
            self.error = self.plan_failure
            raise
        records = [r for r in self.audit[before:] if r.get('purpose') == context.purpose]
        self.plan_failure = next((r['failure'] for r in reversed(records) if r.get('failure')), '')
        if not self.plan_failure:
            self.plan_failure = getattr(context, 'refused', '') or ''
        return answer

    def structured(self, context, question, schema):
        from personal_agent.orchestrator import PURPOSE
        from personal_agent.decision import StructuredDecision, OUTCOME_DECIDED, fixture_confidence
        if context.purpose != PURPOSE:
            return None
        self.input_digest = digest(dict(facts=context.facts, question=question, schema=schema))
        if self.live is None:
            if self.calls[context.purpose]:
                self.error = 'Second plan call in one Work'
                return None
            self.calls[context.purpose] += 1
            answer = StructuredDecision(OUTCOME_DECIDED, self.plan, fixture_confidence())
        else:
            answer = self.forward(context,question,schema)
        self.plan_outcome = getattr(answer, 'outcome', 'malformed')
        raw = getattr(answer,'data',None)
        if self.plan_outcome == OUTCOME_DECIDED and not isinstance(raw, dict):
            self.plan_outcome, self.plan_failure = 'malformed', 'Decided answer has no structured object'
        if self.plan_outcome != OUTCOME_DECIDED:
            self.plan_failure = self.plan_failure or self.plan_outcome
            self.error = 'Plan not decided: ' + self.plan_failure
            return answer
        self.entry = dict(raw)
        self.entry.setdefault('preview',None)
        for key in ('reply','recall_query','worker','ask_location'):
            self.entry.setdefault(key,'')
        for flag in FLAGS[:3]:
            self.entry.setdefault(flag,False)
        return answer


def run_order(base, additions, repetitions):
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError('--heldout-runs must be positive')
    return [(1,c) for c in base['cases'] if c['split']=='dev'] + [
        (r,c) for r in range(1,repetitions+1) for c in base['cases'] if c['split']=='heldout'] + [
        (1,c) for c in additions['cases']]


def validate_start(args):
    from .cli import resolve_seed
    from .paths import REPO
    if not args.seed:
        raise ValueError('recall-record requires --seed (synthetic only)')
    seed = resolve_seed(args.seed)
    marker = seed / 'synthetic-seed.json'
    if not marker.is_file() or json.loads(marker.read_text()).get('owner_data') != 'none':
        raise ValueError('Seed lacks the synthetic-seed.json marker with owner_data=none')
    if args.max_calls is None:
        raise ValueError('recall-record requires explicit --max-calls')
    if type(args.max_calls) is not int or args.max_calls < 1:
        raise ValueError('--max-calls must be positive')
    if args.out is None or Path(args.out).expanduser().resolve().is_relative_to(REPO.resolve()):
        raise ValueError('--out must be outside the repository')
    return seed


def record_terms(service, data, refs, cap, *, blocked=False):
    """The shipped arm -> claim -> run_terms -> finish, including adaptive packing."""
    from personal_agent import owner_model as om, memory_terms as mt
    upkeep = service.owner_model
    manifests, start, complete = [], cap.used, False
    controls = upkeep.settings()
    lifted = max(controls['daily_calls'],4*cap.max_calls)
    original_settings = upkeep.settings
    # MAX_DAILY_CALLS clamps persisted settings to 200. Scoped settings is the
    # equivalent override explicitly permitted by the brief; no seed mutation.
    with fixture_scope(data) as now, mock.patch.object(upkeep,'settings',
            lambda db=None:dict(original_settings(db), daily_calls=lifted)), \
            mock.patch.object(upkeep,'clock',lambda:now):
        judgments = service._owner_model_judgments(None)
        while True:
            with service.store.db() as db:
                eligible = upkeep._terms_rows(db,data['today']).fetchone() is not None
            if not eligible:
                complete = True
                break
            if blocked or cap.used >= cap.max_calls or cap.used-start >= 60:
                break
            ident = upkeep.arm_terms(now=now,available=True)
            row = upkeep.claim_due(now,redact=judgments.redact)
            if row is None or row['job_id'] != ident or row['kind'] != om.KIND_TERMS:
                raise ValueError('Terms claim unavailable or non-terms run selected')
            before = cap.used
            state,calls,event,detail = upkeep.run_terms(row,judgments)
            upkeep.finish(ident,state,calls,event,detail,now,kind=om.KIND_TERMS)
            manifest = json.loads(detail['manifest'])
            reverse = {v:k for k,v in refs.items()}
            for item in manifest['entries']:
                item['ref'] = reverse[item.pop('id')]
            manifests.append(manifest)
            if cap.used == before and manifest['result'] == 'unsent':
                break
    exported = {}
    for ref,ident in refs.items():
        row = raw_row(service.store,ident)
        exported[ref] = dict(terms=row['retrieval_terms'] or '',
            meta=mt.parse_meta(row['retrieval_terms_meta']) if row['retrieval_terms_meta'] else None)
    recording = dict(version=TERMS_VERSION,fixture_version=data['version'],fixture_digest=digest(data),
        filter_digest=filter_digest(),terms_prompt_digest=terms_prompt_digest(),rows=exported,
        manifests=manifests,complete=complete,calls=cap.used-start,
        share_limit_lift=dict(method='scoped Upkeep.settings override in sandbox only',
            previous_daily_calls=controls['daily_calls'],daily_calls=lifted,terms_share=max(1,lifted//4)),
        tokens='not observable')
    recording['status_counts'] = terms_status_counts(recording)
    return recording


def load_inputs(args, data, order):
    """Validate file-only resume bindings before any route/qualification call."""
    resume_path = getattr(args,'resume',None)
    terms_path = getattr(args,'terms',None)
    if resume_path and not terms_path:
        terms_path = Path(resume_path).with_name('terms.json')
    terms = json.loads(Path(terms_path).read_text()) if terms_path else None
    if terms is not None:
        validate_terms(terms,data)
        if terms.get('complete') is not True or terms.get('tokens') != 'not observable':
            raise ValueError('Reused terms must be complete with tokens not observable')
    resume = json.loads(Path(resume_path).read_text()) if resume_path else None
    if resume is not None:
        validate_plans(resume,data,terms)
        if resume['complete']:
            raise ValueError('Resume requires an incomplete plans recording')
        if resume['heldout_runs'] != args.heldout_runs:
            raise ValueError('Resume heldout runs differs')
        expected = {(str(r),c['id']) for r,c in order}
        existing = {(r,ident) for r,entries in resume['runs'].items() for ident in entries}
        if not existing <= expected:
            raise ValueError('Resume contains unexpected run/case pairs')
        if not resume.get('call_budget',{}).get('sessions'):
            raise ValueError('Resume requires per-session call budgets')
    return terms,resume


def record(args, *, factory=None, sandbox_factory=None, requalify=None):
    """Only CLI recall-record calls this; injected dependencies support offline tests."""
    from personal_agent.decision import RoutedDecisionEngine
    from personal_agent.orchestrator import worker_catalogue
    from . import judgments
    from .sandbox import Sandbox, SandboxPool, JUDGMENT_KEYS, read_config, write_config
    from .paths import eval_home
    from .cli import git_head
    seed = validate_start(args)
    base = pairs.load(args.fixture)
    additions = json.loads(Path(args.additions).read_text())
    data = layer(base,additions)
    order = run_order(base,additions,args.heldout_runs)
    terms,resume = load_inputs(args,data,order)
    reused = terms is not None
    cap = judgments.CallCap(args.max_calls)
    # Adopt SandboxPool's per-seed qualification cache and stable slot path,
    # without starting its server/ports. Relocating a strict CLI binding would
    # otherwise force qualification again on every resume.
    anchor = getattr(args,'resume',None) or getattr(args,'terms',None)
    anchor = Path(anchor).expanduser().resolve().parent if anchor else Path(args.out).expanduser().resolve()
    cache_key = ((resume or {}).get('sandbox_cache_key') or
                 (terms or {}).get('sandbox_cache_key') or digest(str(anchor)))
    if not isinstance(cache_key,str) or not re.fullmatch('[0-9a-f]{64}',cache_key):
        raise ValueError('Invalid sandbox cache key')
    pool = SandboxPool(size=1,seed=seed,
        root=eval_home()/'sandboxes'/'recall-record'/cache_key)
    slot = pool._claim_slot()
    sandbox = None
    phase_counts, failure, stop = {}, None, None
    try:
        sandbox = (sandbox_factory or Sandbox)(pool.root,seed=seed,src=args.src,slot=slot)
        path = sandbox.prepare()  # no server, socket, scheduler or Sandbox.start
        cached = pool.cached_judgment(slot,'recall-record')
        if cached:
            write_config(path,cached)
        service = (factory or judgments.service_factory)(path)
        # Synthetic builder creates an empty store; refuse a disguised owner snapshot.
        with service.store.db() as db:
            if db.execute('SELECT 1 FROM memories LIMIT 1').fetchone() or db.execute('SELECT 1 FROM jobs LIMIT 1').fetchone():
                raise ValueError('Synthetic seed must contain no Memory or Work rows')
        audit = []
        original_record = service.record_decision
        def capture(row):
            audit.append(dict(row))
            original_record(row)
        service.record_decision = capture
        # Read-only catalogue check before qualification, so a bad resume spends
        # no calls. Fallback activation uses only the existing fixture probes.
        catalogue = worker_catalogue(service)
        catalogue_source = 'sandbox service worker_catalogue (stored configuration; no live calls)'
        if not catalogue.default:
            from .recall_arms import _runtime_catalogue
            from .recall_regression import install_fixture_workers
            catalogue = _runtime_catalogue()
            install_fixture_workers(service)
            catalogue_source = ('fallback _runtime_catalogue: synthetic seed has no Main AI; '
                                'real activation would need a non-plan live probe; fixture worker setup only')
        cat = safe_catalogue(catalogue,service.store)
        if resume is not None:
            if resume['catalogue'] != cat:
                raise ValueError('Resume catalogue differs')
            if resume['plan_prompt_schema_digest'] != prompt_digest(catalogue):
                raise ValueError('Resume plan prompt/schema digest differs')
        before = cap.used
        if args.requalify:
            shared_maximum = cap.max_calls
            try:
                cap.max_calls = min(shared_maximum,cap.used+44)
                (requalify or judgments.requalify_route)(service,cap)
            finally:
                cap.max_calls = shared_maximum
        phase_counts['qualification'] = cap.used-before
        if phase_counts['qualification'] > 44:
            raise ValueError('Qualification exceeded its 44-call allocation')
        pool.store_judgment(slot,'recall-record',read_config(path,JUDGMENT_KEYS))
        original_engine = service.decision_engine
        execution = service.execution_adapter
        # Replace the Work budget rather than nesting routed engines. Wall time
        # is restored only inside the resolved engine, after facts are built.
        resolve = original_engine.resolve if isinstance(original_engine,RoutedDecisionEngine) else lambda:original_engine
        capped = RoutedDecisionEngine(lambda:RealClockEngine(resolve()),budget=lambda:cap)
        service.use_decision_engine(capped)
        with fixture_scope(data):
            refs = seed_fixture(service.store,data)
            if reused:
                apply_terms(service.store,data,terms,refs)
            else:
                terms = record_terms(service,data,refs,cap,blocked=cap.refused)
        phase_counts['terms'] = 0 if reused else terms['calls']
        runtime = judgments.effective_runtime(service,audit)
        identity = dict(route=runtime['active'].get('transport','not reported'),
            engine=runtime['active'].get('engine','not reported'),
            requested_model=runtime['active'].get('requested_model') or 'not reported',
            observed_model=sorted({r['observed_model'] for r in audit if r.get('observed_model')}) or 'not reported')
        evidence = ('live-route:' + runtime['active'].get('transport','unknown')
                    if factory is None else 'fixture (model-free)')
        if not reused:
            terms.update(identity=identity,evidence_class=evidence,sandbox_cache_key=cache_key,
                call_budget=dict(max_calls=cap.max_calls,
                    admitted=phase_counts['qualification']+phase_counts['terms']))
        # A reused file's object is never modified, including its identity/budget.
        terms_digest = digest(terms)
        plans = copy.deepcopy(resume) if resume is not None else dict(version=PLAN_VERSION,
            fixture_digest=digest(data),terms_digest=terms_digest,
            plan_prompt_schema_digest=prompt_digest(catalogue),catalogue=cat,
            catalogue_source=catalogue_source,heldout_runs=args.heldout_runs,runs={},
            tokens='not observable',git_head=git_head(),live_calls_by_purpose={},evidence_class=evidence)
        plans['sandbox_cache_key'] = cache_key
        plans['terms_source'] = 'reused' if reused else 'recorded'
        if not terms['complete']:
            failure = 'Terms phase incomplete (budget reached or unavailable)'
        executor = Executor(service,data,refs,catalogue)
        start = cap.used
        if failure is None:
            for run,case in order:
                if case['id'] in plans['runs'].get(str(run),{}):
                    continue
                if cap.used >= cap.max_calls:
                    failure = 'Shared call cap reached'
                    stop = dict(run=run,case=case['id'],reason=failure)
                    break
                router = PurposeRouter(live=capped,cap=cap,service=service,execution=execution,audit=audit)
                before = cap.used
                try:
                    observed = executor.run(case,router.engine)
                    if router.error:
                        raise ValueError(router.error)
                    if router.plan_outcome != 'decided':
                        raise ValueError('Plan not decided')
                    if router.calls.get('work-orchestration') != 1:
                        raise ValueError('Work did not make exactly one plan call')
                    if cap.used-before != 1:
                        raise ValueError('Plan call was not admitted exactly once')
                    plans['runs'].setdefault(str(run),{})[case['id']] = dict(query=case['query'],
                        context=case.get('context',''),plan=router.entry,input_digest=router.input_digest,
                        plan_outcome=router.plan_outcome,plan_failure=router.plan_failure,
                        accepted=observed['accepted'],inventory=observed['inventory'],outcome=observed['outcome'])
                except Exception as exc:
                    failure = router.error or type(exc).__name__ + ': ' + str(exc)
                    stop = dict(run=run,case=case['id'],reason=failure,
                        plan_outcome=router.plan_outcome,plan_failure=router.plan_failure)
                    break
                finally:
                    counts = plans['live_calls_by_purpose']
                    for purpose in router.calls:
                        counts[purpose] = counts.get(purpose,0) + (cap.used-before)
        phase_counts['plans'] = cap.used-start
        # Canonical dictionary order even for a valid recording with holes.
        plans['runs'] = {str(r):{c['id']:plans['runs'][str(r)][c['id']]
            for rr,c in order if rr==r and c['id'] in plans['runs'].get(str(r),{})}
            for r in range(1,args.heldout_runs+1) if plans['runs'].get(str(r))}
        plans['complete'] = failure is None
        plans['identity'] = dict(identity,observed_model=sorted({r['observed_model'] for r in audit
            if r.get('purpose')=='work-orchestration' and r.get('observed_model')}) or 'not reported')
        session = dict(max_calls=cap.max_calls,admitted=cap.used,refused=int(cap.refused),
            reached=cap.used>=cap.max_calls,by_phase=phase_counts,stop_reason=failure,
            stop=stop,terms_source=plans['terms_source'],terms_digest=terms_digest,identity=plans['identity'],
            qualification_cache_applied=bool(cached))
        sessions = plans.get('call_budget',{}).get('sessions',[]) + [session]
        budget = dict(sessions=sessions,max_calls=sum(s['max_calls'] for s in sessions),
            admitted=sum(s['admitted'] for s in sessions),refused=sum(s['refused'] for s in sessions),
            reached=any(s['reached'] for s in sessions),
            by_phase={p:sum(s['by_phase'][p] for s in sessions) for p in phase_counts})
        plans.update(call_budget=budget,failure=failure,stop=stop)
        out = Path(args.out).expanduser().resolve()
        out.mkdir(parents=True,exist_ok=True)
        for name,value in (('terms.json',terms),('plans.json',plans)):
            (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(dict(calls=phase_counts,admitted=cap.used,refused=cap.refused,
            cap_reached=session['reached'],complete=plans['complete'],failure=failure)))
        return plans
    finally:
        try:
            if sandbox is not None:
                sandbox.stop()
        finally:
            pool._release_slot(slot)
