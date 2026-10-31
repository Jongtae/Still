"""Multi-turn owner scenarios: format, validation and loading.

A scenario file is JSON: one scenario object or a list of them.  The bundled
set under ``evals/scenarios`` is synthetic and generalized.  Scenarios derived
from the owner's real conversations live only in the owner's local folders
(``paths.local_scenario_dirs``) and are never committed.

Scenario fields:

- ``id`` (unique slug), ``title``, ``split`` (``dev`` or ``heldout``);
- ``turns``: owner turns, each ``{"say": str, "note": str?}``.  ``note`` is a
  simulated clock or current-context note.  It is shown to the judge; it is
  sent to Still only when the turn sets ``"note_in_message": true``, because
  the sandbox runs on the real clock and a hidden note must not leak into the
  owner's words;
- ``rubric``: the rubric dimensions this scenario is judged on;
- ``expect`` (optional deterministic expectations): ``memory_any`` (a list of
  groups; each group is a list of alternative substrings and one of them must
  appear in a new Memory row or MemoryCandidate), ``current_state_any`` (the
  same group form, matched against new current-context hypotheses: a fact
  about today, such as a meal already eaten, belongs there rather than in
  Memory, #627), ``preparation`` (a new
  preparation/reminder must exist), ``no_memory_of_request`` (no new Memory
  row or MemoryCandidate may hold an owner turn's sentence as its content: a
  request is not a fact about the owner, #846), ``allow_status`` (extra terminal statuses
  that count as not failed, e.g. ``awaiting_context``);
- ``good_secretary``: what a good secretary would do, for the judge;
- ``tags``: free labels for grouping;
- ``world`` (#484, a world scenario): ``{"name", "task_id", "task_set", "approvals", "money_capable"?,
  "expect_world_pass"?}``.  ``task_set`` is the world's ``train`` or ``dev`` set (never a test set);
  ``approvals`` maps each money operation (a tool name of the world's server) to ``approve`` or
  ``deny``, which the eval answers as the owner.  A world scenario's turns are ``{"from_world": true}``:
  the owner's words are the world's task instruction, fetched at run time, so no task text is ever
  stored here (``embedded_world_text``).  World scenarios run only with ``sweep --world``.
"""
import json
import re
from pathlib import Path

from .paths import BUNDLED_SCENARIOS, local_scenario_dirs

#: The secretary rubric (docs/secretary-agency-contract.en.md, issue #821).
RUBRIC = {
    'owner_model': 'Uses what Still knows about the owner (stated facts, preferences, places, earlier memory) '
                   'instead of asking again or answering generically.',
    'context_carry': 'Carries the conversation context: a follow-up is read against the earlier turns '
                     '(places, times, items already named).',
    'specific_sourced': 'Specific and sourced: concrete places, menus, prices, times or links, with where they '
                        'came from, rather than generic advice.',
    'honest_unverified': 'Honest about what is unverified: no invented facts, prices, bookings, saved state or '
                         'tool runs; says plainly what it could not check.',
    'follow_through': 'Follows through: sets or offers the reminder, records the memory, acknowledges a missed '
                      'timing, or corrects its own earlier advice when the owner adds new facts.',
    'no_wrong_narrowing': 'Does not wrongly narrow a new, unrelated request to the previous topic.',
    # #407 follow-up: what the owner reads before the answer, on the paired-chat path.
    'conversational': 'Talks like a capable person working for the owner, not like a system: anything it says '
                      'before or while working is natural, in one register, worth hearing (no restating the request '
                      'as a form, no routine "check, change, confirm" lists, no status chatter), and the final '
                      'answer does not repeat it.',
}
#: The dimensions a scenario is judged on when it names none (``conversational`` only when asked for).
DEFAULT_RUBRIC = tuple(name for name in RUBRIC if name != 'conversational')
#: ``web`` sends each turn to ``/api/chat``; ``telegram`` through the eval's Bot API stand-in.
CHANNELS = ('web', 'telegram')

SPLITS = ('dev', 'heldout')
_ID = re.compile(r'^[a-z0-9][a-z0-9-]{2,79}$')
_EXPECT_KEYS = {'memory_any', 'current_state_any', 'preparation', 'no_memory_of_request', 'allow_status'}
_TURN_KEYS = {'say', 'note', 'note_in_message'}
_KEYS = {'id', 'title', 'split', 'turns', 'rubric', 'expect', 'good_secretary', 'tags', 'channel', 'world'}
_WORLD_KEYS = {'name', 'task_id', 'task_set', 'approvals', 'money_capable', 'expect_world_pass'}
#: The world task sets a scenario may use: never a test set (AppWorld forbids tuning on them).
WORLD_TASK_SETS = ('train', 'dev')
DECISIONS = ('approve', 'deny')
_TASK_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
_OPERATION = re.compile(r'^[A-Za-z0-9_.-]{1,60}$')
#: Free text a world scenario may carry (title, good_secretary): short, so it cannot hold a task's text.
WORLD_TEXT_LIMIT = 200


