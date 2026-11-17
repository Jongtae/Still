"""Model-free fixture qualification; no candidate tuning or live/model calls.

Reuse: data-only extension; stdlib/pytest assertions adopt the v2 leakage
scan, real load/seed/run APIs, FTS5 arms and MemoryService snapshot. The caller
separately approved the v3 loader allowlist entry. No scoring or gate is changed.
"""
import copy
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import recall_arms as arms, recall_pairs as pairs  # noqa: E402
from personal_agent.memory_service import (  # noqa: E402
    MemoryService, PROFILE_SNAPSHOT_BYTES, PROFILE_SNAPSHOT_CHARS,
)
from personal_agent.quickstart_store import QuickStore  # noqa: E402

FIXTURE = ROOT / 'evals/recall/recall-pairs-v3.json'
MEANING = {'paraphrase', 'cross-language', 'short-follow-up'}
BASELINES = ['lexical', 'context-concat', 'lexical-context']


def terms(text):
    return re.findall(r'[^\W_]+', text.casefold())


def facets(case):
    return re.findall(r'\[([^]]+)\]', re.match(r'(?:\[[^]]+\]\s*)*', case['note'])[0])


@pytest.fixture(scope='module')
def data():
    return pairs.load(FIXTURE)


@pytest.fixture(scope='module')
def seeded(tmp_path_factory, data):
    store = QuickStore(tmp_path_factory.mktemp('recall-v3') / 'state')
    assert store.memory_search_available, 'Must exercise FTS5, not the LIKE fallback'
    refs = pairs.seed(store, data)
    return store, refs


@pytest.fixture(scope='module')
def results(data, seeded):
    store, refs = seeded
    return {name: pairs.run(arms.ARMS[name](store, data), data['cases'], refs)
            for name in BASELINES}


def test_v3_loads_through_normal_schema_and_unknown_version_is_rejected(data):
    assert pairs.load(FIXTURE) == data
    assert pairs.validate(data) is data
    unknown = dict(data, version='recall-pairs/5')
    with pytest.raises(pairs.RecallPairsError, match='Unsupported fixture version'):
        pairs.validate(unknown)


def test_prior_fixtures_byte_identical_and_first_57_rows_preserved(data):
    digests = {
        1: '44392385d948b6193e1509e7897054f2d059fa7599d1e35f6d7d9b2b838cb0ea',
        2: '8d26395284b42af73110cb05d1be7811197a639db9397b0580c31580a902b712',
    }
    for version, digest in digests.items():
        raw = (FIXTURE.parent / f'recall-pairs-v{version}.json').read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
    v2raw = (FIXTURE.parent / 'recall-pairs-v2.json').read_bytes()
    v2 = json.loads(v2raw)
    assert len(v2['rows']) == 57
    assert data['rows'][:57] == v2['rows']
    # Actual serialized bytes too: flags, field order, spaces and row order.
    old_rows = v2raw.split(b'"rows": [\n', 1)[1].split(b'\n  ],\n  "cases":', 1)[0]
    new_rows = FIXTURE.read_bytes().split(b'"rows": [\n', 1)[1]
    assert new_rows.startswith(old_rows + b',\n')
    assert data['version'] == 'recall-pairs/3'
    assert data['today'] == v2['today']
    assert not {case['query'] for case in data['cases']} & {case['query'] for case in v2['cases']}


def test_rows_and_language_balance(data):
    assert len(data['rows']) == 1000
    assert len({row['ref'] for row in data['rows']}) == 1000
    for row in data['rows']:
        assert MemoryService.is_profile_key(row['key'])
    new = data['rows'][57:]
    assert all(20 <= len(row['content']) <= 120 and '\n' not in row['content'] for row in new)
    korean = sum(bool(re.search('[가-힣]', row['content'])) for row in data['rows'])
    assert 450 <= korean <= 550
    fillers = [row for row in new if row['ref'].startswith('f3-')]
    assert len(fillers) == 865
    assert len({row['key'] for row in fillers}) == len(fillers)
    assert len({row['content'] for row in fillers}) == len(fillers)


