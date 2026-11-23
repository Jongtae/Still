"""#545: the eval's Claude Code worker on an Anthropic API key, the key in the worker process only.

A sentinel key goes through the real wrapper and the real ``Sandbox.start``
process boundary (a stand-in for the CLI and for the Still server, so no model
is reached).  It must reach the worker CLI's environment and nothing else: not
the sandbox server's environment or log, the data folder, the usage file, the
budget ledger, the forwarded CLI output or the run record.
"""
import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))

from agentos_eval import sandbox as sandbox_module  # noqa: E402
from agentos_eval.budget import JudgeBudget  # noqa: E402
from agentos_eval.runner import worker_api_record  # noqa: E402
from agentos_eval.worker_key import (REFUSED_EXIT, WorkerApi, WorkerKey, WorkerKeyError,  # noqa: E402
                                     load_worker_key, reported_cost, usage_totals)

SENTINEL = 'sk-ant-api03-SENTINEL545-' + 'q' * 24
SUBSCRIPTION_TOKEN = 'sk-ant-oat01-SUBSCRIPTION545-' + 'z' * 16
RESULT = {'type': 'result', 'subtype': 'success', 'result': 'done', 'total_cost_usd': 0.0123,
          'modelUsage': {'claude-sonnet-5-5': {'inputTokens': 1200, 'outputTokens': 80, 'cacheReadInputTokens': 0,
                                               'cacheCreationInputTokens': 0, 'costUSD': 0.01},
                         'claude-haiku-5-5': {'inputTokens': 300, 'outputTokens': 20, 'costUSD': 0.0023}}}


def fake_cli(folder, report=True):
    """A stand-in for the real ``claude``: records its environment, prints stream-json lines."""
    env_file = folder / 'worker-env.json'
    lines = [{'type': 'system', 'subtype': 'init'}, *([RESULT] if report else [])]
    script = folder / 'real-claude'
    script.write_text(textwrap.dedent(f'''\
        #!{sys.executable}
        import json, os, sys
        with open({str(env_file)!r}, 'w') as handle:
            json.dump({{'env': dict(os.environ), 'argv': sys.argv[1:]}}, handle)
        for line in json.loads({json.dumps(lines)!r}):
            print(json.dumps(line), flush=True)
        '''))
    script.chmod(0o700)
    return script, env_file


def install(tmp_path, cap=10.0, reserve=1.0, report=True):
    real, env_file = fake_cli(tmp_path, report=report)
    api = WorkerApi(WorkerKey(SENTINEL, 'test'), tmp_path / 'budget.json', cap, reserve, real_binary=real)
    folder = tmp_path / 'slot-0.claude-api'
    bin_dir = api.install(folder)
    return api, folder, bin_dir, env_file


def run_wrapper(bin_dir, args, extra_env=None):
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(bin_dir), 'CLAUDE_CODE_OAUTH_TOKEN': SUBSCRIPTION_TOKEN,
           **(extra_env or {})}
    return subprocess.run([str(bin_dir / 'claude'), *args], env=env, capture_output=True, timeout=60)


def all_text(folder):
    texts = []
    for path in Path(folder).rglob('*'):
        if path.is_file() and path.name != 'key':
            texts.append(path.read_bytes().decode('utf-8', 'replace'))
    return '\n'.join(texts)


def test_key_reaches_only_the_worker_cli_and_the_cost_is_charged(tmp_path):
    api, folder, bin_dir, env_file = install(tmp_path)
    done = run_wrapper(bin_dir, ['-p', '--output-format', 'stream-json'])
    assert done.returncode == 0, done.stderr
    worker = json.loads(env_file.read_text())
    # Present in the worker CLI's environment, as its documented variable, and the subscription is gone.
    assert worker['env']['ANTHROPIC_API_KEY'] == SENTINEL
    assert 'CLAUDE_CODE_OAUTH_TOKEN' not in worker['env']
    assert worker['env']['CLAUDE_CODE_SUBPROCESS_ENV_SCRUB'] == '1'
    # The CLI itself is bounded to the reservation the ledger admitted.
    assert worker['argv'] == ['-p', '--output-format', 'stream-json', '--max-budget-usd', '1']
    # The CLI's output reaches Still unchanged, and never carries the key.
    assert [json.loads(line)['type'] for line in done.stdout.decode().splitlines()] == ['system', 'result']
    assert SENTINEL not in done.stdout.decode() + done.stderr.decode()
    # Charged at the CLI's per-model cost against the shared daily eval budget.
    day = JudgeBudget(tmp_path / 'budget.json').status()
    assert day['worker_calls'] == 1 and day['worker_usd'] == pytest.approx(0.0123)
    assert day['usd'] == pytest.approx(0.0123)
    totals = usage_totals(folder)
    assert totals['launches'] == totals['settled'] == 1
    assert set(totals['models']) == {'claude-sonnet-5-5', 'claude-haiku-5-5'}
    # Neither the usage file, the ledger nor the wrapper/config hold the key; only the 0600 key file does.
    assert SENTINEL not in all_text(folder) + (tmp_path / 'budget.json').read_text()
    assert stat.S_IMODE((folder / 'key').stat().st_mode) == 0o600


