"""The tools a chat client uses on fin over MCP (fin-surfaces 01).

The tool list is the permission list: a chat client reaches fin through these
tools and nothing else. Each tool runs one of fin's own routes in-process, so
it is checked exactly as the app's screens are checked, and every write lands
in the change history stamped as the client's (history.py), undoable from
fin. There is no write path here of its own.

Rulings this module carries:
- Reads return rows, balances and the month check (Q1). Never a file name, a
  server path or a credential: HIDDEN_KEYS are taken out of every answer, and
  no tool reads a file, the environment or a setting.
- Writes land at once, no confirm step (Q3). A write that would change more
  than MANY_ROWS rows runs only when the call says how many it expects:
  without that, or with a different count, it is put back and the count is
  returned, for Claude to tell the operator and ask.
- The "Claude may write" switch (chat_writes.py; 01's Off switches): with it
  off, every write tool refuses with a fixed message before anything runs,
  and reads still work. turn_off_writes turns it off, and works when it is
  already off; no tool turns it on: only the operator, in fin's app.
- A change over MANY_ROWS that stands on the operator's agreed count is
  recorded as asked first (history.record_asked), so Changes can say so.
- The preview (fin-surfaces 21 and 51): change_merchant, change_rule,
  add_rule, delete_rule and merge_merchants take `past` (apply | only_new)
  and the preview's `token`. Without `past`, when past rows would change,
  nothing is written and the answer carries the preview's numbers for Claude
  to ask the operator. An answer sent back with the preview's token stands
  on the count the preview gave (the operator's yes to it), as expected_count
  does, and is recorded as asked first; one with no token is asked the count
  as any write.
- Permission changes (fin-surfaces 51): rerun_rules is removed, as Re-run
  every rule is retired; rule_drift, a read, gives the rows that differ from
  what their rule says now in its place. Applying them is the operator's, on
  Lists › Rules: no chat tool writes them.
- Expect back (personal-ledger 23, E8): seven write tools (mark_expect_back, undo_mark,
  set_off, unlink_set_off, let_go, undo_let_go, not_a_payback) are thin wrappers over the
  routes the app's drawers call, and expected_back reads the open list and an item's page;
  Claude does what the app does, one change each, stamped as the chat's, under the switch.
  A let-go must name mine or a gift: there is no default.
- Paybacks over chat (fin-redesign 12; RUL-016, no per-person shares): paybacks
  reads the Queue's payback suggestions as the Money in lane shows them;
  mark_expect_back given a book or type_id labels the row in the same change
  as the mark (set_label's route, carrying the mark); set_off_payments sets
  several payments off against one expectation, all or none, as one change,
  under MANY_ROWS like any write.
- The Queue (chat-queue-read 02): queue reads the Queue's labelling work
  card by card, as the operator sees it, and one pile a page at a time from
  a cursor when asked (fin-redesign item 27); queue_summary counts every
  pile. Neither asks Jev or writes anything. Chat saves cards only in
  batches, through plan_preview and plan_apply (below); add_rule and
  change_merchant label the past rows a pattern or merchant matches.
- Plans (chat-plan-apply 02): chat labels the Queue in bulk only through
  plan_preview, which writes nothing, and plan_apply, which writes the
  previewed batch as one change entry on that preview's token, refused when
  the book moved since (a replay included). Both stop with the switch off. The
  token stands in for expected_count: a batch over MANY_ROWS rows applies on
  it and is recorded as asked first.
- No tool imports a statement, changes or deletes an imported row, changes
  or deletes an account (adding one is create_account, or
  add_company_or_person; joining one to a party is link_party), sends merchants to the type-suggestion service,
  runs a conversion or changes a bill: bills (fin-redesign 10) reads them,
  with the missed-bill rule's verdict from bills.py, the one home the app's
  screens read too.
  Statements reach the hosted fin through the upload command (fin_upload.py).
- Accounts (fin redesign, design evaluation §4): create_account adds an
  account of any declared kind over the app's Add an account, so the
  statements imported after find it with its right kind. The route refuses a
  name fin already holds. A loan may be made with its hire-purchase terms
  (fin-redesign 14; loan_terms.py), in the same change; terms on an account
  fin already holds are an account change, the operator's on Lists › Accounts.
- The chat's relabel reach (fin redesign ticket 11): label_review, on any
  row, also says a refund (keeping its type), a card payment, or a move into
  or out of a holding (review.MORE_CHOICES); a person stays no movement's
  other side. With tx_ids it labels many rows as one change, behind a
  preview: a call naming more than one row writes nothing and answers with
  the preview and its token, and writes only when sent back with a token
  that still holds. The token is not the operator's count: over MANY_ROWS
  rows changed (the preview's changes, refund links too) the call takes
  expected_count as any write, the number the preview shows. create_merchant adds a
  merchant over the app's own route, which refuses a name fin holds.
- Parties (fin-redesign 09; a link, not a new home): a merchant is the party.
  create_merchant and change_merchant give it a role (party.ROLES), and
  link_party joins a person, company, loan, holding or untracked account to
  it, or takes one off, over the app's own route: one change each, undone
  whole. Two person accounts may join one party and stay two accounts
  (RUL-011). This is the one change to an existing account a tool makes; it
  moves no row and no figure.

The MCP server (mcp_server.py) registers TOOLS and calls call(); the actor is
the client the chat gate verified, labelled as folio labels it: `chat:<sub>`,
the token's subject (access_gate.AccessVerifier), or `chat` without one. There
is no default actor: every call names who asks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import access_gate
import account_kind
import chat_writes
import db
import history
import party
import loan_terms

MANY_ROWS = 20

# Keys taken out of every answer, at any depth.
HIDDEN_KEYS = frozenset({"file", "filename", "filenames", "path", "file_path"})

FIXED_ERROR = "fin could not do that just now; nothing was changed"
UNDO_NOT_CHAT = (
    "the chat can undo only a change made from the chat; nothing was changed. "
    "The operator can undo this one in fin's history."
)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    write: bool
    # args -> (method, path, query or json body)
    request: Callable[[dict], tuple[str, str, dict | None]]

    @property
    def title(self) -> str | None:
        """The plain title the Claude app shows (TITLES, below)."""
        return TITLES.get(self.name)


def _schema(properties: dict | None = None, required: tuple = ()) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}
_MONTH = {"type": "string", "description": "YYYY-MM"}
_DAY = {"type": "string", "description": "YYYY-MM-DD"}
# The Queue's piles the queue tool pages, as fin's route names them
# (app.QUEUE_SECTIONS; a test holds the two together).
QUEUE_SECTIONS = ("untyped", "sure", "pick", "no_guess", "check_each", "transfers")
_EXPECTED = {
    "type": "integer",
    "description": f"For a change over {MANY_ROWS} rows: the row count the operator agreed to.",
}


# The preview's answer (fin-surfaces 21 and 51): a merchant or rule change
# that would relabel past rows writes nothing until it is told what the past
# rows do, and the preview it answers with is the operator's to answer.
_PAST = {
    "past": {
        "type": "string", "enum": ["apply", "only_new"],
        "description": "The operator's answer to the preview: apply (the past rows change too) "
                       "or only_new (only new rows take it; the past rows keep their labels, marked checked).",
    },
    "token": {"type": "string", "description": "The preview's token, sent back with its answer."},
}
_PAST_SAID = (
    "When past rows would change and past is not given, nothing is written: the answer carries "
    "the preview (how many past rows, how many of them the operator checked, how many they chose "
    "stay as they are, for a book the money that moves and the months, three examples, and a "
    "token). Ask the operator, with those numbers, whether the past rows change too or only new "
    "rows take it, unless they already said; then call again with past and the preview's token. "
    "A token from a preview that no longer holds is refused: look again."
)
# The opt-in an Apply may carry (ADR-008): rows a rule would move but keeps
# only because the operator chose their book or type by hand move too. One
# name on every path that changes a rule, the plan's items among them.
_MOVE_CHOSEN_SAID = (
    "move_chosen: true moves too the rows the preview lists apart in flows_chosen, kept only because the "
    "operator chose their book or type by hand (a choice that may be stale): each takes the flow the rule "
    "gives, losing its book and type where that is not spending. Send it only with past apply, the preview's "
    "token and the operator's yes to those rows by name (count_with_chosen is the past rows then); left out, "
    "they stay as they are. "
)
_MOVE_CHOSEN = {
    "move_chosen": {**_B, "description": "With past apply and the preview's token, on the operator's yes: "
                                         "also move the rows listed in flows_chosen."},
}
# What a rule may say its rows are (ADR-009; fin-redesign 07).
_RULE_FLOW = {
    "flow": {"type": "string", "description": "What the rule's rows are: expense, income, refund, transfer, "
                                              "payment or movement (never review). Left out, the rule labels "
                                              "only and fin works the flow out."},
    "other_side_id": {"type": "integer", "description": "With flow: the household account on the other side; "
                                                        "required for a transfer (a bank, card or untracked "
                                                        "account) and a movement (a loan, holding or company). "
                                                        "Never a person or an archived account. A flow changed "
                                                        "without it keeps the held one only where the new flow "
                                                        "allows it."},
}
_RULE_FLOW_SAID = (
    "A rule may state what its rows are (flow, other_side_id): it beats what fin works out, never a flow "
    "the operator set by hand, and the preview lists every past row whose flow would change (flows). "
    "Only a flow fin worked out under the rules before the change moves (not one Only new rows left); a row "
    "a record of expect back rests on keeps its flow where a hand label would be refused, and so does a row whose "
    "book or type the operator chose (flows_kept, with why). "
    "Book and type go only on spending and refund rows: a row moved to any other flow loses both in the same "
    "change (each flows line's label_cleared says what it loses). "
)
# A merchant's role as a party (party.py); "" says none.
_ROLE = {
    "role": {
        "type": "string", "enum": ["", *party.ROLE_NAMES],
        "description": "The part the merchant plays as a party, one of: "
                       + "; ".join(f"{name} ({said})" for name, said in party.ROLES)
                       + ". \"\" says none. It moves no row.",
    },
}
# The tools that answer with the preview.
_PREVIEWED = frozenset({"add_rule", "change_rule", "delete_rule", "change_merchant", "merge_merchants"})


def _pick(args: dict, *names: str) -> dict:
    return {k: args[k] for k in names if k in args and args[k] is not None}


def _get(path: str, *names: str):
    return lambda a: ("GET", path, _pick(a, *names))


# --- reads -------------------------------------------------------------------


def _expected_back_request(a: dict) -> tuple[str, str, dict | None]:
    if a.get("expectation_id") is not None:
        return "GET", f"/api/expectations/{int(a['expectation_id'])}", {}
    return "GET", "/api/expectations", _pick(a, "direction", "person", "currency", "near", "payment")


_READS = [
    Tool(
        "balance_sheet",
        "The household's balance sheet at the end of a month: every account, loan, holding "
        "and company with its balance, the anchor it rests on (date, statement or typed "
        "figure), whether its rows tie; what is expected back, waiting for its row and to pay "
        "back (a person's money is these, never a balance); net worth and what it leaves out; currency "
        "change; and the month check (net-worth change against income less spending plus "
        "currency change, the unexplained difference, the transfers waiting for review).",
        _schema({"month": _MONTH}),
        False,
        _get("/api/balance-sheet", "month"),
    ),
    Tool(
        "spending_cards",
        "One month's spending figures, net of refunds and of what is expected back; a refund "
        "counts in the month it arrives; S$ accounts only: household spending (Household book, household-owned "
        "accounts), each company's costs paid from household accounts, loan principal repaid, "
        "and what is held out waiting for review (money out and in apart), against the "
        "three-month average. Each book's <book>_out is its money out and <book>_back the "
        "refunds that came back, the two parts the net is made of.",
        _schema({"ref_month": _MONTH, "book": _S, "account_id": _I, "exclude_one_off": _B}),
        False,
        _get("/api/dashboard/stat-cards", "ref_month", "book", "account_id", "exclude_one_off"),
    ),
    Tool(
        "spending_by_type",
        "Spending totals by type over a date range, for one book or all: net of refunds and of what is expected back; a refund counts in the month it arrives; S$ accounts only.",
        _schema({"start": _DAY, "end": _DAY, "book": _S, "exclude_one_off": _B, "group_parent": _B}),
        False,
        _get("/api/dashboard/types", "start", "end", "book", "exclude_one_off", "group_parent"),
    ),
    Tool(
        "spending_by_month",
        "Spending by type per month (or week or quarter) over a date range: net of refunds and of what is expected back; a refund counts in the month it arrives; S$ accounts only.",
        _schema({"start": _DAY, "end": _DAY, "book": _S, "granularity": _S, "group_parent": _B}),
        False,
        _get("/api/dashboard/monthly", "start", "end", "book", "granularity", "group_parent"),
    ),
    Tool(
        "transactions",
        "Rows, newest first unless sorted: date, the bank's description, amount, account, "
        "book, type, flow, other side, merchant, note; and the row's parts in whole minor units "
        "of its account's currency (amount_minor; expected_minor: what is expected back or to "
        "pay back on it; placed_minor: what it has set off against others; own_minor: what is "
        "left of its own, which is what spending or income counts). Filter by date range, month, account, "
        "book, types (comma-separated type names as the types tool gives them, a parent taking its "
        "sub-types; __untyped__ picks the spending and refund rows with no type), flow (e.g. review, "
        "movement, expense), or search text.",
        _schema({
            "start": _DAY, "end": _DAY, "month": _MONTH, "account_id": _I, "book": _S,
            "types": _S, "flow": _S, "search": _S, "sort": _S, "sort_dir": _S,
            "page": _I, "per_page": _I,
        }),
        False,
        _get("/api/transactions", "start", "end", "month", "account_id", "book", "types", "flow",
             "search", "sort", "sort_dir", "page", "per_page"),
    ),
    Tool(
        "review_summary",
        "How many transfers wait for a label and what they add up to, and the choices a label "
        "can be (with what each needs). The rows themselves: transactions with flow=review.",
        _schema(),
        False,
        _get("/api/review"),
    ),
    Tool(
        "queue",
        "The Queue's labelling work as its cards hold it. The rows with no type in their "
        "groups, one per Queue card (a merchant string Jev has a guess for, else one wording; "
        "split by side, out or in), the biggest first: each group's rows (date, amount_minor, "
        "counted_minor: the row's own part the Queue shows, with expect_pill saying why they "
        "differ), Jev's guess (types with how sure, and route: prefill, top3 or blank; blank "
        "with Jev off), the name and wording fin suggests for the merchant and its rule (empty "
        "for a PayNow or a transfer: the operator gives them), the book the card prefills and "
        "why, and the merchant fin already holds by that name, if any (held.name is the "
        "spelling the card shows; with held.review_each_time each row keeps its own book). "
        "Currency and account every row of a group shares are on the group; a row names its "
        "own prefilled book only when it differs. Then the rows waiting under Check each row "
        "(merchant, type, usual book, the row's own book) and the transfers waiting for a "
        "label (count, totals, rows). Amounts are in minor units of the row's currency. Each "
        "list holds the biggest 100 and says when it is cut (showing N of M); the rows with no "
        "type share one 100-row budget the biggest cards fill, so small cards wait behind them. "
        "To reach every card, page one section: untyped (every card with no type), sure, pick or "
        "no_guess (those Jev is sure of, offers three types for, or has no guess for), check_each "
        "or transfers; with limit (the rows a page holds, 1 to 100; 100 when not given) and after "
        "(the next cursor the page before gave; next is null on the last page). A page of cards "
        "ends at a card's end; a card over the limit comes alone with its first rows, row_count "
        "saying how many it holds (its later rows come once its first are labelled). A card stands "
        "where its biggest row does, so if rows are labelled, imported or put back while you page, "
        "page again from the start: queue_summary counts every pile, before and after. "
        "This only reads and applies nothing: saving cards (a group's name, wording, type and "
        "book at once) is plan_preview then plan_apply, in batches, on the operator's yes.",
        _schema({
            "section": {"type": "string", "enum": list(QUEUE_SECTIONS)},
            "after": {**_S, "description": "The next cursor from the page before."},
            "limit": {**_I, "description": "The rows a page holds, 1 to 100."},
        }),
        False,
        _get("/api/queue/cards", "section", "after", "limit"),
    ),
    Tool(
        "queue_summary",
        "How much waits in the Queue, counts only: cards and rows in each pile queue pages "
        "(untyped, and of those sure, pick and no_guess by Jev's guess; check_each; transfers), "
        "and jev: whether Jev guesses at all (off, every card with no type is no_guess). "
        "Read it to see the whole Queue before paging a pile with queue.",
        _schema(),
        False,
        _get("/api/queue/summary"),
    ),
    Tool(
        "accounts",
        "Every account: kind, owner, currency, last four digits only, latest anchor, "
        "whether it takes a typed figure, and the party (party_id, party: a merchant) a person, "
        "company, loan, holding or untracked account is joined to.",
        _schema(),
        False,
        _get("/api/accounts"),
    ),
    Tool(
        "figures",
        "Anchors (statement balances and typed figures), for one account or all.",
        _schema({"account_id": _I}),
        False,
        _get("/api/anchors", "account_id"),
    ),
    Tool(
        "merchants",
        "Every merchant with its default book and type, its row and rule counts, its role as a "
        "party (null: none said), and the accounts joined to it as their party (party_accounts).",
        _schema(),
        False,
        _get("/api/services"),
    ),
    Tool(
        "rules",
        "Every merchant rule: pattern, merchant, and the book and type it gives a row.",
        _schema(),
        False,
        _get("/api/rules"),
    ),
    Tool(
        "rule_drift",
        "The rows whose label differs from what the merchant rules give now (re-running the "
        "rules is retired; this count is what replaces it): how many, the newest three as "
        "examples (now and after). Only labels from a merchant or a rule count; "
        "a row the operator checked or chose never does. Applying it is the operator's, on "
        "Lists › Rules in fin, where the count opens the same preview.",
        _schema(),
        False,
        _get("/api/rules/drift"),
    ),
    Tool(
        "types",
        "The type list (one list for every book, with sub-types and what each covers, and each "
        "type's settings: its usual book, one-off by default, Check each row by default). With "
        "filings: fin's own filings instead (loan interest, a gift given, a gift received) and the "
        "type each goes under. Changing any of these is the operator's, in fin's Lists › Types.",
        _schema({"filings": _B}),
        False,
        lambda a: ("GET", "/api/types/slots" if a.get("filings") else "/api/types", {}),
    ),
    Tool("books", "The books, Household first: whose spending a row is. The operator adds books in fin's app.", _schema(), False, _get("/api/books")),
    Tool("flows", "The flows a row can have, with what each means.", _schema(), False, _get("/api/flows")),
    Tool(
        "coverage",
        "Which account-months have statements imported, for the last six months.",
        _schema(),
        False,
        _get("/api/statements/coverage"),
    ),
    Tool(
        "rates",
        "Saved exchange rates, newest first.",
        _schema({"currency": _S}),
        False,
        _get("/api/rates", "currency"),
    ),
    Tool(
        "history",
        "The change history, newest first: who (fin or a chat client), when, what, how many rows, "
        "and whether it was undone.",
        _schema({"limit": _I, "before": _I}),
        False,
        _get("/api/history", "limit", "before"),
    ),
    Tool(
        "change",
        "One change from the history with every row it changed, before and after, and what "
        "would block undoing it.",
        _schema({"entry_id": _I}, ("entry_id",)),
        False,
        lambda a: ("GET", f"/api/history/{int(a['entry_id'])}", None),
    ),
    Tool(
        "expected_back",
        "What is expected back from people, or to pay back to them (an expectation sits on one "
        "row). Give expectation_id alone for one item's page: its amount, what is set off, what "
        "is open, and its lines, each with the id the write tools take (a set-off's id for "
        "unlink_set_off); the filters are then ignored. Otherwise the open items (something still open, nothing let go), "
        "oldest first, each with id, row, amount, open_minor, names and age; filters: direction "
        "back (yours, held by others) or pay (owed by the household); person (an account id); "
        "currency; near (a day, nearest first); payment (a money row's id: what that payment "
        "can be set off against).",
        _schema({"expectation_id": _I, "direction": {"type": "string", "enum": ["back", "pay"]},
                 "person": _I, "currency": _S, "near": _DAY, "payment": _I}),
        False,
        lambda a: _expected_back_request(a),
    ),
    Tool(
        "paybacks",
        "The Queue's payback suggestions, as its Money in lane shows them: each money-in row "
        "waiting for its label with something left to place (payment: id, description, date, "
        "amount_minor, left_minor, currency), newest first, and the open expected-back items it "
        "could be paying back (suggestions: expectation_id, title, date, open_minor, and the "
        "reasons in words: who is named on it, how many days after, the amount against what is "
        "open). An item whose names do not match the sender is never suggested; equal amounts "
        "are offered alone, else every fit, oldest first. waiting_offers: money in left waiting "
        "for its row, with the rows that have come in since. Amounts are in minor units of the "
        "row's currency. This only reads: set_off places one payment, set_off_payments several "
        "against one item, not_a_payback sets one aside.",
        _schema(),
        False,
        _get("/api/queue/paybacks"),
    ),
    Tool(
        "bills",
        "The bills (what renews, from the rows that pay it), with the missed-bill rule's verdict, "
        "the same one fin's Bills screen and Queue show: each bill's merchant, book and type, amount and "
        "currency, how often (every: e.g. monthly, half-yearly, every 2 months), monthly_sgd (the bill spread over the months "
        "one cycle covers, in S$), the account that pays it and that account's currency "
        "(currency_mismatch: the bill is held in another currency than its paying account's, a flag for "
        "the operator to fix in fin's Bills), status, due (the day it was last due), last_paid, "
        "covered_to (the last day the paying account's statements cover) and next. state is one of "
        "missed (a due day the statements cover has no payment since; due is that day), "
        "awaiting_statement (late, but the statement for those days is not imported yet: not missed; "
        "waiting_due is the oldest due day not checked yet and waiting_on the month, YYYY-MM, of "
        "the statement that would show it), on_track, unknown_cadence "
        "(fin cannot read how often it renews), paused or stopped. judged_by is statements, or today "
        "for a bill paid from no account or from one fin holds no statements for (judged against "
        "today's date alone). counts says how many are in each, and rule says the rule in words. Filter "
        "by state. This only reads: no chat tool changes a bill.",
        _schema({"state": {"type": "string", "enum": ["missed", "awaiting_statement", "on_track", "unknown_cadence",
                                                      "paused", "stopped"]}}),
        False,
        _get("/api/bills", "state"),
    ),
]


# --- writes ------------------------------------------------------------------

_MARK = ("direction", "all", "amount", "names", "new_names", "note")


def _mark_request(a: dict) -> tuple[str, str, dict | None]:
    """A mark on its own goes to the mark route; with a book or type it goes
    to the row's own route, carrying the mark, so both are one change."""
    tx_id = int(a["tx_id"])
    label = _pick(a, "book", "type_id")
    if not label:
        return "POST", f"/api/transactions/{tx_id}/expectation", _pick(a, *_MARK)
    return "PUT", f"/api/transactions/{tx_id}", {**label, "expectation": _pick(a, *_MARK)}


