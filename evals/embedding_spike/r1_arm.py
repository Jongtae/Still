"""Read-only R1 retrieval arms and offline, dev-only tuning for #849.

Adopt ORT/tokenizers/numpy and reciprocal rank fusion; adapt the existing R1
load/seed/run/score/compare and QuickStore read APIs. No product-code changes.
Embedder protocol: embed_queries(texts), embed_passages(texts) -> L2 unit arrays.
"""
import argparse
from contextlib import contextmanager
from itertools import product
import json
from pathlib import Path
import socket
import tempfile
import time
from unittest.mock import patch

from evals.agentos_eval import recall_pairs
from .embed import Embedder, MODEL_PINS, MODEL_REVISION, TOKENIZER_PIN


FIXTURE = Path(__file__).resolve().parents[1] / 'recall/recall-pairs-v1.json'
# Frozen from the 14-case dev-only sweep, before the first held-out evaluation.
FROZEN_VECTOR = dict(top_k=1, min_sim=0.82, use_context=True, passage_form='content')
FROZEN_HYBRID = dict(k=60, **FROZEN_VECTOR, vector_only_when_empty=True)


def baseline(store, data):
    """Exactly the tests/docs baseline, including fixture date and ignored context."""
    from personal_agent.memory_service import MemoryService
    service = MemoryService(store, private_read_sink=lambda marker: None)
    service._today = lambda: data['today']

    def retrieve(query, context=''):
        return [row['id'] for row in service.search_memories(data['owner'], query)['memories']]
    return retrieve


def lexical_context(store, data):
    """The exact baseline path, with the preceding owner turn prepended."""
    lexical = baseline(store, data)

    def retrieve(query, context=''):
        return lexical(context + ' ' + query if context else query)
    return retrieve


def _eligible(store, data, ident):
    row = store.memory(ident, owner_id=data['owner'], current_only=False)
    return row if row and row['state'] == 'current' and (
        not row.get('valid_until') or row['valid_until'] >= data['today']) else None


def _rows(store, data):
    rows, offset = [], 0
    while True:
        page = store.memories(owner_id=data['owner'], limit=100, offset=offset, holding_on=data['today'])
        rows.extend(page)
        if len(page) < 100:
            return sorted(rows, key=lambda row: row['id'])
        offset += len(page)


def _passage(row, form):
    if form == 'content':
        return row['content']
    if form == 'key-content':
        return row['memory_key'] + ' ' + row['content']
    raise ValueError('passage_form must be content or key-content')


class _VectorIndex:
    def __init__(self, store, data, embedder, passage_form, use_context):
        self.store, self.data, self.embedder = store, data, embedder
        self.form, self.use_context = passage_form, use_context
        self.rows = _rows(store, data)
        self.texts = [_passage(row, passage_form) for row in self.rows]
        self.vectors = embedder.embed_passages(self.texts)

    def ranked(self, query, context):
        text = context + ' ' + query if self.use_context and context else query
        query_vector = self.embedder.embed_queries([text])[0]
        similarities = self.vectors @ query_vector
        ranked = []
        current = {row['id']: row for row in _rows(self.store, self.data)}
        for old, text, similarity in zip(self.rows, self.texts, similarities):
            # Never use cached eligibility; also discard stale content vectors.
            row = current.get(old['id'])
            if row and _passage(row, self.form) == text:
                ranked.append((row['id'], float(similarity)))
        return sorted(ranked, key=lambda item: (-item[1], item[0]))


def vector(store, data, embedder, *, top_k, min_sim, use_context, passage_form='content'):
    """Cosine-ranked eligible rows; cache is disposable and never writes Memory."""
    if top_k < 1 or not -1 <= min_sim <= 1:
        raise ValueError('Invalid vector cutoff')
    index = _VectorIndex(store, data, embedder, passage_form, use_context)

    def retrieve(query, context=''):
        return [ident for ident, sim in index.ranked(query, context) if sim >= min_sim][:top_k]
    return retrieve


