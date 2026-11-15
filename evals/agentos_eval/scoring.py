"""Scoring one scenario run: deterministic checks plus the secretary-rubric judge.

A *run record* is what the solver collected from one sandbox::

    {'worker': 'codex', 'error': None,
     'turns': [{'say', 'note', 'sent_at', 'task_id', 'status', 'answer', 'answer_withheld',
                'timed_out', 'failure_class', 'error', 'events', 'elapsed'}],
     'diff': {'memories', 'candidates', 'preparations', 'current_state', 'settings_changed',
              'owner_model_changed'}}

Every failure is a ``{'kind', 'name', 'reason'}`` row.  ``kind`` and ``name``
form the root-cause cluster key the report groups by (a rubric dimension, a
deterministic check or an infrastructure failure), so twenty scenarios that
fail for one reason show up as one cluster, not twenty cases.
"""
import json
import math
import re

from .scenarios import RUBRIC
from .runner import delivered_answer

#: Terminal statuses that are not a failed outcome.
OK_STATUSES = frozenset({'succeeded', 'partial'})
ANSWER_CHARS = 6000
#: Work lifecycle rows in a turn's events: not tools, so the judge's tool list leaves them out.
WORK_EVENTS = frozenset({'orchestrator', 'subscription_engine'})
#: The current-context predicate of the running day summary (``personal_agent.current_context.SITUATION``).
SITUATION = 'situation'
JUDGE_MAX_TOKENS = 1500


def _texts(rows, *keys):
    for row in rows:
        for key in keys:
            value = row.get(key)
            if isinstance(value, str) and value:
                yield value


def _normalized(text):
    return ' '.join(str(text or '').split()).casefold()


def deterministic_checks(scenario, run):
    """``(checks, failures)``: check name -> bool, and the failure rows."""
    failures = []
    if observation_error(run):
        failures.append({'kind': 'infra', 'name': 'harness', 'reason': str(observation_error(run))[:200]})
        return {'harness': False}, failures
    checks = {}
    judgment = run.get('judgment') or {}
    if judgment.get('requalified') and not judgment.get('ready'):
        # The sandbox could not get the owner's Judgment AI qualified: the run
        # measured the fallback path, not what the owner gets.
        failures.append({'kind': 'infra', 'name': 'judgment_ai',
                         'reason': judgment.get('failure') or f"not ready after {judgment.get('waited')}s"})
    all_turns = run.get('turns') or []
    owner_steps = [(i, t) for i, t in enumerate(all_turns, 1) if t.get('kind') != 'wait_due']
    turns = [t for _, t in owner_steps]
    waits = [t for t in all_turns if t.get('kind') == 'wait_due']
    if any('wait_due' in t for t in scenario.get('turns', [])):
        checks['due_completed'] = (len(waits) == sum('wait_due' in t for t in scenario['turns'])
                                   and all(t.get('due_observation', {}).get('completed') for t in waits))
        if not checks['due_completed']:
            failures.append({'kind': 'check', 'name': 'due_completed', 'reason': 'Due postconditions/drain incomplete'})
    undelivered = [(index, turn) for index, turn in owner_steps
                   if turn.get('answer_withheld') or not (turn.get('answer') or '').strip()]
    checks['delivered'] = bool(turns) and not undelivered
    for index, turn in undelivered:
        reason = 'withheld' if turn.get('answer_withheld') else 'empty'
        failures.append({'kind': 'check', 'name': 'delivered', 'reason': f'turn {index}: {reason}'})
    allowed = OK_STATUSES | set(scenario['expect'].get('allow_status') or ())
    bad = []
    for index, turn in owner_steps:
        if turn.get('timed_out'):
            bad.append((index, 'timeout'))
        elif turn.get('status') not in allowed:
            detail = turn.get('failure_class') or turn.get('status') or 'unknown'
            bad.append((index, f"{turn.get('status')}/{detail}" if detail != turn.get('status') else detail))
    checks['not_failed'] = bool(turns) and not bad
    for index, reason in bad:
        failures.append({'kind': 'check', 'name': 'not_failed', 'reason': f'turn {index}: {reason}'})
    diff = run.get('diff') or {}
    groups = scenario['expect'].get('memory_any') or []
    if groups:
        stored = ' \n'.join(_texts([*diff.get('memories', []), *diff.get('candidates', [])],
                                   'content', 'value', 'memory_key')).casefold()
        missing = [group for group in groups if not any(item.casefold() in stored for item in group)]
        checks['memory'] = not missing
        for group in missing:
            failures.append({'kind': 'check', 'name': 'memory', 'reason': 'no new memory/candidate mentions '
                             + ' | '.join(group)})
    groups = scenario['expect'].get('current_state_any') or []
    if groups:
        # #392: a fact about today (a meal eaten, where the owner is) is a current-context hypothesis, not Memory.
        stored = ' \n'.join(_texts(diff.get('current_state', []), 'value')).casefold()
        missing = [group for group in groups if not any(item.casefold() in stored for item in group)]
        checks['current_state'] = not missing
        for group in missing:
            failures.append({'kind': 'check', 'name': 'current_state', 'reason': 'no new current-state note mentions '
                             + ' | '.join(group)})
    if scenario['expect'].get('preparation'):
        checks['preparation'] = bool(diff.get('preparations'))
        if not checks['preparation']:
            failures.append({'kind': 'check', 'name': 'preparation', 'reason': 'no reminder/preparation was created'})
    if scenario['expect'].get('no_memory_of_request'):
        # #846: a standing wish is a watch, not a memory; the request sentence must not become a value.
        said = {_normalized(turn.get('say')) for turn in turns if (turn.get('say') or '').strip()}
        said |= {_normalized(turn['say']) for turn in scenario.get('turns') or [] if turn.get('say')}
        stored = [_normalized(text) for text in _texts([*diff.get('memories', []), *diff.get('candidates', [])],
                                                       'content', 'value')]
        echoed = [text for text in stored if text in said]
        checks['no_memory_of_request'] = not echoed
        for text in echoed:
            failures.append({'kind': 'check', 'name': 'no_memory_of_request',
                             'reason': f'a new memory/candidate is the owner\'s request sentence: {text[:80]}'})
    return checks, failures


