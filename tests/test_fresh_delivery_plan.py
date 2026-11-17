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
import re
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


def test_the_workplace_program_is_closed_out_and_selects_no_successor():
    """#206: all seven substeps merged, the last (SLACK-01) by #316; no successor, heartbeat paused."""
    current = plan()
    assert current["next_goal"]["id"] != "WORKPLACE-01"
    assert invariants.assert_declared_goal_shape(TestCase(), plan=current) in {"goal-ready", "terminal"}
    assert "WORKPLACE-01" in invariants.closed_out_programs(current)
    invariants.assert_completed_work_is_unselectable(TestCase(), current)
    program = current["programs"]["WORKPLACE-01"]
    assert program["status"] == "complete"
    assert program["completed_substeps"] == program["ordered_substeps"] == [
        "CLOUD-AI-01", "SURFACE-SEAM-01", "WORK-MODE-01", "INTRANET-DEST-01", "OWNER-MCP-WORK-01", "MATTERMOST-01",
        "SLACK-01"]
    assert program["active_substeps"] == []
    assert "independent review" not in program["closeout"]
    slack_done = items_of(current)["WORKPLACE-01"]["completed_substeps"]["SLACK-01"]
    assert (slack_done["issue"], slack_done["pull_request"]) == (214, 316)
    assert slack_done["merge_commit"].startswith("399ab29")
    assert items_of(current)["SLACK-01"]["activation_status"] == "complete-on-merge"
    mattermost_done = items_of(current)["WORKPLACE-01"]["completed_substeps"]["MATTERMOST-01"]
    assert (mattermost_done["issue"], mattermost_done["pull_request"], mattermost_done["follow_up_pull_request"]) == (213, 299, 306)
    assert mattermost_done["merge_commit"].startswith("bc3a0eb")
    assert mattermost_done["follow_up_merge_commit"].startswith("8929e4a")
    assert items_of(current)["MATTERMOST-01"]["activation_status"] == "complete-on-merge"
    # #214: the owner's two Slack decisions are recorded with the program.
    assert {"slack-action-approvals", "slack-test-workspace"} <= set(program["decisions"])
    owner_mcp_done = items_of(current)["WORKPLACE-01"]["completed_substeps"]["OWNER-MCP-WORK-01"]
    assert (owner_mcp_done["issue"], owner_mcp_done["pull_request"]) == (210, 297)
    assert owner_mcp_done["merge_commit"].startswith("d056223")
    assert items_of(current)["OWNER-MCP-WORK-01"]["activation_status"] == "complete-on-merge"
    intranet_done = items_of(current)["WORKPLACE-01"]["completed_substeps"]["INTRANET-DEST-01"]
    assert (intranet_done["issue"], intranet_done["pull_request"], intranet_done["follow_up_pull_request"]) == (209, 284, 288)
    assert intranet_done["merge_commit"].startswith("7cd87a3")
    assert intranet_done["follow_up_merge_commit"].startswith("c4f66ad")
    assert items_of(current)["INTRANET-DEST-01"]["activation_status"] == "complete-on-merge"
    work_mode = items_of(current)["WORKPLACE-01"]["completed_substeps"]["WORK-MODE-01"]
    assert (work_mode["issue"], work_mode["pull_request"], work_mode["follow_up_pull_request"]) == (208, 278, 280)
    assert work_mode["merge_commit"].startswith("db47d3c")
    assert work_mode["follow_up_merge_commit"].startswith("2bcbd65")
    assert items_of(current)["WORK-MODE-01"]["activation_status"] == "complete-on-merge"
    evidence = items_of(current)["WORKPLACE-01"]["completed_substeps"]["CLOUD-AI-01"]
    assert (evidence["issue"], evidence["pull_request"]) == (211, 230)
    assert evidence["merge_commit"].startswith("4c49a2f")
    seam = items_of(current)["WORKPLACE-01"]["completed_substeps"]["SURFACE-SEAM-01"]
    assert (seam["issue"], seam["pull_request"]) == (212, 229)
    assert seam["merge_commit"].startswith("746d2c8")
    assert items_of(current)["SURFACE-SEAM-01"]["activation_status"] == "complete-on-merge"
    # #256 review: the completed substep names the focused tests that hold its contract.
    focused = items_of(current)["CLOUD-AI-01"]["tests"][0]
    assert all(name in focused for name in ("test_cloud_engines.py", "test_decision_routes.py", "test_settings_ui.py"))
    # #256 review: the files #230 changed outside the activated list are an explicit, auditable amendment.
    amendment = program["allowed_authority_amendment"]
    assert (amendment["substep"], amendment["pull_request"]) == ("CLOUD-AI-01", 230)
    assert amendment["original"] != program["allowed_authority"]
    assert all(name in program["allowed_authority"] for name in amendment["files"])
    assert "cloud_engines.py" not in amendment["original"]
    # #208: the substep's own PR records the files it needs outside the list, beside its plan record (#264).
    work = items_of(current)["WORK-MODE-01"]["allowed_authority_amendment"]
    assert (work["issue"], work["substep"]) == (208, "WORK-MODE-01")
    assert all(name in program["allowed_authority"] and name not in work["original"] for name in work["files"])
    assert "test_work_mode.py" in items_of(current)["WORK-MODE-01"]["tests"][0]
    # #209: the same for the intranet substep's new module, browser plumbing and sandbox runner.
    intranet = items_of(current)["INTRANET-DEST-01"]["allowed_authority_amendment"]
    assert (intranet["issue"], intranet["substep"]) == (209, "INTRANET-DEST-01")
    assert all(name in program["allowed_authority"] and name not in intranet["original"] for name in intranet["files"])
    assert "test_intranet.py" in items_of(current)["INTRANET-DEST-01"]["tests"][0]
    # #210: the substep's observation harness is recorded the same way.
    owner = items_of(current)["OWNER-MCP-WORK-01"]["allowed_authority_amendment"]
    assert (owner["issue"], owner["substep"]) == (210, "OWNER-MCP-WORK-01")
    assert all(name in program["allowed_authority"] and name not in owner["original"] for name in owner["files"])
    assert "test_connect_decide.py" in items_of(current)["OWNER-MCP-WORK-01"]["tests"][0]
    # #213: the surface boundary's files outside the list, recorded the same way.
    surface = items_of(current)["MATTERMOST-01"]["allowed_authority_amendment"]
    assert (surface["issue"], surface["substep"]) == (213, "MATTERMOST-01")
    assert all(name in program["allowed_authority"] for name in surface["files"])
    assert "conversation_surface.py" not in surface["original"]
    assert "test_surface_boundary.py" in items_of(current)["MATTERMOST-01"]["tests"][0]
    # #214: the shared direct-message module, recorded the same way.
    slack = items_of(current)["SLACK-01"]["allowed_authority_amendment"]
    assert (slack["issue"], slack["substep"]) == (214, "SLACK-01")
    assert all(name in program["allowed_authority"] and name not in slack["original"] for name in slack["files"])
    assert "test_slack_surface.py" in items_of(current)["SLACK-01"]["tests"][0]
    assert "Codex review of the exact final head" in program["escalated_review"]
    items = {item["id"]: item for item in current["iterations"]}
    assert {items[name]["issue"] for name in program["ordered_substeps"]} == {208, 209, 210, 211, 212, 213, 214}
    assert items["OWNER-MCP-WORK-01"]["depends_on"] == ["INTRANET-DEST-01"]
    assert items["MATTERMOST-01"]["depends_on"] == items["SLACK-01"]["depends_on"] == ["SURFACE-SEAM-01"]
    for state in ({}, {"completed": []}, {"active": "WORKPLACE-01", "status": "running"}):
        assert DeliveryPlan(PLAN).select(state) is None, state


