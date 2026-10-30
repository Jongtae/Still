"""Connector prerequisite, connection handoff and exactly-once resume.

These tests cover WU3 of PA1-CONV-01.  The owner outcome is that an
unavailable capability produces the smallest next action and a safe
connection handoff, and that the original Work resumes *once* afterwards.

The negative cases are the substance, so they come first: denied, expired,
wrong-owner, replayed and superseded connections must not resume Work, and
neither a duplicated callback nor a restart may run it twice.  Every one of
them is mutation checked - the mutants are recorded in the PR - and the
exactly-once cases are driven by actually firing the duplicate rather than by
asserting a counter nobody incremented.
"""
import hashlib
import json
import tempfile
import unittest

from cryptography.fernet import Fernet

from personal_agent.calendar import (CALENDAR_READ_SCOPE, CALENDAR_SPEC, CALENDAR_WRITE_CONNECTOR_ID,
                                     CalendarConnector,
                                     CALENDAR_WRITE_SCOPE, CALENDAR_WRITE_SPEC)
from personal_agent.connector_contract import (PENDING_WORK_KEY, ConnectorContractError,
                                               ConnectorRegistry, ConnectorState, ConnectorStatus,
                                               ResumeState)
from personal_agent.conversation_handoff import (CONVERSATION_RESUME_KEY, ConnectorHandoff,
                                                 ConversationHandoffError, ConversationJudgments,
                                                 INTENT_CALENDAR_CREATE, INTENT_MAIL_SEARCH,
                                                 IntentClassifier, SUPERSEDED_WORK_ERROR)
from personal_agent.decision import (OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine,
                                     SelectionDecision, UnavailableDecisionEngine, fixture_confidence)
from personal_agent.gmail import (GMAIL_CONNECTOR, GMAIL_CONNECTOR_ID, GMAIL_READONLY_SCOPE,
                                  EncryptedGmailSecretStore, GmailConnector)
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

GENERATION = 'gen-one'
CHAT = 77
OWNER = f'telegram:{CHAT}'
OTHER_CHAT = 99
OTHER_OWNER = f'telegram:{OTHER_CHAT}'
GMAIL_SCOPES = (GMAIL_READONLY_SCOPE,)

MAIL_REQUEST = '메일에서 예산 관련 내용 찾아줘'
CALENDAR_REQUEST = '내일 오후 3시에 팀 회의 일정 잡아줘'
#: #22: a calendar request is the worker's; with no AI route configured, this is what the owner is told.
NO_AI_ROUTE = '설정에서 모델 또는 구독 엔진을 먼저 연결하세요.'
#: #672: a calendar create is selected by the DecisionEngine's
#: ``capability-need`` judgment, scripted here for exactly these requests.
CALENDAR_REQUESTS = (CALENDAR_REQUEST, '내일 오후 3시에 치과 일정 잡아줘')

#: A request may itself contain a secret.  This one is used to prove the
#: pending resume reference never becomes a second place it is stored.
SECRET_MAIL_REQUEST = '메일에서 sk-live-SHOULD-NEVER-PERSIST 관련 내용 찾아줘'

#: #173: a mailbox read is selected by the DecisionEngine's ``capability-need``
#: judgment, never by mail words; the fixture judges exactly these requests.
#: The judgment reads the secret request redacted, as ``ConversationJudgments`` sends it.
MAIL_REQUESTS = (MAIL_REQUEST, SECRET_MAIL_REQUEST, '메일에서 [redacted] 관련 내용 찾아줘',
                 '메일에서 계약서 관련 내용 찾아줘',
                 '메일에서 영수증 관련 내용 찾아줘', '메일에서 숙소 예약 확인 메일 찾아줘',
                 '아니 그거 말고 메일에서 숙소 예약 확인 메일 찾아줘')
#: The scripted worker's model endpoint (a fixture; nothing is contacted).
MODEL_ENDPOINT = 'https://model.example.test/v1'


def scripted_mail_worker(body):
    """A test double for the owner's AI on the compatible-API route (#173).

    For a mail request it calls its own ``mail_search`` once and answers with
    what the tool returned; anything else gets a short answer.  It stands in
    for a provider; the parking, resume and exactly-once policy under test is
    Still's and is not touched.
    """
    names = [tool.get('function', {}).get('name') for tool in body.get('tools', [])]
    if 'agentos_connection_probe' in names:
        return {'choices': [{'message': {'tool_calls': [{'id': 'p', 'type': 'function', 'function': {
            'name': 'agentos_connection_probe', 'arguments': '{}'}}]}}]}
    messages = body.get('messages', [])
    results = [str(message.get('content') or '') for message in messages if message.get('role') == 'tool']
    if results:
        return {'choices': [{'message': {'content': '\n'.join(results)}}]}
    users = [message.get('content') for message in messages if message.get('role') == 'user']
    latest = users[-1] if users and isinstance(users[-1], str) else ''
    if 'mail_search' in names and any(request in latest for request in MAIL_REQUESTS):
        return {'choices': [{'message': {'content': None, 'tool_calls': [{'id': 'm', 'type': 'function', 'function': {
            'name': 'mail_search', 'arguments': json.dumps({'query': '예산'})}}]}}]}
    return {'choices': [{'message': {'content': '답변입니다.'}}]}


