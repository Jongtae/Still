"""#1118: the outcome judgment reads the owner's message after the conversation, next to the reply.

Evidence class: deterministic unit test through a capturing engine; no model is called.
Whether the order keeps a model from reading a long conversation's language as the message's
is a live-model observation (replays on #1118), which this test does not claim.
"""
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import OUTCOME_UNAVAILABLE, BinaryDecision, DecisionPolicy


class _Capture:
    def __init__(self):
        self.contexts = []

    def judge(self, context, proposition):
        self.contexts.append(context)
        return BinaryDecision(OUTCOME_UNAVAILABLE)


def test_the_owner_request_follows_the_conversation_and_precedes_the_reply():
    engine = _Capture()
    ConversationJudgments(engine, DecisionPolicy()).goal_reached(
        "What's the weather like tomorrow?", 'weather: sunny', answer='Sunny.', conversation='owner: 안녕하세요',
        owner_context='profile: none', continues='none')
    order = list(engine.contexts[0].facts)
    assert order.index('recent_conversation') < order.index('owner_request') < order.index('reply')
    assert order.index('owner_context') < order.index('owner_request')
