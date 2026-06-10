"""Data-quality pipeline workers (the actor's "tool" + the deterministic evaluator).

A loop where Python code does the real work: ``clean_dataset`` transforms a messy
dataset (escalating its strategy as the evaluator reports violations) and
``data_quality_check`` gates on a deterministic data contract. The agent is "done"
only when the data actually satisfies the contract — measured, not asserted.

Tasks:
  clean_dataset(dataset, iteration, feedback) -> {records, applied}
  data_quality_check(records, contract)       -> {passed, score, violations, feedback}
"""
import logging
import re

from conductor.client.worker.worker_task import worker_task

log = logging.getLogger("conductor_loop.data_quality")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _to_int(value):
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


@worker_task(task_definition_name="clean_dataset", thread_count=2)
def clean_dataset(dataset: object = None, iteration: int = 0,
                  feedback: str = "", contract: object = None) -> dict:
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

    log.info("clean_dataset iter=%s in=%d out=%d aggressive=%s out_of_range_ages=%d",
             iteration, len(dataset), len(cleaned), aggressive, out_of_range_ages)
    return {"records": cleaned, "applied": applied, "out_of_range_ages": out_of_range_ages,
            "in_count": len(dataset), "out_count": len(cleaned)}


@worker_task(task_definition_name="data_quality_check", thread_count=2)
def data_quality_check(records: object = None, contract: object = None) -> dict:
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

    log.info("data_quality_check rows=%d passed=%s score=%.2f", len(records), passed, score)
    return {"passed": passed, "score": score, "violations": violations,
            "row_count": len(records), "feedback": feedback}
