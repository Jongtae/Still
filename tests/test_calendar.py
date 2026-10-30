import tempfile
import unittest
import threading

from personal_agent.calendar import (
    CALENDAR_CONNECTOR_ID,
    CALENDAR_SPEC,
    CALENDAR_WRITE_CONNECTOR_ID,
    CALENDAR_WRITE_SPEC,
    CalendarConnector,
    CalendarError,
)
from personal_agent.connector_contract import ConnectorRegistry, ConnectorState
from personal_agent.google_calendar import (
    CALENDAR_WRITE_SCOPE,
    GoogleCalendarError,
)
from personal_agent.quickstart_store import QuickStore


EVENT = {
    "summary": "review",
    "start": "2026-09-22T10:00:00+09:00",
    "end": "2026-09-22T11:00:00+09:00",
    "timezone": "Asia/Seoul",
    "description": "private agenda",
}


class Provider:
    def __init__(self):
        self.calls = []
        self.error = None

    def query(self, *args):
        self.calls.append(("query", *args))
        if self.error:
            raise self.error
        return [{"id": "event-1", "etag": '"v1"', "summary": "review", "start": EVENT["start"], "end": EVENT["end"], "location": "", "status": "confirmed"}]


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = QuickStore(self.temp.name)
        self.provider = Provider()
        self.registry = ConnectorRegistry(self.store, (CALENDAR_SPEC, CALENDAR_WRITE_SPEC))
        self.calendar = CalendarConnector(self.store, self.provider, registry=self.registry)
        self.registry.transition(
            "owner",
            CALENDAR_CONNECTOR_ID,
            ConnectorState.CONNECTED,
            granted_scopes=CALENDAR_SPEC.required_scopes,
        )
        self.registry.transition(
            "owner",
            CALENDAR_WRITE_CONNECTOR_ID,
            ConnectorState.CONNECTED,
            granted_scopes=(CALENDAR_WRITE_SCOPE,),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_a_read_rejected_as_expired_requires_reconnecting(self):
        self.provider.error = GoogleCalendarError("scope-expired")
        with self.assertRaises(CalendarError) as rejected:
            self.calendar.query("owner", "2026-09-21T00:00:00+09:00", "2026-09-28T00:00:00+09:00", "Asia/Seoul")
        self.assertEqual((rejected.exception.reason, rejected.exception.recovery), ("scope-expired", "reconnect"))
        self.assertEqual(self.registry.status("owner", CALENDAR_CONNECTOR_ID).state, ConnectorState.REAUTH_REQUIRED)

    def test_scope_expiry_does_not_revoke_a_newer_sibling_reconnect(self):
        write_before = self.registry.status("owner", CALENDAR_WRITE_CONNECTOR_ID).connection_revision

        def rejected_after_write_reconnect(*_args):
            self.registry.transition("owner", CALENDAR_WRITE_CONNECTOR_ID, ConnectorState.REAUTH_REQUIRED)
            reconnected = self.registry.transition("owner", CALENDAR_WRITE_CONNECTOR_ID, ConnectorState.CONNECTED,
                                                   granted_scopes=(CALENDAR_WRITE_SCOPE,))
            self.assertNotEqual(reconnected.connection_revision, write_before)
            raise GoogleCalendarError("scope-expired")

        self.provider.query = rejected_after_write_reconnect
        with self.assertRaises(CalendarError) as rejected:
            self.calendar.query("owner", "2026-09-21T00:00:00+09:00", "2026-09-28T00:00:00+09:00", "Asia/Seoul")
        self.assertEqual(rejected.exception.reason, "scope-expired")
        self.assertEqual(self.registry.status("owner", CALENDAR_CONNECTOR_ID).state, ConnectorState.REAUTH_REQUIRED)
        self.assertEqual(self.registry.status("owner", CALENDAR_WRITE_CONNECTOR_ID).state, ConnectorState.CONNECTED)

    def test_a_read_only_connection_can_query(self):
        store = QuickStore(self.temp.name + "-read-only")
        registry = ConnectorRegistry(store, (CALENDAR_SPEC, CALENDAR_WRITE_SPEC))
        calendar = CalendarConnector(store, Provider(), registry=registry)
        registry.transition("reader", CALENDAR_CONNECTOR_ID, ConnectorState.CONNECTED,
                            granted_scopes=CALENDAR_SPEC.required_scopes)
        result = calendar.query("reader", "2026-09-21T00:00:00+09:00", "2026-09-28T00:00:00+09:00", "Asia/Seoul")
        self.assertEqual(result["evidence"]["result_count"], 1)
        self.assertEqual(registry.status("reader", CALENDAR_WRITE_CONNECTOR_ID).state, ConnectorState.DISCONNECTED)

    def test_query_requires_explicit_bounded_window_and_read_authority(self):
        result = self.calendar.query(
            "owner",
            "2026-09-21T00:00:00+09:00",
            "2026-09-28T00:00:00+09:00",
            "Asia/Seoul",
            max_results=25,
        )
        self.assertEqual(result["events"][0]["id"], "event-1")
        self.assertEqual(
            self.provider.calls,
            [("query", "2026-09-21T00:00:00+09:00", "2026-09-28T00:00:00+09:00", "Asia/Seoul", 25)],
        )
        self.assertNotIn("private agenda", str(result["evidence"]))
        with self.assertRaises(CalendarError):
            self.calendar.query("owner", "2026-01-01T00:00:00Z", "2028-01-01T00:00:00Z", "UTC")
        with self.assertRaises(CalendarError):
            self.calendar.query("owner", "2026-01-01T00:00:00", "2026-01-02T00:00:00", "UTC")

        disconnected = CalendarConnector(
            QuickStore(self.temp.name + "-other"),
            Provider(),
            registry=ConnectorRegistry(QuickStore(self.temp.name + "-other-registry"), (CALENDAR_SPEC,)),
        )
        with self.assertRaises(CalendarError) as denied:
            disconnected.query("owner", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "UTC")
        self.assertEqual(denied.exception.reason, "scope-denied")
        self.assertFalse(disconnected.provider.calls)

    def test_query_releases_global_authority_lock_during_provider_dispatch(self):
        observed = []
        original_query = self.provider.query

        def guarded_query(*args):
            observed.append(self.registry._lock._is_owned())
            return original_query(*args)

        self.provider.query = guarded_query
        self.calendar.query(
            "owner",
            "2026-09-21T00:00:00+09:00",
            "2026-09-28T00:00:00+09:00",
            "Asia/Seoul",
        )
        self.assertEqual(observed, [False])

    def test_query_discards_result_if_authority_changes_during_provider_call(self):
        original_query = self.provider.query

        def revoke_then_return(*args):
            self.registry.transition("owner", CALENDAR_CONNECTOR_ID, ConnectorState.DISCONNECTED)
            return original_query(*args)

        self.provider.query = revoke_then_return
        with self.assertRaises(CalendarError) as changed:
            self.calendar.query(
                "owner",
                "2026-09-21T00:00:00+09:00",
                "2026-09-28T00:00:00+09:00",
                "Asia/Seoul",
            )
        self.assertEqual(changed.exception.reason,"scope-denied")
        self.assertEqual(changed.exception.recovery,"reconnect")

    def test_query_dispatch_is_ordered_before_concurrent_revocation(self):
        authority_checked=threading.Event()
        allow_dispatch=threading.Event()
        transition_finished=threading.Event()
        original_authorize=self.calendar._authorize

        def paused_authorize(*args):
            snapshot=original_authorize(*args)
            authority_checked.set()
            self.assertTrue(allow_dispatch.wait(1))
            return snapshot

        self.calendar._authorize=paused_authorize
        query=threading.Thread(target=lambda:self.calendar.query(
            "owner","2026-09-21T00:00:00+09:00","2026-09-28T00:00:00+09:00","Asia/Seoul"))
        query.start();self.assertTrue(authority_checked.wait(1))

        def revoke():
            self.registry.transition("owner",CALENDAR_CONNECTOR_ID,ConnectorState.DISCONNECTED)
            transition_finished.set()

        revocation=threading.Thread(target=revoke);revocation.start()
        self.assertFalse(transition_finished.wait(.05))
        allow_dispatch.set();query.join(1);revocation.join(1)
        self.assertFalse(query.is_alive());self.assertFalse(revocation.is_alive())
        self.assertEqual([call[0] for call in self.provider.calls],["query"])
        self.assertTrue(transition_finished.is_set())

    def test_query_does_not_lease_unneeded_write_authority(self):
        provider_started=threading.Event();allow_provider=threading.Event()
        transition_finished=threading.Event();errors=[]
        original_query=self.provider.query

        def paused_query(*args):
            provider_started.set()
            if not allow_provider.wait(1): raise AssertionError("provider wait timed out")
            return original_query(*args)

        self.provider.query=paused_query
        query=threading.Thread(target=lambda:self.calendar.query(
            "owner","2026-09-21T00:00:00+09:00","2026-09-28T00:00:00+09:00","Asia/Seoul"))

        def disconnect_write():
            try:self.registry.transition("owner",CALENDAR_WRITE_CONNECTOR_ID,ConnectorState.DISCONNECTED)
            except Exception as error:errors.append(error)
            finally:transition_finished.set()

        query.start();self.assertTrue(provider_started.wait(1))
        transition=threading.Thread(target=disconnect_write);transition.start()
        self.assertTrue(transition_finished.wait(1))
        allow_provider.set();query.join(1);transition.join(1)
        self.assertFalse(query.is_alive());self.assertFalse(errors)
        self.assertEqual(self.registry.status("owner",CALENDAR_WRITE_CONNECTOR_ID).state,
                         ConnectorState.DISCONNECTED)

    def test_dispatch_locks_do_not_cross_owner_store_namespaces(self):
        other_store=QuickStore(self.temp.name+"-independent-runtime")
        other_registry=ConnectorRegistry(other_store,(CALENDAR_SPEC,CALENDAR_WRITE_SPEC))
        other_registry.transition("owner",CALENDAR_CONNECTOR_ID,ConnectorState.CONNECTED,
                                  granted_scopes=CALENDAR_SPEC.required_scopes)
        lease_started=threading.Event();release_lease=threading.Event();transition_finished=threading.Event()

        def hold_lease():
            with self.registry._dispatch_guard("owner",(CALENDAR_CONNECTOR_ID,)):
                lease_started.set();release_lease.wait(1)

        lease=threading.Thread(target=hold_lease);lease.start();self.assertTrue(lease_started.wait(1))
        transition=threading.Thread(target=lambda:(
            other_registry.transition("owner",CALENDAR_CONNECTOR_ID,ConnectorState.DISCONNECTED),
            transition_finished.set()))
        transition.start();self.assertTrue(transition_finished.wait(1))
        release_lease.set();lease.join(1);transition.join(1)
        self.assertFalse(lease.is_alive());self.assertFalse(transition.is_alive())

    def test_query_and_concurrent_scope_expiry_use_one_lock_order(self):
        provider_started=threading.Event();allow_provider=threading.Event()
        marker_finished=threading.Event();errors=[]
        original_query=self.provider.query

        def paused_query(*args):
            provider_started.set()
            if not allow_provider.wait(1): raise AssertionError("provider wait timed out")
            return original_query(*args)

        self.provider.query=paused_query
        snapshot=tuple(
            (connector_id,self.registry.status("owner",connector_id).connection_revision)
            for connector_id in (CALENDAR_CONNECTOR_ID,CALENDAR_WRITE_CONNECTOR_ID)
        )
        query=threading.Thread(target=lambda:self.calendar.query(
            "owner","2026-09-21T00:00:00+09:00","2026-09-28T00:00:00+09:00","Asia/Seoul"))

        def expire():
            try:self.calendar._mark_scope_expired("owner",snapshot)
            except Exception as error:errors.append(error)
            finally:marker_finished.set()

        query.start();self.assertTrue(provider_started.wait(1))
        marker=threading.Thread(target=expire);marker.start()
        self.assertFalse(marker_finished.wait(.05))
        allow_provider.set();query.join(1);marker.join(1)
        self.assertFalse(query.is_alive());self.assertFalse(marker.is_alive())
        self.assertFalse(errors);self.assertTrue(marker_finished.is_set())


class IncompleteReadTests(unittest.TestCase):
    """Review on #1228: a partial calendar read says so in its result and evidence."""

    setUp = CalendarTests.setUp
    tearDown = CalendarTests.tearDown

    def test_a_complete_read_has_no_note(self):
        result = self.calendar.query("owner", "2026-10-11T00:00:00+09:00", "2026-10-12T00:00:00+09:00", "Asia/Seoul")
        self.assertNotIn("incomplete", result)
        self.assertTrue(result["evidence"]["complete"])

    def test_a_failed_discovery_or_a_skipped_calendar_is_marked(self):
        self.provider.calendar_list_failed = True
        result = self.calendar.query("owner", "2026-10-11T00:00:00+09:00", "2026-10-12T00:00:00+09:00", "Asia/Seoul")
        self.assertIn("기본 캘린더만", result["incomplete"])
        self.assertFalse(result["evidence"]["complete"])
        self.provider.calendar_list_failed = False
        self.provider.skipped_calendars = ["Family"]
        result = self.calendar.query("owner", "2026-10-11T00:00:00+09:00", "2026-10-12T00:00:00+09:00", "Asia/Seoul")
        self.assertIn("Family", result["incomplete"])
        self.assertEqual(result["evidence"]["skipped_calendars"], ["Family"])


if __name__ == "__main__":
    unittest.main()