# -- judge --------------------------------------------------------------------
JUDGE_SYSTEM = (
    'You evaluate a personal AI secretary called Still. You see an owner scenario, the owner turns with the '
    'local time each was sent, the tools each turn actually ran (names and statuses only), the answers Still '
    'delivered and the owner-state changes it recorded. Judge only what is shown; do not assume a tool ran, '
    'a place exists or a price is right unless the tool list or the answer shows it. Still saw the real clock '
    'at the time sent: judge timing against that time, even when a situation note describes another time. '
    'Score each listed dimension 2 (meets), 1 (partly) or 0 (fails), or null when the conversation gives '
    'no occasion to show it. Reply with JSON only.')


def _transcript(scenario, run, labelled=False):
    lines = [f'Scenario: {scenario["title"]}', '']
    if scenario.get('good_secretary'):
        lines += ['What a good secretary would do:', scenario['good_secretary'], '']
    lines.append('Conversation:')
    for index, turn in enumerate(run.get('turns') or [], 1):
        if turn.get('note'):
            lines.append(f'[situation before turn {index}: {turn["note"]}]')
        if turn.get('kind') == 'wait_due':
            receipt = turn.get('due_observation') or {}
            lines.append(f'[step {index}: harness wait, no owner message; bN denotes messages during the wait, '
                         f'not before an owner-requested answer; status {turn.get("status")}]')
            for j, message in enumerate(turn.get('before_answer') or [], 1):
                lines.append(f'AGENTOS t{index}.b{j} during wait (at {message.get("at")}, '
                             f'id {message.get("delivery_id")}): {message.get("text", "")[:ANSWER_CHARS]}'
                             + (' [truncated]' if len(message.get('text', '')) > ANSWER_CHARS else ''))
            lines.append('[due evidence: ' + json.dumps({k: v for k, v in receipt.items()
                         if k not in ('messages', 'deliveries', 'events', 'preparations')}, ensure_ascii=False) + ']')
            continue
        sent = f' (sent {turn["sent_at"]})' if turn.get('sent_at') else ''
        lines.append(f'OWNER {index}{sent}: {turn.get("say", "")}')
        tools = [f'{event["tool"]} {event.get("status", "")}'.strip() for event in turn.get('events') or ()
                 if event.get('tool') and event['tool'] not in WORK_EVENTS]
        lines.append(f'[tools run in turn {index}: {", ".join(tools) or "none"}]')
        # #407: on the paired-chat path the owner also read these before the answer.
        for j, message in enumerate(turn.get('before_answer') or (), 1):
            label = f't{index}.b{j}' if labelled else str(index)
            lines.append(f'AGENTOS {label} before the answer (+{message.get("after_s", "?")}s): {message.get("text", "")[:ANSWER_CHARS]}'
                         + (f' [at {message.get("at")}, id {message.get("delivery_id")}]' if message.get('delivery_id') else '')
                         + (' [truncated]' if len(message.get('text', '')) > ANSWER_CHARS else ''))
        delivered = delivered_answer(turn)
        answer = delivered if delivered is not None else (turn.get('answer') or '').strip()
        if turn.get('answer_withheld') and delivered is None:
            answer = '(answer withheld by Still)'
        if len(answer) > ANSWER_CHARS:
            answer = answer[:ANSWER_CHARS] + ' [...]'
        label = f't{index}' if labelled else str(index)
        source = ''
        if delivered is not None:
            message = turn['answer_message']
            source = f' [delivered text; at {message.get("at")}, id {message.get("delivery_id")}]'
        lines.append(f'AGENTOS {label} (status {turn.get("status")}){source}: {answer or "(no answer delivered)"}')
    diff = run.get('diff') or {}
    # The running situation summary is rewritten every turn from the whole day, including the seed's own
    # history, so its text would hand the judge unrelated owner details; the judge learns only that it changed.
    claims = [row for row in diff.get('current_state', []) if row.get('predicate') != SITUATION]
    lines += ['', 'Owner-state changes recorded during the conversation:']
    for label, rows, keys in (('new memory', diff.get('memories', []), ('content',)),
                              ('new memory candidate (awaiting owner confirmation)', diff.get('candidates', []),
                               ('content', 'value')),
                              ('new reminder/preparation', diff.get('preparations', []),
                               ('kind', 'goal', 'due_local', 'every_minutes', 'until_local', 'delivery', 'state')),
                              ('new current-state note (about today, not Memory)', claims, ('predicate', 'value', 'until'))):
        for row in rows[:10]:
            lines.append(f'- {label}: ' + ' / '.join(str(row.get(key)) for key in keys if row.get(key)))
    if len(claims) < len(diff.get('current_state', [])):
        lines.append("- today's running situation summary was updated (its text is not shown)")
    if not any(diff.get(key) for key in ('memories', 'candidates', 'preparations', 'current_state')):
        lines.append('- none')
    if diff.get('settings_changed'):
        lines.append('- settings changed')
    return lines


