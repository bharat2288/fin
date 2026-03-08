---
type: pipeline
project: fin
date: 2026-03-08
created_by: architect
---
# [[fin-home|fin]] — Pipeline
*[[dev-hub|Hub]]*

> Living document. Tracks all remaining feature work, bugs, evaluations, and vision items.
> Last updated: 2026-03-08
>
> **Convention:** Strikethrough (`~~item~~`) when DONE, keep in section for context.
> Sweep to Completed section when tiers get crowded.

---

## CRITICAL

*Empty*

---

## HIGH

| # | Item | Type | Status | Notes |
|---|------|------|--------|-------|
| 30 | ~~**Anomaly / frequency system**~~ | Feature | Done | `is_one_off` flag on services, checkbox in Edit Service modal, `1x` badge in accordion. Dashboard exclusion filter added. |
| 32 | **Import remaining Feb statements** | Data | Not started | Only 31 Feb txs — Allpress, SP Gas, etc. missing. Need DBS/UOB/Citi Feb exports. |
| 33 | **Code review** | Maintenance | Not started | app.py ~1700+ lines, app.js ~3400+ lines — growing, needs modularization review. |
| 37 | **Verify subscription figures** | Data | Not started | 16 subs with suspicious SGD/USD FX ratios after billing model migration. Review amount + currency via Edit modal. |

---

## MEDIUM

| # | Item | Type | Status | Notes |
|---|------|------|--------|-------|
| 31 | **Learn merchant rules priority/amount system** | Learning | Not started | Understand priority, min_amount, max_amount. Study the Shopee dual-purpose example. |
| 9 | **Anomaly detection suggestions** | Feature | Not started | Flag transactions above threshold as potential anomalies |
| 23 | **Mark anomaly transactions** | Feature | Not started | Flag Mount Alvernia childbirth, IRAS tax, etc. as is_anomaly=1 |
| 34 | **Recurring payment detection (Phase 2)** | Feature | Not started | Scan transactions for untracked subscriptions automatically |
| 35 | ~~**Subscription inline editing**~~ | Feature | Done | Edit modal: all fields, category/card dropdowns from masters |
| 36 | ~~**Card→account_id FK migration**~~ | Refactor | Done | Subs use account_id FK instead of text card field; JOIN for display |

---

## FUTURE PHASE

| # | Item | Type | Status | Notes |
|---|------|------|--------|-------|
| 11 | **Budget setting and alerts** | Vision | Not started | Set monthly budgets per category, warn on overspend |
| 12 | **Spending forecasting** | Vision | Not started | Project future spend based on historical patterns |
| 13 | **Multi-currency / investment tracking** | Vision | Not started | Portfolio view, not just expenses |
| 15 | **Automatic statement import** | Vision | Not started | Watch folder or email parsing for new statements |
| 17 | **Subscription renewal reminders** | Vision | Not started | Alerts before renewal dates |
| 18 | **Receipt OCR** | Vision | Not started | Photo of receipt → transaction |
| 19 | **Expense splitting** | Vision | Not started | Shared expenses with partner |
| 20 | **Year-over-year comparison** | Vision | Not started | Same month last year vs. this year |
| 21 | **Tax-relevant expense tagging** | Vision | Not started | Tag for filing season |

---

## Completed

