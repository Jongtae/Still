"""The root delivery plan describes Still, not its private predecessor (#46).

Still was imported without history. The predecessor plan is archived unchanged
under docs/history/, where the historical governance tests read it; the root
plan started empty and selected nothing until the owner activated a goal
(BROWSER-REL-01, #94).
"""
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest import TestCase

from scripts.dev.delivery import DeliveryPlan

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "delivery-plan.yaml"
ARCHIVE = ROOT / "docs" / "history" / "predecessor-delivery-plan.yaml"

# The shared real-controller assertions, loaded by path so --noconftest runs work.
_spec = importlib.util.spec_from_file_location(
    "fresh_plan_invariants", ROOT / "tests" / "delivery_state_invariants.py"
)
invariants = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(invariants)


def plan():
    return json.loads(PLAN.read_text(encoding="utf-8"))


def test_the_root_plan_names_this_repository():
    assert plan()["repository"] == "Jongtae/Still"


def test_the_predecessor_plan_is_archived_and_named():
    archived = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    assert archived["repository"] == "Jongtae/agentos"
    assert archived["next_goal"]["id"] == "V1.4-01"
    history = plan()["history"]
    assert history["predecessor_plan"] == "docs/history/predecessor-delivery-plan.yaml"
    assert history["predecessor_repository"] == "Jongtae/agentos"


def test_no_predecessor_program_or_iteration_carries_over():
    current = plan()
    archived = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    inherited = {item["id"] for item in archived["iterations"]} | set(archived["programs"])
    assert not inherited & {item["id"] for item in current["iterations"]}
    assert not inherited & set(current["programs"])
    assert current["history"]["documented_completed_iterations"] == []


def test_the_owner_activated_browser_program_is_goal_ready_and_heartbeat_paused():
    """#94: the owner selected BROWSER-REL-01; execution stays session-driven."""
    current = plan()
    goal = current["next_goal"]
    assert goal["id"] == "BROWSER-REL-01"
    assert invariants.assert_declared_goal_shape(TestCase(), plan=current) == "goal-ready"
    invariants.assert_active_substeps_are_legitimate(TestCase(), current, goal["id"])
    program = current["programs"][goal["id"]]
    assert program["activated_by"]["issue"] == 94
    order = ["BROWSER-INPUT-01", "BROWSER-LOADMORE-01", "BROWSER-PROFILE-01"]
    assert program["ordered_substeps"] == order
    items = {item["id"]: item for item in current["iterations"]}
    assert [items[name]["issue"] for name in order] == [12, 11, 13]
    for previous, name in zip([None] + order, order):
        assert items[name]["program"] == goal["id"]
        assert items[name]["activation_status"] == "parent-controlled"
        assert items[name]["depends_on"] == ([previous] if previous else [])
    for state in ({}, {"completed": []}, {"active": "BROWSER-REL-01", "status": "running"}):
        assert DeliveryPlan(PLAN).select(state) is None, state


def test_the_controller_can_run_the_program_once_the_owner_makes_it_active(tmp_path):
    """#100: every controller path reads the selected iteration's milestone."""
    from scripts.dev.delivery import DeliveryController

    altered = plan()
    altered["next_goal"] = {"id": "BROWSER-REL-01", "status": "active"}
    root = tmp_path / "repo"
    root.mkdir()
    (root / "delivery-plan.yaml").write_text(json.dumps(altered), encoding="utf-8")
    controller = DeliveryController(root, tmp_path / "state.json", invariants.RecordingRunner(), now=lambda: 1_700_000_000)
    status = controller.status()
    assert (status["active"], status["milestone"]) == ("BROWSER-REL-01", "browser-reliability")
    assert controller.plan.milestone_title(controller.plan.items["BROWSER-REL-01"]) == "Browser reliability (BROWSER-REL-01)"


def test_the_predecessor_goal_cannot_be_reactivated_from_the_root_plan(tmp_path):
    """Declaring an archived program active selects nothing: it has no iteration here."""
    altered = plan()
    altered["next_goal"] = {"id": "V1.4-01", "status": "active"}
    path = tmp_path / "delivery-plan.yaml"
    path.write_text(json.dumps(altered), encoding="utf-8")
    assert DeliveryPlan(path).select({}) is None


def test_runtime_state_is_kept_per_repository(monkeypatch, tmp_path):
    """A machine that ran the predecessor heartbeat must not feed its completions to Still."""
    from scripts.dev import delivery

    monkeypatch.delenv("AGENTOS_DELIVERY_STATE", raising=False)
    monkeypatch.setattr(delivery.Path, "home", classmethod(lambda cls: tmp_path))
    predecessor = delivery.default_state_path()
    predecessor.parent.mkdir(parents=True)
    predecessor.write_text(json.dumps({"completed": ["GOV-01"], "active": "V1.4-01"}), encoding="utf-8")
    controller = delivery.DeliveryController(root=ROOT)
    assert controller.state_store.path == tmp_path / "Library/Application Support/still-delivery/Jongtae--Still/state.json"
    assert controller.state_store.read() == {}
    assert json.loads(predecessor.read_text(encoding="utf-8"))["completed"] == ["GOV-01"]
