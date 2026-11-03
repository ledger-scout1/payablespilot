"""Labelled demonstration dataset for PayablesPilot.

All parties, accounts and documents below are fictional demo data,
created so every rule in the engine fires at least once:
clean matches, a price variance, a short delivery billed in full,
a changed bank account, an exact duplicate, a near duplicate,
a missing PO and a missing goods receipt.
"""

SUPPLIERS = [
    {"id": "SUP-1", "name": "Northgate Components Ltd",
     "bank_account": "GB29NWBK60161331926819", "terms_days": 30},
    {"id": "SUP-2", "name": "Alder Print & Paper Co",
     "bank_account": "GB33BUKB20201555555555", "terms_days": 14},
]

POS = [
    {"id": "PO-1001", "supplier_id": "SUP-1", "lines": [
        {"sku": "WIDGET-A", "qty": 100, "unit_price_cents": 500},
        {"sku": "GASKET-B", "qty": 50, "unit_price_cents": 120}]},
    {"id": "PO-1002", "supplier_id": "SUP-1", "lines": [
        {"sku": "CABLE-C", "qty": 200, "unit_price_cents": 75}]},
    {"id": "PO-1003", "supplier_id": "SUP-1", "lines": [
        {"sku": "WIDGET-A", "qty": 40, "unit_price_cents": 500}]},
    {"id": "PO-1004", "supplier_id": "SUP-1", "lines": [
        {"sku": "GASKET-B", "qty": 10, "unit_price_cents": 120}]},
    {"id": "PO-1005", "supplier_id": "SUP-1", "lines": [
        {"sku": "WIDGET-A", "qty": 100, "unit_price_cents": 500},
        {"sku": "GASKET-B", "qty": 50, "unit_price_cents": 120}]},
    {"id": "PO-1006", "supplier_id": "SUP-2", "lines": [
        {"sku": "PAPER-X", "qty": 20, "unit_price_cents": 300}]},
    {"id": "PO-1007", "supplier_id": "SUP-2", "lines": [
        {"sku": "PAPER-X", "qty": 100, "unit_price_cents": 300}]},
]

GRNS = [
    {"id": "GRN-1", "po_id": "PO-1001", "lines": [
        {"sku": "WIDGET-A", "qty_received": 100},
        {"sku": "GASKET-B", "qty_received": 50}]},
    {"id": "GRN-2", "po_id": "PO-1002", "lines": [
        {"sku": "CABLE-C", "qty_received": 200}]},
    {"id": "GRN-3", "po_id": "PO-1003", "lines": [
        {"sku": "WIDGET-A", "qty_received": 35}]},  # short delivery: 35 of 40
    {"id": "GRN-4", "po_id": "PO-1004", "lines": [
        {"sku": "GASKET-B", "qty_received": 10}]},
    {"id": "GRN-5", "po_id": "PO-1005", "lines": [
        {"sku": "WIDGET-A", "qty_received": 100},
        {"sku": "GASKET-B", "qty_received": 50}]},
    # PO-1006 intentionally has no GRN.
    {"id": "GRN-7", "po_id": "PO-1007", "lines": [
        {"sku": "PAPER-X", "qty_received": 100}]},
]

_B1 = "GB29NWBK60161331926819"
_B2 = "GB33BUKB20201555555555"

INVOICES = [
    # clean three-way match -> MATCHED
    {"id": "inv-demo-1", "supplier_id": "SUP-1", "number": "INV-1001",
     "po_id": "PO-1001", "date": "2026-10-20", "bank_account": _B1,
     "lines": [{"sku": "WIDGET-A", "qty": 100, "unit_price_cents": 500},
               {"sku": "GASKET-B", "qty": 50, "unit_price_cents": 120}],
     "total_cents": 56000},
    # unit price far beyond tolerance -> PRICE_VARIANCE
    {"id": "inv-demo-2", "supplier_id": "SUP-1", "number": "INV-1002",
     "po_id": "PO-1002", "date": "2026-10-21", "bank_account": _B1,
     "lines": [{"sku": "CABLE-C", "qty": 200, "unit_price_cents": 200}],
     "total_cents": 40000},
    # billed 40, only 35 received -> QTY_EXCEEDS_RECEIPT
    {"id": "inv-demo-3", "supplier_id": "SUP-1", "number": "INV-1003",
     "po_id": "PO-1003", "date": "2026-10-22", "bank_account": _B1,
     "lines": [{"sku": "WIDGET-A", "qty": 40, "unit_price_cents": 500}],
     "total_cents": 20000},
    # bank account differs from supplier master -> BANK_DETAIL_CHANGE
    {"id": "inv-demo-4", "supplier_id": "SUP-1", "number": "INV-1004",
     "po_id": "PO-1004", "date": "2026-10-22", "bank_account": "GB00FRAUD0000000000",
     "lines": [{"sku": "GASKET-B", "qty": 10, "unit_price_cents": 120}],
     "total_cents": 1200},
    # same invoice number submitted twice -> BLOCKED duplicate
    {"id": "inv-demo-5", "supplier_id": "SUP-1", "number": "INV-1001",
     "po_id": "PO-1001", "date": "2026-10-23", "bank_account": _B1,
     "lines": [{"sku": "WIDGET-A", "qty": 100, "unit_price_cents": 500},
               {"sku": "GASKET-B", "qty": 50, "unit_price_cents": 120}],
     "total_cents": 56000},
    # different number, same amount, 4 days later -> POSSIBLE_DUPLICATE
    {"id": "inv-demo-6", "supplier_id": "SUP-1", "number": "INV-1006",
     "po_id": "PO-1005", "date": "2026-10-24", "bank_account": _B1,
     "lines": [{"sku": "WIDGET-A", "qty": 100, "unit_price_cents": 500},
               {"sku": "GASKET-B", "qty": 50, "unit_price_cents": 120}],
     "total_cents": 56000},
    # no purchase order -> MISSING_PO
    {"id": "inv-demo-7", "supplier_id": "SUP-2", "number": "INV-1007",
     "po_id": None, "date": "2026-10-25", "bank_account": _B2,
     "lines": [{"sku": "PAPER-X", "qty": 10, "unit_price_cents": 300}],
     "total_cents": 3000},
    # PO exists but nothing received -> MISSING_GRN
    {"id": "inv-demo-8", "supplier_id": "SUP-2", "number": "INV-1008",
     "po_id": "PO-1006", "date": "2026-10-26", "bank_account": _B2,
     "lines": [{"sku": "PAPER-X", "qty": 20, "unit_price_cents": 300}],
     "total_cents": 6000},
    # second clean match, shorter terms -> MATCHED, earlier due date
    {"id": "inv-demo-9", "supplier_id": "SUP-2", "number": "INV-1009",
     "po_id": "PO-1007", "date": "2026-10-28", "bank_account": _B2,
     "lines": [{"sku": "PAPER-X", "qty": 100, "unit_price_cents": 300}],
     "total_cents": 30000},
]


def load_demo(store, process_invoice):
    for s in SUPPLIERS:
        store.put_supplier(s)
    for po in POS:
        store.put_po(po)
    for g in GRNS:
        store.put_grn(g)
    for inv in INVOICES:
        process_invoice(inv)
