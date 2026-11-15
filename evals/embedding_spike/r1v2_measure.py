"""Offline #849 v2 dev sweep and frozen paired measurements (default: dev only).

No held-out selection/tuning. --split all is for Claude's one locked measurement.
Stdlib metrics/report glue; no new dependency or runtime integration.
"""
import argparse
import copy
from itertools import product
import json
import math
import os
from pathlib import Path
import resource
from statistics import median
import subprocess
import sys
import tempfile
import time

from agentos_eval import recall_pairs as pairs, recall_report as rr
from . import r1v2 as arms
from .embed import MODEL_PINS, MODEL_REVISION, TOKENIZER_PIN
from .r1_arm import _CachedEmbedder, _offline_guard

FIXTURE = Path(__file__).resolve().parents[1] / 'recall/recall-pairs-v2.json'
EMBEDDING_ARMS = ('vector', 'hybrid', 'hybrid_lexical_context')
BASELINES = ('lexical', 'lexical-context', 'runtime')
SELECTION = ('Zero dev traps, most exact dev passes, fewest noise rows. Ties: '
             'smaller top_k, higher min_sim, content, no context, empty-only, arm name.')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def load_data(split):
    data = pairs.load(FIXTURE)
    if split == 'dev':
        # Remove cases before ANY runner, lookup, inference or report construction.
        data = dict(data, cases=[c for c in data['cases'] if c['split'] == 'dev'])
    return data


def combined_gate(reports, name):
    """Combine both unchanged-observation runner reports, including needed gains."""
    fixed_sets, recovered_sets = [], []
    rules = dict(preferred_against_both=True, zero_traps_both_splits=True,
                 no_lost_lexical_control=True, no_heldout_regression=True)
    for baseline in ('lexical', 'lexical-context'):
        report = reports[baseline]
        candidate = report['arms'][name]
        reference = report['arms'][baseline]
        rules['preferred_against_both'] &= bool(candidate['verdict'] and candidate['verdict']['preferred'])
        scores = candidate.get('run_scores', [candidate['score']])
        rules['zero_traps_both_splits'] &= all(
            not score[split]['total']['cases_with_trap'] for score in scores for split in pairs.SPLITS)
        changes = candidate['compare']
        rules['no_lost_lexical_control'] &= not any(
            row['kind'] == 'lexical-control' and row['change'] == 'regressed'
            for split in pairs.SPLITS for row in changes[split]['changes'])
        rules['no_heldout_regression'] &= not any(
            row['change'] == 'regressed' for row in changes['heldout']['changes'])
        fixed = {row['id'] for row in changes['heldout']['changes'] if row['change'] == 'fixed'}
        before = {row['id']: row for row in reference['cases']}
        recovered = {row['id'] for row in candidate['cases'] if row['split'] == 'heldout'
                     and row['id'] in fixed and row['needed_hit'] > before[row['id']]['needed_hit']}
        fixed_sets.append(fixed)
        recovered_sets.append(recovered)
    common = sorted(set.intersection(*fixed_sets))
    recovered = sorted(set.intersection(*recovered_sets))
    rules['common_heldout_needed_recovery'] = bool(recovered)
    return dict(preferred=all(rules.values()), rules=rules, common_fixed=common,
                common_needed_recovery=recovered)


def ref_map(store, data):
    definitions = {(row['key'], row['content']): row['ref'] for row in data['rows']}
    with store.db() as db:
        return {row['id']: definitions[row['memory_key'], row['content']]
                for row in db.execute('SELECT id,memory_key,content FROM memories')}


def instrument(factory, *, check_identical=False):
    """Add baseline timing/rank observations, preserving runtime's own hook."""
    def make(store, data):
        retrieve = factory(store, data)
        refs = ref_map(store, data)
        observed = getattr(retrieve, 'observed', None)
        own = isinstance(observed, dict) and 'calls' in observed
        if not own:
            observed = dict(calls=[], runtime_observed=getattr(retrieve, 'observed', {}))
        observed['id_to_ref'] = refs

        def call(query, context=''):
            started = time.perf_counter()
            returned = retrieve(query, context)
            elapsed = (time.perf_counter() - started) * 1000
            if not own:
                observed['calls'].append(dict(query=query, context=context, ranked=returned,
                                              returned=returned, retrieval_ms=elapsed, query_embed_ms=None))
            if check_identical:
                # Exact same canonical IDs, second inference, no text cache.
                first_observation = observed['calls'][-1]
                again = retrieve(query, context)
                if again != returned:
                    raise AssertionError('Non-deterministic returned ids')
                if own:
                    second = observed['calls'].pop()
                    if second['ranked'] != first_observation['ranked']:
                        raise AssertionError('Non-deterministic ranked ids')
            return returned

        call.observed = observed
        return call
    return make


