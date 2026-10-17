import copy
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_master_plan_docs", ROOT / "scripts" / "verify_master_plan_docs.py"
)
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def plan():
    return json.loads((ROOT / "delivery-plan.yaml").read_text(encoding="utf-8"))


def test_every_master_plan_design_has_a_named_implementation_contract_and_evidence():
    VERIFIER.verify_traceability(plan())


def test_historical_korean_reference_needs_canonical_link_but_not_heading_parity():
    with tempfile.TemporaryDirectory() as directory:
        documents = Path(directory)
        (documents / "canonical.en.md").write_text("# Canonical\n## Boundary\n")
        (documents / "reference.ko.md").write_text(
            "# 참고\n\n[영어 원본](canonical.en.md)\n\n## 하나\n## 둘\n"
        )
        VERIFIER.verify_document_references(
            documents, (("reference.ko.md", "canonical.en.md"),)
        )

        (documents / "reference.ko.md").write_text("# 참고\n## 추가 참고\n")
        with pytest.raises(SystemExit, match="lacks English canonical link"):
            VERIFIER.verify_document_references(
                documents, (("reference.ko.md", "canonical.en.md"),)
            )


def test_reference_registry_rejects_an_omitted_pair_and_its_missing_link():
    with tempfile.TemporaryDirectory() as directory:
        documents = Path(directory)
        for stem in ("registered", "omitted"):
            (documents / f"{stem}.en.md").write_text("# Canonical\n")
            (documents / f"{stem}.ko.md").write_text(
                f"# 참고\n\n[영어 원본]({stem}.en.md)\n"
            )
        registered = (("registered.ko.md", "registered.en.md"),)
        with pytest.raises(SystemExit, match="does not cover every eligible internal pair"):
            VERIFIER.verify_reference_registry(documents, registered, exclusions=())
        (documents / "omitted.ko.md").write_text("# 참고\n")
        with pytest.raises(SystemExit, match="lacks English canonical link"):
            VERIFIER.verify_document_references(
                documents, (("omitted.ko.md", "omitted.en.md"),)
            )


def test_design_only_work_cannot_be_promoted_to_development_complete():
    incomplete = copy.deepcopy(plan())
    incomplete["history"]["documented_completed_iterations"].remove("I-MP2-03")
    incomplete["completion_claims"]["MP2"]["status"] = "development_complete"
    with pytest.raises(SystemExit, match="completion claim lacks implemented automated evidence: MP2"):
        VERIFIER.verify_traceability(incomplete)


def complete_top_claim():
    result = plan()
    completed = result["history"]["documented_completed_iterations"]
    for ident in ("TOP-02", "TOP-03"):
        if ident not in completed:
            completed.append(ident)
    result["completion_claims"]["TOP"] = {
        "status": "development_complete",
        "required_implementation_ids": ["TOP-00", "TOP-01", "TOP-02", "TOP-03"],
        "implementation_requirement_ids": ["CORE-01", "CORE-02", "MP2-01", "STAB-01", "DEPLOY-01", "GOV-01", "STATUS-01"],
        "owner_operating_requirement_ids": ["OWNER-01"],
        "separate_owner_product_decision_ids": ["LEGACY-01"],
        "requirements": {
            "CORE-01": {"status": "merged-current-evidence", "evidence": ["PR #1"]},
            "CORE-02": {"status": "merged-current-evidence", "evidence": ["PR #2"]},
            "MP2-01": {"status": "merged-current-evidence", "evidence": ["PR #3"]},
            "STAB-01": {"status": "merged-current-evidence", "evidence": ["PR #4"]},
            "DEPLOY-01": {"status": "merged-current-evidence", "evidence": ["PR #5"]},
            "GOV-01": {"status": "merged-current-evidence", "evidence": ["PR #6"]},
            "STATUS-01": {"status": "merged-current-evidence", "evidence": ["PR #7"]},
            "OWNER-01": {"status": "owner-operating-action", "action": "configure local credentials"},
            "LEGACY-01": {"status": "separate-owner-product-decision", "decision": "resolve the archived prototype conflict"},
        },
        "deployment_candidate": {"commit": "a" * 40, "required_ci": [{"name": "validate", "conclusion": "success", "commit": "a" * 40}]},
        "unresolved_review_findings": [],
    }
    return result


def test_top_claim_rejects_substep_only_completion():
    incomplete = complete_top_claim()
    incomplete["completion_claims"]["TOP"]["required_implementation_ids"] = ["TOP-00"]
    with pytest.raises(SystemExit, match="TOP completion claim lacks every ordered substep"):
        VERIFIER.verify_traceability(incomplete)


def test_top_claim_rejects_a_substep_without_an_iteration_record():
    incomplete = complete_top_claim()
    incomplete["iterations"] = [item for item in incomplete["iterations"] if item["id"] != "TOP-03"]
    with pytest.raises(SystemExit, match="TOP completion claim lacks every ordered substep"):
        VERIFIER.verify_traceability(incomplete)


def test_top_claim_rejects_an_implementation_gap_as_owner_setup():
    incomplete = complete_top_claim()
    claim = incomplete["completion_claims"]["TOP"]
    claim["implementation_requirement_ids"].remove("DEPLOY-01")
    claim["owner_operating_requirement_ids"].append("DEPLOY-01")
    claim["requirements"]["DEPLOY-01"] = {"status": "owner-operating-action", "action": "wrong"}
    with pytest.raises(SystemExit, match="TOP completion claim misclassifies a requirement"):
        VERIFIER.verify_traceability(incomplete)


def test_top_claim_rejects_labels_without_candidate_or_evidence():
    incomplete = complete_top_claim()
    incomplete["completion_claims"]["TOP"]["requirements"]["CORE-01"] = "merged-current-evidence"
    with pytest.raises(SystemExit, match="TOP implementation requirement is not evidenced: CORE-01"):
        VERIFIER.verify_traceability(incomplete)


def test_top_claim_rejects_empty_evidence_or_ci_for_a_different_candidate():
    incomplete = complete_top_claim()
    incomplete["completion_claims"]["TOP"]["requirements"]["CORE-01"]["evidence"] = [None]
    with pytest.raises(SystemExit, match="TOP implementation requirement is not evidenced: CORE-01"):
        VERIFIER.verify_traceability(incomplete)


def test_top_claim_rejects_a_missing_legacy_product_decision():
    incomplete = complete_top_claim()
    claim = incomplete["completion_claims"]["TOP"]
    claim["separate_owner_product_decision_ids"] = []
    claim["requirements"].pop("LEGACY-01")
    with pytest.raises(SystemExit, match="TOP completion claim misclassifies a requirement"):
        VERIFIER.verify_traceability(incomplete)
    incomplete = complete_top_claim()
    incomplete["completion_claims"]["TOP"]["deployment_candidate"]["required_ci"][0]["commit"] = "b" * 40
    with pytest.raises(SystemExit, match="TOP completion claim lacks current verification evidence"):
        VERIFIER.verify_traceability(incomplete)
