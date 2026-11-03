#!/usr/bin/env python3
"""PayablesPilot — accounts-payable operations agent.

A multi-step, deterministic pre-payment control workflow:
  invoice intake -> three-way match (invoice / PO / goods receipt)
  -> duplicate prevention -> bank-detail fraud check
  -> exception queue with drafted supplier follow-ups
  -> proposed payment pack for HUMAN approval (never auto-pays).

Runs on a single Vultr Cloud Compute VM. Python standard library only:
no packages to install, nothing external to call.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from engine import MatchConfig, build_payment_pack, draft_followup, evaluate_invoice
from store import Store

DB_PATH = os.environ.get("PAYABLESPILOT_DB", "payablespilot.db")
PORT = int(os.environ.get("PORT", "8080"))
CONFIG = MatchConfig()

store = Store(DB_PATH)
DB_LOCK = threading.Lock()  # serialize requests: single shared SQLite connection


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def process_invoice(inv):
    supplier = store.get_supplier(inv["supplier_id"])
    if supplier is None:
        raise ValueError(f"unknown supplier {inv['supplier_id']}")
    po = store.get_po(inv.get("po_id"))
    grn = store.get_grn_for_po(inv.get("po_id")) if po else None
    priors = store.invoices_for_supplier(inv["supplier_id"], exclude_id=inv["id"])
    po_priors = store.invoices_for_po(inv.get("po_id"), exclude_id=inv["id"])
    verdict = evaluate_invoice(inv, supplier, po, grn, priors, CONFIG,
                               po_prior_invoices=po_priors)
    store.put_invoice(inv, verdict)
    return verdict


def current_pack():
    invoices = store.all_invoices()
    matched = [i for i in invoices if i["status"] == "MATCHED"]
    suppliers = {s["id"]: s for s in store.all_suppliers()}
    return build_payment_pack(matched, suppliers, date.today())


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------

def money(cents):
    return f"£{cents // 100:,}.{cents % 100:02d}"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;
background:#f6f7f9;color:#17202a}
header{background:#0b2545;color:#fff;padding:14px 22px}
header h1{margin:0;font-size:20px} header p{margin:2px 0 0;color:#bcd;font-size:13px}
nav{background:#13315c;padding:8px 22px} nav a{color:#dbe7ff;margin-right:16px;
text-decoration:none;font-size:14px}
main{max-width:1060px;margin:0 auto;padding:20px}
.cards{display:flex;gap:12px;flex-wrap:wrap}
.card{background:#fff;border:1px solid #dde3ea;border-radius:10px;padding:14px 18px;flex:1;min-width:150px}
.card b{font-size:24px;display:block}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #dde3ea}
th,td{padding:8px 10px;border-bottom:1px solid #edf1f5;text-align:left;font-size:14px}
.chip{padding:2px 8px;border-radius:20px;font-size:12px;font-weight:600}
.MATCHED{background:#e2f4e5;color:#146c2e}.EXCEPTION{background:#fdf0d5;color:#8a5a00}
.BLOCKED{background:#fbe3e3;color:#932020}
.reason{background:#eef2f7;border:1px solid #cfd8e3;border-radius:6px;padding:1px 6px;
font-size:12px;margin-right:4px}
section{background:#fff;border:1px solid #dde3ea;border-radius:10px;padding:16px 18px;margin:16px 0}
input,select,textarea{padding:7px;border:1px solid #b9c4d0;border-radius:6px;font-size:14px}
button{background:#0b5ed7;color:#fff;border:0;padding:8px 14px;border-radius:6px;cursor:pointer}
pre{white-space:pre-wrap;background:#f2f5f8;padding:10px;border-radius:8px;font-size:13px}
.small{color:#5b6b7c;font-size:12px}
.demo{background:#fff3cd;border:2px solid #e0a800;border-radius:10px;
padding:12px 16px;margin:16px 0;font-size:15px}
"""


