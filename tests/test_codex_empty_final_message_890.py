"""#890: codex-cli 0.167.2 completed turns may have no closing text.

Model-free evidence: the captured synthetic exec --json fixture, an injected
CLI runner, Still's own tool events, and the real preparation claim/settle path.
No model, CLI process, network or owner credentials are used.
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from personal_agent import preparations as prep
from personal_agent.bounded_execution import BoundedExecutionAdapter, ExecutionError
from personal_agent.messages import msg
from personal_agent.subscription_engines import SubscriptionEngines
from test_preparations import _WatchCase, watch_engine


STREAM = (Path(__file__).parent / 'fixtures' / 'codex_exec_empty_final_message.jsonl').read_text()


def stream(*records):
    return '\n'.join(json.dumps(record) for record in records)


def message(text, kind='item.completed'):
    return {'type': kind, 'item': {'type': 'agent_message', 'text': text}}


def test_captured_completed_turn_without_text_is_valid():
    assert BoundedExecutionAdapter._content('codex', STREAM) == ''


def test_empty_message_without_turn_completed_is_still_invalid():
    unfinished = '\n'.join(line for line in STREAM.split('\n') if '"type":"turn.completed"' not in line)
    with pytest.raises(ExecutionError, match='엔진 응답에 최종 텍스트 결과가 없습니다.'):
        BoundedExecutionAdapter._content('codex', unfinished)


def test_last_completed_message_is_the_final_text():
    records = [json.loads(line) for line in STREAM.split('\n') if line]
    records.insert(-1, message('closing text'))
    records.insert(-1, message('unfinished draft', 'item.updated'))
    assert BoundedExecutionAdapter._content('codex', stream(*records)) == 'closing text'


def test_empty_final_message_does_not_reuse_an_earlier_message():
    assert BoundedExecutionAdapter._content('codex', stream(
        message('earlier commentary'), message(''), {'type': 'turn.completed'})) == ''


def test_top_level_text_is_not_a_codex_answer():
    with pytest.raises(ExecutionError, match='엔진 응답에 최종 텍스트 결과가 없습니다.'):
        BoundedExecutionAdapter._content('codex', stream({'type': 'turn.started', 'content': 'invented', 'output': 'invented'}))


@pytest.fixture
def case():
    case = _WatchCase('runTest')
    case.setUp()
    case.judge = watch_engine([False])
    case.service.use_decision_engine(case.judge)
    yield case
    case.doCleanups()


def configure(case, events):
    """Inject only the CLI runner; tool records belong to Still, not stdout."""
    home = case.root.parent / 'codex-home'
    home.mkdir()
    results = []

    def runner(argv, **kwargs):
        # These are the same completed bridge records the CLI route consumes.
        for name, status, evidence in events:
            record_event(case, case.service.current_work_id, name, status, evidence)
        return SimpleNamespace(returncode=0, stdout=STREAM, stderr='')

    adapter = BoundedExecutionAdapter(finder=lambda _: '/fixture/codex', runner=runner,
                                      runtime_root=home / 'turns', codex_home=home)
    execute = adapter.execute

    def observed(*args, **kwargs):
        result = execute(*args, **kwargs)
        results.append(result)
        return result

    adapter.execute = observed
    adapter.login_status = lambda *args, **kwargs: {'state': 'signed-in'}
    case.service.execution_adapter = adapter
    case.service.subscription_engines = SubscriptionEngines(finder=lambda _: '/fixture/codex', clock=lambda: case.now)
    case.service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
    return results


def record_event(case, work, name, status, evidence):
    with case.store.db() as db:
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (work, name, status, json.dumps({'scope': 'subscription-mcp-bridge', 'host_action': name,
                                                   'evidence': evidence}), case.now))


def engine_events(case, work):
    with case.store.db() as db:
        return [(row['status'], json.loads(row['detail'])) for row in db.execute(
            "SELECT status,detail FROM tool_events WHERE job_id=? AND tool='subscription_engine' ORDER BY id", (work,))]


@pytest.mark.parametrize('events,key', [
    ([('save_note', 'succeeded', {'saved': True})], 'agent_runtime.note_saved'),
    ([('save_note', 'succeeded', {'saved': True}), ('list_notes', 'succeeded', {'count': 1})], 'agent_runtime.note_saved'),
    ([('schedule_preparation', 'succeeded', {'scheduled': True})], 'agent_runtime.preparation_recorded'),
])
def test_cli_answers_from_completed_effects(case, events, key):
    results = configure(case, events)
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work.')
    row = case.store.job(work)
    assert row['status'] == 'succeeded'
    assert row['response'] == msg(key)
    assert results[0].content == ''
    assert results[0].meta['final_text'] == 'absent'
    [(status, detail)] = [(s, d) for s, d in engine_events(case, work) if s != 'running']
    assert status == 'succeeded'
    assert (detail['final_text'], detail['reply']) == ('absent', 'recorded-effects')


@pytest.mark.parametrize('events', [
    [], [('list_notes', 'succeeded', {'count': 1})],
    [('save_note', 'failed', {'saved': True})],
    [('save_note', 'succeeded', {'saved': False})],
    [('save_note', 'succeeded', {'saved': 'true'})],
])
def test_cli_no_completed_effect_is_a_typed_failure(case, events):
    configure(case, events)
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work.')
    assert case.store.job(work)['status'] == 'failed'
    [(status, detail)] = [(s, d) for s, d in engine_events(case, work) if s != 'running']
    assert status == 'failed'
    assert detail['failure_class'] == 'no-final-text'
    assert detail['error'] == msg('agent_runtime.model_returned_no_answer')
    assert not case.store.job(work)['response']


def test_an_earlier_attempts_effect_cannot_supply_the_reply(case):
    configure(case, [])
    work = case.store.enqueue('Carry out the requested work.', 'earlier-effect')
    record_event(case, work, 'save_note', 'succeeded', {'saved': True})
    with patch('personal_agent.work_executor.BrowserRelay'):
        assert case.service.run_one()
    assert case.store.job(work)['status'] == 'failed'
    assert engine_events(case, work)[-1][1]['failure_class'] == 'no-final-text'


def test_silent_cli_watch_reaches_judgment_and_settles_quiet(case):
    configure(case, [('save_note', 'succeeded', {'saved': True})])
    case.watched = case.watch()
    case.now = case.watched['due_at'] + 1
    assert case.tick()
    with patch('personal_agent.work_executor.BrowserRelay'):
        assert case.service.run_one()
    [run] = case.runs(case.watched['id'])
    assert (run['status'], run['response']) == ('succeeded', msg('agent_runtime.note_saved'))
    assert case.tick()
    row = case.service.preparations.get(case.watched['id'])
    assert (row['last_decision'], row['last_decision_reason']) == ('quiet', prep.REASON_JUDGED_NOT_NEEDED)
    [ask] = case.watch_asks()
    assert ask.facts['run_result'] == run['response']
    assert not case.sends()
