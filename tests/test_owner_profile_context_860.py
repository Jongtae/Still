"""#860: bounded lexical context, readable preceding turns, and brief wiring (offline)."""
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


@pytest.mark.parametrize(('query_request','context'), [
    ('다시','앞서 고른 장소'),
    (' '.join(f'request{i}' for i in range(12)), 'context after request terms'),
    ('q'*490, 'context '*100),
    ('q'*510, 'context'),
    (None, 'context alone'),
])
def test_one_request_first_bounded_query_feeds_all_three_readers(service, query_request, context):
    combined=(query_request+' '+context if query_request else context)[:500]
    with mock.patch.object(MemoryService,'profile_snapshot',return_value={'text':'','entries':[]}) as profile, \
            mock.patch.object(service,'owner_words_block',return_value='') as words, \
            mock.patch.object(MemoryService,'records_block',return_value='') as records, \
            mock.patch.object(service,'_memory_raise_notes',return_value={'memory-id':'note'}) as raising:
        service.owner_profile_snapshot(query_request,context=context)
    assert profile.call_args.kwargs['query']==combined
    assert profile.call_args.kwargs['annotations']=={'memory-id':'note'}
    words.assert_called_once_with(combined)
    records.assert_called_once_with(MEMORY_OWNER,combined)
    raising.assert_called_once()


def test_existing_fts_term_limit_keeps_request_terms_first(service):
    query_request=' '.join(f'request{i}' for i in range(12))
    with mock.patch.object(service.store,'search_memories',wraps=service.store.search_memories) as search:
        service.owner_profile_snapshot(query_request,context='additional context',notes=False)
    assert search.call_args.args[1]==query_request.split()


def test_combined_query_redacts_before_clipping(service):
    secret='stored-secret-860-value'
    service.store.secret('model_key',secret)
    with mock.patch.object(service,'owner_words_block',return_value='') as words:
        service.owner_profile_snapshot('again '+secret,context='x'*460+' '+secret+' more')
    expected=('again [redacted] '+'x'*460+' [redacted] more')[:500]
    assert words.call_args.args[0]==expected
    assert secret not in words.call_args.args[0]


@pytest.mark.parametrize('query_request', [None,'','   ','다시','x'*510])
def test_no_context_preserves_snapshot_bytes_and_queries(service, query_request):
    service.store.save_memory('profile.place','이전에 고른 장소')
    with mock.patch.object(service,'owner_words_block',return_value='') as words:
        before=service.owner_profile_snapshot(query_request)
        absent=service.owner_profile_snapshot(query_request,context=None)
        empty=service.owner_profile_snapshot(query_request,context='')
    assert before==absent==empty
    expected=service._redact_known_secrets(query_request)[:500] if isinstance(query_request,str) and query_request.strip() else None
    assert words.call_args_list==[mock.call(expected)]*3


@pytest.mark.parametrize('query_request', [None,'','again'])
@pytest.mark.parametrize('notes', [False,True])
def test_recall_false_ignores_context_in_output_and_profile_query(service, query_request, notes):
    service.store.save_memory('profile.place','observatory trail')
    original=MemoryService.profile_snapshot
    with mock.patch.object(MemoryService,'profile_snapshot',autospec=True,side_effect=original) as profile, \
            mock.patch.object(service,'owner_words_block') as words, \
            mock.patch.object(MemoryService,'records_block') as records:
        baseline=service.owner_profile_snapshot(query_request,notes=notes,recall=False)
        given=service.owner_profile_snapshot(query_request,notes=notes,recall=False,context='observatory trail')
    assert given==baseline
    expected=query_request if query_request else None
    assert [call.kwargs['query'] for call in profile.call_args_list]==[expected,expected]
    words.assert_not_called()
    records.assert_not_called()


