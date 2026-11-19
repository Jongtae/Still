"""Model-free recovery through the production Work queue, receipt broker and judgment."""
import json
import time
from unittest import mock

import pytest

from personal_agent import owner_mcp, preparations as prep, trust_record
from personal_agent.agent_runtime import WORK_STOP_KEY, effect_call_ref, external_effect_events, unknown_effect_targets
from personal_agent.api_requests import ApiError, ApiRequests, remove_slot, save_slot
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, SelectionDecision, fixture_confidence
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.self_set_writes import set_allowance

from test_agency_loop import Script, call, finish
from test_line_pieces_938 import _PieceCase
from test_orchestrator import decided, plan


HOST = 'receipt.example'
OPEN = {'slot': 'fixture', 'url': f'https://{HOST}/open', 'method': 'POST', 'effect': 'mutate',
        'as_of_field': '$.as_of'}
CLOSE = {**OPEN, 'url': f'https://{HOST}/close'}
READ = {'slot': 'fixture', 'url': f'https://{HOST}/state', 'method': 'GET', 'effect': 'read',
        'as_of_field': '$.as_of'}


class Wire:
    def __init__(self):
        self.now = int(time.time())
        self.sent = []
        self.state = 'initial'
        self.first_ref = None
        self.lost = False
        self.status = 'applied'
        self.reference = None
        self.status_as_of = None

    def __call__(self, method, url, headers, body, timeout):
        path = url.rsplit('/', 1)[-1]
        self.sent.append(path)
        if path == 'open':
            assert self.state == 'initial'
            if not (self.lost and self.status == 'not-applied'):
                self.state = 'opened'
            self.first_ref = headers['Idempotency-Key']
            if self.lost:
                raise TimeoutError
            return 200, {}, json.dumps({'receipt': 'observed', 'as_of': self.now}).encode()
        if path == 'close':
            assert self.state == 'opened'
            assert self.first_ref != headers['Idempotency-Key']
            self.state = 'closed'
            return 200, {}, json.dumps({'receipt': 'closed', 'as_of': self.now}).encode()
        return 200, {}, json.dumps({'request_ref': self.reference or self.first_ref, 'status': self.status,
                                   'state': self.state,
                                   'as_of': self.now if self.status_as_of is None else self.status_as_of}).encode()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    wire = Wire()
    script = Script()

    def model(url, body, headers=None, timeout=60):
        if any(tool['function']['name'] == 'agentos_connection_probe' for tool in body.get('tools', [])):
            return {'choices': [{'message': {'tool_calls': [{'id': 'probe', 'type': 'function',
                                                   'function': {'name': 'agentos_connection_probe', 'arguments': '{}'}}]}}]}
        return script(url, body, headers, timeout)

    store = QuickStore(tmp_path)
    service = AgentService(store, ModelAdapter(model))
    service.save_model({'provider': 'compatible', 'endpoint': 'https://example.test/v1',
                        'model': 'test-model', 'api_key': 'k'})
    tested = service.test_model()
    assert tested['ok'], tested
    save_slot(store, 'fixture', [HOST], 'fixture-transport-key')
    set_allowance(service, 'slot:fixture', True, 0, 'owner')
    monkeypatch.setattr('personal_agent.api_requests.http_request', wire)
    monkeypatch.setattr('time.time', lambda: wire.now)
    return store, service, wire, script


def claim(setup, mandate):
    store, service, wire, _ = setup
    work_id = store.enqueue(mandate, 'fixture-work', owner_typed=True)
    job = service._claim_queued_work()
    assert job['id'] == work_id
    budget = service.work_budget(work_id)
    api = ApiRequests(store, work_id, ledger=budget.ledger, transport=wire, clock=lambda: wire.now)
    return job, budget, api


def interrupted_dispatch(store, job, api):
    # Cut power after the broker's durable receipt, before the loop can record a terminal call.
    with store.db() as db:
        detail = json.dumps({'scope': 'main', 'call_id': 'open', 'declared_effect': 'mutate',
                             'effect_call_ref': 'issued-by-host'})
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (job['id'], 'api_request', 'running', detail, time.time()))
    with effect_call_ref('issued-by-host'):
        return api.call(OPEN)


