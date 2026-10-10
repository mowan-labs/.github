"""Linked-issue requirement: behavior changes need an issue, docs-only do not."""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "actions", "ai-gate"))
sys.path.insert(0, os.path.join(ROOT, "actions", "risk-classify"))
import classify  # noqa: E402
import gate  # noqa: E402

POLICY = '{"high": {"paths": ["infra/**"]}, "medium": {"paths": ["model/**"], "max_changed_lines": 300}}'
CLEAN = {"high": 0, "medium": 0, "low": 0}


def g(tier="low", issue_required=True, linked=None, human_approved=False, tests_ok=True,
      counts=None, automerge=True, touches_github=False):
    return gate.decide(tier, tests_ok, counts or dict(CLEAN), human_approved, False, 0, 0, 3,
                       automerge, touches_github, ["xysr89"],
                       issue_required=issue_required, linked_issues=linked)


class Behavioral(unittest.TestCase):
    def test_docs_only_is_not_behavioral(self):
        r = classify.classify(POLICY, ["docs/adr/0013-x.md", "README.md"], [], {"docs/adr/0013-x.md": 181})
        self.assertEqual(r["tier"], "low")
        self.assertIs(r["behavioral"], False)

    def test_code_change_is_behavioral(self):
        for paths in (["src/app.py"], ["model/train.py"], ["infra/lib/config.ts"], ["docs/x.md", "src/a.py"]):
            r = classify.classify(POLICY, paths, [], 5)
            self.assertIs(r["behavioral"], True, paths)

    def test_github_and_invalid_policy_fail_closed_to_behavioral(self):
        self.assertIs(classify.classify(POLICY, [".github/x.md"], [], 1)["behavioral"], True)
        self.assertIs(classify.classify(None, ["README.md"], [], 1)["behavioral"], True)

    def test_agent_instruction_markdown_is_behavioral(self):
        self.assertIs(classify.classify(POLICY, ["AGENTS.md"], [], 1)["behavioral"], True)

    def test_internal_key_not_leaked(self):
        r = classify.classify(POLICY, ["README.md"], [], 1)
        self.assertNotIn("_behavioral", r)


class GateLinkedIssue(unittest.TestCase):
    def test_behavioral_without_issue_waits(self):
        r = g(issue_required=True, linked=[])
        self.assertEqual(r["decision"], "wait")
        self.assertIn(gate.LINK_ISSUE_REASON, r["reasons"])

    def test_behavioral_with_issue_merges(self):
        self.assertEqual(g(issue_required=True, linked=[13])["decision"], "merge")

    def test_docs_only_without_issue_merges(self):
        self.assertEqual(g(issue_required=False, linked=[])["decision"], "merge")

    def test_failing_tests_still_block_first(self):
        self.assertEqual(g(tests_ok=False, linked=[])["decision"], "block")

    def test_high_needs_issue_and_approval(self):
        self.assertEqual(g(tier="high", linked=[], human_approved=True)["decision"], "wait")
        r = g(tier="high", linked=[5], human_approved=False)
        self.assertEqual(r["decision"], "wait")
        self.assertNotIn(gate.LINK_ISSUE_REASON, r["reasons"])
        self.assertEqual(g(tier="high", linked=[5], human_approved=True)["decision"], "merge")

    def test_shadow_mode_still_reports_missing_issue(self):
        self.assertEqual(g(linked=[], automerge=False)["decision"], "wait")

    def test_default_args_keep_old_behaviour(self):
        r = gate.decide("low", True, dict(CLEAN), False, False, 0, 0, 3, True, False)
        self.assertEqual(r["decision"], "merge")


class BodyRefs(unittest.TestCase):
    def test_keywords(self):
        body = "Closes #13\nfixes #7, Resolves: #9\nsee #99\nCLOSED #13\nfixed #4"
        self.assertEqual(gate.issue_refs_from_body(body), [13, 7, 9, 4])

    def test_no_refs(self):
        self.assertEqual(gate.issue_refs_from_body(None), [])
        self.assertEqual(gate.issue_refs_from_body("refs #3, issue 12"), [])

    def test_cli(self):
        import json
        import subprocess
        out = subprocess.run([sys.executable, os.path.join(ROOT, "actions", "ai-gate", "gate.py"), "issue-refs"],
                             input="Closes #13", capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), [13])

    def test_facts_cli_passes_issue_fields(self):
        import json
        import subprocess
        facts = {"tier": "low", "tests_ok": True, "counts": CLEAN, "automerge_enabled": True,
                 "issue_required": True, "linked_issues": []}
        out = subprocess.run([sys.executable, os.path.join(ROOT, "actions", "ai-gate", "gate.py")],
                             input=json.dumps(facts), capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out)["decision"], "wait")


if __name__ == "__main__":
    unittest.main()
