"""Generic, sandboxed Python code runner worker.

This is the *environment* for a coding-agent loop: it executes whatever Python
the actor (an LLM) produced against an operator-supplied test harness and reports
DETERMINISTIC pass/fail evidence. The loop trusts what the code DOES — not what
the model claims it did.

Contract (task: ``python_code_runner``)
    inputs:
        code            str    Candidate Python source (markdown fences tolerated).
        cases           list   [{"name": str, "expr": "<python boolean expression>"}]
                               Each expr is evaluated against the symbols the candidate
                               code defines. Authored by the (trusted) operator, not the LLM.
        timeout_seconds int    Wall-clock cap (default 15).
    output:
        passed       bool   True iff code ran and every case passed (no errors).
        score        float   passed_count / total  (0.0..1.0).
        feedback     str    Failing cases / tracebacks — fed straight back to the actor.
        total, passed_count, failures, stdout, ran

RESULT-INTEGRITY (anti-spoofing) -- the candidate is the untrusted channel and runs
in the same interpreter as the harness, so a naive design lets it forge a pass by
printing the result sentinel itself. This worker closes that hole:
  * Results are written OUT-OF-BAND to a random temp file (not stdout), whose path +
    a per-run random NONCE live in a sidecar the driver reads and DELETES *before*
    executing the candidate — so the candidate never learns the path or the nonce.
  * The candidate is exec'd in an ISOLATED namespace; it cannot read the driver's
    locals to recover the nonce/path.
  * The parent accepts the result ONLY if the file carries the matching nonce; a
    missing/mismatched/early-exit run is reported as a FAILURE, never a silent pass.

SECURITY (sandboxing) -- this still executes arbitrary code. Hardening: a fresh
subprocess in ``python -I`` isolated mode, a wall-clock timeout that SIGKILLs the
child, RLIMIT_CPU/RLIMIT_AS/RLIMIT_NOFILE inside the child (RLIMIT_AS is a no-op on
macOS; the Linux deploy target enforces it), an isolated temp CWD, and a scrubbed
env. Adequate for a TRUSTED coding-agent loop. For UNTRUSTED code in production, wrap
this in a real sandbox (gVisor / Firecracker / nsjail / a network-less container) —
a subprocess alone is not a security boundary.
"""
import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

from conductor.client.worker.worker_task import worker_task

log = logging.getLogger("conductor_loop.code_runner")

# Driver template. Reads a sidecar (nonce + out-of-band result path), DELETES it,
# then execs the candidate in an isolated namespace and writes nonce'd results to
# the result path. {TEST_LINES} is filled with operator-authored checks.
_DRIVER = '''\
import os, json
_here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_here, "params.json")) as _f:
    _p = json.load(_f)
os.remove(os.path.join(_here, "params.json"))  # destroy before running candidate
_NONCE = _p["nonce"]; _RESULT_PATH = _p["result_path"]

try:
    import resource as _r
    for _lim, _val in ((_r.RLIMIT_CPU, (10, 10)), (_r.RLIMIT_AS, (536870912, 536870912)), (_r.RLIMIT_NOFILE, (64, 64))):
        try:
            _r.setrlimit(_lim, _val)
        except Exception:
            pass
except Exception:
    pass

_results = {"passed": 0, "failed": 0, "total": 0, "failures": []}
def _check(desc, fn):
    _results["total"] += 1
    try:
        ok = bool(fn())
    except Exception as _e:
        ok = False
        desc = desc + " [error: " + repr(_e) + "]"
    if ok:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["failures"].append(desc)

# Candidate (untrusted) runs in its OWN namespace — it cannot reach _NONCE/_RESULT_PATH.
_cand_ns = {"__name__": "candidate"}
try:
    with open(os.path.join(_here, "candidate.py")) as _cf:
        _src = _cf.read()
    exec(compile(_src, "candidate.py", "exec"), _cand_ns)
except Exception as _e:
    import traceback as _tb
    _results["error"] = "".join(_tb.format_exception_only(type(_e), _e)).strip()

if "error" not in _results:
    try:
{TEST_LINES}
        pass
    except Exception as _e:
        import traceback as _tb
        _results["error"] = "".join(_tb.format_exception_only(type(_e), _e)).strip()

with open(_RESULT_PATH, "w") as _rf:
    json.dump({"nonce": _NONCE, "results": _results}, _rf)
'''


