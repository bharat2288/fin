---
type: status
project: fin
date: 2026-03-08
---
# [[fin-home|fin]] — Status
*[[dev-hub|Hub]]*

> Session continuity document. Claude Code updates this at end of each session.
> Keeps current state + last ~5 sessions. Older sessions archived to [[archive-sessions]].

---

## Current State

### Working
- 3-tab Flask SPA (Dashboard, Subscriptions, Services) + Import icon + Masters dropdown on port 8450
- Masters dropdown: Categories, Services Master (with Cleanup view), Merchant Rules, Accounts
- SQLite database: 58 categories, 335+ merchant rules, 334 services, 46 subscriptions
- 1009 categorized transactions across DBS, Citi, UOB (Sep 2025 - Feb 2026), 100% service-linked
- Service-centric data model: `description → rule → service → category` chain
- Services tab: dual view toggle — "By Service" accordion + "By Category" 3-level accordion (category → subcategory → service)
- Deep linking: clickable service names in Dashboard txn table + Subs table → Services tab; clickable category in Subs table → By Category view
- Browser history: back button works across tab/deep-link navigation (pushState + popstate)
- Services Master: CRUD + merge service feature + bulk cleanup view (heuristic-based rename + batch save)
- Re-categorize All: syncs rule categories to service categories, then re-runs all rules
- Subscription names resolve from services JOIN (rename/merge automatically propagates)
- Dashboard txn table: Service column with deep-link, sortable
- Account filter uses short names
- PayNow Transfer catch-all service (priority -10) for unidentified personal transfers

### Known Issues
- Feb 2026 data incomplete (only 31 transactions — more statements needed)
- app.py ~1600+ lines, app.js ~3100+ lines — code review needed (pipeline #33)

---

## Session: 2026-03-08c — Deep Links, Category View, Merge, Cleanup

### Accomplished
- **Service column in Dashboard txn table**: Added `service_name` + `service_id` to transactions API with services JOIN; sortable column
- **Account filter short names**: Dashboard dropdown uses `short_name` instead of full account name
- **Deep linking**: Service names clickable in txn table + subs table → navigates to Services tab, opens accordion, scrolls. Category clickable in subs table → By Category view with parent+subcategory expansion
- **Services tab dual view**: "By Service" (flat accordion) + "By Category" (3-level: parent category → subcategory → service) with toggle buttons
- **Category view**: filter dropdowns (category, personal/moom), search, proper 3-level nesting with visual hierarchy (L1: 15px bold + camel border, L2: 13px medium + subtle border, L3: 12px light)
- **Browser history**: `pushState`/`popstate` on tab switches so back button returns to previous tab; URL hash persistence on refresh
- **Service merge feature**: `POST /api/services/<id>/merge` reassigns all txns/rules/subs from source→target, deletes source. UI in Edit Service modal with target dropdown + confirmation
- **Bulk cleanup view**: Services Master → Cleanup tab. Heuristic filter for pattern-like names, inline rename inputs with modified highlighting, batch save via `/api/services/bulk-rename`
- **Service cleanup**: Renamed 6 pattern-like names, merged 5 duplicates (Food Panda, Google Ads, etc.), merged Claude+Claude.Ai→Claude (Anthropic), ChatGPT+OpenAI→ChatGPT (OpenAI), Shopee split into Shopee (personal) + Shopee Ads (Moom)
- **Subscription service name from JOIN**: `service` field now resolved from `services.name` via JOIN, not stale text column
- **Re-categorize syncs rules**: Now first syncs `merchant_rules.category_id` to service's `category_id` before re-running (18 stale rules fixed)
- **Unlinked transactions fixed**: Created 10 services for identifiable merchants (IRAS, Bakalaki, OpenRouter, etc.), PayNow Transfer catch-all (priority -10) for 5 remaining personal transfers. 0 unlinked transactions.
- **Edit Service error handling**: PUT endpoint now catches IntegrityError (UNIQUE constraint), JS handles non-OK responses gracefully

### Decisions Made
- Service's category wins over rule's direct category; re-categorize syncs both
- Merge is the right tool for duplicates (not rename to same name)
- PayNow Transfer as catch-all service with lowest priority (-10)
- Merchant Rules = pattern detection layer; Services Master = entity + category assignment layer — complementary, not redundant

### Accomplished
- **Card number masking**: `mask_card_number()` masks 16-digit card numbers (`****-****-****-XXXX`) in all API responses (accounts, transactions, import preview, import history)
- **Subscription tab improvements**:
  - Removed scroll container — all subs render on one page
  - DD-MMM-YY date format for Last Paid and Renewal columns
  - Sortable columns (click headers, ▲/▼ indicators)
  - Personal/All/Moom spend filter (defaults to Personal)
  - Billed amount from actual transactions (not static Excel import)
  - Variable subscription detection: monthly sums aggregated, >10% variance triggers 3-month rolling average (prefixed with `~`)
  - Last Paid links to Dashboard — clicks navigate to Dashboard, sets month/year + search pattern, scrolls to tx table
  - Split payment handling: Jan Claude 2x payments summed correctly per month
- **New subscriptions added**: Adobe (deactivated), SICC Membership (Fitness > Golf), Rent + Rent Service Fee (deactivated by default), Allpress Coffee
- **Removed**: Oura (moved to Shopping in rules, deleted from subs), Nespresso (no longer active)
- **Fixed match patterns**: SP Gas → `SP DIGITAL PL-UTILITIE`, Microsoft 365 → `MICROSOFT*MICROSOFT 36`, Tesla → `TESLA MOTORS`
- **Edit Rule modal**: Replaced browser `prompt()` with proper styled modal (pattern, category dropdown, match type, priority, min/max amount). Escape/overlay click to close.
- **Re-categorize All**: New endpoint `POST /api/rules/recategorize` + button in Merchant Rules toolbar. Re-runs all rules against existing transactions, reports updated/unchanged counts.

### Decisions Made
- Variable subs use monthly sum aggregation (not individual tx comparison) to avoid false positives from split payments
- Rent deactivated by default (visible under "All" filter)
- SICC → Fitness > Golf (not "Personal" category — avoid confusing category with is_personal typology)

---

## Session: 2026-03-07b — Subscriptions + Visualization

### Accomplished
- Migrated 43 subscriptions from Excel into fin.db (24 active, 19 inactive)
- Built subscription burn CLI and standalone HTML dashboard
- Applied full design system (dark theme, Geist/Fraunces, camel accent)

---

## Session: 2026-03-07 — Project Kickoff + Implementation

### Accomplished
- Built PDF/CSV parsers, categorization engine, ingestion pipeline
- Schema: 21 categories, 172 merchant rules, is_anomaly support
- Ingested 6 real statements (338 transactions, 100% categorized)

---

> **Archive:** Older sessions archived to [[archive-sessions]].
