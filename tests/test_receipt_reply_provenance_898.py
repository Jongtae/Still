"""#898: model-free reply provenance and deferred-delivery evidence.

Real executor, orchestrator, goal judgment and preparation settle with temporary
stores; only the CLI runner and decision/model transports are fixtures.
"""
from unittest.mock import patch

import pytest

import test_codex_empty_final_message_890 as empty
from test_codex_empty_final_message_890 import case, configure, engine_events, save_note
from test_orchestrator import decided, plan
from test_preparations import call
from personal_agent import preparations as prep
from personal_agent.conversation_handoff import ConversationJudgments, GOAL_REACHED_PROPOSITION
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from personal_agent.messages import msg


def inject_judge(case, source, delivery, worker='codex'):
    seen, order = [], []

    def judge(context, proposition):
        if context.purpose == 'goal-reached':
            seen.append((context, proposition))
            order.append('goal')
            facts = context.facts
            return BinaryDecision(OUTCOME_DECIDED,
                                  facts.get('reply_source') == source and facts.get('reply_delivery') == delivery,
                                  fixture_confidence())
        if context.purpose == 'watch-notification':
            order.append('delivery')
            return BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence())
        return None

    def structured(context, question, schema):
        if context.purpose == 'work-orchestration':
            return decided(plan(worker, 'Carry out the owner request.'))
        return None

    case.judge = FixtureDecisionEngine(judge=judge, structured=structured)
    case.service.use_decision_engine(case.judge)
    return seen, order


def assert_facts(seen, source, delivery):
    assert len(seen) == 1
    facts = seen[0][0].facts
    assert (facts['reply_source'], facts['reply_delivery']) == (source, delivery)
    return facts


def test_receipt_prompt_and_default_worker_facts():
    engine = FixtureDecisionEngine(judge=lambda *_: BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence()))
    assert ConversationJudgments(engine).goal_reached('request', 'observed', answer='text').outcome == 'yes'
    context = engine.asked[0][1]
    assert (context.facts['reply_source'], context.facts['reply_delivery']) == ('worker', 'to_owner')
    for rule in ('reply_source is still_receipt', 'not a message the worker chose to send',
                 'worker sending, restating or notifying', 'reply_delivery is decided_after_judgment',
                 'Still decides whether and what to send afterwards', 'judge as before'):
        assert rule in GOAL_REACHED_PROPOSITION


@pytest.mark.parametrize('source', ['still_receipt', 'worker'])
def test_cli_source_is_set_by_composer_even_for_identical_text(case, monkeypatch, source):
    text = 'The saved comparison.'
    reply = msg('agent_runtime.note_saved') + '\n\n' + text
    configure(case, lambda: [save_note(case, text)])
    if source == 'worker':
        # Same wording as Still's receipt, but actually written by the worker.
        monkeypatch.setattr(empty, 'STREAM', empty.stream(empty.message(reply), {'type': 'turn.completed'}))
    seen, _ = inject_judge(case, source, 'to_owner')
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Save the result.')
    facts = assert_facts(seen, source, 'to_owner')
    assert facts['reply'] == reply
    assert 'save_note' in facts['recorded_effects']
    assert case.store.job(work)['status'] == 'succeeded'
    assert case.store.job(work)['response'] == reply
    detail = engine_events(case, work)[-1][1]
    assert (detail.get('reply') == 'recorded-effects') == (source == 'still_receipt')


def test_no_text_no_effect_still_fails_then_worker_reply_keeps_defaults(case, monkeypatch):
    configure(case, [])
    seen, _ = inject_judge(case, 'worker', 'to_owner')
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Carry out the requested work.')
    assert case.store.job(work)['status'] == 'failed'
    assert engine_events(case, work)[-1][1]['failure_class'] == 'no-final-text'
    assert not case.store.job(work)['response']
    assert seen == []  # no invented receipt goes to judgment
    monkeypatch.setattr(empty, 'STREAM', empty.stream(empty.message('Worker text.'), {'type': 'turn.completed'}))
    with patch('personal_agent.work_executor.BrowserRelay'):
        work = case.web_turn('Reply with the result.')
    assert_facts(seen, 'worker', 'to_owner')
    assert case.store.job(work)['status'] == 'succeeded'