@pytest.mark.parametrize('mandate', [
    'Observe a fill, close the position and verify the final external state.',
    'Obtain a booking receipt, close the temporary hold and verify the final external state.',
])
def test_restart_keeps_receipt_mandate_and_budget_without_resending(setup, mandate, monkeypatch):
    store, service, wire, script = setup
    work_id = store.enqueue(mandate, 'fixture-work', owner_typed=True)
    script.messages.append({'tool_calls': [call('open','api_request',**OPEN)]})
    settle = ApiRequests._settle_effect

    class WorkerInterrupted(BaseException):
        pass

    def settle_then_interrupt(api, claim, status, response_status):
        settle(api, claim, status, response_status)
        if claim is not None:
            raise WorkerInterrupted

    with monkeypatch.context() as interrupted:
        interrupted.setattr(ApiRequests, '_settle_effect', settle_then_interrupt)
        with pytest.raises(WorkerInterrupted):
            service.run_one()
    job = store.job(work_id)
    budget = service.inherited_work_budget(work_id)
    assert job['status'] == 'running' and budget.turns_used == 1
    [(receipt, _)], [] = external_effect_events(store, work_id)
    old_epoch = budget.ledger.epoch
    api = ApiRequests(store, work_id, ledger=budget.ledger, transport=wire, clock=lambda: wire.now)

    service.recover_interrupted_work()
    assert store.job(job['id'])['status'] == 'queued'
    recovered = service._claim_queued_work()
    assert recovered['id'] == job['id']
    assert recovered['read_checkpoint_resume'] == old_epoch + 1
    assert not service._claim_queued_work()
    with pytest.raises(ApiError, match='.') as error:
        api.call(CLOSE)
    assert error.value.code == 'api_work_inactive'
    assert wire.sent == ['open']
    assert service.work_budget(job['id']).turns_used == 1

    def judge(context, _question):
        if context.purpose != 'goal-reached':
            return None
        assert context.facts['owner_request'] == mandate
        assert f'"receipt": {receipt}' in context.facts['observations']
        return BinaryDecision(OUTCOME_DECIDED, wire.sent[-1] == 'state' and wire.state == 'closed', fixture_confidence())

    def replay(body):
        sent = json.dumps(body['messages'])
        assert mandate in sent and f'\\"receipt\\": {receipt}' in sent
        return {'tool_calls': [call('replay','api_request',**OPEN)]}

    service.decision_judge = ConversationJudgments(FixtureDecisionEngine(judge=judge))
    script.messages.extend([replay,
                            {'tool_calls': [call('close','api_request',**CLOSE)]},
                            {'tool_calls': [call('verify','api_request',**READ)]},
                            finish('done','close','verify',summary='The service confirms it is closed.')])
    assert service.work_executor._run_claimed(recovered)
    final = store.job(job['id'])
    assert final['goal_outcome'] == 'reached', final
    assert final['status'] == 'succeeded', final
    assert wire.sent == ['open','close','state']
    assert len(store.jobs()) == 1


@pytest.mark.parametrize('barrier', ['stop', 'expiry', 'revocation'])
def test_recovery_rechecks_authority_at_claim(setup, barrier):
    store, service, wire, _ = setup
    job, budget, api = claim(setup, 'Finish both steps')
    interrupted_dispatch(store, job, api)
    service.recover_interrupted_work()
    assert store.job(job['id'])['status'] == 'queued'
    if barrier == 'stop':
        store.put(WORK_STOP_KEY, [job['id']])
    elif barrier == 'expiry':
        wire.now = budget.ledger.deadline + 1
    else:
        remove_slot(store, 'fixture')
    assert service._claim_queued_work() is True
    assert store.job(job['id'])['status'] == 'interrupted'
    assert wire.sent == ['open']


