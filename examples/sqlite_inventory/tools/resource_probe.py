#!/usr/bin/env python3
"""Measure deterministic scale growth and process resource use."""

import json
import resource
import sqlite3
import sys
import time


def run_query(connection, rows):
    sql = (
        "WITH RECURSIVE seq(x) AS ("
        "SELECT 1 UNION ALL SELECT x + 1 FROM seq WHERE x < :rows"
        ") SELECT SUM(x) FROM seq"
    )
    started = time.perf_counter()
    connection.execute(sql, {"rows": rows}).fetchone()
    return (time.perf_counter() - started) * 1000.0


def main() -> int:
    path = sys.argv[1]
    connection = sqlite3.connect(path)
    before_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    small = run_query(connection, 1000)
    large = run_query(connection, 150000)
    after_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    connection.close()
    growth = large / max(small, 0.001)
    # macOS reports ru_maxrss in bytes; Linux reports it in KiB.
    rss_delta = max(0, after_rss - before_rss)
    rss_delta_kb = rss_delta / 1024.0 if sys.platform == "darwin" else rss_delta
    passed = growth <= 300.0 and rss_delta_kb <= 100000
    print(
        json.dumps(
            {
                "passed": passed,
                "measurements": [
                    {
                        "name": "scale_growth_ratio",
                        "value": growth,
                        "unit": "ratio",
                    },
                    {
                        "name": "rss_delta_kb",
                        "value": rss_delta_kb,
                        "unit": "KiB",
                    },
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
