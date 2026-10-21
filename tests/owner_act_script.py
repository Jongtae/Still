"""A scripted owner-act judgment for model-free tests (#172).

``ConversationJudgments.owner_act_answer`` asks the DecisionEngine one
``structured`` question over the owner's act and the open items.  These
helpers stand in for that engine only: a rule maps ``(act, items)`` to an
answer, and the real judgment, policy, collector and settle paths run.  The
words in ``literal`` are this fixture's, not Still's; Still reads no word list.
"""
import re

from personal_agent.decision import OUTCOME_DECIDED, OUTCOME_UNAVAILABLE, StructuredDecision, fixture_confidence

#: What this fixture reads as a plain yes or no (a scripted model, not a Still rule).
YES = frozenset({'승인', 'approve', 'yes', '네', '응', '넵', 'ok', 'reaction: 👍'})
NO = frozenset({'취소', 'cancel', 'no', '아니', 'reaction: 👎'})


def items_of(context):
    """``[{id, kind, text}]`` of the open items the judgment was shown, in order."""
    listed = context.facts.get('open_items', '')
    items = []
    for block in listed.split('\n\nid: '):
        block = block if block.startswith('id: ') else 'id: ' + block
        ident = re.match(r'id: (\S+)', block)
        kind = re.search(r'\nkind: (\S+)', block)
        text = block.split('shown text:\n', 1)[1] if 'shown text:\n' in block else ''
        if ident and kind:
            items.append({'id': ident.group(1), 'kind': kind.group(1), 'text': text})
    return items


def literal(act, items):
    """The newest item, approved by a fixture yes and declined by a fixture no; anything else is unrelated."""
    value = act.strip().casefold()
    if value in YES:
        return items[0]['id'], 'approve', ''
    if value in NO:
        return items[0]['id'], 'decline', ''
    return 'none', 'unrelated', ''


def answering(rule=literal, probability=1.0, asked=None):
    """A ``structured`` script answering ``owner-act`` contexts with ``rule``; None (unavailable) otherwise.

    ``rule(act, items)`` returns ``(item, answer, rest)`` or None (no answer).
    ``asked`` collects ``(act, items, target)`` per call.
    """
    def script(context, question, schema):
        if context.purpose != 'owner-act':
            return None
        items = items_of(context)
        act = context.facts['owner_message']
        if asked is not None:
            asked.append((act, items, context.facts.get('act_target')))
        answer = rule(act, items)
        if answer is None:
            return None
        item, verdict, rest = answer
        return StructuredDecision(OUTCOME_DECIDED, {'item': item, 'answer': verdict, 'rest': rest},
                                  fixture_confidence(probability))
    return script


def with_owner_acts(engine, rule=literal, probability=1.0, asked=None):
    """Make a ``FixtureDecisionEngine`` answer the owner-act judgment too; every other call is unchanged."""
    original = engine.structured
    ours = answering(rule, probability, asked)

    def structured(context, question, schema, valid=None):
        if context.purpose != 'owner-act':
            return original(context, question, schema, valid)
        if not context.admit():
            return StructuredDecision(OUTCOME_UNAVAILABLE)
        engine.asked.append(('structured', context, question, schema))
        return ours(context, question, schema) or StructuredDecision(OUTCOME_UNAVAILABLE)
    engine.structured = structured
    return engine
