"""Send statement files to fin's import, one file at a time (fin-surfaces 01).

The way a Claude Code session on the operator's machine imports a folder of
statements into the hosted fin: the files go straight to fin's own import
(upload, the tie check, confirm), so the model never handles their contents
and fin's parser and tie check see the exact files the bank issued. Each file
that is confirmed is one entry in fin's change history, undoable from fin.

    python fin_upload.py [--dry-run] <file or folder> ...

Environment (names only; values come from the operator's credential store):
    FIN_URL                    fin's address, e.g. the hosted one, or
                               http://127.0.0.1:8450 for the desk app
    FIN_UPLOAD_CLIENT_ID       the gate's service token for imports
    FIN_UPLOAD_CLIENT_SECRET   (both unset for the desk app)

A statement fin refuses (one that does not tie, an unknown format, a currency
its account is not kept in) is reported and not imported; the others go on.
--dry-run reads every file and reports what fin would import, importing
nothing. Exit 0 when nothing was refused, 1 otherwise, 2 for a usage error.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Callable
from urllib import error, request

EXTENSIONS = (".pdf", ".csv", ".xls", ".xlsx")

# (method path, body bytes, content type) -> (status, parsed JSON or None)
Sender = Callable[[str, bytes, str], tuple[int, object]]


def statement_files(targets: list[str]) -> list[Path]:
    """The files to send: each file named, and the statement files directly
    inside each folder named, in name order."""
    found = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            found.extend(sorted(p for p in path.iterdir()
                                if p.is_file() and p.suffix.lower() in EXTENSIONS))
        elif path.is_file():
            found.append(path)
        else:
            raise FileNotFoundError(target)
    return found


def http_sender(base_url: str, client_id: str | None, client_secret: str | None) -> Sender:
    """Send to fin over HTTPS (or HTTP on loopback) with the gate's service
    token, when there is one."""
    base = base_url.rstrip("/")

    def send(path: str, body: bytes, content_type: str) -> tuple[int, object]:
        req = request.Request(base + path, data=body, method="POST")
        req.add_header("Content-Type", content_type)
        if client_id and client_secret:
            req.add_header("CF-Access-Client-Id", client_id)
            req.add_header("CF-Access-Client-Secret", client_secret)
        try:
            with request.urlopen(req, timeout=120) as resp:
                status, raw = resp.status, resp.read()
        except error.HTTPError as e:
            status, raw = e.code, e.read()
        try:
            return status, json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return status, None

    return send


def _multipart(path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return head + path.read_bytes() + tail, f"multipart/form-data; boundary={boundary}"


def _error_of(status: int, body) -> str:
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    if status in (401, 403):
        return "fin refused the credentials (check FIN_UPLOAD_CLIENT_ID and FIN_UPLOAD_CLIENT_SECRET)"
    return f"fin answered {status}"


def import_one(path: Path, send: Sender, dry_run: bool = False) -> tuple[bool, str]:
    """Upload one file, and confirm it unless dry_run or fin refused part of
    it. Returns (refused nothing, one line saying what happened)."""
    body, content_type = _multipart(path)
    status, preview = send("/api/import/upload", body, content_type)
    if status != 200 or not isinstance(preview, dict):
        return False, f"{path.name}: refused: {_error_of(status, preview)}"
    errors = preview.get("errors") or []
    groups = preview.get("groups") or []
    if errors:
        said = "; ".join(e.get("error", "refused") for e in errors)
        return False, f"{path.name}: refused, nothing imported: {said}"
    if not groups:
        return False, f"{path.name}: refused: fin found no rows in it"
    summary = ", ".join(
        f"{g.get('account')}: {g.get('total', len(g.get('transactions', [])))} rows "
        f"({'ties' if g.get('tie') == 'ties' else 'not checked'})"
        for g in groups
    )
    if dry_run:
        return True, f"{path.name}: would import {summary}"
    confirm = {
        "import_id": preview.get("import_id"),
        "groups": [
            {"account": g["account"], "transactions": g["transactions"],
             "statements": g.get("statements", [])}
            for g in groups
        ],
    }
    status, done = send("/api/import/confirm", json.dumps(confirm).encode("utf-8"), "application/json")
    if status != 200 or not isinstance(done, dict) or not done.get("success"):
        return False, f"{path.name}: refused at confirm, nothing imported: {_error_of(status, done)}"
    return True, (
        f"{path.name}: imported {done.get('transactions_saved', 0)} new rows "
        f"({done.get('duplicates_skipped', 0)} already there) into {summary}"
    )


def main(argv: list[str], send: Sender | None = None) -> int:
    dry_run = "--dry-run" in argv
    targets = [a for a in argv if a != "--dry-run"]
    if not targets:
        print("usage: python fin_upload.py [--dry-run] <file or folder> ...")
        return 2
    if send is None:
        base = os.environ.get("FIN_URL")
        if not base:
            print("FIN_URL is not set: say which fin to send to")
            return 2
        send = http_sender(base, os.environ.get("FIN_UPLOAD_CLIENT_ID"),
                           os.environ.get("FIN_UPLOAD_CLIENT_SECRET"))
    try:
        files = statement_files(targets)
    except FileNotFoundError as e:
        print(f"no such file or folder: {e}")
        return 2
    if not files:
        print("no statement files found")
        return 0
    clean = True
    for path in files:
        ok, line = import_one(path, send, dry_run)
        clean &= ok
        print(line)
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
