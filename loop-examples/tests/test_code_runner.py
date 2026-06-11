"""Tests for the sandboxed code runner behind coding_agent.py's evaluator.

Run from loop-examples:  python3 -m unittest discover -v
(also collectable by pytest). These execute real subprocesses.
"""
import unittest

from code_runner import run_python

CASES = [
    {"name": "add small", "expr": "add(2, 3) == 5"},
    {"name": "add negative", "expr": "add(-1, 1) == 0"},
]


class TestHappyPath(unittest.TestCase):
    def test_passing_code_passes_all_cases(self):
        out = run_python(code="def add(a, b):\n    return a + b\n", cases=CASES)
        self.assertTrue(out["passed"])
        self.assertEqual(out["score"], 1.0)
        self.assertEqual(out["passed_count"], 2)
        self.assertIn("All 2 tests passed", out["feedback"])

    def test_markdown_fences_are_stripped(self):
        out = run_python(
            code="```python\ndef add(a, b):\n    return a + b\n```", cases=CASES)
        self.assertTrue(out["passed"])

    def test_failing_case_named_in_feedback(self):
        # Buggy on the negative case only: one case passes, one fails.
        out = run_python(
            code="def add(a, b):\n    return a + b if b != 1 else 99\n", cases=CASES)
        self.assertFalse(out["passed"])
        self.assertEqual(out["passed_count"], 1)
        self.assertIn("add negative", out["feedback"])
        self.assertIn("add(-1, 1) == 0", out["feedback"])


class TestErrorPaths(unittest.TestCase):
    def test_empty_code_is_rejected(self):
        out = run_python(code="", cases=CASES)
        self.assertFalse(out["passed"])
        self.assertFalse(out["ran"])
        self.assertIn("No code", out["feedback"])

    def test_syntax_error_reported_not_passed(self):
        out = run_python(code="def add(a, b:\n  return", cases=CASES)
        self.assertFalse(out["passed"])
        self.assertIn("SyntaxError", out["feedback"])

    def test_runtime_error_at_import_reported(self):
        out = run_python(code="raise RuntimeError('boom')", cases=CASES)
        self.assertFalse(out["passed"])
        self.assertIn("boom", out["feedback"])

    def test_no_cases_supplied_never_passes(self):
        out = run_python(code="def add(a, b):\n    return a + b\n", cases=[])
        self.assertFalse(out["passed"])
        self.assertEqual(out["total"], 0)

    def test_infinite_loop_is_killed_by_timeout(self):
        out = run_python(code="while True:\n    pass\n", cases=CASES,
                         timeout_seconds=2)
        self.assertFalse(out["passed"])
        self.assertIn("exceeded 2s", out["feedback"])


class TestResultIntegrity(unittest.TestCase):
    """The candidate is the untrusted channel: it must not be able to forge a pass."""

    def test_early_exit_cannot_fake_success(self):
        # Exiting before the harness writes results must read as failure.
        out = run_python(code="import os\nos._exit(0)\n", cases=CASES)
        self.assertFalse(out["passed"])
        self.assertIn("no verifiable result", out["failures"])

    def test_stdout_spoofing_is_ignored(self):
        # Printing a fake result payload to stdout must not be trusted —
        # results travel out-of-band with a nonce the candidate never sees.
        spoof = (
            'print(\'{"nonce": "deadbeef", "results": '
            '{"passed": 2, "failed": 0, "total": 2, "failures": []}}\')\n'
            "import os\nos._exit(0)\n"
        )
        out = run_python(code=spoof, cases=CASES)
        self.assertFalse(out["passed"])

    def test_candidate_namespace_cannot_reach_harness_secrets(self):
        # The candidate runs in its own namespace (no __file__, no driver locals);
        # the nonce/result-path sidecar was consumed and deleted before it executes.
        probe = (
            "import os\n"
            "assert '__file__' not in dir()\n"
            "assert not os.path.exists(os.path.join(os.getcwd(), 'params.json'))\n"
            "found = [n for n in dir() if 'NONCE' in n or 'RESULT' in n]\n"
            "assert not found\n"
            "def add(a, b):\n    return a + b\n"
        )
        out = run_python(code=probe, cases=CASES)
        # The probe asserts pass (secrets unreachable) and the real cases pass.
        self.assertTrue(out["passed"])


if __name__ == "__main__":
    unittest.main()
