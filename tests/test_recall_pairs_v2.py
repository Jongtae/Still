"""Synthetic lexical fixture evidence only; no embedding or model calls.

Reuse review (offline): Adopt stdlib JSON/regex and pytest parametrization;
reuse the existing SQLite FTS5-backed arms, seed/run/score and report CLI.
Build only the requested fixture-version allowlist and fixture assertions:
an external schema/eval framework cannot check Still's seeded eligibility or
these paired labels. No new component, dependency, licence or runtime authority;
external release activity was not checked. VERSION remains v1 for callers.
"""
import copy
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import recall_arms as arms, recall_pairs as pairs  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402

FIXTURE = ROOT / 'evals/recall/recall-pairs-v2.json'
MEANING_KINDS = {'paraphrase', 'cross-language', 'short-follow-up'}


@pytest.fixture
def data():
    return pairs.load(FIXTURE)


@pytest.fixture
def seeded(tmp_path, data):
    store = QuickStore(tmp_path / 'state')
    # These assertions measure FTS5 prefix matching, not the LIKE fallback.
    assert store.memory_search_available
    return store, pairs.seed(store, data)


def terms(text):
    """All tokens, without the search limit, so truncation cannot create freshness."""
    return re.findall(r'[^\W_]+', text.casefold())


def fresh(data):
    return [case for case in data['cases'] if case['split'] == 'heldout']


def facets(case):
    """Facets are leading note tags, leaving the strict fixture schema intact."""
    return re.findall(r'\[([^]]+)\]', re.match(r'(?:\[[^]]+\]\s*)*', case['note'])[0])


def assert_no_needed_overlap(data):
    rows = {row['ref']: row for row in data['rows']}
    for case in fresh(data):
        if case['kind'] not in MEANING_KINDS:
            continue
        assert case['needed'], case['id']
        if case['kind'] == 'short-follow-up':
            assert case.get('context'), case['id']
        # No term/result limit: keys are indexed too, and future multi-answer
        # cases must not leak even one needed row through either source.
        for source in ('query', 'context'):
            for ref in case['needed']:
                for field in ('content', 'key'):
                    overlaps = [(term, token) for term in terms(case.get(source, ''))
                                for token in terms(rows[ref][field]) if token.startswith(term)]
                    assert not overlaps, (case['id'], source, ref, field, overlaps)


def test_versions_and_v1_preserved_except_split(data):
    v1 = pairs.load(ROOT / 'evals/recall/recall-pairs-v1.json')
    assert pairs.VERSION == 'recall-pairs/1'
    assert pairs.VERSIONS == ('recall-pairs/1', 'recall-pairs/2')
    assert data['version'] == 'recall-pairs/2'
    assert pairs.validate(data) is data
    assert data['owner'] == v1['owner']
    assert data['today'] == v1['today'] == '2026-03-25'
    assert data['rows'][:25] == v1['rows']
    assert data['cases'][:24] == [dict(case, split='dev') for case in v1['cases']]
    assert all(case['split'] == 'heldout' for case in data['cases'][24:])
    assert all(case['id'].startswith('h2-') for case in fresh(data))
    new_rows = data['rows'][25:]
    assert new_rows
    assert [row['ref'] for row in new_rows] == [f'n{i}' for i in range(1, len(new_rows) + 1)]
    assert all(row['key'].startswith('profile.v2.') for row in new_rows)
    assert all('\n' not in row['content'] for row in new_rows)
    assert all(ref.startswith('n') for case in fresh(data)
               for group in ('needed', 'traps', 'irrelevant') for ref in case.get(group, []))


