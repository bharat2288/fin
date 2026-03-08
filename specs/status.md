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
- Billing model: `amount` + `currency` (SGD|USD) + `frequency` + `periods` per subscription; Billed (expected) vs Last Paid (actual) separation
- Subscription modals: searchable service dropdown, auto-fill category, auto-derive match_pattern from service rules
- Account FK: subscriptions use `account_id` FK → accounts table (not text card field)
- Anomaly system: `is_one_off` flag on services, `1x` badge in accordion, dashboard "Excl. one-offs" checkbox
- Services tab: dual view toggle — "By Service" accordion + "By Category" 3-level accordion
- Deep linking: service names + billed amounts clickable → Services/Dashboard; Last Paid shows ●/○ indicator
- Browser history: back button works across tab/deep-link navigation (pushState + popstate)
- Dashboard: anomaly + one-off exclusion filters, all 4 endpoints support services JOIN filtering
- USD↔SGD auto-calc in subscription add/edit forms; renewal date auto-advance for past dates
- Responsive subs table with column hiding + horizontal scroll

### Known Issues
- Feb 2026 data incomplete (only 31 transactions — more statements needed)
- 16 subs with suspicious SGD/USD FX ratios need manual review (pipeline #37)
- app.py ~1700+ lines, app.js ~3400+ lines — code review needed (pipeline #33)

---

## Session: 2026-03-08d — Billing Model, Account FK, Anomaly System, Dashboard Filters

### Accomplished
- **Billing model refactor**: Single `amount` + `currency` replaces split `amount_sgd`/`amount_usd`; clear Billed (configured) vs Last Paid (actual tx) separation in table columns
- **Account FK migration**: `card` text field → `account_id` FK to accounts table; 39 subs auto-migrated in db.py; all API endpoints updated
- **Service dropdown in modals**: Subscription add/edit use searchable service select (not text input); auto-fills category, auto-derives match_pattern from service's first merchant rule
- **Anomaly / frequency system**: `is_one_off` flag on services table; checkbox in Edit Service modal; `1x` badge in accordion headers; dashboard "Excl. one-offs" checkbox wired to all 4 endpoints
- **SP Group reorganization**: Renamed/split SP services into SP Group (BGV), SP Group (OGR), SP Group (EV), SP Group (Moom)
- **Deep-link moved to Billed column**: Billed amount clickable → navigates to matching transactions; Last Paid shows ●/○ dot indicator (tx-based vs manual)
- **Modal field visibility**: Better label contrast (`font-weight: 700`, `color: var(--text-secondary)`), explicit input borders/backgrounds, grid layout for amount/currency/freq/periods
- **USD↔SGD auto-calc** in both add/edit subscription forms
- **Renewal date auto-advance**: `_advance_renewal()` helper pushes past renewal dates forward
- **Responsive subs table**: Column hiding + horizontal scroll at breakpoints
- **25 subscription links updated** from Excel hyperlinks
- **Date picker dark theme fix**
- **Dashboard one-off filtering**: `LEFT JOIN services` on all 4 dashboard query endpoints; `_build_filters()` supports `exclude_one_off` param; JS `buildFilterParams`, `buildChartParams`, stat cards all wired

### Decisions Made
- Single `amount` + `currency` is source of truth per billing cycle (not split SGD/USD fields)
- `_monthly_equivalent()` is currency-aware: converts USD→SGD at live FX rate before dividing by cycle length
- `match_pattern` auto-derived from service's first merchant rule — not user-editable in subscription modals
- Anomaly ownership at service level (`is_one_off`) not transaction level for one-off purchases
- USD subscription enrichment skips amount update (keeps configured price, only updates last_paid_date)

### Key Fixes
- Deep-link accordion race condition: `switchServiceView()` async fetch overwrote immediate `renderServicesTab(true)`. Fixed by manually setting view state (button classes, container display) without calling the async function.
- `billed_display` always None in API response despite correct Python code. Moved computation to frontend.

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
