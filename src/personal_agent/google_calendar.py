"""Minimal Google Calendar v3 adapter with an injected HTTP transport.

The adapter owns provider syntax, not owner authority or credentials.  It
only reads (#432: changes go through ``api_request``'s google-calendar slot).
Reads cover every calendar the owner shows (``selected``) in Google Calendar,
plus the primary (#1225); only primary events carry an ``etag``.
The injected transport has the signature ``(method, url, body, headers)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlencode


CALENDAR_API = "https://www.googleapis.com/calendar/v3"
CALENDAR_READ_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
#: #1225: listing which calendars the owner has (and shows) needs this scope;
#: the events scope alone cannot discover non-primary calendar ids.
CALENDAR_LIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
#: Most calendars read per query (the owner's shown ones, primary first).
MAX_CALENDARS = 25
CALENDAR_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"


def _instant(value: str, timezone: str):
    """A sort key that orders RFC3339 starts by instant, all-day dates at local midnight (review on #1228)."""
    from datetime import datetime, timezone as utc
    from zoneinfo import ZoneInfo
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.max.replace(tzinfo=utc.utc)
    if moment.tzinfo is None:
        try:
            moment = moment.replace(tzinfo=ZoneInfo(timezone))
        except Exception:
            moment = moment.replace(tzinfo=utc.utc)
    return moment


@dataclass(frozen=True)
class GoogleCalendarError(ValueError):
    """A provider failure with an explicit external-effect classification."""

    reason: str
    effect: str = "none"

    def __post_init__(self) -> None:
        if self.effect not in {"none", "unknown"}:
            raise ValueError("effect must be none or unknown")
        ValueError.__init__(self, "Google Calendar request failed")


class GoogleCalendarHTTPError(Exception):
    """Transport-neutral HTTP failure used by injected provider transports."""

    def __init__(self, status: int):
        super().__init__(f"Google Calendar HTTP status {status}")
        self.status = status


class GoogleCalendar:
    """Exact GET reads of the owner's shown calendars."""

    def __init__(self, transport, access_token: str | None = None):
        if access_token is not None and (not isinstance(access_token, str) or not access_token):
            raise ValueError("access_token must be a non-empty string")
        self.transport = transport
        self.access_token = access_token

    def _headers(self) -> dict:
        headers = {}
        if self.access_token is not None:
            headers["Authorization"] = "Bearer " + self.access_token
        return headers

    def _get(self, url: str):
        try:
            return self.transport("GET", url, None, self._headers())
        except GoogleCalendarHTTPError as error:
            if error.status == 401:
                raise GoogleCalendarError("scope-expired") from None
            if error.status >= 500:
                raise GoogleCalendarError("provider-error") from None
            raise GoogleCalendarError("provider-rejected") from None
        except (TimeoutError, OSError):
            raise GoogleCalendarError("provider-timeout") from None
        except Exception:
            # Injected transports and response decoders are an external trust
            # boundary. Never expose their exception text.
            raise GoogleCalendarError("provider-error") from None

    def calendars(self) -> list[dict]:
        """The calendars to read: primary first, then each one shown in Google Calendar.

        ``[{"id", "name", "primary"}]``.  When the list cannot be read (an
        older grant, a provider error), only the primary calendar is returned,
        so a read never gets worse than before #1225.
        """
        query = urlencode({"minAccessRole": "freeBusyReader", "maxResults": 250,
                           "fields": "items(id,summary,summaryOverride,primary,selected)"})
        self.calendar_list_failed = False
        try:
            response = self._get(f"{CALENDAR_API}/users/me/calendarList?{query}")
        except GoogleCalendarError as error:
            if error.reason == "scope-expired":
                raise
            response = None
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            # Review on #1228: a primary-only read after a failed discovery is
            # an incomplete read, and says so.
            self.calendar_list_failed = True
        primary = {"id": "primary", "name": "", "primary": True}
        shown = []
        items = response.get("items") if isinstance(response, dict) else None
        for item in items if isinstance(items, list) else ():
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or len(item["id"]) > 1024:
                continue
            name = item.get("summaryOverride") or item.get("summary") or ""
            name = name[:200] if isinstance(name, str) else ""
            if item.get("primary") is True:
                primary["name"] = name
            elif item.get("selected") is True:
                shown.append({"id": item["id"], "name": name, "primary": False})
        return [primary, *shown][:MAX_CALENDARS]

    def query(self, time_min: str, time_max: str, timezone: str, max_results: int) -> list[dict]:
        """Events in the window from every calendar ``calendars`` names, earliest first.

        A non-primary calendar that fails is skipped (named in ``skipped``
        by the caller's evidence); the primary calendar failing fails the read.
        """
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 100:
            raise GoogleCalendarError("malformed-response")
        events, self.skipped_calendars, self.read_calendars = [], [], []
        for calendar in self.calendars():
            try:
                rows = self._query_calendar(calendar["id"], time_min, time_max, timezone, max_results)
            except GoogleCalendarError as error:
                if calendar["primary"] or error.reason == "scope-expired":
                    raise
                self.skipped_calendars.append(calendar["name"] or calendar["id"])
                continue
            self.read_calendars.append(calendar["name"] or ("primary" if calendar["primary"] else calendar["id"]))
            for row in rows:
                row["calendar"] = calendar["name"]
                row["calendar_primary"] = calendar["primary"]
                if not calendar["primary"]:
                    # Only a primary event carries its version (#1225).
                    row["etag"] = ""
                events.append(row)
        events.sort(key=lambda row: _instant(row["start"], timezone))
        return events[:max_results]

    def _query_calendar(self, calendar_id: str, time_min: str, time_max: str, timezone: str, max_results: int) -> list[dict]:
        query = urlencode(
            {
                "timeMin": time_min,
                "timeMax": time_max,
                "timeZone": timezone,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": max_results,
                "fields": "items(id,etag,summary,start,end,location,status)",
            }
        )
        response = self._get(f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events?{query}")
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            raise GoogleCalendarError("malformed-response")
        if len(response["items"]) > max_results:
            raise GoogleCalendarError("malformed-response")
        events = []
        for item in response["items"]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"]
                or not isinstance(item.get("start"), dict)
                or not isinstance(item.get("end"), dict)
            ):
                raise GoogleCalendarError("malformed-response")
            start = item["start"].get("dateTime", item["start"].get("date"))
            end = item["end"].get("dateTime", item["end"].get("date"))
            optional = {
                key: item.get(key, "")
                for key in ("etag", "summary", "location", "status")
            }
            if (
                len(item["id"]) > 1024
                or not isinstance(start, str) or not 1 <= len(start) <= 128
                or not isinstance(end, str) or not 1 <= len(end) <= 128
                or any(not isinstance(value, str) for value in optional.values())
                or len(optional["etag"]) > 1024
                or len(optional["summary"]) > 1000
                or len(optional["location"]) > 1000
                or len(optional["status"]) > 64
            ):
                raise GoogleCalendarError("malformed-response")
            events.append(
                {
                    "id": item["id"],
                    "etag": optional["etag"],
                    "summary": optional["summary"],
                    "start": start,
                    "end": end,
                    "location": optional["location"],
                    "status": optional["status"],
                }
            )
        return events
