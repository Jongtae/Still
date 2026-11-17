"""#921 offline regression contracts and real-executor fixture harness.

Reuse review (Adapt): stdlib JSON/SHA256, SQLite backup and unittest.mock;
existing recall_pairs, recall_report, product terms CAS, catalogue, executor
and snapshot inventory. The approved v5 spec supplies the external retrieval
pattern review. No evaluation framework can supply Still's gate receipts.
No dependency, licence, platform or authority change; no network review/run.
Worker fixtures adapt the Transport/Engine seams in test_orchestrator, without
importing tests. Live entry points live separately in recall_record.
"""
from collections import Counter
import gzip
import hashlib
import inspect
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from unittest import mock

from . import recall_pairs as pairs
from .recall_report import _fixture_digest

TERMS_VERSION = 'recall-terms/1'
PLAN_VERSION = 'recall-plan/2'
ADDITIONS_VERSION = 'recall-gate-dev/1'
FLAGS = ('account_change', 'owner_situation', 'technical_work', 'ask_location', 'preview')


def load_recording(path):
    """Read plain JSON or suffix-selected gzip with stdlib text/JSON decoding."""
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def digest(value):
    return _fixture_digest(value)


def filter_digest():
    from personal_agent import memory_terms as mt
    return hashlib.sha256(inspect.getsource(mt).encode()).hexdigest()


def terms_prompt_digest():
    from personal_agent.owner_model import TERMS_QUESTION, TERMS_SCHEMA
    return digest(dict(question=TERMS_QUESTION, schema=TERMS_SCHEMA, filter=filter_digest()))


def layer(base, additions):
    if additions.get('version') != ADDITIONS_VERSION or additions.get('base_digest') != digest(base):
        raise ValueError('Dev additions fixture digest/version differs')
    if any(c.get('split') != 'dev' for c in additions['cases']):
        raise ValueError('Dev additions must be dev only')
    # Targets precede the newest v4 filler, so saturation remains meaningful.
    data = dict(base, rows=additions['rows'] + base['rows'], cases=base['cases'] + additions['cases'])
    return pairs.validate(data)


def seed_fixture(store, data):
    """Monotonic synthetic creation times preserve row order under a fixed clock."""
    now = datetime.fromisoformat(data['today']).replace(hour=11, tzinfo=timezone.utc).timestamp()
    ticks = iter(range(1, 1000000))
    with fixture_scope(data), mock.patch('time.time', side_effect=lambda:now+next(ticks)/1000):
        return pairs.seed(store,data)


def raw_row(store, ident):
    with store.db() as db:
        row = db.execute('SELECT * FROM memories WHERE id=?', (ident,)).fetchone()
    return dict(row) if row else None


def validate_terms(recording, data):
    if recording.get('version') != TERMS_VERSION or recording.get('fixture_digest') != digest(data):
        raise ValueError('Terms fixture digest/version differs')
    if recording.get('filter_digest') != filter_digest():
        raise ValueError('Terms filter-version differs')
    if recording.get('terms_prompt_digest') != terms_prompt_digest():
        raise ValueError('Terms prompt digest differs')
    if recording.get('fixture_version') != data['version']:
        raise ValueError('Terms fixture version differs')
    if set(recording.get('rows', {})) != {r['ref'] for r in data['rows']}:
        raise ValueError('Terms row coverage differs')
    from personal_agent.memory_terms import parse_meta
    for entry in recording['rows'].values():
        if not isinstance(entry, dict) or not isinstance(entry.get('terms'), str):
            raise ValueError('Invalid terms row')
        meta = entry.get('meta')
        if meta is not None and (not isinstance(meta, dict) or meta.get('v') != TERMS_VERSION
                                 or type(meta.get('g')) is not int or parse_meta(meta)['g'] != meta['g']
                                 or meta.get('status') not in {'keyed', 'empty', 'failed', 'oversize', 'cleared'}):
            raise ValueError('Invalid terms meta')
    if 'status_counts' in recording and recording['status_counts'] != terms_status_counts(recording):
        raise ValueError('Terms status counts differ')
    return recording


def terms_status_counts(recording):
    """Stored row lifecycle and product manifest results, including zero counts."""
    rows = Counter({s: 0 for s in ('keyed','empty','failed','oversize','cleared','no_meta')})
    rows.update((entry.get('meta') or {}).get('status', 'no_meta')
                for entry in recording['rows'].values())
    results = Counter({s: 0 for s in ('ok','rejected','stale','unsent')})
    results.update(m.get('result', 'no_result') for m in recording.get('manifests', []))
    return dict(rows=dict(rows), manifests=dict(results))