def test_recreated_slot_cannot_authorize_a_queued_effect_recovery(setup):
    store, service, wire, _ = setup
    job, _, api = claim(setup, 'Finish both changes')
    interrupted_dispatch(store, job, api)
    service.recover_interrupted_work()
    assert store.job(job['id'])['status'] == 'queued'

    remove_slot(store, 'fixture')
    save_slot(store, 'fixture', [HOST], 'fixture-replacement-key')
    assert service._claim_queued_work() is True
    assert store.job(job['id'])['status'] == 'interrupted'
    assert wire.sent == ['open']


@pytest.mark.parametrize('barrier, code', [('stop', 'api_work_inactive'), ('expiry', 'api_work_inactive'),
                                          ('revocation', 'needs_setup')])
def test_api_rechecks_authority_after_outbound_reservation(setup, monkeypatch, barrier, code):
    from types import SimpleNamespace
    from personal_agent import direct_message_surface as surface

    store, _, wire, _ = setup
    job, budget, api = claim(setup, 'Apply the change')
    budget.ledger.wall = lambda: wire.now
    echo = SimpleNamespace(name='fixture', CONFIG_KEY='fixture-outbound', outbound_target=lambda cfg, url: True,
                           outbound_identity=lambda *_: None, replay_ingress=lambda: None)
    monkeypatch.setitem(store._outbound_surfaces, echo.name, echo)
    store.put(echo.CONFIG_KEY, {'enabled': True, 'generation': '1', 'channel_id': 'channel', 'owner_user_id': 'owner'})
    reserve = surface.reserve_outbound

    def reserve_after_authority_changes(*args):
        # Commit the competing change at the reservation boundary, then run
        # the real reservation transaction and verify it created a live hold.
        if barrier == 'stop':
            store.put(WORK_STOP_KEY, [job['id']])
        elif barrier == 'expiry':
            wire.now = budget.ledger.deadline + 1
        else:
            remove_slot(store, 'fixture')
        reservations = reserve(*args)
        assert len(reservations) == 1
        return reservations

    monkeypatch.setattr(surface, 'reserve_outbound', reserve_after_authority_changes)
    with pytest.raises(ApiError) as error:
        api.call(OPEN)
    assert not wire.sent
    assert error.value.code == code
    assert external_effect_events(store, job['id']) == ([], [])
    with store.db() as db:
        assert not db.execute('SELECT 1 FROM tool_events WHERE tool=?', (surface.OUTBOUND_POST,)).fetchone()


def test_first_recovered_turn_receives_previously_delivered_correction(setup):
    store, service, wire, script = setup
    mandate = 'Apply both changes.'
    correction = 'Leave the second change untouched. Read the current record instead.'
    job, budget, api = claim(setup, mandate)
    interrupted_dispatch(store, job, api)
    source = store.enqueue(correction, 'owner-correction', owner_typed=True)
    assert store.add_steer(job['id'], source, correction)
    assert store.claim_steers(job['id'], ledger=budget.ledger) == [correction]
    service.recover_interrupted_work()
    recovered = service._claim_queued_work()
    assert recovered['id'] == job['id']
    first_turn = []

    def observe(body):
        first_turn.append('\n'.join(message.get('content') or '' for message in body['messages']))
        action = READ if correction in first_turn[0] else CLOSE
        return {'tool_calls': [call('next', 'api_request', **action)]}

    script.messages.extend([observe, finish('done', 'next', summary='The current record was checked.')])
    assert service.work_executor._run_claimed(recovered)
    assert first_turn and mandate in first_turn[0] and correction in first_turn[0]
    assert wire.sent == ['open', 'state']


