"""#744: recalling an old assistant answer retains its source Work's host mark."""
import json
import time
from types import SimpleNamespace

import pytest

from personal_agent.agent_runtime import Capabilities, ToolError, history_provenance
from personal_agent.bounded_execution import AgentOSMcpTools
from personal_agent.memory_service import conversation_refs
from personal_agent.quickstart_store import QuickStore
from personal_agent.api_requests import save_slot
from test_744_effects import host, READ, WRITE, injected_api


QUERY = 'archivetopic'
VALUE = 'cobalt tea'
ANSWER = f'{QUERY}: the owner prefers {VALUE}.'


def archived_exchange(store, service, marked):
    old = store.enqueue('Review the archivetopic discussion.', 'old-recall',
                        owner_typed=True, owner_words=True)
    if marked:
        # Host-mediated read in a web Work, not a model-supplied provenance flag.
        save_slot(store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
        caps = Capabilities(store, None, {}, '', old, lambda *args: None)
        caps.api().transport = lambda *args: (200, {}, b'{"text":"outside content"}')
        caps.execute('api_request', READ)
    else:
        # Generic private labels can also be write receipts; never promote them.
        service.record_work_sources(old, ['owner-conversation', 'personal-space', 'connected-document'])
    with store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded',response=? WHERE id=?", (ANSWER, old))
        db.executemany('INSERT INTO messages(job_id,role,content,channel,created) VALUES (?,?,?,?,?)',
                       [(old, 'user', 'Review the archivetopic discussion.', 'web', 1),
                        (old, 'assistant', ANSWER, 'web', 2)])
        db.executemany('INSERT INTO messages(role,content,channel,created) VALUES (?,?,?,?)',
                       [('user', f'unrelated turn {i}', 'web', 3 + i) for i in range(20)])
    return old


def setup_recall(host, marked=True):
    store, service, _ = host
    old = archived_exchange(store, service, marked)
    current = store.enqueue('Recall the earlier discussion.', 'recall-current',
                            owner_typed=True, owner_words=True)
    history = store.history()[-16:]
    assert all(row.get('job_id') != old for row in history)
    caps = Capabilities(store, None, {}, '', current, lambda *args: None,
                        inherited_provenance=history_provenance(store, history),
                        browser_approvals=service.browser_approvals_for(store.job(current)))
    assert store.outside_content_sources(current) == []
    assert not store.owner_stated_value(current, VALUE)
    return store, service, old, current, caps


def recall(store, service, current, caps, route):
    if route == 'tool':
        return caps.execute('search_conversation', {'query': QUERY})
    if route == 'cli':
        return AgentOSMcpTools(caps).call('search_conversation', {'query': QUERY})
    if route == 'automatic':
        with service.work_decision_scope(current, None):
            return service.owner_words_block(QUERY)
    if route == 'prompt':
        rows = store.search_conversation(QUERY)
        refs = conversation_refs(rows)
        service.record_turn_sent(current, sent=json.dumps(rows), instructions='', instructions_channel='prompt',
                                 owner_information={'earlier_exchanges': refs})
        return rows
    return caps.execute('forget_record', {'action': 'find', 'query': QUERY})


@pytest.mark.parametrize('marked', [True, False], ids=['marked', 'owner-only'])
@pytest.mark.parametrize('route', ['tool', 'cli', 'automatic', 'prompt', 'forget-find'])
def test_old_exchange_intake_inherits_only_explicit_host_marks(host, marked, route):
    store, service, old, current, caps = setup_recall(host, marked)
    result = recall(store, service, current, caps, route)
    assert VALUE in json.dumps(result)
    sources = QuickStore(store.root).outside_content_sources(current)
    if marked:
        assert sources
        assert {source['source_work_id'] for source in sources} == {old}
        assert {source['category'] for source in sources} == {'owner-api'}
        if route in ('tool', 'cli'):
            assert {source['operation'] for source in sources} == {'search_conversation'}
    else:
        assert sources == []
    assert service._browser_request(current) is None, 'recall itself never asks'


@pytest.mark.parametrize('marked', [True, False], ids=['marked', 'owner-only'])
def test_recalled_answer_memory_uses_existing_candidate_boundary(host, marked):
    store, service, old, current, caps = setup_recall(host, marked)
    recall(store, service, current, caps, 'tool')
    saved = caps.execute('save_memory', {'memory_key': 'drink', 'content': VALUE})
    assert saved['state'] == ('pending' if marked else 'current')
    if marked:
        assert store.memories() == []
        candidate = store.memory_candidate(saved['id'])
        assert candidate['source'] == store.outside_content_sources(current)
        assert candidate['source'][0]['source_work_id'] == old
    else:
        assert [row['content'] for row in store.memories()] == [VALUE]
        assert store.memory_candidates() == []


@pytest.mark.parametrize('marked', [True, False], ids=['marked', 'owner-only'])
def test_recalled_answer_cannot_authorize_an_api_write(host, marked):
    store, service, old, current, caps = setup_recall(host, marked)
    recall(store, service, current, caps, 'tool')
    save_slot(store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
    sent = []
    caps.api().transport = lambda *args: injected_api(*args, sent=sent)
    if marked:
        with pytest.raises(ToolError) as held:
            caps.execute('api_request', WRITE)
        assert held.value.code == 'approval_required'
        assert sent == []
        offered = service._browser_request(current)
        assert 'search_conversation' in offered['label'] and old in offered['label']
        assert WRITE['url'] in offered['label']
    else:
        caps.execute('api_request', WRITE)
        assert [row[0] for row in sent] == ['POST']
        assert service._browser_request(current) is None


@pytest.mark.parametrize('route', ['tool', 'cli', 'automatic', 'prompt', 'forget-find'])
def test_recall_mark_persistence_failure_withholds_content(host, monkeypatch, route):
    store, service, _, current, caps = setup_recall(host)
    def unavailable(*args, **kwargs):
        raise RuntimeError('mark storage unavailable')
    monkeypatch.setattr(store, 'mark_outside_content', unavailable)
    with pytest.raises(RuntimeError, match='mark storage unavailable'):
        recall(store, service, current, caps, route)
    assert not caps.evidence, 'failed intake must not leave recalled text for a specialist'
    assert not (store.turn_provenance(current) or {}).get('prompt_envelope')


def test_exhausted_or_empty_recall_does_not_inherit_unreturned_work(host):
    store, service, _, current, caps = setup_recall(host)
    assert caps.execute('search_conversation', {'query': 'no_matching_term'})['exchanges'] == []
    caps._conversation_refs.extend({'message_ids': [n]} for n in range(3))
    result = caps.execute('search_conversation', {'query': QUERY})
    assert result['budget_exhausted'] and result['exchanges'] == []
    assert store.outside_content_sources(current) == []


def test_clipped_automatic_recall_does_not_inherit_unreturned_work(host):
    store, service, _, current, _ = setup_recall(host)
    assert service.conversation_block_for_work(current, store.search_conversation(QUERY), budget=1) == ''
    assert store.outside_content_sources(current) == []


@pytest.mark.parametrize('route', ['goal', 'budget-continue'])
@pytest.mark.parametrize('storage_fails', [False, True], ids=['committed', 'unavailable'])
def test_legacy_tool_recall_marks_before_goal_or_continuation_intake(host, monkeypatch, route, storage_fails):
    from personal_agent.orchestrator import REACHED
    from test_budget_continue import BudgetContinueTests
    case = BudgetContinueTests()
    try:
        if route == 'budget-continue':
            service, store, current, run, _ = case.stopped()
            old = archived_exchange(store, service, True)
        else:
            store, service, old, current, _ = setup_recall(host)
        # Pre-fix receipt: the host recorded the exact recalled ids, without
        # marking this Work. Reopening that receipt must carry today's mark.
        refs = conversation_refs(store.search_conversation(QUERY))
        assert [ref['work_id'] for ref in refs] == [old]
        with store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (current, 'search_conversation', 'succeeded',
                        json.dumps({'host_action': 'search_conversation', 'evidence': {'exchange_refs': refs}}), time.time()))
        assert not store.outside_content_sources(current)
        seen = []
        def judged(answer, observations, failures, **kwargs):
            assert VALUE in observations
            assert store.outside_content_sources(current)[0]['source_work_id'] == old
            seen.append(observations)
            return REACHED
        orchestration = SimpleNamespace(orchestrated=True, _redact=lambda text: text,
            budget_allows=lambda **kwargs: True, evaluate_answer=judged, next=lambda *args, **kwargs: None)
        def intake():
            if route == 'goal':
                return service.orchestration_step(orchestration, object(), current, 0, answer='Recalled.')
            assert service.offer_budget_continue(run)
            assert VALUE in service.budget_continue_offer(current)['progress']
        if storage_fails:
            def unavailable(*args, **kwargs):
                raise RuntimeError('mark storage unavailable')
            monkeypatch.setattr(store, 'mark_outside_content', unavailable)
            with pytest.raises(RuntimeError, match='mark storage unavailable'):
                intake()
            assert not seen
            assert service.budget_continue_offer(current).get('state') != 'offered'
        else:
            intake()
            assert store.outside_content_sources(current)[0]['source_work_id'] == old
            assert bool(seen) == (route == 'goal')
    finally:
        case.doCleanups()