def hybrid(store, data, embedder, *, k=60, top_k=1, min_sim=0.82, use_context=True,
           passage_form='content', vector_only_when_empty=True):
    """RRF, retaining lexical hits and admitting at most top_k semantic hits.

    Vector-only hits require min_sim. With vector_only_when_empty, admit them
    only when the lexical list is empty. Neither branch consults fixture labels.
    RRF uses 1/(k + one-based rank); no final cap can discard a lexical hit.
    """
    return _hybrid(store, data, embedder, baseline(store, data), k=k, top_k=top_k,
                   min_sim=min_sim, use_context=use_context, passage_form=passage_form,
                   vector_only_when_empty=vector_only_when_empty)


def hybrid_over_lexical_context(store, data, embedder, *, k=60, top_k=1, min_sim=0.82,
                                use_context=True, passage_form='content',
                                vector_only_when_empty=True):
    """Frozen hybrid fusion/cutoff with lexical_context as its lexical list."""
    return _hybrid(store, data, embedder, lexical_context(store, data), k=k, top_k=top_k,
                   min_sim=min_sim, use_context=use_context, passage_form=passage_form,
                   vector_only_when_empty=vector_only_when_empty)


def _hybrid(store, data, embedder, lexical, *, k, top_k, min_sim, use_context,
            passage_form, vector_only_when_empty):
    """Shared RRF and admission rule; ablation changes only the lexical input."""
    if k <= 0 or top_k < 1 or not -1 <= min_sim <= 1:
        raise ValueError('Invalid hybrid cutoff')
    index = _VectorIndex(store, data, embedder, passage_form, use_context)

    def retrieve(query, context=''):
        lex = lexical(query, context)
        vec = [ident for ident, sim in index.ranked(query, context) if sim >= min_sim][:top_k]
        scores = {}
        for ranking in (lex, vec):
            for rank, ident in enumerate(ranking, 1):
                scores[ident] = scores.get(ident, 0.0) + 1 / (k + rank)
        allowed = set(lex) | (set(vec) if not vector_only_when_empty or not lex else set())
        return [ident for ident in sorted(allowed, key=lambda ident: (-scores[ident], ident))
                if _eligible(store, data, ident)]
    return retrieve


class _CachedEmbedder:
    """CLI-only text cache to avoid repeating deterministic inference in sweeps."""
    def __init__(self, embedder):
        self.embedder, self.cache = embedder, {}

    def _get(self, texts, kind):
        import numpy as np
        texts = list(texts)
        missing = list(dict.fromkeys(text for text in texts if (kind, text) not in self.cache))
        if missing:
            method = getattr(self.embedder, 'embed_' + kind)
            self.cache.update(((kind, text), value) for text, value in zip(missing, method(missing)))
        return np.stack([self.cache[kind, text] for text in texts]) if texts else np.empty((0, 384))

    def embed_queries(self, texts):
        return self._get(texts, 'queries')

    def embed_passages(self, texts):
        return self._get(texts, 'passages')


@contextmanager
def _offline_guard():
    attempts = []

    def deny(*args, **kwargs):
        attempts.append('network attempt refused')
        raise RuntimeError('Network forbidden in the offline R1 experiment')

    with patch.object(socket.socket, 'connect', deny), patch.object(socket.socket, 'connect_ex', deny), \
            patch.object(socket, 'getaddrinfo', deny):
        yield attempts


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _measure(data, cases, factory, *, observe=None):
    from personal_agent.quickstart_store import QuickStore
    with tempfile.TemporaryDirectory(prefix='r1-embedding-') as directory:
        store = QuickStore(Path(directory) / 'state')
        refs = recall_pairs.seed(store, data)
        before = refs.digest()
        retriever = factory(store)
        if refs.digest() != before:
            raise AssertionError('Factory wrote canonical rows')
        result = recall_pairs.run(retriever, cases, refs)
        if observe is not None:
            observe(store, refs, result)
        if refs.digest() != before:
            raise AssertionError('Arm wrote canonical rows')
        return result


