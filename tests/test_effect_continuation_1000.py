"""Model-free recovery through the production Work queue, receipt broker and judgment."""
import json
import time
from unittest import mock

import pytest

from personal_agent.agent_runtime import WORK_STOP_KEY, effect_call_ref, external_effect_events
from personal_agent.api_requests import ApiError, ApiRequests, remove_slot, save_slot
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, SelectionDecision, fixture_confidence
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.self_set_writes import set_allowance

from test_agency_loop import Script, call, finish
from test_line_pieces_938 import _PieceCase


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

    def __call__(self, method, url, headers, body, timeout):
        path = url.rsplit('/', 1)[-1]
        self.sent.append(path)
        if path == 'open':
            assert self.state == 'initial'
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
                                   'state': self.state, 'as_of': self.now}).encode()


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


class TestLineHandoff(_PieceCase):
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
