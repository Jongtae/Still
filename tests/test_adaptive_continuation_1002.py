"""#1002 evidence: model-free, local and deterministic, with no live accounts.

The scripted model chooses fixture calls through Still's normal Work and API
broker paths. These tests make no claim about uncoached model behaviour.
"""
from ast import literal_eval
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from personal_agent import direct_message_surface as surface
from personal_agent.agent_runtime import WORK_STOP_KEY, external_effect_events, unknown_effect_targets
from personal_agent.api_requests import ApiError, ApiRequests, remove_slot
from personal_agent.decision import (OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, SelectionDecision,
                                     fixture_confidence)

from test_agency_loop import call, finish, judgments
from test_condition_checks_972 import case as condition_case, current, prepared, tick, wake_job

import test_effect_continuation_1000 as continuation
from test_effect_continuation_1000 import CLOSE, HOST, OPEN, READ, Wire, claim, interrupted_dispatch, setup


ORDER = {**READ, 'url': f'https://{HOST}/order'}
QUEUE = {**READ, 'url': f'https://{HOST}/queue'}
CHECKOUT = {**OPEN, 'url': f'https://{HOST}/checkout'}
RESERVATION = {**READ, 'url': f'https://{HOST}/reservation'}


def reply(now, http_status=200, **data):
    return http_status, {}, json.dumps({**data, 'as_of': now}).encode()


class TradingWire(Wire):
    goal = 'Buy one unit, exit once the buy fills, and confirm the position is flat.'
    paths = ['open']

    def __call__(self, method, url, headers, body, timeout):
        request = urlsplit(url)
        path = request.path.removeprefix('/')
        if path in ('open', 'close'):
            assert method == 'POST'
            if path == 'open':
                assert self.first_ref is None, 'opening order sent twice'
            return super().__call__(method, url, headers, body, timeout)
        assert method == 'GET' and path in ('state', 'order')
        self.sent.append(path)
        if path == 'order':
            requested = parse_qs(request.query).get('request_ref', [self.first_ref])[0]
            return reply(self.now, request_ref=requested, status=self.status,
                         filled_units=int(self.status == 'applied'))
        return reply(self.now, position=int(self.state == 'opened'))


class BookingWire(Wire):
    goal = 'Book one place and confirm my reservation.'
    paths = ['queue', 'queue', 'queue', 'checkout']
    token = 'admitted-fixture-token'
    reservation = 'reservation-1002'

    def __init__(self):
        super().__init__()
        self.queue_reads = 0
        self.admitted = False
        self.cursorless = False

    def __call__(self, method, url, headers, body, timeout):
        request = urlsplit(url)
        path = request.path.removeprefix('/')
        self.sent.append(path)
        if path == 'queue' and method == 'GET':
            self.queue_reads += 1
            expected_query = ('', 'cursor=first-wait', 'cursor=changed-wait')[self.queue_reads - 1]
            assert request.query == ('' if self.cursorless else expected_query)
            if self.queue_reads == 1:
                return reply(self.now, position=17, **self.refresh('first-wait'))
            if self.queue_reads == 2:
                return reply(self.now, screen={'name': 'official waiting screen', 'ahead': 3,
                                               **self.refresh('changed-wait')})
            self.admitted = True
            return reply(self.now, admission={'token': self.token})
        if path == 'checkout' and method == 'POST':
            token = json.loads(body or b'{}').get('admission_code')
            if not self.admitted or token != self.token:
                return reply(self.now, 403, error='queue admission required')
            assert self.first_ref is None, 'checkout sent twice'
            self.first_ref = headers['Idempotency-Key']
            if self.status != 'not-applied':
                self.state = 'booked'
            if self.lost:
                raise TimeoutError
            return reply(self.now, reservation_id=self.reservation)
        assert path == 'reservation' and method == 'GET'
        return reply(self.now, request_ref=self.reference or self.first_ref, status=self.status,
                     reservation_id=self.reservation if self.status == 'applied' and self.state == 'booked' else None)


    def refresh(self, cursor):
        return {} if self.cursorless else {'refresh_url': f'{QUEUE["url"]}?cursor={cursor}'}


