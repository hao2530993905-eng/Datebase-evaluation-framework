#!/usr/bin/env python3
"""Exercise lock detection and bounded retry on independent connections."""

import json
import sqlite3
import sys
import time


def main() -> int:
    path = sys.argv[1]
    holder = sqlite3.connect(path, timeout=0.1)
    contender = sqlite3.connect(path, timeout=0.05)
    locked = False
    retried = False
    started = time.perf_counter()
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("UPDATE products SET stock_units = stock_units + 1 WHERE id = 1")
        try:
            contender.execute("UPDATE products SET stock_units = stock_units + 1 WHERE id = 1")
        except sqlite3.OperationalError as exc:
            locked = "locked" in str(exc).lower()
        holder.rollback()
        for _ in range(3):
            try:
                contender.execute("UPDATE products SET stock_units = stock_units + 1 WHERE id = 1")
                contender.commit()
                retried = True
                break
            except sqlite3.OperationalError:
                time.sleep(0.02)
    finally:
        holder.close()
        contender.close()
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "passed": locked and retried,
                "measurements": [
                    {"name": "lock_retry_seconds", "value": elapsed, "unit": "s"}
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