def _w(name, description, properties, required, request):
    properties = {**properties, "expected_count": _EXPECTED}
    return Tool(name, description, _schema(properties, required), True, request)


def _account_body(a: dict) -> dict:
    return {"name": a["name"], "type": a["kind"]}


_WRITES = [
    _w(
        "set_label",
        "Set one row's book and/or type (type_id from the types tool). Each one sent is marked "
        "chosen: no later merchant or rule change reaches it. One not sent keeps its source.",
        {"tx_id": _I, "book": _S, "type_id": _I, "is_one_off": _B},
        ("tx_id",),
        lambda a: ("PUT", f"/api/transactions/{int(a['tx_id'])}", _pick(a, "book", "type_id", "is_one_off")),
    ),
    _w(
        "set_note",
        "Set one row's note.",
        {"tx_id": _I, "notes": _S},
        ("tx_id", "notes"),
        lambda a: ("PUT", f"/api/transactions/{int(a['tx_id'])}", _pick(a, "notes")),
    ),
    _w(
        "label_review",
        "Say what a row was: a transfer waiting for review, or any row (the app's \"Change "
        "what this was…\" uses it too), whose flow, other side, book and type it rewrites. choice: spending (type_id; book where the "
        "type proposes none), gift, own_account (account_id of a bank account, card or untracked "
        "account), company (account_id), "
        "loan_repayment (account_id of a loan), income (income_kind_id), refund (it keeps the row's "
        "book and type, or takes type_id; a row with no spending type takes its merchant's book and "
        "type, chosen; it links to its purchase when exactly one fits, and a row with neither a type "
        "to keep nor a merchant's then takes the purchase's), "
        "card_payment (paying a card's bill or a payment reversed: no book or type), holding "
        "(account_id of a holding: money that bought, or came back from, something owned). A row "
        "that stops being a refund drops its refund link. A person is no "
        "movement's other side: money lent to or owed to a person is mark_expect_back on the row "
        "(direction back on money out, pay on money in), named for them. Many rows: tx_ids, more "
        "rows the same label says the same of, in one change. A call naming more than one row "
        "writes nothing and answers with a preview (each row as it was and would be, and a token): "
        "show the operator, and on their yes call again with the same arguments and the token. A "
        "token from a preview that no longer holds (a row changed since) is refused: preview "
        "again. The preview's changes count every row the change writes (refund links too); over "
        + str(MANY_ROWS) + " it also takes expected_count, the preview's expected_count.",
        {"tx_id": _I, "choice": _S, "type_id": _I, "book": _S, "account_id": _I, "income_kind_id": _I,
         "tx_ids": {"type": "array", "items": _I, "maxItems": 99,
                    "description": "More rows labelled the same way, in the same change."},
         "token": {"type": "string", "description": "The preview's token, sent back on the operator's yes."}},
        ("tx_id", "choice"),
        lambda a: ("POST", f"/api/review/{int(a['tx_id'])}/label",
                   _pick(a, "choice", "type_id", "book", "account_id", "income_kind_id", "tx_ids", "token")),
    ),
    _w(
        "enter_figure",
        "Type a figure for an account with no statement (a loan: what is owed, positive; a "
        "holding: what it is worth; a company, person or bank account: its balance). amount is "
        "text in whole units of the account's currency, e.g. \"1250.00\".",
        {"account_id": _I, "amount": _S, "date": _DAY, "note": _S},
        ("account_id", "amount", "date"),
        lambda a: ("POST", "/api/anchors", _pick(a, "account_id", "amount", "date", "note")),
    ),
    _w(
        "correct_figure",
        "Correct a typed figure: its amount, date or note. A statement's balance cannot be changed.",
        {"anchor_id": _I, "amount": _S, "date": _DAY, "note": _S},
        ("anchor_id",),
        lambda a: ("PUT", f"/api/anchors/{int(a['anchor_id'])}", _pick(a, "amount", "date", "note")),
    ),
    _w(
        "delete_figure",
        "Delete a typed figure. A statement's balance cannot be deleted.",
        {"anchor_id": _I},
        ("anchor_id",),
        lambda a: ("DELETE", f"/api/anchors/{int(a['anchor_id'])}", None),
    ),
    _w(
        "add_rule",
        "Add a merchant rule: rows whose description matches the pattern take the merchant "
        "(service_id) and its book and type, or the rule's own overrides. " + _RULE_FLOW_SAID
        + _MOVE_CHOSEN_SAID + _PAST_SAID,
        {"pattern": _S, "service_id": _I, "match_type": _S, "book_override": _S,
         "type_override_id": _I, "priority": _I, **_RULE_FLOW, **_PAST, **_MOVE_CHOSEN},
        ("pattern", "service_id"),
        lambda a: ("POST", "/api/rules", _pick(a, "pattern", "service_id", "match_type",
                                              "book_override", "type_override_id", "priority",
                                              *_RULE_FLOW, *_PAST, *_MOVE_CHOSEN)),
    ),
    _w(
        "change_rule",
        "Change a merchant rule. " + _RULE_FLOW_SAID + "flow \"\" makes it state none again. "
        + _MOVE_CHOSEN_SAID + _PAST_SAID,
        {"rule_id": _I, "pattern": _S, "service_id": _I, "match_type": _S, "book_override": _S,
         "type_override_id": _I, "priority": _I, **_RULE_FLOW, **_PAST, **_MOVE_CHOSEN},
        ("rule_id",),
        lambda a: ("PUT", f"/api/rules/{int(a['rule_id'])}",
                   _pick(a, "pattern", "service_id", "match_type", "book_override",
                         "type_override_id", "priority", *_RULE_FLOW, *_PAST, *_MOVE_CHOSEN)),
    ),
    _w(
        "delete_rule",
        "Delete a merchant rule. " + _MOVE_CHOSEN_SAID + _PAST_SAID,
        {"rule_id": _I, **_PAST, **_MOVE_CHOSEN},
        ("rule_id",),
        lambda a: ("DELETE", f"/api/rules/{int(a['rule_id'])}", _pick(a, *_PAST, *_MOVE_CHOSEN)),
    ),
    _w(
        "change_merchant",
        "Change a merchant: its name, default book and type, note, one-off, hidden from "
        "spending views, review each time, or its role as a party. A new book or type reaches "
        "its past rows that take the default (never one the operator chose). " + _PAST_SAID,
        {"service_id": _I, "name": _S, "book": _S, "type_id": _I, "notes": _S, "is_one_off": _B,
         "exclude_from_expense_views": _B, "review_each_time": _B, **_ROLE, **_PAST},
        ("service_id",),
        lambda a: ("PUT", f"/api/services/{int(a['service_id'])}",
                   _pick(a, "name", "book", "type_id", "notes", "is_one_off",
                         "exclude_from_expense_views", "review_each_time", "role", *_PAST)),
    ),
    _w(
        "merge_merchants",
        "Merge one merchant into another: its rows and rules move to the target. " + _PAST_SAID,
        {"source_id": _I, "target_id": _I, **_PAST},
        ("source_id", "target_id"),
        lambda a: ("POST", f"/api/services/{int(a['source_id'])}/merge", _pick(a, "target_id", *_PAST)),
    ),
    _w(
        "add_company_or_person",
        "Add a company (so movements can name it as their other side) or a person (to name on "
        "what is expected back or to pay back).",
        {"kind": {"type": "string", "enum": ["company", "person"]}, "name": _S},
        ("kind", "name"),
        lambda a: ("POST", "/api/accounts", _account_body(a)),
    ),
    _w(
        "create_account",
        "Add an account before its statements are imported, so the import finds it by its name "
        "or last four digits and does not make one with a kind guessed from the name. kind: a "
        "holding is something owned that no statement reports; untracked is the household's own "
        "account fin holds no statements for (a family member's own bank). currency: SGD unless "
        "given; owner: Household unless a company's. Refused when fin already has an account of "
        "that name, in any case: read accounts first. A loan bought on hire purchase may carry "
        "its terms, so its statements' total of remaining instalments imports as principal owed: "
        "terms {financed, months, flat_rate (percent a year) or interest (over the term), "
        "first_instalment (YYYY-MM-DD), owed_from (YYYY-MM-DD, the day the amount financed is owed "
        "from; the last day of the month before the first instalment unless given), method rule-of-78}; amounts as text, e.g. \"150000.00\". "
        "Terms on an account fin holds are the operator's, on Lists › Accounts.",
        {"name": _S, "kind": {"type": "string", "enum": list(account_kind.KIND_NAMES)},
         "currency": _S, "owner": {"type": "string", "description": "Household, or a company book: read books"},
         "last_four": {"type": "string", "pattern": "^[0-9]{4}$",
                       "description": "four digits: the account's last four, e.g. 3528"},
         "terms": {"type": "object", "description": "A loan's hire-purchase terms (kind loan only).",
                   "properties": {"financed": _S, "months": _I, "flat_rate": _S, "interest": _S,
                                  "first_instalment": _DAY, "owed_from": _DAY,
                                  "method": {"type": "string", "enum": list(loan_terms.METHOD_NAMES)}}}},
        ("name", "kind"),
        lambda a: ("POST", "/api/accounts",
                   {**_account_body(a), **_pick(a, "currency", "owner", "last_four", "terms")}),
    ),
    _w(
        "create_merchant",
        "Add a merchant, with the book and type its rows take (type_id from the types tool; a type "
        "with no book takes the book the type proposes, and one that proposes none needs a book). "
        "It labels no row: add_rule gives it a wording, and plan_preview and plan_apply attach a "
        "card's rows. Refused when fin already holds a merchant of that name, in any case: read "
        "merchants first. role: the part it plays as a party (a party for people, say, made to "
        "join their person accounts with link_party).",
        {"name": _S, "book": _S, "type_id": _I, **_ROLE},
        ("name",),
        lambda a: ("POST", "/api/services", _pick(a, "name", "book", "type_id", "role")),
    ),
    _w(
        "link_party",
        "Join an account to a party: a merchant (service_id from merchants), so a person's "
        "accounts, or a company and the merchant its money arrives under, are known as one. "
        "Only a person, company, loan, holding or untracked account joins one; two accounts "
        "joined to one party stay two accounts with their own balances (they are linked, not "
        "merged). Without service_id the account leaves its party. One change; it moves no row "
        "and no figure.",
        {"account_id": _I, "service_id": _I},
        ("account_id",),
        # service_id left out is sent as null, said: the route refuses a body without it.
        lambda a: ("PUT", f"/api/accounts/{int(a['account_id'])}/party",
                   {"service_id": a.get("service_id")}),
    ),
    _w(
        "pair_transfers",
        "Pair moves between household accounts (same amount, opposite sign, within three "
        "days). Never changes a row set by hand.",
        {},
        (),
        lambda a: ("POST", "/api/pair-matching", {}),
    ),
    _w(
        "fetch_rate",
        "Fetch and save the European Central Bank's rate for a currency on a day.",
        {"currency": _S, "date": _DAY},
        ("currency", "date"),
        lambda a: ("POST", "/api/rates/fetch", _pick(a, "currency", "date")),
    ),
    _w(
        "set_rate",
        "Enter a rate for a day in place of the saved one (SGD for one unit, decimal text).",
        {"currency": _S, "date": _DAY, "rate": _S},
        ("currency", "date", "rate"),
        lambda a: ("PUT", "/api/rates", _pick(a, "currency", "date", "rate")),
    ),
    _w(
        "mark_expect_back",
        "Expect some of a row back (direction back, on money out: a dinner paid for others) or "
        "mark money in as to pay back (direction pay: a loan from family, a friend who paid "
        "first). Give all: true for the whole row, or amount (text in whole units of the row's "
        "currency, e.g. \"200.00\") for a part; names: person account ids; new_names: names "
        "of people not yet in fin (found or made); note. The part expected back stops being "
        "spending at once. Marking a row that is already marked edits its mark; undo_mark "
        "removes it (this is not the undo tool). book and/or type_id (from the types tool) label "
        "the row in the same change, as set_label does (each marked chosen): a group order with "
        "no type yet is labelled and marked at once, and a mark the label makes impossible (a "
        "company's book) writes neither.",
        {"tx_id": _I, "direction": {"type": "string", "enum": ["back", "pay"]}, "all": _B,
         "amount": _S, "names": {"type": "array", "items": _I},
         "new_names": {"type": "array", "items": _S}, "note": _S, "book": _S, "type_id": _I},
        ("tx_id", "direction"),
        lambda a: _mark_request(a),
    ),
    _w(
        "undo_mark",
        "Remove the mark on an expectation (expectation_id from expected_back): the row counts "
        "whole again. Refused, naming what is set against it, until those are unlinked; refused "
        "after a let-go until that is undone. This is not the undo tool (that takes a change).",
        {"expectation_id": _I},
        ("expectation_id",),
        lambda a: ("DELETE", f"/api/expectations/{int(a['expectation_id'])}", None),
    ),
    _w(
        "set_off",
        "Set a payment (a money row, tx_id) off against what is expected back: placements, each "
        "{expectation_id, amount} with amount text in whole units of the payment's currency; "
        "one request places the payment against several items, all or none. What is left of "
        "the payment stays its own to label. waiting {person_id?, note?, amount?} leaves that "
        "much of the payment (all that is left without amount) waiting for its row, as money "
        "that arrived before its purchase; with a waiting part, placements may be empty. "
        "Money in sets off expected back; money out sets off to pay back. Never income or spending.",
        {"tx_id": _I,
         "placements": {"type": "array", "items": {
             "type": "object", "properties": {"expectation_id": _I, "amount": _S},
             "required": ["expectation_id", "amount"], "additionalProperties": False}},
         "waiting": {"type": "object", "properties": {"person_id": _I, "note": _S, "amount": _S},
                     "additionalProperties": False}},
        ("tx_id",),
        lambda a: ("POST", f"/api/transactions/{int(a['tx_id'])}/set-offs",
                   {"placements": a.get("placements", []), **_pick(a, "waiting")}),
    ),
    _w(
        "set_off_payments",
        "Set several payments off against one expectation in one call (a group order the people "
        "paid back one by one): payments, each {tx_id, amount} with amount text in whole units of "
        "that payment's currency. All or none, as one change that one undo takes back; a refusal "
        "names the payment it stopped on and writes nothing. Each payment is set off as set_off "
        "would set it off against this one item (what is left of it stays its own to label, and "
        "the sender's wording is learnt). The paybacks read gives the payments and what each has "
        "left (left_minor).",
        {"expectation_id": _I,
         "payments": {"type": "array", "minItems": 1, "items": {
             "type": "object", "properties": {"tx_id": _I, "amount": _S},
             "required": ["tx_id", "amount"], "additionalProperties": False}}},
        ("expectation_id", "payments"),
        lambda a: ("POST", f"/api/expectations/{int(a['expectation_id'])}/set-offs",
                   {"payments": a.get("payments", [])}),
    ),
    _w(
        "unlink_set_off",
        "Unlink one set-off (set_off_id from an expectation's page in expected_back): the "
        "payment's part returns to the payment and the item's open amount to the item. A "
        "waiting set-off unlinks too, and the payment returns to its own. Refused after a "
        "let-go until that is undone.",
        {"set_off_id": _I},
        ("set_off_id",),
        lambda a: ("DELETE", f"/api/set-offs/{int(a['set_off_id'])}", None),
    ),
    _w(
        "let_go",
        "Let the whole open rest of an expectation go (the operator's call: ask before using "
        "it). became must be named, with no default: mine (it was the household's spending "
        "after all: counted in the row's book and type, type_id when the row has none) or "
        "gift (Gifts & Donations, to_id the person it went to, if any). On an item to pay "
        "back only gift is offered, and type_id is the income type it counts as. Counted on "
        "today.",
        {"expectation_id": _I, "became": {"type": "string", "enum": ["mine", "gift"]},
         "to_id": _I, "type_id": _I},
        ("expectation_id", "became"),
        lambda a: ("POST", f"/api/expectations/{int(a['expectation_id'])}/let-go",
                   _pick(a, "became", "to_id", "type_id")),
    ),
    _w(
        "undo_let_go",
        "Undo a let-go: the open rest is open again. (This is not the undo tool.)",
        {"expectation_id": _I},
        ("expectation_id",),
        lambda a: ("DELETE", f"/api/expectations/{int(a['expectation_id'])}/let-go", None),
    ),
    _w(
        "not_a_payback",
        "Say a money-in row is not a payback: it is not suggested for set-off again and stays "
        "a row to label.",
        {"tx_id": _I},
        ("tx_id",),
        lambda a: ("POST", f"/api/transactions/{int(a['tx_id'])}/not-a-payback", {}),
    ),
    _w(
        "undo",
        "Undo one change made from the chat. Refused, naming it, when a later change touched "
        "the same rows: undo that one first. A change made in fin itself is the operator's to undo.",
        {"entry_id": _I},
        ("entry_id",),
        lambda a: ("POST", f"/api/history/{int(a['entry_id'])}/undo", {}),
    ),
]

