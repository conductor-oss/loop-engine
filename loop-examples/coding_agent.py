#!/usr/bin/env python3
"""coding_agent.py — an LLM writes Python; real tests are the evidence.

The actor is a prompted LLM (``llm_actor`` — the SDK generates the LLM_CHAT_COMPLETE
sub-workflow, no worker needed). The evaluator runs the candidate code against
operator-supplied test cases in a sandboxed subprocess (see ``code_runner.py``,
including its security boundary notes) and feeds the exact failing assertions back.
The loop closes on the test run, never on the model saying "this works."

Run (server + loop_engine registered, see repo quickstart):
    pip install -e ../sdk
    python coding_agent.py roman          # roman_to_int
    python coding_agent.py payments      # allocate_cents (edge-case heavy)
"""
import sys

from loop import Loop

from code_runner import run_python

DEMOS = {
    "roman": {
        "objective": "Implement a function roman_to_int(s) that converts a Roman "
                     "numeral string to its integer value.",
        "acceptance_criteria": "Handle the symbols I,V,X,L,C,D,M and subtractive "
                               "combinations (IV=4, IX=9, XL=40, XC=90, CD=400, CM=900). "
                               "Return an int. Assume the input is a valid Roman numeral.",
        "context": "Signature: def roman_to_int(s: str) -> int. Tests call roman_to_int "
                   "directly; do not read stdin or print.",
        "cases": [
            {"name": "III", "expr": "roman_to_int('III') == 3"},
            {"name": "IV", "expr": "roman_to_int('IV') == 4"},
            {"name": "IX", "expr": "roman_to_int('IX') == 9"},
            {"name": "LVIII", "expr": "roman_to_int('LVIII') == 58"},
            {"name": "MCMXCIV", "expr": "roman_to_int('MCMXCIV') == 1994"},
            {"name": "MMXXIV", "expr": "roman_to_int('MMXXIV') == 2024"},
        ],
    },
    "payments": {
        "objective": "Implement a function allocate_cents(total_cents, weights) that "
                     "splits an integer amount of cents across recipients proportionally "
                     "to their weights, without losing or inventing a single cent.",
        "acceptance_criteria": "The returned list has one integer share per weight, the "
                               "shares sum EXACTLY to total_cents, and rounding uses the "
                               "largest-remainder method: each share starts at "
                               "floor(total_cents * weight / sum(weights)), then leftover "
                               "cents go one each to the largest fractional remainders, "
                               "ties broken by lower index. Assume sum(weights) > 0; "
                               "total_cents may be 0.",
        "context": "Signature: def allocate_cents(total_cents: int, weights: list[int]) "
                   "-> list[int]. Real payment-splitting bug class: naive rounding loses "
                   "cents. Tests call allocate_cents directly; do not read stdin or print.",
        "cases": [
            {"name": "even split, remainder to lowest index", "expr": "allocate_cents(100, [1, 1, 1]) == [34, 33, 33]"},
            {"name": "two remainder cents", "expr": "allocate_cents(101, [1, 1, 1]) == [34, 34, 33]"},
            {"name": "single recipient", "expr": "allocate_cents(100, [1]) == [100]"},
            {"name": "zero total", "expr": "allocate_cents(0, [3, 7]) == [0, 0]"},
            {"name": "largest remainder wins", "expr": "allocate_cents(7, [3, 1]) == [5, 2]"},
            {"name": "zero weight gets zero", "expr": "allocate_cents(10, [0, 1]) == [0, 10]"},
            {"name": "conservation property", "expr": "sum(allocate_cents(999, [7, 3, 9])) == 999"},
            {"name": "uneven weights conservation", "expr": "sum(allocate_cents(12345, [13, 1, 7, 100])) == 12345"},
        ],
    },
}

coding = Loop(
    name="coding_agent",
    objective=DEMOS["roman"]["objective"],            # per-run override in execute()
    acceptance_criteria=DEMOS["roman"]["acceptance_criteria"],
    llm_provider="anthropic",
    llm_model="claude-opus-4-7",
    max_iterations=5,
    token_budget=400000,
)

coding.llm_actor(
    system_prompt="You are a coding agent. Output ONLY valid Python source code — no "
                  "markdown fences, no commentary, no examples. Define exactly the "
                  "function(s) the task requires so they pass automated tests. If "
                  "evaluator feedback lists failing assertions or a traceback, your "
                  "previous code was wrong: fix those specific issues. Handle edge "
                  "cases. Do not print anything.",
    temperature=0.2,
    max_tokens=2500,
)


@coding.evaluator
def run_tests(result=None, extension_params=None):
    """Execute the candidate against the operator's cases — deterministic evidence."""
    p = extension_params or {}
    out = run_python(code=result if isinstance(result, str) else str(result or ""),
                     cases=p.get("cases") or [],
                     timeout_seconds=p.get("timeout_seconds", 15))
    return {"passed": out["passed"], "score": out["score"], "feedback": out["feedback"],
            "checks": {"total": out["total"], "passed_count": out["passed_count"],
                       "failures": out["failures"]}}


if __name__ == "__main__":
    demo = DEMOS.get(sys.argv[1] if len(sys.argv) > 1 else "roman")
    if demo is None:
        raise SystemExit(f"usage: python coding_agent.py [{'|'.join(DEMOS)}]")
    run = coding.execute(
        objective=demo["objective"],
        acceptance_criteria=demo["acceptance_criteria"],
        context=demo["context"],
        extension_params={"cases": demo["cases"], "timeout_seconds": 10},
    )
    print(f"loop started: {run.id}")
    out = run.watch()
    print(out.get("result"))
    coding.stop_workers()