@pytest.mark.parametrize('resolution', ['applied', 'not-applied'])
def test_only_applied_reconciliation_allows_success(setup, resolution):
    store, service, wire, script = setup
    wire.lost = True
    wire.status = resolution
    work_id = store.enqueue('Apply the change and verify its outcome.', 'fixture-work', owner_typed=True)

    def verify(_body):
        _, [(receipt, _)] = external_effect_events(store, work_id)
        with store.db() as db:
            dispatched = db.execute('SELECT created FROM tool_events WHERE id=?', (receipt,)).fetchone()['created']
        wire.status_as_of = int(dispatched)
        return {'tool_calls': [call('verify', 'api_request', **READ, reconcile_receipt=str(receipt),
                                   receipt_ref_field='$.request_ref')]}

    def choose(context, candidates, _question):
        if context.purpose != 'effect-reconciled':
            return None
        response = json.loads(context.facts['authoritative_service_response'])
        return SelectionDecision(OUTCOME_DECIDED, response['status'], candidates, fixture_confidence())

    def judge(context, _question):
        if context.purpose == 'goal-reached':
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        return None

    service.use_decision_engine(FixtureDecisionEngine(judge=judge, choose=choose))
    script.messages.extend([{'tool_calls': [call('open', 'api_request', **OPEN)]}, verify,
                            finish('done', 'verify', summary='The change was applied.')])
    assert service.run_one()
    [(_, receipt)], [] = external_effect_events(store, work_id)
    assert receipt['resolution'] == resolution
    assert wire.sent == ['open', 'state']
    assert (store.job(work_id)['status'] == 'succeeded') is (resolution == 'applied')


@pytest.mark.parametrize('resolved', [True, False])
def test_unknown_dispatch_requires_matching_authoritative_read(setup, resolved):
    store, service, wire, _ = setup
    job, old, api = claim(setup, 'Read the service outcome before continuing')
    wire.lost = True
    with pytest.raises(ApiError):
        interrupted_dispatch(store, job, api)
    _, [(receipt, _)] = external_effect_events(store, job['id'])
    service.recover_interrupted_work()
    assert service._claim_queued_work()['id'] == job['id']
    budget = service.work_budget(job['id'])
    judged = []
    def choose(context, candidates, _question):
        assert context.purpose == 'effect-reconciled'
        judged.append(context.facts['request_reference'])
        response = json.loads(context.facts['authoritative_service_response'])
        choice = 'applied' if response['status']=='applied' else 'unknown'
        return SelectionDecision(OUTCOME_DECIDED, choice, candidates, fixture_confidence())
    judgment = ConversationJudgments(FixtureDecisionEngine(choose=choose))
    api = ApiRequests(store, job['id'], ledger=budget.ledger, transport=wire, clock=lambda: wire.now,
                      reconcile=judgment.effect_reconciled)
    with pytest.raises(ApiError) as error:
        api.call(CLOSE)
    assert error.value.code == 'effect_unknown'
    save_slot(store, 'other', ['other.example'], 'fixture-other-key')
    with pytest.raises(ApiError) as error:
        api.call({**CLOSE, 'slot': 'other', 'url': 'https://other.example/close'})
    assert error.value.code == 'effect_unknown'
    api.call(READ)  # An ordinary read cannot settle a dispatch.
    assert service._work_has_unknown_effect(job['id'])
    wire.reference = 'unrelated-dispatch'
    wrong = api.call({**READ, 'reconcile_receipt': str(receipt), 'receipt_ref_field': '$.request_ref'})
    assert 'reconciled_receipt' not in wrong['provenance']
    assert not judged and service._work_has_unknown_effect(job['id'])
    wire.reference = None
    if not resolved:
        wire.status = 'pending'
    proof = api.call({**READ, 'reconcile_receipt': str(receipt), 'receipt_ref_field': '$.request_ref'})
    assert judged == [wire.first_ref]
    assert proof['provenance'].get('reconciled_receipt') == (receipt if resolved else None)
    assert service._work_has_unknown_effect(job['id']) is not resolved
    if resolved:
        with pytest.raises(ApiError) as error:
            api.call(OPEN)
        assert error.value.code == 'api_effect_replayed'
        api.call(CLOSE)
        assert api.call(READ)['data']['state'] == 'closed'
        assert wire.sent == ['open','state','state','state','close','state']
    else:
        with pytest.raises(ApiError) as error:
            api.call(CLOSE)
        assert error.value.code == 'effect_unknown'
        assert wire.sent == ['open','state','state','state']


