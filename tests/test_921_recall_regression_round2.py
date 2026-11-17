"""Round-2 sandbox wiring and crash recovery, with no model or real CLI calls."""
import copy
import json
from pathlib import Path
from unittest import mock

import pytest

from agentos_eval import cli, judgments, recall_record as record
from agentos_eval.recall_regression import (
    Executor, fixture_service, install_fixture_workers, layer,
    seed_fixture, validate_plans, validate_terms,
)
from agentos_eval.recall_regression_report import report
from personal_agent.decision import DecisionContext, RoutedDecisionEngine
from personal_agent.orchestrator import plan_schema, plan_shape, worker_catalogue
from personal_agent.quickstart_store import QuickStore
from test_decision_routes import ServiceFixture
from test_921_recall_regression import tiny, plan
from test_921_recall_regression_record import args_for, fake_engine, run_fake


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr('agentos_eval.paths.eval_home',lambda:tmp_path/'eval-home')
    monkeypatch.setattr(cli,'git_head',lambda:'fixture-head')


def forbidden(*args, **kwargs):
    raise AssertionError('Real worker, login, model or network reached')


class TestConfiguredCliRecorder(ServiceFixture):
    def configured(self, store=None):
        if store is not None:
            self.store = store
        service = self.service()
        # Reuse the same shipped activation + fake qualification surfaces as
        # test_eval_judgments.RequalificationTests and the synthetic builder.
        service.activate_decision_route(dict(transport='subscription_cli',engine='codex',
            model_policy='explicit',model='gpt-5.6-terra'))
        for key in ('decision_route','decision_cli_capabilities','decision_route_checks','engine_login'):
            self.assertTrue(service.store.config(key),key)
        return service

    def test_configured_cli_survives_activation_and_work_status(self):
        service = self.configured()
        # Offer a CLI worker as well, with the shared fake login/probe runner.
        # install_fixture_workers itself must activate only the API route.
        service.activate_main_ai({'route':'codex'})
        original = service.execution_adapter
        route = copy.deepcopy(service.store.config('decision_route'))
        data = tiny()
        receipts = []
        run_one = service.run_one
        def run():
            value = run_one()
            with service.store.db() as db:
                rows = [dict(r) for r in db.execute('SELECT status,response,error FROM jobs')]
                self.assertTrue(all(r['status'] != 'failed' for r in rows),rows)
                receipts.extend(r['response'] for r in rows)
            return value
        snapshot = service.worker_profile_snapshot
        def status_before_plan(*args, **kwargs):
            self.assertIs(service.execution_adapter,original)
            self.assertEqual(service.decision_routes.status()['active']['engine'],'codex')
            return snapshot(*args,**kwargs)
        with mock.patch.object(original,'execute',forbidden), \
                mock.patch.object(original,'login_status',forbidden), \
                mock.patch('subprocess.Popen',side_effect=forbidden), \
                mock.patch('urllib.request.urlopen',side_effect=forbidden), \
                mock.patch.object(service,'run_one',run), \
                mock.patch.object(service,'worker_profile_snapshot',status_before_plan):
            install_fixture_workers(service)
            self.assertIs(service.execution_adapter,original)
            self.assertEqual(service.main_ai.current(),'openai')
            self.assertEqual(service.store.config('decision_route'),route)
            refs = seed_fixture(service.store,data)
            def resolve():
                self.assertIs(service.execution_adapter,original)
                service.decision_routes.status()
                return fake_engine([])
            # A fake live engine through the same purpose/admission boundary.
            live = RoutedDecisionEngine(resolve,budget=lambda:judgments.CallCap(1))
            router = record.PurposeRouter(live=live,service=service,execution=original)
            executor = Executor(service,data,refs,worker_catalogue(service))
            result = executor.run(data['cases'][0],router.engine)
            self.assertIsNone(router.error)
            self.assertEqual(result['outcome']['final_answer_by'],'worker')
            self.assertIn('fixture worker answer',receipts)
            self.assertIs(service.execution_adapter,original)

    def test_dry_run_copies_configured_seed_and_never_resolves_live(self):
        root = Path(self.root)
        args = args_for(root)
        self.configured(QuickStore(Path(args.seed)))
        seed_db = Path(args.seed)/'private/quickstart.db'
        seed_bytes = seed_db.read_bytes()
        args.dry_run, args.dry_run_cases, args.max_calls = True, 1, None
        constructed = []
        def factory(path):
            # Same factory boundary and real Sandbox.prepare copy, using the
            # shared fixture adapter's fake finder/qualification surfaces.
            self.store = QuickStore(path)
            service = self.service()
            constructed.append((path,copy.deepcopy(self.store.config('decision_route'))))
            service.execution_adapter.login_status = forbidden
            service.execution_adapter.execute = forbidden
            service.decision_routes._cli_engine = forbidden
            service.use_decision_engine(RoutedDecisionEngine(forbidden))
            run_one = service.run_one
            def run():
                value = run_one()
                with service.store.db() as db:
                    rows = [dict(r) for r in db.execute('SELECT status,response,error FROM jobs')]
                    self.assertTrue(all(r['status'] != 'failed' for r in rows),rows)
                return value
            service.run_one = run
            return service
        with mock.patch.object(judgments,'service_factory',factory), \
                mock.patch.object(judgments,'requalify_route',side_effect=forbidden), \
                mock.patch('subprocess.Popen',side_effect=forbidden), \
                mock.patch('urllib.request.urlopen',side_effect=forbidden):
            result = record.record(args)
        self.assertEqual(len(constructed),1)
        self.assertNotEqual(constructed[0][0],Path(args.seed))
        self.assertEqual(constructed[0][1]['transport'],'subscription_cli')
        self.assertEqual(constructed[0][1]['requested_model'],'gpt-5.6-terra')
        self.assertEqual(seed_bytes,seed_db.read_bytes())
        terms = json.loads((args.out/'terms.json').read_text())
        self.assertEqual(terms['calls'],0)
        self.assertTrue(terms['manifests'])
        self.assertEqual(terms['status_counts']['rows']['no_meta'],0)
        self.assertTrue(all(m['result']=='ok' for m in terms['manifests']))
        self.assertEqual(list(result['runs']['1']),['one','held-gpt','addition'])
        self.assertEqual(result['call_budget']['by_phase'],dict(qualification=0,terms=0,plans=0))
        self.assertEqual(result['call_budget']['admitted'],0)
        self.assertEqual(result['live_calls_by_purpose'],{'work-orchestration':0})
        self.assertIsNone(result['failure'])
        for recording in (terms,result):
            self.assertEqual(recording['evidence_class'],'dry-run (no model call)')
            self.assertFalse(recording['complete'])
        base = json.loads(args.fixture.read_text())
        additions = json.loads(args.additions.read_text())
        data = layer(base,additions)
        # Even changing completeness cannot turn dry-run data into evidence.
        with pytest.raises(ValueError,match='Dry-run'):
            validate_plans(dict(result,complete=True),data,dict(terms,complete=True))
        with pytest.raises(ValueError,match='Dry-run'):
            report(base,additions,terms,result)
        args.dry_run, args.max_calls, args.terms = False, 1, args.out/'terms.json'
        with pytest.raises(ValueError,match='complete'):
            record.load_inputs(args,data,record.run_order(base,additions,3))


