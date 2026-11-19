"""#1031 lesson-retention eval: synthetic stores only, no live data, no model, no network.

The fixtures are invented. They reproduce the shape the issue observed (a version that drops lessons the
version before it held) without any owner words.
"""
import asyncio
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
sys.path.insert(0, str(ROOT / 'evals' / 'lessons'))

import lessons_eval as le  # noqa: E402
from agentos_eval.budget import JudgeBudget  # noqa: E402
from personal_agent.owner_model import WorkingNotes  # noqa: E402
from personal_agent.procedures import NOTES_SLOT  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402

LESSON_A = 'Compare every figure against two independent sources before reporting it'
LESSON_B = 'Keep replies to three short sentences unless asked for more'
LESSON_C = 'Name the file path whenever a file is mentioned'
LESSON_D = 'Offer the cheaper option first when prices differ'


def notes(*points):
    return '\n'.join('- ' + point for point in points)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    import os
    for name in list(os.environ):
        if name.startswith('AGENTOS_EVAL_'):
            monkeypatch.delenv(name)
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path / 'eval-home'))


@pytest.fixture
def store(tmp_path):
    return QuickStore(tmp_path / 'state')


def commit_versions(store, versions, *, start=100.0):
    """Commit each text as the next version through the real library; saved times are start, start+100, ..."""
    library = WorkingNotes(store).library
    for index, text in enumerate(versions):
        row = library.commit(NOTES_SLOT, text, work_id=None, stats={'change': f'change {index + 1}'})
        with store.db() as db:
            db.execute('UPDATE procedure_versions SET saved=?, created=? WHERE id=?',
                       (start + 100 * index, start + 100 * index, row['id']))


def set_state(store, version, state):
    with store.db() as db:
        db.execute('UPDATE procedure_versions SET state=? WHERE slot=? AND version=?', (state, NOTES_SLOT, version))


def collapse_store(store):
    """The issue's shape: three lessons, then a version that keeps only one."""
    commit_versions(store, [notes(LESSON_A, LESSON_B, LESSON_C), notes(LESSON_A)])
    return store


def add_work(store, request, answer, *, at, typed=True):
    job = store.enqueue(request, f'key-{at}-{request[:8]}', owner_typed=typed, owner_words=typed, channel='web')
    with store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded', created=?, source_at=?, response=? WHERE id=?",
                   (at, at, answer, job))
        db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                   ('user', request, 'web', at, job))
        db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                   ('assistant', answer, 'web', at + 1, job))
    return job


def add_filler(store, count=3):
    """search_conversation never returns the newest message; keep unrelated ones last."""
    with store.db() as db:
        db.executemany('INSERT INTO messages(role,content,channel,created) VALUES (?,?,?,?)',
                       [('user', f'zzqq filler {i}', 'web', 9000 + i) for i in range(count)])


# ---------------------------------------------------------------- retention

def test_learned_is_every_distinct_lesson_across_versions(store):
    collapse_store(store)
    learned = le.learned_lessons(store)
    assert [row['text'] for row in learned] == [LESSON_A, LESSON_B, LESSON_C]
    assert [(row['first_version'], row['last_version']) for row in learned] == [(1, 2), (1, 1), (1, 1)]
    assert all(len(row['digest']) == 16 for row in learned)


def test_forgotten_versions_do_not_count_as_learned(store):
    commit_versions(store, [notes(LESSON_A), notes(LESSON_A, LESSON_D), notes(LESSON_A, LESSON_B)])
    set_state(store, 2, 'forgotten')
    assert [row['text'] for row in le.learned_lessons(store)] == [LESSON_A, LESSON_B]


def test_point_repeated_in_other_spacing_or_case_is_one_lesson(store):
    commit_versions(store, [notes(LESSON_A), notes('  ' + LESSON_A.upper() + '  ')])
    assert len(le.learned_lessons(store)) == 1


