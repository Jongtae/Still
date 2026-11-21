"""#1073: the home attention state counts only Works the Activity view lists.

The Diagnostics badge reads ``home()['state']``.  A Work that is interrupted or
has an unknown delivery outside Activity's window can't be opened by the owner,
so it must not hold the badge on.  The stored rows and the full recovery counts
(used by onboarding) are unchanged.
"""
import pytest

from personal_agent.decision import FixtureDecisionEngine
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore


@pytest.fixture
def service(tmp_path):
    store = QuickStore(tmp_path / 'state')
    store.JOBS_WINDOW = 2
    service = AgentService(store)
    service.use_decision_engine(FixtureDecisionEngine())
    return service


def _work(store, n, created, status='succeeded', delivery='sent'):
    work = store.enqueue(f'request {n}', f'key-{n}')
    work = work['id'] if isinstance(work, dict) else work
    with store.db() as db:
        db.execute('UPDATE jobs SET status=?, delivery=?, created=? WHERE id=?', (status, delivery, created, work))
    return work


def _unknown_notification(store, work):
    with store.db() as db:
        db.execute("INSERT INTO telegram_notifications(id,job_id,chat_id,generation,kind,state,created)"
                   " VALUES (?,?,1,'g','browser_approval_needed','unknown',1)", ('n-' + work, work))


def test_records_outside_the_activity_window_do_not_raise_attention(service):
    store = service.store
    old_interrupted = _work(store, 1, 100, status='interrupted')
    old_unknown = _work(store, 2, 200, delivery='unknown')
    _unknown_notification(store, old_unknown)
    _work(store, 3, 300)
    _work(store, 4, 400)

    assert {job['id'] for job in store.jobs()}.isdisjoint({old_interrupted, old_unknown})
    assert store.recovery_summary() == {'interrupted_jobs': 1, 'uncertain_deliveries': 1, 'uncertain_notifications': 1}
    assert store.recovery_summary(recent=True) == {'interrupted_jobs': 0, 'uncertain_deliveries': 0,
                                                   'uncertain_notifications': 0}
    assert service.home()['state'] == 'ready'


@pytest.mark.parametrize('status,delivery,notification', [
    ('interrupted', 'sent', False),
    ('succeeded', 'unknown', False),
    ('succeeded', 'sent', True),
])
def test_a_listed_record_still_raises_attention(service, status, delivery, notification):
    store = service.store
    _work(store, 1, 100, status='interrupted')
    _work(store, 2, 200)
    recent = _work(store, 3, 300, status=status, delivery=delivery)
    if notification:
        _unknown_notification(store, recent)

    assert recent in {job['id'] for job in store.jobs()}
    assert any(store.recovery_summary(recent=True).values())
    assert service.home()['state'] == 'attention'
