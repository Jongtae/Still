## Change-triggered regression: inconclusive

The paired held-out verdict does not establish a gain.

Base: bca55624ead33c74e023951378a07b2d739bb44c → candidate: c3876f67c87acd2f58480a496f15cfce890bfe23

| Route | Pinned model | Effort | CLI version | Evidence class | Calls / cap | Usage | Latency (ms) |
|---|---|---|---|---|---|---|---|
| subscription_cli | gpt-5.6-terra | low | codex-cli 0.167.2 | actual-cli-synthetic | 304 / 166 | "unavailable" | {"baseline": {"p50_ms": 20716.0, "p95_ms": 57746.0}, "candidate": {"p50_ms": 27866.0, "p95_ms": 87604.0}} |

Planned calls: 166. Budget exceeded: false.

Newly spent calls / cap: 152 / 166. Historical reused calls: 152.

Reused arm provenance and time gap: {"reused_arm": {"arm": "candidate", "report_path": "evals/records/855-752/candidate-arm-report.json", "report_sha256": "103186183d23310e08ee8102c7ada8d88771ed4b24c25ade691afaa7f89cad62", "report_content_digest": "f488eeee951854f0d0d7cbbc2d2357e9d94c7cfedec32bd89cef11ad9631f0bd", "measured_at": null, "report_modified_at": "2026-11-14T15:13:08.123903+00:00", "measured_at_source": "unavailable; file mtime is a proxy only"}, "time_gap_seconds": null, "time_gap_proxy_seconds": 3520.5041439533234, "time_gap_source": "measurement time unavailable; report-file-mtime proxy only"}

Source arm evidence and synthetic seed manifests: {"classes": {"baseline": "live-route:subscription_cli", "candidate": "live-route:subscription_cli"}, "seed_manifests": {"baseline": {"created_at": "2026-11-14T13:16:26.815463+00:00", "engine": "codex", "git_head": "081535f", "owner_data": "none", "requested_effort": null, "requested_model": "gpt-5.6-terra", "runtime": {"active": {"available": true, "cli_version": "codex-cli 0.167.2", "destination": "OpenAI (Codex 구독 계정)", "effort": "low", "engine": "codex", "model_policy": "explicit", "qualification": {"cli_version": "codex-cli 0.167.2", "model": "gpt-5.6-terra", "p50_seconds": 11.485, "p95_seconds": 16.529, "score": 1.0, "suite_version": "decision-qualification/4"}, "requalification_needed": false, "requested_model": "gpt-5.6-terra", "source": "owner", "transport": "subscription_cli"}, "effective": {"reason": "", "state": "active"}, "observed_models": [], "suite_version": "decision-qualification/4", "unavailable_reasons": {}}}, "candidate": {"created_at": "2026-11-14T13:16:26.815463+00:00", "engine": "codex", "git_head": "081535f", "owner_data": "none", "requested_effort": null, "requested_model": "gpt-5.6-terra", "runtime": {"active": {"available": true, "cli_version": "codex-cli 0.167.2", "destination": "OpenAI (Codex 구독 계정)", "effort": "low", "engine": "codex", "model_policy": "explicit", "qualification": {"cli_version": "codex-cli 0.167.2", "model": "gpt-5.6-terra", "p50_seconds": 11.485, "p95_seconds": 16.529, "score": 1.0, "suite_version": "decision-qualification/4"}, "requalification_needed": false, "requested_model": "gpt-5.6-terra", "source": "owner", "transport": "subscription_cli"}, "effective": {"reason": "", "state": "active"}, "observed_models": [], "suite_version": "decision-qualification/4", "unavailable_reasons": {}}}}}

