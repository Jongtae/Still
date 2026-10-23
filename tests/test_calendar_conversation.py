"""#22 (option A): calendar requests reach the worker's draft tools; Still keeps the approval.

The owner's chosen AI interprets a calendar request with its ordinary tools
(``calendar_draft_create`` / ``_update`` / ``_cancel``); no owner wording is
parsed into a date, time or title by kernel code.  When the Work ends, Still
reads the drafts back from the connector, renders the exact preview in its own
words, and only the owner's literal approval on the channel that was shown the
preview executes - after every draft's state and payload hash still match.

Evidence class: model-free, fixture transports.  A scripted compatible-API
model, a recording Telegram transport, a fixture calendar provider and a
FixtureDecisionEngine replace only the providers; ``AgentService`` (Telegram
ingress, ``run_one``), ``run_agent``, ``Capabilities`` and
``CalendarConnector`` run unchanged.  No live model, Telegram or Google
Calendar is contacted, and none is claimed.
"""
import re
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from personal_agent.calendar import CALENDAR_SPEC, CALENDAR_WRITE_SPEC, CalendarConnector
from personal_agent.calendar_conversation import (CANCELLED, CHANGED_UNDER_US, CREATED, CANCELLED_EVENT,
                                                  FAILED, NOT_APPLIED_REST, OUTCOME_UNKNOWN,
                                                  PREVIEW_FOOTER, PREVIEW_HEADER, REPLACED_NOTICE, STATE_KEY, UPDATED,
                                                  UPDATE_UNKNOWN, UNVERIFIED_TARGET,
                                                  preview_lines, work_draft_ids)
from personal_agent.connector_contract import ConnectorRegistry, ConnectorState, _owner_key
from personal_agent.current_context import TIMEZONE_CONFIG_KEY, resolve_local_timezone
from personal_agent.google_calendar import CALENDAR_WRITE_SCOPE, GoogleCalendarError
from owner_act_script import with_owner_acts
from test_preparations import CHAT, GENERATION, _Case, call, finish

OWNER = f'telegram:{CHAT}'
ZONE = 'Asia/Seoul'
DENTIST = {'summary': '치과', 'start': '2026-10-15T15:00:00+09:00', 'end': '2026-10-15T16:00:00+09:00',
           'timezone': ZONE}
VENDOR = {'summary': 'Vendor meeting', 'start': '2026-10-15T15:00:00+09:00', 'end': '2026-10-15T16:00:00+09:00',
          'timezone': ZONE}
WEEK = {'start': '2026-10-15T00:00:00+09:00', 'end': '2026-10-22T00:00:00+09:00', 'timezone': ZONE}
GYM = {'summary': '운동', 'start': '2026-10-17T09:00:00+09:00', 'end': '2026-10-17T10:00:00+09:00',
       'timezone': ZONE}


class Provider:
    """The Google Calendar side: records every write, writes nothing anywhere."""

    def __init__(self):
        self.calls = []
        self.fail = None
        self.fail_from = 1  # the first call (1-based) that fails while ``fail`` is set

    def _call(self, kind, *args):
        self.calls.append((kind, *args))
        if self.fail is not None and len(self.calls) >= self.fail_from:
            raise self.fail
        return {'id': f'ev{len(self.calls)}', 'version': '"etag"'}

    def create(self, payload, key):
        return self._call('create', payload)

    #: What a calendar read returns: the owner's existing events.
    events = [{'id': 'event-1', 'etag': '"v1"', 'summary': '치과', 'start': '2026-10-16T15:00:00+09:00',
               'end': '2026-10-16T16:00:00+09:00', 'location': '', 'status': 'confirmed'},
              {'id': 'event-2', 'etag': '"v2"', 'summary': '운동', 'start': '2026-10-17T09:00:00+09:00',
               'end': '2026-10-17T10:00:00+09:00', 'location': '체육관', 'status': 'confirmed'}]

    def query(self, start, end, timezone, max_results):
        return [dict(event) for event in self.events]

    def update(self, event_id, event_version, payload):
        return self._call('update', event_id, event_version, payload)

    def cancel(self, event_id, event_version):
        return self._call('cancel', event_id, event_version)


