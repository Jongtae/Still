"""#207: the codex-review status is green only for the exact head, with every finding answered."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("codex_review_status", ROOT / "scripts/ci/codex_review_status.py")
crs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crs)

HEAD = "a" * 40
CODEX = {"login": "chatgpt-codex-connector[bot]"}
OWNER = {"login": "Jongtae"}


def summary(sha=HEAD, status="completed", findings=False, user=CODEX):
    state = {"code_review": {"commit_sha": sha, "has_any_findings": findings,
                             "has_unresolved_findings": findings, "status": status}}
    return {"user": user, "body": f"<!-- codex-pull-request-review-summary -->\n<!-- codex-review-state:v1 {json.dumps(state)} -->\nsummary"}


def finding(cid):
    return {"id": cid, "user": CODEX, "body": "**P2** something\n<!-- codex-code-review-finding:v1 -->"}


def reply(to, user=OWNER):
    return {"id": to + 1000, "user": user, "in_reply_to_id": to, "body": "Fixed in abc"}


def test_clean_review_of_the_exact_head_is_green():
    assert crs.decide(HEAD, [summary()], []) == ("success", f"Codex reviewed {HEAD[:7]}; no findings")


def test_a_review_of_an_older_head_stays_pending():
    state, text = crs.decide(HEAD, [summary(sha="b" * 40)], [])
    assert state == "pending" and "Waiting for Codex" in text


def test_no_summary_or_a_running_review_stays_pending():
    assert crs.decide(HEAD, [], [])[0] == "pending"
    state, text = crs.decide(HEAD, [summary(status="running")], [])
    assert state == "pending" and "reviewing" in text


def test_findings_need_a_reply_before_green():
    comments = [finding(1), finding(2), reply(1)]
    state, text = crs.decide(HEAD, [summary(findings=True)], comments)
    assert state == "pending" and "1 finding(s) await a reply" in text
    state, _ = crs.decide(HEAD, [summary(findings=True)], comments + [reply(2)])
    assert state == "success"


def test_codex_replying_to_itself_does_not_answer_a_finding():
    state, _ = crs.decide(HEAD, [summary(findings=True)], [finding(1), reply(1, user=CODEX)])
    assert state == "pending"


def test_only_codex_can_post_the_review_state():
    """A copy of the marker in someone else's comment is ignored."""
    assert crs.decide(HEAD, [summary(user=OWNER)], [])[0] == "pending"
    assert crs.decide(HEAD, [summary(), summary(sha="b" * 40, user=OWNER)], [])[0] == "success"


def test_the_latest_codex_summary_wins():
    assert crs.decide(HEAD, [summary(), summary(sha="b" * 40)], [])[0] == "pending"


def test_the_workflow_runs_the_script_from_main_with_status_permission_only():
    workflow = (ROOT / ".github/workflows/codex-review-status.yml").read_text(encoding="utf-8")
    assert "ref: main" in workflow
    assert "statuses: write" in workflow
    assert "contents: write" not in workflow and "pull-requests: write" not in workflow
    assert "contains(github.event.comment.body, 'codex-review-state')" in workflow