@pytest.mark.parametrize('split,expected', [
    ('dev', {('cluster', 'paraphrase'): 2, ('cluster', 'cross-language'): 2,
             ('cut', 'cross-language'): 2, ('cut', 'paraphrase'): 1,
             ('cut', 'short-follow-up'): 2, ('particle', 'paraphrase'): 1,
             ('control', 'lexical-control'): 1, ('control', 'harm-control'): 1}),
    ('heldout', {('cluster', 'paraphrase'): 4, ('cluster', 'cross-language'): 4,
                 ('cut', 'cross-language'): 4, ('cut', 'paraphrase'): 3,
                 ('cut', 'short-follow-up'): 3, ('particle', 'paraphrase'): 2,
                 ('control', 'lexical-control'): 2, ('control', 'harm-control'): 2}),
])
def test_exact_split_kind_facet_counts(split, expected, data):
    cases = [case for case in data['cases'] if case['split'] == split]
    assert len(cases) == {'dev': 12, 'heldout': 24}[split]
    assert Counter((next((f for f in facets(case) if f in {'cluster', 'cut', 'particle'}), 'control'),
                    case['kind']) for case in cases) == expected
    assert all(case['kind'] in pairs.KINDS for case in cases)
    counts = Counter(f for case in cases for f in facets(case))
    assert counts['near-trap'] == {'dev': 4, 'heldout': 8}[split]
    assert counts['misleading-context'] == {'dev': 0, 'heldout': 1}[split]
    assert counts['incidental-noise'] == 0


def test_cross_language_directions(data):
    rows = {row['ref']: row for row in data['rows']}
    for split, n in [('dev', 1), ('heldout', 2)]:
        for facet in ('cluster', 'cut'):
            directions = Counter()
            for case in data['cases']:
                if case['split'] != split or case['kind'] != 'cross-language' or facet not in facets(case):
                    continue
                query, content = case['query'], rows[case['needed'][0]]['content']
                if re.search('[가-힣]', query):
                    assert not re.search('[A-Za-z]', query)
                    assert re.search('[A-Za-z]', content) and not re.search('[가-힣]', content)
                    directions['ko-en'] += 1
                else:
                    assert re.search('[A-Za-z]', query)
                    assert re.search('[가-힣]', content) and not re.search('[A-Za-z]', content)
                    directions['en-ko'] += 1
            assert directions == {'en-ko': n, 'ko-en': n}


def test_six_clusters_have_eight_current_siblings_and_actual_traps(data, seeded):
    store, refs = seeded
    clusters = {}
    for row in data['rows']:
        if row['ref'].startswith('c3-') and not row['ref'].endswith('-old'):
            clusters.setdefault(row['ref'].split('-')[1], set()).add(row['ref'])
    assert len(clusters) == 6 and all(len(group) == 8 for group in clusters.values())
    assert all(refs.eligible(refs[ref]) for group in clusters.values() for ref in group)
    states = Counter()
    for slug, siblings in clusters.items():
        trap = f'c3-{slug}-old'
        actual = store.memory(refs[trap], owner_id=data['owner'], current_only=False)
        assert not refs.eligible(refs[trap])
        if actual['state'] == 'current':
            assert actual['valid_until'] < data['today']
            states['expired'] += 1
        else:
            assert actual['state'] in {'corrected', 'superseded'}
            states[actual['state']] += 1
        cases = [case for case in data['cases'] if 'cluster' in facets(case)
                 and set(case['needed']) <= siblings]
        assert len(cases) == 2
        for case in cases:
            assert len(case['needed']) == 1
            assert set(case['irrelevant']) == siblings - set(case['needed'])
            assert case['traps'] == [trap]
            hit = pairs.run(lambda query, context: [refs[trap]], [case], refs)['rows'][0]
            assert hit['trap_hits'] == 1 and hit['needed_hit'] == 0
    assert states == {'superseded': 2, 'corrected': 2, 'expired': 2}


def assert_no_needed_overlap(data):
    rows = {row['ref']: row for row in data['rows']}
    leaks = []
    for case in data['cases']:
        if case['kind'] not in MEANING:
            continue
        assert case['needed']
        if case['kind'] == 'short-follow-up':
            assert case.get('context')
        for source in ('query', 'context'):
            for ref in case['needed']:
                for field in ('content', 'key'):
                    overlaps = [(term, token) for term in terms(case.get(source, ''))
                                for token in terms(rows[ref][field]) if token.startswith(term)]
                    # Particle cases relax only reverse (stored->query) stem
                    # separation; even they must have zero forward leakage.
                    if overlaps:
                        leaks.append((case['id'], source, field, ref, overlaps))
    assert not leaks, leaks


