# #1275 unit 4: end-to-end method-revision runs

## Second run: verdict `rejected` (2026-11-28)

**The loop ran end to end. The candidate is not adopted, and no product text changed.** `compare_versioned_text` gave `rejected` and the experience entry was written. The loop gaps the first run found were fixed in between (#1300, #1303, #1305, #1306).

Label: **Codex-worker, Codex-judge comparison** (same-family bias). Evidence class: sandbox runs on a synthetic seed, not live.

### Target

The live store held no blocker that names a method in versioned text, so per the issue's restart rule the target is a known regression, not a blocker. The first run's pair (`line-cancel-watch`, `line-cancel-seat-watch`) passed every check on later sweeps, so it had no room left. A screening sweep (12 line, standing-wish, due and reminder scenarios, 1 trial each, same settings as the arms) found one failure: dev `line-status-before-first-run` (`check:preparation`: the worker asked for the source address and scheduled nothing).

- Text: the `schedule_preparation` tool description, `src/personal_agent/agent_runtime.py` lines 459-467 at `48b53628`. It already tells the worker to schedule in this case.
- Dev: `line-status-before-first-run`. Held-out: `line-change-stock-time` (the same request shape: a repeating check with no source address; it passed screening once).

### Method

- Every run: `--worker codex --worker-model gpt-6.1-sol --worker-effort medium --judge subscription-cli:codex --max-judge-calls 60 --seed synthetic-terra --no-local --instances 2`, 3 trials per scenario per arm, through `revise arm`. No API key was set.
- Proposer: `subscription-cli:codex:gpt-6.1-sol:medium`, one call, one candidate (`general`, [general-2.txt](general-2.txt)). Composition was not requested.
- Target measure `passed`; tolerances 0.05 on `rubric_honest_unverified`, `rubric_context_carry`, `check_delivered`, `check_no_memory_of_request`. The same as the first run, fixed before any result of these arms.

### Results ([runs-2.json](runs-2.json): per-run scores, the comparator result and the experience entry)

| Arm | `passed` dev | `passed` held-out |
| --- | --- | --- |
| current | 2/3 | 3/3 |
| candidate `general` | 3/3 | 3/3 |
| current, equal retries | 2/3 | 3/3 |

Rubric and tolerance measures were 1.0 in every arm. No run was refused or ungraded.

**Verdict `rejected`:** `passed` does not improve against the baseline, nor against equal retries, on held-out.

### What this does and does not show

- It does not show the candidate is worse. It also does not show it is better: the dev difference is one run in three, inside the arm's own noise.
- `preferred` was not reachable with this pair, whatever the candidate did. Held-out was already at 3/3. On dev, one scenario with a mixed current arm gives a noise of 1.0 (max minus min across epochs), which no difference between pass rates can exceed. This was visible once the current arm had run, before the candidate.
- `revise propose` refuses only a dev split at its maximum (`no_room_on_dev`). It does not check held-out room or whether the baseline's noise leaves any gain detectable. A follow-up would have to do that, or add scenarios or trials per split. #1331 added both checks.
- Seen in passing, cause not checked: across the three arms, `schedule_preparation` calls often failed one or more times before one succeeded.

### Cost

All on the Codex subscription, 0 USD. 30 worker runs (60 owner turns): 12 screening and 18 in the arms. 30 judge calls (token counts unreported by the CLI) and 1 proposer call (tokens unreported). Recorded worker tokens: about 17.2M input (15.1M cached) and 94k output, of which screening was 6.7M input and 39k output. Judgment and route-qualification calls are not counted.

## First run: no verdict (2026-11-28, #1295)

**Result: the revision is not adopted, and the loop did not complete.** `compare_versioned_text` refused the recorded arms, so there is no comparator verdict and no experience entry. No product text changed.

Label: **Codex-worker, Codex-judge comparison** (same-family bias). Evidence class: sandbox runs on a synthetic seed, not live.

### Target

The live store held no blocker that names a method in versioned text ([#1275 comment](https://github.com/Jongtae/Still/issues/1275)), so per the restart rule the target is a known regression: `line-cancel-watch` (dev) and `line-cancel-seat-watch` (held-out). Earlier sweeps repeatedly failed `check:preparation` on them: the worker could not find the fictional source, asked for an address and scheduled nothing. The suspected text is the `schedule_preparation` tool description (`src/personal_agent/agent_runtime.py` lines 459-467 at `0ca5c69c`). In those failures the rubric judge accepted the honest blocker while the deterministic check failed.

### Method

- Proposer: `subscription-cli:codex`, one call, one candidate (`general`, [general.txt](general.txt)); no `scoped` candidate came back. Composition was not planned.
- Target measure `passed`; tolerances 0.05 on `rubric_honest_unverified`, `rubric_context_carry`, `check_delivered`, `check_no_memory_of_request`; at least 3 trials. Fixed before any result.
- Three arms, 3 trials x 2 scenarios each, run one after another: current text, candidate, current text again (equal retries). Worker `codex`, judge `subscription-cli:codex`, seed `synthetic-terra`. No API key was set.
- Command per arm, run in a `git archive` copy of the repository (the candidate spliced into one copy):

```sh
python -m agentos_eval sweep --worker codex --judge subscription-cli:codex --max-judge-calls 60 \
    --seed synthetic-terra --no-local --scenarios line-cancel-watch,line-cancel-seat-watch \
    --instances 2 --epochs 3
```

### Results ([runs.json](runs.json))

| Arm | `check:preparation` | `passed` dev | `passed` held-out | Worker models seen |
| --- | --- | --- | --- | --- |
| current | 5/6 | 3/3 | 1/3 | gpt-6.1-sol, gpt-6-astra |
| candidate `general` | 6/6 | 3/3 | 3/3 | gpt-6.1-sol |
| current, equal retries | 6/6 | 3/3 | 2/3 | gpt-6.1-sol |

- The targeted failure reproduced once in 12 current-text runs (held-out) and not in 6 candidate runs. That difference is not evidence for the candidate.
- The other two current-text failures are a follow-up turn that ended with `disk I/O error` (one in each current-text arm). The tool description has no evident link to it; the cause was not investigated.
- Two current-text held-out runs miss rubric grades: one in the current arm (`judge_error`: the judge route was not yet qualified, no call made for it), and one in the equal-retries arm (`rubric_honest_unverified` empty on a `disk I/O error` run).

### Why there is no verdict

The comparator takes one `settings.model` per comparison and refuses a run that recorded another one. The default Codex route lets Still's orchestrator pick the model and effort per request, and the current-text arm used two models. The records were not filtered and the engine field was not edited to get a verdict.

Diagnostic only, not a verdict: with the engine field removed, the same function returns `insufficient_evidence`, because the current text has an ungraded held-out run. Independently of that, the current text passed every dev run, so no candidate could show the dev improvement that `preferred` requires.

### Cost

18 worker runs (36 owner turns), 18 judge calls as the sweep totals report them (6 per arm; the `judge_error` run made none for its own rubric), 1 proposer call, all on subscriptions (0 USD). Recorded worker tokens: about 9.5M input (8.1M cached) and 72k output. Judgment and route-qualification calls are not counted. One earlier baseline attempt was stopped after the eval environment failed to import Still's dependencies; it may have started up to about 2 worker runs.

### What the run found about the loop

1. The comparator cannot compare sweeps whose worker model is chosen per request. Either a sweep must pin the worker model, or the comparison must state that routing is part of what is compared.
2. The eval environment lacks Still's runtime dependencies (`cryptography`, `oauthlib`), which the subscription judge and proposer need. This run appended the system interpreter's `site-packages` through a local `sitecustomize.py`.
3. `revise` writes a candidate but running its arm needs a hand-made copy of the source tree with the text swapped in.
4. A target whose dev scenario already passes cannot be adopted; check the current text before proposing.
