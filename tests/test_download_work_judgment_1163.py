"""A Work that downloaded a public file is judged like any other Work (#1163).

Model-free: the judgment engine is replaced at ``_evaluate_goal``; the store, the
read checkpoint, its admission and the one continuation rule run for real.
"""
import json
from unittest import mock

from personal_agent.agent_runtime import Capabilities, declared_effect, run_agent
from personal_agent.conversation_handoff import Judgment

from test_read_checkpoint_recovery_1008 import CHECKPOINT
from test_read_rejudgment_fencing_1007 import case, change  # noqa: F401 - pytest fixture


def record(case, tool, status, detail):
    with case.store.db() as db:
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (case.id, tool, status, json.dumps(detail), case.now[0]))


def call(case, call_id, action, arguments, evidence, *, status='succeeded'):
    """One call as the direct route records it: its running record, then its terminal record."""
    declared = declared_effect(action, arguments)
    record(case, action, 'running', {'scope': 'main', 'call_id': call_id, 'attempt': 1, 'host_action': action,
                                      'arguments': arguments, **declared})
    record(case, action, status, {'scope': 'main', 'call_id': call_id, 'attempt': 1, 'host_action': action,
                                  'evidence': evidence, **declared})


def read_the_line(case):
    """What a Work that read an entrusted line leaves: the list and read calls and the read record."""
    call(case, 'c-list', 'entrusted_line', {'action': 'list'}, {'lines': 2})
    call(case, 'c-read', 'entrusted_line', {'action': 'read', 'line': 'fixture-line'},
         {'line_id': 'fixture-line', 'version': 3, 'state': 'active'})
    record(case, 'entrusted_line_read', 'succeeded', {'evidence': {'line_id': 'fixture-line', 'version': 3}})


def download_and_run(case):
    call(case, 'c-get', 'public_download', {'url': '[masked]', 'operation': 'fetch', 'offset': 0},
         {'artifact': 'opaque-not-a-path', 'received': 32, 'total': 32, 'revision': 1})
    call(case, 'c-run', 'code_run', {'download_artifact': 'opaque-not-a-path', 'code': '[masked]'},
         {'exit_status': 0, 'timed_out': False, 'hosts': [], 'requests': []})
    # A second source in the same Work is refused before it runs.
    record(case, 'public_download', 'failed', {'scope': 'main', 'call_id': 'c-get2', 'attempt': 1,
                                               'error': 'This Work has a checkpoint for a different source.',
                                               'code': 'read_checkpoint_source', 'effect': 'none', 'ran': False})


def run_read_goal(case, records=None):
    """The direct route's run over the checkpointed Work (``test_read_rejudgment_fencing_1007``), records kept."""
    service = case.service
    adapter = mock.Mock()
    adapter.tool_turn.return_value = ({'content': 'Here is the comparison.'}, 'fixture')
    keep = (lambda *row: records.append(row)) if records is not None else (lambda *_: None)
    capabilities = Capabilities(case.store, adapter, {}, None, case.id, lambda *_: None,
                                budget=case.budget, judgments=service.decision_judge)
    with service.work_decision_scope(case.id, case.budget):
        return run_agent(
            adapter, {'provider': 'fixture'}, None, [{'role': 'user', 'content': case.work['message']}],
            '', capabilities, keep, definitions=[],
            read_progress=lambda: service.read_checkpoint_note(case.work, include_fresh=True),
            original_goal=case.work['message'])


def concluded(case):
    records = []
    result = run_read_goal(case, records)
    detail = next(json.loads(detail) for tool, status, detail in reversed(records)
                  if tool == 'model' and status == 'concluded')
    return result, detail


def test_download_work_that_read_a_line_is_judged(case):
    read_the_line(case)
    download_and_run(case)
    with mock.patch.object(case.service.decision_judge, '_evaluate_goal', return_value=Judgment('yes')) as evaluate:
        result = run_read_goal(case)
    evaluate.assert_called_once()
    assert result.goal_judgment == 'yes'
    assert result.outcome == 'succeeded'