def test_the_responsive_program_is_closed_out_and_selects_no_successor():
    """#332: #309, #17 and #346 merged on their own PRs; no successor, heartbeat paused."""
    current = plan()
    assert current["next_goal"]["id"] != "RESPONSIVE-01"
    assert invariants.assert_declared_goal_shape(TestCase(), plan=current) in {"goal-ready", "terminal"}
    assert "RESPONSIVE-01" in invariants.closed_out_programs(current)
    invariants.assert_completed_work_is_unselectable(TestCase(), current)
    program = current["programs"]["RESPONSIVE-01"]
    invariants.assert_activation_record(TestCase(), "RESPONSIVE-01 activation", program["activated_by"])
    assert program["status"] == "complete"
    # #346 was added on 2026-10-26 after #309 and #17 had merged.
    assert program["completed_substeps"] == program["ordered_substeps"] == [
        "REPLY-LATENCY-01", "SIDE-ANSWER-01", "CLI-UPDATE-01"]
    assert program["active_substeps"] == []
    assert "independent review" not in program["closeout"]
    items = items_of(current)
    done = items["RESPONSIVE-01"]["completed_substeps"]
    assert {name: (done[name]["issue"], done[name]["pull_request"]) for name in done} == {
        "REPLY-LATENCY-01": (309, 333), "SIDE-ANSWER-01": (17, 342), "CLI-UPDATE-01": (346, 358)}
    assert done["CLI-UPDATE-01"]["merge_commit"].startswith("1048297")
    assert all(items[name]["activation_status"] == "complete-on-merge"
               for name in ("RESPONSIVE-01", *program["ordered_substeps"]))
    assert {"cli-update-added", "latency-measurement-dropped"} <= set(program["decisions"])
    # #309 L7 and #346 change the #616 isolation qualification, so both waited for the Codex review.
    assert "REPLY-LATENCY-01" in program["escalated_review"] and "CLI-UPDATE-01" in program["escalated_review"]
    assert "#16" in program["non_goals"] and "L6" in program["non_goals"]
    for state in ({}, {"completed": []}, {"active": "RESPONSIVE-01", "status": "running"}):
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
    assert invariants.assert_declared_goal_shape(TestCase(), plan=current) in {"goal-ready", "terminal"}


def items_of(current):
    return {item["id"]: item for item in current["iterations"]}


FRONTIER_FIRST_WAVE = ["FRONTIER-SPEC-01", "FRONTIER-EVAL-01"]
FRONTIER_RECONCILED = ["FRONTIER-PROVENANCE-01", "FRONTIER-RECOVERY-01", "FRONTIER-CONTEXT-01"]
FRONTIER_COMPLETED = FRONTIER_FIRST_WAVE + FRONTIER_RECONCILED + [
    "FRONTIER-JUDGMENT-01", "FRONTIER-TELEGRAM-01", "FRONTIER-FENCE-01", "FRONTIER-WORKCHAT-01", "FRONTIER-CONTEXT-02", "FRONTIER-EVAL-JUDGE-01", "FRONTIER-C2-DUE-01", "FRONTIER-EVAL-TOKENS-01", "FRONTIER-RECEIPT-JUDGE-01", "FRONTIER-EVAL-FALLBACK-01", "FRONTIER-JUDGMENT-TEXT-01", "FRONTIER-COMPLETION-BAR-01", "FRONTIER-UNTRUSTED-ECHO-01", "FRONTIER-PLAN-READING-01", "FRONTIER-RECALL-V3-01", "FRONTIER-UNTRUSTED-01", "FRONTIER-UNTRUSTED-PREVIEW-01", "FRONTIER-PLAN-READING-02", "FRONTIER-RECALL-TERMS-01", "FRONTIER-RECALL-FILL-01", "FRONTIER-RECALL-THREADS-01", "FRONTIER-RECALL-SAVE-TERMS-01", "FRONTIER-RECALL-TERMS-PASS-01", "FRONTIER-RECALL-REPLY-01", "FRONTIER-RECALL-REGRESSION-01", "FRONTIER-RECALL-REPLY-02"]
