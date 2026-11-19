"""#1031 step 3 topic-page experiment: synthetic stores and fake judgments only, no live data, no model, no network.

The fixtures are invented. They pin the page rules the experiment depends on: every operation carries a source,
a page is changed item by item and never rewritten whole, and a held-out request never reaches its own page.
"""
import copy
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
sys.path.insert(0, str(ROOT / 'evals' / 'lessons'))
sys.path.insert(0, str(ROOT / 'evals' / 'topics'))

import topic_pages as tp  # noqa: E402
import topics_eval as te  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402

DAY1, DAY2, DAY3 = '2026-11-01', '2026-11-02', '2026-11-03'


def sources(**works):
    """work id -> (day, owner text)."""
    return {work_id: {'day': day, 'owner_text': text} for work_id, (day, text) in works.items()}


def add(section, text, source, quote=None):
    op = {'op': 'add', 'section': section, 'text': text, 'source': source}
    if quote is not None:
        op['quote'] = quote
    return op


@pytest.fixture
def page():
    return tp.new_page('t1', 'Garden', 'The vegetable garden and its watering')


def filled(page, n):
    """A page holding ``n`` state items, each from its own day-1 Work."""
    src = sources(**{f'w{i}': (DAY1, f'note {i}') for i in range(n)})
    out, report = tp.apply_answer(page, {'ops': [add('state', f'fact {i}', f'w{i}') for i in range(n)]},
                                  day=DAY1, sources=src)
    assert report['applied'] == n and not report['refused']
    return out


# --- operations carry a source ------------------------------------------------------------------------------------

def test_operation_without_source_is_refused_and_others_still_apply(page):
    src = sources(w1=(DAY1, 'Water the tomatoes every morning before eight'))
    answer = {'ops': [add('state', 'Tomatoes planted', None),
                      {'op': 'add', 'section': 'decision', 'text': 'Drip line chosen'},
                      add('standard', 'Water before eight', 'w1', quote='every morning before eight')]}
    out, report = tp.apply_answer(page, answer, day=DAY1, sources=src)
    assert [r['code'] for r in report['refused']] == ['missing_source', 'missing_source']
    assert report['applied'] == 1
    assert [item['text'] for item in out['items']] == ['Water before eight']
    assert out['items'][0]['source'] == 'w1' and out['items'][0]['day'] == DAY1


def test_source_outside_the_days_works_is_refused(page):
    src = sources(w1=(DAY1, 'hello'))
    out, report = tp.apply_answer(page, {'ops': [add('state', 'x', 'w-other')]}, day=DAY1, sources=src)
    assert [r['code'] for r in report['refused']] == ['unknown_source']
    assert out['items'] == []


def test_update_and_remove_need_a_source_too(page):
    page = filled(page, 2)
    item_id = page['items'][0]['id']
    src = sources(w9=(DAY2, 'changed'))
    answer = {'ops': [{'op': 'update', 'id': item_id, 'text': 'fact 0 changed'},
                      {'op': 'remove', 'id': item_id, 'reason': 'stale'}]}
    out, report = tp.apply_answer(page, answer, day=DAY2, sources=src)
    assert [r['code'] for r in report['refused']] == ['missing_source', 'missing_source']
    assert out == page


# --- standards quote the owner's words ----------------------------------------------------------------------------

def test_standard_needs_a_verbatim_owner_quote(page):
    src = sources(w1=(DAY1, 'Please   compare at least two\nsources before you report a price'))
    answer = {'ops': [add('standard', 'Two sources', 'w1'),  # no quote
                      add('standard', 'Two sources', 'w1', quote='always check three sources'),  # not the owner's
                      add('standard', 'Two sources', 'w1', quote='compare at least two sources')]}  # whitespace only
    out, report = tp.apply_answer(page, answer, day=DAY1, sources=src)
    assert [r['code'] for r in report['refused']] == ['quote_not_verbatim', 'quote_not_verbatim']
    assert len(out['items']) == 1 and out['items'][0]['quote'] == 'compare at least two sources'


def test_render_quotes_owner_words_with_source_and_date(page):
    src = sources(w1=(DAY1, 'Keep the summary to one line'))
    out, _ = tp.apply_answer(page, {'ops': [add('standard', 'One-line summary', 'w1', quote='Keep the summary to one line'),
                                            add('state', 'Seeds ordered', 'w1')]}, day=DAY1, sources=src)
    text = tp.render(out)
    assert '"Keep the summary to one line"' in text
    assert 'work:w1' in text and DAY1 in text
    assert 'Garden' in text


