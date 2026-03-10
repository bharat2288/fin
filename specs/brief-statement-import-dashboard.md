---
type: brief
project: fin
date: 2026-03-07
created_by: brainstorm
---
# [[fin-home|fin]] — Statement Import & Dashboard
*[[dev-hub|Hub]]*
> Related: [[design|Design]] · [[decision-frontend-architecture|ADR: Frontend]] · [[pipeline|Backlog]] · [[brief-subscription-tab|Brief: Subs]] · [[status|Status]]

> Output of brainstorm session (2026-03-07).

---

## What

A full-stack web app (Flask + vanilla JS) that replaces the CLI ingestion workflow with an interactive UI for importing bank/CC statements, managing merchant categorization rules, and visualizing spending trends. Registered in Dev Server Manager alongside other project frontends.

## Why

The current workflow is opaque and Claude-mediated: download statements, point Claude to a folder, Claude runs scripts, results disappear into the database with no visibility. There's no way to see what's stored, review categorization, or self-serve routine imports. The static `dashboard.html` doesn't render charts reliably (file:// protocol issues). A proper frontend makes the data visible, the process self-service, and the output useful.

## User Story

When I download my monthly statements from DBS/Citi/UOB, I drag-drop them into the Import tab. The system auto-detects each file's bank and format, parses transactions, groups them by account, and auto-categorizes using merchant rules. I review the preview table — fixing any uncategorized merchants via dropdown, skipping irrelevant transactions — and hit Confirm to commit. The Dashboard tab immediately shows updated spending trends, category breakdowns, and monthly totals. When I notice a miscategorized merchant, I go to Merchant Rules and fix the pattern. Claude remains available for suggesting bulk categorizations on new merchants.

## Scope (v1)

### Dashboard Tab
- [ ] Stat cards row: total spend, personal spend, Moom spend, # transactions
- [ ] Filter bar: date range, personal/Moom/all toggle, anomaly include/exclude, by category/by account
- [ ] Monthly spending trend: stacked bar chart by category, monthly/quarterly toggle
- [ ] Category breakdown: donut chart with legend
- [ ] Transaction detail table below charts (filterable — click bar to filter)
- [ ] Chart library: Chart.js (local bundle, proven in project) or lightweight alternative

### Import Tab
- [ ] Upload configuration: drag-drop zone accepting multiple files (CSV, PDF)
- [ ] Auto-detect bank/format per file (DBS CSV, DBS PDF, Citi CSV, UOB PDF)
- [ ] Parse all files, group transactions by detected account
- [ ] Vantage MK/BS cardholder split via cross-reference with BS-only export
- [ ] Auto-categorize using merchant rules engine
- [ ] Interactive preview table per account group:
  - Checkbox (include/skip), Date, Description, Amount, Category (dropdown), Status badge
  - Editable category with autocomplete dropdown (all 21+ categories)
  - Status: categorized (green), uncategorized (orange), payment/transfer (gray)
- [ ] Stats bar: total transactions, categorized, uncategorized, skipped
- [ ] Confirm bar: review count, Discard / Confirm & Commit buttons
- [ ] On confirm: save transactions + create new merchant rules from user overrides

### History Tab
- [ ] Table of past imports: ID, Account, Filename, Lines (categorized/total), Status, Date
- [ ] Click to expand: line-level detail of what was imported
- [ ] Status values: preview, committed, failed

### Merchant Rules Tab
- [ ] Browse all rules grouped by category (accordion or filter)
- [ ] Search across pattern + category
- [ ] Edit: change category, match type (contains/startswith/exact), pattern
- [ ] Add new rule manually
- [ ] Delete rule
- [ ] Show confidence (auto vs confirmed)

### Backend (Flask)
- [ ] `POST /api/import/upload` — accept files, parse, auto-categorize, return preview
- [ ] `POST /api/import/confirm` — commit previewed transactions to DB
- [ ] `GET /api/import/history` — list past imports
- [ ] `GET /api/dashboard/summary` — stat card data with filters
- [ ] `GET /api/dashboard/monthly` — monthly category breakdown for charts
- [ ] `GET /api/dashboard/categories` — category totals for donut
- [ ] `GET /api/transactions` — paginated transaction list with filters
- [ ] `GET /api/rules` — list merchant rules
- [ ] `POST /api/rules` — add rule
- [ ] `PUT /api/rules/:id` — edit rule
- [ ] `DELETE /api/rules/:id` — delete rule
- [ ] `GET /api/categories` — list all categories (for dropdowns)

### New Parsers
- [ ] `parse_citi_csv.py` — Citi CC CSV format (headerless, negative=expense)
- [ ] `parse_uob.py` — UOB bank + CC PDF statements (pdfplumber)

### Data Model Changes
- [ ] Add "Rent" category
- [ ] Add `batch_imports` table (id, filenames, accounts, status, total_lines, categorized_lines, result_json, created_at)

## Out of Scope (captured for later)

