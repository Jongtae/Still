"""#1046 topic-page revision: synthetic fixtures and fake judgments only, no live data, no model, no network.

#1043 found that topic pages raised needed facts but also misleading items, and the misleading items came from stale
"latest state" entries. These tests pin the revised page rules: standards and decisions come first, a state item has an
as-of date and an optional expiry and is not shown as current once it expires, only the newest state item per thread
is shown, a weak match gets no page, and the pre-registered rule needs B to beat both A and search without more traps
or other misleading items.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
sys.path.insert(0, str(ROOT / 'evals' / 'lessons'))
sys.path.insert(0, str(ROOT / 'evals' / 'topics'))

import topic_pages as tp  # noqa: E402
import topics_eval as te  # noqa: E402

DAY1, DAY2, DAY3, DAY4 = '2026-11-01', '2026-11-02', '2026-11-03', '2026-11-04'


def sources(**works):
    return {work_id: {'day': day, 'owner_text': text} for work_id, (day, text) in works.items()}


def state(text, source, *, thread=None, expires=None):
    op = {'op': 'add', 'section': 'state', 'text': text, 'source': source}
    if thread is not None:
        op['thread'] = thread
    if expires is not None:
        op['expires'] = expires
    return op


@pytest.fixture
def page():
    return tp.new_page('t1', 'Garden', 'The vegetable garden and its watering')


def apply(page, ops, day, **works):
    out, report = tp.apply_answer(page, {'ops': ops}, day=day, sources=sources(**works))
    return out, report


# --- state items: as-of date, expiry, thread -----------------------------------------------------------------------

def test_state_item_keeps_thread_and_expiry(page):
    out, report = apply(page, [state('Seedlings on the sill', 'w1', thread='seedlings', expires=DAY3)],
                        DAY1, w1=(DAY1, 'seedlings are on the sill'))
    assert report['applied'] == 1 and not report['refused']
    item = out['items'][0]
    assert item['thread'] == 'seedlings' and item['expires'] == DAY3 and item['day'] == DAY1


def test_state_item_without_thread_or_expiry_is_still_accepted(page):
    out, report = apply(page, [state('Hose repaired', 'w1')], DAY1, w1=(DAY1, 'fixed the hose'))
    assert report['applied'] == 1
    assert out['items'][0]['expires'] is None


@pytest.mark.parametrize('expires', ['next week', '2026-13-40', '11/03/2026'])
def test_an_expiry_that_is_not_a_date_is_refused(page, expires):
    out, report = apply(page, [state('x', 'w1', expires=expires)], DAY1, w1=(DAY1, 'x'))
    assert [r['code'] for r in report['refused']] == ['bad_op']
    assert out['items'] == []


def test_update_keeps_thread_and_expiry_unless_given(page):
    page, _ = apply(page, [state('Two beds dug', 'w1', thread='beds', expires=DAY4)], DAY1, w1=(DAY1, 'dug two'))
    item_id = page['items'][0]['id']
    page, report = apply(page, [{'op': 'update', 'id': item_id, 'text': 'Three beds dug', 'source': 'w2'}],
                         DAY2, w2=(DAY2, 'dug a third'))
    assert report['applied'] == 1
    assert page['items'][0]['thread'] == 'beds' and page['items'][0]['expires'] == DAY4
    page, _ = apply(page, [{'op': 'update', 'id': item_id, 'text': 'Beds done', 'source': 'w3', 'expires': ''}],
                    DAY3, w3=(DAY3, 'all done'))
    assert page['items'][0]['expires'] == DAY4  # an empty field means unchanged, never a cleared expiry


def test_expired_state_is_not_shown_as_current(page):
    page, _ = apply(page, [state('Watering paused until the rain stops', 'w1', thread='watering', expires=DAY2),
                           state('Compost turned', 'w1', thread='compost')], DAY1, w1=(DAY1, 'paused; compost'))
    shown = tp.render(page, as_of=DAY3)
    assert 'Watering paused' not in shown and 'Compost turned' in shown
    assert '1 older state item' in shown  # the count is stated, never silently dropped
    still_valid = tp.render(page, as_of=DAY2)  # the expiry day itself is still in force
    assert 'Watering paused' in still_valid


def test_only_the_newest_state_item_per_thread_is_shown(page):
    page, _ = apply(page, [state('Four tomato plants', 'w1', thread='Tomato plants')], DAY1, w1=(DAY1, 'four'))
    page, _ = apply(page, [state('Six tomato plants', 'w2', thread='  tomato   PLANTS ')], DAY2, w2=(DAY2, 'six'))
    page, _ = apply(page, [state('Basil sown', 'w3', thread='basil')], DAY3, w3=(DAY3, 'basil'))
    shown = tp.render(page, as_of=DAY4)
    assert 'Six tomato plants' in shown and 'Four tomato plants' not in shown and 'Basil sown' in shown
    assert '1 older state item' in shown


def test_items_without_a_thread_never_supersede_each_other(page):
    page, _ = apply(page, [state('Hose repaired', 'w1'), state('Fence painted', 'w1')], DAY1, w1=(DAY1, 'both'))
    shown = tp.render(page, as_of=DAY2)
    assert 'Hose repaired' in shown and 'Fence painted' in shown
    assert 'older state item' not in shown


def test_state_line_shows_as_of_date_and_expiry(page):
    page, _ = apply(page, [state('Seedlings on the sill', 'w1', thread='seedlings', expires=DAY3)],
                    DAY1, w1=(DAY1, 'sill'))
    line = next(line for line in tp.render(page, as_of=DAY2).splitlines() if 'Seedlings' in line)
    assert f'as of {DAY1}' in line and f'until {DAY3}' in line and 'work:w1' in line


def test_standards_and_decisions_come_first_and_win_the_budget(page):
    src = sources(w1=(DAY1, 'Never spray anything on the herbs'), w2=(DAY2, 'notes'))
    page, _ = tp.apply_answer(page, {'ops': [
        {'op': 'add', 'section': 'standard', 'text': 'No spray on herbs', 'source': 'w1',
         'quote': 'Never spray anything on the herbs'},
        {'op': 'add', 'section': 'decision', 'text': 'Drip line chosen over sprinklers', 'source': 'w1'},
    ]}, day=DAY1, sources=src)
    page, _ = tp.apply_answer(page, {'ops': [
        state(f'Newer state note number {i} with some words', 'w2', thread=f'n{i}') for i in range(30)
    ]}, day=DAY2, sources=src)
    shown = tp.render(page, budget=600, as_of=DAY3)
    assert len(shown) <= 600
    lines = shown.splitlines()
    standard = next(i for i, line in enumerate(lines) if 'No spray on herbs' in line)
    decision = next(i for i, line in enumerate(lines) if 'Drip line chosen' in line)
    first_state = next(i for i, line in enumerate(lines) if 'Newer state note' in line)
    assert standard < decision < first_state
    assert 'omitted' in shown  # state items cut by the budget are counted


def test_builder_view_shows_thread_and_expiry_with_ids(page):
    page, _ = apply(page, [state('Seedlings on the sill', 'w1', thread='seedlings', expires=DAY3)],
                    DAY1, w1=(DAY1, 'sill'))
    shown = tp.render(page, include_ids=True, as_of=DAY2)
    item_id = page['items'][0]['id']
    assert f'[{item_id}]' in shown and 'thread: seedlings' in shown and f'until {DAY3}' in shown


def test_a_page_never_rewritten_whole_still_holds(page):
    page, _ = apply(page, [state(f'fact {i}', 'w1', thread=f't{i}') for i in range(4)], DAY1, w1=(DAY1, 'x'))
    ops = [{'op': 'remove', 'id': item['id'], 'reason': 'old', 'source': 'w2'} for item in page['items']]
    out, report = apply(page, ops, DAY2, w2=(DAY2, 'y'))
    assert report['refused'] == [{'index': None, 'code': 'rewrite'}] and out == page


# --- weak matches get no page ----------------------------------------------------------------------------------

def test_a_weak_match_gets_no_page():
    assert tp.attach_page(0) is False
    assert tp.attach_page(1) is False
    assert tp.attach_page(2) is True


def test_without_a_page_b_and_c_equal_a():
    arms = te.arm_texts('brief', 'page text', 'search text', attach=False)
    assert arms == {'A': 'brief', 'B': 'brief', 'C': 'brief'}
    arms = te.arm_texts('brief', 'page text', 'search text', attach=True)
    assert arms == {'A': 'brief', 'B': 'brief\n\npage text', 'C': 'brief\n\nsearch text'}
    assert te.arm_texts('brief', '', '', attach=True) == {'A': 'brief', 'B': 'brief', 'C': 'brief'}


# --- an uninspected time split ---------------------------------------------------------------------------------

def test_held_out_window_has_an_end():
    works = [{'id': f'w{i}', 'day': day} for i, day in enumerate((DAY1, DAY2, DAY3, DAY4))]
    assert [w['id'] for w in tp.in_window(works, DAY2, DAY4)] == ['w1', 'w2']
    assert [w['id'] for w in tp.in_window(works, DAY2, None)] == ['w1', 'w2', 'w3']


# --- pre-registered rule against A and the length-matched search ------------------------------------------------

def run(a, b, c, *, traps=(0.2, 0.2, 0.2), other=(0.5, 0.5, 0.5)):
    return {arm: {'needed': needed, 'traps': trap, 'misleading_other': mis}
            for arm, needed, trap, mis in zip('ABC', (a, b, c), traps, other)}


CLEAR = [run(.40, .60, .45), run(.41, .62, .44), run(.39, .59, .46)]


def summary(runs, ci_a=(.10, .25), ci_c=(.08, .20)):
    return {'runs': runs, 'ci_b_minus_a': ci_a, 'ci_b_minus_c': ci_c}


def test_revised_rule_integrates_a_clear_win():
    assert tp.decide_revised(summary(CLEAR)) == {'integrate': True, 'reasons': []}


def test_revised_rule_needs_b_to_beat_search_beyond_noise():
    close = [run(.40, .60, .58), run(.41, .62, .63), run(.39, .59, .57)]
    out = tp.decide_revised(summary(close, ci_c=(-.03, .05)))
    assert out['integrate'] is False
    assert {'not_every_run_vs_c', 'within_noise_vs_c', 'ci_vs_c_includes_zero'} <= set(out['reasons'])


def test_revised_rule_needs_b_to_beat_a_beyond_noise():
    noisy = [run(.40, .45, .40), run(.50, .48, .40), run(.35, .47, .40)]
    out = tp.decide_revised(summary(noisy, ci_a=(-.04, .10)))
    assert out['integrate'] is False
    assert {'not_every_run_vs_a', 'within_noise_vs_a', 'ci_vs_a_includes_zero'} <= set(out['reasons'])


def test_revised_rule_scores_traps_and_other_misleading_separately():
    more_traps = [run(.40, .60, .45, traps=(0.2, 0.5, 0.3))] * 3
    out = tp.decide_revised(summary(more_traps))
    assert out['integrate'] is False and out['reasons'] == ['more_traps']
    more_other = [run(.40, .60, .45, other=(0.5, 0.8, 0.6))] * 3
    out = tp.decide_revised(summary(more_other))
    assert out['integrate'] is False and out['reasons'] == ['more_misleading_other']
    # Fewer traps do not buy back more other misleading items: each is its own condition.
    traded = [run(.40, .60, .45, traps=(0.5, 0.1, 0.3), other=(0.5, 0.7, 0.6))] * 3
    assert tp.decide_revised(summary(traded))['reasons'] == ['more_misleading_other']


def test_revised_rule_holds_with_equal_misleading():
    equal = [run(.40, .60, .45, traps=(0.3, 0.3, 0.3), other=(0.6, 0.6, 0.6))] * 3
    assert tp.decide_revised(summary(equal))['integrate'] is True


def test_revised_rule_refuses_without_runs():
    assert tp.decide_revised(summary([]))['integrate'] is False


def test_run_scores_split_traps_from_other_misleading():
    requests = {'r1': {'weak': False, 'topic_id': 't1'}, 'r2': {'weak': False, 'topic_id': 't1'}}
    gold = {key: {'needed': [{'weight': 'essential'}], 'lessons': []} for key in requests}

    def judgment(traps=(), other=()):
        return {'needed': ['yes'], 'traps': list(traps), 'misleading_other': list(other), 'lessons': [],
                'lesson_present': [], 'downgraded': 0}

    judged = {
        'r1': {'A': judgment(), 'B': judgment(traps=[0], other=['a', 'b']), 'C': judgment(other=['c'])},
        'r2': {'A': judgment(traps=[0]), 'B': judgment(), 'C': judgment()},
    }
    scores = te._run_scores(judged, gold, requests, {})
    assert scores['A']['traps'] == 0.5 and scores['A']['misleading_other'] == 0
    assert scores['B']['traps'] == 0.5 and scores['B']['misleading_other'] == 1
    assert scores['C']['traps'] == 0 and scores['C']['misleading_other'] == 0.5
    decided = te._decision_runs([dict(scores, groups=te._group_counts(judged, gold, requests))])[0]
    assert decided['B']['traps'] == 0.5 and decided['B']['misleading_other'] == 1
