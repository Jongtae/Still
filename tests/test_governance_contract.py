import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold())


def _assert_all(text: str, *markers: str) -> None:
    normalized = _normalize(text)
    missing = [marker for marker in markers if _normalize(marker) not in normalized]
    assert not missing, f"missing governance contract markers: {missing}"


def _assert_any(text: str, *alternatives: str) -> None:
    normalized = _normalize(text)
    assert any(_normalize(marker) in normalized for marker in alternatives), (
        f"missing every governance contract alternative: {alternatives}"
    )


def test_agents_declares_permanent_autonomous_delivery_guards() -> None:
    agents = _read("AGENTS.md")
    goal = _read("docs/goal-execution-contract.en.md")

    _assert_all(
        agents,
        "## Autonomous goal execution and verification",
        "[Goal Execution Contract](docs/goal-execution-contract.en.md)",
        "[Incremental Delivery](docs/incremental-delivery.en.md)",
        "requirement-to-evidence audit",
        "stable-head verification budget",
        "explicitly activated goal-ready unit/program",
        "No substep completion selects an unlisted successor or widens authority",
        "Required CI, exact-head validation and truthful evidence remain mandatory",
    )
    _assert_all(
        goal,
        "requested model/reasoning",
        "observed result",
        "already enumerated, dependency-satisfied substep",
        "top-level goal",
        "allowed authority",
        "non-goals",
        "requested, accepted, and observed settings",
    )
    _assert_any(goal, "must not select an unlisted", "may advance only")
    _assert_any(goal, "accepted setting", "tool-accepted setting", "accepted, and observed settings")


def test_english_goal_contract_is_canonical_and_korean_reference_links_to_it() -> None:
    english = _read("docs/goal-execution-contract.en.md")
    korean = _read("docs/goal-execution-contract.ko.md")

    _assert_all(
        english,
        "## Delegation and escalated review",
        "requested, accepted, and observed",
        "already enumerated, dependency-satisfied substep",
        "top-level goal",
        "requirement-to-evidence audit",
    )
    _assert_all(
        english,
        "goal-ready",
        "allowed authority",
        "non-goals",
        "evidence",
        "completion",
        "blocked",
        "automation",
    )
    _assert_all(korean, "과거 참고용 한국어 번역본", "](" + "goal-execution-contract.en.md" + ")")


def test_github_is_authoritative_for_execution_status_and_plans_are_not_status_mirrors() -> None:
    agents = _read("AGENTS.md")
    goal = _read("docs/goal-execution-contract.en.md")
    governance = _read("docs/development-governance.en.md")
    incremental = _read("docs/incremental-delivery.en.md")

    _assert_all(
        agents,
        "GitHub Issues/PRs/Checks own execution status",
        "`delivery-plan.yaml` owns current goal selection/order/scope/dependencies/authority",
        "do not mirror GitHub status",
    )

    for text in (governance, incremental):
        _assert_all(
            text,
            "GitHub Issues, Pull Requests",
            "authoritative for execution status",
        )

    _assert_all(
        governance,
        "they are not a second database of GitHub status",
        "Do not create a follow-up commit or pull request solely",
        "scope, ordering, dependency, authority, acceptance, milestone, activation, re-scope, blocker/disposition, or next-goal selection",
    )
    _assert_all(
        goal,
        "does not mirror GitHub PR/Issue status",
        "Never create a follow-up commit or PR whose sole purpose is to mirror",
        "GitHub-native merge/Issue/Check status does not require tracker/roadmap/ledger mirroring",
        "Do not require or create a tracker/roadmap/ledger update solely to mirror GitHub execution status",
    )
    _assert_all(
        governance,
        "they are not a second database of GitHub status",
        "Do not create a follow-up commit or pull request solely",
    )
    _assert_all(
        incremental,
        "do not create a follow-up commit or PR solely",
        "`delivery-plan.yaml`",
        "`TASKS.md`",
        "`docs/roadmap.md`",
        "a ledger",
    )

    stale_goal_rules = (
        "tracker/roadmap/ledger closeout",
        "update `TASKS.md`, `docs/roadmap.md`, and the ledger together",
    )
    normalized_goal = _normalize(goal)
    for stale in stale_goal_rules:
        assert _normalize(stale) not in normalized_goal, (
            f"goal execution contract still requires redundant status mirroring: {stale}"
        )


