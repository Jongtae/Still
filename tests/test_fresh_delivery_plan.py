"""The root delivery plan describes Still, not its private predecessor (#46).

Still was imported without history. The predecessor plan is archived unchanged
under docs/history/, where the historical governance tests read it; the root
plan starts empty and selects nothing until the owner activates a goal.
"""
import json
import subprocess
from pathlib import Path

from scripts.dev.delivery import DeliveryPlan

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "delivery-plan.yaml"
ARCHIVE = ROOT / "docs" / "history" / "predecessor-delivery-plan.yaml"


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
    assert current["iterations"] == []
    assert current["programs"] == {}
    assert current["history"]["documented_completed_iterations"] == []


def test_no_goal_is_selected_until_the_owner_activates_one():
    goal = plan()["next_goal"]
    assert goal["id"] is None
    assert goal["status"] == "not-selected"
    assert "#44" in goal["action"]
    for state in ({}, {"completed": []}, {"active": "V1.4-01", "status": "running"}):
        assert DeliveryPlan(PLAN).select(state) is None, state


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
