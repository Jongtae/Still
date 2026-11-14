"""Model-free R1 runner evidence, using independent synthetic cases and fake workers."""
import copy
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import cli, recall_pairs as pairs, recall_report as rr  # noqa: E402
from personal_agent.memory_service import MemoryService  # noqa: E402


@pytest.fixture
def data():
    rows = [dict(ref=key, key='profile.' + key, content=text) for key, text in
            [('desk', 'cedar'), ('light', 'lantern'), ('seat', 'cushion'),
             ('floor', 'carpet'), ('old', 'obsolete')]]
    rows[-1]['valid_until'] = '2025-01-01'

    def case(ident, split, kind, query, needed, **extra):
        return dict(id=ident, split=split, kind=kind, query=query, needed=needed,
                    traps=['old'], note='Synthetic runner test', **extra)

    cases = [case('d-lex', 'dev', 'lexical-control', 'cedar', ['desk']),
             case('d-para', 'dev', 'paraphrase', 'illumination', ['light']),
             case('h-lex', 'heldout', 'lexical-control', 'cushion', ['seat']),
             case('h-para', 'heldout', 'paraphrase', 'underfoot', ['floor']),
             case('h-para2', 'heldout', 'paraphrase', 'luminosity', ['light']),
             case('h-para3', 'heldout', 'paraphrase', 'seating', ['seat']),
             dict(id='h-harm', split='heldout', kind='harm-control', query='unrelated',
                  needed=[], traps=[], irrelevant=['desk'], note='No relevant notes')]
    return pairs.validate(dict(version=pairs.VERSION, owner='recall-test-owner', today='2026-01-01',
                               rows=rows, cases=cases))


def lexical(store, data):
    """Independent literal baseline using the production search API."""
    service = MemoryService(store, private_read_sink=lambda marker: None)
    service._today = lambda: data['today']
    return lambda query, context: [row['id'] for row in service.search_memories(data['owner'], query)['memories']]


def scripted(plans, seen=None):
    """Explicit query -> stored key scripts, never reading expected needed/trap labels."""
    calls = 0

    def factory(store, data):
        nonlocal calls
        plan = plans[calls % len(plans)]
        calls += 1
        if seen is not None:
            seen.append(store)
            assert len(store.memories(owner_id=data['owner'])) == 5
        with store.db() as db:
            ids = {row['memory_key'].removeprefix('profile.'): row['id']
                   for row in db.execute('SELECT id, memory_key FROM memories').fetchall()}
        return lambda query, context: [ids[key] for key in plan.get(query, [])]

    return factory


BASE = {'cedar': ['desk'], 'cushion': ['seat']}
FIX = {**BASE, 'underfoot': ['floor']}


def measured(data, factory, deterministic=True, runs=3):
    return rr.run_arm(data, rr.Arm(factory, 'fixture (model-free)', deterministic), runs)


def build(data, plans, sampling=False):
    return rr.report(data, {'lexical': measured(data, lexical),
                            'candidate': measured(data, scripted(plans), not sampling)})


def by_id(rows):
    return {row['id']: row for row in rows}


def test_fresh_stores_and_deterministic_run_count(data):
    seen = []
    result = measured(data, scripted([BASE], seen), runs=9)
    assert len(result['runs']) == len(seen) == 1
    result = measured(data, scripted([BASE], seen), deterministic=False, runs=3)
    assert len(result['runs']) == 3
    assert len(seen) == 4
    assert len({store.root for store in seen}) == 4
    assert all(not store.root.exists() for store in seen)
    assert len({run['fixture'] for run in result['runs']}) == 1
    with pytest.raises(ValueError, match='positive'):
        rr.run_arm(data, lexical, runs=0)


def test_fixture_digest_cannot_change_between_runs(data):
    def factory(store, data):
        data['rows'][0]['content'] += ' changed'
        return lambda q, c: []

    with pytest.raises(pairs.RecallPairsError, match='digest changed'):
        measured(data, factory, deterministic=False)


