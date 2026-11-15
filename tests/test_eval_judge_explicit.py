"""#885 explicit eval judge selection; fake transports/credentials, no live calls."""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
from agentos_eval import cli, report, scoring, continuity, paired, promotion, p1_report
from agentos_eval.budget import JudgeBudget, judge_totals
from test_continuity_report import rubric, make_report, scenario, run, args as continuity_args
from test_eval_paired import report as judgment_report, SPLITS
from test_eval_promotion import inputs as promotion_inputs, fixture_commit_range
from test_p1_report import _one_epoch_log


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith('AGENTOS_EVAL_') or name in cli.KEY_ENV.values():
            monkeypatch.delenv(name)
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path))
    monkeypatch.setattr(cli, 'git_head', lambda: 'fixture')


def sweep_args(extra=()):
    # Capture argparse's real defaults without importing Inspect.
    with pytest.MonkeyPatch.context() as patch:
        captured = []
        patch.setattr(cli, 'cmd_sweep', lambda args: captured.append(args) or 0)
        cli.main(['sweep', '--no-local', '--epochs', '1', *extra])
    return captured[0]


def scored_log(judge, tmp_path, calls):
    async def generate(*args):
        calls.append(args)
        return NS(model='provider-snapshot', usage=NS(input_tokens=20, output_tokens=10),
                  completion=json.dumps({'dimensions': {dim: {'score': 2, 'reason': 'ok'}
                                                        for dim in scenario()['rubric']}, 'summary': 'ok'}))
    value, metadata = asyncio.run(scoring.score_run(scenario(), run(), judge,
                                JudgeBudget.from_environ(tmp_path / 'budget.json'), lambda text: text, generate))
    return NS(samples=[NS(id='dev-case@codex', epoch=1, metadata={},
                          scores={'secretary': NS(value=value, metadata=metadata)})],
              results=None, location='fixture.eval')


def test_sweep_default_never_loads_key_or_calls_judge(tmp_path, monkeypatch, capsys):
    keychain = Mock(return_value='would-be-key')
    loader = Mock(side_effect=lambda judge: cli.ensure_judge_key(judge, keychain=keychain))
    monkeypatch.setattr(cli, 'ensure_judge_key', loader)
    calls, options = [], []
    def evaluate(task, **kwargs):
        assert kwargs['metadata']['judge'] == 'none'
        return [scored_log(task['judge'], tmp_path, calls)]
    assert cli.cmd_sweep(sweep_args(), task_factory=lambda **kw: options.append(kw) or kw,
                         inspect_eval=evaluate, folder=tmp_path / 'reports') == 0
    assert options[0]['judge'] == 'none'
    assert not calls
    loader.assert_not_called()
    keychain.assert_not_called()
    assert 'OPENAI_API_KEY' not in os.environ
    output = capsys.readouterr().out
    notices = [line for line in output.splitlines() if line.startswith('Checks only;')]
    assert len(notices) == 1
    assert '--judge <model>' in notices[0] and 'AGENTOS_EVAL_JUDGE_MODEL=<model>' in notices[0]
    assert 'Eval judge totals: off' in output


@pytest.mark.parametrize('selection,source,explicit_prices', [
    ('flag', 'environment', False), ('env', 'keychain', False), ('flag', 'environment', True),
    ('env', 'environment', True)])
