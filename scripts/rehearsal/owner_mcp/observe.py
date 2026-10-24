"""OWNER-MCP-WORK-01 (#210): a confirmed owner MCP server serving a call in a Still Work, model-free.

    PYTHONPATH=src python3 scripts/rehearsal/owner_mcp/observe.py codex --location-env
    PYTHONPATH=src python3 scripts/rehearsal/owner_mcp/observe.py claude-code --tool comment_ticket

Needs the installed CLIs (/opt/homebrew/bin/codex and claude) and the ``mcp`` package for the
fixture server.  No model provider or company system is contacted: the CLI's model endpoint is a
local fixture.  Prints a JSON report; the scratch directory is removed unless ``--keep``.

Real: the Still service (``run_one``), its CLI launch (argv, env, per-turn MCP
config), the CLI itself, the owner server process, the Codex hook / Claude Code
permission prompt, the relay and ``connector_permission``.  Fixture: the model
endpoint (a local server that asks for one tool call), the orchestration model,
the owner's home (a temporary directory) and the company token mechanism (a
"company CLI" script that reads a file under HOME).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('engine', choices=('codex', 'claude-code'))
    parser.add_argument('--tool', default='read_ticket')
    parser.add_argument('--location-env', action='store_true', help='the definition sets PATH/HOME for its server')
    parser.add_argument('--keep', action='store_true')
    parser.add_argument('--args', default='{"key": "FIX-1", "text": "note"}', help='the tool call arguments, JSON')
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix='still-owner-mcp-'))
    home = root / 'home'
    (home / '.company-fixture').mkdir(parents=True)
    token = f'company-fixture-token-{os.getpid()}'
    (home / '.company-fixture' / 'token').write_text(token + '\n')
    bin_dir = root / 'company-bin'
    bin_dir.mkdir()
    (bin_dir / 'company-auth').write_text('#!/bin/sh\n[ "$1" = token ] && cat "$HOME/.company-fixture/token" 2>/dev/null\n')
    (bin_dir / 'company-auth').chmod(0o755)
    server_log, model_log = root / 'server.log', root / 'model.log'
    secret = f'owner-server-secret-{os.getpid()}'
    env = {'FIXTURE_LOG': str(server_log), 'COMPANY_TOKEN_NAME': secret}
    if args.location_env:
        env.update({'PATH': f'{bin_dir}:/usr/bin:/bin', 'HOME': str(home)})
    definition = {'command': PYTHON, 'args': [str(HERE / 'fixture_server.py')], 'env': env}

    codex_home = root / 'codex-home'
    codex_home.mkdir()
    toml = ['[mcp_servers.fixture]', f'command = {json.dumps(PYTHON)}', f'args = {json.dumps(definition["args"])}',
            '[mcp_servers.fixture.env]', *[f'{k} = {json.dumps(v)}' for k, v in env.items()]]
    (codex_home / 'config.toml').write_text('\n'.join(toml) + '\n')
    (home / '.claude.json').write_text(json.dumps({'mcpServers': {'fixture': {'type': 'stdio', **definition}}}))

    model = subprocess.Popen([PYTHON, str(HERE / 'fixture_model.py'), '0'], stdout=subprocess.PIPE, text=True,
                             stderr=subprocess.DEVNULL,
                             env={**os.environ, 'FIXTURE_TOOL': args.tool, 'FIXTURE_MODEL_LOG': str(model_log),
                                  'FIXTURE_ARGS': args.args})
    port = int(model.stdout.readline())
    base = f'http://127.0.0.1:{port}'
    launched = []

    def runner(argv, **kwargs):
        argv, kwargs = list(argv), dict(kwargs)
        if kwargs.get('stdin') is None and 'input' not in kwargs:
            kwargs['stdin'] = subprocess.DEVNULL
        kwargs.setdefault('timeout', 240)
        if Path(argv[0]).name == 'codex' and argv[1:2] == ['exec']:
            argv[2:2] = ['-c', 'model_provider="fixture"', '-c', 'model_providers.fixture.name="fixture"',
                         '-c', f'model_providers.fixture.base_url="{base}/v1"',
                         '-c', 'model_providers.fixture.wire_api="responses"',
                         '-c', 'model_providers.fixture.env_key="FIXTURE_MODEL_KEY"', '-m', 'fixture-model']
            kwargs['env'] = {**kwargs['env'], 'FIXTURE_MODEL_KEY': 'fixture'}
            launched.append({'argv': argv, 'env': kwargs['env']})
        elif Path(argv[0]).name == 'claude' and '-p' in argv:
            kwargs['env'] = {**kwargs['env'], 'ANTHROPIC_BASE_URL': base, 'ANTHROPIC_API_KEY': 'fixture',
                             'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
            launched.append({'argv': argv, 'env': kwargs['env']})
        return subprocess.run(argv, **kwargs)

    with mock.patch.object(Path, 'home', classmethod(lambda cls: home)):
        from personal_agent import owner_mcp, trust_record
        from personal_agent.bounded_execution import BoundedExecutionAdapter
        from personal_agent.browser_session import BrowserProfile
        from personal_agent.providers import ModelAdapter
        from personal_agent.quickstart_service import AgentService
        from personal_agent.quickstart_store import QuickStore
        from personal_agent.subscription_engines import SubscriptionEngines
        store = QuickStore(root / 'state')
        adapter = BoundedExecutionAdapter(finder=lambda name: f'/opt/homebrew/bin/{name}', runner=runner,
                                          runtime_root=root / 'turns', codex_home=codex_home)
        service = AgentService(store, adapter=ModelAdapter(lambda *a: {'choices': [{'message': {'content': 'x'}}]}),
                               subscription_engines=SubscriptionEngines(
                                   finder=lambda name: f'/opt/homebrew/bin/{name}', clock=time.time),
                               execution_adapter=adapter, browser_profile=BrowserProfile(root / 'browser', available=lambda: False))
        # The fixture model needs no CLI login; the login check is not what is observed.
        service.check_engine_login = lambda engine_id: {'state': 'unknown'}
        service.connect_subscription_engine({'engine': args.engine, 'officially_authenticated': True})
        service.cli_native_search = lambda *a: (False, 'refused')
        service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(store, args.engine, ['fixture'])
        target = trust_record.connection(args.engine, 'owner-mcp', 'fixture')
        trust_record.set_operation(store, target, 'read_ticket', 'read')
        trust_record.set_operation(store, target, 'comment_ticket', 'mutate')
        work = store.enqueue('read ticket FIX-1', f'observe-{os.getpid()}')
        ran = service.run_one()
        job = store.job(work)
        with store.db() as db:
            decisions = [json.loads(row['detail']) | {'status': row['status']} for row in db.execute(
                "SELECT status, detail FROM tool_events WHERE job_id=? AND tool='connector_permission'", (work,))]
            records = ' '.join(str(row[0]) for row in db.execute('SELECT detail FROM tool_events'))
    model.terminate()

    server_calls = [json.loads(line) for line in server_log.read_text().splitlines()] if server_log.exists() else []
    model_calls = [json.loads(line) for line in model_log.read_text().splitlines()] if model_log.exists() else []
    tool_outputs = [item for call in model_calls for item in (call.get('tool_outputs') or call.get('tool_results') or [])]
    launch = launched[-1] if launched else {}
    report = {
        'engine': args.engine, 'tool': args.tool, 'location_env': args.location_env, 'ran': ran,
        'work_status': job.get('status'), 'answer': (job.get('result') or '')[:120],
        'server_saw': server_calls, 'tool_output_to_model': [str(o)[:200] for o in tool_outputs],
        'decisions': decisions,
        'secret_in_argv': secret in ' '.join(launch.get('argv', [])),
        'secret_in_cli_env': secret in json.dumps(launch.get('env', {})),
        'token_in_records': token in records, 'secret_in_records': secret in records,
        'token_in_model_requests': token in model_log.read_text() if model_log.exists() else None,
        'secret_in_model_requests': secret in model_log.read_text() if model_log.exists() else None,
        'root': str(root),
    }
    print(json.dumps(report, indent=1, ensure_ascii=False))
    if not args.keep:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
