"""Conversation-first settings read model over the authoritative connections.

PRESENCE-SETTINGS-01 #506 retired the legacy ``CapabilityRegistry`` as a
Settings control plane.  That registry had no production execution caller:
pausing or disconnecting ``google-drive-read`` there changed a config row
while the real connector credential and execution path stayed usable, so the
owner could be told a capability was stopped while Still could still call it.

This object therefore reads connection state only from the authoritative
boundaries the owning service supplies (Telegram pairing, ``ConnectorRegistry``
status for Gmail/Calendar, the Drive OAuth handoff), and it offers no lifecycle
mutation whose backend would not actually change the represented authority.
It never accepts credentials, OAuth artifacts, arbitrary setting names, or a
provider endpoint.  HTTP, Telegram, and the local companion view call this same
object.

Historical ``capability_registry`` rows, ``retired_capability_evidence`` and
the redacted ``settings_audit`` stay in the store untouched as evidence; they
are not read as current authority.

OWNER-SETTINGS-01 #814: with the owning service supplied, the same object also
reads and changes a few owner settings that already exist in the service -
current context (on/off, time zone), the Judgment AI (mode, model) and the
Main AI (route, model) - through the service's own setters only.  A change is
a digest-bound draft; nothing applies until the owner confirms it (the
적용 button on Telegram or the web, a plain yes typed in the same conversation
judged by the DecisionEngine - OWNER-SETTINGS-02 #855 - or the HTTP
``confirm``).  No command is ever shown to the owner (#855).  Values are fixed-choice or a validated time zone; a credential,
key, token or endpoint is never accepted (those stay in Settings).
"""
from .messages import msg
from . import work_mode
import hashlib
import hmac
import json
import re
import threading
import time
import uuid


class SettingsError(ValueError):
    """A fail-closed settings request error safe to show to an owner."""


# Owner-visible words for the connection contract states.  An unknown value is
# reported as needing a check rather than guessed as connected.
STATE_LABELS = {
    "connected": msg('label.connected'),
    "disconnected": msg('family_share.not_connected'),
    "reauth_required": msg('settings_orchestrator.sign_in_again'),
    "blocked": msg('label.blocked'),
    "pending": msg('label.needs_attention'),
    # #1204: a saved credential is setup, not a connection or a grant.
    "configured": msg('settings_orchestrator.configured'),
    "not_configured": msg('settings_orchestrator.not_set_up'),
}
UNKNOWN_STATE_LABEL = msg('label.needs_attention')

RETIRED_CONTROL_MESSAGE = (
    msg('settings_orchestrator.retired_control')
)
#: #183: what disconnecting a Google connection does, and what it leaves alone.  The same effects
#: ``GoogleConnectionRevoker.preview`` states; forgetting or deleting records is a separate request.
DISCONNECT_NOTE = msg('settings_orchestrator.disconnect_note')


def state_label(state):
    return STATE_LABELS.get(state, UNKNOWN_STATE_LABEL)


def next_action(row):
    """The one truthful owner action for a connection row, as plain text."""
    state = row.get("state")
    if state == "blocked":
        return msg('settings_orchestrator.not_available_under_the_current_policy')
    if row.get("id") == "telegram":
        if state == "connected":
            return msg('settings_orchestrator.telegram_connected_action')
        if state == "pending":
            return msg('settings_orchestrator.telegram_pending_action')
        return msg('settings_orchestrator.telegram_connect_action')
    if not row.get("connectable"):
        if state == "connected":
            return msg('settings_orchestrator.no_further_action')
        return row.get("connect_hint") or msg('settings_orchestrator.cannot_start_connection')
    if state == "connected":
        return msg('settings_orchestrator.connected_action')
    # #1207: name the loopback start address when this install offers one.
    url = row.get("connect_url")
    where = msg('settings_orchestrator.connection_location_url', url=url) if url else msg('settings_orchestrator.connection_location')
    if state == "reauth_required":
        return msg('settings_orchestrator.reconnect_action', where=where)
    if state == "disconnected":
        return msg('settings_orchestrator.connect_action', where=where)
    return msg('settings_orchestrator.check_connection_action')


#: #814: owner-visible names of the categories and settings the conversation may read/change.
CATEGORY_LABELS = {"connections": msg('label.external_connections'), "current_context": msg('label.current_context'), "judgment_ai": msg('label.judgment_ai'), "main_ai": msg('label.main_ai'),
                   "owner_model": msg('settings_orchestrator.owner_model_label'), "family": msg('settings_orchestrator.family_label'), "skills": msg('settings_orchestrator.skills_label'),
                   "phone_link": msg('settings_orchestrator.phone_link_label'), "ai_connections": msg('settings_orchestrator.ai_connections_label'), "api_connections": msg('settings_orchestrator.api_connections_label'),
                   "preparations": msg('settings_orchestrator.preparations_label'), "browser": msg('settings_orchestrator.browser_label')}
SETTINGS = {"current_context": ("enabled", "timezone"), "judgment_ai": ("mode", "model"), "main_ai": ("route", "model"),
            # #805 owner-model upkeep: its pause switch and rolling 24-hour call cap.
            # #402: the once-a-day look back over recent requests has its own switch.
            "owner_model": ("enabled", "daily_calls", "dream"),
            # #912: a family member's own agent, created by asking the assistant (FAMILY-02 #897).
            # #934: the owner shares (and stops sharing) one signed-in site with one of them.
            "family": ("add", "share_site", "unshare_site"),
            # #961: optional skill know-how: the switch, adding one pinned GitHub skill, removing one.
            "skills": ("enabled", "add", "remove"),
            # #1213: a one-time phone link for an input only a page can take (own Google client, Google consent).
            "phone_link": ("send",), "api_connections": ("connect",),
            # #1455: the owner's reservations (#659), seen and managed in the conversation first.
            "preparations": ("cancel", "accept"),
            # #1572: close one exact login window that holds the browser while the owner will not sign in.
            "browser": ("close_login",),
            # #1197: Works may use the owner's AI-side connections (claude.ai connectors), read-only.
            # #1297: which of the owner's MCP servers load, and the owner's trust record per connection.
            "ai_connections": ("enabled", "connect", "servers", "review", "money", "rung", "undo", "forget", "restore"),
            # #183: disconnect one Google connection (``GoogleConnectionRevoker``), confirmed like any draft.
            # #438: connect a place to talk (Telegram) from the terminal conversation; its token is typed there, hidden.
            "connections": ("disconnect", "connect_chat")}
SETTING_LABELS = {"enabled": msg('label.use'), "timezone": msg('label.time_zone'), "mode": msg('settings_orchestrator.mode_label'), "model": msg('label.model'), "route": msg('settings_orchestrator.route_label'),
                  "daily_calls": msg('settings_orchestrator.daily_calls_label'), "dream": msg('settings_orchestrator.dream_label'), "add": msg('settings_orchestrator.add_label'), "share_site": msg('settings_orchestrator.share_site_label'), "unshare_site": msg('settings_orchestrator.unshare_site_label'),
                  "remove": msg('label.remove'), "send": msg('settings_orchestrator.send_label'), "servers": msg('settings_orchestrator.servers_label'), "review": msg('settings_orchestrator.review_label'), "money": msg('settings_orchestrator.money_label'),
                  "connect": msg('settings_orchestrator.connect_label'), "rung": msg('settings_orchestrator.rung_label'), "undo": msg('settings_orchestrator.undo_label'), "forget": msg('settings_orchestrator.forget_label'), "restore": msg('settings_orchestrator.restore_label'),
                  "cancel": msg('label.cancel'), "accept": msg('button.accept'), "disconnect": msg('settings_orchestrator.disconnect_label'), "close_login": msg('settings_orchestrator.close_login_label'),
                  "connect_chat": msg('settings_orchestrator.connect_chat_label')}
_CATEGORY_LINE_IDS = {
    'connections': 'settings_orchestrator.category_line_connections',
    'current_context': 'settings_orchestrator.category_line_current_context',
    'judgment_ai': 'settings_orchestrator.category_line_judgment_ai',
    'main_ai': 'settings_orchestrator.category_line_main_ai',
    'owner_model': 'settings_orchestrator.category_line_owner_model',
    'family': 'settings_orchestrator.category_line_family',
    'skills': 'settings_orchestrator.category_line_skills',
    'phone_link': 'settings_orchestrator.category_line_phone_link',
    'ai_connections': 'settings_orchestrator.category_line_ai_connections',
    'api_connections': 'settings_orchestrator.category_line_api_connections',
    'preparations': 'settings_orchestrator.category_line_preparations',
    'browser': 'settings_orchestrator.category_line_browser',
}
# Catalog ids for whole setting/value pairs; unknown labels retain their original rendering.
_SETTING_VALUE_IDS = {
    SETTING_LABELS['enabled']: 'settings_orchestrator.setting_value_enabled',
    SETTING_LABELS['timezone']: 'settings_orchestrator.setting_value_timezone',
    SETTING_LABELS['mode']: 'settings_orchestrator.setting_value_mode',
    SETTING_LABELS['model']: 'settings_orchestrator.setting_value_model',
    SETTING_LABELS['route']: 'settings_orchestrator.setting_value_route',
    SETTING_LABELS['daily_calls']: 'settings_orchestrator.setting_value_daily_calls',
    SETTING_LABELS['dream']: 'settings_orchestrator.setting_value_dream',
    SETTING_LABELS['add']: 'settings_orchestrator.setting_value_add',
    SETTING_LABELS['share_site']: 'settings_orchestrator.setting_value_share_site',
    SETTING_LABELS['unshare_site']: 'settings_orchestrator.setting_value_unshare_site',
    SETTING_LABELS['remove']: 'settings_orchestrator.setting_value_remove',
    SETTING_LABELS['send']: 'settings_orchestrator.setting_value_send',
    SETTING_LABELS['servers']: 'settings_orchestrator.setting_value_servers',
    SETTING_LABELS['review']: 'settings_orchestrator.setting_value_review',
    SETTING_LABELS['money']: 'settings_orchestrator.setting_value_money',
    SETTING_LABELS['connect']: 'settings_orchestrator.setting_value_connect',
    SETTING_LABELS['rung']: 'settings_orchestrator.setting_value_rung',
    SETTING_LABELS['undo']: 'settings_orchestrator.setting_value_undo',
    SETTING_LABELS['forget']: 'settings_orchestrator.setting_value_forget',
    SETTING_LABELS['restore']: 'settings_orchestrator.setting_value_restore',
    SETTING_LABELS['cancel']: 'settings_orchestrator.setting_value_cancel',
    SETTING_LABELS['accept']: 'settings_orchestrator.setting_value_accept',
    SETTING_LABELS['disconnect']: 'settings_orchestrator.setting_value_disconnect',
    SETTING_LABELS['close_login']: 'settings_orchestrator.setting_value_close_login',
    SETTING_LABELS['connect_chat']: 'settings_orchestrator.setting_value_connect_chat',
    msg('settings_orchestrator.new_api_connection'): 'settings_orchestrator.setting_value_new_api',
}
# Whole compound labels let the surface translate multiword category names unambiguously.
_CHANGE_LABELS = {
    ('current_context', 'enabled'): msg('settings_orchestrator.label_current_context_enabled'),
    ('current_context', 'timezone'): msg('settings_orchestrator.label_current_context_timezone'),
    ('judgment_ai', 'mode'): msg('settings_orchestrator.label_judgment_ai_mode'),
    ('judgment_ai', 'model'): msg('settings_orchestrator.label_judgment_ai_model'),
    ('main_ai', 'route'): msg('settings_orchestrator.label_main_ai_route'),
    ('main_ai', 'model'): msg('settings_orchestrator.label_main_ai_model'),
    ('owner_model', 'enabled'): msg('settings_orchestrator.label_owner_model_enabled'),
    ('owner_model', 'daily_calls'): msg('settings_orchestrator.label_owner_model_daily_calls'),
    ('owner_model', 'dream'): msg('settings_orchestrator.label_owner_model_dream'),
    ('family', 'add'): msg('settings_orchestrator.label_family_add'),
    ('family', 'share_site'): msg('settings_orchestrator.label_family_share_site'),
    ('family', 'unshare_site'): msg('settings_orchestrator.label_family_unshare_site'),
    ('skills', 'enabled'): msg('settings_orchestrator.label_skills_enabled'),
    ('skills', 'add'): msg('settings_orchestrator.label_skills_add'),
    ('skills', 'remove'): msg('settings_orchestrator.label_skills_remove'),
    ('phone_link', 'send'): msg('settings_orchestrator.label_phone_link_send'),
    ('api_connections', 'connect'): msg('settings_orchestrator.label_api_connections_connect'),
    ('preparations', 'cancel'): msg('button.cancel_reservation'),
    ('preparations', 'accept'): msg('settings_orchestrator.label_preparations_accept'),
    ('browser', 'close_login'): msg('settings_orchestrator.label_browser_close_login'),
    ('ai_connections', 'enabled'): msg('settings_orchestrator.label_ai_connections_enabled'),
    ('ai_connections', 'connect'): msg('settings_orchestrator.label_ai_connections_connect'),
    ('ai_connections', 'servers'): msg('settings_orchestrator.label_ai_connections_servers'),
    ('ai_connections', 'review'): msg('settings_orchestrator.label_ai_connections_review'),
    ('ai_connections', 'money'): msg('settings_orchestrator.label_ai_connections_money'),
    ('ai_connections', 'rung'): msg('settings_orchestrator.label_ai_connections_rung'),
    ('ai_connections', 'undo'): msg('settings_orchestrator.label_ai_connections_undo'),
    ('ai_connections', 'forget'): msg('settings_orchestrator.label_ai_connections_forget'),
    ('ai_connections', 'restore'): msg('settings_orchestrator.label_ai_connections_restore'),
    ('connections', 'disconnect'): msg('settings_orchestrator.label_connections_disconnect'),
    ('connections', 'connect_chat'): msg('settings_orchestrator.label_connections_connect_chat'),
}
#: #1297: trust values include "<engine>|<kind>|<stable identity>|..." so a
#: decision cannot cross engines or connection kinds.
TRUST_SETTINGS = frozenset({("ai_connections", "servers"), ("ai_connections", "review"), ("ai_connections", "money"),
                            ("ai_connections", "rung"), ("ai_connections", "undo"), ("ai_connections", "forget"),
                            ("ai_connections", "restore")})