def test_carried_is_what_the_brief_block_carries(store):
    collapse_store(store)
    assert le.carried_lessons(store) == [LESSON_A]
    # The adapter reads the same text the brief carries, not a parallel copy of it.
    block = WorkingNotes(store).block()
    assert LESSON_A in block and LESSON_B not in block


def test_carried_is_empty_without_notes(store):
    assert le.carried_lessons(store) == []
    assert le.learned_lessons(store) == []
    report = le.retention_report(store)
    assert report['learned'] == 0 and report['retention'] is None


def test_retention_of_a_collapse_counts_the_lost_lessons(store):
    collapse_store(store)
    report = le.retention_report(store)
    assert (report['learned'], report['carried']) == (3, 1)
    assert report['retained'] == {'exact': 1, 'reworded': 0, 'total': 1}
    assert report['lost'] == 2
    assert report['retention'] == pytest.approx(1 / 3)
    assert report['versions'] == 2
    assert report['points_per_version'] == [3, 1]
    assert report['drop_events'] == [{'from_version': 1, 'to_version': 2, 'lost': 2}]


def test_retention_is_full_when_every_lesson_survives_every_revision(store):
    commit_versions(store, [notes(LESSON_A), notes(LESSON_A, LESSON_B), notes(LESSON_A, LESSON_B, LESSON_C)])
    report = le.retention_report(store)
    assert report['retention'] == 1.0 and report['lost'] == 0 and report['drop_events'] == []


def test_a_lesson_dropped_then_relearned_counts_as_retained_with_a_drop_event(store):
    commit_versions(store, [notes(LESSON_A, LESSON_B), notes(LESSON_A), notes(LESSON_A, LESSON_B)])
    report = le.retention_report(store)
    assert report['learned'] == 2 and report['retention'] == 1.0
    assert report['drop_events'] == [{'from_version': 1, 'to_version': 2, 'lost': 1}]


def test_reworded_lesson_matches_above_the_threshold_only(store):
    commit_versions(store, [notes(LESSON_A, LESSON_B), notes(LESSON_A + ' please', LESSON_D)])
    strict = le.retention_report(store, threshold=1.0)
    loose = le.retention_report(store, threshold=0.8)
    # Strict: the reworded point is a lesson of its own. Loose: it is LESSON_A reworded, so LESSON_A is still carried.
    assert strict['learned'] == 4 and strict['retained'] == {'exact': 2, 'reworded': 0, 'total': 2}
    assert loose['learned'] == 3 and loose['retained'] == {'exact': 1, 'reworded': 1, 'total': 2}
    assert loose['threshold'] == 0.8


def test_match_lessons_prefers_exact_and_never_reuses_a_carried_point():
    learned = [{'digest': 'd1', 'text': 'alpha beta gamma delta'}, {'digest': 'd2', 'text': 'alpha beta gamma delta epsilon'}]
    matched = le.match_lessons(learned, ['alpha beta gamma delta'], threshold=0.7)
    assert matched == {'d1': 'exact', 'd2': None}


def test_detail_rows_carry_digests_and_never_text(store):
    collapse_store(store)
    report = le.retention_report(store, detail=True)
    assert len(report['lessons']) == 3
    assert {row['carried'] for row in report['lessons']} == {True, False}
    dump = json.dumps(report)
    for text in (LESSON_A, LESSON_B, LESSON_C):
        assert text not in dump
    assert 'text' not in report['lessons'][0]


def test_report_has_counts_only_by_default_and_matches_the_schema(store):
    import jsonschema
    collapse_store(store)
    schema = json.loads((ROOT / 'evals' / 'lessons' / 'schema.json').read_text())
    for detail in (False, True):
        report = le.retention_report(store, detail=detail)
        jsonschema.validate(report, schema)
        assert 'lessons' in report if detail else 'lessons' not in report
        for text in (LESSON_A, LESSON_B, LESSON_C):
            assert text not in json.dumps(report)


# -------------------------------------------------------------- snapshot / paths

