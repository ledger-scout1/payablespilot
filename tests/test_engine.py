"""Engine tests: every rule fires, and clean invoices stay clean."""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import (MatchConfig, build_payment_pack, draft_followup,
                    due_date, evaluate_invoice)

SUP = {"id": "S1", "name": "Acme", "bank_account": "GB00AAAA",
       "terms_days": 30}
PO = {"id": "PO1", "supplier_id": "S1",
      "lines": [{"sku": "A", "qty": 10, "unit_price_cents": 1000}],
      "total_cents": 10000}
GRN = {"id": "G1", "po_id": "PO1",
       "lines": [{"sku": "A", "qty_received": 10}]}


def inv(**kw):
    base = {"id": "i1", "supplier_id": "S1", "number": "N1", "po_id": "PO1",
            "date": "2026-10-01", "bank_account": "GB00AAAA",
            "lines": [{"sku": "A", "qty": 10, "unit_price_cents": 1000}],
            "total_cents": 10000}
    base.update(kw)
    return base


class MatchTests(unittest.TestCase):
    def test_clean_match(self):
        v = evaluate_invoice(inv(), SUP, PO, GRN, [])
        self.assertEqual(v.status, "MATCHED")
        self.assertEqual(v.reasons, [])

    def test_price_within_tolerance(self):
        # +15 on 1000 = 1.5% and below the 100-cent absolute floor allowance
        v = evaluate_invoice(inv(lines=[{"sku": "A", "qty": 10,
                                         "unit_price_cents": 1015}],
                                 total_cents=10150), SUP, PO, GRN, [])
        self.assertEqual(v.status, "MATCHED")

    def test_price_variance(self):
        v = evaluate_invoice(inv(lines=[{"sku": "A", "qty": 10,
                                         "unit_price_cents": 1250}],
                                 total_cents=12500), SUP, PO, GRN, [])
        self.assertEqual(v.status, "EXCEPTION")
        self.assertIn("PRICE_VARIANCE", v.reasons)

    def test_qty_exceeds_receipt(self):
        v = evaluate_invoice(inv(lines=[{"sku": "A", "qty": 11,
                                         "unit_price_cents": 1000}],
                                 total_cents=11000), SUP, PO, GRN, [])
        self.assertIn("QTY_EXCEEDS_RECEIPT", v.reasons)

    def test_missing_po(self):
        v = evaluate_invoice(inv(po_id=None), SUP, None, None, [])
        self.assertIn("MISSING_PO", v.reasons)

    def test_missing_grn_is_not_a_qty_error(self):
        v = evaluate_invoice(inv(), SUP, PO, None, [])
        self.assertIn("MISSING_GRN", v.reasons)
        self.assertNotIn("QTY_EXCEEDS_RECEIPT", v.reasons)

    def test_bank_change(self):
        v = evaluate_invoice(inv(bank_account="GB99FRAUD"), SUP, PO, GRN, [])
        self.assertIn("BANK_DETAIL_CHANGE", v.reasons)

    def test_exact_duplicate_blocked(self):
        prior = [inv(id="i0")]
        v = evaluate_invoice(inv(), SUP, PO, GRN, prior)
        self.assertEqual(v.status, "BLOCKED")
        self.assertIn("DUPLICATE_INVOICE_NUMBER", v.reasons)

    def test_near_duplicate_held(self):
        prior = [inv(id="i0", number="N0", date="2026-09-29")]
        v = evaluate_invoice(inv(), SUP, PO, GRN, prior)
        self.assertIn("POSSIBLE_DUPLICATE", v.reasons)
        self.assertEqual(v.status, "EXCEPTION")

    def test_total_mismatch(self):
        v = evaluate_invoice(inv(total_cents=9999), SUP, PO, GRN, [])
        self.assertIn("TOTAL_MISMATCH", v.reasons)

    def test_cumulative_qty_across_invoices_on_same_po(self):
        # 10 already invoiced on this PO; billing 1 more exceeds the 10 received
        prior_on_po = [inv(id="i0", number="N0")]
        v = evaluate_invoice(
            inv(lines=[{"sku": "A", "qty": 1, "unit_price_cents": 1000}],
                total_cents=1000),
            SUP, PO, GRN, [], po_prior_invoices=prior_on_po)
        self.assertIn("QTY_EXCEEDS_RECEIPT", v.reasons)


class PackTests(unittest.TestCase):
    def test_pack_groups_and_totals(self):
        invoices = [inv(), inv(id="i2", number="N2", date="2026-10-05")]
        pack = build_payment_pack(invoices, {"S1": SUP}, date(2026, 10, 10))
        self.assertEqual(pack["count"], 2)
        self.assertEqual(pack["total_cents"], 20000)
        self.assertTrue(pack["requires_human_approval"])
        self.assertEqual(pack["items"][0]["due_date"], "2026-10-31")

    def test_due_date_uses_terms(self):
        self.assertEqual(due_date(inv(), SUP), date(2026, 10, 31))

    def test_followup_is_a_labelled_draft(self):
        d = draft_followup("BANK_DETAIL_CHANGE", inv(), SUP)
        self.assertTrue(d["draft_only"])
        self.assertIn("N1", d["body"])


if __name__ == "__main__":
    unittest.main()
