"""Work mode (#208): an owner-switched mode with no external exposure and no undeclared egress.

On a company network the owner switches work mode on in Settings.  While it
is on, Still opens nothing to the outside and sends nothing to a destination
the owner did not declare.  The personal pilot posture (#826: an
information-use audit instead of transmission guards) is unchanged while it is
off.

Still-owned boundary, nothing more (C16: the owner's AI does the work; this
module only refuses the egress classes below):

* **Switch.** One row (``CONFIG_KEY``), written only by ``set_enabled`` from the
  local Settings endpoint.  ``settings_change`` in conversation has no
  ``work_mode`` setting, so a proposal cannot reach it.  Every change is kept
  in ``AUDIT_KEY``.
* **Refusal.** ``refusal(store, egress)`` is the one predicate each egress
  class asks at its existing enforcement point; ``refuse`` raises it as
  ``WorkModeRefused`` with an owner-facing reason.  The points are:

  - ``Capabilities.execute``: every model-chosen tool that reaches a host off
    this Mac (``TOOL_EGRESS``), on every route (direct, CLI bridge, browser
    relay, delegated specialist), before the tool runs;
  - ``ProviderRegistry.search``: every web search, the service's own lookup
    preflight included;
  - ``SkillLibrary``: skill acquisition from GitHub;
  - ``family_setup.start_tunnel``: every public tunnel (remote login, phone
    input, family setup);
  - ``TelegramChannel``: every Bot API and file request; the receive and
    delivery loops also stand down;
  - the service's CLI native-search decision: the CLI's own web search stays
    off for the turn.

Allowed while on: the active AI route's own endpoint (the owner chose it in
Settings, and no model output can change it), loopback, and the intranet
destinations the owner declared in Settings (#209, ``intranet``) for the
classes whose every request names its destination and is checked
(``DESTINATION_EGRESS``: page reads, API calls, code runs).  A browser page
loads resources from wherever it names, which Still does not confine, so the
browser stays refused even for a declared host.  A
Mattermost surface (#213) pauses with Telegram unless the owner marked it
company-approved in Settings (``mattermost_surface.MattermostSurface.usable``);
Slack (#214) is admitted the same way when it lands.  Every other destination
is refused.

What work mode does **not** control (stated, never overclaimed): traffic a CLI
engine makes inside its own turn besides the web search Still turns off -- its
connection to its model provider, its own telemetry or update checks, and
whatever its sandbox lets a shell command reach (Codex runs with
``--sandbox read-only``; Claude Code's ``-p`` mode denies a built-in tool that
has no allow rule).  Owner-connected services (Google connectors, the AI's own
claude.ai connectors, the owner's MCP servers) and family instances on this
Mac, which keep their own settings, are not narrowed by this switch.
"""
from __future__ import annotations

import time

from .messages import msg
from .providers import ProviderError

CONFIG_KEY = 'work_mode'
AUDIT_KEY = 'work_mode_audit'
AUDIT_LIMIT = 100

#: Model tool host actions that reach a host off this Mac, and the egress class each belongs to.
TOOL_EGRESS = {
    'web_search': 'web_search', 'capability_search': 'web_search',
    'public_page_read': 'public_read', 'weather': 'public_read',
    **{action: 'browser' for action in ('browser_open', 'browser_read', 'browser_find', 'browser_click',
                                        'browser_type', 'browser_sign_in')},
    'api_request': 'api_request', 'code_run': 'code_run',
}
#: Every egress class this module refuses, with its owner-facing reason.
EGRESS_CLASSES = ('web_search', 'public_read', 'browser', 'api_request', 'code_run', 'skill_acquisition',
                  'tunnel', 'telegram')
#: #209: egress classes whose every request is checked against its destination, so a declared
#: intranet destination may be reached while work mode is on.
DESTINATION_EGRESS = frozenset({'public_read', 'api_request', 'code_run'})


class WorkModeRefused(ProviderError, ValueError):
    """Work mode refused this egress; the message is safe to show the owner.

    A ``ProviderError`` (Telegram and search callers already handle one) and a
    ``ValueError`` (tool and Settings callers already show one).  Its status is
    never transient, so nothing retries it.
    """

    def __init__(self, egress):
        super().__init__(msg(f'work_mode.refused.{egress}'), status='work_mode')
        self.egress = egress


def state(store):
    """``{'enabled': bool, 'changed_at': float | None}`` from the store.

    No row is personal mode.  A row that is not a well-formed record reads as
    on: a damaged switch fails closed.  A store that cannot be read raises, so
    the caller's operation fails instead of going out.
    """
    row = store.config(CONFIG_KEY, None)
    if row is None:
        return {'enabled': False, 'changed_at': None}
    if not isinstance(row, dict) or not isinstance(row.get('enabled'), bool):
        return {'enabled': True, 'changed_at': None}
    changed = row.get('changed_at')
    return {'enabled': row['enabled'], 'changed_at': changed if isinstance(changed, (int, float)) else None}