# --- plans (chat-plan-apply 02): a batch of teaches, previewed, then applied --
# The Queue's labelling work as one change: the operator says yes to the
# preview's numbers, and the apply writes the batch only on that preview's
# token. Both stop with the switch off; the preview too, as it leads only to
# a write.

_PLAN_ITEM = {
    "type": "object",
    "properties": {
        "tx_ids": {"type": "array", "items": _I, "description": "The card's rows this merchant labels."},
        "service_name": {**_S, "description": "A new merchant's name (or one fin finds by that name)."},
        "service_id": {**_I, "description": "A merchant fin holds already, to attach the rows to."},
        "pattern": {**_S, "description": "The wording its rule recognises."},
        "wordings": {"type": "array", "items": _S, "description": "Other wordings, each a rule too."},
        "match_type": {"type": "string", "enum": ["contains", "startswith", "exact"]},
        "type_id": _I,
        "book": _S,
        "check_each_row": {**_B, "description": "Check each row: every row from this merchant is checked."},
        "past": {"type": "string", "enum": ["leave", "queue"],
                 "description": "With check_each_row: the merchant's past rows stay as they are, or go to the Queue."},
        "past_rows": {"type": "string", "enum": ["apply", "only_new"],
                      "description": "The operator's answer for rows another merchant holds that a wording "
                                     "reaches (the preview's overlaps): apply moves them too; unanswered, "
                                     "only new rows take it."},
        "move_chosen": {**_B, "description": "With past_rows apply, on the operator's yes: also move the rows "
                                             "the item's flows_chosen lists, kept only because the operator "
                                             "chose their book or type by hand."},
    },
    "required": ["tx_ids"],
    "additionalProperties": False,
}
_PLAN_ITEMS = {
    "type": "array", "items": _PLAN_ITEM, "minItems": 1, "maxItems": 20,
    "description": "One teach per merchant, up to 20: what saving its Queue card would do.",
}