FRONTIER_WAVE_2 = ["FRONTIER-FENCE-01", "FRONTIER-JUDGMENT-01", "FRONTIER-TELEGRAM-01", "FRONTIER-WORKCHAT-01"]
FRONTIER_OPEN_WAVE_2 = [name for name in FRONTIER_WAVE_2 if name not in FRONTIER_COMPLETED]


def test_frontier_885_records_explicit_eval_judge_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-EVAL-JUDGE-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 885
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    assert 'tests/test_eval_judge_explicit.py' in unit['tests'][0]
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 885 and activation['status'] == 'complete-on-merge'
    assert activation['pull_request'] == '886'
    assert '#885 (FRONTIER-EVAL-JUDGE-01) is selected inside #822' in current['next_goal']['action']


def assert_frontier_wave_2_dependencies_satisfied(current):
    items = items_of(current)
    completed = set(current["history"]["documented_completed_iterations"])
    for name in FRONTIER_WAVE_2:
        missing = set(items[name]["depends_on"]) - completed
        assert not missing, f"{name} has non-completed dependencies: {sorted(missing)}"


def test_frontier_declares_completed_units_and_owner_authorized_wave_2():
    current = plan()
    program = current["programs"]["FRONTIER-01"]
    items = items_of(current)
    assert invariants.assert_declared_goal_shape(TestCase(), plan=current) == "goal-ready"
    assert current["next_goal"]["id"] == "FRONTIER-01"
    invariants.assert_activation_record(TestCase(), "FRONTIER-01 activation", program["activated_by"])
    assert program["activated_by"]["issue"] == program["issue"] == 822
    assert program["ordered_substeps"] == FRONTIER_FIRST_WAVE + FRONTIER_RECONCILED + FRONTIER_WAVE_2 + ["FRONTIER-CONTEXT-02", "FRONTIER-EVAL-JUDGE-01", "FRONTIER-C2-DUE-01", "FRONTIER-EVAL-TOKENS-01", "FRONTIER-RECEIPT-JUDGE-01", "FRONTIER-EVAL-FALLBACK-01", "FRONTIER-JUDGMENT-TEXT-01", "FRONTIER-COMPLETION-BAR-01", "FRONTIER-UNTRUSTED-ECHO-01", "FRONTIER-PLAN-READING-01", "FRONTIER-RECALL-V3-01", "FRONTIER-UNTRUSTED-01", "FRONTIER-UNTRUSTED-PREVIEW-01", "FRONTIER-PLAN-READING-02", "FRONTIER-RECALL-TERMS-01", "FRONTIER-RECALL-FILL-01", "FRONTIER-RECALL-THREADS-01", "FRONTIER-RECALL-SAVE-TERMS-01", "FRONTIER-RECALL-TERMS-PASS-01", "FRONTIER-RECALL-REPLY-01", "FRONTIER-RECALL-REGRESSION-01", "FRONTIER-RECALL-REPLY-02"]
    assert program["active_substeps"] == []
    assert program["completed_substeps"] == FRONTIER_COMPLETED
    assert program["reserved_substeps"] == []
    assert [items[name]["issue"] for name in FRONTIER_FIRST_WAVE] == [823, 824]
    assert all(items[name]["activation_status"] == "complete-on-merge" for name in FRONTIER_COMPLETED)
    completed = set(current["history"]["documented_completed_iterations"])
    assert set(FRONTIER_COMPLETED) <= completed
    assert not {"FRONTIER-01", *FRONTIER_OPEN_WAVE_2} & completed
    done = items["FRONTIER-01"]["completed_substeps"]
    assert set(done) == set(FRONTIER_COMPLETED)
    assert {name: (done[name]["issue"], done[name]["status"]) for name in FRONTIER_FIRST_WAVE} == {
        "FRONTIER-SPEC-01": (823, "complete-on-merge"), "FRONTIER-EVAL-01": (824, "complete-on-merge")}
    for name, pair in zip(FRONTIER_RECONCILED, [(825, 835), (826, 831), (827, 832)]):
        assert (items[name]["issue"], done[name]["pull_request"]) == pair
        assert done[name]["issue"] == pair[0]
        assert done[name]["status"] == "complete-on-merge"
        assert re.fullmatch(r"[0-9a-f]{40}", done[name]["merge_commit"])
        assert done[name]["decision"]
        assert "selection_rule" not in items[name]
    telegram = done["FRONTIER-TELEGRAM-01"]
    assert (telegram["issue"], telegram["status"]) == (842, "complete-on-merge")
    d1, d2 = telegram["pull_request"]
    assert d1 == 856
    assert d2 == 864 or isinstance(d2, int) and d2 > 0
    assert telegram["decision"]
    assert "FRONTIER-RECOVERY-01" in telegram["dependency_disposition"]
    assert "#831" in telegram["dependency_disposition"]
    assert "selection_rule" not in items["FRONTIER-TELEGRAM-01"]
    assert {"first-wave-units-reconciled", "m1-wave-2-selection"} <= set(program["decisions"])
    assert items["FRONTIER-PROVENANCE-01"]["dependency_issues"] == [818, 819, 820]
    assert items["FRONTIER-RECOVERY-01"]["dependency_issues"] == [821]
    assert items["FRONTIER-RECOVERY-01"]["related_pull_requests"] == [816]
    assert items["FRONTIER-CONTEXT-01"]["depends_on"] == FRONTIER_FIRST_WAVE
    judgment = items["FRONTIER-JUDGMENT-01"]
    assert judgment["depends_on"] == ["FRONTIER-SPEC-01"]
    assert "selection_rule" not in judgment
    assert "Complete on the merge of PR #854." in judgment["summary"]
    assert judgment["tests"] == [
        "PYTHONPATH=src python3 -m pytest -q tests/test_no_scenario_code.py",
        "PYTHONPATH=src python3 -m pytest -q tests/test_relayed_judgments_722.py",
    ]
    judgment_done = done["FRONTIER-JUDGMENT-01"]
    assert (judgment_done["issue"], judgment_done["status"], judgment_done["pull_request"]) == (
        722, "complete-on-merge", 854)
    assert judgment_done["merge_commit"] == "65f44aea72eb50f06dfd6f0d4860bcb0fc0586a4"
    assert "Depends on FRONTIER-SPEC-01, which is complete." in judgment_done["decision"]
    fence = items["FRONTIER-FENCE-01"]
    assert fence["depends_on"] == ["FRONTIER-RECOVERY-01"]
    assert "selection_rule" not in fence
    assert fence["summary"].endswith(" Complete on the merge of PR #872.")
    assert fence["tests"] == [
        "PYTHONPATH=src python3 -m pytest -q tests/test_recovery_matrix_826.py",
    ]
    fence_done = done["FRONTIER-FENCE-01"]
    assert (fence_done["issue"], fence_done["status"], fence_done["pull_request"]) == (
        841, "complete-on-merge", 872)
    assert "merge_commit" not in fence_done
    assert "matrix X4, X9" in fence_done["decision"]
    assert "Depends on FRONTIER-RECOVERY-01, which is complete." in fence_done["decision"]
    workchat = items["FRONTIER-WORKCHAT-01"]
    assert workchat["depends_on"] == ["FRONTIER-RECOVERY-01"]
    assert "selection_rule" not in workchat
    assert "Complete on the merge of PR #870." in workchat["summary"]
    assert workchat["tests"] == [
        "PYTHONPATH=src python3 -m pytest -q tests/test_recovery_matrix_826.py",
    ]
    workchat_done = done["FRONTIER-WORKCHAT-01"]
    assert (workchat_done["issue"], workchat_done["status"], workchat_done["pull_request"]) == (
        843, "complete-on-merge", 870)
    assert "merge_commit" not in workchat_done
    assert "depends on FRONTIER-RECOVERY-01, which is complete." in workchat_done["decision"]


