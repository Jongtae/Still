"""C2 harness fixture evidence: no Inspect, model, network or socket required."""
import asyncio
import copy
import io
import json
import math
import sys
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
from agentos_eval import cli, continuity as c, scenarios, scoring as s
from agentos_eval.budget import JudgeBudget
from agentos_eval.report import records_from_samples


@pytest.fixture
def rubric(tmp_path):
    data = {'version': 'initiative-rubric-v1', 'applies_to_tag': 'exp-continuity',
            'messages': 't<turn>, t<turn>.b<j>',
            'measures': {name: {'kind': 'score' if name == 'proactive_usefulness' else
                              'message_list' if name == 'unnecessary_interruptions' else 'turn_list',
                              'definition': f'definition {name}'} for name in s.MEASURES},
            'judge_instructions': 'CUSTOM INSTRUCTIONS: judge only visible evidence',
            'output_schema': reply(), 'agreement': {'window': 5, 'max_disagreements': 1}}
    path = tmp_path / 'rubric.json'
    path.write_text(json.dumps(data))
    return data, path


def reply(n=0, approvals=(), interruptions=(), proactive=2):
    return {'avoidable_interventions': [{'turn': i, 'kind': 'asked_known_fact', 'reason': 'asked again'}
                                         for i in range(1, n + 1)],
            'required_approvals': [{'turn': i, 'reason': 'required'} for i in approvals],
            'unnecessary_interruptions': [{'message': m, 'reason': 'chatter'} for m in interruptions],
            'proactive_usefulness': {'score': proactive, 'reason': 'the judge claimed useful'}}


def scenario(ident='dev-case', split='dev'):
    return scenarios.validate({'id': ident, 'split': split, 'turns': ['first', 'second', 'third'],
                               'rubric': ['follow_through']})


def run():
    return {'worker': 'codex', 'turns': [
        {'say': f'owner {i}', 'answer': f'answer {i}', 'status': 'succeeded', 'elapsed': i,
         'sent_at': f'2026-11-14T12:0{i}:00+09:00', 'events': [{'tool': 'save_memory', 'status': 'succeeded'}],
         'before_answer': [{'text': 'I did something else useful', 'after_s': 0.5}]}
        for i in range(1, 4)], 'diff': {'preparations': [{'id': 1, 'state': 'active'}]}}


def test_prompt_reuses_transcript_and_labels_all_messages(rubric):
    r = run()
    r['diff']['current_state'] = [{'predicate': 'situation', 'value': 'PRIVATE SUMMARY'}]
    prompt = s.measures_prompt(scenario(), r, rubric[0])
    for text in ('AGENTOS t1 ', 'AGENTOS t1.b1 before', 'AGENTOS t3 ', 'AGENTOS t3.b1 before',
                 'sent 2026-11-14T12:01:00', 'save_memory succeeded', 'new reminder/preparation',
                 'CUSTOM INSTRUCTIONS', 'definition avoidable_interventions', 'output schema', 'not_observed'):
        assert text in prompt
    assert 'PRIVATE SUMMARY' not in prompt


@pytest.mark.parametrize('name,item', [
    ('avoidable_interventions', {'turn': 4, 'kind': 'asked_known_fact', 'reason': ''}),
    ('avoidable_interventions', {'turn': 0, 'kind': 'asked_known_fact', 'reason': ''}),
    ('avoidable_interventions', {'turn': True, 'kind': 'asked_known_fact', 'reason': ''}),
    ('avoidable_interventions', {'turn': 1, 'kind': 'made_up', 'reason': ''}),
    ('required_approvals', {'turn': 4, 'reason': ''}),
    ('unnecessary_interruptions', {'message': 't1.b2', 'reason': ''}),
    ('unnecessary_interruptions', {'message': 't4', 'reason': ''}),
    ('unnecessary_interruptions', {'message': 't1', 'reason': '', 'extra': 1}),
])
def test_parser_rejects_malformed_items(rubric, name, item):
    data = reply()
    data[name] = [item]
    message = ('invalid unnecessary_interruptions item' if 'extra' in item else 'unknown message id') if name == 'unnecessary_interruptions' else (
        'unknown intervention kind' if item.get('kind') == 'made_up' else 'turn index out of range')
    with pytest.raises(ValueError, match='^' + message + '$'):
        s.parse_measures(json.dumps(data), rubric[0], run())


def test_not_observed_and_required_override(rubric):
    data = s.parse_measures(json.dumps(reply(2, approvals=[1], interruptions=['t1.b1'])), rubric[0], run())
    assert data['proactive_usefulness']['score'] is None
    assert 'the judge claimed' not in json.dumps(data)
    assert [i['turn'] for i in data['avoidable_interventions']] == [2]
    assert [i['turn'] for i in data['required_approvals']] == [1]
    assert data['inconsistencies'][0]['turn'] == 1
    assert s.proactive_observation(run())['value'] == 'not_observed'
    # Neither a non-progress message nor a preparation state proves delivery.
    fake = run()
    fake['diff']['preparations'][0]['state'] = 'delivered'
    assert not s.proactive_observation(fake)['observed']


def test_approval_counts_only_observed_requests_and_allowed_approval_status():
    r, case = run(), scenario()
    r['turns'][0]['events'] = [{'tool': 'local_authority', 'status': 'requested'},
                              {'tool': 'connector_permission', 'status': 'denied'}]
    r['turns'][1]['status'] = 'awaiting_approval'
    r['turns'][2]['status'] = 'awaiting_context'
    case['expect']['allow_status'] = ['awaiting_approval', 'awaiting_context']
    events = s.required_approvals_events(case, r)
    assert [e['turn'] for e in events] == [1, 2]
    case['expect']['allow_status'] = []
    assert [e['turn'] for e in s.required_approvals_events(case, r)] == [1]
    r['turns'][0]['status'] = 'awaiting_approval'
    case['expect']['allow_status'] = ['awaiting_approval']
    assert len(s.required_approvals_events(case, r)) == 2  # no duplicate fallback on t1


def test_combine_keys_and_records_metadata(rubric):
    case, r = scenario(), run()
    measured = s.parse_measures(json.dumps(reply(1)), rubric[0], r)
    a, _ = s.combine(case, r, {'delivered': True}, [])
    b, meta = s.combine(case, r, {'delivered': True}, [], measures=measured,
                        measures_status='judged', measures_rubric=rubric[0])
    assert set(a) == set(b)
    assert all(math.isnan(a[k]) for k in s.MEASURE_KEYS)
    assert b['measure_avoidable_interventions'] == 1
    assert math.isnan(b['measure_proactive_usefulness'])
    score = SimpleNamespace(value=b, metadata=meta)
    records = records_from_samples([SimpleNamespace(id='dev-case@codex', epoch=2, scores={'secretary': score})])
    assert records[0]['latency'][0] == {'turn': 1, 'sent_at': r['turns'][0]['sent_at'], 'elapsed': 1}
    assert records[0]['measures_rubric_version'] == rubric[0]['version']


def score_with(tmp_path, rubric, *, cap=10, records=None, error=False, malformed=False, usage=True):
    calls = []
    async def generate(system, prompt):
        calls.append((system, prompt))
        if error and len(calls) == 2:
            raise RuntimeError('judge unavailable')
        text = (json.dumps({'dimensions': {'follow_through': {'score': 2, 'reason': 'good'}}})
                if len(calls) == 1 else 'BAD JSON' if malformed else json.dumps(reply(1)))
        return SimpleNamespace(completion=text, usage=SimpleNamespace(input_tokens=20, output_tokens=10) if usage else None)
    budget = JudgeBudget(tmp_path / 'budget.json', cap_usd=cap)
    value, metadata = asyncio.run(s.score_run(scenario(), records or run(), 'grader', budget,
                                            lambda text: text.replace('owner 1', 'REDACTED'), generate, rubric[0]))
    return value, metadata, calls, budget


def test_second_call_budget_redaction_and_settled_usage(tmp_path, rubric):
    value, meta, calls, budget = score_with(tmp_path, rubric)
    assert len(calls) == 2
    assert all('REDACTED' in prompt and 'owner 1' not in prompt for _, prompt in calls)
    assert meta['measures_status'] == 'judged'
    assert meta['judge_usage']['rubric']['input_tokens'] == 20
    assert meta['judge_usage']['measures']['output_tokens'] == 10
    assert sum(u['usd'] for u in meta['judge_usage'].values()) == pytest.approx(budget.status()['usd'])
    assert value['measure_avoidable_interventions'] == 1


def test_budget_refusal_is_nan_and_never_zero(tmp_path, rubric):
    value, meta, calls, _ = score_with(tmp_path, rubric, cap=0)
    assert calls == []
    assert meta['measures_status'] == 'budget_refused'
    assert all(math.isnan(value[k]) for k in s.MEASURE_KEYS)


@pytest.mark.parametrize('option,status,calls', [('error', 'judge_error', 2), ('malformed', 'judge_error', 2),
                                                ('infra', 'not_scored', 0), ('limit', 'not_scored', 0)])
def test_failure_statuses(tmp_path, rubric, option, status, calls):
    r = run()
    if option == 'infra':
        r['error'] = 'sandbox failed'
    if option == 'limit':
        r['turns'][0]['failure_class'] = 'usage-limit'
    value, meta, generated, _ = score_with(tmp_path, rubric, records=r, error=option == 'error', malformed=option == 'malformed')
    assert len(generated) == calls
    assert meta['measures_status'] == status
    assert all(math.isnan(value[k]) for k in s.MEASURE_KEYS)
    if calls:
        assert meta['judge_usage']['measures']['usd'] > 0


