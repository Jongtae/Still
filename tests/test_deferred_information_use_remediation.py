"""#820 B2 verifier regressions: no live operation."""
import sqlite3
from unittest import mock

import pytest
from personal_agent import agent_runtime as runtime, information_use as audit
from personal_agent.owner_forget import OwnerForget
from test_deferred_information_use import store, memory, event, call, view, MEMBER, PHONE


def marked(store, job, **kwargs):
    evidence = {'outbound_comparison': 'deferred', **kwargs.pop('evidence', {})}
    call(store, job, evidence=evidence, **kwargs)


def test_legacy_forgotten_predecessor_not_kept_alive_by_retracted_sibling(store):
    a = memory(store, 'member', MEMBER, 10)
    b = memory(store, 'member', PHONE, 12)
    store.retract_memory('local-owner', b['id'], b['content_digest'], now=13)
    job = store.enqueue('find', 'test')
    call(store, job, evidence={'outbound_comparison': None, 'outbound_matches': [{'source': 'memory', 'key': 'member', 'destination': 'old'}]})
    OwnerForget(store, clock=lambda: 26).forget('local-owner', 'memory:' + a['id'])
    assert view(store, job)['held_value_matches'] == []


def test_deleted_middle_memory_does_not_rewrite_call_time_comparison(store):
    memory(store, 'member', MEMBER, 10)
    middle = memory(store, 'member', PHONE, 15)
    memory(store, 'member', 'Q9999-0000', 30)
    job = store.enqueue('find', 'test')
    marked(store, job)
    assert view(store, job)['held_value_matches'] == []
    store.delete_personal_space_item('memories', middle['id'])
    assert view(store, job)['held_value_matches'] == []


