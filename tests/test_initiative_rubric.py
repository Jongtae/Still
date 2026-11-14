"""Model-free structural evidence for #853; no judge or owner data is used."""
import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval.scenarios import validate  # noqa: E402

FOLDER = ROOT / 'evals' / 'continuity'
KINDS = {'avoidable_interventions': 'turn_list', 'required_approvals': 'turn_list',
         'unnecessary_interruptions': 'message_list', 'proactive_usefulness': 'score'}
ASK_KINDS = {'asked_known_fact', 'asked_entrusted_action'}


def read(name):
    return json.loads((FOLDER / f'{name}-v1.json').read_text())


RUBRIC = read('initiative-rubric')
FIXTURES = read('initiative-judge-fixtures')
LABELS = read('initiative-labels')
CASES = FIXTURES['cases']


def message_ids(run):
    result = set()
    for i, turn in enumerate(run['turns'], 1):
        if turn['answer'] and not turn.get('answer_withheld'):
            result.add(f't{i}')
        result.update(f't{i}.b{j}' for j, _ in enumerate(turn['before_answer'], 1))
    return result


def check_labels(measures, run):
    assert set(measures) == set(KINDS)
    for key in ('avoidable_interventions', 'required_approvals'):
        assert isinstance(measures[key], list)
        assert all(type(t) is int and 1 <= t <= len(run['turns']) for t in measures[key])
        assert len(measures[key]) == len(set(measures[key]))
    assert isinstance(measures['unnecessary_interruptions'], list)
    assert all(m in message_ids(run) for m in measures['unnecessary_interruptions'])
    assert len(measures['unnecessary_interruptions']) == len(set(measures['unnecessary_interruptions']))
    score = measures['proactive_usefulness']
    assert score == 'not_observed' or (type(score) is int and score in (0, 1, 2))


def check_reply(reply, run):
    """Validate the shared output shape independently of task A's parser."""
    data = json.loads(reply)
    assert isinstance(data, dict) and set(data) == set(RUBRIC['output_schema'])
    for key in ('avoidable_interventions', 'required_approvals', 'unnecessary_interruptions'):
        assert isinstance(data[key], list)
        expected = {'message', 'reason'} if key == 'unnecessary_interruptions' else {'turn', 'reason'}
        if key == 'avoidable_interventions':
            expected.add('kind')
        for item in data[key]:
            assert isinstance(item, dict) and set(item) == expected
            assert isinstance(item['reason'], str) and item['reason'].strip()
            if key == 'unnecessary_interruptions':
                assert item['message'] in message_ids(run)
            else:
                assert type(item['turn']) is int and 1 <= item['turn'] <= len(run['turns'])
            if key == 'avoidable_interventions':
                assert item['kind'] in ASK_KINDS
    proactive = data['proactive_usefulness']
    assert set(proactive) == {'score', 'reason'}
    assert proactive['score'] is None or (type(proactive['score']) is int and proactive['score'] in (0, 1, 2))
    assert isinstance(proactive['reason'], str) and proactive['reason'].strip()
    return {key: ([row['message' if key == 'unnecessary_interruptions' else 'turn'] for row in value]
                  if key != 'proactive_usefulness' else
                  ('not_observed' if value['score'] is None else value['score']))
            for key, value in data.items()}


def test_rubric_contract():
    assert set(RUBRIC) == {'version', 'applies_to_tag', 'messages', 'measures', 'judge_instructions',
                           'output_schema', 'agreement'}
    assert RUBRIC['version'] == 'initiative-rubric-v1'
    assert RUBRIC['applies_to_tag'] == 'exp-continuity'
    assert set(RUBRIC['measures']) == set(KINDS) == set(RUBRIC['output_schema'])
    for key, kind in KINDS.items():
        row = RUBRIC['measures'][key]
        assert row['kind'] == kind
        assert isinstance(row['definition'], str) and row['definition'].strip()
        if kind != 'score':
            for field in ('counts', 'never_counts'):
                assert row[field] and all(isinstance(s, str) and s.strip() for s in row[field])
    score = RUBRIC['measures']['proactive_usefulness']
    assert set(score['scale']) == {'0', '1', '2'}
    assert all(isinstance(s, str) and s.strip() for s in score['scale'].values())
    assert isinstance(score['not_observed'], str) and score['not_observed'].strip()
    assert RUBRIC['messages'].strip() and RUBRIC['judge_instructions'].strip()
    assert RUBRIC['agreement'] == {'window': 5, 'max_disagreements': 1}


def test_no_scenario_text_leaks_into_rubric_or_fixture_transcripts():
    sources = json.loads((ROOT / 'evals/scenarios/continuity-experiments.json').read_text())
    text = json.dumps(RUBRIC, ensure_ascii=False).casefold()
    transcripts = json.dumps([c['run']['turns'] for c in CASES], ensure_ascii=False).casefold()
    for scenario in sources:
        assert scenario['id'].casefold() not in text
        for turn in scenario['turns']:
            say = turn['say'].casefold()
            # Korean content-bearing eojeol (3+ syllables); excludes short function words.
            # Also check 2+ syllable stems after common particles, without a Korean NLP dependency.
            words = re.findall(r'[가-힣]{3,}', say)
            stems = [re.sub(r'(까지|에서|으로|이랑|의|을|를|은|는|이|가)$', '', w) for w in words]
            for word in set(words + [s for s in stems if len(s) >= 2]):
                assert word not in text, word
                assert word not in transcripts, word
            # Five-word English spans catch distinctive phrases without banning generic words.
            english = re.findall(r'[a-z0-9]+', say)
            for i in range(len(english) - 4):
                phrase = ' '.join(english[i:i + 5])
                normalized = ' '.join(re.findall(r'[a-z0-9]+', text))
                assert phrase not in normalized, phrase