def decision_rule(base, arm):
    changes = recall_pairs.compare(base, arm)
    a, b = recall_pairs.score(base), recall_pairs.score(arm)
    rules = {
        'zero_traps_both_splits': all(not row['trap_hits'] for row in arm['rows']),
        'no_lost_lexical_control': not any(
            row['kind'] == 'lexical-control' and row['change'] == 'regressed'
            for split in changes.values() for row in split['changes']),
        'heldout_recall_and_fixed': b['heldout']['total']['needed_recall'] >= a['heldout']['total']['needed_recall']
            and any(row['change'] == 'fixed' for row in changes['heldout']['changes']),
        'no_heldout_regressed': not any(row['change'] == 'regressed' for row in changes['heldout']['changes']),
    }
    return dict(rules=rules, preferred=all(rules.values()))


def _sweep(data, embedder, output):
    """All candidate selection uses only dev cases, though all rows are indexed."""
    cases = [case for case in data['cases'] if case['split'] == 'dev']
    base = _measure(data, cases, lambda store: baseline(store, data))
    # Predeclared small grid; k=60 fixed as standard RRF convention, no rank-score cap.
    grid = dict(top_k=[1, 2], min_sim=[0.78, 0.80, 0.82, 0.84, 0.86, 0.88],
                use_context=[False, True], passage_form=['content', 'key-content'],
                vector_only_when_empty=[True, False])
    embedder.embed_queries([text for case in cases for text in (
        case['query'], (case.get('context', '') + ' ' + case['query']).strip())])
    records = []
    # Reuse one seeded dev store; the digest guards construction and every run.
    from personal_agent.quickstart_store import QuickStore
    with tempfile.TemporaryDirectory(prefix='r1-dev-sweep-') as directory:
        store = QuickStore(Path(directory) / 'state')
        refs = recall_pairs.seed(store, data)
        before = refs.digest()
        for top_k, min_sim, use_context, passage_form in product(
                grid['top_k'], grid['min_sim'], grid['use_context'], grid['passage_form']):
            params = dict(top_k=top_k, min_sim=min_sim, use_context=use_context, passage_form=passage_form)
            factories = [('vector', params)] + [('hybrid', dict(
                params, k=60, vector_only_when_empty=empty)) for empty in grid['vector_only_when_empty']]
            for name, parameters in factories:
                arm = globals()[name](store, data, embedder, **parameters)
                result = recall_pairs.run(arm, cases, refs)
                paired = recall_pairs.compare(base, result)
                score = recall_pairs.score(result)['dev']
                records.append(dict(arm=name, parameters=parameters, score=score, paired=paired['dev'],
                                    result=result))
        if refs.digest() != before:
            raise AssertionError('Dev sweep wrote canonical rows')

    def objective(record):
        rows = record['result']['rows']
        changes = record['paired']['changes']
        return (sum(row['trap_hits'] for row in rows),
                sum(row['kind'] == 'lexical-control' and row['change'] == 'regressed' for row in changes),
                -sum(row['needed_hit'] == row['needed_total'] and not row['trap_hits'] and not row['noise_hits']
                     for row in rows), record['score']['total']['noise_count'],
                -record['score']['total']['needed_hit'], record['parameters']['top_k'],
                -record['parameters']['min_sim'], record['parameters']['passage_form'] != 'content',
                record['parameters']['use_context'], not record['parameters'].get('vector_only_when_empty', True))

    selected = {name: min((row for row in records if row['arm'] == name), key=objective)['parameters']
                for name in ('vector', 'hybrid')}
    _write(output / 'r1-dev-sweep.json', dict(grid=grid, cases=len(cases), baseline=base,
        baseline_score=recall_pairs.score(base), records=records, selected=selected,
        selection='Minimise traps, lost lexical controls; maximise exact passes; minimise noise; '
                  'maximise needed hits. Ties: smaller top_k, higher floor, content, no context, empty-only.'))
    print(json.dumps(dict(dev_only=True, candidates=len(records), selected=selected), indent=2))


