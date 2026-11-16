"""Model-free R1 v3 plumbing; fixture arms see DEV cases only.

Fake vector math below is authored test data, not embedding quality evidence.
"""
import copy
import json
from pathlib import Path
import subprocess

import pytest

from agentos_eval import recall_pairs as pairs, recall_report as rr
from evals.embedding_spike import r1v3_measure as m
from evals.embedding_spike.r1v3_snapshot import measure, summarize
from personal_agent.memory_service import MemoryService
from personal_agent.quickstart_store import QuickStore

ROOT = Path(__file__).resolve().parents[1]


def synthetic():
    return dict(version='recall-pairs/3', owner='synthetic-v3-unit', today='2026-03-25',
                rows=[dict(ref='a', key='profile.a', content='saffron pendulum'),
                      dict(ref='b', key='profile.b', content='indigo counterweight')],
                cases=[dict(id='d', split='dev', kind='paraphrase', query='clock adjustment',
                            context='prior unrelated subject', needed=['a'], traps=[], note='[cut] test'),
                       dict(id='never-dispatch', split='heldout', kind='paraphrase',
                            query='HELDOUT_SENTINEL', needed=['b'], traps=[], note='test')])


class Matrix(list):
    @property
    def nbytes(self):
        return len(self) * 8

    def __matmul__(self, query):
        return [sum(a*b for a, b in zip(row, query)) for row in self]


class FakeEmbedder:
    def __init__(self):
        self.queries = []

    def embed_passages(self, texts):
        return Matrix([[1.0, 0.0] if 'saffron' in text else [0.0, 1.0] for text in texts])

    def embed_queries(self, texts):
        self.queries.extend(texts)
        assert all('HELDOUT_SENTINEL' not in text for text in texts)
        return [[1.0, 0.0] for text in texts]


def test_grid_and_request_only_fake_sweep(monkeypatch):
    # The full 66-point grid runs against independently authored test data.
    encoder = FakeEmbedder()
    result = m.sweep(synthetic(), embedder=encoder)
    assert len(result['records']) == 66
    assert result['dev_cases'] == ['d']
    assert len(encoder.queries) == 66 and set(encoder.queries) == {'clock adjustment'}
    assert result['fixture_digest'] == rr._fixture_digest(synthetic())
    assert result['selected']['vector']['parameters']['min_sim'] == 0.90
    assert result['selected']['vector']['parameters']['top_k'] == 1
    assert not result['network_attempts']


def record(name='vector', passes=1, noise=0, floor=.8, top_k=1, traps=0):
    return dict(arm=name, passes=passes, parameters=m.parameters(name, top_k, floor),
                score=dict(noise_count=noise, cases_with_trap=traps))


def test_predeclared_selection_order_and_trap_exclusion():
    records = [record(passes=99, traps=1), record(passes=2, noise=5),
               record(passes=1), record(passes=2, noise=4, floor=.9),
               record(passes=2, noise=4, floor=.88), record('hybrid')]
    chosen, primary = m.choose(records)
    assert chosen['vector'] == records[3] and primary == 'vector'
    with pytest.raises(ValueError, match='trap-free'):
        m.choose([record(traps=1), record('hybrid')])


def frozen_data(data):
    records = [record(name, floor=floor, top_k=top_k) for name in m.NAMES
               for top_k in m.GRID['top_k'] for floor in m.GRID['min_sim']]
    selected, primary = m.choose(records)
    return dict(version='r1v3-frozen/1', fixture_digest=rr._fixture_digest(data), split='dev',
                dev_cases=[c['id'] for c in data['cases'] if c['split'] == 'dev'],
                selection=m.SELECTION, grid=m.GRID, records=records, selected=selected,
                primary=primary, model=m.prior.provenance())


def test_frozen_acceptance_checks_git_tracking_and_head_cleanliness(tmp_path):
    data = synthetic()
    path = tmp_path / 'frozen.json'
    path.write_text(json.dumps(frozen_data(data)))
    calls = []

    def git(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '', '')
    assert m.load_frozen(path, data, root=tmp_path, runner=git)['split'] == 'dev'
    assert calls == [['git', 'ls-files', '--error-unmatch', '--', 'frozen.json'],
                     ['git', 'diff', '--exit-code', 'HEAD', '--', 'frozen.json']]


@pytest.mark.parametrize('failure', ['untracked', 'dirty', 'digest', 'version', 'model',
                                    'protocol', 'dev-cases', 'parameters', 'selection', 'outside', 'incomplete-grid'])
def test_frozen_refusals_before_any_inference(failure, tmp_path, monkeypatch):
    data = synthetic()
    value = frozen_data(data)
    if failure == 'digest': value['fixture_digest'] = 'bad'
    if failure == 'version': value['version'] = 'bad'
    if failure == 'model': value['model'] = {}
    if failure == 'protocol': value['selection'] = 'different ordering'
    if failure == 'dev-cases': value['dev_cases'] = ['never-dispatch']
    if failure == 'parameters':
        value['selected']['vector']['parameters']['use_context'] = True
    if failure == 'selection': value['primary'] = 'unknown'
    if failure == 'incomplete-grid': value['records'].pop()
    path = tmp_path / 'frozen.json'
    path.write_text(json.dumps(value))
    root = tmp_path
    if failure == 'outside':
        root = tmp_path / 'repository'
        root.mkdir()

    def git(argv, **kwargs):
        bad = failure == 'untracked' or (failure == 'dirty' and argv[1] == 'diff')
        return subprocess.CompletedProcess(argv, int(bad), '', '')
    monkeypatch.setattr(m.arms, 'get_embedder', lambda: pytest.fail('must not load a model'))
    with pytest.raises(ValueError):
        m.load_frozen(path, data, root=root, runner=git)