def test_selected_judge_notice_precedes_inspect_and_totals(tmp_path, monkeypatch, capsys,
                                                         selection, source, explicit_prices):
    judge = 'openai/explicit-model'
    extra = ['--judge', judge, '--cap-usd', '7'] if selection == 'flag' else []
    if selection == 'env':
        monkeypatch.setenv('AGENTOS_EVAL_JUDGE_MODEL', judge)
        monkeypatch.setenv('AGENTOS_EVAL_JUDGE_CAP_USD', '7')
    if source == 'environment':
        monkeypatch.setenv('OPENAI_API_KEY', 'fake-env-key')
    if explicit_prices:
        monkeypatch.setenv('AGENTOS_EVAL_JUDGE_PRICE_IN', '3')
        monkeypatch.setenv('AGENTOS_EVAL_JUDGE_PRICE_OUT', '9')
    keychain = Mock(return_value='fake-keychain-key')
    loader = lambda value: cli.ensure_judge_key(value, keychain=keychain)
    calls, captured = [], []
    def evaluate(task, **kwargs):
        assert kwargs['metadata']['judge_key'] == source
        before = capsys.readouterr().out
        captured.append(before)
        notice, = [line for line in before.splitlines() if line.startswith('Eval judge:')]
        assert judge in notice and ('Keychain' if source == 'keychain' else source) in notice
        assert 'daily cap: USD 7' in notice
        assert ('placeholder' in notice) != explicit_prices
        assert f'input USD {3 if explicit_prices else 2}' in notice
        assert f'output USD {9 if explicit_prices else 8}' in notice
        return [scored_log(judge, tmp_path, calls)]
    assert cli.cmd_sweep(sweep_args(extra), task_factory=lambda **kw: kw, inspect_eval=evaluate,
                         key_loader=loader, folder=tmp_path / 'reports') == 0
    assert len(calls) == 1 and len(captured) == 1
    assert keychain.call_count == (source == 'keychain')
    output = capsys.readouterr().out
    assert 'Eval judge totals: openai/explicit-model; calls: 1; input tokens: 20; output tokens: 10;' in output
    saved = json.loads(next((tmp_path / 'reports').glob('*.json')).read_text())
    assert saved['run']['judge_model_resolved'] == ['provider-snapshot']
    assert saved['judge_usage']['usd'] == pytest.approx((20 * (3 if explicit_prices else 2)
                                                      + 10 * (9 if explicit_prices else 8)) / 1_000_000)
    assert str(saved['judge_usage']['usd']) in output


def test_explicit_none_overrides_environment(monkeypatch, capsys):
    monkeypatch.setenv('AGENTOS_EVAL_JUDGE_MODEL', 'openai/selected')
    loader = Mock()
    judge = cli.selected_judge('none')
    assert judge == 'none'
    cli.judge_notice(judge, loader)
    loader.assert_not_called()


@pytest.mark.parametrize('prices,missing', [({'IN': '3'}, 'OUT'), ({'OUT': '9'}, 'IN')])
def test_each_missing_price_is_identified(monkeypatch, capsys, prices, missing):
    for key, value in prices.items():
        monkeypatch.setenv('AGENTOS_EVAL_JUDGE_PRICE_' + key, value)
    cli.judge_notice('openai/selected', lambda _: 'environment')
    assert f'placeholder default prices: {missing}' in capsys.readouterr().out


@pytest.mark.parametrize('judge', ['', 'none'])
def test_continuity_requires_explicit_judge(judge):
    with pytest.raises(SystemExit) as exc:
        cli.run_continuity(NS(judge=judge, seed=None))
    assert '--judge <model>' in str(exc.value)
    assert 'AGENTOS_EVAL_JUDGE_MODEL=<model>' in str(exc.value)


def test_continuity_announces_once_and_prints_settlements(rubric, tmp_path, monkeypatch, capsys):
    calls = []
    def evaluate(task, **kwargs):
        notice = capsys.readouterr().out
        assert len([line for line in notice.splitlines() if line.startswith('Eval judge:')]) == 1
        # Use a frozen, valid C2 sample envelope, already judged by a fake transport.
        result = make_report(rubric)
        for row in result['samples']:
            row['judge_usage'] = {'rubric': {'input_tokens': 20, 'output_tokens': 10, 'usd': .00012}}
        calls.extend(result['samples'])
        samples = [NS(id=row['scenario'] + '@codex', epoch=row['epoch'], metadata={},
                      scores={'secretary': NS(metadata=row, value=row['value'])}) for row in calls]
        return [NS(samples=samples, results=None, location='fixture.eval')]
    # The sweep's report writer is unrelated to selection/Inspect/scorer accounting.
    monkeypatch.setattr(cli, '_write', lambda *args: {'trend': {'regressions': []}})
    def sweep(args):
        return cli.cmd_sweep(args, inspect_eval=evaluate, task_factory=lambda **kw: NS(metadata={}),
                             key_loader=lambda _: 'environment')
    assert cli.run_continuity(continuity_args(rubric), sweep=sweep) == 0
    output = capsys.readouterr().out
    assert f'Eval judge totals: fixture-judge; calls: {len(calls)};' in output
    assert f'input tokens: {20 * len(calls)}; output tokens: {10 * len(calls)};' in output
    assert str(judge_totals(calls)['usd']) in output