def checkout(token):
    return {**CHECKOUT, 'body': json.dumps({'admission_code': token})}


TRADING = pytest.param(TradingWire, id='trading')
BOOKING = pytest.param(BookingWire, id='booking')


@pytest.fixture
def scenario(request, monkeypatch):
    # Let #1000 configure the injected model, registered slot, allowance and
    # clock against this wire, so all services exercise the same broker setup.
    monkeypatch.setattr(continuation, 'Wire', request.param)
    fixture = request.getfixturevalue('setup')
    wire = fixture[2]
    create_api = ApiRequests.__init__

    # ApiRequests binds its default clock at import time. Use the wire clock
    # for both persisted dispatch timestamps and authoritative observations.
    def api_with_clock(api, *args, **kwargs):
        kwargs.setdefault('clock', lambda: wire.now)
        create_api(api, *args, **kwargs)

    monkeypatch.setattr(ApiRequests, '__init__', api_with_clock)
    return fixture


class WorkerInterrupted(BaseException):
    pass


def ask(ref, args):
    return {'tool_calls': [call(ref, 'api_request', **args)]}


def result(body, ref):
    [message] = [message for message in body['messages']
                 if message['role'] == 'tool' and message['tool_call_id'] == ref]
    return json.loads(message['content'])


def booking_turns():
    def read_changed_screen(body):
        waiting = result(body, 'queue-first')['data']
        assert waiting['position'] == 17
        return ask('queue-changed', {**QUEUE, 'url': waiting['refresh_url']})

    def keep_waiting(body):
        data = result(body, 'queue-changed')['data']
        assert 'position' not in data
        screen = data['screen']
        assert (screen['name'], screen['ahead']) == ('official waiting screen', 3)
        return ask('queue-admitted', {**QUEUE, 'url': screen['refresh_url']})

    def use_admission(body):
        token = result(body, 'queue-admitted')['data']['admission']['token']
        return ask('checkout', checkout(token))

    return [ask('queue-first', QUEUE), read_changed_screen, keep_waiting, use_admission]


def status_read(args, receipt):
    return {**args, 'reconcile_receipt': str(receipt), 'receipt_ref_field': '$.request_ref'}


def api_steps(service, work):
    return {detail['call_id']: (state, detail) for _, action, state, detail, _ in service.work_steps(work)
            if action == 'api_request'}


def stale_worker_check(store, service, wire, work, remaining_effect):
    budget = service.inherited_work_budget(work)
    epoch = budget.ledger.epoch
    old_api = ApiRequests(store, work, ledger=budget.ledger, transport=wire, clock=lambda: wire.now)

    def check():
        assert store.job(work)['status'] == 'running'
        assert service.inherited_work_budget(work).ledger.epoch == epoch + 1
        assert budget.ledger.lapse() == 'superseded'
        with pytest.raises(ApiError) as error:
            old_api.call(remaining_effect)
        assert error.value.code == 'api_work_inactive'
        assert wire.sent == wire.paths

    return check


def recovered(service, store, work):
    assert store.job(work)['status'] == 'running'
    service.recover_interrupted_work()
    assert store.job(work)['status'] == 'queued'


def assert_outstanding(store, work):
    job = store.job(work)
    assert job['status'] != 'succeeded' and job['goal_outcome'] != 'reached'