def judge_prompt(scenario, run):
    dims = scenario['rubric']
    lines = _transcript(scenario, run)
    lines += ['', 'Dimensions:']
    lines += [f'- {dim}: {RUBRIC[dim]}' for dim in dims]
    lines += ['', 'Reply as JSON: {"dimensions": {"<dimension>": {"score": 0|1|2|null, "reason": "<one sentence>"}}, '
              '"summary": "<one sentence>"}']
    return '\n'.join(lines)


def parse_judgment(text, dims):
    """Parse the judge reply into ``{dim: {'score': 0..1 or None, 'reason'}}``; raises ValueError."""
    match = re.search(r'\{.*\}', text or '', re.S)
    if not match:
        raise ValueError('judge reply has no JSON object')
    data = json.loads(match.group(0))
    rows = data.get('dimensions') if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        raise ValueError('judge reply has no dimensions')
    result = {}
    for dim in dims:
        row = rows.get(dim)
        if not isinstance(row, dict) or 'score' not in row:
            raise ValueError(f'judge reply misses {dim}')
        score = row['score']
        if score is not None and (isinstance(score, bool) or score not in (0, 1, 2)):
            raise ValueError(f'judge score for {dim} is not 0, 1, 2 or null')
        result[dim] = {'score': None if score is None else score / 2, 'reason': str(row.get('reason') or '')[:300]}
    return {'dimensions': result, 'summary': str(data.get('summary') or '')[:400]}


def rubric_failures(judgment):
    """A dimension scored 0 is a failure; 1 (partly) is a weakness, reported but not a failure."""
    rows = []
    for dim, row in (judgment or {}).get('dimensions', {}).items():
        if row['score'] == 0:
            rows.append({'kind': 'rubric', 'name': dim, 'reason': row['reason']})
    return rows


