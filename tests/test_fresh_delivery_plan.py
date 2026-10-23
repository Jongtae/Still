"""The root delivery plan describes Still, not its private predecessor (#46).

Still was imported without history. The predecessor plan is archived unchanged
under docs/history/, where the historical governance tests read it; the root
plan started empty and selected nothing until the owner activated a goal
(BROWSER-REL-01, #94, since closed out). The owner then selected
QUALITY-CORE-01 (#115), STEADY-01 (#132) and CHECKS-01 (#157), all since closed out.
"""
import importlib.util
import copy
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


def test_the_checks_program_is_closed_out_with_its_evidence():
    """#157: #142, #156 and the #16 measurement merged; it selected no successor."""
    current = plan()
    assert current["next_goal"]["id"] != "CHECKS-01"
    assert {"BROWSER-REL-01", "QUALITY-CORE-01", "STEADY-01", "CHECKS-01"} <= set(invariants.closed_out_programs(current))
    invariants.assert_completed_work_is_unselectable(TestCase(), current)
    program = current["programs"]["CHECKS-01"]
    assert program["ordered_substeps"] == program["completed_substeps"] == CHECKS_TRACK_1 + CHECKS_TRACK_2
    items = {item["id"]: item for item in current["iterations"]}
    evidence = items["CHECKS-01"]["completed_substeps"]
    assert [(evidence[name]["issue"], evidence[name]["pull_request"]) for name in CHECKS_TRACK_1 + CHECKS_TRACK_2] == [
        (142, 163), (156, 165), (16, 162)]
    assert all(len(evidence[name]["merge_commit"]) == 40 for name in evidence)
    assert "#164" in program["closeout"] and "owner decision on #16" in program["closeout"]
    for state in ({}, {"completed": []}, {"active": "CHECKS-01", "status": "running"}):
        assert DeliveryPlan(PLAN).select(state) is None, state


def test_the_owed_reviews_are_resolved_by_the_owner_check_not_claimed_as_independent():
    """#143, #149: the owner checked the merged behaviour; the record does not call it an independent review."""
    current = plan()
    for program, issue in (("QUALITY-CORE-01", 143), ("STEADY-01", 149)):
        (owed,) = current["programs"][program]["pending_reviews"]
        assert owed["tracking_issue"] == issue
        assert owed["resolution"]["date"] == "2026-10-21"
        assert owed["resolution"]["independent_review"] == "not performed by a non-author"


def test_the_browse_audit_program_is_closed_out_with_its_evidence():
    """#168: #167 merged; #164 was measured and needed no change, which is recorded, not hidden."""
    current = plan()
    assert current["next_goal"]["id"] != "BROWSE-AUDIT-01"
    assert "BROWSE-AUDIT-01" in invariants.closed_out_programs(current)
    invariants.assert_completed_work_is_unselectable(TestCase(), current)
    program = current["programs"]["BROWSE-AUDIT-01"]
    assert program["ordered_substeps"] == ["BROWSE-CLOSE-01", "AUDIT-OUTBOUND-01"]
    assert program["completed_substeps"] == ["AUDIT-OUTBOUND-01"]
    no_change = program["unexecuted_substeps"]["BROWSE-CLOSE-01"]
    assert (no_change["disposition"], no_change["evidence_issue"]) == ("no-change-needed", 164)
    assert "successor_issue" not in no_change
    assert "cProfile" in program["unexecuted_substeps"]["BROWSE-CLOSE-01"]["reason"]
    # The completion rule admits the measured no-change outcome, and the amendment says why.
    assert "no-change-needed" in program["completion_rule"]
    assert program["completion_rule_amendment"]["issue"] == 168
    assert "merged on its own PR" in program["completion_rule_amendment"]["original"]
    rule_without = copy.deepcopy(current)
    rule_without["programs"]["BROWSE-AUDIT-01"]["completion_rule"] = program["completion_rule_amendment"]["original"]
    try:
        invariants.assert_closed_out_record(TestCase(), rule_without, "BROWSE-AUDIT-01")
        contradicts = False
    except AssertionError:
        contradicts = True
    assert contradicts
    items = {item["id"]: item for item in current["iterations"]}
    evidence = items["BROWSE-AUDIT-01"]["completed_substeps"]
    assert (evidence["AUDIT-OUTBOUND-01"]["issue"], evidence["AUDIT-OUTBOUND-01"]["pull_request"]) == (167, 178)
    assert items["BROWSE-CLOSE-01"]["activation_status"] == "not-executed"
    assert "BROWSE-CLOSE-01" not in current["history"]["documented_completed_iterations"]
    for state in ({}, {"completed": []}, {"active": "BROWSE-AUDIT-01", "status": "running"}):
        assert DeliveryPlan(PLAN).select(state) is None, state


