"""#921 PR-4: snapshot fill follows the store's recency order (model-free)."""
from unittest import mock

import pytest

from personal_agent.agent_runtime import (
    CUT_TO_FIT_MARKER, CUT_TO_FIT_ORDER, cut_to_fit, render_turn_prompt, turn_context,
)
from personal_agent.bounded_execution import MAX_PROMPT_BYTES
from personal_agent.memory_service import (
    MemoryService, PROFILE_SNAPSHOT_BYTES, PROFILE_SNAPSHOT_CHARS, VOICE_PREFERENCE_KEY,
    conversation_block,
)
from personal_agent.quickstart_store import QuickStore


@pytest.fixture
def profile(tmp_path, monkeypatch):
    clock = [1_794_873_600.0]
    monkeypatch.setattr('personal_agent.quickstart_store.time.time', lambda: clock[0])
    store = QuickStore(tmp_path / 'state')
    service = MemoryService(store, now=lambda: clock[0], private_read_sink=MemoryService.NO_EGRESS_GUARD)

    def save(key, content='background', **kwargs):
        clock[0] += 1
        return store.save_memory(key, content, owner_id='local-owner', **kwargs)

    return service, save


def row_ids(rows):
    return [row['id'] for row in rows]


def test_voice_then_bm25_matches_then_newest_fill(profile):
    memory, save = profile
    if not memory.store.memory_search_available:
        pytest.skip('SQLite FTS5 unavailable: BM25 ordering needs the real index')
    voice = save(VOICE_PREFERENCE_KEY, 'Use a warm voice')
    strong = save('profile.zmatch', 'needle needle needle')
    weak = save('profile.amatch', 'needle ' + 'background ' * 30)
    old = save('profile.afill')
    new = save('profile.zfill')
    matches = memory.search_memories('local-owner', 'needle', key_prefix='profile.')
    assert matches['search_mode'] == 'fts5'
    assert row_ids(matches['memories']) == row_ids([strong, weak])
    snapshot = memory.profile_snapshot('local-owner', query='needle', ids=True,
                                       annotations={strong['id']: 'check this fact', new['id']: 'not a match'})
    assert row_ids(snapshot['entries']) == row_ids([voice, strong, weak, new, old])
    for entry, row, line in zip(snapshot['entries'], [voice, strong, weak, new, old], snapshot['text'].splitlines()):
        assert entry == {key: row[key] for key in ('id', 'memory_key', 'content', 'created', 'content_digest')} | {
            'matched': row in (strong, weak), 'rank': {strong['id']: 1, weak['id']: 2}.get(row['id'])} | (
            {'annotated': True} if row == strong else {})
        assert line.startswith(f"[{row['id'][:8]}] {row['memory_key']}: ")
    assert 'check this fact' in snapshot['text']
    assert 'not a match' not in snapshot['text']
    assert (snapshot['total_count'], snapshot['omitted_count'], snapshot['truncated']) == (5, 0, False)


def test_equal_created_uses_descending_id(profile):
    memory, save = profile
    # Insertion and key order both differ from id DESC at the equal timestamp.
    with mock.patch('personal_agent.quickstart_store.uuid.uuid4', return_value='ffffffff-0000-0000-0000-000000000000'):
        high = save('profile.z')
    with mock.patch('personal_agent.quickstart_store.uuid.uuid4', return_value='00000000-0000-0000-0000-000000000000'):
        low = save('profile.a')
    with memory.store.db() as db:
        db.execute('UPDATE memories SET created=? WHERE id=?', (high['created'], low['id']))
    assert row_ids(memory.profile_snapshot('local-owner')['entries']) == row_ids([high, low])


def test_profile_memories_keeps_key_order(profile):
    memory, save = profile
    a = save('profile.a')
    z = save('profile.z')
    m = save('profile.m')
    assert row_ids(memory.profile_memories('local-owner')['memories']) == row_ids([a, m, z])
    assert row_ids(memory.profile_snapshot('local-owner')['entries']) == row_ids([m, z, a])


@pytest.mark.parametrize('content', ['x' * 190, '가' * 190], ids=['characters', 'korean-bytes'])
def test_saturated_snapshot_keeps_newest_and_omits_oldest(profile, content):
    memory, save = profile
    rows = [save(f'profile.fact.{index:03}', content) for index in range(90)]
    snapshot = memory.profile_snapshot('local-owner')
    shown = len(snapshot['entries'])
    assert 0 < shown < len(rows)
    assert row_ids(snapshot['entries']) == row_ids(list(reversed(rows))[:shown])
    assert snapshot['omitted_count'] == len(rows) - shown
    assert snapshot['total_count'] == len(rows) and snapshot['truncated']
    assert len(snapshot['text']) <= PROFILE_SNAPSHOT_CHARS == 12_000
    assert len(snapshot['text'].encode()) <= PROFILE_SNAPSHOT_BYTES == 12_000
    # The next (older) line is exactly the first one that cannot fit.
    next_row = rows[-shown - 1]
    next_line = f"{next_row['memory_key']}: {content} (saved {memory._saved_date(next_row['created'])})"
    with_next = snapshot['text'] + '\n' + next_line
    if content.isascii():
        assert len(with_next) > PROFILE_SNAPSHOT_CHARS
    else:
        assert len(snapshot['text']) < PROFILE_SNAPSHOT_CHARS
        assert len(with_next) < PROFILE_SNAPSHOT_CHARS
        assert len(with_next.encode()) > PROFILE_SNAPSHOT_BYTES


