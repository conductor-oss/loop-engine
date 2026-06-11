#!/usr/bin/env python3
"""data_quality.py — Python code does the ETL; a deterministic contract is the gate.

A loop where both roles are code: ``clean_dataset`` transforms a messy dataset
(escalating its strategy once the evaluator has rejected a pass) and
``data_quality_check`` gates on a deterministic data contract. The agent is "done"
only when the data actually satisfies the contract — measured, not asserted.

Both functions read THE SAME operator-supplied contract (extension_params.contract):
field names and bounds are configuration, not code, so tuning the contract (say
age_max 65, or renamed columns) re-targets the cleaner and the gate together.

Run (server + loop_engine registered, see repo quickstart):
    pip install -e ../sdk
    python data_quality.py
"""
import json
import re

from loop import Loop

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _to_int(value):
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def clean_dataset(dataset=None, iteration=0, feedback="", contract=None):
    """Normalize records toward THE SAME contract the evaluator enforces; escalate to
    dropping irreparable rows once a pass has already failed the contract.

    The cleaner and the checker must close on one contract: if the cleaner hardcoded
    its own bounds/field names, an operator-tuned contract (say age_max 65) could
    reject rows the cleaner considers fine and the loop would never converge.
    Deterministic and convergent."""
    dataset = dataset or []
    contract = contract or {}
    email_field = contract.get("email_field", "email")
    id_field = contract.get("unique_field", "id")
    age_field = contract.get("age_field", "age")
    age_min = contract.get("age_min", 0)
    age_max = contract.get("age_max", 120)
    required = contract.get("required_fields", ["id", "name", "email", "age"])
    # Required free-text fields (not the typed/identity ones) get a placeholder fill.
    fillable = [f for f in required if f not in (email_field, id_field, age_field)]

    # Escalate once the evaluator has already rejected a pass (non-empty feedback).
    # Keying on feedback (not the iteration number) is robust to the loop's 1-based counter.
    aggressive = bool((feedback or "").strip()) or int(iteration or 0) > 1
    # First-occurrence wins on duplicate ids (documented choice — the contract defines no
    # "cleaner duplicate" metric, so we keep it simple and deterministic).
    applied = ["trim", f"lowercase_{email_field}", f"coerce_{age_field}",
               "fill_required_text", f"dedupe_{id_field}(first-wins)"]
    if aggressive:
        applied.append("drop_irreparable_rows")

    cleaned, seen_ids = [], set()
    out_of_range_ages = 0
    for raw in dataset:
        rec = dict(raw or {})
        for k, v in list(rec.items()):
            if isinstance(v, str):
                rec[k] = v.strip()

        email = (rec.get(email_field) or "").lower()
        rec[email_field] = email
        age = _to_int(rec.get(age_field))
        # Do NOT clamp an out-of-range age into compliance — that would silently turn bad
        # data into "passing" data and hide the defect. Mark it invalid (None) so the
        # contract surfaces it and the aggressive pass drops the row, like any other defect.
        if age is not None and not (age_min <= age <= age_max):
            out_of_range_ages += 1
            age = None
        rec[age_field] = age
        for f in fillable:
            if not rec.get(f):
                rec[f] = "Unknown"

        rid = rec.get(id_field)
        if rid in seen_ids:
            continue  # dedupe (keep first)

        if aggressive:
            # Drop rows that cannot be repaired into contract compliance.
            if not email or not _EMAIL_RE.match(email):
                continue
            if rec.get(age_field) is None:
                continue

        if rid is not None:
            seen_ids.add(rid)
        cleaned.append(rec)

    return {"records": cleaned, "applied": applied, "out_of_range_ages": out_of_range_ages,
            "in_count": len(dataset), "out_count": len(cleaned)}