def page(title, body):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — PayablesPilot</title><style>{CSS}</style></head><body>
<header><h1>PayablesPilot</h1><p>Accounts-payable control agent — three-way match,
duplicate prevention, exception queue, human-approved payment packs.</p></header>
<nav><a href="/">Dashboard</a><a href="/invoices">Invoices</a>
<a href="/exceptions">Exception queue</a><a href="/pack">Payment pack</a>
<a href="/intake">Invoice intake</a><a href="/audit">Audit trail</a></nav>
<main>{body}</main></body></html>"""


def invoice_rows(invoices):
    rows = []
    suppliers = {s["id"]: s["name"] for s in store.all_suppliers()}
    for i in invoices:
        reasons = " ".join(f'<span class="reason">{esc(r)}</span>' for r in i["reasons"])
        rows.append(
            f"<tr><td><a href='/invoice/{esc(i['id'])}'>{esc(i['number'])}</a></td>"
            f"<td>{esc(suppliers.get(i['supplier_id'], i['supplier_id']))}</td>"
            f"<td>{esc(i['date'])}</td><td>{money(i['total_cents'])}</td>"
            f"<td><span class='chip {i['status']}'>{i['status']}</span></td>"
            f"<td>{reasons}</td></tr>")
    return ("<table><tr><th>Number</th><th>Supplier</th><th>Date</th>"
            "<th>Total</th><th>Decision</th><th>Reasons</th></tr>"
            + "".join(rows) + "</table>")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
DEMO_NOTICE = """<div class="demo">⚠ <b>DEMONSTRATION DATA</b> — every invoice,
supplier and bank account shown here is fictional test data. Approving a
payment pack is <b>simulated</b>: it only records the human decision in the
audit trail and <b>does not initiate any bank transfer</b>. PayablesPilot
never executes payments.</div>
"""


def dashboard():
    invoices = store.all_invoices()
    matched = [i for i in invoices if i["status"] == "MATCHED"]
    exceptions = [i for i in invoices if i["status"] != "MATCHED"]
    pack = current_pack()
    overdue = [p for p in pack["items"] if p["overdue"]]
    body = f"""
{DEMO_NOTICE}<div class="cards">
  <div class="card"><b>{len(invoices)}</b>invoices processed</div>
  <div class="card"><b>{len(matched)}</b>matched &amp; payable</div>
  <div class="card"><b>{len(exceptions)}</b>on hold (exceptions)</div>
  <div class="card"><b>{money(pack['total_cents'])}</b>proposed payment pack</div>
  <div class="card"><b>{len(overdue)}</b>overdue in pack</div>
</div>
<section><h2>How it works</h2>
<p>Every invoice passes deterministic rules before it can be paid:
three-way match against its purchase order and goods receipt (quantities
and unit prices, with a configurable tolerance), exact and near-duplicate
prevention, and a bank-account check against the supplier master record —
the control that stops payment-diversion fraud. Failures land in the
exception queue with the exact rule values and a drafted supplier
follow-up. Matched invoices are grouped into a payment pack that a human
approves. <b>PayablesPilot never executes payments.</b></p></section>
<section><h2>Latest invoices</h2>{invoice_rows(invoices[-8:])}</section>"""
    return page("Dashboard", body)


def invoices_page():
    return page("Invoices", "<section><h2>All invoices</h2>"
                + invoice_rows(store.all_invoices()) + "</section>")


def invoice_detail(inv_id):
    inv = next((i for i in store.all_invoices() if i["id"] == inv_id), None)
    if inv is None:
        return None
    checks = "".join(
        f"<tr><td>{'✅' if c['ok'] else '❌'}</td><td>{esc(c['name'])}</td>"
        f"<td>{esc(c['observed'])}</td><td>{esc(c['expected'])}</td></tr>"
        for c in inv["checks"])
    reasons = " ".join(f'<span class="reason">{esc(r)}</span>' for r in inv["reasons"])
    body = f"""<section><h2>Invoice {esc(inv['number'])}</h2>