- Budget setting and alerts
- Spending forecasting / projections chart
- Multi-currency portfolio / investment tracking
- Automatic statement import (watch folder / email parsing)
- Receipt OCR
- Expense splitting
- Year-over-year comparison
- Tax-relevant expense tagging
- Mobile-responsive layout (desktop-first)
- User authentication (single-user local app)

## Interaction Flow

### Import Flow
```
(Statement Files)
       │
       ↓
[Drag-Drop Zone] ──→ [POST /api/import/upload]
                              │
                              ↓
                     [Auto-Detect Format]
                     ├─ DBS CSV parser
                     ├─ DBS PDF parser
                     ├─ Citi CSV parser
                     └─ UOB PDF parser
                              │
                              ↓
                     [Group by Account]
                              │
                              ↓
                     [Categorize via Rules]
                     ├─ merchant_rules match
                     ├─ PayNow rules match
                     └─ unmatched → orange
                              │
                              ↓
                     [Return Preview JSON]
                              │
                              ↓
[Interactive Preview Table] ──→ User fixes categories
       │                              │
       │                              ↓
       │                     [New rules created
       │                      from overrides]
       │
       ↓
[Confirm & Commit] ──→ [POST /api/import/confirm]
                              │
                              ├─→ [Save transactions]
                              ├─→ [Save new rules]
                              └─→ [Save import record]
```

### Dashboard Flow
```
[Dashboard Tab] ──→ [GET /api/dashboard/*]
       │                    │
       ↓                    ↓
[Filter Bar] ──→ [Query DB with filters]
       │                    │
       ↓                    ↓
[Stat Cards]          [Chart Data]
[Bar Chart]           [Donut Chart]
[Transaction Table]
       │
       ↓
{Click bar} ──→ [Filter table to that month]
```

## Impact Analysis

**Classification:** Integrative (new app, reuses existing core logic)

**Existing code reused (not modified):**
- `parse_dbs.py` — DBS PDF parser, `ParsedTransaction`/`ParsedStatement` dataclasses
- `parse_dbs_csv.py` — DBS CSV parser
- `db.py` — `categorize_transaction()`, `get_connection()`, `init_db()`
- `schema.sql` — existing schema (additive changes only)
- `ingest.py` — `categorize_all()`, `save_transactions()`, `ensure_account()`, `ensure_statement()` logic moves to API

**New files:**
- `app.py` — Flask application, API routes
- `parse_citi_csv.py` — Citi CSV parser (already started)
- `parse_uob.py` — UOB PDF parser
- `static/index.html` — Frontend (4-tab SPA)
- `static/chart.min.js` — Chart.js bundle (already exists)

**Absorbed/replaced:**
- `dashboard.py` — chart generation logic moves to frontend
- `dashboard.html` — replaced by new frontend
- `summary.py` — query logic moves to dashboard API endpoints

**Data model changes:**
- New `batch_imports` table
- New "Rent" category in categories table
- No changes to existing tables

**Breaking changes:** None. CLI scripts (`ingest.py`, `summary.py`, `subs.py`) continue to work.

**New dependencies:**
- Flask (lightweight, no ORM needed — raw SQLite stays)
- No frontend dependencies beyond Chart.js (vanilla JS)

## Edge Cases Identified

1. **Duplicate imports** — Same file dropped twice → check file hash or statement_date + account_id uniqueness, warn user
2. **Multi-bank single drop** — 5 DBS CSVs + 1 Citi CSV → auto-detect each, group separately in preview
3. **Vantage MK/BS split** — Requires BS-only export in same batch alongside combined export → backend cross-references to tag cardholder
4. **Vantage without BS reference** — If only combined export dropped, all transactions tagged as "Vantage (combined)" — no split possible, warn user
5. **UOB PDF parsing fragility** — PDF text extraction can break across layouts → need robust line-by-line parsing with fallback error reporting
6. **Zero uncategorized** — If all merchants are known, skip straight to confirm (no friction for routine imports)
7. **New category needed mid-import** — User realizes they need a new category during preview → either add via Merchant Rules tab first, or allow inline "create category" in the dropdown

## Open Questions

- Chart library choice: Chart.js (already have bundle, proven) vs something lighter? Chart.js is fine for v1.
- Should the Flask app serve the frontend directly (static files) or use a separate static server? Flask static serving is simplest for v1.
- Port assignment: needs registration in Dev Server Manager. Check available ports.

## Risk Assessment

- **Effort:** Large — new Flask app, 4-tab frontend, 2 new parsers, API layer, chart integration
- **Risk:** Medium — core logic (parsing, categorization) is proven; risk is in the frontend interactivity and UOB PDF parsing
- **Reason:** The parsers and categorization engine are battle-tested. The new surface area is Flask plumbing + frontend JS. Reference implementation (moom Batch Import) provides a strong template to adapt from.

## Recommended Next Step

Run `/architect` to make structural decisions: Flask app layout, API design, frontend file organization, port assignment, and how to adapt the moom Batch Import codebase for fin's domain.
