"""Run every conversion step on a database, in order, stopping at the first
that does not pass.

    python convert_all.py <path to database>

The steps are conversion.CHAIN. Each runs through the conversion runner, so
each writes its own dated backup first and runs in one transaction; a step
already applied is reported and skipped, so running this again after a stop
picks up where it stopped. When a step fails, no later step runs, and the
last lines name the step it stopped at and the steps not run.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import conversion


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_all.py <path to database>")
        return 2
    path = Path(argv[0]).resolve()
    if not path.is_file():
        print(f"no database at {path}")
        return 1

    total = len(conversion.CHAIN)
    for n, (name, module_name) in enumerate(conversion.CHAIN, start=1):
        print(f"--- step {n} of {total}: {name} (python {module_name}.py)")
        try:
            code = importlib.import_module(module_name).main([str(path)])
        except Exception as exc:
            # The type only: an exception's text can carry row contents.
            print(f"{type(exc).__name__} raised")
            code = 1
        if code != 0:
            not_run = [later for later, _ in conversion.CHAIN[n:]]
            print(f"stopped at step {n} of {total}: {name}. Nothing after it was run.")
            if not_run:
                print(f"not run: {', '.join(not_run)}")
            print("Fix what the step reported and run this command again; steps already "
                  "applied are skipped.")
            return 1
    print(f"all {total} steps applied")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
