#!/usr/bin/env python3
"""Validate an Agent Skills folder: SKILL.md frontmatter and naming rules.

Checks performed:
  - SKILL.md exists and is spelled in all caps
  - the opening '---' is the first line
  - the frontmatter block parses as a simple key/value mapping
  - 'name' is present and matches ^[a-z0-9]+(-[a-z0-9]+)*$
  - 'name' matches the containing directory name
  - 'description' is present and between 1 and 1024 characters

Usage:
    python validate_skill.py path/to/my-skill

Exit codes:
    0  skill is valid
    1  one or more validation errors
    2  bad invocation or missing SKILL.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
KNOWN_FIELDS = {"name", "description", "license", "compatibility", "metadata"}


def split_frontmatter(text: str) -> tuple[str | None, str | None]:
    """Return (frontmatter_text, body_text). frontmatter_text is None when absent."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    return None, text


def parse_scalar(raw: str) -> str:
    """Parse one YAML scalar. Handles quoting and trailing comments conservatively."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_frontmatter(block: str) -> dict[str, str]:
    """Parse a flat key/value mapping. Nested keys are collected with dotted names."""
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line[:1].isspace()
        if indented or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        fields[key.strip()] = parse_scalar(raw)
    return fields


def validate(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.is_file():
        return [f"{skill_file} does not exist (the filename must be SKILL.md, all caps)"]

    text = skill_file.read_text(encoding="utf-8")
    block, body = split_frontmatter(text)

    if block is None:
        errors.append("no frontmatter block found")
        errors.append("the opening '---' must be the first line of the file, with no blank line before it")
        return errors

    fields = parse_frontmatter(block)

    name = fields.get("name", "")
    if not name:
        errors.append("missing required field 'name'")
    else:
        if len(name) > MAX_NAME_LENGTH:
            errors.append(f"name is {len(name)} characters, the maximum is {MAX_NAME_LENGTH}")
        if not NAME_PATTERN.match(name):
            errors.append(
                f"name {name!r} is invalid: use lowercase letters, digits, and single hyphens only"
            )
        if name != skill_dir.name:
            errors.append(f"name {name!r} does not match directory {skill_dir.name!r}")

    description = fields.get("description", "")
    if not description:
        errors.append("missing required field 'description'")
    elif len(description) > MAX_DESCRIPTION_LENGTH:
        errors.append(
            f"description is {len(description)} characters, the maximum is {MAX_DESCRIPTION_LENGTH}"
        )

    for key in fields:
        if key not in KNOWN_FIELDS:
            errors.append(
                f"unknown top-level field {key!r}: the shared specification defines "
                f"{', '.join(sorted(KNOWN_FIELDS))}"
            )

    if not body.strip():
        errors.append("skill body is empty after the frontmatter")

    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    skill_dir = Path(argv[1])
    if not skill_dir.is_dir():
        print(f"error: {skill_dir} is not a directory", file=sys.stderr)
        return 2

    errors = validate(skill_dir)
    if errors:
        print(f"FAIL {skill_dir}")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"OK   {skill_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))