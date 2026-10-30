"""Owner-bound, bounded Calendar query policy.

Only an explicit, bounded query reaches the injected provider.  Changes go
through ``api_request``'s google-calendar slot (#432), which spends the
``google-calendar-write`` connection registered here.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .connector_contract import ConnectorContractError, ConnectorRegistry, ConnectorSpec, ConnectorState
from .google_calendar import (
    CALENDAR_LIST_SCOPE,
    CALENDAR_READ_SCOPE,
    CALENDAR_WRITE_SCOPE,
    GoogleCalendar,
    GoogleCalendarError,
)


CALENDAR_CONNECTOR_ID = "google-calendar"
CALENDAR_WRITE_CONNECTOR_ID = "google-calendar-write"
CALENDAR_SPEC = ConnectorSpec(
    CALENDAR_CONNECTOR_ID,
    # #1225: the list scope finds the calendars the owner shows in Google
    # Calendar.  A connection granted before it reads REAUTH_REQUIRED (the
    # registry's definition-change rule) until the owner reconnects once.
    (CALENDAR_READ_SCOPE, CALENDAR_LIST_SCOPE),
)
CALENDAR_WRITE_SPEC = ConnectorSpec(
    CALENDAR_WRITE_CONNECTOR_ID,
    (CALENDAR_WRITE_SCOPE,),
)
_MAX_WINDOW = timedelta(days=366)
_RFC3339_LOCAL = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?\Z"
)


class CalendarError(ValueError):
    """A redacted policy failure safe to pass through a conversation surface."""

    def __init__(self, reason: str = "rejected", *, effect: str = "none", recovery: str = "review-request"):
        super().__init__("Calendar request could not be completed.")
        self.reason = reason
        self.effect = effect
        self.recovery = recovery


def _owner_key(owner: object) -> str:
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
        raise CalendarError("invalid-owner")
    return hashlib.sha256(owner.encode()).hexdigest()


def _bounded_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum or not value.strip():
        raise CalendarError(f"invalid-{field}")
    return value


def _timestamp(value: object, field: str, *, require_offset: bool = False) -> datetime:
    text = _bounded_text(value, field, 64)
    if _RFC3339_LOCAL.fullmatch(text) is None:
        raise CalendarError(f"invalid-{field}")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise CalendarError(f"invalid-{field}") from None
    if require_offset and (parsed.tzinfo is None or parsed.utcoffset() is None):
        raise CalendarError(f"invalid-{field}")
    return parsed


def _timezone(value: object) -> str:
    name = _bounded_text(value, "timezone", 128)
    try:
        ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        raise CalendarError("invalid-timezone") from None
    return name


def _canonical(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class CalendarConnector:
    """Calendar read authority and recovery boundary."""

    def __init__(self, store, provider: GoogleCalendar, *, registry: ConnectorRegistry | None = None, authority=None):
        if registry is None and authority is None:
            raise ValueError("connector registry or explicit authority callback is required")
        self.store = store
        self.provider = provider
        self.registry = registry
        self.authority = authority
        self.token_owner = lambda owner: owner
        if self.registry is not None:
            self.registry.register(CALENDAR_SPEC)
            self.registry.register(CALENDAR_WRITE_SPEC)

    def _authorize(self, owner: str) -> tuple[tuple[str, str], ...] | None:
        _owner_key(owner)
        try:
            if self.registry is not None:
                self.registry.require_connected(self.token_owner(owner), CALENDAR_CONNECTOR_ID, (CALENDAR_READ_SCOPE,))
                # Both records describe authority backed by this connector's
                # one injected Google credential. Capture each currently
                # connected revision so a provider 401 can revoke the shared
                # credential lifecycle without revoking a later reconnect.
                snapshot = []
                for candidate in (CALENDAR_CONNECTOR_ID, CALENDAR_WRITE_CONNECTOR_ID):
                    status = self.registry.status(self.token_owner(owner), candidate)
                    if status.state is ConnectorState.CONNECTED:
                        snapshot.append((candidate, status.connection_revision))
                return tuple(snapshot)
            elif not self.authority(owner, CALENDAR_READ_SCOPE):
                raise CalendarError("scope-denied", recovery="reconnect")
        except ConnectorContractError as error:
            reason = "scope-expired" if error.reason in {"reauth_required", "scope_mismatch"} else "scope-denied"
            raise CalendarError(reason, recovery="reconnect") from None
        return None

    def _mark_scope_expired(
        self,
        owner: str,
        authority_snapshot: tuple[tuple[str, str], ...] | None,
    ) -> None:
        if self.registry is None or authority_snapshot is None:
            return
        for connector_id, expected_revision in authority_snapshot:
            # Keep the same dispatch -> authority lock order used by provider
            # operations and lifecycle transitions. This is reentrant for the
            # query that observed a 401 and cannot deadlock a concurrent read.
            with self.registry._dispatch_guard(self.token_owner(owner), (connector_id,)):
                with self.registry._authority_guard():
                    current = self.registry.status(self.token_owner(owner), connector_id)
                    if (
                        current.state is ConnectorState.CONNECTED
                        and current.connection_revision == expected_revision
                    ):
                        self.registry.transition(self.token_owner(owner), connector_id, ConnectorState.REAUTH_REQUIRED)

    def _prepare_provider(self) -> None:
        """Let a credential-owning transport fetch its local key before a dispatch lock (#8).

        Fetching the key may wait on the operating-system key store, which
        must not happen while connector locks are held.  A key that cannot be
        obtained fails here, before anything is sent.
        """
        prepare = getattr(getattr(self.provider, "transport", None), "prepare_key", None)
        if not callable(prepare):
            return
        try:
            prepare()
        except ValueError as exc:
            reason = str(getattr(exc, "reason", "") or "provider-error").replace("_", "-")
            raise CalendarError(reason, effect="none") from None

    @staticmethod
    def _provider_error(error: GoogleCalendarError) -> CalendarError:
        recovery = "inspect-calendar-before-retry" if error.effect == "unknown" else (
            "reconnect" if error.reason == "scope-expired" else "review-request"
        )
        return CalendarError(error.reason, effect=error.effect, recovery=recovery)

    def query(self, owner: str, start: str, end: str, timezone: str, *, max_results: int = 50) -> dict:
        # Google events.list requires RFC3339 bounds with an explicit offset.
        start_at = _timestamp(start, "start", require_offset=True)
        end_at = _timestamp(end, "end", require_offset=True)
        try:
            invalid_window = start_at >= end_at or end_at - start_at > _MAX_WINDOW
        except TypeError:
            invalid_window = True
        if invalid_window:
            raise CalendarError("invalid-window")
        timezone = _timezone(timezone)
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 100:
            raise CalendarError("invalid-limit")
        self._prepare_provider()
        if self.registry is not None:
            dispatch_guard = self.registry._dispatch_guard(
                self.token_owner(owner),
                (CALENDAR_CONNECTOR_ID,),
            )
        else:
            dispatch_guard = nullcontext()
        with dispatch_guard:
            if self.registry is not None:
                with self.registry._authority_guard():
                    authority_snapshot = self._authorize(owner)
            else:
                authority_snapshot = self._authorize(owner)
            try:
                events = self.provider.query(start, end, timezone, max_results)
            except GoogleCalendarError as error:
                if error.reason == "scope-expired":
                    self._mark_scope_expired(owner, authority_snapshot)
                raise self._provider_error(error) from None
            if self.registry is not None:
                with self.registry._authority_guard():
                    expected_revision = dict(authority_snapshot or ()).get(CALENDAR_CONNECTOR_ID)
                    current = self.registry.status(self.token_owner(owner), CALENDAR_CONNECTOR_ID)
                    if (expected_revision is None or current.state is not ConnectorState.CONNECTED or
                            current.connection_revision != expected_revision):
                        raise CalendarError("scope-denied", recovery="reconnect")
            else:
                self._authorize(owner)
        skipped = list(getattr(self.provider, "skipped_calendars", ()) or ())
        incomplete = ("캘린더 목록을 읽지 못해 기본 캘린더만 확인했습니다." if getattr(self.provider, "calendar_list_failed", False)
                      else f"다음 캘린더는 읽지 못했습니다: {', '.join(skipped)}." if skipped else "")
        return {
            "events": events,
            "window": {"start": start, "end": end, "timezone": timezone},
            # Review on #1228: a partial read says so, so a free slot is never claimed from it.
            **({"incomplete": incomplete} if incomplete else {}),
            "evidence": {
                "operation": "calendar-query",
                "calendar": "shown",
                "calendars": list(getattr(self.provider, "read_calendars", ()) or ()),
                "skipped_calendars": skipped,
                "complete": not incomplete,
                "window_hash": _canonical({"start": start, "end": end, "timezone": timezone}),
                "result_count": len(events),
                "effect": "none",
            },
        }