class CalendarDraftApprovalCase(_Case):
    def setUp(self):
        super().setUp()
        self.registry = ConnectorRegistry(self.store, (CALENDAR_SPEC, CALENDAR_WRITE_SPEC), clock=lambda: self.now)
        for owner in (OWNER, 'local-owner'):
            self.registry.transition(owner, CALENDAR_WRITE_SPEC.connector_id, ConnectorState.CONNECTED,
                                     granted_scopes=(CALENDAR_WRITE_SCOPE,))
            self.registry.transition(owner, CALENDAR_SPEC.connector_id, ConnectorState.CONNECTED,
                                     granted_scopes=CALENDAR_SPEC.required_scopes)
        self.provider = Provider()
        self.calendar = CalendarConnector(self.store, self.provider, registry=self.registry, now=lambda: self.now)
        self.service.calendar = self.calendar
        self.service.calendar_conversation.now = lambda: self.now
        # #172: the owner-act judgment is scripted (``owner_act_script.literal``); Still reads no word list.
        self.acts = []
        with_owner_acts(self.judge, asked=self.acts)

    def drafting(self, *calls, summary='초안을 만들었어요.'):
        """The worker's turn: these draft calls, then finish citing them."""
        steps = [{'content': None, 'tool_calls': [call(str(index), name, **args)]}
                 for index, (name, args) in enumerate(calls, 1)]
        self.script = steps + [finish('f', *[str(index) for index in range(1, len(calls) + 1)], summary=summary)]

    def drafts(self):
        return self.store.config('calendar_create', {})

    def states(self):
        return sorted(row['state'] for row in self.drafts().values())

    def pending(self, owner=OWNER):
        rows = self.store.config(STATE_KEY, None)
        return ((rows or {}).get(_owner_key(owner)) or {}) if isinstance(rows, dict) else {}

    def typed_on_web(self, text):
        """A message the owner typed in the local web conversation (the other identity)."""
        self.message_id += 1
        work = self.store.enqueue(text, f'web-{self.message_id}', owner_typed=True)
        self.assertTrue(self.service.run_one())
        return work

    def job(self, work):
        return self.store.job(work)

    def worker_message(self, index):
        return self.model_calls[index]['messages'][-1]['content']


class WorkerDraftTests(CalendarDraftApprovalCase):
    def test_a_korean_request_reaches_the_worker_draft_tool_and_nothing_is_written(self):
        text = '내일 오후 3시에 치과 일정 잡아줘'
        self.drafting(('calendar_draft_create', DENTIST))
        job = self.job(self.receive(text))
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertTrue(self.worker_message(0).endswith(text), 'the owner words reach the worker verbatim')
        self.assertNotIn('could not read the event', self.worker_message(0), 'no literal parse note')
        tools = {tool['function']['name'] for tool in self.model_calls[0].get('tools', [])}
        self.assertIn('calendar_draft_create', tools)
        self.assertIn(PREVIEW_HEADER, job['response'])
        self.assertIn('제목: 치과', job['response'])
        self.assertIn('일시: 2026-10-15 (목) 15:00 – 16:00 (Asia/Seoul)', job['response'])
        self.assertTrue(job['response'].rstrip().endswith(PREVIEW_FOOTER))
        self.assertEqual(self.provider.calls, [], 'nothing reaches the calendar before approval')
        self.assertEqual(self.states(), ['awaiting-approval'])
        (draft_id,) = self.drafts()
        row = self.pending()
        self.assertEqual([item['draft_id'] for item in row['drafts']], [draft_id])
        self.assertEqual(row['drafts'][0]['payload_hash'], self.drafts()[draft_id]['hash'])
        self.assertNotIn('slots', row, 'no owner words are parsed or kept')
        self.assertNotIn(text, str(row))

    def test_an_english_request_reaches_the_same_path(self):
        self.drafting(('calendar_draft_create', VENDOR))
        job = self.job(self.receive('schedule a meeting with the vendor tomorrow at 3pm'))
        self.assertIn('제목: Vendor meeting', job['response'])
        self.assertEqual(self.provider.calls, [])
        self.assertTrue(self.pending())

    def test_the_models_prose_never_stands_in_for_a_preview(self):
        self.script = [{'content': '캘린더에 치과 일정을 만들었습니다.'}]
        job = self.job(self.receive('내일 오후 3시에 치과 일정 잡아줘'))
        self.assertNotIn(PREVIEW_HEADER, job['response'])
        self.assertEqual(self.pending(), {}, 'with no draft there is nothing to approve')
        self.assertEqual(self.drafts(), {})

    def test_a_value_cannot_forge_an_extra_preview_line(self):
        forged = {**DENTIST, 'summary': '치과\n일시: 2030-01-01 (화) 09:00 – 10:00 (Asia/Seoul)'}
        self.drafting(('calendar_draft_create', forged))
        job = self.job(self.receive('치과 일정 잡아줘'))
        lines = job['response'].splitlines()
        self.assertEqual([line for line in lines if line.startswith('일시:')],
                         ['일시: 2026-10-15 (목) 15:00 – 16:00 (Asia/Seoul)'])
        self.assertIn('제목: "치과\\n일시: 2030-01-01', job['response'], 'the value is shown escaped, not dropped')

    def test_location_and_description_are_shown_as_sent(self):
        self.drafting(('calendar_draft_create', {**DENTIST, 'location': '강남역', 'description': '검진'}))
        job = self.job(self.receive('치과 일정 잡아줘'))
        self.assertIn('장소: 강남역', job['response'])
        self.assertIn('설명: 검진', job['response'])