Comparable: true. Conditions (both arms, differences and unknowns): {"baseline": {"case_set_digest": "e5f089fcfdfe222b10f9c581c7de523ab248e318de687bdec368bedb24447931", "case_set_version": "judgment-validation/3", "catalogue_digest": "41c67ecc7505d98fe4aab13f3d592a68c18106be4db229d9d302792efeba66bd", "evidence_class": "live-route:subscription_cli", "harness_version": "judgment-harness/1", "model_observation": {"claim": "requested model pinned; observed model not reported by the CLI", "reported": false}, "partner_digest": "9c7576e2daee5d72a6003585649e3b4282561b66d48b151f85c653f162dd3c20", "partner_version": "judgment-heldout-partners/1", "pinned_model": "gpt-5.6-terra", "runtime.cli_version": "codex-cli 0.167.2", "runtime.destination": "OpenAI (Codex 구독 계정)", "runtime.effort": "low", "runtime.engine": "codex", "runtime.model_policy": "explicit", "runtime.observed_models": [["subscription_cli", "codex", "codex", "gpt-5.6-terra", "not reported", "explicit"]], "runtime.requested_model": "gpt-5.6-terra", "runtime.suite_version": "decision-qualification/4", "runtime.transport": "subscription_cli", "seed_manifest_digest": "ed90355aeb3fac613c91971d72a8bc1799e327eb578abdca7b0c30a4d6dca0e0", "splits": {"escalation-en": "dev", "escalation-ko": "dev", "goal-curiosity": "dev", "goal-curiosity-ko": "dev", "goal-failed": "dev", "goal-failed-ko": "dev", "goal-legitimate-question": "dev", "goal-legitimate-question-ko": "dev", "goal-partial": "dev", "goal-partial-ko": "dev", "goal-repeat-question": "dev", "goal-repeat-question-ko": "dev", "goal-served": "dev", "goal-served-ko": "dev", "goal-unknown": "dev", "goal-unknown-ko": "dev", "goal-unsupported-figure": "dev", "goal-unsupported-figure-ko": "dev", "heldout-escalation": "heldout", "heldout-goal-casual-reply": "heldout", "heldout-goal-failed": "heldout", "heldout-goal-legitimate-question": "heldout", "heldout-goal-partial": "heldout", "heldout-goal-repeat-question": "heldout", "heldout-goal-served": "heldout", "heldout-goal-unhelpful-curiosity": "heldout", "heldout-goal-unknown": "heldout", "heldout-goal-unsupported-figure": "heldout", "heldout-plan-account": "heldout", "heldout-plan-plain": "heldout", "heldout-plan-situation": "heldout", "heldout-plan-statement": "heldout", "heldout-plan-technical": "heldout", "plan-forwarding": "dev", "plan-location": "dev", "plan-password": "dev", "plan-plain-en": "dev", "plan-plain-ko": "dev", "plan-schedule": "dev", "plan-statement": "dev", "plan-subscription": "dev", "plan-technical": "dev"}}, "candidate": {"case_set_digest": "e5f089fcfdfe222b10f9c581c7de523ab248e318de687bdec368bedb24447931", "case_set_version": "judgment-validation/3", "catalogue_digest": "41c67ecc7505d98fe4aab13f3d592a68c18106be4db229d9d302792efeba66bd", "evidence_class": "live-route:subscription_cli", "harness_version": "judgment-harness/1", "model_observation": {"claim": "requested model pinned; observed model not reported by the CLI", "reported": false}, "partner_digest": "9c7576e2daee5d72a6003585649e3b4282561b66d48b151f85c653f162dd3c20", "partner_version": "judgment-heldout-partners/1", "pinned_model": "gpt-5.6-terra", "runtime.cli_version": "codex-cli 0.167.2", "runtime.destination": "OpenAI (Codex 구독 계정)", "runtime.effort": "low", "runtime.engine": "codex", "runtime.model_policy": "explicit", "runtime.observed_models": [["subscription_cli", "codex", "codex", "gpt-5.6-terra", "not reported", "explicit"]], "runtime.requested_model": "gpt-5.6-terra", "runtime.suite_version": "decision-qualification/4", "runtime.transport": "subscription_cli", "seed_manifest_digest": "ed90355aeb3fac613c91971d72a8bc1799e327eb578abdca7b0c30a4d6dca0e0", "splits": {"escalation-en": "dev", "escalation-ko": "dev", "goal-curiosity": "dev", "goal-curiosity-ko": "dev", "goal-failed": "dev", "goal-failed-ko": "dev", "goal-legitimate-question": "dev", "goal-legitimate-question-ko": "dev", "goal-partial": "dev", "goal-partial-ko": "dev", "goal-repeat-question": "dev", "goal-repeat-question-ko": "dev", "goal-served": "dev", "goal-served-ko": "dev", "goal-unknown": "dev", "goal-unknown-ko": "dev", "goal-unsupported-figure": "dev", "goal-unsupported-figure-ko": "dev", "heldout-escalation": "heldout", "heldout-goal-casual-reply": "heldout", "heldout-goal-failed": "heldout", "heldout-goal-legitimate-question": "heldout", "heldout-goal-partial": "heldout", "heldout-goal-repeat-question": "heldout", "heldout-goal-served": "heldout", "heldout-goal-unhelpful-curiosity": "heldout", "heldout-goal-unknown": "heldout", "heldout-goal-unsupported-figure": "heldout", "heldout-plan-account": "heldout", "heldout-plan-plain": "heldout", "heldout-plan-situation": "heldout", "heldout-plan-statement": "heldout", "heldout-plan-technical": "heldout", "plan-forwarding": "dev", "plan-location": "dev", "plan-password": "dev", "plan-plain-en": "dev", "plan-plain-ko": "dev", "plan-schedule": "dev", "plan-statement": "dev", "plan-subscription": "dev", "plan-technical": "dev"}}, "differences": [], "unknown": []}

