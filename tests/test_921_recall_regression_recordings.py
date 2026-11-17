"""Validate committed inputs without replaying Works or calling a live engine."""
import json
from pathlib import Path

import pytest
from agentos_eval import recall_pairs as pairs
from agentos_eval.recall_regression import (layer,validate_terms,validate_plans,validate_recording_safety)
from personal_agent.quickstart_store import QuickStore

ROOT=Path(__file__).resolve().parents[1]
RECORDINGS=ROOT/'evals/recall/recordings'


def test_committed_recordings_are_complete_bound_and_synthetic(tmp_path,monkeypatch):
    terms_path,plans_path=RECORDINGS/'terms.json',RECORDINGS/'plans.json'
    if not terms_path.exists() or not plans_path.exists():
        pytest.skip('Committed terms.json and plans.json are absent')
    from agentos_eval.recall_regression import Executor
    def no_replay(*a,**kw):
        pytest.fail('Committed-input validation must not replay Works')
    monkeypatch.setattr(Executor,'run',no_replay)
    data=layer(pairs.load(ROOT/'evals/recall/recall-pairs-v4.json'),
               json.loads((ROOT/'evals/recall/recall-gate-dev-1.json').read_text()))
    terms,plans=json.loads(terms_path.read_text()),json.loads(plans_path.read_text())
    validate_terms(terms,data)
    validate_plans(plans,data,terms)
    store=QuickStore(tmp_path/'state')
    for recording in (terms,plans):
        assert recording['complete'] is True
        validate_recording_safety(recording,store)


@pytest.mark.parametrize('value',['/Users/invented/private','read /tmp/private',r'C:\Users\private',
                                  r'\\server\share\private','api_key=sk-'+'x'*48])
def test_recording_safety_refuses_paths_and_secret_shapes(tmp_path,value):
    with pytest.raises(ValueError,match='absolute path|secret shape'):
        validate_recording_safety(dict(tokens='not observable',nested=[dict(value=value)]),QuickStore(tmp_path/'state'))


def test_recording_safety_tokens_and_clean_fixture(tmp_path):
    store=QuickStore(tmp_path/'state')
    value=dict(tokens='not observable',version='recall-plan/2',query='invented content')
    assert validate_recording_safety(value,store)==value
    with pytest.raises(ValueError,match='tokens'):
        validate_recording_safety(dict(value,tokens=100),store)

