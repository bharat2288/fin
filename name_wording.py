"""The name and the wording a teach suggests for a statement line, on the server
(chat-queue-read 01).

Python twins of suggestName and suggestPattern in static/app.js, where the
Queue's cards draw them, so chat reads the same name and wording the operator
sees (GET /api/queue/cards). The rules are the JavaScript's, copied as data;
tests/test_name_wording_parity.py runs both over one table and fails on any
difference, so a change to one is a change to both.

JavaScript's regular expressions are not Python's in three places, and the
patterns below are written for that: its \\s is every Unicode space (_WS
here), its \\d, \\w and \\b are ASCII (re.ASCII), and its $ is the end of the
text, never before a last line break (\\Z here).
"""

from __future__ import annotations

import re

# JavaScript's \s: white space and line terminators.
_WS = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_S = f"[{_WS}]"
_NS = f"[^{_WS}]"


def _rx(pattern: str, flags: int = 0) -> re.Pattern:
    """A JavaScript pattern as Python reads it: \\s and \\S become JavaScript's
    spaces, inside a [...] set as its characters, outside it as a set."""
    out, in_set, i = [], False, 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            pair = pattern[i:i + 2]
            if pair == r"\s":
                out.append(_WS if in_set else _S)
            elif pair == r"\S" and not in_set:
                out.append(_NS)
            else:
                out.append(pair)
            i += 2
            continue
        if c == "[" and not in_set:
            in_set = True
        elif c == "]" and in_set:
            in_set = False
        out.append(c)
        i += 1
    return re.compile("".join(out), re.ASCII | flags)


def _trim(text: str) -> str:
    return re.sub(f"^{_S}+|{_S}+\\Z", "", text)


# --- suggestPattern: the wording a save suggests as its rule ----------------------

_NOT_A_MERCHANT = _rx(r"\b(PAYNOW|FAST PAYMENT|TRANSFER|I-BANK|GIRO)\b")
_COUNTRY = _rx(r"\s+(SG|SGP|SIN|US|USA|GB|GBR|AU|AUS)\s*\Z", re.IGNORECASE)
_DAY_MONTH = _rx(r"\s+\d{2}/\d{2}\Z")
_GLUED = _rx(r"(\S*?\w\*)\S*\d")
_LONG_CODE = _rx(r"[A-Z0-9]{8,}")


def _stands(word: str) -> bool:
    letters = len(re.findall("[A-Z]", word))
    return letters >= 6 or (letters >= 4 and re.search("[^A-Z]", word) is not None)


def suggest_pattern(description: str | None) -> str:
    """suggestPattern: the merchant text up to a reference, upper case; empty
    for a payee or transfer line."""
    pattern = (description or "").upper()
    if _NOT_A_MERCHANT.search(pattern):
        return ""
    pattern = _DAY_MONTH.sub("", _COUNTRY.sub("", pattern, count=1), count=1)
    parts = re.split(f"{_S}+", pattern)
    glued = _GLUED.match(parts[0])
    if glued and _stands(glued[1]):
        return glued[1]
    cutoff, ref = len(parts), False
    for i in range(1, len(parts)):
        ref = re.search("[0-9]", parts[i]) is not None
        if ref or _LONG_CODE.fullmatch(parts[i]):
            cutoff = i
            break
    least = 1 if ref and _stands(parts[0]) else 2
    return " ".join(parts[:max(cutoff, least)])


# --- suggestName: fin's tidy-up of the wording into a merchant's name ---------------

