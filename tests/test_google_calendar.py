import unittest
from urllib.parse import parse_qs, urlparse

from personal_agent.google_calendar import (
    GoogleCalendar,
    GoogleCalendarError,
    GoogleCalendarHTTPError,
)


class GoogleCalendarTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def transport(method, url, body, headers):
            self.calls.append((method, url, body, headers))
            if method == "GET":
                return {
                    "items": [
                        {
                            "id": "event-1",
                            "etag": '"v1"',
                            "summary": "review",
                            "start": {"dateTime": "2026-09-22T10:00:00+09:00"},
                            "end": {"dateTime": "2026-09-22T11:00:00+09:00"},
                            "description": "not copied into query result",
                        }
                    ]
                }
            return None

        self.calendar = GoogleCalendar(transport, "access-secret")

    def test_exact_get_reads_the_primary_with_an_explicit_window(self):
        result = self.calendar.query(
            "2026-09-21T00:00:00+09:00",
            "2026-09-28T00:00:00+09:00",
            "Asia/Seoul",
            20,
        )
        # #1225: the calendar list comes first; the fixture shows no other calendar.
        self.assertEqual(urlparse(self.calls[0][1]).path, "/calendar/v3/users/me/calendarList")
        method, url, body, headers = self.calls[1]
        query = parse_qs(urlparse(url).query)
        self.assertEqual((method, urlparse(url).path, body), ("GET", "/calendar/v3/calendars/primary/events", None))
        self.assertEqual(query["timeMin"], ["2026-09-21T00:00:00+09:00"])
        self.assertEqual(query["timeMax"], ["2026-09-28T00:00:00+09:00"])
        self.assertEqual(query["singleEvents"], ["true"])
        self.assertEqual(query["orderBy"], ["startTime"])
        self.assertEqual(headers["Authorization"], "Bearer access-secret")
        self.assertNotIn("access-secret", url)
        self.assertNotIn("description", result[0])

    def test_read_timeout_malformed_and_scope_expiry_have_no_claimed_effect(self):
        cases = (
            (lambda *_: (_ for _ in ()).throw(TimeoutError()), "provider-timeout"),
            (lambda *_: {}, "malformed-response"),
            (lambda *_: (_ for _ in ()).throw(GoogleCalendarHTTPError(401)), "scope-expired"),
            (lambda *_: (_ for _ in ()).throw(GoogleCalendarHTTPError(500)), "provider-error"),
            (lambda *_: (_ for _ in ()).throw(GoogleCalendarHTTPError(503)), "provider-error"),
        )
        for transport, reason in cases:
            with self.subTest(reason=reason):
                calendar = GoogleCalendar(transport)
                with self.assertRaises(GoogleCalendarError) as error:
                    calendar.query("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "UTC", 10)
                self.assertEqual((error.exception.reason, error.exception.effect), (reason, "none"))

    def test_query_rejects_over_limit_and_unbounded_provider_fields(self):
        event={
            "id":"event-1","summary":"review",
            "start":{"dateTime":"2026-01-01T00:00:00Z"},
            "end":{"dateTime":"2026-01-01T01:00:00Z"},
        }
        cases=(
            {"items":[event,event]},
            {"items":[{**event,"summary":"x"*1001}]},
            {"items":[{**event,"id":"x"*1025}]},
            {"items":[{**event,"status":7}]},
        )
        for response in cases:
            with self.subTest(response_size=len(response["items"])):
                calendar=GoogleCalendar(lambda *_args,response=response:response)
                with self.assertRaises(GoogleCalendarError) as malformed:
                    calendar.query("2026-01-01T00:00:00Z","2026-01-02T00:00:00Z","UTC",1)
                self.assertEqual(malformed.exception.reason,"malformed-response")

    def test_forbidden_response_does_not_claim_scope_expiry(self):
        calendar = GoogleCalendar(lambda *_: (_ for _ in ()).throw(GoogleCalendarHTTPError(403)))
        with self.assertRaises(GoogleCalendarError) as error:
            calendar.query("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "UTC", 10)
        self.assertEqual((error.exception.reason, error.exception.effect), ("provider-rejected", "none"))


if __name__ == "__main__":
    unittest.main()


class ShownCalendarsTests(unittest.TestCase):
    """#1225: every calendar the owner shows in Google Calendar is read; only primary events carry a version."""

    WINDOW = ("2026-10-11T00:00:00+09:00", "2026-10-12T00:00:00+09:00", "Asia/Seoul", 20)

    def provider(self, listing, events, failing=()):
        calls = []

        def transport(method, url, body, headers):
            path = urlparse(url).path
            calls.append(path)
            if path.endswith("/users/me/calendarList"):
                if isinstance(listing, Exception):
                    raise listing
                return listing
            calendar_id = path.split("/calendars/", 1)[1].split("/events", 1)[0]
            from urllib.parse import unquote
            calendar_id = unquote(calendar_id)
            if calendar_id in failing:
                raise GoogleCalendarHTTPError(404)
            return {"items": events.get(calendar_id, [])}
        return GoogleCalendar(transport, "token"), calls

    @staticmethod
    def event(ident, start):
        return {"id": ident, "etag": f'"{ident}"', "summary": ident,
                "start": {"dateTime": start}, "end": {"dateTime": start}, "status": "confirmed"}

    def test_shown_calendars_are_read_tagged_and_merged_in_time_order(self):
        listing = {"items": [
            {"id": "me@example.com", "summary": "primary account", "primary": True, "selected": True},
            {"id": "family#cal", "summary": "Family", "selected": True},
            {"id": "birthdays", "summary": "Birthdays", "selected": False},
            {"id": "ko.south_korea#holiday", "summary": "Holidays", "summaryOverride": "공휴일", "selected": True},
        ]}
        events = {"primary": [self.event("mine", "2026-10-11T10:00:00+09:00")],
                  "family#cal": [self.event("dinner", "2026-10-11T09:00:00+09:00")],
                  "birthdays": [self.event("never", "2026-10-11T08:00:00+09:00")],
                  "ko.south_korea#holiday": [self.event("holiday", "2026-10-11T00:00:00+09:00")]}
        provider, calls = self.provider(listing, events)
        result = provider.query(*self.WINDOW)
        self.assertEqual([row["id"] for row in result], ["holiday", "dinner", "mine"])
        self.assertEqual([row["calendar"] for row in result], ["공휴일", "Family", "primary account"])
        self.assertEqual([row["etag"] for row in result], ["", "", '"mine"'], "only primary events carry a version")
        self.assertNotIn("/calendar/v3/calendars/birthdays/events", calls)

    def test_a_failing_shown_calendar_is_skipped_and_named(self):
        listing = {"items": [{"id": "me", "primary": True, "selected": True, "summary": "me"},
                             {"id": "gone", "summary": "Gone", "selected": True}]}
        provider, _calls = self.provider(listing, {"primary": [self.event("mine", "2026-10-11T10:00:00+09:00")]},
                                         failing=("gone",))
        self.assertEqual([row["id"] for row in provider.query(*self.WINDOW)], ["mine"])
        self.assertEqual(provider.skipped_calendars, ["Gone"])

    def test_an_unreadable_list_falls_back_to_the_primary_calendar(self):
        provider, calls = self.provider(GoogleCalendarHTTPError(403), {"primary": [self.event("mine", "2026-10-11T10:00:00+09:00")]})
        self.assertEqual([row["id"] for row in provider.query(*self.WINDOW)], ["mine"])
        self.assertEqual(calls[-1], "/calendar/v3/calendars/primary/events")

    def test_an_expired_grant_is_not_hidden_by_the_fallback(self):
        provider, _calls = self.provider(GoogleCalendarHTTPError(401), {})
        with self.assertRaises(GoogleCalendarError) as caught:
            provider.query(*self.WINDOW)
        self.assertEqual(caught.exception.reason, "scope-expired")


class ShownCalendarsReviewTests(unittest.TestCase):
    WINDOW = ShownCalendarsTests.WINDOW
    provider = ShownCalendarsTests.provider
    event = staticmethod(ShownCalendarsTests.event)

    def test_events_are_merged_by_instant_not_by_string(self):
        listing = {"items": [{"id": "me", "primary": True, "selected": True, "summary": "me"},
                             {"id": "abroad", "summary": "Abroad", "selected": True}]}
        events = {"primary": [self.event("later", "2026-10-11T01:15:00-05:00")],
                  "abroad": [self.event("earlier", "2026-10-11T01:30:00-04:00")]}
        provider, _calls = self.provider(listing, events)
        self.assertEqual([row["id"] for row in provider.query(*self.WINDOW)], ["earlier", "later"])

    def test_the_calendars_read_include_ones_without_events(self):
        listing = {"items": [{"id": "me", "primary": True, "selected": True, "summary": "me"},
                             {"id": "quiet", "summary": "Quiet", "selected": True}]}
        provider, _calls = self.provider(listing, {})
        self.assertEqual(provider.query(*self.WINDOW), [])
        self.assertEqual(provider.read_calendars, ["me", "Quiet"])
        self.assertFalse(provider.calendar_list_failed)

    def test_a_failed_discovery_is_marked(self):
        provider, _calls = self.provider(GoogleCalendarHTTPError(500), {})
        provider.query(*self.WINDOW)
        self.assertTrue(provider.calendar_list_failed)