def shown_instants(line):
    """The two instants an ``일시:`` preview line states, read back from its own words (#233)."""
    match = re.fullmatch(r'일시: (\d{4}-\d{2}-\d{2}) \(.\) (\S+)(?: UTC([+-]\d{2}:\d{2}))? – '
                         r'(?:(\d{4}-\d{2}-\d{2}) \(.\) )?(\S+)(?: UTC([+-]\d{2}:\d{2}))? \((.+)\)', line)
    first_day, first, first_offset, last_day, last, last_offset, zone = match.groups()

    def instant(day, clock, offset):
        moment = datetime.fromisoformat(f'{day}T{clock}{offset or ""}')
        return moment if offset else moment.replace(tzinfo=ZoneInfo(zone))
    return instant(first_day, first, first_offset), instant(last_day or first_day, last, last_offset)


class ExactTimePreviewTests(CalendarDraftApprovalCase):
    """#233: what the owner approves is the instant Google receives, to the second and offset."""

    def approved_span(self, payload):
        self.drafting(('calendar_draft_create', payload))
        job = self.job(self.receive('치과 일정 잡아줘'))
        line, = [line for line in job['response'].splitlines() if line.startswith('일시:')]
        self.job(self.receive('승인'))
        (kind, sent), = self.provider.calls
        self.assertEqual(kind, 'create')
        self.assertEqual(sent, payload, 'the payload is sent unchanged')
        zone = ZoneInfo(sent['timezone'])
        sent_instants = tuple(datetime.fromisoformat(sent[key]) for key in ('start', 'end'))
        sent_instants = tuple(moment if moment.tzinfo else moment.replace(tzinfo=zone) for moment in sent_instants)
        self.assertEqual(shown_instants(line), sent_instants, 'the preview states exactly the instants that were sent')
        return line

    def test_seconds_are_shown_not_rounded_away(self):
        line = self.approved_span({**DENTIST, 'start': '2026-10-15T15:00:59+09:00'})
        self.assertEqual(line, '일시: 2026-10-15 (목) 15:00:59 – 16:00 (Asia/Seoul)')

    def test_an_offset_other_than_the_zones_is_shown(self):
        line = self.approved_span({**DENTIST, 'timezone': 'America/New_York'})
        self.assertEqual(line, '일시: 2026-10-15 (목) 15:00 UTC+09:00 – 16:00 UTC+09:00 (America/New_York)')

    def test_a_utc_value_is_not_shown_as_the_zones_wall_clock(self):
        line = self.approved_span({**DENTIST, 'start': '2026-10-15T06:00:00Z', 'end': '2026-10-15T07:00:00.25Z'})
        self.assertEqual(line, '일시: 2026-10-15 (목) 06:00 UTC+00:00 – 07:00:00.25 UTC+00:00 (Asia/Seoul)')
        self.assertNotIn('06:00 – ', line, 'a bare wall clock would read as Seoul time')

    def test_a_repeated_fall_back_wall_time_keeps_both_offsets(self):
        # 01:30 happens twice in New York on 2026-11-01; only the offset says which one is sent.
        line = self.approved_span({**DENTIST, 'start': '2026-11-01T01:30:00-04:00', 'end': '2026-11-01T01:30:00-05:00',
                                   'timezone': 'America/New_York'})
        self.assertEqual(line, '일시: 2026-11-01 (일) 01:30 UTC-04:00 – 01:30 UTC-05:00 (America/New_York)')

    def test_a_skipped_spring_forward_wall_time_keeps_its_offset(self):
        line = self.approved_span({**DENTIST, 'start': '2026-03-08T02:30:00-05:00', 'end': '2026-03-08T04:00:00-04:00',
                                   'timezone': 'America/New_York'})
        self.assertEqual(line, '일시: 2026-03-08 (일) 02:30 UTC-05:00 – 04:00 (America/New_York)')

    def test_a_boundary_year_value_still_renders_with_its_offset(self):
        line = self.approved_span({**DENTIST, 'start': '0001-01-01T00:00:00+14:00', 'end': '0001-01-01T01:00:00+14:00',
                                   'timezone': 'UTC'})
        self.assertEqual(line, '일시: 0001-01-01 (월) 00:00 UTC+14:00 – 01:00 UTC+14:00 (UTC)')
        lines = preview_lines({'action': 'create', 'payload': {**DENTIST, 'start': '9999-12-31T22:00:00-14:00',
                                                                'end': '9999-12-31T23:00:00-14:00', 'timezone': 'UTC'}})
        self.assertIn('일시: 9999-12-31 (금) 22:00 UTC-14:00 – 23:00 UTC-14:00 (UTC)', lines)

    def test_a_value_without_an_offset_is_read_in_its_zone(self):
        line = self.approved_span({**DENTIST, 'start': '2026-10-15T15:00', 'end': '2026-10-15T16:00:30'})
        self.assertEqual(line, '일시: 2026-10-15 (목) 15:00 – 16:00:30 (Asia/Seoul)')