class HandoffTestCase(unittest.TestCase):
    """One paired Telegram owner, one fixture Gmail, one controllable clock."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.clock = [1_000.0]
        self.keys = iter(range(10_000))
        self.sent = []
        self.gmail_calls = []
        self.gmail_list = {'messages': [{'id': 'm_1', 'threadId': 't_1'}]}
        self.gmail_metadata = {
            'id': 'm_1', 'threadId': 't_1',
            'payload': {'headers': [{'name': 'Subject', 'value': '예산 승인 안내'},
                                    {'name': 'From', 'value': 'Finance <finance@example.test>'},
                                    {'name': 'Date', 'value': 'Mon, 1 Sep 2026 10:00:00 +0000'}]},
        }

        def telegram(url, body, headers=None, timeout=60):
            if '/sendMessage' in url or '/editMessageText' in url:
                self.sent.append(body)
                return {'ok': True, 'result': {'message_id': 1 + len(self.sent)}}
            raise AssertionError('unexpected Telegram call: ' + url)

        def gmail_transport(method, endpoint, params, headers):
            self.gmail_calls.append((method, endpoint, params))
            if endpoint.endswith('/messages'):
                return self.gmail_list
            return self.gmail_metadata

        self.encrypted = EncryptedGmailSecretStore(self.store, Fernet.generate_key())
        self.registry = ConnectorRegistry(
            self.encrypted, (GMAIL_CONNECTOR, CALENDAR_SPEC, CALENDAR_WRITE_SPEC),
            clock=lambda: self.clock[0])
        self.gmail = GmailConnector(self.encrypted, 'gmail-client',
                                    'https://connect.example.test/gmail/callback',
                                    registry=self.registry, transport=gmail_transport,
                                    now=lambda: self.clock[0])
        def model(url, body, headers=None, timeout=60):
            if url.startswith(MODEL_ENDPOINT):
                return scripted_mail_worker(body)
            return telegram(url, body, headers, timeout)

        self.service = AgentService(self.store, ModelAdapter(model), telegram,
                                    connector_registry=self.registry, gmail=self.gmail)
        self.service.use_decision_engine(FixtureDecisionEngine(judge=self.reached, choose=self.none_unsupported))
        # Replace the default-clock handoff the service builds so expiry can be
        # driven deterministically.  The store and registry are the same ones.
        self.service.connector_handoff = ConnectorHandoff(self.store, self.registry,
                                                          now=lambda: self.clock[0], token_owner=lambda owner: 'local-owner')
        self.handoff = self.service.connector_handoff
        self.store.put('telegram', {'enabled': True, 'mode': 'owner-token', 'username': 'ownerbot',
                                    'generation': GENERATION, 'cursor': 0, 'user_id': CHAT})

    # -- fixtures ----------------------------------------------------------
    @staticmethod
    def reached(context, proposition):
        """#173: the scripted worker's answer serves the request; nothing else is judged here."""
        if context.purpose == 'goal-reached':
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        return None

    @staticmethod
    def none_unsupported(context, candidates, question):
        if context.purpose == 'unsupported-capability':
            return SelectionDecision(OUTCOME_DECIDED, 'none-of-these', candidates,
                                     fixture_confidence())
        if context.purpose == 'capability-need' and context.facts.get('owner_message') in CALENDAR_REQUESTS:
            return SelectionDecision(OUTCOME_DECIDED, INTENT_CALENDAR_CREATE, candidates,
                                     fixture_confidence())
        if context.purpose == 'capability-need' and context.facts.get('owner_message') in MAIL_REQUESTS:
            return SelectionDecision(OUTCOME_DECIDED, INTENT_MAIL_SEARCH, candidates,
                                     fixture_confidence())

    def judge_withdrawal(self, *withdrawing):
        """Put a fixture DecisionEngine behind the service.

        The fixture judges exactly ``withdrawing`` as withdrawals (with full
        confidence) and every other turn as not; it records every question
        so a test can also prove when nothing was asked.  It stands in for a
        provider; the cancel, card and notice that follow are Still policy
        under test, and that policy code is not touched by the swap.
        """
        asked = []

        def judge(context, proposition):
            if context.purpose == 'goal-reached':
                # #173: the scripted worker's answer to a resumed mail request.
                return self.reached(context, proposition)
            # (utterance, parked connector ids) is what the tests read.
            asked.append((context.facts.get('owner_message'), self.handoff.parked_for(OWNER)))
            return BinaryDecision(OUTCOME_DECIDED, context.facts.get('owner_message') in withdrawing,
                                 fixture_confidence())

        self.service.use_decision_engine(FixtureDecisionEngine(judge=judge,
                                                               choose=self.none_unsupported))
        return asked

    def enqueue(self, message, chat_id=CHAT):
        return self.store.enqueue(message, f'wu3-{next(self.keys)}',
                                  channel=f'telegram:{GENERATION}', chat_id=chat_id)

    def park(self, message=MAIL_REQUEST, chat_id=CHAT):
        """Run one request that needs a connector and assert it was parked."""
        job_id = self.enqueue(message, chat_id=chat_id)
        self.assertTrue(self.service.run_one())
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'awaiting_connection')
        return job_id

    def enable_worker(self):
        """Give the resumed request a worker (#173): the scripted AI on a compatible-API route."""
        if self.store.config('model', {}).get('endpoint') == MODEL_ENDPOINT:
            return
        self.service.save_model({'provider': 'compatible', 'endpoint': MODEL_ENDPOINT,
                                 'model': 'test-model', 'api_key': 'k'})
        self.assertTrue(self.service.test_model()['ok'])

    def connect_gmail(self, owner='local-owner'):
        """Complete a *fixture* OAuth exchange; no live Google call is made.

        #173: a mail request the connection resumes is the worker's, which
        searches mail with its own tool, so connecting Gmail also gives this
        fixture its scripted worker.  A calendar request parked before then
        stays parked; one parked after it would reach the worker instead.
        """
        self.enable_worker()
        self.gmail.begin_oauth(owner)
        pending = self.encrypted.secret(
            'gmail_oauth_pending:' + hashlib.sha256(owner.encode()).hexdigest())
        return self.gmail.complete_oauth(
            owner, {'state': pending['state'], 'code': 'fixture-code'},
            lambda request: {'access_token': 'fixture-access', 'refresh_token': 'fixture-refresh',
                             'expires_in': 7_200, 'scope': GMAIL_READONLY_SCOPE})

    def connect_calendar_write(self, owner='local-owner'):
        """Record fixture connector state; this is not an observed connection."""
        return self.registry.transition(owner, CALENDAR_WRITE_CONNECTOR_ID,
                                        ConnectorState.CONNECTED,
                                        granted_scopes=(CALENDAR_WRITE_SCOPE,))

    def drain(self, limit=5):
        """Run the worker until nothing is queued; returns how many Work ran.

        A resumed mail request is the worker's (#173), so one in the queue gets
        the scripted worker; a resumed calendar request keeps this fixture's
        no-AI-route answer, which its tests read.
        """
        if any(job.get('status') == 'queued' and job.get('message') in MAIL_REQUESTS
               for job in self.store.jobs()):
            self.enable_worker()
        ran = 0
        while ran < limit and self.service.run_one():
            ran += 1
        return ran

    def searches(self):
        """How many Gmail *list* calls happened - one per executed mail Work."""
        return len([call for call in self.gmail_calls if call[1].endswith('/messages')])

    def assistant_messages(self, job_id):
        with self.store.db() as db:
            return [row['content'] for row in db.execute(
                "SELECT content FROM messages WHERE job_id=? AND role='assistant' ORDER BY id",
                (job_id,))]

    def index(self):
        raw = self.store.secret(CONVERSATION_RESUME_KEY)
        return raw if isinstance(raw, dict) else {}