def sweep(output):
    """240 candidates; shared corpus, strictly 24 dev queries, no runtime arm."""
    from personal_agent.quickstart_store import QuickStore
    data = load_data('dev')
    grid = dict(top_k=[1, 2, 3], min_sim=[0.78, 0.80, 0.82, 0.84],
                use_context=[True, False], passage_form=['content', 'key-content'],
                vector_only_when_empty=[True, False], k=[60])
    records = []
    with _offline_guard() as attempts, tempfile.TemporaryDirectory(prefix='r1v2-dev-') as tmp:
        embedder = _CachedEmbedder(arms.get_embedder())
        store = QuickStore(Path(tmp) / 'state')
        refs = pairs.seed(store, data)
        before = refs.digest()
        for top_k, floor, context, form in product(grid['top_k'], grid['min_sim'],
                                                  grid['use_context'], grid['passage_form']):
            p = dict(top_k=top_k, min_sim=floor, use_context=context, passage_form=form)
            for name in EMBEDDING_ARMS:
                for empty in ([None] if name == 'vector' else grid['vector_only_when_empty']):
                    parameters = p if empty is None else dict(p, k=60, vector_only_when_empty=empty)
                    retrieve = arms.make_retriever(store, data, name, parameters, embedder=embedder)
                    result = pairs.run(retrieve, data['cases'], refs)
                    total = pairs.score(result)['dev']['total']
                    passes = sum(rr._passes(row) for row in result['rows'])
                    records.append(dict(arm=name, parameters=dict(parameters), score=total, passes=passes,
                                        cases=[{k: v for k, v in row.items() if k != 'returned'}
                                               for row in result['rows']]))
        if before != refs.digest():
            raise AssertionError('Dev sweep changed canonical Memory')
        if attempts:
            raise AssertionError('Network attempted')

    def objective(row):
        p = row['parameters']
        return (bool(row['score']['cases_with_trap']), -row['passes'], row['score']['noise_count'],
                p['top_k'], -p['min_sim'], p['passage_form'] != 'content', p['use_context'],
                not p.get('vector_only_when_empty', True), row['arm'])

    selected = {name: min((r for r in records if r['arm'] == name), key=objective)
                for name in EMBEDDING_ARMS}
    primary = min(selected.values(), key=objective)['arm']
    result = dict(split='dev', cases=len(data['cases']), grid=grid, selection=SELECTION,
                  records=records, selected=selected, primary=primary,
                  network_attempts=attempts, canonical_memory_unchanged=True,
                  model=provenance())
    write(output / 'r1v2-dev-sweep.json', result)
    print(json.dumps(dict(candidates=len(records), primary=primary, selected=selected), ensure_ascii=False))


def provenance():
    model_file = os.environ.get('STILL_E5_MODEL_FILE', 'onnx/model_O4.onnx')
    return dict(revision=MODEL_REVISION, model_file=model_file, sha256=MODEL_PINS[model_file],
                tokenizer_sha256=TOKENIZER_PIN, evidence=arms.EVIDENCE)


def latency(values):
    values = sorted(value for value in values if value is not None)
    return dict(samples=len(values), median_ms=median(values) if values else None,
                p95_ms=values[max(0, math.ceil(0.95 * len(values)) - 1)] if values else None)


def recall_at_k(data, result):
    """Micro needed-row recall from the full eligible ranking BEFORE all cutoffs."""
    observed = result['observed']['1']
    refs = observed['id_to_ref']
    calls = observed['calls']
    if len(calls) != len(data['cases']):
        raise AssertionError('Ranking/case mismatch')
    rows = []
    for case, call in zip(data['cases'], calls):
        if (case['query'], case.get('context', '')) != (call['query'], call['context']):
            raise AssertionError('Ranking inputs differ')
        ranking = [refs[ident] for ident in call['ranked']]
        needed = set(case['needed'])
        rows.append(dict(id=case['id'], split=case['split'], kind=case['kind'], needed=len(needed),
                         hits={str(k): len(needed & set(ranking[:k])) for k in (1, 3, 5)}))

    def metric(subset):
        total = sum(row['needed'] for row in subset)
        return dict(needed_total=total, cases=len(subset), recall={str(k):
                    sum(row['hits'][str(k)] for row in subset) / total if total else None for k in (1, 3, 5)})

    scores = {}
    for split in sorted({row['split'] for row in rows}):
        subset = [row for row in rows if row['split'] == split]
        scores[split] = dict(total=metric(subset), by_kind={
            kind: metric([row for row in subset if row['kind'] == kind])
            for kind in sorted({row['kind'] for row in subset})})
    return dict(scores=scores, cases=rows)


