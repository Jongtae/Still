"""#242: connector handoffs, resume refusals, folder permission, family sharing, phone sign-in
and setup errors reach the owner in their language, through the paths that send them.

Each fixture produces the line the way the owner gets it: a whole ``AgentService`` turn with a
scripted model, fixture connectors and a recording Telegram transport (the harnesses of the
handoff test modules), the browser's HTTP error reply, or the module function that writes the
receipt followed by the Telegram channel that sends it.  The Telegram seam translates on the way
out, so a line the catalog does not hold stays Korean and fails here.  Korean owners see exactly
the Korean they saw before.

Evidence class: model-free unit tests.  No network, model, Telegram or Google is contacted.
"""
import io
import json
import pathlib
import re
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_contextual_capability_handoff as local_case
import test_conversation_connector_handoff as connector_case
from scripted_capability_need import capability_need_engine
from test_family_share import Opener, cookie, jar_at

from personal_agent import family_share, remote_login
from personal_agent import quickstart_service
from personal_agent.connector_contract import ConnectorRegistry, ConnectorResult, ConnectorResultKind, RecoveryAction
from personal_agent.conversation_handoff import (LOCAL_FOLDER_READ, LOCAL_REFERENCE_READ, LOCAL_RESULT_WRITE,
                                                 RESUME_REFUSALS, LOCAL_REFUSALS, SEVERAL_TASKS, ConnectorHandoff,
                                                 ConversationHandoffError, TelegramChannel, local_refusal_text)
from personal_agent.gmail import GMAIL_CONNECTOR_ID
from personal_agent.messages import msg
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore

LANGS = ('en', 'ja', 'zh')
#: ``None`` is a Korean owner (no translation); the catalog's ``ko`` line is what they read.
OWNERS = (None, *LANGS)
HANGUL = re.compile('[가-힣]')
CONNECT_URL = 'http://127.0.0.1:8787/oauth/gmail/start'


def lang_of(owner):
    return owner or 'ko'


def harness(test, case_class, owner_language):
    """A fresh fixture of ``case_class`` whose paired Telegram owner reads ``owner_language``."""
    case = case_class()
    case.setUp()
    test.addCleanup(case.doCleanups)
    if owner_language:
        config = dict(case.store.config('telegram', {}))
        config['owner_language'] = owner_language
        case.store.put('telegram', config)
    return case


def sent_through_telegram(text, owner_language):
    """``text`` as the Telegram channel sends it to an owner reading ``owner_language``."""
    bodies = []

    def transport(url, body, headers=None, timeout=None):
        bodies.append(body)
        return {'ok': True, 'result': {'message_id': 1}}

    TelegramChannel(lambda: transport, lambda: 'T', language_source=lambda: owner_language).send_message(1, text)
    return bodies[0]['text']