class NegativeResumeTests(HandoffTestCase):
    """A connection that is denied, expired, wrong-owner, replayed or stale."""

    def test_a_denied_connection_fails_the_parked_work_and_cannot_resume_it(self):
        job_id = self.park()
        # The owner refused at Google, so the connector never became CONNECTED.
        work_id = self.service.deny_connector_work(GMAIL_CONNECTOR_ID, OWNER, 'denied')
        self.assertEqual(work_id, job_id)
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'failed')
        self.assertIn('연결이 승인되지 않아', job['error'])
        # A later success report must not revive it.
        self.connect_gmail()
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, 'no_pending_work')
        self.assertEqual(self.drain(), 0)
        self.assertEqual(self.searches(), 0)
        self.assertEqual(self.store.job(job_id)['status'], 'failed')

    def test_a_resume_before_the_connector_is_connected_is_refused_not_ignored(self):
        job_id = self.park()
        # The contract's own `require_connected` is the barrier: a claim can
        # never outrun the connector lifecycle it depends on.
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, ConnectorState.DISCONNECTED.value)
        # The contract marks its row SUPERSEDED on every authority and scope
        # failure, so the handoff is dead here. Parking the Work against a
        # dead row is the stranding defect; it fails explicitly instead.
        self.assertEqual(self.store.job(job_id)['status'], 'failed')
        self.assertEqual(self.drain(), 0)
        self.assertEqual(self.searches(), 0)
        # The refusal is stated to the owner rather than silently swallowed.
        self.assertTrue(any('이어서 처리하지 않았습니다' in str(body.get('text', ''))
                            for body in self.sent))

    def test_an_expired_handoff_cannot_resume_the_work(self):
        job_id = self.park()
        self.clock[0] += 901  # past the 900s resume TTL
        self.connect_gmail()
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, 'expired_resume')
        # An expired handoff is terminal, so the Work must not be left parked.
        # The original form of this assertion required `awaiting_connection`,
        # which was the stranded state: releasing the dead handle removes the
        # only route to resume, supersede or deny the Work, the task card draws
        # its cancel button for `queued` alone, and progress then reports
        # "연결 대기" forever with nothing the owner can do. C8 requires the
        # failure to stay explicit instead.
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'failed')
        self.assertTrue(job['error'], 'an abandoned Work must carry an owner-readable reason')
        self.assertEqual(self.drain(), 0)
        self.assertEqual(self.searches(), 0)

    def test_a_connection_completed_by_a_different_owner_cannot_resume_the_work(self):
        job_id = self.park()
        self.connect_gmail(OTHER_OWNER)
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OTHER_OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, 'wrong_owner')
        # Refused before `claim` is reached, so the contract row is untouched
        # and the handoff stays alive: the rightful owner can still resume.
        self.assertEqual(self.store.job(job_id)['status'], 'awaiting_connection')
        self.assertEqual(self.drain(), 0)
        self.assertEqual(self.searches(), 0)

    def test_the_contract_owner_binding_is_the_second_wrong_owner_barrier(self):
        """Even with this module's own owner check bypassed, the claim fails."""
        self.park()
        self.connect_gmail(OTHER_OWNER)
        record = self.handoff.record(GMAIL_CONNECTOR_ID)
        with self.assertRaises(ConnectorContractError) as caught:
            self.handoff.pending.claim(record['resume_token'], OTHER_OWNER, GMAIL_CONNECTOR_ID,
                                       GMAIL_SCOPES, record['handoff_id'])
        self.assertEqual(caught.exception.reason, 'invalid_resume')

    def test_a_handoff_parked_under_a_previous_bot_generation_cannot_resume(self):
        job_id = self.park()
        # Reconnecting the bot mints a new generation; the conversation that
        # parked this Work no longer exists.
        config = self.store.config('telegram', {})
        config['generation'] = 'gen-two'
        self.store.put('telegram', config)
        self.connect_gmail()
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, 'generation_changed')
        # Refused before `claim` is reached, so the contract row is untouched
        # and the handoff stays alive: the rightful owner can still resume.
        self.assertEqual(self.store.job(job_id)['status'], 'awaiting_connection')
        self.assertEqual(self.searches(), 0)

    def test_a_grant_that_is_not_the_requested_scope_set_is_refused(self):
        """Exact scope equality, not a subset test: a narrower grant and a
        wider one are both refused, so a resume can never run against
        authority the handoff did not ask the owner for."""
        for granted in ((), (GMAIL_READONLY_SCOPE, CALENDAR_WRITE_SCOPE)):
            with self.subTest(granted=granted):
                self.registry.transition('local-owner', GMAIL_CONNECTOR_ID, ConnectorState.DISCONNECTED)
                job_id = self.park()
                self.connect_gmail()
                with self.assertRaises(ConversationHandoffError) as caught:
                    self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, granted)
                self.assertEqual(caught.exception.reason, 'scope_mismatch')
        # The contract marks its row SUPERSEDED on every authority and scope
        # failure, so the handoff is dead here. Parking the Work against a
        # dead row is the stranding defect; it fails explicitly instead.
                self.assertEqual(self.store.job(job_id)['status'], 'failed')
        self.assertEqual(self.drain(), 0)
        self.assertEqual(self.searches(), 0)


