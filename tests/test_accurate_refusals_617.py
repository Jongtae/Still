"""#617: a refusal tells the worker the true reason and a fitting next step; gates that protect nothing are gone.

Evidence class: model-free unit tests with temporary stores, fake transports
and injected redactors.  They prove what each refusal says and which calls
still run; they do not prove how a live model reacts to the words.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from personal_agent import capability_search, preparations
from personal_agent.agent_runtime import (BUDGET_CODES, REPEAT_PAGE_STEP_TEXT, REPEAT_PATH_TEXT, Capabilities,
                                          PresentationBudget, ToolError, WorkBudget, WorkLedger, classify_failure,
                                          mail_rejection, outcome_from_events, repeat_path_error)
from personal_agent.calendar import CALENDAR_CONNECTOR_ID, CALENDAR_SPEC, CalendarConnector, CalendarError
from personal_agent.connector_contract import ConnectorRegistry, ConnectorState
from personal_agent.gmail import GmailError
from personal_agent.google_calendar import MAX_EVENTS, GoogleCalendar
from personal_agent.messages import msg
from personal_agent.quickstart_store import QuickStore

SECRET = 'sk-live-SECRET99'
SAVED = 'Passport-M12345678'


def redact(text):
    return str(text).replace(SECRET, '[redacted]')


def broken(_text):
    raise RuntimeError('secret store unavailable')


class _Store(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')

    def caps(self, **kwargs):
        return Capabilities(self.store, None, {}, '', 'job', lambda *a: None, **kwargs)

    def running(self):
        job = self.store.enqueue('turn', f'k-{time.time_ns()}')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        return job


class RepeatPathWordsFitTheAction(unittest.TestCase):
    def test_a_repeated_page_step_is_not_told_to_change_search_terms(self):
        for action in ('browser_click', 'browser_type'):
            refusal = repeat_path_error(action)
            self.assertEqual((refusal.code, refusal.ran, str(refusal)), ('repeat_path', False, REPEAT_PAGE_STEP_TEXT))
            self.assertIn('browser_read', str(refusal))
        refusal = repeat_path_error('web_search')
        self.assertEqual((refusal.code, str(refusal)), ('repeat_path', REPEAT_PATH_TEXT))
        self.assertNotEqual(REPEAT_PATH_TEXT, REPEAT_PAGE_STEP_TEXT)


class GmailStatusesAreTold(unittest.TestCase):
    def test_only_a_404_is_a_missing_message(self):
        for status, code in ((404, 'mail_not_found'), (403, 'mail_forbidden'), (429, 'mail_rate_limited'),
                             (500, 'mail_unavailable'), (503, 'mail_unavailable'), (400, 'mail_rejected'),
                             (302, 'mail_rejected'), (None, 'mail_rejected')):
            with self.subTest(status=status):
                self.assertEqual(mail_rejection(status).code, code)

    def test_the_capability_maps_the_connectors_status(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        class Rejecting:
            def __init__(self, status):
                self.status = status

            def read(self, message_id):
                raise GmailError('provider_rejected', self.status)

        for status, code in ((404, 'mail_not_found'), (403, 'mail_forbidden'), (429, 'mail_rate_limited')):
            with self.subTest(status=status):
                caps = Capabilities(QuickStore(temp.name), None, {}, None, 'job-1', lambda *a: None, mail=Rejecting(status))
                with self.assertRaises(ToolError) as caught:
                    caps.execute('mail_read', {'message_id': 'm_1'})
                self.assertEqual(caught.exception.code, code)


class BudgetRefusalsSayWhy(_Store):
    def test_the_limit_the_deadline_a_newer_run_and_an_unreadable_record_are_told_apart(self):
        job = self.running()
        now = [1000.0]
        first = WorkLedger(self.store, job, seconds=60, wall=lambda: now[0], fresh=True)
        budget = WorkBudget(attempts=1, ledger=first)
        budget.spend_attempt()
        with self.assertRaises(ToolError) as caught:
            budget.spend_attempt()
        self.assertEqual(caught.exception.code, 'attempt_budget')
        # A newer run of the same request holds the record now: the old run is superseded, not out of time.
        first.pause()
        WorkLedger(self.store, job, seconds=60, wall=lambda: now[0], fresh=True)
        stale = WorkBudget(attempts=5, turns=5, ledger=first)
        self.assertEqual(stale.interrupted(), 'run_superseded')
        for spend in (stale.spend_attempt, stale.spend_turn):
            with self.assertRaises(ToolError) as caught:
                spend()
            self.assertEqual((caught.exception.code, str(caught.exception)),
                             ('run_superseded', msg('agent_runtime.run_superseded')))
        # The deadline is still the deadline.
        current = WorkLedger(self.store, job, wall=lambda: now[0])
        now[0] += 61
        self.assertEqual(WorkBudget(ledger=current).interrupted(), 'deadline_exceeded')

    def test_an_unreadable_record_is_not_reported_as_the_time_limit(self):
        job = self.running()
        ledger = WorkLedger(self.store, job, fresh=True)
        budget = WorkBudget(ledger=ledger)

        class Down:
            root = self.store.root

            def db(self):
                raise OSError('disk unavailable')

            def config(self, *args):
                raise OSError('disk unavailable')
        ledger.store = Down()
        self.assertEqual(budget.interrupted(), 'budget_unavailable')
        with self.assertRaises(ToolError) as caught:
            budget.spend_attempt()
        self.assertEqual((caught.exception.code, str(caught.exception)),
                         ('budget_unavailable', msg('agent_runtime.budget_unavailable')))
        self.assertIn('budget_unavailable', BUDGET_CODES)
        self.assertEqual(classify_failure(caught.exception)[1], 'budget')

    def test_the_reaction_allowance_has_its_own_words(self):
        job = self.running()
        ledger = WorkLedger(self.store, job, fresh=True)
        reactions = PresentationBudget(ledger=ledger)
        for _ in range(6):
            reactions.spend_turn()
        with self.assertRaises(ToolError) as caught:
            reactions.spend_turn()
        self.assertEqual(caught.exception.code, 'presentation_budget')
        self.assertEqual(str(caught.exception), msg('agent_runtime.presentation_turns_exhausted', count=6))
        self.assertNotEqual(str(caught.exception), msg('agent_runtime.model_turns_exhausted', count=15))


class OwnSurfacesKeepTheOwnersWordsAndLoseSecrets(_Store):
    """The owner's own surfaces: saved private values stay (#826), secrets never do."""

    def caps(self, **kwargs):
        # ``lookup_sources`` names a value this Work saved: the #605 set the old redaction removed.
        return super().caps(secret_redactor=redact, lookup_sources=lambda: {'excluded': [SAVED]}, **kwargs)

    def test_tell_owner(self):
        sent = []
        self.caps(owner_update=sent.append).execute('tell_owner', {'text': f'{SAVED} and {SECRET}'})
        self.assertEqual(sent, [f'{SAVED} and [redacted]'])

    def test_ask_location(self):
        asked = []
        self.caps(location_request=lambda *a, **k: asked.append(a)).execute(
            'ask_location', {'reason': f'{SAVED} near {SECRET}', 'button_label': f'{SAVED} {SECRET}'})
        self.assertEqual(asked, [(f'{SAVED} near [redacted]', f'{SAVED} [redacted]')])

    def test_schedule_preparation(self):
        stored = []
        self.caps(preparations=lambda args: stored.append(args) or {'scheduled': False}).execute(
            'schedule_preparation', {'kind': 'reminder', 'goal': f'{SAVED} {SECRET}', 'due': '2026-11-04T09:00:00+09:00',
                                     'title': f'{SAVED} {SECRET}', 'offer': f'{SAVED} {SECRET}'})
        self.assertEqual({field: stored[0][field] for field in ('goal', 'title', 'offer')},
                         dict.fromkeys(('goal', 'title', 'offer'), f'{SAVED} [redacted]'))

    def test_propose_current_state(self):
        proposed = []

        class Context:
            def enabled(self):
                return True

            def propose(self, job_id, args):
                proposed.append(args['value'])
                return {'recorded': True}
        self.caps(current_context=Context()).execute(
            'propose_current_state', {'predicate': 'current_place', 'value': f'{SAVED} {SECRET}', 'kind': 'stated'})
        self.assertEqual(proposed, [f'{SAVED} [redacted]'])

    def test_a_failing_redactor_refuses_each_surface_with_the_true_reason_and_sends_nothing(self):
        sent = []
        caps = Capabilities(self.store, None, {}, '', 'job', lambda *a: None, secret_redactor=broken,
                            owner_update=sent.append, location_request=lambda *a, **k: sent.append(a),
                            preparations=lambda args: sent.append(args))
        for name, args in (('tell_owner', {'text': 'hello'}), ('ask_location', {'reason': 'where'}),
                           ('schedule_preparation', {'kind': 'reminder', 'goal': 'g', 'due': '2026-11-04T09:00:00+09:00'})):
            with self.subTest(name=name):
                with self.assertRaises(ToolError) as caught:
                    caps.execute(name, args)
                self.assertEqual((caught.exception.code, caught.exception.ran),
                                 ('redaction_unavailable', False))
        self.assertEqual(sent, [])