def test_an_unexecuted_substep_needs_a_successor_or_a_no_change_disposition_with_evidence():
    """#168 review: a no-work disposition is its own kind, not a successor pointing back at a closed issue."""
    import copy
    base = plan()

    def closeout_fails(record):
        altered = copy.deepcopy(base)
        altered["programs"]["BROWSE-AUDIT-01"]["unexecuted_substeps"]["BROWSE-CLOSE-01"] = record
        try:
            invariants.assert_closed_out_record(TestCase(), altered, "BROWSE-AUDIT-01")
        except AssertionError:
            return True
        return False

    reason = base["programs"]["BROWSE-AUDIT-01"]["unexecuted_substeps"]["BROWSE-CLOSE-01"]["reason"]
    assert not closeout_fails({"disposition": "no-change-needed", "evidence_issue": 164, "reason": reason})
    # A successor record accounts only for a declared split child, never for an
    # ordered substep on its own: unfinished work cannot close the program.
    assert closeout_fails({"successor_issue": 999, "reason": reason})
    assert closeout_fails({"disposition": "no-change-needed", "reason": reason})
    assert closeout_fails({"disposition": "no-change-needed", "evidence_issue": 164, "successor_issue": 164, "reason": reason})
    assert closeout_fails({"disposition": "something-else", "successor_issue": 999, "reason": reason})
    assert closeout_fails({"reason": reason})
    # A no-change record must name an activated substep, so renaming it cannot hide one.
    renamed = copy.deepcopy(base)
    program = renamed["programs"]["BROWSE-AUDIT-01"]
    program["unexecuted_substeps"] = {"SOMETHING-ELSE": program["unexecuted_substeps"]["BROWSE-CLOSE-01"]}
    try:
        invariants.assert_closed_out_record(TestCase(), renamed, "BROWSE-AUDIT-01")
        hidden = False
    except AssertionError:
        hidden = True
    assert hidden
    for bad in (False, True, 0, -164, "164"):
        assert closeout_fails({"disposition": "no-change-needed", "evidence_issue": bad, "reason": reason}), bad


def test_the_current_plan_holds_the_shared_invariants_whatever_record_is_newest():
    """#231: every plan record passes the shared checks, not only those its own test calls."""
    current = plan()
    assert invariants.assert_declared_goal_shape(TestCase(), plan=current) in {"goal-ready", "terminal"}
    invariants.assert_completed_work_is_unselectable(TestCase(), current)


def test_a_program_with_two_substeps_in_flight_fails_the_shared_check():
    """#231: #218's first head declared two WORKPLACE-01 substeps active and still passed CI."""
    altered = plan()
    program = next(program for program in altered["programs"].values()
                   if len(program.get("ordered_substeps", [])) >= 2)
    program["active_substeps"] = program["ordered_substeps"][:2]
    with TestCase().assertRaisesRegex(AssertionError, "more than one substep in flight"):
        invariants.assert_declared_goal_shape(TestCase(), plan=altered)


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