def test_frontier_historical_programs_and_completed_units_stay_unselectable(tmp_path):
    current = plan()
    closed = set(invariants.closed_out_programs(current))
    assert set(current["programs"]) - {"FRONTIER-01"} <= closed | invariants.LEGACY_CLOSED_PROGRAMS
    invariants.assert_completed_work_is_unselectable(TestCase(), current)
    path = tmp_path / "delivery-plan.yaml"
    completed = current["history"]["documented_completed_iterations"]
    for name in sorted(closed | (set(current["programs"]) & invariants.LEGACY_CLOSED_PROGRAMS)) + FRONTIER_COMPLETED:
        altered = copy.deepcopy(current)
        altered["next_goal"] = {"id": name, "status": "active"}
        path.write_text(json.dumps(altered), encoding="utf-8")
        for state in ({}, {"completed": completed}):
            assert DeliveryPlan(path).select(state) is None, (name, state)


def test_frontier_wave_2_has_disjoint_ownership_and_satisfied_dependencies():
    current = plan()
    program = current["programs"]["FRONTIER-01"]
    items = items_of(current)
    assert program["parallel_eligible_substeps"] == FRONTIER_OPEN_WAVE_2
    authorization = program["parallel_authorization"]
    assert authorization["issue"] == 822
    assert authorization["date"] == "2026-11-14"
    assert authorization["substeps"] == FRONTIER_OPEN_WAVE_2
    assert authorization["decision"]
    wave_1, = [record for record in authorization["history"] if record["wave"] == 1]
    assert wave_1["substeps"] == FRONTIER_FIRST_WAVE
    assert not set(program["parallel_eligible_substeps"]) & set(program["active_substeps"])
    assert [items[name]["issue"] for name in FRONTIER_WAVE_2] == [841, 722, 842, 843]
    plan_files = set(program["delegation"]["FRONTIER-PLAN-M1"]["owned_files"])
    assert plan_files
    owned = set()
    for name in FRONTIER_OPEN_WAVE_2:
        assert items[name]["program"] == "FRONTIER-01"
        assert items[name]["activation_status"] == "parent-controlled"
        assert name in program["ordered_substeps"]
        assert name not in program["completed_substeps"]
        assert name not in items["FRONTIER-01"]["completed_substeps"]
        assert name not in current["history"]["documented_completed_iterations"]
        assert items[name]["depends_on"] == ["FRONTIER-RECOVERY-01"]
        delegation = program["delegation"][name]
        assert delegation["issue"] == items[name]["issue"]
        functions = delegation["owned_functions"]
        assert isinstance(functions, list) and functions
        assert all(isinstance(function, str) and function for function in functions)
        assert owned.isdisjoint(functions)
        assert all(file not in function for file in plan_files for function in functions)
        assert plan_files.isdisjoint(delegation.get("owned_files", []))
        assert isinstance(delegation["shared_files"], list)
        assert plan_files.isdisjoint(delegation["shared_files"])
        assert delegation["shared_rule"]
        owned.update(functions)
    assert items["FRONTIER-FENCE-01"]["dependency_issues"] == [818, 819, 821]
    assert_frontier_wave_2_dependencies_satisfied(current)


def test_frontier_wave_2_rejects_non_completed_dependencies():
    current = plan()
    assert_frontier_wave_2_dependencies_satisfied(current)
    for name in FRONTIER_WAVE_2:
        altered = copy.deepcopy(current)
        items_of(altered)[name]["depends_on"] = ["FRONTIER-01"]
        with TestCase().assertRaisesRegex(AssertionError, "non-completed dependencies"):
            assert_frontier_wave_2_dependencies_satisfied(altered)


