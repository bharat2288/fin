# fin

Personal finance tracker that turns bank/CC statements into categorized spending data with visual dashboards. Drop statement files (CSV or PDF), auto-categorize via merchant pattern matching, review and correct, see trends.

## Features

- **Multi-bank statement import** — DBS (CSV + PDF), Citi (CSV), UOB (PDF). Auto-detects bank and format per file.
- **Merchant rules engine** — Pattern matching (contains/startswith/exact) maps merchants to categories. Rules persist and improve over time.
- **Interactive import preview** — Drag-drop files, review parsed transactions with editable categories and skip toggles before committing.
- **Dashboard** — Monthly stacked bar chart, category donut, stat cards (total spend, personal vs. business), filterable transaction table. Built with Chart.js.
- **Import history** — Browse past imports, drill into line-level detail.
- **Subscription tracker** — Track recurring charges with billing frequency, renewal dates, and active/deactivated status.

## Architecture

```
(Statement Files: CSV, PDF)
        │
        ▼
[Frontend SPA — vanilla JS + Chart.js]
├── Dashboard tab    ← Charts, stat cards, filters
├── Import tab       ← Drag-drop, preview, confirm
├── History tab      ← Past imports, drill-down
└── Merchant Rules   ← CRUD for pattern→category mappings
        │
        ▼
[Flask Backend — app.py]
├── /api/import/*        ← Upload, parse, confirm
├── /api/dashboard/*     ← Summary, monthly, categories
├── /api/transactions    ← Paginated list
├── /api/rules           ← Merchant rule CRUD
└── /api/subscriptions   ← Subscription tracker
        │
        ▼
[SQLite — fin.db]
    accounts, transactions, categories,
    merchant_rules, subscriptions, batch_imports
```

## Quick Start

### Prerequisites

- Python 3.10+

### Installation

```bash
git clone https://github.com/bharat2288/fin.git
cd fin
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt  # (if exists, otherwise: pip install flask openpyxl)
```

### Running

```bash
python app.py
# Open http://localhost:8450
```

The database and seed categories/merchant rules are created automatically on first run.

## Adding Bank Parsers

Each bank has its own parser (`parse_dbs.py`, `parse_dbs_csv.py`, `parse_citi_csv.py`, `parse_uob.py`). To add a new bank:

1. Create `parse_<bank>.py` with a function that returns a list of transaction dicts
2. Register the parser in `ingest.py`'s auto-detection logic
3. Add initial merchant rules for common merchants from that bank

## Limitations

- Single-user, runs locally
- PDF parsing depends on statement format consistency (bank format changes may break parsers)
- No real-time bank connections — manual statement import only

## License

MIT
