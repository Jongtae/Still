# #1275 unit 4: one end-to-end revision run (2026-11-28)

**Result: the revision is not adopted, and the loop did not complete.** `compare_versioned_text` refused the recorded arms, so there is no comparator verdict and no experience entry. No product text changed.

Label: **Codex-worker, Codex-judge comparison** (same-family bias). Evidence class: sandbox runs on a synthetic seed, not live.

## Target

The live store held no blocker that names a method in versioned text ([#1275 comment](https://github.com/Jongtae/Still/issues/1275)), so per the restart rule the target is a known regression: `line-cancel-watch` (dev) and `line-cancel-seat-watch` (held-out). Earlier sweeps repeatedly failed `check:preparation` on them: the worker could not find the fictional source, asked for an address and scheduled nothing. The suspected text is the `schedule_preparation` tool description (`src/personal_agent/agent_runtime.py` lines 459-467 at `0ca5c69c`). In those failures the rubric judge accepted the honest blocker while the deterministic check failed.

## Method

- Proposer: `subscription-cli:codex`, one call, one candidate (`general`, [general.txt](general.txt)); no `scoped` candidate came back. Composition was not planned.
- Target measure `passed`; tolerances 0.05 on `rubric_honest_unverified`, `rubric_context_carry`, `check_delivered`, `check_no_memory_of_request`; at least 3 trials. Fixed before any result.
- Three arms, 3 trials x 2 scenarios each, run one after another: current text, candidate, current text again (equal retries). Worker `codex`, judge `subscription-cli:codex`, seed `synthetic-terra`. No API key was set.
- Command per arm, run in a `git archive` copy of the repository (the candidate spliced into one copy):

```sh
python -m agentos_eval sweep --worker codex --judge subscription-cli:codex --max-judge-calls 60 \
    --seed synthetic-terra --no-local --scenarios line-cancel-watch,line-cancel-seat-watch \
    --instances 2 --epochs 3
```

## Results ([runs.json](runs.json))

| Arm | `check:preparation` | `passed` dev | `passed` held-out | Worker models seen |
| --- | --- | --- | --- | --- |
| current | 5/6 | 3/3 | 1/3 | gpt-6.1-sol, gpt-6-astra |
| candidate `general` | 6/6 | 3/3 | 3/3 | gpt-6.1-sol |
| current, equal retries | 6/6 | 3/3 | 2/3 | gpt-6.1-sol |

- The targeted failure reproduced once in 12 current-text runs (held-out) and not in 6 candidate runs. That difference is not evidence for the candidate.
- The other two current-text failures are a follow-up turn that ended with `disk I/O error` (one in each current-text arm). The tool description has no evident link to it; the cause was not investigated.
- Two current-text held-out runs miss rubric grades: one in the current arm (`judge_error`: the judge route was not yet qualified, no call made for it), and one in the equal-retries arm (`rubric_honest_unverified` empty on a `disk I/O error` run).

## Why there is no verdict

The comparator takes one `settings.model` per comparison and refuses a run that recorded another one. The default Codex route lets Still's orchestrator pick the model and effort per request, and the current-text arm used two models. The records were not filtered and the engine field was not edited to get a verdict.

Diagnostic only, not a verdict: with the engine field removed, the same function returns `insufficient_evidence`, because the current text has an ungraded held-out run. Independently of that, the current text passed every dev run, so no candidate could show the dev improvement that `preferred` requires.

## Cost

18 worker runs (36 owner turns), 18 judge calls as the sweep totals report them (6 per arm; the `judge_error` run made none for its own rubric), 1 proposer call, all on subscriptions (0 USD). Recorded worker tokens: about 9.5M input (8.1M cached) and 72k output. Judgment and route-qualification calls are not counted. One earlier baseline attempt was stopped after the eval environment failed to import Still's dependencies; it may have started up to about 2 worker runs.

## What the run found about the loop

1. The comparator cannot compare sweeps whose worker model is chosen per request. Either a sweep must pin the worker model, or the comparison must state that routing is part of what is compared.
2. The eval environment lacks Still's runtime dependencies (`cryptography`, `oauthlib`), which the subscription judge and proposer need. This run appended the system interpreter's `site-packages` through a local `sitecustomize.py`.
3. `revise` writes a candidate but running its arm needs a hand-made copy of the source tree with the text swapped in.
4. A target whose dev scenario already passes cannot be adopted; check the current text before proposing.