def test_render_stays_within_budget_and_states_omitted(page):
    page = filled(page, 60)
    text = tp.render(page, budget=400)
    assert len(text) <= 400
    assert 'omitted' in text.splitlines()[-1]


# --- item by item, never rewritten whole --------------------------------------------------------------------------

def test_update_keeps_the_id_and_records_history_without_mutating_input(page):
    page = filled(page, 3)
    before = copy.deepcopy(page)
    item_id = page['items'][1]['id']
    src = sources(w9=(DAY2, 'it changed'))
    out, report = tp.apply_answer(page, {'ops': [{'op': 'update', 'id': item_id, 'text': 'fact 1 now', 'source': 'w9'}]},
                                  day=DAY2, sources=src)
    assert report['applied'] == 1
    assert page == before  # the input page is never mutated
    updated = next(item for item in out['items'] if item['id'] == item_id)
    assert updated['text'] == 'fact 1 now' and updated['source'] == 'w9' and updated['day'] == DAY2
    assert [h['op'] for h in updated['history']] == ['add', 'update']
    assert [h['source'] for h in updated['history']] == ['w1', 'w9']
    # Untouched items are exactly as they were.
    assert [i for i in out['items'] if i['id'] != item_id] == [i for i in before['items'] if i['id'] != item_id]


def test_unknown_item_is_refused(page):
    page = filled(page, 1)
    src = sources(w9=(DAY2, 'x'))
    out, report = tp.apply_answer(page, {'ops': [{'op': 'remove', 'id': 'nope', 'reason': 'r', 'source': 'w9'}]},
                                  day=DAY2, sources=src)
    assert [r['code'] for r in report['refused']] == ['unknown_item']
    assert out == page


@pytest.mark.parametrize('answer', [
    {'page': '# Garden\n- everything new'},
    {'items': [{'section': 'state', 'text': 'new'}]},
    '# Garden\n- a whole page as text',
])
def test_a_whole_page_answer_is_refused(page, answer):
    page = filled(page, 2)
    out, report = tp.apply_answer(page, answer, day=DAY2, sources=sources(w9=(DAY2, 'x')))
    assert [r['code'] for r in report['refused']] == ['rewrite']
    assert report['applied'] == 0 and out == page


def test_removing_most_items_at_once_is_a_rewrite_and_changes_nothing(page):
    page = filled(page, 4)
    src = sources(w9=(DAY2, 'start over'))
    ops = [{'op': 'remove', 'id': item['id'], 'reason': 'redo', 'source': 'w9'} for item in page['items']]
    ops += [add('state', f'new {i}', 'w9') for i in range(4)]
    out, report = tp.apply_answer(page, {'ops': ops}, day=DAY2, sources=src)
    assert [r['code'] for r in report['refused']] == ['rewrite']
    assert report['applied'] == 0 and out == page


def test_changing_up_to_half_of_the_items_is_allowed(page):
    page = filled(page, 4)
    src = sources(w9=(DAY2, 'two changed'))
    ops = [{'op': 'update', 'id': page['items'][0]['id'], 'text': 'a', 'source': 'w9'},
           {'op': 'remove', 'id': page['items'][1]['id'], 'reason': 'ended', 'source': 'w9'}]
    out, report = tp.apply_answer(page, {'ops': ops}, day=DAY2, sources=src)
    assert report['applied'] == 2 and not report['refused']
    assert len(out['items']) == 3


# --- the held-out request never leaks into its own page -----------------------------------------------------------

def greedy_judge(calls):
    """A fake judgment that adds one item for every Work it is shown."""
    def judge(day, payload):
        calls.append((day, {topic: [w['id'] for w in data['works']] for topic, data in payload.items()}))
        return {topic: {'ops': [add('state', f"saw {w['id']}", w['id']) for w in data['works']]}
                for topic, data in payload.items()}
    return judge


