"""PayablesPilot matching engine.

Deterministic core: no I/O, no randomness, no external calls.
Every decision is produced by explicit rules and returned with the
observed values so a human can audit exactly why an invoice was
matched, held or blocked. All money is integer cents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchConfig:
    price_tolerance_pct: float = 2.0      # allowed unit-price variance, percent
    price_tolerance_abs_cents: int = 100  # allowed unit-price variance, absolute floor
    near_duplicate_window_days: int = 7   # same supplier+amount inside window -> hold


# ---------------------------------------------------------------------------
# Entities (plain dicts in/out of the engine)
# ---------------------------------------------------------------------------
# supplier:  {id, name, bank_account, terms_days}
# po:        {id, supplier_id, lines: [{sku, qty, unit_price_cents}], total_cents}
# grn:       {id, po_id, lines: [{sku, qty_received}]}
# invoice:   {id, supplier_id, number, po_id, date (ISO), bank_account,
#             lines: [{sku, qty, unit_price_cents}], total_cents}


def _money(lines):
    return sum(int(l["qty"]) * int(l["unit_price_cents"]) for l in lines)


def _line_index(lines):
    return {l["sku"]: l for l in lines}


@dataclass
class Check:
    name: str
    ok: bool
    observed: str
    expected: str


@dataclass
class Verdict:
    status: str  # "MATCHED" | "EXCEPTION" | "BLOCKED"
    reasons: list = field(default_factory=list)   # reason codes, in rule order
    checks: list = field(default_factory=list)    # list[Check]


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def evaluate_invoice(invoice, supplier, po, grn, prior_invoices,
                     config: MatchConfig = MatchConfig(), po_prior_invoices=()) -> Verdict:
    """Run the full pre-payment control pipeline for one invoice.

    prior_invoices: invoices already recorded for the same supplier
    (excluding this one) used for duplicate detection.
    po_prior_invoices: non-blocked invoices already recorded against the
    same purchase order, used for cumulative quantity control — a PO can
    never be billed for more than was received, across all its invoices.
    """
    reasons: list[str] = []
    checks: list[Check] = []
    blocked = False

    inv_total = _money(invoice["lines"])
    checks.append(Check(
        "invoice_total_consistent",
        inv_total == int(invoice["total_cents"]),
        f"lines sum to {inv_total}",
        f"stated total {invoice['total_cents']}",
    ))
    if inv_total != int(invoice["total_cents"]):
        reasons.append("TOTAL_MISMATCH")

    # -- duplicate prevention ------------------------------------------------
    exact = [p for p in prior_invoices
             if p["number"].strip().lower() == invoice["number"].strip().lower()]
    checks.append(Check(
        "not_exact_duplicate", not exact,
        f"{len(exact)} prior invoice(s) with number {invoice['number']}",
        "0",
    ))
    if exact:
        reasons.append("DUPLICATE_INVOICE_NUMBER")
        blocked = True

    inv_date = date.fromisoformat(invoice["date"])
    near = []
    for p in prior_invoices:
        if p["number"].strip().lower() == invoice["number"].strip().lower():
            continue
        if int(p["total_cents"]) != int(invoice["total_cents"]):
            continue
        gap = abs((date.fromisoformat(p["date"]) - inv_date).days)
        if gap <= config.near_duplicate_window_days:
            near.append(p["number"])
    checks.append(Check(
        "not_near_duplicate", not near,
        f"same amount as {near} within {config.near_duplicate_window_days} days"
        if near else "no same-amount invoice in window",
        "no same-amount invoice in window",
    ))
    if near:
        reasons.append("POSSIBLE_DUPLICATE")

    # -- bank detail control (payment-diversion fraud check) ------------------
    bank_ok = invoice["bank_account"] == supplier["bank_account"]
    checks.append(Check(
        "bank_account_matches_supplier_master", bank_ok,
        f"invoice bank {invoice['bank_account']}",
        f"supplier master bank {supplier['bank_account']}",
    ))
    if not bank_ok:
        reasons.append("BANK_DETAIL_CHANGE")

    # -- three-way match: invoice <-> PO <-> goods receipt --------------------
    if po is None:
        checks.append(Check("po_present", False, "no purchase order linked", "PO required"))
        reasons.append("MISSING_PO")
    else:
        checks.append(Check("po_present", True, f"PO {po['id']}", "PO required"))
        if po["supplier_id"] != invoice["supplier_id"]:
            reasons.append("PO_SUPPLIER_MISMATCH")
            checks.append(Check("po_supplier_matches", False,
                                f"PO supplier {po['supplier_id']}",
                                f"invoice supplier {invoice['supplier_id']}"))
        po_lines = _line_index(po["lines"])
        grn_lines = _line_index(grn["lines"]) if grn else {}
        if grn is None:
            reasons.append("MISSING_GRN")
            checks.append(Check("grn_present", False, "no goods receipt for PO",
                                "GRN required"))
        else:
            checks.append(Check("grn_present", True, f"GRN {grn['id']}", "GRN required"))

        for line in invoice["lines"]:
            sku = line["sku"]
            po_line = po_lines.get(sku)
            if po_line is None:
                reasons.append("LINE_NOT_ON_PO")
                checks.append(Check(f"line_{sku}_on_po", False,
                                    "sku absent from PO", "sku present on PO"))
                continue
            # quantity: invoiced must not exceed received (only when a GRN
            # exists — without one, MISSING_GRN is the signal, not quantity).
            # Cumulative across all invoices on this PO, not per-invoice.
            if grn is not None:
                received = int(grn_lines.get(sku, {}).get("qty_received", 0))
                already = sum(
                    int(l["qty"]) for p in po_prior_invoices for l in p["lines"]
                    if l["sku"] == sku)
                cumulative = already + int(line["qty"])
                qty_ok = cumulative <= received
                checks.append(Check(
                    f"line_{sku}_qty_received", qty_ok,
                    f"this invoice {line['qty']}, already invoiced {already}, "
                    f"cumulative {cumulative}, received {received}, "
                    f"ordered {po_line['qty']}",
                    "cumulative invoiced <= received",
                ))
                if not qty_ok:
                    reasons.append("QTY_EXCEEDS_RECEIPT")
            # price: invoiced unit price vs PO unit price, with tolerance
            diff = int(line["unit_price_cents"]) - int(po_line["unit_price_cents"])
            allowed = max(
                config.price_tolerance_abs_cents,
                round(int(po_line["unit_price_cents"]) * config.price_tolerance_pct / 100),
            )
            price_ok = abs(diff) <= allowed
            checks.append(Check(
                f"line_{sku}_price_within_tolerance", price_ok,
                f"invoiced {line['unit_price_cents']} vs PO {po_line['unit_price_cents']} "
                f"(diff {diff})",
                f"|diff| <= {allowed}",
            ))
            if not price_ok:
                reasons.append("PRICE_VARIANCE")

    # de-duplicate reason codes, preserving rule order
    reasons = list(dict.fromkeys(reasons))
    status = "BLOCKED" if blocked else ("EXCEPTION" if reasons else "MATCHED")
    return Verdict(status=status, reasons=reasons, checks=checks)


# ---------------------------------------------------------------------------
# Payment pack
# ---------------------------------------------------------------------------


def due_date(invoice, supplier) -> date:
    return date.fromisoformat(invoice["date"]) + timedelta(days=int(supplier["terms_days"]))


def build_payment_pack(matched_invoices, suppliers_by_id, today: date):
    """Group payable (MATCHED) invoices into a proposed payment run.

    The pack is a *proposal for human approval* — PayablesPilot never
    executes payments.
    """
    items = []
    total = 0
    for inv in matched_invoices:
        sup = suppliers_by_id[inv["supplier_id"]]
        d = due_date(inv, sup)
        items.append({
            "invoice_id": inv["id"],
            "invoice_number": inv["number"],
            "supplier_id": sup["id"],
            "supplier_name": sup["name"],
            "bank_account": sup["bank_account"],
            "amount_cents": int(inv["total_cents"]),
            "due_date": d.isoformat(),
            "overdue": d < today,
            "days_until_due": (d - today).days,
        })
        total += int(inv["total_cents"])
    items.sort(key=lambda x: (x["due_date"], x["supplier_name"]))
    return {"generated_on": today.isoformat(),
            "items": items,
            "count": len(items),
            "total_cents": total,
            "requires_human_approval": True}


# ---------------------------------------------------------------------------
# Supplier follow-up drafts (local templates; no external AI)
# ---------------------------------------------------------------------------

_FOLLOWUP = {
    "MISSING_GRN": (
        "Goods receipt not yet recorded",
        "Hello {supplier}, we have invoice {number} for {amount} linked to PO "
        "{po}, but our warehouse has not recorded the goods receipt yet. "
        "Could you confirm the delivery date and carrier reference so we can "
        "complete the three-way match?"),
    "PRICE_VARIANCE": (
        "Unit price differs from purchase order",
        "Hello {supplier}, on invoice {number} the unit price differs from "
        "PO {po} beyond our agreed tolerance. Could you confirm whether a "
        "price change was agreed, or issue a corrected invoice?"),
    "BANK_DETAIL_CHANGE": (
        "Bank details on invoice differ from our records",
        "Hello {supplier}, the bank account shown on invoice {number} "
        "differs from the account we hold for you. For payment security we "
        "have placed this invoice on hold. Please confirm your bank details "
        "through our verified contact channel before we proceed."),
    "POSSIBLE_DUPLICATE": (
        "Possible duplicate invoice",
        "Hello {supplier}, invoice {number} for {amount} closely matches "
        "another recent invoice from you. Could you confirm it is a separate "
        "charge and not a re-send?"),
    "QTY_EXCEEDS_RECEIPT": (
        "Invoiced quantity exceeds goods received",
        "Hello {supplier}, invoice {number} bills for more units than our "
        "goods receipt records against PO {po}. Could you check the "
        "delivery quantities and advise?"),
    "MISSING_PO": (
        "No purchase order reference",
        "Hello {supplier}, invoice {number} does not reference a purchase "
        "order. Please re-issue it quoting the PO number so we can match "
        "and schedule it for payment."),
}


def _fmt_amount(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def draft_followup(reason, invoice, supplier):
    subject, body = _FOLLOWUP.get(
        reason,
        ("Invoice on hold: " + reason,
         "Hello {supplier}, invoice {number} is on hold for review "
         "(reason: " + reason + "). We will update you shortly."))
    return {
        "reason": reason,
        "to_supplier": supplier["name"],
        "subject": subject,
        "body": body.format(
            supplier=supplier["name"],
            number=invoice["number"],
            amount=_fmt_amount(int(invoice["total_cents"])),
            po=invoice.get("po_id") or "(none)",
        ),
        "draft_only": True,
    }