@pytest.mark.parametrize('maximum',[None,0])
def test_dry_run_start_and_cli_flags(tmp_path,monkeypatch,maximum):
    args = args_for(tmp_path)
    args.dry_run, args.dry_run_cases, args.max_calls = True, 6, maximum
    assert record.validate_start(args)==Path(args.seed)
    captured = []
    monkeypatch.setattr(record,'record',lambda args:captured.append(args))
    argv = ['recall-record','--seed',args.seed,'--dry-run','--dry-run-cases','2','--out',str(args.out)]
    if maximum is not None:
        argv += ['--max-calls',str(maximum)]
    assert cli.main(argv)==0
    assert captured[0].dry_run and captured[0].dry_run_cases==2


@pytest.mark.parametrize('bad',['positive-cap','negative-cap','cases','marker','repo-out','terms','resume'])
def test_dry_run_refusals(tmp_path,bad):
    args = args_for(tmp_path)
    args.dry_run, args.dry_run_cases, args.max_calls = True, 6, None
    if bad=='positive-cap': args.max_calls=1
    if bad=='negative-cap': args.max_calls=-1
    if bad=='cases': args.dry_run_cases=0
    if bad=='marker': (Path(args.seed)/'synthetic-seed.json').unlink()
    if bad=='repo-out': args.out=Path(__file__).resolve().parents[1]
    if bad=='terms': args.terms=tmp_path/'terms.json'
    if bad=='resume': args.resume=tmp_path/'plans.json'
    with pytest.raises(ValueError): record.validate_start(args)


