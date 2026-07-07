from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import Iterable, Sequence

CONSUMER_MARKER = "# memory" "-consumer"
MEMORY_PATH_MARKERS = ("inbox" "/", "work-centric" "/", "knowledge" "/", "runtime" "/index")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="policy_consumer_lint")
    parser.add_argument("paths", nargs="+", help="Files or directories to scan")
    args = parser.parse_args(argv)

    violations = find_violations(args.paths)
    for violation in violations:
        print(violation)
    return 1 if violations else 0


def find_violations(paths: Iterable[str | Path]) -> tuple[str, ...]:
    violations: list[str] = []
    for path in paths:
        for candidate in _python_files(Path(path)):
            if _ignored(candidate):
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                violations.append(f"{candidate}: unable to decode as UTF-8; treat as policy violation")
                continue
            except OSError:
                violations.append(f"{candidate}: unable to read file; treat as policy violation")
                continue
            if _is_memory_consumer(text) and not _has_boundary_call(text):
                violations.append(f"{candidate}: memory consumer must call policy.check_boundary(")
    return tuple(violations)


def _python_files(path: Path) -> tuple[Path, ...]:
    if path.is_file():
        return (path,) if path.suffix == ".py" else ()
    if not path.exists():
        return ()
    return tuple(sorted(candidate for candidate in path.rglob("*.py") if candidate.is_file()))


def _ignored(path: Path) -> bool:
    parts = path.parts
    if "tests" in parts or path.name.startswith("test_"):
        return True

    for index in range(len(parts) - 2):
        if parts[index:index + 2] == ("paulsha_hippo", "policy"):
            return True
        if parts[index:index + 2] == ("paulsha_hippo", "ledger"):
            return True

    return False


def _is_memory_consumer(text: str) -> bool:
    return CONSUMER_MARKER in text or any(marker in text for marker in MEMORY_PATH_MARKERS)


def _has_boundary_call(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    policy_aliases: set[str] = set()
    memory_aliases: set[str] = set()
    direct_function_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.rsplit(".", 1)[-1]
                if alias.name == "paulsha_hippo.policy":
                    policy_aliases.add(local_name)
                elif alias.name == "paulsha_hippo":
                    memory_aliases.add(local_name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            for alias in node.names:
                local_name = alias.asname or alias.name
                if module == "paulsha_hippo" and alias.name == "policy":
                    policy_aliases.add(local_name)
                elif module == "paulsha_hippo.policy" and alias.name == "check_boundary":
                    direct_function_aliases.add(local_name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in direct_function_aliases:
            return True
        if not isinstance(func, ast.Attribute) or func.attr != "check_boundary":
            continue
        if isinstance(func.value, ast.Name) and func.value.id in policy_aliases:
            return True
        if (
            isinstance(func.value, ast.Attribute)
            and func.value.attr == "policy"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id in memory_aliases
        ):
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
