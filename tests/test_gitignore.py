"""The files a database leaves beside it are never committed: SQLite's
shared-memory and write-ahead files, and the conversion runner's backups."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.parametrize("name", [
    "fin.db",
    "fin.db-shm",
    "fin.db-wal",
    "fin.db-journal",
    "fin.db.pre-movements-20261005.bak",
    "fin.db.pre-flows-20261005-2.bak",
])
def test_a_database_and_what_it_leaves_beside_it_are_ignored(name):
    if subprocess.run(["git", "-C", str(ROOT), "rev-parse"], capture_output=True).returncode:
        pytest.skip("not a git checkout")
    found = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "--no-index", "-q", name], capture_output=True
    )
    assert found.returncode == 0, f"{name} is not ignored"
