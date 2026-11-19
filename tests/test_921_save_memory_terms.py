"""#921 PR-6: worker-written search terms, real SQLite, fake transports only."""

import io
import json
import sys
import time

import pytest

from personal_agent.agent_runtime import (
    DEFINITIONS, MEMORY_OWNER, SAVE_MEMORY_ARGUMENTS, SECRET_SHAPED_VALUE,
    THIRD_PARTY_MEMORY_WRITE, Capabilities, check_arguments, recorded_arguments,
    run_agent, trail_steps, worker_result,
)
from personal_agent import information_use, mcp_bridge
from personal_agent.bounded_execution import AgentOSMcpTools, CLI_PROFILES, profile_mcp_tools
from personal_agent.cli_browser_relay import SECRET_REDACTION
from personal_agent.current_context import redact_known_secrets
from personal_agent.manifests import runtime_packages
from personal_agent.memory_service import MemoryService
from personal_agent.memory_terms import eligible, parse_meta
from personal_agent.owner_forget import OwnerForget
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore

NOW = 1794916800.0
KEY = 'profile.preference.drink'
VALUE = 'jasmine tea'
TERMS = '차 beverage'
TERMS_DESCRIPTION = (
    "Optional search words a later request might use that the fact's own words lack: "
    'base forms without particles or endings, the key terms in Korean and in English, '
    'and words that tell this fact apart from similar ones. Space-separated words only.'
)
CFG = {'provider': 'compatible', 'endpoint': 'https://model.invalid/v1', 'model': 'requested-fixture'}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(time, 'time', lambda: NOW)
    store = QuickStore(tmp_path / 'state')
    work = store.enqueue('Remember my preferred drink.', 'save-terms', owner_typed=True,
                         owner_words=True, channel='telegram:fixture', chat_id=921)
    caps = Capabilities(store, None, {}, '', work, lambda *args: None, allowed_tools=['save_memory'])
    return store, work, caps


def raw_row(store, memory_id):
    with store.db() as db:
        return dict(db.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone())


def saved(state, **kwargs):
    return state[2].execute('save_memory', {'memory_key': KEY, 'content': VALUE, **kwargs})


def hits(store, query):
    service = MemoryService(store, private_read_sink=MemoryService.NO_EGRESS_GUARD)
    return [row['id'] for row in service.search_memories(MEMORY_OWNER, query)['memories']]


@pytest.mark.parametrize('fts', [True, False])
def test_terms_are_filtered_saved_atomically_and_searchable(state, fts, monkeypatch):
    store, work, caps = state
    store.memory_search_available = fts
    store.put_turn_provenance(work, {'workers': [
        {'route': 'direct-api', 'model': 'previous-worker'},
        {'route': 'subscription', 'model': 'worker-fixture'},
    ], 'reported_model': 'previous-attempt-report'})
    inserts = []
    original = store._save_memory

    def insert(db, *args, **kwargs):
        inserts.append((kwargs['retrieval_terms'], kwargs['retrieval_terms_meta']))
        return original(db, *args, **kwargs)

    monkeypatch.setattr(store, '_save_memory', insert)
    result = saved(state, terms='JASMINE tea preference 차 beverage 차 invented921')
    row = raw_row(store, result['id'])
    assert row['retrieval_terms'] == TERMS
    assert inserts == [(TERMS, row['retrieval_terms_meta'])]
    assert parse_meta(row['retrieval_terms_meta']) == {
        'v': 'recall-terms/1', 'g': 0, 'status': 'keyed', 'route': 'subscription',
        'model': 'worker-fixture', 'observed_model': 'not reported',
        'policy': 'worker', 'run': work, 'at': NOW,
    }
    assert hits(store, 'beverage') == [result['id']]
    assert hits(store, 'invented921') == []
    assert store.memory_candidates() == []
    # #955: the save is told in the reply; no separate saved notice is held.
    assert store.held_notifications(store.MEMORY_SAVED_NOTICE_KIND) == []
    shown = json.dumps([result, caps.evidence, worker_result('save_memory', result)])
    assert 'beverage' not in shown
    assert 'retrieval_terms' not in shown


@pytest.mark.parametrize('arguments', [{}, {'terms': ''}, {'terms': '   '}, {'terms': 'tea jasmine preference 123'}])
def test_absent_empty_or_fully_filtered_terms_leave_null_meta(state, arguments):
    store, _, _ = state
    result = saved(state, **arguments)
    row = raw_row(store, result['id'])
    assert row['retrieval_terms'] is None
    assert row['retrieval_terms_meta'] is None
    assert eligible(row['retrieval_terms_meta'])


def test_unknown_worker_identity_is_not_invented(state):
    store, _, _ = state
    result = saved(state, terms=TERMS)
    meta = parse_meta(raw_row(store, result['id'])['retrieval_terms_meta'])
    assert (meta['route'], meta['model'], meta['observed_model']) == ('', '', 'not reported')


