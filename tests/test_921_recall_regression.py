"""Model-free executor and recording contracts; never invoke the live entry point by default."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from agentos_eval import recall_pairs as pairs
from agentos_eval.recall_record import PurposeRouter
from agentos_eval.recall_regression import (Executor, fixture_service, fixture_scope, digest,
    apply_terms, TERMS_VERSION, filter_digest, terms_prompt_digest, raw_row, catalogue_from, seed_fixture)
from personal_agent.quickstart_store import QuickStore
from personal_agent import memory_terms as mt
from personal_agent.orchestrator import worker_catalogue

ROOT = Path(__file__).resolve().parents[1]


def tiny():
    return dict(version='recall-pairs/4',owner='recall-fixture',today='2026-03-25',rows=[
        dict(ref='target',key='profile.target',content='needle owner preference'),
        *[dict(ref=f'fill-{i}',key=f'profile.fill.{i}',content='background '+'x'*180) for i in range(100)]],
        cases=[dict(id='one',split='dev',kind='paraphrase',query='request',needed=['target'],traps=[],note='fixture')])


def plan(reply='DIRECT',selector='',worker='codex'):
    return dict(worker=worker,model='',effort='',brief={'notes':'','lens':'','needs':''},reason='fixture',
        reply=reply,recall_query=selector,account_change=False,owner_situation=False,technical_work=False,
        ask_location='',preview=None)


@pytest.fixture
def env(tmp_path):
    data = tiny()
    store = QuickStore(tmp_path/'state')
    service = fixture_service(store)
    with fixture_scope(data):
        refs = seed_fixture(store,data)
    catalogue = worker_catalogue(service)
    return data,store,service,refs,Executor(service,data,refs,catalogue)


def terms_recording(data):
    rows = {r['ref']:dict(terms='',meta=json.loads(mt.build_meta('empty',previous=None,at=1))) for r in data['rows']}
    rows['target'] = dict(terms='selector',meta=json.loads(mt.build_meta('keyed',previous=None,at=1)))
    return dict(version=TERMS_VERSION,fixture_version=data['version'],fixture_digest=digest(data),
        filter_digest=filter_digest(),terms_prompt_digest=terms_prompt_digest(),rows=rows)


@pytest.mark.parametrize('reply,selector,answer,withheld',[
    ('DIRECT','','planner',False),('DIRECT','needle','worker',True),('','needle','worker',False)])
def test_real_executor(env,reply,selector,answer,withheld):
    data,store,service,refs,executor = env
    router = PurposeRouter(plan=plan(reply,selector))
    result = executor.run(data['cases'][0],router.engine)
    assert router.error is None
    assert result['outcome']['final_answer_by'] == answer
    assert result['outcome']['withheld'] == withheld
    assert len(router.calls)==1 and sum(router.calls.values())==1
    if answer == 'worker':
        assert 'target' in result['outcome']['worker_refs']
        assert result['dispatched'][0]['profile']
    assert 'target' not in {i['ref'] for i in result['inventory']}
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0


def test_terms_guard_idempotence_stale_and_digests(env,monkeypatch):
    data,store,_,refs,_ = env
    recording = terms_recording(data)
    apply_terms(store,data,recording,refs)
    apply_terms(store,data,recording,refs)
    assert raw_row(store,refs['target'])['retrieval_terms'] == 'selector'
    for key in ('fixture_digest','filter_digest','terms_prompt_digest'):
        bad = copy.deepcopy(recording); bad[key]='bad'
        with pytest.raises(ValueError,match='differs'):
            apply_terms(store,data,bad,refs)
    with store.db() as db:
        db.execute('UPDATE memories SET retrieval_terms_meta=? WHERE id=?', (mt.cleared_meta(None,at=2),refs['target']))
    with pytest.raises(ValueError,match='Stale terms'):
        apply_terms(store,data,recording,refs)


def test_guarded_write_refusal(env,monkeypatch):
    data,store,_,refs,_ = env
    monkeypatch.setattr(store,'write_retrieval_terms',lambda *a,**kw:False)
    with pytest.raises(ValueError,match='Guarded'):
        apply_terms(store,data,terms_recording(data),refs)


def test_inventory_and_outcome_replay_checks(env):
    from agentos_eval.recall_regression import replay_case
    data,_,_,_,executor=env
    p=plan(selector='needle')
    observed=executor.run(data['cases'][0],PurposeRouter(plan=p).engine)
    entry=dict(plan=p,inventory=observed['inventory'],accepted=observed['accepted'],outcome=observed['outcome'])
    replay_case(executor,data['cases'][0],entry)
    bad=copy.deepcopy(entry); bad['inventory']=[]
    with pytest.raises(ValueError,match='inventory A'):
        replay_case(executor,data['cases'][0],bad)
    bad=copy.deepcopy(entry); bad['outcome']['worker_refs']=[]
    with pytest.raises(ValueError,match='outcome'):
        replay_case(executor,data['cases'][0],bad)


@pytest.mark.parametrize('condition', ['steer','preparation','location','early-revision'])
def test_conditions(env,condition,monkeypatch):
    data,_,service,_,executor=env
    recalls=[]
    original=service.profile_section
    def profile(*a,**kw):
        recalls.append(kw.get('recall',True))
        return original(*a,**kw)
    monkeypatch.setattr(service,'profile_section',profile)
    case=dict(data['cases'][0],query='needle' if condition in {'early-revision','steer','preparation'} else 'request')
    p=plan(selector='needle')
    if condition=='location': p['ask_location']='fixture position?'
    result=executor.run(case,PurposeRouter(plan=p).engine,condition=condition)
    assert result['outcome']['final_answer_by'] == ('planner' if condition=='preparation' else 'worker')
    if condition=='steer':
        assert not result['outcome']['withheld']  # only a concurrent steer prevented the reply
    if condition=='preparation':
        assert len(recalls)>=2 and not any(recalls)
        a={(i['ref'],i['digest']) for i in result['inventory']}
        b={(i['ref'],i['digest']) for i in result['outcome']['checked_items'] if i['matched']}
        assert b <= a
        assert 'target' in {i['ref'] for i in result['inventory']}
    if condition=='location':
        assert result['reply']==''
    if condition=='early-revision':
        assert result['early_used']
        assert result['inventory']==result['early_inventory']


def test_api_fixture_dispatch_captures_checked_refresh(env):
    from agentos_eval.recall_regression import FixtureTransport
    from personal_agent.providers import ModelAdapter
    data,_,service,_,_=env
    service.adapter=ModelAdapter(FixtureTransport())
    from unittest import mock
    with mock.patch.object(service.decision_routes,'follow_main_switched',return_value={'state':'skipped'}):
        service.activate_main_ai({'route':'openai'})
    executor=Executor(service,data,env[3],worker_catalogue(service))
    result=executor.run(data['cases'][0],PurposeRouter(plan=plan(selector='needle',worker='openai')).engine)
    assert result['outcome']['withheld']
    assert 'target' in result['outcome']['worker_refs']
    assert result['dispatched'][0]['profile']


def test_recorded_cli_worker_identity_is_routable_in_replay(env,monkeypatch):
    from agentos_eval.recall_regression import FixtureWorker,safe_catalogue,catalogue_from
    from unittest import mock
    data,store,service,refs,_=env
    with mock.patch.object(service.decision_routes,'follow_main_switched',return_value={'state':'skipped'}):
        service.activate_main_ai({'route':'claude-code'})
    recorded=safe_catalogue(worker_catalogue(service),store)
    with mock.patch.object(service.decision_routes,'follow_main_switched',return_value={'state':'skipped'}):
        service.activate_main_ai({'route':'codex'})
    sent=[]; original=FixtureWorker.execute
    def execute(self,engine,*args,**kwargs):
        sent.append(engine)
        return original(self,engine,*args,**kwargs)
    monkeypatch.setattr(FixtureWorker,'execute',execute)
    executor=Executor(service,data,refs,catalogue_from(recorded))
    result=executor.run(data['cases'][0],PurposeRouter(plan=plan(reply='',worker='claude-code')).engine)
    assert result['accepted'] and sent==['claude-code']
