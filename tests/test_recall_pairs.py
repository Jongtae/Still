"""Synthetic fixture/harness evidence only; no live model or scheduler calls."""
import copy
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import recall_pairs as pairs, scenarios  # noqa: E402
from personal_agent.owner_forget import OwnerForget  # noqa: E402
from personal_agent.memory_service import MemoryService  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402

FIXTURE = ROOT / 'evals/recall/recall-pairs-v1.json'


@pytest.fixture
def data():
    return pairs.load(FIXTURE)


@pytest.fixture
def seeded(tmp_path, data):
    store = QuickStore(tmp_path / 'state')
    return store, pairs.seed(store, data)


def lexical(store, data, read_context=False):
    """Today's path: MemoryService.search_memories on the request text, on the fixture's date."""
    service = MemoryService(store, private_read_sink=lambda marker: None)
    service._today = lambda: data['today']

    def retrieve(query, context=''):
        text = f'{context} {query}' if read_context else query
        return [row['id'] for row in service.search_memories(data['owner'], text)['memories']]
    return retrieve


def test_fixture_shape_and_coverage(data):
    assert pairs.validate(data) is data
    assert len(data['rows']) == 25
    assert Counter(c['split'] for c in data['cases']) == {'dev': 14, 'heldout': 10}
    assert {c['kind'] for c in data['cases']} == pairs.KINDS
    for case in data['cases']:
        if case['kind'] == 'harm-control':
            assert case['needed'] == case['traps'] == []
            assert case['irrelevant']


@pytest.mark.parametrize('where,key,value', [
    ('root', 'unknown', True), ('root', 'version', 'recall-pairs/2'),
    ('root', 'today', '2026-02-30'), ('root', 'today', '20260325'),
    ('root', 'owner', ''), ('root', 'rows', {}), ('root', 'cases', []),
    ('row', 'unknown', 1), ('row', 'ref', 'r2'), ('row', 'key', 'not-profile'),
    ('row', 'corrected', 'true'), ('row', 'forgotten', 1),
    ('row', 'valid_until', 'tomorrow'), ('row', 'after', 'missing'),
    ('case', 'unknown', 1), ('case', 'id', 'd-lex-desk'),
    ('case', 'split', 'train'), ('case', 'kind', 'made-up'),
    ('case', 'split', []), ('case', 'kind', {}),
    ('case', 'query', ''), ('case', 'needed', ['missing']),
    ('case', 'needed', ['r9']), ('case', 'needed', ['r11']),
    ('case', 'needed', ['r5']), ('case', 'traps', ['r1']),
    ('case', 'traps', ['r13']), ('case', 'needed', 'r1'),
    ('case', 'needed', ['r1', 'r1']), ('case', 'irrelevant', ['r11']),
    ('case', 'irrelevant', ['r1']),
])
def test_malformed_variants_raise(data, where, key, value):
    target = data if where == 'root' else data['rows'][0] if where == 'row' else data['cases'][0]
    target[key] = value
    with pytest.raises(pairs.RecallPairsError):
        pairs.validate(data)


def test_missing_fields_and_bad_json(data, tmp_path):
    for target in (data, data['rows'][0], data['cases'][0]):
        broken = copy.deepcopy(target)
        broken.pop(next(iter(broken)))
        fixture = copy.deepcopy(data)
        if target is data:
            fixture = broken
        elif target is data['rows'][0]:
            fixture['rows'][0] = broken
        else:
            fixture['cases'][0] = broken
        with pytest.raises(pairs.RecallPairsError):
            pairs.validate(fixture)
    path = tmp_path / 'bad.json'
    path.write_text('{')
    with pytest.raises(pairs.RecallPairsError):
        pairs.load(path)
    with pytest.raises(pairs.RecallPairsError):
        pairs.load(tmp_path / 'missing.json')