class ConnectorHandoffLines(unittest.TestCase):
    """The handoff that parks a request for a connection, and every refusal of its resume."""

    def park(self, owner, result=None, url='', request=connector_case.MAIL_REQUEST):
        case = harness(self, connector_case.HandoffTestCase, owner)
        patches = [mock.patch.object(case.service, 'connector_connect_url', return_value=url)]
        if result is not None:
            patches.append(mock.patch.object(case.handoff, 'prerequisite', return_value=result))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        job_id = case.park(request)
        case.service.deliver_one()
        return case, job_id, case.sent[-1]['text']

    def test_korean_owners_read_the_same_guidance_as_before(self):
        _case, _job, bubble = self.park(None)
        self.assertEqual(bubble, 'Gmail 연결이 아직 없어 이 요청을 실행하지 않았습니다. 지금 필요한 다음 단계는 하나입니다: '
                                 'Gmail을 연결해 주세요. 연결이 확인되면 방금 요청을 한 번만 이어서 처리합니다.')
        _case, _job, bubble = self.park(None, url=CONNECT_URL)
        self.assertTrue(bubble.endswith(f'처리합니다. Still이 실행 중인 PC(Mac)의 브라우저에서 {CONNECT_URL} 주소를 열어 연결해 주세요. 중요한 설정이라 휴대폰에서는 열리지 않습니다.'))

    def test_each_unmet_connection_is_said_in_the_owner_language(self):
        scopes = connector_case.GMAIL_SCOPES
        cases = (
            (None, '', 'handoff.connection_required', False),
            (None, CONNECT_URL, 'handoff.connection_required', True),
            (ConnectorResult(ConnectorResultKind.REAUTH_REQUIRED, GMAIL_CONNECTOR_ID, scopes,
                             RecoveryAction.REAUTHENTICATE), CONNECT_URL, 'handoff.reauth_required', True),
            (ConnectorResult(ConnectorResultKind.REAUTH_REQUIRED, GMAIL_CONNECTOR_ID, scopes,
                             RecoveryAction.REAUTHENTICATE), '', 'handoff.reauth_required', False),
            # BLOCKED never carries the start route, even when one exists.
            (ConnectorResult(ConnectorResultKind.BLOCKED, GMAIL_CONNECTOR_ID, scopes, RecoveryAction.CONNECT),
             CONNECT_URL, 'handoff.connection_blocked', False),
        )
        for owner in OWNERS:
            for result, url, message_id, linked in cases:
                with self.subTest(owner=owner, message=message_id, linked=linked):
                    case, job_id, bubble = self.park(owner, result, url)
                    lang = lang_of(owner)
                    guidance = msg(message_id, lang, label='Gmail', obj='을')
                    expected = msg('handoff.connect_link', lang, guidance=guidance, url=url) if linked else guidance
                    self.assertEqual(bubble, expected)
                    if owner:
                        self.assertIsNone(HANGUL.search(bubble), bubble)
                    # The stored record stays in Still's source language.
                    self.assertEqual(case.store.job(job_id)['response'],
                                     msg('handoff.connect_link', guidance=msg(message_id, label='Gmail', obj='을'),
                                         url=url) if linked else msg(message_id, label='Gmail', obj='을'))

    def test_a_translated_connector_name_is_translated_inside_the_guidance(self):
        for owner in OWNERS:
            with self.subTest(owner=owner):
                case = harness(self, connector_case.HandoffTestCase, owner)
                case.registry.transition(connector_case.OWNER, connector_case.CALENDAR_SPEC.connector_id,
                                         connector_case.ConnectorState.CONNECTED,
                                         granted_scopes=connector_case.CALENDAR_SPEC.required_scopes)
                case.park(connector_case.CALENDAR_REQUEST)
                case.service.deliver_one()
                lang = lang_of(owner)
                self.assertEqual(case.sent[-1]['text'],
                                 msg('handoff.connection_required', lang,
                                     label=msg('handoff.create_google_calendar_events', lang), obj='를'))

    def test_a_connector_this_install_lacks_is_said_in_the_owner_language(self):
        for owner in OWNERS:
            with self.subTest(owner=owner):
                case = harness(self, connector_case.HandoffTestCase, owner)
                case.service.connector_handoff = ConnectorHandoff(case.store, ConnectorRegistry(case.encrypted, ()))
                job_id = case.enqueue(connector_case.MAIL_REQUEST)
                self.assertTrue(case.service.run_one())
                self.assertEqual(case.store.job(job_id)['status'], 'failed')
                case.service.deliver_one()
                lang = lang_of(owner)
                self.assertEqual(case.sent[-1]['text'], '\n\n'.join((
                    msg('projection.i_couldn_t_complete_this_request', lang),
                    msg('handoff.connector_unavailable', lang, label='Gmail'),
                    msg('projection.see_the_run_record_and_next', lang))))

    #: Every resume refusal and the catalog line the owner reads for it.
    REFUSALS = {
        'no_pending_work': 'handoff.there_was_no_request_to_continue',
        'wrong_owner': 'handoff.resume_wrong_owner',
        'generation_changed': 'handoff.the_telegram_bot_connection_was_replaced',
        'expired_resume': 'handoff.the_request_waiting_for_a_connection',
        'superseded_resume': 'handoff.request_changed',
        'replayed_resume': 'handoff.that_request_was_already_continued_so',
        'resume_claimed': 'handoff.resume_in_progress',
        'unclaimed_resume': 'handoff.resume_unclaimed',
        'scope_mismatch': 'handoff.the_approved_access_does_not_match',
        'disconnected': 'handoff.the_connection_couldn_t_be_confirmed',
        'reauth_required': 'handoff.resume_reauth_required',
        'blocked': 'handoff.resume_blocked',
        'denied': 'handoff.the_connection_was_not_approved_so',
        'callback_failed': 'handoff.resume_callback_failed',
        'work_already_claimed': 'handoff.resume_work_claimed',
        'work_already_completed': 'handoff.resume_work_completed',
        'invalid_resume': 'handoff.resume_invalid',
    }

    def test_every_resume_refusal_reaches_the_owner_in_their_language(self):
        self.assertEqual(set(self.REFUSALS), set(RESUME_REFUSALS), 'a new refusal needs its line here')
        for owner in OWNERS:
            case = harness(self, connector_case.HandoffTestCase, owner)
            lang = lang_of(owner)
            for reason, message_id in (*self.REFUSALS.items(), ('an-unknown-reason', 'handoff.resume_default')):
                with self.subTest(owner=owner, reason=reason):
                    # The one send every refusal path uses (resume, abandon, deny, settle).
                    self.assertTrue(case.service._notify_owner(connector_case.OWNER, ConnectorHandoff.refusal_text(reason)))
                    self.assertEqual(case.sent[-1]['text'], msg(message_id, lang))

    def test_a_premature_connection_callback_is_refused_in_the_owner_language(self):
        for owner in LANGS:
            with self.subTest(owner=owner):
                case = harness(self, connector_case.HandoffTestCase, owner)
                case.park()
                with self.assertRaises(ConversationHandoffError):
                    case.service.resume_connector_work(GMAIL_CONNECTOR_ID, connector_case.OWNER,
                                                       connector_case.GMAIL_SCOPES)
                self.assertIn(msg('handoff.the_connection_couldn_t_be_confirmed', owner),
                              [body['text'] for body in case.sent])


