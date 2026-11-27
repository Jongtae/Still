"""#1265: a check plan a Work writes is the Work's own data, not a stored secret.

Real store, encrypted check state, check scheduler and redaction pass with fixture processes
(as ``test_check_plan_1201``).  No live API.
"""
import json

from personal_agent import condition_checks as checks, preparations as prep
from personal_agent.current_context import redact_known_secrets
from test_check_plan_1201 import update
from test_condition_check_state_972 import StateProcess, keyed, run, saved  # noqa: F401 - fixture
from test_condition_checks_972 import case, current, finish, prepared, wake_job  # noqa: F401 - fixture

HOST_PATH = 'openapi.example-broker.com/v1/orders'  # 24+ characters: long enough to be held as a token
EXPIRES = '2026-11-27T16:10:00+09:00T'


def settled(c, row):
    if current(c, row)['state'] == prep.STATE_RUNNING:
        finish(c, row)


def planned(c):
    row = prepared(c)
    run(c, '{"count":1}', stdout='Condition met')
    job = wake_job(c, row)
    plan = {'calls': [], 'expires': None, 'target': HOST_PATH, 'until': EXPIRES}
    assert update(c, job['id'], row, plan)['keys']
    finish(c, row)
    return row, job


def test_a_work_written_plan_is_not_redacted_and_the_check_keeps_running(keyed):
    c = keyed
    row, _job = planned(c)
    assert redact_known_secrets(c.store, f'next {HOST_PATH} {EXPIRES}') == f'next {HOST_PATH} {EXPIRES}'
    # The check's next runs read and rewrite the state; the plan stays plain and the check is not paused.
    for count in (2, 3):
        run(c, json.dumps({**json.loads(saved(c, row)), 'count': count}))
        settled(c, row)
    run(c, stdout=f'plan {HOST_PATH} until {EXPIRES}')
    assert checks.handoff_event(c.store, wake_job(c, row))['trace']['output'].startswith(f'plan {HOST_PATH} until {EXPIRES}')
    after = current(c, row)
    assert not json.loads(after['check_spec']).get('scrubbed')
    assert json.loads(after['check_status'] or '{}').get('paused') is None
    assert after['state'] != prep.STATE_PAUSED
    assert redact_known_secrets(c.store, HOST_PATH) == HOST_PATH


def test_a_token_the_check_saves_beside_the_plan_stays_secret(keyed):
    c = keyed
    row, _job = planned(c)
    token = 'opaque-check-state-token-value-1265'
    run(c, json.dumps({**json.loads(saved(c, row)), 'token': token}), stdout=f'saw {token} for {HOST_PATH}')
    job = wake_job(c, row)
    output = checks.handoff_event(c.store, job)['trace']['output']
    assert token not in output and HOST_PATH in output
    assert redact_known_secrets(c.store, f'later {token}') == 'later [redacted]'


def test_a_plan_value_the_check_replaces_with_a_token_is_secret_again(keyed):
    c = keyed
    row, _job = planned(c)
    token = 'opaque-check-state-token-value-1266'
    state = json.loads(saved(c, row))
    state['target'] = token
    run(c, json.dumps(state))
    assert redact_known_secrets(c.store, f'later {token}') == 'later [redacted]'


def test_a_check_whose_own_host_was_in_a_retired_plan_keeps_running(keyed):
    """The live case: the plan named the check's own host, then the state dropped it (so it was retired)."""
    from test_condition_checks_972 import ARGS, prove, schedule
    c = keyed
    long_host = 'openapi.long-broker-example.com'
    args = {**ARGS, 'hosts': [long_host]}
    prove(c, args)
    row = schedule(c, args)
    run(c, '{"count":1}', stdout='Condition met')
    job = wake_job(c, row)
    assert update(c, job['id'], row, {'target': long_host})['keys']
    finish(c, row)
    run(c, json.dumps({'count': 2}))  # the check's own save drops the plan value: it is retired
    settled(c, row)
    run(c, json.dumps({'count': 3}))
    after = current(c, row)
    spec = json.loads(after['check_spec'])
    assert not spec.get('scrubbed') and spec['arguments']['hosts'] == [long_host]
    assert json.loads(after['check_status'] or '{}').get('paused') is None