def test_majority_uncertainty_and_any_run_trap_override(data):
    plans = [{**FIX, 'luminosity': ['light']}, {**FIX, 'luminosity': ['light']},
             {**FIX, 'illumination': ['old']}]
    full_report = build(data, plans, sampling=True)
    result = full_report['arms']['candidate']
    cases = by_id(result['cases'])
    changes = by_id(result['compare']['heldout']['changes'])
    assert changes['h-para']['change'] == 'fixed'
    assert cases['h-para']['passes'] == 3 and cases['h-para']['spread'] == 0
    assert changes['h-para2']['change'] == 'uncertain'
    assert cases['h-para2']['passes'] == 2 and cases['h-para2']['majority'] is True
    assert cases['h-para2']['spread'] == 1 and cases['h-para2']['uncertain'] is True
    assert [row['needed_hit'] for row in cases['h-para2']['per_run']] == [1, 1, 0]
    assert result['compare']['heldout']['counts']['fixed'] == 1
    dev = result['compare']['dev']
    assert by_id(dev['changes'])['d-para']['change'] == 'regressed'
    assert dev['trap_regressions'] == [dict(id='d-para', kind='paraphrase', change='regressed',
                                           trap_hits=1, per_run=[0, 0, 1])]
    assert result['score']['dev']['total']['cases_with_trap'] == 0  # representative is an observed non-trap row
    heldout = result['compare']['heldout']
    assert heldout['needed_recalls'] == [0.75, 0.75, 0.5]
    assert heldout['recall_min'] == 0.5 and heldout['recall_max'] == 0.75
    assert heldout['recall_spread'] == 0.25
    assert heldout['recall_difference'] == pytest.approx(2 / 3 - 0.25)
    assert len(result['run_scores']) == 3
    text = rr.report_markdown(full_report)
    assert '| candidate | 0.500 | 1/2 | 1 |' in text
    assert 'd-para (paraphrase): 1 trap hit(s)' in text


def test_majority_first_matching_row_and_tie_fails(data):
    result = build(data, [BASE, FIX, FIX], sampling=True)['arms']['candidate']
    assert by_id(result['cases'])['h-para']['needed_hit'] == 1
    assert by_id(result['compare']['heldout']['changes'])['h-para']['change'] == 'uncertain'
    baseline = measured(data, lexical)
    tied = measured(data, scripted([BASE, FIX]), deterministic=False, runs=2)
    arm = rr.report(data, {'lexical': baseline, 'tie': tied})['arms']['tie']
    case = by_id(arm['cases'])['h-para']
    assert case['passes'] == 1 and case['majority'] is False and case['needed_hit'] == 0


@pytest.mark.parametrize('plans,sampling,failed', [
    ([{**FIX, 'illumination': ['old']}], False, 'a-zero-traps'),
    ([{**FIX, 'cedar': []}], False, 'b-no-lexical-control-regressions'),
    ([BASE], False, 'c-heldout-gain-and-fix'),
    ([{**FIX, 'unrelated': ['desk']}], False, 'd-no-heldout-regressions'),
    ([{**FIX, 'luminosity': ['light'], 'seating': ['seat']}, FIX, FIX], True,
     'e-heldout-spread-within-gain'),
])
def test_each_verdict_condition_can_fail_independently(data, plans, sampling, failed):
    verdict = build(data, plans, sampling)['arms']['candidate']['verdict']
    assert verdict['preferred'] is False
    assert verdict['failed'] == [failed]


def test_preferred_requires_certain_heldout_fix_and_no_regression(data):
    for sampling in (False, True):
        result = build(data, [FIX], sampling)
        assert result['arms']['candidate']['verdict']['preferred'] is True
        assert result['arms']['candidate']['verdict']['failed'] == []
        assert result['arms']['lexical']['verdict'] is None
    uncertain = build(data, [FIX, FIX, BASE], True)['arms']['candidate']
    assert uncertain['compare']['heldout']['counts']['fixed'] == 0
    assert 'c-heldout-gain-and-fix' in uncertain['verdict']['failed']


def test_uncertain_regression_still_counts_and_trap_beats_passing_majority(data):
    plans = [FIX, FIX, {**FIX, 'cushion': ['old']}]
    arm = build(data, plans, True)['arms']['candidate']
    case = by_id(arm['cases'])['h-lex']
    assert case['majority'] is True and case['uncertain'] is True
    assert case['needed_hit'] == case['needed_total']
    assert by_id(arm['compare']['heldout']['changes'])['h-lex']['change'] == 'regressed'
    assert arm['compare']['heldout']['counts']['regressed'] == 1
    assert {'a-zero-traps', 'b-no-lexical-control-regressions', 'd-no-heldout-regressions'} <= set(arm['verdict']['failed'])
    # A non-trap uncertain loss is also retained as regressed.
    arm = build(data, [{**FIX, 'cushion': []}, {**FIX, 'cushion': []}, FIX], True)['arms']['candidate']
    assert by_id(arm['compare']['heldout']['changes'])['h-lex']['uncertain'] is True
    assert by_id(arm['compare']['heldout']['changes'])['h-lex']['change'] == 'regressed'