def test_fresh_coverage(data):
    counts = Counter(case['split'] for case in data['cases'])
    assert counts['dev'] == 24
    assert counts['heldout'] == 24
    cases = fresh(data)
    kinds = Counter(case['kind'] for case in cases)
    assert kinds == {'paraphrase': 8, 'cross-language': 7, 'short-follow-up': 3,
                     'lexical-control': 3, 'harm-control': 3}
    assert sum(case['kind'] == 'lexical-control' and 'context' not in case for case in cases) >= 2
    assert any(case['kind'] == 'harm-control' and 'context' not in case for case in cases)
    assert sum('context' in case and bool(case.get('irrelevant')) for case in cases) >= 3
    assert sum(case['kind'] == 'paraphrase' and bool(case.get('irrelevant')) for case in cases) >= 3
    rows = {row['ref']: row for row in data['rows']}
    directions = Counter()
    for case in cases:
        contents = ' '.join(rows[ref]['content'] for ref in case['needed'])
        if case['kind'] == 'paraphrase':
            assert re.search('[가-힣]', case['query']) and re.search('[가-힣]', contents)
            assert not re.search('[a-zA-Z]', case['query'] + contents)
        if case['kind'] != 'cross-language':
            continue
        if re.search('[a-zA-Z]', case['query']) and not re.search('[가-힣]', case['query']):
            assert re.search('[가-힣]', contents) and not re.search('[a-zA-Z]', contents)
            directions['en-ko'] += 1
        else:
            assert re.search('[가-힣]', case['query']) and not re.search('[a-zA-Z]', case['query'])
            assert re.search('[a-zA-Z]', contents) and not re.search('[가-힣]', contents)
            directions['ko-en'] += 1
    assert directions == {'en-ko': 4, 'ko-en': 3}


@pytest.mark.parametrize('ident,trap,state', [
    ('h2-proofing', 'n10', 'superseded'),
    ('h2-binoculars', 'n12', 'corrected'),
    ('h2-apiary', 'n14', 'current'),  # expiry alone excludes an otherwise-current row
    ('h2-riddle', 'n32', 'forgotten'),
])
def test_near_traps_isolate_each_exclusion_reason(ident, trap, state, data, seeded):
    store, refs = seeded
    rows = {row['ref']: row for row in data['rows']}
    case = next(case for case in fresh(data) if case['id'] == ident)
    assert 'near-trap' in facets(case)
    assert case['traps'] == [trap] and case['needed']
    actual = store.memory(refs[trap], owner_id=data['owner'], current_only=False)
    assert actual['state'] == state
    assert not refs.eligible(refs[trap])
    assert all(refs.eligible(refs[ref]) for ref in case['needed'])
    assert any(token.startswith(term) for term in terms(case['query'])
               for token in terms(rows[trap]['content'] + ' ' + rows[trap]['key']))
    if state == 'current':
        assert actual['valid_until'] < data['today']
    else:
        assert not actual['valid_until'] or actual['valid_until'] >= data['today']
    if state in {'current', 'forgotten'}:
        assert [row['ref'] for row in data['rows'] if row['key'] == rows[trap]['key']] == [trap]
        assert all(rows[ref]['key'] != rows[trap]['key'] and 'after' not in rows[ref]
                   for ref in case['needed'])
    else:
        assert all(rows[ref]['after'] == trap for ref in case['needed'])
    # A retriever cannot receive credit for reviving any of these reasons.
    result = pairs.run(lambda query, context: [refs[trap]], [case], refs)['rows'][0]
    assert result['trap_hits'] == 1 and result['needed_hit'] == 0


def test_lexical_zero_traps_on_both_splits(data, seeded):
    store, refs = seeded
    result = pairs.run(arms.lexical(store, data), data['cases'], refs)
    for split in pairs.score(result).values():
        assert split['total']['cases_with_trap'] == 0
    for row in result['rows']:
        if row['kind'] == 'lexical-control':
            assert row['needed_hit'] == row['needed_total'], row['id']


def test_meaning_cases_have_no_query_or_context_prefix_overlap(data):
    assert_no_needed_overlap(data)


