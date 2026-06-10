"""Tests for the data-quality workers (clean_dataset + data_quality_check)."""
import unittest

from conductor_loop_workers.data_quality import clean_dataset, data_quality_check

MESSY = [
    {"id": 1, "name": "  Ada  ", "email": "  ADA@Example.com ", "age": "36"},
    {"id": 2, "name": "", "email": "bob@example.com", "age": 41},
    {"id": 2, "name": "Bob Dup", "email": "dup@example.com", "age": 42},   # dup id
    {"id": 3, "name": "Cy", "email": "not-an-email", "age": 28},
    {"id": 4, "name": "Di", "email": "di@example.com", "age": 999},        # bad age
]


class TestCleanDataset(unittest.TestCase):
    def test_light_pass_normalizes_without_dropping(self):
        out = clean_dataset(dataset=MESSY, iteration=0, feedback="")
        recs = {r["id"]: r for r in out["records"]}
        self.assertEqual(recs[1]["email"], "ada@example.com")
        self.assertEqual(recs[1]["age"], 36)            # coerced from string
        self.assertEqual(recs[2]["name"], "Unknown")    # filled, first-wins dedupe
        self.assertNotIn("Bob Dup", [r["name"] for r in out["records"]])
        self.assertIn(3, recs)                          # bad email kept on light pass
        self.assertIsNone(recs[4]["age"])               # out-of-range age marked invalid, not clamped
        self.assertNotIn("drop_irreparable_rows", out["applied"])

    def test_feedback_triggers_aggressive_drop(self):
        out = clean_dataset(dataset=MESSY, iteration=1,
                            feedback="Violations: email_format: 1 invalid email(s)")
        ids = [r["id"] for r in out["records"]]
        self.assertIn("drop_irreparable_rows", out["applied"])
        self.assertNotIn(3, ids)   # irreparable email dropped
        self.assertNotIn(4, ids)   # irreparable age dropped
        self.assertEqual(sorted(ids), [1, 2])

    def test_deterministic(self):
        a = clean_dataset(dataset=MESSY, iteration=1, feedback="x")
        b = clean_dataset(dataset=MESSY, iteration=1, feedback="x")
        self.assertEqual(a["records"], b["records"])

    def test_cleaner_honors_custom_contract_bounds(self):
        # Cleaner and checker must close on the SAME contract, or the loop
        # can never converge when the operator tunes the bounds.
        data = [{"id": 1, "name": "Old", "email": "old@example.com", "age": 70},
                {"id": 2, "name": "Young", "email": "young@example.com", "age": 30}]
        contract = {"age_min": 0, "age_max": 65}
        light = clean_dataset(dataset=data, iteration=0, feedback="", contract=contract)
        self.assertIsNone(light["records"][0]["age"])   # 70 is invalid under THIS contract
        verdict = data_quality_check(records=light["records"], contract=contract)
        self.assertFalse(verdict["passed"])
        aggressive = clean_dataset(dataset=data, iteration=1,
                                   feedback=verdict["feedback"], contract=contract)
        self.assertEqual([r["id"] for r in aggressive["records"]], [2])
        self.assertTrue(data_quality_check(records=aggressive["records"],
                                           contract=contract)["passed"])

    def test_cleaner_honors_custom_field_names(self):
        data = [{"uid": 7, "title": "", "mail": "USER@X.COM ", "years": "44"}]
        contract = {"required_fields": ["uid", "title", "mail", "years"],
                    "unique_field": "uid", "email_field": "mail", "age_field": "years"}
        out = clean_dataset(dataset=data, contract=contract)
        rec = out["records"][0]
        self.assertEqual(rec["mail"], "user@x.com")
        self.assertEqual(rec["years"], 44)
        self.assertEqual(rec["title"], "Unknown")
        self.assertTrue(data_quality_check(records=out["records"], contract=contract)["passed"])


class TestDataQualityCheck(unittest.TestCase):
    def test_clean_data_passes_contract(self):
        records = [{"id": 1, "name": "Ada", "email": "ada@example.com", "age": 36}]
        out = data_quality_check(records=records)
        self.assertTrue(out["passed"])
        self.assertEqual(out["score"], 1.0)
        self.assertEqual(out["violations"], [])

    def test_violations_are_specific_and_score_graded(self):
        out = data_quality_check(records=[
            {"id": 1, "name": "Ada", "email": "bad", "age": 36},
            {"id": 1, "name": "Eve", "email": "eve@example.com", "age": 200},
        ])
        self.assertFalse(out["passed"])
        self.assertLess(out["score"], 1.0)
        self.assertGreater(out["score"], 0.0)
        joined = " ".join(out["violations"])
        self.assertIn("email_format", joined)
        self.assertIn("age_range", joined)
        self.assertIn("unique_id", joined)

    def test_empty_dataset_fails(self):
        out = data_quality_check(records=[])
        self.assertFalse(out["passed"])

    def test_loop_converges_light_then_aggressive(self):
        # Simulates the loop: light clean -> contract fails -> aggressive clean -> passes.
        light = clean_dataset(dataset=MESSY, iteration=0, feedback="")
        verdict1 = data_quality_check(records=light["records"])
        self.assertFalse(verdict1["passed"])
        aggressive = clean_dataset(dataset=MESSY, iteration=1, feedback=verdict1["feedback"])
        verdict2 = data_quality_check(records=aggressive["records"])
        self.assertTrue(verdict2["passed"])


if __name__ == "__main__":
    unittest.main()