def make_report(rubric, *, ident='before', head='abc', dev=(2, 2, 2), held=(2, 2, 2), checks=True):
    records = []
    for case, counts in ((scenario(), dev), (scenario('held-case', 'heldout'), held)):
        for epoch, count in enumerate(counts, 1):
            r = run()
            r['turns'][0]['elapsed'] = epoch
            measures = s.parse_measures(json.dumps(reply(count)), rubric[0], r)
            value, meta = s.combine(case, r, {'delivered': checks}, [], measures=measures,
                                    measures_status='judged', measures_rubric=rubric[0],
                                    judge_usage={'rubric': {'input_tokens': 10, 'output_tokens': 5, 'usd': .001},
                                                 'measures': {'input_tokens': 20, 'output_tokens': 8, 'usd': .002}})
            records.append({**meta, 'epoch': epoch, 'value': value})
    run_meta = {'id': ident, 'git_head': head, 'worker': 'codex', 'judge': 'fixture-judge',
                'epochs': len(dev), 'seed': 'fresh'}
    fixture = {'scenario_files': [{'path': 'synthetic.json', 'sha256': 'fixture-sha'}],
               'scenario_digest': 'fixture-sha', 'rubric_digest': 'rubric-sha', 'rubric_version': rubric[0]['version'],
               'scenario_ids': {'dev': ['dev-case'], 'heldout': ['held-case']}, 'rubric': rubric[0]}
    return c.build_report(records, run_meta, fixture)


def test_report_split_spread_cost_and_unavailable_worker(rubric, tmp_path):
    report = make_report(rubric, dev=(0, 1, 2), held=(1, 1, 1))
    dev = report['live_evidence']['splits']['dev']['scenarios']['dev-case']
    measure = dev['measures']['avoidable_interventions']
    assert {k: measure[k] for k in ('n', 'mean', 'min', 'max', 'spread', 'total', 'not_observed', 'not_scored')} == {
        'n': 3, 'mean': 1, 'min': 0, 'max': 2, 'spread': 2, 'total': 3, 'not_observed': 0, 'not_scored': 0}
    assert [r['value'] for r in measure['epochs']] == [0, 1, 2]
    assert report['live_evidence']['splits']['heldout']['totals']['measures']['avoidable_interventions']['spread'] == 0
    assert {k: dev['latency']['1'][k] for k in ('median', 'min', 'max')} == {'median': 2, 'min': 1, 'max': 3}
    assert report['samples'][1]['measures']['avoidable_interventions'][0]['turn'] == 1
    assert report['unsupported_paths']['cost']['worker_usage'] == 'unavailable'
    assert report['unsupported_paths']['cost']['judge']['usd'] == pytest.approx(.018)
    assert report['unsupported_paths']['proactive_not_observed'] == 6
    md = c.render_markdown(report)
    for section in ('Fixture support', 'Live evidence', 'Uncertainty', 'Unsupported paths'):
        assert f'## {section}' in md
    assert 'unavailable (subscription CLI)' in md
    paths = c.write_report(report, tmp_path)
    assert all(p.exists() and p.stat().st_mode & 0o777 == 0o600 for p in paths)
    assert json.loads(paths[0].read_text()) == report


def labels_for(report):
    labels = []
    for sample in report['samples']:
        labels.append({**{k: sample[k] for k in ('run_id', 'scenario', 'worker', 'epoch')}, 'labeller': 'owner',
                       'measures': {'avoidable_interventions': [r['turn'] for r in sample['measures']['avoidable_interventions']],
                                    'required_approvals': [], 'unnecessary_interruptions': [],
                                    'proactive_usefulness': 'not_observed'}})
    return {'version': 'initiative-labels-v1', 'rubric': report['fixture_support']['rubric_version'], 'labels': labels}


def test_agreement_rate_and_fewer_than_five(rubric):
    report = make_report(rubric)
    labels = labels_for(report)
    assert c.agreement(report, labels)['avoidable_interventions']['agreed'] == 6
    labels['labels'] = labels['labels'][:3]
    labels['labels'][0]['measures']['avoidable_interventions'] = []
    result = c.agreement(report, labels)['avoidable_interventions']
    assert result['uncertain']  # 1/3 exceeds 1/5, even with fewer than five labels
    assert result['heldout_status'] == 'unlabelled' and result['heldout_labelled'] == 0
    full = labels_for(report)
    full['labels'] = full['labels'][:5]
    full['labels'][0]['measures']['avoidable_interventions'] = []
    result = c.agreement(report, full)['avoidable_interventions']
    assert not result['uncertain']  # exactly 1/5 is not above the threshold
    assert result['heldout_status'] == 'certain'
    full = labels_for(report)
    for index in (0, 5):
        full['labels'][index]['measures']['avoidable_interventions'] = []
    assert c.agreement(report, full)['avoidable_interventions']['uncertain']
    full['labels'].reverse()
    assert c.agreement(report, full)['avoidable_interventions']['uncertain']


@pytest.mark.parametrize('bad', ['version', 'duplicate', 'missing', 'null', 'bad_list', 'extra_measure', 'bad_score'])
def test_bad_labels_fail(rubric, bad):
    report = make_report(rubric)
    labels = labels_for(report)
    if bad == 'version':
        labels['rubric'] = 'wrong'
    elif bad == 'duplicate':
        labels['labels'].append(copy.deepcopy(labels['labels'][0]))
    elif bad == 'missing':
        labels['labels'][0]['scenario'] = 'missing'
    elif bad == 'null':
        labels['labels'][0]['measures']['avoidable_interventions'] = None
    elif bad == 'bad_list':
        labels['labels'][0]['measures']['required_approvals'] = ['1']
    elif bad == 'extra_measure':
        labels['labels'][0]['measures']['extra'] = []
    else:
        labels['labels'][0]['measures']['proactive_usefulness'] = .5
    message = {'version':'Invalid labels file or different rubric version',
               'duplicate':'Duplicate human label', 'missing':'Label sample does not exist',
               'null':'Human list label required', 'bad_list':'Unknown/duplicate human turn or message',
               'extra_measure':'Malformed human label', 'bad_score':'Invalid proactive human label'}[bad]
    with pytest.raises(ValueError, match=message):
        c.agreement(report, labels)


def test_template_heldout_and_extra_ids(rubric):
    report = make_report(rubric)
    template = c.labels_template(report)
    assert len(template['labels']) == 3
    assert all(r['scenario'] == 'held-case' and all(v is None for v in r['measures'].values()) for r in template['labels'])
    assert len(c.labels_template(report, ['dev-case'])['labels']) == 6
    with pytest.raises(ValueError, match='^Extra scenario ids must exist in the report$'):
        c.labels_template(report, ['unknown'])


@pytest.mark.parametrize('field', ['judge', 'epochs', 'worker', 'scenario_digest', 'rubric_version', 'rubric_digest',
                                   'scenario_ids', 'stopped', 'duplicate', 'missing_epoch'])
def test_compare_refuses_mismatched_conditions(rubric, field):
    a, b = make_report(rubric), make_report(rubric, ident='after', head='def')
    if field in ('judge', 'epochs', 'worker'):
        b['live_evidence'][field] = 'different'
    elif field in ('scenario_digest', 'rubric_version', 'rubric_digest', 'scenario_ids'):
        b['fixture_support'][field] = 'different'
    elif field == 'stopped':
        b['live_evidence']['stopped'] = {'tripped_by': 'usage-limit'}
    elif field == 'duplicate':
        b['samples'].append(b['samples'][0])
    else:
        a['samples'].pop()
        b['samples'].pop()
    message = ('Cannot compare different ' + field if field in
               ('worker','epochs','scenario_digest','rubric_version','rubric_digest','scenario_ids') else
               {'judge':'Cannot compare eval judges', 'stopped':'Cannot compare a stopped C2 report',
                'duplicate':'Duplicate result ids', 'missing_epoch':'Incomplete paired scenario/epoch selection'}[field])
    with pytest.raises(ValueError, match=message):
        c.compare(a, b)


def test_comparison_better_and_not_observed_stays_non_numeric(rubric):
    a = make_report(rubric)
    b = make_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1))
    result = c.compare(a, b)
    assert result['verdict'] == 'better'
    assert not result['failed_conditions']
    for split in ('dev', 'heldout'):
        row = next(iter(result['splits'][split].values()))['proactive_usefulness']
        assert row['change'] == 'not_observed' and 'delta' not in row
    assert c.HELDOUT_RULE in c.compare_markdown(result)


def with_infra_row(report, rubric):
    records = copy.deepcopy(report['samples'])
    failed_run = {**run(), 'error': 'sandbox failed before observation'}
    case = scenario()
    checks, failures = s.deterministic_checks(case, failed_run)
    value, meta = s.combine(case, failed_run, checks, failures, measures_status='not_scored',
                            measures_rubric=rubric[0])
    records[0] = {**meta, 'epoch': 1, 'value': value}
    return c.build_report(records, report['live_evidence'], report['fixture_support'])