@pytest.mark.parametrize('version', [2, 3])
def test_real_fixture_dev_only_fake_vector_and_snapshot(version, tmp_path):
    data = pairs.load(ROOT / f'evals/recall/recall-pairs-v{version}.json')
    data = m.select_data(data, 'dev')
    # One existing dev case exercises all real rows and the canonical boundary.
    data['cases'] = data['cases'][:1]
    assert all(c['split'] == 'dev' for c in data['cases'])
    store = QuickStore(tmp_path / 'state')
    refs = pairs.seed(store, data)
    encoder = FakeEmbedder()
    retrieve = m.arms.make_retriever(store, data, 'vector', m.parameters('vector', 1, .8), embedder=encoder)
    result = pairs.run(retrieve, data['cases'], refs)
    row = measure(store, data, data['cases'][0], result['rows'][0]['returned'])
    assert row['split'] == 'dev'
    assert encoder.queries == [data['cases'][0]['query']]
    if version == 3:
        assert row['snapshot_omitted'] > 0


def test_snapshot_uses_real_bounds_and_counts_unmatched_needed(tmp_path):
    data = m.select_data(synthetic(), 'dev')
    store = QuickStore(tmp_path / 'state')
    refs = pairs.seed(store, data)
    # Empty arm still shows needed a through the real alphabetical fill.
    row = measure(store, data, data['cases'][0], [])
    assert row['needed_in_snapshot'] == row['request_only']['needed_in_snapshot'] == 1
    assert summarize([row])['scores']['dev']['by_facet']['cut']['needed_in_snapshot'] == 1
    for i in range(30):
        store.save_memory(f'profile.bulk{i:02d}', '가' * 200, owner_id=data['owner'])
    returned = [r['id'] for r in store.memories(data['owner'], limit=100)]
    assert measure(store, data, data['cases'][0], returned)['snapshot_omitted'] > 0
    assert refs.digest()  # snapshot reads did not invalidate any seeded row


def test_v2_report_verdict_and_cases_byte_identical_with_diagnostics():
    data = pairs.load(ROOT / 'evals/recall/recall-pairs-v2.json')
    data = m.select_data(data, 'dev')
    results = {name: rr.run_arm(data, rr.resolve_arm(name)) for name in ('lexical', 'lexical-context')}
    with_diagnostic = rr.report(data, results)
    without = rr.report(data, {name: {k: v for k, v in value.items() if k != 'snapshot_diagnostics'}
                               for name, value in results.items()})
    expected = copy.deepcopy(with_diagnostic)
    expected.pop('snapshot_diagnostics')
    assert json.dumps(expected, sort_keys=True) == json.dumps(without, sort_keys=True)
    assert 'Bounded snapshot diagnostic (no verdict)' in rr.report_markdown(with_diagnostic)


def gate_reports(*, fault=None):
    # Independent gate unit data, not the v3 held-out fixture.
    definitions = [('d','dev','lexical-control'), ('h','heldout','paraphrase'),
                   ('h2','heldout','cross-language'),
                   ('hc','heldout','lexical-control')]
    data = dict(version='recall-pairs/3', today='2026-03-25',
                cases=[dict(id=i,split=s,kind=k,query=i) for i,s,k in definitions])
    candidate = [dict(id=i, split=s, kind=k, needed_hit=1, needed_total=1,
                      noise_hits=0, trap_hits=0, returned=[i]) for i,s,k in definitions]
    if fault == 'trap': candidate[0]['trap_hits'] = 1
    if fault == 'regression': candidate[-1]['needed_hit'] = 0
    def envelope(rows):
        return dict(evidence='fixture (model-free)', deterministic=True,
                    runs=[dict(version=data['version'], fixture=rr._fixture_digest(data), rows=rows)])
    observations = {'vector': envelope(candidate)}
    for name in m.BASELINES:
        baseline = copy.deepcopy(candidate)
        baseline[0]['trap_hits'] = 0
        baseline[-1]['needed_hit'] = 1
        baseline[1]['needed_hit'] = 0
        if fault == 'noise-only':
            baseline[1].update(needed_hit=1, noise_hits=1)
        if fault == 'runtime-no-fix' and name == 'runtime':
            baseline[1]['needed_hit'] = 1
        if fault == 'disjoint' and name == 'runtime':
            baseline[1]['needed_hit'] = 1
            baseline[2]['needed_hit'] = 0
        observations[name] = envelope(baseline)
    return {b: rr.report(data, observations, baseline=b) for b in m.BASELINES}


@pytest.mark.parametrize('fault', [None, 'trap', 'regression', 'noise-only', 'runtime-no-fix', 'disjoint'])
def test_three_baseline_gate_requires_common_needed_recovery(fault):
    gate = m.combined_gate(gate_reports(fault=fault), 'vector')
    assert gate['preferred'] == (fault is None)
    if fault is None:
        assert gate['common_needed_recovery'] == ['h']
    if fault in ('noise-only', 'runtime-no-fix', 'disjoint'):
        assert gate['common_needed_recovery'] == []
    if fault == 'disjoint':
        assert gate['rules']['preferred_against_each']


def test_measure_dev_writes_reports_with_snapshots_and_rankings(tmp_path):
    data = synthetic()
    frozen = frozen_data(data)
    result = m.measure(data, frozen, tmp_path, 'dev', embedder=FakeEmbedder())
    assert result['gate'] is None
    assert result['recall_at_k']['vector']['cases'][0]['id'] == 'd'
    report = json.loads((tmp_path / 'r1v3-vs-runtime.json').read_text())
    assert 'snapshot_diagnostics' in report
    assert report['arms']['vector']['verdict'] is None
    assert set(result['recall_at_k']['vector']['scores']) == {'dev'}