def test_frontier_wave_2_cannot_declare_two_substeps_in_flight():
    altered = copy.deepcopy(plan())
    altered["programs"]["FRONTIER-01"]["active_substeps"] = FRONTIER_WAVE_2[:2]
    with TestCase().assertRaisesRegex(AssertionError, "more than one substep in flight"):
        invariants.assert_declared_goal_shape(TestCase(), plan=altered)


def test_frontier_parent_selection_never_self_activates_children_or_successors(tmp_path):
    current = plan()
    path = tmp_path / "delivery-plan.yaml"
    current["next_goal"]["status"] = "active"
    path.write_text(json.dumps(current), encoding="utf-8")
    delivery = DeliveryPlan(path)
    assert delivery.select({})["id"] == "FRONTIER-01"
    assert delivery.select({"completed": ["FRONTIER-01"]}) is None
    # Even all nine units in runtime completion state select only the parent.
    all_substeps = FRONTIER_COMPLETED + FRONTIER_OPEN_WAVE_2
    assert delivery.select({"completed": all_substeps})["id"] == "FRONTIER-01"
    for name in all_substeps:
        altered = copy.deepcopy(current)
        altered["next_goal"] = {"id": name, "status": "active"}
        path.write_text(json.dumps(altered), encoding="utf-8")
        for state in ({}, {"completed": FRONTIER_COMPLETED}, {"completed": all_substeps}):
            assert DeliveryPlan(path).select(state) is None, (name, state)


def test_frontier_883_has_unique_successor_and_preserves_827_history():
    current=plan()
    ids=[item['id'] for item in current['iterations']]
    assert len(ids)==len(set(ids))
    items=items_of(current)
    assert items['FRONTIER-CONTEXT-01']['issue']==827
    assert items['FRONTIER-CONTEXT-01']['activation_status']=='complete-on-merge'
    successor=items['FRONTIER-CONTEXT-02']
    assert successor['issue']==883
    assert successor['depends_on']==['FRONTIER-CONTEXT-01']
    assert successor['activation_status']=='complete-on-merge'
    assert successor['summary'].endswith('Complete on the merge of PR #884')
    assert successor['tests'][0].startswith('PYTHONPATH=src python3 -m pytest -q ')
    assert 'tests/test_connect_preferences.py' in successor['tests'][0]
    assert 'tests/test_prompt_files.py' in successor['tests'][0]
    assert 'FRONTIER-CONTEXT-02' in current['history']['documented_completed_iterations']
    assert 'FRONTIER-CONTEXT-02' in current['programs']['FRONTIER-01']['completed_substeps']
    record=items['FRONTIER-01']['completed_substeps']['FRONTIER-CONTEXT-02']
    assert record['issue']==883 and record['status']=='complete-on-merge'
    assert record['pull_request']=='884'
    assert 'No other successor is selected' in current['next_goal']['action']
    assert 'FRONTIER-CONTEXT-02' in current['programs']['FRONTIER-01']['ordered_substeps']
    assert current['programs']['FRONTIER-01']['active_substeps']==[]
    assert '#883' in current['next_goal']['action'] and '2026-11-15' in current['next_goal']['action']


def test_frontier_887_records_eval_only_come_due_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-C2-DUE-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 887
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    assert 'tests/test_eval_come_due.py' in unit['tests'][0]
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 887 and activation['status'] == 'complete-on-merge'
    assert activation['pull_request'] == '889'
    assert '#887 (FRONTIER-C2-DUE-01) is selected inside #822' in current['next_goal']['action']


def test_frontier_896_897_records_eval_tokens_and_infra_pairing_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-EVAL-TOKENS-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 896
    assert unit['related_issues'] == [897]
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    assert unit['tests'] == [
        'PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_eval_judge_explicit.py '
        'tests/test_continuity_report.py tests/test_judged_continuity.py tests/test_fresh_delivery_plan.py']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 896 and activation['related_issues'] == [897]
    assert activation['status'] == 'complete-on-merge' and activation['pull_request'] == '899'
    assert unit['summary'].endswith('Complete on the merge of its own PR (#899).')
    assert ('#896 and #897 (FRONTIER-EVAL-TOKENS-01) are selected inside #822 by owner direction '
            'on 2026-11-15 and are complete on the merge of its own PR.') in current['next_goal']['action']


def test_frontier_898_records_receipt_judgment_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-RECEIPT-JUDGE-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 898
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    assert 'tests/test_receipt_reply_provenance_898.py' in unit['tests'][0]
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 898 and activation['status'] == 'complete-on-merge'
    assert activation['pull_request'] == '900'
    assert ('#898 (FRONTIER-RECEIPT-JUDGE-01) is selected inside #822 by owner direction on 2026-11-15 '
            'and is complete on the merge of its own PR.') in current['next_goal']['action']


def test_frontier_902_records_eval_fallback_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-EVAL-FALLBACK-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 902
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    assert unit['tests'] == [
        'PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_eval_fallback.py '
        'tests/test_continuity_report.py tests/test_judged_continuity.py tests/test_eval_loop.py '
        'tests/test_eval_come_due.py tests/test_eval_judge_explicit.py tests/test_fresh_delivery_plan.py tests/test_p1_report.py']
    program = current['programs']['FRONTIER-01']
    assert name in program['ordered_substeps']
    assert name in program['completed_substeps']
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 902 and activation['status'] == 'complete-on-merge'
    assert activation['pull_request'] == '903'
    assert unit['summary'].endswith('Complete on the merge of its own PR.')
    assert ('#902 (FRONTIER-EVAL-FALLBACK-01) is selected inside #822 by owner direction on 2026-11-15 '
            'and is complete on the merge of its own PR.') in current['next_goal']['action']