class LookupRedactorFailure(_Store):
    def test_a_lookup_is_refused_as_redaction_unavailable_not_as_empty_words(self):
        sent = []

        class Network:
            def execute(self, plan):
                sent.append(plan)
                return {'results': []}
        caps = self.caps(secret_redactor=broken, lookup_sources=lambda: {'excluded': []}, network=Network())
        with self.assertRaises(ToolError) as caught:
            caps.execute('web_search', {'query': 'weather tomorrow'})
        self.assertEqual((caught.exception.code, caught.exception.ran), ('redaction_unavailable', False))
        self.assertEqual(classify_failure(caught.exception)[0], 'redaction_unavailable')
        self.assertEqual(sent, [])


class CapabilityQueries(_Store):
    def test_only_a_secret_blocks_the_query_and_the_refusal_says_so(self):
        asked = []
        caps = self.caps(secret_redactor=redact, lookup_sources=lambda: {'excluded': [SAVED]},
                         settings=lambda name, args: asked.append(args) or {'ok': True})
        caps.execute('capability_search', {'source': 'registry', 'query': SAVED})
        self.assertEqual(asked, [{'source': 'registry', 'query': SAVED}], 'a saved value is no secret (#826)')
        with self.assertRaises(ToolError) as caught:
            caps.execute('capability_search', {'source': 'registry', 'query': SECRET})
        self.assertEqual((caught.exception.code, str(caught.exception)),
                         ('private_capability_query', msg('agent_runtime.public_server_query')))
        self.assertEqual(len(asked), 1)

    def test_the_registry_states_its_query_rule(self):
        search = capability_search.CapabilitySearch(transport=lambda *a: self.fail('nothing is sent'))
        for query in ('two words', '한글', '', 'x' * 81):
            with self.subTest(query=query):
                with self.assertRaises(ValueError) as caught:
                    search.registry(query)
                self.assertEqual(caught.exception.code, 'registry_query_shape')
                self.assertIn('ASCII', msg('agent_runtime.registry_query_shape', 'en'))
        with self.assertRaises(ValueError) as caught:
            search.registry('github', redact=lambda text: '[redacted]')
        self.assertEqual(caught.exception.code, 'private_capability_query')

    def test_the_settings_route_keeps_the_registry_code(self):
        def settings(name, args):
            return capability_search.CapabilitySearch(transport=lambda *a: self.fail('nothing is sent')).registry(args['query'])
        caps = self.caps(settings=settings)
        with self.assertRaises(ToolError) as caught:
            caps.execute('capability_search', {'source': 'registry', 'query': 'two words'})
        self.assertEqual((caught.exception.code, caught.exception.ran), ('registry_query_shape', False))