class FolderPermissionLines(unittest.TestCase):
    """A file request that waits for a folder on the Mac, and every refusal of its resume."""

    NEEDS = {LOCAL_FOLDER_READ: ('handoff.local_need_folder_read', 'handoff.local_preview_folder_read'),
             LOCAL_REFERENCE_READ: ('handoff.local_need_reference_read', 'handoff.local_preview_reference_read'),
             LOCAL_RESULT_WRITE: ('handoff.local_need_result_write', 'handoff.local_preview_result_write')}
    LOCAL_URL = 'http://127.0.0.1:8765/#settings/files'

    def expected(self, key, lang, linked):
        need, preview = (msg(message_id, lang) for message_id in self.NEEDS[key])
        if linked:
            return msg('handoff.local_next_action_link', lang, need=need, url=self.LOCAL_URL, preview=preview)
        return msg('handoff.local_next_action', lang, need=need, preview=preview)

    def test_korean_owners_read_the_same_guidance_as_before(self):
        case = harness(self, local_case.HandoffTestCase, None)
        _job, bubbles = case.park_read()
        self.assertEqual(bubbles, [
            '이 요청에는 이 Mac의 폴더를 읽는 권한이 필요해 아직 파일을 찾지 않았습니다. 지금 필요한 다음 단계는 하나입니다: '
            'Mac에서 계속 — Mac에서 Still을 열고 설정 → 파일 · 권한에서 폴더를 선택해 주세요. '
            f'(Mac의 브라우저에서 {self.LOCAL_URL} 주소를 열어도 됩니다.) 선택한 폴더 하나를 읽기만 허용합니다. '
            '파일을 바꾸거나 지우지 않고, 이 허용만으로 파일 내용을 외부 AI에 보내지 않습니다. '
            '휴대폰이나 다른 기기에서는 Mac 폴더 권한을 줄 수 없으며, 요청은 그대로 기다립니다. '
            '허용하면 방금 요청을 한 번만 이어서 처리합니다.'])

    def test_a_file_request_parked_in_a_whole_turn_is_guided_in_the_owner_language(self):
        for owner in LANGS:
            with self.subTest(owner=owner):
                case = harness(self, local_case.HandoffTestCase, owner)
                _job, bubbles = case.park_read()
                self.assertEqual(bubbles, [self.expected(LOCAL_FOLDER_READ, owner, True)])
                self.assertIsNone(HANGUL.search(bubbles[0]), bubbles[0])

    def test_each_folder_need_with_and_without_the_mac_address(self):
        for owner in OWNERS:
            for key in self.NEEDS:
                for linked in (True, False):
                    with self.subTest(owner=owner, key=key, linked=linked):
                        case = harness(self, local_case.HandoffTestCase, owner)
                        if not linked:
                            case.service.local_server_port = None
                        job_id = case.store.enqueue('결과를 폴더에 정리해 줘', f'local-{key}-{linked}',
                                                    channel=f'telegram:{local_case.GENERATION}', chat_id=local_case.CHAT)
                        self.assertTrue(case.service.park_for_local_authority(case.store.job(job_id), key))
                        case.service.deliver_one()
                        self.assertEqual(case.sent[-1], self.expected(key, lang_of(owner), linked))

    REFUSALS = {
        'no_pending_work': 'handoff.local_no_pending_work',
        'wrong_owner': 'handoff.local_wrong_owner',
        'generation_changed': 'handoff.the_telegram_bot_connection_was_replaced',
        'expired_resume': 'handoff.the_request_waiting_for_folder_access',
        'superseded_resume': 'handoff.request_changed',
        'replayed_resume': 'handoff.that_request_was_already_continued_so',
        'resume_claimed': 'handoff.local_resume_in_progress',
        'scope_mismatch': 'handoff.local_scope_mismatch',
        'denied': 'handoff.folder_access_was_not_allowed_so',
        'work_already_completed': 'handoff.local_work_already_completed',
        'invalid_resume': 'handoff.local_invalid_resume',
    }

    def test_every_folder_refusal_reaches_the_owner_in_their_language(self):
        self.assertEqual(set(self.REFUSALS), set(LOCAL_REFUSALS), 'a new refusal needs its line here')
        for owner in OWNERS:
            case = harness(self, local_case.HandoffTestCase, owner)
            lang = lang_of(owner)
            for reason, message_id in (*self.REFUSALS.items(), ('an-unknown-reason', 'handoff.local_refusal_default')):
                with self.subTest(owner=owner, reason=reason):
                    self.assertTrue(case.service._notify_owner(local_case.OWNER, local_refusal_text(reason)))
                    self.assertEqual(case.sent[-1], msg(message_id, lang))

    def test_a_request_with_several_tasks_is_answered_in_the_owner_language(self):
        """With no AI route to hand it to, the judged several-tasks turn gets Still's own clarification."""
        text = 'schedule a meeting and save a note'
        for owner in OWNERS:
            with self.subTest(owner=owner):
                case = harness(self, local_case.HandoffTestCase, owner)
                case.service.use_decision_engine(capability_need_engine({text: SEVERAL_TASKS}))
                with mock.patch.object(case.service, 'model_loop_available', return_value=False):
                    _job, bubbles = case.ask(text)
                self.assertEqual(bubbles, [msg('handoff.mixed_tasks', lang_of(owner))])