# NAME_RULES in static/app.js, as written there.
NAME_RULES = {
    "payee": _rx(r"PAY ?NOW|PAYLAH|\bFAST\b|\bIBG\b|GIRO|TRANSFER|\bTRF\b|INWARD|REMITT|\bATM\b|\bCASH\b|\bFUNDS\b|I-BANK|:IB\b|\bMEP\b|\bFT\d"),
    "bank": _rx(r"^(ADVICE|BILL PAYMENT|PAYMENT\b|PAYMT\b|CARD PAYMENT|SERVICE CHARGE|MISC DEBIT|BUSINESS ADVANCE|INTEREST|CASH REBATE|SALARY|DIVIDEND|GST\b|FINANCE CHARGE|CCY CONVERSION FEE|FEE\Z)|\b(SERVICE FEE|ADMINISTRATIVE FEE|FOREIGN CURRENCY TRANSACTION FEE|ANNUAL (MEMBERSHIP )?FEE|LATE (PAYMENT )?(CHARGE|FEE)|ADMIN(ISTRATION)? FEE|OVERLIMIT FEE|GOODS (AND|&) SERVICES TAX|((CREDIT )?CARDS?|CC) (PAYMENT|PAYMT))\b"),
    "processor": _rx(r"^(?:(?:2C2P|2C2|PAYPAL|PP|STRIPE|SQUARE|SQ|OMISE|GLOBAL-E|GLOBALE|ADYEN|SUMUP|ATOME|SMP|PYU|EZYPAYSGD|EZYPAY)\s*[*/]\s*|(?:2C2P|PAYPAL|STRIPE|OMISE|GLOBAL-E|GLOBALE|SP)\s+(?!(?:GROUP|SERVICES)\b)(?=\S))"),
    "web": _rx(r"^(?:WWW\.)?(?:[A-Z0-9-]+\.)*?([A-Z0-9-]+)\.(?:COM|NET|ORG|IO|CO|AI|APP|SHOP|STORE)(?:\.[A-Z]{2})?/?\Z"),
    "branch": _rx(r"\s+-\s+.*\Z"),
    "trailing": _rx(r"(\s+(SINGAPORE|SGP|SGD|SG|SIN|USA|USD|US|GBR|GBP|GB|UK|LONDON|AUS|AUD|AU|SYDNEY|IND|INR|IN|HKG|HKD|HK|JPN|JPY|JP|TOKYO|MYS|MYR|MY|THA|THB|TH|BANGKOK|IDN|IDR|ID|NLD|NL|IRL|IE|DUBLIN|CAN|CAD|CA|DEU|DE|FRA|FR|EUR|EU|CHF|NZD|CNY))+\Z"),
}
_EDGES = _rx(r"^[\s*\-/.]+|[\s*\-/.]+\Z")
_WORD_START = _rx(r"(^|[\s\-/&(])([a-z])")


def suggest_name(description: str | None) -> str:
    """suggestName: the merchant's name as fin would save it; empty when the
    wording names no merchant (a PayNow, a transfer, a bank line)."""
    text = _rx(r"\s+").sub(" ", (description or "").upper())
    text = re.sub(r" ?\* ?", "*", _trim(text))
    if NAME_RULES["payee"].search(text):
        return ""
    text = NAME_RULES["processor"].sub("", text, count=1)
    words = text.split(" ")
    if not re.search("[A-Z]", words[0]):
        return ""
    # "X*REST": a web address X names the merchant; any other X keeps the rest.
    star = words[0].find("*")
    if star > 0 and NAME_RULES["web"].search(words[0][:star]):
        words = [words[0][:star]]
    else:
        words = _trim(" ".join(words).replace("*", " ")).split(" ")
    kept = []
    for i, w in enumerate(words):
        web = NAME_RULES["web"].search(w)
        kept.append(w if not web else web[1] if i == 0 else "")
    words = [w for w in kept if w]
    # every word from the first after the name's own with a digit is a reference
    ref = next((i for i, w in enumerate(words) if i > 0 and re.search("[0-9]", w)), -1)
    if ref > 0:
        words = words[:ref]
    text = NAME_RULES["branch"].sub("", " ".join(words), count=1)
    text = _EDGES.sub("", NAME_RULES["trailing"].sub("", text, count=1))
    if not re.search("[A-Z]", text) or NAME_RULES["bank"].search(text):
        return ""
    return _WORD_START.sub(lambda m: m[1] + m[2].upper(), text.lower())