@pytest.mark.parametrize('scenario', [TRADING], indirect=True)
def test_trading_recovers_a_known_fill_and_exits_once(scenario, monkeypatch):
    store, service, wire, script = scenario
    work = store.enqueue(wire.goal, 'fixture-work', owner_typed=True)
    script.messages.append(ask('open', OPEN))
    settle = ApiRequests._settle_effect

    def cut_after_receipt(api, claim_id, status, response_status):
        settle(api, claim_id, status, response_status)
        if claim_id is not None:
            raise WorkerInterrupted

    with monkeypatch.context() as cut:
        cut.setattr(ApiRequests, '_settle_effect', cut_after_receipt)
        with pytest.raises(WorkerInterrupted):
            service.run_one()
    [(receipt, opened)], [] = external_effect_events(store, work)
    assert opened['response_status'] == 200
    assert wire.state == 'opened' and wire.sent == ['open']
    assert 'open' not in api_steps(service, work)
    check_stale = stale_worker_check(store, service, wire, work, CLOSE)
    recovered(service, store, work)

    def replay(body):
        check_stale()
        brief = '\n'.join(message.get('content') or '' for message in body['messages'])
        assert wire.goal in brief and f'"receipt": {receipt}' in brief
        return ask('replay', OPEN)

    def check_order(body):
        assert result(body, 'replay')['code'] == 'api_effect_replayed'
        return ask('order', status_read(ORDER, receipt))

    def exit_filled_order(body):
        data = result(body, 'order')['data']
        assert (data['request_ref'], data['status'], data['filled_units']) == (wire.first_ref, 'applied', 1)
        return ask('exit', CLOSE)

    def confirmed_flat(body):
        assert result(body, 'flat')['data']['position'] == 0
        return finish('done', 'flat', summary='The broker confirms the position is flat.')

    def assess(facts):
        assert facts['owner_request'] == wire.goal
        assert f'"receipt": {receipt}' in facts['observations']
        return '[flat] api_request:' in facts['observations'] and '"position": 0' in facts['observations']

    service.decision_judge = judgments(assess)
    script.messages.extend([replay, check_order, exit_filled_order, ask('flat', READ), confirmed_flat])
    assert service.run_one()
    steps = api_steps(service, work)
    assert steps['replay'][0] == 'failed' and steps['replay'][1]['code'] == 'api_effect_replayed'
    assert [steps[ref][0] for ref in ('order', 'exit', 'flat')] == ['succeeded'] * 3
    assert steps['order'][1]['evidence']['source']['path'] == '/order'
    assert store.job(work)['status'] == 'succeeded' and store.job(work)['goal_outcome'] == 'reached'
    assert wire.sent == ['open', 'order', 'close', 'state'] and wire.state == 'closed'
    [first, (_, exited)], [] = external_effect_events(store, work)
    # An already observed receipt is checked against status without rewriting it.
    assert first == (receipt, opened)
    assert exited['response_status'] == 200
    assert [job['id'] for job in store.jobs()] == [work]


def lose_response_and_recover(scenario, turns, first_ref, resolution, remaining_effect):
    store, service, wire, script = scenario
    wire.lost = True
    wire.status = resolution
    work = store.enqueue(wire.goal, 'fixture-work', owner_typed=True)

    def premature(body):
        assert result(body, first_ref)['code'] == 'effect_unknown'
        return finish('premature', first_ref, summary='The request is complete.')

    def interrupt(body):
        assert result(body, 'premature')['code'] == 'failed_ref'
        assert_outstanding(store, work)
        raise WorkerInterrupted

    script.messages.extend([*turns, premature, interrupt])
    with pytest.raises(WorkerInterrupted):
        service.run_one()
    [], [(receipt, dispatched)] = external_effect_events(store, work)
    assert dispatched['request_ref'] == wire.first_ref
    assert wire.sent == wire.paths
    check_stale = stale_worker_check(store, service, wire, work, remaining_effect)
    recovered(service, store, work)
    return work, receipt, check_stale


def configure_judgments(service, wire, authoritative, goal_check):
    proofs = []

    def choose(context, candidates, _question):
        if context.purpose != 'effect-reconciled':
            return None
        source = literal_eval(context.facts['read_source'])
        assert source['path'] == authoritative and source['method'] == 'GET'
        payload = json.loads(context.facts['authoritative_service_response'])
        assert context.facts['request_reference'] == payload['request_ref'] == wire.first_ref
        proofs.append(payload['status'])
        resolution = payload['status'] if payload['status'] in ('applied', 'not-applied') else 'unknown'
        return SelectionDecision(OUTCOME_DECIDED, resolution, candidates, fixture_confidence())

    def judge(context, _question):
        if context.purpose == 'goal-reached':
            assert context.facts['owner_request'] == wire.goal
            return BinaryDecision(OUTCOME_DECIDED, goal_check(context.facts), fixture_confidence())
        return None

    service.use_decision_engine(FixtureDecisionEngine(choose=choose, judge=judge))
    return proofs


