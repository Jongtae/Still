"""The development rehearsal keeps the route copier formerly hosted in family setup."""
import json
import runpy
from pathlib import Path

from personal_agent.quickstart_store import QuickStore


def test_rehearsal_copies_only_routes_and_their_own_secrets(tmp_path):
    copy = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/rehearsal/run.py'))['copy_ai_route']
    owner = QuickStore(tmp_path / 'owner')
    scratch = QuickStore(tmp_path / 'scratch')
    assert copy(owner, scratch) is None
    owner.put('model', {'provider': 'openai', 'model': 'fixture'})
    owner.put('model_test', {'ok': True})
    owner.secret('model_key', 'fixture-model-key')
    owner.put('decision_route', {'transport': 'direct_api'})
    owner.put('decision_route_checks', [{'ok': True}])
    owner.put('decision_jev', {'model': 'fixture'})
    owner.secret('decision_jev_key', 'fixture-jev-key')
    owner.secret('decision_model_key', 'orphan-key')
    owner.secret('telegram_token', 'owner-token')
    copied = copy(owner, scratch)
    assert scratch.config('model_test') == {'ok': True}
    assert scratch.config('decision_route_checks') == [{'ok': True}]
    assert scratch.secret('model_key') == 'fixture-model-key'
    assert scratch.secret('decision_jev_key') == 'fixture-jev-key'
    assert not scratch.secret('decision_model_key')
    assert not scratch.secret('telegram_token')
    assert 'fixture-model-key' not in json.dumps(copied)
    owner.put('model', {'provider': 'other'})
    assert copy(owner, scratch) == []
    assert scratch.config('model')['model'] == 'fixture'
