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




MCS_POLICY = json.dumps({
    "version": 1,
    "high": {"paths": [".kiro/agents/**", "infra/**", "docs/adr/**"],
             "deleted_paths": ["**/tests/**"]},
    "medium": {"paths": ["pipeline/**"], "max_changed_lines": 300},
})


class DocumentationIgnored(unittest.TestCase):
    """Only changes that can alter system behaviour count toward the tier."""

    def test_adr_only_pr_is_low_even_if_policy_lists_docs_high(self):
        # PR #17's shape: a new ADR plus a design.md line.
        r = classify.classify(MCS_POLICY,
                              ["docs/adr/0013-ontology.md", "docs/design.md"], [],
                              {"docs/adr/0013-ontology.md": 181, "docs/design.md": 1})
        self.assertEqual(r["tier"], "low")
        self.assertIn("documentation only", r["reasons"])

    def test_readme_inside_high_dir_is_ignored(self):
        r = classify.classify(MCS_POLICY, ["infra/README.md"], [], {"infra/README.md": 4})
        self.assertEqual(r["tier"], "low")

    def test_code_next_to_docs_still_classified(self):
        r = classify.classify(MCS_POLICY, ["docs/adr/0012.md", "infra/lib/config.ts"], [],
                              {"docs/adr/0012.md": 50, "infra/lib/config.ts": 2})
        self.assertEqual(r["tier"], "high")
        self.assertTrue(any("infra/lib/config.ts" in x for x in r["reasons"]))
        self.assertFalse(any("high path docs/" in x for x in r["reasons"]))

    def test_doc_lines_do_not_count_toward_line_budget(self):
        r = classify.classify(MCS_POLICY, ["docs/big.md", "src/app.py"], [],
                              {"docs/big.md": 900, "src/app.py": 10})
        self.assertEqual(r["tier"], "low")

    def test_code_lines_still_count_toward_line_budget(self):
        r = classify.classify(MCS_POLICY, ["docs/big.md", "src/app.py"], [],
                              {"docs/big.md": 900, "src/app.py": 301})
        self.assertEqual(r["tier"], "medium")

    def test_int_line_total_keeps_legacy_behaviour(self):
        r = classify.classify(MCS_POLICY, ["src/app.py", "README.md"], [], 301)
        self.assertEqual(r["tier"], "medium")

    def test_agent_prompt_markdown_is_behaviour(self):
        r = classify.classify(MCS_POLICY, [".kiro/agents/prompts/engineer.md"], [], 3)
        self.assertEqual(r["tier"], "high")

    def test_skill_and_agents_md_are_behaviour(self):
        for p in ["skills/x/SKILL.md", "AGENTS.md", "pkg/CLAUDE.md"]:
            self.assertFalse(classify.is_non_behavioral(p, {}), p)

    def test_github_markdown_stays_high(self):
        r = classify.classify(MCS_POLICY, [".github/PULL_REQUEST_TEMPLATE.md"], [], 1)
        self.assertEqual(r["tier"], "high")

    def test_deleted_doc_under_tests_is_ignored(self):
        r = classify.classify(MCS_POLICY, [], ["pipeline/tests/README.md"], 0)
        self.assertEqual(r["tier"], "low")

    def test_deleted_test_code_still_high(self):
        r = classify.classify(MCS_POLICY, ["docs/x.md"], ["pipeline/tests/test_a.py"], 5)
        self.assertEqual(r["tier"], "high")

    def test_requirements_txt_is_not_documentation(self):
        self.assertFalse(classify.is_non_behavioral("pipeline/requirements.txt", {}))

    def test_policy_can_override_lists(self):
        pol = json.loads(MCS_POLICY)
        pol["non_behavioral"] = {"paths": ["**/*.md"], "except": ["docs/adr/**"]}
        r = classify.classify(json.dumps(pol), ["docs/adr/1.md"], [], 1)
        self.assertEqual(r["tier"], "high")

    def test_invalid_non_behavioral_shape_is_medium(self):
        pol = json.loads(MCS_POLICY)
        pol["non_behavioral"] = ["docs/**"]
        r = classify.classify(json.dumps(pol), ["docs/a.md"], [], 1)
        self.assertEqual(r["tier"], "medium")


class ParseNumstatByPath(unittest.TestCase):
    def test_plain_binary_and_rename(self):
        text = "10\t5\ta.py\0-\t-\timg.png\0" "3\t1\t\0docs/old.md\0docs/new.md\0"
        self.assertEqual(classify.parse_numstat_by_path(text),
                         {"a.py": 15, "img.png": 0, "docs/new.md": 4})


if __name__ == "__main__":
    unittest.main()