@pytest.mark.parametrize('channel', ['web','telegram:generation','slack:generation','mattermost:generation'])
def test_conversation_binding_always_compares_channel_and_chat_id(service, channel):
    turn(service,'same conversation',5,channel=channel,chat=9)
    turn(service,'other conversation',10,channel=channel,chat=7)
    current=turn(service,'again',30,channel=channel,chat=9)
    assert service.preceding_owner_turn(current)=='same conversation'


def test_queued_predecessor_can_be_claimed_after_followup_is_enqueued(service):
    turn(service,'older unrelated turn',10)
    earlier=turn(service,'queued predecessor',50,written=False)
    current=turn(service,'followup',51,written=False)
    with service.store.db() as db:
        db.execute("UPDATE jobs SET status='queued' WHERE id IN (?,?)",(earlier['id'],current['id']))
    with mock.patch('personal_agent.work_executor.time.time',return_value=100):
        assert service._claim_queued_work()['id']==earlier['id']
    with service.store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded' WHERE id=?",(earlier['id'],))
    with mock.patch('personal_agent.work_executor.time.time',return_value=130):
        claimed=service._claim_queued_work()
    assert claimed['id']==current['id']
    rows=service.store.history(through=current['id'])
    assert next(row['created'] for row in rows if row['job_id']==earlier['id'])==100
    assert current['created']==51
    assert service.preceding_owner_turn(claimed)=='queued predecessor'


@pytest.mark.parametrize(('typed','owner_words','key'), [
    (False,False,preparations.request_key('day-plan',30)),
    (True,False,'bare-api-request'),
])
def test_non_owner_current_work_has_no_preceding_context(service, typed, owner_words, key):
    turn(service,'old owner words',10,channel='web',chat=None)
    current=turn(service,'scheduled or machine request',30,channel='web',chat=None,
                 typed=typed,owner_words=owner_words,key=key)
    with mock.patch.object(service.store,'history',wraps=service.store.history) as history:
        assert service.preceding_owner_turn(current)==''
    history.assert_not_called()


@pytest.mark.parametrize('excluded', [
    'forgotten','assistant','not-typed','api-not-owner-words','other-channel',
    'other-chat','document',
])
def test_preceding_owner_turn_skips_ineligible_rows(service, excluded):
    turn(service,'eligible owner words',10)
    options={
        'assistant':{'role':'assistant'},
        'not-typed':{'typed':False},
        'api-not-owner-words':{'channel':'web','owner_words':False},
        'other-channel':{'channel':'telegram:other-generation'},
        'other-chat':{'chat':43},
    }.get(excluded,{})
    if excluded=='api-not-owner-words':
        # Same binding as the current API Work; attribution alone excludes it.
        with service.store.db() as db:
            db.execute("UPDATE jobs SET channel='web' WHERE created=10")
            db.execute("UPDATE messages SET channel='web' WHERE created=10")
    rejected=turn(service,'excluded words',20,**options)
    if excluded=='forgotten':
        message=service.store.history()[-1]['id']
        OwnerForget(service.store).forget(MEMORY_OWNER,f'message:{message}')
    elif excluded=='document':
        service.store.put('file_workspace_document_jobs',[rejected['id']])
    current=turn(service,'다시',30,channel='web' if excluded=='api-not-owner-words' else 'telegram:generation')
    turn(service,'later owner words',40,channel=current['channel'])
    assert service.preceding_owner_turn(current)=='eligible owner words'


def test_preceding_owner_turn_uses_latest_owner_and_redacts_before_bound(service):
    service.store.secret('model_key','current-secret-860')
    service.store.secret('api_key:openai','retired-secret-860')
    service.store.secret('api_key:openai','replacement-secret-860')
    turn(service,'older owner words',10)
    text='current-secret-860 retired-secret-860 '+'x'*600
    turn(service,text,20)
    current=turn(service,'그거',30)
    assert service.preceding_owner_turn(current)==('[redacted] [redacted] '+'x'*600)[:500]