def test_dev_and_heldout_never_pooled_in_scores_comparison_or_markdown(data):
    result = build(data, [{**BASE, 'illumination': ['light']}])
    candidate = result['arms']['candidate']
    assert set(candidate['score']) == set(candidate['compare']) == {'dev', 'heldout'}
    assert candidate['score']['dev']['total']['needed_recall'] == 1
    assert candidate['score']['heldout']['total']['needed_recall'] == 0.25
    assert candidate['compare']['dev']['counts']['fixed'] == 1
    assert candidate['compare']['heldout']['counts']['fixed'] == 0
    text = rr.report_markdown(result)
    dev, heldout = text.split('## dev\n')[1].split('## heldout\n')
    assert '| candidate | 1.000 | 2/2 |' in dev
    assert '| candidate | 0.250 | 1/4 |' in heldout
    assert 'd-para (paraphrase): fixed' in dev
    assert 'd-para' not in heldout
    assert result['fixture'] in text and result['version'] in text and data['today'] in text
    assert candidate['evidence'] in text
    json.dumps(result)  # no callable/path objects leak into the persisted report


def test_plugin_resolution_metadata_and_clear_errors(monkeypatch):
    module = types.ModuleType('r1_test_plugin')
    module.factory = lambda store, data: lambda q, c: []
    monkeypatch.setitem(sys.modules, module.__name__, module)
    arm = rr.resolve_arm('r1_test_plugin:factory')
    assert arm.factory is module.factory and arm.deterministic is True
    assert arm.evidence == 'fixture (model-free)'
    module.EVIDENCE = 'test module evidence'
    module.DETERMINISTIC = False
    assert rr.resolve_arm('r1_test_plugin:factory').deterministic is False
    assert rr.resolve_arm('r1_test_plugin:factory').evidence == module.EVIDENCE
    module.factory.evidence = 'test factory evidence'
    module.factory.deterministic = True
    assert rr.resolve_arm('r1_test_plugin:factory').deterministic is True
    assert rr.resolve_arm('r1_test_plugin:factory').evidence == module.factory.evidence
    module.bad = 12
    with pytest.raises(ValueError, match='callable'):
        rr.resolve_arm('r1_test_plugin:bad')
    with pytest.raises(ValueError, match='Cannot load'):
        rr.resolve_arm('r1_test_plugin:missing')
    with pytest.raises(ValueError, match='Unknown recall arm'):
        rr.resolve_arm('nonexistent-recall-arm')
    with pytest.raises(ValueError, match='requires'):
        rr.resolve_arm('phrasing')


def test_command_phraser_prompt_stdout_failure_and_timeout(monkeypatch):
    phrase = rr.command_phraser([sys.executable, '-c',
                               'import sys; p=sys.stdin.read(); print(p.split("Request: ")[1].strip(), '
                               'p.split("Turn before: ")[1].split("\\nRequest:")[0])'])
    assert phrase('test-query', 'prior context') == 'test-query prior context'
    assert phrase.evidence == 'local model observation'
    failing = rr.command_phraser([sys.executable, '-c', 'import sys; sys.stderr.write("bad worker"); sys.exit(7)'])
    with pytest.raises(RuntimeError, match='exited 7: bad worker'):
        failing('x', 'y')

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'], stderr=b'timeout detail')

    monkeypatch.setattr(rr.subprocess, 'run', timeout)
    with pytest.raises(RuntimeError, match='timed out.*timeout detail'):
        phrase('x', 'y')


def test_phrasing_retrieves_extra_words_and_replays_exact_counts(data):
    calls = []

    def phraser(query, context):
        calls.append((query, context))
        return 'carpet' if query == 'underfoot' else ''

    baseline = measured(data, lexical)
    arm = rr.resolve_arm('phrasing', phraser=phraser)
    results = rr.run_arm(data, arm)
    result = rr.report(data, {'lexical': baseline, 'phrasing': results})
    observed = result['arms']['phrasing']
    assert len(calls) == len(data['cases']) * 3
    assert observed['deterministic'] is False and observed['runs'] == 3
    assert observed['evidence'] == 'local model observation'
    assert by_id(observed['compare']['heldout']['changes'])['h-para']['change'] == 'fixed'
    assert observed['phrasings']['1']['h-para'] == 'carpet'
    replay = rr.resolve_arm('phrasing', phraser=rr.replay_phraser(json.loads(json.dumps(observed['phrasings']))))
    rebuilt = rr.report(data, {'lexical': measured(data, lexical), 'phrasing': rr.run_arm(data, replay)})
    replayed = rebuilt['arms']['phrasing']
    assert replayed.pop('evidence') == 'fixture replay of a local model observation'
    expected = dict(observed)
    expected.pop('evidence')
    assert replayed == expected
    text = rr.report_markdown(result)
    assert '3/3, spread 0' in text and 'mean difference from baseline' in text
    assert 'Trap regressions:' in text and 'preferred; failed: none' in text
    with pytest.raises(ValueError, match='Missing recorded phrasing'):
        rr.run_arm(data, rr.resolve_arm('phrasing', phraser=rr.replay_phraser({'1': {}})))