def test_owner_local_reads_and_a_download_do_not_block_redelegation(case):
    read_the_line(case)
    download_and_run(case)
    assert case.service.continuation_reasons(case.id, 'redelegate') == ()


def test_a_line_write_is_still_a_change(case):
    # A changed line is no read: the declaration follows the action that ran, never the tool's name.
    # #1170: a re-delegation of the same Work revises it in place; a retry or steer still stops on it.
    from personal_agent.entrusted_lines import EntrustedLines
    line = EntrustedLines(case.service.store).open(case.id, task='fixture task', report_promise='fixture report')
    call(case, 'c-revise', 'entrusted_line', {'action': 'revise', 'line': line['line_id']},
         {'line_id': line['line_id'], 'version': line['version'], 'state': 'active'})
    for purpose in ('retry', 'steer'):
        assert case.service.continuation_reasons(case.id, purpose) == ('effect',)
    assert case.service.continuation_reasons(case.id, 'redelegate') == ()


def test_a_work_with_a_recorded_effect_is_still_judged(case):
    # The continuation rule decides whether a Work may be handed out again, not whether its reply is judged.
    call(case, 'c-revise', 'entrusted_line', {'action': 'revise', 'line': 'fixture-line'},
         {'line_id': 'fixture-line', 'version': 4, 'state': 'active'})
    with mock.patch.object(case.service.decision_judge, '_evaluate_goal', return_value=Judgment('yes')) as evaluate:
        result = run_read_goal(case)
    evaluate.assert_called_once()
    assert result.goal_judgment == 'yes'


def test_a_changed_read_input_is_still_judged_again_once(case):
    # INPUT_CHANGED keeps its design: new verified coverage during the judgment is judged once more on the new input.
    read_the_line(case)
    facts = []

    def evaluate(inputs, *_):
        facts.append(inputs)
        if len(facts) == 1:
            change(case, 'checkpoint')
        return Judgment('yes')

    with mock.patch.object(case.service.decision_judge, '_evaluate_goal', side_effect=evaluate):
        result = run_read_goal(case)
    assert len(facts) == 2
    assert '16 of 32 bytes verified' in facts[0]['continues']
    assert '24 of 32 bytes verified' in facts[1]['continues']
    assert result.goal_judgment == 'yes'


def test_input_changing_on_every_try_records_why_it_was_not_judged(case):
    progress = [16]

    def evaluate(inputs, *_):
        # More bytes are verified while each judgment runs.
        progress.append(progress[-1] + 4)
        assert case.store.record_read_checkpoint(
            case.id, {**CHECKPOINT, 'received': progress[-1], 'revision': len(progress)}, epoch=case.budget.ledger.epoch)
        return Judgment('yes')

    with mock.patch.object(case.service.decision_judge, '_evaluate_goal', side_effect=evaluate):
        result, detail = concluded(case)
    assert result.goal_judgment == 'unavailable'
    assert detail['judgment_reason'] == 'input_changed'


def test_a_judgment_that_raises_records_why_it_was_not_judged(case):
    with mock.patch.object(case.service.decision_judge, 'goal_reached', side_effect=RuntimeError('fixture')):
        result, detail = concluded(case)
    assert result.goal_judgment == 'unavailable'
    assert detail['judgment_reason'] == 'error:RuntimeError'


def test_a_declined_checkpoint_admission_records_why_it_was_not_judged(case):
    # A stale writer is still fenced; the record now says the checkpoint admission declined.
    with mock.patch.object(case.service.decision_judge, 'checkpoint_judgment', return_value=None), \
            mock.patch.object(case.service.decision_judge, '_evaluate_goal') as evaluate:
        result, detail = concluded(case)
    evaluate.assert_not_called()
    assert result.goal_judgment == 'unavailable'
    assert detail['judgment_reason'] == 'read_checkpoint'
