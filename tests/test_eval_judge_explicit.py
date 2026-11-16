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
from agentos_eval.budget import JudgeBudget, judge_totals, scorer_judge_resources
from agentos_eval.redaction import Redactor
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
    assert ('Eval judge totals: openai/explicit-model; calls: 1; input tokens: 20 '
            '(uncached 20, cache write 0, cache read 0); output tokens: 10;') in output
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
    assert (f'input tokens: {20 * len(calls)} '
            f'(uncached unavailable, cache write unavailable, cache read unavailable); '
            f'output tokens: {10 * len(calls)};') in output
    assert str(judge_totals(calls)['usd']) in output


@pytest.mark.parametrize('argument', ['', 'none'])
def test_task_judge_default_without_inspect_has_no_roles_keys_or_calls(argument, monkeypatch, capsys):
    keychain = Mock(return_value='would-be-key')
    ensure_key = cli.ensure_judge_key
    ensure = Mock(side_effect=lambda judge: ensure_key(judge, keychain=keychain))
    monkeypatch.setattr(cli, 'ensure_judge_key', ensure)
    judge, scorer_judge, roles, announce = cli.task_judge_settings(argument, True)
    assert judge == scorer_judge == 'none' and roles is None and announce is True
    if announce:
        cli.judge_notice(judge)
    generate = Mock(side_effect=AssertionError('no judge call'))
    _, metadata = asyncio.run(scoring.score_run(scenario(), run(), scorer_judge, None, None, generate))
    assert metadata['judge_status'] == 'skipped'
    ensure.assert_not_called()
    keychain.assert_not_called()
    generate.assert_not_called()
    assert 'Checks only;' in capsys.readouterr().out


@pytest.mark.parametrize('selection', ['argument', 'environment'])
def test_task_named_judge_without_inspect_uses_grader_role(selection, monkeypatch):
    argument = 'openai/named-model' if selection == 'argument' else ''
    if selection == 'environment':
        monkeypatch.setenv('AGENTOS_EVAL_JUDGE_MODEL', 'openai/named-model')
    ensure = Mock(side_effect=AssertionError('selection must not load a key'))
    monkeypatch.setattr(cli, 'ensure_judge_key', ensure)
    assert cli.task_judge_settings(argument, True) == (
        'openai/named-model', 'grader', {'grader': 'openai/named-model'}, True)
    ensure.assert_not_called()


@pytest.mark.parametrize('flag,announce', [(False, False), ('false', False), ('0', False), (True, True)])
def test_task_notice_flag_and_explicit_off_override_without_inspect(flag, announce, monkeypatch):
    monkeypatch.setenv('AGENTOS_EVAL_JUDGE_MODEL', 'openai/named-model')
    assert cli.task_judge_settings('none', flag) == ('none', 'none', None, announce)


@pytest.mark.parametrize('judge', ['', None, 'none'])
def test_checks_only_scorer_loads_no_budget_or_redactor_without_inspect(judge, monkeypatch):
    budget = Mock(side_effect=AssertionError('checks-only must not load a budget'))
    redactor = Mock(side_effect=AssertionError('checks-only must not read stored secrets'))
    monkeypatch.setattr(JudgeBudget, 'from_environ', budget)
    monkeypatch.setattr(Redactor, 'for_seed', redactor)
    assert scorer_judge_resources(judge) == (None, None)
    budget.assert_not_called()
    redactor.assert_not_called()


def test_named_scorer_resources_reuse_existing_factories_without_inspect(tmp_path, monkeypatch):
    budget, redact = object(), object()
    budget_factory, redact_factory = Mock(return_value=budget), Mock(return_value=redact)
    monkeypatch.setattr(JudgeBudget, 'from_environ', budget_factory)
    monkeypatch.setattr(Redactor, 'for_seed', redact_factory)
    path, seed = tmp_path / 'budget.json', tmp_path / 'seed'
    assert scorer_judge_resources('grader', path, seed) == (budget, redact)
    budget_factory.assert_called_once_with(path)
    redact_factory.assert_called_once_with(seed)