<p>Status: <span class="chip {inv['status']}">{inv['status']}</span> {reasons}</p>
<p class="small">Decided at {esc(inv['decided_at'])} (UTC) by the deterministic
rule engine. Every check below records the observed value and the expected
value, so the decision can be re-performed by hand.</p>
<table><tr><th></th><th>Check</th><th>Observed</th><th>Expected</th></tr>{checks}</table>
</section>"""
    return page(f"Invoice {inv['number']}", body)


def exceptions_page():
    invoices = [i for i in store.all_invoices() if i["status"] != "MATCHED"]
    suppliers = {s["id"]: s for s in store.all_suppliers()}
    blocks = []
    for i in invoices:
        drafts = []
        for r in i["reasons"]:
            d = draft_followup(r, i, suppliers[i["supplier_id"]])
            drafts.append(f"<p><b>Draft follow-up — {esc(d['subject'])}</b></p>"
                          f"<pre>{esc(d['body'])}</pre>"
                          f"<p class='small'>Draft only — a human reviews and sends.</p>")
        blocks.append(
            f"<section><h3>{esc(i['number'])} — {esc(suppliers[i['supplier_id']]['name'])} "
            f"— {money(i['total_cents'])}</h3>"
            f"<p><span class='chip {i['status']}'>{i['status']}</span> "
            + " ".join(f'<span class="reason">{esc(r)}</span>' for r in i["reasons"])
            + "</p>" + "".join(drafts) + "</section>")
    return page("Exception queue",
                "<h2>Exception queue</h2>" + ("".join(blocks) or "<p>No exceptions.</p>"))


def pack_page():
    run = store.latest_payment_run()
    pack = run["pack"] if run else current_pack()
    items = "".join(
        f"<tr><td>{esc(p['due_date'])}{' (overdue)' if p['overdue'] else ''}</td>"
        f"<td>{esc(p['supplier_name'])}</td><td>{esc(p['invoice_number'])}</td>"
        f"<td>{esc(p['bank_account'])}</td><td>{money(p['amount_cents'])}</td></tr>"
        for p in pack["items"])
    approval = ""
    if run:
        if run["approved_by"]:
            approval = (f"<p>✅ Approved by {esc(run['approved_by'])} at "
                        f"{esc(run['approved_at'])} (UTC). Approval records the human "
                        f"decision only — PayablesPilot does not move money.</p>")
        else:
            approval = f"""<form method="post" action="/api/pack/approve">
<input type="hidden" name="run_id" value="{run['id']}">
<label>Approver name <input name="approver" required></label>
<button type="submit">Approve payment run #{run['id']}</button></form>"""
    body = f"""{DEMO_NOTICE}<section><h2>Proposed payment pack</h2>
<p>{pack['count']} matched invoice(s), total <b>{money(pack['total_cents'])}</b>,
generated {esc(pack['generated_on'])}. This is a proposal: a finance team
member approves it before anything is scheduled in the bank.</p>
<table><tr><th>Due</th><th>Supplier</th><th>Invoice</th><th>Pay to</th><th>Amount</th></tr>
{items}</table>{approval}
<form method="post" action="/api/pack/generate" style="margin-top:12px">
<button type="submit">Regenerate pack from currently matched invoices</button></form>
</section>"""
    return page("Payment pack", body)


INTAKE_JS = """
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const lines = document.getElementById('lines').value.trim().split('\\n')
    .filter(Boolean).map(s => { const [sku, qty, price] = s.split(',').map(x => x.trim());
      return {sku, qty: parseInt(qty), unit_price_cents: parseInt(price)}; });
  const total = lines.reduce((a, l) => a + l.qty * l.unit_price_cents, 0);
  const inv = { id: 'inv-' + Date.now(), supplier_id: v('supplier_id'),
    number: v('number'), po_id: v('po_id') || null, date: v('date'),
    bank_account: v('bank_account'), lines, total_cents: total };
  const r = await fetch('/api/invoices', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(inv)});
  const out = await r.json();
  document.getElementById('out').textContent =
    JSON.stringify(out, null, 2);
  function v(id){ return document.getElementById(id).value; }
});
"""


