"""Natural intent classification for the one personal conversation.

These tests cover WU2 of PA1-CONV-01 as RULES-OUT-01 (#173) revised it:
ordinary Korean and English utterances are no longer read by keyword rules;
they reach the worker (the owner's chosen AI) on the ordinary conversation
route with the owner's words verbatim, supported slash commands keep working,
a judged several-tasks turn never triggers a consequential effect, and a
model's opinion cannot by itself authorise anything.
"""
import itertools
import tempfile
import unittest

from personal_agent.capabilities import CapabilityRegistry
from personal_agent.conversation_handoff import (AUTHORITY_DEFAULT, AUTHORITY_OWNER, AUTHORITY_RULE,
                                                 CONSEQUENTIAL_INTENTS, INTENT_AMBIGUOUS,
                                                 INTENT_CALENDAR_CREATE,
                                                 INTENT_CONVERSATION, INTENT_DRIVE_READ, INTENT_GREETING,
                                                 INTENT_KNOWLEDGE, INTENT_NOTE_CREATE,
                                                 INTENT_NOTE_LIST,
                                                 INTENT_RESEARCH, INTENT_SETTINGS,
                                                 INTENT_WORKSPACE_SEARCH, SEVERAL_TASKS,
                                                 JUDGMENT_UNAVAILABLE, ConversationFocus,
                                                 ConversationJudgments, IntentClassifier)
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from scripted_capability_need import capability_need_engine


def classifier():
    """The classifier exactly as the service builds it."""
    return IntentClassifier()


#: Paraphrases the retired keyword rules once read as personal-space,
#: note, saved-result or settings requests (#173).  They are owner messages
#: like any other now: the ordinary conversation route, where the worker reads
#: them verbatim and chooses its own tools.
WORKER_PARAPHRASES = (
    '개인 공간에서 오로라 관련 자료 찾아줘', '내 지식에서 예산 검토 알려줘',
    'what do i know about aurora?', 'search my knowledge base for the budget review',
    '금요일 3시 출시 검토 메모해줘', '오로라 예산 확정 적어둬',
    'take a note: aurora budget approved', 'make a note of the vendor call tomorrow',
    '작업공간에 저장한 파일 열어줘', '내 워크스페이스에서 회의 브리프 보여줘',
    'open the saved file about aurora', 'pull up the saved result about aurora',
    '연결 상태 알려줘', '드라이브 연결 해제해줘', "what's connected?", 'pause the calendar connection',
    '메일에서 예산 관련 내용 찾아줘', 'check my inbox for the invoice',
    # The four #173 examples.
    '아까 결과 다시 해줘', '내 기록에서 지난달 지출 합계 알려줘',
    '회의 내용 정리해서 노트로 저장해줘', 'Gmail 연결 상태 보여주고 끊어줘',
)

#: Natural-language strings once parsed as owner "commands" (#173); the slash forms stay.
RETIRED_NATURAL_COMMANDS = ('무엇이 연결되어 있어?', '무엇을 바꿀 수 있어?', '메모 목록',
                            '메모: 오로라 예산', '기록: 오로라 예산')

#: Public web research has no routing rule (#672): these stay on the ordinary
#: conversation route, where the Work model loop chooses its search tools.
RESEARCH_PARAPHRASES = ('웹에서 최신 환율 찾아줘', '인터넷에서 조사해줘',
                        'look up the latest exchange rate', 'search the web for vendor reviews')

#: Reached from ordinary prose only through the DecisionEngine's
#: ``capability-need`` judgment (#672), scripted below for exactly these.
#: What it reaches is a draft with an exact preview; the effect waits for the
#: owner's explicit approval of that preview.
CALENDAR_PARAPHRASES = ('내일 오후 3시에 팀 회의 일정 잡아줘', '금요일 약속 하나 등록해줘',
                        'schedule a meeting with the vendor tomorrow',
                        'add a calendar event for friday')

#: Natural memory phrasing is judged by the DecisionEngine's
#: ``explicit_memory_request`` (#597), which may let Still issue an approval
#: and leaves the utterance on the conversation route.  It is a flag, never a
#: capability selection.
MEMORY_PARAPHRASES = ('이 선호를 기억해줘', '다음 내용을 저장해',
                      'remember this preference', 'save this for later')

