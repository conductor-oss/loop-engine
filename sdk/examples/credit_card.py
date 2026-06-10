#!/usr/bin/env python3
"""credit_card.py — resolve a credit-card dispute as a durable loop, in one file.

The whole loop lives here:
  @dispute.pre_planner  code that runs BEFORE the LLM planner: pulls the case facts
                        and policy so the strategy is grounded in real data
  @dispute.actor        applies the chargeback policy and writes to the ledger
  @dispute.evaluator    verifies the LEDGER — never the actor's claim

The durable control loop (retries, replans, budgets, escalation, termination) is
loop_engine on the Conductor server; this file only supplies judgment and work.

Run (server + loop_engine registered, see repo quickstart):
    pip install -e ..        # the loop SDK, from sdk/
    python credit_card.py
"""
import json
import os
import tempfile

from loop import Loop

# --- a tiny file-backed "bank" so the example is self-contained ---------------
# (a file, not module state: actor and evaluator run in separate worker processes)
LEDGER_FILE = os.path.join(tempfile.gettempdir(), "loop_sdk_credit_card_ledger.json")

DISPUTES = {
    "D-1001": {"customer": "alice", "amount": 220.00, "days_since_charge": 12,
               "card_present": False, "merchant": "skyhub-air", "prior_disputes": 0},
    "D-1002": {"customer": "bob", "amount": 1450.00, "days_since_charge": 75,
               "card_present": True, "merchant": "luxe-watches", "prior_disputes": 3},
}

POLICY = ("Auto-refund only when ALL hold: amount <= $500, charge is <= 60 days old, "
          "card was not present, and the customer has < 2 prior disputes. "
          "Otherwise deny with the failing condition(s) as the reason. "
          "Never refund more than the disputed amount.")


def _ledger_read():
    try:
        with open(LEDGER_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _ledger_write(case_id, entry):
    ledger = _ledger_read()
    ledger[case_id] = entry
    with open(LEDGER_FILE, "w") as f:
        json.dump(ledger, f)


# --- the loop -------------------------------------------------------------------
dispute = Loop(
    name="credit_card_dispute",
    objective="Resolve the credit-card dispute identified by extension_params.case_id, "
              "strictly following the chargeback policy.",
    acceptance_criteria="A decision (refund or deny) is recorded in the ledger for the "
                        "case, the decision matches the chargeback policy, and any "
                        "refund amount never exceeds the disputed amount.",
    llm_provider="anthropic",
    llm_model="claude-opus-4-7",
    max_iterations=4,
)


@dispute.pre_planner
def gather_case(extension_params=None):
    """Ground the planner in the real case facts + policy before it strategizes."""
    case_id = (extension_params or {}).get("case_id", "")
    case = DISPUTES.get(case_id)
    if case is None:
        return {"context": f"Case '{case_id}' not found in the dispute system.",
                "plan_hints": "The only valid resolution is deny with reason 'unknown case'."}
    return {
        "context": f"CASE {case_id} FACTS: {json.dumps(case)}\nCHARGEBACK POLICY: {POLICY}",
        "plan_hints": "Decide refund/deny purely from the policy conditions; "
                      "record the decision in the ledger; cite every failing condition.",
    }


@dispute.actor
def resolve(feedback="", extension_params=None):
    """Apply the policy and write the decision to the ledger (the real side effect)."""
    case_id = (extension_params or {}).get("case_id", "")
    case = DISPUTES.get(case_id)
    if case is None:
        decision = {"case_id": case_id, "action": "deny", "amount": 0,
                    "reason": "unknown case"}
    else:
        failing = []
        if case["amount"] > 500:
            failing.append(f"amount ${case['amount']} > $500 auto-refund limit")
        if case["days_since_charge"] > 60:
            failing.append(f"charge {case['days_since_charge']} days old > 60-day window")
        if case["card_present"]:
            failing.append("card was present at the charge")
        if case["prior_disputes"] >= 2:
            failing.append(f"{case['prior_disputes']} prior disputes >= 2")
        if failing:
            decision = {"case_id": case_id, "action": "deny", "amount": 0,
                        "reason": "; ".join(failing)}
        else:
            decision = {"case_id": case_id, "action": "refund", "amount": case["amount"],
                        "reason": "all auto-refund conditions met"}
    _ledger_write(case_id, decision)
    return {"result": decision,
            "summary": f"{decision['action']} ${decision['amount']} — {decision['reason']}"}


@dispute.evaluator
def verify(extension_params=None):
    """Judge from the ledger (system state), not from what the actor returned."""
    case_id = (extension_params or {}).get("case_id", "")
    entry = _ledger_read().get(case_id)
    checks = {"ledger_entry_exists": entry is not None}
    if entry is None:
        return {"passed": False, "score": 0.0, "checks": checks,
                "feedback": f"No ledger entry for case '{case_id}': the dispute was not resolved."}
    case = DISPUTES.get(case_id, {"amount": 0})
    checks["action_valid"] = entry.get("action") in ("refund", "deny")
    checks["amount_within_dispute"] = 0 <= entry.get("amount", -1) <= case["amount"]
    checks["reason_given"] = bool(entry.get("reason"))
    passed = all(checks.values())
    failed = [k for k, ok in checks.items() if not ok]
    return {"passed": passed, "score": 1.0 if passed else 0.5, "checks": checks,
            "feedback": "Ledger verified." if passed else f"Failed checks: {', '.join(failed)}"}


if __name__ == "__main__":
    run = dispute.execute(extension_params={"case_id": "D-1001"})
    print(f"loop started: {run.id}")
    out = run.watch()  # streams each iteration's decision until the loop terminates
    print(json.dumps(out.get("result"), indent=2))
    print(f"ledger now: {json.dumps(_ledger_read(), indent=2)}")
    dispute.stop_workers()
