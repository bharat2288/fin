# fin — Personal Finance Tracker

Flask + vanilla JS + Chart.js. Single-page app, no build step.

## Structure

```
app.py              ← Flask backend, all API routes
static/
    index.html      ← SPA shell (2 tabs + Import + Masters dropdown)
    app.js          ← All frontend logic
    styles.css      ← All styles
db.py               ← Database helpers, categorization engine
schema.sql          ← SQLite schema (source of truth)
parsers.py          ← Statement parser orchestrator
parse_dbs.py        ← DBS PDF/CSV parser
parse_citi_csv.py   ← Citi CSV parser
parse_uob.py        ← UOB PDF parser
seed_mock_data.py   ← Demo data generator (for GitHub)
specs/              ← Project specs (design, status, pipeline, decisions)
```

## Commands

```bash
python app.py                    # Start server (port 8450)
python seed_mock_data.py         # Generate demo DB (refuses if fin.db exists)
```

## Specs

Read `specs/` before starting work:
- `design.md` — scope, architecture, data model
- `status.md` — current state, recent sessions
- `pipeline.md` — backlog and priorities
- `decisions.md` — why we chose what we chose

## Key Patterns

- **Categorization chain:** description → merchant_rule → service → category
- **Service-centric model:** services are the central entity. Rules, subscriptions, and transactions all link via `service_id`
- **No build tools.** No npm, no webpack, no React. Vanilla JS by design.
- **Dashboard absorbed Services tab** — don't suggest re-adding it (see decisions.md)

## Don't

- Don't add a build step or framework
- Don't suggest features marked as deliberately removed in decisions.md
- Don't modify fin.db schema without checking schema.sql first