class ApprovalTests(CalendarDraftApprovalCase):
    def test_approval_executes_exactly_once(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('내일 오후 3시에 치과 일정 잡아줘')
        calls_before = len(self.model_calls)
        job = self.job(self.receive('승인'))
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(len(self.model_calls), calls_before, 'the approval runs no model')

    def test_the_approval_turn_asks_no_planner_either(self):
        # #22 review: no speculative orchestration plan is started for the owner's answer.
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        with mock.patch.object(self.service, 'early_work_plan') as plan:
            job = self.job(self.receive('승인'))
        plan.assert_not_called()
        self.assertIn(CREATED, job['response'])
        self.assertIn(CREATED, job['response'])
        self.assertIn('제목: 치과', job['response'])
        self.assertEqual(self.provider.calls, [('create', DENTIST)], 'exactly the payload shown')
        self.assertEqual(self.states(), ['completed'])
        self.assertEqual(self.pending(), {})
        # A second approval has nothing to approve: it is an ordinary message.
        self.receive('승인')
        self.assertEqual(len(self.provider.calls), 1)

    def test_an_english_approval_judged_approve_executes(self):
        self.drafting(('calendar_draft_create', VENDOR))
        self.receive('schedule a meeting with the vendor tomorrow at 3pm')
        job = self.job(self.receive('approve'))
        self.assertIn(CREATED, job['response'])
        self.assertEqual(len(self.provider.calls), 1)

    def test_an_expired_preview_cannot_be_approved(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        self.now += 901
        self.receive('승인')
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.states(), ['awaiting-approval'], 'the draft stays inert')

    def test_the_approval_must_come_from_the_channel_that_was_shown_the_preview(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        # #172: the other identity's drafts are never collected for this one, so nothing is judged here.
        self.typed_on_web('승인')
        self.typed_on_web('취소')
        self.assertEqual(self.acts, [], 'the web conversation had nothing open to answer')
        self.assertEqual(self.provider.calls, [])
        self.assertTrue(self.pending(), 'the Telegram drafts are untouched')
        job = self.job(self.receive('승인'))
        self.assertIn(CREATED, job['response'])
        self.assertEqual(len(self.provider.calls), 1)

    def test_a_wrong_payload_hash_is_not_approved(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        row = dict(self.pending())
        row['drafts'] = [{**row['drafts'][0], 'payload_hash': 'not-the-hash-that-was-shown'}]
        self.store.put(STATE_KEY, {_owner_key(OWNER): row})
        job = self.job(self.receive('승인'))
        self.assertEqual(job['response'], CHANGED_UNDER_US)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.pending(), {})

    def test_a_draft_settled_elsewhere_is_not_approved_again(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        (draft_id,) = self.drafts()
        self.calendar.approve(draft_id, OWNER)  # the owner-local surface approved it first
        job = self.job(self.receive('승인'))
        self.assertEqual(job['response'], CHANGED_UNDER_US)
        self.assertEqual(self.provider.calls, [])

    def test_an_unknown_provider_outcome_is_reported_and_never_retried(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        self.provider.fail = GoogleCalendarError('provider-timeout', 'unknown')
        job = self.job(self.receive('승인'))
        # #598 I1: the Work outcome is unknown, not succeeded, from its own Evidence.
        self.assertEqual(job['status'], 'unknown')
        self.assertEqual(job['response'], OUTCOME_UNKNOWN)
        self.assertEqual(self.states(), ['outcome-unknown'])
        self.receive('승인')
        self.assertEqual(len(self.provider.calls), 1, 'an unknown outcome must not be retried')

    def test_the_judgment_reads_the_act_against_the_preview_that_was_shown(self):
        # #172: one judgment over the act and the open item, with the exact preview text it was shown.
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        job = self.job(self.receive('승인'))
        self.assertIn(CREATED, job['response'])
        act, items, _target = self.acts[-1]
        self.assertEqual(act, '승인')
        self.assertEqual([item['kind'] for item in items], ['calendar-drafts'])
        self.assertIn('제목: 치과', items[0]['text'])

    def test_only_a_message_the_owner_typed_answers_the_drafts(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        # A scheduled or system-made turn carrying the same word is not the owner's answer.
        work = self.store.enqueue('승인', 'not-typed', channel=f'telegram:{GENERATION}', chat_id=CHAT)
        self.assertTrue(self.service.run_one())
        self.assertNotIn(CREATED, self.job(work)['response'] or '')
        self.assertEqual(self.provider.calls, [])

    def test_cancel_writes_nothing_and_leaves_nothing_to_approve(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        job = self.job(self.receive('취소'))
        self.assertEqual(job['response'], CANCELLED)
        self.receive('승인')
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.states(), ['awaiting-approval'], 'the cancelled draft is inert, not applied')


class InterleavingTests(CalendarDraftApprovalCase):
    def test_an_interleaved_request_runs_as_a_normal_work_and_leaves_the_draft_pending(self):
        # #172: a message the judgment reads as unrelated settles nothing and deletes nothing.
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        before = dict(self.drafts())
        shown = self.pending()
        turn = len(self.model_calls)
        job = self.job(self.receive('오늘 날씨 어때?'))
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertTrue(self.worker_message(turn).endswith('오늘 날씨 어때?'), 'the message runs on the worker as usual')
        self.assertEqual(self.drafts(), before, 'the draft is neither changed nor applied')
        self.assertEqual(self.pending(), shown, 'the drafts stay pending')
        self.assertEqual(self.provider.calls, [])
        job = self.job(self.receive('승인'))
        self.assertIn(CREATED, job['response'], 'still answerable until it expires')
        self.assertEqual(self.provider.calls, [('create', DENTIST)])

    def test_a_cue_free_detail_is_not_parsed_into_the_draft(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        before = dict(self.drafts())
        self.drafting(('calendar_draft_create', {**DENTIST, 'start': '2026-10-15T16:00:00+09:00',
                                                 'end': '2026-10-15T17:00:00+09:00'}))
        turn = len(self.model_calls)
        job = self.job(self.receive('4시로'))
        self.assertTrue(self.worker_message(turn).endswith('4시로'), 'the correction is the worker\'s to read')
        self.assertIn('일시: 2026-10-15 (목) 16:00 – 17:00 (Asia/Seoul)', job['response'])
        self.assertEqual(before, {key: self.drafts()[key] for key in before}, 'the first draft is untouched')
        self.receive('승인')
        self.assertEqual([item[1]['start'] for item in self.provider.calls], ['2026-10-15T16:00:00+09:00'],
                         'only the newest preview executes')

    def test_newer_drafts_replace_the_pending_ones_and_say_so(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        first = self.pending()['drafts']
        offered = self.service.calendar_conversation.offer(OWNER, [self.calendar.draft_create(GYM, OWNER)['id']])
        self.assertTrue(offered.startswith(REPLACED_NOTICE))
        self.assertNotEqual(self.pending()['drafts'], first)

    def test_a_draft_the_owner_cannot_see_is_never_offered(self):
        other = self.calendar.draft_create(GYM, 'local-owner')['id']
        self.assertEqual(self.service.calendar_conversation.offer(OWNER, [other]), '')
        self.assertEqual(self.pending(), {})


class UpdateCancelAndSeveralDraftsTests(CalendarDraftApprovalCase):
    def test_update_and_cancel_drafts_show_their_target_and_apply_only_on_approval(self):
        self.drafting(('calendar_query', WEEK),
                      ('calendar_draft_update', {'event_id': 'event-1', 'event_version': '"v1"', 'summary': '치과 검진'}),
                      ('calendar_draft_cancel', {'event_id': 'event-2', 'event_version': '"v2"'}))
        job = self.job(self.receive('치과 일정 제목 바꾸고 운동 일정은 취소해줘'))
        self.assertIn('1. 일정 변경', job['response'])
        # #22 review: the target is shown as this Work's own calendar read returned it, not only by id.
        self.assertIn('대상 일정: 치과\n대상 일시: 2026-10-16 (금) 15:00 UTC+09:00 – 16:00 UTC+09:00\n대상 일정 ID: event-1', job['response'])
        self.assertIn('제목: 치과 검진', job['response'])
        self.assertIn('2. 일정 취소', job['response'])
        self.assertIn('대상 일정: 운동\n대상 일시: 2026-10-17 (토) 09:00 UTC+09:00 – 10:00 UTC+09:00\n대상 장소: 체육관\n대상 일정 ID: event-2',
                      job['response'])
        self.assertEqual(self.provider.calls, [])
        job = self.job(self.receive('승인'))
        self.assertEqual(self.provider.calls, [('update', 'event-1', '"v1"', {'summary': '치과 검진'}),
                                               ('cancel', 'event-2', '"v2"')])
        self.assertIn(UPDATED, job['response'])
        self.assertIn(CANCELLED_EVENT, job['response'])

    def test_a_change_to_an_event_this_work_did_not_read_is_not_offered(self):
        # Without the target event from this Work's own read, the owner could not see which
        # event the approval changes, so the draft cannot be approved here.
        self.drafting(('calendar_draft_cancel', {'event_id': 'event-2', 'event_version': '"v2"'}))
        job = self.job(self.receive('운동 일정 취소해줘'))
        self.assertNotIn(PREVIEW_HEADER, job['response'])
        self.assertIn(UNVERIFIED_TARGET.format(count=1), job['response'])
        self.assertEqual(self.pending(), {})
        self.receive('승인')
        self.assertEqual(self.provider.calls, [])

    def test_a_changed_version_is_not_the_event_that_was_read(self):
        self.drafting(('calendar_query', WEEK),
                      ('calendar_draft_cancel', {'event_id': 'event-2', 'event_version': '"v9"'}))
        job = self.job(self.receive('운동 일정 취소해줘'))
        self.assertIn(UNVERIFIED_TARGET.format(count=1), job['response'])
        self.assertEqual(self.pending(), {})

    def test_several_drafts_in_one_work_are_shown_together_and_approved_together(self):
        self.drafting(('calendar_draft_create', DENTIST), ('calendar_draft_create', GYM))
        job = self.job(self.receive('치과랑 운동 일정 잡아줘'))
        self.assertIn('1. 새 일정', job['response'])
        self.assertIn('2. 새 일정', job['response'])
        self.assertEqual(len(self.pending()['drafts']), 2)
        self.receive('승인')
        self.assertEqual([item[1]['summary'] for item in self.provider.calls], ['치과', '운동'])
        self.assertEqual(self.states(), ['completed', 'completed'])

    def test_one_changed_draft_stops_the_whole_batch_before_anything_is_sent(self):
        self.drafting(('calendar_draft_create', DENTIST), ('calendar_draft_create', GYM))
        self.receive('치과랑 운동 일정 잡아줘')
        row = dict(self.pending())
        row['drafts'] = [row['drafts'][0], {**row['drafts'][1], 'payload_hash': 'changed'}]
        self.store.put(STATE_KEY, {_owner_key(OWNER): row})
        job = self.job(self.receive('승인'))
        self.assertEqual(job['response'], CHANGED_UNDER_US)
        self.assertEqual(self.provider.calls, [], 'not even the unchanged first draft is sent')

    def test_a_failed_draft_stops_the_rest_of_the_batch(self):
        self.drafting(('calendar_draft_create', DENTIST), ('calendar_draft_create', GYM))
        self.receive('치과랑 운동 일정 잡아줘')
        self.provider.fail = GoogleCalendarError('provider-rejected', 'none')
        job = self.job(self.receive('승인'))
        self.assertIn(FAILED, job['response'])
        self.assertIn(NOT_APPLIED_REST, job['response'])
        self.assertEqual(job['status'], 'failed', 'nothing was applied')
        self.assertEqual(len(self.provider.calls), 1, 'the second draft is never sent')
        self.assertEqual(self.states(), ['awaiting-approval', 'failed'])

    def test_a_refused_single_approval_is_a_failed_work(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        self.provider.fail = GoogleCalendarError('provider-rejected', 'none')
        job = self.job(self.receive('승인'))
        self.assertEqual(job['status'], 'failed')
        self.assertTrue(job['response'].startswith(FAILED))
        self.assertNotIn(CREATED, job['response'])

    def test_a_batch_that_applied_only_part_is_partial(self):
        self.drafting(('calendar_draft_create', DENTIST), ('calendar_draft_create', GYM))
        self.receive('치과랑 운동 일정 잡아줘')
        self.provider.fail, self.provider.fail_from = GoogleCalendarError('provider-rejected', 'none'), 2
        job = self.job(self.receive('승인'))
        self.assertEqual(job['status'], 'partial')
        self.assertIn(CREATED, job['response'])
        self.assertIn(FAILED, job['response'])
        self.assertEqual(self.states(), ['completed', 'failed'])

    def test_an_unconfirmed_change_is_reported_as_a_change(self):
        self.drafting(('calendar_query', WEEK),
                      ('calendar_draft_update', {'event_id': 'event-1', 'event_version': '"v1"', 'summary': '치과 검진'}))
        self.receive('치과 일정 제목 바꿔줘')
        self.provider.fail = GoogleCalendarError('provider-timeout', 'unknown')
        job = self.job(self.receive('승인'))
        self.assertEqual(job['status'], 'unknown')
        self.assertEqual(job['response'], UPDATE_UNKNOWN)
        self.assertNotEqual(job['response'], OUTCOME_UNKNOWN, 'not the "was it created" text')


class HelperTests(unittest.TestCase):
    def test_draft_ids_come_from_the_works_own_tool_events_in_order(self):
        events = [('calendar_draft_create', '{"evidence": {"draft_id": "a"}}'),
                  ('mcp__still__x', '{"host_action": "calendar_draft_cancel", "evidence": {"draft_id": "b"}}'),
                  ('calendar_query', '{"evidence": {"draft_id": "c"}}'),
                  ('calendar_draft_create', '{"evidence": {"draft_id": "a"}}'),
                  ('calendar_draft_update', 'not json')]
        self.assertEqual(work_draft_ids(events), ['a', 'b'])

    def test_every_payload_field_is_shown(self):
        lines = preview_lines({'action': 'create', 'payload': {**DENTIST, 'extra': 'x'}})
        self.assertIn('extra: x', lines)

    def test_the_timezone_resolver_moved_with_its_behavior(self):
        class Store:
            def __init__(self, value):
                self.value = value

            def config(self, key, default=None):
                return self.value if key == TIMEZONE_CONFIG_KEY else default

        self.assertEqual(TIMEZONE_CONFIG_KEY, 'calendar_timezone')
        self.assertEqual(resolve_local_timezone(Store('Asia/Seoul'), {'TZ': 'Europe/Paris'}), 'Asia/Seoul')
        self.assertEqual(resolve_local_timezone(Store('Not/AZone'), {'TZ': 'Europe/Paris'}), 'Europe/Paris')


if __name__ == '__main__':
    unittest.main()
