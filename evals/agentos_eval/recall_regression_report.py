"""Model-free #921 replay report; no live imports are invoked here."""
import json
import tempfile
from pathlib import Path

from . import recall_pairs as pairs, recall_arms as arms
from .recall_report import Arm, run_arm, _majority
from .recall_regression import (digest, validate_plans, apply_terms, seed_fixture,
    fixture_service, catalogue_from, Executor, replay_case, compose, terms_status_counts, load_recording)


def facets(case):
    import re
    return re.findall(r'\[([^]]+)\]',re.match(r'(?:\[[^]]+\]\s*)*',case['note'])[0])


def counts(case, items, refs):
    returned = {i['ref'] if isinstance(i,dict) else i for i in items}
    row = pairs.run(lambda query,context:[refs[ref] for ref in returned], [case], refs)['rows'][0]
    return {k:row[k] for k in ('needed_hit','needed_total','trap_hits','noise_hits')}


def gate_row(case, entry, result, data, refs):
    outcome = result['outcome']
    a = counts(case,result['inventory'],refs)
    b = counts(case,outcome['worker_items'],refs)
    # Gate-cost attribution uses the actual matched refresh items and A, with the gate's own
    # rule since #965: only the best-ranked match of each kind (profile, older record) counts.
    # The product receipt decides; its `new` count cross-checks this attribution.
    seen = {(i['ref'],i['digest']):i for i in result['inventory']}
    ranked = [i for i in outcome['checked_items'] if i['matched'] and i.get('rank')]
    best = [min(group,key=lambda i:i['rank']) for group in
            ([i for i in ranked if i['kind']==kind] for kind in ('profile','record')) if group]
    fresh = [i for i in best if (i['ref'],i['digest']) not in seen
             or i['annotated'] and not seen[(i['ref'],i['digest'])]['annotated']] if outcome['withheld'] else []
    if outcome['withheld'] and len(fresh) != outcome['receipt_counts']['new']:
        raise ValueError('Fresh-item count differs from product receipt')
    noise = counts(case,fresh,refs)
    final = a if outcome['final_answer_by']=='planner' else b
    return dict(id=case['id'],split=case['split'],kind=case['kind'],**final,
        final_answer_by=outcome['final_answer_by'],withheld=outcome['withheld'],
        plan_not_accepted=not entry['accepted'], no_reply=not bool(result['reply']),a=a,worker=b,receipt_counts=outcome['receipt_counts'],
        noise_only_withheld=bool(outcome['withheld'] and fresh and not noise['needed_hit'] and not noise['trap_hits']),
        **({'inventory_changed':result['inventory_changed']} if 'inventory_changed' in result else {}))


def summarize(rows, *, gate=False):
    result = pairs._metrics(rows)
    if gate or rows and 'final_answer_by' in rows[0]:
        planner = [r for r in rows if r['final_answer_by']=='planner']
        worker = [r for r in rows if r['final_answer_by']=='worker']
        result.update(planner_cases=len(planner),planner_needed_hit=sum(r['a']['needed_hit'] for r in planner),
            planner_needed_total=sum(r['needed_total'] for r in planner),worker_cases=len(worker),
            worker_needed_hit=sum(r['worker']['needed_hit'] for r in worker),
            worker_needed_total=sum(r['needed_total'] for r in worker),
            plans_not_accepted=sum(r['plan_not_accepted'] for r in rows),
            plans_not_accepted_cases=[r['id'] for r in rows if r['plan_not_accepted']],
            withheld=sum(r['withheld'] for r in rows),replies=len(planner),no_reply=sum(r['no_reply'] for r in rows),
            noise_only_withheld=sum(r['noise_only_withheld'] for r in rows),
            inventory_a=dict(needed_hit=sum(r['a']['needed_hit'] for r in rows),
                traps=sum(r['a']['trap_hits'] for r in rows),noise=sum(r['a']['noise_hits'] for r in rows)),
            worker_profile=dict(needed_hit=sum(r['worker']['needed_hit'] for r in rows),
                traps=sum(r['worker']['trap_hits'] for r in rows),noise=sum(r['worker']['noise_hits'] for r in rows)))
    return result


