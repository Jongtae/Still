import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.verify_reuse_review import ReuseReviewError, main, validate_pr_body


def body(section):
    return "## User outcome\n\nAnything\n\n## Existing solutions review (Adopt / Adapt / Build)\n\n" + section + "\n\n## Validation\n"


VALID_ADAPT = """- problem/boundary: OAuth URL construction
- internal repository candidates: shared oauth helper in src/personal_agent/oauth.py
- search evidence (paths/symbols/docs checked): searched oauth, PKCE, authorization_url in src/ and tests/
- standard-library/platform candidates: urllib.parse covers encoding but not the protocol contract
- official SDK/reference/standard candidates: RFC 6749/7636 and provider docs
- mature open-source/framework candidates: oauthlib
- maintenance/security/supply-chain fit: maintained and reviewed
- licence fit: BSD-3-Clause compatible
- runtime/deployment/compatibility fit: pure Python and supported on the current runtime
- decision: `Adapt`
- why rejected candidates are insufficient: internal helper lacks PKCE protocol construction
- Still-owned policy/authority boundary kept outside the dependency: scope and secret policy remain in Still
- N/A reason:
"""


class ReuseReviewGateTests(unittest.TestCase):
    def test_accepts_complete_adapt_review(self):
        validate_pr_body(body(VALID_ADAPT))

    def test_accepts_na_with_concrete_reason(self):
        validate_pr_body(body("""- decision: `N/A`
- N/A reason: bug fix changes one condition and introduces no new component, abstraction, integration, dependency, or framework
"""))

    def test_rejects_na_without_reason(self):
        with self.assertRaisesRegex(ReuseReviewError, "concrete reason"):
            validate_pr_body(body("- decision: `N/A`\n- N/A reason:\n"))

    def test_rejects_missing_internal_search_record(self):
        section = VALID_ADAPT.replace(
            "- internal repository candidates: shared oauth helper in src/personal_agent/oauth.py",
            "- internal repository candidates:",
        )
        with self.assertRaisesRegex(ReuseReviewError, "internal repository candidates"):
            validate_pr_body(body(section))

    def test_rejects_missing_section(self):
        with self.assertRaisesRegex(ReuseReviewError, "missing Existing solutions review"):
            validate_pr_body("## User outcome\n\nNo reuse section")

    def test_rejects_explanation_after_decision_word(self):
        section = VALID_ADAPT.replace("- decision: `Adapt`", "- decision: `Adapt` (reuses the shared helper)")
        with self.assertRaisesRegex(ReuseReviewError, "decision must be one of"):
            validate_pr_body(body(section))


class ReuseReviewCliTests(unittest.TestCase):
    def run_main(self, argv, env):
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            return main(argv), err.getvalue()

    def write_body(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "body.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_file_argument_accepts_valid_body(self):
        code, _ = self.run_main([self.write_body(body(VALID_ADAPT))], {})
        self.assertEqual(code, 0)

    def test_file_argument_applies_the_same_rules(self):
        code, err = self.run_main([self.write_body("## User outcome\n\nNo reuse section")], {})
        self.assertEqual(code, 1)
        self.assertIn("missing Existing solutions review", err)

    def test_file_argument_takes_precedence_over_pr_body(self):
        code, _ = self.run_main([self.write_body("no section")], {"PR_BODY": body(VALID_ADAPT)})
        self.assertEqual(code, 1)

    def test_unreadable_file_is_a_usage_error(self):
        code, err = self.run_main([str(Path(tempfile.gettempdir()) / "missing-still-pr-body.md")], {})
        self.assertEqual(code, 2)
        self.assertIn("cannot read PR body file", err)

    def test_pr_body_environment_path_is_unchanged(self):
        self.assertEqual(self.run_main([], {"PR_BODY": body(VALID_ADAPT)})[0], 0)
        self.assertEqual(self.run_main([], {})[0], 2)


if __name__ == "__main__":
    unittest.main()