def data_quality_check(records=None, contract=None):
    """Evaluate a dataset against a deterministic data contract. Evidence-based gate."""
    records = records or []
    contract = contract or {}
    required = contract.get("required_fields", ["id", "name", "email", "age"])
    email_field = contract.get("email_field", "email")
    id_field = contract.get("unique_field", "id")
    age_field = contract.get("age_field", "age")
    age_min = contract.get("age_min", 0)
    age_max = contract.get("age_max", 120)

    rules, violations = [], []

    def rule(name, ok, detail):
        rules.append(ok)
        if not ok:
            violations.append(f"{name}: {detail}")

    # Rule: non-empty dataset
    rule("non_empty", len(records) > 0, "dataset is empty")

    # Rule: required fields present and non-null on every row
    missing = [f for f in required
               for r in records if r.get(f) in (None, "", [])]
    rule("required_fields", not missing,
         f"{len(missing)} null/empty required field(s) across rows")

    # Rule: valid email format
    bad_email = [r.get(email_field) for r in records
                 if not _EMAIL_RE.match(str(r.get(email_field) or ""))]
    rule("email_format", not bad_email,
         f"{len(bad_email)} invalid email(s) e.g. {bad_email[:2]}")

    # Rule: age within range
    bad_age = [r.get(age_field) for r in records
               if not (isinstance(r.get(age_field), int)
                       and age_min <= r.get(age_field) <= age_max)]
    rule("age_range", not bad_age,
         f"{len(bad_age)} age(s) outside [{age_min},{age_max}] e.g. {bad_age[:2]}")

    # Rule: unique ids
    ids = [r.get(id_field) for r in records]
    dupes = len(ids) - len(set(ids))
    rule("unique_id", dupes == 0, f"{dupes} duplicate id(s)")

    passed_rules = sum(1 for ok in rules if ok)
    total_rules = len(rules)
    passed = passed_rules == total_rules and len(records) > 0
    score = round(passed_rules / total_rules, 3) if total_rules else 0.0
    feedback = ("All data-quality rules passed (%d rows)." % len(records)
                if passed else "Violations: " + "; ".join(violations) + ".")

    return {"passed": passed, "score": score, "violations": violations,
            "row_count": len(records), "feedback": feedback}


# --- the loop -------------------------------------------------------------------
quality = Loop(
    name="data_quality",
    objective="Clean the customer dataset so it satisfies the data-quality contract.",
    acceptance_criteria="Every row must have non-null id, name, email and age; emails "
                        "must be validly formatted; age must be an integer in [0,120]; "
                        "there must be no duplicate ids.",
    llm_provider="anthropic",
    llm_model="claude-opus-4-7",
    max_iterations=4,
    max_replans=0,
    token_budget=400000,
)


@quality.actor
def clean(iteration=0, feedback="", extension_params=None):
    p = extension_params or {}
    out = clean_dataset(dataset=p.get("dataset"), iteration=iteration,
                        feedback=feedback, contract=p.get("contract"))
    return {"result": out["records"],
            "summary": (f"cleaned {out['in_count']} -> {out['out_count']} rows; "
                        f"applied: {', '.join(out['applied'])}")}


@quality.evaluator
def check(result=None, extension_params=None):
    out = data_quality_check(records=result,
                             contract=(extension_params or {}).get("contract"))
    return {"passed": out["passed"], "score": out["score"], "feedback": out["feedback"],
            "checks": {"violations": out["violations"], "row_count": out["row_count"]}}


MESSY_CUSTOMERS = [
    {"id": 1, "name": " Alice ", "email": "ALICE@EXAMPLE.COM", "age": "30"},
    {"id": 1, "name": "Alice", "email": "alice@example.com", "age": "30"},
    {"id": 2, "name": "", "email": "not-an-email", "age": "45"},
    {"id": 3, "name": "Bob", "email": "bob@example.com", "age": "abc"},
    {"id": 4, "name": "Cy", "email": "cy@example.com", "age": "200"},
    {"id": 5, "name": "Dee", "email": "dee@example.com", "age": "27"},
]

CONTRACT = {
    "required_fields": ["id", "name", "email", "age"],
    "unique_field": "id",
    "email_field": "email",
    "age_field": "age",
    "age_min": 0,
    "age_max": 120,
}


if __name__ == "__main__":
    run = quality.execute(extension_params={"dataset": MESSY_CUSTOMERS,
                                            "contract": CONTRACT})
    print(f"loop started: {run.id}")
    out = run.watch()  # expect: light clean -> violations -> aggressive clean -> pass
    print(json.dumps(out.get("result"), indent=2))
    quality.stop_workers()
