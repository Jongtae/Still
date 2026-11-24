"""#1199: an eval sandbox root where Still's strict isolation can never qualify is refused up front.

Model-free. The product check (``bounded_execution`` ``runtime-root-not-baseline-readable``) is unchanged;
the harness reads the same constant from the checkout a sandbox runs and refuses before any server starts.
"""
from pathlib import Path
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import sandbox as sb


def test_harness_reads_the_products_own_baseline_roots():
    from personal_agent.bounded_execution import BASELINE_READABLE_ROOTS
    assert sb.baseline_readable_roots(ROOT / 'src') == tuple(BASELINE_READABLE_ROOTS)
    # The product's protection itself still covers the system temp folders.
    assert {'/tmp', '/private/tmp'} <= set(BASELINE_READABLE_ROOTS)


@pytest.mark.parametrize('base', ['/tmp', '/private/tmp'])
def test_sandbox_under_a_baseline_readable_root_is_refused_before_anything_is_made(base):
    root = Path(base) / f'agentos-eval-1199-{uuid.uuid4().hex}' / 'sandboxes'
    box = sb.Sandbox(root, src=ROOT / 'src')
    with pytest.raises(sb.SandboxError, match='Judgment AI cannot qualify'):
        box.prepare()
    assert not root.parent.exists()


def test_sandbox_outside_the_baseline_roots_prepares(tmp_path):
    # Pytest's own temp folder is not one of the product's baseline-readable roots.
    roots = [Path(item).resolve() for item in sb.baseline_readable_roots(ROOT / 'src')]
    assert not any(tmp_path.resolve() == item or item in tmp_path.resolve().parents for item in roots)
    data = sb.Sandbox(tmp_path / 'sandboxes', src=ROOT / 'src').prepare()
    assert data.is_dir() and data.parent == (tmp_path / 'sandboxes')


def test_a_checkout_without_the_check_is_not_refused(tmp_path):
    # An older baseline checkout (or a stand-in src) has no such constant: nothing to mirror.
    assert sb.baseline_readable_roots(tmp_path / 'no-src') == ()
    assert sb.refuse_baseline_readable(Path('/private/tmp') / 'x', tmp_path / 'no-src') == Path('/private/tmp/x').resolve()