def test_frontier_202_records_judgment_text_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-JUDGMENT-TEXT-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 202
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    assert name in program['ordered_substeps']
    assert name in program['completed_substeps']
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 202 and activation['status'] == 'complete-on-merge'
    assert activation['pull_request'] == '912'
    assert unit['summary'].endswith('Complete on the merge of its own PR.')
    assert ('#202 phase 2 (FRONTIER-JUDGMENT-TEXT-01) is selected inside #822 by owner direction on 2026-11-16 '
            'and is complete on the merge of its own PR.') in current['next_goal']['action']


def test_frontier_202_records_completion_bar_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-COMPLETION-BAR-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 202
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    assert name in program['ordered_substeps']
    assert name in program['completed_substeps']
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 202 and activation['status'] == 'complete-on-merge'
    assert activation['pull_request'] == '914'
    assert unit['summary'].endswith('Complete on the merge of its own PR.')
    assert ('#202 phase 2 (FRONTIER-COMPLETION-BAR-01) is selected inside #822 by owner direction on 2026-11-16 '
            'and is complete on the merge of its own PR.') in current['next_goal']['action']


def test_frontier_744_records_untrusted_echo_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-UNTRUSTED-ECHO-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 744
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    assert unit['tests'] == ['PYTHONPATH=src python -m pytest tests/test_744_owner_acts.py tests/test_api_requests.py tests/test_mattermost_surface.py tests/test_slack_surface.py tests/test_conversation_surface.py tests/test_surface_boundary.py tests/test_telegram_conversation_channel.py tests/test_telegram_native_presence.py tests/test_telegram_delivery_842.py tests/test_telegram_silent_failures.py tests/test_telegram_locale.py tests/test_owner_acts.py tests/test_fresh_delivery_plan.py -q -p no:cacheprovider']
    program = current['programs']['FRONTIER-01']
    for ordered in (program['ordered_substeps'], program['completed_substeps'],
                    current['history']['documented_completed_iterations']):
        assert name in ordered
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 744 and activation['status'] == 'complete-on-merge'
    assert unit['summary'].startswith('Owner-selected #744 phase 2 (PR-2 (items 3, 4d)) within #822, owner disposition 2026-11-16:')
    assert unit['summary'].endswith('Complete on the merge of its own PR.')
    assert ('#744 phase 2 PR-2 (FRONTIER-UNTRUSTED-ECHO-01; items 3, 4d) is selected inside #822 '
            'by owner disposition on 2026-11-16 and is complete on the merge of its own PR.') in current['next_goal']['action']


def test_frontier_202_records_plan_reading_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-PLAN-READING-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 202
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    assert name in program['ordered_substeps']
    assert name in program['completed_substeps']
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 202 and activation['status'] == 'complete-on-merge'
    assert activation['pull_request'] == '915'
    assert unit['summary'].endswith('Complete on the merge of its own PR.')
    assert ('#202 phase 2 (FRONTIER-PLAN-READING-01) is selected inside #822 by owner direction on 2026-11-16 '
            'and is complete on the merge of its own PR.') in current['next_goal']['action']


def test_frontier_744_records_untrusted_scope():
    current = plan()
    items = items_of(current)
    name = "FRONTIER-UNTRUSTED-01"
    unit = items[name]
    assert unit["program"] == "FRONTIER-01" and unit["issue"] == 744
    assert unit["milestone"] == "frontier-delivery" and unit["kind"] == "implementation"
    assert unit["activation_status"] == "complete-on-merge"
    assert unit["contract"] == "frontier-delivery.en.md" and unit["depends_on"] == []
    assert unit["tests"] == ['PYTHONPATH=src python -m pytest tests/test_744_effects.py tests/test_744_memory.py tests/test_744_mark.py tests/test_744_costs.py tests/test_744_owner_words.py tests/test_744_resume.py tests/test_744_recall.py tests/test_744_intake.py tests/test_744_native_search.py tests/test_744_event_isolation.py tests/test_fresh_delivery_plan.py -q -p no:cacheprovider']
    program = current["programs"]["FRONTIER-01"]
    assert name in program["ordered_substeps"]
    assert name in program["completed_substeps"]
    assert name in current["history"]["documented_completed_iterations"]
    activation = items["FRONTIER-01"]["completed_substeps"][name]
    assert activation["issue"] == 744 and activation["status"] == "complete-on-merge"
    assert unit["summary"].startswith("Owner-selected #744 phase 2 (PR-1 (items 5, 1, 2)) within #822, owner disposition 2026-11-16:")
    assert "matching-call-start barrier, not launch marking" in unit["summary"]
    assert "issuecomment-6931823807" in unit["summary"]
    assert unit["summary"].endswith("Complete on the merge of its own PR.")
    assert '#744 (FRONTIER-UNTRUSTED-01, phase 2 PR-1 items 5, 1, 2) is selected inside #822 by owner disposition on 2026-11-16 and is complete on the merge of its own PR.' in current["next_goal"]["action"]


def test_frontier_744_records_approval_preview_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-UNTRUSTED-PREVIEW-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 744
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    assert unit['tests'] == ['PYTHONPATH=src python -m pytest tests/test_744_previews.py tests/test_browser_session.py tests/test_browser_session_context.py tests/test_browser_limits.py tests/test_browser_step_observation.py tests/test_browser_timeout_recovery.py tests/test_browser_layout.py tests/test_memory_service.py tests/test_memory_search.py tests/test_memory_continuity.py tests/test_pa1_memory_candidate_owner_path.py tests/test_web_item_deep_links.py tests/test_a2a_peers.py tests/test_action_bound_approvals_619.py tests/test_payment_autonomy_662.py tests/test_self_set_writes_717.py tests/test_page_dialogs.py tests/test_messages.py tests/test_browser_settings_ui.py tests/test_no_scenario_code.py tests/test_fresh_delivery_plan.py -q -p no:cacheprovider']
    program = current['programs']['FRONTIER-01']
    for steps in (program['ordered_substeps'], program['completed_substeps'],
                  current['history']['documented_completed_iterations']):
        assert steps[steps.index('FRONTIER-UNTRUSTED-01') + 1] == name
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 744 and activation['status'] == 'complete-on-merge'
    assert unit['summary'].startswith('Owner-selected #744 phase 2 (PR-3 (items 4a, 4b)) within #822, owner disposition 2026-11-16:')
    assert unit['summary'].endswith('Complete on the merge of its own PR.')
    assert '#744 phase 2 (PR-3 (items 4a, 4b), FRONTIER-UNTRUSTED-PREVIEW-01) is selected inside #822 by owner disposition on 2026-11-16 and is complete on the merge of its own PR.' in current['next_goal']['action']


