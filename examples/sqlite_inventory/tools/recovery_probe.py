#!/usr/bin/env python3
"""Simulate an abrupt connection termination and verify rollback on reopen."""

import json
import sqlite3
import sys
import time
from pathlib import Path


def scalar(connection, sql):
    return connection.execute(sql).fetchone()[0]


def main() -> int:
    path = Path(sys.argv[1])
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    started = time.perf_counter()
    before = None
    after = None
    first = sqlite3.connect(str(path), timeout=timeout)
    try:
        before = scalar(first, "SELECT stock_units FROM products WHERE id = 1")
        first.execute("BEGIN IMMEDIATE")
        first.execute("UPDATE products SET stock_units = stock_units + 1000 WHERE id = 1")
        # Closing without commit represents an abrupt data-access lifecycle.
    finally:
        first.close()
    reopened = sqlite3.connect(str(path), timeout=timeout)
    try:
        after = scalar(reopened, "SELECT stock_units FROM products WHERE id = 1")
        integrity = scalar(reopened, "PRAGMA integrity_check")
    finally:
        reopened.close()
    elapsed = time.perf_counter() - started
    passed = before == after and integrity == "ok"
    print(
        json.dumps(
            {
                "passed": passed,
                "measurements": [
                    {
                        "name": "recovery_seconds",
                        "value": elapsed,
                        "unit": "s",
                    },
                    {
                        "name": "rpo_uncommitted_rows",
                        "value": 0 if before == after else 1,
                        "unit": "rows",
                    },
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
