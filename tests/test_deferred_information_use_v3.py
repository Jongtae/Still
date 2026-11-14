"""#820 final verifier: recorded labels use display/deadline bounds only."""
from unittest import mock

import pytest
from personal_agent import agent_runtime as runtime, information_use as audit
from test_deferred_information_use import store, memory, call, view, MEMBER, PHONE
from test_deferred_information_use_v2 import legacy


def test_eight_recorded_calls_are_not_capped_as_comparisons(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    for i in range(8): legacy(store, job, destination='host-' + str(i))
    with mock.patch.object(runtime, 'held_value_matches', side_effect=AssertionError('legacy recomputed')):
        result = view(store, job)
    assert len(result['held_value_matches']) == 8
    assert 'calls' not in {row['kind'] for row in result.get('held_value_comparison_cut', [])}
    assert 'held_value_comparison_time' not in result


def test_single_legacy_record_uses_display_bound_not_matcher_bound(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    call(store, job, evidence={'outbound_comparison': None, 'outbound_matches': [
        {'source': 'memory', 'key': 'member', 'destination': 'host-' + str(i)}
        for i in range(audit.MAX_ITEMS + 1)]})
    result = view(store, job)
    assert len(result['held_value_matches']) == audit.MAX_ITEMS
    assert {'kind': 'legacy_display', 'limit': audit.MAX_ITEMS} in result['held_value_comparison_cut']
    assert 'matches' not in {row['kind'] for row in result['held_value_comparison_cut']}


@pytest.mark.parametrize('lang,updated', [('ko', '수정'), ('en', 'updated'), ('ja', '更新'), ('zh', '更新')])
def test_legacy_update_cut_names_update_reason_in_each_locale(store, lang, updated):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    legacy(store, job)
    assert len(view(store, job)['held_value_matches']) == 1
    memory(store, 'member', PHONE, 30)
    result = view(store, job)
    assert result['held_value_matches'] == []
    assert {'kind': 'legacy_match_withheld', 'limit': 0} in result['held_value_comparison_cut']
    real_msg = audit.msg
    message = real_msg('information_use.comparison_cut_legacy_match_withheld', lang)
    assert updated in message
    with mock.patch.object(audit, 'msg', side_effect=lambda key, **values: real_msg(key, lang, **values)):
        assert message in audit.render_korean({'recorded': True, 'sent_to': result})
