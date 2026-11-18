from dataclasses import dataclass
import json
import sqlite3
import time

import pytest

from personal_agent.agent_runtime import WORK_STOP_KEY
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.work_execution import WorkRun


CHECKPOINT = {
    'artifact': 'opaque-not-a-path',
    'received': 16,
    'total': 32,
    'revision': 1,
    'etag': '"source-revision"',
    'source_digest': 'a' * 64,
    'url_digest': 'b' * 64,
}


@dataclass
class CheckpointCase:
    service: AgentService
    work: dict
    budget: object

    @property
    def store(self):
        return self.service.store

    @property
    def id(self):
        return self.work['id']

    def recover(self):
        return self.store.recover(can_resume_checkpoint=self.service.can_resume_read_checkpoint)


@pytest.fixture
def case(tmp_path, monkeypatch):
    store = QuickStore(tmp_path)
    service = AgentService(store)
    monkeypatch.setattr(service, 'can_resume_read_checkpoint',
                        lambda work_id, _checkpoint: service.may_continue(work_id, 'resume')[0],
                        raising=False)
    work_id = store.enqueue('Analyze this data.', 'checkpoint-fixture', owner_typed=True)
    work = service._claim_queued_work()
    assert work['id'] == work_id
    budget = service.work_budget(work_id)
    budget.spend_turn()
    assert store.record_read_checkpoint(work_id, CHECKPOINT, epoch=budget.ledger.epoch)
    return CheckpointCase(service, work, budget)


def test_checkpoint_requeues_same_work_without_duplicate_claim_or_intake(case):
    old_epoch = case.budget.ledger.epoch
    assert case.recover() == []
    assert case.store.job(case.id)['status'] == 'queued'
    assert case.store.job(case.id)['delivery'] == 'none'
    assert case.store.read_checkpoint(case.id)['resume_pending']
    assert case.budget.ledger.lapse() == 'superseded'

    # A second restart while the continuation is queued does not fence it again.
    assert case.recover() == []
    claimed = case.service._claim_queued_work()
    assert claimed['id'] == case.id
    assert claimed['read_checkpoint_resume'] == old_epoch + 1
    assert case.service._claim_queued_work() is False
    later = case.service.work_budget(case.id)
    assert later.ledger.epoch == old_epoch + 1
    assert later.turns_used == 1
    assert later.ledger.counts()['deadline'] <= case.budget.ledger.deadline
    saved = case.store.read_checkpoint(case.id)
    assert saved['resumed'] and not saved['resume_pending']
    assert all(saved[key] == value for key, value in CHECKPOINT.items())
    with case.store.db() as db:
        assert db.execute("SELECT count(*) FROM messages WHERE job_id=? AND role='user'", (case.id,)).fetchone()[0] == 1

    slow = WorkRun(case.work, time.time())
    slow.work_budget = case.budget
    assert case.service._finish_work(slow) is False
    assert case.store.job(case.id)['status'] == 'running'
    assert not case.store.record_read_checkpoint(case.id, {**CHECKPOINT, 'received': 32, 'revision': 2}, epoch=old_epoch)
    assert case.store.record_read_checkpoint(case.id, {**CHECKPOINT, 'received': 32, 'revision': 2},
                                             epoch=later.ledger.epoch)


@pytest.mark.parametrize('barrier', ['stop', 'expired', 'unknown', 'unaccounted'])
def test_checkpoint_cannot_override_existing_recovery_boundaries(case, barrier):
    if barrier == 'stop':
        case.store.put(WORK_STOP_KEY, [case.id])
    elif barrier == 'expired':
        ledger = case.budget.ledger.counts()
        case.store.put(case.budget.ledger.key, {**ledger, 'deadline': time.time()-1})
    else:
        action = ('api_request', 'failed', {'effect': 'unknown'}) if barrier == 'unknown' else ('subscription_engine', 'running', {})
        with case.store.db() as db:
            tool, status, detail = action
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (case.id,tool,status,json.dumps(detail),time.time()))
        assert barrier in case.service.continuation_reasons(case.id, 'resume')
    case.recover()
    assert case.store.job(case.id)['status'] == 'interrupted'
    assert not case.store.read_checkpoint(case.id)['resume_pending']


def test_checkpoint_claim_rechecks_authority(case, monkeypatch):
    case.recover()
    assert case.store.job(case.id)['status'] == 'queued'
    monkeypatch.setattr(case.service, 'can_resume_read_checkpoint', lambda *_: False)
    assert case.service._claim_queued_work() is True
    assert case.store.job(case.id)['status'] == 'interrupted'
    assert not case.store.read_checkpoint(case.id)['resume_pending']


def test_checkpoint_validation_prevents_progress_regression_or_changed_source(case):
    epoch = case.budget.ledger.epoch
    assert case.store.record_read_checkpoint(case.id, CHECKPOINT, epoch=epoch)
    for delta in (
        {'artifact': '../escape'}, {'etag': '"another-representation"'},
        {'url_digest': 'c' * 64}, {'received': 8, 'revision': 2},
        {'received': 32}, {'received': 33, 'revision': 2},
    ):
        assert not case.store.record_read_checkpoint(case.id, {**CHECKPOINT, **delta}, epoch=epoch)


def test_delayed_claimant_cannot_attach_to_a_later_epoch(case):
    case.recover()
    stale = case.service._claim_queued_work()
    case.recover()
    current = case.service._claim_queued_work()
    assert current['read_checkpoint_resume'] != stale['read_checkpoint_resume']
    run = WorkRun(stale, time.time())
    assert case.service._route_work(run) is True
    assert run.work_budget is None
    assert case.service.work_budget(case.id).ledger.epoch == current['read_checkpoint_resume']


def test_failed_queue_transaction_rolls_back_fence_and_resume_marker(case):
    with case.store.db() as db:
        db.execute("CREATE TRIGGER fail_checkpoint_queue BEFORE UPDATE ON work_read_checkpoints "
                   "WHEN NEW.resume_pending=1 BEGIN SELECT RAISE(ABORT,'fixture queue failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='fixture queue failure'):
        case.recover()
    assert case.store.job(case.id)['status'] == 'running'
    assert case.budget.ledger.lapse() is None
    assert not case.store.read_checkpoint(case.id)['resume_pending']