TRUST_CLASS_LABELS = {"read": msg('settings_orchestrator.read_class'), "mutate": msg('label.change'), "payment": msg('settings_orchestrator.payment_class'), "refused": msg('settings_orchestrator.refused_class')}
TRUST_RUNG_LABELS = {"report": msg('settings_orchestrator.report_rung'), "propose": msg('settings_orchestrator.propose_rung'), "approve": msg('settings_orchestrator.approve_rung'), "mandate": msg('settings_orchestrator.mandate_rung')}
#: #934: the value of a share is "<family assistant>|<site>"; a stop may name the site alone.
FAMILY_SHARE_SETTINGS = frozenset({("family", "share_site"), ("family", "unshare_site")})
FAMILY_SHARE_NOTE = msg('settings_orchestrator.family_share_note')
VALUE_LABELS = {"on": msg('label.on'), "off": msg('label.off'), "follow_main": msg('settings_orchestrator.follow_main_ai'), "explicit": msg('label.separate_configuration')}
JUDGMENT_MODE_LABELS = {"off": msg('settings_orchestrator.not_used')}
UNKNOWN_SETTING_MESSAGE = msg('settings_orchestrator.unknown_setting')
CREDENTIAL_VALUE_MESSAGE = msg('settings_orchestrator.credential_value')
UNAVAILABLE_MESSAGE = msg('settings_orchestrator.unavailable')
MAX_VALUE_CHARS = 200
MAX_REASON_CHARS = 300
#: #814 review: settings whose setter probes or qualifies an AI (seconds to minutes).
#: With a follow-up channel they are applied off the caller's thread, one at a time.
SLOW_SETTINGS = frozenset({("judgment_ai", "model"), ("main_ai", "route"), ("main_ai", "model"),
                           # #961: adding a skill downloads and inspects one pinned GitHub folder.
                           ("skills", "add"), ("ai_connections", "connect"), ('api_connections', 'connect'),
                           # #1213/#523: opening a phone link starts a tunnel (up to ~30 seconds; the first quick tunnel also downloads cloudflared).
                           ("phone_link", "send"),
                           # #183: a disconnect asks Google to revoke the credential (one network call).
                           ("connections", "disconnect")})
#: #961: what adding a skill does, shown with the draft.
SKILL_ADD_NOTE = msg('settings_orchestrator.skill_add_note')
#: #438: what connecting Telegram from the terminal conversation does, shown with the draft.
CONNECT_CHAT_NOTE = msg('settings_orchestrator.connect_chat_note')
CONNECT_CHAT_TERMINAL_ONLY = msg('settings_orchestrator.connect_chat_terminal_only')
#: How long a confirmation waits for another apply of the same category (#814 review).
APPLY_WAIT_SECONDS = 5
#: #814 review: a draft left ``applying`` by a restart: its setter may or may not have committed.
IN_DOUBT_MESSAGE = msg('settings_orchestrator.in_doubt')
#: #814 review P1: text confirmation needs a message the owner typed, not one Still ran.
NOT_OWNER_TYPED_MESSAGE = msg('settings_orchestrator.not_owner_typed')
BUSY_MESSAGE = msg('settings_orchestrator.busy')
STALE_MESSAGE = msg('settings_orchestrator.stale')
WORKER_START_MESSAGE = msg('settings_orchestrator.apply_not_started')
FOLLOW_REQUESTED_MESSAGE = msg('settings_orchestrator.follow_requested')
#: #912: the family setup runs in the background; its link and outcome follow on Telegram.
FAMILY_REQUESTED_MESSAGE = msg('settings_orchestrator.family_requested')


def canonical_timezone(name):
    """The IANA spelling of a zone ``valid_timezone`` accepted (#814 review): ``asia/seoul`` -> ``Asia/Seoul``."""
    from zoneinfo import available_timezones
    if not name:
        return name
    return next((zone for zone in sorted(available_timezones()) if zone.lower() == name.lower()), name)


RECOVERY_DECLINED_MESSAGE = msg('settings_orchestrator.recovery_declined')