#: A turn the scripted judgment reads as several tasks (#672 review).
SEVERAL = '일정 잡고 메모도 남겨줘'


def several_tasks_classifier():
    return IntentClassifier(judge=ConversationJudgments(capability_need_engine({SEVERAL: SEVERAL_TASKS})))


class ParaphraseTests(unittest.TestCase):
    """No keyword rule reads an owner message for its intent (#173)."""

    def setUp(self):
        self.classifier = classifier()

    def test_every_former_rule_paraphrase_reaches_the_worker_route_verbatim(self):
        for text in (*WORKER_PARAPHRASES, *RETIRED_NATURAL_COMMANDS):
            with self.subTest(text=text):
                decision = self.classifier.classify(text)
                self.assertEqual(decision.intent, INTENT_CONVERSATION)
                self.assertEqual(decision.authority, AUTHORITY_DEFAULT)
                self.assertIsNone(decision.argument, 'no rule extracts a query or a note from the message')
                self.assertEqual(decision.cues, ())
                self.assertIsNone(decision.clarification, 'no rule answers the owner')
                self.assertTrue(decision.executes)

    def test_memory_phrasing_stays_on_the_conversation_route_as_an_approval_flag(self):
        for text in MEMORY_PARAPHRASES:
            with self.subTest(text=text):
                self.assertIn(self.classifier.classify(text).intent,
                              (INTENT_CONVERSATION, INTENT_RESEARCH))

    def test_no_regex_decides_a_memory_request_any_more(self):
        # #597: with no DecisionEngine answer nothing is judged a memory
        # request, however literal the phrasing.  #173 slice B: no lexical
        # prefilter keeps such turns away from the follow-up judgment either.
        judge = ConversationJudgments()
        for text in (*MEMORY_PARAPHRASES, '기억하지 마세요', 'do not save this'):
            with self.subTest(text=text):
                self.assertEqual(judge.explicit_memory_request(text).outcome, JUDGMENT_UNAVAILABLE)
        self.assertFalse(hasattr(AgentService, 'memory_followup_prefilter'))
        self.assertFalse(hasattr(AgentService, 'explicit_memory_request'))

    def test_a_judged_mail_need_carries_no_query_taken_from_the_message(self):
        # #173: the worker searches mail with its own tools; Still extracts no query.
        text = '집주인한테 답장 왔는지 메일 확인해줘'
        judged = IntentClassifier(judge=ConversationJudgments(capability_need_engine({text: 'mail-search'})))
        decision = judged.classify(text)
        self.assertEqual(decision.intent, 'mail-search')
        self.assertEqual(decision.cues, ('judgment:capability-need',))
        self.assertIsNone(decision.argument)
        self.assertIsNone(decision.clarification)
        self.assertTrue(decision.executes)


class ExplicitFormTests(unittest.TestCase):
    """Supported slash commands remain authoritative; retired ones fall through."""

    def setUp(self):
        self.classifier = classifier()

    def test_every_slash_form_keeps_its_route_and_its_argument(self):
        cases = (
            ('/start', INTENT_GREETING, None),
            ('/help', INTENT_GREETING, None),
            ('/knowledge aurora', INTENT_KNOWLEDGE, 'aurora'),
            ('/settings Drive pause', INTENT_SETTINGS, 'Drive pause'),
            ('/settings', INTENT_SETTINGS, '/settings'),
            ('/note 회의: 금요일 출시 검토', INTENT_NOTE_CREATE, '회의: 금요일 출시 검토'),
            ('/notes', INTENT_NOTE_LIST, None),
            ('/workspace-search aurora', INTENT_WORKSPACE_SEARCH, 'aurora'),
        )
        for text, intent, argument in cases:
            with self.subTest(text=text):
                decision = self.classifier.classify(text)
                self.assertEqual(decision.intent, intent)
                self.assertEqual(decision.authority, AUTHORITY_OWNER)
                self.assertEqual(decision.argument, argument)

    def test_natural_language_strings_are_no_longer_owner_commands(self):
        for text in RETIRED_NATURAL_COMMANDS:
            with self.subTest(text=text):
                self.assertIsNone(self.classifier.explicit(text))
                self.assertFalse(self.classifier.has_local_candidate(text))

    def test_an_unrecognised_slash_command_falls_through_instead_of_guessing(self):
        for text in ('/summarize', '/workspace-summary Aurora :: Launch notes',
                     '/search Still personal assistant verification', '/assistant Drive 검색: 예산',
                     '/recommend specialist-research'):
            with self.subTest(text=text):
                decision = self.classifier.classify(text)
                self.assertIn(decision.intent, (INTENT_CONVERSATION, INTENT_RESEARCH))
                self.assertNotIn(decision.intent, CONSEQUENTIAL_INTENTS)