def test_frontier_202_records_direct_plan_reading():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-PLAN-READING-02'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 202
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    assert name in program['ordered_substeps']
    assert name in program['completed_substeps']
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 202 and activation['status'] == 'complete-on-merge'
    assert activation['pull_request'] == '923'
    assert unit['summary'].endswith('Complete on the merge of its own PR.')
    assert ('#202 phase 2 (FRONTIER-PLAN-READING-02) is selected inside #822 by owner direction on 2026-11-16 '
            'and is complete on the merge of its own PR.') in current['next_goal']['action']


def test_frontier_921_pr1_records_retrieval_terms_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-RECALL-TERMS-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 921
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    for steps in (program['ordered_substeps'], program['completed_substeps']):
        assert steps[steps.index('FRONTIER-PLAN-READING-02') + 1] == name
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 921 and activation['status'] == 'complete-on-merge'
    assert activation['decision'] == unit['summary']
    assert unit['summary'] == 'Owner-approved #921 PR-1 within #822 (owner decision 2026-11-17, #921 comment 6934192576; spec v5 #921 comment 6934099196): derived retrieval_terms/meta on Memory rows in a three-column FTS5 index (savepoint migration, bm25 weights), a guarded per-row write, forget and retract clearing terms with a non-repeating generation, and a shape/secret/number term filter that is not a relevance filter. No writer calls it yet (PR-3 and PR-6); no model call, dependency or new destination. Complete on the merge of its own PR.'
    assert unit['tests'] == ['PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_921_terms_index.py tests/test_memory_search.py tests/test_memory_service.py tests/test_owner_forget.py tests/test_forget_transcript.py tests/test_744_memory.py tests/test_conversation_recall.py tests/test_fresh_delivery_plan.py']
    assert '#921 PR-1 (FRONTIER-RECALL-TERMS-01) is selected inside #822 by owner decision on 2026-11-17 and is complete on the merge of its own PR.' in current['next_goal']['action']


def test_frontier_921_pr4_records_relevance_ordered_fill_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-RECALL-FILL-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 921
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    for steps in (program['ordered_substeps'], program['completed_substeps']):
        assert steps[steps.index('FRONTIER-RECALL-TERMS-01') + 1] == name
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 921 and activation['status'] == 'complete-on-merge'
    assert 'pull_request' not in activation
    assert activation['decision'] == unit['summary']
    assert unit['summary'] == "Owner-approved #921 PR-4 within #822 (spec v5 #921 comment 6934099196, §3): the profile snapshot fills unmatched facts newest first from the newest 500 candidates after the voice row and BM25-ordered matches, instead of key order, so a bound cut drops the oldest unmatched facts; _profile_rows keeps key order for the owner's Memory list; bounds, attribution and filters unchanged. No model call, dependency or new destination. Complete on the merge of its own PR."
    assert unit['tests'] == ['PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_921_fill_order.py tests/test_memory_service.py tests/test_memory_search.py tests/test_turn_context.py tests/test_past_words_brief_860.py tests/test_owner_profile_context_860.py tests/test_fresh_delivery_plan.py']
    assert '#921 PR-4 (FRONTIER-RECALL-FILL-01) is selected inside #822 by owner direction on 2026-11-17 and is complete on the merge of its own PR.' in current['next_goal']['action']


def test_frontier_921_pr7_records_conversation_by_work_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-RECALL-THREADS-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 921
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    for steps in (program['ordered_substeps'], program['completed_substeps']):
        assert steps[steps.index('FRONTIER-RECALL-FILL-01') + 1] == name
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 921 and activation['status'] == 'complete-on-merge'
    assert 'pull_request' not in activation
    assert activation['decision'] == unit['summary']
    assert unit['summary'] == "Owner-approved #921 PR-7 within #822 (spec v5 #921 comment 6934099196, §4): search_conversation takes an optional work_id from a search_memory source and returns that Work's owner turns and delivered reply as an addressed lookup, independent of FTS and the recent window, under the existing owner-words, forgotten and secret filters, bounds and three-exchange budget, with an explicit found/not_found/forgotten/document_work outcome; document Works stay excluded (an open C17 question for the owner). No model call, dependency or new destination. Complete on the merge of its own PR."
    assert unit['tests'] == ['PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_921_conversation_by_work.py tests/test_conversation_recall.py tests/test_forget_transcript.py tests/test_744_recall.py tests/test_information_use.py tests/test_fresh_delivery_plan.py']
    assert '#921 PR-7 (FRONTIER-RECALL-THREADS-01) is selected inside #822 by owner direction on 2026-11-17 and is complete on the merge of its own PR.' in current['next_goal']['action']