@pytest.mark.parametrize('service_time, reconciled', [
    (1_795_000_000, True),
    (1_794_999_999, False),
    (1_795_000_000_120, True),
    (1_795_000_000_119, False),
    (1_795_000_000.12, True),
    ('2026-11-18T11:06:40Z', True),
    ('2026-11-18T11:06:40.000Z', False),
    ('2026-11-18T11:06:40.120Z', True),
    ('2026-11-18T11:06:40.12Z', True),
    ('2026-11-18T11:06:40.119Z', False),
])
def test_reconciliation_compares_at_the_service_timestamp_precision(setup, service_time, reconciled):
    store, _, wire, _ = setup
    wire.now = 1_795_000_000.1207
    job, _, api = claim(setup, 'Determine the outcome of the dispatch')
    wire.lost = True
    with pytest.raises(ApiError) as error:
        interrupted_dispatch(store, job, api)
    assert error.value.effect == 'unknown'
    _, [(receipt, _)] = external_effect_events(store, job['id'])

    wire.now = 1_795_000_001
    wire.status_as_of = service_time
    api.reconcile = lambda *args, **kwargs: 'applied'
    proof = api.call({**READ, 'reconcile_receipt': str(receipt), 'receipt_ref_field': '$.request_ref'})
    assert proof['provenance']['freshness'] == 'fresh'
    assert proof['provenance'].get('reconciled_receipt') == (receipt if reconciled else None)