_PLANS = [
    Tool(
        "plan_preview",
        "Preview a batch of teaches for the Queue's cards (from the queue tool): writes nothing. "
        "Ask the operator about the cards in batches across turns, then preview the settled "
        "batch: the answer gives, per merchant, the rows that would be labelled (a row's was is the "
        "label it holds now, which the batch overwrites: tell the operator), the rows of "
        "another merchant a wording would reach (overlaps: ask whether they move too, past_rows), "
        "every row whose flow or other side it would move, a row labelled for itself among them "
        "(flows: tell the operator), the rows it would move but keeps only because the operator chose their "
        "book or type by hand (flows_chosen: name them; an item's move_chosen true, with past_rows apply, "
        "moves them too, on the operator's yes, and is previewed again as part of the items), any refusal by merchant, the batch's merchants and rows labelled, "
        "the rows only checked (rows_checked), and a token (none while "
        "anything is refused). Show the operator those numbers; call plan_apply only on their yes.",
        _schema({"items": _PLAN_ITEMS}, ("items",)),
        False,
        lambda a: ("POST", "/api/plans/preview", _pick(a, "items")),
    ),
    Tool(
        "plan_apply",
        "Apply a previewed batch, only after the operator said yes to plan_preview's numbers: "
        "the same items, and the preview's token. It is written as one change, which one undo "
        "takes back. Refused, writing nothing, when any item is refused or the book changed "
        "since the preview (a row labelled since, or the batch applied already): preview again "
        "and ask again. The token is the operator's yes to the preview's count, so no "
        "expected_count is taken.",
        _schema({"items": _PLAN_ITEMS,
                 "token": {"type": "string", "description": "plan_preview's token for exactly these items."}},
                ("items", "token")),
        True,
        lambda a: ("POST", "/api/plans/apply", _pick(a, "items", "token")),
    ),
]

