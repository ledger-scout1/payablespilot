"""SQLite persistence for PayablesPilot (stdlib only).

Stores suppliers, purchase orders, goods receipts, invoices, the
decision produced for every invoice (status + reasons + checks) and an
append-only audit trail. Money is integer cents throughout.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    bank_account TEXT NOT NULL, terms_days INTEGER NOT NULL DEFAULT 30
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    id TEXT PRIMARY KEY, supplier_id TEXT NOT NULL,
    lines TEXT NOT NULL, total_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS goods_receipts (
    id TEXT PRIMARY KEY, po_id TEXT NOT NULL, lines TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY, supplier_id TEXT NOT NULL, number TEXT NOT NULL,
    po_id TEXT, date TEXT NOT NULL, bank_account TEXT NOT NULL,
    lines TEXT NOT NULL, total_cents INTEGER NOT NULL,
    status TEXT NOT NULL, reasons TEXT NOT NULL, checks_json TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
    pack TEXT NOT NULL, approved_by TEXT, approved_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
    event TEXT NOT NULL, detail TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: str):
        # check_same_thread=False is safe here: app.py serializes all
        # request handling through a single lock.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- audit ---------------------------------------------------------------
    def log(self, event: str, detail: str):
        self.db.execute("INSERT INTO audit (at, event, detail) VALUES (?,?,?)",
                        (_now(), event, detail))
        self.db.commit()

    def audit_trail(self, limit=100):
        rows = self.db.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # -- suppliers -------------------------------------------------------------
    def put_supplier(self, s):
        self.db.execute(
            "INSERT OR REPLACE INTO suppliers (id, name, bank_account, terms_days)"
            " VALUES (?,?,?,?)",
            (s["id"], s["name"], s["bank_account"], int(s["terms_days"])))
        self.db.commit()
        self.log("supplier_saved", s["id"])

    def get_supplier(self, sid):
        r = self.db.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None

    def all_suppliers(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM suppliers ORDER BY name")]

    # -- purchase orders / goods receipts --------------------------------------
    def put_po(self, po):
        total = sum(int(l["qty"]) * int(l["unit_price_cents"]) for l in po["lines"])
        self.db.execute(
            "INSERT OR REPLACE INTO purchase_orders (id, supplier_id, lines, total_cents)"
            " VALUES (?,?,?,?)",
            (po["id"], po["supplier_id"], json.dumps(po["lines"]), total))
        self.db.commit()
        self.log("po_saved", po["id"])
        return {**po, "total_cents": total}

    def get_po(self, pid):
        if not pid:
            return None
        r = self.db.execute("SELECT * FROM purchase_orders WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["lines"] = json.loads(d["lines"])
        return d

    def put_grn(self, grn):
        self.db.execute(
            "INSERT OR REPLACE INTO goods_receipts (id, po_id, lines) VALUES (?,?,?)",
            (grn["id"], grn["po_id"], json.dumps(grn["lines"])))
        self.db.commit()
        self.log("grn_saved", grn["id"])

    def get_grn_for_po(self, po_id):
        if not po_id:
            return None
        r = self.db.execute(
            "SELECT * FROM goods_receipts WHERE po_id=? ORDER BY id DESC LIMIT 1",
            (po_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["lines"] = json.loads(d["lines"])
        return d

    # -- invoices ---------------------------------------------------------------
    def put_invoice(self, inv, verdict):
        self.db.execute(
            "INSERT OR REPLACE INTO invoices (id, supplier_id, number, po_id, date,"
            " bank_account, lines, total_cents, status, reasons, checks_json, decided_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (inv["id"], inv["supplier_id"], inv["number"], inv.get("po_id"),
             inv["date"], inv["bank_account"], json.dumps(inv["lines"]),
             int(inv["total_cents"]), verdict.status,
             json.dumps(verdict.reasons),
             json.dumps([vars(c) for c in verdict.checks]), _now()))
        self.db.commit()
        self.log("invoice_decided", f"{inv['id']} -> {verdict.status} "
                                     f"{','.join(verdict.reasons)}")

    def _invoice_row(self, r):
        d = dict(r)
        d["lines"] = json.loads(d["lines"])
        d["reasons"] = json.loads(d["reasons"])
        d["checks"] = json.loads(d.pop("checks_json"))
        return d

    def all_invoices(self):
        return [self._invoice_row(r) for r in
                self.db.execute("SELECT * FROM invoices ORDER BY date, number")]

    def invoices_for_supplier(self, sid, exclude_id=None):
        rows = self.db.execute(
            "SELECT * FROM invoices WHERE supplier_id=?", (sid,)).fetchall()
        out = []
        for r in rows:
            d = self._invoice_row(r)
            if exclude_id and d["id"] == exclude_id:
                continue
            out.append(d)
        return out

    def invoices_for_po(self, po_id, exclude_id=None):
        if not po_id:
            return []
        rows = self.db.execute(
            "SELECT * FROM invoices WHERE po_id=? AND status != 'BLOCKED'",
            (po_id,)).fetchall()
        out = []
        for r in rows:
            d = self._invoice_row(r)
            if exclude_id and d["id"] == exclude_id:
                continue
            out.append(d)
        return out

    # -- payment runs -------------------------------------------------------------
    def save_payment_run(self, pack):
        cur = self.db.execute(
            "INSERT INTO payment_runs (created_at, pack) VALUES (?,?)",
            (_now(), json.dumps(pack)))
        self.db.commit()
        self.log("payment_run_proposed", f"run {cur.lastrowid}: "
                                         f"{pack['count']} invoices, {pack['total_cents']} cents")
        return cur.lastrowid

    def approve_payment_run(self, run_id, approver):
        self.db.execute(
            "UPDATE payment_runs SET approved_by=?, approved_at=? WHERE id=?",
            (approver, _now(), run_id))
        self.db.commit()
        self.log("payment_run_approved", f"run {run_id} approved by {approver}")

    def latest_payment_run(self):
        r = self.db.execute(
            "SELECT * FROM payment_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        d = dict(r)
        d["pack"] = json.loads(d["pack"])
        return d