def assert_pending(store, service, work, receipt):
    [], [(pending, _)] = external_effect_events(store, work)
    assert pending == receipt
    assert service._work_has_unknown_effect(work)
    assert unknown_effect_targets(store, work)


def assert_resolution(store, service, work, receipt, resolution, read_ref):
    steps = api_steps(service, work)
    assert steps[read_ref][0] == 'succeeded'
    evidence = steps[read_ref][1]['evidence']
    if resolution == 'pending':
        assert 'reconciled_receipt' not in evidence
        assert_pending(store, service, work, receipt)
        assert store.job(work)['status'] == store.job(work)['effect_state'] == 'unknown'
        return
    observed, pending = external_effect_events(store, work)
    assert pending == []
    settled = dict(observed)[receipt]
    assert settled['resolution'] == resolution
    assert evidence['reconciled_receipt'] == receipt
    assert settled['read_digest'] == evidence['content_digest']
    assert not service._work_has_unknown_effect(work)


@pytest.mark.parametrize('scenario', [TRADING], indirect=True)
@pytest.mark.parametrize(('resolution', 'blocked_effect'), [
    pytest.param('applied', OPEN, id='applied'),
    pytest.param('not-applied', OPEN, id='not-applied'),
    pytest.param('pending', OPEN, id='pending'),
    pytest.param('applied', CLOSE, id='applied-after-refused-exit'),
])
def test_trading_lost_open_waits_for_matching_order_status(scenario, resolution, blocked_effect):
    store, service, wire, script = scenario
    work, receipt, check_stale = lose_response_and_recover(
        scenario, [ask('open', OPEN)], 'open', resolution, CLOSE)
    checks = configure_judgments(
        service, wire, '/order',
        lambda facts: '[flat] api_request:' in facts['observations'] and '"position": 0' in facts['observations'])

    def held_mutation(_body):
        check_stale()
        assert_pending(store, service, work, receipt)
        return ask('blocked-mutation', blocked_effect)

    def ordinary_read(body):
        assert result(body, 'blocked-mutation')['code'] == 'effect_unknown'
        return ask('ordinary', ORDER)

    def wrong_reference(body):
        assert result(body, 'ordinary')['data']['request_ref'] == wire.first_ref
        assert_pending(store, service, work, receipt)
        wrong_order = {**ORDER, 'url': f'{ORDER["url"]}?request_ref=unrelated-dispatch'}
        return ask('wrong-ref', status_read(wrong_order, receipt))

    def matching_reference(body):
        assert result(body, 'wrong-ref')['data']['request_ref'] == 'unrelated-dispatch'
        assert_pending(store, service, work, receipt)
        assert checks == []
        matching_order = {**ORDER, 'url': f'{ORDER["url"]}?request_ref={wire.first_ref}'}
        return ask('authoritative', status_read(matching_order, receipt))

    def remaining(body):
        proof = result(body, 'authoritative')
        assert proof['data']['status'] == resolution
        if resolution == 'applied':
            assert proof['provenance']['reconciled_receipt'] == receipt
            return ask('exit', CLOSE)
        return finish('partial', 'authoritative', status='partial', summary='No confirmed fill to exit.')

    script.messages.extend([held_mutation, ordinary_read, wrong_reference, matching_reference, remaining])
    if resolution == 'applied':
        script.messages.extend([ask('flat', READ), finish('done', 'flat', summary='The broker confirms a flat position.')])
    assert service.run_one()
    assert not script.messages
    steps = api_steps(service, work)
    state, detail = steps['blocked-mutation']
    assert state == 'failed' and detail['code'] == 'effect_unknown' and detail['ran'] is False
    for ref in ('ordinary', 'wrong-ref'):
        assert steps[ref][0] == 'succeeded' and 'reconciled_receipt' not in steps[ref][1]['evidence']
    assert checks == [resolution]
    assert_resolution(store, service, work, receipt, resolution, 'authoritative')
    expected = ['open', 'order', 'order', 'order']
    if resolution == 'applied':
        assert steps['exit'][0] == steps['flat'][0] == 'succeeded'
        assert store.job(work)['status'] == 'succeeded' and store.job(work)['goal_outcome'] == 'reached'
        assert wire.state == 'closed'
        expected += ['close', 'state']
    else:
        assert_outstanding(store, work)
    assert wire.sent == expected
    assert [job['id'] for job in store.jobs()] == [work]


