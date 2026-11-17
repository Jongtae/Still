"""Fixture isolation, author facets and gate attribution, without model calls."""
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import recall_pairs as pairs, recall_arms as arms  # noqa: E402
from agentos_eval.recall_regression import layer, digest, seed_fixture, fixture_service, Executor, apply_terms  # noqa: E402
from agentos_eval.recall_regression_report import facet_report, gate_row, markdown  # noqa: E402
from personal_agent.orchestrator import worker_catalogue  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402
from agentos_eval.recall_record import PurposeRouter  # noqa: E402
from test_recall_pairs_v3 import assert_no_needed_overlap, terms, stem, FUNCTION_WORDS  # noqa: E402
from test_921_recall_regression import tiny, plan  # noqa: E402

BASE=ROOT/'evals/recall/recall-pairs-v4.json'
ADD=BASE.parent/'recall-gate-dev-1.json'


def test_v4_pinned_and_builder_repeatable():
    assert hashlib.sha256(BASE.read_bytes()).hexdigest()=='06c7df08c4c9e97714c9d33c024e0464657f0173aed7ceda3a3e28f0881ea9fa'
    base=pairs.load(BASE)
    assert digest([c for c in base['cases'] if c['split']=='heldout'])=='d5ad6cea6a611244091c03e476e836def339722842d7588a6952684d1683671c'
    spec=importlib.util.spec_from_file_location('gate_builder',ADD.parent/'build_gate_dev.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert module.render()==module.render()==ADD.read_bytes()
    additions=json.loads(ADD.read_text())
    assert len(additions['rows'])==28 and len(additions['cases'])==4
    assert all(20<=len(r['content'])<=120 and '\n' not in r['content'] for r in additions['rows'])
    layered=layer(base,additions)
    assert layered['cases'][:72]==base['cases']
    assert layered['rows'][28:]==base['rows']
    assert_no_needed_overlap(layered)
    # Reuse the exact v3 forward-leakage scan on every addition row against
    # EVERY v4 meaning case, including keys and unbounded tokens.
    probe=copy.deepcopy(base)
    probe['rows']+=additions['rows']
    for case in probe['cases']:
        if case['kind'] in {'paraphrase','cross-language','short-follow-up'}:
            case['needed'] += [r['ref'] for r in additions['rows']]
    assert_no_needed_overlap(probe)
    words=lambda cases:{stem(w) for c in cases for field in ('query','context') for w in terms(c.get(field,''))
                         if w not in FUNCTION_WORDS and stem(w) not in FUNCTION_WORDS}
    old,new=words(base['cases']),words(additions['cases'])
    shared=[(a,b) for a in old for b in new if a.startswith(b) or b.startswith(a)]
    assert not shared,shared


def test_addition_rows_have_no_noise_hits_on_any_v4_case(tmp_path):
    base=pairs.load(BASE); additions=json.loads(ADD.read_text()); data=layer(base,additions)
    store=QuickStore(tmp_path/'state'); refs=seed_fixture(store,data)
    extra={refs[r['ref']] for r in additions['rows']}
    for name in ('lexical','context-concat','lexical-context','prefix-lexical'):
        retrieve=arms.ARMS[name](store,data)
        for case in base['cases']:
            assert not extra & set(retrieve(case['query'],case.get('context',''))),(name,case['id'])


def test_dev_case_checks_use_executor_inventories(tmp_path):
    base=pairs.load(BASE); additions=json.loads(ADD.read_text()); data=layer(base,additions)
    store=QuickStore(tmp_path/'state'); service=fixture_service(store); refs=seed_fixture(store,data)
    executor=Executor(service,data,refs,worker_catalogue(service))
    selectors={'gate-second':'celesta','gate-saturated':'octant','gate-noise':'marquetry','gate-annotated':'도편수첩'}
    for case in additions['cases']:
        observed=executor.run(case,PurposeRouter(plan=plan(selector=selectors[case['id']])).engine)
        row=gate_row(case,dict(plan=plan(selector=selectors[case['id']]),accepted=observed['accepted']),observed,data,refs)
        assert observed['outcome']['withheld'],case['id']
        if case['id']=='gate-noise':
            assert row['noise_only_withheld'] and row['needed_hit']==0
        elif case['id']=='gate-annotated':
            assert row['a']['needed_hit']==1
            assert any(i['ref']=='gate-annotated-row' and i['annotated'] for i in observed['outcome']['worker_items'])
        else:
            assert row['a']['needed_hit']==0 and row['worker']['needed_hit']==1


def test_facets_attribution_noise_and_sample_majority():
    data=tiny()
    data['cases'] += [dict(data['cases'][0],id='gpt',split='heldout',note='[author:gpt] [particle] fixture'),
                     dict(data['cases'][0],id='claude',split='heldout',note='[author:claude] fixture')]
    def row(case,answer,hit,withheld=False,noise=False):
        m=dict(needed_hit=hit,needed_total=1,trap_hits=0,noise_hits=int(noise))
        return dict(id=case['id'],split=case['split'],kind=case['kind'],**m,final_answer_by=answer,
            a=dict(m,needed_hit=int(answer=='planner')),worker=m,withheld=withheld,no_reply=False,
            noise_only_withheld=noise,receipt_counts={},plan_not_accepted=False)
    cases=data['cases']
    run1=[row(cases[0],'planner',1),row(cases[1],'worker',0,True,True),row(cases[2],'worker',1,True)]
    run2=[row(cases[1],'worker',1,True),row(cases[2],'planner',1)]
    report=facet_report(data,[run1,run2,run2])
    assert report['facets']['overall']['majority']['cases']==3
    assert report['facets']['dev']['majority']['planner_cases']==1
    assert report['facets']['heldout [author:gpt]']['majority']['worker_needed_hit']==1
    assert report['facets']['heldout [author:gpt]']['per_run'][0]['noise_only_withheld']==1
    assert report['facets']['heldout [author:claude]']['majority']['worker_cases']==1
    gpt=next(c for c in report['cases'] if c['id']=='gpt')
    assert gpt['majority'] and len(gpt['per_run'])==3


def test_noise_attribution_must_match_receipt(tmp_path):
    data=tiny(); store=QuickStore(tmp_path/'state'); service=fixture_service(store)
    refs=seed_fixture(store,data); executor=Executor(service,data,refs,worker_catalogue(service))
    observed=executor.run(data['cases'][0],PurposeRouter(plan=plan(selector='needle')).engine)
    assert observed['outcome']['withheld']
    observed['outcome']['receipt_counts']['new']+=1
    with pytest.raises(ValueError,match='Fresh-item count'):
        gate_row(data['cases'][0],dict(accepted=True),observed,data,refs)


def test_decided_not_accepted_keeps_worker_attribution_and_is_listed(tmp_path):
    data=tiny(); store=QuickStore(tmp_path/'state'); service=fixture_service(store)
    refs=seed_fixture(store,data); executor=Executor(service,data,refs,worker_catalogue(service))
    router=PurposeRouter(plan=plan(worker='not-in-catalogue'))
    observed=executor.run(data['cases'][0],router.engine)
    assert router.plan_outcome=='decided' and not observed['accepted']
    row=gate_row(data['cases'][0],dict(accepted=False),observed,data,refs)
    assert row['final_answer_by']=='worker' and row['plan_not_accepted']
    results=facet_report(data,[[row]])
    for facet in ('overall','dev'):
        summary=results['facets'][facet]
        for m in (summary['majority'],*summary['per_run']):
            assert m['plans_not_accepted']==1 and m['plans_not_accepted_cases']==['one']
            assert m['worker_cases']==1
    for author in ('gpt','claude'):
        m=results['facets']['heldout [author:'+author+']']['majority']
        assert m['plans_not_accepted']==0 and m['plans_not_accepted_cases']==[]


def test_terms_status_and_manifest_counts():
    from agentos_eval.recall_regression import terms_status_counts
    from test_921_recall_regression import terms_recording
    data=tiny(); recording=terms_recording(data)
    for ref,status in zip(list(recording['rows'])[:6],('keyed','empty','failed','oversize','cleared',None)):
        recording['rows'][ref]['meta']=dict(status=status) if status else None
    recording['manifests']=[dict(result=r) for r in ('ok','ok','rejected','stale','unsent','another-result')]
    result=terms_status_counts(recording)
    assert result['rows']==dict(keyed=1,empty=96,failed=1,oversize=1,cleared=1,**{'no_meta':1})
    assert result['manifests']==dict(ok=2,rejected=1,stale=1,unsent=1,**{'another-result':1})