class PreparationGoalReason(unittest.TestCase):
    def test_an_empty_or_long_goal_is_told_only_what_is_wrong(self):
        for goal in ('', '가' * (preparations.GOAL_MAX_CHARS + 1)):
            with self.subTest(length=len(goal)):
                with self.assertRaises(preparations.PreparationRefusal) as caught:
                    preparations.normalize_goal(goal)
                self.assertEqual(caught.exception.code, 'invalid_goal')
                self.assertNotIn('한 가지 목표', str(caught.exception))
                self.assertTrue(str(caught.exception).startswith('goal' if not goal else 'kind'))


class CalendarArguments(_Store):
    def connector(self, provider):
        registry = ConnectorRegistry(self.store, (CALENDAR_SPEC,))
        connector = CalendarConnector(self.store, provider, registry=registry)
        registry.transition('owner', CALENDAR_CONNECTOR_ID, ConnectorState.CONNECTED,
                            granted_scopes=CALENDAR_SPEC.required_scopes)
        return connector

    def test_each_argument_error_says_what_is_wrong(self):
        connector = self.connector(None)
        start, end = '2026-11-03T09:00:00+09:00', '2026-11-03T18:00:00+09:00'
        for args, reason in (((('2026-11-03 09:00', end, 'Asia/Seoul')), 'invalid-start'),
                             (((start, '2026-11-03T18:00:00', 'Asia/Seoul')), 'invalid-end'),
                             (((end, start, 'Asia/Seoul')), 'invalid-window'),
                             (((start, end, 'Mars/Base')), 'invalid-timezone')):
            with self.subTest(reason=reason):
                with self.assertRaises(CalendarError) as caught:
                    connector.query('owner', *args)
                self.assertEqual((caught.exception.reason, caught.exception.code), (reason, 'invalid_calendar_query'))
                self.assertEqual(str(caught.exception), msg('calendar.' + reason.replace('-', '_')))
                self.assertEqual(classify_failure(caught.exception)[:2], ('invalid_calendar_query', 'permanent'))