def test_iteration_issue_template_collects_the_execution_contract() -> None:
    template = _read(".github/ISSUE_TEMPLATE/iteration.md")

    _assert_all(
        template,
        "## Goal-ready activation",
        "## Allowed authority",
        "## Evidence and completion audit",
        "## Delegation and escalated review",
        "## Blocked / restart rule",
        "## Top-level goal / substep",
        "## Non-goals",
        "<type>/<issue>-<slug>",
    )
    # #1378: branch names describe the work's purpose, never one AI tool.
    for path in ("AGENTS.md", "docs/development-governance.en.md", "docs/goal-execution-contract.en.md"):
        assert "`codex/` branch" not in _read(path), path


def test_required_ci_runs_focused_guards_with_a_separate_full_suite_backstop() -> None:
    workflow = _read(".github/workflows/validate.yml")
    full = _read(".github/workflows/full-validate.yml")

    _assert_all(workflow, "Run focused governance and README tests",
                "tests/test_governance_contract.py", "tests/test_readme_localization.py",
                "scripts/verify_assistant_execution.py --check-spec",
                "scripts/verify_reuse_review.py", "scripts/verify_readme_localization.py --check-git-diff")
    _assert_all(full, "workflow_dispatch", "python3 -m pytest -q -n auto --dist loadfile --durations=25 tests")


def test_required_validate_runs_the_whole_linux_suite() -> None:
    """#234: the required `validate` check runs every test on Linux, with full-validate's
    Linux invocation; only the macOS real-WebKit classes are left to full-validate."""
    workflow = _read(".github/workflows/validate.yml")
    linux = _read(".github/workflows/full-validate.yml").split("full-validate-macos:", 1)[0]
    # The ruleset requires the context `validate`: the job id, with no other `name:`.
    jobs = workflow.split("\njobs:\n", 1)[1]
    assert re.findall(r"^  (\S+):$", jobs, re.M) == ["validate"]
    assert not re.search(r"^    name:", jobs, re.M)
    assert "runs-on: ubuntu-latest" in jobs
    # Same locked environment and the same parallel invocation of the whole `tests` directory.
    sync = "uv sync --locked --extra mcp-host --extra schema-validation --group dev --no-python-downloads"
    assert sync in workflow and sync in linux
    suite = 'PATH="$PWD/.venv/bin:$PATH" python3 -m pytest -q -n auto --dist loadfile --durations=25 tests\n'
    assert suite in workflow and suite in linux
    assert "--ignore" not in workflow and "--deselect" not in workflow
    assert "AGENTOS_REAL_BROWSER_TESTS" not in workflow
    # #260: only a text-only pull request may skip it; a missing or unknown scope runs it.
    full_step = workflow.split("- name: Run the full test suite (parallel)", 1)[1].split("- name:", 1)[0]
    assert "if: steps.scope.outputs.suite != 'text'" in full_step
    scope_step = workflow.split("- name: Decide test scope", 1)[1].split("- name:", 1)[0]
    assert 'git show "$BASE_SHA:scripts/ci/test_scope.py"' in scope_step, "the scope script comes from the base"
    assert 'echo "suite=full"' in scope_step, "a push to main runs everything"