def test_model_free_launches_pass_through_without_key_or_charge(tmp_path):
    api, folder, bin_dir, env_file = install(tmp_path)
    assert run_wrapper(bin_dir, ['--version']).returncode == 0
    assert 'ANTHROPIC_API_KEY' not in json.loads(env_file.read_text())['env']
    # Still's model-free qualification points the CLI at a loopback fake model with its own fake key.
    assert run_wrapper(bin_dir, ['-p'], {'ANTHROPIC_BASE_URL': 'http://127.0.0.1:9', 'ANTHROPIC_API_KEY': 'fake-key'}
                       ).returncode == 0
    assert json.loads(env_file.read_text())['env']['ANTHROPIC_API_KEY'] == 'fake-key'
    assert JudgeBudget(tmp_path / 'budget.json').status()['usd'] == 0
    # A non-loopback base URL is a model launch: the key and the charge apply.
    assert run_wrapper(bin_dir, ['-p'], {'ANTHROPIC_BASE_URL': 'https://api.anthropic.com'}).returncode == 0
    assert json.loads(env_file.read_text())['env']['ANTHROPIC_API_KEY'] == SENTINEL


def test_each_launch_is_bounded_to_its_reservation(tmp_path):
    api, folder, bin_dir, env_file = install(tmp_path, reserve=0.75)
    assert run_wrapper(bin_dir, ['-p', '--max-budget-usd', '9']).returncode == 0
    assert json.loads(env_file.read_text())['argv'] == ['-p', '--max-budget-usd', '0.75']
    assert run_wrapper(bin_dir, ['--print', '--max-budget-usd=0.2']).returncode == 0
    assert json.loads(env_file.read_text())['argv'] == ['--print', '--max-budget-usd', '0.2']


def test_login_check_sees_the_key_and_is_not_charged(tmp_path):
    """Still's ``claude auth status --json`` must report the API key, and is not a model run."""
    api, folder, bin_dir, env_file = install(tmp_path, report=False)
    done = run_wrapper(bin_dir, ['auth', 'status', '--json'])
    assert done.returncode == 0
    worker = json.loads(env_file.read_text())
    assert worker['env']['ANTHROPIC_API_KEY'] == SENTINEL
    assert 'CLAUDE_CODE_OAUTH_TOKEN' not in worker['env']
    assert worker['argv'] == ['auth', 'status', '--json']
    assert JudgeBudget(tmp_path / 'budget.json').status()['usd'] == 0
    assert usage_totals(folder)['launches'] == 0
    assert SENTINEL not in done.stdout.decode() + done.stderr.decode() + all_text(folder)


def test_cap_refuses_the_launch_before_the_cli_starts(tmp_path):
    api, folder, bin_dir, env_file = install(tmp_path, cap=0.5, reserve=1.0)
    done = run_wrapper(bin_dir, ['-p'])
    assert done.returncode == REFUSED_EXIT
    assert not env_file.exists()
    assert b'budget' in done.stderr
    assert usage_totals(folder)['refused'] == 1
    assert api.admits() is False
    pool = sandbox_module.SandboxPool(size=1, root=tmp_path / 'sandboxes', worker_api=api,
                                      sandbox_factory=lambda *a, **k: pytest.fail('no sandbox past the cap'))
    with pytest.raises(sandbox_module.SandboxError, match='budget'):
        with pool.sandbox('claude-code'):
            pass