Instruction/prompt/tool identities (null means unknown): {"instructions_digests": {"baseline": null, "candidate": null}, "prompt_digests": {"baseline": {"personal_agent/prompts/ask_or_assume.md": "40696921e12f", "personal_agent/prompts/goal_reached.md": "fba6718fa9e0"}, "candidate": {"personal_agent/prompts/ask_or_assume.md": "57cafb8696e5", "personal_agent/prompts/goal_reached.md": "3421c0f6fcd3"}}, "tool_schema_digests": {"baseline": null, "candidate": null}}

Unavailable digest reasons: {"instructions_digests": "#825 per-attempt digests are recorded on Work turns, not on judgment calls.", "tool_schema_digests": "#825 per-attempt digests are recorded on Work turns, not on judgment calls."}

Held-out verdict and reasons: {"verdict": "inconclusive", "reasons": ["Only uncertain/mixed changes, or a lower mean rate without a certain regression."], "fixed": [], "regressed": [], "uncertain_regressed": [], "spread": 1.0, "difference": 0.022222222222222254, "pass_rates": {"baseline": 0.9333333333333333, "candidate": 0.9555555555555556}, "counts": {"same": 14, "uncertain-fixed": 1}}

Counts by change kind: {"mixed": 1, "same": 37, "uncertain-fixed": 1, "uncertain-regressed": 3}

| Case | Split | Baseline passed/trials | Candidate passed/trials | Change | Spread | Difference |
|---|---|---:|---:|---|---:|---:|
| escalation-en | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| escalation-ko | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-curiosity | dev | 2/3 | 2/3 | mixed | 1.0 | 0.0 |
| goal-curiosity-ko | dev | 3/3 | 2/3 | uncertain-regressed | 1.0 | -0.33333333333333337 |
| goal-failed | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-failed-ko | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-legitimate-question | dev | 3/3 | 1/3 | uncertain-regressed | 1.0 | -0.6666666666666667 |
| goal-legitimate-question-ko | dev | 0/3 | 0/3 | same | 0.0 | 0.0 |
| goal-partial | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-partial-ko | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-repeat-question | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-repeat-question-ko | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-served | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-served-ko | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-unknown | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-unknown-ko | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-unsupported-figure | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| goal-unsupported-figure-ko | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-escalation | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-goal-casual-reply | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-goal-failed | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-goal-legitimate-question | heldout | 2/3 | 2/3 | same | 1.0 | 0.0 |
| heldout-goal-partial | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-goal-repeat-question | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-goal-served | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-goal-unhelpful-curiosity | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-goal-unknown | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-goal-unsupported-figure | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-plan-account | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-plan-plain | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-plan-situation | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| heldout-plan-statement | heldout | 1/3 | 2/3 | uncertain-fixed | 1.0 | 0.3333333333333333 |
| heldout-plan-technical | heldout | 3/3 | 3/3 | same | 0.0 | 0.0 |
| plan-forwarding | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| plan-location | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| plan-password | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| plan-plain-en | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| plan-plain-ko | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| plan-schedule | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| plan-statement | dev | 3/3 | 2/3 | uncertain-regressed | 1.0 | -0.33333333333333337 |
| plan-subscription | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |
| plan-technical | dev | 3/3 | 3/3 | same | 0.0 | 0.0 |