def works_fixture():
    return [
        {'id': 'a1', 'day': DAY1, 'owner_text': 'tomato watering', 'reply': 'ok', 'topics': ['t1']},
        {'id': 'a2', 'day': DAY2, 'owner_text': 'tomato blight', 'reply': 'ok', 'topics': ['t1']},
        {'id': 'held', 'day': DAY3, 'owner_text': 'tomato harvest plan', 'reply': 'ok', 'topics': ['t1']},
        {'id': 'b1', 'day': DAY2, 'owner_text': 'unrelated', 'reply': 'ok', 'topics': []},
    ]


def test_replay_calls_once_per_active_day_and_never_shows_a_later_day():
    calls = []
    topics = [{'id': 't1', 'name': 'Garden', 'summary': 'garden'}]
    result = te_replay(topics, works_fixture(), greedy_judge(calls))
    assert [day for day, _ in calls] == [DAY1, DAY2, DAY3]
    for day, shown in calls:
        assert all(next(w for w in works_fixture() if w['id'] == wid)['day'] == day for wid in shown['t1'])
    assert result['calls'] == 3


def te_replay(topics, works, judge):
    return tp.replay(topics, works, judge)


def replay_two_topics():
    topics = [{'id': topic, 'name': topic, 'summary': ''} for topic in ('t1', 't2')]
    works = [
        {'id': work, 'day': DAY1, 'owner_text': text, 'reply': '', 'topics': [topic]}
        for work, text, topic in (('w1', 'first detail', 't1'), ('w2', 'second detail', 't2'))
    ]
    return topics, works


def test_replay_does_not_retry_a_duplicate_add():
    topics, works = replay_two_topics()
    first = add('state', 'first detail', 'w1')
    retry_calls = []

    def judge(day, payload):
        return {'t1': {'ops': [first, first]}, 't2': {'ops': [add('state', 'second detail', 'w2')]}}

    result = tp._replay(topics, works, judge, retry=lambda *args: retry_calls.append(args))
    assert retry_calls == []
    assert result['calls'] == 1
    assert result['applied'] == 2
    assert result['refused'] == {'bad_op': 1}
    assert [tp.page_at(result['snapshots'][topic], DAY2)['items'][0]['source']
            for topic in ('t1', 't2')] == ['w1', 'w2']


def test_replay_retries_only_the_topic_whose_answer_was_refused():
    topics, works = replay_two_topics()
    first = add('state', 'first detail', 'w1')
    retry_calls = []

    def judge(day, payload):
        return {'t1': {'page': 'replacement'}, 't2': {'ops': [add('state', 'second detail', 'w2')]}}

    def retry(day, payload, codes):
        retry_calls.append((day, set(payload), codes))
        return {'t1': {'ops': [first, first]}}

    result = tp._replay(topics, works, judge, retry=retry)
    assert retry_calls == [(DAY1, {'t1'}, ['rewrite'])]
    assert result['calls'] == 2
    assert result['applied'] == 2
    assert result['refused'] == {'rewrite': 1, 'bad_op': 1}
    assert [tp.page_at(result['snapshots'][topic], DAY2)['items'][0]['source']
            for topic in ('t1', 't2')] == ['w1', 'w2']


def test_replay_counts_capped_retries_and_preserves_accepted_topics():
    topics, works = replay_two_topics()
    retry_calls = []

    def judge(day, payload):
        return {'t2': {'ops': [add('state', 'second detail', 'w2')]}}

    result = tp._replay(topics, works, judge, retry=lambda *args: retry_calls.append(args), max_retries=0)
    assert retry_calls == []
    assert result['applied'] == 1
    assert result['refused'] == {'bad_op': 1, 'retry_capped': 1}
    assert not tp.page_at(result['snapshots']['t1'], DAY2)['items']
    assert tp.page_at(result['snapshots']['t2'], DAY2)['items'][0]['source'] == 'w2'


