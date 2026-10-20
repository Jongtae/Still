"""The root delivery plan describes Still, not its private predecessor (#46).

Still was imported without history. The predecessor plan is archived unchanged
under docs/history/, where the historical governance tests read it; the root
plan started empty and selected nothing until the owner activated a goal
(BROWSER-REL-01, #94, since closed out). The owner then selected
QUALITY-CORE-01 (#115) and STEADY-01 (#132), both since closed out, and then CHECKS-01 (#157).
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
    assert "BROWSER-REL-01" in invariants.closed_out_programs(current)
    # Each completed child is durable history too, so a later goal may depend on it directly.
    assert {"BROWSER-REL-01", "BROWSER-INPUT-01", "BROWSER-LOADMORE-01", "BROWSER-PROFILE-01"} <= set(
        current["history"]["documented_completed_iterations"])
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


def test_the_quality_program_is_closed_out_with_its_evidence():
    """#115: both tracks merged; each substep's issue, PR and merge commit are recorded."""
    current = plan()
    assert current["next_goal"]["id"] != "QUALITY-CORE-01"
    assert {"BROWSER-REL-01", "QUALITY-CORE-01"} <= set(invariants.closed_out_programs(current))
    assert {"QUALITY-CORE-01", *TRACK_C, *TRACK_D} <= set(current["history"]["documented_completed_iterations"])
    invariants.assert_completed_work_is_unselectable(TestCase(), current)
    program = current["programs"]["QUALITY-CORE-01"]
    assert program["ordered_substeps"] == program["completed_substeps"] == TRACK_C + TRACK_D
    items = {item["id"]: item for item in current["iterations"]}
    evidence = items["QUALITY-CORE-01"]["completed_substeps"]
    assert [(evidence[name]["issue"], evidence[name]["pull_request"]) for name in TRACK_C + TRACK_D] == [
        (25, 117), (24, 124), (15, 128), (32, 131), (21, 119), (22, 139), (16, 140)]
    assert all(len(evidence[name]["merge_commit"]) == 40 for name in evidence)
    # The record says what was not done rather than implying it.
    assert "without that review" in program["closeout"]
    # #143: closing the program does not waive the owed review of the merged calendar change.
    (owed,) = program["pending_reviews"]
    assert (owed["substep"], owed["pull_request"], owed["tracking_issue"]) == ("CORE-CALENDAR-01", 139, 143)
    assert owed["merge_commit"] == evidence["CORE-CALENDAR-01"]["merge_commit"]
    assert "#143" in program["closeout"] and "not waived" in program["closeout"]
    assert "#21 slice 4" in program["closeout"] and "#16 slices 2-4" in program["closeout"]
    for state in ({}, {"completed": []}, {"active": "QUALITY-CORE-01", "status": "running"}):
        assert DeliveryPlan(PLAN).select(state) is None, state


TRACK_1 = ["STEADY-GOOGLE-01", "STEADY-LOCALE-01"]
TRACK_2 = ["STEADY-NOTES-01"]


def test_the_steady_program_is_closed_out_with_its_evidence():
    """#132: #8, #10 and #19 merged; it selected no successor."""
    current = plan()
    assert current["next_goal"]["id"] != "STEADY-01"
    assert {"BROWSER-REL-01", "QUALITY-CORE-01", "STEADY-01"} <= set(invariants.closed_out_programs(current))
    assert {"STEADY-01", *TRACK_1, *TRACK_2} <= set(current["history"]["documented_completed_iterations"])
    invariants.assert_completed_work_is_unselectable(TestCase(), current)
    program = current["programs"]["STEADY-01"]
    assert program["activated_by"]["issue"] == 132
    assert program["ordered_substeps"] == program["completed_substeps"] == TRACK_1 + TRACK_2
    items = {item["id"]: item for item in current["iterations"]}
    evidence = items["STEADY-01"]["completed_substeps"]
    assert [(evidence[name]["issue"], evidence[name]["pull_request"]) for name in TRACK_1 + TRACK_2] == [
        (8, 145), (10, 146), (19, 144)]
    assert all(len(evidence[name]["merge_commit"]) == 40 for name in evidence)
    assert "#147" in program["closeout"] and "FV_RESULT" not in program["closeout"]
    # #146/#147 merged before their full-validate finished; the exact-head runs that followed are recorded.
    assert all(run in program["closeout"] for run in ("b68acf3", "39432663328", "4b369b0", "39432668620"))
    # The owed review of the merged credential change is kept, not waived.
    (owed,) = program["pending_reviews"]
    assert (owed["substep"], owed["pull_request"], owed["tracking_issue"]) == ("STEADY-GOOGLE-01", 145, 149)
    assert owed["merge_commit"] == evidence["STEADY-GOOGLE-01"]["merge_commit"]
    assert "#149" in program["closeout"] and "not waived" in program["closeout"]
    for state in ({}, {"completed": []}, {"active": "STEADY-01", "status": "running"}):
        assert DeliveryPlan(PLAN).select(state) is None, state


CHECKS_TRACK_1 = ["CHECKS-FLAKY-01", "CHECKS-SPEED-01"]
CHECKS_TRACK_2 = ["CHECKS-MEASURE-01"]


def test_the_owner_activated_checks_program_is_goal_ready_and_heartbeat_paused():
    """#157: #142 -> #156 share the suite's timing; #16's measurement runs on its own and changes no behavior."""
    current = plan()
    assert current["next_goal"]["id"] == "CHECKS-01"
    assert invariants.assert_declared_goal_shape(TestCase(), plan=current) == "goal-ready"
    program = current["programs"]["CHECKS-01"]
    assert program["activated_by"]["issue"] == 157
    assert program["ordered_substeps"] == CHECKS_TRACK_1 + CHECKS_TRACK_2
    assert program["active_substeps"] == [] and program["completed_substeps"] == {}
    items = {item["id"]: item for item in current["iterations"]}
    assert [items[name]["issue"] for name in CHECKS_TRACK_1 + CHECKS_TRACK_2] == [142, 156, 16]
    assert items["CHECKS-SPEED-01"]["depends_on"] == ["CHECKS-FLAKY-01"]
    assert items["CHECKS-FLAKY-01"]["depends_on"] == items["CHECKS-MEASURE-01"]["depends_on"] == []
    for name in CHECKS_TRACK_1 + CHECKS_TRACK_2:
        assert items[name]["program"] == "CHECKS-01" and items[name]["milestone"] == "checks"
        (command,) = items[name]["tests"]
        targets = command.split("pytest -q ", 1)[1].split()
        assert targets and all((ROOT / target).exists() for target in targets), name
    assert "no behavior change" in items["CHECKS-MEASURE-01"]["summary"]
    assert "sleeps or retries" in program["non_goals"]
    assert "full-validate" in program["completion_rule"]
    for state in ({}, {"completed": []}):
        assert DeliveryPlan(PLAN).select(state) is None, state


def test_the_controller_can_run_the_checks_program_once_the_owner_makes_it_active(tmp_path):
    """#100: every controller path reads the selected iteration's milestone."""
    from scripts.dev.delivery import DeliveryController

    altered = plan()
    altered["next_goal"] = {"id": "CHECKS-01", "status": "active"}
    root = tmp_path / "repo"
    root.mkdir()
    (root / "delivery-plan.yaml").write_text(json.dumps(altered), encoding="utf-8")
    controller = DeliveryController(root, tmp_path / "state.json", invariants.RecordingRunner(), now=lambda: 1_700_000_000)
    status = controller.status()
    assert (status["active"], status["milestone"]) == ("CHECKS-01", "checks")


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