def _strip_fences(code: str) -> str:
    """Remove ```python ... ``` fences an LLM may have wrapped the code in."""
    c = (code or "").strip()
    if c.startswith("```"):
        c = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", c)
        c = re.sub(r"\s*```$", "", c)
    return c.strip()


def _build_driver(cases: list) -> str:
    lines = []
    for i, c in enumerate(cases):
        name = c.get("name", f"case {i}") if isinstance(c, dict) else f"case {i}"
        expr = c.get("expr", "False") if isinstance(c, dict) else str(c)
        # Put the assertion itself in the description so a failure tells the actor
        # exactly which check broke (pytest-style). The expr is eval'd in the
        # candidate namespace, deferred via a lambda so per-case errors are isolated.
        desc = f"{name} :: {expr}"
        lines.append(f"        _check({json.dumps(desc)}, lambda: eval({json.dumps(expr)}, _cand_ns))")
    test_block = "\n".join(lines) if lines else "        pass"
    return _DRIVER.replace("{TEST_LINES}", test_block)


@worker_task(task_definition_name="python_code_runner", thread_count=2)
def python_code_runner(code: str = "", cases: object = None,
                       timeout_seconds: int = 15) -> dict:
    cases = cases or []
    try:
        timeout_seconds = int(timeout_seconds)
    except (TypeError, ValueError):
        timeout_seconds = 15
    code = _strip_fences(code)
    if not code:
        return {"passed": False, "score": 0.0, "ran": False, "total": len(cases),
                "passed_count": 0, "failures": [],
                "feedback": "No code was provided to run."}

    workdir = tempfile.mkdtemp(prefix="loop_coderun_")
    result_fd, result_path = tempfile.mkstemp(prefix="loop_result_", suffix=".json")
    os.close(result_fd)
    nonce = secrets.token_hex(16)
    try:
        with open(os.path.join(workdir, "candidate.py"), "w") as fh:
            fh.write(code)
        with open(os.path.join(workdir, "runner.py"), "w") as fh:
            fh.write(_build_driver(cases))
        # Sidecar with the secret path + nonce — the driver deletes it before the
        # candidate runs, so the candidate can never read either value.
        with open(os.path.join(workdir, "params.json"), "w") as fh:
            json.dump({"nonce": nonce, "result_path": result_path}, fh)

        env = {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1", "HOME": workdir}
        try:
            proc = subprocess.run(
                [sys.executable, "-I", os.path.join(workdir, "runner.py")],
                capture_output=True, text=True, timeout=max(1, timeout_seconds),
                cwd=workdir, env=env,
            )
        except subprocess.TimeoutExpired:
            return {"passed": False, "score": 0.0, "ran": True, "total": len(cases),
                    "passed_count": 0, "failures": ["timeout"],
                    "feedback": (f"Execution exceeded {timeout_seconds}s and was killed "
                                 "(likely an infinite loop or a blocking call).")}

        # Read trusted results out-of-band; require the matching nonce.
        try:
            with open(result_path) as fh:
                payload = json.load(fh)
        except (json.JSONDecodeError, OSError):
            payload = None

        if not payload or payload.get("nonce") != nonce:
            err = (proc.stderr or proc.stdout or "no output").strip()[-1500:]
            return {"passed": False, "score": 0.0, "ran": True, "total": len(cases),
                    "passed_count": 0, "failures": ["no verifiable result"],
                    "stdout": (proc.stdout or "")[-1000:],
                    "feedback": ("Code did not produce a verifiable test result — it crashed, "
                                 f"exited before the tests ran, or failed to import:\n{err}")}

        result = payload["results"]
        total = int(result.get("total", 0))
        passed_count = int(result.get("passed", 0))
        failures = result.get("failures", []) or []
        run_error = result.get("error")
        passed = total > 0 and int(result.get("failed", 0)) == 0 and not run_error
        score = round(passed_count / total, 3) if total else 0.0

        fb = []
        if run_error:
            fb.append(f"Runtime error while testing: {run_error}")
        if failures:
            fb.append("Failing cases: " + "; ".join(str(f) for f in failures) + ".")
        if passed:
            fb.append(f"All {total} tests passed.")
        if not total and not run_error:
            fb.append("No test cases were supplied.")

        return {"passed": passed, "score": score, "ran": True, "total": total,
                "passed_count": passed_count, "failures": failures,
                "stdout": (proc.stdout or "")[-1000:],
                "feedback": " ".join(fb)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        with contextlib.suppress(OSError):
            os.remove(result_path)
