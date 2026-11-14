"""#820 B2: unit evidence only; retained records, no live tools or networks."""
import json
import time
from unittest import mock

import pytest

from personal_agent import agent_runtime as runtime, information_use as audit
from personal_agent.quickstart_store import QuickStore

MEMBER = 'M1234-5678'
PHONE = '010-9876-5432'


@pytest.fixture
def store(tmp_path):
    return QuickStore(tmp_path / 'state')


def memory(store, key, value, at, **kwargs):
    with mock.patch('personal_agent.quickstart_store.time.time', return_value=at):
        return store.save_memory(key, value, **kwargs)


def event(store, job, tool, status, trace, at):
    with store.db() as db:
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (job, tool, status, json.dumps({'host_action': tool, **trace}), at))


def call(store, job, action='web_search', args=None, evidence=None, at=20, status='succeeded', **trace):
    identity = str(time.time_ns())
    event(store, job, action, 'running', {'call_id': identity, 'arguments': {'query': MEMBER} if args is None else args}, at)
    detail = {'call_id': identity, 'evidence': {'outbound_comparison': 'deferred', **(evidence or {})}, **trace}
    if status == 'failed' and trace.get('state') != 'withheld':
        detail['outbound_comparison'] = (evidence or {}).get('outbound_comparison', 'deferred')
    event(store, job, action, status, detail, at + 1)


def view(store, job):
    return audit.work_information_use(store, job)['sent_to']


@pytest.mark.parametrize('action', sorted(runtime.OUTBOUND_MATCH_ACTIONS))
def test_recording_never_reads_or_compares_owner_values(action):
    caps = mock.Mock()
    caps.held_values.side_effect = AssertionError('recording read Memory')
    caps.store.memories.side_effect = AssertionError('recording read Memory')
    caps._code_runner.sent = [(MEMBER, 'example.test')]
    args = {'query': MEMBER, 'url': 'https://example.test/?q=' + MEMBER, 'text': MEMBER, 'body': PHONE}
    with mock.patch.object(runtime, 'held_value_matches', side_effect=AssertionError('compared')), \
         mock.patch.object(runtime, 'outbound_texts', side_effect=AssertionError('extracted text')):
        receipt = runtime.outbound_evidence(caps, action, args, {})
        attempt = runtime.attempted_outbound(caps, action, args, RuntimeError('after dispatch'))
    assert receipt['outbound_comparison'] == attempt['outbound_comparison'] == 'deferred'
    caps.held_values.assert_not_called()
    caps.store.memories.assert_not_called()


@pytest.mark.parametrize('action,args,evidence,destination,gap', [
    ('web_search', {'query': MEMBER}, {'provider': 'brave'}, 'brave', False),
    ('weather', {'city': MEMBER}, {}, 'geocoding-api.open-meteo.com', False),
    ('public_page_read', {'url': 'https://example.test/' + MEMBER}, {}, 'example.test', True),
    ('browser_open', {'url': 'https://example.test/' + MEMBER}, {}, 'example.test', False),
    ('api_request', {'url': 'https://example.test/' + MEMBER, 'body': '[redacted]'}, {}, 'example.test', True),
    ('code_run', {'code': '[redacted]'}, {'requests': [{'path': '/' + MEMBER, 'host': 'example.test', 'allowed': True}]}, 'example.test', True),
])
def test_view_matches_only_retained_outbound_text(store, action, args, evidence, destination, gap):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    call(store, job, action, args, evidence)
    result = view(store, job)
    assert result['held_value_matches'] == [{'source': 'memory', 'key': 'member', 'tool': action,
                                           'destination': destination, 'status': 'succeeded'}]
    assert ('outbound_unrecorded' in {cut['kind'] for cut in result['held_value_comparison_cut']}) == gap
    assert MEMBER not in json.dumps(result['held_value_matches'])
    assert result['held_value_comparison_time'] == 'view_time_retained_history'


def test_call_uses_running_time_and_supersession_not_view_time(store):
    memory(store, 'member', MEMBER, 10)
    memory(store, 'member', PHONE, 21)
    memory(store, 'future', MEMBER, 30)
    job = store.enqueue('find', 'test')
    call(store, job, args={'query': MEMBER + ' ' + PHONE}, at=20)
    assert [r['key'] for r in view(store, job)['held_value_matches']] == ['member']
    later = store.enqueue('later', 'later')
    call(store, later, args={'query': MEMBER}, at=25)
    assert view(store, later)['held_value_matches'] == []