@pytest.mark.parametrize('selection', ['off', 'argument', 'environment'])
def test_world_sweep_notice_never_loads_judge_credentials(selection, tmp_path, monkeypatch, capsys):
    extra = ['--world', 'fake']
    if selection == 'argument':
        extra += ['--judge', 'openai/named-model']
    elif selection == 'environment':
        monkeypatch.setenv('AGENTOS_EVAL_JUDGE_MODEL', 'openai/named-model')
    args = sweep_args(extra)
    ensure = Mock(side_effect=AssertionError('world sweeps never load judge keys'))
    keychain = Mock(side_effect=AssertionError('world sweeps never read Keychain'))
    monkeypatch.setattr(cli, 'ensure_judge_key', ensure)
    monkeypatch.setattr(cli, 'keychain_secret', keychain)
    monkeypatch.setattr(cli.scenario_module, 'load', lambda **kw: [
        {'id': 'world-fixture', 'world': {'expect_world_pass': True}}])
    fake_run = lambda *a, **kw: {'worker': 'codex', 'turns': [], 'still': {'check': True},
                               'world': {'name': 'fake', 'result': {'passed': True, 'failures': []}}}
    assert cli.run_world_sweep(args, run_scenario=fake_run, pool_factory=lambda **kw: None,
                               folder=tmp_path / 'reports') == 0
    notice, = [line for line in capsys.readouterr().out.splitlines() if line.startswith('World sweep')]
    assert 'checks only' in notice and 'eval judge is not used' in notice
    assert ('ignored for world sweeps' in notice) == (selection != 'off')
    assert '--judge' not in notice and 'Enable' not in notice
    ensure.assert_not_called()
    keychain.assert_not_called()


def test_direct_task_default_has_no_roles_or_judge_calls(tmp_path, monkeypatch, capsys):
    pytest.importorskip('inspect_ai')
    from agentos_eval import task
    keychain = Mock(return_value='would-be-key')
    ensure = Mock(side_effect=lambda judge: cli.ensure_judge_key(judge, keychain=keychain))
    monkeypatch.setattr(cli, 'ensure_judge_key', ensure)
    transport = Mock(side_effect=AssertionError('no model should be loaded'))
    monkeypatch.setattr(task, 'get_model', transport)
    redactor = Mock(side_effect=AssertionError('checks-only must not read stored provider secrets'))
    monkeypatch.setattr(Redactor, 'for_seed', redactor)
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
    monkeypatch.setattr(Redactor, 'for_seed', lambda seed: lambda text: text)
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


@pytest.mark.parametrize('models', [(None, 'model-A', 'model-B'), ('model-B', None, 'model-A')])
def test_p1_combining_checks_all_prior_resolved_judges(models):
    logs = [_one_epoch_log('arm', i) for i in range(1, 4)]
    for log, model in zip(logs, models):
        log.eval.metadata['judge'] = 'openai/alias'
        if model:
            log.eval.metadata['judge_model_resolved'] = [model]
    with pytest.raises(ValueError, match='different resolved judge models'):
        p1_report.combine_arm_logs(logs, 'arm')


def test_p1_combining_accepts_unknown_then_matching_resolved_judges():
    logs = [_one_epoch_log('arm', i) for i in range(1, 4)]
    for log in logs:
        log.eval.metadata['judge'] = 'openai/alias'
    for log in logs[1:]:
        log.eval.metadata['judge_model_resolved'] = ['model-A']
    assert p1_report.combine_arm_logs(logs, 'arm')['run']['epochs'] == 3


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
    assert all(totals[key] == 'unavailable' for key in
               ('input_tokens_uncached', 'input_tokens_cache_write', 'input_tokens_cache_read'))
    assert totals['usd'] == budget.status()['usd']
    assert 'judge_model_resolved' not in metadata


async def judge_with_usage(budget, tokens):
    async def generate(*args):
        return NS(completion='{}', usage=tokens)
    return await scoring.judge_call('x', json.loads, generate, budget, lambda text: text, system='')


@pytest.mark.parametrize('cache_write,cache_read,full', [
    (1543, None, 1546), (None, 1543, 1546), (1000, 543, 1546), (None, None, 3)])