# --- the off switch (01's Off switches): off only, never on ------------------

_SWITCHES = [
    Tool(
        "turn_off_writes",
        "Turn off Claude's writes to fin: every write tool then refuses, and reads still work. "
        "Use it when the operator asks, or when something looks wrong. Only the operator can "
        "turn writes back on, in fin's app; no tool does.",
        _schema(),
        True,
        lambda a: ("PUT", "/api/chat-writes", {"enabled": False}),
    ),
]

TOOLS = _READS + _WRITES + _PLANS + _SWITCHES
# What the Claude app shows on a tool's permission prompt, in the operator's
# words: a read says "Read", a write says what it changes. One per tool; a
# test holds the two lists together.
TITLES = {
    "balance_sheet": "Read the balance sheet",
    "spending_cards": "Read a month's spending figures",
    "spending_by_type": "Read spending by type",
    "spending_by_month": "Read spending by month",
    "transactions": "Read rows",
    "review_summary": "Read the transfers waiting for a label",
    "queue": "Read your Queue",
    "queue_summary": "Read how much waits in your Queue",
    "accounts": "Read your accounts",
    "figures": "Read statement balances and typed figures",
    "merchants": "Read your merchants",
    "rules": "Read your merchant rules",
    "rule_drift": "Read rows the rules would now label differently",
    "types": "Read the type list",
    "books": "Read the books",
    "flows": "Read the flows",
    "coverage": "Read which statements are imported",
    "rates": "Read saved exchange rates",
    "history": "Read the change history",
    "change": "Read one change, before and after",
    "expected_back": "Read what is expected back",
    "paybacks": "Read the payback suggestions",
    "bills": "Read your bills",
    "set_label": "Change a row's book or type",
    "set_note": "Change a row's note",
    "label_review": "Say what a row was",
    "enter_figure": "Enter a figure for an account",
    "correct_figure": "Correct a typed figure",
    "delete_figure": "Delete a typed figure",
    "add_rule": "Add a merchant rule",
    "change_rule": "Change a merchant rule",
    "delete_rule": "Delete a merchant rule",
    "change_merchant": "Change a merchant",
    "merge_merchants": "Merge two merchants",
    "add_company_or_person": "Add a company or a person",
    "create_account": "Add an account",
    "create_merchant": "Add a merchant",
    "link_party": "Join an account to a party",
    "pair_transfers": "Pair transfers between your accounts",
    "fetch_rate": "Fetch and save an exchange rate",
    "set_rate": "Enter an exchange rate",
    "mark_expect_back": "Mark money as expected back",
    "undo_mark": "Remove an expected-back mark",
    "set_off": "Set a payment off against what is expected back",
    "set_off_payments": "Set several payments off against one expectation",
    "unlink_set_off": "Unlink a set-off",
    "let_go": "Let an expected-back amount go",
    "undo_let_go": "Undo a let-go",
    "not_a_payback": "Say a row is not a payback",
    "undo": "Undo a chat change",
    "plan_preview": "Preview a batch of Queue changes",
    "plan_apply": "Apply a batch of Queue changes",
    "turn_off_writes": "Turn off Claude's writes to fin",
}
# The tools the switch stops: every write but the switch itself, and the
# plan's preview, which leads only to a write.
_STOPPED_WHEN_OFF = frozenset(t.name for t in _WRITES + _PLANS)
BY_NAME = {t.name: t for t in TOOLS}