class ExactlyOnceTests(HandoffTestCase):
    """Three independent barriers, each driven by a real duplicate."""

    def test_a_duplicated_connection_callback_cannot_resume_the_work_twice(self):
        job_id = self.park()
        self.connect_gmail()
        first = self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual((first['work_id'], first['scheduled']), (job_id, True))
        # Barrier 1, actually fired: the same callback is delivered again.
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, 'no_pending_work')
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.searches(), 1)
        self.assertEqual(self.store.job(job_id)['status'], 'succeeded')

    def test_a_replayed_callback_that_still_holds_the_handle_is_refused(self):
        """Barrier 2: the process died after `complete` but before releasing."""
        job_id = self.park()
        self.connect_gmail()
        saved = self.index()
        self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.store.secret(CONVERSATION_RESUME_KEY, saved)  # the lost release
        self.assertIsNotNone(self.handoff.record(GMAIL_CONNECTOR_ID))
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, 'replayed_resume')
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.searches(), 1)
        self.assertEqual(self.store.job(job_id)['status'], 'succeeded')

    def test_a_restart_recovering_a_claimed_handoff_does_not_run_the_work_twice(self):
        """Barrier 3: the durable compare-and-set, the only one that survives
        a crash between `claim` and `complete` - which the contract *intends*
        the claimant to recover from."""
        job_id = self.park()
        self.connect_gmail()
        original = self.handoff.pending.complete

        def dying_complete(*args, **kwargs):
            raise RuntimeError('process died before completing the handoff')

        self.handoff.pending.complete = dying_complete
        with self.assertRaises(RuntimeError):
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.handoff.pending.complete = original
        # The Work was scheduled and runs; the handoff record was never released.
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.store.job(job_id)['status'], 'succeeded')
        # Idle settlement completes the already-finished claim and drops its
        # index, without scheduling it again or rewriting the original result.
        self.assertIsNone(self.handoff.record(GMAIL_CONNECTOR_ID))
        self.assertEqual({row['state'] for row in self.store.config(PENDING_WORK_KEY, {}).values()},
                         {ResumeState.COMPLETED.value})
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, 'no_pending_work')
        self.assertEqual(self.drain(), 0)
        self.assertEqual(self.searches(), 1)
        self.assertEqual(self.store.job(job_id)['status'], 'succeeded')

    def test_the_claimed_handoff_state_is_what_recovery_relies_on(self):
        """The recovery above is a contract state, not an accident."""
        self.park()
        self.connect_gmail()
        record = self.handoff.record(GMAIL_CONNECTOR_ID)
        self.handoff.pending.claim(record['resume_token'], OWNER, GMAIL_CONNECTOR_ID,
                                   GMAIL_SCOPES, record['handoff_id'])
        rows = self.store.config(PENDING_WORK_KEY, {})
        self.assertEqual({ResumeState(row['state']) for row in rows.values()},
                         {ResumeState.CLAIMED})

    def test_retry_and_restart_do_not_duplicate_a_consequential_calendar_effect(self):
        """Calendar's parked Work must execute once, however often the
        connection is reported.  What it executes is asserted separately."""
        job_id = self.park(CALENDAR_REQUEST)
        self.connect_calendar_write()
        self.service.resume_connector_work(CALENDAR_WRITE_CONNECTOR_ID, OWNER,
                                           (CALENDAR_WRITE_SCOPE,))
        for _ in range(3):  # duplicated callbacks, actually fired
            with self.assertRaises(ConversationHandoffError):
                self.service.resume_connector_work(CALENDAR_WRITE_CONNECTOR_ID, OWNER,
                                                   (CALENDAR_WRITE_SCOPE,))
        self.assertEqual(self.drain(), 1)
        # One parking message plus exactly one execution of the resumed Work.
        self.assertEqual(len(self.assistant_messages(job_id)), 2)
        # And no calendar mutation was produced or recorded by any of it.
        self.assertEqual([row for row in self.store.config('personal_assistant_evidence', [])
                          if 'calendar' in row.get('kind', '')], [])


class SupersessionTests(HandoffTestCase):
    """A changed request must not execute its stale intent later."""

    def test_a_changed_request_supersedes_the_pending_resume_path(self):
        job_id = self.park()
        # The owner changes course while the handoff is still pending.  Since
        # FU1-DEC-01 / #521 "changed course" is a judgment; the double judges
        # this turn a withdrawal and the supersession is what is under test.
        self.judge_withdrawal('아니 그거 말고 오늘 일정 대신 메모 목록 보여줘')
        changed = self.enqueue('아니 그거 말고 오늘 일정 대신 메모 목록 보여줘')
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(job_id)['status'], 'cancelled')
        self.assertIn('요청이 바뀌어', self.store.job(job_id)['error'])
        self.assertNotEqual(changed, job_id)
        self.connect_gmail()
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, 'no_pending_work')
        self.assertEqual(self.drain(), 0)
        self.assertEqual(self.searches(), 0, 'the stale mail intent must never run')

    def test_supersession_never_touches_work_that_was_never_parked(self):
        """Both supersede statements are guarded on the parked state, not just one.

        The status-cancel guard was covered; the delivery-cancel guard above it
        was not. Its reachable case is *not* a resumed Work -- the resume CAS
        sets ``delivery='none'``, so the ``delivery='pending'`` predicate
        already excludes those. The case that matters is an ordinary Work that
        was never parked at all and is simply waiting to be delivered: without
        the status guard, naming it here would cancel a delivery the owner is
        owed for an answer that already succeeded.
        """
        ordinary = self.enqueue('오늘 기분이 어때')
        self.assertTrue(self.service.run_one())
        job = self.store.job(ordinary)
        self.assertEqual(job['delivery'], 'pending', 'fixture must leave a delivery to protect')
        status_before = job['status']

        cancelled = self.service.cancel_superseded_work([ordinary])

        self.assertEqual(cancelled, [], 'a Work that was never parked must not be reported cancelled')
        job = self.store.job(ordinary)
        self.assertEqual(job['status'], status_before, 'supersession must not touch an unparked Work')
        self.assertEqual(job['delivery'], 'pending',
                         'supersession must not cancel the delivery of an unparked Work')

    def test_same_connector_requests_both_resume_to_their_original_results(self):
        first = self.park()
        second = self.park('메일에서 계약서 관련 내용 찾아줘')
        self.assertEqual(self.store.job(first)['status'], 'awaiting_connection')
        self.assertEqual(self.store.job(second)['status'], 'awaiting_connection')
        self.connect_gmail()
        resumed = self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual({row['work_id'] for row in resumed['resumed']}, {first, second})
        self.assertEqual(self.drain(), 2)
        self.assertEqual(self.searches(), 2)
        for work_id in (first, second):
            self.assertEqual(self.store.job(work_id)['status'], 'succeeded')
            self.assertIn('예산 승인 안내', self.store.job(work_id)['response'])

    def test_superseding_never_rewrites_work_that_already_left_the_parked_state(self):
        job_id = self.park()
        self.connect_gmail()
        self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.service.cancel_superseded_work([job_id]), [])
        self.assertEqual(self.store.job(job_id)['status'], 'succeeded')