def test_judge_input_includes_all_inspect_cache_parts(tmp_path, cache_write, cache_read, full):
    budget = JudgeBudget(tmp_path / 'budget.json', price_in=3, price_out=9)
    tokens = NS(input_tokens=3, input_tokens_cache_write=cache_write,
                input_tokens_cache_read=cache_read, output_tokens=577, reasoning_tokens=398)
    result, status, usage, error = asyncio.run(judge_with_usage(budget, tokens))
    assert result == {} and status == 'judged' and error is None
    assert usage == {'input_tokens': full, 'input_tokens_uncached': 3,
                     'input_tokens_cache_write': cache_write or 0,
                     'input_tokens_cache_read': cache_read or 0,
                     'output_tokens': 577, 'usd': (full * 3 + 577 * 9) / 1_000_000,
                     'basis': 'observed'}
    totals = judge_totals([{'judge_usage': {'rubric': usage, 'measures': usage}}])
    assert totals == {'calls': 2, **{k: v * 2 for k, v in usage.items() if k != 'basis'}}
    ledger = budget.status()
    assert ledger['input_tokens'] == full and ledger['output_tokens'] == 577
    assert ledger['usd'] == pytest.approx(usage['usd'])


def test_judge_full_input_settlement_blocks_next_call_at_cap(tmp_path):
    budget = JudgeBudget(tmp_path / 'budget.json', cap_usd=.001, price_in=1, price_out=0)
    assert budget.cost(3, 577) < budget.cap < budget.cost(1546, 577)
    tokens = NS(input_tokens=3, input_tokens_cache_write=1000, input_tokens_cache_read=543,
                output_tokens=577)
    _, status, usage, _ = asyncio.run(judge_with_usage(budget, tokens))
    assert status == 'judged' and usage['usd'] == .001546
    # The reservation fits, but observed full input crosses the cap and is not clipped.
    assert budget.status()['input_tokens'] == 1546
    assert budget.status()['usd'] == .001546
    result, status, usage, error = asyncio.run(judge_with_usage(budget, tokens))
    assert (result, status, usage, error) == (None, 'budget_refused', {}, None)
    assert budget.status()['calls'] == budget.status()['refused'] == 1


def test_judge_totals_line_shows_full_input_and_each_part(tmp_path, capsys):
    budget = JudgeBudget(tmp_path / 'budget.json')
    tokens = NS(input_tokens=3, input_tokens_cache_write=1000, input_tokens_cache_read=543,
                output_tokens=577, reasoning_tokens=398)
    _, _, usage, _ = asyncio.run(judge_with_usage(budget, tokens))
    cli.judge_totals_line('fixture-judge', [{'judge_usage': {'rubric': usage}}])
    assert capsys.readouterr().out == (
        'Eval judge totals: fixture-judge; calls: 1; input tokens: 1546 '
        '(uncached 3, cache write 1000, cache read 543); output tokens: 577; '
        f'settled USD: {usage["usd"]}.\n')


def test_judge_totals_old_shape_keeps_total_and_only_missing_parts_unavailable(tmp_path, capsys):
    old = {'input_tokens': 20, 'output_tokens': 10, 'usd': .00012}
    cli.judge_totals_line('fixture-judge', [{'judge_usage': {'rubric': old}}])
    assert capsys.readouterr().out == (
        'Eval judge totals: fixture-judge; calls: 1; input tokens: 20 '
        '(uncached unavailable, cache write unavailable, cache read unavailable); '
        'output tokens: 10; settled USD: 0.00012.\n')
    # One missing historical part must not poison other recorded metrics, including
    # a part independently present in a partially upgraded historical record.
    old['input_tokens_cache_read'] = 4
    tokens = NS(input_tokens=3, input_tokens_cache_write=1000, input_tokens_cache_read=543,
                output_tokens=577)
    _, _, usage, _ = asyncio.run(judge_with_usage(JudgeBudget(tmp_path / 'budget.json'), tokens))
    totals = judge_totals([{'judge_usage': {'rubric': old}}, {'judge_usage': {'rubric': usage}}])
    assert totals['calls'] == 2 and totals['input_tokens'] == 1566
    assert totals['output_tokens'] == 587 and totals['usd'] == pytest.approx(.00012 + usage['usd'])
    assert totals['input_tokens_cache_read'] == 547
    assert totals['input_tokens_uncached'] == totals['input_tokens_cache_write'] == 'unavailable'


def test_judge_missing_usage_keeps_parts_unavailable_and_reservation(tmp_path):
    budget = JudgeBudget(tmp_path / 'budget.json')
    _, status, usage, _ = asyncio.run(judge_with_usage(budget, None))
    assert status == 'judged' and usage['basis'] == 'reservation'
    assert usage['usd'] == budget.status()['usd'] > 0
    assert all(value == 'unavailable' for key, value in usage.items() if key not in ('usd', 'basis'))
