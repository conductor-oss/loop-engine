"""Tests for the refund loop's roles and the file-backed datastore.

Each test runs against an isolated temp store so tests never touch
loop-examples/.state/ and can run in parallel with live workers.
"""
import os
import tempfile
import unittest

import datastore
from refund_support import account_lookup, issue_refund, verify_refund


class StoreIsolatedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (datastore._STATE_DIR, datastore._STORE_PATH, datastore._LOCK_PATH)
        datastore._STATE_DIR = self._tmp.name
        datastore._STORE_PATH = os.path.join(self._tmp.name, "store.json")
        datastore._LOCK_PATH = os.path.join(self._tmp.name, "store.lock")

    def tearDown(self):
        datastore._STATE_DIR, datastore._STORE_PATH, datastore._LOCK_PATH = self._orig
        self._tmp.cleanup()

    @staticmethod
    def _age_order(days):
        def _txn(store):
            store["orders"]["ORD-5001"]["days_since_purchase"] = days
        datastore.transact(_txn)


class TestAccountLookup(StoreIsolatedTest):
    def test_found_returns_policy_facts(self):
        out = account_lookup(customer_id="CUST-1001", order_id="ORD-5001")
        self.assertTrue(out["found"])
        self.assertEqual(out["policy"]["max_refundable"], 120.0)
        self.assertTrue(out["policy"]["within_window"])

    def test_unknown_order_not_found(self):
        out = account_lookup(customer_id="CUST-1001", order_id="ORD-NOPE")
        self.assertFalse(out["found"])

    def test_order_owned_by_other_customer_not_found(self):
        out = account_lookup(customer_id="CUST-9999", order_id="ORD-5001")
        self.assertFalse(out["found"])


class TestIssueRefund(StoreIsolatedTest):
    def test_valid_refund_recorded_in_ledger(self):
        out = issue_refund(order_id="ORD-5001", amount=100.0, reason="defective")
        self.assertEqual(out["status"], "recorded")
        self.assertTrue(out["refund_id"].startswith("RF-"))
        ledger = datastore.read()["refund_ledger"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["amount"], 100.0)

    def test_identical_redelivery_is_idempotent(self):
        first = issue_refund(order_id="ORD-5001", amount=100.0, reason="defective")
        replay = issue_refund(order_id="ORD-5001", amount=100.0, reason="defective")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["refund_id"], first["refund_id"])
        self.assertEqual(len(datastore.read()["refund_ledger"]), 1)

    def test_correction_updates_amount_and_keeps_audit_trail(self):
        issue_refund(order_id="ORD-5001", amount=150.0, reason="over")  # rejected (>total)
        issue_refund(order_id="ORD-5001", amount=120.0, reason="corrected")
        out = issue_refund(order_id="ORD-5001", amount=90.0, reason="partial only")
        ledger = datastore.read()["refund_ledger"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["amount"], 90.0)
        self.assertEqual([r["amount"] for r in ledger[0]["revisions"]], [120.0])

    def test_rejections_never_write_to_ledger(self):
        for amount in (0, -5, 120.01):
            out = issue_refund(order_id="ORD-5001", amount=amount, reason="bad")
            self.assertEqual(out["status"], "rejected")
        out = issue_refund(order_id="ORD-NOPE", amount=10, reason="bad")
        self.assertEqual(out["status"], "rejected")
        self.assertEqual(datastore.read()["refund_ledger"], [])

    def test_outside_window_rejected(self):
        self._age_order(45)
        out = issue_refund(order_id="ORD-5001", amount=50.0, reason="late")
        self.assertEqual(out["status"], "rejected")
        self.assertIn("window", out["reason"])


class TestVerifyRefund(StoreIsolatedTest):
    def test_verified_refund_passes(self):
        issue_refund(order_id="ORD-5001", amount=120.0, reason="defective")
        out = verify_refund(order_id="ORD-5001", action="issue_refund", amount=120.0)
        self.assertTrue(out["passed"])
        self.assertEqual(out["score"], 1.0)

    def test_claimed_but_unrecorded_refund_fails(self):
        # The agent SAYS it refunded but nothing is in the ledger — evidence wins.
        out = verify_refund(order_id="ORD-5001", action="issue_refund", amount=100.0)
        self.assertFalse(out["passed"])
        self.assertIn("No refund is recorded", out["feedback"])

    def test_over_refund_claim_fails_with_cap_feedback(self):
        out = verify_refund(order_id="ORD-5001", action="issue_refund", amount=240.0)
        self.assertFalse(out["passed"])
        self.assertIn("exceeds the order total", out["feedback"])

    def test_claim_ledger_mismatch_fails(self):
        issue_refund(order_id="ORD-5001", amount=80.0, reason="partial")
        out = verify_refund(order_id="ORD-5001", action="issue_refund", amount=120.0)
        self.assertFalse(out["passed"])
        self.assertIn("Mismatch", out["feedback"])

    def test_unnecessary_escalation_fails(self):
        out = verify_refund(order_id="ORD-5001", action="escalate", amount=0)
        self.assertFalse(out["passed"])
        self.assertIn("Escalation is unnecessary", out["feedback"])

    def test_escalation_outside_window_passes(self):
        self._age_order(45)
        out = verify_refund(order_id="ORD-5001", action="escalate", amount=0)
        self.assertTrue(out["passed"])

    def test_seeded_out_of_window_order_demands_escalation(self):
        # ORD-5002 ships in the seed 45 days old: escalation verifies, refunding fails.
        lookup = account_lookup(customer_id="CUST-1001", order_id="ORD-5002")
        self.assertTrue(lookup["found"])
        self.assertFalse(lookup["policy"]["within_window"])
        self.assertTrue(verify_refund(order_id="ORD-5002", action="escalate", amount=0)["passed"])
        self.assertFalse(verify_refund(order_id="ORD-5002", action="issue_refund", amount=50)["passed"])

    def test_unrecognized_action_fails(self):
        out = verify_refund(order_id="ORD-5001", action="make_coffee", amount=0)
        self.assertFalse(out["passed"])


if __name__ == "__main__":
    unittest.main()
