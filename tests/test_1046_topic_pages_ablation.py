"""#1046 next run: a page without its state section (arm D) beside the full page (B), with the judge's spans saved.

Synthetic fixtures and fake judgments only: no live data, no model, no network. These tests pin what the next run is
pre-registered to measure, before any of its data exists: D renders only standards and decisions; every saved span
can be located in the page section, the shared brief or the added text it came from; the choice between B and D
follows the pre-registered rule.
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

DAY1, DAY2, DAY3 = '2026-11-21', '2026-11-22', '2026-11-23'


@pytest.fixture
def page():
    page = tp.new_page('t1', 'Garden', 'The vegetable garden and its watering')
    src = {'w1': {'day': DAY1, 'owner_text': 'Never spray anything on the herbs'}}
    page, report = tp.apply_answer(page, {'ops': [
        {'op': 'add', 'section': 'standard', 'text': 'No spray on herbs', 'source': 'w1',
         'quote': 'Never spray anything on the herbs'},
        {'op': 'add', 'section': 'decision', 'text': 'Drip line chosen over sprinklers', 'source': 'w1'},
        {'op': 'add', 'section': 'state', 'text': 'Six tomato plants in bed two', 'source': 'w1', 'thread': 'tomatoes'},
    ]}, day=DAY1, sources=src)
    assert report['applied'] == 3
    return page


# --- arm D: the page without its state section -------------------------------------------------------------------

def test_page_without_state_keeps_standards_and_decisions_only(page):
    shown = tp.render(page, as_of=DAY2, sections=('standard', 'decision'))
    assert 'No spray on herbs' in shown and 'Drip line chosen' in shown
    assert 'Six tomato plants' not in shown and 'latest state' not in shown
    assert 'older state item' not in shown  # nothing about hidden state when state is not part of the page


def test_full_page_is_unchanged_by_the_sections_parameter(page):
    assert tp.render(page, as_of=DAY2) == tp.render(page, as_of=DAY2, sections=tp.SECTIONS)


def test_empty_full_page_keeps_its_header_for_the_builder():
    page = tp.new_page('t1', 'Garden', 'The vegetable garden')
    shown = tp.render(page, include_ids=True, as_of=DAY1)
    assert 'Garden' in shown and 'The vegetable garden' in shown


def test_page_with_no_section_left_renders_nothing():
    page = tp.new_page('t1', 'Garden', 'x')
    page, _ = tp.apply_answer(page, {'ops': [
        {'op': 'add', 'section': 'state', 'text': 'Hose repaired', 'source': 'w1'},
    ]}, day=DAY1, sources={'w1': {'day': DAY1, 'owner_text': 'fixed the hose'}})
    assert tp.render(page, as_of=DAY2, sections=('standard', 'decision')) == ''


def test_four_arm_contexts_share_one_brief():
    arms = te.arm_texts('brief', 'full page', 'search text', attach=True, page_without_state='short page')
    assert arms == {'A': 'brief', 'B': 'brief\n\nfull page', 'C': 'brief\n\nsearch text', 'D': 'brief\n\nshort page'}
    arms = te.arm_texts('brief', 'full page', 'search text', attach=True, page_without_state='')
    assert arms['D'] == 'brief'
    assert te.arm_texts('brief', 'full page', 'search text', attach=True) == {
        'A': 'brief', 'B': 'brief\n\nfull page', 'C': 'brief\n\nsearch text'}


def test_the_judge_sees_one_label_per_arm():
    assert te.judge_labels(('A', 'B', 'C')) == ('X1', 'X2', 'X3')
    assert te.judge_labels(('A', 'B', 'C', 'D')) == ('X1', 'X2', 'X3', 'X4')
    schema = te.judge_schema(('A', 'B', 'C', 'D'))
    contexts = schema['properties']['contexts']
    assert contexts['minItems'] == contexts['maxItems'] == 4


# --- spans are saved and can be located ---------------------------------------------------------------------------

def test_a_span_is_located_in_its_page_section(page):
    page_text = tp.render(page, as_of=DAY2)
    brief = 'profile: lives near the river\nrecall: asked about compost last week'
    context = f'{brief}\n\n{page_text}'
    assert tp.locate('No spray on herbs', context, brief, page_text) == 'standard'
    assert tp.locate('Drip line chosen', context, brief, page_text) == 'decision'
    assert tp.locate('Six tomato   plants', context, brief, page_text) == 'state'  # whitespace-insensitive
    assert tp.locate('asked about compost', context, brief, page_text) == 'brief'
    assert tp.locate('not anywhere', context, brief, page_text) == 'none'
    assert tp.locate('', context, brief, page_text) == 'none'


def test_a_span_in_added_search_text_is_located_as_added():
    brief = 'profile: lives near the river'
    search = 'earlier exchange: the owner moved the hose'
    context = f'{brief}\n\n{search}'
    assert tp.locate('moved the hose', context, brief, '') == 'added'


def test_judged_entries_keep_spans_without_changing_the_counts():
    gold = {'needed': [{'fact': 'f1', 'weight': 'essential'}, {'fact': 'f2', 'weight': 'helpful'}],
            'traps': [{'statement': 't1', 'why': 'old'}], 'lessons': [], 'lesson_list': []}
    contexts = {'A': 'alpha text', 'B': 'alpha text\n\nbeta page'}
    raw = {
        'needed': [{'verdict': 'yes', 'support': 'beta page'}, {'verdict': 'yes', 'support': 'invented span'}],
        'traps': [{'number': 1, 'support': 'beta page'}],
        'misleading_other': [{'statement': 'an old plan', 'support': 'alpha text'}],
        'lesson_present': [], 'lessons': [],
    }
    entry = te.judged_entry(raw, gold, contexts['B'])
    assert entry['needed'] == ['yes', 'partial'] and entry['downgraded'] == 1  # the invented span is downgraded
    assert entry['support'] == ['beta page', 'invented span']
    assert entry['traps'] == [1] and entry['trap_support'] == ['beta page']
    assert entry['misleading_other'] == ['an old plan'] and entry['misleading_support'] == ['alpha text']


def test_a_trap_number_outside_the_list_is_still_refused():
    gold = {'needed': [], 'traps': [{'statement': 't1', 'why': 'old'}], 'lessons': [], 'lesson_list': []}
    raw = {'needed': [], 'traps': [{'number': 2, 'support': 'x'}], 'misleading_other': [],
           'lesson_present': [], 'lessons': []}
    with pytest.raises(te.ModelError):
        te.judged_entry(raw, gold, 'x')


# --- the pre-registered choice between B and D ----------------------------------------------------------------------

def run(a, b, c, d, *, traps=(0.4, 0.4, 0.4, 0.4), other=(0.6, 0.6, 0.6, 0.6)):
    return {arm: {'needed': needed, 'traps': trap, 'misleading_other': mis}
            for arm, needed, trap, mis in zip('ABCD', (a, b, c, d), traps, other)}


def summary(runs, **cis):
    base = {'ci_b_minus_a': (.10, .25), 'ci_b_minus_c': (.08, .20), 'ci_d_minus_a': (.08, .20),
            'ci_d_minus_c': (.06, .15), 'ci_b_minus_d': (-.03, .08)}
    base.update(cis)
    return dict(base, runs=runs)


CLEAR = [run(.40, .60, .45, .57), run(.41, .62, .44, .58), run(.39, .59, .46, .56)]


def test_both_pass_and_b_not_clearly_better_chooses_the_smaller_page():
    out = tp.decide_ablation(summary(CLEAR))
    assert out['B']['integrate'] and out['D']['integrate']
    assert out['choice'] == 'D'


def test_both_pass_and_b_clearly_better_chooses_the_full_page():
    out = tp.decide_ablation(summary(CLEAR, ci_b_minus_d=(.01, .08)))
    assert out['choice'] == 'B'


def test_full_page_fails_on_traps_and_page_without_state_passes():
    runs = [run(.40, .60, .45, .57, traps=(0.4, 0.6, 0.4, 0.4))] * 3
    out = tp.decide_ablation(summary(runs))
    assert out['B']['reasons'] == ['more_traps'] and out['D']['integrate']
    assert out['choice'] == 'D'


def test_page_without_state_must_beat_search_too():
    runs = [run(.40, .60, .45, .45)] * 3
    out = tp.decide_ablation(summary(runs, ci_d_minus_c=(-.04, .05)))
    assert not out['D']['integrate'] and 'within_noise_vs_c' in out['D']['reasons']
    assert out['choice'] == 'B'


def test_neither_passes_means_no_integration():
    runs = [run(.40, .60, .45, .57, traps=(0.4, 0.6, 0.4, 0.5))] * 3
    out = tp.decide_ablation(summary(runs))
    assert out['choice'] is None


def test_no_runs_means_no_integration():
    assert tp.decide_ablation(summary([]))['choice'] is None


def test_replay_cap_covers_the_history_through_the_next_window():
    assert te.MAX_REPLAY_DAYS >= 75  # 53 active days through 2026-11-19, about 10 more by 2026-11-30
