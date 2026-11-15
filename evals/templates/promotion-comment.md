## Change-triggered regression: $decision

$reason

Base: $base → candidate: $head

| Route | Pinned model | Effort | CLI version | Evidence class | Calls / cap | Usage | Latency (ms) |
|---|---|---|---|---|---|---|---|
| $route | $model | $effort | $cli | $evidence | $calls / $cap | $usage | $latency |

Planned calls: $planned_calls. Budget exceeded: $budget_exceeded.

Newly spent calls / cap: $budget_actual / $cap. Historical reused calls: $reused_calls.

Reused arm provenance and time gap: $reuse

Source arm evidence and synthetic seed manifests: $evidence_details

Comparable: $comparable. Conditions (both arms, differences and unknowns): $conditions

Instruction/prompt/tool identities (null means unknown): $digests

Unavailable digest reasons: $digest_reasons

Held-out verdict and reasons: $heldout

Counts by change kind: $counts

$cases

$selection

Unmatched change signals: $unmatched

Selected but not run, count by set: $not_run_counts

<details>
<summary>Selected cases requiring their own runners</summary>

| Set | Case | Split | Reason |
|---|---|---|---|
$not_run

</details>

Rollback (not executed): $rollback

Re-run on the reverted state (not executed): $rerun

Evidence applies only to the named route/model and class. Fixture, actual-CLI synthetic,
and owner-live observations are separate; unobserved classes are not claimed.
Subscription usage reported as unavailable is not a zero-cost claim.
