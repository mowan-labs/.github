"""Unit tests for actions/ai-review/ai_review.py. Stdlib only: python3 -m unittest discover tests"""
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions", "ai-review"))
import ai_review  # noqa: E402

GOOD = '{"verdict": "APPROVE", "summary": "ok", "findings": []}'


def run_main(diff, env, fake_stdout=None, fake_exit=0):
    """Run main() with an optional fake kiro-cli on PATH; return the verdict dict."""
    with tempfile.TemporaryDirectory() as d:
        diff_path, out_path = os.path.join(d, "diff"), os.path.join(d, "out")
        with open(diff_path, "w") as fh:
            fh.write(diff)
        path = os.environ.get("PATH", "")
        if fake_stdout is not None:
            out_file = os.path.join(d, "stdout.txt")
            with open(out_file, "w") as fh:
                fh.write(fake_stdout)
            exe = os.path.join(d, "kiro-cli")
            with open(exe, "w") as fh:
                fh.write("#!/bin/sh\ncat >/dev/null\ncat '%s'\nexit %d\n" % (out_file, fake_exit))
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
            path = d + os.pathsep + path
        with mock.patch.dict(os.environ, dict(env, PATH=path), clear=True):
            ai_review.main(diff_path, out_path)
        with open(out_path) as fh:
            return json.load(fh)


class ExtractVerdict(unittest.TestCase):
    def test_takes_last_verdict_object_after_prose_and_ansi(self):
        text = '\x1b[32mthinking {not json}\x1b[0m\n{"verdict": "REQUEST_CHANGES"}\nfinal:\n' + GOOD
        self.assertEqual(ai_review.extract_verdict(text)["verdict"], "APPROVE")

    def test_nested_findings_do_not_shadow_outer_object(self):
        text = '{"verdict": "REQUEST_CHANGES", "findings": [{"severity": "high"}]}'
        self.assertEqual(ai_review.extract_verdict(text)["verdict"], "REQUEST_CHANGES")

    def test_no_verdict_raises(self):
        with self.assertRaises(ValueError):
            ai_review.extract_verdict('looks fine to me {"summary": "x"}')


class MainFailsClosed(unittest.TestCase):
    def test_missing_key(self):
        r = run_main("diff --git a b\n+x\n", {})
        self.assertEqual(r["verdict"], "REQUEST_CHANGES")
        self.assertIn("KIRO_API_KEY", r["body"])

    def test_empty_diff_approves_without_calling_kiro(self):
        self.assertEqual(run_main("  \n", {"KIRO_API_KEY": "k"})["verdict"], "APPROVE")

    def test_oversized_diff(self):
        r = run_main("x" * (ai_review.MAX_DIFF_CHARS + 1), {"KIRO_API_KEY": "k"})
        self.assertEqual(r["verdict"], "REQUEST_CHANGES")

    def test_kiro_nonzero_exit(self):
        r = run_main("+x\n", {"KIRO_API_KEY": "k"}, fake_stdout=GOOD, fake_exit=1)
        self.assertEqual(r["verdict"], "REQUEST_CHANGES")
        self.assertIn("exited 1", r["body"])

    def test_unparseable_output(self):
        r = run_main("+x\n", {"KIRO_API_KEY": "k"}, fake_stdout="LGTM!")
        self.assertEqual(r["verdict"], "REQUEST_CHANGES")

    def test_bad_verdict_value(self):
        r = run_main("+x\n", {"KIRO_API_KEY": "k"}, fake_stdout='{"verdict": "SHIP_IT"}')
        self.assertEqual(r["verdict"], "REQUEST_CHANGES")

    def test_approve_path_renders_body(self):
        out = ('{"verdict": "APPROVE", "summary": "fine", '
               '"findings": [{"path": "a.py", "line": 3, "severity": "low", "issue": "i|j", "fix": "f"}]}')
        r = run_main("+x\n", {"KIRO_API_KEY": "k"}, fake_stdout=out)
        self.assertEqual(r["verdict"], "APPROVE")
        self.assertIn("`a.py:3`", r["body"])
        self.assertIn("i\\|j", r["body"])


if __name__ == "__main__":
    unittest.main()