def test_direct_task_default_has_no_roles_or_judge_calls(tmp_path, monkeypatch, capsys):
    pytest.importorskip('inspect_ai')
    from agentos_eval import task
    keychain = Mock(return_value='would-be-key')
    ensure = Mock(side_effect=lambda judge: cli.ensure_judge_key(judge, keychain=keychain))
    monkeypatch.setattr(cli, 'ensure_judge_key', ensure)
    transport = Mock(side_effect=AssertionError('no model should be loaded'))
    monkeypatch.setattr(task, 'get_model', transport)
    redactor = Mock(side_effect=AssertionError('checks-only must not read stored provider secrets'))
    monkeypatch.setattr(task.Redactor, 'for_seed', redactor)
    original = task.secretary
    selected = []
    def scorer(judge, **kwargs):
        selected.append((judge, original(judge, **kwargs)))
        return selected[-1][1]
    monkeypatch.setattr(task, 'secretary', scorer)
    built = task.agentos_secretary(include_local=False, sample_limit=1)
    assert not built.model_roles and built.metadata['judge'] == 'none'
    assert selected[0][0] == 'none'
    state = NS(metadata={'scenario': scenario(), 'run': run()}, output=NS(completion='ok'))
    score = asyncio.run(selected[0][1](state, None))
    assert score.metadata['judge_status'] == 'skipped'
    transport.assert_not_called()
    redactor.assert_not_called()
    ensure.assert_not_called()
    keychain.assert_not_called()
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert '--judge <model>' in output and 'AGENTOS_EVAL_JUDGE_MODEL=<model>' in output


def test_task_uses_inspect_grader_role_and_records_resolved_id(tmp_path, monkeypatch, capsys):
    pytest.importorskip('inspect_ai')
    from agentos_eval import task
    monkeypatch.setenv('AGENTOS_EVAL_JUDGE_MODEL', 'openai/explicit-model')
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key')
    monkeypatch.setattr(task.Redactor, 'for_seed', lambda seed: lambda text: text)
    async def generate(*args, **kwargs):
        return NS(model='answered-model-id', usage=NS(input_tokens=12, output_tokens=4),
                  completion=json.dumps({'dimensions': {dim: {'score': 2, 'reason': 'ok'}
                                                        for dim in scenario()['rubric']}}))
    get_model = Mock(return_value=NS(generate=generate))
    monkeypatch.setattr(task, 'get_model', get_model)
    selected = []
    original = task.secretary
    def scorer(judge, **kwargs):
        selected.append(original(judge, **kwargs))
        return selected[-1]
    monkeypatch.setattr(task, 'secretary', scorer)
    built = task.agentos_secretary(include_local=False, sample_limit=1)
    assert set(built.model_roles) == {'grader'}
    assert built.model_roles['grader'].name == 'explicit-model'
    state = NS(metadata={'scenario': scenario(), 'run': run()}, output=NS(completion='ok'))
    score = asyncio.run(selected[0](state, None))
    get_model.assert_called_once_with(role='grader')
    assert score.metadata['judge_model_resolved'] == ['answered-model-id']
    assert score.metadata['judge_usage']['rubric']['judge_model_resolved'] == 'answered-model-id'
    assert 'Eval judge: openai/explicit-model' in capsys.readouterr().out


# Exercise each public comparison boundary with valid matching fixtures first.
@pytest.mark.parametrize('candidate', ['other/model', 'none', None])
@pytest.mark.parametrize('boundary', ['report', 'continuity', 'paired', 'p1', 'p1_combine', 'promotion'])
def test_comparisons_reject_different_off_and_unknown_judges(candidate, boundary, rubric,
                                                            promotion_inputs, monkeypatch, tmp_path):
    known = 'provider/judge'
    if boundary == 'report':
        a = report.aggregate([], {'judge': known})
        b = report.aggregate([], {'judge': known})
        compare = lambda: report.compare(b, a)
        target = b['run']
    elif boundary == 'continuity':
        a, b = make_report(rubric), make_report(rubric, ident='after', head='def')
        a['live_evidence']['judge'] = b['live_evidence']['judge'] = known
        target = b['live_evidence']
        compare = lambda: continuity.compare(a, b, allow_partial=True)
    elif boundary == 'paired':
        a, b = judgment_report(), judgment_report()
        a['judge'] = b['judge'] = known
        target = b
        compare = lambda: paired.compare_paired(a, b, splits=SPLITS)
    elif boundary == 'p1_combine':
        a, b = _one_epoch_log('a', 1), _one_epoch_log('a', 2)
        a.eval.metadata['judge'] = b.eval.metadata['judge'] = known
        target = b.eval.metadata
        compare = lambda: p1_report.combine_arm_logs([a, b], 'arm')
    elif boundary == 'p1':
        a, b = _one_epoch_log('a', 1), _one_epoch_log('b', 1)
        a.eval.metadata['judge'] = b.eval.metadata['judge'] = known
        arms = {label: p1_report.combine_arm_logs([log], label) for label, log in [('a', a), ('b', b)]}
        target = arms['b']['run']
        compare = lambda: p1_report.compare_arms(arms, names=frozenset())
    else:
        monkeypatch.setattr(promotion, '_git_commits', lambda base, head, **kw: [head])
        selection, pair, identity, options = promotion_inputs
        pair['conditions']['baseline']['judge'] = pair['conditions']['candidate']['judge'] = known
        target = pair['conditions']['candidate']
        compare = lambda: promotion.build_record(selection, pair, identity, **options)
    compare()  # Matching models still work.
    if candidate is None:
        target.pop('judge')
    else:
        target['judge'] = candidate
    with pytest.raises(ValueError, match='judge') as exc:
        compare()
    if candidate is None:
        assert 'unknown' in str(exc.value) and 'missing' in str(exc.value)


