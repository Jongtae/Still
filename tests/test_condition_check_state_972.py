"""#972 D4: a check's own state between its runs (``CHECKSTATEFILE``).

Real SQLite, scheduler and secret store; injected sandbox processes and a fake clock.
"""
import json
from pathlib import Path

import pytest

from personal_agent import condition_checks as checks, preparations as prep
from personal_agent.api_credentials import CHECK_STATE_PREFIX, MAX_STATE_BYTES, ApiCredentials
from personal_agent.api_requests import save_slot
from personal_agent.browser_jar import MemoryKey
from personal_agent.current_context import redact_known_secrets
from test_code_run import FakeProcess
from test_condition_checks_972 import ARGS, HOST, SECRET, case, current, finish, prepared, prove, schedule, status, wake_job  # noqa: F401


class StateProcess(FakeProcess):
    """Sees the state the host gave the run, then rewrites it as the check code would."""

    def __init__(self, write=None, **kwargs):
        super().__init__(**kwargs)
        self.write_state, self.seen = write, None

    def _lines(self):
        name = self.config['env'].get(checks.STATE_ENV)
        if name:
            path = Path(name)
            self.seen = path.read_text()
            if self.write_state is not None:
                path.write_text(self.write_state)
        yield from super()._lines()


@pytest.fixture
def keyed(case):  # noqa: F811
    case.store.api_slot_key = MemoryKey()
    return case


def run(c, write=None, stdout=''):
    process = StateProcess(write, result={'stdout': stdout})
    c.processes.append(process)
    assert c.checks.run_due()
    c.clock[0] += 15
    return process


def saved(c, row):
    return ApiCredentials(c.store).read_check_state(row['id'], json.loads(current(c, row)['check_spec'])['digest'])


def test_state_carries_between_runs_so_the_check_code_holds_a_level(keyed):
    c = keyed
    row = prepared(c)
    first = run(c, json.dumps({'below_since': 100}))
    assert first.seen == '{}'
    assert status(c, row)['last_result'] == 'quiet'
    second = run(c, json.dumps({'below_since': 100, 'reported': True}), stdout='held 180 s')
    assert json.loads(second.seen) == {'below_since': 100}
    assert wake_job(c, row)
    assert json.loads(saved(c, row)) == {'below_since': 100, 'reported': True}
    # The state is kept encrypted, never as plain text in the secret file.
    assert 'below_since' not in c.store.secret_path.read_text()


def test_proving_run_has_no_check_state(keyed):
    c = keyed
    process = FakeProcess()
    prove(c, process=process)
    assert checks.STATE_ENV not in process.config['env']


def test_state_is_bound_to_the_check_digest(keyed):
    c = keyed
    row = prepared(c)
    credentials = ApiCredentials(c.store)
    assert credentials.write_check_state(row['id'], 'other-check-code', json.dumps({'level': 1}))
    assert run(c).seen == '{}'
    assert credentials.read_check_state(row['id'], 'other-check-code') == json.dumps({'level': 1})


def test_replacement_starts_with_empty_state_and_the_old_state_is_discarded(keyed):
    c = keyed
    old = prepared(c)
    run(c, json.dumps({'level': 1}))
    new = schedule(c, goal='Report the revised observation', replaces=old['id'])
    assert current(c, old)['state'] == prep.STATE_CANCELLED
    assert run(c).seen == '{}'
    assert c.checks.sweep_states() == 1
    assert ApiCredentials(c.store).check_state_ids() == []
    assert current(c, new)['state'] == prep.STATE_SCHEDULED


def test_sweep_keeps_active_and_paused_checks_and_drops_ended_ones(keyed):
    c = keyed
    row = prepared(c)
    run(c, json.dumps({'n': 1}))
    assert c.checks.sweep_states() == 0
    with c.store.db() as db:
        db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_PAUSED, row['id']))
    assert c.checks.sweep_states() == 0
    assert saved(c, row)
    for state in (prep.STATE_EXPIRED, prep.STATE_CANCELLED):
        with c.store.db() as db:
            db.execute('UPDATE preparations SET state=? WHERE id=?', (state, row['id']))
    assert c.checks.sweep_states() == 1
    assert not c.store.secret_items(CHECK_STATE_PREFIX)


def test_sweep_never_drops_a_state_it_cannot_retire(keyed):
    c = keyed
    row = prepared(c)
    run(c, json.dumps({'token': 'opaque-check-state-token-value-0001'}))
    with c.store.db() as db:
        db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_CANCELLED, row['id']))
    c.store.api_slot_key.delete()
    assert c.checks.sweep_states() == 0
    assert c.store.secret_items(CHECK_STATE_PREFIX)


