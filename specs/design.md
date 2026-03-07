---
type: design
project: fin
date: 2026-03-07
created_by: architect
---
# [[fin-home|fin]] — Design
*[[dev-hub|Hub]]*

> Living document: what we're building and why.
> Update when scope changes, architecture evolves, or constraints are discovered.

---

## Purpose

Personal finance tracker that gives visibility into monthly spending by category. The core problem: zero handle on where money goes each month. Everything currently lives in a static Excel workbook (subscriptions, scattered expense sheets) disconnected from actual bank/CC statements. fin solves this by making Claude the ingestion interface — drop a statement, discuss categorization, store structured data, see trends.

---

## Current Scope

- **Full-stack web app**: Flask backend (port 8450) + vanilla JS frontend. 4-tab SPA: Dashboard, Import, History, Merchant Rules. Registered in Dev Server Manager. Replaces CLI-only workflow with visual, interactive experience. Claude remains available for suggesting bulk categorizations.
- **Statement ingestion (Import tab)**: Multi-file drag-drop. Auto-detect bank/format per file (DBS CSV, DBS PDF, Citi CSV, UOB PDF). Parse, group by account, auto-categorize via merchant rules. Interactive preview table with editable categories, skip checkboxes, status badges. Confirm to commit.
- **Dashboard tab**: Stat cards (total spend, personal, Moom, # transactions), filter bar (date range, personal/Moom/all, anomaly toggle, by category/by account), monthly stacked bar chart, category donut, filterable transaction table. Chart.js for visualization. Inspired by moom Sales Dashboard patterns.
- **Import History tab**: Past imports with status, drill into line-level detail.
- **Merchant Rules tab**: Browse/search/edit/add pattern→category mappings. The persistent knowledge base that makes future imports smarter.
- **Expense categorization**: Match merchant descriptions to categories using a rules engine (pattern matching: contains, startswith, exact). Unknown merchants flagged for user review in Import preview. Once categorized, the rule is remembered permanently.
- **Multi-bank support**: DBS (CSV + PDF), Citi (CSV), UOB (PDF). Parser auto-detection from file contents.
- **Subscription tracker**: Migrated from existing Excel "Subs" sheet. Tracks service name, category, amount (SGD/USD), billing frequency, card, renewal date, status (active/deactivated). CLI interface (`subs.py`).

---

## Architecture

```
(Statement Files: CSV, PDF)
        │
        ↓
[Frontend SPA — localhost:8450]
├── Dashboard tab    ← Chart.js charts, stat cards, filters
├── Import tab       ← Drag-drop, preview table, confirm
├── History tab      ← Past imports, drill-down
└── Merchant Rules   ← CRUD for pattern→category mappings
        │
        ↓
[Flask Backend — app.py]
├── /api/import/*        ← Upload, parse, confirm
├── /api/dashboard/*     ← Summary, monthly, categories
├── /api/transactions    ← Paginated list
├── /api/rules/*         ← Merchant rules CRUD
└── /api/categories      ← Reference data
        │
        ↓
[Parser Layer]
├── parse_dbs.py         ← DBS PDF (CC + bank)
├── parse_dbs_csv.py     ← DBS CSV (CC + bank)
├── parse_citi_csv.py    ← Citi CSV (CC)
└── parse_uob.py         ← UOB PDF (bank + CC)
        │
        ↓
[Categorization — db.py]
merchant_rules table, pattern matching
        │
        ↓
[Storage — fin.db (SQLite)]
transactions, batch_imports, subscriptions,
categories, merchant_rules, accounts, statements
```

**Key files:**
- `app.py` — Flask application, all API routes
- `static/index.html` — SPA shell (4-tab structure)
- `static/app.js` — All frontend JS (tabs, upload, charts, CRUD)
- `static/styles.css` — Design system styles
- `static/chart.min.js` — Chart.js local bundle
- `db.py` — Database init, seeding, categorization engine
- `parse_dbs.py` — PDF parser for DBS CC and bank statements
- `parse_dbs_csv.py` — CSV parser for DBS transaction exports
- `parse_citi_csv.py` — CSV parser for Citi CC exports
- `parse_uob.py` — PDF parser for UOB bank + CC statements
- `ingest.py` — CLI ingestion (kept for Claude-assisted workflow)
- `schema.sql` — Database schema
- `fin.db` — SQLite database (gitignored)

**Data flow (frontend):**
1. User drags statement files into Import tab
2. Backend auto-detects format, parses, groups by account, auto-categorizes
3. Frontend shows interactive preview — user fixes uncategorized via dropdown
4. User confirms → transactions + new merchant rules saved to DB
5. Dashboard tab shows updated charts and summaries

**Data flow (Claude-assisted):**
1. User asks Claude to import/categorize statements
2. Claude runs parsers and suggests merchant→category mappings
3. User confirms via conversation
4. Claude commits via CLI scripts or API

**Dual interface:** The frontend handles routine self-service imports. Claude remains available for bulk categorization suggestions, anomaly flagging, and exploratory analysis. Neither replaces the other.

**Design reference:** Frontend adapted from moom-order-forecasting Batch Import (3-tab UI) + Sales Dashboard (chart patterns). See [[decision-frontend-architecture]] for ADRs.

---

## Data Model

### Categories

| Category | Personal? | Notes |
|----------|-----------|-------|
| Groceries | Yes | Supermarkets, food delivery (FoodPanda), health food stores |
| Dining | Yes | Restaurants, cafes, coffee, fast food, bubble tea |
| Transport | Yes | Grab, Uber, BUS/MRT, EV charging (Kigo, ChargePoint, Tesla), fuel (Shell, SPC), parking, NETS top-up |
| Shopping | Yes | Amazon, Shopee, Lazada, Takashimaya, department stores, online retail |
| Health & Beauty | Yes | Guardian, Watsons, Aesop, spas, skincare |
| Medical | Yes | Hospitals, clinics, specialists, procedures |
| Entertainment | Yes | Netflix, Spotify, YouTube, Tesla Premium Connectivity |
| Utilities | Yes | Singtel, MyRepublic, SP Group/Gas, 1Password, power/water |
| Subscriptions | Yes | Claude, ChatGPT, Notion, TradingView, Remnote, Microsoft 365, Oura, RunPod |
| Insurance | Yes | Manulife, Prudential, Singapore Life, AIA, Great Eastern |
| Loan/EMI | Yes | Car loan (Tesla), home loan, fixed obligations |
| Travel | Yes | Flights, hotels, duty free, visas |
| Pet | Yes | Goodwoof, Pet Lovers Centre |
| Home | Yes | Dyson, furniture, appliance repairs, home goods |
| Personal | Yes | Miscellaneous personal (Zerodha brokerage, etc.) |
| Education | Yes | Brilliant, Welch Labs, courses |
| Fitness | Yes | Barry's Bootcamp, golf (SICC, Vin Golf, Bukit Timah, Orchid CC), Vive Active, UPlay |
| Kids | Yes | Nanny/childcare, Mothers Work, Pramfox, pediatrician |
| Gifts & Donations | Yes | |
| Moom | No | Google Ads, Alibaba, LoyaltyLion, Xero, GSuite, Klaviyo, Shopify, Moom Health |
| Other | Yes | Unmatched merchants, TBD items |

### Anomaly Flag

Transactions can be flagged as `is_anomaly = 1` for one-time or exceptional expenses (childbirth, medical procedures, large furniture purchases). This is a cross-cutting concern — any category can have anomalies. Visualizations support toggling anomalies on/off to show true recurring burn rate vs. total spend.

### Merchant Rules

Rules map merchant description patterns to categories. Three match types:
- `contains` — pattern appears anywhere in description (most common)
- `startswith` — description starts with pattern
- `exact` — exact match

Rules have a `confidence` field: `auto` (seeded/inferred) vs `confirmed` (user-verified). Longest pattern matches first to handle specificity (e.g., "BACHA COFFEE" matches before "COFFEE").

---

## Constraints

- **Technical**: Python 3.13+, Flask, SQLite, pdfplumber for PDF parsing, Chart.js for visualization. No external APIs or cloud services. All data local.
- **Bank support**: DBS (CSV + PDF), Citi (CSV), UOB (PDF). Auto-detection from file contents.
- **Workflow**: Dual interface — web frontend for self-service imports + dashboard; Claude for bulk categorization suggestions and analysis.
- **Scope**: Single household. SGD primary currency. Foreign transactions tracked but converted to SGD at statement rate.
- **Privacy**: All financial data stays local. fin.db is gitignored. No data leaves the machine.
- **Port**: 8450 (registered in Dev Server Manager).

---

## Future Ideas

> Captured during kickoff + brainstorm. Not in current scope.

- [ ] Budget setting and alerts (overspend warnings)
- [ ] Spending forecasting based on historical trends
- [ ] Multi-currency portfolio / investment tracking
- [ ] Automatic statement import (watch folder or email parsing)
- [ ] Subscription renewal reminders / alerts
- [ ] Receipt OCR (photo of receipt → transaction)
- [ ] Expense splitting (shared expenses with partner)
- [ ] Year-over-year comparison views
- [ ] Tax-relevant expense tagging for filing season
- [ ] Mobile-responsive layout (desktop-first for now)
- [ ] Subscriptions tab in frontend (currently CLI only)

---

## References

- [[pipeline]] — Feature backlog
- [[status]] — Current state
- Source data: `G:\My Drive\Personal Docs\Statements (Bank, Income)\`
- Subs sheet: `C:\Users\bhara\Downloads\Sg Home Exp.xlsx` (Subs tab)