class ParkedRequestSurvivesConversationTests(HandoffTestCase):
    """FU1-473 / #473: only a judged withdrawal cancels a parked request.

    Reproduces TEST-FIRST-USER-01 / #472 scenario A turns 20-21 and scenario B
    turns 25-26: the owner acknowledges the connection guidance, connects, and
    the parked request must then run exactly once as it promised.
    """

    def park_with_card(self, message):
        """Park a request whose Telegram card already shows the waiting state.

        The card is recorded directly: nothing sends a new card since #958,
        and what is under test is what happens to a card that already exists.
        """
        job_id = self.park(message)
        self.store.save_task_card(job_id, CHAT, 500 + len(self.sent), 'awaiting_connection')
        return job_id

    def say(self, message, chat_id=CHAT):
        job_id = self.enqueue(message, chat_id=chat_id)
        self.assertTrue(self.service.run_one())
        return job_id

    def test_scenario_a_acknowledgement_then_gmail_connection_resumes_once(self):
        job_id = self.park('메일에서 숙소 예약 확인 메일 찾아줘')
        self.say('알겠어, 지금 연결할게')
        self.assertEqual(self.store.job(job_id)['status'], 'awaiting_connection')
        self.connect_gmail()
        resumed = self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(resumed['work_id'], job_id)
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.searches(), 1)
        self.assertEqual(self.store.job(job_id)['status'], 'succeeded')

    def test_scenario_b_acknowledgement_then_calendar_connection_resumes_once(self):
        job_id = self.park('내일 오후 3시에 치과 일정 잡아줘')
        self.say('잠깐만, 연결하고 올게')
        self.assertEqual(self.store.job(job_id)['status'], 'awaiting_connection')
        self.connect_calendar_write()
        resumed = self.service.resume_connector_work(CALENDAR_WRITE_CONNECTOR_ID, OWNER,
                                                     (CALENDAR_WRITE_SCOPE,))
        self.assertEqual(resumed['work_id'], job_id)
        self.assertEqual(self.drain(), 1)
        self.assertNotEqual(self.store.job(job_id)['status'], 'awaiting_connection')
        # Resuming reaches at most a draft; no calendar effect was produced.

    def test_ordinary_conversation_and_other_requests_do_not_cancel(self):
        job_id = self.park_with_card(MAIL_REQUEST)
        for message in ('/start', '안녕', '오늘 기분이 어때', '메모 목록', CALENDAR_REQUEST):
            with self.subTest(message=message):
                self.say(message)
                self.assertEqual(self.store.job(job_id)['status'], 'awaiting_connection')
        self.assertIsNotNone(self.handoff.record(GMAIL_CONNECTOR_ID))
        self.assertNotIn(SUPERSEDED_WORK_ERROR, [body.get('text') for body in self.sent])

    def test_with_the_placeholder_judge_correction_words_keep_the_request(self):
        # FU1-DEC-01 / #521: the placeholder judges nothing, so policy takes
        # its fallback and keeps the promise; no cue list decides instead.
        job_id = self.park_with_card(MAIL_REQUEST)
        for message in ('아니 그거 말고 메모 목록 보여줘', '취소해줘', '항공권 취소 규정 알려줘'):
            with self.subTest(message=message):
                self.say(message)
                self.assertEqual(self.store.job(job_id)['status'], 'awaiting_connection')
        self.assertNotIn(SUPERSEDED_WORK_ERROR, [body.get('text') for body in self.sent])

    def test_a_judged_withdrawal_cancels_and_is_announced(self):
        asked = self.judge_withdrawal('아니 그거 말고 메모 목록 보여줘')
        job_id = self.park_with_card(MAIL_REQUEST)
        card = self.store.task_card(job_id)
        before = len(self.sent)
        self.say('아니 그거 말고 메모 목록 보여줘')
        # The judgment saw the utterance and which connector waits, nothing more.
        self.assertIn(('아니 그거 말고 메모 목록 보여줘', (GMAIL_CONNECTOR_ID,)), asked)
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(job['error'], SUPERSEDED_WORK_ERROR)
        # The parked request's own card changes...
        self.assertEqual(self.store.task_card(job_id)['state'], 'superseded')
        edits = [body for body in self.sent[before:]
                 if body.get('message_id') == card['message_id'] and body.get('chat_id') == CHAT]
        self.assertEqual([body['text'] for body in edits], [SUPERSEDED_WORK_ERROR])
        # ...and the paired chat is told once, without request content.
        notices = [body for body in self.sent[before:]
                   if body.get('text') == SUPERSEDED_WORK_ERROR and 'message_id' not in body]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['chat_id'], CHAT)
        self.connect_gmail()
        with self.assertRaises(ConversationHandoffError):
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(self.drain(), 0)
        self.assertEqual(self.searches(), 0)

    def test_an_independent_same_connector_request_keeps_the_old_card_waiting(self):
        first = self.park_with_card(MAIL_REQUEST)
        before = len(self.sent)
        second = self.park_with_card('메일에서 계약서 관련 내용 찾아줘')
        self.assertEqual(self.store.job(first)['status'], 'awaiting_connection')
        self.assertEqual(self.store.task_card(first)['state'], 'awaiting_connection')
        self.assertEqual(self.store.job(second)['status'], 'awaiting_connection')
        notices = [body for body in self.sent[before:]
                   if body.get('text') == SUPERSEDED_WORK_ERROR and 'message_id' not in body]
        self.assertEqual(notices, [])

    def test_judged_withdrawal_cancels_only_the_focused_wait(self):
        first = self.park(MAIL_REQUEST)
        second = self.park('메일에서 계약서 관련 내용 찾아줘')
        self.judge_withdrawal('아니 그거 말고 메모 목록 보여줘')
        self.say('아니 그거 말고 메모 목록 보여줘')
        self.assertEqual(self.store.job(first)['status'], 'awaiting_connection')
        self.assertEqual(self.store.job(second)['status'], 'cancelled')
        self.connect_gmail()
        self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.store.job(first)['status'], 'succeeded')

    def test_a_resumed_work_is_not_judged_again(self):
        # This Work's words were judged when it first ran; its resume must not
        # cancel a request parked for another connector since.
        asked = self.judge_withdrawal('아니 그거 말고 메일에서 숙소 예약 확인 메일 찾아줘')
        mail = self.park('아니 그거 말고 메일에서 숙소 예약 확인 메일 찾아줘')
        calendar = self.park(CALENDAR_REQUEST)
        self.connect_gmail()
        self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        before = len(self.sent)
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.store.job(mail)['status'], 'succeeded')
        self.assertEqual(self.store.job(calendar)['status'], 'awaiting_connection')
        self.assertNotIn(SUPERSEDED_WORK_ERROR, [body.get('text') for body in self.sent[before:]])
        self.assertEqual(len(asked), 1, 'only the calendar turn found something parked to ask about')

    def test_another_owner_identity_withdrawal_does_not_cancel(self):
        asked = self.judge_withdrawal('아니 됐고 메모 목록 보여줘')
        job_id = self.park_with_card(MAIL_REQUEST)
        other = self.store.enqueue('아니 됐고 메모 목록 보여줘', 'wu3-web-correction', channel='web')
        self.assertTrue(self.service.run_one())
        self.assertNotEqual(other, job_id)
        self.assertEqual(self.store.job(job_id)['status'], 'awaiting_connection')
        self.assertIsNotNone(self.handoff.record(GMAIL_CONNECTOR_ID))
        # The web owner has nothing parked, so no judgment is asked and the
        # Telegram owner's parked connector ids never reach a judge.
        self.assertEqual(asked, [])

