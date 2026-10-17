"""#1356: AgentOS's own owner-facing words come from one id-keyed catalog."""
import json
import re
import unittest
from pathlib import Path

from personal_agent import messages, telegram_locale

SRC = Path(messages.__file__).parent
PLACEHOLDER = re.compile(r'\{([a-z_]+)\}')


class CatalogTests(unittest.TestCase):
    def test_every_id_has_every_language_and_the_same_placeholders(self):
        for message_id, entry in messages.catalog().items():
            with self.subTest(id=message_id):
                self.assertEqual(set(entry), set(messages.LANGUAGES))
                names = sorted(PLACEHOLDER.findall(entry['ko']))
                for lang in messages.LANGUAGES:
                    self.assertTrue(entry[lang].strip())
                    self.assertEqual(sorted(PLACEHOLDER.findall(entry[lang])), names, lang)
                    if lang != 'ko':
                        self.assertIsNone(re.search('[가-힣]', entry[lang]), f'{lang} still Korean')

    def test_ids_are_unique_by_korean_source(self):
        sources = [entry['ko'] for entry in messages.catalog().values()]
        self.assertEqual(len(sources), len(set(sources)), 'two ids must not share one Korean line')

    def test_msg_fills_values_and_defaults_to_korean(self):
        self.assertEqual(messages.msg('card.state', state='완료'), '이 요청은 완료 상태입니다.')
        self.assertEqual(messages.msg('card.state', 'en', state='done'), 'This request is done.')
        self.assertEqual(messages.msg('button.undo', 'ja'), '元に戻す')

    def test_migrated_lines_live_only_in_the_catalog(self):
        """A migrated sentence edited at a call site instead of the catalog would lose its translations.

        Whole sentences only: one-word state labels (완료, 진행 중) also name web statuses elsewhere.
        Docstrings may quote a line.
        """
        import ast
        migrated = {entry['ko'] for message_id, entry in messages.catalog().items() if not message_id.startswith('state.')
                    and not PLACEHOLDER.search(entry['ko']) and (' ' in entry['ko'] and len(entry['ko']) >= 6)}
        for path in sorted(SRC.glob('*.py')):
            name = path.name
            tree = ast.parse(path.read_text(encoding='utf-8'))
            docs = {ast.get_docstring(node, clean=False) for node in ast.walk(tree)
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
            literals = {node.value for node in ast.walk(tree)
                        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in docs}
            with self.subTest(module=name):
                self.assertEqual(sorted(literals & migrated), [])

    def test_the_telegram_seam_translates_catalog_lines(self):
        self.assertEqual(telegram_locale.translate(messages.msg('card.queued'), 'en'), messages.msg('card.queued', 'en'))
        self.assertEqual(telegram_locale.translate(messages.msg('step.web.query', query='coffee') + ' · · ·', 'zh'),
                         messages.msg('step.web.query', 'zh', query='coffee') + ' · · ·')


if __name__ == '__main__':
    unittest.main()