def test_replay_resumes_a_pending_retry_without_reapplying_the_first_answer():
    topics, works = replay_two_topics()
    checkpoints = []

    def judge(day, payload):
        return {'t2': {'ops': [add('state', 'second detail', 'w2')]}}

    def budget_exhausted(*args):
        raise te.BudgetExceeded('budget_exceeded')

    with pytest.raises(te.BudgetExceeded):
        tp._replay(topics, works, judge, retry=budget_exhausted, fatal_errors=(te.BudgetExceeded,),
                   checkpoint=lambda state: checkpoints.append(copy.deepcopy(state)), max_retries=1)

    def retry(day, payload, codes):
        assert set(payload) == {'t1'}
        assert codes == ['bad_op']
        return {'t1': {'ops': [add('state', 'first detail', 'w1')]}}

    result = tp._replay(topics, works, lambda *_: pytest.fail('first answer already saved'),
                        retry=retry, resume=checkpoints[-1], max_retries=1)
    assert result['retries'] == 1 and result['calls'] == 2
    assert result['applied'] == 2
    assert result['refused'] == {'bad_op': 1}
    assert [len(tp.page_at(result['snapshots'][topic], DAY2)['items']) for topic in ('t1', 't2')] == [1, 1]


def test_page_for_a_held_out_request_is_the_start_of_its_day():
    topics = [{'id': 't1', 'name': 'Garden', 'summary': 'garden'}]
    result = tp.replay(topics, works_fixture(), greedy_judge([]))
    request = {'id': 'held', 'day': DAY3}
    page = tp.page_at(result['snapshots']['t1'], DAY3)
    cited = {item['source'] for item in page['items']}
    assert cited == {'a1', 'a2'}  # earlier days only: never the request, never its own day
    tp.assert_no_leak(page, request)
    assert tp.page_at(result['snapshots']['t1'], DAY1) is None  # nothing before the first day


def test_leak_check_refuses_a_page_citing_the_request_or_its_day(page):
    src = sources(held=(DAY3, 'tomato harvest plan'), same=(DAY3, 'same day'))
    leaked, _ = tp.apply_answer(page, {'ops': [add('state', 'harvest', 'held')]}, day=DAY3, sources=src)
    with pytest.raises(tp.LeakError):
        tp.assert_no_leak(leaked, {'id': 'held', 'day': DAY3})
    same_day, _ = tp.apply_answer(page, {'ops': [add('state', 'x', 'same')]}, day=DAY3, sources=src)
    with pytest.raises(tp.LeakError):
        tp.assert_no_leak(same_day, {'id': 'held', 'day': DAY3})


def test_leak_check_also_reads_item_history(page):
    src = sources(held=(DAY3, 'harvest'), a1=(DAY1, 'water'))
    early, _ = tp.apply_answer(page, {'ops': [add('state', 'water', 'a1')]}, day=DAY1, sources=src)
    item = copy.deepcopy(early)
    item['items'][0]['history'].append({'op': 'update', 'day': DAY3, 'source': 'held'})
    with pytest.raises(tp.LeakError):
        tp.assert_no_leak(item, {'id': 'held', 'day': DAY3})


def test_finder_listing_excludes_the_held_out_window():
    works = works_fixture()
    listed = tp.before_window(works, DAY3)
    assert {w['id'] for w in listed} == {'a1', 'a2', 'b1'}


# --- decision rule ------------------------------------------------------------------------------------------------

def run(a, b, c, mis_a=1.0, mis_b=1.0, mis_c=1.0):
    return {'A': {'needed': a, 'misleading': mis_a}, 'B': {'needed': b, 'misleading': mis_b},
            'C': {'needed': c, 'misleading': mis_c}}


def test_decide_integrates_only_beyond_noise_without_more_misleading():
    ok_c = (.08, .20)
    clear = {'runs': [run(.50, .70, .55), run(.52, .71, .56), run(.49, .69, .57)],
             'ci_b_minus_a': (.12, .25), 'ci_b_minus_c': ok_c}
    assert tp.decide(clear)['integrate'] is True
    noisy = {'runs': [run(.50, .55, .55), run(.60, .58, .55), run(.45, .56, .55)],
             'ci_b_minus_a': (-.05, .12), 'ci_b_minus_c': ok_c}
    assert tp.decide(noisy)['integrate'] is False
    misleading = {'runs': [run(.50, .70, .55, mis_b=2.0)] * 3, 'ci_b_minus_a': (.12, .25), 'ci_b_minus_c': ok_c}
    out = tp.decide(misleading)
    assert out['integrate'] is False and 'more_misleading' in out['reasons']
    search_wins = {'runs': [run(.50, .70, .80)] * 3, 'ci_b_minus_a': (.12, .25), 'ci_b_minus_c': (-.15, -.05)}
    out = tp.decide(search_wins)
    assert out['integrate'] is False and 'search_not_worse' in out['reasons']
    # A tie with search goes to search, the cheaper change.
    tie = {'runs': [run(.50, .70, .69)] * 3, 'ci_b_minus_a': (.12, .25), 'ci_b_minus_c': (-.04, .06)}
    out = tp.decide(tie)
    assert out['integrate'] is False and out['reasons'] == ['search_not_worse']


