"""#1356: a change a line Work's own model loop attempts in rehearsal is counted as rehearsed, on each path.

Real local code: the line store, the scheduled run, ``run_agent`` and ``Capabilities``, the API, browser and
generic effect gates, and the line's account.  The model's tool calls, the API transport, the browser driver
and the peer channel are fixtures.  Each call runs in rehearsal and on a live line, so the count can fail.
"""
import json

import pytest

from personal_agent import work_status as ws
from personal_agent.agent_runtime import EFFECT_REHEARSAL, Capabilities, run_agent, runtime_packages
from personal_agent.api_requests import save_slot
from personal_agent.providers import ModelAdapter

from test_api_requests import CFG, Script, judgments, tool_call
from test_browser_session import FakeDriver
from test_condition_check_orders_1194 import switch
from test_condition_checks_972 import case  # noqa: F401 - fixture
from test_line_effects_1252 import line_with_check
from test_line_rehearsal_1274 import rehearse
from test_refused_effects_1250 import woken


def line_work(c, rehearsal):
    line, row = line_with_check(c)
    if rehearsal:
        rehearse(c, line)
    job = woken(c, row)
    with c.store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
    return line, job


def loop(c, job, calls, *, wire=None, **caps):
    """``run_agent`` for the line's Work: the model asks for ``calls`` one turn at a time, then answers."""
    adapter = ModelAdapter(Script(*[{'tool_calls': [call]} if isinstance(call, dict) else call for call in calls],
                                  {'content': 'Done.'}))

    def record(tool, status, detail):
        with c.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job['id'], tool, status, detail, c.clock[0]))

    capabilities = Capabilities(c.store, adapter, CFG, '', job['id'], record,
                                browser_approvals=c.service.browser_approvals_for(job), judgments=judgments(), **caps)
    if wire:
        capabilities.api().transport = wire
    run_agent(adapter, CFG, '', [{'role': 'user', 'content': job['message']}], '', capabilities, record)


def outcomes(c, job, tool):
    """``(status, failure code)`` of each recorded end of ``tool`` in the Work."""
    with c.store.db() as db:
        rows = db.execute("SELECT status,detail FROM tool_events WHERE job_id=? AND tool=? AND status!='running' "
                          'ORDER BY id', (job['id'], tool)).fetchall()
    return [(row['status'], json.loads(row['detail']).get('code')) for row in rows]


def counts(c, line):
    effects = c.service.preparations_service.line_effects(line)
    return {key: effects[key] for key in ('rehearsed', 'sent', 'refused', 'unconfirmed')}


REHEARSED = {'rehearsed': 1, 'sent': 0, 'refused': 0, 'unconfirmed': 0}
SENT = {'rehearsed': 0, 'sent': 1, 'refused': 0, 'unconfirmed': 0}


def assert_not_held(c, job):
    """A rehearsed change is not a refused one: the Work may still succeed and shows no effect."""
    assert c.service.goal_upgrade_allowed(job['id'])
    assert c.service.work_effect_state(job['id']) == ws.EFFECT_NONE


@pytest.mark.parametrize('rehearsal', [True, False])
def test_a_direct_api_change(case, rehearsal):
    save_slot(case.store, 'plain', ['plain.example'], 'plain-transport-key')
    line, job = line_work(case, rehearsal)
    sent = []

    def wire(method, url, headers, body, timeout):
        sent.append((method, url))
        return 200, {}, b'{"ok": true}'

    url = 'https://plain.example/orders'
    loop(case, job, [tool_call('write', 'api_request', slot='plain', url=url, method='POST', effect='mutate',
                               body='{"order": 1}', approval_note='Place the order.')], wire=wire)

    if rehearsal:
        assert sent == [], 'the transport is never reached'
        assert outcomes(case, job, 'api_request') == [('failed', EFFECT_REHEARSAL)]
        assert counts(case, line) == REHEARSED
        assert_not_held(case, job)
    else:
        assert sent == [('POST', url)]
        assert outcomes(case, job, 'api_request') == [('succeeded', None)]
        assert counts(case, line) == SENT


@pytest.mark.parametrize('rehearsal', [True, False])
@pytest.mark.parametrize('effect', ['mutate', 'payment'])
def test_a_browser_step_declared_to_change(case, effect, rehearsal):
    """The owner's payment autonomy is on, so rehearsal is what keeps a payment step from being pressed."""
    switch(case, True)
    line, job = line_work(case, rehearsal)
    driver = FakeDriver(pages={'/items': '<title>Notes</title><form method="post" action="/posted">'
                                         '<button type="submit">Send note</button></form>',
                               '/posted': '<title>Posted</title><p>Done</p>'}, origin='https://notes.test')

    def press(body):
        page = json.loads([m for m in body['messages'] if m.get('role') == 'tool'][-1]['content'])
        target = next(element['n'] for element in page['elements'] if element['name'] == 'Send note')
        return {'tool_calls': [tool_call('press', 'browser_click', target=str(target), effect=effect)]}

    loop(case, job, [tool_call('page', 'browser_open', url='https://notes.test/items', effect='read'), press],
         browser=lambda: driver)

    assert outcomes(case, job, 'browser_open') == [('succeeded', None)], 'the step is reached, not an earlier failure'
    if rehearsal:
        assert driver.posts == [], 'the button is never pressed'
        assert outcomes(case, job, 'browser_click') == [('failed', EFFECT_REHEARSAL)]
        assert counts(case, line) == REHEARSED
        assert_not_held(case, job)
    elif effect == 'payment':
        assert driver.posts == [('post', '/posted')]
        assert outcomes(case, job, 'browser_click') == [('succeeded', None)]
        assert counts(case, line) == SENT
    else:
        # A live line's unattended run still may not submit this form: refused for that reason, not as rehearsed.
        assert driver.posts == []
        assert outcomes(case, job, 'browser_click') == [('failed', 'unattended_run')]
        assert counts(case, line) == {**REHEARSED, 'rehearsed': 0}


@pytest.mark.parametrize('rehearsal', [True, False])
def test_a_package_tool_that_changes(case, rehearsal):
    """A package's own tool id, so only the tool loop's generic fence can hold it."""
    line, job = line_work(case, rehearsal)
    packages = runtime_packages([{'version': 1, 'id': 'relay', 'enabled': True, 'roles': [], 'tools': [
        {'id': 'relay_note', 'host_action': 'a2a_message', 'mode': 'bounded_write'}]}])
    sent = []

    def peer(args):
        sent.append(args)
        return {'reply': 'ok'}

    loop(case, job, [tool_call('relay', 'relay_note', peer_id='peer-1', message='hello')],
         packages=packages, current_packages=lambda: packages, a2a=peer)

    if rehearsal:
        assert sent == [], 'the package tool is never reached'
        assert outcomes(case, job, 'relay_note') == [('failed', EFFECT_REHEARSAL)]
        assert counts(case, line) == REHEARSED
        assert_not_held(case, job)
    else:
        assert sent == [{'peer_id': 'peer-1', 'message': 'hello'}]
        assert outcomes(case, job, 'relay_note') == [('succeeded', None)]
        assert counts(case, line) == SENT