def test_snapshot_copies_without_touching_the_source(tmp_path, store):
    collapse_store(store)
    source = store.path
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    folder = le.snapshot(source, tmp_path / 'out')
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    copy = QuickStore(folder)
    assert [row['text'] for row in le.learned_lessons(copy)] == [LESSON_A, LESSON_B, LESSON_C]
    assert folder == (tmp_path / 'out' / 'snapshot')


def test_snapshot_opens_the_source_read_only(tmp_path, monkeypatch):
    seen = []
    real = sqlite3.connect

    def spy(target, *args, **kwargs):
        seen.append(str(target))
        return real(target, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', spy)
    source = tmp_path / 'source.db'
    with real(source) as db:
        db.execute('CREATE TABLE t(x)')
    le.snapshot(source, tmp_path / 'out')
    assert any('mode=ro' in target for target in seen)


def test_snapshot_refuses_a_missing_source(tmp_path):
    with pytest.raises(SystemExit):
        le.snapshot(tmp_path / 'nope.db', tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_outputs_inside_the_repository_are_refused(tmp_path):
    with pytest.raises(SystemExit):
        le.check_out_path(ROOT / 'evals' / 'lessons' / 'out.json')
    assert le.check_out_path(tmp_path / 'ok.json') == tmp_path / 'ok.json'


# ----------------------------------------------------------------- application

def application_store(store):
    """Lesson A first saved at 100; B at 100 and dropped at 200. Works around it."""
    collapse_store(store)
    early = add_work(store, 'Compare quarterly figures from the report', 'Reported the figure from one source.', at=50)
    late = add_work(store, 'Compare shipping figures for the order', 'Checked two sources and reported the figure.', at=300)
    later = add_work(store, 'Compare housing figures for the city', 'Reported a figure.', at=400)
    add_work(store, 'Keep replies short for the status update', 'Done.', at=150)
    add_filler(store)
    return early, late, later


def test_pairs_only_use_works_after_the_lesson_was_saved(store):
    early, late, later = application_store(store)
    learned = le.learned_lessons(store)
    pairs = le.build_pairs(store, learned, per_lesson=3, max_pairs=40)
    works = {pair['work_id'] for pair in pairs}
    assert early not in works
    assert {late, later} <= works
    assert all(pair['lesson'] for pair in pairs)


def test_pairs_are_capped_and_spread_over_lessons(store):
    collapse_store(store)
    for index in range(4):
        add_work(store, f'Compare figures batch {index} for review', 'Done.', at=300 + index)
        add_work(store, f'Keep replies short batch {index} please', 'Done.', at=150 + index)
    add_filler(store)
    pairs = le.build_pairs(store, le.learned_lessons(store), per_lesson=3, max_pairs=3)
    assert len(pairs) == 3
    assert len({pair['lesson'] for pair in pairs}) >= 2


def test_documents_and_non_owner_typed_works_are_never_paired(store):
    collapse_store(store)
    add_work(store, 'Compare figures from an automated feed', 'Done.', at=300, typed=False)
    add_filler(store)
    assert le.build_pairs(store, le.learned_lessons(store), per_lesson=3, max_pairs=40) == []


def test_parse_verdict_is_strict_and_defaults_to_unclear():
    assert le.parse_verdict('{"verdict": "followed", "reason": "x"}') == 'followed'
    assert le.parse_verdict('```json\n{"verdict": "not_followed"}\n```') == 'not_followed'
    assert le.parse_verdict('{"verdict": "not_applicable"}') == 'not_applicable'
    assert le.parse_verdict('{"verdict": "maybe"}') == 'unclear'
    assert le.parse_verdict('followed') == 'unclear'
    assert le.parse_verdict('') == 'unclear'


def test_prompt_holds_the_lesson_request_and_answer_and_is_bounded():
    prompt = le.judge_prompt('LESSONTEXT', 'REQUESTTEXT', 'A' * 50000)
    assert 'LESSONTEXT' in prompt and 'REQUESTTEXT' in prompt
    assert len(prompt) < 12000
    for verdict in ('followed', 'not_followed', 'not_applicable', 'unclear'):
        assert verdict in prompt


def run_application(store, replies, tmp_path, *, cap=10.0, redact=None, **kwargs):
    calls = []

    async def generate(system, prompt):
        calls.append(prompt)
        reply = replies[len(calls) - 1] if isinstance(replies, list) else replies
        if isinstance(reply, Exception):
            raise reply
        return NS(model='fake-model', usage=NS(input_tokens=30, output_tokens=5),
                  completion=json.dumps({'verdict': reply, 'reason': 'r'}))
    budget = JudgeBudget(tmp_path / 'budget.json', cap_usd=cap)
    report = asyncio.run(le.application_report(store, generate, budget, redact or (lambda text: text), **kwargs))
    return report, calls


def test_application_counts_every_verdict_and_reports_follow_rate(store, tmp_path):
    application_store(store)
    pairs = le.build_pairs(store, le.learned_lessons(store), per_lesson=3, max_pairs=40)
    assert len(pairs) >= 3
    replies = ['followed', 'not_followed', 'not_applicable', 'unclear', 'followed', 'followed'][:len(pairs)]
    replies += ['unclear'] * (len(pairs) - len(replies))
    report, calls = run_application(store, replies, tmp_path)
    assert report['pairs'] == len(pairs) == len(calls)
    counted = sum(report[key] for key in ('followed', 'not_followed', 'not_applicable', 'unclear', 'judge_error', 'budget_refused'))
    assert counted == report['pairs']
    assert report['followed'] == replies.count('followed')
    denominator = report['followed'] + report['not_followed']
    assert report['follow_rate'] == pytest.approx(report['followed'] / denominator)
    assert report['judge'] == 'explicit'


def test_follow_rate_is_none_when_nothing_was_applicable(store, tmp_path):
    application_store(store)
    report, _ = run_application(store, 'not_applicable', tmp_path)
    assert report['follow_rate'] is None and report['not_applicable'] == report['pairs'] > 0


def test_a_judge_outage_is_counted_and_never_a_verdict(store, tmp_path):
    application_store(store)
    report, _ = run_application(store, RuntimeError('down'), tmp_path)
    assert report['judge_error'] == report['pairs'] > 0 and report['followed'] == 0


def test_a_spent_budget_refuses_every_call(store, tmp_path):
    application_store(store)
    report, calls = run_application(store, 'followed', tmp_path, cap=0.0)
    assert calls == [] and report['budget_refused'] == report['pairs'] > 0


def test_the_prompt_is_redacted_before_the_judge_sees_it(store, tmp_path):
    collapse_store(store)
    add_work(store, 'Compare the figures with token SECRET123', 'Done with SECRET123.', at=300)
    add_filler(store)
    report, calls = run_application(store, 'followed', tmp_path, redact=lambda text: text.replace('SECRET123', '[redacted]'))
    assert calls and all('SECRET123' not in call for call in calls)


def test_application_report_has_no_owner_text_and_matches_the_schema(store, tmp_path):
    import jsonschema
    application_store(store)
    report, _ = run_application(store, 'followed', tmp_path)
    jsonschema.validate(report, json.loads((ROOT / 'evals' / 'lessons' / 'schema.json').read_text()))
    dump = json.dumps(report)
    for text in (LESSON_A, LESSON_B, 'Compare shipping', 'Checked two sources'):
        assert text not in dump


def test_plan_prices_the_run_without_a_judge(store):
    application_store(store)
    plan = le.plan_application(store, JudgeBudget(Path('unused.json')))
    assert plan['pairs'] > 0
    assert plan['est_input_tokens'] > 0 and plan['worst_case_usd'] > 0
    assert plan['judge'] == 'not called'


# ------------------------------------------------------------------------- CLI

def seeded_snapshot(tmp_path):
    source = QuickStore(tmp_path / 'live')
    application_store(source)
    return le.snapshot(source.path, tmp_path / 'private-out')


def test_cli_retention_writes_counts_to_the_named_output(tmp_path, capsys):
    folder = seeded_snapshot(tmp_path)
    out = tmp_path / 'private-out' / 'retention.json'
    assert le.main(['retention', '--snapshot', str(folder), '--out', str(out)]) == 0
    report = json.loads(out.read_text())
    assert (report['learned'], report['carried']) == (3, 1)
    assert 'retention' in capsys.readouterr().out


def test_cli_application_never_calls_a_judge_unless_one_is_named(tmp_path, capsys):
    folder = seeded_snapshot(tmp_path)
    calls = []

    async def generate(system, prompt):
        calls.append(prompt)
        raise AssertionError('the judge must not run')
    assert le.main(['application', '--snapshot', str(folder)], generate=generate) != 0
    assert calls == []
    assert 'judge' in capsys.readouterr().out.lower()


def test_cli_plan_needs_no_judge_and_no_key(tmp_path, capsys):
    folder = seeded_snapshot(tmp_path)

    def no_key(judge):
        raise AssertionError('no key may be read for a plan')
    assert le.main(['application', '--snapshot', str(folder), '--plan'], key_loader=no_key) == 0
    assert 'pairs' in capsys.readouterr().out


def test_cli_runs_the_judge_only_when_a_route_is_named(tmp_path):
    folder = seeded_snapshot(tmp_path)
    out = tmp_path / 'private-out' / 'application.json'
    calls = []

    async def generate(system, prompt):
        calls.append(prompt)
        return NS(model='fake-model', usage=NS(input_tokens=30, output_tokens=5),
                  completion=json.dumps({'verdict': 'followed', 'reason': 'r'}))
    code = le.main(['application', '--snapshot', str(folder), '--judge', 'fake/route', '--max-pairs', '2',
                    '--out', str(out)], generate=generate, key_loader=lambda judge: 'environment')
    assert code == 0
    report = json.loads(out.read_text())
    assert report['pairs'] == len(calls) == 2 and report['followed'] == 2
    assert report['judge_route'] == 'fake/route'


def test_cli_refuses_an_output_inside_the_repository(tmp_path):
    folder = seeded_snapshot(tmp_path)
    with pytest.raises(SystemExit):
        le.main(['retention', '--snapshot', str(folder), '--out', str(ROOT / 'evals' / 'lessons' / 'x.json')])


@pytest.fixture(autouse=True)
def synthetic_live_profile(tmp_path, monkeypatch):
    from agentos_eval import paths, redaction

    live = tmp_path / 'synthetic-live'
    monkeypatch.setattr(paths, 'LIVE_DATA', live)
    monkeypatch.setattr(redaction, 'LIVE_DATA', live)
    return live


def test_cli_redacts_literal_snapshot_and_live_profile_secrets(tmp_path, synthetic_live_profile):
    folder = seeded_snapshot(tmp_path)
    snapshot_secret = 'orchid-harbor'
    live_secret = 'cobalt-valley'
    (folder / 'private' / 'connections.json').write_text(json.dumps({'seed': snapshot_secret}))
    live_private = synthetic_live_profile / 'private'
    live_private.mkdir(parents=True)
    (live_private / 'connections.json').write_text(json.dumps({'live': live_secret}))
    store = QuickStore(folder)
    add_work(store, f'Compare figures for {snapshot_secret} and {live_secret}', 'Done.', at=500)
    add_filler(store)
    calls = []

    async def generate(system, prompt):
        calls.append(prompt)
        return NS(model='fake', usage=None, completion='{"verdict": "followed"}')

    assert le.main(['application', '--snapshot', str(folder), '--judge', 'fake/route'],
                   generate=generate, key_loader=lambda _: 'environment') == 0
    assert any('[redacted] and [redacted]' in prompt for prompt in calls)
    assert all(snapshot_secret not in prompt and live_secret not in prompt for prompt in calls)


def test_final_message_is_redacted_before_clipping(store, tmp_path):
    commit_versions(store, [notes(LESSON_A)])
    secret = 'orchid-harbor-secret'
    work = add_work(store, 'Compare figures', 'Draft', at=150)
    answer = 'x' * 5990 + secret
    with store.db() as db:
        db.execute("INSERT INTO messages(role,content,channel,created,job_id) VALUES ('assistant',?,'web',152,?)",
                   (answer, work))
    add_filler(store)

    report, calls = run_application(store, 'followed', tmp_path,
                                    redact=lambda value: value.replace(secret, '[redacted]'))
    assert report['pairs'] == 1
    assert calls[0].endswith('x' * 5990 + '[redacted]')
    assert 'orchid-har' not in calls[0]


@pytest.mark.parametrize('disqualifier', ['forgotten', 'failed'])
def test_unavailable_answers_are_neither_paired_nor_sent(store, tmp_path, disqualifier):
    commit_versions(store, [notes(LESSON_A)])
    work = add_work(store, 'Compare figures', 'Private answer.', at=150)
    with store.db() as db:
        if disqualifier == 'forgotten':
            db.execute("INSERT INTO forgotten_messages SELECT id, 'test-forget', 160 FROM messages "
                       "WHERE job_id=? AND role='assistant'", (work,))
        else:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (work,))
    add_filler(store)
    assert le.build_pairs(store, le.learned_lessons(store)) == []
    report, calls = run_application(store, 'followed', tmp_path)
    assert report['pairs'] == 0 and calls == []


def test_cli_refuses_live_profile_via_alias(tmp_path, synthetic_live_profile):
    QuickStore(synthetic_live_profile)
    alias = tmp_path / 'live-alias'
    alias.symlink_to(synthetic_live_profile, target_is_directory=True)
    with pytest.raises(SystemExit, match='live'):
        le.main(['application', '--snapshot', str(alias), '--plan'])


def test_cli_refuses_missing_snapshot_db(tmp_path):
    folder = tmp_path / 'missing-snapshot'
    with pytest.raises(SystemExit, match='Snapshot database does not exist'):
        le.main(['application', '--snapshot', str(folder), '--plan'])
    assert not folder.exists()


def test_snapshot_refuses_destination_inside_live_profile(store, synthetic_live_profile):
    with pytest.raises(SystemExit, match='live'):
        le.snapshot(store.path, synthetic_live_profile / 'eval')
    assert not synthetic_live_profile.exists()


def test_drop_events_use_the_retention_threshold(store):
    commit_versions(store, [notes(LESSON_A, LESSON_B), notes(LESSON_A + ' please', LESSON_D)])
    loose = le.retention_report(store, threshold=0.8)
    strict = le.retention_report(store, threshold=1.0)
    assert loose['drop_events'] == [{'from_version': 1, 'to_version': 2, 'lost': 1}]
    assert strict['drop_events'] == [{'from_version': 1, 'to_version': 2, 'lost': 2}]


def test_threshold_one_disables_token_set_matches(store):
    commit_versions(store, [notes('alpha beta'), notes('beta alpha')])
    report = le.retention_report(store, threshold=1.0)
    assert report['learned'] == 2
    assert report['retained'] == {'exact': 1, 'reworded': 0, 'total': 1}


def test_pairs_stop_at_the_first_nonforgotten_version_after_the_lesson(store):
    commit_versions(store, [notes(LESSON_B), notes(LESSON_D), notes(LESSON_D)])
    set_state(store, 2, 'forgotten')
    inside = add_work(store, 'Keep replies short', 'Done.', at=250)
    add_work(store, 'Keep replies short', 'Done.', at=300)
    add_work(store, 'Keep replies short', 'Done.', at=350)
    add_filler(store)
    learned = le.learned_lessons(store)
    assert [pair['work_id'] for pair in le.build_pairs(store, learned) if pair['lesson'] == learned[0]['digest']] == [inside]
    assert [lesson['end_saved'] for lesson in learned] == [300.0, None]