def test_early_stop_after_two_losing_runs_or_a_gain_too_small_to_matter():
    losing = [run(.60, .55, .60, mis_b=2.0), run(.62, .58, .61, mis_b=1.5)]
    assert tp.early_stop(losing) is True
    assert tp.early_stop(losing[:1]) is False
    mixed = [run(.60, .65, .60), run(.62, .58, .61, mis_b=1.5)]
    assert tp.early_stop(mixed) is False
    assert tp.early_stop(mixed, ci=(-.02, tp.MIN_GAIN - .01)) is True
    assert tp.early_stop(mixed, ci=(-.02, tp.MIN_GAIN + .05)) is False


def test_bootstrap_averages_runs_per_request_first():
    deltas = {f'r{i}': [d] for i, d in enumerate([.3, -.1, .2, .0, .1, .25, -.05, .15])}
    tripled = {k: v * 3 for k, v in deltas.items()}
    assert tp.bootstrap_delta(deltas) == tp.bootstrap_delta(tripled)
    lo, hi = tp.bootstrap_delta(deltas)
    assert lo < sum(v[0] for v in deltas.values()) / len(deltas) < hi


def test_bootstrap_can_resample_whole_topics():
    flat = {f'r{i}': [d] for i, d in enumerate([.3, .3, .3, -.2, -.2, -.2])}
    clustered = {k: ('t1' if i < 3 else 't2', v) for i, (k, v) in enumerate(flat.items())}
    flat_lo, flat_hi = tp.bootstrap_delta(flat)
    lo, hi = tp.bootstrap_delta(clustered)
    assert hi - lo >= flat_hi - flat_lo  # two clusters carry less information than six requests


def test_judge_support_must_be_in_the_context():
    context = 'The owner prefers   morning\nwalks by the river.'
    assert tp.check_support('yes', 'prefers morning walks', context) == ('yes', False)
    assert tp.check_support('yes', 'prefers evening walks', context) == ('partial', True)
    assert tp.check_support('partial', '', context) == ('no', True)
    assert tp.check_support('no', '', context) == ('no', False)


def test_short_ref_is_short_and_survives_redaction():
    work_id = '3f2a9c1e-7b4d-4e8a-9c1f-2b3d4e5f6a7b'
    ref = tp.short_ref(work_id)
    assert ref == '3f2a9c1e' and te.redact(f'work:{ref}') == f'work:{ref}'
    assert te.redact(work_id) != work_id  # why prompts never carry the full id


# --- store-backed pieces: membership, budget, privacy -------------------------------------------------------------

def add_work(store, request, answer, *, at, typed=True, relation=None, related=None):
    job = store.enqueue(request, f'key-{at}-{request[:8]}', owner_typed=typed, owner_words=typed, channel='web')
    with store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded', created=?, source_at=?, response=?, relation_kind=?, "
                   'related_job_id=? WHERE id=?', (at, at, answer, relation, related, job))
        db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                   ('user', request, 'web', at, job))
        db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                   ('assistant', answer, 'web', at + 1, job))
    return job


def test_members_use_fts_on_owner_words_and_follow_relations(tmp_path):
    store = QuickStore(tmp_path / 'state')
    garden = add_work(store, 'how often should the tomatoes get water', 'every morning', at=1000)
    follow = add_work(store, 'no, less', 'ok, every other day', at=2000, relation='correction', related=garden)
    other = add_work(store, 'book a train', 'done', at=3000)
    not_owner = add_work(store, 'tomatoes report', 'scheduled', at=4000, typed=False)
    reply_only = add_work(store, 'what next', 'the tomatoes need staking', at=5000)
    members = te.topic_members(store, [{'id': 't1', 'terms': ['tomatoes', 'watering']}])
    assert members['t1'] == {garden, follow}
    assert other not in members['t1'] and not_owner not in members['t1'] and reply_only not in members['t1']