def test_the_in_context_program_is_closed_out_and_the_owed_reviews_stay_owed():
    """#175: all five substeps merged; #179 and #186 merged before their human non-author review, which stays owed on #188."""
    current = plan()
    assert current["next_goal"]["id"] != "IN-CONTEXT-01"
    assert "IN-CONTEXT-01" in invariants.closed_out_programs(current)
    invariants.assert_completed_work_is_unselectable(TestCase(), current)
    program = current["programs"]["IN-CONTEXT-01"]
    assert program["completed_substeps"] == program["ordered_substeps"] == [
        "JUDGE-CONTEXT-01", "GUARD-NL-01", "OWNER-ACT-01", "RULES-OUT-01", "BROWSER-DIALOG-01"]
    owed = {review["substep"]: review for review in program["pending_reviews"]}
    assert set(owed) == {"OWNER-ACT-01", "BROWSER-DIALOG-01"}
    assert {review["tracking_issue"] for review in owed.values()} == {188}
    assert all("not waived" in review["reason"] for review in owed.values())
    assert "independent review" not in program["closeout"].replace("non-author review", "")
    items = {item["id"]: item for item in current["iterations"]}
    evidence = items["IN-CONTEXT-01"]["completed_substeps"]
    assert [slice_["pull_request"] for slice_ in evidence["RULES-OUT-01"]["slices"]] == [181, 185, 187]
    assert (evidence["OWNER-ACT-01"]["pull_request"], evidence["BROWSER-DIALOG-01"]["pull_request"]) == (179, 186)
    for state in ({}, {"completed": []}, {"active": "IN-CONTEXT-01", "status": "running"}):
        assert DeliveryPlan(PLAN).select(state) is None, state


def test_the_owner_activated_workplace_program_is_goal_ready_and_heartbeat_paused():
    """#206: selected by the owner on 2026-10-23; cloud AI first, then the surface refactor."""
    current = plan()
    assert current["next_goal"]["id"] == "WORKPLACE-01"
    assert current["next_goal"]["status"] == "owner-activated-goal-ready"
    assert "heartbeat stays paused" in current["next_goal"]["action"]
    assert invariants.assert_declared_goal_shape(TestCase(), plan=current) == "goal-ready"
    program = current["programs"]["WORKPLACE-01"]
    assert program["ordered_substeps"] == ["CLOUD-AI-01", "SURFACE-SEAM-01", "WORK-MODE-01", "INTRANET-DEST-01",
                                           "OWNER-MCP-WORK-01", "MATTERMOST-01", "SLACK-01"]
    # One substep in flight at a time (#211 and #212 both edit quickstart_service.py).
    assert program["active_substeps"] == ["CLOUD-AI-01"]
    assert invariants.assert_active_substeps_are_legitimate(TestCase(), current, "WORKPLACE-01") == "CLOUD-AI-01"
    assert program["completed_substeps"] == []
    assert "Codex review of the exact final head" in program["escalated_review"]
    items = {item["id"]: item for item in current["iterations"]}
    assert {items[name]["issue"] for name in program["ordered_substeps"]} == {208, 209, 210, 211, 212, 213, 214}
    assert items["OWNER-MCP-WORK-01"]["depends_on"] == ["INTRANET-DEST-01"]
    assert items["MATTERMOST-01"]["depends_on"] == items["SLACK-01"]["depends_on"] == ["SURFACE-SEAM-01"]
    # Session-driven: the heartbeat never selects it on its own.
    for state in ({}, {"completed": []}):
        assert DeliveryPlan(PLAN).select(state) is None, state


def test_the_post_closeout_audit_notes_correct_without_rewriting_the_closeouts():
    """#250: the 2026-10-23 audit adds dated corrections beside each closeout (C12), never in place of it."""
    current = plan()
    archived = json.loads(subprocess.check_output(
        ["git", "show", "06fefcd:delivery-plan.yaml"], cwd=ROOT, text=True))
    expected = {"QUALITY-CORE-01": {233, 236, 248}, "STEADY-01": {233, 235, 237, 238, 246},
                "BROWSE-AUDIT-01": {233, 239, 245}, "IN-CONTEXT-01": {240, 247}}
    for name, follow_ups in expected.items():
        program = current["programs"][name]
        note = program["post_closeout_audit"]
        assert note["date"] == "2026-10-23" and note["issue"] == 250
        assert note["corrections"] and all(isinstance(line, str) and line for line in note["corrections"])
        assert follow_ups <= set(note["follow_ups"]), name
        # The original closeout and its owed reviews stay exactly as recorded.
        assert program["closeout"] == archived["programs"][name]["closeout"], name
        assert program.get("pending_reviews") == archived["programs"][name].get("pending_reviews"), name
    assert invariants.assert_declared_goal_shape(TestCase(), plan=current) == "goal-ready"
