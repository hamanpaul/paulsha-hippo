from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path

from . import schema


@dataclass(frozen=True)
class GateCheckResult:
    ok: bool
    errors: list[str]
    frontmatter: dict[str, object]

    def to_json(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "frontmatter": self.frontmatter,
        }


def run_static_gate_check_text(text: str) -> GateCheckResult:
    try:
        artifact = schema.parse_artifact_text(text)
    except ValueError as exc:
        return GateCheckResult(
            ok=False,
            errors=[str(exc)],
            frontmatter={},
        )

    validation = schema.validate_frontmatter(
        frontmatter=artifact.frontmatter,
        body=artifact.body,
    )
    return GateCheckResult(
        ok=validation.ok,
        errors=list(validation.errors),
        frontmatter=artifact.frontmatter,
    )


def run_static_gate_check_file(path: Path | str) -> GateCheckResult:
    content = Path(path).read_text(encoding="utf-8")
    return run_static_gate_check_text(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage3 static lifecycle gate checker")
    parser.add_argument("--artifact", required=True, help="artifact file path")
    args = parser.parse_args(argv)

    result = run_static_gate_check_file(args.artifact)
    print(json.dumps(result.to_json(), ensure_ascii=False))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
