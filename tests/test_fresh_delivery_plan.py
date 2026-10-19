"""The root delivery plan describes Still, not its private predecessor (#46).

Still was imported without history. The predecessor plan is archived unchanged
under docs/history/, where the historical governance tests read it; the root
plan started empty and selected nothing until the owner activated a goal
(BROWSER-REL-01, #94, since closed out). The owner then selected
QUALITY-CORE-01 (#115).
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
    assert not inherited & set(current["history"]["documented_completed_iterations"])


def test_the_browser_program_is_closed_out_with_its_evidence():
    """#94: BROWSER-REL-01 ran #12 -> #11 -> #13 and closed out; it selected no successor."""
    current = plan()
    assert current["next_goal"]["id"] != "BROWSER-REL-01"
    assert invariants.closed_out_programs(current) == ["BROWSER-REL-01"]
    # Each completed child is durable history too, so a later goal may depend on it directly.
    assert set(current["history"]["documented_completed_iterations"]) == {
        "BROWSER-REL-01", "BROWSER-INPUT-01", "BROWSER-LOADMORE-01", "BROWSER-PROFILE-01"}
    invariants.assert_completed_work_is_unselectable(TestCase(), current)
    program = current["programs"]["BROWSER-REL-01"]
    assert program["activated_by"]["issue"] == 94
    order = ["BROWSER-INPUT-01", "BROWSER-LOADMORE-01", "BROWSER-PROFILE-01"]
    assert program["ordered_substeps"] == program["completed_substeps"] == order
    items = {item["id"]: item for item in current["iterations"]}
    evidence = items["BROWSER-REL-01"]["completed_substeps"]
    assert [(evidence[name]["issue"], evidence[name]["pull_request"]) for name in order] == [(12, 98), (11, 99), (13, 108)]
    for previous, name in zip([None] + order, order):
        assert items[name]["program"] == "BROWSER-REL-01"
        assert items[name]["depends_on"] == ([previous] if previous else [])
    for state in ({}, {"completed": []}, {"active": "BROWSER-REL-01", "status": "running"}):
        assert DeliveryPlan(PLAN).select(state) is None, state


TRACK_C = ["QUALITY-DELIVERY-01", "QUALITY-FRESH-01", "QUALITY-REPORT-01", "QUALITY-METHODS-01"]
TRACK_D = ["CORE-SERVICE-01", "CORE-CALENDAR-01", "CORE-PARALLEL-01"]


def test_the_owner_activated_quality_program_is_goal_ready_and_heartbeat_paused():
    """#115: two tracks in order (track D reordered #21 -> #22 by the owner's #22 decision); #16 waits for #32."""
    current = plan()
    assert current["next_goal"]["id"] == "QUALITY-CORE-01"
    assert invariants.assert_declared_goal_shape(TestCase(), plan=current) == "goal-ready"
    program = current["programs"]["QUALITY-CORE-01"]
    assert program["activated_by"]["issue"] == 115
    assert program["ordered_substeps"] == TRACK_C + TRACK_D
    assert program["active_substeps"] == [] and program["completed_substeps"] == {}
    items = {item["id"]: item for item in current["iterations"]}
    assert [items[name]["issue"] for name in TRACK_C + TRACK_D] == [25, 24, 15, 32, 21, 22, 16]
    for track in (TRACK_C, TRACK_D[:2]):
        for previous, name in zip([None] + track, track):
            assert items[name]["program"] == "QUALITY-CORE-01"
            assert items[name]["milestone"] == "quality-core"
            assert (previous is None) or previous in items[name]["depends_on"]
    # #16 needs #21's extraction and #32's agent_runtime.py change, not #22's human review.
    assert items["CORE-PARALLEL-01"]["depends_on"] == ["CORE-SERVICE-01", "QUALITY-METHODS-01"]
    assert items["QUALITY-DELIVERY-01"]["depends_on"] == items["CORE-SERVICE-01"]["depends_on"] == []
    # #22: the owner chose option A, which changes approval semantics, so a human non-author reviews it.
    assert "CORE-CALENDAR-01" in program["independent_review"] and program["decisions"][0]["issue"] == 22
    # validate runs only the governance list, so each substep names behavioral tests of its own surface.
    for name in TRACK_C + TRACK_D:
        (command,) = items[name]["tests"]
        assert "test_fresh_delivery_plan" not in command, name
        targets = command.split("pytest -q ", 1)[1].split()
        assert targets and all((ROOT / target).exists() for target in targets), name
    assert "full-validate" in program["completion_rule"]
    # Goal-ready is not active: the heartbeat selects nothing on its own.
    for state in ({}, {"completed": []}):
        assert DeliveryPlan(PLAN).select(state) is None, state


def test_the_controller_can_run_the_quality_program_once_the_owner_makes_it_active(tmp_path):
    """#100: every controller path reads the selected iteration's milestone."""
    from scripts.dev.delivery import DeliveryController

    altered = plan()
    altered["next_goal"] = {"id": "QUALITY-CORE-01", "status": "active"}
    root = tmp_path / "repo"
    root.mkdir()
    (root / "delivery-plan.yaml").write_text(json.dumps(altered), encoding="utf-8")
    controller = DeliveryController(root, tmp_path / "state.json", invariants.RecordingRunner(), now=lambda: 1_700_000_000)
    status = controller.status()
    assert (status["active"], status["milestone"]) == ("QUALITY-CORE-01", "quality-core")


def test_a_closed_program_stays_unselectable_even_if_declared_active(tmp_path):
    """#100: the controller reads the milestone on every path; #94: a closed program never runs again."""
    from scripts.dev.delivery import DeliveryController

    altered = plan()
    altered["next_goal"] = {"id": "BROWSER-REL-01", "status": "active"}
    root = tmp_path / "repo"
    root.mkdir()
    (root / "delivery-plan.yaml").write_text(json.dumps(altered), encoding="utf-8")
    runner = invariants.RecordingRunner()
    controller = DeliveryController(root, tmp_path / "state.json", runner, now=lambda: 1_700_000_000)
    assert controller.status()["active"] is None
    assert controller.plan.milestone_title(controller.plan.items["BROWSER-REL-01"]) == "Browser reliability (BROWSER-REL-01)"
    assert runner.calls == []


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
