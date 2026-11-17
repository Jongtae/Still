"""The live recorder is exercised only with injected fixture engines/sandboxes."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import recall_record as record  # noqa: E402
from agentos_eval.recall_regression import (fixture_service, seed_fixture, fixture_scope, digest, layer,  # noqa: E402
    validate_plans, validate_terms, safe_catalogue, catalogue_from, prompt_digest, Executor, replay_case)
from agentos_eval.judgments import CallCap  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402
from personal_agent.decision import FixtureDecisionEngine, StructuredDecision, DecisionContext, OUTCOME_DECIDED, DecisionConfidence, RoutedDecisionEngine  # noqa: E402
from personal_agent import owner_model as om  # noqa: E402
from test_921_recall_regression import tiny, plan, terms_recording  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_eval_home(tmp_path,monkeypatch):
    monkeypatch.setattr('agentos_eval.paths.eval_home',lambda:tmp_path/'eval-home')


def fake_engine(seen, reject_first=False):
    def answer(context,question,schema):
        seen.append(context.purpose)
        if context.purpose=='work-orchestration':
            data=plan(selector='needle')
        else:
            assert context.purpose=='memory-terms'
            data={'notes':[dict(n=n,terms='expanded') for n in range(1,len(context.facts['notes'].splitlines())+1)]}
            if reject_first and len(seen)==1:
                data={'notes':[]}
        return StructuredDecision(OUTCOME_DECIDED,data,DecisionConfidence(1,sent=True,route='fixture',model='fixture'))
    return FixtureDecisionEngine(structured=answer)


def args_for(tmp_path):
    seed=tmp_path/'seed'; seed.mkdir()
    QuickStore(seed)
    (seed/'synthetic-seed.json').write_text(json.dumps({'owner_data':'none'}))
    base=tiny()
    # Two heldout authors prove the facet/run ordering without using v4 live.
    base['cases'] += [dict(base['cases'][0],id='held-'+author,split='heldout',note='[author:'+author+'] fixture')
                      for author in ('gpt','claude')]
    addition=dict(version='recall-gate-dev/1',base_digest=digest(base),rows=[
        dict(ref='extra',key='profile.extra',content='extra fixture content')],cases=[
        dict(base['cases'][0],id='addition')])
    fixture=tmp_path/'fixture.json'; fixture.write_text(json.dumps(base))
    additions=tmp_path/'additions.json'; additions.write_text(json.dumps(addition))
    return SimpleNamespace(seed=str(seed),max_calls=20,out=tmp_path/'out',fixture=fixture,additions=additions,
                           src=Path(__file__).resolve().parents[1]/'src',requalify=True,heldout_runs=3)


def run_fake(args, seen, *, engine_factory=None):
    def factory(path):
        service=fixture_service(QuickStore(path))
        service.use_decision_engine((engine_factory or fake_engine)(seen))
        return service
    class Sandbox:
        def __init__(self,*a,**kw): self.data=args.out.parent/('sandbox-'+args.out.name)
        def prepare(self):
            QuickStore(self.data)
            return self.data
        def stop(self): pass
    def requalify(service,cap):
        # Same shared cap even before terms; no fake qualification implementation
        # is used by production, which calls judgments.requalify_route unchanged.
        cap.spend_turn()
    return record.record(args,factory=factory,sandbox_factory=Sandbox,requalify=requalify)


def test_recorder_complete_and_replays(tmp_path):
    args=args_for(tmp_path); seen=[]
    result=run_fake(args,seen)
    assert result['complete'],result['failure']
    assert result['call_budget']['by_phase']==dict(qualification=1,terms=6,plans=8)
    assert result['tokens']=='not observable'
    terms=json.loads((args.out/'terms.json').read_text())
    assert terms['tokens']=='not observable'
    data=layer(json.loads(args.fixture.read_text()),json.loads(args.additions.read_text()))
    validate_plans(result,data,terms)
    for key in ('fixture_digest','terms_digest','plan_prompt_schema_digest'):
        bad=copy.deepcopy(result); bad[key]='bad'
        with pytest.raises(ValueError,match='differs'):
            validate_plans(bad,data,terms)
    assert set(result['catalogue'])=={'workers','default','descriptions'}
    assert result['live_calls_by_purpose']=={'work-orchestration':8}
    assert list(result['runs']['1'])[-1]=='addition'
    assert set(result['runs']['2'])=={'held-gpt','held-claude'}


def test_shared_cap_stops_cleanly(tmp_path):
    args=args_for(tmp_path); args.max_calls=3
    result=run_fake(args,[])
    assert not result['complete'] and result['call_budget']['reached']
    assert result['call_budget']['admitted']==3
    assert result['call_budget']['by_phase']['qualification']==1
    assert result['runs']=={}


@pytest.mark.parametrize('which', ['no-seed','no-marker','owner-marker','no-max','repo-out'])
def test_start_refusals(tmp_path,which):
    args=args_for(tmp_path)
    if which=='no-seed': args.seed=None
    if which=='no-marker': (Path(args.seed)/'synthetic-seed.json').unlink()
    if which=='owner-marker': (Path(args.seed)/'synthetic-seed.json').write_text('{"owner_data":"present"}')
    if which=='no-max': args.max_calls=None
    if which=='repo-out': args.out=Path(__file__).resolve().parents[1]/'evals'
    with pytest.raises(ValueError): record.validate_start(args)


def test_share_lift_scoped_and_product_adaptive_cap(tmp_path):
    data=tiny(); store=QuickStore(tmp_path/'state'); service=fixture_service(store)
    refs=seed_fixture(store,data)
    settings=service.owner_model.settings()
    cap=CallCap(30); seen=[]
    service.use_decision_engine(RoutedDecisionEngine(lambda:fake_engine(seen,reject_first=True),budget=lambda:cap))
    result=record.record_terms(service,data,refs,cap)
    assert result['complete']
    assert [m['cap'] for m in result['manifests'][:4]]==[8,4,8,16]
    assert service.owner_model.settings()==settings
    assert result['share_limit_lift']['daily_calls']==120
    assert all('text' not in m and 'notes' not in m for m in result['manifests'])


def test_router_refuses_non_plan_and_second_plan_before_send():
    sent=[]; cap=CallCap(3)
    router=record.PurposeRouter(live=RoutedDecisionEngine(lambda:fake_engine(sent),budget=lambda:cap),cap=cap)
    with pytest.raises(RuntimeError,match='Non-plan'):
        router.forward(DecisionContext('upkeep',{}),'?',{})
    assert not sent and cap.used==0
    context=DecisionContext('work-orchestration',{})
    router.forward(context,'?',{})
    with pytest.raises(RuntimeError,match='Second live plan'):
        router.forward(DecisionContext('work-orchestration',{}),'?',{})
    assert sent==['work-orchestration'] and cap.used==1


def test_real_worker_adapters_guarded(tmp_path,monkeypatch):
    from personal_agent.providers import ModelAdapter
    from personal_agent.bounded_execution import BoundedExecutionAdapter
    from agentos_eval.recall_regression import FixtureWorker
    data=tiny(); store=QuickStore(tmp_path/'state'); service=fixture_service(store)
    refs=seed_fixture(store,data)
    from personal_agent.orchestrator import worker_catalogue
    executor=Executor(service,data,refs,worker_catalogue(service))
    reached=[]
    def execute(self,*args,**kwargs):
        for call in (lambda:BoundedExecutionAdapter.execute(None,*args,**kwargs),
                     lambda:ModelAdapter(lambda *a:reached.append(a))._send('url',{}, {},60)):
            with pytest.raises(RuntimeError,match='Real worker'):
                call()
        return original(self,*args,**kwargs)
    original=FixtureWorker.execute
    monkeypatch.setattr(FixtureWorker,'execute',execute)
    executor.run(data['cases'][0],record.PurposeRouter(plan=plan(reply='')).engine)
    assert reached==[]


def test_catalogue_secret_and_path_refusals(tmp_path):
    from personal_agent.orchestrator import worker_catalogue
    store=QuickStore(tmp_path/'state'); service=fixture_service(store)
    cat=worker_catalogue(service)
    safe=safe_catalogue(cat,store)
    assert prompt_digest(catalogue_from(safe))==prompt_digest(cat)
    cat.workers[0]['name']='/Users/invented/private'
    with pytest.raises(ValueError,match='local path'): safe_catalogue(cat,store)


def test_run_order_additions_last():
    base=tiny(); addition=dict(cases=[dict(base['cases'][0],id='addition')])
    base['cases'].append(dict(base['cases'][0],id='held',split='heldout'))
    assert [(r,c['id']) for r,c in record.run_order(base,addition,3)]==[(1,'one'),(1,'held'),(2,'held'),(3,'held'),(1,'addition')]


def test_end_to_end_model_free_report_and_replay_resolver(tmp_path):
    from agentos_eval.recall_regression_report import report,markdown
    from agentos_eval.recall_arms import replay_resolver
    args=args_for(tmp_path); plans=run_fake(args,[])
    base=json.loads(args.fixture.read_text()); additions=json.loads(args.additions.read_text())
    terms=json.loads((args.out/'terms.json').read_text()); data=layer(base,additions)
    resolver=replay_resolver(plans,data,terms_recording=terms)
    assert resolver('request','')=='needle'
    result=report(base,additions,terms,plans,code_head='fixture')
    assert len(result['arms'])==7
    assert result['arms']['gate+terms']['facets']['overall']['majority']['cases']==4
    text=markdown(result)
    assert result['evidence']['recorded_inputs']=='fixture (model-free)'
    assert 'fixture (model-free)' in text
    assert 'noise_only_withheld' in text and 'Work-condition checks' in text
    assert 'plans_not_accepted_cases' in text
    assert terms['status_counts']==result['terms_status_counts']
    assert 'Terms status counts:' in text


def test_nested_service_route_uses_shared_cap(tmp_path):
    args=args_for(tmp_path); args.max_calls=3
    # A service has its own RoutedDecisionEngine with a DIFFERENT Work budget.
    # The recorder must adopt its resolver and replace admission with CallCap.
    def factory(path):
        service=fixture_service(QuickStore(path)); ignored=CallCap(1000)
        service.use_decision_engine(RoutedDecisionEngine(lambda:fake_engine([]),budget=lambda:ignored))
        return service
    from agentos_eval.sandbox import Sandbox
    args.requalify=False
    result=record.record(args,factory=factory,
        sandbox_factory=lambda *a,**kw:Sandbox(args.out.parent/'sandboxes'),requalify=lambda *a:None)
    assert result['call_budget']['admitted']==3 and result['call_budget']['reached']
    assert not result['complete']


def test_live_route_resolver_and_guard_keep_original_execution_adapter(tmp_path):
    args=args_for(tmp_path); args.requalify=False
    def factory(path):
        service=fixture_service(QuickStore(path)); original=service.execution_adapter
        def resolve():
            assert service.execution_adapter is original
            return fake_engine([])
        service.use_decision_engine(RoutedDecisionEngine(resolve,budget=lambda:CallCap(100)))
        return service
    from agentos_eval.sandbox import Sandbox
    result=record.record(args,factory=factory,sandbox_factory=lambda *a,**kw:Sandbox(args.out.parent/'sandboxes'))
    assert result['complete'],result['failure']


def test_second_plan_in_a_work_stops_recorder(tmp_path):
    args=args_for(tmp_path); args.requalify=False; seen=[]
    def factory(path):
        service=fixture_service(QuickStore(path)); service.use_decision_engine(fake_engine(seen))
        original=service._prepare_worker_turn
        def twice(run):
            value=original(run)
            service.decision_engine.structured(DecisionContext('work-orchestration',{}),'?',{})
            return value
        service._prepare_worker_turn=twice
        return service
    from agentos_eval.sandbox import Sandbox
    result=record.record(args,factory=factory,sandbox_factory=lambda *a,**kw:Sandbox(args.out.parent/'sandboxes'))
    assert not result['complete'] and 'Second live plan' in result['failure']
    assert seen.count('work-orchestration')==1


def test_fallback_catalogue_without_another_live_probe(tmp_path):
    from personal_agent.quickstart_service import AgentService
    from personal_agent.providers import ModelAdapter
    from personal_agent.subscription_engines import SubscriptionEngines
    from agentos_eval.recall_regression import FixtureWorker,FixtureTransport
    from agentos_eval.sandbox import Sandbox
    args=args_for(tmp_path); args.requalify=False
    def factory(path):
        service=AgentService(QuickStore(path),adapter=ModelAdapter(FixtureTransport()),execution_adapter=FixtureWorker(),
            subscription_engines=SubscriptionEngines(finder=lambda _: '/fixture/cli',clock=lambda:1))
        engine=fake_engine([])
        original=engine._structured
        def answer(context,question,schema):
            result=original(context,question,schema)
            if context.purpose=='work-orchestration': result.data['worker']='fixture'
            return result
        engine._structured=answer
        service.use_decision_engine(engine)
        assert not service.main_ai.current()
        return service
    result=record.record(args,factory=factory,sandbox_factory=lambda *a,**kw:Sandbox(args.out.parent/'sandboxes'))
    assert result['complete'],result['failure']
    assert result['catalogue']['default']=='fixture'
    assert 'fallback _runtime_catalogue' in result['catalogue_source']


@pytest.mark.parametrize('outcome', ['provider_unavailable','malformed','timeout','context_rejected'])
def test_non_decided_plan_stops_without_entry(tmp_path,outcome):
    args=args_for(tmp_path)
    def engine_factory(seen):
        engine=fake_engine(seen)
        original=engine._structured
        def answer(context,question,schema):
            if context.purpose=='work-orchestration':
                seen.append(context.purpose)
                return StructuredDecision(outcome,confidence=DecisionConfidence(sent=True))
            return original(context,question,schema)
        engine._structured=answer
        return engine
    seen=[]
    result=run_fake(args,seen,engine_factory=engine_factory)
    assert not result['complete'] and result['runs']=={}
    assert result['stop']['case']=='one'
    assert result['stop']['plan_outcome']==outcome
    assert result['stop']['plan_failure']==outcome
    assert seen.count('work-orchestration')==1
    assert result['call_budget']['sessions'][0]['stop_reason']==result['failure']
    assert json.loads((args.out/'plans.json').read_text())==result


def test_router_records_content_free_failure_reason():
    audit=[]
    def answer(context,question,schema):
        audit.append(dict(purpose=context.purpose,failure='usage-limit'))
        return StructuredDecision('provider_unavailable')
    router=record.PurposeRouter(live=FixtureDecisionEngine(structured=answer),audit=audit)
    router.structured(DecisionContext('work-orchestration',{}),'?',{})
    assert router.plan_outcome=='provider_unavailable' and router.plan_failure=='usage-limit'
    assert router.entry is None


def test_reuse_terms_and_resume_match_uninterrupted(tmp_path):
    args=args_for(tmp_path); args.max_calls=10
    facts=[]
    def engine_factory(seen):
        engine=fake_engine(seen); original=engine._structured
        def answer(context,question,schema):
            if context.purpose=='work-orchestration': facts.append(copy.deepcopy(context.facts))
            return original(context,question,schema)
        engine._structured=answer
        return engine
    stopped=run_fake(args,[],engine_factory=engine_factory)
    assert not stopped['complete'] and len(stopped['runs']['1'])==3
    terms_path=args.out/'terms.json'
    terms_bytes=terms_path.read_bytes()
    original_terms=json.loads(terms_bytes)
    resumed_args=copy.copy(args)
    resumed_args.out=tmp_path/'resumed'; resumed_args.resume=args.out/'plans.json'
    resumed_args.max_calls=5; resumed_args.requalify=False
    seen=[]; resumed=run_fake(resumed_args,seen,engine_factory=engine_factory)
    assert resumed['complete'],resumed['failure']
    assert seen==['work-orchestration']*5
    full_args=copy.copy(args)
    full_args.out=tmp_path/'full'; full_args.terms=terms_path
    full_args.requalify=False; full_args.max_calls=8
    seen=[]; full=run_fake(full_args,seen,engine_factory=engine_factory)
    assert full['complete'],full['failure']
    assert facts[:8]==facts[8:], [(i,k,a.get(k),b.get(k)) for i,(a,b) in enumerate(zip(facts[:8],facts[8:])) for k in a.keys()|b.keys() if a.get(k)!=b.get(k)]
    assert full['runs']==resumed['runs']
    assert [list(v) for v in full['runs'].values()]==[list(v) for v in resumed['runs'].values()]
    assert seen==['work-orchestration']*8
    assert terms_path.read_bytes()==terms_bytes
    for result,output in ((resumed,resumed_args.out),(full,full_args.out)):
        assert result['terms_source']=='reused'
        assert result['terms_digest']==digest(original_terms)
        assert json.loads((output/'terms.json').read_text())==original_terms
        assert result['call_budget']['sessions'][-1]['by_phase']['terms']==0
    budget=resumed['call_budget']
    assert len(budget['sessions'])==2
    assert budget['admitted']==15 and budget['max_calls']==15
    assert budget['by_phase']==dict(qualification=1,terms=6,plans=8)
    assert budget['sessions'][0]['stop_reason']=='Shared call cap reached'
    assert budget['sessions'][1]['stop_reason'] is None
    assert budget['sessions'][1]['max_calls']==5


@pytest.mark.parametrize('binding',['fixture_digest','terms_digest','catalogue','plan_outcome'])
def test_resume_refuses_changed_bindings_before_calls(tmp_path,binding):
    args=args_for(tmp_path); args.max_calls=8
    stopped=run_fake(args,[])
    if binding=='catalogue':
        stopped['catalogue']['workers'][0]['name']='a different public name'
        stopped['plan_prompt_schema_digest']=prompt_digest(catalogue_from(stopped['catalogue']))
    elif binding=='plan_outcome':
        stopped['runs']['1']['one']['plan_outcome']='malformed'
    else:
        stopped[binding]='different'
    args.resume=args.out/'plans.json'
    args.resume.write_text(json.dumps(stopped))
    args.out=tmp_path/'resume'
    seen=[]
    with pytest.raises(ValueError,match='differs|not decided'):
        run_fake(args,seen)
    assert not seen


@pytest.mark.parametrize('bad',['fixture_digest','filter_digest','terms_prompt_digest','complete'])
def test_reuse_refuses_invalid_or_incomplete_terms_before_calls(tmp_path,bad):
    args=args_for(tmp_path)
    data=layer(json.loads(args.fixture.read_text()),json.loads(args.additions.read_text()))
    terms=dict(terms_recording(data),complete=True,tokens='not observable')
    terms[bad]=False if bad=='complete' else 'different'
    args.terms=tmp_path/'terms.json'; args.terms.write_text(json.dumps(terms))
    seen=[]
    with pytest.raises(ValueError,match='differs|complete'):
        run_fake(args,seen)
    assert not seen


def test_live_engine_clock_and_fixture_facts_are_separate(tmp_path):
    import time
    from datetime import datetime, timezone
    args=args_for(tmp_path); args.requalify=False
    fixture_now=datetime(2026,3,25,12,tzinfo=timezone.utc).timestamp()
    boundaries=[]; purposes=[]
    def engine_factory(seen):
        engine=fake_engine(seen); original=engine._structured
        def answer(context,question,schema):
            # Both phases see the real function, including admission/caller work.
            assert abs(time.time()-record.real_time()) < 5
            assert time.time()!=fixture_now
            purposes.append(context.purpose)
            if context.purpose=='work-orchestration':
                assert '2026-03-25' in json.dumps(context.facts,ensure_ascii=False)
            return original(context,question,schema)
        engine._structured=answer
        return engine
    # Observe immediately around the proxy: restoration must also nest correctly
    # through record_terms -> fixture_scope, not merely at the end of a Work.
    original=record.RealClockEngine.structured
    def checked(self,*a,**kw):
        assert time.time()==fixture_now
        value=original(self,*a,**kw)
        assert time.time()==fixture_now
        boundaries.append(time.time())
        return value
    from unittest import mock
    with mock.patch.object(record.RealClockEngine,'structured',checked):
        result=run_fake(args,[],engine_factory=engine_factory)
    assert result['complete'],result['failure']
    assert set(purposes)=={'memory-terms','work-orchestration'}
    assert len(boundaries)==result['call_budget']['admitted']
    assert time.time()!=fixture_now


def test_resume_keeps_sandbox_binding_and_cached_qualification(tmp_path):
    from agentos_eval.sandbox import Sandbox
    args=args_for(tmp_path); args.max_calls=10
    paths=[]
    def factory(path):
        paths.append(path)
        service=fixture_service(QuickStore(path)); service.use_decision_engine(fake_engine([]))
        return service
    def sandbox_factory(root,**kw):
        # Empty synthetic seed substitute; use real stable-slot prepare/stop.
        return Sandbox(root,seed=args.seed,slot=kw['slot'])
    def requalify(service,cap):
        if not service.store.config('decision_route_checks',{}).get('cache_test'):
            cap.spend_turn()
            service.store.put('decision_route_checks',dict(cache_test=True))
    first=record.record(args,factory=factory,sandbox_factory=sandbox_factory,requalify=requalify)
    assert not first['complete']
    args.resume=args.out/'plans.json'; args.out=tmp_path/'resumed'; args.max_calls=2
    second=record.record(args,factory=factory,sandbox_factory=sandbox_factory,requalify=requalify)
    assert paths[0]==paths[1]
    assert second['call_budget']['sessions'][1]['qualification_cache_applied']
    assert second['call_budget']['sessions'][1]['by_phase']==dict(qualification=0,terms=0,plans=2)
    # A further resume from the NEW output still uses the original bound slot.
    args.resume=args.out/'plans.json'; args.out=tmp_path/'resumed-again'; args.max_calls=3
    third=record.record(args,factory=factory,sandbox_factory=sandbox_factory,requalify=requalify)
    assert third['complete'] and paths==[paths[0]]*3
    assert third['call_budget']['sessions'][2]['by_phase']['qualification']==0


def test_live_clock_restored_after_caller_exception():
    import time
    cap=CallCap(1)
    def answer(context,question,schema):
        assert time.time is record.real_time
        raise TimeoutError('fixture timeout')
    engine=RoutedDecisionEngine(lambda:record.RealClockEngine(FixtureDecisionEngine(structured=answer)),budget=lambda:cap)
    with fixture_scope(tiny()) as now:
        with pytest.raises(TimeoutError,match='fixture timeout'):
            engine.structured(DecisionContext('memory-terms',{}),'?',{})
        assert time.time()==now and cap.used==1