def intake_page():
    suppliers = "".join(
        f"<option value='{esc(s['id'])}'>{esc(s['name'])}</option>"
        for s in store.all_suppliers())
    body = f"""<section><h2>Invoice intake</h2>
<p class="small">Lines format, one per line: <code>SKU, quantity, unit price in cents</code>.
The invoice is evaluated by the rule engine as soon as it is submitted.</p>
<form id="f">
<p><label>Supplier <select id="supplier_id">{suppliers}</select></label></p>
<p><label>Invoice number <input id="number" required></label>
<label>PO reference <input id="po_id"></label></p>
<p><label>Date <input id="date" type="date" required></label>
<label>Bank account on invoice <input id="bank_account" required></label></p>
<p><label>Lines<br><textarea id="lines" rows="4" cols="48"
placeholder="WIDGET-A, 10, 500"></textarea></label></p>
<button type="submit">Submit &amp; evaluate</button></form>
<pre id="out"></pre></section>
<script>{INTAKE_JS}</script>"""
    return page("Invoice intake", body)


def audit_page():
    rows = "".join(
        f"<tr><td>{esc(a['at'])}</td><td>{esc(a['event'])}</td><td>{esc(a['detail'])}</td></tr>"
        for a in store.audit_trail())
    return page("Audit trail",
                "<section><h2>Audit trail</h2><table><tr><th>When (UTC)</th>"
                "<th>Event</th><th>Detail</th></tr>" + rows + "</table></section>")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep stdout clean
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, indent=1), "application/json")

    def do_GET(self):
        with DB_LOCK:
            self._get()

    def _get(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._json(200, {"status": "ok"})
        if path == "/":
            return self._send(200, dashboard())
        if path == "/invoices":
            return self._send(200, invoices_page())
        if path.startswith("/invoice/"):
            html = invoice_detail(path.split("/invoice/", 1)[1])
            return self._send(200, html) if html else self._send(404, "not found")
        if path == "/exceptions":
            return self._send(200, exceptions_page())
        if path == "/pack":
            return self._send(200, pack_page())
        if path == "/intake":
            return self._send(200, intake_page())
        if path == "/audit":
            return self._send(200, audit_page())
        if path == "/api/state":
            return self._json(200, {
                "suppliers": store.all_suppliers(),
                "invoices": store.all_invoices(),
                "pack": current_pack(),
                "latest_run": store.latest_payment_run(),
            })
        return self._send(404, "not found")

    def do_POST(self):
        with DB_LOCK:
            self._post()

    def _post(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            if path == "/api/invoices":
                inv = json.loads(raw or b"{}")
                verdict = process_invoice(inv)
                return self._json(201, {"invoice": inv["id"],
                                        "status": verdict.status,
                                        "reasons": verdict.reasons,
                                        "checks": [vars(c) for c in verdict.checks]})
            if path == "/api/suppliers":
                s = json.loads(raw or b"{}")
                store.put_supplier(s)
                return self._json(201, {"supplier": s["id"]})
            if path == "/api/pos":
                po = json.loads(raw or b"{}")
                return self._json(201, store.put_po(po))
            if path == "/api/grns":
                g = json.loads(raw or b"{}")
                store.put_grn(g)
                return self._json(201, {"grn": g["id"]})
            if path == "/api/pack/generate":
                run_id = store.save_payment_run(current_pack())
                self.send_response(303)
                self.send_header("Location", "/pack")
                self.end_headers()
                return
            if path == "/api/pack/approve":
                form = parse_qs(raw.decode())
                store.approve_payment_run(int(form["run_id"][0]),
                                          form["approver"][0])
                self.send_response(303)
                self.send_header("Location", "/pack")
                self.end_headers()
                return
        except Exception as e:  # surface errors as JSON, never crash the server
            return self._json(400, {"error": str(e)})
        return self._send(404, "not found")


def main():
    if not store.all_suppliers():
        import seed
        seed.load_demo(store, process_invoice)
        store.log("demo_seed_loaded", "labelled demonstration dataset")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"PayablesPilot listening on :{PORT} (db: {DB_PATH})")
    server.serve_forever()


if __name__ == "__main__":
    main()