def test_frontier_921_pr6_records_save_memory_terms_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-RECALL-SAVE-TERMS-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 921
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    steps = program['ordered_substeps']
    assert steps[steps.index('FRONTIER-RECALL-THREADS-01') + 1] == 'FRONTIER-RECALL-SAVE-TERMS-01'
    steps = program['completed_substeps']
    assert steps[steps.index('FRONTIER-RECALL-THREADS-01') + 1] == 'FRONTIER-RECALL-SAVE-TERMS-01'
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 921 and activation['status'] == 'complete-on-merge'
    assert 'pull_request' not in activation
    assert activation['decision'] == unit['summary']
    assert unit['summary'] == "Owner-approved #921 PR-6 within #822 (spec v5 #921 comment 6934099196, §2 Writers; N11 = C, comment 6934192576): save_memory takes an optional terms string; on the direct-save path the terms pass memory_terms.filter_terms (secret check first) and are written in the same INSERT as the row with a keyed meta naming the worker's route and model when known; empty or filtered terms leave the meta NULL for the later terms pass; candidates carry no terms. No model call, dependency or new destination. Complete on the merge of its own PR."
    assert unit['tests'] == ['PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_921_save_memory_terms.py tests/test_744_memory.py tests/test_owner_forget.py tests/test_memory_search.py tests/test_921_terms_index.py tests/test_fresh_delivery_plan.py']
    assert '#921 PR-6 (FRONTIER-RECALL-SAVE-TERMS-01) is selected inside #822 by owner direction on 2026-11-17 and is complete on the merge of its own PR.' in current['next_goal']['action']


def test_frontier_921_pr3_records_terms_pass_scope():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-RECALL-TERMS-PASS-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 921
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    steps = program['ordered_substeps']
    assert steps[steps.index('FRONTIER-RECALL-SAVE-TERMS-01') + 1] == 'FRONTIER-RECALL-TERMS-PASS-01'
    steps = program['completed_substeps']
    assert steps[steps.index('FRONTIER-RECALL-SAVE-TERMS-01') + 1] == 'FRONTIER-RECALL-TERMS-PASS-01'
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 921 and activation['status'] == 'complete-on-merge'
    assert 'pull_request' not in activation
    assert activation['decision'] == unit['summary']
    assert unit['summary'] == "Owner-approved #921 PR-3 within #822 (spec v5 #921 comment 6934099196, §2 Writers and the terms pass; N11 = C, comment 6934192576): upkeep and dream proposals carry a terms string filtered and written with the fact; a repeatable, Work-independent terms run kind keys the remaining eligible facts on the Judgment AI's active model, within a quarter of the owner's daily upkeep cap, after every other kind, never aging out and never blocking the dream; whole facts packed under size bounds with a singleton allowance and an adaptive row cap (start 8, halve the rows sent after a rejected answer, double the cap after a valid one, up to 25); host validation and guarded per-row commits; a content-free manifest and judgments_read fan-out instead of a Work event. No dependency or new destination. Complete on the merge of its own PR."
    assert unit['tests'] == ['PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_921_terms_pass.py tests/test_owner_model_upkeep.py tests/test_owner_model_dream.py tests/test_self_review.py tests/test_921_terms_index.py tests/test_fresh_delivery_plan.py']
    assert '#921 PR-3 (FRONTIER-RECALL-TERMS-PASS-01) is selected inside #822 by owner direction on 2026-11-17 and is complete on the merge of its own PR.' in current['next_goal']['action']


def test_frontier_921_records_direct_reply_gate():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-RECALL-REPLY-01'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 921
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    assert name in program['ordered_substeps']
    assert name in program['completed_substeps']
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 921 and activation['status'] == 'complete-on-merge'
    assert activation['pull_request'] == '959'
    assert unit['summary'].endswith('Complete on the merge of its own PR.')
    assert ('#921 PR-5 (FRONTIER-RECALL-REPLY-01) is selected inside #822 by owner direction on 2026-11-17 '
            'and is complete on the merge of its own PR.') in current['next_goal']['action']


def test_frontier_921_records_eval_only_regression():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-RECALL-REGRESSION-01'
    unit = items[name]
    assert unit['issue'] == 921 and unit['program'] == 'FRONTIER-01'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['depends_on'] == [] and unit['contract'] == 'frontier-delivery.en.md'
    for key in ('ordered_substeps', 'completed_substeps'):
        assert name in current['programs']['FRONTIER-01'][key]
    assert name in current['history']['documented_completed_iterations']
    assert items['FRONTIER-01']['completed_substeps'][name]['status'] == 'complete-on-merge'
    recorded = ('The one approved live run was recorded: 204 calls on the subscription route, '
                'no judge; results are on #921.')
    assert recorded in unit['summary']
    assert recorded in items['FRONTIER-01']['completed_substeps'][name]['decision']
    assert unit['summary'].endswith('Complete on the merge of its own PR.')
    assert '#921 PR-8 (FRONTIER-RECALL-REGRESSION-01) is selected' in current['next_goal']['action']


def test_frontier_921_records_gate_best_match():
    current = plan()
    items = items_of(current)
    name = 'FRONTIER-RECALL-REPLY-02'
    unit = items[name]
    assert unit['program'] == 'FRONTIER-01' and unit['issue'] == 921
    assert unit['milestone'] == 'frontier-delivery' and unit['kind'] == 'implementation'
    assert unit['activation_status'] == 'complete-on-merge'
    assert unit['contract'] == 'frontier-delivery.en.md' and unit['depends_on'] == []
    program = current['programs']['FRONTIER-01']
    assert name in program['ordered_substeps']
    assert name in program['completed_substeps']
    assert name in current['history']['documented_completed_iterations']
    activation = items['FRONTIER-01']['completed_substeps'][name]
    assert activation['issue'] == 921 and activation['status'] == 'complete-on-merge'
    assert activation['pull_request'] == '965'
    assert unit['summary'].endswith('Complete on the merge of its own PR.')
    assert ('#921 gate follow-up (FRONTIER-RECALL-REPLY-02) is selected inside #822 by owner direction on 2026-11-18 '
            'and is complete on the merge of its own PR.') in current['next_goal']['action']