def synthetic_passages(count):
    """Unique, deterministic KO/EN note-like passages, 30–200 characters."""
    ko = '작업실 창가의 화분은 흙이 마른 뒤 아침에 물을 준다. 주말에는 잎의 상태와 햇빛을 확인한다. '
    en = 'Keep the workshop supplies in a dry labelled box. Check the garden pots in the morning before watering. '
    for i in range(count):
        length = 30 + (i * 37) % 171
        prefix = f'{i:05d} '
        body = ko if i % 2 else en
        yield (prefix + body * 4)[:length]


def peak_rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def model_cost(sizes):
    # Process-cold means a fresh interpreter; the OS file cache is NOT flushed.
    cold = subprocess.run([sys.executable, '-m', 'evals.embedding_spike.r1v2_measure', '--cold-load'],
                          check=True, capture_output=True, text=True)
    child = json.loads(cold.stdout)
    with _offline_guard() as attempts:
        embedder = arms.get_embedder()
        started = time.perf_counter()
        assert arms.get_embedder() is embedder
        warm_lookup = time.perf_counter() - started
        builds = {}
        for size in sizes:
            started = time.perf_counter()
            texts = list(synthetic_passages(size))
            vectors = embedder.embed_passages(texts)
            builds[str(size)] = dict(passage_count=size, build_seconds=time.perf_counter() - started,
                                      vector_bytes=vectors.nbytes, min_chars=min(map(len, texts)),
                                      max_chars=max(map(len, texts)))
            del vectors
            print(f'Synthetic build {size} complete', file=sys.stderr, flush=True)
        if attempts or child['network_attempts']:
            raise AssertionError('Network attempted')
    directory = Path(os.environ['STILL_E5_MODEL_DIR'])
    model = directory / os.environ.get('STILL_E5_MODEL_FILE', 'onnx/model_O4.onnx')
    return dict(evidence='local operating observation',
                process_cold=child, warm_load_seconds=arms.LOAD_SECONDS,
                cached_acquisition_seconds=warm_lookup, synthetic_builds=builds,
                model_file_bytes=model.stat().st_size,
                model_directory_bytes=sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()),
                peak_rss_bytes=peak_rss(), network_attempts=attempts,
                note='Process-cold includes pin verification/import/session load; OS cache not flushed. '
                     'Warm load is first main-process load, cached acquisition is a second cache lookup. '
                     'Peak RSS is process high-water mark, not incremental model allocation.')


def dev_report(report):
    """Empty held-out tables are not results: omit them and defer all verdicts."""
    report = copy.deepcopy(report)
    report['selection'] = 'dev only; heldout not evaluated'
    for arm in report['arms'].values():
        arm['score'].pop('heldout', None)
        arm['compare'].pop('heldout', None)
        arm['verdict'] = None
    return report


def markdown(report, split):
    if split != 'dev':
        return rr.report_markdown(report)
    # Renderer expects both split keys; render then remove the empty held-out
    # section and verdict block. The JSON report omits those entries entirely.
    rendered = rr.report_markdown(report)
    rendered = rendered.split('\n## heldout\n')[0]
    return rendered + '\nHeld-out not evaluated. All preference verdicts deferred.\n'