def test_invalidated_memory_is_current_only_before_invalidation(store):
    old = memory(store, 'profile.member', MEMBER, 10)
    memory(store, 'profile.new', PHONE, 20, invalidates=[(old['id'], 'changed')])
    early = store.enqueue('find', 'test')
    call(store, early, args={'query': MEMBER}, at=15)
    late = store.enqueue('late', 'late')
    call(store, late, args={'query': MEMBER}, at=25)
    assert view(store, early)['held_value_matches'][0]['key'] == 'profile.member'
    assert view(store, late)['held_value_matches'] == []


@pytest.mark.parametrize('when', (5, 25))
@pytest.mark.parametrize('operation', ('forget', 'delete'))
def test_forgotten_or_deleted_memory_never_matched_or_named(store, when, operation):
    row = memory(store, 'private-key', MEMBER, 1)
    job = store.enqueue('find', 'test')
    call(store, job, at=20)
    from personal_agent.owner_forget import OwnerForget
    forget = OwnerForget(store, clock=lambda: when)
    forget.forget('local-owner', 'memory:' + row['id'], delete=operation == 'delete')
    assert view(store, job)['held_value_matches'] == []
    assert 'private-key' not in json.dumps(view(store, job))


def test_unrecorded_typing_api_body_and_runner_sent_stay_unrecorded(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    for action, args, evidence in [
        ('browser_type', {'text': '[가림: 10자]'}, {'url': 'https://example.test/'}),
        ('api_request', {'url': 'https://example.test/', 'body': '[가림]'}, {}),
        ('code_run', {}, {'requests': [{'host': 'example.test', 'path': '/', 'allowed': True}]}),
    ]:
        call(store, job, action, args, evidence)
    with store.db() as db:
        before = db.execute('SELECT detail FROM tool_events WHERE job_id=?', (job,)).fetchall()
    result = view(store, job)
    assert result['held_value_matches'] == []
    assert 'outbound_unrecorded' in {row['kind'] for row in result['held_value_comparison_cut']}
    with store.db() as db:
        assert before == db.execute('SELECT detail FROM tool_events WHERE job_id=?', (job,)).fetchall()
    assert MEMBER not in json.dumps([list(row) for row in before])


def test_legacy_labels_and_cuts_are_not_recomputed(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    labels = [{'source': 'memory', 'key': 'member', 'destination': 'recorded.test', 'tool': 'browser_type'}]
    call(store, job, 'browser_type', {'text': '[redacted]'},
         {'outbound_matches': labels, 'outbound_comparison_cut': [{'kind': 'text_chars', 'limit': 1000}]})
    with mock.patch.object(runtime, 'held_value_matches', side_effect=AssertionError('legacy recomputed')):
        result = view(store, job)
    assert result['held_value_matches'] == [{**labels[0], 'status': 'succeeded'}]
    assert result['held_value_comparison_cut'] == [{'kind': 'text_chars', 'limit': 1000}]
    with store.db() as db:
        db.execute("UPDATE memories SET state='forgotten'")
    assert view(store, job)['held_value_matches'] == []


def test_work_writes_are_recovered_as_later_comparison_and_missing_writes_named(store):
    job = store.enqueue('find', 'test')
    store.save_memory_candidate(job, 'candidate', MEMBER, work_id=job)
    with store.db() as db:
        db.execute('INSERT INTO notes(id,content,created) VALUES (?,?,?)', (job, PHONE, 30))
    call(store, job, args={'query': MEMBER + ' ' + PHONE})
    result = view(store, job)
    assert result['held_value_matches'] == [{'source': 'work_write', 'key': '', 'tool': 'web_search',
        'destination': '', 'status': 'succeeded', 'comparison': 'later_work_writes'}]
    assert 'work_writes_unavailable' in {r['kind'] for r in result['held_value_comparison_cut']}
    assert 'Later comparison' in audit.msg('information_use.later_work_writes', 'en')


def test_undo_ambiguity_is_explicit_later_memory_comparison(store):
    row = memory(store, 'member', MEMBER, 10)
    memory(store, 'member', PHONE, 15)
    with store.db() as db:
        db.execute("UPDATE memories SET state='retracted' WHERE content=?", (PHONE,))
        db.execute("UPDATE memories SET state='current' WHERE id=?", (row['id'],))
    job = store.enqueue('find', 'test')
    call(store, job)
    result = view(store, job)
    assert result['held_value_matches'][0]['comparison'] == 'later_memory'


def test_failed_dispatched_call_is_compared_but_local_refusal_is_not(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    call(store, job, status='failed')
    call(store, job, status='failed', ran=False)
    assert view(store, job)['held_value_matches'] == [{'source': 'memory', 'key': 'member', 'tool': 'web_search',
                                                     'destination': '', 'status': 'failed'}]


def test_view_bounds_calls_texts_matches_and_memory_reads(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    for _ in range(audit.COMPARED_CALLS_MAX + 5):
        call(store, job)
    with mock.patch.object(store, 'comparison_memories', wraps=store.comparison_memories) as reads:
        result = view(store, job)
    assert reads.call_count == audit.COMPARED_CALLS_MAX
    assert {'kind': 'calls', 'limit': audit.COMPARED_CALLS_MAX} in result['held_value_comparison_cut']
    assert all(c.args[-1] == runtime.HELD_MEMORY_MAX + 1 for c in reads.call_args_list)
    # Matcher and outer result limits are independent, and every truncated
    # dimension is owner-visible even when it yields no match.
    for i in range(20):
        memory(store, 'member-' + str(i), MEMBER, 10)
    found, cuts = audit.deferred_comparison(store, job, 'code_run', {}, {'requests': [
        {'path': '/' + MEMBER + 'x' * 1100, 'host': 'host' + str(i), 'allowed': True} for i in range(30)]}, 20)
    assert len(found) == runtime.OUTBOUND_MATCHES_MAX
    assert {'texts', 'text_chars', 'matches'} <= {r['kind'] for r in cuts}


def test_unknown_call_time_does_not_become_current_memory_evidence(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    found, cuts = audit.deferred_comparison(store, job, 'web_search', {'query': MEMBER}, {}, None)
    assert found == []
    assert 'memory_history_unavailable' in {r['kind'] for r in cuts}


def test_all_new_cut_kinds_and_later_comparisons_render_in_every_locale():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    root = Path(__file__).resolve().parents[1]
    script = r'''
const ui=require(process.argv[1]);
class Node{constructor(){this.children=[];this.textContent='';} append(...nodes){this.children.push(...nodes);}}
global.document={createElement:()=>new Node()};
for(const locale of ['ko','en','ja','zh-CN']){
  ui.setLanguage(locale);const box=new Node();
  ui.informationUseView({information_use:{recorded:true,sent_to:{workers:[],lookups:[],
    held_value_comparison_time:'view_time_retained_history',
    held_value_matches:[{source:'memory',key:'member',tool:'web_search',comparison:'later_memory'},
                        {source:'work_write',tool:'web_search',comparison:'later_work_writes'}],
    held_value_comparison_cut:['calls','outbound_unrecorded','work_writes_unavailable','memory_history_unavailable','texts','matches','comparison_time','comparison_failed','value_chars','held_values'].map(kind=>({kind,limit:20}))}}},box);
  console.log(JSON.stringify(box));
}
'''
    result = subprocess.run([node, '-e', script, str(root / 'src/personal_agent/web/app.js')], check=True, text=True, capture_output=True)
    catalogue = json.loads((root / 'src/personal_agent/messages.json').read_text())
    for locale, output in zip(('ko', 'en', 'ja', 'zh'), result.stdout.splitlines()):
        for kind in ('calls', 'outbound_unrecorded', 'work_writes_unavailable', 'memory_history_unavailable', 'texts', 'matches', 'comparison_time', 'comparison_failed', 'value_chars', 'held_values'):
            label = catalogue['information_use.comparison_cut_' + kind][locale].replace('{limit}', '20')
            assert label in output
        assert catalogue['information_use.view_time_comparison'][locale] in output
        for kind in ('later_memory', 'later_work_writes'):
            assert catalogue['information_use.' + kind][locale] in output


def test_forgetting_legacy_memory_then_recreating_its_key_does_not_revive_the_label(store):
    memory(store, 'member', MEMBER, 10)
    job = store.enqueue('find', 'test')
    call(store, job, evidence={'outbound_matches': [{'source': 'memory', 'key': 'member', 'destination': 'old'}]})
    with store.db() as db:
        db.execute('DELETE FROM memories')
    memory(store, 'member', PHONE, 30)
    assert view(store, job)['held_value_matches'] == []
