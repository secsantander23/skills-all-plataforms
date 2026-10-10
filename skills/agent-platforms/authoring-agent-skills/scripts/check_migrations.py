#!/usr/bin/env python3
"""Print migration files containing statements that need a lock-level review.

This is the example script referenced by the db-migration-review skill body.
Adapt the RISKY pattern to your database and the findings format to your review flow.

Usage:
    python check_migrations.py [root]     # root defaults to ./migrations

Exit codes:
    0  no risky statements found
    1  at least one statement needs review
    2  bad invocation, or the root directory does not exist
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RISKY = re.compile(
    r"\b(CREATE\s+INDEX|ALTER\s+TABLE|DROP\s+(TABLE|COLUMN|INDEX))\b",
    re.IGNORECASE,
)


def scan(root: Path) -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for path in sorted(root.rglob("*.sql")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = RISKY.search(line)
            if match:
                findings.append((path, lineno, " ".join(match.group(0).split()).upper()))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="migrations", type=Path)
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2

    findings = scan(args.root)
    for path, lineno, statement in findings:
        print(f"{path}:{lineno}: review {statement}")

    if findings:
        print(f"{len(findings)} statement(s) need lock review")
        return 1

    print("no risky statements found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())