@pytest.mark.parametrize('arms', [('baseline',), ('candidate',), ('baseline', 'candidate')])
def test_compare_harness_in_either_or_both_arms_keeps_infra_unscored(rubric, arms):
    reports = {'baseline': make_report(rubric),
               'candidate': make_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1))}
    assert c.compare(reports['baseline'], reports['candidate'])['verdict'] == 'better'
    for arm in arms:
        reports[arm] = with_infra_row(reports[arm], rubric)
        failed = reports[arm]['samples'][0]
        assert failed['checks'] == {'harness': False}
        assert failed['measures_status'] == 'not_scored'
        group = reports[arm]['live_evidence']['splits']['dev']['scenarios']['dev-case']
        assert group['measures']['avoidable_interventions']['not_scored'] == 1
        assert group['checks']['delivered']['n'] == 3
        assert group['checks']['delivered']['pass_rate'] == pytest.approx(2 / 3)
    result = c.compare(reports['baseline'], reports['candidate'])
    assert result['verdict'] == 'not better'
    assert 'complete_judged_pairs' in result['failed_conditions']
    dev = result['splits']['dev']['dev-case']
    assert dev['infra_rows'] == {arm: int(arm in arms) for arm in reports}
    assert dev['check_delivered']['baseline']['mean'] == (2 / 3 if 'baseline' in arms else 1)
    assert dev['check_delivered']['candidate']['mean'] == (2 / 3 if 'candidate' in arms else 1)
    assert ('check_harness' in dev) == (len(arms) == 2)
    assert result['splits']['heldout']['held-case']['infra_rows'] == {'baseline': 0, 'candidate': 0}


@pytest.mark.parametrize('infra_arms', [('candidate',), ('baseline', 'candidate')])
def test_compare_harness_does_not_allow_real_check_selection_difference(rubric, infra_arms):
    reports = {'baseline': make_report(rubric), 'candidate': make_report(rubric, ident='after', head='def')}
    for arm in infra_arms:
        reports[arm] = with_infra_row(reports[arm], rubric)
    # Harness differences are comparable before a genuine selection mismatch.
    result = c.compare(reports['baseline'], reports['candidate'])
    assert result['splits']['dev']['dev-case']['infra_rows']['candidate'] == 1
    checks = reports['candidate']['live_evidence']['splits']['dev']['scenarios']['dev-case']['checks']
    checks['preparation'] = copy.deepcopy(checks['delivered'])
    with pytest.raises(ValueError, match='^Paired deterministic check selections differ$'):
        c.compare(reports['baseline'], reports['candidate'])


@pytest.mark.parametrize('dev,held', [((1, 1, 1), (3, 3, 3)),  # heldout regression
                                     ((1, 1, 1), (2, 2, 2)),  # dev-only gain
                                     ((0, 1, 2), (0, 1, 2))])  # gain inside spread

def test_comparison_requires_certain_heldout_gain(rubric, dev, held):
    a = make_report(rubric)
    b = make_report(rubric, ident='after', head='def', dev=dev, held=held)
    result = c.compare(a, b)
    assert result['verdict'] == 'not better'
    assert 'certain_heldout_gain' in result['failed_conditions']
    if dev == (0, 1, 2):
        row = result['splits']['heldout']['held-case']['avoidable_interventions']
        assert row['change'] == 'uncertain'


def test_deterministic_regression_and_uncertain_labels_veto_better(rubric):
    a = make_report(rubric)
    b = make_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1), checks=False)
    assert c.compare(a, b)['verdict'] == 'not better'
    b = make_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1))
    labels = labels_for(b)
    for label in labels['labels'][:2]:
        label['measures']['avoidable_interventions'] = []
    b['uncertainty']['agreement'] = c.agreement(b, labels)
    result = c.compare(a, b)
    assert result['verdict'] == 'not better'
    assert 'avoidable_interventions' in result['uncertain_measures']
    assert result['splits']['heldout']['held-case']['avoidable_interventions']['change'] == 'uncertain'


def args(rubric, **overrides):
    values = dict(worker='codex', epochs=3, cap_usd=None, judge='fixture-judge', instances=2,
                  turn_timeout=5, display='plain', rubric=rubric[1], seed=None)
    return Namespace(**(values | overrides))


def test_runner_refuses_seed_and_no_judge(rubric):
    for changes, env in (({'seed': ''}, {}), ({'seed': 'owner'}, {}), ({}, {'AGENTOS_EVAL_SEED': 'owner'}),
                         ({'judge': 'none'}, {}), ({'judge': ''}, {'AGENTOS_EVAL_JUDGE_MODEL': 'none'})):
        message = 'C2 refuses --seed/AGENTOS_EVAL_SEED' if changes.get('seed') is not None or env.get('AGENTOS_EVAL_SEED') else 'C2 refuses checks-only runs'
        with patch.dict('os.environ', env, clear=True), pytest.raises(SystemExit, match=message):
            cli.run_continuity(args(rubric, **changes), sweep=lambda *a, **k: pytest.fail('must not sweep'), )


def test_runner_selects_exact_eleven_bundled_ids(rubric, tmp_path):
    captured = []
    def sweep(ns, folder=None):
        captured.append(ns)
        assert folder == tmp_path
        return 2
    with patch.object(scenarios, 'load', wraps=scenarios.load) as load:
        assert cli.run_continuity(args(rubric), sweep=sweep, folder=tmp_path, ) == 2
    load.assert_called_once_with(include_local=False)
    selected = [r for r in scenarios.load(include_local=False) if 'exp-continuity' in r['tags']]
    assert len(selected) == 11
    assert set(captured[0].scenarios.split(',')) == {r['id'] for r in selected}
    assert captured[0].no_local and captured[0].seed == ''
    assert captured[0].measures == str(rubric[1])
    fixture = captured[0].continuity
    assert len(fixture['scenario_digest']) == 64
    assert {s: len(ids) for s, ids in fixture['scenario_ids'].items()} == {'dev': 7, 'heldout': 4}


def test_actual_sweep_path_is_injectable_and_writes_c2(rubric, tmp_path, monkeypatch):
    """Exercise real cmd_sweep/_write/records_from_samples boundary without Inspect."""
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path))
    monkeypatch.delenv('AGENTOS_EVAL_SEED', raising=False)
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        return 'task'
    def inspect_eval(task, **kwargs):
        assert task == 'task'
        run_meta = kwargs['metadata']
        assert run_meta['seed'] == 'fresh'
        assert sum(map(len, run_meta['continuity']['scenario_ids'].values())) == 11
        return [SimpleNamespace(samples=[], location='fixture.eval', results=None)]
    def sweep(ns, folder=None):
        return cli.cmd_sweep(ns, folder=folder, task_factory=factory, inspect_eval=inspect_eval,
                             key_loader=lambda judge: 'fixture-key')
    with redirect_stdout(io.StringIO()) as output:
        assert cli.run_continuity(args(rubric), sweep=sweep, folder=tmp_path, ) == 0
    assert not calls[0]['include_local']
    assert calls[0]['measures'] == str(rubric[1])
    report = json.loads(next(tmp_path.glob('*.c2.json')).read_text())
    assert report['live_evidence']['seed'] == 'fresh'
    assert report['uncertainty']['missing_samples'] == 33
    assert str(next(tmp_path.glob('*.c2.md'))) in output.getvalue()


def test_cli_template_report_and_compare_rebuild(rubric, tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path))
    a = make_report(rubric)
    b = make_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1))
    ap, _ = c.write_report(a, tmp_path)
    bp, _ = c.write_report(b, tmp_path)
    with redirect_stdout(io.StringIO()) as output:
        assert cli.main(['continuity', 'labels-template', str(ap)]) == 0
    assert len(json.loads(output.getvalue())['labels']) == 3
    with redirect_stdout(io.StringIO()) as output:
        assert cli.main(['continuity', 'compare', str(ap), str(bp)]) == 0
    assert 'better' in output.getvalue()
    assert (tmp_path / 'after.c2.compare.json').exists()
    # Read-log boundary, preserving the run-time fingerprint rather than current fixture bytes.
    run_meta = a['live_evidence'] | {'continuity': a['fixture_support']}
    log = SimpleNamespace(eval=SimpleNamespace(metadata=run_meta), samples=[], results=None)
    monkeypatch.setattr(cli, 'read_log', lambda path: log)
    with redirect_stdout(io.StringIO()):
        assert cli.main(['continuity', 'report', '--log', 'synthetic.eval']) == 0
    assert json.loads((tmp_path / 'reports/before.c2.json').read_text())['fixture_support'] == a['fixture_support']


FIXTURE_FOLDER = Path(__file__).resolve().parents[1] / 'evals/continuity'
REAL_RUBRIC = c.load_rubric(c.DEFAULT_RUBRIC)
REAL_CASES = json.loads((FIXTURE_FOLDER / 'initiative-judge-fixtures-v1.json').read_text())['cases']


def test_real_rubric_prompt_and_default_runner(monkeypatch):
    case = next(row for row in REAL_CASES if row['id'] == 'fixture-bare-ping')
    prompt = s.measures_prompt(scenarios.validate(case['scenario']), case['run'], REAL_RUBRIC)
    assert 'AGENTOS t1.b1 before' in prompt and 'AGENTOS t1 (status' in prompt
    assert case['run']['turns'][0]['sent_at'] in prompt
    for measure in REAL_RUBRIC['measures'].values():
        assert measure['definition'] in prompt
    assert REAL_RUBRIC['judge_instructions'] in prompt
    assert json.dumps(REAL_RUBRIC['output_schema'], ensure_ascii=False) in prompt
    monkeypatch.delenv('AGENTOS_EVAL_SEED', raising=False)
    captured = []
    monkeypatch.setattr(cli, 'cmd_sweep', lambda args: captured.append(args) or 0)
    assert cli.main(['continuity', 'run', '--judge', 'fixture-judge']) == 0
    assert Path(captured[0].measures) == FIXTURE_FOLDER / 'initiative-rubric-v2.json'
    assert captured[0].continuity['rubric'] == REAL_RUBRIC