@pytest.mark.parametrize('written', [False,True])
def test_preceding_owner_turn_empty_without_prior_turn(service, written):
    current=turn(service,'그거',30,written=written)
    assert service.preceding_owner_turn(current)==''


@pytest.mark.parametrize('current_state', ['unwritten','forgotten','continued'])
def test_preceding_owner_turn_handles_missing_or_repeated_current_row(service, current_state):
    turn(service,'prior owner words',10)
    current=turn(service,'다시',30,written=current_state!='unwritten')
    if current_state=='forgotten':
        OwnerForget(service.store).forget(MEMORY_OWNER,f"message:{service.store.history()[-1]['id']}")
    elif current_state=='continued':
        with service.store.db() as db:
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('user','다시',current['channel'],40,current['id']))
    assert service.preceding_owner_turn(current)=='prior owner words'


@pytest.mark.parametrize('reader', ['history','config','job','retired_secret_ledger'])
def test_preceding_owner_turn_fails_to_empty_and_logs_no_content(service, caplog, reader):
    turn(service,'private earlier words',10)
    current=turn(service,'다시',30)
    with mock.patch.object(service.store,reader,side_effect=RuntimeError('private failure body')):
        assert service.preceding_owner_turn(current)==''
    assert 'preceding owner turn unavailable' in caplog.text
    assert 'private failure body' not in caplog.text
    assert 'private earlier words' not in caplog.text


def test_context_recall_keeps_information_use_references(service):
    earlier=turn(service,'observatory trail',10)
    matched=service.store.history()[-1]['id']
    service.store.save_memory('profile.observatory','observatory trail')
    with service.store.db() as db:
        db.executemany('INSERT INTO messages(role,content,channel,created) VALUES (?,?,?,?)',
                       [('assistant','unrelated',earlier['channel'],11)]*16)
    turn(service,'observatory',20)
    current=turn(service,'그거',30)
    profile=service.owner_profile_snapshot(current['message'],context=service.preceding_owner_turn(current))
    refs=service.owner_information_refs({'profile':profile})
    assert 'profile.observatory' in refs['profile_keys']
    assert refs['earlier_exchanges']==[{'work_id':earlier['id'],'matched_id':matched,'message_ids':[matched]}]
    service.record_turn_provenance(current['id'],owner_information=refs)
    recorded=service.store.turn_provenance(current['id'])['owner_information']
    assert recorded['earlier_exchanges']==refs['earlier_exchanges']
    assert recorded['profile_keys']==refs['profile_keys']


@pytest.mark.parametrize('preparation', [False,True])
@pytest.mark.parametrize('caller', ['early','worker'])
def test_brief_callers_pass_context_and_keep_recall_flag(service, caller, preparation):
    turn(service,'prior owner words',10)
    key=preparations.request_key('preparation-id',30) if preparation else None
    current=turn(service,'다시',30,key=key)
    with mock.patch.object(service,'owner_profile_snapshot',wraps=service.owner_profile_snapshot) as snapshot, \
            mock.patch.object(service,'preceding_owner_turn',wraps=service.preceding_owner_turn) as preceding, \
            mock.patch.object(service,'work_orchestration',return_value=None), \
            mock.patch.object(service,'preparation_history',return_value=[]), \
            mock.patch.object(service,'day_plan_run',return_value=False), \
            mock.patch.object(service,'current_context_text',return_value=''), \
            mock.patch.object(service,'prepared_text',return_value=''):
        if caller=='early':
            assert service.early_work_plan(current,current['message'],budget=SimpleNamespace(ledger=None)) is None
        else:
            run=WorkRun(current,30)
            run.prompt=current['message']
            assert service._prepare_worker_turn(run) is PROCEED
    snapshot.assert_called_once_with('다시',recall=not preparation,context=None if preparation else 'prior owner words')
    if preparation:
        preceding.assert_not_called()
    else:
        preceding.assert_called_once_with(current)
