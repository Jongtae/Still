"""#1071 (owner decision 2026-11-21): the outcome judgment asks for the route's most capable qualified model.

Evidence class: deterministic unit tests through a fake provider transport and a capturing
engine.  No live provider is called; nothing here claims a model is calibrated.
"""
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import (OUTCOME_DECIDED, OUTCOME_UNAVAILABLE, BinaryDecision, DecisionContext,
                                     DecisionPolicy)
from personal_agent.decision_routes import strongest_qualified
from personal_agent.providers import ProviderError
from test_decision_engine import OPENAI, ScriptedTransport, engine_for, openai_tool_reply

ROUTE = {'requested_model': 'model-fast', 'qualification': {'compared': [
    {'model': 'model-fast', 'qualified': True}, {'model': 'model-old', 'qualified': False},
    {'model': 'model-strong', 'qualified': True}, {'model': 'model-biggest', 'qualified': False}]}}


def test_strongest_qualified_is_the_last_qualified_candidate():
    assert strongest_qualified(ROUTE) == 'model-strong'


def test_no_stronger_model_without_another_qualified_candidate():
    only = {'requested_model': 'model-fast', 'qualification': {'compared': [
        {'model': 'model-fast', 'qualified': True}, {'model': 'model-strong', 'qualified': False}]}}
    assert strongest_qualified(only) is None
    assert strongest_qualified({'requested_model': 'model-fast', 'qualification': {}}) is None
    assert strongest_qualified(None) is None


def sent_model(transport, index=-1):
    return transport.calls[index]['body']['model']


def test_a_context_asking_for_it_uses_the_stronger_model_and_others_keep_the_route_model():
    yes = openai_tool_reply({'answer': True, 'confidence': 0.9})
    transport = ScriptedTransport(yes, yes)
    engine, audit = engine_for(transport, strongest_model='gpt-6.1-terra')
    assert engine.judge(DecisionContext('goal-reached', {'a': 'b'}, qualified_strongest=True), 'p').answer is True
    assert sent_model(transport) == 'gpt-6.1-terra' and audit[-1]['model'] == 'gpt-6.1-terra'
    assert engine.judge(DecisionContext('turn-reaction', {'a': 'b'}), 'p').answer is True
    assert sent_model(transport) == OPENAI['model']


def test_a_refused_stronger_model_falls_back_to_the_route_model_once():
    transport = ScriptedTransport(ProviderError('model not found', status=404),
                                  openai_tool_reply({'answer': False, 'confidence': 0.9}))
    engine, _ = engine_for(transport, strongest_model='gpt-6.1-terra')
    decision = engine.judge(DecisionContext('goal-reached', {'a': 'b'}, qualified_strongest=True), 'p')
    assert decision.outcome == OUTCOME_DECIDED and decision.answer is False
    assert [call['body']['model'] for call in transport.calls] == ['gpt-6.1-terra', OPENAI['model']]


def test_an_unavailable_stronger_model_is_not_retried():
    transport = ScriptedTransport(ProviderError('overloaded', status=529))
    engine, _ = engine_for(transport, strongest_model='gpt-6.1-terra')
    decision = engine.judge(DecisionContext('goal-reached', {'a': 'b'}, qualified_strongest=True), 'p')
    assert decision.outcome == OUTCOME_UNAVAILABLE and len(transport.calls) == 1


class _Capture:
    def __init__(self):
        self.contexts = []

    def judge(self, context, proposition):
        self.contexts.append(context)
        return BinaryDecision(OUTCOME_UNAVAILABLE)


def test_the_goal_judgment_asks_for_the_stronger_model():
    engine = _Capture()
    ConversationJudgments(engine, DecisionPolicy()).goal_reached('Find the date.', 'web_search: found it', answer='It is Sunday.')
    assert engine.contexts and all(context.purpose == 'goal-reached' and context.qualified_strongest
                                   for context in engine.contexts)
