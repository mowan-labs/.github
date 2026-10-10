"""Whole-PR LLM classifier: prompt, parsing, combine, fallback, reuse."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "actions", "risk-classify"))
import llm_classify as lc  # noqa: E402

SCRIPT = os.path.join(ROOT, "actions", "risk-classify", "llm_classify.py")
RULES_HIGH_INFRA = {"tier": "high", "behavioral": True,
                    "reasons": ["high path infra/lib/config.ts matched by infra/**"]}
RULES_GITHUB = {"tier": "high", "behavioral": True,
                "reasons": ["changed path under .github/: .github/workflows/x.yml"]}
RULES_DOCS = {"tier": "low", "behavioral": False, "reasons": ["documentation only"]}


def llm(kind, tier, summary="s", changes=None):
    return lc.validate({"kind": kind, "tier": tier, "summary": summary,
                        "changes": changes or []})


class Combine(unittest.TestCase):
    def test_llm_can_lower_a_path_rule_tier(self):
        r = lc.combine(RULES_HIGH_INFRA, llm("structural", "low"))
        self.assertEqual((r["tier"], r["kind"], r["behavioral"], r["source"]),
                         ("low", "structural", False, "llm"))

    def test_llm_can_raise_a_docs_tier(self):
        r = lc.combine(RULES_DOCS, llm("behavioral", "medium"))
        self.assertEqual(r["tier"], "medium")
        self.assertTrue(r["behavioral"])

    def test_deployed_state_is_behavioral_for_issue_rule(self):
        self.assertTrue(lc.combine(RULES_HIGH_INFRA, llm("deployed-state", "high"))["behavioral"])

    def test_docs_and_structural_need_no_issue(self):
        for k in ("docs", "structural"):
            self.assertFalse(lc.combine(RULES_DOCS, llm(k, "low"))["behavioral"])

    def test_github_floor_survives(self):
        r = lc.combine(RULES_GITHUB, llm("structural", "low"))
        self.assertEqual(r["tier"], "high")
        self.assertTrue(any("always high" in x for x in r["reasons"]))


class Validate(unittest.TestCase):
    def test_normalises_case(self):
        self.assertEqual(lc.validate({"kind": "Structural", "tier": "LOW"})["tier"], "low")

    def test_rejects_unknown(self):
        with self.assertRaises(ValueError):
            lc.validate({"kind": "refactor", "tier": "low"})
        with self.assertRaises(ValueError):
            lc.validate({"kind": "docs", "tier": "none"})

    def test_extract_takes_last_object(self):
        text = 'echo {"kind":"docs","tier":"low"} then\n\x1b[0m{"kind": "behavioral", "tier": "medium", "summary": "x"}'
        self.assertEqual(lc.extract_result(text)["kind"], "behavioral")

    def test_extract_without_json_raises(self):
        with self.assertRaises(ValueError):
            lc.extract_result("no json here")


class Prompt(unittest.TestCase):
    def test_contains_rubric_hints_guidance_and_diff(self):
        p = lc.build_prompt("+x = 1", RULES_HIGH_INFRA, "Nothing is deployed yet.")
        for needle in ("deployed-state", "PHYSICAL name", "UNTRUSTED", "tier=high",
                       "infra/**", "Nothing is deployed yet.", "<diff>\n+x = 1\n</diff>"):
            self.assertIn(needle, p)

    def test_guidance_from_policy(self):
        self.assertEqual(lc.guidance_from_policy('{"classifier": {"guidance": "g"}}'), "g")
        for bad in ("", "{not json", "[]", '{"classifier": "x"}'):
            self.assertEqual(lc.guidance_from_policy(bad), "")


class Fallback(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_no_key_uses_rules(self):
        os.environ.pop("KIRO_API_KEY", None)
        r = lc.classify_with_llm("+x", RULES_HIGH_INFRA)
        self.assertEqual((r["tier"], r["source"]), ("high", "rules"))
        self.assertIn("KIRO_API_KEY not set", r["reasons"][0])

    def test_oversized_diff_uses_rules(self):
        os.environ["KIRO_API_KEY"] = "k"
        r = lc.classify_with_llm("x" * (lc.MAX_DIFF_CHARS + 1), RULES_DOCS)
        self.assertEqual(r["source"], "rules")
        self.assertFalse(r["behavioral"])

    def test_rules_docs_maps_to_docs_kind(self):
        self.assertEqual(lc.fallback(RULES_DOCS, "x")["kind"], "docs")


class Reuse(unittest.TestCase):
    def test_parses_llm_status(self):
        r = lc.reuse_recorded("tier=high kind=deployed-state (llm)")
        self.assertEqual((r["tier"], r["kind"], r["behavioral"]), ("high", "deployed-state", True))

    def test_ignores_rules_and_legacy_statuses(self):
        for d in ("", "tier=low", "tier=low kind=docs (rules)", "tier=low kind=weird (llm)"):
            self.assertIsNone(lc.reuse_recorded(d), d)


class EndToEndWithFakeKiro(unittest.TestCase):
    """Runs the CLI with a fake kiro-cli on PATH."""

    def run_cli(self, fake_stdout, rc=0, policy='{"classifier": {"guidance": "ctx"}}'):
        with tempfile.TemporaryDirectory() as d:
            kiro = os.path.join(d, "kiro-cli")
            with open(kiro, "w") as fh:
                fh.write("#!/bin/sh\ncat > %s/prompt.txt\ncat <<'JSON'\n%s\nJSON\nexit %d\n"
                         % (d, fake_stdout, rc))
            os.chmod(kiro, os.stat(kiro).st_mode | stat.S_IEXEC)
            paths = {}
            for name, content in (("diff.patch", "+TABLE = 'b'\n"),
                                  ("rules.json", json.dumps(RULES_HIGH_INFRA)),
                                  ("policy.json", policy)):
                paths[name] = os.path.join(d, name)
                with open(paths[name], "w") as fh:
                    fh.write(content)
            out = os.path.join(d, "out.json")
            env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], KIRO_API_KEY="k")
            subprocess.run([sys.executable, SCRIPT, paths["diff.patch"], paths["rules.json"],
                            paths["policy.json"], out], env=env, check=True, capture_output=True)
            with open(os.path.join(d, "prompt.txt")) as fh:
                prompt = fh.read()
            with open(out) as fh:
                return json.load(fh), prompt

    def test_llm_verdict_used(self):
        r, prompt = self.run_cli('prose {"kind": "deployed-state", "tier": "high", "summary": "renames tableName", '
                                 '"changes": [{"path": "infra/lib/config.ts", "kind": "deployed-state", "why": "replacement"}]}')
        self.assertEqual((r["tier"], r["kind"], r["source"]), ("high", "deployed-state", "llm"))
        self.assertEqual(r["changes"][0]["path"], "infra/lib/config.ts")
        self.assertIn("ctx", prompt)
        self.assertIn("+TABLE = 'b'", prompt)

    def test_model_failure_falls_back(self):
        r, _ = self.run_cli("boom", rc=3)
        self.assertEqual((r["tier"], r["source"]), ("high", "rules"))
        self.assertIn("kiro-cli exited 3", r["reasons"][0])

    def test_garbage_output_falls_back(self):
        r, _ = self.run_cli('{"kind": "vibes", "tier": "low"}')
        self.assertEqual(r["source"], "rules")


if __name__ == "__main__":
    unittest.main()