def test_call_budget_refuses_once_spent_and_survives_restart(tmp_path):
    budget = te.CallBudget(tmp_path / 'calls.jsonl', max_calls=2)
    budget.take('codex')
    budget.take('claude')
    with pytest.raises(te.BudgetExceeded):
        budget.take('codex')
    again = te.CallBudget(tmp_path / 'calls.jsonl', max_calls=2)
    with pytest.raises(te.BudgetExceeded):
        again.take('codex')


def test_call_budget_enforces_cap_across_threads(tmp_path):
    path = tmp_path / 'calls.jsonl'

    def take(_):
        try:
            te.CallBudget(path, max_calls=2).take('claude')
            return True
        except te.BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(take, range(9))) == 2
    assert len(path.read_text().splitlines()) == 2


def test_parallel_model_items_retry_only_invalid_outputs_and_save_skips():
    attempts = Counter()
    saved = {}

    def action(item):
        attempts[item] += 1
        if item == 'unavailable':
            raise te.ModelError('model_failed')
        if item == 'invalid' or (item == 'recoverable' and attempts[item] == 1):
            raise te.ModelError('invalid_model_output')
        return item

    te._model_items(3, ('invalid', 'recoverable', 'valid', 'unavailable'), action,
                    saved.__setitem__, retry_invalid=True)
    assert attempts == {'invalid': 2, 'recoverable': 2, 'valid': 1, 'unavailable': 1}
    assert saved == {'invalid': None, 'recoverable': 'recoverable', 'valid': 'valid', 'unavailable': None}


def test_weak_requests_are_reported_without_affecting_primary_metrics():
    requests = {'strong': {'weak': False, 'topic_id': 't1'}, 'weak': {'weak': True, 'topic_id': 't1'}}
    gold = {key: {'needed': [{'weight': 'essential'}], 'lessons': []} for key in requests}

    def judgment(verdict, misleading=()):
        return {'needed': [verdict], 'traps': [], 'misleading_other': misleading, 'lessons': [],
                'lesson_present': [], 'downgraded': 0}

    judged = {
        'strong': {'A': judgment('no'), 'B': judgment('yes'), 'C': judgment('no')},
        'weak': {'A': judgment('yes'), 'B': judgment('no', ['wrong']), 'C': judgment('yes')},
    }
    scores = te._run_scores(judged, gold, requests, {})
    assert scores['A']['needed'] == scores['A']['essential'] == 0
    assert scores['A']['needed_weak'] == 1
    assert scores['B']['needed'] == scores['B']['essential'] == 1
    assert scores['B']['needed_weak'] == 0
    assert scores['B']['misleading'] == 0.5
    assert scores['B']['misleading_weak'] == 1
    run = dict(scores, groups=te._group_counts(judged, gold, requests))
    assert te._decision_runs([run])[0]['B']['misleading'] == 0
    deltas = te._paired_deltas([judged], requests)
    assert deltas['B_minus_A'] == {'mean': 1, 'ci95': (1, 1), 'pairs': 1}


