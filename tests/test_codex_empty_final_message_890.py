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
from personal_agent.agent_runtime import Capabilities, MEMORY_OWNER, SETTINGS_PENDING, WORKSPACE_REFERENCE_PREFIX, evidence_summary
from personal_agent.bounded_execution import BoundedExecutionAdapter, ExecutionError
from personal_agent.file_workspace import FileWorkspace
from personal_agent.messages import msg
from personal_agent.owner_forget import OwnerForget
from personal_agent.subscription_engines import SubscriptionEngines
from test_preparations import SECRET, _WatchCase, watch_engine


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
        for name, status, evidence in (events() if callable(events) else events):
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


def save_note(case, text):
    """Use Still's real save path, then retain only its bridge evidence."""
    tools = Capabilities(case.store, None, {}, None, case.service.current_work_id, lambda *args: None)
    saved = tools.execute('save_note', {'content': text})
    return 'save_note', 'succeeded', evidence_summary('save_note', saved)


def test_cli_reply_includes_the_stored_note_and_records_its_source(case):
    text = '검사 결과: 새 변경 사항이 있어요. /Users/owner/reports/check.md'
    events = []

    def saved_events():
        event = save_note(case, text)
        # Neither recorded arguments nor tool-result prose supply the text.
        event[2]['content'] = 'This is not the saved content.'
        events.append(event)
        return [event, ('list_notes', 'succeeded', {'count': 1})]

    configure(case, saved_events)
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work.')
    assert case.store.job(work)['response'] == msg('agent_runtime.note_saved') + '\n\n' + text
    detail = engine_events(case, work)[-1][1]
    assert detail['reply'] == 'recorded-effects'
    assert detail['reply_content'] == 'saved-record'
    assert case.service.personal_item('note', events[0][2]['id'])['content'] == text


def test_cli_missing_saved_note_keeps_only_the_receipt(case):
    events = []
    configure(case, events)
    # First prove this receipt resolves its exact saved row; then remove it.
    work = case.store.enqueue('Carry out the requested work.', 'missing-note-first')
    tools = Capabilities(case.store, None, {}, None, work, lambda *args: None)
    saved = tools.execute('save_note', {'content': 'The retained note.'})
    events.append(('save_note', 'succeeded', evidence_summary('save_note', saved)))
    with patch('personal_agent.work_executor.BrowserRelay'):
        assert case.service.run_one()
    assert saved['content'] in case.store.job(work)['response']
    with case.store.db() as db:
        db.execute('DELETE FROM notes WHERE id=?', (saved['id'],))
    # A different retained note must never stand in for the missing identity.
    tools.execute('save_note', {'content': 'An unrelated note.'})
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work again.')
    assert case.store.job(work)['response'] == msg('agent_runtime.note_saved')
    assert 'reply_content' not in engine_events(case, work)[-1][1]


def test_changed_saved_notes_in_silent_watch_runs_reach_judgment_again(case):
    texts = ['검사 결과: 첫 번째 변경이에요.', '검사 결과: 두 번째 변경이에요.']
    remaining = iter(texts)
    configure(case, lambda: [save_note(case, next(remaining))])
    case.judge = watch_engine([True, True])
    case.service.use_decision_engine(case.judge)
    case.watched = case.watch()
    for index in (1, 2):
        row = case.service.preparations.get(case.watched['id'])
        case.now = row['due_at'] + 1
        assert case.tick()
        with patch('personal_agent.work_executor.BrowserRelay'):
            assert case.service.run_one()
        case.service.deliver_one()
        assert case.tick()
        case.service.deliver_notification()
        row = case.service.preparations.get(case.watched['id'])
        assert row['last_decision_reason'] != prep.REASON_UNCHANGED
        assert row['last_decision'] == 'notify'
        assert len(case.watch_asks()) == index
        assert row['last_notified_digest'] == prep.text_digest(case.watch_asks()[-1].facts['run_result'])
    for text, ask in zip(texts, case.watch_asks()):
        assert text in ask.facts['run_result']
    assert len(case.sends()) == 2


def test_cli_saved_note_uses_the_existing_secret_pass_and_unreadable_fallback(case):
    text = f'Owner text: {SECRET}\n/Users/owner/private/check.md'
    configure(case, lambda: [save_note(case, text)])
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work.')
    response = case.store.job(work)['response']
    assert response == msg('agent_runtime.note_saved') + '\n\n' + case.service._redact_known_secrets(text)
    assert SECRET not in response
    assert '/Users/owner/private/check.md' in response
    with patch('personal_agent.work_executor.BrowserRelay'), patch.object(
            case.service, 'personal_item', side_effect=OSError('unreadable')):
        work = case.web_turn('Carry out the requested work again.')
    assert case.store.job(work)['response'] == msg('agent_runtime.note_saved')
    assert 'reply_content' not in engine_events(case, work)[-1][1]