def test_the_test_scope_is_full_unless_every_changed_path_is_text(tmp_path) -> None:
    """#260: code, dependency, workflow or unknown paths run the whole suite; text runs the text readers."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("test_scope", ROOT / "scripts/ci/test_scope.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for paths in (["src/personal_agent/x.py"], ["tests/test_x.py"], ["scripts/ci/test_scope.py"], ["uv.lock"],
                  ["pyproject.toml"], [".github/workflows/validate.yml"], ["docs/a.en.md", "src/x.py"],
                  ["install.sh"], [".gitignore"], [], [""]):
        assert module.scope(paths) == "full", paths
    for paths in (["docs/a.en.md"], ["README.md", "docs/i18n/README.ko.md"], ["delivery-plan.yaml"],
                  [".github/ISSUE_TEMPLATE/bug.md"], ["AGENTS.md", "LICENSE"]):
        assert module.scope(paths) == "text", paths
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_reads_docs.py").write_text("PATH = 'docs/x.en.md'\n")
    (tmp_path / "tests/test_names_plan.py").write_text("open('roadmap.yaml')\n")
    (tmp_path / "tests/test_pure.py").write_text("assert 1 + 1 == 2\n")
    assert module.text_tests(["roadmap.yaml"], tmp_path) == ["tests/test_names_plan.py", "tests/test_reads_docs.py"]
    # Codex review on #261: a test that reads text only through a local helper (directly or via another helper).
    (tmp_path / "tests/plan_helpers.py").write_text("def load():\n    return open('delivery-plan.yaml').read()\n")
    (tmp_path / "tests/more_helpers.py").write_text("from plan_helpers import load\n")
    (tmp_path / "tests/test_via_helper.py").write_text("from more_helpers import load\n\ndef test_x():\n    load()\n")
    assert "tests/test_via_helper.py" in module.text_tests(["docs/a.en.md"], tmp_path)
    assert "tests/test_pure.py" not in module.text_tests(["docs/a.en.md"], tmp_path)
    assert "tests/test_fu1_program_governance.py" in module.text_tests(["docs/history/predecessor-delivery-plan.yaml"])
    selected = set(module.text_tests(["docs/a.en.md"]))
    readers = {f"tests/{path.name}" for path in (ROOT / "tests").glob("test_*.py")
               if any(marker in path.read_text(encoding="utf-8") for marker in module.TEXT_MARKERS)}
    assert readers <= selected and "tests/test_governance_contract.py" in selected
    # Codex review on #261: renaming code into a text path still reports the code path.
    import subprocess
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/foo.py").write_text("x = 1\n" * 20)
    git = lambda *args: subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout
    git("init", "-q"); git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "root")
    git("add", "."); git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD").strip()
    (repo / "docs").mkdir()
    git("mv", "src/foo.py", "docs/foo.md"); git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "move")
    paths = module.changed(base, "HEAD", repo)
    assert "src/foo.py" in paths and module.scope(paths) == "full"


def test_the_full_suite_runs_in_parallel_and_real_browser_classes_run_serially() -> None:
    """#135, #156: every test runs in parallel; on macOS the classes that open real WebKit
    windows or Keychain items skip there and run serially afterwards."""
    full = _read(".github/workflows/full-validate.yml")
    linux, mac = full.split("full-validate-macos:", 1)
    assert "python3 -m pytest -q -n auto --dist loadfile --durations=25 tests\n" in linux
    assert "--ignore" not in full
    listed = re.search(r"REAL_BROWSER_TESTS: >-\n((?:\s+tests/\S+\.py::\w+\n)+)", mac)
    assert listed, "macOS job names its real-WebKit classes"
    gated = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and any(
                    re.search(r"_webkit_ready\(\)|AGENTOS_REAL_BROWSER_TESTS|REAL_BROWSER_TESTS\b",
                              ast.get_source_segment(source, decorator) or "")
                    for decorator in node.decorator_list):
                assert isinstance(node, ast.ClassDef), f"{path.name}::{node.name}: gate whole classes"
                gated.add(f"tests/{path.name}::{node.name}")
    assert set(listed.group(1).split()) == gated
    # The parallel step never sets the opt-in, so the gated classes skip there; only the serial step sets it.
    assert "AGENTOS_REAL_BROWSER_TESTS: '1'" in mac.split("Real-WebKit classes (serial)", 1)[1]
    assert mac.count("AGENTOS_REAL_BROWSER_TESTS: '1'") == 1
    assert "python3 -m pytest -q -n auto --dist loadfile --durations=25 tests\n" in mac
    assert "python3 -m pytest -q --durations=25 $REAL_BROWSER_TESTS" in mac
    assert "if: ${{ !cancelled() }}" in mac


def test_verification_budget_requires_stable_heads_and_batched_remediation() -> None:
    agents = _read("AGENTS.md")
    goal = _read("docs/goal-execution-contract.en.md")
    pa1 = _read("docs/pa1-parallel-delivery.en.md")

    _assert_all(
        agents,
        "stable-head verification budget",
        "Goal Execution Contract",
        "Required CI, exact-head validation",
        "Escalated review is risk-triggered",
    )
    _assert_all(
        goal,
        "verification budget",
        "stable",
        "focused tests",
        "coherent checkpoint",
        "batch",
        "unchanged head",
        "duplicate review",
        "poll",
        "exact-head",
        "escalated review",
    )

    _assert_all(
        goal,
        "cycle 1",
        "cycle 2",
        "third or later",
        "newly discovered security",
        "unknown root cause",
        "do not run the full suite after every narrow mechanical edit",
        "never skip, weaken, relabel, or bypass",
        "is repeated only when the remediation changes the boundary that triggered review",
        "mechanical or non-boundary remediation does not automatically reopen that additional review",
        "consolidated final remediation head",
    )
    _assert_all(
        pa1,
        "## Verification budget",
        "soft budget",
        "one review-ready broad cycle",
        "one consolidated remediation broad cycle",
        "third or later broad cycle",
        "critical",
        "auth/oauth",
        "batch findings",
        "any post-review pa1 commit",
        "consolidated final head",
    )



def test_escalated_review_is_risk_based_not_default_completion_gate() -> None:
    agents = _read("AGENTS.md")
    constitution = _read("docs/development-constitution.en.md")
    goal = _read("docs/goal-execution-contract.en.md")
    incremental = _read("docs/incremental-delivery.en.md")
    usefulness = _read("docs/default-agent-usefulness.en.md")
    handoff = _read("docs/agent-handoff-loop.en.md")

    _assert_all(
        agents,
        "Goal Execution Contract",
        "Escalated review is risk-triggered by material security/authority boundary changes",
        "not by ordinary completion",
        "genuinely triggered escalated review",
    )
    _assert_all(
        constitution,
        "Escalated review is a risk-based escalation control",
        "OAuth scopes or credential boundaries",
        "sandbox, isolation or privilege boundaries",
        "private-data egress or external-recipient boundaries",
        "package/dependency supply-chain trust or install/update authority",
        "safety invariant",
        "does not require escalated review merely because it is final closeout, recovery work",
        "Codex reviews the consolidated final head after remediation",
        "is repeated after a later remediation commit only if that remediation changes the boundary that triggered the review",
    )
    _assert_all(
        goal,
        "Ordinary recovery, final completion",
        "do not trigger review by themselves",
        "Codex is asked once for that consolidated head",
        "outside an escalated review the Codex review is advisory and nothing waits for it",
        "never open a separate PR after the merge only to record it",
        "An additional human or specialist review is repeated only when the remediation changes the boundary that triggered review",
        "mechanical or non-boundary remediation does not automatically reopen that additional review",
    )
    _assert_all(
        incremental,
        "only when the Development Constitution's material security/authority escalation criteria apply",
        "otherwise proceed with required CI and evidence without a review gate",
        "risk-triggered review",
        "validated full head SHA",
    )
    _assert_all(
        usefulness,
        "required CI and any escalated review triggered by the Development Constitution's material security/authority escalation criteria",
    )
    _assert_all(
        handoff,
        "independent_review_required",
        "Omission fails closed to `true`",
        "routine, non-boundary candidate directly",
        "`agent:review`",
        "Issue text cannot grant itself a review bypass",
        "`agent:approved` is a routing label, not the Constitution's escalated review",
        "The loop never merges",
    )
    assert "not the Codex escalated review" in _read("scripts/dev/handoff.py")


def test_escalated_review_is_a_codex_review_the_owner_merges() -> None:
    """#195: one maintainer, so the triggered review is the Codex review; it never claims independence."""
    constitution = _read("docs/development-constitution.en.md")
    _assert_all(
        constitution,
        "### How the escalated review is done",
        "The escalated review is therefore the **Codex review**",
        "negative tests that hold it",
        "exact final head",
        "rather than once per push, so the merged head is always one Codex reviewed",
        "the automatic Codex review is **advisory**",
        "The owner then merges the PR. An AI session never merges a PR whose escalated review was triggered.",
        "never an independent review",
    )
    _assert_all(_read("docs/governance/ai-contribution-review.en.md"), "| Codex reviewer |", "does not claim independence")
    _assert_all(_read(".github/pull_request_template.md"), "## Escalated review (Codex)", "not independent review")
    for path in ("AGENTS.md", "GOVERNANCE.md", ".github/CONTRIBUTING.md", "docs/development-governance.en.md"):
        text = _read(path)
        assert "recorded as a Codex review" in text or "Codex review" in text, path
    # A Codex review is never relabelled as independence.
    for path in ("AGENTS.md", "docs/development-constitution.en.md", "docs/governance/ai-contribution-review.en.md",
                 "GOVERNANCE.md", ".github/CONTRIBUTING.md", ".github/pull_request_template.md"):
        lowered = _read(path).lower()
        for phrase in ("codex review is an independent review", "codex review counts as independent",
                       "independent codex review", "an independent review by codex"):
            assert phrase not in lowered, (path, phrase)