@pytest.mark.parametrize('source', ['query', 'context'])
@pytest.mark.parametrize('field', ['content', 'key'])
@pytest.mark.parametrize('query,indexed', [
    ('KEST', 'kestrel'), ('73', '731'), ('라벤', '라벤더'), ('물결', '물결무늬'),
    ('a', 'alphabet'),
])
def test_overlap_scan_rejects_leaks_beyond_search_limits(data, source, field, query, indexed):
    leaking = copy.deepcopy(data)
    case = next(case for case in fresh(leaking) if case['id'] == 'h2-shoe-lining')
    row = next(row for row in leaking['rows'] if row['ref'] == case['needed'][0])
    row[field] = 'profile.v2.' + indexed if field == 'key' else indexed
    # Even a second needed answer and a matching term after twelve earlier
    # terms must be rejected; result/term limits cannot establish freshness.
    case['needed'].append('n18')
    case[source] = ' '.join(f'absent{i}' for i in range(13)) + ' ' + query
    pairs.validate(leaking)  # valid schema is not evidence of lexical separation
    with pytest.raises(AssertionError) as failure:
        assert_no_needed_overlap(leaking)
    diagnostic = str(failure.value)
    assert all(value in diagnostic for value in (case['id'], source, row['ref'], field,
                                                query.casefold(), indexed))


@pytest.mark.parametrize('name', ['lexical', 'context-concat', 'lexical-context'])
def test_fresh_meaning_cases_are_not_solved_by_lexical_arms(name, data, seeded):
    store, refs = seeded
    cases = [case for case in fresh(data) if case['kind'] in MEANING_KINDS]
    retrieve = arms.ARMS[name](store, data)
    for row in pairs.run(retrieve, cases, refs)['rows']:
        assert row['needed_total'] > 0
        assert row['needed_hit'] == 0, (name, row['id'])


def test_facets_and_only_labelled_meaning_cases_have_incidental_hits(data, seeded):
    store, refs = seeded
    cases = fresh(data)
    tagged = {tag: {case['id'] for case in cases if tag in facets(case)}
              for tag in ('near-trap', 'misleading-context', 'incidental-noise')}
    assert tagged == {
        'near-trap': {'h2-proofing', 'h2-binoculars', 'h2-apiary', 'h2-riddle'},
        'misleading-context': {'h2-context-aquarium', 'h2-context-inkstone', 'h2-context-lift'},
        'incidental-noise': {'h2-pond', 'h2-thawing'},
    }
    meaning = [case for case in cases if case['kind'] in MEANING_KINDS]
    expected = {'h2-pond': {'n20', 'n21', 'n22'}, 'h2-thawing': {'n19'}}
    for name in ('lexical', 'context-concat', 'lexical-context'):
        result = pairs.run(arms.ARMS[name](store, data), meaning, refs)
        reverse = {ident: ref for ref, ident in refs.items()}
        observed = {row['id']: {reverse[ident] for ident in row['returned']}
                    for row in result['rows'] if row['returned']}
        assert observed == expected, name
        print(f'{name} heldout meaning incidental hits: {observed}')


def test_misleading_context_is_retrievable_and_report_fallback(data, seeded, record_property):
    store, refs = seeded
    cases = [case for case in fresh(data) if case.get('context') and case.get('irrelevant')]
    assert len(cases) >= 3
    concat = pairs.run(arms.context_concat(store, data), cases, refs)
    for case, row in zip(cases, concat['rows']):
        assert {refs[ref] for ref in case['irrelevant']} <= set(row['returned']), case['id']
    # Observe context_aware's harm direction without pinning its success/failure.
    observed = pairs.run(arms.context_aware(store, data), cases, refs)
    reverse = {ident: ref for ref, ident in refs.items()}
    diagnostics = [dict(row, returned=[reverse[ident] for ident in row['returned']])
                   for row in observed['rows']]
    record_property('context_aware_misleading_context', json.dumps(diagnostics, ensure_ascii=False))
    print('context_aware misleading context: ' + json.dumps(diagnostics, ensure_ascii=False))


def test_compare_still_refuses_versions_and_fixture_digests(data, seeded):
    store, refs = seeded
    result = pairs.run(arms.lexical(store, data), data['cases'], refs)
    changed = copy.deepcopy(result)
    changed['version'] = pairs.VERSION
    with pytest.raises(pairs.RecallPairsError, match='versions'):
        pairs.compare(result, changed)
    changed = copy.deepcopy(result)
    changed['fixture'] = 'different-v2-contents'
    with pytest.raises(pairs.RecallPairsError, match='contents'):
        pairs.compare(result, changed)