@pytest.mark.parametrize('reported', ['observed-fixture', None])
def test_api_loop_records_the_current_response_identity(state, reported):
    store, work, caps = state
    # A retry's old reported model must never stand in for this response.
    store.put_turn_provenance(work, {'workers': [{'route': 'subscription', 'model': 'old-worker'}],
                                     'reported_model': 'old-report'})
    calls = []

    def transport(url, body, *args, **kwargs):
        calls.append(body)
        message = {'content': 'Done.'}
        if len(calls) == 1:
            message = {'tool_calls': [{'id': 'save', 'type': 'function', 'function': {
                'name': 'save_memory', 'arguments': json.dumps({'memory_key': KEY, 'content': VALUE, 'terms': TERMS}),
            }}]}
        return {'model': reported, 'choices': [{'message': message}]}

    run_agent(ModelAdapter(transport), CFG, '', [{'role': 'user', 'content': 'Remember my drink.'}],
              '', caps, lambda *args: None)
    [row] = store.memories()
    meta = parse_meta(raw_row(store, row['id'])['retrieval_terms_meta'])
    assert (meta['route'], meta['model'], meta['observed_model']) == (
        'direct-api', CFG['model'], reported or 'not reported')
    assert hits(store, 'beverage') == [row['id']]
    returned = [json.loads(m['content']) for body in calls for m in body['messages'] if m.get('role') == 'tool']
    assert returned and all('beverage' not in json.dumps(item) for item in returned)


@pytest.mark.parametrize('secret', ['CaseSensitiveSecret921', 'sk-proj-abcdefghijklmnopqrstuvwxyz123456'])
def test_a_secret_in_raw_terms_drops_all_terms_but_saves_the_fact(state, secret):
    store, _, caps = state
    store.secret('telegram_token', secret)
    result = saved(state, terms='beverage ' + secret + ' 차')
    row = raw_row(store, result['id'])
    assert (row['content'], row['state']) == (VALUE, 'current')
    assert row['retrieval_terms'] is None and row['retrieval_terms_meta'] is None
    assert hits(store, 'beverage') == []
    shown = json.dumps([result, caps.evidence, worker_result('save_memory', result)])
    assert secret not in shown and 'beverage' not in shown


@pytest.mark.parametrize('field', ['memory_key', 'content'])
def test_a_secret_in_the_fact_is_still_refused_before_any_write(state, field):
    store, _, caps = state
    secret = 'FactSecret921Value'
    store.secret('telegram_token', secret)
    result = saved(state, **{field: secret, 'terms': TERMS})
    assert result == {'saved': False, 'state': 'refused', 'refused_because': SECRET_SHAPED_VALUE}
    assert store.memories() == [] and store.memory_candidates() == []
    assert store.held_notifications(store.MEMORY_SAVED_NOTICE_KIND) == []
    assert secret not in json.dumps([result, caps.evidence])


@pytest.mark.parametrize('writer', ['outside-content', 'delegated', 'package'])
def test_candidates_drop_terms_without_filtering_or_persisting_them(state, writer, monkeypatch):
    store, work, caps = state
    tool = 'save_memory'
    if writer == 'outside-content':
        store.mark_outside_content(work, {'category': 'document', 'operation': 'read_file'})
    elif writer == 'delegated':
        caps.delegated = True
    else:
        tool = 'notebook_remember'
        package = {'version': 1, 'id': 'notebook', 'enabled': True, 'roles': [],
                   'tools': [{'id': tool, 'host_action': 'save_memory', 'mode': 'bounded_write'}]}
        caps = Capabilities(store, None, {}, '', work, lambda *args: None,
                            packages=runtime_packages([package]), allowed_tools=[tool])

    def unexpected_filter(*args):
        pytest.fail('a candidate must drop terms before filtering')

    monkeypatch.setattr('personal_agent.memory_terms.filter_terms', unexpected_filter)
    result = caps.execute(tool, {'memory_key': KEY, 'content': VALUE, 'terms': TERMS})
    assert result['state'] == 'pending' and result['refused_because'] == THIRD_PARTY_MEMORY_WRITE
    assert store.memories() == []
    [candidate] = store.memory_candidates()
    assert (candidate['memory_key'], candidate['content']) == (KEY, VALUE)
    with store.db() as db:
        persisted = [dict(row) for row in db.execute('SELECT * FROM memory_candidates')]
    shown = json.dumps([result, caps.evidence, candidate, persisted, worker_result('save_memory', result)])
    assert 'beverage' not in shown and 'retrieval_terms' not in shown
    assert store.held_notifications(store.MEMORY_SAVED_NOTICE_KIND) == []


