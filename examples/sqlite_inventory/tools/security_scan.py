#!/usr/bin/env python3
"""Small normalized-tool contract example used by the reference Gold."""

import json
import re
import sys
import time
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    started = time.perf_counter()
    findings = []
    patterns = [
        ("hardcoded-db-password", re.compile(r"(?i)(db_)?password\s*=\s*['\"][^'\"]+['\"]")),
        ("credentialed-database-url", re.compile(r"(?i)(postgres|mysql)://[^/\s:]+:[^@\s]+@")),
        ("sql-string-concatenation", re.compile(r"(?i)(execute|executemany)\s*\(\s*['\"][^'\"]+['\"]\s*\+")),
    ]
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() not in {".py", ".js", ".ts", ".tsx", ".sql", ".json", ".txt", ".md"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            for rule_id, pattern in patterns:
                if pattern.search(line):
                    findings.append(
                        {
                            "id": rule_id,
                            "severity": "high" if "password" in rule_id else "medium",
                            "title": rule_id.replace("-", " "),
                            "evidence": line.strip()[:300],
                            "location": f"{path.relative_to(root)}:{line_number}",
                        }
                    )
    print(
        json.dumps(
            {
                "tool": "dbeval-reference-security-scan",
                "findings": findings,
                "measurements": [
                    {
                        "name": "security_scan_seconds",
                        "value": time.perf_counter() - started,
                        "unit": "s",
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
