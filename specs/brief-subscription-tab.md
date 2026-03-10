---
type: brief
project: fin
date: 2026-03-07
created_by: brainstorm
---
# [[fin-home|fin]] — Subscription Management Tab
*[[dev-hub|Hub]]*
> Related: [[design|Design]] · [[decision-service-master-nav-restructure|ADR: Service Nav]] · [[pipeline|Backlog]] · [[brief-statement-import-dashboard|Brief: Import]] · [[status|Status]]

> Output of brainstorm session (2026-03-07).

---

## What

A 5th tab in the fin SPA that shows all subscriptions as a live table — what you're paying, how much monthly, when it renews, which card, and a link to manage it. Enriched automatically from actual transaction data so it never goes stale.

## Why

Subscriptions slip through the cracks. The user takes up new subs and forgets to log them, forgets to cancel ones they wanted to stop, and the Excel tracker goes stale because it requires manual updates. Since fin already ingests all CC/bank statement data, subscriptions should be **derived from actual payments** rather than manually maintained.

## User Story

When I open the Subscriptions tab, I see all my active subscriptions with accurate last-paid dates and amounts pulled from my actual transactions. Monthly burn totals are visible at a glance. When I want to cancel something, the management link is one click away. When the system detects a recurring payment I haven't registered, it tells me.

## Scope (v1)

### Phase 1 — Table UI
- [ ] 5th tab "Subscriptions" in the SPA
- [ ] Table mirroring Excel layout: Service, Category, SGD Billed, SGD/month, Frequency, Card, Last Paid, Renewal Date, Status, Link
- [ ] Active subs on top, deactivated below (collapsible section)
- [ ] Monthly burn stat cards (total, personal, Moom) like dashboard
- [ ] Add subscription form
- [ ] Inline edit (click to edit fields)
- [ ] Status toggle (Active → Deactivated and back)
- [ ] Sortable columns
- [ ] Live FX rate (USD→SGD) for accurate SGD equivalents on USD-billed subs
- [ ] Auto-enrich from transactions: match each sub to its most recent transaction, pull real `last_paid` and `amount_sgd`

### Phase 2 — Detection (right after Phase 1)
- [ ] Scan transaction history for recurring payment patterns (same merchant, similar amount, regular cadence)
- [ ] Surface detected recurring payments not in subscription table as suggestions
- [ ] Flag stale subscriptions (active but no recent payment matching expected frequency)

## Out of Scope (captured for later)

- Subscription renewal reminders / notifications (pipeline #17)
- Budget alerts when subscription costs exceed threshold
- Auto-cancel or pause from the UI (just link to provider's management page)
- Subscription price history / cost tracking over time

## Interaction Flow

```
[Open Subs Tab] ──→ [Fetch subs + enrich from txns + FX rate] ──→ [Render table]
                          │                                              │
                          ↓                                              ↓
                    [Match sub service                           [Stat cards:
                     to transaction                               monthly burn
                     descriptions]                                personal/moom]
                          │
                          ↓
                    [Update last_paid                     [Add Sub] ──→ [Form] ──→ [Save]
                     + actual amount]
                                                         [Edit] ──→ [Inline edit] ──→ [Save]

                                                         [Deactivate] ──→ [Toggle status]
```

**Key interaction: Transaction matching**
For each subscription, search transactions for the most recent match by service name pattern. Example: "Netflix" sub → find most recent transaction where description contains "NETFLIX" → update last_paid date and amount_sgd with actual values. The subscription's existing merchant rule pattern (if any) or service name is used for matching.

## Impact Analysis

**Classification:** Additive — new tab, new API endpoints. Minimal changes to existing code.

**Files affected:**
- `app.py` — New API endpoints: GET/POST/PUT /api/subscriptions, GET /api/subscriptions/enrich
- `static/app.js` — New tab rendering: table, stat cards, forms, inline edit
- `static/index.html` — New tab button + tab content section
- `static/styles.css` — Subscription-specific styles (minimal, reuses existing patterns)
- `subs.py` — `monthly_equivalent()` helper can be reused or moved to shared util

**Data model changes:**
- None — `subscriptions` table already exists with all needed fields
- 43 entries already migrated from Excel
- May add a `match_pattern` column later for transaction matching (Phase 2), but can use service name initially

**Breaking changes:**
- None

**New dependencies:**
- Free FX rate API (e.g., exchangerate-api.com or similar) — one fetch on tab load, cached

## Edge Cases Identified

1. **USD-billed sub with no USD amount** — fall back to SGD amount as-is, show "no FX" indicator
2. **Transaction matching ambiguity** — "GOOGLE" matches multiple subs (Google One, Google Workspace). Use the most specific pattern available.
3. **Multi-period subs** — HBO billed quarterly (3x monthly). Monthly equivalent = billed / periods. Already handled in `subs.py`.
4. **Deactivated sub with recent payment** — user forgot to update status. Flag this in Phase 2.
5. **Amount changed** — sub price increased but stored amount is old. Transaction enrichment auto-corrects this.
6. **FX API down** — cache last known rate, show "(cached)" indicator. Fall back to hardcoded 1.35.

## Open Questions

- Should transaction enrichment update the DB directly, or just display enriched values in the UI? (Recommend: update DB on explicit "Refresh" action, show enriched in UI always)
- Category for subs: use existing fin category tree, or keep the simpler "Type" labels from Excel? (Recommend: use existing categories for consistency)

## Risk Assessment

- **Effort:** Medium — table UI is straightforward, transaction matching is the interesting part
- **Risk:** Low — purely additive, existing schema, no breaking changes
- **Reason:** Schema exists, data exists, patterns established by other tabs. FX API is the only external dependency and has a safe fallback.

## Recommended Next Step

Run `/architect` for two decisions: (1) FX rate source and caching strategy, (2) transaction-to-subscription matching algorithm. Then implement.