def test_correction_terms_belong_only_to_the_new_row(state):
    store, _, _ = state
    old = saved(state, terms=TERMS)
    new = saved(state, content='oolong tea', correction=True, terms='우롱 oxidized')
    assert new['supersedes'] == old['id']
    assert raw_row(store, old['id'])['state'] == 'corrected'
    assert raw_row(store, old['id'])['retrieval_terms'] == TERMS
    assert raw_row(store, new['id'])['retrieval_terms'] == '우롱 oxidized'
    assert parse_meta(raw_row(store, new['id'])['retrieval_terms_meta'])['g'] == 0
    assert hits(store, 'beverage') == []
    assert hits(store, 'oxidized') == [new['id']]


def test_forget_and_undo_clear_terms_and_keep_the_raised_generation(state):
    store, _, _ = state
    result = saved(state, terms=TERMS)
    forget = OwnerForget(store, clock=lambda: NOW)
    receipt = forget.forget(MEMORY_OWNER, 'memory:' + result['id'])
    row = raw_row(store, result['id'])
    assert row['retrieval_terms'] is None
    assert (parse_meta(row['retrieval_terms_meta'])['status'], parse_meta(row['retrieval_terms_meta'])['g']) == ('cleared', 1)
    assert hits(store, 'beverage') == []
    forget.undo(MEMORY_OWNER, receipt['receipt'])
    restored = raw_row(store, result['id'])
    assert restored['state'] == 'current' and restored['retrieval_terms'] is None
    assert restored['retrieval_terms_meta'] == row['retrieval_terms_meta']
    assert eligible(restored['retrieval_terms_meta'])
    assert hits(store, 'beverage') == []
    assert hits(store, 'jasmine') == [result['id']]


def test_request_and_forgotten_value_refusals_are_unchanged(state):
    store, work, _ = state
    result = saved(state, content=store.job(work)['message'], terms=TERMS)
    assert result == {'saved': False, 'state': 'refused', 'memory_key': KEY,
                      'refused_because': 'value-is-the-request'}
    first = saved(state, terms=TERMS)
    OwnerForget(store, clock=lambda: NOW).forget(MEMORY_OWNER, 'memory:' + first['id'])
    result = saved(state, terms=TERMS)
    assert result == {'saved': False, 'state': 'refused', 'memory_key': KEY,
                      'refused_because': 'forgotten-value'}
    assert store.memories() == [] and store.memory_candidates() == []


def test_optional_schema_description_and_validation_agree_on_api_and_cli(state):
    caps = state[2]
    declared = next(d['function'] for d in DEFINITIONS if d['function']['name'] == 'save_memory')
    native = next(d['function'] for d in caps.definitions() if d['function']['name'] == 'save_memory')
    wire = next(d for d in AgentOSMcpTools(caps).definitions() if d['name'] == 'save_memory')
    assert SAVE_MEMORY_ARGUMENTS['terms'] == TERMS_DESCRIPTION
    assert declared['parameters']['properties']['terms'] == {'type': 'string', 'description': TERMS_DESCRIPTION}
    assert declared['parameters']['required'] == ['memory_key', 'content']
    assert wire['inputSchema'] == native['parameters']
    for profile in CLI_PROFILES:
        for tool in profile_mcp_tools(profile):
            if tool['name'] == 'save_memory':
                assert tool['inputSchema'] == native['parameters']
    for arguments in ({'memory_key': KEY, 'content': VALUE}, {'memory_key': KEY, 'content': VALUE, 'terms': TERMS}):
        assert check_arguments(native['parameters'], arguments) == arguments
        assert AgentOSMcpTools(caps)._admit('save_memory', arguments)[0] == arguments
    invalid = {'memory_key': KEY, 'content': VALUE, 'terms': ['beverage']}
    with pytest.raises(ValueError):
        check_arguments(native['parameters'], invalid)
    with pytest.raises(ValueError):
        AgentOSMcpTools(caps)._admit('save_memory', invalid)


def test_cli_facade_accepts_terms_and_uses_the_same_handler(state):
    store, work, caps = state
    store.put_turn_provenance(work, {'workers': [{'route': 'subscription', 'model': 'cli-fixture'}]})
    result = AgentOSMcpTools(caps).call('save_memory', {'memory_key': KEY, 'content': VALUE, 'terms': TERMS})
    row = raw_row(store, result['id'])
    assert row['retrieval_terms'] == TERMS
    assert parse_meta(row['retrieval_terms_meta'])['model'] == 'cli-fixture'
    assert hits(store, 'beverage') == [result['id']]


