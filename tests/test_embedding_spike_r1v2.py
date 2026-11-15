"""Model-free adapter/import and combined #849 gate checks."""
import copy
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import recall_report as rr  # noqa: E402
from evals.embedding_spike import r1v2  # noqa: E402
from evals.embedding_spike.r1v2_measure import combined_gate  # noqa: E402


def test_import_without_inference_packages():
    code = '''
import importlib.abc
import sys
class NoInference(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy', 'onnxruntime', 'tokenizers'}:
            raise ImportError('inference extras intentionally unavailable')
sys.meta_path.insert(0, NoInference())
from evals.embedding_spike import r1v2
from evals.embedding_spike import r1v2_measure
from agentos_eval.recall_report import resolve_arm
for name in ('vector', 'hybrid', 'hybrid_lexical_context', 'hybrid_runtime'):
    assert callable(resolve_arm('evals.embedding_spike.r1v2:' + name).factory)
assert not {'numpy', 'onnxruntime', 'tokenizers'} & sys.modules.keys()
'''
    done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=dict(
        os.environ, PYTHONPATH='src:evals:.'), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize('name', ['vector', 'hybrid', 'hybrid_lexical_context', 'hybrid_runtime'])
def test_factory_resolution(name):
    arm = rr.resolve_arm('evals.embedding_spike.r1v2:' + name)
    assert arm.factory is getattr(r1v2, name)
    assert arm.evidence == 'local operating observation (offline embedding model)'
    assert arm.deterministic is True


def reports(*, different_fixes=False, trap=False, regression=False, noise_only=False):
    definitions = [('d', 'dev', 'lexical-control'), ('h1', 'heldout', 'paraphrase'),
                   ('h2', 'heldout', 'cross-language'), ('hc', 'heldout', 'lexical-control')]
    data = dict(version='recall-pairs/2', today='2026-01-01',
                cases=[dict(id=i, query=i, split=s, kind=k) for i, s, k in definitions])
    candidate = [dict(id=i, split=s, kind=k, needed_total=1, needed_hit=1,
                      trap_hits=0, noise_hits=0, returned=[i]) for i, s, k in definitions]
    lexical, context = copy.deepcopy(candidate), copy.deepcopy(candidate)
    if noise_only:
        lexical[1]['noise_hits'] = context[1]['noise_hits'] = 1
    else:
        lexical[1]['needed_hit'] = 0
        context[2 if different_fixes else 1]['needed_hit'] = 0
    if trap:
        candidate[0]['trap_hits'] = 1
    if regression:
        candidate[3]['needed_hit'] = 0

    def result(rows):
        return dict(evidence='fixture (model-free)', deterministic=True, runs=[dict(
            version=data['version'], fixture=rr._fixture_digest(data), rows=rows)])

    observations = dict(lexical=result(lexical), **{'lexical-context': result(context)},
                        vector=result(candidate))
    return {name: rr.report(data, observations, baseline=name) for name in ('lexical', 'lexical-context')}


def test_combined_gate_pass():
    verdict = combined_gate(reports(), 'vector')
    assert verdict['preferred']
    assert verdict['common_needed_recovery'] == ['h1']


def test_combined_gate_no_common_fix():
    verdict = combined_gate(reports(different_fixes=True), 'vector')
    assert verdict['rules']['preferred_against_both']
    assert not verdict['preferred']
    assert verdict['common_fixed'] == []


def test_combined_gate_trap():
    verdict = combined_gate(reports(trap=True), 'vector')
    assert not verdict['preferred']
    assert not verdict['rules']['zero_traps_both_splits']


def test_combined_gate_regression():
    verdict = combined_gate(reports(regression=True), 'vector')
    assert not verdict['preferred']
    assert not verdict['rules']['no_heldout_regression']
    assert not verdict['rules']['no_lost_lexical_control']


def test_noise_removal_is_not_needed_recovery():
    verdict = combined_gate(reports(noise_only=True), 'vector')
    assert verdict['rules']['preferred_against_both']
    assert verdict['common_fixed'] == ['h1']
    assert not verdict['preferred']
    assert verdict['common_needed_recovery'] == []


def test_runtime_composition_is_explicitly_unavailable():
    with pytest.raises(NotImplementedError, match='jobs/messages'):
        r1v2.hybrid_runtime(None, None)