class TestLineHandoff(_PieceCase):
    def test_piece_read_cannot_replace_ancestor_mutation_with_same_call_id(self):
        parent = self.running(self.line_row())
        result = self.schedule(parent)
        piece = self.piece_run(self.service.preparations.get(result['preparation_id']))
        with self.store.db() as db:
            for job, tool, status, detail in [
                (parent, 'browser_open', 'running', {'arguments': {'url': OPEN['url']}, 'declared_effect': 'mutate'}),
                (parent, 'browser_open', 'failed', {'effect': 'unknown', 'declared_effect': 'mutate'}),
                (piece, 'api_request', 'succeeded', {'declared_effect': 'read'}),
            ]:
                record = {'scope': 'main', 'call_id': 'reused-id', **detail}
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job['id'], tool, status, json.dumps(record), self.now))
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (parent['id'],))
        assert unknown_effect_targets(self.store, piece['id']) == [('page', HOST, frozenset({HOST}))]
        save_slot(self.store, 'fixture', [HOST], 'fixture-key')
        wire = Wire()
        with self.assertRaises(ApiError) as error:
            ApiRequests(self.store, piece['id'], transport=wire).call(CLOSE)
        assert error.exception.code == 'effect_unknown'
        assert not wire.sent

    def test_piece_runs_with_receipts_after_completed_ancestors_are_deleted(self):
        root = self.line_row(goal='Complete both changes and verify the final record.')
        parent = self.running(root)
        wire = Wire()
        save_slot(self.store, 'fixture', [HOST], 'fixture-key')
        ApiRequests(self.store, parent['id'], transport=wire).call(OPEN)
        middle = self.service.preparations.get(self.schedule(parent)['preparation_id'])
        self.now = middle['due_at']
        child = self.running(middle)
        next_piece = self.service.preparations.get(self.schedule(child)['preparation_id'])
        with self.store.db() as db:
            for row, job in ((root, parent), (middle, child)):
                db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_DELIVERED, row['id']))
                db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (job['id'],))
        self.service.preparations.delete(middle['id'])
        self.service.preparations.delete(root['id'])
        self.now = next_piece['due_at']
        work = self.piece_run(next_piece)
        self.script = [
            {'tool_calls': [call('repeat', 'api_request', **OPEN)]},
            {'tool_calls': [call('close', 'api_request', **CLOSE)]},
            {'tool_calls': [call('read', 'api_request', **READ)]},
            finish('done', 'close', 'read', summary='The changes are verified.'),
        ]
        with mock.patch('personal_agent.api_requests.http_request', wire):
            assert self.service.run_one()
        assert self.store.job(work['id'])['status'] == 'succeeded'
        assert any(step[3].get('code') == 'api_effect_replayed' for step in self.service.work_steps(work['id']))
        assert wire.sent == ['open', 'close', 'state']

    def test_piece_with_missing_historical_lineage_settles_as_unknown(self):
        root = self.line_row()
        parent = self.running(root)
        next_piece = self.service.preparations.get(self.schedule(parent)['preparation_id'])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (parent['id'],))
            # Older versions removed the row without retaining line linkage.
            db.execute('DELETE FROM preparations WHERE id=?', (root['id'],))
        self.now = next_piece['due_at']
        work = self.piece_run(next_piece)
        before = len(self.model_calls)
        assert self.service.run_one()
        final = self.store.job(work['id'])
        assert final['status'] == final['effect_state'] == 'unknown'
        assert len(self.model_calls) == before

    def test_piece_inherits_receipt_and_original_mandate(self):
        mandate = 'Complete both changes and verify the final record.'
        parent = self.running(self.line_row(goal=mandate))
        wire = Wire()
        save_slot(self.store, 'fixture', [HOST], 'fixture-key')
        ApiRequests(self.store,parent['id'],transport=wire).call(OPEN)
        result = self.schedule(parent, goal='Read the record and finish the pending step.')
        piece = self.piece_run(self.service.preparations.get(result['preparation_id']))
        assert self.service.effect_mandate(piece, piece['message']) == mandate
        with self.assertRaises(ApiError) as error:
            ApiRequests(self.store,piece['id'],transport=wire).call(OPEN)
        assert error.exception.code == 'api_effect_replayed'
        assert wire.sent == ['open']

    def test_successor_keeps_owner_corrections_from_every_handoff(self):
        mandate = 'Complete both changes and verify the final record.'
        corrections = ['Use the revised record.', 'Include the final timestamp.', 'Report in UTC.']
        parent = self.running(self.line_row(goal=mandate))
        wire = Wire()
        save_slot(self.store, 'fixture', [HOST], 'fixture-key')
        ApiRequests(self.store, parent['id'], transport=wire).call(OPEN)

        for index, correction in enumerate(corrections):
            source = self.store.enqueue(correction, f'owner-steer-{index}', owner_typed=True)
            assert self.store.add_steer(parent['id'], source, correction)
            result = self.schedule(parent, goal=f'Pending step {index + 1}')
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='partial' WHERE id=?", (parent['id'],))
                db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (source,))
            row = self.service.preparations.get(result['preparation_id'])
            self.now = row['due_at']
            parent = self.piece_run(row) if index == len(corrections) - 1 else self.running(row)

        planned = self.service.work_orchestration(parent, parent['message'], [], {}, None)
        judged_goals = []

        def judge(context, _question):
            if context.purpose == 'goal-reached':
                judged_goals.append(context.facts['owner_request'])
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None

        self.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
        self.script = [
            {'tool_calls': [call('close', 'api_request', **CLOSE)]},
            {'tool_calls': [call('verify', 'api_request', **READ)]},
            finish('done', 'close', 'verify', summary='The final record is verified.'),
        ]
        with mock.patch('personal_agent.api_requests.http_request', wire):
            assert self.service.run_one()
        expected = '\n\n'.join((mandate, *corrections))
        assert judged_goals == [expected]
        assert planned.request == expected + '\n\n' + self.service.preparation_run_fact(parent)
        assert wire.sent == ['open', 'close', 'state']

    def test_line_worker_schedules_the_remaining_piece(self):
        mandate = 'Complete both changes and verify the final record.'
        job = self.piece_run(self.line_row(goal=mandate))
        wire = Wire()
        save_slot(self.store, 'fixture', [HOST], 'fixture-key')
        due = self.due_iso(600)
        self.script = [
            {'tool_calls': [call('open','api_request',**OPEN)]},
            {'tool_calls': [call('next','schedule_preparation',kind='prepare',due=due,
                                 goal='Finish the pending change and verify the final record.')]},
            finish('partial','open','next',summary='First change observed. The next step is scheduled.',status='partial'),
        ]
        with mock.patch('personal_agent.api_requests.http_request',wire):
            self.assertTrue(self.service.run_one())
        [piece] = self.pieces()
        self.assertEqual(piece['created_from'], job['id'])
        self.assertEqual(piece['due_at'], self.now + 600)
        self.assertEqual(self.store.job(job['id'])['status'], 'partial')
        self.assertEqual(wire.sent, ['open'])