def test_seed_real_states_and_lexical_authority_invariant(data, seeded):
    store, refs = seeded
    states = {ref: store.memory(ident, owner_id=data['owner'], current_only=False)['state']
              for ref, ident in refs.items()}
    assert states['r5'] == states['r19'] == 'superseded'
    assert states['r7'] == states['r21'] == 'corrected'
    assert states['r10'] == states['r11'] == states['r24'] == 'forgotten'
    assert states['r9'] == 'current'  # Expiry is distinct from row state.
    result = pairs.run(lexical(store, data), data['cases'], refs)
    for row in result['rows']:
        assert row['trap_hits'] == 0, row['id']
        if row['kind'] == 'lexical-control':
            assert row['needed_hit'] == row['needed_total'], row['id']
    summary = pairs.score(result)
    assert set(summary) == {'dev', 'heldout'}
    assert summary['dev']['total']['cases'] == 14
    assert summary['heldout']['total']['cases'] == 10
    for split in summary.values():
        assert split['by_kind']['lexical-control']['needed_recall'] == 1
        assert split['total']['cases_with_trap'] == 0


def test_paired_fixes_authority_override_and_split_separation(data, seeded):
    _, refs = seeded
    cases = [case for case in data['cases'] if case['kind'] == 'paraphrase']
    needed = {case['query']: [refs[ref] for ref in case['needed']] for case in cases}
    before = pairs.run(lambda query, context='': [], cases, refs)
    after = pairs.run(lambda query, context='': needed[query], cases, refs)
    comparison = pairs.compare(before, after)
    assert {row['id'] for row in comparison['dev']['changes']} == {'d-para-focus', 'd-para-posture'}
    assert {row['id'] for row in comparison['heldout']['changes']} == {'h-para-layout'}
    assert all(row['change'] == 'fixed' for split in comparison.values() for row in split['changes'])
    dangerous = pairs.run(lambda query, context='': needed[query] + [refs['r24']], cases, refs)
    authority = pairs.compare(before, dangerous)
    for split in ('dev', 'heldout'):
        assert len(authority[split]['trap_regressions']) == len(comparison[split]['changes'])
        assert all(row['change'] == 'regressed' for row in authority[split]['changes'])
    wrong_version = copy.deepcopy(after)
    wrong_version['version'] = 'recall-pairs/2'
    with pytest.raises(pairs.RecallPairsError, match='versions'):
        pairs.compare(before, wrong_version)
    different_cases = copy.deepcopy(after)
    different_cases['rows'].pop()
    with pytest.raises(pairs.RecallPairsError, match='selections'):
        pairs.compare(before, different_cases)
    different_fixture = copy.deepcopy(after)
    different_fixture['fixture'] = 'another-fixture'
    with pytest.raises(pairs.RecallPairsError, match='contents'):
        pairs.compare(before, different_fixture)


def test_all_four_paired_verdicts(data, seeded):
    _, refs = seeded
    cases = data['cases'][:4]
    expected = {case['query']: [refs[ref] for ref in case['needed']] for case in cases}
    a = pairs.run(lambda q, c='': expected[q] if q in (cases[0]['query'], cases[2]['query']) else [], cases, refs)
    b = pairs.run(lambda q, c='': expected[q] if q in (cases[0]['query'], cases[1]['query']) else [], cases, refs)
    assert [r['change'] for r in pairs.compare(a, b)['dev']['changes']] == [
        'unchanged-pass', 'fixed', 'regressed', 'unchanged-fail']


def test_noise_is_not_an_authority_trap_and_ids_are_deduplicated(data, seeded):
    _, refs = seeded
    cases = [case for case in data['cases'] if case['kind'] == 'harm-control']
    values = {case['query']: [refs[ref] for ref in case['irrelevant']] for case in cases}
    clean = pairs.run(lambda q, c='': [], cases, refs)
    noisy = pairs.run(lambda q, c='': values[q] * 3, cases, refs)
    for row in noisy['rows']:
        assert (row['needed_hit'], row['needed_total'], row['trap_hits'], row['noise_hits']) == (0, 0, 0, 1)
        assert len(row['returned']) == 1
    summary = pairs.score(noisy)
    for split in ('dev', 'heldout'):
        assert summary[split]['total']['needed_recall'] is None
        assert summary[split]['total']['noise_count'] == 1
        assert pairs.compare(clean, noisy)[split]['trap_regressions'] == []
        assert pairs.compare(clean, noisy)[split]['changes'][0]['change'] == 'regressed'