def test_candidate_window_is_newest_500_before_any_key_sort(profile):
    memory, save = profile
    rows = [save(f'profile.fact.{index:03}', 'x') for index in range(505)]
    candidates = memory._profile_rows('local-owner', holding_on=memory._today(), newest_first=True)
    assert row_ids(candidates) == row_ids(list(reversed(rows))[:500])
    snapshot = memory.profile_snapshot('local-owner')
    assert snapshot['total_count'] == 500
    assert row_ids(snapshot['entries']) == row_ids(list(reversed(rows))[:len(snapshot['entries'])])
    assert not set(row_ids(rows[:5])) & set(row_ids(snapshot['entries']))
    # The default keeps its pre-existing sort-then-cap behaviour for the owner's list.
    assert row_ids(memory.profile_memories('local-owner')['memories']) == row_ids(rows[:500])


def test_saturated_worker_prompt_cuts_older_fill_after_matches(profile):
    memory, save = profile
    voice = save(VOICE_PREFERENCE_KEY, 'warm voice')
    matches = [save(f'profile.match.{index}', 'needle ' + 'm' * 170) for index in range(3)]
    fills = [save(f'profile.fill.{index:03}', 'x' * 190) for index in range(90)]
    snapshot = memory.profile_snapshot('local-owner', query='needle')
    assert snapshot['truncated']
    # The complete profile section includes all the kinds of material supplied to a worker.
    notes = 'working_lessons (fixture):\n- ' + 'lesson ' * 100 + '(memory:01234567-0000-0000-0000-0123456789ab)'
    exchanges = conversation_block([{'work_id': 'old-work', 'matched_id': 1,
                                      'turns': [{'id': 1, 'role': 'user', 'content': 'needle from earlier'}]}])
    save('diary.2000-01-01', 'needle record ' * 100)
    records = memory.records_block('local-owner', 'needle')
    assert exchanges and records
    full_profile = '\n'.join([snapshot['text'], notes, exchanges, records])
    sections = dict(profile=full_profile, skills=('skill description ' * 100 + '\n') * 12,
                    prepared=('prepared answer ' * 100 + '\n') * 4,
                    brief='supplementary notes\n' * 30, current_context='clock observation\n' * 10)
    request = 'needle ' + '𐀀' * 10_750
    history = [{'role': 'user', 'content': 'earlier turn'}, {'role': 'user', 'content': request}]

    def build(values):
        context = turn_context(history, 'cli', **values)
        return context, render_turn_prompt(context, include_instructions=False)

    original, prompt = build(sections)
    assert original['profile'] == full_profile
    assert original['conversation'] == []  # the existing shared 42 KB budget drops earlier turns
    assert len(prompt.encode()) > MAX_PROMPT_BYTES == 48_000
    trimmed, cut = cut_to_fit(sections, len(prompt.encode()) - MAX_PROMPT_BYTES)
    context, sent = build(trimmed)
    assert cut == list(CUT_TO_FIT_ORDER)
    assert len(sent.encode()) <= MAX_PROMPT_BYTES
    assert context['request'] == request
    assert context['profile'].endswith(CUT_TO_FIT_MARKER)
    sent_lines = context['profile'].splitlines()[:-1]
    assert sent_lines == snapshot['text'].splitlines()[:len(sent_lines)]
    surviving = snapshot['entries'][:len(sent_lines)]
    assert surviving[0]['id'] == voice['id']
    assert set(row_ids(matches)) <= set(row_ids(surviving))
    surviving_fill = [row for row in surviving if row['memory_key'].startswith('profile.fill.')]
    assert 0 < len(surviving_fill) < len(snapshot['entries']) - 4
    assert row_ids(surviving_fill) == row_ids(list(reversed(fills))[:len(surviving_fill)])


from test_orchestrator import Harness


class WorkerFallback(Harness):
    def test_oversized_envelope_still_falls_back_to_verbatim_request(self):
        """Exercise work_executor's real fallback with a fixture worker, never a live CLI."""
        self.store.save_memory('profile.fact', 'owner fact')
        self.script([None], [True])
        request = '𐀀' * 11_998
        with mock.patch('personal_agent.work_executor.cut_to_fit', wraps=cut_to_fit) as cut:
            work, _ = self.run_work(request, key='921-bare-request')
        assert cut.called
        turn = self.engine.turns[-1]
        assert turn['prompt'] == request
        assert turn['context'] is None
        assert self.store.turn_provenance(work)['context_mode'] == 'bare-request'
