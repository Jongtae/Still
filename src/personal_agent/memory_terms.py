"""Derived Memory index terms; no model calls or independent storage.

Reuse review (#921 PR-1): Adopt SQLite FTS5 ``bm25()`` column weights,
SAVEPOINT and JSON1, and the existing ``memory_value_has_secret`` guard.
Anthropic Contextual Retrieval supplies the pattern: per-unit text indexed
in BM25. Build only the shape/secret/number term filter, meta lifecycle and
guarded write on Still's canonical rows. This is NOT a relevance filter.
Doc2Query-- (arXiv 2301.03266) filters expansions with a relevance model;
``pyterrier_doc2query`` needs Terrier/Java plus a separate model. Neither is
available on the owner's route. No dependency, licence, deployment, network
authority or supply-chain change: Python re/json and the existing SQLite
runtime provide the adopted facilities. External candidates are evaluated
as documented in the approved v5 spec, without a live lookup in this PR.
"""

import json
import re

PROMPT_VERSION = 'recall-terms/1'
MAX_TERMS = 24
TERM_CHARS = 64
TERMS_CHARS = 400
FAIL_LIMIT = 3


def filter_terms(store, memory_key, content, raw):
    """Bound expansion shape after checking the untouched output for secrets."""
    from .agent_runtime import memory_value_has_secret
    if memory_value_has_secret(store, raw, f'{memory_key}: {raw}'):
        return '', 'secret'
    content_tokens = set(re.findall(r'[^\W_]+', content.casefold()))
    existing = content_tokens | set(re.findall(r'[^\W_]+', memory_key.casefold()))
    terms = []
    seen = set()
    chars = 0
    for token in re.findall(r'[^\W_]+', raw.casefold()):
        if token in existing or token in seen or len(token) > TERM_CHARS:
            continue
        if any(char.isdigit() for char in token) and token not in content_tokens:
            continue
        size = len(token) + bool(terms)
        if len(terms) == MAX_TERMS or chars + size > TERMS_CHARS:
            break
        terms.append(token)
        seen.add(token)
        chars += size
    return ' '.join(terms), None


def parse_meta(text):
    """Read legacy/absent meta with generation zero."""
    try:
        meta = json.loads(text) if not isinstance(text, dict) else dict(text)
    except (TypeError, ValueError):
        return {'g': 0}
    if not isinstance(meta, dict):
        return {'g': 0}
    if type(meta.get('g')) is not int or meta['g'] < 0:
        meta['g'] = 0
    return meta


def build_meta(status, *, previous, route='', model='', observed_model='', policy='', run='', at, n=None):
    """Canonical provenance JSON; ordinary transitions retain the generation."""
    meta = dict(v=PROMPT_VERSION, g=parse_meta(previous)['g'], status=status,
                route=route, model=model, observed_model=observed_model or 'not reported',
                policy=policy, run=run, at=at)
    if n is not None:
        meta['n'] = n
    return json.dumps(meta, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def cleared_meta(previous, *, at):
    """Invalidate in-flight results, including across a subsequent undo."""
    return build_meta('cleared', previous={'g': parse_meta(previous)['g'] + 1}, at=at)


def eligible(meta_text):
    """The same admission predicate as ELIGIBLE_SQL."""
    if meta_text is None:
        return True
    meta = parse_meta(meta_text)
    return (meta.get('status') == 'cleared' or
            (meta.get('status') == 'failed' and type(meta.get('n')) is int and meta['n'] < FAIL_LIMIT))


# CASE guards JSON1 against legacy malformed JSON, including in aggregate queries.
ELIGIBLE_SQL = f"""(retrieval_terms_meta IS NULL OR CASE WHEN json_valid(retrieval_terms_meta) THEN
    (json_extract(retrieval_terms_meta,'$.status')='cleared' OR
     (json_extract(retrieval_terms_meta,'$.status')='failed' AND
      json_type(retrieval_terms_meta,'$.n')='integer' AND
      json_extract(retrieval_terms_meta,'$.n')<{FAIL_LIMIT}))
    ELSE 0 END IS TRUE)"""
