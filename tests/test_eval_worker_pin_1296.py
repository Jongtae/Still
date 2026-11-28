"""#1296: a sweep pins the sandbox worker's model; recorded samples and a real store, no model calls."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
sys.path.insert(0, str(ROOT / 'evals' / 'agentos_eval' / 'sandbox_site'))
import eval_worker_pin  # noqa: E402
from agentos_eval import arm_compare as ac, cli, report, runner, sandbox  # noqa: E402
from personal_agent import decision_adapters, orchestrator  # noqa: E402
from personal_agent.main_ai import MAIN_AI_CHOICES, SUBSCRIPTION_MODELS, MainAiRoutes  # noqa: E402
from personal_agent.quickstart_store import QuickStore  # noqa: E402
from test_orchestrator import BUNDLED, Harness, plan  # noqa: E402

PIN = {'worker': 'codex', 'model': 'm-pin', 'effort': 'high'}


def test_the_pin_accepts_only_what_still_would_keep():
    assert sandbox.PIN_MODEL_ID.pattern == decision_adapters.MODEL_ID.pattern
    assert sandbox.PIN_EFFORTS == decision_adapters.EFFORT_LEVELS
    assert sandbox.worker_pin('codex', 'm-pin', 'high') == PIN
    assert sandbox.worker_pin('claude-code', 'm-pin') == {'worker': 'claude-code', 'model': 'm-pin', 'effort': ''}
    assert sandbox.worker_pin('both') is None
    for args in (('both', 'm-pin'), ('codex', 'bad model'), ('codex', 'm-pin', 'huge'), ('codex', '', 'high')):
        with pytest.raises(ValueError):
            sandbox.worker_pin(*args)


@pytest.mark.parametrize('extra', [['--worker', 'both', '--worker-model', 'm-pin'],
                                   ['--worker-effort', 'high'],
                                   ['--world', 'shop', '--worker-model', 'm-pin']])
def test_sweep_refuses_a_pin_it_cannot_hold_before_running_anything(extra, monkeypatch):
    for name in ('judge_notice', 'run_world_sweep', 'resolve_seed'):
        monkeypatch.setattr(cli, name, lambda *a, **k: pytest.fail('the sweep started'))
    with pytest.raises(SystemExit) as stopped:
        cli.main(['sweep', *extra])
    assert '--worker' in str(stopped.value)


def test_sweep_passes_the_pin_to_the_task_and_the_run_settings(tmp_path):
    seen = {}

    class Stop(Exception):
        pass

    def task_factory(**kwargs):
        seen['task'] = kwargs
        return SimpleNamespace(metadata={})

    def inspect_eval(task, **kwargs):
        seen['run'] = kwargs['metadata']
        raise Stop

    args = argparse.Namespace(worker='codex', worker_model='m-pin', worker_effort='high', cap_usd=None, world='',
                              judge='none', seed='', scenarios='', split=None, instances=1, epochs=1, turn_timeout=1,
                              no_local=True, keep_sandboxes=False, limit=None, display='plain')
    with pytest.raises(Stop):
        cli.cmd_sweep(args, folder=tmp_path, inspect_eval=inspect_eval, task_factory=task_factory)
    for row in (seen['task'], seen['run']):
        assert (row['worker_model'], row['worker_effort']) == ('m-pin', 'high')


def test_the_sandbox_setting_is_the_one_stills_main_ai_reads(tmp_path):
    store = QuickStore(tmp_path)
    store.put(SUBSCRIPTION_MODELS, {'codex': 'm-seeded', 'claude-code': 'm-other'})
    store.put(MAIN_AI_CHOICES, {'codex': {'mode': 'auto', 'effort': 'low'}})
    main = MainAiRoutes.__new__(MainAiRoutes)
    main.store = store

    sandbox.pin_main_ai(tmp_path, PIN)
    assert (main.subscription_model('codex'), main.subscription_effort('codex')) == ('m-pin', 'high')
    assert main.subscription_model('claude-code') == 'm-other'

    sandbox.pin_main_ai(tmp_path, {**PIN, 'effort': ''})
    assert main.subscription_effort('codex') == '', 'a seeded effort never survives an effort-less pin'


def catalogue(reason=''):
    codex = {'id': 'codex', 'available': not reason, 'reason': reason, 'default': True,
             'default_model': 'm-auto', 'default_refused': 'm-pin' if reason else '',
             'models': ['m-auto', 'm-big'], 'model_tiers': {'m-auto': 'unranked', 'm-big': 'higher'},
             'model_efforts': {'m-big': ['low', 'high']}}
    other = {'id': 'claude-code', 'available': True, 'reason': '', 'models': ['c-1'], 'default_model': 'c-1'}
    return orchestrator.Catalogue([codex, other], {}, 'codex')


@pytest.mark.parametrize('reason', ['', 'default_model_refused'])
def test_the_plan_is_offered_only_the_pinned_model_and_effort(reason):
    module = SimpleNamespace(worker_catalogue=lambda service: catalogue(reason))
    eval_worker_pin.apply(module, PIN)
    held = module.worker_catalogue(None)
    [offered] = held.available()
    assert (offered['id'], offered['models'], offered['default_model']) == ('codex', ['m-pin'], 'm-pin')
    assert offered['model_efforts'] == {'m-pin': ['high']}
    assert held.worker('claude-code')['available'] is False

    # Still's own refusal handling leaves nothing to re-route to.
    run = orchestrator.Orchestration.__new__(orchestrator.Orchestration)
    run.catalogue = held
    run.drop_model('codex', 'm-pin')
    assert held.available() == []


def test_no_effort_pin_declares_no_effort_for_the_plan_to_choose():
    held = eval_worker_pin.hold(catalogue(), {**PIN, 'effort': ''})
    assert held.worker('codex')['model_efforts'] == {}


def test_a_pinned_sandbox_process_loads_the_wrapped_catalogue(tmp_path):
    probe = ('from personal_agent import quickstart_service\n'
             'print(quickstart_service.worker_catalogue.__name__)\n')

    def loaded(pin):
        env = sandbox.sandbox_environment(tmp_path, src=ROOT / 'src', worker_pin=pin)
        done = subprocess.run([sys.executable, '-c', probe], env=env, capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    assert loaded(PIN) == 'pinned_worker_catalogue'
    assert loaded(None) == 'worker_catalogue'


def test_the_pool_pins_only_the_pinned_workers_sandboxes(tmp_path, monkeypatch):
    class Box:
        def __init__(self, root, **kwargs):
            self.kwargs = kwargs

        def start(self, config=None):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(sandbox, 'reserve_port', lambda **kwargs: SimpleNamespace(port=18900, close=lambda: None))
    pool = sandbox.SandboxPool(size=1, root=tmp_path, sandbox_factory=Box, worker_pin=PIN)
    with pool.sandbox('codex') as box:
        assert box.kwargs['worker_pin'] == PIN
    with pool.sandbox('claude-code') as box:
        assert 'worker_pin' not in box.kwargs


class _Client:
    """A recorded refused turn: the Work failed with the CLI's own words."""

    def __init__(self, url):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: {}


