---
type: status
project: fin
date: 2026-03-07
---
# [[fin-home|fin]] — Status
*[[dev-hub|Hub]]*

> Session continuity document. Claude Code updates this at end of each session.
> Keeps current state + last ~5 sessions. Older sessions archived to [[archive-sessions]].

---

## Current State

### Working
- PDF parser for DBS CC statements (`parse_dbs.py`)
- CSV parser for DBS CC + bank exports (`parse_dbs_csv.py`)
- SQLite database: 21 categories, 172 merchant rules, is_anomaly support
- Categorization engine: 338 transactions fully categorized (0 uncategorized)
- Ingestion pipeline: `py ingest.py <files>` (preview) / `py ingest.py --commit <files>` (save)
- Monthly summary: `py summary.py --all` / `py summary.py 2025-12` / `py summary.py --personal`
- 6 statements committed to DB (DBS Vantage 3696 CC + BS/MK Home bank, Sep 2025 - Feb 2026)
- Subscription tracker: 43 subs migrated from Excel, `py subs.py` / `py subs.py --renewals`
- HTML dashboard: `py dashboard.py` generates spending charts (Chart.js, design system tokens)

### In Progress
- Dashboard charts not rendering (Chart.js CDN → local file fix applied, untested)
- Only one CC card ingested — need Woman's World 4777 + other cards
- Anomaly flagging on specific transactions (Mount Alvernia, IRAS tax) not yet done
- Dashboard toggle buttons (anomaly/personal filters) are UI-only, not wired to re-query

---

## Session: 2026-03-07b — Subscriptions + Visualization

### Accomplished
- Migrated 43 subscriptions from Excel Subs sheet into fin.db (24 active, 19 inactive)
- Built `migrate_subs.py` with category mapping overrides (Excel types didn't match fin categories)
- Built `subs.py` CLI — active subs, monthly burn, renewals view
- Monthly subscription burn: SGD $2,035 ($1,376 personal + $659 Moom)
- Built `dashboard.py` — generates standalone HTML dashboard with Chart.js
- Dashboard has: stats row, stacked bar (monthly by category), trend line, category donut, subscription burn donut + list, top merchants table
- Applied full design system (dark theme, Geist/Fraunces, camel accent, proper elevation)
- Hit Chart.js CDN loading issue from file:// protocol — downloaded locally as fix

### Decisions Made
- BFT ($520/mo) marked inactive (user confirmed no longer active)
- SP Gas OGR marked inactive (tenant pays)
- SP Gas BGV amount updated to $420/mo (Excel had stale $347)
- MyRepublic recategorized from "Crypto" to Utilities (Excel miscategorized)
- Standalone HTML dashboard (not Obsidian Charts) — more flexible, richer interactivity

---

## Session: 2026-03-07 — Project Kickoff + Implementation

### Accomplished
- Explored Excel workbook (Subs sheet + 20 sheets) to understand current tracking
- Built PDF parser, CSV parser, categorization engine, ingestion pipeline, monthly summary
- Ran kickoff discovery, wrote design.md, status.md, pipeline.md
- Schema: 21 categories (including Medical, Loan/EMI, Kids), is_anomaly flag, 172 merchant rules
- Ingested 6 real statements (338 transactions, 100% categorized)
- Monthly breakdown working: SGD ~$14K/mo avg across Sep 2025 - Feb 2026
- Key finding: Medical ($14K, childbirth), Shopping ($12.5K), Loan/EMI ($10K, Tesla) are top spend areas

### Decisions Made
- Claude-as-interface, SQLite storage, DBS-only in v1
- Medical separate from Health & Beauty; Loan/EMI for Tesla car loan
- Kids includes nanny ($4K/mo PayNow to Sitoh)
- Anomaly flag for one-time expenses (childbirth, tax), toggle in visualizations
- HP HPR134991E = Tesla car loan EMI ($1,996/mo)
- Barry's Bootcamp no longer active (not on current statements)

---

> **Archive:** Older sessions archived to [[archive-sessions]].