class SafeFallbackTests(unittest.TestCase):
    """An unrecognised utterance answers in conversation, it never guesses."""

    def setUp(self):
        self.classifier = classifier()

    def test_ordinary_prose_stays_on_the_conversation_route(self):
        for text in ('hello', '일반적인 다음 질문입니다.', 'show my notes', '성남시 날씨를 찾아줘',
                     '“Aurora” 자료를 요약해 “Launch notes”로 저장해줘'):
            with self.subTest(text=text):
                decision = self.classifier.classify(text)
                self.assertEqual(decision.intent, INTENT_CONVERSATION)
                self.assertEqual(decision.authority, AUTHORITY_DEFAULT)

    def test_external_peer_prose_stays_non_consequential(self):
        for text in ('이 작업을 외부 에이전트에게 맡겨줘', '다른 agent에게 위임해줘',
                     'delegate this task to the external agent',
                     'hand this off to the research peer'):
            with self.subTest(text=text):
                decision = self.classifier.classify(text)
                self.assertNotIn(decision.intent, CONSEQUENTIAL_INTENTS)

    def test_drive_prose_stays_on_a_safe_non_consequential_route(self):
        for text in ('구글 드라이브 파일을 요약해줘', '드라이브에서 자료 읽어줘'):
            with self.subTest(text=text):
                decision = self.classifier.classify(text)
                self.assertNotIn(decision.intent, CONSEQUENTIAL_INTENTS)


class ConsequentialEffectTests(unittest.TestCase):
    """Calendar creation is an effect; research and file reads are not."""

    def setUp(self):
        self.classifier = classifier()

    def test_a_recognised_calendar_request_is_consequential_and_carries_the_utterance(self):
        judged = IntentClassifier(judge=ConversationJudgments(
            capability_need_engine({text: INTENT_CALENDAR_CREATE for text in CALENDAR_PARAPHRASES})))
        for text in CALENDAR_PARAPHRASES:
            with self.subTest(text=text):
                decision = judged.classify(text)
                self.assertEqual(decision.intent, INTENT_CALENDAR_CREATE)
                self.assertEqual(decision.cues, ('judgment:capability-need',))
                self.assertTrue(decision.consequential)
                self.assertEqual(decision.authority, AUTHORITY_RULE)
                # It may proceed - as far as a draft.  The utterance is the
                # argument the draft rules read; nothing is extracted here.
                self.assertTrue(decision.executes)
                self.assertEqual(decision.argument, text)

    def test_a_pending_draft_no_longer_claims_cue_free_follow_ups_in_the_classifier(self):
        # #22: the service answers a pending draft's literal approve or cancel
        # before classification (``CalendarConversation.claims``); the
        # classifier never turns cue-free words into a calendar continuation.
        pending = {'intent': INTENT_CALENDAR_CREATE, 'calendar_pending': True}
        for text in ('치과', '오후 4시', '아니 4시로'):
            with self.subTest(text=text):
                decision = self.classifier.classify(text, focus=pending)
                self.assertNotEqual(decision.intent, INTENT_CALENDAR_CREATE)
        stale = {'intent': INTENT_CALENDAR_CREATE}
        self.assertEqual(self.classifier.classify('치과', focus=stale).intent, INTENT_CONVERSATION)
        # An explicit form still wins.
        self.assertEqual(self.classifier.classify('/notes', focus=pending).intent, INTENT_NOTE_LIST)

    def test_former_rule_paraphrases_are_never_consequential(self):
        for text in WORKER_PARAPHRASES:
            with self.subTest(text=text):
                decision = self.classifier.classify(text)
                self.assertNotIn(decision.intent, CONSEQUENTIAL_INTENTS)
                self.assertTrue(decision.executes)

    def test_a_judged_several_tasks_turn_resolves_to_a_clarification_and_no_action(self):
        decision = several_tasks_classifier().classify(SEVERAL)
        self.assertEqual(decision.intent, INTENT_AMBIGUOUS)
        self.assertFalse(decision.executes)
        self.assertIn('아무 작업도 실행하지 않았습니다', decision.clarification)