@pytest.mark.parametrize('case', REAL_CASES, ids=lambda row: row['id'])
def test_real_fixture_replies_parse_or_reject(case):
    if case.get('_malformed'):
        with pytest.raises(ValueError, match='turn index out of range'):
            s.parse_measures(case['judge_reply'], REAL_RUBRIC, case['run'])
        return
    parsed = s.parse_measures(case['judge_reply'], REAL_RUBRIC, case['run'])
    expected = json.loads(case['judge_reply'])
    for measure in s.MEASURES[:3]:
        assert parsed[measure] == expected[measure]
    assert parsed['proactive_usefulness']['score'] is None
    assert parsed['proactive_observation']['value'] == 'not_observed'
    # The two delivered-output examples explicitly live outside the recorded run;
    # their _note must never become evidence or a numeric proactive score.
    if case.get('_note', '').startswith('UNSUPPORTED'):
        assert case['human']['proactive_usefulness'] in (0, 2)
        assert parsed['proactive_usefulness']['value'] == 'not_observed'


def test_real_fixture_agreement_has_exactly_two_record_supported_disagreements():
    # Calibration uses the record-supported fixtures. The two hypothetical
    # deliveries have no provenance and cannot calibrate a delivered-output grade.
    supported = [case for case in REAL_CASES if not case.get('_malformed')
                 and not case.get('_note', '').startswith('UNSUPPORTED')]
    assert len(supported) == len(REAL_CASES) - 3
    records, human_labels = [], []
    for case in supported:
        scenario_case = scenarios.validate(case['scenario'])
        parsed = s.parse_measures(case['judge_reply'], REAL_RUBRIC, case['run'])
        value, meta = s.combine(scenario_case, case['run'], {'delivered': True}, [],
                                measures=parsed, measures_status='judged', measures_rubric=REAL_RUBRIC)
        records.append({**meta, 'value': value, 'epoch': 1})
        human_labels.append({'run_id': 'fixture', 'scenario': case['id'], 'worker': 'codex', 'epoch': 1,
                             'labeller': 'contributor', 'measures': case['human']})
    fixture = c.fixture_support([scenarios.validate(case['scenario']) for case in supported], c.DEFAULT_RUBRIC)
    result = c.build_report(records, {'id': 'fixture', 'seed': 'fresh', 'worker': 'codex', 'epochs': 1,
                                      'judge': 'fixture', 'git_head': 'fixture'}, fixture,
                            {'version': 'initiative-labels-v1', 'rubric': REAL_RUBRIC['version'], 'labels': human_labels})
    agreement = result['uncertainty']['agreement']
    actual = {(row['scenario'], measure) for measure, table in agreement.items() for row in table['disagreements']}
    expected = {(case['id'], case['_disagreement']) for case in REAL_CASES if '_disagreement' in case}
    assert actual == expected and len(actual) == 2
    assert agreement['avoidable_interventions']['uncertain'] is True
    assert {m for m, table in agreement.items() if table['uncertain']} == {'avoidable_interventions'}
    assert agreement['avoidable_interventions']['labelled'] == len(supported)


def test_only_measures_budget_refused_and_pass_off_still_has_one_call(tmp_path, rubric):
    from agentos_eval.budget import estimate_tokens
    reserved = JudgeBudget(tmp_path / 'pricing').cost(
        estimate_tokens(s.JUDGE_SYSTEM + s.judge_prompt(scenario(), run())), s.JUDGE_MAX_TOKENS)
    budget = JudgeBudget(tmp_path / 'limited.json', cap_usd=reserved)
    calls = []
    async def generate(system, prompt):
        calls.append(prompt)
        return SimpleNamespace(usage=None, completion=json.dumps({'dimensions': {'follow_through': {'score': 2}}}))
    values, meta = asyncio.run(s.score_run(scenario(), run(), 'grader', budget, str, generate, rubric[0]))
    assert len(calls) == 1 and meta['judge_status'] == 'judged'
    assert meta['measures_status'] == 'budget_refused'
    assert all(math.isnan(values[k]) for k in s.MEASURE_KEYS)
    assert meta['judge_usage']['rubric']['usd'] == reserved
    assert meta['judge_usage']['rubric']['input_tokens'] == 'unavailable'
    assert budget.status()['refused'] == 1
    calls.clear()
    off, metadata = asyncio.run(s.score_run(scenario(), run(), 'grader', JudgeBudget(tmp_path / 'off.json'), str, generate))
    assert len(calls) == 1 and metadata['measures_status'] == 'skipped'
    assert off.keys() == values.keys() and off['rubric_follow_through'] == 1
    assert all(math.isnan(off[k]) for k in s.MEASURE_KEYS)


def test_same_turn_distinct_intervention_kinds_are_not_silently_lost(rubric):
    data = reply(1)
    data['avoidable_interventions'].append({'turn': 1, 'kind': 'asked_entrusted_action', 'reason': 'separate ask'})
    parsed = s.parse_measures(json.dumps(data), rubric[0], run())
    values, _ = s.combine(scenario(), run(), {}, [], measures=parsed, measures_status='judged')
    assert len(parsed['avoidable_interventions']) == 2
    assert values['measure_avoidable_interventions'] == 2
    data['avoidable_interventions'].append(data['avoidable_interventions'][0])
    with pytest.raises(ValueError, match='duplicate'):
        s.parse_measures(json.dumps(data), rubric[0], run())


def test_cli_labels_on_comparison_and_duplicate_across_files(rubric, tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path))
    a = make_report(rubric)
    b = make_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1))
    ap, _ = c.write_report(a, tmp_path)
    bp, _ = c.write_report(b, tmp_path)
    lp, rp = tmp_path / 'a-labels.json', tmp_path / 'b-labels.json'
    lp.write_text(json.dumps(labels_for(a)))
    rp.write_text(json.dumps(labels_for(b)))
    with redirect_stdout(io.StringIO()) as out:
        assert cli.main(['continuity', 'compare', str(ap), str(bp), '--labels', str(lp), '--labels', str(rp)]) == 0
    assert '"verdict": "better"' in out.getvalue()
    with pytest.raises(ValueError, match='Duplicate human label'), redirect_stdout(io.StringIO()):
        cli.main(['continuity', 'compare', str(ap), str(bp), '--labels', str(lp), '--labels', str(lp)])


def test_sweep_records_survive_to_c2_and_usage_stop_refuses_comparison(rubric, tmp_path, monkeypatch):
    from agentos_eval import report as sweep_report
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path))
    monkeypatch.delenv('AGENTOS_EVAL_SEED', raising=False)
    def factory(**kwargs):
        assert kwargs['include_local'] is False and kwargs['measures'] == str(rubric[1])
        return kwargs
    def inspect_eval(task, **kwargs):
        samples = []
        for case in scenarios.load(include_local=False, ids=task['scenarios'].split(',')):
            measures = s.parse_measures(json.dumps(reply()), rubric[0], run())
            values, metadata = s.combine(case, run(), {'delivered': True}, [], measures=measures,
                                         measures_status='judged', measures_rubric=rubric[0])
            samples.append(SimpleNamespace(id=case['id'] + '@codex', epoch=1,
                                           scores={'secretary': SimpleNamespace(value=values, metadata=metadata)}))
        stopped = SimpleNamespace(metadata={'tripped_by': 'fixture@codex'}, early_stops=['pending'])
        return [SimpleNamespace(samples=samples, location='fixture.eval',
                                results=SimpleNamespace(early_stopping=stopped))]
    def sweep(ns, **kw):
        return cli.cmd_sweep(ns, task_factory=factory, inspect_eval=inspect_eval, key_loader=lambda _: 'fixture', **kw)
    with redirect_stdout(io.StringIO()):
        assert cli.run_continuity(args(rubric, epochs=1), sweep=sweep, folder=tmp_path) == 2
    result = json.loads(next(tmp_path.glob('*.c2.json')).read_text())
    assert len(result['samples']) == 11
    assert result['live_evidence']['stopped']['skipped'] == 1
    assert result['samples'][0]['measures_status'] == 'judged'
    assert result['samples'][0]['latency'][0]['elapsed'] == 1
    assert 'STOPPED' in c.render_markdown(result)
    with pytest.raises(ValueError, match='stopped'):
        c.compare(result, result)
    assert sweep_report.latest_report(tmp_path) is None


def test_report_outage_is_unscored_not_zero_and_keeps_missing_samples(rubric):
    case = scenario()
    sample = SimpleNamespace(id='dev-case@codex', epoch=1, scores={},
                             metadata={'scenario': case, 'worker': 'codex'}, error='outage')
    record = records_from_samples([sample])[0]
    fixture = {'scenario_ids': {'dev': ['dev-case'], 'heldout': []}, 'rubric_version': rubric[0]['version'],
               'rubric_digest': 'digest', 'scenario_digest': 'digest', 'rubric': rubric[0]}
    data = c.build_report([record], {'id': 'outage', 'worker': 'codex', 'judge': 'fixture', 'seed': 'fresh', 'epochs': 3}, fixture)
    measure = data['live_evidence']['splits']['dev']['scenarios']['dev-case']['measures']['avoidable_interventions']
    assert measure['mean'] is None and measure['n'] == 0 and measure['not_scored'] == 1
    assert data['uncertainty']['status_counts'] == {'not_scored': 1}
    assert data['uncertainty']['missing_samples'] == 2


