#!/usr/bin/env python3
"""Post a ``codex-review`` commit status on a PR head (#207).

Green only when Codex's own ``codex-review-state`` summary names the exact head
as reviewed and every Codex finding on the PR has a reply. The status is
informational: it never approves or merges anything.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Any

CONTEXT = "codex-review"
STATE_MARK = "codex-review-state:v1"
FINDING_MARK = "codex-code-review-finding"
CODEX_LOGIN = "chatgpt-codex-connector[bot]"
_STATE = re.compile(r"codex-review-state:v1\s*(\{.*?\})\s*-->", re.S)


def _is_codex(user: dict[str, Any] | None) -> bool:
    # Exact login and Bot type: a "[bot]" login can only belong to a GitHub App.
    user = user or {}
    return user.get("login") == CODEX_LOGIN and user.get("type") == "Bot"


def review_state(issue_comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The code-review part of Codex's latest summary comment, if any."""
    for comment in reversed(issue_comments):
        body = comment.get("body") or ""
        if STATE_MARK not in body or not _is_codex(comment.get("user")):
            continue
        match = _STATE.search(body)
        if not match:
            continue
        try:
            state = json.loads(match.group(1))
        except ValueError:
            continue
        code = state.get("code_review")
        return code if isinstance(code, dict) else None
    return None


def unanswered(review_comments: list[dict[str, Any]]) -> list[int]:
    """Codex findings with no reply from anyone but Codex."""
    replied = {c.get("in_reply_to_id") for c in review_comments
               if c.get("in_reply_to_id") and not _is_codex(c.get("user"))}
    return [c["id"] for c in review_comments
            if _is_codex(c.get("user")) and not c.get("in_reply_to_id")
            and FINDING_MARK in (c.get("body") or "") and c["id"] not in replied]


def decide(head: str, issue_comments: list[dict[str, Any]],
           review_comments: list[dict[str, Any]]) -> tuple[str, str]:
    short = head[:7]
    code = review_state(issue_comments)
    if not code or code.get("commit_sha") != head:
        return "pending", f"Waiting for Codex to review {short}"
    if code.get("status") != "completed":
        return "pending", f"Codex is reviewing {short}"
    open_ids = unanswered(review_comments)
    if open_ids:
        return "pending", f"Codex reviewed {short}; {len(open_ids)} finding(s) await a reply"
    if code.get("has_any_findings"):
        return "success", f"Codex reviewed {short}; every finding has a reply"
    return "success", f"Codex reviewed {short}; no findings"


def _gh(args: list[str]) -> Any:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=60).stdout
    return json.loads(out) if out.strip() else None


def _pages(path: str) -> list[dict[str, Any]]:
    data = _gh(["api", "--paginate", "--slurp", path])
    return [item for page in data or [] for item in page]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    pr = _gh(["api", f"repos/{args.repo}/pulls/{args.pr}"])
    if pr.get("state") != "open":
        print(f"PR #{args.pr} is {pr.get('state')}; nothing to report")
        return 0
    head = pr["head"]["sha"]
    state, description = decide(
        head,
        _pages(f"repos/{args.repo}/issues/{args.pr}/comments?per_page=100"),
        _pages(f"repos/{args.repo}/pulls/{args.pr}/comments?per_page=100"),
    )
    print(f"{head[:7]} {state}: {description}")
    if not args.dry_run:
        _gh(["api", "-X", "POST", f"repos/{args.repo}/statuses/{head}",
             "-f", f"state={state}", "-f", f"context={CONTEXT}", "-f", f"description={description[:140]}",
             "-f", f"target_url={pr['html_url']}"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