def test_report_refuses_incompatible_runs_or_fixture(data):
    base = measured(data, lexical)
    changed = copy.deepcopy(base)
    changed['runs'][0]['fixture'] = 'wrong'
    with pytest.raises(pairs.RecallPairsError, match='contents'):
        rr.report(data, {'lexical': base, 'bad': changed})
    other = copy.deepcopy(data)
    other['today'] = '2026-01-02'
    with pytest.raises(pairs.RecallPairsError, match='contents'):
        rr.report(other, {'lexical': base})
    changed = dict(base, deterministic=False, runs=[base['runs'][0], changed['runs'][0]])
    with pytest.raises(pairs.RecallPairsError, match='contents'):
        rr.report(data, {'lexical': base, 'bad': changed})


def test_cli_recall_plugin_writes_reports_and_exits_zero(data, tmp_path, monkeypatch, capsys):
    module = types.ModuleType('r1_cli_plugin')
    module.factory = scripted([FIX])
    monkeypatch.setitem(sys.modules, module.__name__, module)
    original = rr.resolve_arm
    monkeypatch.setattr(rr, 'resolve_arm', lambda spec, **kw: rr.Arm(lexical) if spec == 'lexical'
                        else original(spec, **kw))
    path = tmp_path / 'fixture.json'
    path.write_text(json.dumps(data))
    folder = tmp_path / 'reports'
    assert cli.main(['recall', '--arm', 'r1_cli_plugin:factory', '--fixture', str(path),
                     '--out', str(folder)]) == 0
    json_path, = folder.glob('recall-*.json')
    markdown_path, = folder.glob('recall-*.md')
    report = json.loads(json_path.read_text())
    assert set(report['arms']) == {'lexical', 'r1_cli_plugin:factory'}
    assert report['arms']['r1_cli_plugin:factory']['verdict']['preferred'] is True
    printed = capsys.readouterr().out
    assert markdown_path.read_text().strip() in printed and str(json_path) in printed
    monkeypatch.setattr(cli, 'eval_home', lambda: tmp_path / 'eval-home')
    assert cli.main(['recall', '--fixture', str(path)]) == 0
    assert len(list((tmp_path / 'eval-home/reports').glob('recall-*.json'))) == 1
    with pytest.raises(SystemExit, match='positive'):
        cli.main(['recall', '--runs', '0'])
    with pytest.raises(SystemExit, match='outside the repository'):
        cli.main(['recall', '--fixture', str(path), '--out', str(ROOT / 'reports')])


@pytest.mark.parametrize('mode', ['command', 'replay'])
def test_cli_phrasing_options_and_runs(data, tmp_path, monkeypatch, mode):
    original = rr.resolve_arm
    monkeypatch.setattr(rr, 'resolve_arm', lambda spec, **kw: rr.Arm(lexical) if spec == 'lexical'
                        else original(spec, **kw))
    path = tmp_path / 'fixture.json'
    path.write_text(json.dumps(data))
    words = {case['id']: ('carpet' if case['query'] == 'underfoot' else '') for case in data['cases']}
    if mode == 'replay':
        recorded = tmp_path / 'phrases.json'
        recorded.write_text(json.dumps({'1': words, '2': words}))
        option = ['--phrasings', str(recorded)]
    else:
        def command(argv):
            assert argv == ['worker', '--model', 'two words']
            return lambda query, context: 'carpet' if query == 'underfoot' else ''

        monkeypatch.setattr(rr, 'command_phraser', command)
        option = ['--phraser-cmd', 'worker --model "two words"']
    folder = tmp_path / 'reports'
    assert cli.main(['recall', '--arm', 'phrasing', '--runs', '2', '--fixture', str(path),
                     '--out', str(folder), *option]) == 0
    report_path, = folder.glob('recall-*.json')
    arm = json.loads(report_path.read_text())['arms']['phrasing']
    assert arm['runs'] == 2 and arm['phrasings'] == {'1': words, '2': words}
    assert arm['evidence'] == ('local model observation' if mode == 'command'
                               else 'fixture replay of a local model observation')
