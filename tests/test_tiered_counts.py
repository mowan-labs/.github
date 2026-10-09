"""Unit tests for the tiered-mode additions to ai_review.py: severity counts
and fail-closed counts. Stdlib only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions", "ai-review"))
import ai_review  # noqa: E402

from test_ai_review import run_main, GOOD  # reuse the fake-kiro harness


class CountFindings(unittest.TestCase):
    def test_tallies_by_severity(self):
        findings = [
            {"severity": "high"}, {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"}, {"severity": "low"}, {"severity": "low"},
        ]
        self.assertEqual(ai_review.count_findings(findings),
                         {"high": 2, "medium": 1, "low": 3})

    def test_unknown_severity_ignored(self):
        self.assertEqual(ai_review.count_findings([{"severity": "trivial"}, {}]),
                         {"high": 0, "medium": 0, "low": 0})

    def test_case_insensitive(self):
        self.assertEqual(ai_review.count_findings([{"severity": "HIGH"}])["high"], 1)

    def test_empty(self):
        self.assertEqual(ai_review.count_findings(None),
                         {"high": 0, "medium": 0, "low": 0})


class CountsInVerdictJson(unittest.TestCase):
    def test_success_path_writes_counts(self):
        out = ('{"verdict": "REQUEST_CHANGES", "summary": "x", "findings": '
               '[{"severity": "high"}, {"severity": "medium"}, {"severity": "low"}]}')
        r = run_main("+x\n", {"KIRO_API_KEY": "k"}, fake_stdout=out)
        self.assertEqual(r["counts"], {"high": 1, "medium": 1, "low": 1})

    def test_empty_diff_counts_all_zero(self):
        r = run_main("  \n", {"KIRO_API_KEY": "k"})
        self.assertEqual(r["counts"], {"high": 0, "medium": 0, "low": 0})

    def test_missing_key_fails_closed_one_high(self):
        r = run_main("+x\n", {})
        self.assertEqual(r["counts"], ai_review.FAIL_COUNTS)
        self.assertEqual(r["counts"]["high"], 1)

    def test_kiro_error_fails_closed_one_high(self):
        r = run_main("+x\n", {"KIRO_API_KEY": "k"}, fake_stdout=GOOD, fake_exit=1)
        self.assertEqual(r["counts"]["high"], 1)

    def test_oversize_diff_fails_closed(self):
        r = run_main("x" * (ai_review.MAX_DIFF_CHARS + 1), {"KIRO_API_KEY": "k"})
        self.assertEqual(r["counts"]["high"], 1)

    def test_unparseable_fails_closed(self):
        r = run_main("+x\n", {"KIRO_API_KEY": "k"}, fake_stdout="LGTM, no json")
        self.assertEqual(r["counts"]["high"], 1)

    def test_legacy_consumer_still_sees_verdict_and_body(self):
        # counts is additive; .verdict and .body remain for legacy jq consumers.
        r = run_main("  \n", {"KIRO_API_KEY": "k"})
        self.assertIn("verdict", r)
        self.assertIn("body", r)


if __name__ == "__main__":
    unittest.main()