CHECK_NAMES = ('harness', 'delivered', 'not_failed', 'memory', 'current_state', 'preparation', 'no_memory_of_request', 'due_completed')


def combine(scenario, run, checks, failures, judgment=None, judge_status='skipped',
            measures=None, measures_status='skipped', measures_rubric=None, judge_usage=None):
    """The score value (all floats, for Inspect) and the metadata the report reads."""
    value = {f'check_{name}': 1.0 if ok else 0.0 for name, ok in checks.items()}
    if judgment:
        for dim, row in judgment['dimensions'].items():
            if row['score'] is not None:
                value[f'rubric_{dim}'] = float(row['score'])
        failures = [*failures, *rubric_failures(judgment)]
    rubric_scores = [value[key] for key in value if key.startswith('rubric_')]
    value['checks_pass'] = 1.0 if checks and all(checks.values()) else 0.0
    value['rubric_mean'] = sum(rubric_scores) / len(rubric_scores) if rubric_scores else math.nan
    # Inspect's per-key metrics need every score to carry the same keys; a key that
    # does not apply to this scenario is NaN, which Inspect counts as unscored.
    for key in [f'check_{name}' for name in CHECK_NAMES] + [f'rubric_{dim}' for dim in RUBRIC]:
        value.setdefault(key, math.nan)
    for key in MEASURE_KEYS:
        value[key] = math.nan
    if measures_status == 'judged' and measures:
        value.update(measure_values(measures, scenario, run))
    value['passed'] = 1.0 if value['checks_pass'] and not failures else 0.0
    metadata = {'scenario': scenario['id'], 'source': scenario.get('source', 'bundled'), 'split': scenario['split'],
                'worker': run.get('worker'), **({'observation_version': run['observation_version']} if run.get('observation_version') else {}), 'checks': checks, 'failures': failures, 'judge_status': judge_status,
                'judgment': judgment, 'judgment_ai': run.get('judgment'), 'statuses': [turn.get('status') for turn in run.get('turns') or []],
                'failure_classes': [turn.get('failure_class') for turn in run.get('turns') or []],
                'elapsed': [turn.get('elapsed') for turn in run.get('turns') or []]}
    metadata.update(measures=measures, measures_status=measures_status,
                    measures_rubric_version=(measures_rubric or {}).get('version'),
                    proactive_observation=proactive_observation(run),
                    required_approvals_events=required_approvals_events(scenario, run),
                    judge_usage=judge_usage or {},
                    turn_count=len(run.get('turns') or []),
                    message_ids=sorted(message_ids(run)),
                    due_observations=due_observations(run),
                    latency=[{'turn': i, 'sent_at': t.get('sent_at'), 'elapsed': t.get('elapsed')}
                             for i, t in enumerate(run.get('turns') or [], 1) if t.get('kind') != 'wait_due'])
    resolved = sorted({call['judge_model_resolved'] for call in (judge_usage or {}).values()
                       if call.get('judge_model_resolved')})
    if resolved:
        metadata['judge_model_resolved'] = resolved
    return value, metadata


MEASURE_KEYS = ('measure_avoidable_interventions', 'measure_required_approvals_judged',
                'measure_required_approvals_events', 'measure_unnecessary_interruptions',
                'measure_proactive_usefulness')
MEASURE_VALUES = tuple(k.removeprefix('measure_') for k in MEASURE_KEYS)
MEASURES = ('avoidable_interventions', 'required_approvals', 'unnecessary_interruptions', 'proactive_usefulness')


def observation_error(run):
    return run.get('error') or next((r.get('reason', 'observation error') for r in due_observations(run)
                                    if r.get('status') == 'observation_error'), None)


def due_observations(run):
    return run.get('due_observations') or [t['due_observation'] for t in run.get('turns', []) if 'due_observation' in t]


