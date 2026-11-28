"""#1359: every check a run can carry is a padded score key, so mixed sweeps give Inspect the same keys."""
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import scoring


def test_every_check_deterministic_checks_can_set_is_a_padded_key():
    source = inspect.getsource(scoring.deterministic_checks)
    names = set(re.findall(r"checks\['(\w+)'\]", source)) | set(re.findall(r"\{'(\w+)': False\}", source))
    assert 'readable' in names
    assert names <= set(scoring.CHECK_NAMES)


def test_readable_and_other_scenarios_score_the_same_keys():
    run = {'turns': [{'status': 'succeeded'}]}
    readable = {'id': 'a', 'split': 'dev', 'turns': [{}], 'expect': {'readable': True}}
    other = {'id': 'b', 'split': 'dev', 'turns': [{}], 'expect': {}}
    one, _ = scoring.combine(readable, run, {'delivered': True, 'readable': True}, [])
    two, _ = scoring.combine(other, run, {'delivered': True}, [])
    assert one.keys() == two.keys() and 'check_readable' in two