class PairingBoundaryTests(HandoffTestCase):
    """Refusal notices must honour the same pairing gate as every other send."""

    def test_a_refusal_notice_never_reaches_an_unpaired_chat(self):
        """The wrong-owner path is exactly where owner_id must not be trusted.

        `_notify_owner` derived its destination from the caller-supplied
        owner_id, so a resume attempt naming another chat produced an outbound
        Telegram message to *that* chat -- at the one moment the code had
        already decided the value was untrustworthy. Every other send in the
        service checks the paired user_id and the enabled flag; this one did
        not.
        """
        self.park()
        self.connect_gmail()
        before = len(self.sent)
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OTHER_OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, 'wrong_owner')
        for body in self.sent[before:]:
            self.assertNotEqual(body.get('chat_id'), OTHER_CHAT,
                                'a refusal must never be delivered to an unpaired chat')

    def test_the_paired_owner_still_receives_refusals(self):
        """Positive control: the gate is a gate, not a mute button."""
        self.park()
        self.clock[0] += 901
        self.connect_gmail()
        before = len(self.sent)
        with self.assertRaises(ConversationHandoffError):
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertTrue([b for b in self.sent[before:] if b.get('chat_id') == CHAT],
                        'the paired owner must still be told why nothing resumed')

    def test_a_notice_is_withheld_when_telegram_is_disabled(self):
        cfg = dict(self.store.config('telegram', {}))
        cfg['enabled'] = False
        self.store.put('telegram', cfg)
        self.assertFalse(self.service._notify_owner(OWNER, 'notice'))


class AbandonedHandoffTests(HandoffTestCase):
    """A dead handoff must not leave an unreachable parked Work behind."""

    def test_an_expired_handoff_leaves_no_work_the_owner_cannot_clear(self):
        """Releasing the handle removes every exit from awaiting_connection.

        Resume, supersession and denial all look the Work up through the resume
        index, and the task card only draws its cancel button for `queued`, so
        a released-but-parked Work reported "연결 대기" with nothing the owner
        could do. C8 requires the failure to stay explicit.
        """
        job_id = self.park()
        self.clock[0] += 901
        self.connect_gmail()
        with self.assertRaises(ConversationHandoffError):
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(self.store.job(job_id)['status'], 'failed')
        # And the stranding route specifically: supersession can no longer
        # reach it, which is why the terminal state has to be set here.
        self.assertEqual(self.service.supersede_pending_handoffs(), [])
        self.assertEqual(self.store.job(job_id)['status'], 'failed')

    def test_a_premature_callback_fails_the_work_explicitly_rather_than_stranding_it(self):
        """A callback before the connector is connected is terminal by contract.

        `PendingWorkRegistry.claim` transitions its row to SUPERSEDED whenever
        `require_connected` fails, so the handoff can never succeed again --
        even though the reason it surfaces is "disconnected", which does not
        say so. The owner therefore gets an explicit failure and a notice,
        instead of a job parked against a dead contract row.
        """
        job_id = self.park()
        before = len(self.sent)
        with self.assertRaises(ConversationHandoffError):
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'failed')
        self.assertTrue(job['error'])
        self.assertTrue([b for b in self.sent[before:] if b.get('chat_id') == CHAT],
                        'the owner must be told the handoff died')
        # And connecting afterwards must not silently revive it.
        self.connect_gmail()
        with self.assertRaises(ConversationHandoffError) as caught:
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(caught.exception.reason, 'no_pending_work')
        self.assertEqual(self.searches(), 0)


class ShippedPathGuardTests(HandoffTestCase):
    """The no-connector guards on routes that run in the shipped deployment."""

    def test_a_mail_request_without_gmail_refuses_instead_of_crashing(self):
        """This is the one WU3-adjacent line that executes without wiring.

        The connector handoff is inert in the shipped deployment, but the
        mail route is reachable, and `self.gmail` is `None` there. Without
        the guard the worker raises AttributeError on NoneType, which is the
        same unhandled-exception shape WU4 fixed one file away.
        """
        store = QuickStore(tempfile.mkdtemp(dir=self.temp.name))
        bare = AgentService(store)  # exactly how the shipped deployment builds it
        bare.use_decision_engine(FixtureDecisionEngine(choose=lambda context,candidates,question:
            SelectionDecision(OUTCOME_DECIDED,'none-of-these',candidates,fixture_confidence())
            if context.purpose == 'unsupported-capability' else None))
        job_id = store.enqueue(MAIL_REQUEST, 'k-bare', 'web', None)
        self.assertTrue(bare.run_one())
        job = store.job(job_id)
        self.assertEqual(job['status'], 'failed')
        self.assertNotIn('NoneType', str(job.get('error') or ''),
                         'the owner must get a refusal, not a Python attribute error')

    def test_a_mail_request_without_a_registry_does_not_park(self):
        """No registry means this installation declares no connectors."""
        store = QuickStore(tempfile.mkdtemp(dir=self.temp.name))
        bare = AgentService(store)
        job_id = store.enqueue(MAIL_REQUEST, 'k-bare2', 'web', None)
        bare.run_one()
        self.assertNotEqual(store.job(job_id)['status'], 'awaiting_connection')


class ReleaseGuardTests(HandoffTestCase):
    """The index must only ever be released by the handoff that owns it."""

    def test_a_stale_resume_cannot_release_a_newer_park(self):
        """Without the handoff_id check an in-flight stale resume would drop
        the record a newer request had just written, stranding the new Work.
        """
        first = self.park()
        stale = dict(self.handoff.record(GMAIL_CONNECTOR_ID))
        self.handoff.park(OWNER, first, GMAIL_CONNECTOR_ID, generation=GENERATION)
        second = first
        current = self.handoff.record(GMAIL_CONNECTOR_ID)
        self.assertNotEqual(current['handoff_id'], stale['handoff_id'])

        self.handoff._release(GMAIL_CONNECTOR_ID, stale['handoff_id'])

        survived = self.handoff.record(GMAIL_CONNECTOR_ID)
        self.assertIsNotNone(survived, 'a stale handoff must not release the current record')
        self.assertEqual(survived['handoff_id'], current['handoff_id'])
        self.assertEqual(survived['work_id'], second)

    def test_a_dead_handle_is_dropped_so_it_cannot_be_retried(self):
        self.park()
        self.clock[0] += 901
        self.connect_gmail()
        with self.assertRaises(ConversationHandoffError):
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertIsNone(self.handoff.record(GMAIL_CONNECTOR_ID),
                          'a terminal handoff must not stay resumable')


