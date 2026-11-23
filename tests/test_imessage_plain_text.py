"""#1180: iMessage shows plain text only, so Still's Markdown is rendered to readable plain text.

The renderer is CommonMark through markdown-it-py (already locked through rich, #438); these tests
pin the readable form, and that text without Markdown is sent byte for byte.
"""
import unittest

from personal_agent.imessage_surface import plain_text


class PlainTextTest(unittest.TestCase):
    def test_emphasis_markers_are_dropped(self):
        self.assertEqual(plain_text('**굵게**, *기울임*, __밑줄__, ~~취소~~ 그리고 `코드`'),
                         '굵게, 기울임, 밑줄, 취소 그리고 코드')

    def test_a_heading_is_its_own_line(self):
        self.assertEqual(plain_text('# 오늘 일정\n\n오전 회의가 있어요.'), '오늘 일정\n\n오전 회의가 있어요.')
        self.assertEqual(plain_text('## 요약\n- 하나'), '요약\n\n• 하나')

    def test_lists_keep_bullets_and_numbers(self):
        self.assertEqual(plain_text('- 사과\n- 배\n  - 국산'), '• 사과\n• 배\n  • 국산')
        self.assertEqual(plain_text('1. 첫째\n2. 둘째'), '1. 첫째\n2. 둘째')
        self.assertEqual(plain_text('3. 셋째\n4. 넷째'), '3. 셋째\n4. 넷째', 'an ordered list keeps its start')

    def test_links_show_their_address(self):
        self.assertEqual(plain_text('[예약 페이지](https://example.com/book?id=7&x=1)에서 확인하세요.'),
                         '예약 페이지 (https://example.com/book?id=7&x=1)에서 확인하세요.')
        self.assertEqual(plain_text('[https://example.com/a](https://example.com/a)'), 'https://example.com/a')
        self.assertEqual(plain_text('<https://example.com/a>'), 'https://example.com/a')

    def test_a_code_fence_is_its_contents(self):
        self.assertEqual(plain_text('명령:\n\n```bash\nls -la\necho "a*b*c"\n```'),
                         '명령:\n\nls -la\necho "a*b*c"')

    def test_a_table_is_one_line_per_row(self):
        table = '| 이름 | 가격 |\n| --- | ---: |\n| **사과** | 3,000원 |\n| 배 | 4,500원 |'
        self.assertEqual(plain_text(table), '이름 · 가격\n사과 · 3,000원\n배 · 4,500원')

    def test_a_mixed_answer_leaves_no_markdown_syntax(self):
        answer = ('## 추천\n\n**순두부찌개**가 좋아요.\n\n1. [가게](https://example.com/s) 예약\n2. *7시* 출발\n\n'
                  '| 메뉴 | 가격 |\n|---|---|\n| 순두부 | 9,000원 |\n\n```\nmenu\n```')
        rendered = plain_text(answer)
        self.assertEqual(rendered, '추천\n\n순두부찌개가 좋아요.\n\n1. 가게 (https://example.com/s) 예약\n2. 7시 출발\n\n'
                                   '메뉴 · 가격\n순두부 · 9,000원\n\nmenu')
        for syntax in ('**', '##', '](', '|', '```', '---'):
            self.assertNotIn(syntax, rendered)

    def test_plain_text_and_urls_are_sent_byte_for_byte(self):
        for text in (
            '가격은 3,000원이에요. https://example.com/a_b?q=1&x=2 를 보세요.\n다음 줄도 그대로예요.',
            '2*3=6이고 snake_case_name 그대로, 이메일 owner@example.com',
            'Still이 연결되었어요. 이제 여기서 Still과 대화할 수 있어요.',
            '첫 문단\n\n둘째 문단',
            '',
        ):
            with self.subTest(text=text):
                self.assertEqual(plain_text(text), text)


    def test_a_bare_url_is_never_read_as_markdown(self):
        # A URL is taken whole before emphasis is parsed, in plain and in marked-up answers alike.
        for url in ('https://x.com/_name_', 'https://example.com/a*b*c', 'https://example.com/%EC%95%88?q=a%20b',
                    'https://ko.wikipedia.org/wiki/서울_(도시)', 'https://example.com/a_b_c#frag_1_'):
            with self.subTest(url=url):
                self.assertEqual(plain_text(f'see {url} now'), f'see {url} now')
                self.assertEqual(plain_text(f'**a** {url}'), f'a {url}')
                self.assertEqual(plain_text(f'- {url}'), f'• {url}')

    def test_sentence_punctuation_after_a_bare_url_stays_outside_it(self):
        self.assertEqual(plain_text('**여기** https://example.com/a_b_.'), '여기 https://example.com/a_b_.')
        self.assertEqual(plain_text('*링크*(https://example.com/_x_)'), '링크(https://example.com/_x_)')


if __name__ == '__main__':
    unittest.main()