def test_premature_reply_continues_in_the_same_work(setup):
    store, service, wire, script = setup
    mandate = 'Perform both changes, then verify the final record.'
    work_id = store.enqueue(mandate, 'fixture-work', owner_typed=True)
    verdicts = []

    def judge(context, _question):
        if context.purpose != 'goal-reached':
            return None
        assert context.facts['owner_request'] == mandate
        assert '"state": "observed"' in context.facts['observations']
        reached = wire.state == 'closed' and wire.sent[-1] == 'state'
        verdicts.append(reached)
        return BinaryDecision(OUTCOME_DECIDED, reached, fixture_confidence(), gap='Verify the final state.')

    service.decision_judge = ConversationJudgments(FixtureDecisionEngine(judge=judge))
    script.messages.extend([
        {'tool_calls': [call('open','api_request',**OPEN)]},
        {'content': 'The first change was observed.'},
        {'content': 'The first change was observed.'},
        {'tool_calls': [call('close','api_request',**CLOSE)]},
        {'tool_calls': [call('verify','api_request',**READ)]},
        finish('done','close','verify',summary='The changes and final state are verified.'),
    ])
    assert service.run_one()
    assert store.job(work_id)['status'] == 'succeeded', (wire.sent, verdicts, [(step[2], step[3].get('code'), step[4]) for step in service.work_steps(work_id)])
    assert verdicts == [False, True]
    assert wire.sent == ['open','close','state']


def test_plain_reply_shortfall_fences_browser_mutation_in_same_worker(setup, monkeypatch):
    store, service, wire, script = setup
    work_id = store.enqueue('Apply both changes and verify the state.', 'fixture-work', owner_typed=True)

    def judge(context, _question):
        if context.purpose == 'goal-reached':
            return BinaryDecision(OUTCOME_DECIDED, wire.state == 'closed', fixture_confidence(), gap='Finish the second step.')
        return None

    service.use_decision_engine(FixtureDecisionEngine(judge=judge))
    monkeypatch.setattr(service.browser_profile, 'driver_factory', lambda *args, **kwargs: lambda: None)
    browser = mock.Mock(return_value={'effect': 'changed'})
    monkeypatch.setattr('personal_agent.browser_session.BrowserSession.run', browser)
    script.messages.extend([
        {'tool_calls': [call('open', 'api_request', **OPEN)]},
        {'content': 'The first change was observed.'},
        {'content': 'The first change was observed.'},
        {'tool_calls': [call('browser', 'browser_open', url=OPEN['url'], effect='mutate')]},
        {'tool_calls': [call('close', 'api_request', **CLOSE)]},
        {'tool_calls': [call('verify', 'api_request', **READ)]},
        finish('done', 'close', 'verify', summary='Both changes were verified.'),
    ])
    assert service.run_one()
    browser.assert_not_called()
    assert any(step[3].get('code') == 'effect_route_unavailable' for step in service.work_steps(work_id))
    assert store.job(work_id)['status'] == 'succeeded'
    assert wire.sent == ['open', 'close', 'state']


def test_recovered_api_effect_holds_connector_mutation_but_allows_reviewed_read(setup):
    store, service, wire, _ = setup
    job, _, api = claim(setup, 'Apply both changes and verify the state')
    interrupted_dispatch(store, job, api)
    service.recover_interrupted_work()
    recovered = service._claim_queued_work()
    assert recovered['id'] == job['id']

    service.set_ai_connections({'enabled': True})
    owner_mcp.set_confirmed(store, 'codex', ['synthetic'])
    target = trust_record.connection('codex', 'owner-mcp', 'synthetic')
    trust_record.set_operation(store, target, 'apply_change', 'mutate')
    trust_record.set_operation(store, target, 'read_state', 'read')
    arguments = {'engine': 'codex', 'input': {'item': 'fixture'}}
    assert service.connector_permission(recovered, {**arguments, 'tool_name': 'mcp__synthetic__apply_change'})['behavior'] == 'deny'
    assert service.connector_permission(recovered, {**arguments, 'tool_name': 'mcp__synthetic__read_state'})['behavior'] == 'allow'
    assert external_effect_events(store, job['id'])[1] == []
    assert wire.sent == ['open']