@pytest.mark.parametrize('baseline,current', [
    ({'judge': 'provider/judge'}, {'judge': 'none'}),
    ({'judge': 'provider/judge'}, {'judge': 'other/judge'}),
    ({'judge': 'provider/judge'}, {}),
    ({}, {'judge': 'provider/judge'}),
    ({'judge': 'alias', 'judge_model_resolved': ['old']},
     {'judge': 'alias', 'judge_model_resolved': ['new']}),
])
def test_trend_writes_report_without_a_matching_judge_baseline(tmp_path, baseline, current):
    records = [{'scenario': 'fixture', 'worker': 'codex', 'value': {'passed': 1.0}}]
    report.write_report(records, tmp_path, {'id': 'a', **baseline})
    json_path, md_path, result = report.write_report(records, tmp_path, {'id': 'b', **current})
    assert json_path.is_file() and md_path.is_file()
    assert result['trend']['previous_run'] is None
    assert json.loads(json_path.read_text())['trend']['previous_run'] is None


@pytest.mark.parametrize('newer', [
    {'judge': 'other/judge'},
    {'judge': 'provider/judge', 'judge_model_resolved': ['new']},
])
def test_trend_picks_older_matching_judge_over_newer_incompatible_report(tmp_path, newer):
    records = [{'scenario': 'fixture', 'worker': 'codex', 'value': {'passed': 1.0}}]
    matching = {'judge': 'provider/judge', 'judge_model_resolved': ['old']}
    report.write_report(records, tmp_path, {'id': 'a', **matching})
    report.write_report(records, tmp_path, {'id': 'b', **newer})
    _, _, result = report.write_report(records, tmp_path, {'id': 'c', **matching})
    assert result['trend']['previous_run'] == 'a'


@pytest.mark.parametrize('baseline,current', [
    ({}, {}),
    ({'judge': 'provider/judge'}, {'judge': 'provider/judge', 'judge_model_resolved': ['snapshot']}),
    ({'judge': 'provider/judge', 'judge_model_resolved': ['snapshot']}, {'judge': 'provider/judge'}),
])
def test_trend_uses_same_judge_matching_rule_as_compare(tmp_path, baseline, current):
    report.write_report([], tmp_path, {'id': 'a', **baseline})
    _, _, result = report.write_report([], tmp_path, {'id': 'b', **current})
    assert result['trend']['previous_run'] == 'a'


def test_resolved_model_mismatch_and_historical_unknowns():
    report.require_same_judge({}, {})
    report.require_same_judge({'judge': 'none'}, {'judge': 'none'})
    with pytest.raises(ValueError, match='resolved judge'):
        report.require_same_judge({'judge': 'alias', 'judge_model_resolved': ['v1']},
                                  {'judge': 'alias', 'judge_model_resolved': ['v2']})


def test_totals_include_errors_and_unknown_usage_without_daily_ledger(tmp_path):
    calls = []
    async def fail(*args):
        calls.append(args)
        raise RuntimeError('fake outage')
    budget = JudgeBudget(tmp_path / 'budget.json')
    _, metadata = asyncio.run(scoring.score_run(scenario(), run(), 'selected', budget, lambda x: x, fail))
    totals = judge_totals([metadata])
    assert totals['calls'] == len(calls) == 1
    assert totals['input_tokens'] == totals['output_tokens'] == 'unavailable'
    assert totals['usd'] == budget.status()['usd']
    assert 'judge_model_resolved' not in metadata
