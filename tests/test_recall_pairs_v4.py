"""Model-free v4 fixture qualification; no candidate or live-model evidence.

Reuse review: N/A for data/content generation and fixture assertions: no new
runtime component, abstraction, dependency or retrieval case/branch. Stdlib
only apart from the existing pytest/store APIs; adopt v3's leakage/stem scans,
real FTS5 load/seed/run, unchanged scorer and MemoryService snapshot bounds.
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

from test_recall_pairs_v3 import (
    BASELINES, FUNCTION_WORDS, MEANING, assert_no_needed_overlap, facets, stem, terms,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import recall_arms as arms, recall_pairs as pairs  # noqa: E402
from personal_agent.memory_service import (  # noqa: E402
    MemoryService, PROFILE_SNAPSHOT_BYTES, PROFILE_SNAPSHOT_CHARS,
)
from personal_agent.quickstart_store import QuickStore  # noqa: E402

FIXTURE = ROOT / 'evals/recall/recall-pairs-v4.json'
STATES = {'cultures': 'superseded', 'meteorites': 'corrected',
          'cheeses': 'expired', 'relays': 'superseded',
          'kx': 'corrected', 'ky': 'superseded'}


@pytest.fixture(scope='module')
def builder():
    spec = importlib.util.spec_from_file_location('recall_build_v4', FIXTURE.parent / 'build_v4.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def data():
    return pairs.load(FIXTURE)


def fresh(data):
    return [case for case in data['cases'] if case['split'] == 'heldout']


@pytest.fixture(scope='module')
def seeded(tmp_path_factory, data):
    store = QuickStore(tmp_path_factory.mktemp('recall-v4') / 'state')
    assert store.memory_search_available, 'Must exercise FTS5, not the LIKE fallback'
    return store, pairs.seed(store, data)


@pytest.fixture(scope='module')
def results(data, seeded):
    store, refs = seeded
    return {name: pairs.run(arms.ARMS[name](store, data), fresh(data), refs)
            for name in BASELINES}


def test_claude_verbatim_source_constants_pinned(builder):
    # SHA-256 of the supplied spec's rows, queries and contexts mapped to these
    # three constants, json.dumps(ensure_ascii=False). No /tmp source required.
    text = json.dumps([builder.CLAUDE_CLUSTERS, builder.CLAUDE_CLUSTER_CASES,
                       builder.CLAUDE_TARGETS], ensure_ascii=False)
    assert hashlib.sha256(text.encode('utf-8')).hexdigest() == (
        '290702b8c7a8883eb5f87d81f82a3b5c6f819e26d06842f65bb4534eb847cc6b')


def test_heldout_authors(data):
    observed = Counter()
    for case in fresh(data):
        tags = [tag for tag in facets(case) if tag.startswith('author:')]
        expected = 'claude' if case['id'].startswith('h4c-') else 'gpt'
        assert tags == [f'author:{expected}']
        assert case['note'].startswith(f'[author:{expected}] ')
        assert case['id'].startswith({'gpt': 'h4-', 'claude': 'h4c-'}[expected])
        observed[expected] += 1
    assert observed == {'gpt': 24, 'claude': 12}
    assert all(not any(tag.startswith('author:') for tag in facets(case))
               for case in data['cases'] if case['split'] == 'dev')


def test_load_schema_and_version_allowlist(data):
    assert pairs.load(FIXTURE) == data
    assert pairs.validate(data) is data
    assert data['version'] == 'recall-pairs/4'
    assert pairs.VERSION == 'recall-pairs/1'
    assert pairs.VERSIONS == tuple(f'recall-pairs/{v}' for v in range(1, 5))
    with pytest.raises(pairs.RecallPairsError, match='Unsupported fixture version'):
        pairs.validate(dict(data, version='recall-pairs/5'))


def test_prior_fixtures_pinned_and_v3_rows_cases_carried_exactly(data):
    digests = {
        1: '44392385d948b6193e1509e7897054f2d059fa7599d1e35f6d7d9b2b838cb0ea',
        2: '8d26395284b42af73110cb05d1be7811197a639db9397b0580c31580a902b712',
        3: 'f2bdbb5aa9f00ba138dd6b2c45853c073634a654bfff78c20139cbb00420a2de',
    }
    for version, digest in digests.items():
        raw = (FIXTURE.parent / f'recall-pairs-v{version}.json').read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
    v3 = pairs.load(FIXTURE.parent / 'recall-pairs-v3.json')
    assert (data['owner'], data['today']) == (v3['owner'], v3['today'])
    old_refs = {row['ref'] for row in v3['rows']}
    carried = [row for row in data['rows'] if row['ref'] in old_refs]
    assert carried == v3['rows']
    # Also pin each object's serialized field order, beyond dict equality.
    assert json.dumps(carried, ensure_ascii=False) == json.dumps(v3['rows'], ensure_ascii=False)
    assert data['cases'][:36] == [dict(case, split='dev') for case in v3['cases']]
    assert json.dumps(data['cases'][:36], ensure_ascii=False) == json.dumps(
        [dict(case, split='dev') for case in v3['cases']], ensure_ascii=False)
    assert len(data['cases']) == 72
    assert len(fresh(data)) == 36
    assert all(case['id'].startswith(('h4-', 'h4c-')) for case in fresh(data))
    assert all(ref.startswith(('t4-', 'c4-', 't4c-', 'c4c-')) for case in fresh(data)
               for group in ('needed', 'traps', 'irrelevant') for ref in case.get(group, []))


def test_new_rows_order_size_keys_and_language(data):
    rows = data['rows']
    assert len(rows) == len({row['ref'] for row in rows}) == 1078
    content = '\n'.join(row['content'] for row in rows)
    assert (len(content), len(content.encode('utf-8'))) == (67690, 101812)
    new = [row for row in rows if row['ref'].startswith(('t4-', 'c4-', 't4c-', 'c4c-'))]
    assert len(new) == 78
    assert all(MemoryService.is_profile_key(row['key']) for row in rows)
    for row in new:
        assert 20 <= len(row['content']) <= 120
        assert not any(ch in row['content'] for ch in '\r\n')
        assert bool(re.search('[가-힣]', row['content'])) != bool(re.search('[A-Za-z]', row['content']))
    standalones = [row for row in new if row['ref'].startswith(('t4-', 't4c-'))]
    assert len(standalones) == 24
    assert [row['key'] for row in standalones] == (
        [f'profile.v4.zzcut.t{i:02d}' for i in range(16)] +
        [f'profile.v4.zzcut.u{i:02d}' for i in range(8)])
    first_filler = next(i for i, row in enumerate(rows) if row['ref'].startswith('f3-'))
    assert rows[first_filler - 24:first_filler] == standalones
    assert all(row['ref'].startswith('c4-') for row in rows[-54:-18])
    assert all(row['ref'].startswith('c4c-') for row in rows[-18:])
    assert rows[-55]['ref'] == 'c3-cylinders-7'
    assert len({row['content'] for row in new}) == len(new)


@pytest.mark.parametrize('split,expected', [
    ('dev', {('cluster', 'paraphrase'): 6, ('cluster', 'cross-language'): 6,
             ('cut', 'cross-language'): 6, ('cut', 'paraphrase'): 4,
             ('cut', 'short-follow-up'): 5, ('particle', 'paraphrase'): 3,
             ('control', 'lexical-control'): 3, ('control', 'harm-control'): 3}),
    ('heldout', {('cluster', 'paraphrase'): 6, ('cluster', 'cross-language'): 6,
                 ('cut', 'cross-language'): 6, ('cut', 'paraphrase'): 5,
                 ('cut', 'short-follow-up'): 5, ('particle', 'paraphrase'): 3,
                 ('control', 'lexical-control'): 2, ('control', 'harm-control'): 3}),
])
def test_exact_split_kind_facet_counts(split, expected, data):
    cases = [case for case in data['cases'] if case['split'] == split]
    assert len(cases) == {'dev': 36, 'heldout': 36}[split]
    assert Counter((next((f for f in facets(case) if f in {'cluster', 'cut', 'particle'}), 'control'),
                    case['kind']) for case in cases) == expected
    assert Counter(case['kind'] for case in cases) == {
        'paraphrase': {'dev': 13, 'heldout': 14}[split],
        'cross-language': {'dev': 12, 'heldout': 12}[split],
        'short-follow-up': {'dev': 5, 'heldout': 5}[split],
        'lexical-control': {'dev': 3, 'heldout': 2}[split],
        'harm-control': {'dev': 3, 'heldout': 3}[split],
    }
    counts = Counter(f for case in cases for f in facets(case))
    assert counts['near-trap'] == {'dev': 12, 'heldout': 12}[split]
    assert counts['misleading-context'] == 1
    assert counts['incidental-noise'] == 0


def test_fresh_languages_followups_and_single_needed_row(data):
    rows = {row['ref']: row for row in data['rows']}
    for facet in ('cluster', 'cut'):
        directions = Counter()
        for case in fresh(data):
            if case['kind'] != 'cross-language' or facet not in facets(case):
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
        assert directions == {'en-ko': 3, 'ko-en': 3}
    for case in fresh(data):
        if case['kind'] in MEANING:
            assert len(case['needed']) == 1
        if case['kind'] == 'paraphrase':
            assert bool(re.search('[가-힣]', case['query'])) == bool(
                re.search('[가-힣]', rows[case['needed'][0]]['content']))
    # These complete contexts identify the referent, without an answer value.
    assert {case['id']: case['context'] for case in fresh(data)
            if case['kind'] == 'short-follow-up'} == {
        'h4-lacrosse': '십자봉 구기 종목의 포획용 실주머니 건으로 돌아가자.',
        'h4-hammock': '매달아 누워 쉬는 그네 양단 버팀목에 대한 논의야.',
        'h4-zither': '납작한 발현악기 울림통 금갔는지 살피던 얘기.',
        'h4c-drill': '천공 연장 전원팩 얘기.',
        'h4c-fountainpen': 'Refillable writing tool, metal tip, again.',
    }


def test_six_clusters_eight_current_siblings_and_real_traps(data, seeded):
    store, refs = seeded
    for slug, state in STATES.items():
        prefix = 'c4c' if slug in {'kx', 'ky'} else 'c4'
        siblings = {f'{prefix}-{slug}-{i}' for i in range(8)}
        assert {row['ref'] for row in data['rows'] if row['ref'].startswith(f'{prefix}-{slug}-')} == (
            siblings | {f'{prefix}-{slug}-old'})
        assert all(refs.eligible(refs[ref]) for ref in siblings)
        trap = f'{prefix}-{slug}-old'
        actual = store.memory(refs[trap], owner_id=data['owner'], current_only=False)
        assert not refs.eligible(refs[trap])
        if state == 'expired':
            assert actual['state'] == 'current'
            assert actual['valid_until'] == '2026-03-01' < data['today']
        else:
            assert actual['state'] == state
        cases = [case for case in fresh(data) if set(case['needed']) & siblings]
        assert len(cases) == 2
        for i, case in enumerate(cases):
            assert case['needed'] == [f'{prefix}-{slug}-{i}']
            assert set(case['irrelevant']) == siblings - set(case['needed'])
            assert case['traps'] == [trap]
            result = pairs.run(lambda query, context: [refs[trap]], [case], refs)
            hit = result['rows'][0]
            assert hit['trap_hits'] == 1 and hit['needed_hit'] == 0
            assert pairs.score(result)['heldout']['total']['cases_with_trap'] == 1


def test_fresh_meaning_forward_leakage_uses_v3_scan(data):
    assert_no_needed_overlap(dict(data, cases=fresh(data)))


@pytest.mark.parametrize('source', ['query', 'context'])
@pytest.mark.parametrize('field', ['content', 'key'])
@pytest.mark.parametrize('term,token', [('KEST', 'kestrel'), ('73', '731'),
                                     ('라벤', '라벤더'), ('a', 'alphabet')])
def test_scan_rejects_unbounded_leakage(source, field, term, token, data):
    bad = copy.deepcopy(dict(data, cases=fresh(data)))
    case = bad['cases'][0]
    case['needed'].append('t4-origami')
    row = next(row for row in bad['rows'] if row['ref'] == case['needed'][-1])
    row[field] = f'profile.v4.{token}' if field == 'key' else token
    case[source] = ' '.join(f'absent{i}' for i in range(13)) + ' ' + term
    with pytest.raises(AssertionError, match=case['id']):
        assert_no_needed_overlap(bad)


def test_particle_reverse_stem_exceptions_are_exact(data):
    rows = {row['ref']: row for row in data['rows']}
    expected = {'h4-thimble': ('골무를', '골무'), 'h4-inkwell': ('잉크병에서', '잉크병'),
                'h4c-millstone': ('맷돌을', '맷돌')}
    observed = {}
    for case in fresh(data):
        if case['kind'] not in MEANING:
            continue
        reverse = [(query, token)
                   for query in terms(case['query'] + ' ' + case.get('context', ''))
                   for ref in case['needed'] for field in ('content', 'key')
                   for token in terms(rows[ref][field]) if query.startswith(token)]
        if 'particle' in facets(case):
            assert reverse == [expected[case['id']]]
            assert len(reverse[0][0]) > len(reverse[0][1])
            observed[case['id']] = reverse[0]
        else:
            assert not reverse, (case['id'], reverse)
    assert observed == expected


@pytest.mark.parametrize('name', BASELINES)
def test_fresh_meaning_zero_needed_hits_traps_and_unlabelled_noise(name, data, results):
    cases = {case['id']: case for case in fresh(data)}
    for row in results[name]['rows']:
        assert row['trap_hits'] == 0
        if cases[row['id']]['kind'] in MEANING:
            assert row['needed_total'] == 1
            assert row['needed_hit'] == 0, (name, row['id'])
            assert row['noise_hits'] == 0, (name, row['id'], row['returned'])


def test_fresh_controls_lexical_pass_no_noise_and_real_harm_lures(data, seeded, results):
    store, refs = seeded
    cases = {case['id']: case for case in data['cases']}
    carried_controls = [case for case in data['cases']
                        if case['split'] == 'dev' and case['kind'] == 'lexical-control']
    assert len(carried_controls) == 3
    carried = pairs.run(arms.lexical(store, data), carried_controls, refs)
    for row in results['lexical']['rows'] + carried['rows']:
        case = cases[row['id']]
        if case['kind'] == 'lexical-control':
            assert row['needed_hit'] == row['needed_total'] == 1
            assert row['noise_hits'] == row['trap_hits'] == 0
            assert row['returned'] == [refs[case['needed'][0]]]
        if case['kind'] == 'harm-control':
            assert case['needed'] == [] and case['irrelevant']
    case = cases['h4-syntax']
    assert refs[case['irrelevant'][0]] in arms.lexical(store, data)(case['query'])
    case = cases['h4-eclipse']
    assert refs[case['irrelevant'][0]] in arms.context_concat(store, data)(case['query'], case['context'])
    case = cases['h4c-lightning']
    assert case['needed'] == [] and case['irrelevant'] == ['t4c-lightning']
    assert refs[case['irrelevant'][0]] in arms.lexical(store, data)(case['query'])


def test_snapshot_cuts_all_fresh_cut_and_particle_targets(data, seeded, record_property):
    store, refs = seeded
    service = MemoryService(store, private_read_sink=MemoryService.NO_EGRESS_GUARD)
    service._today = lambda: data['today']
    assert PROFILE_SNAPSHOT_CHARS == PROFILE_SNAPSHOT_BYTES == 12000
    current = [store.memory(ident, owner_id=data['owner'], current_only=False)
               for ident in refs.values() if refs.eligible(ident)]
    content = '\n'.join(row['content'] for row in current)
    chars, byte_count = len(content), len(content.encode('utf-8'))
    assert (len(current), chars, byte_count) == (1053, 66811, 100373)  # README measurements
    assert chars >= 4 * PROFILE_SNAPSHOT_CHARS
    assert byte_count >= 4 * PROFILE_SNAPSHOT_BYTES
    record_property('eligible_content_chars', chars)
    record_property('eligible_content_utf8_bytes', byte_count)
    candidate_ids = {row['id'] for row in service._profile_rows(data['owner'], holding_on=data['today'])}
    assert len(candidate_ids) == 500
    cuts = [case for case in fresh(data) if {'cut', 'particle'} & set(facets(case))]
    assert len(cuts) == 19
    for case in cuts:
        # Request only: no context concatenation or rewritten selector.
        snapshot = service.profile_snapshot(data['owner'], query=case['query'])
        needed = {refs[ref] for ref in case['needed']}
        assert not needed & candidate_ids, case['id']
        assert not needed & {entry['id'] for entry in snapshot['entries']}, case['id']
        assert snapshot['truncated'] and snapshot['omitted_count'] > 0
        assert len(snapshot['text']) <= PROFILE_SNAPSHOT_CHARS
        assert len(snapshot['text'].encode('utf-8')) <= PROFILE_SNAPSHOT_BYTES


def test_fresh_subjects_and_query_stems_separate_from_all_36_dev_cases(data):
    words, subjects = {}, {}
    for split in ('dev', 'heldout'):
        cases = [case for case in data['cases'] if case['split'] == split]
        assert len(cases) == {'dev': 36, 'heldout': 36}[split]
        words[split] = {stem(word) for case in cases for word in terms(case['query'])
                        if word not in FUNCTION_WORDS and stem(word) not in FUNCTION_WORDS}
        subjects[split] = {ref.rsplit('-', 1)[0] if ref.startswith(('c3-', 'c4-', 'c4c-')) else ref
                           for case in cases for ref in case['needed'] + case.get('irrelevant', [])}
    assert not subjects['dev'] & subjects['heldout']
    shared = sorted((a, b) for a in words['dev'] for b in words['heldout']
                    if a.startswith(b) or b.startswith(a))
    assert not shared, shared


def test_generator_check_and_repeatable_build(data, tmp_path):
    script = FIXTURE.parent / 'build_v4.py'
    spec = importlib.util.spec_from_file_location('recall_build_v4', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.build() == data
    assert module.render() == module.render() == FIXTURE.read_bytes()
    result = subprocess.run([sys.executable, str(script), '--check'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'byte-identical' in result.stdout
    # Check-mode mismatch fails without writing; exercise the same main entry
    # against an isolated output, never editing the locked source fixture.
    module.OUTPUT = tmp_path / 'fixture.json'
    module.OUTPUT.write_bytes(b'mismatch\n')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, 'argv', [str(script), '--check'])
        with pytest.raises(SystemExit) as failure:
            module.main()
    assert failure.value.code == 1
    assert module.OUTPUT.read_bytes() == b'mismatch\n'
