"""Deterministic retriever arms for the R1 paired recall experiment (#848).

Existing Solutions Review: Adopt the real MemoryService.search_memories path
(SQLite FTS5 via QuickStore); Build only the two-line query composition, which
an external retrieval framework cannot supply for this experiment. No dependency
is added and no runtime code is changed; this offline review checks no external
release activity. These arms are eval-side only and change no runtime behaviour.
Adopt AgentService.preceding_owner_turn + owner_profile_snapshot for the real
product brief path; Build only eval-side turn seeding and a stdlib mock spy.
"""
from unittest import mock

from personal_agent.memory_service import MemoryService, PROFILE_PREFIX


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


def runtime(store, data):
    """Score the real brief's matched profile rows that fit in its snapshot.

    Bind the product's single-owner constant to the synthetic fixture owner,
    without moving or rewriting Memory. Spies and the fixture date are scoped
    to each call; all brief readers run with their normal defaults.
    ``observed`` records actual snapshot queries and preceding turns by case id.
    """
    from personal_agent import quickstart_service
    from .recall_report import _case_lookup

    service = quickstart_service.AgentService(store)
    identify = _case_lookup(data['cases'])
    inputs = {(case['query'], case.get('context', '')) for case in data['cases']}
    observed, counter = {}, 0

    def turn(text, at):
        channel = 'telegram:recall-eval'
        work = store.enqueue(text, f'recall-eval:{at}', channel=channel, chat_id=counter,
                             owner_typed=True, owner_words=True)
        with store.db() as db:
            db.execute("UPDATE jobs SET created=?,status='succeeded' WHERE id=?", (at, work))
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('user', text, channel, at, work))
        return store.job(work)

    def retrieve(query, context=''):
        nonlocal counter
        counter += 1
        ident = identify(query, context) if (query, context) in inputs else f'call-{counter}'
        if context:
            turn(context, 2 * counter)
        job = turn(query, 2 * counter + 1)
        ctx = service.preceding_owner_turn(job)
        if ctx != context:
            raise ValueError(f'Runtime preceding owner turn differs from fixture context on case {ident}')

        snapshots = []
        original_snapshot = MemoryService.profile_snapshot

        def snapshot(memory, *args, **kwargs):
            matched = []
            original_search = memory.search_memories

            def search(*args, **kwargs):
                result = original_search(*args, **kwargs)
                if kwargs.get('key_prefix') == PROFILE_PREFIX:
                    matched.extend(result['memories'])
                return result

            with mock.patch.object(memory, 'search_memories', side_effect=search):
                result = original_snapshot(memory, *args, **kwargs)
            snapshots.append((kwargs['query'], matched, result['entries']))
            return result

        with mock.patch.object(quickstart_service, 'MEMORY_OWNER', data['owner']), \
                mock.patch.object(MemoryService, '_today', return_value=data['today']), \
                mock.patch.object(MemoryService, 'profile_snapshot', autospec=True, side_effect=snapshot):
            service.owner_profile_snapshot(job['message'], recall=True, context=ctx)
        if len(snapshots) != 1:
            raise ValueError(f'Expected one runtime profile snapshot on case {ident}')
        actual_query, matched, entries = snapshots[0]
        observed[ident] = dict(query=actual_query, context=ctx)
        shown = {entry['id'] for entry in entries}
        return [row['id'] for row in matched
                if MemoryService.is_profile_key(row.get('memory_key')) and row['id'] in shown]

    retrieve.observed = observed
    return retrieve


ARMS = {'lexical': lexical, 'context': context_aware, 'context-concat': context_concat,
        'lexical-context': lexical_context, 'runtime': runtime}
EVIDENCE = {'lexical': 'fixture (model-free)', 'context': 'fixture (model-free)',
            'context-concat': 'fixture (model-free)', 'lexical-context': 'fixture (model-free)',
            'runtime': 'fixture (model-free)'}
DETERMINISTIC = frozenset(ARMS)
DIAGNOSTIC = frozenset({'context-concat'})
