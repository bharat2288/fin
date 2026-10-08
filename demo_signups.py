"""The emails the public sample has collected, as CSV on stdout.

    DEMO_DATA_DIR=/data python demo_signups.py > signups.csv

Run where the sample's volume is (a Railway shell on the sample service).
Reads the control file only; writes nothing.
"""

from __future__ import annotations

import csv
import os
import sqlite3
import sys
from pathlib import Path

import demo_serve

FIELDS = ("email", "first_signed_in_at", "last_signed_in_at", "sign_ins")


def main() -> int:
    data_dir = Path((os.environ.get(demo_serve.DATA_DIR_VARIABLE) or "").strip() or demo_serve.DEFAULT_LOCAL_DATA_DIR)
    control = data_dir / "demo-control.db"
    if not control.exists():
        print(f"no sign-ups yet: {control} does not exist", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{control}?mode=ro", uri=True)
    try:
        rows = conn.execute(f"SELECT {', '.join(FIELDS)} FROM signups ORDER BY first_signed_in_at, email").fetchall()
    finally:
        conn.close()
    writer = csv.writer(sys.stdout)
    writer.writerow(FIELDS)
    writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