def test_claude_bootstrap_points_at_no_completed_program() -> None:
    """The first file a Claude session reads must not name finished work.

    It used to. `CLAUDE.md` told every session to read the EPIC-PA1 / #386
    execution cursor first and to update that cursor after any state
    transition -- long after #386 closed, its execution authority was retired
    (GOV-PA1-07 / #467) and `next_goal.id` became `null`. No runtime effect,
    because `DeliveryPlan.select` refuses completed work outright, but it
    pointed a fresh session at a completed program as the place to look for
    the next action. That is the re-selection risk arriving through
    documentation instead of through the controller.
    """
    claude = _read("CLAUDE.md")

    # No program identity is hardcoded here. A program that wants a resume
    # source defines its own; this file must not name one, and must not
    # outlive the program it named.
    for stale in ("#386", "EPIC-PA1", "agentos-execution-cursor"):
        assert _normalize(stale) not in _normalize(claude), (
            f"CLAUDE.md still names {stale}; a bootstrap must not point at a "
            "specific program, least of all a closed one"
        )

    # Fail closed, stated rather than implied.
    _assert_all(
        claude,
        "next_goal",
        "no work is selected",
        "do not infer one from open issues",
        # #1191: a direct owner request is authorised by itself; no plan ceremony.
        "a change the owner asks for directly in the conversation is authorised by that request",
    )
    _assert_any(
        claude,
        "only an explicit owner `active` transition authorises execution",
        "only an explicit owner `active` transition authorizes execution",
    )


