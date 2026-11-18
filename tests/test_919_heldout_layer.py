"""#919: the fresh held-out layer that replaces v4's spent held-out cases (#827), model-free.

Reuse review: N/A for fixture content and assertions: no runtime component, dependency or
retrieval case. Adopt v3/v4's leakage and stem scans, the real FTS5 seed/run and the
MemoryService snapshot bounds.
"""
import copy
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

from test_recall_pairs_v3 import BASELINES, FUNCTION_WORDS, MEANING, assert_no_needed_overlap, facets, stem, terms

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import recall_arms as arms, recall_pairs as pairs  # noqa: E402
from agentos_eval.recall_regression import compose, digest, layer  # noqa: E402
from personal_agent.memory_service import MemoryService  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402

RECALL = ROOT / 'evals/recall'
LAYER = RECALL / 'recall-heldout-919-1.json'


@pytest.fixture(scope='module')
def inputs():
    return (pairs.load(RECALL / 'recall-pairs-v4.json'), json.loads((RECALL / 'recall-gate-dev-1.json').read_text()),
            json.loads(LAYER.read_text()))


@pytest.fixture(scope='module')
def data(inputs):
    return compose(*inputs)


def fresh(data):
    return [case for case in data['cases'] if case['split'] == 'heldout']


@pytest.fixture(scope='module')
def seeded(tmp_path_factory, data):
    store = QuickStore(tmp_path_factory.mktemp('recall-919') / 'state')
    assert store.memory_search_available, 'Must exercise FTS5, not the LIKE fallback'
    return store, pairs.seed(store, data)