def test_preparation_excerpts_require_typed_unsolicited_origin_and_sent_receipt(rubric):
    r = run()
    r['turns'][0]['task_id'] = 'owner-work'
    row = {'id': 'account', 'last_work_id': 'account-work', 'last_delivery': 'sent',
           'last_status': 'succeeded', 'last_result': 'Observed account result excerpt'}
    r['diff']['preparations'] = [row]
    # A generic preparation could have been run-now: its excerpt is not enough.
    assert not s.proactive_observation(r)['observed']
    row['line_account'] = True
    observation = s.proactive_observation(r)
    assert observation['observed'] and observation['outputs'][0]['text'] == row['last_result']
    prompt = s.measures_prompt(scenario(), r, rubric[0])
    assert row['last_result'] in prompt
    parsed = s.parse_measures(json.dumps(reply(proactive=2)), rubric[0], r)
    assert parsed['proactive_usefulness']['score'] == 2
    value, metadata = s.combine(scenario(), r, {}, [], measures=parsed, measures_status='judged')
    assert value['measure_proactive_usefulness'] == 1
    assert metadata['proactive_observation']['observed']
    null = reply(proactive=None)
    with pytest.raises(ValueError, match='needs a grade'):
        s.parse_measures(json.dumps(null), rubric[0], r)
    for field, value in [('last_work_id', 'owner-work'), ('last_delivery', 'pending'), ('last_result', '')]:
        broken = copy.deepcopy(r)
        broken['diff']['preparations'][0][field] = value
        assert not s.proactive_observation(broken)['observed']


def test_agreement_three_disagreements_in_eleven_is_order_independent(rubric):
    report = make_report(rubric, dev=(2,) * 6, held=(2,) * 6)
    labels = labels_for(report)
    labels['labels'] = labels['labels'][:11]
    # Five apart: the old sliding window sees at most one disagreement per window.
    for index in (0, 5, 10):
        labels['labels'][index]['measures']['avoidable_interventions'] = []
    expected = c.agreement(report, labels)
    assert expected['avoidable_interventions']['uncertain']
    assert expected['avoidable_interventions']['labelled'] == 11
    assert len(expected['avoidable_interventions']['disagreements']) == 3
    for ordering in (list(reversed(labels['labels'])), labels['labels'][4:] + labels['labels'][:4]):
        actual = c.agreement(report, {**labels, 'labels': ordering})
        assert actual == expected
        for measure in s.MEASURES:
            assert {k: v for k, v in actual[measure].items() if k not in ('rows', 'disagreements')} == {
                k: v for k, v in expected[measure].items() if k not in ('rows', 'disagreements')}
            assert sorted((r['scenario'], r['epoch']) for r in actual[measure]['disagreements']) == sorted(
                (r['scenario'], r['epoch']) for r in expected[measure]['disagreements'])


@pytest.mark.parametrize('messages,kept,conflicts', [
    (['t1.b1'], ['t1.b1'], 0),
    (['t1'], [], 1),
    (['t1', 't1.b1', 't3.b1'], ['t1.b1', 't3.b1'], 1),
])
def test_avoidable_intervention_deduplicates_only_answer_message(rubric, messages, kept, conflicts):
    data = reply(1, interruptions=messages)
    parsed = s.parse_measures(json.dumps(data), rubric[0], run())
    assert [row['turn'] for row in parsed['avoidable_interventions']] == [1]
    assert [row['message'] for row in parsed['unnecessary_interruptions']] == kept
    assert len(parsed['inconsistencies']) == conflicts
    if conflicts:
        assert parsed['inconsistencies'] == [{
            'turn': 1, 'message': 't1', 'interruption_item': data['unnecessary_interruptions'][0],
            'reason': 'Judge listed an answer as both avoidable intervention and interruption; counted only as intervention'}]
    value, meta = s.combine(scenario(), run(), {}, [], measures=parsed, measures_status='judged')
    assert value['measure_avoidable_interventions'] == 1
    assert value['measure_unnecessary_interruptions'] == len(kept)
    assert meta['measures']['inconsistencies'] == parsed['inconsistencies']


def test_errored_samples_are_not_labellable_and_labels_fail_clearly(rubric):
    report = make_report(rubric)
    sample = SimpleNamespace(id='held-case@codex', epoch=1, scores={},
                             metadata={'scenario': scenario('held-case', 'heldout'), 'worker': 'codex'}, error='outage')
    errored = {**records_from_samples([sample])[0], 'run_id': report['live_evidence']['id']}
    report['samples'][3] = errored
    assert 'turn_count' not in errored
    template = c.labels_template(report)
    assert [(row['scenario'], row['epoch']) for row in template['labels']] == [('held-case', 2), ('held-case', 3)]
    assert template['not_labellable'] == [{
        'run_id': 'before', 'scenario': 'held-case', 'worker': 'codex', 'epoch': 1,
        'reason': 'not labellable: not_scored'}]
    bad = {'version': 'initiative-labels-v1', 'rubric': rubric[0]['version'], 'labels': [{
        'run_id': 'before', 'scenario': 'held-case', 'worker': 'codex', 'epoch': 1,
        'labeller': 'owner', 'measures': {'avoidable_interventions': [], 'required_approvals': [],
                                         'unnecessary_interruptions': [], 'proactive_usefulness': 'not_observed'}}]}
    with pytest.raises(ValueError, match='not labellable: not_scored'):
        c.agreement(report, bad)
    # A filled template may retain its separate exclusion list.
    good = copy.deepcopy(template)
    for row in good['labels']:
        row['measures'] = {'avoidable_interventions': [1, 2], 'required_approvals': [],
                           'unnecessary_interruptions': [], 'proactive_usefulness': 'not_observed'}
    assert c.agreement(report, good)['avoidable_interventions']['labelled'] == 2


def test_compare_candidate_labels_do_not_erase_baseline_uncertainty(rubric, tmp_path):
    baseline = make_report(rubric)
    candidate = make_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1))
    labels = labels_for(baseline)
    for label in labels['labels'][:2]:
        label['measures']['avoidable_interventions'] = []
    # Model the persisted report --labels path (including legacy reports storing only agreement rows).
    baseline['uncertainty']['agreement'] = c.agreement(baseline, labels)
    assert baseline['uncertainty']['agreement']['avoidable_interventions']['uncertain']
    ap, _ = c.write_report(baseline, tmp_path)
    bp, _ = c.write_report(candidate, tmp_path)
    supplied = tmp_path / 'candidate-labels.json'
    supplied.write_text(json.dumps(labels_for(candidate)))
    with redirect_stdout(io.StringIO()):
        assert cli.main(['continuity', 'compare', str(ap), str(bp), '--labels', str(supplied)]) == 0
    compared = json.loads((tmp_path / 'after.c2.compare.json').read_text())
    assert compared['uncertain_measures'] == ['avoidable_interventions']
    assert compared['splits']['heldout']['held-case']['avoidable_interventions']['change'] == 'uncertain'
    assert compared['verdict'] == 'not better'
    assert 'certain_heldout_gain' in compared['failed_conditions']


@pytest.mark.parametrize('legacy', [False, True])
def test_labels_merge_with_stored_labels_and_reject_repeated_sample(rubric, tmp_path, legacy):
    report = make_report(rubric)
    labels = labels_for(report)
    first = {**labels, 'labels': labels['labels'][:2]}
    rest = {**labels, 'labels': labels['labels'][2:]}
    if legacy:
        report['uncertainty']['agreement'] = c.agreement(report, first)
    else:
        report = c.with_labels(report, first)
    merged = c.with_labels(report, rest)
    assert merged['uncertainty']['agreement']['avoidable_interventions']['labelled'] == 6
    assert len(merged['uncertainty']['labels']) == 6
    assert report['uncertainty']['agreement']['avoidable_interventions']['labelled'] == 2
    with pytest.raises(ValueError, match='Duplicate human label'):
        c.with_labels(report, first)
    # CLI must also reject a stored-versus-passed duplicate, not just duplicates among new files.
    candidate = make_report(rubric, ident='after', head='def')
    ap, _ = c.write_report(report, tmp_path)
    bp, _ = c.write_report(candidate, tmp_path)
    repeated = tmp_path / 'repeat.json'
    repeated.write_text(json.dumps(first))
    with pytest.raises(ValueError, match='Duplicate human label'), redirect_stdout(io.StringIO()):
        cli.main(['continuity', 'compare', str(ap), str(bp), '--labels', str(repeated)])


def test_unlabelled_heldout_blocks_gain_only_when_labels_exist(rubric):
    baseline = make_report(rubric)
    candidate = make_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1))
    judge_only = c.compare(baseline, candidate)
    assert judge_only['verdict'] == 'better'
    assert judge_only['verdict_basis'] == 'judge-only, unlabelled'
    assert 'Verdict: **better** (judge-only, unlabelled)' in c.compare_markdown(judge_only)
    assert 'judged deltas are judge-only, unlabelled' in c.render_markdown(baseline)
    labels = labels_for(candidate)
    labels['labels'] = [row for row in labels['labels'] if row['scenario'] == 'dev-case']
    candidate = c.with_labels(candidate, labels)
    for row in candidate['uncertainty']['agreement'].values():
        assert row['heldout_status'] == 'unlabelled' and row['heldout_labelled'] == 0
    assert 'Held-out measures without human labels are unlabelled: neither certain nor uncertain.' in c.render_markdown(candidate)
    compared = c.compare(baseline, candidate)
    assert compared['verdict'] == 'not better'
    assert compared['failed_conditions'] == ['certain_heldout_gain']
    # Covering just the candidate's held-out cases still leaves the baseline unlabelled.
    rest = labels_for(candidate)
    rest['labels'] = [row for row in rest['labels'] if row['scenario'] == 'held-case']
    candidate = c.with_labels(candidate, rest)
    assert c.compare(baseline, candidate)['failed_conditions'] == ['certain_heldout_gain']
    baseline = c.with_labels(baseline, labels_for(baseline))
    assert c.compare(baseline, candidate)['verdict'] == 'better'


