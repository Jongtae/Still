"""Model-free fixture evidence for eval-side retriever arms; no runtime changes."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import recall_arms as arms, recall_pairs as pairs  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402
from test_recall_pairs import lexical as baseline_lexical  # noqa: E402


@pytest.fixture
def data():
    return pairs.load(ROOT / 'evals/recall/recall-pairs-v1.json')


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
                         'context-concat': arms.context_concat, 'lexical-context': arms.lexical_context}
    assert arms.ARMS.keys() == arms.EVIDENCE.keys() == arms.DETERMINISTIC
    assert isinstance(arms.DETERMINISTIC, frozenset)
    assert set(arms.EVIDENCE.values()) == {'fixture (model-free)'}