def test_builder_is_repeatable_and_bound_to_v4_and_the_dev_additions(inputs):
    spec = importlib.util.spec_from_file_location('build_heldout_919', RECALL / 'build_heldout_919.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.render() == module.render() == LAYER.read_bytes()
    base, additions, heldout = inputs
    assert heldout['version'] == 'recall-heldout/1'
    assert heldout['base_digest'] == digest(base) and heldout['additions_digest'] == digest(additions)


def test_compose_keeps_dev_puts_fresh_rows_first_and_drops_spent_heldout(inputs, data):
    base, additions, heldout = inputs
    dev = layer(base, additions)
    assert data['rows'] == heldout['rows'] + dev['rows']
    assert [c for c in data['cases'] if c['split'] == 'dev'] == [c for c in dev['cases'] if c['split'] == 'dev']
    assert fresh(data) == heldout['cases']
    assert not {c['id'] for c in base['cases'] if c['split'] == 'heldout'} & {c['id'] for c in data['cases']}
    assert compose(base, additions) == dev


@pytest.mark.parametrize('change', ['version', 'base', 'additions', 'dev-case', 'empty'])
def test_compose_refuses_a_mismatched_or_non_heldout_layer(inputs, change):
    base, additions, heldout = inputs
    bad = copy.deepcopy(heldout)
    if change == 'version':
        bad['version'] = 'recall-heldout/0'
    elif change == 'base':
        bad['base_digest'] = '0' * 64
    elif change == 'additions':
        bad['additions_digest'] = '0' * 64
    elif change == 'dev-case':
        bad['cases'][0]['split'] = 'dev'
    else:
        bad['cases'] = []
    with pytest.raises(ValueError, match='Held-out layer'):
        compose(base, additions, bad)


def test_fresh_cases_are_claude_written_with_the_planned_mix(data):
    cases = fresh(data)
    assert len(cases) == 24
    assert all({'author:claude', 'fresh'} <= set(facets(c)) for c in cases)
    assert Counter(c['kind'] for c in cases) == {'cross-language': 8, 'paraphrase': 12, 'short-follow-up': 2,
                                                 'lexical-control': 1, 'harm-control': 1}
    assert sum('particle' in facets(c) for c in cases) == 6
    rows = {row['ref']: row for row in data['rows']}
    directions = Counter()
    for case in cases:
        if case['kind'] in MEANING:
            assert len(case['needed']) == 1
        if case['kind'] == 'cross-language':
            query, content = case['query'], rows[case['needed'][0]]['content']
            korean = bool(re.search('[가-힣]', query))
            assert korean != bool(re.search('[가-힣]', content))
            assert not re.search('[A-Za-z]' if korean else '[가-힣]', query)
            directions['ko-en' if korean else 'en-ko'] += 1
        if case['kind'] == 'paraphrase':
            assert bool(re.search('[가-힣]', case['query'])) == bool(re.search('[가-힣]', rows[case['needed'][0]]['content']))
    assert directions == {'ko-en': 4, 'en-ko': 4}


def test_fresh_meaning_cases_have_no_forward_leakage(data):
    assert_no_needed_overlap(dict(data, cases=fresh(data)))


def test_particle_reverse_stems_are_exact(data):
    rows = {row['ref']: row for row in data['rows']}
    expected = {'h5c-whetstone': ('숫돌을', '숫돌'), 'h5c-strawmat': ('맷방석이', '맷방석'),
                'h5c-brazier': ('화로에서', '화로'), 'h5c-mortar': ('절구에', '절구'),
                'h5c-kettle': ('주전자에', '주전자'), 'h5c-barrow': ('외바퀴수레로', '외바퀴수레')}
    observed = {}
    for case in fresh(data):
        if case['kind'] not in MEANING:
            continue
        reverse = [(query, token) for query in terms(case['query'] + ' ' + case.get('context', ''))
                   for ref in case['needed'] for field in ('content', 'key')
                   for token in terms(rows[ref][field]) if query.startswith(token)]
        if 'particle' in facets(case):
            observed[case['id']] = reverse[0]
            assert reverse == [expected[case['id']]]
        else:
            assert not reverse, (case['id'], reverse)
    assert observed == expected


def test_fresh_query_stems_and_subjects_are_separate_from_dev(data):
    def words(cases):
        return {stem(word) for case in cases for word in terms(case['query'])
                if word not in FUNCTION_WORDS and stem(word) not in FUNCTION_WORDS}
    dev, held = [c for c in data['cases'] if c['split'] == 'dev'], fresh(data)
    shared = sorted((a, b) for a in words(dev) for b in words(held) if a.startswith(b) or b.startswith(a))
    assert not shared, shared
    assert not ({r for c in dev for r in c['needed'] + c.get('irrelevant', [])}
                & {r for c in held for r in c['needed'] + c.get('irrelevant', [])})


@pytest.mark.parametrize('name', BASELINES)
def test_fresh_meaning_cases_are_lexically_unreachable_and_controls_behave(name, data, seeded):
    store, refs = seeded
    cases = {case['id']: case for case in fresh(data)}
    for row in pairs.run(arms.ARMS[name](store, data), list(cases.values()), refs)['rows']:
        case = cases[row['id']]
        assert row['trap_hits'] == 0
        if case['kind'] in MEANING:
            assert (row['needed_hit'], row['noise_hits']) == (0, 0), (name, case['id'], row['returned'])
        if case['kind'] == 'lexical-control' and name == 'lexical':
            assert row['returned'] == [refs[case['needed'][0]]]
        if case['kind'] == 'harm-control' and name == 'lexical':
            assert refs[case['irrelevant'][0]] in row['returned']


def test_fresh_rows_add_no_noise_to_any_dev_case(data, seeded):
    store, refs = seeded
    new = {refs[row['ref']] for row in json.loads(LAYER.read_text())['rows']}
    for name in ('lexical', 'context-concat', 'lexical-context', 'prefix-lexical'):
        retrieve = arms.ARMS[name](store, data)
        for case in data['cases']:
            if case['split'] == 'dev':
                assert not new & set(retrieve(case['query'], case.get('context', ''))), (name, case['id'])


def test_fresh_targets_are_cut_from_the_request_only_snapshot(data, seeded):
    store, refs = seeded
    service = MemoryService(store, private_read_sink=MemoryService.NO_EGRESS_GUARD)
    service._today = lambda: data['today']
    candidates = {row['id'] for row in service._profile_rows(data['owner'], holding_on=data['today'])}
    cut = [case for case in fresh(data) if {'cut', 'particle'} & set(facets(case))]
    assert len(cut) == 22
    for case in cut:
        snapshot = service.profile_snapshot(data['owner'], query=case['query'])
        needed = {refs[ref] for ref in case['needed']}
        assert not needed & candidates, case['id']
        assert not needed & {entry['id'] for entry in snapshot['entries']}, case['id']