- ~~**PDF parser (DBS CC)**~~ — Working, tested on Jun 2024 statement
- ~~**CSV parser (DBS CC + bank)**~~ — Working, tested on 6 files
- ~~**SQLite schema + DB init**~~ — 58 categories, 170+ merchant rules
- ~~**Kickoff design doc**~~ — Design, status, pipeline written
- ~~**#1 Schema update**~~ — is_anomaly, Medical, Loan/EMI, Kids categories
- ~~**#2 Merchant rule expansion**~~ — 170+ rules, 1000+ transactions categorized
- ~~**#4 Monthly category breakdown**~~ — `summary.py` with filters
- ~~**#5 Statement commit workflow**~~ — `ingest.py --commit` pipeline
- ~~**#6 Bank PayNow/transfer handling**~~ — PayNow rules, transfers classified
- ~~**#3 Subscription migration**~~ — 43 subs from Excel
- ~~**#8 Subscription renewal tracking**~~ — Renewals view
- ~~**#7 Full-stack web app**~~ — Flask SPA on port 8450
- ~~**#14 Multi-bank parsers**~~ — Citi CSV, UOB bank+CC PDF
- ~~**#16 Web dashboard**~~ — Chart.js monthly bar + category donut, stat cards
- ~~**Batch Import UI**~~ — Drag-drop upload, auto-detect, preview, confirm
- ~~**Rent category**~~ — Citi Prestige rental payments
- ~~**Interactive chart-table linking**~~ — Click chart segments to filter tx table
- ~~**Searchable multi-select category dropdown**~~ — Custom component
- ~~**Stat cards redesign**~~ — Single month (15th cutoff) + % vs 3-month avg
- ~~**Category trend chart**~~ — Bar | Trend toggle, top 8 categories, MMM-YY labels
- ~~**Merchant rule priority + amount thresholds**~~ — Dual-purpose merchant support (Shopee)
- ~~**Admin category**~~ — Admin > Tax, Insurance, Bank Fees, Government
- ~~**Moom subcategories**~~ — 17 vendor-level subcats
- ~~**#22 Ingest all statement data**~~ — All bank statements imported
- ~~**#10 Duplicate detection**~~ — Implemented
- ~~**#29 Subscription management tab**~~ — 5th tab: view/filter/sort subs, stat cards, transaction enrichment, variable detection, FX rate, Last Paid linking
- ~~**Card number masking**~~ — All API responses mask 16-digit card numbers
- ~~**Edit Rule modal**~~ — Proper modal replacing browser prompt(), all fields editable
- ~~**Re-categorize All**~~ — Propagate rule changes to existing transactions
- ~~**Account Master**~~ — 6th tab: CRUD for cards/bank accounts, feeds dashboard filter + sub card dropdowns
- ~~**Subscription editing**~~ — Edit modal with all fields, category/card dropdowns from masters, delete
- ~~**Service Master + Nav Restructure**~~ — Services table, 3 main tabs + Import icon + Masters dropdown, accordion transaction view, anchor-based renewal dates, rule→service→category chain
- ~~**Deep linking**~~ — Service names clickable in txn table + subs table → Services tab accordion; category clickable in subs → By Category view
- ~~**Services dual view**~~ — By Service (flat accordion) + By Category (3-level: parent → subcategory → service) with filter dropdowns
- ~~**Service merge**~~ — Reassign txns/rules/subs from source→target, delete source. UI in Edit Service modal
- ~~**Bulk cleanup view**~~ — Heuristic filter for pattern-like names, inline rename, batch save
- ~~**Browser history**~~ — pushState/popstate for tab navigation, back button works across deep-links
- ~~**Re-categorize syncs rules**~~ — Syncs stale rule category_ids to service categories before re-running
- ~~**Unlinked transaction fix**~~ — 10 new services for identifiable merchants, PayNow Transfer catch-all (priority -10)
- ~~**Sub names from services JOIN**~~ — Subscription display names resolved live from services table
- ~~**Dashboard service column**~~ — Service name with deep-link in txn table, sortable
- ~~**Account filter short names**~~ — Dashboard dropdown uses short_name
- ~~**USD↔SGD auto-calc**~~ — Both add/edit subscription forms
- ~~**Renewal auto-advance**~~ — _advance_renewal helper for past dates
- ~~**Responsive subs table**~~ — Column hiding + horizontal scroll
- ~~**Subscription links**~~ — 25 links updated from Excel hyperlinks
- ~~**Account Master**~~ — 6th tab: CRUD for cards/bank accounts, inline edit, FK-protected delete

---

## Notes

- v1 focus: get data in, categorize it, see where money goes
- Dual interface: web UI for import/dashboard + Claude CLI for bulk operations
- Moom (business) expenses always tracked but separated in views
- Multi-bank: DBS (CSV/PDF), Citi (CSV), UOB (PDF bank + CC)
- DBS Vantage MK/BS split supported via fingerprint cross-referencing
