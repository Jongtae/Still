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
import unittest

from personal_agent.calendar import CALENDAR_SPEC, CALENDAR_WRITE_SPEC, CalendarConnector
from personal_agent.calendar_conversation import (CANCELLED, CHANGED_UNDER_US, CREATED, CANCELLED_EVENT, DROPPED_NOTICE,
                                                  FAILED, NOT_APPLIED_REST, OTHER_CHANNEL, OUTCOME_UNKNOWN,
                                                  PREVIEW_FOOTER, PREVIEW_HEADER, REPLACED_NOTICE, STATE_KEY, UPDATED,
                                                  is_approval, is_cancel, preview_lines, work_draft_ids)
from personal_agent.connector_contract import ConnectorRegistry, ConnectorState, _owner_key
from personal_agent.current_context import TIMEZONE_CONFIG_KEY, resolve_local_timezone
from personal_agent.google_calendar import CALENDAR_WRITE_SCOPE, GoogleCalendarError
from test_preparations import CHAT, GENERATION, _Case, call, finish

OWNER = f'telegram:{CHAT}'
ZONE = 'Asia/Seoul'
DENTIST = {'summary': '치과', 'start': '2026-10-15T15:00:00+09:00', 'end': '2026-10-15T16:00:00+09:00',
           'timezone': ZONE}
VENDOR = {'summary': 'Vendor meeting', 'start': '2026-10-15T15:00:00+09:00', 'end': '2026-10-15T16:00:00+09:00',
          'timezone': ZONE}
GYM = {'summary': '운동', 'start': '2026-10-17T09:00:00+09:00', 'end': '2026-10-17T10:00:00+09:00',
       'timezone': ZONE}


class Provider:
    """The Google Calendar side: records every write, writes nothing anywhere."""

    def __init__(self):
        self.calls = []
        self.fail = None

    def _call(self, kind, *args):
        self.calls.append((kind, *args))
        if self.fail is not None:
            raise self.fail
        return {'id': f'ev{len(self.calls)}', 'version': '"etag"'}

    def create(self, payload, key):
        return self._call('create', payload)

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
        self.provider = Provider()
        self.calendar = CalendarConnector(self.store, self.provider, registry=self.registry, now=lambda: self.now)
        self.service.calendar = self.calendar
        self.service.calendar_conversation.now = lambda: self.now

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


class ApprovalTests(CalendarDraftApprovalCase):
    def test_approval_executes_exactly_once(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('내일 오후 3시에 치과 일정 잡아줘')
        calls_before = len(self.model_calls)
        job = self.job(self.receive('승인'))
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(len(self.model_calls), calls_before, 'the approval runs no model')
        self.assertIn(CREATED, job['response'])
        self.assertIn('제목: 치과', job['response'])
        self.assertEqual(self.provider.calls, [('create', DENTIST)], 'exactly the payload shown')
        self.assertEqual(self.states(), ['completed'])
        self.assertEqual(self.pending(), {})
        # A second approval has nothing to approve: it is an ordinary message.
        self.receive('승인')
        self.assertEqual(len(self.provider.calls), 1)

    def test_english_approval_and_cancel_words(self):
        self.assertTrue(is_approval('approve') and is_approval('승인') and not is_approval('승인하지 마'))
        self.assertTrue(is_cancel('cancel') and is_cancel('그거 취소') and not is_cancel('취소된 회의 알려줘'))
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
        job = self.job(self.typed_on_web('승인'))
        self.assertEqual(job['response'], OTHER_CHANNEL)
        job = self.job(self.typed_on_web('취소'))
        self.assertEqual(job['response'], OTHER_CHANNEL, 'nor may it cancel them')
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
    def test_an_interleaved_request_runs_as_a_normal_work_and_never_touches_the_draft(self):
        self.drafting(('calendar_draft_create', DENTIST))
        self.receive('치과 일정 잡아줘')
        before = dict(self.drafts())
        turn = len(self.model_calls)
        job = self.job(self.receive('오늘 날씨 어때?'))
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertTrue(self.worker_message(turn).endswith('오늘 날씨 어때?'), 'the message runs on the worker as usual')
        self.assertTrue(job['response'].startswith(DROPPED_NOTICE), 'the owner is told the draft was dropped')
        self.assertEqual(self.drafts(), before, 'the draft is neither changed nor applied')
        self.assertEqual(self.pending(), {})
        self.receive('승인')
        self.assertEqual(self.provider.calls, [])

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
        self.drafting(('calendar_draft_update', {'event_id': 'event-1', 'event_version': '"v1"', 'summary': '치과 검진'}),
                      ('calendar_draft_cancel', {'event_id': 'event-2', 'event_version': '"v2"'}))
        job = self.job(self.receive('치과 일정 제목 바꾸고 운동 일정은 취소해줘'))
        self.assertIn('1. 일정 변경', job['response'])
        self.assertIn('대상 일정 ID: event-1', job['response'])
        self.assertIn('제목: 치과 검진', job['response'])
        self.assertIn('2. 일정 취소', job['response'])
        self.assertIn('대상 일정 ID: event-2', job['response'])
        self.assertEqual(self.provider.calls, [])
        job = self.job(self.receive('승인'))
        self.assertEqual(self.provider.calls, [('update', 'event-1', '"v1"', {'summary': '치과 검진'}),
                                               ('cancel', 'event-2', '"v2"')])
        self.assertIn(UPDATED, job['response'])
        self.assertIn(CANCELLED_EVENT, job['response'])

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
        self.assertEqual(len(self.provider.calls), 1, 'the second draft is never sent')
        self.assertEqual(self.states(), ['awaiting-approval', 'failed'])


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