class ToolRefused(Exception):
    """The call was not made: an unknown tool or arguments it does not take."""


def _hidden_out(value):
    if isinstance(value, dict):
        return {k: _hidden_out(v) for k, v in value.items() if k not in HIDDEN_KEYS}
    if isinstance(value, list):
        return [_hidden_out(v) for v in value]
    return value


def _checked(tool: Tool, args: dict) -> dict:
    args = dict(args or {})
    schema = tool.input_schema
    unknown = set(args) - set(schema["properties"])
    if unknown:
        raise ToolRefused(f"{tool.name} does not take {', '.join(sorted(unknown))}")
    missing = [k for k in schema["required"] if args.get(k) is None]
    if missing:
        raise ToolRefused(f"{tool.name} needs {', '.join(missing)}")
    for k, v in args.items():
        kind = schema["properties"][k].get("type")
        ok = {
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "string": isinstance(v, str),
            "boolean": isinstance(v, bool),
        }.get(kind, True)
        if not ok:
            raise ToolRefused(f"{k} must be a{'n' if kind == 'integer' else ''} {kind}")
        allowed = schema["properties"][k].get("enum")
        if allowed and v not in allowed:
            raise ToolRefused(f"{k} must be one of {', '.join(allowed)}")
        # A pattern the schema declares; the refusal says it as the description does.
        pattern = schema["properties"][k].get("pattern")
        if pattern and not re.fullmatch(pattern, v, re.ASCII):
            raise ToolRefused(f"{k} must be {schema['properties'][k]['description']}")
    return args