class WorkFailureLines(unittest.TestCase):
    """A Work that fails on its photos or on the model's reply says why in the owner's language."""

    def failed_bubble(self, owner, prepare):
        case = harness(self, local_case.HandoffTestCase, owner)
        job_id = case.store.enqueue('이 사진 봐 줘', 'photo-work', channel=f'telegram:{local_case.GENERATION}',
                                    chat_id=local_case.CHAT)
        with prepare(case, job_id):
            self.assertTrue(case.service.run_one())
        self.assertEqual(case.store.job(job_id)['status'], 'failed')
        before = len(case.sent)
        case.service.deliver_one()
        (bubble,) = case.sent[before:]
        return bubble

    @staticmethod
    def failed(lang, cause):
        return '\n\n'.join((msg('projection.i_couldn_t_complete_this_request', lang), cause,
                            msg('projection.see_the_run_record_and_next', lang)))

    def test_a_telegram_photo_that_cannot_be_read(self):
        def prepare(case, job_id):
            case.store.attach_telegram_photo(job_id, 'file-1')
            return mock.patch.object(case.service.telegram, 'download_photo', return_value={'data': None})
        for owner in OWNERS:
            with self.subTest(owner=owner):
                lang = lang_of(owner)
                self.assertEqual(self.failed_bubble(owner, prepare),
                                 self.failed(lang, msg('quickstart.telegram_photo_unreadable', lang)))

    def test_an_album_over_the_size_limit(self):
        def prepare(case, job_id):
            case.store.attach_telegram_photo(job_id, 'file-1')
            return mock.patch.object(quickstart_service, 'TELEGRAM_WORK_PHOTO_BYTES_LIMIT', 0)
        for owner in OWNERS:
            with self.subTest(owner=owner):
                lang = lang_of(owner)
                self.assertEqual(self.failed_bubble(owner, prepare),
                                 self.failed(lang, msg('quickstart.album_photos_too_large', lang)))

    def test_a_model_reply_the_loop_cannot_use(self):
        """The cause line names the step (``AI 응답``) and the reason, then the suggested retry."""
        replies = {
            'agent_runtime.model_returned_no_answer': {'content': ''},
            'agent_runtime.tool_call_limit_or_format': {'content': '', 'tool_calls': {'id': 'x'}},
            'agent_runtime.tool_call_ids_invalid': {'content': '', 'tool_calls': [
                {'id': 'same', 'function': {'name': 'web_search', 'arguments': {'query': 'a'}}},
                {'id': 'same', 'function': {'name': 'web_search', 'arguments': {'query': 'b'}}}]},
        }
        for message_id, reply in replies.items():
            def prepare(case, job_id, reply=reply):
                return mock.patch.object(case.service.adapter, 'tool_turn', return_value=(reply, 'test-model'))
            for owner in OWNERS:
                with self.subTest(owner=owner, message=message_id):
                    lang = lang_of(owner)
                    cause = '\n'.join((
                        msg('projection.unfinished_step', lang, step=msg('label.ai_response', lang),
                            reason=msg(message_id, lang)),
                        msg('projection.suggested_next_step_line', lang,
                            step=msg('service.trying_again_runs_the_same_request', lang))))
                    self.assertEqual(self.failed_bubble(owner, prepare), self.failed(lang, cause))


