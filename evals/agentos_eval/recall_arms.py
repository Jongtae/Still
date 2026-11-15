"""Deterministic retriever arms for the R1 paired recall experiment (#848).

Existing Solutions Review: Adopt the real MemoryService.search_memories path
(SQLite FTS5 via QuickStore); Build only the two-line query composition, which
an external retrieval framework cannot supply for this experiment. No dependency
is added and no runtime code is changed; this offline review checks no external
release activity. These arms are eval-side only and change no runtime behaviour.
"""
from personal_agent.memory_service import MemoryService


def lexical(store, data):
    """Search the request alone through the real service, ignoring context."""
    service = MemoryService(store, private_read_sink=lambda marker: None)
    service._today = lambda: data['today']

    def retrieve(query, context=''):
        return [row['id'] for row in service.search_memories(data['owner'], query)['memories']]

    return retrieve


def context_aware(store, data):
    """Pre-registered #403 step-1-revisited candidate: fall back only on no hits.

    A request that finds its own facts is never narrowed or diluted by the
    earlier turn. A short follow-up with no searchable words alone falls back
    to the turn it follows: append non-empty context only after an empty result.
    There is no length threshold, word list, or split-specific rule.
    """
    service = MemoryService(store, private_read_sink=lambda marker: None)
    service._today = lambda: data['today']

    def retrieve(query, context=''):
        rows = service.search_memories(data['owner'], query)['memories']
        if not rows and context:
            rows = service.search_memories(data['owner'], f'{query} {context}')['memories']
        return [row['id'] for row in rows]

    return retrieve


def context_concat(store, data):
    """Diagnostic, not a candidate: always append context when it is non-empty."""
    service = MemoryService(store, private_read_sink=lambda marker: None)
    service._today = lambda: data['today']

    def retrieve(query, context=''):
        text = f'{query} {context}' if context else query
        return [row['id'] for row in service.search_memories(data['owner'], text)['memories']]

    return retrieve


def lexical_context(store, data):
    """#849 reference baseline: search context first, then the request."""
    search = lexical(store, data)

    def retrieve(query, context=''):
        return search(f'{context} {query}' if context else query)

    return retrieve


ARMS = {'lexical': lexical, 'context': context_aware, 'context-concat': context_concat,
        'lexical-context': lexical_context}
EVIDENCE = {'lexical': 'fixture (model-free)', 'context': 'fixture (model-free)',
            'context-concat': 'fixture (model-free)', 'lexical-context': 'fixture (model-free)'}
DETERMINISTIC = frozenset(ARMS)
DIAGNOSTIC = frozenset({'context-concat'})
