"""Refund / customer-support agent workers (the "tools" + the "verifier").

These back a loop where the ACTOR (an LLM) decides what to do and the EVALUATOR
verifies the *actual backend state* — the core loop-engineering principle: a support
agent must not mark a refund complete merely because it generated a message saying so.
``verify_refund`` reads the ledger.

Tasks:
  account_lookup(customer_id, order_id) -> account + order facts (read-only)
  issue_refund(order_id, amount, reason) -> validates policy then upserts; idempotent on
                                            identical (order_id, amount); corrections audited
  verify_refund(order_id, action, amount) -> deterministic verdict from ledger + policy
"""
import logging

from conductor.client.worker.worker_task import worker_task

from . import datastore

log = logging.getLogger("conductor_loop.refund")

# Policy (deterministic): a refund must be within the window and not exceed the order total.
REFUND_WINDOW_DAYS = 30


@worker_task(task_definition_name="account_lookup", thread_count=2)
def account_lookup(customer_id: str = "", order_id: str = "") -> dict:
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


@worker_task(task_definition_name="issue_refund", thread_count=2)
def issue_refund(order_id: str = "", amount: float = 0.0, reason: str = "") -> dict:
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

    result = datastore.transact(_txn)
    # DEBUG, not INFO: amounts/order ids are financial detail and shouldn't sit in INFO logs.
    log.debug("issue_refund order=%s amount=%.2f -> %s",
              order_id, amount, result.get("refund_id") or result.get("status"))
    return result


@worker_task(task_definition_name="verify_refund", thread_count=2)
def verify_refund(order_id: str = "", action: str = "", amount: float = 0.0) -> dict:
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