def test_all_meaning_queries_and_contexts_have_zero_forward_prefix_leakage(data):
    assert_no_needed_overlap(data)


@pytest.mark.parametrize('source', ['query', 'context'])
@pytest.mark.parametrize('field', ['content', 'key'])
@pytest.mark.parametrize('term,token', [('KEST', 'kestrel'), ('73', '731'),
                                         ('라벤', '라벤더'), ('a', 'alphabet')])
def test_scan_catches_unbounded_leakage(source, field, term, token, data):
    bad = copy.deepcopy(data)
    case = bad['cases'][0]
    case['needed'].append('t3-metronome')
    row = next(row for row in bad['rows'] if row['ref'] == case['needed'][-1])
    row[field] = f'profile.v3.{token}' if field == 'key' else token
    case[source] = ' '.join(f'absent{i}' for i in range(13)) + ' ' + term
    with pytest.raises(AssertionError, match=case['id']):
        assert_no_needed_overlap(bad)


def test_particle_exception_is_longer_than_stored_stem_and_only_exception(data):
    rows = {row['ref']: row for row in data['rows']}
    expected = {'d3-sieve': ('거름체를', '거름체'),
                'h3-plumb': ('다림추를', '다림추'),
                'h3-crucible': ('도가니에서', '도가니')}
    observed = set()
    for case in data['cases']:
        if case['kind'] not in MEANING:
            continue
        indexed = terms(rows[case['needed'][0]]['content'])
        reverse = [(query, token) for query in terms(case['query'] + ' ' + case.get('context', ''))
                   for token in indexed if query.startswith(token)]
        if 'particle' in facets(case):
            assert reverse == [expected[case['id']]]
            assert len(reverse[0][0]) > len(reverse[0][1])
            observed.add(case['id'])
        else:
            assert not reverse, (case['id'], reverse)
    assert observed == expected.keys()


@pytest.mark.parametrize('name', BASELINES)
def test_zero_needed_hits_on_every_meaning_case_and_no_unlabelled_noise(name, data, results):
    cases = {case['id']: case for case in data['cases']}
    needed_hits, noise = [], []
    for row in results[name]['rows']:
        case = cases[row['id']]
        assert row['trap_hits'] == 0
        if case['kind'] in MEANING:
            assert row['needed_total'] == 1
            if row['needed_hit']:
                needed_hits.append(case['id'])
            if 'incidental-noise' not in facets(case):
                if row['noise_hits']:
                    noise.append((case['id'], row['noise_hits']))
    assert not needed_hits and not noise, (name, needed_hits, noise)


def test_lexical_controls_exact_pass_and_harm_needs_nothing(data, seeded, results):
    store, refs = seeded
    cases = {case['id']: case for case in data['cases']}
    for row in results['lexical']['rows']:
        case = cases[row['id']]
        if case['kind'] == 'lexical-control':
            assert row['needed_hit'] == row['needed_total'] == 1
            assert row['noise_hits'] == row['trap_hits'] == 0
            assert row['returned'] == [refs[case['needed'][0]]]
        if case['kind'] == 'harm-control':
            assert case['needed'] == [] and case['irrelevant']
    for ident in ('d3-timbre', 'h3-fulcrum'):
        case = cases[ident]
        assert refs[case['irrelevant'][0]] in arms.lexical(store, data)(case['query'])
    harm = cases['h3-vacuum']
    assert refs[harm['irrelevant'][0]] in arms.context_concat(store, data)(harm['query'], harm['context'])