class SettingsOrchestrator:
    TTL_SECONDS = 10 * 60
    _CATEGORIES = ("connections",)

    def __init__(self, store, now=time.time, connections=None, service=None):
        self.store, self.now = store, now
        # A callable returning owner-visible connection rows derived from the
        # authoritative boundaries.  With none supplied there are no
        # connections to report, never a guessed one.
        self.connections = connections or (lambda: [])
        # #814: the owning service whose existing setters are the only mutation
        # path for the owner settings below; None offers only ``connections``.
        self.service = service
        if service is not None:
            self._CATEGORIES = tuple(dict.fromkeys(("connections", *SETTINGS)))
        # One confirmation moves a draft out of awaiting at a time (exactly once);
        # every read-modify-write of the draft rows holds it (re-entrant).
        self._confirming = threading.RLock()
        # #814 review P2-3: slow applies run in order on one background worker.
        self._queue, self._worker_running = [], False
        self.thread = None
        # #814 review: one compare-and-apply per category at a time (the stale ``before``
        # check and the setter under one lock, handed to the off-thread apply).
        self._category_locks = {name: threading.Lock() for name in SETTINGS}
        # Drafts this process is applying; any other ``applying`` draft is in doubt.
        self._in_flight = set()

    def _drafts(self):
        rows = self.store.config("settings_change_drafts", {})
        return rows if isinstance(rows, dict) else {}

    def _put_drafts(self, rows):
        # OwnerForget can revoke drafts through another store instance. A stale
        # settings snapshot must never overwrite that terminal revocation.
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute("SELECT value FROM config WHERE key='settings_change_drafts'").fetchone()
            current = json.loads(found['value']) if found else {}
            for key, row in rows.items():
                if not (current.get(key) or {}).get('trust_revoked'):
                    current[key] = row
            db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       ('settings_change_drafts', json.dumps(current)))
        return current

    def _rows(self):
        rows = []
        for row in self.connections() or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            item = {"id": str(row["id"]), "service": str(row.get("service") or row["id"]),
                    "state": str(row.get("state") or "unknown")}
            item["state_label"] = state_label(item["state"])
            item["next_action"] = next_action(row)
            # #183: settings_change connections disconnect takes this id.
            if row.get("disconnectable"):
                item["disconnectable"] = True
            rows.append(item)
        return rows

    def _activity(self):
        audit = self.store.config("settings_audit", [])
        if not isinstance(audit, list):
            return []
        return [{key: item[key] for key in ("at", "target", "before", "after", "terminal", "error_class") if key in item}
                for item in audit[-20:] if isinstance(item, dict) and item.get("terminal") != "drafted"]

    @staticmethod
    def _summary(rows):
        if not rows:
            return msg('settings_orchestrator.no_connections')
        return "\n".join(msg('settings_orchestrator.connection_summary', service=row['service'], state=row['state_label'])
                         if row['state'] == 'connected' else
                         msg('settings_orchestrator.connection_action_summary', service=row['service'],
                             state=row['state_label'], action=row['next_action']) for row in rows)

    def read(self, owner, category=None):
        if not isinstance(owner, str) or not owner:
            raise SettingsError(msg('settings_orchestrator.check_owner'))
        if category is not None and category not in self._CATEGORIES:
            raise SettingsError(msg('settings_orchestrator.select_category'))
        self.reconcile()
        rows = self._rows() if category in (None, "connections") else []
        result = {"state": "read", "category": category or "all", "connections": rows,
                  "response": self._summary(rows), "activity": self._activity()}
        result['input_requests'] = [
            {key: row.get(key) for key in ('id', 'work_id', 'state', 'effect', 'input_expires_at')}
            for row in self._drafts().values() if isinstance(row, dict) and row.get('owner') == owner
            and row.get('category') == 'api_connections' and row.get('state') in ('awaiting-input', 'unknown')
            and category in (None, 'api_connections')]
        channels = {row.get('channel') for row in self._drafts().values()
                    if isinstance(row, dict) and row.get('owner') == owner}
        result['pending_changes'] = [
            {**{key: row.get(key) for key in ('id', 'work_id', 'category', 'effect', 'expires_at')},
             'renewable': row.get('state') in ('expired','failed','unknown') or float(row.get('expires_at') or 0) < self.now(),
             'state': row.get('state'), 'setup_failure': row.get('setup_failure'),
             'recovery_mode': 'observe-only' if row.get('state')=='unknown' else 'retry' if row.get('state')=='failed' else None}
            for channel in sorted(channels, key=str) for row in self.pending_or_renewable(owner, channel)
            if category in (None, row.get('category'))]
        result['setup_failures'] = [
            {**{key:row.get(key) for key in ('id','work_id','state','effect','setup_failure')},
             'retryable': row.get('state')=='failed' and self.service is not None and self.service.mcp_setup_recoverable(row),
             'recoverable': self.service is not None and self.service.mcp_setup_recoverable(row),
             'recovery_mode': 'observe-only' if row.get('state')=='unknown' else 'retry'}
            for row in self._drafts().values() if isinstance(row,dict) and row.get('owner')==owner
            and row.get('state') in ('failed','unknown') and (row.get('category'),row.get('setting'))==('ai_connections','connect')
            and category in (None,row.get('category'))]
        if self.service is None:
            return result
        # #814: the owner settings, redacted: values, labels and allowed choices only.
        # #183: connections keep their own rows (and line) below; their disconnect draft needs no snapshot here.
        settings = {name: self._snapshot(name) for name in SETTINGS if category in (None, name) and name != "connections"}
        lines = [self._category_line(name, row) for name, row in settings.items()]
        lines += [IN_DOUBT_MESSAGE.format(effect=row["effect"]) for row in self.in_doubt()
                  if category in (None, row.get("category"))]
        if category in (None, "connections"):
            lines.append(msg('settings_orchestrator.connections_read', connections=result['response']) if category is None else result["response"])
        if category is None:
            # #208: shown, never offered as a change; only Settings on the Mac switches it.
            on = work_mode.state(self.store)["enabled"]
            work = {"enabled": on, "label": msg('work_mode.label'),
                    "value_label": msg('label.on' if on else 'label.off'), "changeable": False}
            lines.append(msg('work_mode.read_line', state=work["value_label"]))
            return {**result, "settings": settings, "work_mode": work, "response": "\n".join(lines)}
        return {**result, "settings": settings, "response": "\n".join(lines)}

    # -- #814 owner settings ------------------------------------------------------
    def _snapshot(self, category):
        """``{setting: {value, value_label, label, options}}`` of one category, or ``{unavailable}``."""
        try:
            return getattr(self, "_" + category)()
        except Exception:
            return {"unavailable": UNAVAILABLE_MESSAGE}

    def _api_connections(self):
        # #1445 part 3: the code-run set format is the validator's own JSON Schema.
        from .draft_formats import CodeSetDraft, schema_text
        return {'connect': self._row('connect', '', msg('settings_orchestrator.connection_request'), [],
            label=msg('settings_orchestrator.new_api_connection'),
            note=msg('settings_orchestrator.api_input_note'),
            format='JSON: name, hosts, header, scheme, money(boolean), note, subject_field/subject_value(null or strings), '
                   'probe: {url: public HTTPS read URL, required_fields: nonempty JSONPath array}. '
                   'Optional oauth: {token_url: public HTTPS URL, client_auth: basic or body, scope: string array}. '
                   'OAuth client id and secret are each 8-4096 characters and entered only on the input page. '
                   'Or, for an API you will call with code_run (#1380), JSON matching this JSON Schema; code_run sees each '
                   'value as <NAME>_<FIELD> (name upper-cased, - as _); readiness is the stored values, and your code run '
                   'shows whether they work: ' + schema_text(CodeSetDraft))}

    @staticmethod
    def _options(values, labels=None):
        return [{"value": value, "label": (labels or {}).get(value) or VALUE_LABELS.get(value) or value} for value in values]

    @staticmethod
    def _row(setting, value, value_label, options, **extra):
        return {"label": SETTING_LABELS[setting], "value": value, "value_label": value_label, "options": options, **extra}

    def _current_context(self):
        settings = self.service.context_observations.settings()
        enabled = "on" if settings.get("enabled") else "off"
        zone = settings.get("timezone") or ""
        return {"enabled": self._row("enabled", enabled, VALUE_LABELS[enabled], self._options(("on", "off"))),
                "timezone": self._row("timezone", zone, zone or msg('settings_orchestrator.default_timezone'), None,
                                      format=msg('settings_orchestrator.timezone_format'))}

    def _owner_model(self):
        from .owner_model import MAX_DAILY_CALLS
        settings = self.service.owner_model.settings()
        enabled = "on" if settings.get("enabled") else "off"
        cap = str(settings.get("daily_calls"))
        dream = "on" if settings.get("dream") else "off"
        return {"enabled": self._row("enabled", enabled, VALUE_LABELS[enabled], self._options(("on", "off"))),
                "dream": self._row("dream", dream, VALUE_LABELS[dream], self._options(("on", "off"))),
                "daily_calls": self._row("daily_calls", cap, msg('settings_orchestrator.call_count', value=cap), None,
                                         format=msg('settings_orchestrator.daily_calls_format', limit=MAX_DAILY_CALLS))}

    def _family(self):
        """#912: the family members' agents on this Mac; ``add`` takes the new one's display name.

        #934: ``share_site`` / ``unshare_site`` carry the current shares (names
        only) and, for the model to match the owner's words against, the
        sites the owner is signed in to and the family assistants' names.
        """
        from . import family_share
        # #957: every other instance on this Mac (the owner's main one included), never this one, each with
        # its bot's display name and the state its own store reports.
        others = family_share.instances(own=self.store.root)
        assistants = {name: {**row, "state_label": family_share.STATE_LABELS.get(row.get("state"), "")}
                      for name, row in others.items()}
        shares = family_share.listing(self.store, others)
        shared = ", ".join((f"{row['label']}: {row['site']}" if row["delivered"] else msg('settings_orchestrator.share_delivery_pending', label=row['label'], site=row['site'])) for row in shares) or msg('label.none')
        signed_in = self._signed_in_sites()
        family = ", ".join(family_share.describe(name, row) for name, row in others.items() if not row.get("main")) or msg('label.none')
        return {"add": self._row("add", "", family, None,
                                 format=msg('settings_orchestrator.family_name_format'),
                                 note=msg('settings_orchestrator.family_add_note')),
                "share_site": self._row("share_site", "", shared, None, format=msg('settings_orchestrator.share_format'),
                                        note=FAMILY_SHARE_NOTE, assistants=assistants, signed_in_sites=signed_in,
                                        shared=[{"instance": row["instance"], "site": row["site"]} for row in shares],
                                        received_sites=sorted(family_share.received(self.store))),
                "unshare_site": self._row("unshare_site", "", shared, None,
                                          format=msg('settings_orchestrator.unshare_format'),
                                          shared=[{"instance": row["instance"], "site": row["site"]} for row in shares])}

    def _ai_connections(self):
        """#1197: whether Works may use the owner's AI-side connections; unavailable on a family instance."""
        status = self.service.ai_connections_status()
        if not status["available"]:
            return {"unavailable": status["note"]}
        enabled = "on" if status["enabled"] else "off"
        # #1297: the owner's servers per AI (names only) and the trust record per connection.
        from . import owner_mcp, trust_record
        available = self.service.owner_mcp_available()
        confirmed = {engine_id: owner_mcp.confirmed(self.store, engine_id) for engine_id in owner_mcp.ENGINES}
        servers = " · ".join(f"{engine_id}: {', '.join(names) or msg('label.none')}" for engine_id, names in confirmed.items())
        hands = trust_record.hands(self.store)
        recorded = " / ".join(trust_record.describe(row) for row in hands.values()) or msg('label.not_recorded')
        from .agent_runtime import MEMORY_OWNER
        from .owner_forget import OwnerForget
        restores = OwnerForget(self.store).undoable(MEMORY_OWNER, limit=trust_record.MAX_HANDS, kind='trust')
        undoable = []
        for name, record in hands.items():
            try:
                previous = trust_record.undo_snapshot(record, record['revision'])
            except ValueError:
                continue
            undoable.append({'value': name + '|' + record['revision'],
                             'label': trust_record.describe({**record, **previous})})
        return {"enabled": self._row("enabled", enabled, VALUE_LABELS[enabled], self._options(("on", "off")),
                                     note=status["note"]),
                "connect": self._row("connect", "", msg('settings_orchestrator.server_review_on_request'), None,
                                     format="JSON: engine, name, url, source, publisher, licence, required_tools, read_tools, money",
                                     note=msg('settings_orchestrator.mcp_connect_note')),
                "servers": self._row("servers", "", servers, None, format=msg('settings_orchestrator.servers_format'),
                                     note=msg('settings_orchestrator.servers_note'),
                                     available=available, confirmed=confirmed),
                "review": self._row("review", "", recorded, None,
                                    format=msg('settings_orchestrator.review_format'),
                                    note=msg('settings_orchestrator.review_note'),
                                    hands={name: {key: row[key] for key in ('money', 'rung', 'operations', 'state', 'revision', 'history')}
                                           for name, row in hands.items()}),
                "money": self._row("money", "", recorded, None, format=msg('settings_orchestrator.money_format'),
                                   note=msg('settings_orchestrator.money_note')),
                "rung": self._row("rung", "", recorded, None, format=msg('settings_orchestrator.rung_format'),
                                  note=msg('settings_orchestrator.rung_note')),
                "undo": self._row("undo", "", "", undoable,
                                  note=msg('settings_orchestrator.undo_note')),
                "forget": self._row("forget", "", "", self._options(tuple(name for name, record in hands.items()
                                                                                 if record['state'] == 'active')),
                                    note=msg('settings_orchestrator.forget_note')),
                "restore": self._row("restore", "", "", [
                    {'value': row['item']['ref'][6:] + '|' + row['receipt'], 'label': row['item']['summary']}
                    for row in restores], note=msg('settings_orchestrator.restore_note'))}

    def _phone_link(self):
        """#1213: what a one-time phone link can be opened for on this install now (``send``'s options)."""
        kinds = self.service.phone_input_kinds()
        return {"send": self._row("send", "", "", self._options(tuple(kinds), kinds),
                                  note=msg('settings_orchestrator.phone_link_note'))}

    def _preparations(self):
        """#1455: the owner's active reservations; ``cancel`` offers each, ``accept`` the ones waiting for a yes."""
        from . import preparations as prep
        from .current_context import redact_known_secrets
        states = {prep.STATE_PROPOSED: msg('settings_orchestrator.awaiting_acceptance'), prep.STATE_SCHEDULED: msg('settings_orchestrator.scheduled'), prep.STATE_RUNNING: msg('settings_orchestrator.running')}
        rows = [row for row in self.service.preparations.rows() if row.get("state") in states]
        labels = {row["id"]: f"{redact_known_secrets(self.store, prep.proposal_summary(row))} ({states[row['state']]})"
                  for row in rows}
        listed = "\n".join(labels.values()) or msg('label.none')
        proposed = [row["id"] for row in rows if row["state"] == prep.STATE_PROPOSED]
        return {"cancel": self._row("cancel", "", listed, self._options(tuple(labels), labels), format=msg('settings_orchestrator.cancel_reservation_format')),
                "accept": self._row("accept", "", ", ".join(labels[item] for item in proposed) or msg('label.none'),
                                    self._options(tuple(proposed), labels), format=msg('settings_orchestrator.accept_reservation_format'))}

    def _connections(self):
        """#183: the Google connections that can be disconnected now, by connector id."""
        labels = {row["id"]: f"{row['label']} ({state_label(row['state'])})"
                  for row in self.service.connection_disconnect_options()}
        telegram = msg("label.connected" if self.service.store.config("telegram", {}).get("enabled")
                       else "family_share.not_connected")
        return {"disconnect": self._row("disconnect", "", ", ".join(labels.values()) or msg('label.none'),
                                        self._options(tuple(labels), labels), format=msg('settings_orchestrator.disconnect_format'),
                                        note=DISCONNECT_NOTE),
                "connect_chat": self._row("connect_chat", "", f"Telegram: {telegram}",
                                          self._options(("telegram",), {"telegram": "Telegram"}), note=CONNECT_CHAT_NOTE)}

    def _browser(self):
        """#1572: what holds the browser now, and the login windows waiting for the owner (``close_login``'s options)."""
        windows = self.service.open_login_windows()
        occupancy = {"none": msg('settings_orchestrator.browser_idle'), "login": msg('settings_orchestrator.browser_login'), "keepalive": msg('settings_orchestrator.browser_keepalive'),
                     "work": msg('settings_orchestrator.browser_other_work')}[self.service.browser_profile.occupancy()]
        return {"close_login": self._row("close_login", "", msg('settings_orchestrator.browser_occupancy', browser_occupancy=occupancy),
                                         self._options(tuple(windows), windows), format=msg('settings_orchestrator.close_login_format'),
                                         note=msg('settings_orchestrator.close_login_note'))}

    def _skills(self):
        """#961: the switch, and the installed skills for ``add`` / ``remove`` (names and pinned sources only)."""
        status = self.service.skills_status()
        enabled = "on" if status.get("enabled") else "off"
        rows = status.get("skills") or []
        listed = ", ".join((f"{row['skill']} ({row['source']})" if row.get('enabled') else msg('settings_orchestrator.skill_disabled', skill=row['skill'], source=row['source'])) for row in rows) or msg('label.none')
        removable = [row["skill"].split("/", 1)[1] for row in rows if not row["skill"].startswith("agentos/")]
        # #963: Still's published reference skills, listed even while skills are off, addable by name.
        available = [{key: row[key] for key in ("name", "description", "address", "installed")}
                     for row in status.get("available") or ()]
        return {"enabled": self._row("enabled", enabled, VALUE_LABELS[enabled], self._options(("on", "off")),
                                    required_skills=[item['skill']+'@'+item['digest'] for item in rows if item.get('enabled') and item.get('digest')]),
                "add": self._row("add", "", listed, None,
                                 format=msg('settings_orchestrator.skill_source_format'),
                                 note=SKILL_ADD_NOTE, available=available),
                "remove": self._row("remove", "", ", ".join(removable) or msg('label.none'), None, format=msg('settings_orchestrator.skill_name_format'),
                                    installed=removable)}

    def _signed_in_sites(self):
        """The sites the owner's browser is signed in to, names only, from the profile's non-blocking view."""
        profile = getattr(self.service, "browser_profile", None)
        view = getattr(profile, "_jar_view", None)
        if not callable(view):
            return []
        try:
            _state, sessions = view()
        except Exception:
            return []
        return [row["site"] for row in sessions if isinstance(row, dict) and row.get("site")]

    @staticmethod
    def _share_value(setting, value, row):
        """``"<instance>|<site>"`` for a share or a stop (#934), resolved and normalized, or ``SettingsError``."""
        from . import family_share
        # Without a "|" the value is the site: a stop may name it alone; a share still needs the assistant.
        who, _sep, where = value.rpartition("|")
        try:
            site = family_share.normalize_site(where)
        except ValueError as exc:
            raise SettingsError(str(exc)) from None
        shared = row[setting].get("shared") or []
        if setting == "unshare_site" and not who.strip():
            holders = [item["instance"] for item in shared if item["site"] == site]
            if not holders:
                raise SettingsError(msg('family_share.not_shared', site=site))
            if len(holders) > 1:
                raise SettingsError(msg('settings_orchestrator.share_ambiguous', site=site, assistants=', '.join(holders)))
            return f"{holders[0]}|{site}"
        if setting == "share_site" and site in (row["share_site"].get("received_sites") or ()):
            # Review P1-1: a session received from the owner is not this instance's to pass on.
            raise SettingsError(family_share.NOT_YOURS_TEXT.format(site=site))
        try:
            instance = family_share.resolve_instance(who, row["share_site"].get("assistants") or {})
        except ValueError as exc:
            raise SettingsError(str(exc)) from None
        already = any(item["instance"] == instance and item["site"] == site for item in shared)
        if setting == "share_site" and already:
            raise SettingsError(msg('settings_orchestrator.share_already_enabled', instance=instance, site=site))
        if setting == "unshare_site" and not already:
            raise SettingsError(msg('settings_orchestrator.share_already_disabled', instance=instance, site=site))
        return f"{instance}|{site}"

    def _model_lists(self):
        rows = self.store.config("decision_model_lists", {})
        return rows if isinstance(rows, dict) else {}

    def _judgment_ai(self):
        from .decision_routes import ROUTE_NAMES, known_models
        status = self.service.decision_routes.status()
        active, mode = status.get("active") or {}, status.get("mode")
        route = active.get("engine") if active.get("transport") == "subscription_cli" else (
            active.get("provider") if active.get("transport") == "direct_api" else "")
        model = active.get("requested_model") or ""
        models = known_models(route, self._model_lists()) if route else []
        return {"mode": self._row("mode", mode, JUDGMENT_MODE_LABELS.get(mode) or VALUE_LABELS.get(mode, mode or "-"),
                                  self._options(("follow_main", "off"), JUDGMENT_MODE_LABELS)),
                "model": self._row("model", model, model or msg('label.default_model'), self._options(models),
                                   route=ROUTE_NAMES.get(route, route or "-"),
                                   note=msg('settings_orchestrator.judgment_model_note') if mode == "follow_main" else ""),
                "effective": (status.get("effective") or {}).get("text") or ""}

    @staticmethod
    def _ready_route(row):
        """A Main AI route already connected and checked: the only switch targets (#814)."""
        if (row.get("check") or {}).get("state") != "ok":
            return False
        if row.get("kind") == "subscription":
            return bool(row.get("installed")) and (row.get("login") or {}).get("state") != "signed-out"
        return bool((row.get("key") or {}).get("saved"))

    def _main_ai(self):
        from .decision_routes import known_models
        from .main_ai import API_ROUTES, SUBSCRIPTION_ROUTES
        status = self.service.main_ai.status()
        current, routes = status.get("current") or "", status.get("routes") or []
        names = {row["id"]: row["name"] for row in routes}
        options = [{"value": row["id"], "label": row["name"], "destination": row.get("destination") or ""}
                   for row in routes if self._ready_route(row)]
        row = next((item for item in routes if item["id"] == current), {})
        model = row.get("model") or ""
        selectable = (current in SUBSCRIPTION_ROUTES and row.get("model_selectable")) or current in API_ROUTES
        models = known_models(current, self._model_lists()) if selectable else []
        return {"route": self._row("route", current, names.get(current) or (msg('label.other_connection') if current else msg('label.none')), options,
                                   note=msg('settings_orchestrator.main_route_note')),
                "model": self._row("model", model, model or msg('label.default_model'), self._options(models))}

    @staticmethod
    def _category_line(category, row):
        if row.get("unavailable"):
            return msg(_CATEGORY_LINE_IDS[category], details=row['unavailable'])
        parts = [(msg(_SETTING_VALUE_IDS[item['label']], value=item['value_label'])
                  if item['label'] in _SETTING_VALUE_IDS else f"{item['label']}: {item['value_label']}") for item in row.values() if isinstance(item, dict)]
        line = msg(_CATEGORY_LINE_IDS[category], details=' · '.join(parts))
        return msg(_CATEGORY_LINE_IDS[category] + '_effective', details=' · '.join(parts), effective=row['effective']) if row.get('effective') else line

    def _normalized(self, category, setting, value):
        """The canonical allowed value, or a fail-closed ``SettingsError`` (#814)."""
        if not isinstance(category, str) or not isinstance(setting, str):
            raise SettingsError(UNKNOWN_SETTING_MESSAGE)
        if category == "connections" and setting not in ("disconnect", "connect_chat"):
            raise SettingsError(RETIRED_CONTROL_MESSAGE)
        # #208: work mode is switched only by the owner in Settings on the Mac, never by a proposal.
        if category == "work_mode":
            raise SettingsError(msg('work_mode.local_only'))
        if category not in SETTINGS or setting not in SETTINGS[category] or self.service is None:
            raise SettingsError(UNKNOWN_SETTING_MESSAGE)
        if isinstance(value, bool) and setting == "enabled":
            value = "on" if value else "off"
        if isinstance(value, int) and not isinstance(value, bool) and setting == "daily_calls":
            value = str(value)
        if not isinstance(value, str) or len(value) > (4000 if setting == 'connect' and category in ('ai_connections','api_connections') else MAX_VALUE_CHARS):
            raise SettingsError(msg('settings_orchestrator.invalid_value'))
        value = value.strip()
        from .current_context import redact_known_secrets
        if redact_known_secrets(self.store, value) != value:
            raise SettingsError(CREDENTIAL_VALUE_MESSAGE)
        row = self._snapshot(category)
        if row.get("unavailable"):
            raise SettingsError(row["unavailable"])
        if (category, setting) == ('api_connections', 'connect'):
            from .api_input import value as api_value
            return api_value(self.store, value), row
        if (category, setting) == ("ai_connections", "connect"):
            try:
                return self.service.mcp_setup_value(value), row
            except ValueError as exc:
                raise SettingsError(str(exc)) from None
        if (category, setting) == ("current_context", "timezone"):
            from .context_observations import valid_timezone
            try:
                return valid_timezone(canonical_timezone(value)), row
            except ValueError as exc:
                raise SettingsError(msg('settings_orchestrator.error_unchanged', exc=exc)) from None
        if (category, setting) == ("family", "add"):
            # A display name only: letters and spaces, 1-64 characters, no control characters.
            name = " ".join(value.split())
            if not name or len(name) > 64 or any(ord(char) < 32 for char in value):
                raise SettingsError(msg('settings_orchestrator.invalid_family_name'))
            # SKILL-MANAGE-01 (#962): a repeated request never makes a second assistant of the same name.
            # Only an already paired one blocks; an unfinished setup is reused by the setter itself.
            from . import family_share
            same = [(instance, item) for instance, item in (row["share_site"].get("assistants") or {}).items()
                    if " ".join(str(item.get("name") or "").split()).lower() == name.lower() and item.get("state") == "paired"]
            # The listing leaves this instance out (it never offers itself), so its own bot name is checked here.
            own = " ".join(str(family_share.display_name(self.store.root) or "").split())
            if own and own.lower() == name.lower():
                raise SettingsError(msg('settings_orchestrator.family_name_is_self', name=name))
            if same:
                listed = ", ".join(family_share.describe(instance, item) for instance, item in same)
                raise SettingsError(msg('settings_orchestrator.family_name_exists', name=name, listed=listed))
            return name, row
        if (category, setting) in FAMILY_SHARE_SETTINGS:
            return self._share_value(setting, value, row), row
        if (category, setting) in TRUST_SETTINGS:
            return self._trust_value(setting, value, row), row
        if (category, setting) in (("skills", "add"), ("skills", "remove")):
            from .skills import SkillError
            try:
                return self.service.skill_setting_value(setting, value), row
            except SkillError as exc:
                raise SettingsError(msg('settings_orchestrator.error_unchanged', exc=exc)) from None
        if setting == "daily_calls":
            from .owner_model import MAX_DAILY_CALLS
            if not value.isascii() or not value.isdigit() or not 0 <= int(value) <= MAX_DAILY_CALLS:
                raise SettingsError(msg('settings_orchestrator.invalid_daily_calls', limit=MAX_DAILY_CALLS))
            return str(int(value)), row
        allowed = [option["value"] for option in row[setting]["options"] or ()]
        if value not in allowed:
            listed = ", ".join(allowed) or msg('settings_orchestrator.none_available_now')
            raise SettingsError(msg('settings_orchestrator.invalid_choice', label=_CHANGE_LABELS[category, setting], listed=listed))
        return value, row

    def _trust_value(self, setting, value, row):
        """#1297: the canonical scoped trust value, or a fail-closed error.

        Trust drafts name the engine, connection kind and stable identity so a
        confirmation for one hand cannot be replayed on another hand with the
        same display name.
        """
        from . import owner_mcp, trust_record
        parts = [part.strip() for part in value.split("|")]
        if setting == "servers":
            if len(parts) != 2 or parts[0] not in owner_mcp.ENGINES:
                raise SettingsError(msg('settings_orchestrator.invalid_servers_format'))
            names = [name.strip() for name in parts[1].split(",") if name.strip()]
            offered = set(row["servers"]["available"].get(parts[0]) or ())
            missing = [name for name in names if name not in offered]
            if missing:
                raise SettingsError(msg('settings_orchestrator.unknown_servers', engine=parts[0], missing=', '.join(missing), available=', '.join(sorted(offered)) or msg('label.none')))
            if not all(owner_mcp.valid_name(name) for name in names) or len(set(names)) > owner_mcp.MAX_SERVERS:
                raise SettingsError(msg('settings_orchestrator.invalid_server_names', limit=owner_mcp.MAX_SERVERS))
            if names == row["servers"]["confirmed"].get(parts[0]):
                raise SettingsError(msg('settings_orchestrator.servers_unchanged', engine=parts[0]))
            return f"{parts[0]}|{','.join(dict.fromkeys(names))}"
        if len(parts) < 3 or parts[0] not in trust_record.ENGINES or parts[1] not in trust_record.KINDS \
                or not trust_record.valid_hand(parts[2]):
            raise SettingsError(msg('settings_orchestrator.invalid_connection_name'))
        try:
            target = trust_record.connection(parts[0], parts[1], parts[2], parts[2])
        except ValueError:
            raise SettingsError(msg('settings_orchestrator.invalid_connection_scope')) from None
        current = trust_record.hand(self.store, target)
        if setting in ("undo", "forget", "restore"):
            offered = {option['value'] for option in row[setting].get('options') or ()}
            if '|'.join(parts) not in offered:
                raise SettingsError(msg('settings_orchestrator.trust_record_unavailable'))
        elif setting == "review":
            if len(parts) != 5 or not trust_record.valid_operation(parts[3]) or parts[4] not in trust_record.CLASSES:
                raise SettingsError(msg('settings_orchestrator.invalid_review_format'))
            if (current.get("operations") or {}).get(parts[3]) == parts[4]:
                raise SettingsError(msg('settings_orchestrator.operation_unchanged'))
        elif setting == "money":
            if len(parts) != 4 or parts[3] not in ("on", "off"):
                raise SettingsError(msg('settings_orchestrator.invalid_money_format'))
            if bool(current.get("money")) == (parts[3] == "on"):
                raise SettingsError(msg('settings_orchestrator.money_unchanged'))
        else:
            if len(parts) != 4 or parts[3] not in trust_record.RUNGS:
                raise SettingsError(msg('settings_orchestrator.invalid_rung_format'))
            if parts[3] == "mandate" and current.get("money"):
                raise SettingsError(msg('settings_orchestrator.money_mandate_refused'))
            if (current.get("rung") or trust_record.default_rung(bool(current.get("money")))) == parts[3]:
                raise SettingsError(msg('settings_orchestrator.rung_unchanged'))
        return "|".join(parts)

    def _trust_summary(self, setting, after):
        parts = after.split("|")
        if setting == "servers":
            return (msg('settings_orchestrator.servers_summary', engine=parts[0], servers=parts[1].replace(',', ', ')) if parts[1]
                    else msg('settings_orchestrator.servers_disable_summary', engine=parts[0]))
        if setting == "review":
            return msg(
                'settings_orchestrator.review_summary',
                connection=parts[2],
                engine=parts[0],
                kind=parts[1],
                operation=parts[3],
                action_class=TRUST_CLASS_LABELS[parts[4]],
            )
        if setting == "money":
            return msg(
                'settings_orchestrator.money_mark_summary' if parts[3] == 'on' else 'settings_orchestrator.money_unmark_summary',
                connection=parts[2],
                engine=parts[0],
                kind=parts[1],
            )
        if setting == 'undo':
            from . import trust_record
            current = trust_record.hand(self.store, trust_record.connection(*parts[:3]))
            previous = trust_record.undo_snapshot(current, parts[3])
            return msg('settings_orchestrator.undo_summary', record=trust_record.describe({**current, **previous}))
        if setting == 'restore':
            option = next(item for item in self._ai_connections()['restore']['options'] if item['value'] == after)
            return msg('settings_orchestrator.restore_summary', record=option['label'])
        if setting == 'forget':
            return msg('settings_orchestrator.forget_summary', connection=parts[2], engine=parts[0], kind=parts[1])
        return msg('settings_orchestrator.rung_summary', connection=parts[2], engine=parts[0], kind=parts[1], rung=TRUST_RUNG_LABELS[parts[3]])

    def _trust_before(self, setting, after):
        """Current value for the exact scoped trust target in a draft."""
        from . import owner_mcp, trust_record
        parts = after.split("|")
        if setting == "servers":
            return f"{parts[0]}|{','.join(owner_mcp.confirmed(self.store, parts[0]))}"
        target = trust_record.connection(parts[0], parts[1], parts[2], parts[2])
        current = trust_record.hand(self.store, target)
        # Every authority revision invalidates old drafts, including an ABA change or forget/restore.
        return current['revision'] + ':' + current['state'] + ':' + current['epoch']

    def _before(self, category, setting, value, row):
        if (category, setting) == ('api_connections', 'connect'):
            from .api_input import before
            return before(self.store, value)
        if (category, setting) == ("ai_connections", "connect"):
            return self.service.mcp_setup_before(value)
        if (category, setting) == ("connections", "connect_chat"):
            # #438: bound to whether Telegram is connected, so a connect from Settings since makes it stale.
            return f"telegram:{'connected' if self.service.store.config('telegram', {}).get('enabled') else 'disconnected'}"
        if category == "connections":
            # #183: bound to the connection's state and revision, so a reconnect or disconnect since makes it stale.
            return self.service.connection_disconnect_before(value)
        if category == "browser":
            # #1572: bound to the login's state; the value already names the exact window.
            work_id = value.partition("|")[0]
            return f"{value}:{(self.service._browser_login(work_id) or {}).get('state')}"
        if category == "preparations":
            # #1455: bound to the reservation's state, so a run, a cancel or an accept since the draft makes it stale.
            current = self.service.preparations.get(value) or {}
            return f"{value}:{current.get('state')}"
        return self._trust_before(setting, value) if (category, setting) in TRUST_SETTINGS else row[setting]["value"]

    def _describe(self, category, setting, value, row):
        options = {option["value"]: option["label"] for option in row[setting].get("options") or ()}
        if setting == "daily_calls":
            return msg('settings_orchestrator.call_count', value=value)
        return options.get(value) or VALUE_LABELS.get(value) or value or msg('settings_orchestrator.value_not_set')

    @staticmethod
    def _digest(row):
        body = {key: row.get(key) for key in ("id", "owner", "channel", "category", "setting", "before", "after", "expires_at")}
        if "setup_guard" in row:
            body.update(work_id=row.get("work_id"), setup_guard=row["setup_guard"])
        if "continue_work" in row:
            body["continue_work"] = row["continue_work"]
        if "required_skill" in row:
            body["required_skill"] = row["required_skill"]
        if "recovery_of" in row:
            body["recovery_of"] = row["recovery_of"]
        if "observe_only" in row:
            body["observe_only"] = row["observe_only"]
        if "continuation" in row:
            body.update(work_id=row.get("work_id"), continuation=row["continuation"])
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def propose(self, owner, channel, category, setting, value, reason=None, work_id=None, *, continue_work=False, _recovery_of=None, _observe_only=False, required_skill=None):
        """A digest-bound draft of one owner setting change; applies nothing (#814)."""
        if not isinstance(owner, str) or not owner or not isinstance(channel, str) or not channel:
            raise SettingsError(msg('settings_orchestrator.check_owner_channel'))
        if reason is not None and not isinstance(reason, str):
            raise SettingsError(msg('settings_orchestrator.invalid_reason'))
        if not isinstance(continue_work, bool):
            raise SettingsError(msg('settings_orchestrator.invalid_continue_work'))
        if continue_work and (not work_id or not (
                category == "skills" and (setting == "add" or setting == "enabled" and value == "on")
                or setting == 'connect' and category in ('ai_connections', 'api_connections'))):
            raise SettingsError(msg('settings_orchestrator.resume_unsupported'))
        after, row = self._normalized(category, setting, value)
        if (category, setting) == ("connections", "connect_chat"):
            # #438: the token is typed in the terminal that asked; no other conversation can show that prompt.
            if not self.service.terminal_surface.in_conversation(channel):
                raise SettingsError(CONNECT_CHAT_TERMINAL_ONLY)
            if self.service.store.config("telegram", {}).get("enabled"):
                raise SettingsError(msg('settings_orchestrator.telegram_already_connected'))
        if continue_work and setting == "enabled":
            if not isinstance(required_skill, str) or required_skill not in row["enabled"].get("required_skills", []):
                raise SettingsError(msg('settings_orchestrator.select_required_skill'))
        elif required_skill is not None:
            raise SettingsError(msg('settings_orchestrator.unexpected_required_skill'))
        if continue_work and (category, setting) == ("ai_connections", "connect") and not json.loads(after)["required_tools"]:
            raise SettingsError(msg('settings_orchestrator.missing_required_tools'))
        before = self._before(category, setting, after, row)
        if after == before:
            raise SettingsError(msg('settings_orchestrator.setting_unchanged', label=_CHANGE_LABELS[category, setting], value=self._describe(category, setting, after, row)))
        if (category, setting) == ("family", "add"):
            summary = msg('settings_orchestrator.family_add_summary', after=after)
        elif (category, setting) == ("skills", "add"):
            from .skills import parse_source
            source = parse_source(after)
            name = source["path"].rstrip("/").split("/")[-1]
            replacing = any(row["skill"].split("/", 1)[-1] == name and not row["skill"].startswith("agentos/")
                            for row in self.service.skills_status().get("skills") or ())
            summary = (msg(
                'settings_orchestrator.skill_replace_summary' if replacing else 'settings_orchestrator.skill_add_summary',
                repo=source['repo'],
                path=source['path'],
                revision=source['revision'][:7],
            ))
            if not self.service.skills_status().get("enabled"):
                summary = msg('settings_orchestrator.skill_enable_summary', summary=summary)

        elif (category, setting) == ("skills", "remove"):
            summary = msg('settings_orchestrator.skill_remove', after=after)
        elif (category, setting) == ("connections", "connect_chat"):
            summary = msg('settings_orchestrator.connect_chat_summary')
        elif category == "connections":
            summary = msg('settings_orchestrator.disconnect_summary', connection=self._describe(category, setting, after, row))
        elif category == "browser":
            summary = (msg('settings_orchestrator.close_login_summary', window=self._describe(category, setting, after, row)))
        elif category == "preparations":
            summary = (msg('settings_orchestrator.cancel_reservation_summary', reservation=self._describe(category, setting, after, row)) if setting == "cancel"
                       else msg('settings_orchestrator.accept_reservation_summary', reservation=self._describe(category, setting, after, row)))
        elif (category, setting) == ('api_connections', 'connect'):
            data = json.loads(after)
            if 'fields' in data:
                # #1380: a code-run secret set; the owner sees what each value is and how code uses it.
                # #519: in the owner's words, one fact per line; every authority detail the draft carries
                # (destinations, which values code sees, the requests sent without approval, money) is kept.
                from .api_input import kept_fields
                kept = kept_fields(self.service.store, data)

                def labels(items):
                    return ', '.join(item['label'] for item in items)
                asked = [item for item in data['fields'] if item['name'] not in kept]
                masked = [item for item in data['fields'] if item['use'] == 'header_or_body']
                in_code = [item for item in data['fields'] if item['use'] == 'in_code']
                lines = [msg('settings_orchestrator.api_code_title', name=data['name'], hosts=', '.join(data['hosts']))]
                if asked:
                    lines.append(msg('settings_orchestrator.api_values_to_enter', labels=labels(asked)))
                if kept:
                    lines.append(msg('settings_orchestrator.api_values_kept',
                                     labels=labels(item for item in data['fields'] if item['name'] in kept)))
                if masked:
                    lines.append(msg('settings_orchestrator.api_values_masked', labels=labels(masked)))
                if in_code:
                    lines.append(msg('settings_orchestrator.api_values_in_code', labels=labels(in_code)))
                if data['reads']:
                    lines.append(msg('settings_orchestrator.api_reads', reads=', '.join(data['reads'])))
                lines.append(msg('settings_orchestrator.api_money' if data['money'] else 'settings_orchestrator.api_no_money'))
                summary = '\n'.join(lines)
            else:
                summary = (msg(
                    'settings_orchestrator.api_connect_summary',
                    name=data['name'],
                    hosts=', '.join(data['hosts']),
                    probe_url=data['probe']['url'],
                    subject=data['subject_value'] or msg('settings_orchestrator.subject_not_set'),
                    money=msg('settings_orchestrator.yes') if data['money'] else msg('settings_orchestrator.no'),
                ))
            if data.get('oauth'):
                summary += "\n" + msg(
                    'settings_orchestrator.oauth_destination',
                    token_url=data['oauth']['token_url'],
                    scope=', '.join(data['oauth']['scope']) or msg('settings_orchestrator.default_scope'),
                )
        elif (category, setting) == ("ai_connections", "connect"):
            data = json.loads(after)
            summary = '\n'.join((
                msg('settings_orchestrator.mcp_connect_line', engine=data['engine'], name=data['name'], url=data['url']),
                msg('settings_orchestrator.mcp_connect_source', source=data['source'], publisher=data['publisher'],
                    licence=data['licence']),
                msg('settings_orchestrator.mcp_connect_required_tools',
                    required_tools=', '.join(data['required_tools']) or msg('label.none')),
                msg('settings_orchestrator.mcp_connect_read_tools', read_tools=', '.join(data['read_tools']) or msg('label.none'),
                    money=msg('settings_orchestrator.yes') if data['money'] else msg('settings_orchestrator.no'))))
        elif (category, setting) in TRUST_SETTINGS:
            summary = self._trust_summary(setting, after)
        elif (category, setting) == ("phone_link", "send"):
            summary = msg('settings_orchestrator.phone_link_summary', link=self._describe(category, setting, after, row))
        elif (category, setting) in FAMILY_SHARE_SETTINGS:
            from . import family_share
            instance, _sep, site = after.partition("|")
            label = family_share.names(row["share_site"].get("assistants") or {}).get(instance, instance)
            summary = (msg('settings_orchestrator.share_summary', label=label, site=site) if setting == "share_site"
                       else msg('settings_orchestrator.unshare_summary', label=label, site=site))
        else:
            summary = (msg(
                'settings_orchestrator.change_summary',
                label=_CHANGE_LABELS[category, setting],
                before=self._describe(category, setting, before, row),
                after=self._describe(category, setting, after, row),
            ))
        note = row[setting].get("note") or ""
        if (category, setting) == ("main_ai", "route"):
            destination = next((option.get("destination") for option in row["route"]["options"] if option["value"] == after), "")
            note = msg('settings_orchestrator.route_destination_note', destination=destination) if destination else note
        reason = reason.strip()[:MAX_REASON_CHARS] if isinstance(reason, str) else ""
        from .current_context import redact_known_secrets
        now = self.now()
        draft = {"id": uuid.uuid4().hex[:12], "owner": owner, "channel": channel, "category": category, "setting": setting,
                 "target": f"{category}.{setting}", "before": before, "after": after, "effect": summary, "note": note,
                 "reason": redact_known_secrets(self.store, reason), "work_id": work_id if isinstance(work_id, str) else None,
                 "created_at": now, "expires_at": now + self.TTL_SECONDS, "state": "awaiting-confirmation"}
        if _recovery_of is not None:
            draft['recovery_of'] = _recovery_of
        if _observe_only:
            draft['observe_only'] = True
        if self.service is not None:
            if (category, setting) == ('api_connections', 'connect'):
                from .api_input import guard, before as api_before
                draft['setup_guard'] = guard(self.service, draft)
                if api_before(self.store, after) != before:
                    raise SettingsError(STALE_MESSAGE)
            if (category, setting) == ("ai_connections", "connect"):
                draft["setup_guard"] = self.service.mcp_setup_guard(draft)
                if self.service.mcp_setup_before(after) != before:
                    raise SettingsError(STALE_MESSAGE)
            if continue_work:
                draft["continue_work"] = True
                if required_skill is not None:
                    draft["required_skill"] = required_skill
                    skill, _, version = required_skill.partition('@')
                    draft["note"] = (msg('settings_orchestrator.required_skill_note', note=draft['note'], skill=skill, version=version[:8]) if draft['note'] else ' ' + msg('settings_orchestrator.required_skill_note_only', skill=skill, version=version[:8]))
                draft["note"] = (msg('settings_orchestrator.continue_work_note', note=draft['note']) if draft['note'] else ' ' + msg('settings_orchestrator.continue_work_note_only'))
            continuation = self.service.settings_continuation(draft)
            if continuation is not None:
                draft["continuation"] = continuation
            elif continue_work:
                raise SettingsError(msg('settings_orchestrator.resume_owner_unknown'))
        note = draft["note"]
        draft["digest"] = self._digest(draft)
        with self._confirming:
            self._put_drafts({draft["id"]: draft})
            self._audit(draft, "drafted")
        return {"state": "awaiting-confirmation", "draft_id": draft["id"], "digest": draft["digest"],
                "category": category, "setting": setting, "before": before, "after": after, "summary": summary,
                "note": note, "expires_at": draft["expires_at"], "requires_owner_confirmation": True, "applied": False,
                "continues_work": bool(draft.get("continuation")),
                "response": summary + (f"\n{note}" if note else "") + "\n" + msg('settings_orchestrator.confirmation_required')}

    def _apply(self, row):
        """Run the service's own setter; ``applied``, or ``requested`` when it only queued the change."""
        category, setting, after = row["category"], row["setting"], row["after"]
        if category == 'api_connections':
            from .api_input import reads_update, start
            if reads_update(self.service, row):
                # Live 2026-10-14: only the set's reviewed reads changed; no keys are asked for again.
                self.__dict__.setdefault('_apply_text', {})[row['id']] = msg('settings_orchestrator.reads_added')
                return 'applied'
            self.__dict__.setdefault('_apply_text', {})[row['id']] = start(self.service, row)
            return 'awaiting-input'
        elif category == "current_context":
            self.service.set_current_context({"enabled": after == "on"} if setting == "enabled" else {"timezone": after})
        elif (category, setting) in FAMILY_SHARE_SETTINGS:
            # #934: the push (or the revocation) runs now over loopback; the receipt's text is the answer.
            instance, _sep, site = after.partition("|")
            receipt = (self.service.share_site(instance, site) if setting == "share_site"
                       else self.service.unshare_site(instance, site))
            self.__dict__.setdefault("_apply_text", {})[row["id"]] = (receipt or {}).get("response")
        elif category == "family":
            # #912: runs in the background; the link, then the outcome, follow in the confirming conversation.
            notify = self.__dict__.get("_family_notify", {}).pop(row["id"], None)
            self.service.start_family_setup(after, notify=notify)
            return "requested"
        elif (category, setting) in TRUST_SETTINGS:
            result = self.service.apply_trust_setting(setting, after, expected_revision=row['before'].partition(':')[0])
            if isinstance(result, dict) and result.get('response'):
                self.__dict__.setdefault("_apply_text", {})[row["id"]] = result['response']
        elif (category, setting) == ("ai_connections", "connect"):
            result = self.service.apply_mcp_setup(row)
            self.__dict__.setdefault("_apply_text", {})[row["id"]] = result["response"]
        elif category == "ai_connections":
            self.service.set_ai_connections({"enabled": after == "on"})
        elif category == "phone_link":
            # #1213: the link goes to the paired Telegram chat; its text is the answer.
            self.__dict__.setdefault("_apply_text", {})[row["id"]] = self.service.start_phone_input(after)
        elif (category, setting) == ("connections", "connect_chat"):
            # #438: the terminal shows its hidden token prompt; the token goes from there to ``connect_telegram``.
            if not self.service.terminal_surface.request_input(after):
                raise ValueError(CONNECT_CHAT_TERMINAL_ONLY)
            self.__dict__.setdefault("_apply_text", {})[row["id"]] = msg('settings_orchestrator.connect_chat_opened')
            return "requested"
        elif category == "connections":
            # #183: the same disconnect as Settings (local stop, credential delete, Google revocation request).
            receipt = self.service.apply_connection_disconnect(row)
            self.__dict__.setdefault("_apply_text", {})[row["id"]] = (receipt or {}).get("message")
        elif category == "browser":
            # #1572: the prompt's own 건너뛰기 settlement for exactly this window; its text is the answer.
            self.__dict__.setdefault("_apply_text", {})[row["id"]] = self.service.close_login_window_by_owner(after)
        elif category == "preparations":
            # #1455: the same operations as the Settings list (#659), confirmed by the owner in the conversation.
            self.service.preparation_request({"operation": setting, "id": after})
        elif category == "skills":
            # #961: the service's own setters; adding returns the installed identity as the answer.
            receipt = self.service.apply_skill_setting(setting, after)
            self.__dict__.setdefault("_apply_text", {})[row["id"]] = (receipt or {}).get("response")
        elif category == "owner_model":
            self.service.owner_model_request({"operation": "set", **({setting: after == "on"} if setting in ("enabled", "dream")
                                                                      else {"daily_calls": int(after)})})
        elif (category, setting) == ("judgment_ai", "mode"):
            self.service.activate_decision_route({"transport": after})
            # #814 review P2-1: following the Main AI queues a background qualification
            # (#685/#760); the Judgment AI changes only when it passes.
            if after == "follow_main":
                return "requested"
        elif category == "judgment_ai":
            active = self.service.decision_routes.status().get("active") or {}
            if active.get("transport") == "subscription_cli":
                self.service.activate_decision_route({"transport": "subscription_cli", "engine": active.get("engine"),
                                                      "model_policy": "explicit", "model": after})
            else:
                self.service.activate_decision_route({"transport": "direct_api", "model": after})
        elif setting == "route":
            self.service.activate_main_ai({"route": after})
        else:
            self.service.activate_main_ai({"route": self.service.main_ai.current(), "model": after})
        return "applied"

    def _settle(self, draft_id, state, terminal, error_class=None, *, expected=None, **fields):
        """Update only this draft, with conditional admission/cancellation across processes."""
        with self._confirming:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                found = db.execute("SELECT value FROM config WHERE key='settings_change_drafts'").fetchone()
                rows = json.loads(found['value']) if found else {}
                row = rows.get(draft_id)
                if expected is not None and (not row or row.get('trust_revoked')
                        or any(row.get(key) != value for key, value in expected.items())
                        or state != 'canceled' and 'digest' in expected and self._digest(row) != row.get('digest')):
                    raise SettingsError(STALE_MESSAGE)
                allowed = {
                    'applying': ('awaiting-confirmation', 'awaiting-input'),
                    'awaiting-input': ('applying',),
                    'expired': ('awaiting-confirmation', 'awaiting-input'),
                    'satisfied': ('awaiting-confirmation',),
                    'renewed': ('awaiting-confirmation', 'expired', 'failed', 'unknown'),
                    'canceled': ('awaiting-confirmation', 'awaiting-input', 'expired', 'failed', 'unknown'),
                    'cancelled': ('awaiting-confirmation', 'awaiting-input', 'expired'),
                    'applied': ('applying',), 'requested': ('applying',),
                    'unknown': ('applying',),
                    'failed': ('awaiting-confirmation', 'applying', 'expired'),
                }
                if row and row.get('category') == 'api_connections' and row.get('api_receipt'):
                    allowed['applied'] = ('applying', 'unknown')
                if row and row.get('category') == 'api_connections' and not row.get('api_receipt'):
                    allowed['expired'] = (*allowed['expired'], 'applying', 'unknown')
                if row and not row.get('trust_revoked') and row.get('state') in allowed.get(state, ()):
                    if state == 'applying' and row.get('category') == 'api_connections' and row.get('state') == 'awaiting-input':
                        attempts = sum(item.get('api_attempts', 0) for item in rows.values()
                                       if isinstance(item, dict) and item.get('work_id') == row.get('work_id'))
                        if attempts >= 3:
                            raise SettingsError(msg('settings_orchestrator.api_attempt_limit'))
                        fields['api_attempts'] = row.get('api_attempts', 0) + 1
                    if (state=='applying' and (row.get('category'),row.get('setting'))==('ai_connections','connect')
                            and sum(1 for item in rows.values() if isinstance(item,dict)
                                    and item.get('work_id')==row.get('work_id') and 'applying_at' in item
                                    and (item.get('category'),item.get('setting'))==('ai_connections','connect'))>=3):
                        raise SettingsError(msg('settings_orchestrator.connection_attempt_limit'))
                    row.update(state=state, **fields)
                    db.execute('UPDATE config SET value=? WHERE key=?',
                               (json.dumps(rows), 'settings_change_drafts'))
            if row and terminal:
                self._audit(row, row['state'], error_class)
            return row

    def reconcile(self):
        """#814 review: a draft ``applying`` that this process is not applying was cut off (a restart).

        Its setter may or may not have committed, so it is settled ``unknown``
        (audited ``interrupted``), never ``applied`` or ``failed``, and never
        re-applied; ``read`` tells the owner to check the current value.  Run
        at service start, on every read and before every confirm.
        """
        with self._confirming:
            if self.service is not None:
                from .api_input import reconcile
                reconcile(self.service, self)
            for row in list(self._drafts().values()):
                if isinstance(row, dict) and row.get("state") == "applying" and row.get("id") not in self._in_flight:
                    self._settle(row["id"], "unknown", "unknown", "interrupted", settled_at=self.now())

    def in_doubt(self, within=24 * 3600):
        """Drafts whose outcome a restart left unknown in the last day, oldest first."""
        now = self.now()
        rows = [row for row in self._drafts().values() if isinstance(row, dict) and row.get("state") == "unknown"
                and now - float(row.get("settled_at") or 0) <= within]
        return sorted(rows, key=lambda row: row.get("settled_at") or 0)

    def _finish(self, row):
        """Apply one checked draft and settle it; the owner-facing result, or ``SettingsError``."""
        try:
            outcome = self._apply(row)
        except Exception as exc:
            from .mcp_setup import SetupFailure
            setup = isinstance(exc, SetupFailure) and (row.get('category'),row.get('setting'))==('ai_connections','connect')
            state = exc.settled_state if setup else 'failed'
            fields = ({'setup_failure': {'phase':exc.phase,'state':exc.observed_state,'retryable':exc.retryable},
                       'settled_at':self.now()} if setup else {'settled_at': self.now()} if row.get('category') == 'api_connections' else {})
            self._settle(row["id"], state, state, type(exc).__name__, **fields)
            if setup and self.service is not None:
                self.service.resume_settings_work()
            message = str(exc) if isinstance(exc, ValueError) and str(exc) else msg('settings_orchestrator.apply_failed_fallback')
            raise SettingsError(message) from None
        outcome_fields = {}
        if outcome == 'awaiting-input':
            session = self.service.phone_input_session()
            outcome_fields['input_expires_at'] = session.expires if session is not None else None
        settled = self._settle(row["id"], outcome, outcome, **outcome_fields)
        if settled.get('trust_revoked'):
            self.__dict__.get('_apply_text', {}).pop(row['id'], None)
            return {'state': 'cancelled', 'draft_id': row['id'], 'applied': False,
                    'response': msg('settings_orchestrator.trust_revoked_receipt')}
        if settled.get('state') != outcome:
            self.__dict__.get('_apply_text', {}).pop(row['id'], None)
            raise SettingsError(IN_DOUBT_MESSAGE.format(effect=row['effect']))
        if self.service is not None:
            self.service.resume_settings_work()
        response = ((FAMILY_REQUESTED_MESSAGE if row.get("category") == "family" else FOLLOW_REQUESTED_MESSAGE)
                    if outcome == "requested" else msg('settings_orchestrator.changed_receipt', effect=row['effect']))
        # #934: a share's receipt says what was delivered (names and counts); it replaces the generic line.
        response = self.__dict__.get("_apply_text", {}).pop(row["id"], None) or response
        return {"state": outcome, "draft_id": row["id"], "target": row["target"], "before": row["before"],
                "after": row["after"], "response": response}

    def _admit(self, row, digest):
        """Check one draft is still confirmable and move it to ``applying``, in flight (exactly once)."""
        with self._confirming:
            row = self._drafts().get(row["id"]) or row
            if row.get("state") in ("applied", "requested"):
                raise SettingsError(msg('settings_orchestrator.draft_already_applied'))
            if row.get("state") == "unknown":
                raise SettingsError(IN_DOUBT_MESSAGE.format(effect=row["effect"]))
            if row.get("state") != "awaiting-confirmation":
                raise SettingsError(msg('settings_orchestrator.draft_no_longer_confirmable'))
            if not hmac.compare_digest(str(row.get("digest") or ""), digest) or self._digest(row) != row.get("digest"):
                raise SettingsError(msg('settings_orchestrator.draft_digest_mismatch'))
            if self.now() > float(row.get("expires_at") or 0):
                self._settle(row["id"], "expired", "expired")
                raise SettingsError(msg('settings_orchestrator.draft_expired'))
            if ((row.get('category'),row.get('setting'))==('ai_connections','connect')
                    and self.service.mcp_setup_attempts(row.get('work_id'))>=3):
                self._settle(row['id'],'failed','failed','setup-attempt-budget')
                raise SettingsError(msg('settings_orchestrator.connection_attempt_limit'))
            row = self._settle(row["id"], "applying", None,
                               expected={'state': 'awaiting-confirmation', 'digest': digest}, applying_at=self.now())
            if row.get('trust_revoked'):
                raise SettingsError(msg('settings_orchestrator.trust_revoked_confirmation'))
            self._in_flight.add(row["id"])
            return row

    def _before_covered(self, row, current_before):
        """Exact prior state, or the sole commuting-new-read exception, for apply and renewal."""
        if current_before == row['before']:
            return True
        if row['category'] != 'ai_connections' or row['setting'] != 'review':
            return False
        from . import trust_record
        parts = row['after'].split('|')
        previous, observed = row['before'].split(':'), current_before.split(':')
        if len(previous) != 3 or previous[1:] != observed[1:] or parts[-1] != 'read':
            return False
        record = trust_record.hand(self.store, trust_record.connection(*parts[:3]))
        return trust_record.read_revision_covers(record, previous[0], parts[3])

    def _checked_apply(self, row):
        """Compare-and-apply (#814 review): re-check ``before``, then the setter.  The caller holds the category lock."""
        try:
            try:
                if ((row.get('category'), row.get('setting')) == ('ai_connections', 'connect')
                        and self.service.satisfy_admitted_mcp_setup(row)):
                    self.service.resume_settings_work()
                    return {'state': 'satisfied', 'draft_id': row['id'], 'applied': False,
                            'response': msg('settings_orchestrator.connection_ready_elsewhere')}
                after, current = self._normalized(row["category"], row["setting"], row["after"])
                current_before = self._before(row["category"], row["setting"], after, current)
                if after != row["after"] or not self._before_covered(row, current_before):
                    raise SettingsError(STALE_MESSAGE)
            except Exception as exc:
                self._settle(row["id"], "failed", "failed", type(exc).__name__)
                message = str(exc) if isinstance(exc, ValueError) and str(exc) else msg('settings_orchestrator.apply_failed_fallback')
                raise SettingsError(message) from None
            return self._finish(row)
        finally:
            with self._confirming:
                self._in_flight.discard(row["id"])

    def _worker(self):
        """The one background worker: queued slow drafts, in order, each compare-and-apply under its category lock."""
        while True:
            with self._confirming:
                if not self._queue:
                    self._worker_running = False
                    return
                row, notify = self._queue.pop(0)
            with self._category_locks[row["category"]]:
                try:
                    text = self._checked_apply(row)["response"]
                except Exception as exc:
                    text = (msg('settings_orchestrator.apply_failed', label=self.short_label(row), reason=exc)
                            if isinstance(exc, SettingsError) and str(exc)
                            else msg('settings_orchestrator.apply_failed_plain', label=self.short_label(row)))
            try:
                notify(text)
            except Exception:
                pass

    def _enqueue(self, row, notify):
        """Queue one admitted slow draft on the background worker; start it when idle."""
        with self._confirming:
            self._queue.append((row, notify))
            start = not self._worker_running
            self._worker_running = True
        if start:
            thread = threading.Thread(target=self._worker, daemon=True, name="agentos-settings-apply")
            try:
                thread.start()
            except Exception:
                # #814 review P3: nothing ran; every queued draft fails and nothing stays in flight.
                with self._confirming:
                    items, self._queue, self._worker_running = self._queue, [], False
                    for item, _notify in items:
                        self._in_flight.discard(item["id"])
                        self._settle(item["id"], "failed", "failed", "worker-start")
                raise SettingsError(WORKER_START_MESSAGE) from None
            self.thread = thread
        return {"state": "applying", "draft_id": row["id"], "target": row["target"], "before": row["before"],
                "after": row["after"],
                "response": msg('settings_orchestrator.applying', label=self.short_label(row))}

    def _confirm_owner_setting(self, row, digest, notify=None):
        """Apply one awaiting draft exactly once, after re-checking it is still current (#814).

        With ``notify`` (a follow-up to the confirming conversation) a slow
        setter is queued on the one background worker and ``notify`` gets its
        result; the caller is answered at once.  Otherwise the stale check and
        the setter run here under the category lock.
        """
        if notify is not None and (row.get("category"), row.get("setting")) in SLOW_SETTINGS:
            return self._enqueue(self._admit(row, digest), notify)
        if row.get("category") == "family":
            # #913 review P2-1: the family setup's link and outcome go to the conversation that confirmed it.
            self.__dict__.setdefault("_family_notify", {})[row.get("id")] = notify
        category_lock = self._category_locks.get(row.get("category"))
        if category_lock is None or not category_lock.acquire(timeout=APPLY_WAIT_SECONDS):
            raise SettingsError(BUSY_MESSAGE)
        try:
            return self._checked_apply(self._admit(row, digest))
        finally:
            category_lock.release()

    def pending_drafts(self):
        """Every draft still awaiting the owner (not expired), oldest first."""
        now = self.now()
        rows = [row for row in self._drafts().values() if isinstance(row, dict)
                and row.get("state") == "awaiting-confirmation" and float(row.get("expires_at") or 0) >= now]
        return sorted(rows, key=lambda row: (row.get("created_at") or 0, row["id"]))

    def pending_for_conversation(self, owner, channel):
        """This conversation's drafts still awaiting the owner, oldest first (#855)."""
        return [row for row in self.pending_drafts() if row.get("owner") == owner and row.get("channel") == channel]

    def settle_pending(self, owner, channel, rows, apply, notify=None):
        """Confirm (``apply``) or cancel every offered draft of one conversation; one owner-facing result (#855).

        The same path as the 적용 / 바꾸지 않음 buttons: each draft goes through
        ``confirm`` (digest, TTL, stale check, audit) or ``cancel``.
        """
        lines, states = [], []
        prepared = []
        # Renew the complete offered set before any setter can reconcile its
        # Work. Otherwise the first apply consumes the other expired waits.
        with self._confirming:
            for row in rows:
                try:
                    if apply and (row.get("state") != "awaiting-confirmation"
                                  or float(row.get("expires_at") or 0) < self.now()):
                        row = self._renew(owner, channel, row)
                    prepared.append((row, None))
                except SettingsError as exc:
                    prepared.append((row, exc))
        for row, error in prepared:
            try:
                if error is not None:
                    raise error
                if row.get("state") != "awaiting-confirmation" or float(row.get("expires_at") or 0) < self.now():
                    # Only time ran out: the owner's answer carries the exact change forward (or declines it).
                    if not apply:
                        with self._confirming:
                            current = self._owned(owner, channel, row['id'])
                            if (current.get('state') not in ('awaiting-confirmation', 'expired', 'failed', 'unknown')
                                    or current.get('digest') != row.get('digest')):
                                raise SettingsError(STALE_MESSAGE)
                            self._settle(row["id"], "canceled", "canceled",
                                         expected={'state': current['state'], 'digest': row['digest']})
                        states.append("canceled")
                        lines.append(RECOVERY_DECLINED_MESSAGE
                                     if current.get('setup_failure') or current.get('recovery_of') or current.get('state')=='unknown' else msg('settings_orchestrator.the_settings_change_was_canceled_nothing'))
                        continue
                    row = self._renew(owner, channel, row)
                result = (self.confirm(owner, channel, row["id"], row.get("digest", ""), notify) if apply
                          else self.cancel(owner, channel, row["id"]))
                states.append(result.get("state"))
                lines.append(result.get("response") or msg('service.done'))
            except SettingsError as exc:
                states.append("failed")
                lines.append(f"{self.short_label(row)}: {exc}")
        return {"state": states[0] if len(states) == 1 else ("mixed" if len(set(states)) > 1 else states[0]),
                "drafts": [row["id"] for row in rows], "response": "\n".join(lines)}

    def pending_for_work(self, work_id):
        """This Work's drafts still awaiting the owner, oldest first (#814)."""
        return [row for row in self.pending_drafts() if row.get("work_id") == work_id]

    #: How long after its time ran out a draft can still be carried forward by the owner's own yes / 적용.
    RENEW_GRACE_SECONDS = 60 * 60

    def _timed_out(self, owner, channel, work_id=None):
        now = self.now()
        def deadline(row):
            return float(row.get('settled_at') or 0) if row.get('category') == 'api_connections' and row.get('state') == 'expired' else float(row.get('expires_at') or 0)
        return sorted((row for row in self._drafts().values() if isinstance(row, dict)
                       and row.get("owner") == owner and row.get("channel") == channel
                       and (work_id is None or row.get("work_id") == work_id)
                       and (row.get("state") == "expired" or row.get("state") == "awaiting-confirmation")
                       and deadline(row) <= now
                       and now - deadline(row) <= self.RENEW_GRACE_SECONDS),
                      key=lambda row: (row.get("created_at") or 0, row["id"]))

    def pending_or_renewable(self, owner, channel, work_id=None):
        """Awaiting, recently expired and recoverable failed drafts, oldest first.

        An owner's yes / 적용 carries the second kind forward (``settle_pending``);
        nothing here applies anything by itself.
        """
        rows = [row for row in self.pending_drafts() if row.get("owner") == owner and row.get("channel") == channel
                and (work_id is None or row.get("work_id") == work_id)]
        failed = [row for row in self._drafts().values() if isinstance(row,dict)
                  and row.get('owner')==owner and row.get('channel')==channel
                  and (work_id is None or row.get('work_id')==work_id)
                  and 0 <= self.now()-float(row.get('settled_at') or 0) <= self.RENEW_GRACE_SECONDS
                  and self._recoverable(row)]
        return sorted([*rows, *self._timed_out(owner, channel, work_id), *failed], key=lambda row: (row.get("created_at") or 0, row["id"]))

    def _recoverable(self, row):
        if self.service is None:
            return False
        if row.get('category') == 'api_connections':
            if row.get('state') != 'failed' or row.get('api_receipt'):
                return False
            from .api_input import guard, before
            try:
                attempts = [item for item in self._drafts().values() if isinstance(item, dict)
                            and item.get('work_id') == row.get('work_id') and item.get('category') == 'api_connections']
                return (sum('applying_at' in item for item in attempts) < 3
                        and sum(item.get('api_attempts', 0) for item in attempts) < 3
                        and guard(self.service, row) == row.get('setup_guard')
                        and before(self.store, row['after']) == row['before'])
            except (ValueError, KeyError, TypeError):
                return False
        return self.service.mcp_setup_recoverable(row)

    def _renew(self, owner, channel, row):
        """Renew exact expiry/failure, or bind unknown setup to observation only."""
        with self._confirming:
            current = self._owned(owner, channel, row['id'])
            retry = current.get('state') in ('failed','unknown') and self._recoverable(current)
            if ((current.get('state') not in ('awaiting-confirmation', 'expired') and not retry)
                    or current.get('digest') != row.get('digest')):
                raise SettingsError(STALE_MESSAGE)
            draft = self.propose(owner, channel, row["category"], row["setting"], row["after"], row.get("reason"), row.get("work_id"),
                                 continue_work=row.get("continue_work", False), required_skill=row.get("required_skill"),
                                 _recovery_of=row['id'] if retry else row.get('recovery_of'),
                                 _observe_only=current['state']=='unknown' if retry else row.get('observe_only',False))
            fresh=self._drafts()[draft['draft_id']]
            if (not self._before_covered(row, draft['before'])
                    or (retry or row.get('category') == 'api_connections') and fresh.get('setup_guard')!=row.get('setup_guard')):
                # The owner approved the change from the value they saw; a later value is not covered.
                self._settle(draft["draft_id"], "canceled", None)
                self._settle(row["id"], "failed", "failed", "stale")
                raise SettingsError(STALE_MESSAGE)
            try:
                self._settle(row["id"], "renewed", None,
                             expected={'state': current['state'], 'digest': row['digest']})
            except SettingsError:
                self._settle(draft["draft_id"], "canceled", None)
                raise
            return self._drafts()[draft["draft_id"]]

    def renew_expired(self, owner, channel, work_id, fingerprint):
        """Fresh drafts of the changes a tapped message offered, when they are exactly the offered set and only timed out.

        The message showed the exact effects and the tap approves those values; each new
        draft still passes the stale-state check when confirmed.  A superseded, settled
        or canceled draft is never revived.
        """
        old = self._timed_out(owner, channel, work_id)
        for start in range(len(old)):
            if self.drafts_digest(old[start:]) == fingerprint:
                return [self._renew(owner, channel, row) for row in old[start:]]
        return []

    @staticmethod
    def short_label(row):
        """The draft named in a few words, for the lines after its confirmation (#519).

        The full ``effect`` is shown once, with the confirmation; the progress
        and result lines name it only, so a long summary is never repeated.
        """
        if (row.get("category"), row.get("setting")) == ("api_connections", "connect"):
            try:
                return msg('settings_orchestrator.api_short', name=json.loads(row["after"])["name"])
            except (KeyError, TypeError, ValueError):
                pass
        return str(row.get("effect") or "").split("\n", 1)[0]

    @staticmethod
    def drafts_digest(rows):
        """The fingerprint of exactly these drafts (ids and digests) a confirmation message offers (#814)."""
        body = [[row["id"], row.get("digest")] for row in rows]
        return hashlib.sha256(json.dumps(body).encode()).hexdigest()

    @staticmethod
    def confirmation_text(rows):
        lines = [msg('settings_orchestrator.this_settings_change_was_requested_in')]
        for row in rows:
            # #511: catalog lines, so each surface shows them in its language.
            lines.append(msg('settings_orchestrator.draft_line_with_note', effect=row['effect'], note=row['note'])
                         if row.get("note") else msg('settings_orchestrator.draft_line', effect=row['effect']))
            if row.get("reason"):
                lines.append("  " + msg('settings_orchestrator.draft_reason', reason=row['reason']))
        return "\n".join(lines)

    def work_tools(self, owner, channel, work_id, telegram=False):
        """``settings_read`` / ``settings_change`` bound to one Work (#814).

        A change is only a draft bound to this conversation; the model gets
        no digest and no way to confirm.  The owner confirms in the same
        conversation: the 적용 button, or a plain yes (#855).  ``telegram`` is
        kept for callers; the wording no longer differs by channel.
        """
        def call(action, args):
            args = args if isinstance(args, dict) else {}
            if action == "capability_search":
                return self.service.search_capabilities(work_id, args)
            if action == "settings_read":
                category = args.get("category") or None
                result = self.read(owner, category)
                return {key: result[key] for key in ("category", "settings", "connections", "pending_changes", "response") if key in result}
            if args.get("continue_work", "no") not in ("yes", "no"):
                raise SettingsError(msg('settings_orchestrator.invalid_continue_work_option'))
            draft = self.propose(owner, channel, args.get("category"), args.get("setting"), args.get("value"),
                                 args.get("reason"), work_id, continue_work=args.get("continue_work") == "yes", required_skill=args.get("required_skill"))
            minutes = self.TTL_SECONDS // 60
            how = (f"소유자에게 이 대화에서 확인을 요청합니다: 적용 버튼을 누르거나 그렇게 하라고 답하면 적용되고, "
                   f"아니라고 답하면 취소됩니다({minutes}분 안에).")
            return {**{key: value for key, value in draft.items() if key not in ("digest", "response", "expires_at")},
                    "next_step": f"{draft['summary']} 변경은 소유자 확인을 기다립니다. {how} 확인 전에는 아무것도 바뀌지 않았습니다."}
        return call

    def _audit(self, row, terminal, error_class=None):
        with self._confirming:
            audit = self.store.config("settings_audit", [])
            audit = audit if isinstance(audit, list) else []
            event = {"at": self.now(), "draft_ref": row["id"], "target": row.get("target"),
                     "before": row.get("before"), "after": row.get("after"), "terminal": terminal}
            if error_class: event["error_class"] = error_class
            self.store.put("settings_audit", [*audit, event][-100:])

    def draft(self, owner, channel, intent):
        """No lifecycle draft exists any more: say so and change nothing.

        #814: a structured ``{category, setting, value}`` intent drafts one
        owner setting change (``propose``); free text is still not parsed.
        """
        if not isinstance(owner, str) or not owner or not isinstance(channel, str) or not channel:
            raise SettingsError(msg('settings_orchestrator.check_owner_channel'))
        if isinstance(intent, dict) and self.service is not None:
            return self.propose(owner, channel, intent.get("category"), intent.get("setting"), intent.get("value"),
                                intent.get("reason"))
        if not isinstance(intent, str):
            raise SettingsError(msg('settings_orchestrator.specify_reviewed_request'))
        return {"state": "unsupported", "response": RETIRED_CONTROL_MESSAGE}

    def _owned(self, owner, channel, draft_id):
        row = self._drafts().get(draft_id)
        if not row or row.get("owner") != owner or row.get("channel") != channel:
            raise SettingsError(msg('settings_orchestrator.draft_not_found'))
        return row

    def confirm(self, owner, channel, draft_id, digest, notify=None):
        """A draft made by the retired control plane can never apply."""
        if not isinstance(draft_id, str) or not isinstance(digest, str):
            raise SettingsError(msg('settings_orchestrator.draft_confirmation_required'))
        row = self._owned(owner, channel, draft_id)
        if row.get("category") in SETTINGS and self.service is not None:
            self.reconcile()
            return self._confirm_owner_setting(row, digest, notify)
        with self._confirming:
            row = self._drafts().get(draft_id) or row
            if row.get("state") == "awaiting-confirmation":
                self._settle(draft_id, "failed", "failed", "retired-control")
        raise SettingsError(RETIRED_CONTROL_MESSAGE)

    def cancel(self, owner, channel, draft_id):
        with self._confirming:
            row = self._owned(owner, channel, draft_id)
            if row["state"] == "canceled": return {"state": "canceled", "idempotent": True}
            if row["state"] not in ('awaiting-confirmation', 'awaiting-input'): raise SettingsError(msg('settings_orchestrator.draft_cannot_cancel'))
            self._settle(draft_id, "canceled", "canceled",
                         expected={'state': row['state'], 'digest': row['digest']})
            if row.get('category') == 'api_connections' and self.service is not None:
                session = self.service.phone_input_session()
                if session is not None and getattr(session, 'api_draft', {}).get('id') == draft_id:
                    session.finish('cancelled')
                self.service.resume_settings_work()
        return {"state": "canceled", "idempotent": False,
                "response": RECOVERY_DECLINED_MESSAGE if row.get('recovery_of') else msg('settings_orchestrator.the_settings_change_was_canceled_nothing')}

    def recovery(self, owner, subject):
        if not isinstance(owner, str) or not owner or not isinstance(subject, str):
            raise SettingsError(msg('settings_orchestrator.select_recovery_connection'))
        rows = {str(row.get("id")): row for row in self.connections() or [] if isinstance(row, dict)}
        row = rows.get(subject)
        if not row:
            raise SettingsError(msg('settings_orchestrator.select_recovery_connection'))
        return {"state": "recovery", "target": subject, "action": next_action(row)}

    def handle_text(self, owner, channel, text, owner_typed=True, notify=None):
        """``owner_typed`` (#814 review P1): False for a message Still ran on the owner's
        behalf (a preparation goal, a continuation, a retry); it can never confirm or cancel.

        The ``확인 <id>`` / ``취소 <id>`` forms are kept for compatibility only;
        they are never shown to the owner (#855).

        RULES-OUT-01 (#173): this is the explicit ``/settings`` command, and its
        free text is never re-read for a phrase.  Anything but an id form is the
        read: every connection row already carries its one truthful next action.
        A settings request in the owner's own words reaches the worker, whose
        ``settings_read`` / ``settings_change`` tools read or draft it, including a
        connection disconnect (#183); pausing a connection does not exist."""
        if not isinstance(text, str): raise SettingsError(msg('settings_orchestrator.check_request'))
        value = text.strip()
        match = re.fullmatch(r"(?:confirm|확인)\s+([A-Za-z0-9_-]+)", value, re.I)
        if match:
            if owner_typed is not True: raise SettingsError(NOT_OWNER_TYPED_MESSAGE)
            row = self._owned(owner, channel, match.group(1))
            return self.confirm(owner, channel, row["id"], row.get("digest", ""), notify)
        match = re.fullmatch(r"(?:cancel|취소)\s+([A-Za-z0-9_-]+)", value, re.I)
        if match:
            if owner_typed is not True: raise SettingsError(NOT_OWNER_TYPED_MESSAGE)
            return self.cancel(owner, channel, match.group(1))
        return self.read(owner)

