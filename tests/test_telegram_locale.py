"""#1350: fixed Telegram lines in the owner's language; AI answers untouched."""
import re
import unittest

from personal_agent import telegram_locale as loc
from personal_agent.conversation_handoff import TelegramChannel


class TableTests(unittest.TestCase):
    def test_every_entry_has_all_languages_and_matching_placeholders(self):
        from personal_agent.messages import KOREAN_ONLY_PLACEHOLDERS, catalog
        data = {entry['ko']: entry for entry in catalog().values()}
        self.assertGreater(len(data), 200)
        names = re.compile(r'\{([a-z_]+)\}')
        for source, targets in data.items():
            with self.subTest(source=source):
                expected = sorted(name for name in names.findall(source) if name not in KOREAN_ONLY_PLACEHOLDERS)
                for lang in loc.LANGUAGES:
                    self.assertTrue(targets.get(lang), f'{lang} missing')
                    self.assertEqual(expected, sorted(names.findall(targets[lang])))
                    self.assertIsNone(re.search('[가-힣]', targets[lang]), f'{lang} still Korean')


class TranslateTests(unittest.TestCase):
    def test_language_codes(self):
        self.assertIsNone(loc.language('ko'))
        self.assertIsNone(loc.language(None))
        self.assertEqual(loc.language('ja-JP'), 'ja')
        self.assertEqual(loc.language('zh-hans'), 'zh')
        self.assertEqual(loc.language('de'), 'en', 'other languages fall back to English')

    def test_fixed_lines_templates_and_draft_dots(self):
        self.assertEqual(loc.translate('요청을 처리했어요.', 'en'), 'Done.')
        self.assertEqual(loc.translate('ssg.com에서 찾아보고 있어요 · · ·', 'en'), 'Searching ssg.com · · ·')
        self.assertEqual(loc.translate('이 요청은 취소됨 상태예요.', 'ja'), 'この依頼は「キャンセル済み」です。')
        self.assertEqual(loc.translate('기억했어요: Ethiopia Guji', 'zh'), '记住了：Ethiopia Guji')
        self.assertEqual(loc.translate('일부 단계만 완료했어요.\nThe cart was updated.\n시도한 단계: search', 'en'),
                         'Only some steps were completed.\nThe cart was updated.\nSteps tried: search')

    def test_a_value_named_twice_must_be_the_same_both_times(self):
        """#242: ``{label} ... {label}{obj}`` matches only one label, and the Korean particle is dropped."""
        from personal_agent.messages import msg
        line = msg('handoff.connection_required', label='Gmail', obj='을')
        self.assertEqual(loc.translate(line, 'en'), msg('handoff.connection_required', 'en', label='Gmail'))
        mixed = line.replace('Gmail을 연결', 'Drive를 연결')
        self.assertEqual(loc.translate(mixed, 'en'), mixed, 'two different names are not one template')

    def test_ai_answers_and_korean_owners_are_untouched(self):
        answer = 'The same Ethiopia Guji, 500 g, is in your cart.'
        self.assertEqual(loc.translate(answer, 'en'), answer)
        self.assertEqual(loc.translate('요청을 처리했어요.', None), '요청을 처리했어요.')
        self.assertEqual(loc.translate('모르는 새 문구예요.', 'en'), '모르는 새 문구예요.', 'unknown lines stay as they are')

    def test_buttons_and_rich_drafts(self):
        body = {'chat_id': 1, 'text': '상세',
                'reply_markup': {'inline_keyboard': [[{'text': '되돌리기', 'callback_data': 'p7u:1:1'},
                                                      {'text': '2 되돌리기', 'callback_data': 'p7u:1:2'}]]}}
        out = loc.localize_body('sendMessage', body, 'en')
        self.assertEqual([b['text'] for b in out['reply_markup']['inline_keyboard'][0]], ['Undo', 'Undo 2'])
        self.assertEqual(out['reply_markup']['inline_keyboard'][0][0]['callback_data'], 'p7u:1:1')
        self.assertEqual(body['reply_markup']['inline_keyboard'][0][0]['text'], '되돌리기', 'the caller body is not mutated')
        rich = {'chat_id': 1, 'draft_id': 3, 'rich_message': {'blocks': [{'type': 'thinking', 'text': '웹에서 찾아보고 있어요 · · ·'}]}}
        self.assertEqual(loc.localize_body('sendRichMessageDraft', rich, 'ja')['rich_message']['blocks'][0]['text'],
                         'ウェブで探しています · · ·')
        self.assertEqual(loc.localize_body('getUpdates', {'offset': 1}, 'en'), {'offset': 1})


class ChannelTests(unittest.TestCase):
    def test_channel_translates_on_the_way_out_with_the_owner_language(self):
        sent = []

        def transport(url, body, headers=None, timeout=None):
            sent.append(body)
            return {'ok': True, 'result': {}}

        language = ['ja']
        channel = TelegramChannel(lambda: transport, lambda: 'T', language_source=lambda: language[0])
        channel.send_message(1, '요청을 받았어요. 곧 시작할게요.')
        language[0] = 'ko'
        channel.send_message(1, '요청을 받았어요. 곧 시작할게요.')
        self.assertEqual(sent[0]['text'], '受け取りました。すぐに始めます。')
        self.assertEqual(sent[1]['text'], '요청을 받았어요. 곧 시작할게요.')

    def test_a_broken_language_source_never_blocks_a_send(self):
        sent = []
        channel = TelegramChannel(lambda: (lambda url, body, headers=None, timeout=None: sent.append(body) or {'ok': True, 'result': {}}),
                                  lambda: 'T', language_source=lambda: 1 / 0)
        channel.send_message(1, '상세')
        self.assertEqual(sent[0]['text'], '상세')


class AnswerBoundaryTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from personal_agent.quickstart_store import QuickStore
        from personal_agent.quickstart_service import AgentService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.sent = []
        def transport(url, body, headers=None, timeout=None):
            self.sent.append((url.rsplit('/', 1)[-1], body))
            return {'ok': True, 'result': {'message_id': len(self.sent)}}
        self.service = AgentService(self.store, telegram_transport=transport)
        self.store.put('telegram', {'enabled': True, 'user_id': 42, 'generation': 'g', 'owner_language': 'en'})

    def enqueue(self, message):
        return self.store.enqueue(message, 'request-' + message,
                                  'telegram:g', 42, owner_typed=True)

    def test_real_delivery_preserves_ai_even_when_it_is_a_catalog_line(self):
        from unittest.mock import patch
        # An exact catalog hit and a broad template hit must both be protected.
        answer = '다시 접속했지만 보유 종목은 아직 확인하지 못했어요.\n요청을 처리했어요.\n참, 다른 문장입니다.'
        work = self.enqueue('Please check.')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial',response=?,delivery='pending' WHERE id=?", (answer, work))
        with patch.object(loc, '_match', wraps=loc._match) as match:
            self.service.deliver_one()
        body = next(body for method, body in self.sent if method == 'sendMessage')
        self.assertEqual(body['text'], answer)
        for call in match.call_args_list:
            for line in answer.splitlines():
                self.assertNotIn(line, call.args[0], 'AI words must not enter catalog matching')
        self.service.telegram.send_message(42, '요청을 처리했어요.')
        self.assertEqual(self.sent[-1][1]['text'], 'Done.', 'the same words still translate as a Still notice')

    def test_partial_answers_have_no_fallback_in_any_owner_language(self):
        from personal_agent.phone_input import owner_language
        for text, expected in [('한국어로 알려 주세요.', None), ('Please answer.', 'en'),
                               ('確認してください', 'ja'), ('请确认结果', 'zh')]:
            with self.subTest(text=text):
                work = self.enqueue(text)
                self.assertEqual(loc.owner_language(self.store, work), expected)
                self.assertEqual(owner_language(self.store), expected)
                with self.store.db() as db:
                    db.execute("UPDATE jobs SET status='partial',response='AI answer',delivery='pending' WHERE id=?", (work,))
                self.service.deliver_one()
                bodies = [body for method, body in self.sent if method == 'sendMessage']
                self.assertEqual(bodies[-1]['text'], 'AI answer')
        self.assertEqual(self.store.config('telegram')['owner_language'], 'en')

    def test_presence_prose_is_protected_in_rich_and_plain_drafts(self):
        from types import SimpleNamespace
        from personal_agent.telegram_presence import draft_frame, step_line
        work = self.enqueue('Please answer.')
        answer = '다시 접속했지만 아직 확인 중이에요.'
        state = SimpleNamespace(said=0, rich_draft_failed=False, rich_draft_retry=False)
        job = self.store.job(work)
        line = draft_frame(step_line({'status': answer}), 0)
        self.service.telegram_surface.show_draft(job, state, line)
        rich = self.sent[-1][1]['rich_message']['blocks']
        self.assertEqual(rich, [{'type': 'thinking', 'text': answer + ' · · ·'}])
        state.rich_draft_failed = True
        self.service.telegram_surface.show_draft(job, state, line)
        self.assertEqual(self.sent[-1][1]['text'], answer + ' · · ·')

    def test_provenance_survives_rendering_preview_slicing_and_plain_fallback(self):
        from personal_agent.conversation_projection import terminal_text
        from personal_agent.telegram_presence import render_telegram_html
        answer = '다시 접속했지만 확인 중이에요.'
        for projected in (terminal_text(answer, outcome='partial'),
                          terminal_text(answer * 200, outcome='succeeded')):
            for text in (projected, render_telegram_html(projected)):
                self.service.telegram.send_message(42, text)
                self.assertTrue(self.sent[-1][1]['text'].startswith(answer))

    def test_script_priority_and_fallback(self):
        for text, expected in [('漢字とかな', 'ja'), ('漢字 한글 かな', None), ('ㄱㄴ', None),
                               ('English text', 'en'), ('123?!', 'en')]:
            self.assertEqual(loc.language('en', text), expected)

    def test_plan_previews_and_interim_updates_keep_ai_prose(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        answer = '다시 접속했지만 아직 확인 중이에요.'
        work = self.enqueue('Please answer.')
        job = self.store.job(work)
        attempt = SimpleNamespace(number=1, preview={'understanding': answer, 'steps': []})
        self.assertTrue(self.service.show_plan_preview(job, Mock(), attempt))
        self.assertEqual(self.sent[-1][1]['text'], answer)
        tell = self.service.owner_updater(job)
        self.assertIsNotNone(tell)
        tell(answer)
        self.assertEqual(self.sent[-1][1]['text'], answer)

    def test_judgment_note_is_prose_while_fixed_truth_floor_is_localized(self):
        from personal_agent.conversation_projection import answer_note, terminal_text
        from personal_agent.messages import msg
        note = '다시 접속했지만 아직 확인 중이에요.'
        projected = terminal_text('Answer', outcome='partial', note=answer_note([], 'partial', report={'note': note}, lang='en'))
        self.service.telegram.send_message(42, projected)
        self.assertEqual(self.sent[-1][1]['text'], 'Answer\n\n' + note)
        fixed = answer_note([('model', 'failed')], 'partial', lang='en')
        self.assertEqual(fixed, msg('projection.some_steps_unfinished', 'en'))


if __name__ == '__main__':
    unittest.main()