def test_state_values_are_redacted_from_the_run_and_later_output(keyed):
    c = keyed
    row = prepared(c)
    token = 'opaque-check-state-token-value-0001'
    run(c, json.dumps({'token': token}), stdout=f'saw {token}')
    job = wake_job(c, row)
    assert checks.handoff_event(c.store, job)['trace']['output'] == 'saw [redacted]'
    assert token not in json.dumps(c.store.task_events(job['id']))
    assert token not in c.service.preparation_history(job)[-1]['content']


def test_plain_state_values_stay_readable(keyed):
    c = keyed
    row = prepared(c)
    cursor = 'cursor-value-longer-than-the-redaction-floor'
    run(c, json.dumps({'plain': {'cursor': cursor}}), stdout=f'at {cursor}')
    assert checks.handoff_event(c.store, wake_job(c, row))['trace']['output'] == f'at {cursor}'


@pytest.mark.parametrize('write', ['[1, 2]', 'not json', json.dumps({'x': 'y' * MAX_STATE_BYTES})],
                         ids=['not-object', 'not-json', 'too-large'])
def test_a_state_that_cannot_be_saved_is_reported_to_the_ai(keyed, write):
    c = keyed
    row = prepared(c)
    run(c, write)
    assert status(c, row)['last_result'] == 'failure'
    assert status(c, row)['failures'] == 1
    assert saved(c, row) == ''
    run(c, write, stdout='crossed')
    handoff = checks.handoff_event(c.store, wake_job(c, row))['trace']
    assert handoff['kind'] == 'hit'
    assert handoff['output'].startswith('crossed\n')
    assert checks.STATE_ENV in handoff['output']


def test_clearing_the_file_discards_the_state(keyed):
    c = keyed
    row = prepared(c)
    run(c, json.dumps({'n': 1}))
    assert saved(c, row)
    run(c, '')
    assert saved(c, row) == ''
    assert status(c, row)['last_result'] == 'quiet'


def test_a_saved_check_state_is_redacted_store_wide_without_any_api_credential(keyed):
    c = keyed
    prepared(c)
    token = 'opaque-check-state-token-value-0002'
    run(c, json.dumps({'token': token}))
    assert not c.store.secret_items('api_credential:')
    assert redact_known_secrets(c.store, f'later {token}') == 'later [redacted]'


def test_a_set_named_check_keeps_its_own_state_variable(keyed):
    c = keyed
    args = dict(ARGS, secrets=['check'])
    save_slot(c.store, 'check', [HOST], {'TOKEN': SECRET}, encrypted=True, code={'fields': ['TOKEN']})
    prove(c, args)
    schedule(c, args)
    process = run(c, json.dumps({'level': 2}))
    env = process.config['env']
    assert env['CHECK_STATE_FILE'] != env[checks.STATE_ENV]
    assert Path(env['CHECK_STATE_FILE']).name == 'state-check.json'
    assert process.seen == '{}'


def test_a_hit_refused_by_the_budget_leaves_the_earlier_state(keyed):
    # #1126 review: a report-once state saved with a hit that was never handed off would hide it for good.
    c = keyed
    row = prepared(c)
    run(c, json.dumps({'reported': False}))
    c.preps.set_budget(1)
    run(c, stdout='first hit')
    assert wake_job(c, row)
    finish(c, row)
    first = current(c, row)['last_run_job_id']
    run(c, json.dumps({'reported': True}), stdout='second hit')
    assert current(c, row)['last_run_job_id'] == first
    assert 'pending' not in status(c, row)
    assert json.loads(saved(c, row)) == {'reported': False}
    # The window is shorter than a day here: lift the budget and make the check due again.
    c.preps.set_budget(0)
    with c.store.db() as db:
        db.execute('UPDATE preparations SET check_next_at=? WHERE id=?', (c.clock[0], row['id']))
    again = run(c, json.dumps({'reported': True}), stdout='second hit')
    assert json.loads(again.seen) == {'reported': False}
    assert current(c, row)['last_run_job_id'] != first
    assert json.loads(saved(c, row)) == {'reported': True}


def test_a_hit_held_for_a_full_queue_saves_its_state_and_never_stores_it_in_sqlite(keyed):
    c = keyed
    row = prepared(c)
    for i in range(99):
        c.store.enqueue('Queue filler', f'queued:{i}')
    marker = 'state-marker-that-must-not-reach-sqlite-01'
    run(c, json.dumps({'reported': True, 'marker': marker}), stdout='held hit')
    assert status(c, row)['pending']['result']['output'] == 'held hit'
    assert json.loads(saved(c, row))['reported'] is True
    with c.store.db() as db:
        assert marker not in json.dumps([dict(item) for item in db.execute('SELECT * FROM preparations')])


def test_a_stop_between_settling_and_saving_repeats_rather_than_loses_the_hit(keyed, monkeypatch):
    c = keyed
    row = prepared(c)
    monkeypatch.setattr(c.checks, '_commit_state', lambda *args: None)
    run(c, json.dumps({'reported': True}), stdout='hit')
    assert wake_job(c, row)
    assert saved(c, row) == ''