FAILED, DONE = {'status': 'failed'}, {'status': 'succeeded'}


@pytest.mark.parametrize('refused_at, turns, launches, said', [
    (1, [DONE, FAILED], ['succeeded', 'failed'], 'refused by the provider'),
    (None, [FAILED, FAILED], ['failed', 'failed'], 'completed no turn'),   # e.g. an effort the CLI rejects
    (-3600, [DONE, FAILED], ['succeeded', 'failed'], None),                # a refusal the seed already had
    (None, [FAILED], ['succeeded'], None),                # the worker answered; the Work still failed
    (None, [FAILED], [], None),                           # no worker was launched
    (None, [{**FAILED, 'failure_class': 'usage-limit'}], ['failed'], None),
])
def test_a_pin_the_provider_rejects_is_a_failed_run_with_its_reason(refused_at, turns, launches, said, monkeypatch):
    reason = "The 'm-pin' model is not supported when using Codex with a ChatGPT account."
    rows = {} if refused_at is None else {orchestrator.MODEL_REFUSALS_KEY: json.dumps(
        {'codex': {'m-pin': {'at': time.time() + refused_at}}})}
    box = SimpleNamespace(url='', data=None, slot=None, worker_pin=PIN, wait_healthy=lambda client: True,
                          read_config=lambda keys: {key: rows[key] for key in keys if key in rows})

    class Pool:
        worker_pin = PIN

        @__import__('contextlib').contextmanager
        def sandbox(self, worker):
            yield box

    monkeypatch.setattr(runner, 'prepare_judgment', lambda *a, **k: {})
    monkeypatch.setattr(runner, 'sandbox_zone', lambda *a: None)
    monkeypatch.setattr(runner, 'run_turns', lambda *a, **k: [
        {**turn, 'error': reason if turn['status'] == 'failed' else None} for turn in turns])
    events = [{'tool': 'subscription_engine', 'status': status} for status in ('running', *launches)]
    monkeypatch.setattr(runner, 'work_measurements', lambda *a: {'t': {'tools': events if launches else None}})
    monkeypatch.setattr(runner, 'state_diff', lambda *a: {})
    record = runner.run_scenario({'id': 's', 'turns': []}, 'codex', Pool(), client_factory=_Client)
    assert record['worker_pin'] == {'model': 'm-pin', 'effort': 'high'}
    if said:
        assert all(part in record['error'] for part in ('m-pin', said, reason))
    else:
        assert record['error'] is None