def test_snapshot_cuts_store_size_and_cluster_visibility(data, seeded, record_property):
    store, refs = seeded
    service = MemoryService(store, private_read_sink=MemoryService.NO_EGRESS_GUARD)
    service._today = lambda: data['today']
    assert PROFILE_SNAPSHOT_CHARS == PROFILE_SNAPSHOT_BYTES == 12000
    current = [store.memory(ident, owner_id=data['owner'], current_only=False)
               for ident in refs.values() if refs.eligible(ident)]
    content = '\n'.join(row['content'] for row in current)
    chars, byte_count = len(content), len(content.encode('utf-8'))
    assert (len(current), chars, byte_count) == (981, 62982, 94598)  # README measurements
    assert chars >= 4 * PROFILE_SNAPSHOT_CHARS
    assert byte_count >= 4 * PROFILE_SNAPSHOT_BYTES
    # Content alone exceeds 4x, before key/date formatting is charged.
    record_property('eligible_content_chars', chars)
    record_property('eligible_content_utf8_bytes', byte_count)
    print(f'eligible content: {chars} chars / {byte_count} UTF-8 bytes')
    cluster_visibility = {}
    candidate_ids = {row['id'] for row in service._profile_rows(data['owner'], holding_on=data['today'])}
    assert len(candidate_ids) == 500
    for case in data['cases']:
        tags = facets(case)
        if not {'cut', 'cluster'} & set(tags):
            continue
        # Request only, default product bounds, no context/selector expansion.
        snapshot = service.profile_snapshot(data['owner'], query=case['query'])
        shown = {entry['id'] for entry in snapshot['entries']}
        assert snapshot['truncated'] and snapshot['omitted_count'] > 0
        assert len(snapshot['text']) <= PROFILE_SNAPSHOT_CHARS
        assert len(snapshot['text'].encode('utf-8')) <= PROFILE_SNAPSHOT_BYTES
        if 'cut' in tags:
            assert not {refs[ref] for ref in case['needed']} & shown, case['id']
            assert not {refs[ref] for ref in case['needed']} & candidate_ids, case['id']
        if 'cluster' in tags:
            cluster_visibility[case['id']] = bool({refs[ref] for ref in case['needed']} & shown)
    # Observational, not an acceptance threshold for cluster visibility.
    record_property('cluster_needed_shown', json.dumps(cluster_visibility, sort_keys=True))
    print('cluster needed shown:', json.dumps(cluster_visibility, sort_keys=True))


# Conservative content-word check: strip common English inflections and Korean
# particles/endings, then reject prefix-related stems across query splits.
# Only grammatical function words are exempt, never subject vocabulary.
FUNCTION_WORDS = {'a', 'an', 'the', 'of', 'on', 'in', 'at', 'to', 'for', 'from',
                  'with', 'by', 'and', 'or', 'is', 'are', 'do', 'does', 'my', 'i',
                  'it', 'that', 'this', 'what', 'which', 'how', 'when', 'where',
                  '좀', '만', '그', '것', '뒤', '다음', '이후', '얼마나', '정도'}


def stem(term):
    if re.fullmatch('[a-z]+', term):
        for suffix in ('ing', 'ed', 'es', 's'):
            if term.endswith(suffix) and len(term) - len(suffix) >= 4:
                return term[:-len(suffix)]
    if re.search('[가-힣]', term):
        for suffix in ('했던', '에서는', '에서', '으로', '하려던', '할', '한', '을', '를',
                       '은', '는', '이', '가', '에', '의', '만'):
            if term.endswith(suffix) and len(term) - len(suffix) >= 2:
                return term[:-len(suffix)]
    return term


def test_heldout_subjects_and_query_wording_are_separate_from_dev(data):
    words = {}
    subjects = {}
    for split in ('dev', 'heldout'):
        cases = [case for case in data['cases'] if case['split'] == split]
        words[split] = {stem(word) for case in cases for word in terms(case['query'])
                        if word not in FUNCTION_WORDS and stem(word) not in FUNCTION_WORDS}
        subjects[split] = {ref.rsplit('-', 1)[0] if ref.startswith('c3-') else ref
                           for case in cases for ref in case['needed'] + case.get('irrelevant', [])}
    assert not subjects['dev'] & subjects['heldout']
    shared = [(a, b) for a in words['dev'] for b in words['heldout']
              if a.startswith(b) or b.startswith(a)]
    assert not shared, shared


def test_generator_check_is_byte_exact_and_filler_is_label_blind(data):
    result = subprocess.run([sys.executable, str(FIXTURE.parent / 'build_v3.py'), '--check'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'byte-identical' in result.stdout
    spec = importlib.util.spec_from_file_location('recall_build_v3', FIXTURE.parent / 'build_v3.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = list(module.filler_rows(865))
    assert expected == [row for row in data['rows'] if row['ref'].startswith('f3-')]
    module.TARGETS = []
    module.CLUSTER_CASES = []
    assert list(module.filler_rows(865)) == expected