class ModelAuthorityTests(unittest.TestCase):
    """A model suggestion is input, never authority."""

    def setUp(self):
        self.classifier = several_tasks_classifier()

    def test_a_suggestion_cannot_introduce_an_intent(self):
        decision = self.classifier.classify('오늘 뭐 하면 좋을까?',
                                            model_suggestion={'intent': INTENT_KNOWLEDGE,
                                                              'confidence': 0.99})
        self.assertEqual(decision.intent, INTENT_CONVERSATION)
        self.assertEqual(decision.model_suggestion['state'], 'rejected')
        self.assertEqual(decision.model_suggestion['reason'], 'no-ambiguity-to-resolve')

    def test_a_suggestion_cannot_override_a_judged_decision(self):
        judged = IntentClassifier(judge=ConversationJudgments(
            capability_need_engine({CALENDAR_PARAPHRASES[0]: INTENT_CALENDAR_CREATE})))
        decision = judged.classify(CALENDAR_PARAPHRASES[0], model_suggestion={'intent': INTENT_KNOWLEDGE})
        self.assertEqual(decision.intent, INTENT_CALENDAR_CREATE)
        self.assertEqual(decision.model_suggestion['state'], 'rejected')

    def test_a_suggestion_cannot_override_an_owner_explicit_form(self):
        decision = self.classifier.classify('/notes',
                                            model_suggestion={'intent': INTENT_KNOWLEDGE})
        self.assertEqual(decision.intent, INTENT_NOTE_LIST)
        self.assertEqual(decision.authority, AUTHORITY_OWNER)
        self.assertEqual(decision.model_suggestion['state'], 'rejected')

    def test_no_suggestion_narrows_a_judged_several_tasks_turn(self):
        # #173: with no keyword rule there are no deterministic candidates, so
        # nothing - consequential or not, well-formed or not - is accepted.
        for suggestion in ({'intent': INTENT_CALENDAR_CREATE}, {'intent': INTENT_NOTE_CREATE},
                           {'intent': INTENT_SETTINGS}, {}, {'intent': None}, {'intent': 'rm -rf'},
                           'personal-knowledge', 42):
            with self.subTest(suggestion=suggestion):
                decision = self.classifier.classify(SEVERAL, model_suggestion=suggestion)
                self.assertEqual(decision.intent, INTENT_AMBIGUOUS)
                self.assertFalse(decision.executes)
                self.assertEqual(decision.model_suggestion['state'], 'rejected')
                self.assertEqual(decision.model_suggestion['reason'], 'not-a-deterministic-candidate')

    def test_no_decision_can_ever_carry_model_authority(self):
        texts = ['/notes', 'hello', SEVERAL, *CALENDAR_PARAPHRASES, *WORKER_PARAPHRASES]
        for text in texts:
            for suggestion in (None, {'intent': INTENT_CALENDAR_CREATE}, {'intent': INTENT_KNOWLEDGE}):
                with self.subTest(text=text, suggestion=suggestion):
                    decision = self.classifier.classify(text, model_suggestion=suggestion)
                    self.assertIn(decision.authority,
                                  (AUTHORITY_OWNER, AUTHORITY_RULE, AUTHORITY_DEFAULT))
                    self.assertNotEqual((decision.model_suggestion or {}).get('state'), 'accepted')