class PhoneSignInLines(unittest.TestCase):
    """The phone sign-in link and why one could not be opened, as the owner gets them in Telegram."""

    class Session:
        def __init__(self, site):
            self.site, self.link, self.expires = site, 'https://example.ngrok.app/remote/abc', 1600.0

        @staticmethod
        def clock():
            return 1000.0

        def link_sent(self, chat_id, message_id):
            return True

    def test_the_link_message(self):
        for owner in OWNERS:
            for site in ('shop.test', ''):
                with self.subTest(owner=owner, site=site):
                    case = harness(self, local_case.HandoffTestCase, owner)
                    session = self.Session(site)
                    self.assertTrue(case.service._send_remote_login_link(session, local_case.CHAT))
                    lang = lang_of(owner)
                    self.assertEqual(case.sent[-1], msg('remote_login.link', lang, site=site or msg('label.sign_in', lang),
                                                        minutes=10, link=session.link))
                    if owner:
                        self.assertIsNone(HANGUL.search(case.sent[-1]), case.sent[-1])

    def test_a_link_that_cannot_be_opened(self):
        for owner in OWNERS:
            with self.subTest(owner=owner):
                case = harness(self, local_case.HandoffTestCase, owner)
                lang = lang_of(owner)
                with mock.patch('personal_agent.subscription_engines.find_cli', return_value=None), \
                        mock.patch('personal_agent.quick_tunnel.pin', return_value=None), \
                        self.assertRaises(remote_login.RemoteLoginError) as no_ngrok:
                    case.service.start_remote_login('window-1', 'shop.test', lambda reason: None, chat_id=local_case.CHAT)
                case.service.remote_login_popen = lambda *args, **kwargs: None
                case.service.local_server_port = None
                with self.assertRaises(remote_login.RemoteLoginError) as no_port:
                    case.service.start_remote_login('window-1', 'shop.test', lambda reason: None, chat_id=local_case.CHAT)
                # As the in-flow and Settings starts tell the owner (``_notify_owner(str(exc))``).
                for error, message_id in ((no_ngrok, 'remote_login.no_tunnel'), (no_port, 'remote_login.no_local_address')):
                    self.assertTrue(case.service._notify_owner(local_case.OWNER, str(error.exception)))
                    self.assertEqual(case.sent[-1], msg(message_id, lang))


