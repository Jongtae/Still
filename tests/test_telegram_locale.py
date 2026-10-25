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
        self.assertEqual(loc.translate('이 요청은 취소됨 상태입니다.', 'ja'), 'この依頼は「キャンセル済み」です。')
        self.assertEqual(loc.translate('기억했어요: Ethiopia Guji', 'zh'), '记住了：Ethiopia Guji')
        self.assertEqual(loc.translate('일부 단계만 완료했습니다.\nThe cart was updated.\n시도한 단계: search', 'en'),
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
        channel.send_message(1, '요청을 받았습니다. 곧 시작할게요.')
        language[0] = 'ko'
        channel.send_message(1, '요청을 받았습니다. 곧 시작할게요.')
        self.assertEqual(sent[0]['text'], '受け取りました。すぐに始めます。')
        self.assertEqual(sent[1]['text'], '요청을 받았습니다. 곧 시작할게요.')

    def test_a_broken_language_source_never_blocks_a_send(self):
        sent = []
        channel = TelegramChannel(lambda: (lambda url, body, headers=None, timeout=None: sent.append(body) or {'ok': True, 'result': {}}),
                                  lambda: 'T', language_source=lambda: 1 / 0)
        channel.send_message(1, '상세')
        self.assertEqual(sent[0]['text'], '상세')


if __name__ == '__main__':
    unittest.main()