def proactive_observation(run):
    """Typed scheduled receipts or the frozen historical line-account evidence route.

    Requested replies and unknown-origin messages never establish provenance.
    API excerpts are used only by the historical account route, deduplicated
    against stronger scheduled receipts for the same preparation/Work.
    """
    owner_works = {t['task_id'] for t in run.get('turns') or [] if t.get('task_id')}
    outputs = [{'preparation_id': row.get('id'), 'work_id': row['last_work_id'],
                'text': row['last_result'], 'delivery': 'sent', 'kind': 'line_account'}
               for row in (run.get('diff') or {}).get('preparations', [])
               if row.get('line_account') is True and row.get('last_work_id')
               and row['last_work_id'] not in owner_works and row.get('last_delivery') == 'sent'
               and isinstance(row.get('last_result'), str) and row['last_result'].strip()]
    covered = {(r['preparation_id'], r['work_id']) for r in outputs}
    for receipt in due_observations(run):
        work = receipt.get('work_id')
        proof = receipt.get('reminder_origin') or {}
        reminder = False
        if proof:
            from .runner import scheduled_reminder_origin
            settled = (receipt.get('settlement') or {}).get('facts') or {}
            reminder = (proof.get('work', {}).get('id') == work
                        and proof.get('preparation', {}).get('id') == receipt.get('preparation_id')
                        and scheduled_reminder_origin(proof.get('preparation', {}), proof.get('work', {}),
                                                      receipt.get('start'), proof.get('settlement'), receipt.get('due_epoch'))
                        and settled.get('outcome') == 'delivered' and settled.get('delivery') == 'sent'
                        and (receipt.get('settlement') or {}).get('job_id') == work
                        and settled.get('preparation_id') == receipt.get('preparation_id'))
        if not ((receipt.get('admission') or reminder) and receipt.get('start') and receipt.get('settlement')
                and work and work not in owner_works):
            continue
        identity = (receipt.get('preparation_id'), work)
        linked = [m for m in receipt.get('deliveries', []) if (m.get('text') or '').strip()]
        if not linked:
            continue
        if identity in covered:
            outputs = [r for r in outputs if (r['preparation_id'], r['work_id']) != identity]
        seen = set()
        for message in linked:
            ident = message.get('delivery_id')
            if not ident or ident in seen:
                continue
            seen.add(ident)
            text = message['text']
            outputs.append({'preparation_id': identity[0], 'work_id': work, 'kind': 'scheduled_reminder' if reminder else 'scheduled',
                            'delivery': 'sent', 'message_ref': ident, 'transcript_ref': message.get('transcript_ref'), 'at': message.get('at'),
                            'text': text[:ANSWER_CHARS], 'truncated': len(text) > ANSWER_CHARS,
                            'coverage_limit': receipt.get('coverage_limit')})
        covered.add(identity)
    if outputs:
        return {'observed': True, 'outputs': outputs,
                'reason': 'Typed provenance and a distinct Work establish observed delivery; only collected text is graded.'}
    return {'observed': False, 'value': 'not_observed',
            'reason': 'No linked scheduled or line-account output. Quiet, missing and unsupported delivery have no grade.',
            'due_shortfalls': [{k: r.get(k) for k in ('step', 'status', 'reason', 'coverage')} for r in due_observations(run)]}


def required_approvals_events(scenario, run):
    """Observed approval requests, a lower bound, independent of judge lists.

    compact_events retains tool/status/label only. local_authority/requested is
    an explicit recorded request (quickstart_service.request_local_authority).
    connector_permission allowed/denied/claiming do NOT establish an approval.
    The public task progress events omit tool error codes and decision payloads;
    payment/browser approval requests cannot be reconstructed from those pairs.
    An awaiting_approval terminal status allowed by expect.allow_status provides
    one fallback observation per turn, only when no explicit request was counted.
    Other allowed statuses (e.g. awaiting_context) are not approvals. Events are
    capped at 40 by compact_events, so this is explicitly a lower bound.
    """
    rows = []
    allowed = scenario.get('expect', {}).get('allow_status') or []
    for i, turn in enumerate(run.get('turns') or [], 1):
        requests = [e for e in turn.get('events') or []
                    if e.get('tool') == 'local_authority' and e.get('status') == 'requested']
        rows.extend({'turn': i, 'tool': e['tool'], 'status': e['status'], 'source': 'tool_event'} for e in requests)
        if not requests and turn.get('status') == 'awaiting_approval' and 'awaiting_approval' in allowed:
            rows.append({'turn': i, 'status': 'awaiting_approval', 'source': 'allow_status'})
    return rows


