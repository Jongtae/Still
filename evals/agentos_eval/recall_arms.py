"""Deterministic retriever arms for the R1 paired recall experiment (#848).

Existing Solutions Review: Adopt the real MemoryService.search_memories path
(SQLite FTS5 via QuickStore); Build only the two-line query composition, which
an external retrieval framework cannot supply for this experiment. No dependency
is added and no runtime code is changed; this offline review checks no external
release activity. These arms are eval-side only and change no runtime behaviour.
Adapt conversational reformulation through the existing structured plan call;
adopt its validated Attempt and AgentService.worker_profile_snapshot. No new
model call, dependency or retrieval engine. Resolver strings are fixture evidence.
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


def plan_prompt_schema_digest():
    """Bind replays to the exact plan prompt and schema used by this arm."""
    from personal_agent.orchestrator import QUESTION, plan_schema, digest
    return digest({'question': QUESTION, 'schema': plan_schema(_runtime_catalogue().available())})


def _runtime_catalogue():
    from personal_agent.orchestrator import Catalogue
    return Catalogue([{'id':'fixture', 'kind':'api', 'name':'Fixture', 'destination':'',
                       'default':True, 'available':True, 'reason':'', 'native_search':False,
                       'browser':False, 'cost':'fixture', 'latency':'fixture', 'default_model':'',
                       'models':[], 'model_tiers':{}, 'tools':[]}], {}, 'fixture')


def replay_resolver(recorded, data, *, run=1):
    """Strict replay of one frozen sampling run; missing entries are errors."""
    from .recall_report import _fixture_digest, _phrasing_cases, _case_lookup
    if recorded.get('version') != 'recall-plan/1':
        raise ValueError('Recorded plan version differs')
    if recorded.get('fixture_digest') != _fixture_digest(data):
        raise ValueError('Recorded plan fixture digest differs')
    if recorded.get('plan_prompt_schema_digest') != plan_prompt_schema_digest():
        raise ValueError('Recorded plan input digest differs')
    entries = recorded.get('runs', {}).get(str(run))
    if not isinstance(entries, dict) or set(entries) != set(_phrasing_cases(data)):
        raise ValueError('Missing recorded plan cases')
    for ident, binding in _phrasing_cases(data).items():
        entry = entries[ident]
        if not isinstance(entry, dict) or any(entry.get(k) != v for k,v in binding.items()):
            raise ValueError('Recorded plan query/context differs')
        if not isinstance(entry.get('recall_query'), str):
            raise ValueError('Recorded plan selector must be text')
    identify = _case_lookup(data['cases'])

    def resolve(query, context):
        return entries[identify(query, context)]['recall_query']
    resolve.evidence = 'fixture replay: ' + '; '.join(sorted({str(entry.get('evidence','unspecified provenance')) for entry in entries.values()}))
    return resolve


def runtime(store, data, *, resolver=None):
    """Score the real brief's matched profile rows that fit in its snapshot.

    Bind the product's single-owner constant to the synthetic fixture owner,
    without moving or rewriting Memory. Spies and the fixture date are scoped
    to each call; all brief readers run with their normal defaults.
    ``observed`` records accepted plans and actual worker selectors by case id.
    """
    from personal_agent import quickstart_service
    from .recall_report import _case_lookup
    from personal_agent.decision import FixtureDecisionEngine, StructuredDecision, OUTCOME_DECIDED, fixture_confidence
    from personal_agent.orchestrator import PURPOSE

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
        rows = []
        if context:
            previous = turn(context, 2 * counter)
            rows.extend(store.history(through=previous['id'])[-1:])
        job = turn(query, 2 * counter + 1)
        rows.extend(store.history(through=job['id'])[-1:])
        calls = []
        selector = resolver(query, context) if resolver is not None else ''
        def structured(plan_context, question, schema):
            if plan_context.purpose != PURPOSE:
                return None
            calls.append(plan_context)
            return StructuredDecision(OUTCOME_DECIDED,
                {'worker':'fixture', 'model':'', 'effort':'', 'brief':{'notes':'','lens':''},
                 'reason':'fixture plan', 'reply':'', 'recall_query':selector,
                 'account_change':False,'owner_situation':False,'technical_work':False}, fixture_confidence())
        service.use_decision_engine(FixtureDecisionEngine(structured=structured))

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
            snapshots.append((kwargs['query'], matched, result))
            return result

        with mock.patch.object(quickstart_service, 'MEMORY_OWNER', data['owner']), \
                mock.patch.object(quickstart_service, 'worker_catalogue', return_value=_runtime_catalogue()), \
                mock.patch.object(MemoryService, '_today', return_value=data['today']), \
                mock.patch.object(MemoryService, 'profile_snapshot', autospec=True, side_effect=snapshot):
            sections = {'profile':service.owner_profile_snapshot(query), 'current_context':'', 'prepared':''}
            orchestration = service.work_orchestration(job, query, service.history_for_models(rows), sections, None)
            input_digest = orchestration.plan_digest(orchestration.catalogue.available())
            attempt = orchestration.first()
            snapshots.clear()  # score only the worker refresh, never planning/general-profile supply
            _, identity = service.worker_profile_snapshot(job, attempt)
        if len(snapshots) != 1:
            raise ValueError(f'Expected one runtime profile snapshot on case {ident}')
        actual_query, matched, snapshot_result = snapshots[0]
        observed[ident] = dict(query=actual_query, context=context, planning_query_source='request',
                               selector_source=identity['source'], plan_accepted=attempt.planned,
                               input_digest=input_digest, plan_prompt_schema_digest=plan_prompt_schema_digest(),
                               plan_calls=len(calls), snapshot_omitted=snapshot_result.get('omitted_count',0),
                               snapshot_shown=len(snapshot_result['entries']), matched_count=len(matched), evidence=getattr(resolver,'evidence','author-written selector' if resolver else 'request-only fixture'))
        shown = {entry['id'] for entry in snapshot_result['entries']}
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