def validate_recording_safety(recording, store):
    """Content-only check for committed synthetic inputs; never replay Works."""
    from personal_agent.agent_runtime import memory_value_has_secret
    if recording.get('tokens') != 'not observable':
        raise ValueError('Recording tokens must be not observable')
    if memory_value_has_secret(store,json.dumps(recording,ensure_ascii=False)):
        raise ValueError('Recording contains a secret shape')
    path = re.compile(r"(?:^|[\s\"'(=])(?:/(?!/)|[A-Za-z]:[\\/]|\\\\)")
    def scan(value):
        if isinstance(value,str) and path.search(value):
            raise ValueError('Recording contains an absolute path')
        if isinstance(value,dict):
            for key,item in value.items():
                scan(key)
                scan(item)
        elif isinstance(value,list):
            for item in value:
                scan(item)
    scan(recording)
    return recording


def apply_terms(store, data, recording, refs=None):
    """Filter again and CAS against the current fixture row; never overwrite a revision."""
    from personal_agent import memory_terms as mt
    validate_terms(recording, data)
    if refs is None:
        refs = fixture_refs(store, data)
    for source in data['rows']:
        ref = source['ref']
        row = raw_row(store, refs[ref])
        if row is None or row['content'] != source['content'] or row['memory_key'] != source['key'] or row['content_digest'] != store.memory_digest(source['key'],source['content']):
            raise ValueError('Stale terms input: ' + ref)
        entry = recording['rows'][ref]
        meta = entry['meta']
        if meta is None:
            if row['retrieval_terms_meta'] is not None and row['state'] == 'current' and not source.get('forgotten'):
                raise ValueError('Stale terms meta: ' + ref)
            continue
        if source.get('forgotten') or row['state'] != 'current' or row.get('valid_until') and row['valid_until'] < data['today']:
            # Export contains lifecycle meta for these rows, never applies new terms.
            continue
        terms, reason = mt.filter_terms(store, row['memory_key'], row['content'], entry['terms'])
        if reason or terms != entry['terms']:
            raise ValueError('Terms filter rejected recording: ' + ref)
        encoded = json.dumps(meta, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        current = row['retrieval_terms_meta']
        if current == encoded and (row['retrieval_terms'] or '') == terms:
            continue
        if current is not None or meta['g'] != mt.parse_meta(current)['g']:
            raise ValueError('Stale terms meta: ' + ref)
        if not store.write_retrieval_terms(data['owner'], row['id'], terms=terms or None, meta=encoded,
                expected_meta=current, content_digest=row['content_digest'], today=data['today']):
            raise ValueError('Guarded terms write refused: ' + ref)
    return refs


def fixture_refs(store, data):
    with store.db() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM memories')]
    refs = {}
    for source in data['rows']:
        matches = [r for r in rows if r['memory_key'] == source['key'] and r['content'] == source['content']]
        if len(matches) != 1:
            raise ValueError('Fixture row binding differs: ' + source['ref'])
        refs[source['ref']] = matches[0]['id']
    return refs


def safe_catalogue(catalogue, store):
    """Only public plan catalogue fields; never serialize routes, keys or destinations."""
    fields = {'id','kind','name','default','available','reason','native_search','browser','cost',
              'latency','default_model','models','model_tiers','tools','own_connections'}
    workers = [{k:v for k,v in w.items() if k in fields} for w in catalogue.workers]
    result = dict(workers=workers, default=catalogue.default, descriptions=catalogue.descriptions)
    # Reject rather than silently changing what the live planner saw.
    from personal_agent.agent_runtime import memory_value_has_secret
    text = json.dumps(result, ensure_ascii=False)
    if memory_value_has_secret(store, text) or re.search(r'(?:[\s"]/(?!/)|[A-Z]:\\)', text):
        raise ValueError('Catalogue contains a secret or local path')
    return result


def catalogue_from(recording):
    from personal_agent.orchestrator import Catalogue
    return Catalogue(recording['workers'], {}, recording['default'], recording.get('descriptions'))


def prompt_digest(catalogue):
    from .recall_arms import plan_prompt_schema_digest
    return plan_prompt_schema_digest(catalogue)


def validate_plans(recording, data, terms):
    if any(r.get('evidence_class') == 'dry-run (no model call)' for r in (recording, terms)):
        raise ValueError('Dry-run recording is not regression evidence')
    validate_terms(terms, data)
    if recording.get('version') != PLAN_VERSION or recording.get('fixture_digest') != digest(data):
        raise ValueError('Plan fixture digest/version differs')
    if recording.get('terms_digest') != digest(terms):
        raise ValueError('Plan terms-recording digest differs')
    cat = recording.get('catalogue')
    if not isinstance(cat, dict) or not cat.get('workers') or cat.get('default') not in {w['id'] for w in cat['workers']}:
        raise ValueError('Invalid recorded catalogue')
    if recording.get('plan_prompt_schema_digest') != prompt_digest(catalogue_from(cat)):
        raise ValueError('Plan prompt/schema digest differs')
    if (type(recording.get('complete')) is not bool or type(recording.get('heldout_runs')) is not int
            or recording['heldout_runs'] < 1 or not isinstance(recording.get('runs'),dict)
            or recording.get('tokens') != 'not observable'):
        raise ValueError('Invalid plan recording schema')
    def items(values):
        if not isinstance(values,list):
            raise ValueError('Invalid inventory schema')
        for item in values:
            if (not isinstance(item,dict) or set(item) != {'ref','kind','digest','matched','annotated'}
                    or item['ref'] not in {r['ref'] for r in data['rows']}
                    or item['kind'] not in {'profile','record'} or not isinstance(item['digest'],str)
                    or type(item['matched']) is not bool or type(item['annotated']) is not bool):
                raise ValueError('Invalid inventory schema')
    cases = {c['id']:c for c in data['cases']}
    for run, entries in recording.get('runs', {}).items():
        if not run.isdigit() or int(run) < 1 or not isinstance(entries, dict):
            raise ValueError('Invalid plan run')
        for ident, entry in entries.items():
            case = cases.get(ident)
            if not case or entry.get('query') != case['query'] or entry.get('context') != case.get('context',''):
                raise ValueError('Recorded plan case binding differs')
            if any(k not in entry for k in ('plan','accepted','inventory','input_digest','outcome')):
                raise ValueError('Incomplete plan entry')
            if entry.get('plan_outcome') != 'decided':
                raise ValueError('Recorded plan outcome is not decided')
            if not isinstance(entry.get('plan_failure'), str):
                raise ValueError('Invalid plan failure reason')
            plan = entry['plan']
            if not isinstance(plan, dict) or any(k not in plan for k in ('reply','recall_query','worker',*FLAGS)):
                raise ValueError('Invalid recorded plan')
            if any(not isinstance(plan[k], str) for k in ('reply','recall_query','worker')):
                raise ValueError('Invalid plan text')
            if any(type(plan[k]) is not bool for k in FLAGS[:3]):
                raise ValueError('Invalid plan flags')
            if not re.fullmatch('[0-9a-f]{64}', entry['input_digest']):
                raise ValueError('Invalid plan-input digest')
            if type(entry['accepted']) is not bool:
                raise ValueError('Invalid plan acceptance')
            items(entry['inventory'])
            outcome = entry['outcome']
            if (not isinstance(outcome,dict) or type(outcome.get('withheld')) is not bool
                    or not isinstance(outcome.get('receipt_counts'),dict)
                    or not isinstance(outcome.get('worker_refs'),list)):
                raise ValueError('Invalid plan outcome')
            items(outcome.get('worker_items'))
            items(outcome.get('checked_items'))
            if outcome['worker_refs'] != [i['ref'] for i in outcome['worker_items']]:
                raise ValueError('Worker refs differ from dispatched inventory')
            if entry['outcome'].get('final_answer_by') not in {'planner','worker'}:
                raise ValueError('Invalid plan outcome')
    if set(recording['runs']) - {str(i) for i in range(1,recording['heldout_runs']+1)}:
        raise ValueError('Unexpected plan run')
    if recording.get('complete'):
        r = recording['heldout_runs']
        expected = {c['id'] for c in data['cases']}
        if set(recording['runs'].get('1', {})) != expected:
            raise ValueError('Missing recorded plan cases')
        for run in range(2,r+1):
            if set(recording['runs'].get(str(run),{})) != {c['id'] for c in data['cases'] if c['split']=='heldout'}:
                raise ValueError('Missing recorded heldout cases')
    return recording


@contextmanager
def fixture_scope(data):
    from personal_agent import quickstart_service as qs, agent_runtime as ar
    from personal_agent.memory_service import MemoryService
    now = datetime.fromisoformat(data['today']).replace(hour=12, tzinfo=timezone.utc).timestamp()
    # Both classes expose clock injection, but their defaults captured the real
    # functions at import. Bind those clocks explicitly so plan budget facts do
    # not vary by a second with host scheduling or a resume boundary.
    class FixtureBudget(qs.WorkBudget):
        def __init__(self, **kwargs):
            super().__init__(**(kwargs | dict(clock=lambda:now,wall=lambda:now)))
    class FixtureLedger(qs.WorkLedger):
        def __init__(self, *args, **kwargs):
            super().__init__(*args,**(kwargs | dict(wall=lambda:now)))
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(qs,'WorkBudget',FixtureBudget))
        stack.enter_context(mock.patch.object(qs,'WorkLedger',FixtureLedger))
        stack.enter_context(mock.patch.object(qs, 'MEMORY_OWNER', data['owner']))
        stack.enter_context(mock.patch.object(ar, 'MEMORY_OWNER', data['owner']))
        stack.enter_context(mock.patch.object(MemoryService, '_today', return_value=data['today']))
        stack.enter_context(mock.patch('time.time', return_value=now))
        yield now