def measure(data, output, split, sizes, runtime_python=None):
    before_load = os.getloadavg()
    results = {}
    with _offline_guard() as attempts:
        for name in (*EMBEDDING_ARMS, *BASELINES):
            if name == 'runtime' and runtime_python:
                with tempfile.TemporaryDirectory(prefix='r1v2-runtime-') as tmp:
                    target = Path(tmp) / 'runtime.json'
                    subprocess.run([str(runtime_python), '-m', 'evals.embedding_spike.r1v2_measure',
                                    '--runtime-only', str(target), '--split', split],
                                   check=True, text=True, capture_output=True)
                    record = json.loads(target.read_text())
                    if record['network_attempts']:
                        raise AssertionError('Runtime worker attempted network')
                    results[name] = record['result']
                continue
            spec = f'evals.embedding_spike.r1v2:{name}' if name in EMBEDDING_ARMS else name
            resolved = rr.resolve_arm(spec)
            wrapped = rr.Arm(instrument(resolved.factory, check_identical=name in EMBEDDING_ARMS),
                             resolved.evidence, resolved.deterministic)
            results[name] = rr.run_arm(data, wrapped)
        if attempts:
            raise AssertionError('Network attempted')
    # All three reports reuse these exact observations; no arm is rerun.
    reports = {baseline: rr.report(data, results, baseline=baseline) for baseline in BASELINES}
    gates = {name: combined_gate(reports, name) for name in EMBEDDING_ARMS} if split != 'dev' else {}
    for baseline, report in reports.items():
        write(output / f'r1v2-vs-{baseline}.json', dev_report(report) if split == 'dev' else report)
        (output / f'r1v2-vs-{baseline}.md').write_text(markdown(report, split), encoding='utf-8')
    observed_costs = {}
    rankings = {}
    for name, result in results.items():
        obs = result['observed']['1']
        observed_costs[name] = dict(
            query_embedding=latency([call['query_embed_ms'] for call in obs['calls']]),
            retrieval=latency([call['retrieval_ms'] for call in obs['calls']]),
            index_build=obs.get('index_build'))
        rankings[name] = recall_at_k(data, result)
    costs = model_cost(sizes)
    summary = dict(split=split, case_count=len(data['cases']), primary=arms.PRIMARY,
                   frozen=arms.FROZEN, model=provenance(), gate=gates,
                   gate_status='deferred; no heldout evaluated' if split == 'dev' else 'evaluated',
                   hybrid_runtime_unavailable=arms.RUNTIME_UNAVAILABLE,
                   identical_ids_on_two_calls=True, costs=dict(costs, per_arm=observed_costs),
                   loadavg_before=before_load, loadavg_after=os.getloadavg(), network_attempts=attempts,
                   recall_at_k=rankings,
                   recall_at_k_definition='Micro recall = sum needed refs in first k unique eligible ranked refs / '
                   'sum needed refs, separately per split and kind. Cases with no needed refs add zero to '
                   'numerator/denominator (all-empty groups: null). Vector: full cosine rank; hybrid: RRF '
                   'of exact lexical list and full cosine rank before min_sim/top_k/empty-only admission. '
                   'Baselines expose only their returned rank (no additional cutoff).',
                   changes={baseline: {name: {s: r['arms'][name]['compare'][s]['changes']
                            for s in ({'dev'} if split == 'dev' else pairs.SPLITS)}
                            for name in EMBEDDING_ARMS} for baseline, r in reports.items()})
    write(output / 'r1v2-observations.json', results)
    write(output / 'r1v2-measurement.json', summary)
    print('hybrid_runtime: unavailable; ' + arms.RUNTIME_UNAVAILABLE)
    for name in EMBEDDING_ARMS:
        score = reports['lexical']['arms'][name]['score']['dev']['total']
        mark = ' (PRIMARY)' if name == arms.PRIMARY else ''
        verdict = gates[name]['preferred'] if gates else 'DEFERRED (dev only)'
        print(f'{name}{mark}: gate={verdict}; dev={json.dumps(score)}')
    print(json.dumps(dict(costs=summary['costs'], network_attempts=attempts), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, default=Path('evals/embedding_spike/results'))
    parser.add_argument('--split', choices=('dev', 'all'), default='dev')
    parser.add_argument('--dev-sweep', action='store_true')
    parser.add_argument('--synthetic-sizes', nargs='+', type=int, default=[1000, 10000])
    parser.add_argument('--runtime-python', type=Path, help='Optional .venv/bin/python for runtime dependencies')
    parser.add_argument('--cold-load', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--runtime-only', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.cold_load:
        with _offline_guard() as attempts:
            arms.get_embedder()
            print(json.dumps(dict(load_seconds=arms.LOAD_SECONDS, peak_rss_bytes=peak_rss(),
                                  network_attempts=attempts)))
        return
    data = load_data(args.split)
    if args.runtime_only:
        with _offline_guard() as attempts:
            resolved = rr.resolve_arm('runtime')
            result = rr.run_arm(data, rr.Arm(instrument(resolved.factory), resolved.evidence, True))
            write(args.runtime_only, dict(result=result, network_attempts=attempts))
        return
    if any(size < 1 for size in args.synthetic_sizes):
        parser.error('synthetic sizes must be positive')
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.dev_sweep:
        if args.split != 'dev':
            parser.error('Sweeps are dev only')
        sweep(args.out_dir)
    else:
        if arms.PRIMARY == 'PENDING_DEV_SWEEP':
            parser.error('Freeze parameters and declare primary in R1v2.md before measurement')
        measure(data, args.out_dir, args.split, args.synthetic_sizes, args.runtime_python)


if __name__ == '__main__':
    main()