def test_fixture_digest_includes_all_contributing_bundled_files(rubric, tmp_path):
    import hashlib
    folder = tmp_path / 'scenarios'
    folder.mkdir()
    for name, ident, tags in [('z.json', 'case-zed', ['exp-continuity']),
                              ('a.json', 'case-aye', ['exp-continuity']), ('ignore.json', 'case-other', [])]:
        (folder / name).write_text(json.dumps({'id': ident, 'turns': ['Synthetic'], 'tags': tags}))
    loaded = scenarios.load(dirs=[folder], include_local=False)
    selected = [row for row in loaded if 'exp-continuity' in row['tags']]
    fingerprint = c.fixture_support(selected, rubric[1], scenario_dir=folder)
    assert [row['relative_path'] for row in fingerprint['scenario_files']] == ['a.json', 'z.json']
    digest = hashlib.sha256()
    for name in ('a.json', 'z.json'):
        content = (folder / name).read_bytes()
        assert next(row for row in fingerprint['scenario_files'] if row['relative_path'] == name)['sha256'] == hashlib.sha256(content).hexdigest()
        for part in (name.encode(), content):
            digest.update(len(part).to_bytes(8, 'big'))
            digest.update(part)
    assert fingerprint['scenario_digest'] == digest.hexdigest()
    (folder / 'ignore.json').write_text(json.dumps({'id': 'case-other', 'turns': ['Changed but unrelated']}))
    assert c.fixture_support(selected, rubric[1], scenario_dir=folder)['scenario_digest'] == fingerprint['scenario_digest']
    (folder / 'z.json').write_text((folder / 'z.json').read_text() + '\n')
    changed = c.fixture_support(selected, rubric[1], scenario_dir=folder)
    assert changed['scenario_digest'] != fingerprint['scenario_digest']
    # Paths participate too, and absolute checkout location does not.
    (folder / 'z.json').rename(folder / 'b.json')
    assert c.fixture_support(selected, rubric[1], scenario_dir=folder)['scenario_digest'] != changed['scenario_digest']
    import shutil
    other = tmp_path / 'other-checkout'
    shutil.copytree(folder, other)
    assert c.fixture_support(selected, rubric[1], scenario_dir=other)['scenario_digest'] == c.fixture_support(selected, rubric[1], scenario_dir=folder)['scenario_digest']


@pytest.mark.parametrize('seed', [False, None, ''])
def test_report_rebuild_accepts_legacy_fresh_seed_metadata(rubric, tmp_path, monkeypatch, seed):
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path))
    original = make_report(rubric)
    metadata = {'id': 'real-shape', 'git_head': 'd256835', 'worker': 'codex', 'instances': 3,
                'epochs': 3, 'judge': 'openai/gpt-6.1-terra', 'judge_key': 'environment',
                'seed': seed, 'continuity': original['fixture_support']}
    log = SimpleNamespace(eval=SimpleNamespace(metadata=metadata), samples=[], results=None)
    monkeypatch.setattr(cli, 'read_log', lambda _: log)
    with redirect_stdout(io.StringIO()):
        assert cli.main(['continuity', 'report', '--log', 'baseline.eval']) == 0
    rebuilt = json.loads((tmp_path / 'reports/real-shape.c2.json').read_text())
    run = rebuilt['live_evidence']
    assert run['seed'] == 'fresh' and run['started'] == 'unavailable'
    assert run['judge_key'] == 'environment' and run['instances'] == 3
    assert run['git_head'] == 'd256835' and 'stopped' not in run
    assert metadata['seed'] is seed  # normalization does not rewrite the log object


@pytest.mark.parametrize('seed', ['owner-seed', True])
def test_report_rebuild_refuses_seeded_log(rubric, tmp_path, monkeypatch, seed):
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path))
    metadata = make_report(rubric)['live_evidence'] | {'seed': seed, 'continuity': make_report(rubric)['fixture_support']}
    log = SimpleNamespace(eval=SimpleNamespace(metadata=metadata), samples=[], results=None)
    monkeypatch.setattr(cli, 'read_log', lambda _: log)
    with pytest.raises(ValueError, match='requires a fresh data folder'):
        cli.main(['continuity', 'report', '--log', 'seeded.eval'])
    assert not (tmp_path / 'reports').exists()


def test_log_metadata_fallbacks_preserve_provenance_and_stops():
    from datetime import datetime
    metadata = {'seed': False, 'stopped': {'tripped_by': 'stored', 'skipped': 1}}
    log = SimpleNamespace(eval=SimpleNamespace(metadata=metadata, created='2026-11-14T09:00:00+00:00',
                                               config=SimpleNamespace(max_samples=3)), results=None)
    recovered = cli.continuity_run_from_log(log)
    assert recovered['started'] == datetime.fromisoformat(log.eval.created).timestamp()
    assert recovered['started_source'] == 'inspect.eval.created'
    assert recovered['instances'] == 3 and recovered['judge_key'] == 'unavailable'
    assert recovered['stopped'] == metadata['stopped']
    metadata.update(started=1234.5, judge_key='environment', instances=2)
    recovered = cli.continuity_run_from_log(log)
    assert recovered['started'] == 1234.5 and 'started_source' not in recovered
    assert recovered['instances'] == 2 and recovered['judge_key'] == 'environment'
    summary = SimpleNamespace(metadata={'tripped_by': 'observed'}, early_stops=['one', 'two'])
    log.results = SimpleNamespace(early_stopping=summary)
    assert cli.continuity_run_from_log(log)['stopped'] == {'tripped_by': 'observed', 'skipped': 2}
    metadata.pop('instances')
    metadata.pop('started')
    log.eval.created, log.eval.config = 'invalid historical timestamp', None
    recovered = cli.continuity_run_from_log(log)
    assert recovered['started'] == recovered['instances'] == 'unavailable'


@pytest.mark.parametrize('stopped', [False, True])
def test_new_sweep_metadata_and_log_rebuild_match(rubric, tmp_path, monkeypatch, stopped):
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path))
    monkeypatch.delenv('AGENTOS_EVAL_SEED', raising=False)
    captured = []
    def factory(**kwargs):
        # The real task's metadata is the source of the legacy boolean.
        return SimpleNamespace(metadata={'seed': False, 'worker': kwargs['worker'],
                                         'instances': kwargs['instances'], 'judge': kwargs['judge']})
    def evaluate(task, **kwargs):
        assert task.metadata == kwargs['metadata']
        assert task.metadata['seed'] == 'fresh'
        assert all(k in task.metadata for k in ('started', 'judge_key', 'instances'))
        summary = SimpleNamespace(metadata={'tripped_by': 'fixture@codex'}, early_stops=['pending']) if stopped else None
        log = SimpleNamespace(eval=SimpleNamespace(metadata={**kwargs['metadata'], **task.metadata}),
                              samples=[], results=SimpleNamespace(early_stopping=summary), location='fixture.eval')
        captured.append(log)
        return [log]
    def sweep(ns, **kwargs):
        return cli.cmd_sweep(ns, task_factory=factory, inspect_eval=evaluate,
                             key_loader=lambda _: 'environment', **kwargs)
    with redirect_stdout(io.StringIO()):
        assert cli.run_continuity(args(rubric), sweep=sweep, folder=tmp_path / 'reports') == (2 if stopped else 0)
    path = next((tmp_path / 'reports').glob('*.c2.json'))
    original = json.loads(path.read_text())
    assert bool(original['live_evidence'].get('stopped')) is stopped
    monkeypatch.setattr(cli, 'read_log', lambda _: captured[0])
    with redirect_stdout(io.StringIO()):
        assert cli.main(['continuity', 'report', '--log', 'fixture.eval']) == 0
    assert json.loads(path.read_text()) == original


@pytest.mark.parametrize('extra,timeout', [([], cli.JUDGMENT_TIMEOUT), (['--judgment-timeout', '120'], 120)])
def test_c2_judgment_timeout_reaches_task_and_metadata(rubric, tmp_path, monkeypatch, extra, timeout):
    monkeypatch.delenv('AGENTOS_EVAL_SEED', raising=False)
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path))
    captured = []
    def factory(**kwargs):
        assert kwargs['judgment_timeout'] == timeout
        return SimpleNamespace(metadata={'seed': False})
    def evaluate(task, **kwargs):
        metadata = kwargs['metadata']
        assert metadata['judgment_timeout'] == timeout
        assert task.metadata['judgment_timeout'] == timeout
        captured.append(metadata)
        return [SimpleNamespace(samples=[], results=None, location='fixture.eval')]
    real_sweep = cli.cmd_sweep
    monkeypatch.setattr(cli, 'cmd_sweep', lambda ns: real_sweep(
        ns, task_factory=factory, inspect_eval=evaluate, key_loader=lambda _: 'fixture'))
    with redirect_stdout(io.StringIO()):
        assert cli.main(['continuity', 'run', '--judge', 'fixture-judge', '--rubric', str(rubric[1]), *extra]) == 0
    result = json.loads(next((tmp_path / 'reports').glob('*.c2.json')).read_text())
    assert result['live_evidence']['judgment_timeout'] == timeout
    assert len(captured) == 1