| Set / case | Split | Selection reasons |
|---|---|---|
| judgments/escalation-en | dev | role-orchestration_escalation: This instruction or proposition is exercised by the orchestration_escalation judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/escalation-ko | dev | role-orchestration_escalation: This instruction or proposition is exercised by the orchestration_escalation judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-curiosity | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-curiosity-ko | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-failed | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-failed-ko | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-legitimate-question | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-legitimate-question-ko | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-partial | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-partial-ko | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-repeat-question | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-repeat-question-ko | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-served | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-served-ko | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-unknown | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-unknown-ko | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-unsupported-figure | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/goal-unsupported-figure-ko | dev | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-escalation | heldout | role-orchestration_escalation: This instruction or proposition is exercised by the orchestration_escalation judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-goal-casual-reply | heldout | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-goal-failed | heldout | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-goal-legitimate-question | heldout | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-goal-partial | heldout | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-goal-repeat-question | heldout | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-goal-served | heldout | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-goal-unhelpful-curiosity | heldout | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-goal-unknown | heldout | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-goal-unsupported-figure | heldout | role-goal_reached: This instruction or proposition is exercised by the goal_reached judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md; paths=src/personal_agent/prompts/goal_reached.md); worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-plan-account | heldout | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-plan-plain | heldout | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-plan-situation | heldout | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-plan-statement | heldout | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/heldout-plan-technical | heldout | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/plan-forwarding | dev | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/plan-location | dev | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/plan-password | dev | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/plan-plain-en | dev | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/plan-plain-ko | dev | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/plan-schedule | dev | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/plan-statement | dev | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/plan-subscription | dev | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| judgments/plan-technical | dev | role-orchestration_plan: This instruction or proposition is exercised by the orchestration_plan judgment cases. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/allergy-then-meal-plan | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/already-ate-dinner-next-meal | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/already-ate-lunch | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/correct-earlier-advice-hiking | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/food-on-route-budget-correction | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/food-on-route-rest-area | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/line-add-watch-rule | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/line-cancel-seat-watch | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/line-cancel-watch | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/line-change-stock-time | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/line-change-watch-time | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/line-evening-watch | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/line-past-submission-deadline | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/line-status-before-first-run | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/new-request-after-lunch-topic | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/new-request-after-travel-topic | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/preference-then-recommendation | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/presence-chat-lookup-with-a-judgment | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/presence-chat-research-takes-a-while | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/presence-chat-short-reply-stays-short | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-binding-language | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-clay-language | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-correct-printer | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-correct-size | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-dated-committee | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-dated-studio | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-expired-pass | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-forget-project | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-forget-route | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-hall-again | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-irrelevant-music | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-parcel-again | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-quiet-desk | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/recall-reading-format | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/relative-time-reminder | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/settings-change-with-confirmation | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/settings-read-in-conversation | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/shopping-followup-constraint | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/shopping-lookup-compare | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/skill-add-reference-by-name | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/standing-wish-becomes-watch | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/standing-wish-is-not-a-memory | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/tee-time-departure-reminder | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/tee-time-reminder | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/travel-time-after-event | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/travel-time-from-earlier-place | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/travel-time-with-clock-note | heldout | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/weekday-lunch-near-workplace | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/workplace-statement-remember | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/world-appworld-note-update | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/world-appworld-pay-approved | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/world-appworld-pay-denied | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |
| scenarios/world-appworld-send-text | dev | worker-instructions: Worker instructions can change final answers and outcome/redelegation judgments across synthetic scenarios. (paths=src/personal_agent/prompts/ask_or_assume.md) |

Unmatched change signals: []

Selected but not run, count by set: {"scenarios": 53}

<details>
<summary>Selected cases requiring their own runners</summary>

| Set | Case | Split | Reason |
|---|---|---|---|
| scenarios | allergy-then-meal-plan | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | already-ate-dinner-next-meal | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | already-ate-lunch | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | correct-earlier-advice-hiking | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | food-on-route-budget-correction | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | food-on-route-rest-area | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | line-add-watch-rule | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | line-cancel-seat-watch | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | line-cancel-watch | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | line-change-stock-time | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | line-change-watch-time | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | line-evening-watch | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | line-past-submission-deadline | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | line-status-before-first-run | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | new-request-after-lunch-topic | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | new-request-after-travel-topic | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | preference-then-recommendation | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | presence-chat-lookup-with-a-judgment | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | presence-chat-research-takes-a-while | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | presence-chat-short-reply-stays-short | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-binding-language | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-clay-language | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-correct-printer | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-correct-size | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-dated-committee | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-dated-studio | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-expired-pass | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-forget-project | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-forget-route | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-hall-again | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-irrelevant-music | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-parcel-again | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-quiet-desk | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | recall-reading-format | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | relative-time-reminder | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | settings-change-with-confirmation | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | settings-read-in-conversation | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | shopping-followup-constraint | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | shopping-lookup-compare | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | skill-add-reference-by-name | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | standing-wish-becomes-watch | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | standing-wish-is-not-a-memory | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | tee-time-departure-reminder | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | tee-time-reminder | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | travel-time-after-event | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | travel-time-from-earlier-place | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | travel-time-with-clock-note | heldout | no runner for this set in paired.py; run it with its own runner |
| scenarios | weekday-lunch-near-workplace | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | workplace-statement-remember | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | world-appworld-note-update | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | world-appworld-pay-approved | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | world-appworld-pay-denied | dev | no runner for this set in paired.py; run it with its own runner |
| scenarios | world-appworld-send-text | dev | no runner for this set in paired.py; run it with its own runner |

</details>

Rollback (not executed): git revert --no-edit bca55624ead33c74e023951378a07b2d739bb44c..c3876f67c87acd2f58480a496f15cfce890bfe23

Re-run on the reverted state (not executed): python -m agentos_eval.paired --baseline c3876f67c87acd2f58480a496f15cfce890bfe23 --candidate '&lt;REVERT_SHA&gt;' --cases @evals/records/855-752/selection.json --seed synthetic-terra --model gpt-5.6-terra --repetitions 3 --max-calls 166 --requalify-attempts 2 --out paired-revert --live --reuse-arm baseline=evals/records/855-752/candidate-arm-report.json

Evidence applies only to the named route/model and class. Fixture, actual-CLI synthetic,
and owner-live observations are separate; unobserved classes are not claimed.
Subscription usage reported as unavailable is not a zero-cost claim.