@pytest.mark.parametrize('route', ['cli', 'api'])
def test_when_needed_judges_before_settle_decides_delivery(case, route):
    text = 'The two lists match.'
    if route == 'cli':
        configure(case, lambda: [save_note(case, text)])
    else:
        case.script = [{'tool_calls': [call('save', 'save_note', content=text)]}, {'content': ''}, {'content': ''}]
    seen, order = inject_judge(case, 'still_receipt', 'decided_after_judgment',
                               worker='codex' if route == 'cli' else 'api')
    row = case.service.preparations.create(
        kind='prepare', goal='Compare the two lists, keep the result, and send nothing if they match.',
        due_at=case.now + 60, timezone='Asia/Seoul', recurrence=None, channel='web',
        created_from='settings', state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
        window=(600, case.now + 3600, 3), delivery_mode=prep.DELIVERY_WHEN_NEEDED)
    case.now = row['due_at'] + 1
    assert case.tick()
    with patch('personal_agent.work_executor.BrowserRelay'):
        assert case.service.run_one()
    facts = assert_facts(seen, 'still_receipt', 'decided_after_judgment')
    assert 'save_note' in facts['recorded_effects']
    assert 'save_note' in facts['observations']
    if route == 'cli':
        assert text in facts['reply']
    assert order == ['goal']
    assert case.service.preparations.get(row['id'])['state'] == prep.STATE_RUNNING
    [run] = case.runs(row['id'])
    assert run['status'] == 'succeeded'
    assert run['delivery'] == 'none'
    assert case.tick()  # the real settle path owns the later notification judgment
    assert order == ['goal', 'delivery']
    settled = case.service.preparations.get(row['id'])
    assert (settled['last_decision'], settled['last_decision_reason']) == ('quiet', prep.REASON_JUDGED_NOT_NEEDED)
    assert not case.sends()
    with case.store.db() as db:
        assert not db.execute('SELECT 1 FROM telegram_notifications').fetchall()


@pytest.mark.parametrize('source', ['still_receipt', 'worker'])
def test_direct_fallback_receipt_is_not_worker_text_on_owner_turn(case, source):
    seen, _ = inject_judge(case, source, 'to_owner', worker='api')
    reply = '' if source == 'still_receipt' else msg('agent_runtime.note_saved')
    case.script = [{'tool_calls': [call('save', 'save_note', content='A saved result.')]},
                   {'content': reply}, {'content': reply}]
    work = case.web_turn('Save the result.')
    facts = assert_facts(seen, source, 'to_owner')
    assert facts['reply'] == msg('agent_runtime.note_saved')
    assert 'save_note' in facts['recorded_effects']
    assert case.store.job(work)['status'] == 'succeeded'


@pytest.mark.parametrize('draft,source', [(None, 'still_receipt'), ('Worker text.', 'worker')])
def test_budget_end_retains_composer_provenance(draft, source):
    from personal_agent.agent_runtime import _budget_end
    result = _budget_end(RuntimeError('Stopped.'), [('save_note', {'saved': True})], 1, [], [],
                         {'provider': 'compatible'}, 'fixture', draft)
    assert result.reply_source == source
    assert result.outcome == 'partial'


@pytest.fixture
def line_case():
    import test_line_bundles_799 as bundles
    case = bundles.Bundles('runTest')
    case.setUp()
    yield case
    case.doCleanups()


def test_held_scheduled_run_is_judged_before_bundle_delivery(line_case):
    case = line_case
    _brief, row = case.line()
    assert case.helper.held_row(case.service.preparations.get(row['id']))
    seen, _ = inject_judge(case, 'worker', 'decided_after_judgment', worker='other')
    work = case.report(row, text='The held result.', deliver=False)
    facts = assert_facts(seen, 'worker', 'decided_after_judgment')
    assert facts['reply'] == 'The held result.'
    assert case.store.job(work)['delivery'] == 'none'
    assert case.bundles() == []


def test_ordinary_scheduled_run_is_judged_as_direct_delivery(line_case):
    case = line_case
    row = case.scheduled(channel='telegram')
    assert not case.helper.held_row(row)
    seen, _ = inject_judge(case, 'worker', 'to_owner', worker='other')
    work = case.report(row, text='The direct result.', deliver=False)
    facts = assert_facts(seen, 'worker', 'to_owner')
    assert facts['reply'] == 'The direct result.'
    assert case.store.job(work)['delivery'] == 'pending'
    case.telegram.clear()
    case.service.deliver_one()
    assert case.store.job(work)['delivery'] == 'sent'
    assert len(case.sends()) == 1
    assert 'The direct result.' in case.sends()[0]['text']


@pytest.mark.parametrize('when_needed', [False, True])
def test_owner_requested_held_run_is_judged_as_direct_delivery(line_case, when_needed):
    case = line_case
    _brief, row, job = case.request_held_run(when_needed=when_needed)
    assert case.helper.held_row(row)
    assert case.helper.owner_requested_held_run(job)
    seen, _ = inject_judge(case, 'worker', 'to_owner', worker='other')
    case.finish_owner_run(job)
    facts = assert_facts(seen, 'worker', 'to_owner')
    assert facts['reply'] == 'Owner requested result'
    assert case.store.job(job['id'])['delivery'] == 'sent'
    assert case.receipts() == []