@pytest.mark.parametrize('scenario', [BOOKING], indirect=True)
@pytest.mark.parametrize('resolution', ['applied', 'not-applied', 'pending'])
def test_booking_waits_for_admission_then_reconciles_lost_checkout(scenario, resolution):
    store, service, wire, script = scenario
    second_checkout = checkout(wire.token)
    work, receipt, check_stale = lose_response_and_recover(
        scenario, booking_turns(), 'checkout', resolution, second_checkout)
    checks = configure_judgments(
        service, wire, '/reservation',
        lambda facts: '[reservation] api_request:' in facts['observations']
        and f'"reservation_id": "{wire.reservation}"' in facts['observations'])

    def try_again(_body):
        check_stale()
        assert_pending(store, service, work, receipt)
        return ask('second-checkout', second_checkout)

    def read_reservation(body):
        assert result(body, 'second-checkout')['code'] == 'effect_unknown'
        assert_pending(store, service, work, receipt)
        assert_outstanding(store, work)
        return ask('reservation', status_read(RESERVATION, receipt))

    def report(body):
        proof = result(body, 'reservation')
        data = proof['data']
        assert data['request_ref'] == wire.first_ref and data['status'] == resolution
        if data['reservation_id'] is not None:
            assert proof['provenance']['reconciled_receipt'] == receipt
            return finish('done', 'reservation', summary=f'The site confirms {data["reservation_id"]}.')
        return finish('partial', 'reservation', status='partial', summary='The site has no confirmed reservation.')

    script.messages.extend([try_again, read_reservation, report])
    assert service.run_one()
    assert not script.messages
    steps = api_steps(service, work)
    assert [steps[ref][0] for ref in ('queue-first', 'queue-changed', 'queue-admitted')] == ['succeeded'] * 3
    assert steps['second-checkout'][0] == 'failed' and steps['second-checkout'][1]['code'] == 'effect_unknown'
    assert checks == [resolution]
    assert_resolution(store, service, work, receipt, resolution, 'reservation')
    if resolution == 'applied':
        job = store.job(work)
        assert job['status'] == 'succeeded' and job['goal_outcome'] == 'reached'
        assert wire.reservation in job['response']
    else:
        assert_outstanding(store, work)
    assert wire.sent == ['queue', 'queue', 'queue', 'checkout', 'reservation']
    assert [job['id'] for job in store.jobs()] == [work]


@pytest.mark.parametrize('scenario', [BOOKING], indirect=True)
def test_booking_can_refresh_a_cursorless_waiting_screen(scenario):
    store, service, wire, script = scenario
    wire.cursorless = True
    work = store.enqueue(wire.goal, 'fixture-work', owner_typed=True)

    def later_poll(body):
        assert result(body, 'first')['data']['position'] == 17
        wire.now += 1
        return ask('second', QUEUE)

    script.messages.extend([ask('first', QUEUE), later_poll,
                            finish('partial', status='partial', summary='The queue is still waiting.')])
    assert service.run_one()
    steps = api_steps(service, work)
    assert steps['first'][0] == 'succeeded'
    assert wire.sent == ['queue', 'queue']
    assert steps['second'][0] == 'succeeded'
    assert result(script.bodies[-1], 'second')['data']['screen']['ahead'] == 3
    assert_outstanding(store, work)


def admit(api):
    waiting = api.call(QUEUE)['data']
    changed = api.call({**QUEUE, 'url': waiting['refresh_url']})['data']['screen']
    admitted = api.call({**QUEUE, 'url': changed['refresh_url']})['data']
    return checkout(admitted['admission']['token'])


def impose_barrier(store, wire, work, ledger, barrier):
    if barrier == 'stop':
        store.put(WORK_STOP_KEY, [work])
    elif barrier == 'expiry':
        wire.now = ledger.deadline + 1
    else:
        assert barrier == 'revocation'
        remove_slot(store, 'fixture')