def test_redelegated_work_refuses_browser_mutation_after_observed_api_dispatch(setup, monkeypatch):
    store, service, wire, script = setup
    work_id = store.enqueue('Perform both changes, then verify the final record.', 'fixture-work', owner_typed=True)
    briefs = iter(['Make the requested changes.', 'Complete the outstanding change and verify it.'])

    def judge(context, _question):
        if context.purpose == 'goal-reached':
            reached = wire.state == 'closed' and wire.sent[-1] == 'state'
            return BinaryDecision(OUTCOME_DECIDED, reached, fixture_confidence(), gap='Verify the final state.')
        return None

    service.use_decision_engine(FixtureDecisionEngine(
        judge=judge, structured=lambda *_: decided(plan('other', next(briefs)))))
    monkeypatch.setattr(service.browser_profile, 'driver_factory', lambda *args, **kwargs: lambda: None)
    browser_dispatch = mock.Mock(return_value={'effect': 'changed'})
    monkeypatch.setattr('personal_agent.browser_session.BrowserSession.run', browser_dispatch)
    script.messages.extend([
        {'tool_calls': [call('open', 'api_request', **OPEN)]},
        finish('first-claim', 'open', summary='The first change was observed.'),
        finish('second-claim', 'open', summary='The first change was observed.'),
        {'tool_calls': [call('repeat-in-browser', 'browser_open', url=OPEN['url'], effect='mutate')]},
        {'tool_calls': [call('close', 'api_request', **CLOSE)]},
        {'tool_calls': [call('verify', 'api_request', **READ)]},
        finish('done', 'close', 'verify', summary='The changes and final state are verified.'),
    ])
    assert service.run_one()
    assert [worker['attempt'] for worker in store.turn_provenance(work_id)['workers']] == [1, 2]
    browser_dispatch.assert_not_called()
    assert any(step[3].get('code') == 'effect_route_unavailable' for step in service.work_steps(work_id))
    assert wire.sent == ['open', 'close', 'state']
    assert store.job(work_id)['status'] == 'succeeded'


def test_shortfall_retains_pending_work_without_creating_a_schedule(setup):
    store, service, wire, script = setup
    work_id = store.enqueue('Make both changes and verify the final state.', 'fixture-work', owner_typed=True)

    def judge(context, _question):
        if context.purpose == 'goal-reached':
            return BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence(), gap='The final state is still pending.')
        return None

    service.decision_judge = ConversationJudgments(FixtureDecisionEngine(judge=judge))
    script.messages.extend([
        {'tool_calls': [call('open','api_request',**OPEN)]},
        finish('partial','open',summary='The first change was observed.',status='partial'),
    ])
    assert service.run_one()
    work = store.job(work_id)
    assert work['status'] == 'partial'
    assert 'Remaining work is pending' in work['response']
    assert wire.sent == ['open']
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM preparations').fetchone()[0] == 0


def test_unresolved_receipt_is_visible_in_the_terminal_work(setup):
    store, service, wire, script = setup
    wire.lost = True
    work_id = store.enqueue('Perform both changes.', 'fixture-work', owner_typed=True)
    script.messages.extend([
        {'tool_calls': [call('open','api_request',**OPEN)]},
        {'tool_calls': [call('close','api_request',**CLOSE)]},
        finish('partial',summary='The first dispatch has no confirmed outcome.',status='partial'),
    ])
    assert service.run_one()
    work = store.job(work_id)
    assert work['status'] == 'unknown' and work['effect_state'] == 'unknown'
    assert wire.sent == ['open']
