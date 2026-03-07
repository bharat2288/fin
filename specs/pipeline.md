---
type: pipeline
project: fin
date: 2026-03-07
created_by: architect
---
# [[fin-home|fin]] — Pipeline
*[[dev-hub|Hub]]*

> Living document. Tracks all remaining feature work, bugs, evaluations, and vision items.
> Last updated: 2026-03-07
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
| 3 | ~~**Subscription migration from Excel**~~ | Feature | DONE | 43 subs migrated, `subs.py` CLI viewer with --renewals |
| 7 | ~~**Visualization: monthly spending charts**~~ | Feature | DONE | Full-stack Flask SPA with Chart.js (monthly bar + category donut) |
| 14 | ~~**Other bank support (Citi, UOB, OCBC)**~~ | Feature | DONE | Parsers built: `parse_citi_csv.py`, `parse_uob.py` (bank + CC PDF) |
| 16 | ~~**Web dashboard**~~ | Feature | DONE | 4-tab SPA: Dashboard, Import, History, Merchant Rules on port 8450 |
| 22 | **Ingest new statement data** | Feature | Not started | DBS Woman's World, Citi Prestige/Rewards, UOB bank/CC, new Vantage MK+BS — parsers ready, need to import via UI |
| 24 | ~~**Category Master with subcategories**~~ | Feature | DONE | `parent_id` FK on categories, POST /api/categories, hierarchical dropdowns |
| 25 | ~~**Sortable transaction table**~~ | Feature | DONE | Click column headers, sort arrows, backend whitelist sort params |
| 26 | ~~**Unified filter system**~~ | Feature | DONE | `buildFilterParams()` shared by charts + transactions, year/month dropdowns replace date inputs |
| 27 | ~~**Filter presets: year & month dropdowns**~~ | Feature | DONE | Year/month selects, auto-weekly granularity when month selected, chart title updates |
| 28 | ~~**Fix Personal/Moom filter toggle**~~ | Bug | DONE | Added `moom_only` param to `_build_filters()`, frontend passes it correctly |

---

## MEDIUM

| # | Item | Type | Status | Notes |
|---|------|------|--------|-------|
| 8 | ~~**Subscription renewal tracking**~~ | Feature | DONE | `subs.py --renewals` shows upcoming + overdue |
| 9 | **Anomaly detection suggestions** | Feature | Not started | Flag transactions above a threshold as potential anomalies for user confirmation |
| 10 | **Duplicate detection** | Feature | Not started | Prevent re-importing same statement; warn on overlapping date ranges |
| 23 | **Mark anomaly transactions** | Feature | Not started | Flag Mount Alvernia childbirth, IRAS tax payment, etc. as is_anomaly=1 |

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
- ~~**CSV parser (DBS CC + bank)**~~ — Working, tested on 6 files (Oct 2025 - Feb 2026)
- ~~**SQLite schema + DB init**~~ — 21 categories, 172 merchant rules, is_anomaly column
- ~~**Kickoff design doc**~~ — Design, status, pipeline written
- ~~**#1 Schema update**~~ — Added is_anomaly, Medical, Loan/EMI, Kids categories
- ~~**#2 Merchant rule expansion**~~ — 172 rules, 338 transactions fully categorized (0 uncategorized)
- ~~**#4 Monthly category breakdown**~~ — `summary.py` with month range, --personal, --no-anomaly flags
- ~~**#5 Statement commit workflow**~~ — `ingest.py --commit` pipeline: parse → categorize → store
- ~~**#6 Bank PayNow/transfer handling**~~ — PayNow rules, MEP/SI/bank-to-bank classified as transfers
- ~~**#3 Subscription migration**~~ — 43 subs from Excel, `migrate_subs.py` + `subs.py` CLI
- ~~**#8 Subscription renewal tracking**~~ — `subs.py --renewals` with overdue/upcoming views
- ~~**#7 Full-stack web app**~~ — Flask SPA on port 8450: Dashboard, Import, History, Merchant Rules
- ~~**#14 Multi-bank parsers**~~ — Citi CSV (`parse_citi_csv.py`), UOB bank+CC PDF (`parse_uob.py`)
- ~~**#16 Web dashboard**~~ — Chart.js monthly bar + category donut, stat cards, paginated tx table
- ~~**Batch Import UI**~~ — Drag-drop upload, auto-detect format, preview with category overrides, confirm flow
- ~~**Rent category**~~ — Added for Citi Prestige rental payments
- ~~**Registered in DSM**~~ — Port 8450, "Personal Finance"

---

## Notes

- v1 focus: get data in, categorize it, see where money goes
- Dual interface: web UI for import/dashboard + Claude CLI for bulk operations
- Moom (business) expenses always tracked but separated in views
- Multi-bank: DBS (CSV/PDF), Citi (CSV), UOB (PDF bank + CC)
- DBS Vantage MK/BS split supported via fingerprint cross-referencing