def judgment_ai_failure_report(rubric, *, ident='before', head='abc', dev=(2, 2, 2), held=(2, 2, 2)):
    result = make_report(rubric, ident=ident, head=head, dev=dev, held=held)
    records = copy.deepcopy(result['samples'])
    # Readiness metadata and scorer failure receipt are independent evidence paths.
    for i in range(3):
        records[i]['measures_status'] = 'not_scored'
    records[0]['judgment_ai'] = {'ready': False, 'requalified': True, 'waited': 318}
    records[0]['failures'] = [{'kind': 'infra', 'name': 'judgment_ai', 'reason': 'not ready'}]
    records[1]['failures'] = [{'kind': 'infra', 'name': 'judgment_ai', 'reason': 'not ready'}]
    records[2]['judgment_ai'] = {'ready': False, 'waited': 318}
    return c.build_report(records, result['live_evidence'], result['fixture_support'])


def test_report_prominently_counts_judgment_ai_failures(rubric):
    result = judgment_ai_failure_report(rubric)
    state = result['live_evidence']['judgment_ai']
    assert state['not_ready'] == 3 and state['unscored_measures'] == 3 and state['samples'] == 6
    assert result['uncertainty']['judgment_ai'] == state
    assert result['uncertainty']['status_counts'] == {'not_scored': 3, 'judged': 3}
    markdown = c.render_markdown(result)
    assert "Still's Judgment AI was not ready in 3 of 6 samples" in state['warning']
    assert 'not a representative observation' in state['warning']
    live, uncertainty = markdown.split('## Live evidence')[1].split('## Uncertainty')
    assert state['warning'] in live and state['warning'] in uncertainty
    assert not c.judgment_ai_status([{'measures_status': 'not_scored'}])['not_ready']
    assert c.judgment_ai_status([{'judgment_ai': {'ready': False}, 'measures_status': 'judged'}])['unscored_measures'] == 0


@pytest.mark.parametrize('arm', ['baseline', 'candidate'])
def test_compare_requires_opt_in_for_missing_judgment_ai_measures(rubric, arm):
    baseline = make_report(rubric)
    candidate = make_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1))
    if arm == 'baseline':
        baseline = judgment_ai_failure_report(rubric)
    else:
        candidate = judgment_ai_failure_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1))
    # Gate also protects historical reports lacking the new aggregate readiness section.
    baseline['live_evidence'].pop('judgment_ai', None)
    candidate['live_evidence'].pop('judgment_ai', None)
    with pytest.raises(ValueError, match='infra:judgment_ai.*--allow-partial'):
        c.compare(baseline, candidate)
    result = c.compare(baseline, candidate, allow_partial=True)
    assert result['partial'] is True and result['verdict'] == 'not better'
    assert result['judgment_ai'][arm]['unscored_measures'] == 3
    assert 'representative_judgment_ai' in result['failed_conditions']
    assert 'PARTIAL' in c.compare_markdown(result)
    assert 'verdict cannot be better' in c.compare_markdown(result)


def test_compare_cli_allow_partial_and_clean_comparison(rubric, tmp_path):
    baseline = make_report(rubric)
    candidate = make_report(rubric, ident='after', head='def', dev=(1, 1, 1), held=(1, 1, 1))
    clean = c.compare(baseline, candidate, allow_partial=True)
    assert clean['partial'] is False and clean['verdict'] == 'better'
    baseline = judgment_ai_failure_report(rubric)
    bp, _ = c.write_report(baseline, tmp_path)
    cp, _ = c.write_report(candidate, tmp_path)
    with pytest.raises(ValueError, match='--allow-partial'):
        cli.main(['continuity', 'compare', str(bp), str(cp)])
    with redirect_stdout(io.StringIO()) as output:
        assert cli.main(['continuity', 'compare', str(bp), str(cp), '--allow-partial']) == 0
    compared = json.loads((tmp_path / 'after.c2.compare.json').read_text())
    assert compared['partial'] and compared['verdict'] == 'not better'
    assert 'PARTIAL' in output.getvalue()


@pytest.mark.parametrize('grade', [0, 1, 2, None])
def test_due_grade_provenance_two_calls_and_wait_ask_attribution(tmp_path, grade):
    from test_eval_come_due import Trace, SPEC, SOURCE
    trace=Trace(tmp_path,quiet=grade is None)
    trace.task_events = [{'tool': 'local_authority', 'status': 'requested'}]
    receipt=trace.wait(spec={'until':'settled' if grade is None else 'delivered'})
    case=scenarios.validate({'id':'due-fixture','channel':'telegram','rubric':['follow_through'],
                             'turns':['entrust',{'wait_due':SPEC},'requested followup']})
    messages=[{'text':'Please supply that known fact?', 'delivery_id':'ask'},
              {'text':'Still working', 'delivery_id':'ping'}]
    r={'worker':'codex','turns':[
        {**SOURCE,'say':'entrust','status':'succeeded','answer':'accepted'},
        {'kind':'wait_due','status':receipt['status'],'due_observation':receipt,
         'events': receipt['work_events'], 'before_answer':messages},
        {'say':'requested followup','status':'succeeded','answer':'requested result'}],
       'due_observations':[receipt], 'diff':{}}
    rr=c.load_rubric(c.DEFAULT_RUBRIC)
    generated=[]
    async def generate(system,prompt):
        generated.append(prompt)
        completion=({'dimensions':{'follow_through':{'score':2,'reason':'fixture'}},'summary':'fixture'} if len(generated)==1
                    else {'avoidable_interventions':[{'turn':2,'kind':'asked_known_fact','reason':'t2.b1 asks again'}],
                          'required_approvals':[],
                          'unnecessary_interruptions':[{'message':'t2.b1','reason':'same ask'}, {'message':'t2.b2','reason':'separate ping'}],
                          'proactive_usefulness':{'score':grade,'reason':'shown result'}})
        return SimpleNamespace(completion=json.dumps(completion),usage=None,model='fixture')
    value,meta=asyncio.run(s.score_run(case,r,'fixture',JudgeBudget(tmp_path/'budget'),lambda x:x,generate,rr))
    assert len(generated)==2
    assert value['check_due_completed']==1
    assert 't2' not in meta['message_ids'] and 't2.b1' in meta['message_ids'] and 't3' in meta['message_ids']
    assert value['measure_avoidable_interventions']==1
    assert value['measure_unnecessary_interruptions']==1
    assert meta['measures']['unnecessary_interruptions'][0]['message']=='t2.b2'
    assert len(meta['required_approvals_events'])==1  # independent, never auto-approved
    if grade is None:
        assert math.isnan(value['measure_proactive_usefulness'])
    else:
        assert value['measure_proactive_usefulness']==grade/2
    # No requested follow-up reply was in the proactive observation.
    assert all(o['text']!='requested result' for o in meta['proactive_observation'].get('outputs',[]))


@pytest.mark.parametrize('failure', ['usage-limit', 'observation_error'])
def test_due_outage_or_limit_makes_no_judge_calls(tmp_path,failure):
    r=run()
    r['turns'].insert(1,{'kind':'wait_due','failure_class':failure if failure=='usage-limit' else None,
                         'due_observation':{'status':failure,'reason':'fixture failure','completed':False}})
    async def forbidden(*args): raise AssertionError('judge called')
    values,metadata=asyncio.run(s.score_run(scenario(),r,'fixture',JudgeBudget(tmp_path/'budget'),lambda x:x,forbidden,REAL_RUBRIC))
    assert metadata['judge_status']=='skipped' and metadata['measures_status']=='not_scored'
    if failure=='usage-limit': assert failure in metadata['failure_classes']
    else: assert metadata['failures'][0]['kind']=='infra'


def test_due_round_trip_applicable_denominators_labels_and_versions(rubric,tmp_path):
    from test_eval_come_due import Trace
    receipt=Trace(tmp_path/'db').wait()
    base=make_report(rubric)
    raw=copy.deepcopy(base['samples'])
    for row in raw:
        if row['epoch']==1:
            row['due_observations']=[receipt]
            row['checks']['due_completed']=True
    raw[1]['checks']['due_completed']=False
    raw[1]['due_observations']=[{**receipt,'completed':False,'status':'timed_out','reason':'not_delivered'}]
    fixture={**base['fixture_support'],'observation_version':c.OBSERVATION_VERSION,
             'due_scenario_ids': ['dev-case', 'held-case']}
    report=c.build_report(raw,base['live_evidence'],fixture)
    for split,block in report['live_evidence']['splits'].items():
        assert block['totals']['checks']['due_completed']['n'] == 3
        assert block['totals']['checks']['due_completed']['pass_rate'] == pytest.approx(1/3)
        assert block['totals']['due']['created']
        assert len(block['totals']['latency'])==3
    text=c.render_markdown(report)
    assert 'not_delivered' in text and 'wait duration' in text and 'message references' in text
    template=c.labels_template(report)
    assert template['references'] and template['references'][0]['due_observations']
    for suffix in ('a','b'):
        sample=SimpleNamespace(id='x',epoch=1,scores=None if suffix=='b' else {'secretary':SimpleNamespace(value={},metadata={})},
                               metadata={'run':{'turns':[{'kind':'wait_due','due_observation':receipt}], 'due_observations':[receipt]}})
        record=records_from_samples([sample])[0]
        assert record['turns'][0]['due_observation']==receipt and record['due_observations']==[receipt]
    jp,_=c.write_report(report,tmp_path/'reports')
    assert json.loads(jp.read_text())==report
    other=copy.deepcopy(report)
    other['observation_version']='continuity-observation-v1'
    with pytest.raises(ValueError,match='observation'): c.compare(report,other)