def forbidden_worker(*args, **kwargs):
    raise RuntimeError('Real worker adapter reached in recall fixture')


class FixtureWorker:
    def __init__(self):
        self.contexts = []

    def execute(self, engine, prompt, tools, **kw):
        from personal_agent.bounded_execution import ExecutionResult
        self.contexts.append(kw.get('context'))
        return ExecutionResult('fixture worker answer', engine, 0, {'tool_calls':[], 'stream_tail':['thread.started','turn.started','turn.completed']})

    def login_status(self, *args, **kw):
        return {'state':'signed-in'}


class FixtureTransport:
    def __init__(self):
        self.bodies = []

    def __call__(self, url, body, headers=None, timeout=60):
        self.bodies.append(body)
        names = [(t.get('function') or {}).get('name') for t in body.get('tools', [])]
        message = {'role':'assistant','content':'fixture worker answer'}
        if 'agentos_connection_probe' in names:
            message = {'role':'assistant','tool_calls':[{'id':'fixture-probe','type':'function',
                       'function':{'name':'agentos_connection_probe','arguments':'{}'}}]}
        return {'model':body['model'], 'choices':[{'message':message}]}


def fixture_service(store):
    from personal_agent.quickstart_service import AgentService
    from personal_agent.browser_session import BrowserProfile
    from personal_agent.providers import ModelAdapter
    from personal_agent.subscription_engines import SubscriptionEngines
    service = AgentService(store, browser_profile=BrowserProfile(store.private/'browser-fixture', available=lambda:False), adapter=ModelAdapter(FixtureTransport()), execution_adapter=FixtureWorker(),
        subscription_engines=SubscriptionEngines(finder=lambda _: '/fixture/cli', clock=lambda:1))
    install_fixture_workers(service)
    # Offline replay offers CLI identities too; this adapter's login is a fixture.
    with mock.patch.object(service.decision_routes,'follow_main_switched',return_value={'state':'skipped'}):
        service.activate_main_ai({'route':'codex'})
    return service


