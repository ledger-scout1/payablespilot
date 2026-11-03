# PayablesPilot

**Track 2 — Powering the Future of Work (Finance & Accounting)**
**Vultr: Agent Rush Hackathon, 3–8 November 2026**

PayablesPilot is an accounts-payable operations agent for a small finance
team. It runs the pre-payment control workflow that stops the three ways
companies lose money on supplier invoices: paying a duplicate, paying for
goods that never arrived (or at a price nobody agreed), and paying a
fraudster who swapped the bank details on an invoice.

## The workflow

1. **Intake** — an invoice arrives (web form or JSON API) and is evaluated
   immediately.
2. **Three-way match** — invoice lines are matched against the purchase
   order and the goods receipt: quantities (cumulative across *all*
   invoices on the PO, so a PO can never be billed beyond what was
   received) and unit prices within a configurable tolerance
   (2% or £1.00, whichever is greater).
3. **Duplicate prevention** — an exact invoice-number re-submission is
   blocked outright; a same-amount invoice from the same supplier within
   7 days is held as a possible duplicate.
4. **Bank-detail control** — the bank account on the invoice must equal
   the supplier master record. A mismatch is held as a possible
   payment-diversion attempt.
5. **Exception queue** — every failure lands here with the precise rule,
   the observed value and the expected value, plus a drafted supplier
   follow-up (local templates, no external AI — the verdict is always
   deterministic, never an LLM's opinion).
6. **Payment pack** — matched invoices are grouped into a proposed
   payment run ordered by due date (from supplier terms), with totals and
   overdue flags. A human approves the run. **PayablesPilot never
   executes payments.**

Every decision is written to an audit trail with UTC timestamps, and each
invoice page shows every check that was run, so any decision can be
re-performed by hand.

## Architecture

- `engine.py` — the deterministic rule engine (pure functions, no I/O).
  This is the product's brain: match, duplicates, bank control, payment
  pack builder, follow-up drafter.
- `store.py` — SQLite persistence (suppliers, POs, goods receipts,
  invoices + decisions, payment runs, audit trail). Money is integer
  cents end-to-end.
- `app.py` — the web app: dashboard, invoice list and per-invoice check
  sheet, exception queue, payment pack with human approval, intake form,
  audit page, plus a JSON API (`/api/invoices`, `/api/pos`, `/api/grns`,
  `/api/suppliers`, `/api/pack/generate`, `/api/pack/approve`,
  `/api/state`).
- `seed.py` — a labelled demonstration dataset in which every rule fires
  at least once (2 clean matches, 6 exceptions, 1 blocked duplicate).
- `tests/` — unit tests for every rule, incl. the cumulative-quantity
  control and tolerance boundaries.

**Stack: Python 3 standard library only** — no packages to install, no
external services to call, nothing that can break the night before
judging. Runs as a single process behind a systemd unit.

## Vultr services used

- **Vultr Cloud Compute (Regular Performance, 1 vCPU / 1 GB)** — the VM
  is the central control point: it hosts the app, the database and the
  audit trail for the whole workflow.
- Vultr account credits from the hackathon gift code fund the VM; the
  instance is destroyed automatically before the credits expire.

Serverless Inference is intentionally **not** used: the financial
controls must be deterministic and reproducible, so the verdict engine is
rules-based by design. Follow-up messages are template drafts reviewed by
a human, which also keeps the app fully functional with zero external
dependencies.

## Run it

```bash
python3 app.py            # serves on :8080, seeds the demo dataset
python3 -m unittest discover -s tests
```

Environment: `PORT` (default 8080), `PAYABLESPILOT_DB` (default
`payablespilot.db`).

## Licence

MIT — see `LICENSE`.