def test_due_shortfall_and_coverage_loss_cannot_win(rubric):
    a=make_report(rubric)
    b=make_report(rubric,ident='after',head='def',dev=(1,1,1),held=(1,1,1))
    assert c.compare(a,b)['verdict']=='better'
    a['samples'][0]['proactive_observation']={'observed':True}
    result=c.compare(a,b)
    assert result['verdict']=='not better' and result['partial']
    assert 'proactive_coverage_preserved' in result['failed_conditions']
    a['samples'][0]['proactive_observation']={'observed':False}
    b['samples'][0]['checks']['due_completed']=False
    assert c.compare(a,b)['verdict']=='not better'  # lost receipt still fails its applicable check
    for status in ('timed_out','unsupported'):
        b['samples'][0]['due_observations']=[{'status':status,'completed':False}]
        result=c.compare(a,b)
        assert result['verdict']=='not better' and result['partial']
        assert 'complete_due_observation' in result['failed_conditions']


def test_account_and_due_routes_deduplicate_same_preparation_work(tmp_path):
    from test_eval_come_due import Trace
    receipt=Trace(tmp_path).wait()
    r={'due_observations':[receipt], 'diff':{'preparations':[{'id':'p','last_work_id':'due',
        'line_account':True,'last_delivery':'sent','last_result':'API excerpt'}]}}
    observation=s.proactive_observation(r)
    assert len(observation['outputs'])==1
    assert observation['outputs'][0]['text']=='observed result'
    # Changes to a final row cannot erase the retained delivery receipt.
    r['diff']['preparations'][0].update(last_delivery='none',last_result='')
    assert s.proactive_observation(r)['observed']


def test_wait_asks_require_message_attribution_and_keep_separate_gate(rubric):
    r={'turns':[{'kind':'wait_due','before_answer':[{'text':'redundant ask?'},{'text':'ping'},{'text':'real gate?'}]}]}
    data={'avoidable_interventions':[{'turn':1,'kind':'asked_known_fact','reason':'t1.b1 asks a known fact'}],
          'required_approvals':[{'turn':1,'reason':'t1.b3 requires a gate'}],
          'unnecessary_interruptions':[{'message':'t1.b1','reason':'ask'},{'message':'t1.b2','reason':'ping'},{'message':'t1.b3','reason':'gate'}],
          'proactive_usefulness':{'score':None,'reason':'absent'}}
    parsed=s.parse_measures(json.dumps(data),rubric[0],r)
    assert len(parsed['avoidable_interventions'])==len(parsed['required_approvals'])==1
    assert [m['message'] for m in parsed['unnecessary_interruptions']]==['t1.b2']
    data['avoidable_interventions'][0]['reason']='unattributed ask'
    with pytest.raises(ValueError,match='actual tN.bJ'):s.parse_measures(json.dumps(data),rubric[0],r)


@pytest.mark.parametrize('error_kind', ['sample_error', 'harness'])
def test_error_epoch_stays_in_applicable_check_denominators(rubric, error_kind):
    old = scenarios.validate({'id': 'old-case', 'turns': ['owner']})
    due = scenarios.validate({'id': 'due-case', 'channel': 'telegram', 'turns': [
        'entrust', {'wait_due': {'after_turn': 1, 'until': 'settled', 'timeout_seconds': 30}}]})
    samples = []
    for case in (old, due):
        for epoch in (1, 2, 3):
            checks = {'delivered': True, 'not_failed': True}
            if case is due:
                checks['due_completed'] = True
            failures = []
            if epoch == 2:
                checks = {'harness': False}
                failures = [{'kind': 'infra', 'name': 'harness', 'reason': 'fixture outage'}]
            value, meta = s.combine(case, {'worker': 'codex', 'turns': []}, checks, failures)
            score = SimpleNamespace(value=value, metadata=meta)
            if epoch == 2 and error_kind == 'sample_error':
                score = None
            samples.append(SimpleNamespace(id=case['id'], epoch=epoch,
                           scores={'secretary': score} if score else None,
                           metadata={'scenario': case, 'worker': 'codex'}, error='fixture outage'))
    fixture = c.fixture_support([old, due], rubric[1])
    report = c.build_report(records_from_samples(samples),
                           {'id': 'errors', 'worker': 'codex', 'epochs': 3, 'seed': 'fresh'}, fixture)
    groups = report['live_evidence']['splits']['dev']
    for ident in ('old-case', 'due-case'):
        check = groups['scenarios'][ident]['checks']['delivered']
        assert check['n'] == 3 and check['pass_rate'] == pytest.approx(2/3)
    assert 'due_completed' not in groups['scenarios']['old-case']['checks']
    for group in (groups['scenarios']['due-case'], groups['totals']):
        assert group['checks']['due_completed']['n'] == 3
        assert group['checks']['due_completed']['pass_rate'] == pytest.approx(2/3)
    assert groups['totals']['checks']['delivered']['n'] == 6
    # Even an entirely unscored due scenario has an applicable, failed check.
    errors = [row for row in records_from_samples(samples) if row['epoch'] == 2]
    all_error = c.build_report(errors, {'id': 'errors-only', 'worker': 'codex', 'epochs': 3}, fixture)
    due_check = all_error['live_evidence']['splits']['dev']['scenarios']['due-case']['checks']['due_completed']
    assert due_check['n'] == 1 and due_check['pass_rate'] == 0


def test_cli_compare_accepts_filled_labels_template_with_references(rubric, tmp_path):
    a = make_report(rubric)
    b = make_report(rubric, ident='after', head='def', dev=(1,1,1), held=(1,1,1))
    paths = [c.write_report(report, tmp_path)[0] for report in (a,b)]
    label_paths = []
    for report, path in zip((a,b), paths):
        with redirect_stdout(io.StringIO()) as output:
            assert cli.main(['continuity', 'labels-template', str(path)]) == 0
        template = json.loads(output.getvalue())
        assert 'references' in template
        filled = {row['epoch']: row for row in labels_for(report)['labels'] if row['scenario'] == 'held-case'}
        for row in template['labels']:
            row['measures'] = filled[row['epoch']]['measures']
        label_path = tmp_path / (report['live_evidence']['id'] + '-labels.json')
        label_path.write_text(json.dumps(template))
        label_paths.append(label_path)
    with redirect_stdout(io.StringIO()):
        assert cli.main(['continuity','compare',str(paths[0]),str(paths[1]),
                         '--labels',str(label_paths[0]),'--labels',str(label_paths[1])]) == 0
    result = json.loads((tmp_path/'after.c2.compare.json').read_text())
    assert result['verdict_basis'] == 'human labels supplied'


@pytest.mark.parametrize('grade', [0, 1, 2])
def test_reminder_delivered_body_can_be_graded_without_admission(tmp_path, grade):
    from test_eval_come_due import ReminderTrace, SOURCE, SPEC
    receipt = ReminderTrace(tmp_path).wait()
    run_record = {'turns':[
        {**SOURCE,'say':'entrust','answer':'accepted','status':'succeeded'},
        {'kind':'wait_due','due_observation':receipt,'before_answer':receipt['messages']},
        {'task_id':'follow','say':'follow up','answer':'requested revision','status':'succeeded'}],
        'due_observations':[receipt]}
    rubric = c.load_rubric(c.DEFAULT_RUBRIC)
    parsed = s.parse_measures(json.dumps(reply(proactive=grade)),rubric,run_record)
    assert parsed['proactive_usefulness']['score'] == grade
    assert parsed['proactive_observation']['outputs'][0]['text'] == 'the observed reminder body'
    assert parsed['proactive_observation']['outputs'][0]['kind'] == 'scheduled_reminder'
    assert 'requested revision' not in str(parsed['proactive_observation'])
    values = s.measure_values(parsed,scenario(),run_record)
    assert values['measure_proactive_usefulness'] == grade/2
    # Losing typed origin cannot be repaired by the result excerpt or by a label.
    receipt.pop('reminder_origin')
    assert s.parse_measures(json.dumps(reply(proactive=2)),rubric,run_record)['proactive_usefulness']['score'] is None


def test_reminder_proof_uses_new_observation_identity_and_survives_reports(rubric,tmp_path):
    from test_eval_come_due import ReminderTrace
    receipt = ReminderTrace(tmp_path/'store').wait()
    baseline = make_report(rubric)
    records = copy.deepcopy(baseline['samples'])
    for record in records:
        record['due_observations'] = [receipt]
        record['observation_version'] = c.OBSERVATION_VERSION
        record['checks']['due_completed'] = True
    fixture = {**baseline['fixture_support'],'observation_version':c.OBSERVATION_VERSION,
               'due_scenario_ids':['dev-case','held-case']}
    report = c.build_report(records,baseline['live_evidence'],fixture)
    assert report['observation_version'] == 'continuity-observation-v3'
    assert report['samples'][0]['due_observations'][0]['due_slot'] == receipt['start']['facts']['slot']
    assert c.labels_template(report)['references'][0]['due_observations'][0]['reminder_origin']
    assert not any('builtin reminder provenance remain unsupported' in text
                   for text in report['unsupported_paths']['limitations'])
    old = copy.deepcopy(report)
    old['observation_version'] = 'continuity-observation-v2'
    old['fixture_support']['observation_version'] = 'continuity-observation-v2'
    with pytest.raises(ValueError,match='observation'):
        c.compare(old,report)
