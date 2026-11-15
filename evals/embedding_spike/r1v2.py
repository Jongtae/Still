"""Read-only paired-runner adapters for #849; optional inference imports are lazy.

Adapt the spike's pinned official ORT/tokenizers encoder and _VectorIndex;
use the paired runner's exact lexical factories. See R1v2.md for reuse review.
"""
import os
from pathlib import Path
import time

from .embed import Embedder, MODEL_PINS, TOKENIZER_PIN
from .r1_arm import _VectorIndex, _eligible

EVIDENCE = 'local operating observation (offline embedding model)'
# Verified by two dev runs, comparing ordered fixture refs across fresh stores.
DETERMINISTIC = True
FROZEN = {
    'vector': dict(top_k=1, min_sim=0.84, use_context=True, passage_form='content'),
    'hybrid': dict(top_k=1, min_sim=0.84, use_context=True, passage_form='content',
                   k=60, vector_only_when_empty=True),
    'hybrid_lexical_context': dict(top_k=1, min_sim=0.84, use_context=False,
                                 passage_form='content', k=60, vector_only_when_empty=True),
}
PRIMARY = 'vector'
RUNTIME_UNAVAILABLE = (
    'recall_arms.runtime writes synthetic jobs/messages on every retrieval call; '
    'it cannot compose on the supplied store under this lane\'s no-store-write contract. '
    'Measure runtime separately as the runner\'s baseline.'
)
_EMBEDDER = None
LOAD_SECONDS = None


def get_embedder():
    """One pinned, offline CPU encoder per process, never an automatic download."""
    global _EMBEDDER, LOAD_SECONDS
    if _EMBEDDER is None:
        for key in ('HF_HUB_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY',
                    'HF_HUB_DISABLE_IMPLICIT_TOKEN', 'TRANSFORMERS_OFFLINE',
                    'ORT_DISABLE_TELEMETRY'):
            os.environ[key] = '1'
        model_file = os.environ.get('STILL_E5_MODEL_FILE', 'onnx/model_O4.onnx')
        if model_file not in MODEL_PINS:
            raise ValueError('STILL_E5_MODEL_FILE must name a pinned model file')
        directory = Path(os.environ['STILL_E5_MODEL_DIR'])
        started = time.perf_counter()
        _EMBEDDER = Embedder(directory, model_file=model_file,
                             model_sha256=MODEL_PINS[model_file], tokenizer_sha256=TOKENIZER_PIN)
        LOAD_SECONDS = time.perf_counter() - started
    return _EMBEDDER


class _QueryClock:
    """Time inference inside the reused index without copying its ranker."""
    def __init__(self, embedder):
        self.embedder, self.query_ms = embedder, 0.0

    def embed_passages(self, texts):
        return self.embedder.embed_passages(texts)

    def embed_queries(self, texts):
        started = time.perf_counter()
        result = self.embedder.embed_queries(texts)
        self.query_ms = (time.perf_counter() - started) * 1000
        return result


def _rrf(first, second, k):
    scores = {}
    for ranking in (first, second):
        for rank, ident in enumerate(ranking, 1):
            scores[ident] = scores.get(ident, 0.0) + 1 / (k + rank)
    return sorted(scores, key=lambda ident: (-scores[ident], ident))


def make_retriever(store, data, name, parameters, *, embedder=None):
    """Shared tuning/measurement seam; never consult case labels or write state."""
    p = dict(parameters)
    if p['top_k'] < 1 or not -1 <= p['min_sim'] <= 1 or p.get('k', 60) <= 0:
        raise ValueError('Invalid retrieval cutoff')
    if name not in FROZEN:
        raise ValueError(f'Unknown embedding arm: {name}')
    lexical = None
    if name != 'vector':
        from agentos_eval import recall_arms
        factory = recall_arms.lexical if name == 'hybrid' else recall_arms.lexical_context
        lexical = factory(store, data)
    clock = _QueryClock(get_embedder() if embedder is None else embedder)
    started = time.perf_counter()
    index = _VectorIndex(store, data, clock, p['passage_form'], p['use_context'])
    build = dict(passage_count=len(index.rows), build_seconds=time.perf_counter() - started,
                 vector_bytes=index.vectors.nbytes)
    observed = dict(parameters=p, index_build=build, calls=[])

    def retrieve(query, context=''):
        started = time.perf_counter()
        ranking = index.ranked(query, context)
        raw = [ident for ident, sim in ranking]
        selected = [ident for ident, sim in ranking if sim >= p['min_sim']][:p['top_k']]
        if lexical is None:
            ranked, returned = raw, selected
        else:
            lex = [ident for ident in lexical(query, context) if _eligible(store, data, ident)]
            # Diagnostic rank BEFORE cosine/top-k/empty-list admission. Returned
            # ranks use only the admitted vector prefix, exactly as in the spike.
            ranked = _rrf(lex, raw, p['k'])
            allowed = set(lex) | (set(selected) if not p['vector_only_when_empty'] or not lex else set())
            returned = [ident for ident in _rrf(lex, selected, p['k']) if ident in allowed]
        total_ms = (time.perf_counter() - started) * 1000
        observed['calls'].append(dict(query=query, context=context, query_embed_ms=clock.query_ms,
                                      retrieval_ms=max(0.0, total_ms - clock.query_ms),
                                      ranked=ranked, returned=returned))
        return returned

    retrieve.observed = observed
    retrieve.index_build = build
    retrieve.timings = observed['calls']
    return retrieve


def vector(store, data):
    return make_retriever(store, data, 'vector', FROZEN['vector'])


def hybrid(store, data):
    return make_retriever(store, data, 'hybrid', FROZEN['hybrid'])


def hybrid_lexical_context(store, data):
    return make_retriever(store, data, 'hybrid_lexical_context', FROZEN['hybrid_lexical_context'])


def hybrid_runtime(store, data):
    """Resolvable but explicitly unavailable: runtime mutates the supplied store."""
    raise NotImplementedError(RUNTIME_UNAVAILABLE)
