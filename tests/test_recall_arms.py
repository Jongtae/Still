"""Model-free fixture evidence for eval-side retriever arms; no runtime changes."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import recall_arms as arms, recall_pairs as pairs, recall_report as rr  # noqa: E402
from personal_agent.memory_service import MemoryService, PROFILE_PREFIX  # noqa: E402
from personal_agent.quickstart_service import AgentService  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402
from test_recall_pairs import lexical as baseline_lexical  # noqa: E402


def dev_data(version):
    data = pairs.load(ROOT / f'evals/recall/recall-pairs-v{version}.json')
    data['cases'] = [case for case in data['cases'] if case['split'] == 'dev']
    return data


@pytest.fixture
def data():
    return dev_data(1)


@pytest.fixture
def seeded(tmp_path, data):
    store = QuickStore(tmp_path / 'state')
    return store, pairs.seed(store, data)


@pytest.fixture
def dev_controls(data):
    """Use only dev lexical-control facts to construct additional contexts."""
    refs = {ref for case in data['cases']
            if case['split'] == 'dev' and case['kind'] == 'lexical-control'
            for ref in case['needed']}
    return [row for row in data['rows'] if row['ref'] in refs]


def test_lexical_matches_existing_baseline_on_all_cases(data, seeded):
    store, refs = seeded
    expected = pairs.run(baseline_lexical(store, data), data['cases'], refs)
    actual = pairs.run(arms.lexical(store, data), data['cases'], refs)
    assert [row['returned'] for row in actual['rows']] == [row['returned'] for row in expected['rows']]


def test_lexical_context_reuses_lexical_with_context_first(monkeypatch):
    calls = []

    def factory(store, data):
        def search(query, context=''):
            calls.append((query, context))
            return ['synthetic-id']
        return search

    monkeypatch.setattr(arms, 'lexical', factory)
    retrieve = arms.lexical_context(None, {})
    assert retrieve('request', 'prior turn') == ['synthetic-id']
    assert retrieve('request') == ['synthetic-id']
    assert calls == [('prior turn request', ''), ('request', '')]


def test_context_first_order_matters_at_real_search_term_limit(data, seeded, dev_controls):
    store, refs = seeded
    request, context = dev_controls[:2]
    query = ' '.join(f'unmatched{i}' for i in range(12)) + ' ' + request['content']
    reference = arms.lexical_context(store, data)
    diagnostic = arms.context_concat(store, data)
    returned = reference(query, context['content'])
    assert refs[context['ref']] in returned
    assert refs[request['ref']] not in returned
    assert diagnostic(query, context['content']) == []


@pytest.mark.parametrize('name', arms.ARMS)
def test_no_trap_hits_on_either_split(name, data, seeded):
    store, refs = seeded
    results = pairs.run(arms.ARMS[name](store, data), data['cases'], refs)
    for split in pairs.score(results).values():
        assert split['total']['cases_with_trap'] == 0


def test_context_preserves_every_nonempty_request_result(data, seeded, dev_controls):
    store, refs = seeded
    baseline = arms.lexical(store, data)
    candidate = arms.context_aware(store, data)
    checked = 0
    for case in data['cases']:
        expected = baseline(case['query'], '')
        if not expected:
            continue
        checked += 1
        assert candidate(case['query'], case.get('context', '')) == expected, case['id']
        unrelated = next(row for row in dev_controls if refs[row['ref']] not in expected)
        synthetic = dict(case, context=unrelated['content'])
        result = pairs.run(candidate, [synthetic], refs)['rows'][0]
        assert result['returned'] == expected, case['id']
    assert checked


def test_context_falls_back_for_dev_follow_up(data, seeded):
    store, refs = seeded
    case = next(case for case in data['cases']
                if case['split'] == 'dev' and case['kind'] == 'short-follow-up')
    baseline = arms.lexical(store, data)
    candidate = arms.context_aware(store, data)
    assert baseline(case['query'], case['context']) == []
    assert candidate(case['query'], '') == []
    expected = baseline(f"{case['query']} {case['context']}", '')
    assert expected
    result = pairs.run(candidate, [case], refs)['rows'][0]
    assert result['returned'] == expected
    assert result['needed_hit'] == result['needed_total'] > 0
    assert result['trap_hits'] == result['noise_hits'] == 0


def test_concat_can_add_noise_to_a_request_with_its_own_fact(data, seeded, dev_controls):
    store, refs = seeded
    request, context = dev_controls[:2]
    case = dict(id='constructed-context-noise', split='dev', kind='lexical-control',
                query=request['content'], context=context['content'],
                needed=[request['ref']], traps=[], note='Distinct current facts from dev controls.')
    baseline = arms.lexical(store, data)(case['query'], '')
    assert baseline == [refs[request['ref']]]
    candidate = pairs.run(arms.context_aware(store, data), [case], refs)['rows'][0]
    diagnostic = pairs.run(arms.context_concat(store, data), [case], refs)['rows'][0]
    assert candidate['returned'] == baseline
    assert set(diagnostic['returned']) >= set(baseline) | {refs[context['ref']]}
    assert candidate['noise_hits'] == 0
    assert diagnostic['noise_hits'] > 0


@pytest.mark.parametrize('name', arms.ARMS)
def test_retriever_leaves_store_unchanged(name, data, seeded):
    store, refs = seeded
    before = refs.digest()
    retriever = arms.ARMS[name](store, data)
    pairs.run(retriever, data['cases'], refs)
    assert refs.digest() == before


def test_arm_metadata_agrees():
    assert arms.ARMS == {'lexical': arms.lexical, 'context': arms.context_aware,
                         'context-concat': arms.context_concat, 'lexical-context': arms.lexical_context,
                         'runtime': arms.runtime}
    assert arms.ARMS.keys() == arms.EVIDENCE.keys() == arms.DETERMINISTIC
    assert isinstance(arms.DETERMINISTIC, frozenset)
    assert set(arms.EVIDENCE.values()) == {'fixture (model-free)'}
    assert 'runtime' not in arms.DIAGNOSTIC


def test_runtime_default_is_request_only_without_model(data, seeded):
    store, refs = seeded
    case = next(case for case in data['cases'] if case.get('context'))
    retrieve = arms.runtime(store, data)
    result = pairs.run(retrieve, [case], refs)['rows'][0]
    assert result['returned'] == []
    observed = retrieve.observed[case['id']]
    assert observed['query'] == case['query']
    assert observed['selector_source'] == observed['planning_query_source'] == 'request'
    assert observed['plan_accepted'] and observed['plan_calls'] == 1
    assert retrieve(case['query']) == []


@pytest.mark.parametrize(('query','context'), [('request','prior topic'), ('q'*510,'prior topic'), ('request','')])
def test_runtime_observes_real_request_only_bounded_query(query, context, data, seeded):
    store, _ = seeded
    retrieve = arms.runtime(store, data)
    retrieve(query, context)
    assert retrieve.observed['call-1']['query'] == query[:500]
    assert retrieve.observed['call-1']['plan_calls'] == 1


def test_runtime_uses_fixture_date_and_excludes_all_traps(data, seeded):
    store, refs = seeded
    retrieve = arms.runtime(store, data)
    boundary = next(row for row in data['rows'] if row.get('valid_until') == data['today'])
    assert refs[boundary['ref']] in retrieve(boundary['content'])
    results = pairs.run(retrieve, data['cases'], refs)
    forbidden = {ident for ident in refs.values() if not refs.eligible(ident)}
    assert forbidden
    assert all(not forbidden.intersection(row['returned']) for row in results['rows'])


def test_runtime_scores_worker_matches_only_not_planner_or_general_profile(data, seeded):
    store, _ = seeded
    # Twenty matches exceed the real snapshot's UTF-8 byte budget.
    for index in range(20):
        store.save_memory(f'profile.bulk{index}', 'needle ' + '가' * 500,
                          owner_id=data['owner'])
    # Prefix alone is insufficient: this is not a valid profile key.
    invalid = store.save_memory('profile.bad..key', 'needle', owner_id=data['owner'])['id']
    retrieve = arms.runtime(store, data)
    memory = MemoryService(store, private_read_sink=MemoryService.NO_EGRESS_GUARD)
    memory._today = lambda: data['today']
    matched = memory.search_memories(data['owner'], 'needle', limit=20,
                                     key_prefix=PROFILE_PREFIX)['memories']
    snapshot = memory.profile_snapshot(data['owner'], query='needle')
    shown = {entry['id'] for entry in snapshot['entries']}
    expected = [row['id'] for row in matched
                if memory.is_profile_key(row.get('memory_key')) and row['id'] in shown]
    returned = retrieve('needle')
    assert 0 < len(returned) < len(matched)
    assert returned == expected
    assert invalid not in returned


def test_runtime_memory_write_fails_digest_guard(monkeypatch, data, seeded):
    store, refs = seeded
    original = MemoryService.profile_snapshot

    def writing_snapshot(memory, *args, **kwargs):
        store.save_memory('profile.unexpected', 'unexpected write', owner_id=data['owner'])
        return original(memory, *args, **kwargs)

    monkeypatch.setattr(MemoryService, 'profile_snapshot', writing_snapshot)
    with pytest.raises(pairs.RecallPairsError, match='Retriever wrote to the store on case'):
        pairs.run(arms.runtime(store, data), data['cases'][:1], refs)


@pytest.mark.parametrize('version', [1, 2])
def test_runtime_run_arm_and_report_both_fixtures(version):
    # Full-fixture smoke only: no heldout recall/noise targets or printed retrievals.
    data = pairs.load(ROOT / f'evals/recall/recall-pairs-v{version}.json')
    runtime = rr.run_arm(data, rr.resolve_arm('runtime'))
    assert runtime['deterministic'] and not runtime['diagnostic']
    assert len(runtime['runs']) == 1
    rows = runtime['runs'][0]['rows']
    assert len(rows) == len(data['cases'])
    for row in rows:
        assert isinstance(row['returned'], list)
        assert all(isinstance(ident, str) for ident in row['returned'])
        assert row['trap_hits'] == 0
    assert {row['split'] for row in rows} == {'dev','heldout'}
    baseline = rr.run_arm(data, rr.resolve_arm('lexical'))
    report = rr.report(data, {'lexical': baseline, 'runtime': runtime})
    assert report['arms']['runtime']['observed'] == runtime['observed']
    assert report['arms']['runtime']['verdict'] is not None


def replay_record(data, selectors):
    return dict(version='recall-plan/1', fixture_digest=rr._fixture_digest(data),
                plan_prompt_schema_digest=arms.plan_prompt_schema_digest(),
                runs={'1':{case['id']:dict(query=case['query'],context=case.get('context',''),
                          recall_query=selectors.get(case['id'],''),evidence='author-written fixture')
                           for case in data['cases']}})


def test_runtime_replay_validates_plan_and_uses_shared_worker_path(monkeypatch, data, seeded):
    store, refs = seeded
    selectors = {case['id']:case.get('context','') for case in data['cases']}
    resolver = arms.replay_resolver(replay_record(data, selectors),data)
    calls = []
    original = AgentService.worker_profile_snapshot
    def refresh(service, job, attempt=None):
        calls.append(attempt)
        return original(service,job,attempt)
    monkeypatch.setattr(AgentService,'worker_profile_snapshot',refresh)
    retrieve = arms.runtime(store,data,resolver=resolver)
    pairs.run(retrieve,data['cases'],refs)
    assert len(calls) == len(data['cases'])
    assert all(attempt.planned for attempt in calls)
    assert all(item['plan_calls'] == 1 for item in retrieve.observed.values())
    invalid = arms.runtime(store,data,resolver=lambda q,c:None)
    case = data['cases'][0]
    invalid(case['query'],case.get('context',''))
    assert invalid.observed[case['id']]['plan_accepted']
    assert invalid.observed[case['id']]['selector_source'] == 'request'


@pytest.mark.parametrize('fault',['fixture','case','input','missing','type'])
def test_runtime_replay_rejects_fixture_case_or_plan_input_mismatch(data,fault):
    recorded = replay_record(data,{})
    entry = recorded['runs']['1'][data['cases'][0]['id']]
    if fault == 'fixture': recorded['fixture_digest'] = 'wrong'
    if fault == 'input': recorded['plan_prompt_schema_digest'] = 'wrong'
    if fault == 'case': entry['context'] = 'wrong'
    if fault == 'missing': recorded['runs']['1'].pop(data['cases'][0]['id'])
    if fault == 'type': entry['recall_query'] = None
    with pytest.raises(ValueError): arms.replay_resolver(recorded,data)


def test_runtime_resolved_dev_followups_and_lexical_controls(tmp_path):
    data = dev_data(2)
    wanted = {'d-follow':['r4'],'h-follow':['r18'],'d-lex-instrument':['r1'],
              'd-lex-last-day':['r13'],'d-lex-desk':['r14'],'h-lex-tool':['r15']}
    data['cases'] = [case for case in data['cases'] if case['id'] in wanted]
    assert len(data['cases']) == len(wanted) == 6
    store = QuickStore(tmp_path/'state')
    refs = pairs.seed(store,data)
    selectors = {'d-follow':'발표 장소','h-follow':'상자'}
    retrieve = arms.runtime(store,data,resolver=arms.replay_resolver(replay_record(data,selectors),data))
    results = pairs.run(retrieve,data['cases'],refs)
    assert len(results['rows']) == len(wanted)
    for row in results['rows']:
        assert row['returned'] == [refs[ref] for ref in wanted[row['id']]]
        assert row['noise_hits'] == row['trap_hits'] == 0


def synthetic_data():
    # Four independently authored dev probes from the accepted diagnosis.
    data = dev_data(2)
    data['rows'] += [dict(ref='s883-a',key='profile.synthetic883.a',content='quartzclang compiler flags'),
                     dict(ref='s883-b',key='profile.synthetic883.b',content='velvostring rehearsal Tuesday'),
                     dict(ref='s883-c',key='profile.synthetic883.c',content='rehearsal notes archived')]
    data['cases'] = [dict(id=ident,split='dev',kind=kind,query=q,context=c,needed=needed,traps=[],note='author-written')
        for ident,kind,q,c,needed in [
            ('s883-lex-context','lexical-control','quartzclang flags','velvostring rehearsal',['s883-a']),
            ('s883-topic-shift','harm-control','photosynthesis explanation','velvostring rehearsal',[]),
            ('s883-follow','short-follow-up','Repeat that','velvostring',['s883-b']),
            ('s883-prefix-noise','lexical-control','quartzclang','rehear',['s883-a'])]]
    data['cases'][1]['irrelevant']=['s883-a','s883-b','s883-c']
    return data


def test_runtime_synthetic_context_does_not_add_topic_noise(tmp_path):
    data = synthetic_data()
    store = QuickStore(tmp_path/'state')
    refs = pairs.seed(store,data)
    retrieve = arms.runtime(store,data,resolver=lambda q,c:'velvostring' if q == 'Repeat that' else '')
    for row in pairs.run(retrieve,data['cases'],refs)['rows']:
        assert row['needed_hit'] == row['needed_total']
        assert row['noise_hits'] == row['trap_hits'] == 0
