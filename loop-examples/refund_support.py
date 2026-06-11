#!/usr/bin/env python3
"""refund_support.py — a support loop that closes on the refund LEDGER, not claims.

Every role is in this file:
  @refund.pre_planner  looks up the account/order/policy facts (the new code-shapes-
                       the-planner extension point) so the LLM planner strategizes
                       from real data
  @refund.actor        applies the refund policy and writes through ``issue_refund``
                       to a durable, idempotent, audited ledger (datastore.py)
  @refund.evaluator    ``verify_refund`` re-reads the ledger + policy independently —
                       a refund the actor merely *claimed* can never pass

The ledger lives in .state/store.json next to this file — open it to SEE the refund.
Seed data ships both policy cases: ORD-5001 (12 days old — refund is correct) and
ORD-5002 (45 days old — escalation is correct).

Run (server + loop_engine registered, see repo quickstart):
    pip install -e ../sdk
    python refund_support.py in-window        # ORD-5001: refund, capped at the total
    python refund_support.py out-of-window    # ORD-5002: escalate, ledger untouched
    python3 datastore.py reset                # reset the ledger between runs
"""
import json
import sys

from loop import Loop

import datastore

# Policy (deterministic): a refund must be within the window and not exceed the order total.
REFUND_WINDOW_DAYS = 30


def account_lookup(customer_id="", order_id=""):
    """Read-only lookup of customer account + order facts and refund policy."""
    store = datastore.read()
    account = store["accounts"].get(customer_id)
    order = store["orders"].get(order_id)
    if not account or not order or order.get("customer_id") != customer_id:
        return {"found": False,
                "error": f"No matching order '{order_id}' for customer '{customer_id}'."}
    return {
        "found": True,
        "customer": account,
        "order": order,
        "policy": {"refund_window_days": REFUND_WINDOW_DAYS,
                   "max_refundable": order["total"],
                   "within_window": order["days_since_purchase"] <= REFUND_WINDOW_DAYS},
    }


def issue_refund(order_id="", amount=0.0, reason=""):
    """Validate against policy, then upsert the refund for an order.

    Write-time validation (defense in depth — the evaluator still verifies independently):
    the order must exist, be within the refund window, and 0 < amount <= order total. An
    invalid request is REJECTED and never written to the ledger.

    Idempotent on re-delivery: an identical (order_id, amount) call is a no-op replay, so
    Conductor redelivering the same task never double-pays. A *different* valid amount is a
    correction: the prior amount is preserved in a `revisions` audit trail (never destroyed)
    so the ledger stays auditable. `refund_id` comes from a persisted monotonic counter, so
    deleting an entry can never cause id reuse."""
    amount = round(float(amount or 0), 2)

    def _txn(store):
        order = store["orders"].get(order_id)
        if not order:
            return {"status": "rejected", "refund_id": "", "order_id": order_id,
                    "amount": amount, "reason": f"Unknown order '{order_id}'."}
        total = round(float(order["total"]), 2)
        if amount <= 0:
            return {"status": "rejected", "refund_id": "", "order_id": order_id,
                    "amount": amount, "reason": "Refund amount must be positive."}
        if amount > total + 0.001:
            return {"status": "rejected", "refund_id": "", "order_id": order_id, "amount": amount,
                    "reason": f"Amount ${amount:.2f} exceeds order total ${total:.2f}."}
        if order["days_since_purchase"] > REFUND_WINDOW_DAYS:
            return {"status": "rejected", "refund_id": "", "order_id": order_id, "amount": amount,
                    "reason": f"Order is outside the {REFUND_WINDOW_DAYS}-day refund window."}

        for entry in store["refund_ledger"]:
            if entry["order_id"] == order_id:
                if abs(float(entry["amount"]) - amount) < 0.001:
                    return {**entry, "idempotent_replay": True}
                # Correction: preserve the prior amount in an audit trail; never destroy it.
                entry.setdefault("revisions", []).append(
                    {"amount": entry["amount"], "reason": entry.get("reason", "")})
                entry["amount"] = amount
                entry["reason"] = reason or entry.get("reason", "")
                entry["status"] = "recorded"
                return {**entry, "idempotent_replay": False}

        seq = store.get("next_refund_seq", len(store["refund_ledger"]) + 1)
        store["next_refund_seq"] = seq + 1
        entry = {"refund_id": f"RF-{seq:04d}", "order_id": order_id, "amount": amount,
                 "reason": reason or "", "status": "recorded"}
        store["refund_ledger"].append(entry)
        return {**entry, "idempotent_replay": False}

    return datastore.transact(_txn)


