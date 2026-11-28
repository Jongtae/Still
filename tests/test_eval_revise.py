"""#1283 revision candidates: injected transports, temporary folders, no model call, no owner data."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import cli, revise
from agentos_eval.arm_compare import text_digest

SKILL = 'src/personal_agent/bundled_skills/agentos-skills/SKILL.md'
SCENARIO = 'progress-weather-watch-nochange-short'
FORBIDDEN = {SCENARIO, 'held-out-case-7'}
CAUSE = {'kind': 'blocker', 'source': 'work-1', 'text': 'The skill tells the AI to stop before checking the result.'}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    for rel, text in {'src/pkg/SKILL.md': 'line one\nline two\nline three\n',
                      'evals/scenarios/s.json': '{}', 'evals/agentos_eval/arm_compare.py': 'x = 1\n',
                      'tests/test_x.py': 'pass\n', 'README.md': 'readme\n'}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def target(repo):
    return revise.load_target('src/pkg/SKILL.md', repo=repo)


def transport(*replies):
    calls = []

    async def generate(system, prompt):
        calls.append((system, prompt))
        reply = replies[len(calls) - 1]
        return SimpleNamespace(completion=reply if isinstance(reply, str) else json.dumps(reply),
                               usage=None, model='fake-model')
    return generate, calls


def cand(scope, text, path='src/pkg/SKILL.md', summary='check the result first', **extra):
    return {'scope': scope, 'path': path, 'text': text, 'summary': summary, **extra}


def run(target, *replies, **kwargs):
    generate, calls = transport(*replies)
    kwargs.setdefault('forbidden', FORBIDDEN)
    result = asyncio.run(revise.propose(target, CAUSE, generate, **kwargs))
    return result, calls


# --- target ---------------------------------------------------------------------------------------

def test_target_is_the_whole_file_or_a_line_range_with_its_digest(repo, target):
    assert target['path'] == 'src/pkg/SKILL.md' and target['lines'] is None
    assert target['text'] == 'line one\nline two\nline three\n'
    assert target['digest'] == text_digest(target['text'])
    ranged = revise.load_target('src/pkg/SKILL.md', lines=(2, 3), repo=repo)
    assert ranged['text'] == 'line two\nline three\n' and ranged['lines'] == [2, 3]
    assert ranged['digest'] == text_digest('line two\nline three\n')


@pytest.mark.parametrize('path', ['evals/scenarios/s.json', 'evals/agentos_eval/arm_compare.py', 'tests/test_x.py',
                                  'README.md', '../outside.md', 'src/pkg/missing.md', 'src/pkg'])
def test_target_outside_src_or_on_eval_machinery_is_refused(repo, path):
    (repo.parent / 'outside.md').write_text('x')
    with pytest.raises(ValueError):
        revise.load_target(path, repo=repo)


@pytest.mark.parametrize('lines', [(0, 1), (2, 1), (3, 9)])
def test_line_range_out_of_bounds_is_refused(repo, lines):
    with pytest.raises(ValueError):
        revise.load_target('src/pkg/SKILL.md', lines=lines, repo=repo)


# --- proposer input guard -------------------------------------------------------------------------

def test_proposer_input_holds_only_target_text_cause_and_prior(target):
    data = revise.proposer_input(target, CAUSE, [], forbidden=FORBIDDEN)
    assert set(data) == set(revise.PROPOSER_KEYS) == {'target_path', 'lines', 'base_digest', 'text', 'cause', 'prior'}
    assert data['cause'] == CAUSE['text'] and data['text'] == target['text'] and data['prior'] == []
    assert CAUSE['source'] not in json.dumps(data)


def test_cause_naming_a_scenario_is_refused(target):
    with pytest.raises(ValueError):
        revise.proposer_input(target, dict(CAUSE, text=f'fails on {SCENARIO}'), [], forbidden=FORBIDDEN)


@pytest.mark.parametrize('cause', [dict(CAUSE, kind='request'), dict(CAUSE, text=''), dict(CAUSE, text=None)])
def test_cause_needs_a_known_kind_and_text(target, cause):
    with pytest.raises(ValueError):
        revise.proposer_input(target, cause, [], forbidden=FORBIDDEN)


def entry(target, recorded_at, digest, verdict, *, path=None, summary='s', reasons=('heldout target falls',)):
    return {'schema': 'revision-experience/1', 'recorded_at': recorded_at, 'target_path': path or target['path'],
            'lines': None, 'base_digest': 'sha256:' + '0' * 64, 'cause': CAUSE, 'proposer': {'proposer': 'p'},
            'settings': {'worker': 'w', 'model': 'm'}, 'scenario_ids': [SCENARIO], 'measure': 'passed',
            'tolerances': {'honesty': 0.05},
            'candidates': [{'label': 'general', 'scope': 'general', 'candidate_digest': digest, 'summary': summary,
                            'parents': [], 'verdict': verdict, 'reasons': list(reasons)}],
            'cost': {'baseline': {'usd': 1.0}}}


def test_prior_is_newest_first_bounded_same_target_and_hides_scenarios_reasons_and_tolerances(target):
    old = entry(target, '2026-11-01T00:00:00Z', 'sha256:' + '1' * 64, 'rejected', summary='old')
    adopted = entry(target, '2026-11-02T00:00:00Z', target['digest'], 'preferred', summary='adopted one')
    new = entry(target, '2026-11-03T00:00:00Z', 'sha256:' + '2' * 64, 'insufficient_evidence', summary='new')
    other = entry(target, '2026-11-04T00:00:00Z', 'sha256:' + '3' * 64, 'rejected', path='src/pkg/other.md')
    prior = revise.prior_for_proposer([old, other, new, adopted], target, limit=2)
    assert [p['summary'] for p in prior] == ['new', 'adopted one']
    assert [p['adopted'] for p in prior] == [False, True]
    assert all(set(p) == {'candidate_digest', 'scope', 'summary', 'cause', 'verdict', 'adopted'} for p in prior)
    text = json.dumps(revise.proposer_input(target, CAUSE, [old, new, adopted], forbidden=FORBIDDEN))
    for hidden in (SCENARIO, 'heldout target falls', 'honesty', '"worker"', 'tolerances'):
        assert hidden not in text


def test_the_prompt_sent_is_the_guarded_input(target):
    _, calls = run(target, {'candidates': [cand('general', 'new text\n')]})
    system, prompt = calls[0]
    assert json.loads(prompt) == revise.proposer_input(target, CAUSE, [], forbidden=FORBIDDEN)
    assert SCENARIO not in system + prompt


# --- candidate guard ------------------------------------------------------------------------------

def test_at_most_one_general_and_one_scoped_with_digests(target):
    result, calls = run(target, {'candidates': [cand('general', 'general text\n'), cand('scoped', 'scoped text\n')]})
    assert len(calls) == 1 and result['refused'] == []
    by = {c['label']: c for c in result['candidates']}
    assert set(by) == {'general', 'scoped'}
    assert by['general']['base_digest'] == target['digest']
    assert by['general']['candidate_digest'] == text_digest('general text\n')


@pytest.mark.parametrize('bad, reason', [
    (cand('general', 'x\n', path='evals/scenarios/s.json'), 'path'),
    (cand('general', 'x\n', path='src/pkg/other.md'), 'path'),
    (cand('general', 'x\n', scenarios=[{'id': 'new'}]), 'key'),
    (cand('general', 'x\n', tolerances={'honesty': 1}), 'key'),
    (cand('general', 'x\n', files={'evals/judgments/cases-v1.json': '{}'}), 'key'),
    (cand('general', ''), 'text'),
    (cand('general', 'line one\nline two\nline three\n'), 'text'),
    (cand('general', f'see {SCENARIO}\n'), 'scenario'),
    (cand('general', 'x\n', summary=''), 'summary'),
    (cand('general', 'x\n', summary=f'fixes {SCENARIO}'), 'scenario'),
    (cand('composed', 'x\n'), 'scope'),
])
def test_a_candidate_touching_anything_but_the_target_text_is_refused(target, bad, reason):
    result, _ = run(target, {'candidates': [bad, cand('scoped', 'fine\n')]})
    assert [c['label'] for c in result['candidates']] == ['scoped']
    assert len(result['refused']) == 1 and 'text' not in result['refused'][0]
    assert reason in result['refused'][0]['reason'].lower()


def test_repeated_scope_and_a_third_candidate_are_refused(target):
    result, _ = run(target, {'candidates': [cand('general', 'a\n'), cand('general', 'b\n'),
                                            cand('scoped', 'c\n'), cand('scoped', 'd\n')]})
    assert [c['text'] for c in result['candidates']] == ['a\n', 'c\n']
    assert len(result['refused']) == 2


@pytest.mark.parametrize('reply', ['not json', '[1]', {'candidates': 'x'},
                                   {'candidates': [cand('general', 'a\n')], 'scenarios': [{'id': 'x'}]},
                                   {'candidates': [cand('general', 'a\n')], 'adoption_rule': 'always'}])
def test_a_reply_outside_the_shape_is_refused_whole(target, reply):
    result, calls = run(target, reply, compose=True)
    assert result['candidates'] == [] and len(result['refused']) == 1 and len(calls) == 1


# --- compose --------------------------------------------------------------------------------------

def test_compose_is_one_more_call_and_a_new_arm_with_parents(target):
    first = {'candidates': [cand('general', 'g\n'), cand('scoped', 's\n')]}
    result, calls = run(target, first, {'candidates': [cand('composed', 'g and s\n')]}, compose=True)
    assert len(calls) == 2
    data = json.loads(calls[1][1])
    assert set(data) == set(revise.PROPOSER_KEYS) | {'candidates'}
    assert [c['text'] for c in data['candidates']] == ['g\n', 's\n']
    assert all(set(c) == {'scope', 'summary', 'text'} for c in data['candidates'])
    composed = [c for c in result['candidates'] if c['label'] == 'composed'][0]
    assert composed['parents'] == [text_digest('g\n'), text_digest('s\n')]
    assert composed['candidate_digest'] == text_digest('g and s\n')


def test_compose_needs_both_candidates(target):
    result, calls = run(target, {'candidates': [cand('general', 'g\n')]}, compose=True)
    assert len(calls) == 1 and [c['label'] for c in result['candidates']] == ['general']


def test_compose_reply_is_guarded_too(target):
    first = {'candidates': [cand('general', 'g\n'), cand('scoped', 's\n')]}
    result, _ = run(target, first, {'candidates': [cand('composed', 'x\n', path='tests/test_x.py')]}, compose=True)
    assert [c['label'] for c in result['candidates']] == ['general', 'scoped'] and len(result['refused']) == 1


# --- files and experience -------------------------------------------------------------------------

def written(target, tmp_path, compose=False):
    replies = [{'candidates': [cand('general', 'g\n'), cand('scoped', 's\n')]}]
    if compose:
        replies.append({'candidates': [cand('composed', 'gs\n')]})
    result, _ = run(target, *replies, compose=compose)
    return revise.write_candidates(result, target, CAUSE, tmp_path / 'out', settings={'proposer': 'fake/model'})


def test_candidates_are_files_outside_the_repo_and_the_target_is_unchanged(repo, target, tmp_path):
    before = (repo / 'src/pkg/SKILL.md').read_bytes()
    folder = written(target, tmp_path)
    assert (repo / 'src/pkg/SKILL.md').read_bytes() == before
    assert (folder / 'general.txt').read_text() == 'g\n'
    meta = json.loads((folder / 'general.json').read_text())
    assert meta['schema'] == 'revision-candidate/1' and meta['target_path'] == 'src/pkg/SKILL.md'
    assert meta['base_digest'] == target['digest'] and meta['candidate_digest'] == text_digest('g\n')
    assert meta['summary'] and meta['cause'] == CAUSE and meta['proposer'] == {'proposer': 'fake/model'}
    manifest = json.loads((folder / 'manifest.json').read_text())
    assert manifest['schema'] == 'revision-candidates/1' and manifest['candidates'] == ['general', 'scoped']
    assert json.loads((folder / 'refused.json').read_text()) == []


def test_output_folder_inside_the_repository_is_refused(target):
    result, _ = run(target, {'candidates': [cand('general', 'g\n')]})
    with pytest.raises(ValueError):
        revise.write_candidates(result, target, CAUSE, revise.REPO / 'evals' / 'out', settings={})


def comparison(target, verdicts):
    arm = lambda digest: {'text_digest': digest, 'executions': 6, 'cost': {'usd': 0.5}, 'refused_runs': 0,
                          'partitions': {}}
    return {'schema': 'arm-compare/1', 'target': 'passed', 'tolerances': {'honesty': 0.05}, 'min_trials': 3,
            'settings': {'worker': 'w', 'model': 'm'}, 'scenario_ids': [SCENARIO],
            'candidate_path_executions': {}, 'baseline': arm(target['digest']), 'equal_retries': arm(target['digest']),
            'candidates': {label: dict(arm(digest), verdict=verdict, preferred=verdict == 'preferred',
                                       reasons=[] if verdict == 'preferred' else ['honesty falls'], against={})
                           for label, (digest, verdict) in verdicts.items()}}


def test_experience_entry_records_the_comparison_and_composed_never_inherits(target, tmp_path):
    folder = written(target, tmp_path, compose=True)
    result = comparison(target, {'general': (text_digest('g\n'), 'preferred'),
                                 'scoped': (text_digest('s\n'), 'preferred')})
    record = revise.experience_entry(folder, result, recorded_at='2026-11-27T10:00:00Z')
    assert record['schema'] == 'revision-experience/1' and record['target_path'] == target['path']
    assert record['base_digest'] == target['digest'] and record['cause'] == CAUSE
    assert record['scenario_ids'] == [SCENARIO] and record['settings'] == {'worker': 'w', 'model': 'm'}
    by = {c['label']: c for c in record['candidates']}
    assert by['general']['verdict'] == 'preferred'
    assert by['composed']['verdict'] == 'not_compared' and by['composed']['reasons'] == []
    assert by['composed']['parents'] == [text_digest('g\n'), text_digest('s\n')]
    assert record['cost']['baseline'] == {'usd': 0.5} and 'proposer' in record['cost']
    assert record['tolerances'] == {'honesty': 0.05} and record['measure'] == 'passed'


@pytest.mark.parametrize('change', ['schema', 'baseline', 'digest', 'label'])
def test_a_comparison_of_other_text_is_refused(target, tmp_path, change):
    folder = written(target, tmp_path)
    result = comparison(target, {'general': (text_digest('g\n'), 'rejected')})
    if change == 'schema':
        result['schema'] = 'other/1'
    elif change == 'baseline':
        result['baseline']['text_digest'] = 'sha256:' + 'f' * 64
    elif change == 'digest':
        result['candidates']['general']['text_digest'] = 'sha256:' + 'e' * 64
    else:
        result['candidates']['unknown'] = result['candidates']['general']
    with pytest.raises(ValueError):
        revise.experience_entry(folder, result, recorded_at='2026-11-27T10:00:00Z')


def test_experience_is_stored_outside_the_repo_and_read_back_newest_first(target, tmp_path):
    store = tmp_path / 'experience'
    for when in ('2026-11-01T00:00:00Z', '2026-11-03T00:00:00Z', '2026-11-02T00:00:00Z'):
        revise.record_experience(entry(target, when, 'sha256:' + '1' * 64, 'rejected'), store)
    revise.record_experience(entry(target, '2026-11-04T00:00:00Z', 'sha256:' + '1' * 64, 'rejected',
                                   path='src/pkg/other.md'), store)
    (store / 'junk.json').write_text('{not json')
    rows = revise.read_experience(store, target['path'], limit=2)
    assert [r['recorded_at'] for r in rows] == ['2026-11-03T00:00:00Z', '2026-11-02T00:00:00Z']
    with pytest.raises(ValueError):
        revise.record_experience(entry(target, '2026-11-05T00:00:00Z', 'x', 'rejected'), revise.REPO / 'evals')


# --- command --------------------------------------------------------------------------------------

def test_cli_propose_then_record_without_touching_the_repository(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path / 'home'))
    skill = revise.REPO / SKILL
    before = skill.read_bytes()
    generate, calls = transport({'candidates': [cand('general', 'revised\n', path=SKILL)]})
    seen = {}

    def fake_make_generate(spec, **kwargs):
        seen['spec'] = spec
        return generate, {'proposer': spec}, lambda: None
    monkeypatch.setattr(revise, 'make_generate', fake_make_generate)
    cause = tmp_path / 'cause.txt'
    cause.write_text(CAUSE['text'])
    out = tmp_path / 'cands'
    from agentos_eval import scenarios
    dev = next(s['id'] for s in scenarios.load(include_local=False) if s['split'] == 'dev')
    arm = tmp_path / 'baseline.json'  # #1298: propose needs a current-text arm with room on dev
    arm.write_text(json.dumps({'schema': 'revision-arm/1', 'role': 'current',
                               'text_digest': revise.load_target(SKILL, (1, 5))['digest'], 'target_path': SKILL,
                               'lines': [1, 5], 'records': [{'scenario': dev, 'epoch': 1, 'refused_effects': 0,
                                                             'value': {'passed': 0.0}}]}))
    assert cli.main(['revise', 'propose', '--target', SKILL, '--lines', '1-5', '--cause-kind', 'blocker',
                     '--cause-source', 'work-1', '--cause-file', str(cause), '--proposer', 'fake/model',
                     '--baseline', str(arm), '--out', str(out)]) == 0
    assert seen['spec'] == 'fake/model' and len(calls) == 1
    assert skill.read_bytes() == before
    meta = json.loads((out / 'general.json').read_text())
    assert meta['lines'] == [1, 5]
    base = meta['base_digest']
    compare = tmp_path / 'compare.json'
    target = {'digest': base}
    compare.write_text(json.dumps(comparison(target, {'general': (text_digest('revised\n'), 'rejected')})))
    assert cli.main(['revise', 'record', '--candidates', str(out), '--comparison', str(compare)]) == 0
    stored = list((tmp_path / 'home' / 'experience').rglob('*.json'))
    assert len(stored) == 1 and json.loads(stored[0].read_text())['candidates'][0]['verdict'] == 'rejected'
    assert skill.read_bytes() == before


def test_forbidden_ids_include_world_scenarios(tmp_path):
    ids = revise.forbidden_ids(include_local=False)
    assert SCENARIO in ids and 'world-appworld-note-update' in ids
    assert any(i.startswith('world-') and 'appworld' not in i for i in ids)


def test_make_generate_refuses_no_proposer():
    with pytest.raises(ValueError):
        revise.make_generate('none')
    with pytest.raises(ValueError):
        revise.make_generate('')
