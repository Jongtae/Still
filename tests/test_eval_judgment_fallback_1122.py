"""#1122: a sandbox whose seeded judgment route cannot qualify falls back to the worker's route, or stops as infra."""
from types import SimpleNamespace
from unittest import mock

from agentos_eval import runner
from agentos_eval.client import AgentOSClient


def api_status(ready=False):
    return {'available': ready, 'mode': 'route', 'main': 'codex', 'engine': 'anthropic', 'model': 'claude-haiku-5-5',
            'active': {'transport': 'api', 'engine': 'anthropic'}, 'qualification': {'state': 'failed'}}


def follow_status(ready):
    return {'available': ready, 'mode': 'follow_main', 'main': 'codex', 'engine': 'codex', 'model': 'fixture',
            'qualification': {}}


def client_for(*waits):
    client = mock.Mock()
    client.judgment.return_value = api_status()
    client.wait_judgment.side_effect = list(waits)
    client.judgment_ready.side_effect = AgentOSClient.judgment_ready
    return client


def test_an_unqualifiable_api_route_falls_back_to_the_worker_route_and_says_so():
    client = client_for(api_status(), follow_status(True))
    result = runner.prepare_judgment(client, mock.Mock(), SimpleNamespace(cached=None), 'codex')
    client.post.assert_called_once_with('/api/decision-route/activate', {'transport': 'follow_main'})
    assert result['ready'] is True and result['fallback'] == 'follow_main'
    assert result['mode'] == 'follow_main' and result['engine'] == 'codex'


def test_a_ready_route_is_left_as_the_owner_set_it():
    client = client_for()
    client.judgment.return_value = {**api_status(True), 'qualification': {}}
    result = runner.prepare_judgment(client, mock.Mock(), SimpleNamespace(cached=None), 'codex')
    client.post.assert_not_called()
    assert result['ready'] is True and 'fallback' not in result


def test_a_world_run_without_a_judgment_ai_is_an_infra_error_not_a_product_failure(monkeypatch):
    monkeypatch.setattr(runner, 'prepare_judgment', lambda *args, **kwargs: {'ready': False})
    world = mock.MagicMock()
    world.__enter__.return_value = world
    world.instruction.return_value = 'Add an item.'
    world.browser_origins.return_value = ['127.0.0.1:8001']
    box = mock.MagicMock()
    pool = mock.MagicMock()
    pool.sandbox.return_value.__enter__.return_value = box
    scenario = {'id': 'world-shop-x', 'turns': [{'from_world': True}],
                'world': {'name': 'shop', 'task_id': 'x', 'task_set': 'dev', 'approvals': {}, 'money_capable': False}}
    turn = mock.Mock()
    monkeypatch.setattr(runner, 'world_turn', turn)
    record = runner.run_world_scenario(scenario, pool, client_factory=lambda url: mock.Mock(),
                                       world_loader=lambda *args: world, worker='codex')
    assert record['error'].startswith('SandboxError: infra:judgment_ai')
    turn.assert_not_called()


def test_a_seed_with_no_judgment_route_keeps_that_choice():
    client = client_for()
    client.judgment.return_value = {**api_status(), 'active': {'transport': 'none'}}
    client.requalify_judgment.return_value = None
    result = runner.prepare_judgment(client, mock.Mock(), SimpleNamespace(cached=None), 'codex')
    client.post.assert_not_called()
    client.wait_judgment.assert_not_called()
    assert result['ready'] is False and 'fallback' not in result