def facet_report(data, observations):
    definitions = {c['id']:c for c in data['cases']}
    selected = {
        'overall':lambda c:True,
        'dev':lambda c:c['split']=='dev',
        'heldout [author:gpt]':lambda c:c['split']=='heldout' and 'author:gpt' in facets(c),
        'heldout [author:claude]':lambda c:c['split']=='heldout' and 'author:claude' in facets(c),
    }
    # Dev is observed once, heldout R times. Select an actual majority
    # representative per case with the existing v3 rule; never average rows.
    by_case = {}
    for rows in observations:
        for row in rows:
            by_case.setdefault(row['id'],[]).append(row)
    representatives, case_results = [], []
    for ident,rows in by_case.items():
        envelopes = [dict(version=data['version'],fixture=digest(data),rows=[r]) for r in rows]
        representative, details = _majority(envelopes)
        representatives.extend(representative['rows'])
        case_results.extend(details)
    result = {}
    gate = any('final_answer_by' in r for rows in observations for r in rows)
    for name,predicate in selected.items():
        ids = {ident for ident,c in definitions.items() if predicate(c)}
        result[name] = dict(cases_with_trap_any_run=len({r['id'] for rows in observations
                for r in rows if r['id'] in ids and r['trap_hits']}),
            majority=summarize([r for r in representatives if r['id'] in ids],gate=gate),
            per_run=[summarize([r for r in rows if r['id'] in ids],gate=gate) for rows in observations])
        if any('inventory_changed' in r for rows in observations for r in rows):
            # Held-plan replay: observations whose recomputed A differs from the recording.
            result[name]['inventory_changed'] = sum(bool(r.get('inventory_changed'))
                for rows in observations for r in rows if r['id'] in ids)
    return dict(facets=result,cases=case_results)


def condition_checks(executor, data, plans):
    dev = [c for c in data['cases'] if c['split']=='dev']
    candidates = [(c,plans['runs']['1'][c['id']]) for c in dev]
    reply = next(((c,e) for c,e in candidates if e['plan']['reply'] and e['accepted']
        and not any(e['plan'][f] for f in ('account_change','owner_situation','technical_work','ask_location','preview'))),None)
    checks = {}
    for condition in ('steer','preparation','location','early-revision'):
        chosen = reply
        if condition=='steer':
            chosen = next(((c,e) for c,e in candidates if e['plan']['reply'] and e['plan']['recall_query']
                and e['accepted'] and e['outcome']['final_answer_by']=='planner'),None)
        if condition=='early-revision':
            chosen = next(((c,e) for c,e in candidates if e['inventory'] and e['accepted']),None)
        if chosen is None:
            checks[condition] = dict(passed=False,reason='No suitable accepted dev plan in recording')
            continue
        case,entry = chosen
        try:
            result = replay_case(executor,case,entry,condition=condition)
            if condition=='early-revision':
                passed = result.get('early_used') and result['inventory']==result['early_inventory']
            elif condition=='location':
                passed = not result['reply'] and result['outcome']['final_answer_by']=='worker'
            else:
                passed = result['outcome']['final_answer_by']==('planner' if condition=='preparation' else 'worker')
                if condition=='preparation':
                    a = {(i['ref'],i['digest']) for i in result['inventory']}
                    b = {(i['ref'],i['digest']) for i in result['outcome']['checked_items'] if i['matched']}
                    passed = passed and b <= a
            checks[condition] = dict(passed=bool(passed),case=case['id'])
        except Exception as exc:
            checks[condition] = dict(passed=False,case=case['id'],reason=str(exc))
    return checks