def test_unreported_cost_keeps_the_reserve(tmp_path):
    api, folder, bin_dir, env_file = install(tmp_path, reserve=0.75, report=False)
    assert run_wrapper(bin_dir, ['-p']).returncode == 0
    day = JudgeBudget(tmp_path / 'budget.json').status()
    assert day['usd'] == pytest.approx(0.75) and day['worker_usd'] == pytest.approx(0.75)
    assert usage_totals(folder)['settled'] == 0


def test_reported_cost_prefers_per_model_cost():
    assert reported_cost(RESULT)[0] == pytest.approx(0.0123)
    assert reported_cost({'type': 'result', 'total_cost_usd': 0.5})[0] == 0.5
    assert reported_cost({'type': 'result'})[0] is None


def test_sandbox_server_never_holds_the_key(tmp_path, monkeypatch):
    """The real ``Sandbox.start`` boundary: the server process gets the wrapper on PATH, not the key."""
    api, _, _, _ = install(tmp_path)
    server = tmp_path / 'fake-python'
    server.write_text(f'#!{sys.executable}\nimport json, os, sys\n'
                      'print(json.dumps({"env": dict(os.environ), "argv": sys.argv[1:]}), flush=True)\n')
    server.chmod(0o700)
    monkeypatch.setenv('ANTHROPIC_API_KEY', SENTINEL)  # even when the eval process has it (an anthropic judge)
    box = sandbox_module.Sandbox(tmp_path / 'sandboxes', slot=3, python=str(server), worker_api=api)
    box.start()
    box.process.wait(timeout=30)
    log = (box.data / 'server.log').read_text()
    seen = json.loads(log.splitlines()[0])
    worker_folder = box.worker_folder
    assert seen['env']['PATH'].split(os.pathsep)[0] == str(worker_folder / 'bin')
    assert worker_folder.parent == box.data.parent and worker_folder != box.data
    assert (worker_folder / 'key').read_text() == SENTINEL
    assert SENTINEL not in log
    assert SENTINEL not in all_text(box.data)
    box.stop()
    assert not worker_folder.exists()
    assert box.worker_usage == {'launches': 0, 'settled': 0, 'refused': 0, 'usd': 0.0, 'models': {}}


def test_sandbox_environment_drops_the_key():
    env = sandbox_module.sandbox_environment('/tmp/data', base={'PATH': '/usr/bin', 'HOME': '/tmp',
                                                                 'ANTHROPIC_API_KEY': SENTINEL},
                                             worker_bin='/tmp/w/bin')
    assert SENTINEL not in json.dumps(env)
    assert env['PATH'].startswith('/tmp/w/bin' + os.pathsep)


def test_run_record_carries_usage_and_never_the_key(tmp_path):
    api = WorkerApi(WorkerKey(SENTINEL, 'test'), tmp_path / 'b.json', 10, 1, real_binary='/bin/true')
    pool = type('Pool', (), {'worker_api': api})()
    box = type('Box', (), {'worker_usage': {'launches': 1, 'settled': 1, 'refused': 0, 'usd': 0.2, 'models': {}}})()
    record = {'worker': 'claude-code', 'turns': [{'answer': f'the key is {SENTINEL}'}]}
    scrubbed = worker_api_record(record, pool, box)
    assert SENTINEL not in json.dumps(scrubbed)
    assert scrubbed['worker_api']['usd'] == 0.2
    assert worker_api_record({'worker': 'codex'}, pool, box) == {'worker': 'codex'}
    assert SENTINEL not in repr(api.key) + str(api.key) + repr(vars(api)).replace(repr(api.key), '')


def test_key_loading_prefers_the_keychain_and_never_sets_the_environment():
    environ = {}
    key = load_worker_key(environ, keychain=lambda account: SENTINEL if account == 'anthropic' else '')
    assert key.source == 'keychain' and key.reveal() == SENTINEL and environ == {}
    assert load_worker_key({'ANTHROPIC_API_KEY': SENTINEL}, keychain=lambda _: '').source == 'environment'
    with pytest.raises(WorkerKeyError):
        load_worker_key({}, keychain=lambda _: '')