def test_generated_report_matches_counts_only_schema(tmp_path):
    schema = json.loads((ROOT / 'evals/topics/schema.json').read_text())
    types = {'object': (dict,), 'array': (list,), 'integer': (int,),
             'number': (int, float), 'boolean': (bool,), 'null': (type(None),)}

    def matches(value, rule):
        if '$ref' in rule:
            return matches(value, schema['$defs'][rule['$ref'].removeprefix('#/$defs/')])
        if 'anyOf' in rule:
            return any(matches(value, choice) for choice in rule['anyOf'])
        if 'enum' in rule:
            return any(type(value) is type(choice) and value == choice for choice in rule['enum'])
        if type(value) not in types[rule['type']]:
            return False
        if isinstance(value, dict):
            properties = rule.get('properties', {})
            extra = rule.get('additionalProperties', False)
            return set(rule.get('required', ())) <= value.keys() and all(
                ('propertyNames' not in rule or matches(key, rule['propertyNames']))
                and (matches(item, properties[key]) if key in properties
                     else bool(extra) and matches(item, extra))
                for key, item in value.items()
            )
        if isinstance(value, list):
            return (rule.get('minItems', 0) <= len(value) <= rule.get('maxItems', len(value))
                    and all(matches(item, rule['items']) for item in value))
        if type(value) in (int, float):
            return rule.get('minimum', value) <= value <= rule.get('maximum', value)
        return True

    args = SimpleNamespace(out=tmp_path, heldout_from=DAY2, max_calls=400, max_retries=10)
    requests = [
        {'id': work_id, 'topic_id': 't1', 'weak': weak, 'day': DAY2,
         'relation_kind': None, 'owner_text': 'synthetic request'}
        for work_id, weak in [('strong', False), ('weak', True), ('missed-gold', False), ('missed-judge', False)]
    ]
    te._save(tmp_path / 'heldout.json', {'heldout_from': DAY2, 'requests': requests})
    labels = {'needed': [{'weight': 'essential'}], 'lessons': [1]}
    te._save_stage(tmp_path / 'gold.json', {key: labels for key in ('strong', 'weak', 'missed-judge')},
                   {'missed-gold'})
    for run in (1, 2):
        folder = tmp_path / f'run{run}'
        te._save(folder / 'pages.json', {'applied': 2, 'calls': 3, 'refused': {'retry_capped': 1}})
        judged = {
            work_id: {
                arm: {'needed': [mark], 'traps': [], 'misleading_other': [], 'downgraded': 0,
                      'lesson_present': ['yes'], 'lessons': ['followed'] if run == 1 and arm != 'C' else []}
                for arm, mark in zip(te.ARMS, ('no', 'yes', 'partial'))
            }
            for work_id in ('strong', 'weak')
        }
        te._save_stage(folder / 'judged.json', judged, {'missed-judge'})
        te._save(folder / 'contexts.json', {key: {'chars': {'A': 20, 'B': 30, 'C': 29}} for key in judged})
    te._save_stage(tmp_path / 'run1/drafts.json', {}, {'missed-judge/A'})
    budget = te.CallBudget(tmp_path / 'calls.jsonl', max_calls=args.max_calls)
    budget.take('claude')
    budget.record('claude', seconds=0.5, cost_usd=0.02)

    te._report(args, None, requests)
    report = json.loads((tmp_path / 'report.json').read_text())
    assert matches(report, schema['$defs']['report'])
    assert report['early_stop_ci_b_minus_a'] == [1, 1]
    report['runs'][0]['A']['owner_text'] = 'synthetic text'
    assert not matches(report, schema['$defs']['report'])


def test_output_folder_must_be_outside_the_repository(tmp_path):
    with pytest.raises(ValueError):
        te.private_out(ROOT / 'evals' / 'topics' / 'out')
    assert te.private_out(tmp_path / 'out') == (tmp_path / 'out').resolve()


def test_text_for_a_model_is_redacted():
    token = 'sk-proj-' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4'
    assert token not in te.redact(f'my key is {token} thanks')


def test_topics_are_kept_by_days_before_the_held_out_window():
    members_days = {'t1': ['2026-11-01', '2026-11-02', '2026-11-03', '2026-11-04', '2026-11-12', '2026-11-13'],
                    't2': ['2026-11-01', '2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05']}
    assert te.kept_topics(members_days, '2026-11-10') == ['t2']


def test_match_strength_counts_distinct_terms_by_prefix():
    assert te.match_strength(['tomato', 'garden'], 'the tomatoes in my garden') == 2
    assert te.match_strength(['tomato', 'garden'], 'tomato tomato') == 1
    assert te.match_strength(['토마토가'], '토마토 물주기') == 1
    assert te.match_strength(['tomato'], 'a train') == 0


def test_transcript_block_never_returns_the_day_or_later(tmp_path):
    import datetime
    store = QuickStore(tmp_path / 'state')
    def at(day, hour):
        return datetime.datetime.strptime(f'{day} {hour:02d}', '%Y-%m-%d %H').timestamp()
    earlier = add_work(store, 'tomatoes need water daily', 'noted', at=at(DAY1, 9))
    same_day = add_work(store, 'tomatoes look yellow', 'checking', at=at(DAY3, 8))
    later = add_work(store, 'tomatoes harvested', 'great', at=at(DAY3, 20) + 86400)
    text, works = te.transcript_block(store, ['tomatoes'], day=DAY3, target_chars=5000)
    assert works == [earlier]
    assert same_day not in text and later not in text
    none, works = te.transcript_block(store, ['tomatoes'], day=DAY3, exclude_works={earlier}, target_chars=5000)
    assert works == [] and none == ''