SETTINGS = {'worker': 'codex', 'model': 'm-pin', 'effort': 'high'}


def recorded(model='m-pin', effort='high', pin={'model': 'm-pin', 'effort': 'high'}, settings=SETTINGS):
    """One arm built from samples shaped as a sweep log records them."""
    samples = []
    for ident in ('d', 'h'):
        for epoch in (1, 2):
            run = {'work': {'t': {'engine': [{'model': model, 'effort': effort}], 'refused_effects': 0}},
                   **({'worker_pin': pin} if pin else {})}
            score = SimpleNamespace(value={'passed': 1.0}, metadata={'scenario': ident, 'worker': 'codex',
                                                                      'checks': {}, 'judge_status': 'skipped'})
            samples.append(SimpleNamespace(id=f'{ident}@codex', epoch=epoch, metadata={'run': run},
                                           scores={report.SCORER: score}))
    return {'settings': dict(settings), 'scenario_ids': ['d', 'h'], 'records': report.records_from_samples(samples),
            'cost': {'calls': 1}}


def compared(**candidate):
    meta = {'d': {'split': 'dev'}, 'h': {'split': 'heldout'}}
    base = {**recorded(), 'text_digest': ac.text_digest('current')}
    new = {**recorded(**candidate), 'text_digest': ac.text_digest('revised')}
    return ac.compare_versioned_text(base, {'new': new}, meta=meta, target='passed', tolerances={'passed': 0},
                                     min_trials=2)


def test_arms_whose_runs_report_the_pinned_model_compare():
    result = compared()
    assert result['candidates']['new']['executions'] == 4
    assert all(row['worker_pin'] == {'model': 'm-pin', 'effort': 'high'} for row in recorded()['records'])


def test_a_mixed_model_arm_is_refused_and_told_to_pin():
    with pytest.raises(ValueError, match='--worker-model'):
        compared(model='m-routed', pin=None)


@pytest.mark.parametrize('pin', [{'model': 'm-other', 'effort': 'high'}, {'model': 'm-pin', 'effort': 'low'},
                                 {'model': 'm-pin', 'effort': ''}])
def test_a_run_pinned_otherwise_is_refused_even_when_its_engine_matches(pin):
    with pytest.raises(ValueError, match='worker pin'):
        compared(pin=pin)


class PinnedWork(Harness):
    def test_a_plan_naming_another_model_or_effort_runs_the_pinned_one(self):
        """Through Still's own planner: the setting alone would let the plan choose ``BUNDLED[1]``."""
        from personal_agent import quickstart_service
        pin = {'worker': 'codex', 'model': BUNDLED[0], 'effort': 'high'}
        self.service.decision_routes.bundled_efforts = lambda engine: {model: ['low', 'high'] for model in BUNDLED}
        sandbox.pin_main_ai(self.store.root, pin)
        original = quickstart_service.worker_catalogue
        self.addCleanup(setattr, quickstart_service, 'worker_catalogue', original)
        eval_worker_pin.apply(quickstart_service, pin)
        for index, (model, effort) in enumerate([(BUNDLED[1], 'low'), ('', '')]):
            with self.subTest(model=model):
                self.script([plan('codex', 'Check it.', model=model, effort=effort)], goals=[True])
                self.run_work(f'Fixture pinned work {index}')
                turn = self.engine.turns[-1]
                self.assertEqual((turn['model'], turn['effort']), (BUNDLED[0], 'high'))
