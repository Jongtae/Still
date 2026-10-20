"""#16 slices 2-3: what Still's completion checks add to one direct-API run, per request shape.

Measurement only; it changes no runtime behavior.  Each shape runs the real
``run_agent`` and ``Capabilities`` with scripted transports in place of the
provider and the public network:

* the worker model is a scripted compatible-API transport;
* the outcome judgment is the real ``ConversationJudgments`` over the real
  ``ModelDecisionEngine`` (the direct-API judgment adapter, one model tool call
  per judgment), whose transport is scripted too, so every judgment request
  that would reach a provider is counted;
* the public network is a fake wire.

Every worker request is attributed to the first check the loop itself had
sent before it, because without that check the run would have ended at the
reply that triggered it:

* ``checked_direct`` - requests after the "Execution check" system message
  (a plain reply before any tool ran);
* ``completion_turn`` - requests after ``COMPLETION_CHECK`` (a plain reply
  after tools ran, before any completion claim);
* ``goal_judgment`` - requests after a ``finish`` claim the judgment rejected
  (``goal_not_observed``), plus every judgment request;
* ``base`` - everything else, i.e. the requests the shape needs without these
  checks.

Evidence class: model-free fixture measurement.  Call and event counts are
exact for these scripts.  Request sizes are character counts of the fixture
requests (the real policy prompt and tool definitions, fixture history), an
indication of what each added request re-sends, not tokens.  Wall-clock time
with scripted transports means nothing and is not reported.

Run: ``PYTHONPATH=src python3 scripts/dev/measure_completion_checks.py``.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from personal_agent.agent_runtime import COMPLETION_CHECK, Capabilities, WorkBudget, run_agent
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import ModelDecisionEngine
from personal_agent.providers import ModelAdapter, ProviderError
from personal_agent.quickstart_store import QuickStore

CFG = {'provider': 'compatible', 'endpoint': 'https://openrouter.ai/api/v1', 'model': 'fixture-model'}
EXECUTION_CHECK_PREFIX = 'Execution check:'
CATEGORIES = ('base', 'checked_direct', 'completion_turn', 'goal_judgment')


def call(ident, name, **args):
    return {'id': ident, 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}


def finish(ident, *refs, summary='done', status='done'):
    return {'tool_calls': [call(ident, 'finish', status=status, evidence_refs=list(refs), summary=summary)]}


def decide(answer, gap=''):
    """One scripted ``decide`` tool call of the judgment model."""
    return {'tool_calls': [{'id': 'd', 'function': {'name': 'decide', 'arguments': json.dumps(
        {'answer': answer, 'gap': gap, 'note': '', 'confidence': 0.95})}}]}


class Transport:
    """A compatible-API transport answering from a list of scripted messages; keeps every request body."""

    def __init__(self, *messages):
        self.messages = list(messages)
        self.bodies = []

    def __call__(self, url, body, headers=None, timeout=60):
        self.bodies.append(json.loads(json.dumps(body)))  # the loop mutates its message list
        if not self.messages:
            raise AssertionError('the shape asked for more model requests than it scripted')
        return {'choices': [{'message': self.messages.pop(0)}]}


class Wire:
    """The public network: a search answers unless its query is in ``failing``."""

    def __init__(self, failing=()):
        self.failing = set(failing)

    def execute(self, plan):
        query = plan.get('query')
        if query in self.failing:
            raise ProviderError('web search failed')
        return {'tool': 'web_search', 'query': query, 'retrieved_at': 1,
                'results': [{'title': query, 'url': 'https://result.example/', 'snippet': query}],
                'sources': ['https://result.example/']}


def check_in(message):
    """The check this loop-sent message belongs to, or None."""
    content = message.get('content')
    if message.get('role') == 'system' and str(content or '').startswith(EXECUTION_CHECK_PREFIX):
        return 'checked_direct'
    if message.get('role') == 'system' and content == COMPLETION_CHECK:
        return 'completion_turn'
    if message.get('role') == 'tool':
        try:
            reply = json.loads(content or '{}')
        except (TypeError, ValueError):
            return None
        if isinstance(reply, dict) and reply.get('code') == 'goal_not_observed':
            return 'goal_judgment'
    return None


def attribute(body):
    """Which check (if any) this worker request follows.

    The first check the loop sent in this run is the cause of every later
    request: without it the run would have ended at the reply that triggered it.
    """
    for message in body.get('messages') or []:
        category = check_in(message)
        if category:
            return category
    return 'base'


#: name -> (what it stands for, worker script, judgment script or None for no judgment route, failing queries)
SHAPES = {
    'plain_reply': (
        'Plain conversational reply, no tool needed.',
        [{'content': 'Hello.'}, {'content': 'Hello.'}], [], ()),
    'plain_draft_then_tool': (
        'First reply claims an action no tool ran; after the execution check the model saves the note.',
        [{'content': 'Saved.'}, {'tool_calls': [call('1', 'save_note', content='buy milk')]}, finish('f', '1', summary='Saved.')],
        [decide(True)], ()),
    'tool_then_finish': (
        'One tool call, then a completion claim citing it.',
        [{'tool_calls': [call('1', 'web_search', query='alpha')]}, finish('f', '1', summary='Found alpha.')],
        [decide(True)], ()),
    'tool_then_plain_reply': (
        'One tool call, then a plain reply; the completion check asks for a claim and the model gives one.',
        [{'tool_calls': [call('1', 'web_search', query='alpha')]}, {'content': 'Found alpha.'}, finish('f', '1', summary='Found alpha.')],
        [decide(True)], ()),
    'tool_then_plain_reply_twice': (
        'One tool call, then a plain reply, and a plain reply again to the completion check.',
        [{'tool_calls': [call('1', 'web_search', query='alpha')]}, {'content': 'Found alpha.'}, {'content': 'Yes.'}],
        [decide(True)], ()),
    'failed_step_then_reply': (
        'The only tool call fails; the model then replies plainly.',
        [{'tool_calls': [call('1', 'web_search', query='alpha')]}, {'content': 'The search failed.'}],
        [decide(False, 'nothing was found')], ('alpha',)),
    'judgment_rejects_once': (
        'One tool call; the first claim is judged not shown, the model searches again and claims again.',
        [{'tool_calls': [call('1', 'web_search', query='alpha')]}, finish('f1', '1', summary='Found.'),
         {'tool_calls': [call('2', 'web_search', query='alpha details')]}, finish('f2', '1', '2', summary='Found.')],
        [decide(False, 'the details are missing'), decide(True)], ()),
    'tool_then_finish_no_judgment_route': (
        'One tool call and a claim on an installation with no judgment route configured.',
        [{'tool_calls': [call('1', 'web_search', query='alpha')]}, finish('f', '1', summary='Found alpha.')],
        None, ()),
}


def measure_shape(name, root):
    """Run one shape; return its counts per category and its outcome."""
    _about, worker_script, judge_script, failing = SHAPES[name]
    worker = Transport(*worker_script)
    judge = Transport(*(judge_script or []))
    events, audit = [], []

    def record(tool, status, detail):
        events.append((tool, status))

    route = (lambda: (dict(CFG), '')) if judge_script is not None else (lambda: None)
    engine = ModelDecisionEngine(ModelAdapter(judge), route, audit=audit.append)
    budget = WorkBudget()
    store = QuickStore(Path(root) / name)
    caps = Capabilities(store, ModelAdapter(worker), CFG, '', 'job', record, network=Wire(failing),
                        judgments=ConversationJudgments(engine), budget=budget)
    result = run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': 'request'}], '', caps, record)
    if worker.messages or judge.messages:
        raise AssertionError(f'{name}: scripted requests were left unused')

    worker_calls = {category: 0 for category in CATEGORIES}
    request_chars = {category: 0 for category in CATEGORIES}
    for body in worker.bodies:
        category = attribute(body)
        worker_calls[category] += 1
        request_chars[category] += len(json.dumps(body, ensure_ascii=False))
    judge_chars = sum(len(json.dumps(body, ensure_ascii=False)) for body in judge.bodies)
    rejected = sum(1 for tool, status in events if (tool, status) == ('model', 'claim_rejected'))
    added = {
        'checked_direct': {'worker_calls': worker_calls['checked_direct'], 'judgment_calls': 0,
                           'events': worker_calls['checked_direct'], 'request_chars': request_chars['checked_direct']},
        'completion_turn': {'worker_calls': worker_calls['completion_turn'], 'judgment_calls': 0,
                            'events': worker_calls['completion_turn'], 'request_chars': request_chars['completion_turn']},
        # A judgment adds its request, its decision-audit record, and per rejection one
        # claim_rejected event plus the worker request that follows it (with its responded event).
        'goal_judgment': {'worker_calls': worker_calls['goal_judgment'], 'judgment_calls': len(judge.bodies),
                          'events': rejected + worker_calls['goal_judgment'], 'audit_records': len(audit),
                          'request_chars': request_chars['goal_judgment'] + judge_chars},
    }
    tools = [tool for tool, status in events if tool != 'model' and status == 'succeeded']
    return {
        'shape': name,
        'base_worker_calls': worker_calls['base'],
        'base_request_chars': request_chars['base'],
        'worker_calls': len(worker.bodies),
        'judgment_calls': len(judge.bodies),
        'budget_turns': budget.turns_used,
        'added': added,
        'outcome': result.outcome,
        'judgment': None if result.judgment is None else str(result.judgment),
        'succeeded_tools': tools,
        'model_events': [status for tool, status in events if tool == 'model'],
    }


def measure():
    with tempfile.TemporaryDirectory() as root:
        return [measure_shape(name, root) for name in SHAPES]


def table(rows):
    lines = ['| shape | base worker calls | +checked_direct | +completion turn | +goal judgment (worker + judgment) '
             '| total model requests | chars sent by added requests | outcome |',
             '|---|---|---|---|---|---|---|---|']
    for row in rows:
        added = row['added']
        lines.append(f"| `{row['shape']}` | {row['base_worker_calls']} | {added['checked_direct']['worker_calls']} "
                     f"| {added['completion_turn']['worker_calls']} "
                     f"| {added['goal_judgment']['worker_calls']} + {added['goal_judgment']['judgment_calls']} "
                     f"| {row['worker_calls'] + row['judgment_calls']} "
                     f"| {sum(part['request_chars'] for part in added.values()):,} | {row['outcome']} |")
    return '\n'.join(lines)


if __name__ == '__main__':
    rows = measure()
    if '--json' in sys.argv[1:]:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        print(table(rows))
