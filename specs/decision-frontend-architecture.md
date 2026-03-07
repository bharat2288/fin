---
type: decision
project: fin
date: 2026-03-07
created_by: architect
---
# [[fin-home|fin]] — Frontend Architecture Decisions
*[[dev-hub|Hub]]*

> ADRs from architect session (2026-03-07). Covers the full-stack Statement Import & Dashboard feature.

---

## ADR 1: Application Framework

**Status:** Accepted

**Context:** fin needs a web server to serve a 4-tab frontend (Dashboard, Import, History, Merchant Rules) and handle API requests. Currently has no server infrastructure.

**Options Considered:**
- **Flask** — Minimal, well-known, synchronous, matches moom reference implementation
- **FastAPI** — Type hints, auto docs, modern async

**Decision:** Flask. Single-user local app with synchronous SQLite access. No need for async complexity. Matches the moom Batch Import reference pattern.

**Consequences:** Simple setup, no ORM (raw SQLite stays), but no auto-generated API docs. Acceptable for a personal tool.

---

## ADR 2: Port Assignment

**Status:** Accepted

**Context:** Dev Server Manager tracks all project ports. Current registry: 8000 (Thread Unroller), 8100 (Lit Processor), 8200 (Scholia), 9000 (DSM).

**Decision:** Port 8450 for fin backend. 8300 conflicts with moom-order-forecasting, 8400 with tweet-explorer.

---

## ADR 3: Frontend File Organization

**Status:** Accepted

**Context:** Moom Batch Import is a single index.html (~2300 lines). fin's 4-tab app will be larger (~3000+ lines JS).

**Options Considered:**
1. Single `index.html` with all JS inline (moom pattern)
2. `index.html` + separate `app.js` + `styles.css`
3. Multiple HTML files per tab

**Decision:** Option 2. HTML structure separate from JS logic. Still vanilla JS, no build step, no framework. Chart.js loaded from local bundle.

**File layout:**
```
fin/
├── app.py              # Flask app + all API routes
├── static/
│   ├── index.html      # SPA shell (4-tab structure)
│   ├── app.js          # All JS (tabs, upload, charts, CRUD)
│   ├── styles.css      # Styles (design system tokens)
│   └── chart.min.js    # Chart.js local bundle (already exists)
├── parse_dbs.py        # Existing DBS PDF parser
├── parse_dbs_csv.py    # Existing DBS CSV parser
├── parse_citi_csv.py   # New Citi CSV parser
├── parse_uob.py        # New UOB PDF parser
├── db.py               # Existing DB helpers (unchanged)
├── schema.sql          # Existing schema (+ batch_imports)
├── ingest.py           # CLI kept (not broken)
├── summary.py          # CLI kept
├── subs.py             # CLI kept
└── fin.db              # SQLite database
```

---

## ADR 4: Chart Library

**Status:** Accepted

**Context:** Moom Sales Dashboard uses Recharts (React-only). fin is vanilla JS.

**Options Considered:**
- **Chart.js** — Already downloaded locally, vanilla JS compatible, bar + donut support
- **uPlot / Frappe Charts** — Lighter, but less ecosystem
- **Raw SVG/Canvas** — Maximum control, maximum effort

**Decision:** Chart.js. Already in the project, handles stacked bars + donuts, good tooltip/legend support. Visual quality comes from color choices and layout, not the library.

---

## ADR 5: API Design

**Status:** Accepted

**Context:** ~12 endpoints needed. Question of whether to use Flask blueprints.

**Decision:** Single `app.py` with logical route grouping. No blueprints — not enough endpoints to justify splitting.

**Endpoints:**
```
# Import workflow
POST   /api/import/upload       # Parse files, categorize, return preview
POST   /api/import/confirm      # Commit previewed transactions
GET    /api/import/history      # List past imports

# Dashboard
GET    /api/dashboard/summary   # Stat cards (filterable)
GET    /api/dashboard/monthly   # Monthly category breakdown for bar chart
GET    /api/dashboard/categories # Category totals for donut chart

# Transactions
GET    /api/transactions        # Paginated, filterable list

# Merchant rules CRUD
GET    /api/rules               # List all rules
POST   /api/rules               # Add rule
PUT    /api/rules/<id>          # Edit rule
DELETE /api/rules/<id>          # Delete rule

# Reference data
GET    /api/categories          # All categories (for dropdowns)
GET    /api/accounts            # All accounts (for filters)
```

---

## ADR 6: Moom Batch Import Adaptation Strategy

**Status:** Accepted

**Context:** Moom Batch Import (3-tab, ~3300 lines) is the reference implementation. Need to adapt for fin's financial domain.

**Decision:** Adapt the UI patterns, remap the domain concepts.

**What transfers directly:**
- Tab structure + tab switching JS
- Drag-drop upload zone with multi-file support
- Preview table with editable cells + status badges
- Confirm bar pattern (sticky bottom)
- History table layout
- CSS patterns (adapted to fin color palette)

**Domain remapping:**

| Moom Concept | fin Equivalent |
|---|---|
| Channel selector | Auto-detected from file (no pre-selection) |
| Operation Type | N/A (always "import transactions") |
| External Code → SKU | Merchant Description → Category |
| SKU Autocomplete | Category Autocomplete (21+ categories) |
| Code Mappings tab | Merchant Rules tab |
| Resolution cascade | `categorize_transaction()` engine |

**Key simplification:** No channel pre-selection needed. fin auto-detects bank/format from the file contents. Upload config is just the drag-drop zone.

---

## ADR 7: Vantage MK/BS Cardholder Split

**Status:** Accepted

**Context:** DBS Vantage combined export (MK+BS) doesn't distinguish cardholder per transaction. BS-only export is available separately.

**Decision:** Cross-reference strategy.
1. If both BS-only and MK+BS exports are in the same upload batch, backend fingerprints BS transactions (date + description + amount) and tags each combined transaction as "BS" or "MK"
2. Creates two accounts: "DBS Vantage 7436 (MK)" and "DBS Vantage 7436 (BS)"
3. If only combined export uploaded: all transactions under "DBS Vantage 7436" with warning — no split possible

**Consequences:** Requires user to upload BS-only alongside combined. User confirmed this is trivial (one extra monthly report download).

---

## Date

2026-03-07
