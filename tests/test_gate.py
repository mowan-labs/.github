"""Unit tests for actions/ai-gate/gate.py decide(). Stdlib only.

Walks the decision ladder from spec.md section 4.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions", "ai-gate"))
import gate  # noqa: E402

CLEAN = {"high": 0, "medium": 0, "low": 0}


def d(tier="low", tests_ok=True, counts=None, human_approved=False,
      human_changes_requested=False, unresolved_human_threads=0,
      fix_rounds=0, fix_round_cap=3, automerge_enabled=True,
      touches_github=False, approvers=None):
    return gate.decide(tier, tests_ok, counts or dict(CLEAN), human_approved,
                       human_changes_requested, unresolved_human_threads,
                       fix_rounds, fix_round_cap, automerge_enabled,
                       touches_github, approvers)


class Ladder(unittest.TestCase):
    def test_tests_unknown_waits(self):
        r = d(tests_ok=None)
        self.assertEqual(r["decision"], "wait")
        self.assertIn("tests not recorded", r["reasons"])

    def test_low_clean_merges(self):
        self.assertEqual(d(tier="low")["decision"], "merge")

    def test_low_escalates_to_medium_on_high_finding(self):
        r = d(tier="low", counts={"high": 1, "medium": 0, "low": 0})
        self.assertEqual(r["effective_tier"], "medium")
        # medium review_ok requires high==0 and medium==0 -> blocks
        self.assertEqual(r["decision"], "block")

    def test_tests_failing_blocks(self):
        r = d(tier="low", tests_ok=False)
        self.assertEqual(r["decision"], "block")
        self.assertTrue(any("tests failing" in x for x in r["reasons"]))

    def test_medium_blocks_on_medium_finding(self):
        r = d(tier="medium", counts={"high": 0, "medium": 2, "low": 0})
        self.assertEqual(r["decision"], "block")

    def test_medium_clean_merges(self):
        self.assertEqual(d(tier="medium")["decision"], "merge")

    def test_human_changes_requested_blocks(self):
        r = d(tier="low", human_changes_requested=True)
        self.assertEqual(r["decision"], "block")
        self.assertTrue(any("requested changes" in x for x in r["reasons"]))

    def test_unresolved_threads_block(self):
        r = d(tier="low", unresolved_human_threads=2)
        self.assertEqual(r["decision"], "block")
        self.assertTrue(any("unresolved" in x for x in r["reasons"]))

    def test_fix_cap_reached_needs_human(self):
        r = d(tier="medium", counts={"high": 1, "medium": 0, "low": 0},
              fix_rounds=3, fix_round_cap=3)
        self.assertEqual(r["decision"], "needs-human")
        self.assertTrue(any("cap reached" in x for x in r["reasons"]))

    def test_high_needs_approval_waits(self):
        r = d(tier="high", human_approved=False, approvers=["xysr89"])
        self.assertEqual(r["decision"], "wait")
        self.assertTrue(any("needs approval from xysr89" in x for x in r["reasons"]))

    def test_high_approved_merges(self):
        r = d(tier="high", human_approved=True)
        self.assertEqual(r["decision"], "merge")

    def test_touches_github_needs_human(self):
        r = d(tier="low", touches_github=True)
        self.assertEqual(r["decision"], "needs-human")
        self.assertTrue(any(".github/" in x for x in r["reasons"]))

    def test_shadow_when_automerge_disabled(self):
        r = d(tier="low", automerge_enabled=False)
        self.assertEqual(r["decision"], "shadow")
        self.assertTrue(any("AI_AUTOMERGE=false" in x for x in r["reasons"]))

    def test_order_block_before_approval(self):
        # a failing high PR blocks (step 4) before the approval check (step 5)
        r = d(tier="high", counts={"high": 1, "medium": 0, "low": 0},
              human_approved=False)
        self.assertEqual(r["decision"], "block")

    def test_order_approval_before_github(self):
        # high + touches_github + not approved -> wait (step 5 before step 6)
        r = d(tier="high", touches_github=True, human_approved=False)
        self.assertEqual(r["decision"], "wait")

    def test_github_before_shadow(self):
        r = d(tier="low", touches_github=True, automerge_enabled=False)
        self.assertEqual(r["decision"], "needs-human")


class CLIHelpers(unittest.TestCase):
    def test_tests_ok_coercion(self):
        self.assertIsNone(gate._to_tests_ok(""))
        self.assertIsNone(gate._to_tests_ok(None))
        self.assertIsNone(gate._to_tests_ok("unknown"))
        self.assertTrue(gate._to_tests_ok("success"))
        self.assertFalse(gate._to_tests_ok("failure"))
        self.assertFalse(gate._to_tests_ok("skipped"))
        self.assertTrue(gate._to_tests_ok(True))
        self.assertFalse(gate._to_tests_ok(False))


class CountsFromStatus(unittest.TestCase):
    FAIL = {"high": 1, "medium": 0, "low": 0}

    def test_missing_fails_closed(self):
        r = gate.counts_from_status(None)
        self.assertEqual(r["counts"], self.FAIL)
        self.assertEqual(r["reason"], gate.REVIEW_NOT_RECORDED)

    def test_empty_fails_closed(self):
        r = gate.counts_from_status("")
        self.assertEqual(r["counts"], self.FAIL)
        self.assertEqual(r["reason"], gate.REVIEW_NOT_RECORDED)

    def test_garbage_fails_closed(self):
        for bad in ("totally unparseable", "high= medium= low=",
                    "high=1 medium=2", "reviewer crashed"):
            r = gate.counts_from_status(bad)
            self.assertEqual(r["counts"], self.FAIL, bad)
            self.assertEqual(r["reason"], gate.REVIEW_NOT_RECORDED, bad)

    def test_valid_parses_and_no_reason(self):
        r = gate.counts_from_status("high=2 medium=3 low=5")
        self.assertEqual(r["counts"], {"high": 2, "medium": 3, "low": 5})
        self.assertIsNone(r["reason"])

    def test_valid_clean(self):
        r = gate.counts_from_status("high=0 medium=0 low=0")
        self.assertEqual(r["counts"], {"high": 0, "medium": 0, "low": 0})
        self.assertIsNone(r["reason"])

    def test_valid_embedded_in_marker(self):
        r = gate.counts_from_status("review done high=1 medium=0 low=4 ok")
        self.assertEqual(r["counts"], {"high": 1, "medium": 0, "low": 4})
        self.assertIsNone(r["reason"])

    def test_missing_review_fails_closed_through_decide(self):
        # A low-tier PR with no recorded review must NOT merge: fail-closed
        # counts escalate it and the reason reaches the gate reasons.
        parsed = gate.counts_from_status(None)
        r = gate.decide("low", True, parsed["counts"], False, False, 0, 0, 3,
                        True, False, review_reason=parsed["reason"])
        self.assertEqual(r["decision"], "block")
        self.assertIn(gate.REVIEW_NOT_RECORDED, r["reasons"])


if __name__ == "__main__":
    unittest.main()