@pytest.mark.parametrize('scenario', [TRADING, BOOKING], indirect=True)
@pytest.mark.parametrize('barrier', ['stop', 'expiry', 'revocation'])
def test_claim_rechecks_authority_for_both_services(scenario, barrier):
    store, service, wire, script = scenario
    if isinstance(wire, TradingWire):
        job, budget, api = claim(scenario, wire.goal)
        work, ledger = job['id'], budget.ledger
        interrupted_dispatch(store, job, api)
        recovered(service, store, work)
    else:
        work, _, _ = lose_response_and_recover(
            scenario, booking_turns(), 'checkout', 'applied', checkout(wire.token))
        ledger = service.inherited_work_budget(work).ledger
    receipts = external_effect_events(store, work)
    impose_barrier(store, wire, work, ledger, barrier)
    assert service._claim_queued_work() is True
    assert store.job(work)['status'] == 'interrupted'
    assert_outstanding(store, work)
    assert wire.sent == wire.paths
    assert external_effect_events(store, work) == receipts
    assert not service._claim_queued_work() and not script.messages


@pytest.mark.parametrize('scenario', [TRADING, BOOKING], indirect=True)
@pytest.mark.parametrize(('barrier', 'code'), [
    ('stop', 'api_work_inactive'), ('expiry', 'api_work_inactive'), ('revocation', 'needs_setup'),
])
def test_dispatch_rechecks_authority_after_reserving_outbound(scenario, monkeypatch, barrier, code):
    store, service, wire, _ = scenario
    job, budget, api = claim(scenario, wire.goal)
    work = job['id']
    budget.ledger.wall = lambda: wire.now
    if isinstance(wire, TradingWire):
        interrupted_dispatch(store, job, api)
        assert api.call(ORDER)['data']['filled_units'] == 1
        action, expected = CLOSE, ['open', 'order']
    else:
        action, expected = admit(api), ['queue', 'queue', 'queue']
    receipts = external_effect_events(store, work)

    echo = SimpleNamespace(name='fixture', CONFIG_KEY='fixture-outbound', outbound_target=lambda cfg, url: True)
    monkeypatch.setitem(store._outbound_surfaces, echo.name, echo)
    store.put(echo.CONFIG_KEY, {'enabled': True, 'generation': '1', 'channel_id': 'channel', 'owner_user_id': 'owner'})
    reserve = surface.reserve_outbound

    def revoke_after_reservation(*args):
        reservations = reserve(*args)
        [(_, event, _)] = reservations
        with store.db() as db:
            assert db.execute('SELECT status FROM tool_events WHERE id=?', (event,)).fetchone()['status'] == 'pending'
        assert wire.sent == expected
        impose_barrier(store, wire, work, budget.ledger, barrier)
        return reservations

    monkeypatch.setattr(surface, 'reserve_outbound', revoke_after_reservation)
    with pytest.raises(ApiError) as error:
        api.call(action)
    assert error.value.code == code
    assert wire.sent == expected
    assert external_effect_events(store, work) == receipts
    with store.db() as db:
        assert not db.execute('SELECT 1 FROM tool_events WHERE tool=?', (surface.OUTBOUND_POST,)).fetchone()
    assert_outstanding(store, work)


def test_condition_wake_latency(condition_case, monkeypatch, record_property):
    c = condition_case
    monkeypatch.setattr('time.time', lambda: c.clock[0])
    row = prepared(c, every=15)
    tick(c)
    assert not wake_job(c, row)

    condition_true_at = c.clock[0] + 1
    c.clock[0] = condition_true_at
    assert not c.checks.run_due()
    assert not wake_job(c, row)

    c.clock[0] = current(c, row)['check_next_at']
    tick(c, {'stdout': 'Admission observed'})
    job = wake_job(c, row)
    assert job['status'] == 'queued'
    latency = job['created'] - condition_true_at
    record_property('wake_latency_fake_seconds', latency)
    print(f'Condition wake latency: {latency:g} fake seconds')
    assert 0 <= latency <= row['every_seconds']