def install_fixture_workers(service):
    """Sandbox-only Main AI fixture setup; activation probes use fixture transports."""
    from personal_agent.providers import ModelAdapter
    with mock.patch.object(service,'adapter',ModelAdapter(FixtureTransport())), \
            mock.patch.object(service.decision_routes,'follow_main_switched',return_value={'state':'skipped'}):
        # Keep CLI discovery/status on the original adapter. Only the API worker
        # needs activation: its connection probe uses FixtureTransport, never a
        # local CLI login probe, and the stored decision route is left intact.
        service.save_main_ai_key({'provider':'openai','key':'sk-'+'f'*48})
        service.activate_main_ai({'route':'openai'})



class Executor:
    """One product run_one path for phase B and replay, with isolated case state.

    Mirroring _commit_profile_shown's exact positional line rule for ALL items
    yields the worker inventory; the real method still commits annotations.
    SQLite backup restores the pre-case store after every observation so no
    previous evaluation Work, consumption or upkeep can alter another case.
    """
    def __init__(self, service, data, refs, catalogue):
        self.service, self.data, self.refs, self.catalogue = service, data, refs, catalogue
        self.reverse = {ident:ref for ref,ident in refs.items()}

    def inventory(self, items):
        result = []
        for i in items:
            if i['id'] not in self.reverse:
                raise ValueError('Non-fixture row in executor inventory')
            result.append({k:i[k] for k in ('kind','digest','matched','annotated')} | {'ref':self.reverse[i['id']]})
        return result

    def run(self, case, engine, *, condition=None):
        from personal_agent import quickstart_service as qs, preparations, owner_model as om
        from personal_agent.work_executor import WorkExecutor
        from personal_agent.providers import ModelAdapter
        from personal_agent.bounded_execution import BoundedExecutionAdapter
        service, store = self.service, self.service.store
        baseline = sqlite3.connect(':memory:')
        with store.db() as db:
            db.backup(baseline)
        captured, dispatched, worker_items, attempts, checked = {}, [], [], [], []
        original_prepare, original_commit = service._prepare_worker_turn, WorkExecutor._commit_profile_shown
        worker = FixtureWorker()
        transport = FixtureTransport()
        def prepared(run):
            # Normal path retains #1261; at most one plan is admitted by the router.
            if condition == 'early-revision':
                early = service._early_plan.result()
                if not early.get('profile_items'):
                    raise ValueError('Early revision check requires a nonempty inventory')
                item = early['profile_items'][0]
                row = raw_row(store, item['id'])
                with store.db() as db:
                    db.execute('UPDATE memories SET content_digest=? WHERE id=?', ('revision-for-check',row['id']))
                # Rendered text is unchanged; early inventory must stay frozen.
                captured['early_inventory'] = self.inventory(early['profile_items'])
            value = original_prepare(run)
            captured['inventory'] = self.inventory(run.orchestration.plan_inventory)
            captured['accepted'] = bool(run.attempt and run.attempt.planned)
            attempts.append(run.attempt)
            if condition == 'early-revision':
                captured['early_used'] = run.orchestration.early_used
            annotation = case.get('id') == 'gate-annotated'
            if annotation:
                rowid = self.refs['gate-annotated-row']
                om.MemoryRaise(store).add([{'kind':om.RAISE_HELD,'memory_id':rowid,'reason':'fixture annotation'}])
            return value
        def commit(executor, run, context):
            dispatched.append(context)
            text = (context or {}).get('profile','') if (context or {}).get('mode') != 'bare-request' else ''
            items = [i for i in run.profile_items if text[i['start']:i['start']+len(i['line'])] == i['line']]
            worker_items.extend(self.inventory(items))
            return original_commit(executor, run, context)
        original_snapshot = service.worker_profile_snapshot
        gate_started = []
        def refresh(*args, **kwargs):
            value = original_snapshot(*args, **kwargs)
            if kwargs.get('with_items') and not kwargs.get('refresh'):
                from personal_agent.orchestrator import cut_profile_fact
                _, items = cut_profile_fact(value[0],value[2],limit_chars=len(value[0]))
                checked[:] = self.inventory(items)
            if condition == 'steer':
                gate_started.append('fixture owner correction')
            return value
        # Keep catalogue identity/schema while routing execution to fixtures only.
        def route(orchestration, attempt, config, key, snapshot):
            selected = self.catalogue.worker(attempt.worker) if attempt else None
            if selected and selected['kind'] == 'api':
                return store.config('model',{}), store.secret('model_key'), {}, store.config('model_test',{})
            return config, key, dict(snapshot or {}, id=selected['id'] if selected else (snapshot or {}).get('id','codex')), None
        try:
            with fixture_scope(self.data) as now, ExitStack() as stack:
                service.context_observations.clock = lambda:now
                service.use_decision_engine(engine)
                for target, attr, value in ((qs,'worker_catalogue',lambda _:self.catalogue),
                        (service.execution_adapter,'execute',worker.execute),(service,'adapter',ModelAdapter(transport)),
                        (service,'attempt_route',route),(service,'_prepare_worker_turn',prepared),
                        (service,'worker_profile_snapshot',refresh), (service,'day_plan_run',lambda *_:False)):
                    stack.enter_context(mock.patch.object(target,attr,value))
                stack.enter_context(mock.patch('personal_agent.work_executor.BrowserRelay',
                    lambda *a,**kw:SimpleNamespace(address=('fixture',0),close=lambda:None)))
                # Tool discovery must not launch a node --version subprocess
                # while listing tools for a worker that is entirely scripted.
                stack.enter_context(mock.patch('personal_agent.code_run.CodeRunner.available',return_value=False))
                stack.enter_context(mock.patch.object(WorkExecutor,'_commit_profile_shown',commit))
                stack.enter_context(mock.patch.object(BoundedExecutionAdapter,'execute',forbidden_worker))
                if service.isolated_engine_adapter is not None:
                    stack.enter_context(mock.patch.object(service.isolated_engine_adapter,'execute',forbidden_worker))
                # A transport fixture is the ONLY allowed API worker transport.
                original_send = ModelAdapter._send
                def send(adapter,*args,**kwargs):
                    if not isinstance(adapter.transport,FixtureTransport):
                        return forbidden_worker()
                    return original_send(adapter,*args,**kwargs)
                stack.enter_context(mock.patch.object(ModelAdapter,'_send',send))
                if condition == 'steer':
                    stack.enter_context(mock.patch.object(store,'work_steer_texts',lambda *_:list(gate_started)))
                if condition == 'location':
                    stack.enter_context(mock.patch.object(service,'location_requester',lambda *_:lambda *_:None))
                    stack.enter_context(mock.patch.object(service,'plan_asked_location',lambda *_:False))
                if case.get('context'):
                    previous = store.enqueue(case['context'],'recall-previous',owner_typed=True,owner_words=True)
                    with store.db() as db:
                        db.execute("UPDATE jobs SET created=?, status='succeeded' WHERE id=?",(now-1,previous))
                        db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                                   ('user',case['context'],'web',now-1,previous))
                if condition == 'preparation':
                    # Public preparation start calls enqueue with its request key
                    # atomically and satisfies the real queue admission check.
                    stack.enter_context(mock.patch.object(service.preparations,'clock',lambda:now))
                    preparation = service.preparations.create(kind='prepare', goal=case['query'],
                        due_at=now, timezone='UTC', recurrence=None, channel='web',
                        created_from='settings', state=preparations.STATE_SCHEDULED,
                        accepted_by=preparations.ACCEPTED_OWNER_SETTINGS)
                    job = service.preparations.start(preparation,channel='web',chat_id=None,now=now)
                    if not job:
                        raise ValueError('Preparation Work was not enqueued')
                else:
                    job = store.enqueue(case['query'],'recall-current',owner_typed=True,owner_words=True)
                if not service.run_one():
                    raise ValueError('Executor did not claim fixture Work')
                receipts = store.work_decisions(job)
                withheld = [r for r in receipts if r.get('route') == 'plan-reply-withheld']
                direct = [r for r in receipts if r.get('purpose') == 'plan-direct-reply']
                outcome = dict(final_answer_by='planner' if direct else 'worker', withheld=bool(withheld),
                    receipt_counts=dict(withheld=len(withheld), replies=len(direct),
                        seen=sum(r['seen_count'] for r in withheld), matched=sum(r['matched_count'] for r in withheld),
                        new=sum(r['new_count'] for r in withheld)), worker_items=worker_items,
                    worker_refs=[i['ref'] for i in worker_items], checked_items=checked)
                if not captured or (not direct and not dispatched):
                    raise ValueError('Executor did not reach planner/fixture worker dispatch')
                captured.update(outcome=outcome, reply=attempts[0].reply, dispatched=dispatched)
                return captured
        finally:
            with store.db() as db:
                baseline.backup(db)
            baseline.close()


def replay_case(executor, case, entry, *, condition=None):
    from .recall_record import PurposeRouter
    plan = dict(entry['plan'])
    if condition == 'location':
        plan['ask_location'] = 'fixture position?'
    router = PurposeRouter(plan=plan)
    result = executor.run(case, router.engine, condition=condition)
    if router.error:
        raise ValueError(router.error)
    if not condition:
        if result['inventory'] != entry['inventory']:
            raise ValueError('Replay inventory A mismatch')
        if result['accepted'] != entry['accepted'] or result['outcome'] != entry['outcome']:
            raise ValueError('Replay executor outcome mismatch')
    return result
