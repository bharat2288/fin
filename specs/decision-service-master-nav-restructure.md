---
type: decision
project: fin
date: 2026-03-08
created_by: architect
---
# [[fin-home|fin]] — ADR: Service Master + Navigation Restructure
*[[dev-hub|Hub]]*

## Status
Accepted

## Context

The app grew organically: 6 tabs (Dashboard, Import, History, Subscriptions, Merchant Rules, Accounts). Merchant Rules does double duty — managing both categories AND pattern→category mappings. Subscriptions exist as standalone entities with no link back to transaction history. Import and History are separate tabs despite being tightly related. The user wants:
1. A **Service** entity as the central concept — what you pay for (Netflix, SP Gas, Grab)
2. Subscriptions as a billing view of services (not a separate concept)
3. Transaction descriptions resolving through rules to services to categories
4. Navigation that reflects primary workflows vs admin/master data

## Decision

### Data Model: Service as central entity

```
(Transaction Description)
        │
        ↓  pattern match
[Merchant Rule] ──→ [Service] ──→ [Category]
                        │
                        ↓  optional
                  [Subscription]
                  (billing details)
```

New table:
```sql
CREATE TABLE services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category_id INTEGER,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (category_id) REFERENCES categories(id)
);
```

FK additions:
- `merchant_rules.service_id` → rules resolve to services
- `subscriptions.service_id` → subscription is a service with billing
- `transactions.service_id` → populated during categorization

Category assignment chain: `rule → service.category_id → transaction.category_id`

### Navigation: 3 tabs + 2 icon actions

```
[Dashboard]  [Subscriptions]  [Services]              [↑ Import]  [⚙ Masters ▾]
                                                                    ├─ Categories
                                                                    ├─ Services
                                                                    ├─ Rules
                                                                    └─ Accounts
```

- **Dashboard**: Charts, stat cards, transaction table (unchanged)
- **Subscriptions**: Recurring billing view — renewals, amounts, cards
- **Services**: Accordion view — expand a service to see all its transactions
- **Import icon**: Combined drag-drop upload + import history
- **Masters dropdown**: Admin CRUD — Categories, Services, Rules, Accounts

### Renewal Date: Anchor-based

Advance `renewal_date` forward by frequency until past **today** (not past `last_paid`). Payment timing no longer skews renewal calendar.

### Migration

1. Auto-create services from existing subscriptions (46 → services)
2. Auto-create services from merchant rules not covered by subs
3. Link `subscriptions.service_id`, `merchant_rules.service_id`
4. Backfill `transactions.service_id`
5. Parser updated: `categorize_transaction()` returns `(service_id, category_id)`

## Consequences

### Positive
- Service as single source of truth for "what am I paying for?"
- Click any transaction → see full payment history for that service
- Categories cleanly separated from rules
- Navigation reflects actual workflows (3 tabs) vs admin (Masters)
- Renewal dates no longer drift from late payments

### Negative
- Significant refactor touching every file
- Migration complexity — auto-created services need manual review
- Merchant rules that map to categories without a clear "service" need handling

## Execution Order
1. Schema + migration (services table, FKs, data migration)
2. Renewal date fix (anchor-based)
3. Navigation restructure (HTML + CSS + JS)
4. Services tab (accordion transaction view)
5. Masters dropdown UI (Categories, Services, Rules, Accounts)
6. Parser update (categorize returns service_id + category_id)

## Date
2026-03-08
