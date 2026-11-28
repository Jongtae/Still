# #1355: does the automatic self-revision loop improve Still dramatically?

Sandbox experiment. Nothing here changes live Still, the owner store or product text.

## Success bar (fixed before any loop result)

Written and committed before the loop's first round ran. The final held-out step (`revise final`) decides it.

- **Dramatic**: on held-out scenarios the loop never saw, the final text's `passed` is at least **+20 points**
  (0.20) above the start text **and** at least +20 points above an equal-retries arm of the start text; the
  comparator's verdict on held-out is `preferred` (the gain exceeds the epoch noise of both comparisons); and no
  tolerance dimension falls by more than 0.05 (`rubric_honest_unverified`, `check_delivered`, `check_not_failed`).
- **Modest**: `preferred` on held-out, but the gain is under +20 points against either the start text or equal
  retries.
- **None**: not `preferred` and not worse: within noise, insufficient evidence, or the loop promoted no text.
- **Worse**: the final text is `rejected` on held-out because `passed` falls beyond the noise or a tolerance
  dimension regresses beyond 0.05.

Only **dramatic** would justify proposing a runtime self-revision mode (a separate issue with Codex escalated
review). Same settings for every arm: worker `codex` pinned to one model and effort `medium`, judge
`subscription-cli:codex`, seed `synthetic-terra`, at least 3 trials per scenario per arm.

## Target

Fixed after the screening sweep and before the loop's first round.

**Screening** (52 bundled scenarios: every one except the 56 progress-update scenarios, which pass 24/24 and have
no room, and the readable-check scenarios, held back until #1359 landed; 1 trial each, the same settings as the
arms). 46/52 passed. Dev failed 5 of 37, and held-out 1 of 15. Four of the five dev failures are scheduling
scenarios: `line-add-watch-rule` and `line-cancel-watch` (`check:not_failed`, the Work ended `awaiting_connection`),
`tee-time-reminder` and `due-exhibit-quiet-check` (`check:preparation`, no preparation was created). The fifth,
`skill-add-reference-by-name`, is a settings change and shares no text with the others.

**Text**: the `schedule_preparation` tool description, `src/personal_agent/agent_runtime.py` lines 459-467 at
`9ffc6130`. It is shared guidance, not one scenario's text: every reminder, watch, line and come-due scenario reaches
it, and it is the one text the scheduling failures have in common. It is tool guidance, not authority, approval,
payment, secret, budget or evaluation code.

**Dev** (the loop sees these): the six preparation scenarios with the most signal: the four scheduling failures above,
plus `line-status-before-first-run` (the #1275 regression) and `standing-wish-becomes-watch`.

**Held-out** (only `revise final` runs these): every held-out scenario that schedules a preparation:
`due-radio-rehearsal-cue`, `line-past-submission-deadline`, `line-change-stock-time`, `line-cancel-seat-watch`,
`relative-time-reminder` and `standing-wish-is-not-a-memory`. No scenario was added to either split.

**Known ceiling, stated before the run**: all six held-out scenarios passed screening. The final step can show a gain
only as large as the start text's held-out failure rate over 3 trials. If the start text passes held-out at 0.8 or
more, **dramatic is unreachable whatever the loop does**, and the record will say that, not "none". The bar is not
lowered because of it.

**Run settings**:
- worker `codex --worker-model gpt-6.2-sol --worker-effort medium`;
- judge `subscription-cli:codex` with `--max-judge-calls 120`;
- seed `synthetic-terra --no-local --instances 3`;
- 3 epochs per arm;
- proposer `subscription-cli:codex:gpt-6.2-sol:medium`, two candidates per round, no composition;
- measure `passed`, with the default tolerances of 0.05.

**Rounds and budget**: 3 rounds, the minimum, because there are few failures to learn from. `--max-worker-runs 240`
for the loop: 18 base runs, then up to 72 per round. 54 for the final step: start, final and equal retries, each
18 runs. That is at most 294 Codex worker runs, plus one judge call per run and up to 2 proposer calls per round,
all on the subscription. The loop stops on its own at no room or the budget. If round 1 promotes nothing and round 2
promotes nothing, the third round still runs, because stopping mid-loop leaves no final text.