class CorrectionAndTopicChangeTests(unittest.TestCase):
    """The route says only whether it changed; how a message relates to earlier Work is judged.

    RULES-OUT-01 (#173 slice B): no correction or continuation word list reads
    the message here.  ``supersedes_previous`` is structural (the route differs
    from the focused one); a correction, retry, reference or cancel is the
    ``followup_relation`` judgment's (``AgentService.continuity_relation``).
    """

    def setUp(self):
        self.classifier = classifier()

    def test_a_changed_route_supersedes_the_previous_focus(self):
        first = self.classifier.classify('/workspace-search aurora')
        self.assertEqual(first.intent, INTENT_WORKSPACE_SEARCH)
        focus = {'intent': first.intent}
        second = self.classifier.classify('/settings', focus=focus)
        self.assertEqual(second.intent, INTENT_SETTINGS)
        self.assertTrue(second.supersedes_previous)
        third = self.classifier.classify('연결 상태 알려줘', focus=focus)
        self.assertEqual(third.intent, INTENT_CONVERSATION)
        self.assertTrue(third.supersedes_previous)

    def test_cue_words_no_longer_change_the_route_decision(self):
        # "아니"/"말고"/"계속" decided correction and continuation before #173 slice B.
        focus = {'intent': INTENT_CONVERSATION}
        for text in ('아니, 그거 말고 오로라 예산 확정 적어둬', '아니, 계속하지 말고 다른 얘기 하자',
                     'continue', 'keep going', '계속해줘', '이어서 말해줘', '아니 4시로'):
            with self.subTest(text=text):
                decision = self.classifier.classify(text, focus=focus)
                self.assertEqual(decision.intent, INTENT_CONVERSATION)
                self.assertEqual((decision.authority, decision.cues), (AUTHORITY_DEFAULT, ()))
                self.assertFalse(decision.supersedes_previous)
                self.assertFalse(hasattr(decision, 'continuation'))

    def test_keep_going_never_replays_an_intent_that_needs_its_own_content(self):
        # "keep going" must not re-run a note write or a search with whatever
        # the previous turn happened to contain.
        for previous in (INTENT_NOTE_CREATE, INTENT_WORKSPACE_SEARCH, INTENT_KNOWLEDGE,
                         INTENT_CALENDAR_CREATE, INTENT_SETTINGS):
            with self.subTest(previous=previous):
                decision = self.classifier.classify('계속해줘', focus={'intent': previous})
                self.assertEqual(decision.intent, INTENT_CONVERSATION)
                self.assertTrue(decision.supersedes_previous)

    def test_the_focus_record_holds_no_utterance_content(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = QuickStore(temp.name)
        focus = ConversationFocus(store)
        decision = self.classifier.classify('/knowledge 오로라')
        focus.record(decision)
        saved = focus.current()
        self.assertEqual(set(saved), {'intent', 'at'})
        self.assertEqual(saved['intent'], INTENT_KNOWLEDGE)
        self.assertNotIn('오로라', repr(saved))

    def test_an_ambiguous_turn_does_not_overwrite_the_focus(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = QuickStore(temp.name)
        focus = ConversationFocus(store)
        focus.record(self.classifier.classify('/knowledge aurora'))
        focus.record(several_tasks_classifier().classify(SEVERAL))
        self.assertEqual(focus.current()['intent'], INTENT_KNOWLEDGE)


class ServiceRoutingTests(unittest.TestCase):
    """The queued conversation worker honours the classifier's decision."""

    MODEL_ROUTE_ERROR = '설정에서 모델 또는 구독 엔진을 먼저 연결하세요.'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.keys = itertools.count()

        def transport(url, body, headers=None, timeout=60):
            raise AssertionError('no model or Telegram call is expected here: ' + url)

        self.service = AgentService(self.store, ModelAdapter(transport), transport)
        self.registry = CapabilityRegistry(self.store)

    def run_one(self, message, channel='web', chat_id=None):
        job = self.store.enqueue(message, f'intent-{next(self.keys)}', channel=channel, chat_id=chat_id)
        self.assertTrue(self.service.run_one())
        return self.store.job(job)

    def assertConversationRoute(self, job):
        """No model is configured, so the conversation route fails distinctly."""
        self.assertEqual(job['status'], 'failed')
        self.assertIn(self.MODEL_ROUTE_ERROR, job['error'])

    # -- supported slash commands still work --------------------------------
    def test_slash_commands_still_route_after_the_prefix_chain_is_replaced(self):
        self.assertIn("Hi, I'm Still", self.run_one('/start')['response'])
        self.assertIn('메모를 저장했습니다', self.run_one('/note 금요일 출시 검토')['response'])
        self.assertIn('금요일 출시 검토', self.run_one('/notes')['response'])
        self.assertEqual(self.run_one('/workspace-search aurora')['response'],
                         '현재 원본과 일치하는 저장 결과를 찾지 못했습니다.')

    def test_the_explicit_settings_command_reads_and_drafts_no_retired_change(self):
        # #173: the command's free text is never re-read for a phrase; it reads.
        self.registry.transition('google-drive-read', 'enabled', ('read',))
        before = self.store.config('capability_registry')
        self.assertIn('Telegram · 연결 안 됨', self.run_one('/settings Drive pause')['response'])
        self.assertIn('Telegram · 연결 안 됨', self.run_one('/settings')['response'])
        self.assertEqual(self.store.config('capability_registry'), before)
        self.assertEqual(self.store.config('settings_change_drafts', {}), {})

    # -- the former rule paraphrases reach the worker ------------------------
    def test_former_rule_paraphrases_reach_the_worker_and_no_rule_answers(self):
        # No AI route is configured here, so the worker route fails with its own
        # setup reason: no rule ran a search, wrote a note or read the settings.
        self.registry.transition('google-drive-read', 'enabled', ('read',))
        before = self.store.config('capability_registry')
        for text in (*WORKER_PARAPHRASES, *RETIRED_NATURAL_COMMANDS):
            with self.subTest(text=text):
                self.assertConversationRoute(self.run_one(text, channel='telegram:fixture', chat_id=7))
        self.assertEqual(self.store.config('personal_knowledge_audit', []), [])
        self.assertEqual(self.store.notes(), [])
        self.assertEqual(self.store.config('settings_change_drafts', {}), {})
        self.assertEqual(self.store.config('capability_registry'), before)

    def test_research_paraphrases_stay_on_the_conversation_route(self):
        for text in RESEARCH_PARAPHRASES:
            with self.subTest(text=text):
                self.assertConversationRoute(self.run_one(text))

    # -- safety -------------------------------------------------------------
    def test_a_judged_several_tasks_turn_triggers_no_consequential_action(self):
        self.service.use_decision_engine(capability_need_engine({SEVERAL: SEVERAL_TASKS}))
        job = self.run_one(SEVERAL)
        self.assertEqual(job['status'], 'succeeded')
        self.assertIn('아무 작업도 실행하지 않았습니다', job['response'])
        self.assertEqual(self.store.config('personal_assistant_evidence', []), [])
        self.assertEqual(self.store.config('settings_change_drafts', {}), {})

    def test_a_natural_calendar_request_without_a_connector_drafts_and_creates_nothing(self):
        # This service has no Calendar connector and no AI route.  #22: the
        # request is the worker's, so the owner is told to connect an AI; Still
        # neither asks about the event nor drafts anything itself.
        self.service.use_decision_engine(capability_need_engine(
            {text: INTENT_CALENDAR_CREATE for text in CALENDAR_PARAPHRASES}))
        for text in CALENDAR_PARAPHRASES:
            with self.subTest(text=text):
                job = self.run_one(text)
                self.assertEqual(job['status'], 'failed')
                self.assertIn('설정에서 모델 또는 구독 엔진을 먼저 연결하세요', job['error'])
        self.assertEqual(self.store.config('personal_assistant_evidence', []), [])

    def test_natural_delegation_never_reaches_the_orchestrator(self):
        for text in ('이 작업을 외부 에이전트에게 맡겨줘', 'delegate this task to the external agent'):
            with self.subTest(text=text):
                self.assertConversationRoute(self.run_one(text))
        self.assertEqual(self.store.config('personal_assistant_evidence', []), [])

    def test_drive_prose_keeps_reaching_the_shipped_picker_path(self):
        # The Drive preflight answers, exactly as it did before this unit,
        # once the DecisionEngine judges the turn a Drive read (#672).  The
        # orchestrator's unwired Drive seam - the WU4 subject - is not reached.
        self.service.use_decision_engine(capability_need_engine({'구글 드라이브 파일을 요약해줘': INTENT_DRIVE_READ}))
        job = self.run_one('구글 드라이브 파일을 요약해줘')
        self.assertEqual(job['status'], 'failed')
        self.assertIn('Google Drive capability is not configured locally', job['error'])
        self.assertEqual(self.store.config('personal_assistant_evidence', []), [])

    def test_the_worker_never_supplies_a_model_suggestion_to_the_classifier(self):
        seen = []
        original = self.service.intent_classifier.classify

        def recording(text, model_suggestion=None, focus=None):
            seen.append(model_suggestion)
            return original(text, model_suggestion=model_suggestion, focus=focus)

        self.service.intent_classifier.classify = recording
        self.run_one('what do i know about aurora?', channel='telegram:fixture', chat_id=7)
        self.assertEqual(seen, [None])

    def test_topic_change_is_visible_to_the_worker_across_two_turns(self):
        self.run_one('/workspace-search aurora')
        self.assertEqual(self.service.conversation_focus.current()['intent'],
                         INTENT_WORKSPACE_SEARCH)
        decision = self.service.classify_intent('연결 상태 알려줘')
        self.assertEqual(decision.intent, INTENT_CONVERSATION)
        self.assertTrue(decision.supersedes_previous)



class RecommendationRoutingTests(unittest.TestCase):
    """Retired mock capability recommendations never claim owner-usable availability."""

    TURN_35 = '10월에 제주 2박 3일 여행 가려는데 숙소랑 일정 추천해줘'
    INTERNAL_TAGS = ('private-document-research', 'specialist-research', 'local-specialist-processing')
    LEGACY_CAPABILITY_ASKS = (
        '전문가 조사에 연결할 만한 걸 알려줘',
        '내 문서 조사에 뭘 연결하면 좋을까',
        'which capability fits document research?',
        'what should i connect for specialist research?',
        '전문가 조사 추천해줘',
        'capability 하나 추천해줘',
        '/recommend specialist-research',
    )

    MODEL_ROUTE_ERROR = ServiceRoutingTests.MODEL_ROUTE_ERROR
    run_one = ServiceRoutingTests.run_one
    assertConversationRoute = ServiceRoutingTests.assertConversationRoute

    def setUp(self):
        ServiceRoutingTests.setUp(self)
        self.classifier = classifier()

    def assertNoInternalTag(self, text):
        for tag in self.INTERNAL_TAGS:
            self.assertNotIn(tag, text or '')

    def test_travel_and_product_recommendations_stay_on_the_conversation_route(self):
        for text in (self.TURN_35, '제주 맛집 추천해줘', '노이즈캔슬링 헤드폰 추천해줘',
                     '블루투스 연결 잘 되는 헤드폰 추천해줘', '세무 전문가 추천해줘',
                     'recommend a good hotel in jeju', 'any restaurant recommendation near gangnam?',
                     '공항 연결 추천해줘', '제주 공항에서 숙소까지 연결 추천해줘', '블루투스 연결 하나 추천해줘',
                     'hdmi 커넥터 추천해줘', '세무 전문가 연결 하나 추천해줘', '전문가 연결 추천해줘',
                     'recommend a usb-c connector cable', 'recommend a good integration test framework'):
            with self.subTest(text=text):
                decision = self.classifier.classify(text)
                self.assertEqual(decision.intent, INTENT_CONVERSATION)
                self.assertIsNone(decision.clarification)

    def test_retired_capability_catalogue_asks_use_only_the_ordinary_model_route(self):
        for text in self.LEGACY_CAPABILITY_ASKS:
            with self.subTest(text=text):
                decision = self.classifier.classify(text)
                self.assertIn(decision.intent, (INTENT_CONVERSATION, INTENT_RESEARCH))
                self.assertIsNone(decision.clarification)
                self.assertNotIn(decision.intent, CONSEQUENTIAL_INTENTS)
                job = self.run_one(text, channel='telegram:fixture', chat_id=7)
                self.assertConversationRoute(job)
                self.assertNoInternalTag(job.get('response'))
                self.assertNoInternalTag(job.get('error'))
        self.assertIsNone(self.store.config('capability_recommendation_audit'))

    def test_turn_35_reaches_the_same_route_as_the_itinerary_request(self):
        for text in (self.TURN_35, '제주 2박 3일 여행 일정 짜줘'):
            with self.subTest(text=text):
                job = self.run_one(text, channel='telegram:fixture', chat_id=7)
                self.assertConversationRoute(job)
                self.assertNoInternalTag(job.get('response'))
                self.assertNoInternalTag(job.get('error'))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