def measures_prompt(scenario, run, rubric):
    lines = _transcript(scenario, run, labelled=True)
    lines += ['', 'Initiative measures (separate from the secretary dimensions):',
              rubric['messages'], json.dumps(rubric['measures'], ensure_ascii=False),
              'Harness proactive observation: ' + json.dumps(proactive_observation(run)),
              rubric['judge_instructions'], 'Reply with JSON matching this output schema:',
              json.dumps(rubric['output_schema'], ensure_ascii=False)]
    return '\n'.join(lines)


def parse_measures(text, rubric, run):
    """Strict shared-interface reply validation; nothing malformed is silently discarded.

    Required-approval overlap is retained as an inconsistency, then excluded
    from avoidable interventions. Unobserved proactive grades are never retained.
    """
    match = re.search(r'\{.*\}', text or '', re.S)
    if not match:
        raise ValueError('measures reply has no JSON object')
    data = json.loads(match.group())
    if not isinstance(data, dict) or set(data) != set(MEASURES):
        raise ValueError('measures reply must contain exactly the four measures')
    turns = run.get('turns') or []
    ids = message_ids(run)
    for name in MEASURES[:3]:
        rows = data[name]
        if not isinstance(rows, list):
            raise ValueError(f'{name} must be a list')
        seen = set()
        for row in rows:
            keys = {'message', 'reason'} if name == 'unnecessary_interruptions' else {'turn', 'reason'}
            if name == 'avoidable_interventions':
                keys.add('kind')
            if not isinstance(row, dict) or set(row) != keys or not isinstance(row['reason'], str):
                raise ValueError(f'invalid {name} item')
            index = row.get('message') if name == 'unnecessary_interruptions' else row.get('turn')
            if name == 'unnecessary_interruptions':
                if not isinstance(index, str) or index not in ids:
                    raise ValueError('unknown message id')
            elif type(index) is not int or not 1 <= index <= len(turns):
                raise ValueError('turn index out of range')
            if (name != 'unnecessary_interruptions' and turns[index - 1].get('kind') == 'wait_due'
                    and not any(ref in ids and ref.startswith(f't{index}.b')
                                for ref in re.findall(r't[0-9]+\.b[0-9]+', row['reason']))):
                raise ValueError('wait ask reason must identify its actual tN.bJ message')
            if name == 'avoidable_interventions' and row['kind'] not in ('asked_known_fact', 'asked_entrusted_action'):
                raise ValueError('unknown intervention kind')
            identity = (index, row['kind']) if name == 'avoidable_interventions' else index
            if identity in seen:
                raise ValueError(f'duplicate {name} index')
            seen.add(identity)
    proactive = data['proactive_usefulness']
    if (not isinstance(proactive, dict) or set(proactive) != {'score', 'reason'}
            or not isinstance(proactive['reason'], str)
            or (proactive['score'] is not None and (type(proactive['score']) is not int or proactive['score'] not in (0, 1, 2)))):
        raise ValueError('invalid proactive_usefulness')
    observation = proactive_observation(run)
    if not observation['observed']:
        data['proactive_usefulness'] = {'score': None, 'reason': observation['reason'], 'value': 'not_observed'}
    elif proactive['score'] is None:
        raise ValueError('observable proactive usefulness needs a grade')
    def ask_id(row):
        refs = re.findall(r't[0-9]+(?:\.b[0-9]+)?', row['reason'])
        refs = [ref for ref in refs if ref in ids and ref.startswith(f't{row["turn"]}.b')]
        if refs:
            return refs[0]
        return f't{row["turn"]}'
    required = {ask_id(row) for row in data['required_approvals']}
    overlaps = [row for row in data['avoidable_interventions'] if ask_id(row) in required]
    data['inconsistencies'] = [{'turn': r['turn'], 'reason': 'Judge listed an ask as both required and avoidable',
                              'avoidable_item': r} for r in overlaps]
    data['avoidable_interventions'] = [r for r in data['avoidable_interventions'] if ask_id(r) not in required]
    # Turn-only intervention items refer to the answer by default. A separate
    # before_answer message can still be a valid interruption in that turn.
    avoidable = {ask_id(row): row['turn'] for row in data['avoidable_interventions']}
    interruptions = []
    for row in data['unnecessary_interruptions']:
        if row['message'] in required:
            data['inconsistencies'].append({
                'message': row['message'], 'interruption_item': row,
                'reason': 'Judge listed a required approval ask as an interruption; counted only as approval'})
        elif row['message'] in avoidable:
            data['inconsistencies'].append({
                'turn': avoidable[row['message']], 'message': row['message'], 'interruption_item': row,
                'reason': 'Judge listed an answer as both avoidable intervention and interruption; counted only as intervention'})
        else:
            interruptions.append(row)
    data['unnecessary_interruptions'] = interruptions
    data['proactive_observation'] = observation
    data['rubric_version'] = rubric['version']
    return data


