"""#1356: Still's own owner-facing words come from one id-keyed catalog."""
import ast
import json
import re
import unittest
from pathlib import Path

from personal_agent import messages, telegram_locale

SRC = Path(messages.__file__).parent
PLACEHOLDER = re.compile(r'\{([a-z_]+)\}')


# #514 preserves existing Korean output byte for byte, including historical role names.
# Translations and all other entries still reject that framing.
PRESERVED_KOREAN_ROLE_IDS = frozenset({
    'family_share.session_store_unavailable',
    'family_share.session_save_failed',
    'family_share.session_read_failed',
    'settings_orchestrator.category_line_family',
    'settings_orchestrator.category_line_family_effective',
    'settings_orchestrator.family_label',
    'settings_orchestrator.unknown_setting',
    'settings_orchestrator.family_requested',
    'settings_orchestrator.family_name_format',
    'settings_orchestrator.family_add_note',
    'settings_orchestrator.share_format',
    'settings_orchestrator.unshare_format',
    'settings_orchestrator.share_ambiguous',
    'settings_orchestrator.share_already_enabled',
    'settings_orchestrator.share_already_disabled',
    'settings_orchestrator.invalid_family_name',
    'settings_orchestrator.family_name_is_self',
    'settings_orchestrator.family_name_exists',
    'settings_orchestrator.family_add_summary',
    'settings_orchestrator.share_summary',
    'settings_orchestrator.unshare_summary',
    'settings_orchestrator.label_family_add',
    'settings_orchestrator.label_family_share_site',
    'settings_orchestrator.label_family_unshare_site',
})

# Every retained literal is input syntax, stored identity, or guidance for the model.
MIGRATED_MODULES = {
    'settings_orchestrator.py': {
        r'(?:confirm|확인)\s+([A-Za-z0-9_-]+)',  # Matches owner input; never emitted.
        r'(?:cancel|취소)\s+([A-Za-z0-9_-]+)',  # Matches owner input; never emitted.
        '소유자에게 이 대화에서 확인을 요청합니다: 적용 버튼을 누르거나 그렇게 하라고 답하면 적용되고, 아니라고 답하면 취소됩니다(',  # Model-only next_step instruction.
        '분 안에).',  # Tail of the model-only confirmation deadline instruction.
        ' 변경은 소유자 확인을 기다립니다. ',  # Model-only next_step instruction.
        ' 확인 전에는 아무것도 바뀌지 않았습니다.',  # Model-only next_step instruction.
    },
    'telegram_surface.py': {
        '[사진 첨부]',  # Stored photo-only message sentinel, also compared when assembling albums.
    },
    'api_input.py': set(),
    'phone_input.py': set(),
    'connector_revocation.py': set(),
    'mcp_setup.py': set(),
    'skills.py': {
        '이 작업에서 쓸 수 있는 스킬 목록에 없는 스킬이에요. 스킬 없이 진행하세요.',  # Model-only unavailable-skill guidance.
        '이 작업이 시작될 때의 스킬({skill}) 버전이 바뀌었거나 꺼져 읽지 않았어요. 스킬 없이 진행하세요.',  # Model-only stale-skill guidance.
        '스킬은 방법 안내일 뿐 권한이 아니에요. 요청과 지금 관찰한 결과가 기준이에요.',  # Model-only skill_load result note.
        '머리말만 읽었어요. 요청에 맞으면 skill_load로 전체 방법을 읽고, 맞는 스킬이 없으면 method_gap에 남기세요.',  # Model-only skill_header result note.
        '기록만 했어요. 실패가 아니니 스킬 없이 요청에 계속 답하세요.',  # Model-only method_gap result note.
    },
    'trust_record.py': set(),
    'family_share.py': set(),
    'conversation_handoff.py': set(),
    'owner_forget.py': set(),
}