def report(base, additions, terms, plans, *, code_head=None, held_plan=False, heldout=None):
    data = compose(base,additions,heldout)
    validate_plans(plans,data,terms)
    if not plans['complete'] or not terms['complete']:
        raise ValueError('Incomplete recording is not regression evidence')
    results = {}
    for name in ('lexical','runtime','prefix-lexical'):
        for with_terms in (False,True):
            def factory(store, fixture, name=name, with_terms=with_terms):
                if with_terms:
                    apply_terms(store,fixture,terms)
                return arms.ARMS[name](store,fixture)
            # run_arm owns the store, seeding and pairs.run/scoring boundaries.
            measured = run_arm(data,Arm(factory,'fixture (model-free)',True,diagnostic=name=='prefix-lexical'),1)
            key = name + ('+terms' if with_terms else ' no-terms')
            results[key] = facet_report(data,[r['rows'] for r in measured['runs']])
            results[key].update(diagnostic=name=='prefix-lexical',evidence='fixture (model-free)')
    with tempfile.TemporaryDirectory(prefix='recall-gate-') as tmp:
        from personal_agent.quickstart_store import QuickStore
        store = QuickStore(Path(tmp)/'state')
        service = fixture_service(store)
        refs = seed_fixture(store,data)
        apply_terms(store,data,terms,refs)
        executor = Executor(service,data,refs,catalogue_from(plans['catalogue']))
        observations = []
        for run,entries in plans['runs'].items():
            rows = []
            for case in data['cases']:
                if case['id'] not in entries:
                    continue
                entry = entries[case['id']]
                observed = replay_case(executor,case,entry,strict=not held_plan)
                rows.append(gate_row(case,entry,observed,data,refs))
            observations.append(rows)
        results['gate+terms'] = facet_report(data,observations)
        results['gate+terms']['evidence'] = 'fixture (model-free)'
        checks = condition_checks(executor,data,plans)
    return dict(version='recall-regression/1',store_composition=dict(v4_rows=len(base['rows']),
        addition_rows=len(additions['rows']),total_rows=len(data['rows']),addition_digest=digest(additions),
        **({'heldout_rows':len(heldout['rows']),'heldout_cases':len(heldout['cases']),'heldout_digest':digest(heldout)}
           if heldout else {})),
        evidence=dict(fixture_digest=digest(data),v4_digest=digest(base),terms_digest=digest(terms),
            plan_digest=digest(plans),code_head=code_head,recorded_code_head=plans.get('git_head'),
            recorded_inputs=plans.get('evidence_class','live-route:subscription_cli'),
            replay='held-plan (model-free)' if held_plan else 'fixture (model-free)',
            calls=plans['call_budget'],identity=plans['identity'],tokens='not observable'),
        terms_status_counts=terms_status_counts(terms),
        arms=results,work_condition_checks=checks,
        named_cases={
            'particle':[c['id'] for c in data['cases'] if 'particle' in facets(c)],
            'cross-language':[c['id'] for c in data['cases'] if c['kind']=='cross-language']})


def markdown(result):
    lines = ['# #921 recall regression','', 'Evidence: '+json.dumps(result['evidence'],ensure_ascii=False),
        '', 'Store: '+json.dumps(result['store_composition']), '',
        'Terms status counts: '+json.dumps(result['terms_status_counts']), '',
        'Counts are observed majority representatives; dev runs once, held-out uses predeclared repetitions. No judge or selection verdict.', '',
        '| Arm | Facet | Needed | Trap cases | Noise |','| --- | --- | --- | --- | --- |']
    for name,arm in result['arms'].items():
        for facet,values in arm['facets'].items():
            m=values['majority']
            lines.append(f"| {name} | {facet} | {m['needed_hit']}/{m['needed_total']} | {values['cases_with_trap_any_run']} | {m['noise_count']} |")
    for name,arm in result['arms'].items():
        lines += ['', '## '+name+' per-run and gate attribution', '']
        for facet,values in arm['facets'].items():
            lines.append('- '+facet+': '+json.dumps(values,ensure_ascii=False))
    lines += ['## Work-condition checks (not measurements)','',json.dumps(result['work_condition_checks'],ensure_ascii=False)]
    for name,ids in result['named_cases'].items():
        lines += ['',f'## {name}', '']
        for ident in ids:
            observations = {arm:next(c for c in value['cases'] if c['id']==ident) for arm,value in result['arms'].items()}
            lines.append('- '+ident+': '+json.dumps(observations,ensure_ascii=False))
    return '\n'.join(lines)+'\n'


def command(args):
    from .paths import REPO
    from .cli import git_head
    folder = args.out.expanduser().resolve()
    if folder.is_relative_to(REPO.resolve()):
        raise ValueError('Recall reports belong outside the repository')
    result = report(pairs.load(args.fixture),json.loads(args.additions.read_text()),
        load_recording(args.terms),load_recording(args.plans),code_head=git_head(),
        **({'held_plan':True} if getattr(args,'held_plan',False) else {}),
        **({'heldout':json.loads(args.heldout_layer.read_text())} if getattr(args,'heldout_layer',None) else {}))
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'recall-regression.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (folder/'recall-regression.md').write_text(markdown(result))
    print('Model-free recall regression report written')