def measure_values(measures, scenario, run):
    score = measures['proactive_usefulness']['score']
    return {'measure_avoidable_interventions': float(len(measures['avoidable_interventions'])),
            'measure_required_approvals_judged': float(len(measures['required_approvals'])),
            'measure_required_approvals_events': float(len(required_approvals_events(scenario, run))),
            'measure_unnecessary_interruptions': float(len(measures['unnecessary_interruptions'])),
            'measure_proactive_usefulness': score / 2 if score is not None and proactive_observation(run)['observed'] else math.nan}


async def judge_call(prompt, parse, generate, budget, redact, system=JUDGE_SYSTEM):
    """One shared budgeted/redacted judge call; injected transport, no Inspect dependency.

    Unknown usage retains the reservation, exactly as JudgeBudget.settle does.
    Returned usage records the settled charge even on malformed replies/errors.
    """
    from .budget import estimate_tokens
    prompt = redact(prompt)
    reserved = budget.reserve(estimate_tokens(system + prompt), JUDGE_MAX_TOKENS)
    if reserved is None:
        return None, 'budget_refused', {}, None
    usage = {'input_tokens': 'unavailable', 'output_tokens': 'unavailable', 'usd': reserved,
             'basis': 'reservation'}
    try:
        output = await generate(system, prompt)
        resolved = getattr(output, 'model', None)
        if resolved:
            usage['judge_model_resolved'] = resolved
        tokens = output.usage
        usd = budget.settle(reserved, tokens.input_tokens if tokens else None, tokens.output_tokens if tokens else None)
        if tokens:
            usage.update(input_tokens=tokens.input_tokens, output_tokens=tokens.output_tokens,
                         usd=usd, basis='observed')
        return parse(output.completion), 'judged', usage, None
    except Exception as exc:  # a judge outage or malformed reply stays unscored
        return None, 'judge_error', usage, redact(f'{type(exc).__name__}: {exc}')[:200]


def message_ids(run):
    return {f't{i}' for i, t in enumerate(run.get('turns') or [], 1)
            if t.get('kind') != 'wait_due' and ((t.get('answer') and not t.get('answer_withheld'))
                                                or delivered_answer(t) is not None)} | {
            f't{i}.b{j}' for i, t in enumerate(run.get('turns') or [], 1)
            for j, _ in enumerate(t.get('before_answer') or [], 1)}


async def score_run(scenario, run, judge, budget, redact, generate, rubric=None):
    """Testable scorer orchestration, with one additional measures call when enabled."""
    from .runner import usage_limited
    checks, failures = deterministic_checks(scenario, run)
    judgment, judge_status = None, 'skipped'
    measured, measures_status, usage = None, 'skipped', {}
    eligible = bool(judge) and judge != 'none' and not observation_error(run) and not usage_limited(run)
    infra = any(f['kind'] == 'infra' for f in failures)
    if eligible:
        judgment, judge_status, usage['rubric'], error = await judge_call(
            judge_prompt(scenario, run), lambda text: parse_judgment(text, scenario['rubric']),
            generate, budget, redact)
        if error:
            failures = [*failures, {'kind': 'infra', 'name': 'judge', 'reason': error}]
        if rubric and not infra:
            measured, measures_status, usage['measures'], error = await judge_call(
                measures_prompt(scenario, run, rubric), lambda text: parse_measures(text, rubric, run),
                generate, budget, redact)
            if error:
                failures = [*failures, {'kind': 'infra', 'name': 'measures_judge', 'reason': error}]
        elif rubric:
            measures_status = 'not_scored'
    elif rubric:
        measures_status = 'not_scored' if observation_error(run) or usage_limited(run) else 'skipped'
    return combine(scenario, run, checks, failures, judgment, judge_status,
                   measured, measures_status, rubric, usage)