class CatalogTests(unittest.TestCase):
    def test_owner_messages_do_not_use_secretary_framing(self):
        for message_id, entry in messages.catalog().items():
            for lang, value in entry.items():
                with self.subTest(id=message_id, lang=lang):
                    self.assertNotRegex(value, r'(?i)secretary')
                    if lang != 'ko' or message_id not in PRESERVED_KOREAN_ROLE_IDS:
                        self.assertNotIn('비서', value)

    def test_every_id_has_every_language_and_the_same_placeholders(self):
        for message_id, entry in messages.catalog().items():
            with self.subTest(id=message_id):
                self.assertEqual(set(entry), set(messages.LANGUAGES))
                names = sorted(PLACEHOLDER.findall(entry['ko']))
                for lang in messages.LANGUAGES:
                    self.assertTrue(entry[lang].strip())
                    if lang == 'ko':
                        continue
                    # #242: a Korean-grammar placeholder ({obj}, 을/를) may be left out; every other one stays.
                    self.assertEqual(sorted(PLACEHOLDER.findall(entry[lang])),
                                     [name for name in names if name not in messages.KOREAN_ONLY_PLACEHOLDERS], lang)
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

    def test_migrated_modules_have_no_owner_visible_korean_literals(self):
        for module, allowlist in MIGRATED_MODULES.items():
            tree = ast.parse((SRC / module).read_text(encoding='utf-8'))
            docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                          and ast.get_docstring(node, clean=False) is not None}
            literals = {node.value for node in ast.walk(tree)
                        if isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in docstrings and re.search('[가-힣]', node.value)}
            with self.subTest(module=module):
                self.assertEqual(literals, allowlist)

    def test_catalog_templates_are_not_shadowed(self):
        # A broad structural template can intercept a more specific existing message.
        # Exercise the actual matcher, including all pre-existing catalog entries.
        for message_id, entry in messages.catalog().items():
            values = {name: 'VALUE_' + name.upper() for name in PLACEHOLDER.findall(entry['ko'])}
            source = messages.msg(message_id, **values)
            for lang in telegram_locale.LANGUAGES:
                with self.subTest(id=message_id, lang=lang):
                    self.assertEqual(telegram_locale.translate(source, lang),
                                     messages.msg(message_id, lang, **values))

    def test_settings_summary_and_receipt_translate_nested_labels(self):
        from personal_agent.settings_orchestrator import SettingsOrchestrator, VALUE_LABELS, SETTING_LABELS
        summary = SettingsOrchestrator._category_line('current_context', {
            'enabled': {'label': SETTING_LABELS['enabled'], 'value_label': VALUE_LABELS['on']},
            'timezone': {'label': SETTING_LABELS['timezone'], 'value_label': 'Asia/Seoul'},
        })
        self.assertEqual(summary, '현재 맥락 · 사용: 켜짐 · 시간대: Asia/Seoul')
        self.assertEqual(telegram_locale.translate(summary, 'en'),
                         'Current context · Use: On · Time zone: Asia/Seoul')
        change = messages.msg('settings_orchestrator.change_summary',
                              label=messages.msg('settings_orchestrator.label_current_context_enabled'),
                              before=VALUE_LABELS['off'], after=VALUE_LABELS['on'])
        receipt = messages.msg('settings_orchestrator.changed_receipt', effect=change)
        self.assertEqual(receipt, '현재 맥락 사용: 꺼짐 → 켜짐(으)로 바꿨습니다.')
        self.assertEqual(telegram_locale.translate(receipt, 'en'),
                         'Changed Current context · Use: Off → On.')
        self.assertEqual(telegram_locale.translate(receipt, 'ko'), receipt)
        from personal_agent.settings_orchestrator import next_action, state_label
        row = {'service': 'Gmail', 'state': 'disconnected', 'connectable': True}
        row.update(state_label=state_label(row['state']), next_action=next_action(row))
        connections = SettingsOrchestrator._summary([row])
        self.assertEqual(telegram_locale.translate(connections, 'en'),
                         'Gmail · Not connected · In Settings > External connections, connect.')

    def test_chat_surface_status_translates_nested_labels(self):
        # The status line and its label must both translate through the existing seam.
        status = messages.msg('telegram_surface.job_status', status=messages.msg('label.partly_done'))
        self.assertEqual(status, '작업 상태: 일부 완료')
        self.assertEqual(telegram_locale.translate(status, 'en'), 'Task status: Partly done')

    def test_trust_summary_translates_operations_and_money_notice(self):
        from personal_agent.trust_record import describe
        record = {'name': 'Tools', 'state': 'active', 'rung': 'approve', 'money': True,
                  'operations': {'get': 'read', 'put': 'mutate'}}
        summary = describe(record)
        self.assertEqual(summary, 'Tools: 건별 승인 후 실행 · 돈이 오가는 연결 · get(읽기), put(변경)')
        self.assertEqual(telegram_locale.translate(summary, 'en'),
                         'Tools: Run after approval for each action · Connection involving money · get(Read), put(Change)')

    def test_the_telegram_seam_translates_catalog_lines(self):
        self.assertEqual(telegram_locale.translate(messages.msg('card.queued'), 'en'), messages.msg('card.queued', 'en'))
        self.assertEqual(telegram_locale.translate(messages.msg('step.web.query', query='coffee') + ' · · ·', 'zh'),
                         messages.msg('step.web.query', 'zh', query='coffee') + ' · · ·')


if __name__ == '__main__':
    unittest.main()