def event(index):
    return {'id': f'e{index}', 'summary': f'event {index}',
            'start': {'dateTime': f'2026-11-03T{index % 24:02d}:00:00+09:00'},
            'end': {'dateTime': f'2026-11-03T{index % 24:02d}:30:00+09:00'}}


class CalendarPaging(_Store):
    """#617: events.list is read page by page (``pageToken``); a cut read is never complete."""

    def provider(self, total, page_size):
        self.urls = []

        def transport(method, url, body, headers):
            self.urls.append(url)
            if '/calendarList' in url:
                return {'items': []}
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(url).query)
            offset = int(query.get('pageToken', ['0'])[0])
            size = min(page_size, int(query['maxResults'][0]))
            page = {'items': [event(index) for index in range(offset, min(total, offset + size))]}
            if offset + size < total:
                page['nextPageToken'] = str(offset + size)
            return page
        return GoogleCalendar(transport)

    def test_every_page_is_read_and_the_read_is_complete(self):
        calendar = self.provider(total=120, page_size=50)
        events = calendar.query('2026-11-03T00:00:00+09:00', '2026-11-04T00:00:00+09:00', 'Asia/Seoul', MAX_EVENTS)
        self.assertEqual(len(events), 120)
        self.assertFalse(calendar.truncated)
        self.assertEqual(sum('pageToken=' in url for url in self.urls), 2)

    def test_a_window_past_the_cap_is_reported_as_truncated(self):
        calendar = self.provider(total=MAX_EVENTS + 40, page_size=100)
        result = self.connector(calendar).query('owner', '2026-11-03T00:00:00+09:00', '2026-11-04T00:00:00+09:00',
                                                'Asia/Seoul')
        self.assertEqual(len(result['events']), MAX_EVENTS)
        self.assertFalse(result['evidence']['complete'])
        self.assertTrue(result['evidence']['truncated'])
        self.assertIn(msg('calendar.truncated', count=MAX_EVENTS), result['incomplete'])

    def connector(self, provider):
        return CalendarArguments.connector(self, provider)


class ContextFaults(_Store):
    def test_a_work_shown_no_location_is_not_refused_over_the_context_tables(self):
        class Unreadable:
            def withdrawn_strings(self, *args, **kwargs):
                raise RuntimeError('context tables unavailable')
        caps = self.caps(current_context=Unreadable())
        self.assertEqual(caps.withdrawn_context(), [])

    def test_unverifiable_sources_withhold_what_the_work_was_shown(self):
        from personal_agent.current_context import CurrentContext
        CurrentContext(self.store)  # creates the tables
        with self.store.db() as db:
            db.execute('INSERT INTO current_context_exposures VALUES (?,?,?,?,?)',
                       ('job', 'obs:1', '1', json.dumps(['Hapjeong', '37.55,126.91']), 0))

        class Unreadable:
            def withdrawn_strings(self, *args, **kwargs):
                raise RuntimeError('observations unavailable')
        self.assertEqual(self.caps(current_context=Unreadable()).withdrawn_context(), ['37.55,126.91', 'Hapjeong'])

    def test_an_unreadable_exposure_record_still_refuses(self):
        caps = self.caps()

        class Down:
            def db(self):
                raise OSError('disk unavailable')
        caps.store = Down()
        with self.assertRaises(ToolError) as caught:
            caps.withdrawn_context()
        self.assertEqual((caught.exception.code, caught.exception.ran), ('context_unverified', False))


class AdvisoryNotes(_Store):
    def test_a_failed_method_gap_never_lowers_the_works_outcome(self):
        read = ('web_search', 'succeeded', json.dumps({'evidence': {'result_count': 1}}))
        gap = ('method_gap', 'failed', json.dumps({'code': 'redaction_unavailable', 'retry': 'permanent', 'ran': False,
                                                  'error': msg('agent_runtime.redaction_unavailable')}))
        self.assertEqual(outcome_from_events([read, gap])[0], 'succeeded')
        self.assertEqual(outcome_from_events([read, ('web_search', 'failed', json.dumps({'code': 'tool_failed'}))])[0],
                         'partial', 'a failed read still lowers it')

    def test_a_failing_redactor_refuses_the_note_as_such(self):
        class Skills:
            loaded, screened = {}, {}

            def recall(self, *args):
                pass

            def gap(self, note):
                raise AssertionError('nothing is kept')
        caps = self.caps(secret_redactor=broken, skills=Skills())
        with self.assertRaises(ToolError) as caught:
            caps.execute('method_gap', {'note': 'a method for comparing invoices'})
        self.assertEqual((caught.exception.code, caught.exception.ran), ('redaction_unavailable', False))


if __name__ == '__main__':
    unittest.main()