@pytest.fixture(params=['direct-api', 'cli-host'])
def recorded_save(state, monkeypatch, request):
    """Real dispatch and SQLite records; only worker/relay transports are fixtures."""
    store, work, caps = state
    caps.secret_redactor = lambda text: redact_known_secrets(store, text)

    def record(tool, status, detail):
        with store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work, tool, status, detail, NOW))

    caps.record = record

    def save(arguments):
        if request.param == 'direct-api':
            responses = iter([
                {'tool_calls': [{'id': 'save', 'type': 'function', 'function': {
                    'name': 'save_memory', 'arguments': json.dumps(arguments),
                }}]},
                {'content': 'Done.'},
            ])

            def transport(*args, **kwargs):
                return {'choices': [{'message': next(responses, {'content': 'Done.'})}]}

            run_agent(ModelAdapter(transport), CFG, '', [{'role': 'user', 'content': 'Remember my drink.'}],
                      '', caps, record)
        else:
            # Exercise stdio MCP framing, the bridge's durable records and the host facade,
            # with an in-process relay transport (no socket or CLI process).
            host = AgentOSMcpTools(caps)

            class Relay:
                def __init__(self, directory):
                    pass

                def call(self, name, args):
                    if name == SECRET_REDACTION:
                        return redact_known_secrets(store, args['text'])
                    return host.call(name, args)

            with store.db() as db:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
            requests = [
                {'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                 'params': {'name': 'save_memory', 'arguments': arguments}},
            ]
            output = io.StringIO()
            with monkeypatch.context() as patch:
                patch.setattr(mcp_bridge, 'RelayClient', Relay)
                patch.setattr(sys, 'stdin', io.StringIO(''.join(json.dumps(row) + '\n' for row in requests)))
                patch.setattr(sys, 'stdout', output)
                mcp_bridge.serve(str(store.root), work, browser_relay='fixture', relay_browser=False)
            reply = next(row for row in map(json.loads, output.getvalue().splitlines()) if row['id'] == 1)
            assert 'error' not in reply and not reply['result'].get('isError')

        with store.db() as db:
            events = [dict(row) for row in db.execute(
                'SELECT tool,status,detail FROM tool_events WHERE job_id=? ORDER BY id', (work,))]
        saves = [row for row in events if row['tool'] == 'save_memory']
        assert len(saves) == 2 and saves[0]['status'] == 'running'
        assert saves[1]['status'] in ('succeeded', 'failed')
        if request.param == 'direct-api':
            assert any(row['tool'] == 'model' and row['status'] == 'responded' for row in events)
        return events

    return save


@pytest.mark.parametrize('field', ['memory_key', 'content', 'terms'])
@pytest.mark.parametrize('secret', ['StoredSecret921Value', 'sk-proj-abcdefghijklmnopqrstuvwxyz123456'])
def test_save_secrets_never_reach_durable_call_records(state, recorded_save, field, secret):
    store, work, _ = state
    # Only the non-credential-shaped value is stored: shape redaction must work independently.
    if secret == 'StoredSecret921Value':
        store.secret('telegram_token', secret)
    events = recorded_save({'memory_key': KEY, 'content': VALUE, 'terms': TERMS,
                            field: 'before ' + secret + ' after'})
    for event in events:
        assert secret not in event['detail'], (event['tool'], event['status'])
    projected = trail_steps([(row['tool'], row['status'], row['detail']) for row in events])
    assert secret not in json.dumps(projected, ensure_ascii=False)
    # No display-time redactor: the durable records themselves must already be safe.
    audit = information_use.work_information_use(store, work)
    assert audit['results'] and secret not in json.dumps(audit, ensure_ascii=False)
    if field == 'terms':
        [row] = store.memories()
        assert row['content'] == VALUE and raw_row(store, row['id'])['retrieval_terms'] is None
    else:
        assert store.memories() == []


def test_an_ordinary_save_keeps_readable_recorded_arguments(state, recorded_save):
    arguments = {'memory_key': KEY, 'content': VALUE, 'terms': TERMS}
    events = recorded_save(arguments)
    assert state[0].memories()[0]['content'] == VALUE
    for event in events:
        detail = json.loads(event['detail'])
        if event['tool'] == 'model' and event['status'] == 'responded' and detail['tool_calls']:
            assert json.loads(detail['tool_calls'][0]['function']['arguments']) == arguments
        if 'arguments' in detail:
            assert detail['arguments'] == arguments
    assert recorded_arguments('save_memory', arguments, state[2].judgment_text) == arguments


@pytest.mark.parametrize('redactor', [None, 'failing'])
def test_save_recording_without_a_working_redactor_uses_length_placeholders(redactor):
    arguments = {'memory_key': KEY, 'content': VALUE, 'terms': TERMS, 'correction': True}

    def failing(text):
        raise ValueError('redaction unavailable')

    recorded = recorded_arguments('save_memory', arguments, failing if redactor else None)
    assert recorded == {**{field: f'[가림: {len(arguments[field])}자]'
                          for field in ('memory_key', 'content', 'terms')}, 'correction': True}
    assert arguments == {'memory_key': KEY, 'content': VALUE, 'terms': TERMS, 'correction': True}
