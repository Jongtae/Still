"""Model-free dispatch and polling checks for the shared loop guard."""
import json
from collections import Counter
from itertools import count

import pytest

from personal_agent import agent_runtime
from personal_agent.agent_runtime import Capabilities, ToolError, run_agent
from personal_agent.api_requests import ApiRequests, save_slot
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore
from test_adaptive_continuation_1002 import api_steps, ask, result
from test_agency_loop import CFG, Script, call
from test_effect_continuation_1000 import HOST, OPEN, READ, setup
from test_peer_cli_relay import peer_turn, state


@pytest.mark.parametrize('first_code', [None, 'effect_unknown', 'api_unauthorized'])
def test_dispatched_mutation_cannot_repeat(setup, monkeypatch, first_code):
    store, service, wire, script = setup
    work = store.enqueue('Apply one change.', 'fixture-work', owner_typed=True)
    if first_code == 'effect_unknown':
        wire.lost = True
    elif first_code == 'api_unauthorized':
        def rejected(method, url, headers, body, timeout):
            wire.sent.append('open')
            return 403, {}, b'{}'
        monkeypatch.setattr('personal_agent.api_requests.http_request', rejected)

    script.messages.extend([ask('first', OPEN), ask('repeat', OPEN), {'content': 'Finished.'}])
    assert service.run_one()

    steps = api_steps(service, work)
    first, repeat = steps['first'], steps['repeat']
    assert first[0] == ('failed' if first_code else 'succeeded')
    assert first[1].get('code') == first_code
    assert first[1].get('ran') is not False
    assert (repeat[1]['code'], repeat[1]['ran']) == ('duplicate_call', False)
    assert wire.sent == ['open']


def test_refusal_before_dispatch_leaves_attempt_and_read_scope_available(setup, monkeypatch):
    store, service, wire, script = setup
    work = store.enqueue('Apply one change.', 'fixture-work', owner_typed=True)
    original = agent_runtime.effect_mutation_refusal
    checks = 0

    def refuse_first_reservation(*args, **kwargs):
        nonlocal checks
        if kwargs.get('dispatches') is not None:
            checks += 1
            if checks == 2:  # The preflight passed. Refuse inside the reservation.
                return 'effect_unknown'
        return original(*args, **kwargs)

    monkeypatch.setattr(agent_runtime, 'effect_mutation_refusal', refuse_first_reservation)
    script.messages.extend([
        {'tool_calls': [call('notes', 'list_notes')]}, ask('blocked', OPEN),
        {'tool_calls': [call('same-notes', 'list_notes')]}, ask('sent', OPEN),
        {'tool_calls': [call('after-write', 'list_notes')]}, ask('repeat', OPEN), {'content': 'Finished.'},
    ])
    assert service.run_one()

    steps = api_steps(service, work)
    blocked = steps['blocked'][1]
    assert (blocked['code'], blocked['ran'], blocked['effect'], blocked['attempt']) == ('effect_unknown', False, 'none', 1)
    assert steps['sent'][0] == 'succeeded' and steps['sent'][1]['attempt'] == 1
    assert steps['repeat'][1]['code'] == 'duplicate_call'
    assert result(script.bodies[-1], 'same-notes')['code'] == 'duplicate_call'
    assert result(script.bodies[-1], 'after-write')['ref'] == 'after-write'
    assert wire.sent == ['open']


def test_peer_relay_releases_a_refused_mutation_but_keeps_a_dispatched_one(state, monkeypatch):
    attempts = []

    def calls(handle, tools):
        execute = tools.capabilities.execute

        def refuse_once(name, args):
            attempts.append(name)
            if len(attempts) == 1:
                raise ToolError('Held before dispatch', 'effect_unknown', ran=False)
            return execute(name, args)

        params = {'name': 'save_memory', 'arguments': {'memory_key': 'peer.fact', 'content': 'Third party fact'}}
        with monkeypatch.context() as patched:
            patched.setattr(tools.capabilities, 'execute', refuse_once)
            refused, applied, duplicate = [handle('tools/call', params) for _ in range(3)]
        assert (refused['structuredContent']['code'], refused['structuredContent']['ran']) == ('effect_unknown', False)
        assert not applied.get('isError')
        assert duplicate['structuredContent']['code'] == 'duplicate_call'
        return [refused, applied, duplicate]

    seen = peer_turn(state, callback=calls)
    assert seen['turn']['state'] == 'completed'
    assert attempts == ['save_memory', 'save_memory']


@pytest.mark.parametrize('states', [(1, 1), (1, 2, 2)], ids=['unchanged', 'changed'])
def test_polling_stops_after_same_observation_even_with_read_overlap(tmp_path, states):
    store = QuickStore(tmp_path)
    save_slot(store, 'fixture', [HOST], 'fixture-transport-key')
    polls, sent, clock = iter(states), [], count(100)
    read = {key: value for key, value in READ.items() if key != 'as_of_field'}
    other = {**read, 'url': f'https://{HOST}/other'}

    def transport(method, url, headers, body, timeout):
        sent.append(url)
        return 200, {}, json.dumps({'state': next(polls) if url == read['url'] else 0}).encode()

    script = Script(ask('first', read), {'tool_calls': [call('poll', 'api_request', **read),
                                                       call('other', 'api_request', **other),
                                                       call('third', 'api_request', **read),
                                                       call('fourth', 'api_request', **read)]},
                    {'content': 'Finished.'})
    caps = Capabilities(store, ModelAdapter(script), CFG, '', 'job', lambda *_: None)
    caps._api = ApiRequests(store, 'job', transport=transport, clock=lambda: next(clock))
    run_agent(caps.adapter, CFG, '', [], '', caps, caps.record)

    final = script.bodies[-1]
    first, poll = result(final, 'first'), result(final, 'poll')
    assert first['data']['state'] == states[0] and poll['data']['state'] == states[1]
    assert first['provenance']['retrieved_at'] != poll['provenance']['retrieved_at']
    assert result(final, 'other')['data']['state'] == 0
    if len(states) == 3:
        assert result(final, 'third')['data']['state'] == states[2]
    else:
        assert result(final, 'third')['code'] == 'duplicate_call'
    fourth = result(final, 'fourth')
    assert (fourth['code'], fourth['ran']) == ('duplicate_call', False)
    assert Counter(sent) == {read['url']: len(states), other['url']: 1}