def verify_refund(order_id="", action="", amount=0.0):
    """Independent verification from the system of record + policy. EVIDENCE, not claims.

    Checks the claimed action/amount against policy BEFORE consulting the ledger, so the
    feedback is precise (e.g. "exceeds cap") even when the write was rejected upstream."""
    store = datastore.read()
    order = store["orders"].get(order_id)
    if not order:
        return {"passed": False, "score": 0.0,
                "feedback": f"Cannot verify: order '{order_id}' does not exist.", "recommend": ""}

    total = round(float(order["total"]), 2)
    within_window = order["days_since_purchase"] <= REFUND_WINDOW_DAYS
    ledger_entry = next((e for e in store["refund_ledger"] if e["order_id"] == order_id), None)

    action = (action or "").strip().lower()
    amount = round(float(amount or 0), 2)

    # Case 1: agent issued a refund — check policy on the claim, then confirm the ledger.
    if action == "issue_refund":
        if not within_window:
            return {"passed": False, "score": 0.2, "recommend": "",
                    "feedback": (f"Policy violation: order is {order['days_since_purchase']} days old, "
                                 f"outside the {REFUND_WINDOW_DAYS}-day window. ESCALATE instead of refunding.")}
        if amount <= 0:
            return {"passed": False, "score": 0.1, "recommend": "",
                    "feedback": "Refund amount must be positive."}
        if amount > total + 0.001:
            return {"passed": False, "score": 0.3, "recommend": "",
                    "feedback": (f"Refund of ${amount:.2f} exceeds the order total ${total:.2f}. "
                                 f"Re-issue at or below ${total:.2f}.")}
        if ledger_entry is None:
            return {"passed": False, "score": 0.0, "recommend": "",
                    "feedback": ("No refund is recorded in the ledger — the write was rejected or never "
                                 f"happened. Issue a valid refund of at most ${total:.2f}.")}
        recorded = round(float(ledger_entry["amount"]), 2)
        if recorded > total + 0.001:
            return {"passed": False, "score": 0.3, "recommend": "",
                    "feedback": (f"Recorded refund ${recorded:.2f} exceeds order total ${total:.2f}.")}
        if abs(recorded - amount) > 0.001:
            return {"passed": False, "score": 0.5, "recommend": "",
                    "feedback": f"Mismatch: you reported ${amount:.2f} but the ledger records ${recorded:.2f}."}
        return {"passed": True, "score": 1.0, "recommend": "",
                "feedback": (f"Verified: refund {ledger_entry['refund_id']} of ${recorded:.2f} is recorded, "
                             f"within the ${total:.2f} cap and the {REFUND_WINDOW_DAYS}-day window.")}

    # Case 2: agent escalated — correct only when the case is genuinely ineligible.
    if action in ("escalate", "request_info"):
        if within_window:
            return {"passed": False, "score": 0.4, "recommend": "",
                    "feedback": (f"Escalation is unnecessary: order is within the {REFUND_WINDOW_DAYS}-day "
                                 f"window and refundable up to ${total:.2f}. Issue the refund directly.")}
        return {"passed": True, "score": 1.0, "recommend": "",
                "feedback": "Verified: case is outside auto-refund policy, so escalation is the correct action."}

    return {"passed": False, "score": 0.0, "recommend": "",
            "feedback": f"Unrecognized action '{action}'. Choose 'issue_refund' or 'escalate'."}


# --- the loop -------------------------------------------------------------------
refund = Loop(
    name="refund_support",
    objective="Resolve the customer refund request for the order in "
              "extension_params, taking the policy-correct action.",
    acceptance_criteria=f"Refund policy: refunds are allowed only within "
                        f"{REFUND_WINDOW_DAYS} days of purchase; the refund amount must "
                        f"NOT exceed the order total; if the order is outside the "
                        f"window, escalate to a human instead of refunding.",
    llm_provider="anthropic",
    llm_model="claude-opus-4-7",
    max_iterations=5,
    max_replans=0,
    token_budget=400000,
)


@refund.pre_planner
def case_facts(extension_params=None):
    """Ground the LLM planner in the account/order/policy facts before it strategizes."""
    p = extension_params or {}
    facts = account_lookup(p.get("customer_id", ""), p.get("order_id", ""))
    return {"context": "ACCOUNT, ORDER & POLICY FACTS (from the backend): "
                       + json.dumps(facts),
            "plan_hints": "Refund only within the window and never above the order "
                          "total; otherwise escalate. The evaluator verifies the "
                          "ledger, so the decision must actually be recorded."}


@refund.actor
def resolve(feedback="", extension_params=None):
    """Apply the policy; a refund goes through the validated, idempotent ledger write."""
    p = extension_params or {}
    order_id = p.get("order_id", "")
    facts = account_lookup(p.get("customer_id", ""), order_id)
    if not facts.get("found"):
        action = {"action": "escalate", "amount": 0,
                  "reason": facts.get("error", "case not found")}
    elif facts["policy"]["within_window"]:
        cap = facts["policy"]["max_refundable"]
        requested = float(p.get("requested_amount") or cap)
        amount = round(min(requested, cap), 2)  # never over-refund, whatever was asked
        written = issue_refund(order_id=order_id, amount=amount,
                               reason="customer refund request")
        action = {"action": "issue_refund", "amount": amount, "ledger": written}
    else:
        action = {"action": "escalate", "amount": 0,
                  "reason": f"order is {facts['order']['days_since_purchase']} days old, "
                            f"outside the {REFUND_WINDOW_DAYS}-day window"}
    return {"result": action,
            "summary": f"{action['action']} ${action.get('amount', 0)} for {order_id}"}


@refund.evaluator
def verify(result=None, extension_params=None):
    """Re-read the ledger and policy independently — the actor's claim is not evidence."""
    r = result or {}
    out = verify_refund(order_id=(extension_params or {}).get("order_id", ""),
                        action=r.get("action", ""), amount=r.get("amount", 0))
    return {"passed": out["passed"], "score": out["score"], "feedback": out["feedback"]}


CASES = {
    # Customer asks $200 on a $120 order — over-refund protection is exercised.
    "in-window": {"customer_id": "CUST-1001", "order_id": "ORD-5001",
                  "requested_amount": 200.00},
    # 45 days old — escalation, not a refund, is the policy-correct action.
    "out-of-window": {"customer_id": "CUST-1001", "order_id": "ORD-5002",
                      "requested_amount": 80.00},
}


if __name__ == "__main__":
    case = CASES.get(sys.argv[1] if len(sys.argv) > 1 else "in-window")
    if case is None:
        raise SystemExit(f"usage: python refund_support.py [{'|'.join(CASES)}]")
    run = refund.execute(extension_params=case)
    print(f"loop started: {run.id}")
    out = run.watch()
    print(json.dumps(out.get("result"), indent=2))
    print(f"ledger: {json.dumps(datastore.read()['refund_ledger'], indent=2)}")
    refund.stop_workers()