def test_legacy_empty_comparison_is_never_recomputed(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    event(store, job, 'web_search', 'running', {'call_id': 'old', 'arguments': {'query': MEMBER}}, 20)
    event(store, job, 'web_search', 'succeeded', {'call_id': 'old', 'evidence': {'provider': 'brave'}}, 21)
    with mock.patch.object(runtime, 'held_value_matches', side_effect=AssertionError('legacy compared')):
        result = view(store, job)
    assert result['held_value_matches'] == []
    assert 'held_value_comparison_time' not in result


@pytest.mark.parametrize('target,error', [('comparison_work_writes', sqlite3.OperationalError('locked')),
                                         ('matcher', RuntimeError('broken')),
                                         ('comparison_memories', TypeError('broken store reader'))])
def test_one_comparison_failure_does_not_break_audit(store, target, error):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    marked(store, job)
    patch = mock.patch.object(runtime, 'held_value_matches', side_effect=error) if target == 'matcher' else mock.patch.object(store, target, side_effect=error)
    with patch:
        result = audit.work_information_use(store, job)
    assert result['usage']['totals'] is not None
    assert result['results'] and result['sent_to']['lookups']
    assert {'kind': 'comparison_failed', 'limit': 0} in result['sent_to']['held_value_comparison_cut']


def test_usage_only_audit_skips_memory_and_matcher(store):
    job = store.enqueue('find', 'test')
    marked(store, job)
    with mock.patch.object(store, 'comparison_memories', side_effect=AssertionError('Memory read')), mock.patch.object(runtime, 'held_value_matches', side_effect=AssertionError('matcher')):
        result = audit.work_information_use(store, job, compare_held_values=False)
    assert result['usage']['totals'] is not None
    assert 'held_value_comparison_time' not in result['sent_to']


def test_usage_only_budget_offer_passes_skip_flag():
    from test_budget_continue import BudgetContinueTests
    fixture = BudgetContinueTests()
    try:
        service, store, job, run, _budget = fixture.stopped()
        marked(store, job, at=run.claimed_at + 0.001)
        with mock.patch.object(service, 'work_information_use', wraps=service.work_information_use) as audit_call, mock.patch.object(store, 'comparison_memories', side_effect=AssertionError('usage read Memory')):
            assert service.offer_budget_continue(run)
        audit_call.assert_called_with(job, compare_held_values=False)
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize('calls,values', [(1, 10), (5, 1)])
def test_deadline_stops_matching_and_preserves_rest_of_audit(store, calls, values):
    for i in range(values):
        memory(store, 'member-' + str(i), MEMBER, 10)
    job = store.enqueue('find', 'test')
    for _ in range(calls): marked(store, job)
    now = [0.0]
    original = runtime.held_value_matches
    def slow(texts, held):
        now[0] += 0.6
        return original(texts, held)
    with mock.patch.object(runtime, 'held_value_matches', side_effect=slow) as matcher:
        result = audit.work_information_use(store, job, clock=lambda: now[0])
    assert matcher.call_count <= 3
    assert {'kind': 'comparison_time', 'limit': 1500} in result['sent_to']['held_value_comparison_cut']
    assert result['results'] and result['sent_to']['lookups']


@pytest.mark.parametrize('mutation', ['memory', 'forget', 'delete', 'note', 'event'])
def test_label_cache_hits_and_invalidates_on_owner_or_work_changes(store, mutation):
    a = memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    marked(store, job)
    with mock.patch.object(runtime, 'held_value_matches', wraps=runtime.held_value_matches) as matcher:
        first = view(store, job)
        calls = matcher.call_count
        assert calls
        assert view(store, job) == first
        assert matcher.call_count == calls
        if mutation == 'memory': memory(store, 'other', MEMBER, 11)
        elif mutation == 'forget': OwnerForget(store, clock=lambda: 26).forget('local-owner', 'memory:' + a['id'])
        elif mutation == 'delete': store.delete_personal_space_item('memories', a['id'])
        elif mutation == 'event': marked(store, job, args={'query': MEMBER + ' again'})
        else:
            with store.db() as db: db.execute('INSERT INTO notes VALUES (?,?,?)', (job, PHONE, 25))
        after = view(store, job)
        if mutation in ('forget', 'delete'): assert after['held_value_matches'] == []
        else: assert matcher.call_count > calls
    # Process cache retains labels/cuts only, never either private value.
    assert MEMBER not in repr(audit._COMPARISON_CACHE[store].results)
    assert PHONE not in repr(audit._COMPARISON_CACHE[store].results)


def test_exact_match_limit_is_not_a_cut_and_overflow_is(store):
    job = store.enqueue('find', 'test')
    for i in range(runtime.OUTBOUND_MATCHES_MAX): memory(store, 'member-' + str(i), MEMBER, 10)
    def compare(): return audit.deferred_comparison(store, job, 'web_search', {'query': MEMBER}, {}, 20)
    found, cuts = compare()
    assert len(found) == runtime.OUTBOUND_MATCHES_MAX
    assert 'matches' not in {r['kind'] for r in cuts}
    memory(store, 'extra', MEMBER, 10)
    found, cuts = compare()
    assert len(found) == runtime.OUTBOUND_MATCHES_MAX
    assert 'matches' in {r['kind'] for r in cuts}


def test_memory_and_work_writes_share_one_value_bound(store):
    job = store.enqueue('find', 'test')
    for i in range(10): memory(store, 'member-' + str(i), MEMBER, 10)
    with mock.patch.object(store, 'comparison_work_writes', return_value=['Q9999-' + str(i) for i in range(1000)]) as writes, mock.patch.object(runtime, 'held_value_matches', return_value=[]) as matcher:
        audit.deferred_comparison(store, job, 'web_search', {'query': MEMBER}, {}, 20)
    assert sum(len(c.args[1]) for c in matcher.call_args_list) <= runtime.HELD_MEMORY_MAX
    assert writes.call_args.args[-1] == runtime.HELD_MEMORY_MAX - 10 + 1


def test_running_terminal_pair_includes_attempt(store):
    memory(store, 'member', MEMBER, 10)
    memory(store, 'phone', PHONE, 10)
    job = store.enqueue('find', 'test')
    for attempt, text in [(1, MEMBER), (2, PHONE)]:
        event(store, job, 'web_search', 'running', {'call_id': 'reused', 'attempt': attempt, 'arguments': {'query': text}}, 20)
    for attempt in (1, 2):
        event(store, job, 'web_search', 'succeeded', {'call_id': 'reused', 'attempt': attempt, 'evidence': {'outbound_comparison': 'deferred'}}, 21)
    assert {r['key'] for r in view(store, job)['held_value_matches']} == {'member', 'phone'}


def test_deletion_uncertainty_labels_a_future_current_match_as_later(store):
    memory(store, 'member', MEMBER, 10)
    middle = memory(store, 'member', PHONE, 15)
    memory(store, 'member', 'Q9999-0000', 30)
    job = store.enqueue('find', 'test')
    marked(store, job, args={'query': 'Q9999-0000'})
    assert view(store, job)['held_value_matches'] == []
    store.delete_personal_space_item('memories', middle['id'])
    assert view(store, job)['held_value_matches'][0]['comparison'] == 'later_memory'


def test_long_held_value_has_an_explicit_prefix_cut(store):
    memory(store, 'member', 'x' * (audit.COMPARISON_VALUE_MAX + 10) + MEMBER, 10)
    job = store.enqueue('find', 'test')
    marked(store, job)
    result = view(store, job)
    assert result['held_value_matches'] == []
    assert {'kind': 'value_chars', 'limit': audit.COMPARISON_VALUE_MAX} in result['held_value_comparison_cut']


def test_process_label_cache_has_a_bounded_work_window(store):
    memory(store, 'member', MEMBER, 10)
    jobs = []
    for i in range(audit.COMPARISON_CACHE_WORKS + 1):
        job = store.enqueue('find ' + str(i), 'cache-' + str(i))
        marked(store, job)
        jobs.append(job)
    for job in jobs: view(store, job)
    cached = audit._COMPARISON_CACHE[store].results
    assert len(cached) == audit.COMPARISON_CACHE_WORKS
    assert all(key[0] != jobs[0] for key in cached)
