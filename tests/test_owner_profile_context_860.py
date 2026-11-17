"""#883: accepted selectors, request-only planning and existing reader boundaries (offline)."""
from types import SimpleNamespace
from unittest import mock

import pytest

from personal_agent import preparations
from personal_agent.memory_service import MemoryService
from personal_agent.owner_forget import OwnerForget
from personal_agent.quickstart_service import AgentService, MEMORY_OWNER
from personal_agent.quickstart_store import QuickStore
from personal_agent.work_execution import PROCEED, WorkRun


@pytest.fixture
def service(tmp_path):
    return AgentService(QuickStore(tmp_path / 'state'))


def turn(service, text, at, *, channel='telegram:generation', chat=42,
         typed=True, owner_words=True, role='user', written=True, key=None):
    store=service.store
    work=store.enqueue(text, key or f'turn-{at}', channel=channel, chat_id=chat,
                       owner_typed=typed, owner_words=owner_words)
    with store.db() as db:
        db.execute("UPDATE jobs SET created=?,status='succeeded' WHERE id=?", (at,work))
        if written:
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       (role,text,channel,at,work))
    return store.job(work)



from personal_agent.orchestrator import Attempt
from personal_agent import owner_model as om


def test_accepted_recall_query_precedes_request_for_all_three_readers(service):
    with mock.patch.object(MemoryService,'profile_snapshot',return_value={'text':'','entries':[]}) as profile, \
            mock.patch.object(service,'owner_words_block',return_value='') as words, \
            mock.patch.object(MemoryService,'records_block',return_value=('',[])) as records:
        service.owner_profile_snapshot('unrelated request',recall_query='resolved identifier')
    assert profile.call_args.kwargs['query'] == 'resolved identifier unrelated request'
    words.assert_called_once_with('resolved identifier unrelated request')
    records.assert_called_once_with(MEMORY_OWNER,'resolved identifier unrelated request',with_items=True)


@pytest.mark.parametrize('selector',[None,'','   '])
def test_absent_or_empty_recall_query_uses_request_only(service,selector):
    with mock.patch.object(service,'owner_words_block',return_value='') as words:
        service.owner_profile_snapshot('current request',recall_query=selector)
    words.assert_called_once_with('current request')


def test_recall_selector_redacts_before_500_character_bound(service):
    secret='stored-secret-883-value'
    service.store.secret('model_key',secret)
    query,identity=service.recall_selector('request',recall_query='x'*480+secret+' more')
    assert query == ('x'*480+'[redacted] more request')[:500]
    assert secret not in query and identity['query_chars']==len(query)
    import hashlib
    assert identity['query_digest']==hashlib.sha256(query.encode()).hexdigest()[:16]


def test_recall_selector_uses_existing_12_literal_terms_not_fts_operators(service):
    selector='"first" OR second* NOT third NEAR(fourth) '+ ' '.join(f'term{i}' for i in range(12))
    service.store.save_memory('profile.control','first')
    with mock.patch.object(service.store,'search_memories',wraps=service.store.search_memories) as search:
        service.owner_profile_snapshot('ignored',recall_query=selector,notes=False)
    assert search.call_args.args[1] == ['first','or','second','not','third','near','fourth','term0','term1','term2','term3','term4']


@pytest.mark.parametrize('day_plan',[False,True])
def test_preparation_ignores_selector_and_day_plan_keeps_existing_recall(service,day_plan):
    job=turn(service,'request',30,key=preparations.request_key('preparation-id',30))
    with mock.patch.object(service,'day_plan_run',return_value=day_plan), \
            mock.patch.object(service,'owner_words_block',return_value='') as words:
        _,identity=service.worker_profile_snapshot(job,Attempt(1,'fixture',planned=True,recall_query='resolved'))
    assert identity['recall_enabled'] == day_plan
    assert identity['source'] == ('plan' if day_plan else 'request')
    if day_plan: words.assert_called_once_with('resolved request')
    else: words.assert_not_called()


@pytest.mark.parametrize('preparation',[False,True])
@pytest.mark.parametrize('day_plan',[False,True])
def test_early_and_final_planner_profiles_use_request_only_and_same_day_plan_policy(service,preparation,day_plan):
    turn(service,'prior words',10)
    key=preparations.request_key('preparation-id',30) if preparation else None
    job=turn(service,'request',30,key=key)
    with mock.patch.object(service,'profile_section',return_value=('',[])) as snapshot, \
            mock.patch.object(service,'work_orchestration',return_value=None), \
            mock.patch.object(service,'preparation_history',return_value=[]), \
            mock.patch.object(service,'day_plan_run',return_value=day_plan), \
            mock.patch.object(service,'current_context_text',return_value=''), \
            mock.patch.object(service,'prepared_text',return_value=''):
        service.early_work_plan(job,'request',budget=SimpleNamespace(ledger=None))
        run=WorkRun(job,30); run.prompt='request'
        assert service._prepare_worker_turn(run) is PROCEED
    assert snapshot.call_args_list == [mock.call('request',recall=not preparation or day_plan,consume=False)]*2