class ScenarioError(ValueError):
    """A scenario file that does not match the format."""


def _groups(expect, key):
    return [[item.strip() for item in group] for group in expect.get(key, [])]


def _world(raw, where):
    """A world scenario's ``world`` block, normalized."""
    if not isinstance(raw, dict) or set(raw) - _WORLD_KEYS:
        raise ScenarioError(f'{where}: world may only hold {sorted(_WORLD_KEYS)}')
    if not isinstance(raw.get('name'), str) or not _ID.match(raw['name']):
        raise ScenarioError(f'{where}: world.name must be a world name')
    if not isinstance(raw.get('task_id'), str) or not _TASK_ID.match(raw['task_id']):
        raise ScenarioError(f'{where}: world.task_id must be a task id')
    if raw.get('task_set') not in WORLD_TASK_SETS:
        raise ScenarioError(f'{where}: world.task_set must be one of {WORLD_TASK_SETS} (never a test set)')
    approvals = raw.get('approvals', {})
    if (not isinstance(approvals, dict) or len(approvals) > 16
            or any(not isinstance(op, str) or not _OPERATION.match(op) or value not in DECISIONS
                   for op, value in approvals.items())):
        raise ScenarioError(f'{where}: world.approvals must map operation names to approve or deny')
    for key in ('money_capable', 'expect_world_pass'):
        if key in raw and not isinstance(raw[key], bool):
            raise ScenarioError(f'{where}: world.{key} must be true or false')
    return {'name': raw['name'], 'task_id': raw['task_id'], 'task_set': raw['task_set'], 'approvals': dict(approvals),
            'money_capable': raw.get('money_capable') is True, 'expect_world_pass': raw.get('expect_world_pass', True)}


def validate(raw, origin='scenario'):
    """Return a normalized scenario dict or raise ``ScenarioError``."""
    if not isinstance(raw, dict):
        raise ScenarioError(f'{origin}: a scenario must be an object')
    unknown = set(raw) - _KEYS
    if unknown:
        raise ScenarioError(f'{origin}: unknown fields {sorted(unknown)}')
    ident = raw.get('id')
    if not isinstance(ident, str) or not _ID.match(ident):
        raise ScenarioError(f'{origin}: id must be a lowercase slug')
    where = f'{origin}:{ident}'
    split = raw.get('split', 'dev')
    if split not in SPLITS:
        raise ScenarioError(f'{where}: split must be one of {SPLITS}')
    turns = raw.get('turns')
    if not isinstance(turns, list) or not 1 <= len(turns) <= 12:
        raise ScenarioError(f'{where}: turns must be a list of 1-12 owner turns')
    world = _world(raw['world'], where) if 'world' in raw else None
    normalized_turns = []
    for index, turn in enumerate(turns):
        if world is not None:
            # #484: the owner's words come from the world at run time; the file holds no task text.
            if turn != {'from_world': True}:
                raise ScenarioError(f'{where}: a world scenario turn must be {{"from_world": true}}')
            normalized_turns.append({'say': '', 'note': '', 'note_in_message': False, 'from_world': True})
            continue
        if isinstance(turn, str):
            turn = {'say': turn}
        if not isinstance(turn, dict) or set(turn) - _TURN_KEYS:
            raise ScenarioError(f'{where}: turn {index} must be a string or {{say, note?, note_in_message?}}')
        say = turn.get('say')
        if not isinstance(say, str) or not say.strip() or len(say) > 4000:
            raise ScenarioError(f'{where}: turn {index} needs 1-4000 characters of owner text')
        note = turn.get('note')
        if note is not None and (not isinstance(note, str) or len(note) > 1000):
            raise ScenarioError(f'{where}: turn {index} note must be text up to 1000 characters')
        normalized_turns.append({'say': say.strip(), 'note': (note or '').strip(),
                                 'note_in_message': turn.get('note_in_message') is True})
    channel = raw.get('channel', 'web')
    if channel not in CHANNELS:
        raise ScenarioError(f'{where}: channel must be one of {CHANNELS}')
    rubric = raw.get('rubric', list(DEFAULT_RUBRIC))
    if not isinstance(rubric, list) or not rubric or any(item not in RUBRIC for item in rubric):
        raise ScenarioError(f'{where}: rubric must name dimensions from {sorted(RUBRIC)}')
    expect = raw.get('expect', {})
    if not isinstance(expect, dict) or set(expect) - _EXPECT_KEYS:
        raise ScenarioError(f'{where}: expect may only hold {sorted(_EXPECT_KEYS)}')
    for key in ('memory_any', 'current_state_any'):
        groups = expect.get(key, [])
        if (not isinstance(groups, list) or any(not isinstance(group, list) or not group or
                                                any(not isinstance(item, str) or not item.strip() for item in group)
                                                for group in groups)):
            raise ScenarioError(f'{where}: expect.{key} must be a list of non-empty lists of text')
    allow_status = expect.get('allow_status', [])
    if not isinstance(allow_status, list) or any(not isinstance(item, str) for item in allow_status):
        raise ScenarioError(f'{where}: expect.allow_status must be a list of status names')
    tags = raw.get('tags', [])
    if not isinstance(tags, list) or any(not isinstance(item, str) for item in tags):
        raise ScenarioError(f'{where}: tags must be a list of text')
    if world is not None:
        if len(normalized_turns) != 1:
            raise ScenarioError(f'{where}: a world scenario has exactly one turn, the world task')
        if any(len(str(raw.get(key) or '')) > WORLD_TEXT_LIMIT for key in ('title', 'good_secretary')):
            raise ScenarioError(f'{where}: a world scenario keeps title and good_secretary under '
                                f'{WORLD_TEXT_LIMIT} characters (no task text)')
    return {'id': ident, 'title': str(raw.get('title') or ident), 'split': split, 'turns': normalized_turns,
            'rubric': list(dict.fromkeys(rubric)),
            'expect': {'memory_any': _groups(expect, 'memory_any'),
                       'current_state_any': _groups(expect, 'current_state_any'),
                       'preparation': expect.get('preparation') is True,
                       'no_memory_of_request': expect.get('no_memory_of_request') is True,
                       'allow_status': list(allow_status)},
            'good_secretary': str(raw.get('good_secretary') or ''), 'tags': list(tags), 'channel': channel,
            **({'world': world} if world is not None else {})}