def test_cli_reads_saved_memory_by_id_and_does_not_reuse_forgotten_content(case):
    saved = case.store.save_memory('fixture.check', 'The stored memory result.')
    configure(case, [('save_memory', 'succeeded', evidence_summary('save_memory', saved))])
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work.')
    assert case.store.job(work)['response'] == msg('agent_runtime.remembered') + '\n\n' + saved['content']
    assert engine_events(case, work)[-1][1]['reply_content'] == 'saved-record'
    OwnerForget(case.store).forget(MEMORY_OWNER, 'memory:' + saved['id'])
    case.store.save_memory('fixture.other', 'An unrelated memory.')
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work again.')
    assert case.store.job(work)['response'] == msg('agent_runtime.remembered')
    assert 'reply_content' not in engine_events(case, work)[-1][1]


@pytest.mark.parametrize('long', [False, True])
def test_cli_reads_the_exact_saved_result_with_existing_read_bound(case, long):
    reference = case.root.parent / 'reference'
    workspace = case.root.parent / 'results'
    reference.mkdir()
    workspace.mkdir()
    (reference / 'check.md').write_text('Source for this report.')
    files = FileWorkspace(case.store)
    files.configure([str(reference)], str(workspace))
    root = WORKSPACE_REFERENCE_PREFIX + files.status()['references'][0]['id']
    text = '한' * 23990 + SECRET + '\nRemaining report.' if long else 'The substantive saved report.'
    expected, events = [], []

    def saved_events():
        if events:
            return events
        tools = Capabilities(case.store, None, {}, None, case.service.current_work_id, lambda *args: None)
        tools.execute('read_file', {'root_id': root, 'path': 'check.md'})
        saved = tools.execute('save_result', {'title': 'Check', 'content': text})
        content = (workspace / saved['path']).read_text()
        expected.append(msg('agent_runtime.result_saved', path=saved['path']) + '\n\n' +
                        case.service._redact_known_secrets(content)[:24000] + '\n\n' +
                        msg('quickstart_service.file_saved', path=saved['path']))
        events.append(('save_result', 'succeeded', evidence_summary('save_result', saved)))
        return events

    configure(case, saved_events)
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work.')
    assert case.store.job(work)['response'] == expected[0]
    if long:
        assert '[redacted]' in case.store.job(work)['response']
        assert SECRET not in case.store.job(work)['response']
        assert 'Remaining report.' not in case.store.job(work)['response']
    assert engine_events(case, work)[-1][1]['reply_content'] == 'saved-record'
    # A file replaced after saving is no longer the content this receipt recorded.
    (workspace / events[0][2]['path']).write_text('A replacement must not be used.')
    # Keep this attempt's evidence pointing at the original saved record.
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work again.')
    assert case.store.job(work)['response'] == msg('agent_runtime.result_saved', path=events[0][2]['path'])
    assert 'reply_content' not in engine_events(case, work)[-1][1]


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


def test_cli_applied_settings_are_not_a_pending_draft(case):
    # The real bridge's summary deliberately drops the tool's next_step.
    evidence = evidence_summary('settings_change', {
        'applied': True, 'category': 'preparations', 'setting': 'cancel',
        'next_step': msg('preparations.self_change_applied'),
    })
    assert 'next_step' not in evidence
    configure(case, [('settings_change', 'succeeded', evidence)])
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work.')
    row = case.store.job(work)
    assert row['status'] == 'succeeded'
    assert row['response'] != SETTINGS_PENDING
    assert row['response'] == msg('service.i_ve_already_applied_it')


@pytest.mark.parametrize('name,evidence,key,values', [
    ('settings_change', {'applied': True, 'next_step': 'A raw-result field is not a receipt.'},
     'service.i_ve_already_applied_it', {}),
    ('schedule_preparation', {'scheduled': True}, 'agent_runtime.preparation_recorded', {}),
    ('entrusted_line', {'saved': True}, 'phone_input.saved', {}),
    ('save_note', {'saved': True}, 'agent_runtime.note_saved', {}),
    ('save_result', {'saved': True, 'path': 'report.md'}, 'agent_runtime.result_saved', {'path': 'report.md'}),
    ('save_result', {'saved': True}, 'phone_input.saved', {}),
    ('save_memory', {'saved': True, 'state': 'current', 'id': 'm1'}, 'agent_runtime.remembered', {}),
    # Receipt rendering also covers other host actions without inventing a task branch.
    ('example_change', {'applied': True}, 'service.i_ve_already_applied_it', {}),
    ('example_schedule', {'scheduled': True}, 'agent_runtime.completion_scheduled', {}),
])
def test_cli_renders_typed_receipts_without_the_raw_result_renderer(case, name, evidence, key, values):
    configure(case, [(name, 'succeeded', evidence)])
    with patch('personal_agent.work_executor.BrowserRelay'), patch(
            'personal_agent.agent_runtime._fallback_text', side_effect=AssertionError('Evidence is not a raw result.')):
        work = case.web_turn('Carry out the requested work.')
    row = case.store.job(work)
    assert row['status'] == 'succeeded'
    assert row['response'] == msg(key, **values)


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