def test_following_the_bootstrap_selects_nothing_while_no_goal_is_active() -> None:
    """The behavioural half: do what CLAUDE.md says and get nothing.

    The document assertions above would pass against a file that said the
    right words over a plan that still handed out work. This walks the
    bootstrap's own stated route -- read `next_goal`, and select only on an
    explicit `active` transition -- against the real plan and the real
    controller.
    """
    import json

    from scripts.dev.delivery import DeliveryPlan

    plan_path = ROOT / "delivery-plan.yaml"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    # Step 3 of the bootstrap. Step 4 covers both "no top-level goal is
    # active" forms: `next_goal.id` is null, OR its status is not `active`.
    # An earlier form pinned `id is None`, i.e. the first form only, and so
    # broke as soon as the owner declared a goal-ready program (GOV-FU1-01 /
    # #502). Each form is now asserted in full for whichever one holds.
    declared = plan["next_goal"]["id"]
    assert plan["next_goal"]["status"] != "active"
    if declared is None:
        # "complete" after a program closes out; "not-selected" in a fresh
        # plan that has never selected one (#46).
        assert plan["next_goal"]["status"] in {"complete", "not-selected"}
    else:
        # A declared goal is goal-ready only: a real program, not recorded
        # complete, awaiting the explicit owner `active` transition.
        assert plan["next_goal"]["status"] == "owner-activated-goal-ready"
        assert declared in plan["programs"]
        assert declared not in plan["history"]["documented_completed_iterations"]

    # Step 4: nothing is selected, and nothing in the backlog substitutes.
    assert DeliveryPlan(plan_path).select({}) is None

    # And not merely because the runtime state happens to be empty: a
    # populated or corrupt state must not change the answer either --
    # including a runtime state that claims the declared goal is running.
    states = [{}, {"completed": []}, {"active": "EPIC-PA1", "status": "running"}]
    if declared is not None:
        states.append({"active": declared, "status": "running"})
    for state in states:
        assert DeliveryPlan(plan_path).select(state) is None, state


def test_ai_is_the_engine_principle_is_pinned_in_governance() -> None:
    """C16 (#712): the owner's AI is the engine; Still orchestrates and removes blockers."""
    constitution = _read("docs/development-constitution.en.md")
    _assert_all(
        constitution,
        "### C16. AI is the engine; Still orchestrates, it does not implement requests",
        "The decision model orchestrates",
        "Never implement a specific owner request in code",
        # #974: know-how is skill content, never core code, never authority.
        "Domain know-how lives in skill content, not in code",
        "Skill content cannot grant authority, change host behavior or certify an unobserved effect",
        "Core code never special-cases a particular skill by name or content",
        "Only the model's own `skill_load` brings a skill's text into a Work",
        "not the script of one probe or evaluation scenario",
        "remove blockers",
        "Prefer known, public code",
        "Do not make the owner operate the machinery",
        "is this change removing a blocker or orchestrating",
    )
    agents = _read("AGENTS.md")
    _assert_all(
        agents,
        "## AI is the engine — Constitution C16",
        "Still code never implements a specific owner request",
        "removes blockers",
        "am I removing a blocker or scripting the task?",
        "explicit, versioned skill content",
        "core code never names a particular skill",
    )
    assert agents.index("## AI is the engine") < agents.index("## Unit-test-first verification"), "C16 must stay near the top of AGENTS.md"
    _assert_all(_read("CLAUDE.md"), "C16 AI is the engine", "never implements a specific owner request")