def test_eligibility_comes_from_store_even_after_seed(data, seeded):
    store, refs = seeded
    case = copy.deepcopy(data['cases'][0])
    OwnerForget(store).forget(data['owner'], 'memory:' + refs['r1'])
    result = pairs.run(lambda q, c='': [refs['r1']], [case], refs)['rows'][0]
    assert result['needed_hit'] == 0
    assert result['trap_hits'] == 1
    # Relabeling a forbidden id as needed cannot create credit, either.
    case['needed'] = ['r9', 'r5', 'r7', 'r24']
    result = pairs.run(lambda q, c='': [refs[ref] for ref in case['needed']], [case], refs)['rows'][0]
    assert (result['needed_hit'], result['needed_total'], result['trap_hits']) == (0, 4, 4)
    with pytest.raises(pairs.RecallPairsError):
        pairs.run(lambda q, c='': [{'id': refs['r1'], 'state': 'current'}], [case], refs)


def test_score_uses_weighted_recall_and_never_pools_splits(data, seeded):
    _, refs = seeded
    cases = copy.deepcopy(data['cases'][:2])
    cases[0]['needed'] = ['r1', 'r2']
    result = pairs.run(lambda q, c='': [refs['r1']] if q == cases[0]['query'] else [], cases, refs)
    score = pairs.score(result)
    assert score['dev']['total']['needed_recall'] == 1 / 3
    assert score['dev']['by_kind']['lexical-control']['needed_recall'] == 1 / 3
    assert score['heldout']['total']['needed_recall'] is None


def test_seed_expiry_does_not_depend_on_wall_clock(tmp_path, data):
    data['rows'] = [
        dict(ref='old', key='profile.test', content='old value', valid_until='2099-01-01'),
        dict(ref='new', key='profile.test', content='new value', after='old'),
    ]
    data['cases'] = [dict(id='expiry-cleared', split='dev', kind='dated-contradiction',
                          query='value', needed=['new'], traps=['old'], note='New fact has no end date.')]
    store = QuickStore(tmp_path / 'expiry')
    refs = pairs.seed(store, data)
    assert not store.memory(refs['new'], owner_id=data['owner'])['valid_until']
    assert pairs.run(lambda q, c='': [refs['new']], data['cases'], refs)['rows'][0]['needed_hit'] == 1


@pytest.mark.parametrize('filename,prefix,tag,counts', [
    ('recall-experiments.json', 'recall-', 'exp-recall', {'dev': 8, 'heldout': 6}),
    ('continuity-experiments.json', 'line-', 'exp-continuity', {'dev': 5, 'heldout': 3}),
])
def test_bundled_scenarios_load(filename, prefix, tag, counts):
    raw = json.loads((ROOT / 'evals/scenarios' / filename).read_text())
    normalized = [scenarios.validate(row) for row in raw]
    loaded = {row['id']: row for row in scenarios.load(include_local=False)}
    assert Counter(row['split'] for row in normalized) == counts
    for row in normalized:
        assert row['id'].startswith(prefix)
        assert row['id'] in loaded
        assert tag in row['tags']
        assert sum(t.startswith('kind-') for t in row['tags']) == 1
        if prefix == 'line-':
            assert row['expect']['preparation']
        for turn in row['turns']:
            if turn['note']:
                assert turn['note_in_message']
                assert turn['note'] in scenarios.owner_message(turn)


def test_short_follow_ups_need_context_and_reading_it_recalls_them(data, seeded):
    store, refs = seeded
    follow = [case for case in data['cases'] if case['kind'] == 'short-follow-up']
    assert follow and all(case.get('context') for case in follow)
    broken = copy.deepcopy(data)
    next(case for case in broken['cases'] if case['kind'] == 'short-follow-up').pop('context')
    with pytest.raises(pairs.RecallPairsError, match='context'):
        pairs.validate(broken)
    # The cases are passable honestly: a retriever that reads the preceding turn recalls them.
    with_context = pairs.run(lexical(store, data, read_context=True), follow, refs)
    assert all(row['needed_hit'] == row['needed_total'] and not row['trap_hits'] for row in with_context['rows'])


def test_a_retriever_that_writes_to_the_store_is_refused(data, seeded):
    store, refs = seeded
    forgotten = next(row for row in data['rows'] if row.get('forgotten'))

    def resave(query, context=''):
        saved = store.save_memory(forgotten['key'], forgotten['content'], owner_id=data['owner'], work_id='retriever')
        return [saved['id']]
    with pytest.raises(pairs.RecallPairsError, match='wrote to the store'):
        pairs.run(resave, data['cases'][:1], refs)