def test_planner_history_preserves_existing_exclusions_and_window(service):
    hidden=turn(service,'forgotten words',10)
    OwnerForget(service.store).forget(MEMORY_OWNER,f"message:{service.store.history()[-1]['id']}")
    document=turn(service,'document payload',20)
    service.store.put('file_workspace_document_jobs',[document['id']])
    service.store.secret('model_key','retired-secret-883')
    turn(service,'retired-secret-883 prior words',25)
    service.store.secret('model_key','replacement-secret-883')
    job=turn(service,'current request',30)
    original=service.work_orchestration
    from test_orchestrator import OrchestrationUnit
    with mock.patch('personal_agent.quickstart_service.worker_catalogue',return_value=OrchestrationUnit().catalogue()):
        rows=service.history_for_models(service.store.history(through=job['id'])[-16:])
        orchestration=original(job,job['message'],rows,{},None,document_jobs=[document['id']])
        text=orchestration._plan_context(orchestration.catalogue.available()).facts['recent_conversation']
        assert 'forgotten words' not in text and 'document payload' not in text
        assert 'retired-secret-883' not in text and '[redacted]' in text
        assert 'current request' not in text and 'prior words' in text
        empty=original(job,job['message'],[],{},None)
        assert empty.conversation == ''
        # The existing history read stays at its 16-row bound; no alternate predecessor lookup.
        with mock.patch.object(service.store,'history',wraps=service.store.history) as history:
            service.early_work_plan(job,job['message'],budget=SimpleNamespace(ledger=None))
        history.assert_called_once_with(through=job['id'])


def test_worker_refresh_does_not_reraise_consumed_annotation(service):
    row=service.store.save_memory('profile.control','needle')
    om.MemoryRaise(service.store).add([{'kind':om.RAISE_HELD,'memory_id':row['id'],'reason':'unsure'}])
    job=turn(service,'needle',30)
    service.current_work_id=job['id']
    first=service.owner_profile_snapshot('needle')
    second,_=service.worker_profile_snapshot(job,Attempt(1,'fixture',planned=True,recall_query='needle'))
    # Same-Work briefs intentionally retain the annotation, without reopening it.
    assert 'unsure' in first and 'unsure' in second
    assert om.MemoryRaise(service.store).open() == {}
    assert om.MemoryRaise(service.store).open(job['id'])[row['id']]['shown_in'] == job['id']
    service.current_work_id='later-work'
    later,_=service.worker_profile_snapshot(job,Attempt(1,'fixture',planned=True,recall_query='needle'))
    assert 'unsure' not in later


def test_worker_selector_is_sanitized_once_and_matches_snapshot_and_identity(service):
    import hashlib
    job=turn(service,'request',30)
    selectors=[]
    original=service.recall_selector
    def select(*args,**kwargs):
        result=original(*args,**kwargs);selectors.append(result);return result
    with mock.patch.object(service,'recall_selector',side_effect=select), \
            mock.patch.object(MemoryService,'profile_snapshot',return_value={'text':'','entries':[]}) as snapshot:
        _,identity=service.worker_profile_snapshot(job,Attempt(1,'fixture',planned=True,recall_query='plan'))
    assert len(selectors)==1
    assert snapshot.call_args.kwargs['query']=='plan request'
    assert identity['query_digest']==hashlib.sha256(b'plan request').hexdigest()[:16]


def test_resolved_terms_keep_priority_but_request_matches_remain_admitted(service):
    service.store.save_memory('profile.planned','resolvedneedle')
    service.store.save_memory('profile.request','requestneedle')
    # The real reader must match both sources; raw preceding-topic words are absent.
    seen=[]
    original=service.store.search_memories
    def search(*args,**kwargs):
        result=original(*args,**kwargs);seen.extend(row['memory_key'] for row in (result['memories'] if isinstance(result,dict) else result));return result
    with mock.patch.object(service.store,'search_memories',side_effect=search):
        service.owner_profile_snapshot('requestneedle',recall_query='resolvedneedle',notes=False)
    assert set(seen)=={'profile.planned','profile.request'}
    query,_=service.recall_selector('requestneedle',recall_query=' '.join(f'plan{i}' for i in range(12)))
    with mock.patch.object(service.store,'search_memories',wraps=original) as search:
        service.owner_profile_snapshot('requestneedle',recall_query=' '.join(f'plan{i}' for i in range(12)),notes=False)
    assert search.call_args.args[1]==[f'plan{i}' for i in range(12)]


def test_planner_profile_receipts_exclude_mid_line_cut_key(service):
    from test_orchestrator import OrchestrationUnit, decided, plan
    from personal_agent.decision import FixtureDecisionEngine
    first='profile.whole: completely sent'
    profile=first+'\nprofile.partial: '+('x'*200)+'\nprofile.later: not sent'
    job=turn(service,'request',30)
    asked=[]
    def structured(context,question,schema):
        asked.append(context);return decided(plan('a',''))
    service.use_decision_engine(FixtureDecisionEngine(structured=structured))
    with mock.patch('personal_agent.quickstart_service.worker_catalogue',return_value=OrchestrationUnit().catalogue()), \
            mock.patch.object(service,'profile_section',return_value=(profile,[])), \
            mock.patch('personal_agent.orchestrator.PROFILE_FACT_CHARS',64):
        from personal_agent.agent_runtime import WorkBudget
        service.early_work_plan(job,job['message'],budget=WorkBudget())
    assert asked[0].facts['owner_profile']==first
    assert service.store.turn_provenance(job['id'])['owner_information']['profile_keys']==['profile.whole']
