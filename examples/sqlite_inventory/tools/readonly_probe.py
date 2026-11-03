#!/usr/bin/env python3
"""Verify that a read-only connection rejects a write."""

import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1]).resolve()
    # cache=private allows a read-only connection to inspect an isolated WAL
    # database without requiring write access to the shared-memory file.
    connection = sqlite3.connect(f"file:{path}?mode=ro&cache=private&immutable=1", uri=True)
    before = connection.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    rejected = False
    error = ""
    try:
        connection.execute("INSERT INTO categories(id, name) VALUES (999, 'forbidden')")
        connection.commit()
    except sqlite3.Error as exc:
        rejected = True
        error = str(exc)
    finally:
        connection.close()
    verifier = sqlite3.connect(f"file:{path}?mode=ro&cache=private&immutable=1", uri=True)
    try:
        after = verifier.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    finally:
        verifier.close()
    print(
        json.dumps(
            {
                "passed": rejected and before == after,
                "error": error,
                "measurements": [
                    {"name": "category_count_before", "value": before, "unit": "rows"},
                    {"name": "category_count_after", "value": after, "unit": "rows"},
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