def _send(client, method: str, path: str, payload, environ: dict):
    if method == "GET":
        query = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in (payload or {}).items()}
        return client.get(path, query_string=query, environ_base=environ)
    return client.open(path, method=method, json=payload, environ_base=environ)


def _answer(resp) -> dict:
    if resp.status_code >= 500:
        return {"ok": False, "error": FIXED_ERROR}
    try:
        body = resp.get_json(silent=True)
    except Exception:
        body = None
    if resp.status_code >= 400:
        message = body.get("error") if isinstance(body, dict) else None
        answer = {"ok": False, "error": message if isinstance(message, str) else FIXED_ERROR}
        # A change that would relabel past rows and was not answered: the
        # preview's numbers, for Claude to ask with; a stale answer says so.
        if resp.status_code == 409 and isinstance(body, dict):
            if isinstance(body.get("preview"), dict):
                answer["preview"] = _hidden_out(body["preview"])
            if body.get("stale") is True:
                answer["stale"] = True
        # A plan refused by item: each refusal by merchant, for Claude to ask about.
        if isinstance(body, dict) and isinstance(body.get("refusals"), list):
            answer["refusals"] = _hidden_out(body["refusals"])
        return answer
    return {"ok": True, "result": _hidden_out(body)}


def call(name: str, args: dict | None, actor: str) -> dict:
    """Run one tool for a chat client. Returns {"ok": True, "result": ...}
    (and "change": the history entry and its row count, for a write that
    changed something), {"ok": False, "error": ...}, or, for a write over
    MANY_ROWS rows without the agreed count, {"ok": False, "stopped": True,
    "would_change_rows": n} with nothing changed."""
    import app as fin_app  # the Flask app; imported here so importing TOOLS is cheap

    tool = BY_NAME.get(name)
    if tool is None:
        return {"ok": False, "error": f"no tool named {name!r}"}
    # The operator's switch, read on every call: off (or unreadable) refuses
    # every write before anything runs.
    if name in _STOPPED_WHEN_OFF and not chat_writes.enabled():
        return {"ok": False, "error": chat_writes.WRITES_OFF}
    try:
        args = _checked(tool, args or {})
        expected = args.pop("expected_count", None)
        method, path, payload = tool.request(args)
    except ToolRefused as e:
        return {"ok": False, "error": str(e)}
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "error": f"{name} was given arguments it cannot use"}

    # The chat identity, where the Flask app's second layer (access_gate.
    # RequireGateIdentity) takes it: an environ key no HTTP request can set.
    # It stamps the write via='chat', actor=the client the chat gate verified.
    environ = {access_gate.CHAT_CALL_ENVIRON_KEY: access_gate.chat_call_identity(actor)}
    client = fin_app.app.test_client()
    if not tool.write:
        try:
            return _answer(_send(client, method, path, payload, environ))
        except Exception:
            return {"ok": False, "error": FIXED_ERROR}

    with history.WRITE_LOCK:
        if name == "undo":
            conn = db.get_connection()
            try:
                undoing = conn.execute(
                    "SELECT via FROM change_entries WHERE id = ?", (int(args["entry_id"]),)
                ).fetchone()
                # An undo writes back exactly the rows its entry changed, so
                # its count is known before anything is written: ask first.
                rows = history.row_count(conn, int(args["entry_id"]))
            finally:
                conn.close()
            # The chat undoes only what the chat did (a default the operator
            # may overturn): an import, an account or a subscription is not
            # the chat's to take back. Fin's own undo is not limited.
            if undoing is not None and undoing["via"] != history.VIA_CHAT:
                return {"ok": False, "error": UNDO_NOT_CHAT}
            if rows > MANY_ROWS and expected != rows:
                return _stopped(name, rows)
        try:
            resp = _send(client, method, path, payload, environ)
        except Exception:
            # The type and text stay here: they can carry row contents.
            return {"ok": False, "error": FIXED_ERROR}
        answer = _answer(resp)
        entry_id = resp.headers.get("X-Fin-Change")
        if entry_id is None:
            return answer
        rows = int(resp.headers.get("X-Fin-Change-Rows", "0"))
        # An answer to a preview, sent back with its token, stands on the
        # count the operator was asked: the route wrote only because the
        # token still held, so the change is the one the preview showed
        # (fin-surfaces 51). An answer with no token behind it is asked the
        # count as any other write.
        # A plan applies only on its preview's token (chat-plan-apply 02),
        # so it always stands on the preview's count.
        answered = name == "plan_apply" or (
            name in _PREVIEWED and args.get("past") is not None and args.get("token") is not None)
        if rows > MANY_ROWS and expected != rows and not answered:
            conn = db.get_connection()
            try:
                history.discard(conn, int(entry_id))
            except Exception:
                # The change stands: say so, and where it is. The type and
                # text stay here: they can carry row contents.
                return {
                    "ok": False,
                    "change": {"entry_id": int(entry_id), "rows": rows},
                    "error": (
                        f"this changed {rows} rows, over the {MANY_ROWS} that need the operator's "
                        f"count, and the change could not be put back: it stands as change "
                        f"{entry_id}. Tell the operator; they can undo it from fin's history."
                    ),
                }
            finally:
                conn.close()
                db.invalidate_rules_cache()
            return _stopped(name, rows)
        if rows > MANY_ROWS:
            # It stands on the count the operator agreed to in chat: say so
            # in the history ("asked first, yes in chat").
            conn = db.get_connection()
            try:
                history.record_asked(conn, int(entry_id), rows)
            finally:
                conn.close()
        answer["change"] = {"entry_id": int(entry_id), "rows": rows}
        return answer


def _stopped(name: str, rows: int) -> dict:
    return {
        "ok": False,
        "stopped": True,
        "would_change_rows": rows,
        "error": (
            f"this would change {rows} rows, so nothing was changed. Tell the operator "
            f"the count, and if they agree call {name} again with expected_count={rows}."
        ),
    }