def load_file(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ScenarioError(f'{path.name}: cannot read JSON ({exc})') from None
    items = data if isinstance(data, list) else [data]
    return [validate(item, path.name) for item in items]


def load(dirs=None, include_local=True, split=None, ids=None, environ=None, world=None):
    """Load scenarios from the bundled set and, by default, the owner-local folders.

    ``world`` selects world scenarios (#484): ``None`` leaves them out (the
    rubric sweep), a world name keeps only that world's scenarios.

    Every scenario records its ``source`` (``bundled`` or ``local``) so a
    report can keep owner-derived cases apart without printing their text.
    Duplicate ids fail loudly rather than shadowing each other.
    """
    sources = [(BUNDLED_SCENARIOS, 'bundled')] if dirs is None else [(Path(item), 'bundled') for item in dirs]
    if include_local:
        sources.extend((folder, 'local') for folder in local_scenario_dirs(environ))
    found = {}
    for folder, source in sources:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob('*.json')):
            for scenario in load_file(path):
                if scenario['id'] in found:
                    raise ScenarioError(f'{path.name}: duplicate scenario id {scenario["id"]}')
                found[scenario['id']] = {**scenario, 'source': source}
    scenarios = [item for item in found.values() if (item.get('world') or {}).get('name') == world]
    if split:
        scenarios = [item for item in scenarios if item['split'] == split]
    if ids:
        wanted = set(ids)
        missing = wanted - {item['id'] for item in scenarios}
        if missing:
            raise ScenarioError(f'unknown scenario ids: {sorted(missing)}')
        scenarios = [item for item in scenarios if item['id'] in wanted]
    return scenarios


def _plain(text):
    return ' '.join(re.sub(r'[^\w]+', ' ', str(text).casefold()).split())


def embedded_world_text(scenario_text, instruction, window=6):
    """Whether ``scenario_text`` (a scenario file's text) holds any ``window`` consecutive words of ``instruction``.

    The guard behind "scenarios reference task ids only" (#484): the runner
    checks every world scenario against the instruction it fetched before it
    sends anything, and the tests check the bundled files.
    """
    words, blob = _plain(instruction).split(), f' {_plain(scenario_text)} '
    if len(words) < window:
        return bool(words) and f' {" ".join(words)} ' in blob
    return any(f' {" ".join(words[start:start + window])} ' in blob for start in range(len(words) - window + 1))


def owner_message(turn, instruction=None):
    """The text actually sent to Still for one turn (a world turn sends the world's ``instruction``)."""
    if turn.get('from_world'):
        if not isinstance(instruction, str) or not instruction.strip():
            raise ScenarioError('a world turn needs the world task instruction')
        return instruction.strip()
    if turn.get('note_in_message') and turn.get('note'):
        return f"({turn['note']}) {turn['say']}"
    return turn['say']
