"""Validate committed gzip inputs and replay only three cases, without live calls."""
import json
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))

from agentos_eval import recall_pairs as pairs  # noqa: E402
from agentos_eval.recall_regression import (Executor, apply_terms, catalogue_from, fixture_service,  # noqa: E402
    layer, load_recording, replay_case, seed_fixture, validate_terms, validate_plans, validate_recording_safety)
from personal_agent.quickstart_store import QuickStore  # noqa: E402

RECORDINGS=ROOT/'evals/recall/recordings'


@pytest.fixture(scope='module')
def recordings():
    paths = [RECORDINGS/name for name in ('terms.json.gz','plans.json.gz')]
    if not any(path.exists() for path in paths):
        pytest.skip('Committed terms.json.gz and plans.json.gz are absent')
    assert all(path.is_file() for path in paths), 'Both committed recordings must be present'
    for path in paths:
        header = path.read_bytes()[:10]
        assert header[:4] == b'\x1f\x8b\x08\x00'  # gzip/deflate, no optional fields or stored name
        assert header[4:8] == b'\x00'*4  # mtime 0
        assert header[8] == 2  # maximum compression (gzip -9 -n)
    data = layer(pairs.load(ROOT/'evals/recall/recall-pairs-v4.json'),
                 json.loads((ROOT/'evals/recall/recall-gate-dev-1.json').read_text()))
    return data, *(load_recording(path) for path in paths)


def test_committed_recordings_are_complete_bound_and_synthetic(tmp_path,monkeypatch,recordings):
    def no_replay(*a,**kw):
        pytest.fail('Committed-input validation must not replay Works')
    monkeypatch.setattr(Executor,'run',no_replay)
    data,terms,plans = recordings
    validate_terms(terms,data)
    validate_plans(plans,data,terms)
    store=QuickStore(tmp_path/'state')
    for recording in (terms,plans):
        assert recording['complete'] is True
        assert recording['evidence_class'] == 'live-route:subscription_cli'
        validate_recording_safety(recording,store)


def test_first_three_recorded_cases_replay_exactly(tmp_path,monkeypatch,recordings):
    def forbidden(*a,**kw):
        pytest.fail('Replay must not launch a process or contact a live service')
    monkeypatch.setattr('subprocess.Popen',forbidden)
    monkeypatch.setattr('socket.create_connection',forbidden)
    monkeypatch.setattr('urllib.request.urlopen',forbidden)
    data,terms,plans = recordings
    store = QuickStore(tmp_path/'state')
    service = fixture_service(store)
    refs = seed_fixture(store,data)
    apply_terms(store,data,terms,refs)
    executor = Executor(service,data,refs,catalogue_from(plans['catalogue']))
    cases = {case['id']:case for case in data['cases']}
    sample = list(plans['runs']['1'].items())[:3]
    assert len(sample) == 3
    for ident,entry in sample:
        replay_case(executor,cases[ident],entry)


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

