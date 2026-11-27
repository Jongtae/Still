"""Work-scoped plan updates through the host tool, encrypted store and check scheduler."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest

from personal_agent import agent_runtime as runtime, code_run, condition_checks as checks, preparations as prep
from personal_agent.api_credentials import MAX_STATE_BYTES, ApiCredentials
from personal_agent.api_requests import save_slot
from personal_agent.bounded_execution import AgentOSMcpTools
from personal_agent.providers import ModelAdapter
from test_agency_loop import CFG, Script, call, finish as complete, judgments
from test_code_run import FakeProcess
from test_condition_check_orders_1194 import ARGS_WITH_SET, POST, Transport, receipts, start_piece, switch
from test_condition_check_state_972 import StateProcess, keyed, run, saved  # noqa: F401 - fixture
from test_condition_checks_972 import (  # noqa: F401 - fixture
    HOST, SECRET, WIRE, case, current, finish, prepared, prove, record, schedule, wake_job,
)
from test_preparations import engine


ACTION = 'update_check_state'


def arguments(row, patch):
    return {'preparation_id': row['id'], 'check_digest': json.loads(row['check_spec'])['digest'],
            'patch': json.dumps(patch)}


def capabilities(c, work_id, *, adapter=None, record=lambda *args: None):
    return runtime.Capabilities(c.store, adapter, CFG, '', work_id, record, allowed_tools={ACTION},
                                check_state_update=lambda args: c.checks.update_state(work_id, args),
                                judgments=judgments(True))


def update(c, work_id, row, values, **overrides):
    return AgentOSMcpTools(capabilities(c, work_id)).call(ACTION, arguments(row, values) | overrides)


def line(c):
    return c.service.lines.open(c.owner, task='Observe the condition', report_promise='Report findings')['line_id']


def test_own_work_updates_next_check_and_preserves_bookkeeping(keyed):
    c = keyed
    row = prepared(c)
    run(c, '{"count":1}', stdout='Condition met')
    job = wake_job(c, row)
    args = arguments(row, {'plan': {'limit': 2}})
    assert args['check_digest'] in c.service.preparation_run_fact(job)
    assert update(c, job['id'], row, {'plan': {'limit': 2}})['keys'] == ['plan']
    finish(c, row)
    assert json.loads(run(c, '{"count":2,"plan":{"limit":2}}').seen) == {'count': 1, 'plan': {'limit': 2}}
    assert json.loads(run(c).seen) == {'count': 2, 'plan': {'limit': 2}}


def test_only_the_same_piece_tree_can_update_a_check(keyed):
    c = keyed
    line_id = line(c)
    row = prepared(c, line=line_id)
    run(c, stdout='Condition met')
    piece = start_piece(c, start_piece(c, row))
    work = wake_job(c, piece)
    fact = c.service.preparation_run_fact(work)
    assert row['id'] in fact and arguments(row, {})['check_digest'] in fact
    assert update(c, work['id'], row, {'plan': {'limit': 3}})['keys'] == ['plan']

    other = schedule(c, goal='Independent observation', line=line_id)
    assert c.preps.run_now(other['id'], channel='web', chat_id=None)
    for caller in (wake_job(c, other)['id'], c.owner):
        with pytest.raises(runtime.ToolError) as exc:
            update(c, caller, row, {'plan': 'out of scope'})
        assert exc.value.code == 'check_outside_preparation_tree'
    with pytest.raises(runtime.ToolError) as exc:
        update(c, work['id'], row, {}, preparation_id=piece['id'])
    assert exc.value.code == 'check_inactive'
    assert json.loads(saved(c, row)) == {'plan': {'limit': 3}}


@pytest.mark.parametrize('end', ['cancel', 'replace'])
def test_ended_and_replaced_rows_refuse_writes(keyed, end):
    c = keyed
    row = prepared(c, line=line(c))
    run(c, '{"count":1}', stdout='Condition met')
    piece = start_piece(c, row)
    if end == 'cancel':
        c.preps.cancel(row['id'])
    else:
        replacement = schedule(c, goal='Revised observation', replaces=row['id'])
        assert replacement['id'] != row['id']
        assert not c.checks.state_targets(piece['id'])
        with pytest.raises(runtime.ToolError) as exc:
            update(c, wake_job(c, piece)['id'], replacement, {'plan': 1})
        assert exc.value.code == 'check_outside_preparation_tree'
    with pytest.raises(runtime.ToolError) as exc:
        update(c, wake_job(c, piece)['id'], row, {'plan': 1})
    assert exc.value.code == 'check_inactive'
    assert json.loads(saved(c, row)) == {'count': 1}


def test_cancellation_during_sealing_fences_the_write(keyed, monkeypatch):
    c = keyed
    row = prepared(c)
    run(c, '{"count":1}', stdout='Condition met')
    original_seal = ApiCredentials.seal_check_state

    def cancel_after_seal(credentials, *args):
        sealed = original_seal(credentials, *args)
        c.preps.cancel(row['id'])
        return sealed

    monkeypatch.setattr(ApiCredentials, 'seal_check_state', cancel_after_seal)
    with pytest.raises(runtime.ToolError) as exc:
        update(c, wake_job(c, row)['id'], row, {'plan': 1})
    assert exc.value.code == 'check_inactive'
    assert json.loads(saved(c, row)) == {'count': 1}


def test_previous_digest_refused_after_in_place_check_replacement(keyed):
    c = keyed
    row = prepared(c)
    run(c, '{"count":1}', stdout='Condition met')
    job = wake_job(c, row)
    spec = json.loads(row['check_spec'])
    args = {**spec['arguments'], 'code': 'print(2)'}
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
    runner = code_run.CodeRunner(c.store, job['id'], popen=lambda *a, **k: FakeProcess(), **WIRE)
    record(c, 'code_run', runner.run(args), work=job['id'])
    c.preps.change_own(row['id'], job['id'], 'check', {'check': {**args, 'every_seconds': 15}})
    updated = current(c, row)
    assert arguments(updated, {})['check_digest'] != spec['digest']
    with pytest.raises(runtime.ToolError) as exc:
        update(c, job['id'], row, {'plan': 1})
    assert exc.value.code == 'check_digest_mismatch'
    assert not saved(c, updated)
    update(c, job['id'], updated, {'plan': 2})
    assert json.loads(saved(c, updated)) == {'plan': 2}


def test_top_level_replacement_and_null_removal(keyed):
    c = keyed
    row = prepared(c)
    run(c, '{"plan":{"keep":1},"obsolete":true,"count":4}', stdout='Condition met')
    work_id = wake_job(c, row)['id']
    update(c, work_id, row, {'obsolete': None, 'plan': {'new': None}})
    assert json.loads(saved(c, row)) == {'plan': {'new': None}, 'count': 4}


@pytest.mark.parametrize('patch', [
    '[1,2]', 'null', 'malformed', '{"value":NaN}', json.dumps({'big': 'x' * MAX_STATE_BYTES}),
    json.dumps({'also_big': 'y' * (MAX_STATE_BYTES // 2)}),
], ids=['list', 'null', 'malformed', 'nan', 'large-patch', 'large-result'])
def test_invalid_patch_or_result_is_refused_atomically(keyed, patch):
    c = keyed
    row = prepared(c)
    original = json.dumps({'existing': 'x' * (MAX_STATE_BYTES // 2)})
    run(c, original, stdout='Condition met')
    with pytest.raises(runtime.ToolError) as exc:
        update(c, wake_job(c, row)['id'], row, {}, patch=patch)
    assert exc.value.code == 'invalid_check_state'
    assert saved(c, row) == original


def test_inflight_check_rebases_changes_onto_the_new_plan(keyed):
    c = keyed
    row = prepared(c, line=line(c))
    baseline = {'plan': {'limit': 1}, 'drop_by_work': 1, 'drop_by_check': 2, 'count': 1}
    run(c, json.dumps(baseline), stdout='Condition met')
    piece = start_piece(c, row)
    loaded, release = threading.Event(), threading.Event()

    class BlockedCheck(StateProcess):
        def _lines(self):
            for line in super()._lines():
                loaded.set()
                assert release.wait(10)
                yield line

    process = BlockedCheck(json.dumps({'plan': {'limit': 99}, 'drop_by_work': 1, 'count': 2, 'check_null': None}))
    c.processes.append(process)
    with ThreadPoolExecutor() as pool:
        settled = pool.submit(c.checks.run_due)
        try:
            assert loaded.wait(5)
            assert json.loads(process.seen) == baseline
            write = pool.submit(update, c, wake_job(c, piece)['id'], row,
                                {'plan': {'limit': 2}, 'drop_by_work': None})
            assert write.result(timeout=5)['keys'] == ['drop_by_work', 'plan']
        finally:
            release.set()
        assert settled.result(timeout=5)
    assert json.loads(saved(c, row)) == {'plan': {'limit': 2}, 'count': 2, 'check_null': None}


@pytest.mark.parametrize('owner_accepted', [False, True])
def test_check_dispatch_uses_plan_and_existing_authority(keyed, owner_accepted):
    c = keyed
    save_slot(c.store, 'fixture', [HOST], {'TOKEN': SECRET}, encrypted=True, money=True,
              code={'fields': ['TOKEN']})
    c.service.use_decision_engine(engine(preparation=owner_accepted))
    args = {**ARGS_WITH_SET, 'code': f'''import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

if state := os.environ.get("CHECKSTATEFILE"):
    plan = json.loads(Path(state).read_text()).get("plan")
    if plan:
        urlopen(Request("https://{HOST}{POST['path']}", json.dumps(plan).encode(), method="POST"))
'''}
    prove(c, args)
    row = schedule(c, args)
    assert row['accepted_by'] in (prep.OWNER_ACCEPTED if owner_accepted else prep.SELF_SET)
    switch(c, True)
    run(c, stdout='Condition met')
    plan = {'order_id': 'next-effect'}
    update(c, wake_job(c, row)['id'], row, {'plan': plan})
    after = current(c, row)
    assert (after['accepted_by'], after['check_spec']) == (row['accepted_by'], row['check_spec'])
    finish(c, row)
    sent = []

    class PlanTransport(Transport):
        def _lines(self):
            state = json.loads(Path(self.config['env'][checks.STATE_ENV]).read_text())
            self.request = dict(POST, bodyText=json.dumps(state['plan']))
            self._requests = [self.request]
            yield from super()._lines()

    process = PlanTransport(sent)
    c.processes.append(process)
    assert c.checks.run_due()
    decision, = process.decisions
    assert decision['allow'] is owner_accepted
    assert len(receipts(c, row, 'payment_autonomy')) == int(owner_accepted)
    assert len(receipts(c, row)) == int(owner_accepted)
    assert [json.loads(request['bodyText']) for request in sent] == ([plan] if owner_accepted else [])


def test_tool_result_and_work_evidence_keep_only_redacted_keys_and_size(keyed):
    c = keyed
    row = prepared(c)
    run(c, stdout='Condition met')
    work_id = wake_job(c, row)['id']
    c.store.secret('telegram_token', SECRET)
    fresh = 'fresh-opaque-state-value-0001'
    alternate = 'value-only-used-by-the-check'
    patch = {'plan': {'payload': fresh, 'secondary': alternate}, SECRET: 1, fresh: 2}
    args = arguments(row, patch)
    tool = call('update', ACTION, **args, status=f'Updating {alternate}')
    script = Script({'tool_calls': [tool]}, complete('done', 'update', summary='Updated.'))

    def record(name, status, detail):
        with c.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work_id, name, status, detail, c.clock[0]))

    caps = capabilities(c, work_id, adapter=ModelAdapter(script), record=record)
    runtime.run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': 'Update the check'}], '', caps, record)
    results = [json.loads(message['content']) for message in script.bodies[1]['messages'] if message['role'] == 'tool']
    result, = results
    # #1265: a stored secret used as a key stays redacted; the values the Work wrote are its own plan.
    assert result['keys'].count('[redacted]') == 1 and 'plan' in result['keys'] and fresh in result['keys']
    assert result['bytes'] == len(saved(c, row).encode())
    assert json.loads(saved(c, row)) == patch

    events = c.store.task_events(work_id)
    event, = [event for event in events if event['tool'] == ACTION and event['status'] == 'succeeded']
    assert event['trace']['evidence'] == {'keys': result['keys'], 'bytes': result['bytes']}
    assert SECRET not in json.dumps(results)
    assert SECRET not in json.dumps(events)