def test_fixture_envelope():
    assert FIXTURES['version'] == 'initiative-judge-fixtures-v1'
    assert FIXTURES['rubric'] == RUBRIC['version']
    assert 8 <= len(CASES) <= 10
    assert len({c['id'] for c in CASES}) == len(CASES)
    assert {c['split'] for c in CASES} == {'dev', 'heldout'}


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_fixture_scenario_run_and_labels(case):
    scenario = validate(case['scenario'])
    assert scenario['id'] == case['id'] and scenario['split'] == case['split']
    assert 'exp-continuity' in scenario['tags']
    assert any(tag.startswith('kind-') for tag in scenario['tags'])
    run = case['run']
    assert run['worker'] == 'codex' and run['error'] is None
    assert isinstance(run['diff'], dict)
    assert len(run['turns']) == len(scenario['turns'])
    for owner, turn in zip(scenario['turns'], run['turns']):
        assert turn['say'] == owner['say']
        assert {'say', 'sent_at', 'status', 'answer', 'before_answer', 'events', 'elapsed'} <= set(turn)
        assert turn['sent_at'] and isinstance(turn['answer'], str)
        assert isinstance(turn['elapsed'], (int, float)) and turn['elapsed'] >= 0
        assert isinstance(turn['before_answer'], list) and isinstance(turn['events'], list)
        assert all(isinstance(m['text'], str) and m['text'] for m in turn['before_answer'])
        for event in turn['events']:
            assert {'tool', 'status'} <= set(event) <= {'tool', 'status', 'label'}
    check_labels(case['human'], run)
    if case.get('_malformed'):
        with pytest.raises(AssertionError):
            check_reply(case['judge_reply'], run)
    else:
        actual = check_reply(case['judge_reply'], run)
        differences = [k for k in KINDS if
                       (actual[k] != case['human'][k] if k == 'proactive_usefulness'
                        else sorted(actual[k]) != sorted(case['human'][k]))]
        assert differences == ([case['_disagreement']] if '_disagreement' in case else [])


def test_label_template_matches_fixtures_in_order():
    assert LABELS['version'] == 'initiative-labels-v1' and LABELS['rubric'] == RUBRIC['version']
    assert LABELS['_note'].strip()
    assert [label['scenario'] for label in LABELS['labels']] == [c['id'] for c in CASES]
    keys = set()
    for label, case in zip(LABELS['labels'], CASES):
        key = (label['run_id'], label['scenario'], label['worker'], label['epoch'])
        assert key not in keys
        keys.add(key)
        assert label['run_id'] == 'fixture' and label['worker'] == case['run']['worker']
        assert label['epoch'] == 1 and label['labeller'] in ('owner', 'contributor')
        assert label['measures'] == case['human']
        check_labels(label['measures'], case['run'])


def test_disagreements_exceed_threshold_in_same_five_label_window():
    marked = [c for c in CASES if '_disagreement' in c]
    assert len(marked) == 2
    assert all(c['_disagreement'] == 'avoidable_interventions' for c in marked)
    assert sum('_disagreement' in c for c in CASES[:5]) > RUBRIC['agreement']['max_disagreements']
    assert sum(c.get('_malformed', False) for c in CASES) == 1


def test_fixture_coverage_and_proactive_evidence_boundary():
    assert any(c['human'] == dict(avoidable_interventions=[], required_approvals=[],
                                 unnecessary_interruptions=[], proactive_usefulness='not_observed')
               for c in CASES)
    assert any(c['human']['avoidable_interventions'] == [2] for c in CASES)
    assert any(c['human']['avoidable_interventions'] == [1] for c in CASES)
    approvals = [c for c in CASES if c['human']['required_approvals']]
    assert approvals and all(not c['human']['avoidable_interventions'] for c in approvals)
    assert any({'tool': 'connector_permission', 'status': 'denied'} in t['events']
               for c in approvals for t in c['run']['turns'])
    assert any('t1.b1' in c['human']['unnecessary_interruptions'] for c in CASES)
    assert any(c['run']['turns'][0]['before_answer'] and
               not c['human']['unnecessary_interruptions'] for c in CASES)
    proactive = [c for c in CASES if c['human']['proactive_usefulness'] != 'not_observed']
    assert {c['human']['proactive_usefulness'] for c in proactive} == {0, 2}
    assert all('UNSUPPORTED' in c['_note'] and 'separate delivery' in c['_note'] for c in proactive)


def test_shape_validator_rejects_invalid_values():
    case = CASES[0]
    for value in (-1, 3, True, '2'):
        data = json.loads(case['judge_reply'])
        data['proactive_usefulness']['score'] = value
        with pytest.raises(AssertionError):
            check_reply(json.dumps(data), case['run'])
    human = copy.deepcopy(case['human'])
    human['unnecessary_interruptions'] = ['t1.b1']
    with pytest.raises(AssertionError):
        check_labels(human, case['run'])
