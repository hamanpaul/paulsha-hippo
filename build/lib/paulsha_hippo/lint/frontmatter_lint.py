from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = (
    "memory_layer",
    "project",
    "source_agent",
    "source_session",
    "source_artifact",
    "captured_at",
    "provenance.repo",
    "provenance.commit",
    "provenance.path",
)


def validate_file(path: str | Path) -> list[str]:
    """Return frontmatter validation errors for a markdown file."""
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read file: {exc}"]

    frontmatter, errors = _extract_frontmatter(text)
    if errors:
        return errors

    data = _parse_frontmatter(frontmatter)
    for field in REQUIRED_FIELDS:
        has_field, value = _get_field(data, field)
        if not has_field:
            errors.append(f"missing required frontmatter field: {field}")
        elif not _is_non_empty_leaf(value):
            errors.append(f"empty required frontmatter field: {field}")
    return errors


def _extract_frontmatter(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], ["missing frontmatter block"]

    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:index], []
    return [], ["unterminated frontmatter block"]


def _parse_frontmatter(lines: list[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_parent: str | None = None

    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        if raw_line.startswith((" ", "\t")):
            if current_parent is None:
                continue
            key, value = _split_key_value(raw_line.strip())
            if key:
                parent = data.setdefault(current_parent, {})
                if isinstance(parent, dict):
                    parent[key] = value
            continue

        key, value = _split_key_value(raw_line.strip())
        if not key:
            current_parent = None
            continue
        if value == "":
            data[key] = {}
            current_parent = key
        else:
            data[key] = value
            current_parent = None

    return data


def _split_key_value(line: str) -> tuple[str, str]:
    if ":" not in line:
        return "", ""
    key, value = line.split(":", 1)
    return key.strip(), value.strip().strip('"').strip("'")


def _get_field(data: dict[str, Any], dotted_field: str) -> tuple[bool, Any]:
    current: Any = data
    for part in dotted_field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _is_non_empty_leaf(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Stage 2 memory frontmatter")
    parser.add_argument("paths", nargs="+", help="Markdown files to validate")
    args = parser.parse_args(argv)

    failed = False
    for file_name in args.paths:
        errors = validate_file(file_name)
        for error in errors:
            failed = True
            print(f"{file_name}: {error}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