def _ablation(data, embedder, output, model_file, network_attempts):
    """Post-hoc attribution only: no search, tuning, or original artifact writes."""
    params = {
        'baseline': {}, 'lexical-context': {},
        'vector': dict(FROZEN_VECTOR), 'hybrid': dict(FROZEN_HYBRID),
        'vector-no-context': dict(FROZEN_VECTOR, use_context=False),
        'hybrid-no-context': dict(FROZEN_HYBRID, use_context=False),
        'hybrid-over-lexical-context': dict(FROZEN_HYBRID),
    }
    functions = {
        'baseline': baseline, 'lexical-context': lexical_context,
        'vector': vector, 'hybrid': hybrid,
        'vector-no-context': vector, 'hybrid-no-context': hybrid,
        'hybrid-over-lexical-context': hybrid_over_lexical_context,
    }
    suffix = '' if model_file == 'onnx/model.onnx' else '-' + Path(model_file).stem
    metadata = dict(evidence='post-hoc ablation after held-out was seen; no retuning',
        parameters=params, model_file=model_file, model_sha256=MODEL_PINS[model_file],
        tokenizer_sha256=TOKENIZER_PIN, model_revision=MODEL_REVISION)
    _write(output / f'r1-ablation-frozen{suffix}.json', metadata)
    results, refs_by_arm, diagnostics = {}, {}, []

    def observe(name, store, refs, result):
        refs_by_arm[name] = dict(refs)
        if name != 'hybrid':
            return
        id_to_ref = {ident: ref for ref, ident in refs.items()}
        index = _VectorIndex(store, data, embedder, FROZEN_VECTOR['passage_form'], True)
        no_context = _VectorIndex(store, data, embedder, FROZEN_VECTOR['passage_form'], False)
        for case in data['cases']:
            if case['split'] != 'heldout':
                continue

            def ranks(source):
                return [dict(rank=rank, id=ident, ref=id_to_ref[ident], similarity=similarity,
                             above_floor=similarity >= FROZEN_VECTOR['min_sim'])
                        for rank, (ident, similarity) in enumerate(
                            source.ranked(case['query'], case.get('context', '')), 1)]

            diagnostics.append(dict(case=case['id'], kind=case['kind'], needed=case['needed'],
                context=case.get('context', ''), query=case['query'],
                context_vector_ranking=ranks(index), no_context_vector_ranking=ranks(no_context)))

    for name, function in functions.items():
        def factory(store):
            return function(store, data, **params[name]) if name in ('baseline', 'lexical-context') else (
                function(store, data, embedder, **params[name]))
        results[name] = _measure(data, data['cases'], factory,
            observe=lambda store, refs, result: observe(name, store, refs, result))
        _write(output / f'r1-ablation-{name}{suffix}.json', results[name])

    # The diagnostic-only replays must reproduce the original per-case metrics.
    original_matches, originals = {}, {}
    for name in ('baseline', 'vector', 'hybrid'):
        path = output / f'r1-{name}{suffix}.json'
        if path.exists():
            original = json.loads(path.read_text(encoding='utf-8'))
            originals[name] = original
            def metrics(envelope):
                return [dict((key, value) for key, value in row.items() if key != 'returned')
                        for row in envelope['rows']]
            original_matches[name] = metrics(original) == metrics(results[name])
            if not original_matches[name]:
                raise AssertionError(f'Post-hoc replay differs from original {name}')

    for case in diagnostics:
        case['returned'] = {}
        case['original_primary_returned'] = {
            name: next(row['returned'] for row in result['rows'] if row['id'] == case['case'])
            for name, result in originals.items()}
        for name, result in results.items():
            row = next(row for row in result['rows'] if row['id'] == case['case'])
            mapping = {ident: ref for ref, ident in refs_by_arm[name].items()}
            case['returned'][name] = dict(ids=row['returned'], refs=[mapping[ident] for ident in row['returned']],
                needed_hit=row['needed_hit'], needed_total=row['needed_total'],
                trap_hits=row['trap_hits'], noise_hits=row['noise_hits'])
    summary = dict(metadata,
        scores={name: recall_pairs.score(result) for name, result in results.items()},
        paired={reference: {name: recall_pairs.compare(results[reference], result)
                            for name, result in results.items()}
                for reference in ('baseline', 'lexical-context')},
        decision={reference: {name: decision_rule(results[reference], result)
                              for name, result in results.items()}
                  for reference in ('baseline', 'lexical-context')},
        original_per_case_metrics_match=original_matches,
        memory_digest_unchanged=True, network_attempts=network_attempts)
    _write(output / f'r1-ablation-summary{suffix}.json', summary)
    _write(output / f'r1-ablation-diagnostics{suffix}.json', dict(metadata,
        refs_by_arm=refs_by_arm, heldout=diagnostics))
    print(json.dumps(dict(evidence=metadata['evidence'],
        scores={name: {split: score['total'] for split, score in scores.items()}
                for name, scores in summary['scores'].items()}, decision=summary['decision']), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', required=True, type=Path)
    parser.add_argument('--out-dir', default=Path('evals/embedding_spike/results'), type=Path)
    parser.add_argument('--model-file', default='onnx/model.onnx', choices=MODEL_PINS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dev-sweep', action='store_true', help='Dev only; never evaluates held-out')
    mode.add_argument('--ablation', action='store_true', help='Post-hoc context attribution; no retuning')
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    data = recall_pairs.load(FIXTURE)
    started = time.perf_counter()
    with _offline_guard() as network_attempts:
        embedder = _CachedEmbedder(Embedder(args.model_dir, model_file=args.model_file,
            model_sha256=MODEL_PINS[args.model_file], tokenizer_sha256=TOKENIZER_PIN))
        if args.dev_sweep:
            _sweep(data, embedder, args.out_dir)
            return
        if args.ablation:
            _ablation(data, embedder, args.out_dir, args.model_file, network_attempts)
            return
        params = dict(vector=FROZEN_VECTOR, hybrid=FROZEN_HYBRID)
        suffix = '' if args.model_file == 'onnx/model.onnx' else '-' + Path(args.model_file).stem
        # Persist frozen parameters before any held-out query is embedded or scored.
        _write(args.out_dir / f'r1-frozen{suffix}.json', dict(parameters=params,
            model_file=args.model_file, model_sha256=MODEL_PINS[args.model_file],
            tokenizer_sha256=TOKENIZER_PIN, model_revision=MODEL_REVISION))
        results = {}
        for name in ('baseline', 'vector', 'hybrid'):
            factory = (lambda store: baseline(store, data)) if name == 'baseline' else (
                lambda store, name=name: globals()[name](store, data, embedder, **params[name]))
            results[name] = _measure(data, data['cases'], factory)
            _write(args.out_dir / f'r1-{name}{suffix}.json', results[name])
        summary = dict(model_file=args.model_file, parameters=params, fixture=results['baseline']['fixture'],
            scores={name: recall_pairs.score(result) for name, result in results.items()},
            paired={name: recall_pairs.compare(results['baseline'], result) for name, result in results.items()},
            decision={name: decision_rule(results['baseline'], result) for name, result in results.items()},
            elapsed_seconds=time.perf_counter() - started, network_attempts=network_attempts,
            memory_digest_unchanged=True)
        _write(args.out_dir / f'r1-summary{suffix}.json', summary)
        print(json.dumps({key: summary[key] for key in ('scores', 'decision', 'elapsed_seconds')}, indent=2))


if __name__ == '__main__':
    main()