def test_task_selects_the_api_key_only_for_claude_code(tmp_path):
    pytest.importorskip('inspect_ai')
    from agentos_eval.task import claude_worker_api
    loader = lambda: WorkerKey(SENTINEL, 'keychain')  # noqa: E731
    assert claude_worker_api('codex', key_loader=loader) is None
    assert claude_worker_api('claude-code', 'subscription', key_loader=loader) is None
    api = claude_worker_api('both', key_loader=loader, budget_path=tmp_path / 'b.json')
    assert api.key.reveal() == SENTINEL
    with pytest.raises(SystemExit):
        claude_worker_api('claude-code', key_loader=lambda: (_ for _ in ()).throw(WorkerKeyError('none')))


def test_world_sweep_runs_claude_code_on_the_api_key(tmp_path, capsys, monkeypatch):
    """``sweep --world shop --worker claude-code`` takes the same API-key route as a scenario sweep."""
    monkeypatch.delenv('AGENTOS_EVAL_JUDGE_CAP_USD', raising=False)
    from argparse import Namespace
    from agentos_eval import cli
    pools, ran = [], []

    def pool_factory(**kw):
        pools.append(kw)
        return type('Pool', (), {'worker_api': kw['worker_api']})()

    def fake_run(scenario, pool, turn_timeout, judgment_timeout, worker='codex'):
        ran.append(worker)
        box = type('Box', (), {'worker_usage': {'launches': 2, 'settled': 2, 'refused': 0, 'usd': 0.3,
                                                'models': {'claude-sonnet-5-5': {}}}})()
        record = {'worker': worker, 'error': None, 'turns': [{'status': 'succeeded', 'answer': SENTINEL}],
                  'approvals': [], 'decisions': [], 'still': {'reply_matches_world': True},
                  'world': {'name': 'shop', 'task_id': 'clear-then-add', 'task_set': 'dev',
                            'evidence_class': 'sandbox-world',
                            'result': {'passed': True, 'num_tests': 1, 'failures': [], 'collateral': [],
                                       'unreached': []}}}
        return worker_api_record(record, pool, box)

    args = Namespace(world='shop', worker='claude-code', scenarios='world-shop-clear-then-add', no_local=True,
                     split=None, seed='', instances=1, epochs=1, limit=None, turn_timeout=10, keep_sandboxes=False)
    assert cli.run_world_sweep(args, run_scenario=fake_run, pool_factory=pool_factory, folder=tmp_path / 'reports',
                               worker_key_loader=lambda: WorkerKey(SENTINEL, 'keychain')) == 0
    assert ran == ['claude-code'] and pools[0]['worker_api'].key.reveal() == SENTINEL
    assert pools[0]['worker_api'].cap_usd == 10.0  # no --cap-usd: the default daily cap
    out = capsys.readouterr().out
    assert 'API key from Keychain' in out and 'USD 0.3' in out
    report = next((tmp_path / 'reports').rglob('*.json')).read_text()
    assert SENTINEL not in report
    # --cap-usd bounds the world route's worker, a zero cap included.
    for cap in (5.0, 0.0):
        args.cap_usd = cap
        pools.clear()
        cli.run_world_sweep(args, run_scenario=fake_run, pool_factory=pool_factory, folder=tmp_path / f'r{cap}',
                            worker_key_loader=lambda: WorkerKey(SENTINEL, 'keychain'))
        assert pools[0]['worker_api'].cap_usd == cap
    # The subscription choice gives the world pool no API worker.
    args.claude_auth = 'subscription'
    pools.clear()
    cli.run_world_sweep(args, run_scenario=fake_run, pool_factory=pool_factory, folder=tmp_path / 'reports2',
                        worker_key_loader=lambda: pytest.fail('no key for the subscription'))
    assert pools[0]['worker_api'] is None


def test_real_cli_is_found_like_still_finds_it(tmp_path, monkeypatch):
    """A CLI only in ``~/.local/bin`` (not on the eval's minimal PATH) is found, as Still's find_cli finds it."""
    from agentos_eval import worker_key
    home = tmp_path / 'home'
    (home / '.local' / 'bin').mkdir(parents=True)
    cli = home / '.local' / 'bin' / 'claude'
    cli.write_text('#!/bin/sh\n')
    cli.chmod(0o700)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    assert worker_key._real_claude('/usr/bin:/bin') == cli.resolve()
    if not any(os.access(f'{folder}/claude', os.X_OK) for folder in ('/opt/homebrew/bin', '/usr/local/bin')):
        monkeypatch.setenv('HOME', str(tmp_path / 'empty'))
        with pytest.raises(WorkerKeyError):
            worker_key._real_claude('/usr/bin:/bin')
