"""#945: owner-facing host text reads like the assistant, not like the machinery.

Exact values stay exact and complete; only their rendering and wording change.
"""
import json
from unittest import mock

from personal_agent import preparations as prep
from personal_agent.code_run import APPROVAL_EXACT_LABEL
from personal_agent.messages import catalog, msg
from personal_agent.quickstart_service import request_prompt_parts
from personal_agent.self_set_writes import value_lines

#: Messages the owner reads directly (settings cards, payment mode, sign-in, status lines).
OWNER_READ = ('payment_autonomy.risk', 'payment_autonomy.owner_only', 'settings_orchestrator.money_note',
              'settings_orchestrator.rung_note', 'settings_orchestrator.provider_sign_in_note',
              'settings_orchestrator.owner_confirm_only', 'settings_orchestrator.not_owner_typed',
              'settings_orchestrator.browser_login', 'settings_orchestrator.telegram_pending_action',
              'telegram_surface.cancelled_by_owner', 'steer.not_applied.owner',
              'information_use.browser_provider_sign_in', 'write_allowances.owner_only')


def test_the_owner_is_addressed_directly_not_as_the_owner():
    for key in OWNER_READ:
        assert '소유자' not in catalog()[key]['ko'], key


def test_owner_messages_name_no_tool_binaries():
    for key in ('remote_login.no_tunnel', 'phone_input.no_tunnel'):
        for lang, text in catalog()[key].items():
            assert 'ngrok' not in text and 'cloudflared' not in text, (key, lang)


def test_value_lines_keep_every_value_exactly_and_readably():
    values = {'title': '회의록', 'pages': 2, 'parent': {'id': 'p1'}, 'empty': ''}
    text = value_lines(values)
    assert text.splitlines() == ['empty: ""', 'pages: 2', 'parent: {"id": "p1"}', 'title: 회의록']
    assert '{"title"' not in text
    # A value cannot pass as another line of the approval: it stays quoted JSON.
    spoof = value_lines({'note': 'ok\namount: 1', 'name': ' x', 'a\nb': 1})
    assert spoof.splitlines() == ['"a\\nb": 1', 'name: " x"', 'note: "ok\\namount: 1"']


def test_the_exact_request_is_headed_once():
    label = '1주 25,900원 예약매수\n\n' + APPROVAL_EXACT_LABEL + ' POST /order {"qty":1}'
    _title, summary, _rest, _check, exact = request_prompt_parts(label)
    assert summary == '1주 25,900원 예약매수'
    assert exact == 'POST /order {"qty":1}'
    assert not exact.startswith(APPROVAL_EXACT_LABEL)


def test_the_proposal_overflow_line_is_catalogued_in_the_assistant_register():
    with mock.patch.object(prep, 'proposal_summary', lambda row, zone=None, **kwargs: '요약'):
        text = prep.proposal_text([{'id': 'a'}], 2)
    assert msg('preparations.more_proposals', remaining=2) in text
    assert '습니다' not in text
    assert set(catalog()['preparations.more_proposals']) == {'ko', 'en', 'ja', 'zh'}
    assert json.dumps(catalog()['telegram_surface.settings_final'], ensure_ascii=False).count('{part}') == 4
