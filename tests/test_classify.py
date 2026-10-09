"""Unit tests for actions/risk-classify/classify.py. Stdlib only:
python3 -m unittest discover -s tests

Covers the glob matcher, numstat/name-status parsing, and the full tier
classification table from spec.md section 1 with representative paths.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions", "risk-classify"))
import classify  # noqa: E402

POLICY = json.dumps({
    "version": 1,
    "high": {"paths": ["infra/**"], "deleted_paths": ["**/tests/**"]},
    "medium": {"paths": ["pipeline/**"], "max_changed_lines": 300},
})


class Glob(unittest.TestCase):
    def test_double_star_spans_directories_including_zero(self):
        self.assertTrue(classify.match_glob("infra/**", "infra/main.tf"))
        self.assertTrue(classify.match_glob("infra/**", "infra/a/b/c.tf"))
        # ** matches zero segments: pattern a/**/b matches a/b
        self.assertTrue(classify.match_glob("a/**/b", "a/b"))
        self.assertTrue(classify.match_glob("a/**/b", "a/x/y/b"))

    def test_leading_double_star(self):
        self.assertTrue(classify.match_glob("**/tests/**", "src/tests/t.py"))
        self.assertTrue(classify.match_glob("**/tests/**", "tests/t.py"))
        self.assertFalse(classify.match_glob("**/tests/**", "src/testing/t.py"))

    def test_single_star_stays_in_segment(self):
        self.assertTrue(classify.match_glob("src/*.py", "src/a.py"))
        self.assertFalse(classify.match_glob("src/*.py", "src/sub/a.py"))

    def test_question_mark_one_char_in_segment(self):
        self.assertTrue(classify.match_glob("f?o", "foo"))
        self.assertFalse(classify.match_glob("f?o", "fo"))
        self.assertFalse(classify.match_glob("a?b", "a/b"))

    def test_anchored_both_ends(self):
        self.assertFalse(classify.match_glob("infra", "infra/x"))
        self.assertTrue(classify.match_glob("infra", "infra"))


class ParseNameStatus(unittest.TestCase):
    def test_rename_counts_both_old_and_new(self):
        changed, deleted = classify.parse_name_status("R100\told/path.py\tnew/path.py")
        self.assertEqual(changed, ["new/path.py"])
        self.assertEqual(deleted, ["old/path.py"])

    def test_delete_and_add_and_modify(self):
        text = "A\tadded.py\nM\tmod.py\nD\tgone.py"
        changed, deleted = classify.parse_name_status(text)
        self.assertEqual(sorted(changed), ["added.py", "mod.py"])
        self.assertEqual(deleted, ["gone.py"])


class ParseNumstat(unittest.TestCase):
    def test_sum_added_deleted(self):
        self.assertEqual(classify.parse_numstat("10\t5\ta.py\n3\t2\tb.py"), 20)

    def test_binary_counts_zero(self):
        self.assertEqual(classify.parse_numstat("-\t-\timg.png\n4\t1\ta.py"), 5)


class Classify(unittest.TestCase):
    def test_github_is_hard_floor_high_even_without_policy(self):
        r = classify.classify(None, [".github/workflows/x.yml"], [], 0)
        self.assertEqual(r["tier"], "high")
        self.assertTrue(any(".github/" in x for x in r["reasons"]))

    def test_github_beats_everything(self):
        # even with a valid policy and a tiny diff, .github/ wins
        r = classify.classify(POLICY, [".github/risk-policy.json", "README.md"], [], 1)
        self.assertEqual(r["tier"], "high")

    def test_missing_policy_is_medium(self):
        r = classify.classify(None, ["src/app.py"], [], 10)
        self.assertEqual(r["tier"], "medium")
        self.assertIn("policy missing or invalid", r["reasons"])

    def test_invalid_json_is_medium(self):
        r = classify.classify("{not json", ["src/app.py"], [], 10)
        self.assertEqual(r["tier"], "medium")
        self.assertIn("policy missing or invalid", r["reasons"])

    def test_wrong_schema_is_medium(self):
        bad = json.dumps({"high": {"paths": "infra/**"}})  # paths must be a list
        r = classify.classify(bad, ["src/app.py"], [], 10)
        self.assertEqual(r["tier"], "medium")
        self.assertIn("policy missing or invalid", r["reasons"])

    def test_high_path_match(self):
        r = classify.classify(POLICY, ["infra/main.tf"], [], 5)
        self.assertEqual(r["tier"], "high")
        self.assertTrue(any("infra/main.tf" in x for x in r["reasons"]))

    def test_high_deleted_path_match(self):
        r = classify.classify(POLICY, ["src/app.py"], ["src/tests/test_app.py"], 5)
        self.assertEqual(r["tier"], "high")
        self.assertTrue(any("src/tests/test_app.py" in x for x in r["reasons"]))

    def test_medium_path_match(self):
        r = classify.classify(POLICY, ["pipeline/deploy.yml"], [], 5)
        self.assertEqual(r["tier"], "medium")

    def test_medium_line_budget_exceeded(self):
        r = classify.classify(POLICY, ["src/app.py"], [], 301)
        self.assertEqual(r["tier"], "medium")
        self.assertTrue(any("301" in x for x in r["reasons"]))

    def test_line_budget_boundary_is_low(self):
        r = classify.classify(POLICY, ["src/app.py"], [], 300)
        self.assertEqual(r["tier"], "low")

    def test_low_default(self):
        r = classify.classify(POLICY, ["src/app.py", "README.md"], [], 42)
        self.assertEqual(r["tier"], "low")

    def test_high_wins_over_medium(self):
        r = classify.classify(POLICY, ["infra/main.tf", "pipeline/x.yml"], [], 500)
        self.assertEqual(r["tier"], "high")


if __name__ == "__main__":
    unittest.main()
