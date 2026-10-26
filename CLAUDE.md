# fin — Personal Finance Tracker

Flask + vanilla JS + Chart.js. Single-page app, no build step.

## Specs

The three homes every workflow skill asks for. All three resolve through this repo's `specs/` junction.

- **Issue tracker** — `specs/tickets/` (one folder per effort, `issues/NN-slug.md`); the dialect
  and the mint template are in `specs/tracker.md`.
- **Decisions** — `specs/decisions.md`, the sole ADR home (`ADR-NNN`, 1–3 sentences + link).
- **Glossary** — `specs/glossary.md`, this project's vocabulary; read it before changing language.
- **Rulings** — `specs/rulings.md`, this project's ruling ledger (ADR-031), created at its first ruling;
  search it before asking, record a ruling there the same turn. House rulings: `house-rulings.md` at the vault root.

`specs/` is a junction into the private specs vault. `specs/tracker.md` §Paths owns the
vault path; run `python <cws>/scripts/specs_root.py specs` before any write through it.

## Read First

<!-- ≤6 paths, and never the retired status file; state lives in the home page's `## Now`. -->

- `specs/fin-home.md` — `## Now` is the current state
- `specs/pipeline.md` — the backlog, one line per item
- `specs/decisions.md` — why things are the way they are
- `specs/design.md` — what is merged; include only when the file exists

## Structure

```
app.py              ← Flask backend, all API routes
static/
    index.html      ← SPA shell: Home, Queue, Books, Changes (bottom tabs on phone, top bar on desk)
    app.js          ← All frontend logic
    styles.css      ← All styles
db.py               ← Database helpers, categorization engine
suggest.py          ← Type suggestion for unknown merchants (off without OPENROUTER_API_KEY)
history.py          ← Change history: every write recorded (schema.sql triggers), undoable
screens.py          ← What the screens keep: last-looked mark, refused statements (the switch is chat_writes.py)
mcp_tools.py        ← The chat tools over MCP; the tool list is the permission list
chat_writes.py      ← The "Claude may write" switch: chat-writes.json beside the book; chat turns it off, never on
fin_upload.py       ← Upload command: sends a folder of statements to fin's import
schema.sql          ← SQLite schema (source of truth)
parsers.py          ← Statement parser orchestrator
parse_dbs.py        ← DBS PDF/CSV parser
bills.py            ← What a bill reads: cadence, monthly share, the missed-bill rule (awaiting statement), currency flag
card_balance.py     ← A card split by cardholder held as one balance (the Vantage card)
book_prefill.py     ← A row's book picker prefill: last book on that card, a company's card, the type's
parse_citi_csv.py   ← Citi CSV parser
parse_uob.py        ← UOB PDF parser
seed_mock_data.py   ← Demo data generator (for GitHub)
serve.py            ← Hosted entry point: access gate → /mcp or Flask (fin-online; not yet live)
access_gate.py      ← Cloudflare Access JWT check on every request (copied from folio)
backup.py           ← Nightly backup to the object store, the seed step (copied from folio)
mcp_server.py       ← /mcp; registers mcp_tools.TOOLS and calls them as the chat-gate client
railway.json        ← Railway start command (python serve.py); deploy is by hand only
specs/              ← Project specs (design, status, pipeline, decisions)
```

## Commands

```bash
python app.py                    # Start server (port 8450)
python seed_mock_data.py         # Generate demo DB (refuses if fin.db exists)
FIN_LOCAL_DEV=1 python serve.py  # The hosted process on loopback, gate off (port 8000)
FIN_CONVERT_ON_START=1 python serve.py  # Hosted, one deploy: convert_all on the book before it opens; remove after
python fin_snapshot.py snap fin.db s0.json  # Baseline snapshot, book read on a copy; then diff / check-reference (module doc)
```

## Don't

- Don't add a build step or framework
- Don't suggest features marked as deliberately removed in decisions.md
- Don't modify fin.db schema without checking schema.sql first

## Carried (no template slot)

<!-- Ticket 22: carried verbatim; disposition on touch. -->

## Key Patterns

- **Categorization chain:** description → merchant_rule → service → category
- **Service-centric model:** services are the central entity. Rules, subscriptions, and transactions all link via `service_id`
- **No build tools.** No npm, no webpack, no React. Vanilla JS by design.
- **Dashboard absorbed Services tab** — don't suggest re-adding it (see decisions.md)