class OwnerSafetyTests(HandoffTestCase):
    """The resume reference must be as content free as ConversationFocus."""

    FIELDS = {'connector_id', 'work_id', 'handoff_id', 'resume_token', 'owner', 'generation'}

    def test_the_pending_resume_record_stores_no_utterance_subject_or_argument(self):
        job_id = self.park(SECRET_MAIL_REQUEST)
        record = self.handoff.record(GMAIL_CONNECTOR_ID)
        self.assertEqual(set(record), self.FIELDS)
        self.assertEqual(record['work_id'], job_id)
        self.assertEqual(record['connector_id'], GMAIL_CONNECTOR_ID)
        # Nothing derived from what the owner typed is present anywhere in it.
        serialized = repr(record)
        for fragment in ('sk-live', 'SHOULD-NEVER-PERSIST', '메일', '찾아'):
            self.assertNotIn(fragment, serialized)
        # The raw owner identifier is hashed exactly as the contract hashes it.
        self.assertNotIn(str(CHAT), record['owner'])
        self.assertRegex(record['owner'], r'\A[0-9a-f]{64}\Z')

    def test_no_durable_state_outside_the_owners_own_job_row_holds_the_request(self):
        job_id = self.park(SECRET_MAIL_REQUEST)
        with self.store.db() as db:
            config = ' '.join(str(row['value']) for row in db.execute('SELECT value FROM config'))
        secrets_text = self.store.secret_path.read_text()
        for blob in (config, secrets_text):
            self.assertNotIn('sk-live-SHOULD-NEVER-PERSIST', blob)
        # The utterance lives only where it always did: the Work the owner
        # created.  The resume reference points at it instead of copying it.
        self.assertIn('sk-live-SHOULD-NEVER-PERSIST', self.store.job(job_id)['message'])

    def test_the_conversation_focus_record_is_still_content_free(self):
        job_id=self.park(SECRET_MAIL_REQUEST)
        focus=self.service.conversation_focus.current()
        self.assertEqual(set(focus), {'intent', 'at', 'work_id'})
        self.assertEqual(focus['intent'], INTENT_MAIL_SEARCH)
        self.assertEqual(focus['work_id'],job_id)
        self.assertNotIn('sk-live-SHOULD-NEVER-PERSIST',repr(focus))


