"""Attempt identities for #825: what one worker attempt was given.

Canonical JSON (RFC 8785-style key sorting, compact separators, UTF-8) and
SHA-256 over the instructions and over each offered tool's name, description
and parameters, with no new dependency.  Stored records carry names, digests,
counts and short labels only, never descriptions or schemas.

Representations: ``still-host-api`` is the exact list the direct-API loop
hands its provider adapter (complete: a direct call has no provider-side
tools).  ``still-host-mcp:parent-listing`` is the host's own listing for a CLI
turn and ``still-host-mcp:bridge`` what the bridge process actually answered
``tools/list`` with; the two are separate computations, so both are kept.
A CLI's vendor-native tools and an isolated sidecar's served list are
``unavailable``: Still cannot observe their definitions and claims no identity.
"""

import hashlib
import json
import threading


TOOLS_KEEP = 40


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def short_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def instructions_digest(instructions) -> str | None:
    return short_digest(instructions) if instructions else None


def normalize_tool(definition) -> dict:
    if isinstance(definition, dict) and definition.get('type') == 'function':
        definition = definition.get('function', definition)
    get = definition.get if isinstance(definition, dict) else lambda key, default=None: getattr(definition, key, default)
    name = get('name')
    if not name:
        raise ValueError('Tool definition has no name')
    normalized = {
        'name': str(name),
        'description': str(get('description') or ''),
        'parameters': get('parameters', get('input_schema', get('inputSchema'))) or {},
    }
    if get('strict'):
        normalized['strict'] = True
    # MCP behaviour hints (``readOnlyHint`` and the like) change what a CLI may auto-approve.
    annotations = get('annotations')
    if annotations:
        normalized['annotations'] = annotations if isinstance(annotations, dict) else getattr(annotations, 'model_dump', dict)()
    return normalized


def tool_digest(definition) -> str:
    return short_digest(canonical_json(normalize_tool(definition)))


def offered_tools_identity(definitions, *, representation: str) -> dict:
    tools = [normalize_tool(definition) for definition in definitions]
    ordered = sorted(((tool, short_digest(canonical_json(tool))) for tool in tools),
                     key=lambda item: (item[0]['name'], item[1]))
    record = {
        'representation': representation[:40],
        'digest': short_digest(canonical_json([tool for tool, digest in ordered])),
        'count': len(ordered),
        'tools': [{'name': tool['name'][:80], 'digest': digest}
                  for tool, digest in ordered[:TOOLS_KEEP]],
    }
    if len(ordered) > TOOLS_KEEP:
        record['tools_truncated'] = len(ordered) - TOOLS_KEEP
    return record


def unavailable(representation: str, reason: str) -> dict:
    return {'representation': representation[:40], 'digest': None, 'unavailable': reason[:120]}


def identity_or_unavailable(definitions, *, representation: str) -> dict:
    """``offered_tools_identity``, or ``unavailable`` when a definition cannot be canonicalised.

    Recording must never break the turn: a schema carrying a non-JSON value (``Infinity``, a set)
    is still sent as before; only its identity is reported as not computable.
    """
    try:
        return offered_tools_identity(definitions, representation=representation)
    except Exception as exc:
        return unavailable(representation, f'identity not computable: {type(exc).__name__}')


# The turn record keeps one input entry per worker attempt (#825), newest
# last, bounded like ``workers``; ``attempt_inputs_dropped`` counts the
# entries the bound removed, so a truncated list never reads as complete.
ATTEMPT_INPUTS_KEEP = 6
INPUT_STATES = ('prepared', 'sent', 'not-sent', 'unknown')


def _bounded(rows, dropped, row, keep):
    rows = [*rows, row]
    extra = max(0, len(rows) - keep)
    return rows[extra:], dropped + extra


def appended(current, key, row, keep):
    """``key`` with ``row`` appended and its ``<key>_dropped`` count, read from ``current``.

    A list already at its bound with no count was cut before counts were kept: its count is a
    lower bound, so ``<key>_dropped_exact`` says False rather than reading as complete.
    """
    rows = [item for item in current.get(key) or [] if isinstance(item, dict)]
    counted = isinstance(current.get(f'{key}_dropped'), int)
    exact = current.get(f'{key}_dropped_exact', True) is not False and (counted or len(rows) < keep)
    rows, dropped = _bounded(rows, current.get(f'{key}_dropped') if counted else 0, row, keep)
    # The count is written from the first row on (0 included), so a later reader can tell a
    # counted list from one cut before counts were kept.
    return {key: rows, f'{key}_dropped': dropped, **({f'{key}_dropped_exact': False} if not exact else {})}


BRIDGE_NOT_OBSERVED = unavailable('still-host-mcp:bridge', 'no tools/list answer was observed for this attempt')


def updated_entry(current, ref, changes):
    """``attempt_inputs`` with the entry ``ref`` changed; ``{}`` when it is gone (bounded out).

    A CLI attempt that ends without a recorded bridge listing says so instead of leaving a gap.
    """
    rows = [item for item in current.get('attempt_inputs') or [] if isinstance(item, dict)]
    if not any(item.get('ref') == ref for item in rows):
        return {}
    def change(item):
        item = {**item, **changes}
        if item.get('route') == 'subscription' and 'served' not in item:
            item['served'] = BRIDGE_NOT_OBSERVED
        return item
    return {'attempt_inputs': [change(item) if item.get('ref') == ref else item for item in rows]}


def served_entry(current, identity):
    """The in-flight CLI attempt's entry with what its bridge actually served at ``tools/list``.

    Only the newest entry, and only while it is still ``prepared`` (its CLI is
    running), so a later or unrelated bridge never rewrites a finished attempt.
    """
    rows = [item for item in current.get('attempt_inputs') or [] if isinstance(item, dict)]
    if not rows or rows[-1].get('input') != 'prepared' or rows[-1].get('route') != 'subscription':
        return {}
    return {'attempt_inputs': [*rows[:-1], {**rows[-1], 'served': identity}]}


def record_served(store, job_id, definitions, representation):
    """Record the definitions a bridge answered ``tools/list`` with; never breaks the bridge.

    Off the response path: a non-daemon thread, so the answer is not delayed by the store and
    the bridge process still finishes the write before it exits.  Nothing is written when no
    running CLI attempt is waiting for it.
    """
    def write():
        try:
            identity = identity_or_unavailable(definitions, representation=representation)
            if not served_entry(store.turn_provenance(job_id) or {}, identity):
                return
            store.merge_turn_provenance(job_id, {}, combine=lambda current, _fields: served_entry(current, identity) or current)
        except Exception:
            pass
    worker = threading.Thread(target=write, name='record-served-tools')
    worker.start()
    return worker