class FamilySharingLines(unittest.TestCase):
    """The receipts ``share`` and ``unshare`` write, sent through the Telegram channel."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.store = QuickStore(self.root / 'owner')
        self.jar = jar_at(self.root / 'owner')
        self.jar.save_export({'shop.test': [cookie('.shop.test'), cookie('www.shop.test', 'pref', 'p')],
                              'news.test': [cookie('news.test')]})
        self.opener = Opener()
        self.ports = {'family-1': 8797, 'family-2': 8798}
        family_share._DELIVERED.clear()
        self.addCleanup(family_share._DELIVERED.clear)

    def locate(self, instance):
        return self.ports.get(instance), self.root / instance

    def share(self, instance='family-1', site='shop.test', opener=None):
        return family_share.share(self.store, self.jar, instance, site, locate=self.locate,
                                  opener=opener or self.opener, now=2000.0)

    def unshare(self, instance='family-1', site='shop.test', opener=None):
        return family_share.unshare(self.store, instance, site, locate=self.locate, opener=opener or self.opener, now=2500.0)

    def assertSent(self, text, message_id, **values):
        for owner in OWNERS:
            with self.subTest(owner=owner, message=message_id):
                lang = lang_of(owner)
                self.assertEqual(sent_through_telegram(text, owner), msg(message_id, lang, **values))

    def refusal(self, call):
        with self.assertRaises(ValueError) as caught:
            call()
        return str(caught.exception)

    def test_a_delivered_share(self):
        with self.assertLogs('personal_agent.family_share', level='INFO'):
            receipt = self.share()
        self.assertSent(receipt['response'], 'family_share.shared', label='family-1', site='shop.test', count=2)

    def test_a_share_the_assistant_did_not_take_yet(self):
        self.opener.down = True
        with self.assertLogs('personal_agent.family_share', level='WARNING'):
            receipt = self.share()
        self.assertSent(receipt['response'], 'family_share.share_pending', label='family-1', site='shop.test')

    def test_an_assistant_signed_in_on_its_own(self):
        def refusing(request, timeout=None):
            raise HTTPError(request.full_url, 400, 'refused', {}, io.BytesIO(json.dumps(
                {'code': family_share.RECEIVER_SIGNED_IN, 'error': family_share.RECEIVER_SIGNED_IN_TEXT}).encode()))
        with self.assertLogs('personal_agent.family_share', level='WARNING'):
            receipt = self.share(opener=refusing)
        self.assertSent(receipt['response'], 'family_share.receiver_signed_in_receipt', label='family-1', site='shop.test')

    def test_a_site_with_no_session_here(self):
        self.assertSent(self.refusal(lambda: self.share(site='other.test')), 'family_share.no_session_signed_in',
                        site='other.test', sites=', '.join(row['site'] for row in self.jar.sites()))
        self.jar.save_export({})
        self.assertSent(self.refusal(lambda: self.share(site='other.test')), 'family_share.no_session', site='other.test')

    def test_an_assistant_that_is_not_installed(self):
        self.assertSent(self.refusal(lambda: self.share(instance='family-9')), 'family_share.not_installed',
                        instance='family-9')

    def test_stopping_a_share(self):
        self.assertSent(self.unshare()['response'], 'family_share.not_shared', site='shop.test')
        with self.assertLogs('personal_agent.family_share', level='INFO'):
            self.share()
            receipt = self.unshare()
        self.assertSent(receipt['response'], 'family_share.unshared', done='family-1', site='shop.test')
        with self.assertLogs('personal_agent.family_share', level='INFO'):
            self.share()
        self.opener.down = True
        with self.assertLogs('personal_agent.family_share', level='WARNING'):
            receipt = self.unshare()
        self.assertSent(receipt['response'], 'family_share.unshare_pending', pending='family-1', site='shop.test')

    def test_stopping_a_share_one_assistant_could_not_confirm_yet(self):
        with self.assertLogs('personal_agent.family_share', level='INFO'):
            self.share('family-1')
            self.share('family-2')

        def half_down(request, timeout=None):
            if ':8798/' in request.full_url:
                raise OSError('down')
            return self.opener(request, timeout)
        with self.assertLogs('personal_agent.family_share', level='INFO'):
            receipt = self.unshare(instance=None, opener=half_down)
        self.assertEqual((receipt['instances'], receipt['pending']), (['family-1'], ['family-2']))
        self.assertEqual(receipt['response'], msg('family_share.unshared', done='family-1', site='shop.test') + ' '
                         + msg('family_share.unshare_pending', pending='family-2', site='shop.test'),
                         'Korean owners read the two sentences as before')
        self.assertSent(receipt['response'], 'family_share.unshared_and_pending', done='family-1', pending='family-2',
                        site='shop.test')

    def test_an_assistant_name_that_names_none_or_several(self):
        rows = {'family-1': {'name': 'Mom'}, 'family-2': {'name': 'Mom'}}
        self.assertSent(self.refusal(lambda: family_share.resolve_instance('mom', rows)), 'family_share.several_named',
                        value='mom', listed='Mom(family-1), Mom(family-2)')
        self.assertSent(self.refusal(lambda: family_share.resolve_instance('Dad', {'family-1': {'name': 'Mom'}})),
                        'family_share.not_found', value='Dad', listed='Mom(family-1)')
        for owner in OWNERS:
            with self.subTest(owner=owner):
                lang = lang_of(owner)
                self.assertEqual(sent_through_telegram(self.refusal(lambda: family_share.resolve_instance('Dad', {})), owner),
                                 msg('family_share.not_found', lang, value='Dad', listed=msg('label.none', lang)))

    def test_a_received_site_refuses_payment(self):
        self.store.put(family_share.SHARED_KEY, {'shop.test': {'from': 'owner', 'since': 1.0}})
        self.assertSent(family_share.payment_refusal(self.store, 'www.shop.test'), 'family_share.payment_refused')


class BrowserSetupErrors(unittest.TestCase):
    """Setup failures the browser shows, in the language its screen sends (``X-Still-Language``)."""

    def setUp(self):
        from http.server import ThreadingHTTPServer
        from cryptography.fernet import Fernet
        from personal_agent.quickstart import configured_service, make_handler
        from personal_agent.quickstart_store import QuickStore
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(str(pathlib.Path(temp.name) / 'data'))
        self.service = configured_service(self.store, {'AGENTOS_GOOGLE_OAUTH_KEY': Fernet.generate_key().decode()})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.service))
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), thread.join(), self.server.server_close()))
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.cookie = 'agentos_session=' + self.store.local_session()

    def error(self, path, body, lang):
        headers = {'Content-Type': 'application/json', 'Cookie': self.cookie}
        if lang:
            headers['X-Still-Language'] = lang
        try:
            urlopen(Request(self.url + path, data=json.dumps(body).encode(), headers=headers, method='POST'), timeout=5)
        except HTTPError as exc:
            return json.load(exc)['error']
        raise AssertionError('expected an error reply')

    def assertReplies(self, path, body, expected):
        for owner in OWNERS:
            with self.subTest(path=path, owner=owner):
                self.assertEqual(self.error(path, body, owner), expected(lang_of(owner)))

    def test_model_lists_and_the_account_connection(self):
        with mock.patch.object(quickstart_service, 'request_json', return_value={}):
            self.assertReplies('/api/openrouter/connect', {'code': 'c', 'verifier': 'v' * 43},
                               lambda lang: msg('quickstart.account_connection_not_completed', lang))
            self.assertReplies('/api/openrouter/models', {}, lambda lang: msg('quickstart.free_models_not_fetched', lang))
            self.assertReplies('/api/ollama/models', {}, lambda lang: msg('quickstart.model_list_unreadable', lang))

    def test_a_telegram_bot_that_cannot_be_checked(self):
        token = {'token': '123456:' + 'A' * 30}
        with mock.patch.object(self.service.telegram, 'get_me', return_value={'username': '!'}), \
                mock.patch.object(self.service.telegram, 'get_webhook_info', return_value={}):
            self.assertReplies('/api/telegram', token, lambda lang: msg('quickstart.telegram_bot_invalid', lang))
        with mock.patch.object(self.service.telegram, 'get_me', return_value={'username': 'still_test_bot'}), \
                mock.patch.object(self.service.telegram, 'get_webhook_info', return_value=None):
            self.assertReplies('/api/telegram', token, lambda lang: msg('quickstart.telegram_webhook_unchecked', lang))

    def test_a_main_ai_without_native_tool_calls(self):
        self.service.save_main_ai_key({'provider': 'openai', 'key': 'sk-made-up-0001'})
        with mock.patch.object(ModelAdapter, 'invoke', return_value=SimpleNamespace(model='m', content='ok')), \
                mock.patch.object(ModelAdapter, 'tool_turn', return_value=({'content': 'hello'}, 'm')):
            self.assertReplies('/api/main-ai/activate', {'route': 'openai'},
                               lambda lang: msg('main_ai.api_check_failed', lang, name='OpenAI',
                                                error=msg('quickstart.native_tool_calls_unconfirmed', lang)))


if __name__ == '__main__':
    unittest.main()