class PrerequisiteAndGuidanceTests(HandoffTestCase):
    """Detection happens before anything runs, and says only what is true."""

    def test_the_handoff_message_names_one_next_action_and_claims_nothing(self):
        job_id = self.park()
        guidance = self.store.job(job_id)['response']
        self.assertIn('Gmail', guidance)
        self.assertIn('연결해 주세요', guidance)
        self.assertIn('실행하지 않았습니다', guidance)
        for lie in ('연결되었습니다', '완료했습니다', '찾았습니다', '검색했습니다'):
            self.assertNotIn(lie, guidance)
        # Never fabricate tool execution: no provider call was made at all.
        self.assertEqual(self.gmail_calls, [])
        self.assertEqual(self.store.job(job_id)['delivery'], 'pending')

    def test_a_disconnected_gmail_request_parks_and_resumes_the_original_work(self):
        job_id = self.park()
        self.connect_gmail()
        self.assertEqual(self.registry.status('local-owner', GMAIL_CONNECTOR_ID).state,
                         ConnectorState.CONNECTED)
        self.service.resume_connector_work(GMAIL_CONNECTOR_ID, OWNER, GMAIL_SCOPES)
        self.assertEqual(self.drain(), 1)
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'succeeded')
        self.assertIn('예산 승인 안내', job['response'])
        self.assertEqual(self.searches(), 1)
        # The resumed Work is the original one, not a new request.
        self.assertEqual(job['message'], MAIL_REQUEST)

    def test_a_missing_calendar_write_grant_parks_even_when_the_read_grant_exists(self):
        self.registry.transition('local-owner', CALENDAR_SPEC.connector_id, ConnectorState.CONNECTED,
                                 granted_scopes=CALENDAR_SPEC.required_scopes)
        job_id = self.park(CALENDAR_REQUEST)
        guidance = self.store.job(job_id)['response']
        self.assertIn('Google Calendar', guidance)
        self.assertIn('실행하지 않았습니다', guidance)
        # A read grant never became a write grant on the way through.
        self.assertEqual(self.registry.status('local-owner', CALENDAR_SPEC.connector_id).granted_scopes,
                         CALENDAR_SPEC.required_scopes)
        self.assertEqual(self.registry.status('local-owner', CALENDAR_WRITE_CONNECTOR_ID).state,
                         ConnectorState.DISCONNECTED)

    def test_calendar_resume_reaches_the_worker_and_has_no_effect(self):
        """The resumed Work is the original utterance, so once the write grant
        exists it reaches the worker, whose draft tools make proposals only (#22).
        No AI route is configured here, so the owner is told to connect one and
        nothing is drafted or sent."""
        calls = []

        class Provider:
            def create(self, payload, key):
                calls.append(payload)
                return {'id': 'never', 'version': '"x"'}

        self.service.calendar = CalendarConnector(self.store, Provider(), registry=self.registry)
        job_id = self.park(CALENDAR_REQUEST)
        self.connect_calendar_write()
        self.service.resume_connector_work(CALENDAR_WRITE_CONNECTOR_ID, OWNER,
                                           (CALENDAR_WRITE_SCOPE,))
        self.assertEqual(self.drain(), 1)
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'failed')
        self.assertIn(NO_AI_ROUTE, job['error'])
        self.assertEqual(calls, [], 'a resumed create request must not reach the provider')

    def _resume_after_the_engine_changes(self, engine):
        """#672 review: the resumed Work runs what was judged when it parked."""
        calls = []

        class Provider:
            def create(self, payload, key):
                calls.append(payload)
                return {'id': 'never', 'version': '"x"'}

        self.service.calendar = CalendarConnector(self.store, Provider(), registry=self.registry)
        job_id = self.park(CALENDAR_REQUEST)
        self.assertIn(job_id, self.store.config(AgentService.JUDGED_RESUME_KEY, {}))
        self.assertNotIn(CALENDAR_REQUEST, json.dumps(self.store.config(AgentService.JUDGED_RESUME_KEY, {}),
                                                      ensure_ascii=False), 'only a digest of the words is kept')
        # The second judgment would now be unavailable, or different.
        self.service.use_decision_engine(engine)
        self.connect_calendar_write()
        self.service.resume_connector_work(CALENDAR_WRITE_CONNECTOR_ID, OWNER, (CALENDAR_WRITE_SCOPE,))
        self.assertEqual(self.drain(), 1)
        job = self.store.job(job_id)
        # #22: the judged capability resumes on the worker route (no AI route here).
        self.assertEqual(job['status'], 'failed')
        self.assertIn(NO_AI_ROUTE, job['error'])
        self.assertEqual(calls, [])
        self.assertNotIn(job_id, self.store.config(AgentService.JUDGED_RESUME_KEY, {}), 'consumed once')
        return engine

    def test_a_resumed_calendar_work_keeps_its_judgment_when_the_engine_is_unavailable(self):
        self._resume_after_the_engine_changes(UnavailableDecisionEngine())

    def test_a_resumed_calendar_work_is_not_judged_again(self):
        engine = self._resume_after_the_engine_changes(FixtureDecisionEngine(
            choose=lambda context, candidates, question: SelectionDecision(
                OUTCOME_DECIDED, 'none-of-these', candidates, fixture_confidence())))
        self.assertFalse([item for item in engine.asked if item[1].purpose == 'capability-need'])

    def test_calendar_resume_without_a_configured_connector_drafts_nothing(self):
        """With the specs registered but no connector object, the resumed Work
        reaches the worker (#22), whose draft tool would refuse; with no AI route
        configured here the owner is told to connect one, and nothing is drafted."""
        job_id = self.park(CALENDAR_REQUEST)
        self.connect_calendar_write()
        self.service.resume_connector_work(CALENDAR_WRITE_CONNECTOR_ID, OWNER,
                                           (CALENDAR_WRITE_SCOPE,))
        self.assertEqual(self.drain(), 1)
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'failed')
        self.assertIn(NO_AI_ROUTE, job['error'])

    def test_a_connected_row_with_a_narrower_grant_fails_closed_into_reauth(self):
        """Defence in depth against a stale or edited connector row."""
        real = self.registry.status

        def narrowed(owner_id, connector_id):
            status = real(owner_id, connector_id)
            if connector_id != GMAIL_CONNECTOR_ID:
                return status
            return ConnectorStatus(connector_id, ConnectorState.CONNECTED,
                                   status.required_scopes, (), status.health,
                                   status.connection_revision)

        self.connect_gmail()
        self.registry.status = narrowed
        self.addCleanup(setattr, self.registry, 'status', real)
        result = self.handoff.prerequisite(OWNER, GMAIL_CONNECTOR_ID)
        self.assertIsNotNone(result)
        self.assertEqual(result.recovery.value, 'reauthenticate')

    def test_an_unknown_connector_is_reported_rather_than_parked(self):
        handoff = ConnectorHandoff(self.store, ConnectorRegistry(self.encrypted, ()))
        self.service.connector_handoff = handoff
        job_id = self.enqueue(MAIL_REQUEST)
        self.assertTrue(self.service.run_one())
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'failed')
        self.assertIn('구성되어 있지', job['error'])
        self.assertEqual(handoff.record(GMAIL_CONNECTOR_ID), None)

    def test_an_installation_with_no_registry_keeps_its_previous_behaviour(self):
        """Injecting no connector registry must change nothing about WU3's handoff:
        nothing is parked, and the request reaches the worker route (#22), which
        here has no AI route, rather than a Still-held question about the event."""
        self.service.connector_handoff = None
        self.service.connector_registry = None
        job_id = self.enqueue(CALENDAR_REQUEST)
        self.assertTrue(self.service.run_one())
        job = self.store.job(job_id)
        self.assertEqual(job['status'], 'failed')
        self.assertIn(NO_AI_ROUTE, job['error'])
        self.assertEqual(self.index(), {})


class MailIntentTests(unittest.TestCase):
    """The mail route is a read, chosen by judgment; no mail word routes it (#173)."""

    MAIL_TEXTS = ('메일에서 예산 관련 내용 찾아줘', '받은편지함에서 계약서 확인해줘',
                  'search my email for the budget', 'check my inbox for the contract')

    def classifier(self, needs=None):
        needs = needs or {}

        def choose(context, candidates, question):
            if context.purpose == 'capability-need':
                return SelectionDecision(OUTCOME_DECIDED, needs.get(context.facts.get('owner_message'), 'none-of-these'),
                                         candidates, fixture_confidence())
            return None
        return IntentClassifier(judge=ConversationJudgments(FixtureDecisionEngine(choose=choose)))

    def test_mail_words_alone_reach_the_worker_route(self):
        # #173: the worker reads the owner's words and searches mail with its own tools.
        classifier = self.classifier()
        for text in self.MAIL_TEXTS:
            with self.subTest(text=text):
                decision = classifier.classify(text)
                self.assertEqual(decision.intent, 'conversation')
                self.assertIsNone(decision.argument)
                self.assertIsNone(decision.clarification)

    def test_a_judged_mail_need_reaches_the_mail_route_with_no_extracted_query(self):
        classifier = self.classifier({text: INTENT_MAIL_SEARCH for text in self.MAIL_TEXTS})
        for text in self.MAIL_TEXTS:
            with self.subTest(text=text):
                decision = classifier.classify(text)
                self.assertEqual(decision.intent, INTENT_MAIL_SEARCH)
                self.assertIsNone(decision.argument, 'Still extracts no query from the owner message')
                self.assertTrue(decision.executes)

    def test_a_request_to_send_mail_never_becomes_a_mailbox_read(self):
        classifier = self.classifier()
        for text in ('메일 보내줘', '거래처에 이메일 답장해줘',
                     'send an email to the vendor', 'reply to that mail'):
            with self.subTest(text=text):
                self.assertNotEqual(classifier.classify(text).intent, INTENT_MAIL_SEARCH)

    def test_a_mail_request_with_no_subject_is_not_answered_by_a_rule(self):
        decision = self.classifier().classify('메일 좀 찾아줘')
        self.assertEqual(decision.intent, 'conversation')
        self.assertIsNone(decision.clarification, 'the worker, not a rule, asks what to look for')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