@pytest.mark.parametrize('phase',['qualification','terms','plans'])
@pytest.mark.parametrize('error',[LookupError,KeyboardInterrupt])
def test_exception_writes_spent_calls_before_reraise(tmp_path,monkeypatch,phase,error):
    args = args_for(tmp_path)
    if phase=='qualification':
        def requalify(service,cap):
            cap.spend_turn()
            raise error('qualification crashed')
        monkeypatch.setattr(judgments,'requalify_route',requalify)
        # run_fake injects requalify; use the real sandbox + injected service.
        def run():
            def factory(path):
                service=fixture_service(QuickStore(path))
                service.use_decision_engine(fake_engine([]))
                return service
            return record.record(args,factory=factory)
    else:
        if phase=='terms':
            from personal_agent.owner_model import Upkeep
            original = Upkeep.finish
            def finish(self,*a,**kw):
                original(self,*a,**kw)
                raise error('terms crashed')
            monkeypatch.setattr(Upkeep,'finish',finish)
        else:
            original = Executor.run
            calls = []
            def execute(self,*a,**kw):
                result = original(self,*a,**kw)
                calls.append(1)
                if len(calls)==2:
                    raise error('plans crashed')
                return result
            monkeypatch.setattr(Executor,'run',execute)
        run = lambda:run_fake(args,[])
    with pytest.raises(error,match=phase+' crashed'):
        run()
    terms = json.loads((args.out/'terms.json').read_text())
    plans = json.loads((args.out/'plans.json').read_text())
    session = plans['call_budget']['sessions'][-1]
    assert not plans['complete']
    assert session['stop_reason']==f'{error.__name__}: {phase} crashed'
    assert plans['stop']['phase']==phase
    assert sum(session['by_phase'].values())==session['admitted']
    if phase=='plans':
        assert terms['complete'] and session['by_phase']['plans']==2
        assert list(plans['runs']['1'])==['one']
        assert plans['live_calls_by_purpose']=={'work-orchestration':2}
    else:
        assert not terms['complete'] and plans['runs']=={}
        assert terms['failure']==session['stop_reason']
        assert session['admitted']==(1 if phase=='qualification' else 2)
        if phase=='terms':
            data = layer(json.loads(args.fixture.read_text()),json.loads(args.additions.read_text()))
            validate_terms(terms,data)
            assert terms['calls']==1 and terms['manifests']
            assert terms['status_counts']['rows']['keyed']>0
            args.terms=args.out/'terms.json'
            with pytest.raises(ValueError,match='complete'):
                record.load_inputs(args,data,[])


@pytest.mark.parametrize('location,preview',[(False,False),(True,False),(False,True),(True,True)])
def test_dry_run_answers_match_product_schemas(tmp_path,location,preview):
    from personal_agent.owner_model import TERMS_SCHEMA, terms_answer
    service=fixture_service(QuickStore(tmp_path/'state'))
    catalogue=worker_catalogue(service)
    engine=record.dry_run_engine(catalogue)
    schema=plan_schema(catalogue.available(),ask_location=location,preview=preview)
    answer=engine.structured(DecisionContext('work-orchestration',{}),'?',schema).data
    assert set(answer)==set(schema['properties'])
    assert plan_shape(answer)
    assert answer['worker'] in schema['properties']['worker']['enum']
    if preview:
        assert set(answer['preview'])==set(schema['properties']['preview']['required'])
    terms=engine.structured(DecisionContext('memory-terms',dict(notes='1. first note\n2. second note')),
        '?',TERMS_SCHEMA).data
    assert terms_answer(terms,2)


def test_cleanup_exception_preserves_calls_and_marks_plans_incomplete(tmp_path,monkeypatch):
    from agentos_eval.sandbox import Sandbox
    args=args_for(tmp_path)
    args.requalify=False
    def factory(path):
        service=fixture_service(QuickStore(path))
        service.use_decision_engine(fake_engine([]))
        return service
    original=Sandbox.stop
    def stop(self):
        original(self)
        raise OSError('cleanup crashed')
    monkeypatch.setattr(Sandbox,'stop',stop)
    with pytest.raises(OSError,match='cleanup crashed'):
        record.record(args,factory=factory)
    terms=json.loads((args.out/'terms.json').read_text())
    plans=json.loads((args.out/'plans.json').read_text())
    assert terms['complete'] and not plans['complete']
    assert plans['stop']==dict(phase='cleanup',reason='OSError: cleanup crashed')
    assert plans['call_budget']['admitted']==14
    assert plans['call_budget']['by_phase']==dict(qualification=0,terms=6,plans=8)


def test_isolated_worker_execute_is_blocked(tmp_path,monkeypatch):
    from types import SimpleNamespace
    data = tiny()
    service = fixture_service(QuickStore(tmp_path/'state'))
    refs = seed_fixture(service.store,data)
    install_fixture_workers(service)
    catalogue=worker_catalogue(service)
    isolated = SimpleNamespace(execute=forbidden)
    service.isolated_engine_adapter=isolated
    executor = Executor(service,data,refs,catalogue)
    router = record.PurposeRouter(plan=plan(reply='',worker='openai'))
    # API dispatch reaches the same guarded scope without sidecar setup.
    original_prepare=service._prepare_worker_turn
    def prepare(run):
        with pytest.raises(RuntimeError,match='Real worker'):
            isolated.execute()
        return original_prepare(run)
    monkeypatch.setattr(service,'_prepare_worker_turn',prepare)
    executor.run(data['cases'][0],router.engine)
