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
    """``key`` with ``row`` appended and its ``<key>_dropped`` count, read from ``current``."""
    rows = [item for item in current.get(key) or [] if isinstance(item, dict)]
    dropped = current.get(f'{key}_dropped') if isinstance(current.get(f'{key}_dropped'), int) else 0
    rows, dropped = _bounded(rows, dropped, row, keep)
    return {key: rows, **({f'{key}_dropped': dropped} if dropped else {})}


def updated_entry(current, ref, changes):
    """``attempt_inputs`` with the entry ``ref`` changed; ``{}`` when it is gone (bounded out)."""
    rows = [item for item in current.get('attempt_inputs') or [] if isinstance(item, dict)]
    if not any(item.get('ref') == ref for item in rows):
        return {}
    return {'attempt_inputs': [{**item, **changes} if item.get('ref') == ref else item for item in rows]}


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
    """Record the definitions a bridge answered ``tools/list`` with; never breaks the bridge."""
    try:
        identity = offered_tools_identity(definitions, representation=representation)
        store.merge_turn_provenance(job_id, {}, combine=lambda current, _fields: served_entry(current, identity))
    except Exception:
        pass