def active(store):
    """Whether work mode is on for this owner store (``None``: no owner store, personal mode)."""
    return store is not None and state(store)['enabled']


def was_on_since(store, since):
    """Whether work mode was on at any moment from ``since`` until now (#208 review).

    Read from the switch's audit.  A Telegram reply or notification whose Work
    was under way while work mode was on is never sent later, even after it is
    switched off.  When the audit cannot answer (``since`` is older than every
    kept row, which the bound of ``AUDIT_LIMIT`` rows can cause, or ``since`` is
    not a time) this answers True: fail closed.
    """
    if state(store)['enabled']:
        return True
    if not isinstance(since, (int, float)) or isinstance(since, bool):
        return True
    audit = store.config(AUDIT_KEY, [])
    rows = [row for row in audit if isinstance(row, dict) and isinstance(row.get('at'), (int, float))] \
        if isinstance(audit, list) else []
    if not rows:
        return store.config(CONFIG_KEY, None) is not None   # a row with no audit: unknown history
    if len(audit) >= AUDIT_LIMIT and since < rows[0]['at']:
        return True
    earlier = [row for row in rows if row['at'] <= since]
    if earlier and earlier[-1].get('after') is True:
        return True
    return any(row.get('after') is True or row.get('before') is True for row in rows if row['at'] > since)


def refusal(store, egress, destination=None):
    """The owner-facing reason ``egress`` (to ``destination``) is refused now, or None when it may go out.

    ``destination`` (#209): the ``(host, port)`` one request goes to.  While work
    mode is on, a ``DESTINATION_EGRESS`` request to a declared intranet
    destination may go out; nothing else.
    """
    if egress not in EGRESS_CLASSES:
        raise ValueError(f'unknown egress class: {egress}')
    if not active(store):
        return None
    if egress in DESTINATION_EGRESS and destination is not None:
        from . import intranet
        if intranet.declared(store, *destination):
            return None
    return msg(f'work_mode.refused.{egress}')


def refuse(store, egress):
    """Raise ``WorkModeRefused`` when work mode is on; otherwise return."""
    if refusal(store, egress) is not None:
        raise WorkModeRefused(egress)


def _tool_destinations(host_action, args):
    """#209: every ``(host, port)`` a tool call names, or None when the call reaches somewhere it does not name."""
    from . import intranet
    if host_action in ('public_page_read', 'api_request'):
        found = intranet.destination(args.get('url'))
        return [found] if found else None
    if host_action == 'code_run':
        # Installing packages reaches the package index, which no call names.
        if args.get('packages'):
            return None
        hosts = args.get('hosts') or []
        hosts = hosts.replace(',', ' ').split() if isinstance(hosts, str) else hosts
        if not isinstance(hosts, list) or not hosts:
            return None
        found = [intranet.destination(f'https://{host}') if isinstance(host, str) else None for host in hosts]
        return None if None in found else found
    return None


def tool_refusal(store, host_action, args=None):
    """``WorkModeRefused`` for a model tool that would leave this Mac, or None.

    #209: a page read, API call or code run whose every named destination is a
    declared intranet destination may run; each of its requests is checked
    again where it is sent.
    """
    egress = TOOL_EGRESS.get(host_action)
    if not egress or not active(store):
        return None
    if egress in DESTINATION_EGRESS:
        from . import intranet
        found = _tool_destinations(host_action, args if isinstance(args, dict) else {})
        if found and all(intranet.declared(store, *item) for item in found):
            return None
    return WorkModeRefused(egress)


def set_enabled(store, enabled, *, surface, clock=time.time):
    """The owner's Settings switch: record the new state and an audit row; returns the state.

    ``surface`` names where the owner changed it (``settings``).  Only a real
    bool is accepted, so a missing or malformed value never turns it off.
    """
    if not isinstance(enabled, bool):
        raise ValueError(msg('work_mode.invalid_value'))
    now = clock()
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        before = store.config(CONFIG_KEY, None, db=db)
        was = before.get('enabled') if isinstance(before, dict) else None
        store.put(CONFIG_KEY, {'enabled': enabled, 'changed_at': now}, db=db)
        audit = store.config(AUDIT_KEY, [], db=db)
        audit = audit if isinstance(audit, list) else []
        store.put(AUDIT_KEY, [*audit, {'at': now, 'before': was, 'after': enabled, 'surface': str(surface)}][-AUDIT_LIMIT:],
                  db=db)
    return state(store)


def status(store):
    """The Settings read model: the state and its last recorded change.

    What it blocks and what it does not control is fixed page text (Settings >
    External connections), so it is translated with the rest of the page.
    """
    audit = store.config(AUDIT_KEY, [])
    last = audit[-1] if isinstance(audit, list) and audit and isinstance(audit[-1], dict) else None
    return {**state(store), 'last_change': last}
