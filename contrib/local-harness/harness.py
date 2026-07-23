#!/usr/bin/env python3
"""hippo-local-harness — direct vLLM engine for the `co-gem` hippo launcher.

Local-only deployment artifact (NOT part of the paulsha-hippo repo/wheel).
Design + rationale: https://github.com/hamanpaul/paulsha-hippo/issues/55

Contract (docs/backend-matrix.md, tier-3 co-gem):
    co-gem --model {MODEL} --effort {EFFORT} --headless --stdin
    - prompt on stdin, response on stdout (ONE canonical JSON value), exit 0 ok
    - zero-tool is STRUCTURAL: the request contains no tools field at all

Boundary (issue #55 但書):
    - hippo never manages model / entry point / key; this harness reuses the
      existing co-gem BYOK env file as the single source of truth:
      ~/.config/paulshaclaw/copilot-local-vllm.env
      (COPILOT_PROVIDER_BASE_URL / COPILOT_PROVIDER_API_KEY / COPILOT_MODEL)

Quality levers (方案 1/3 of issue #55):
    - guided decoding: response_format json_schema = hippo canonical schema v1
    - reasoning control mapped from {EFFORT}: low -> thinking off,
      medium -> reasoning_effort low, high -> model default
    - temperature 0 + fixed seed, bounded max_tokens, request timeout < hippo
      deadline_seconds (300)
    - one retry-with-repair round on canonical-validation failure; then fail
      closed (non-zero exit, empty stdout) so hippo parks the session
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = HARNESS_DIR / "schema-v1.json"
DEFAULT_ENV_FILE = "~/.config/paulshaclaw/copilot-local-vllm.env"
REQUEST_TIMEOUT_S = 270  # keep under hippo external_agents.deadline_seconds=300
SEED = 20260723

ARTIFACT_KINDS = {
    "research", "spec", "roadmap", "test", "task", "todo",
    "plan", "report", "review", "ship-record", "gate-report",
}
MAX_TOKENS = {"low": 4096, "medium": 6144, "high": 8192}


def die(code: int, message: str) -> "None":
    sys.stderr.write(f"hippo-local-harness: {message}\n")
    sys.exit(code)


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal sourceable-env parser: KEY=VALUE lines, optional leading
    whitespace / `export `, optional quotes, $NAME / ${NAME} expansion from
    earlier keys then os.environ. Never logs values."""
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value.startswith("${") and value.endswith("}"):
            ref = value[2:-1]
            value = env.get(ref, os.environ.get(ref, ""))
        elif value.startswith("$") and value[1:].isidentifier():
            ref = value[1:]
            value = env.get(ref, os.environ.get(ref, ""))
        env[key] = value
    return env


def parse_args(argv: list[str]) -> tuple[str, str]:
    model, effort = "", "low"
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--model":
            model = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        elif arg.startswith("--model="):
            model = arg[len("--model="):]
            i += 1
        elif arg == "--effort":
            effort = argv[i + 1] if i + 1 < len(argv) else "low"
            i += 2
        elif arg.startswith("--effort="):
            effort = arg[len("--effort="):]
            i += 1
        elif arg in ("--headless", "--stdin"):
            i += 1
        else:
            die(2, f"unknown argument: {arg}")
    return model, effort


def canonical_errors(data: object) -> list[str]:
    """Mirror hippo's HARD parse rules (llm_output.parse_response) so a repair
    round can run before hippo ever sees the response. Soft rules (project
    coercion, relation drops) are left to hippo."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["response must be a JSON object"]
    required = {"schema_version", "disposition", "reason", "findings"}
    unknown = sorted(set(data) - required)
    missing = sorted(required - set(data))
    if unknown:
        errs.append(f"unknown fields: {', '.join(unknown)}")
    if missing:
        errs.append(f"missing fields: {', '.join(missing)}")
        return errs
    if isinstance(data["schema_version"], bool) or data["schema_version"] != 1:
        errs.append("schema_version must be 1")
    disposition = data["disposition"]
    findings = data["findings"]
    reason = data["reason"]
    if disposition == "no_findings":
        if findings != []:
            errs.append("no_findings requires findings=[]")
        if not isinstance(reason, str) or not reason.strip():
            errs.append("no_findings requires a non-empty string reason")
    elif disposition == "findings":
        if reason is not None:
            errs.append("findings requires reason=null")
        if not isinstance(findings, list) or not findings:
            errs.append("findings requires a non-empty findings array")
        else:
            seen_titles: set[str] = set()
            for idx, item in enumerate(findings):
                if not isinstance(item, dict):
                    errs.append(f"finding {idx} is not an object")
                    continue
                title = item.get("title")
                if not isinstance(title, str) or not title.strip():
                    errs.append(f"finding {idx} needs a non-empty title")
                elif title in seen_titles:
                    errs.append(f"finding {idx} duplicate title: {title}")
                else:
                    seen_titles.add(title)
                if item.get("artifact_kind") not in ARTIFACT_KINDS:
                    errs.append(f"finding {idx} invalid artifact_kind")
                body = item.get("body")
                if not isinstance(body, str) or not body.strip():
                    errs.append(f"finding {idx} needs a non-empty body")
                sfi = item.get("source_fragment_indices")
                if (
                    not isinstance(sfi, list)
                    or not sfi
                    or any(isinstance(v, bool) or not isinstance(v, int) for v in sfi)
                ):
                    errs.append(f"finding {idx} needs non-empty int source_fragment_indices")
    else:
        errs.append("disposition must be findings or no_findings")
    return errs


def chat(base_url: str, api_key: str, payload: dict) -> str:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    choice = (body.get("choices") or [{}])[0]
    finish = choice.get("finish_reason")
    content = (choice.get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"empty content (finish_reason={finish})")
    if finish == "length":
        raise ValueError("response truncated at max_tokens")
    return content.strip()


ENUM_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": ["string", "null"]},
        "concepts": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "artifact_kind": {
                        "type": "string",
                        "enum": sorted(ARTIFACT_KINDS),
                    },
                    "fragment_indices": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0},
                        "minItems": 1,
                    },
                },
                "required": ["title", "artifact_kind", "fragment_indices"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reason", "concepts"],
    "additionalProperties": False,
}

ENUM_INSTRUCTION = """\
# Task: concept enumeration (pass 1 of 2)

List the DISTINCT reusable concepts in the session fragments below. One
concept = one topic/decision/procedure/spec/conclusion that can stand alone.
Do NOT merge unrelated topics. Typical content-rich sessions have 3-8.
If the session is only greetings, title-generation requests, metadata or
boilerplate, return {"reason": "<one sentence why>", "concepts": []}.
Otherwise return {"reason": null, "concepts": [...]} where each concept has a
short stable unique title, an artifact_kind, and the fragment indices that
support it.
"""

WRITE_INSTRUCTION = """\
# Task: write ONE knowledge slice (pass 2 of 2)

Using the session fragments below, write the single slice for this concept:
- title: {title}
- artifact_kind: {kind}
- source fragments: {indices}

Sibling slices in this batch (usable as relates_to target_title): {siblings}

Follow the field rules from the skill contract in the original prompt: body is
distilled standalone markdown (not a copy of fragments), tags are retrieval
keys, project comes from the known projects list (else "_unknown"), relations
only relates_to (exact sibling titles) or mentions (stable entity names).
HARD LIMIT: body must be at most 250 words — distill to the reusable core
(conclusions, exact commands/keys/values, ordering constraints); do NOT
enumerate every detail from the fragments.
Return ONLY the JSON object for this one slice.
"""


def guided(payload: dict, name: str, schema: dict) -> dict:
    out = dict(payload)
    out["response_format"] = {
        "type": "json_schema",
        "json_schema": {"name": name, "schema": schema},
    }
    return out


def request_json(base_url: str, api_key: str, payload: dict, validate, what: str):
    """One guided call + one repair round; returns parsed object or dies."""
    content = None
    for attempt in (1, 2):
        try:
            if content is None:
                content = chat(base_url, api_key, payload)
            data = json.loads(content)
            errs = validate(data)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            die(4, f"{what}: vLLM HTTP {exc.code}: {detail}")
        except json.JSONDecodeError as exc:
            errs = [f"invalid JSON: {exc}"]
        except Exception as exc:  # noqa: BLE001
            die(4, f"{what}: vLLM request failed: {exc}")
        if not errs:
            return data
        if attempt == 2:
            die(5, f"{what}: validation failed after repair: {'; '.join(errs[:4])}")
        sys.stderr.write(f"hippo-local-harness: {what}: repair round: {'; '.join(errs[:4])}\n")
        repair = dict(payload)
        repair["messages"] = [
            {
                "role": "user",
                "content": (
                    "Your previous reply failed validation. Errors:\n- "
                    + "\n- ".join(errs[:8])
                    + "\n\nPrevious reply:\n"
                    + (content or "")[:20000]
                    + "\n\nReturn ONLY the corrected JSON, nothing else."
                ),
            }
        ]
        payload = repair
        content = None
    return None  # unreachable


def finding_errors(item: object) -> list[str]:
    """Validate one finding object (pass-2 output) against hippo hard rules."""
    wrapper = {
        "schema_version": 1,
        "disposition": "findings",
        "reason": None,
        "findings": [item],
    }
    return canonical_errors(wrapper)


def main() -> None:
    model_arg, effort = parse_args(sys.argv[1:])

    env_path = Path(
        os.environ.get("HIPPO_LOCAL_HARNESS_ENV_FILE")
        or os.environ.get("PSC_COPILOT_LOCAL_VLLM_ENV_FILE")
        or DEFAULT_ENV_FILE
    ).expanduser()
    if not env_path.is_file():
        die(1, f"env file not readable: {env_path}")
    env = parse_env_file(env_path)
    base_url = env.get("COPILOT_PROVIDER_BASE_URL", "")
    api_key = env.get("COPILOT_PROVIDER_API_KEY", "")
    env_model = env.get("COPILOT_MODEL", "")
    if not base_url or not api_key:
        die(1, "COPILOT_PROVIDER_BASE_URL / COPILOT_PROVIDER_API_KEY missing in env file")

    # "local"/"default" are hippo profile sentinels -> use the env file model
    model = env_model if model_arg in ("", "local", "default") else model_arg
    if not model:
        die(1, "no model resolved (COPILOT_MODEL missing and no --model given)")

    prompt = sys.stdin.read()
    if not prompt.strip():
        die(2, "empty prompt on stdin")

    base_payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "seed": SEED,
        "max_tokens": MAX_TOKENS.get(effort, MAX_TOKENS["low"]),
    }
    # {EFFORT} -> reasoning control (issue #55 方案 1); medium/high thinking is
    # unbounded on this model (eats max_tokens whole), so thinking stays off
    # for guided passes and only plain tasks honor it.
    thinking_off = {"chat_template_kwargs": {"enable_thinking": False}}

    # Task sniffing: hippo atomization prompts carry the canonical schema-1
    # contract; title/skillopt tasks do not and must NOT be schema-forced.
    is_atomization = '"disposition"' in prompt or '"no_findings"' in prompt
    if not is_atomization:
        plain = dict(base_payload)
        if effort == "low":
            plain.update(thinking_off)
        elif effort == "medium":
            plain["reasoning_effort"] = "low"
        try:
            sys.stdout.write(chat(base_url, api_key, plain))
        except Exception as exc:  # noqa: BLE001
            die(4, f"plain task failed: {exc}")
        return

    # ---- atomization: map-reduce (issue #55 方案 1b) ----------------------
    # Pass 1: concept enumeration (structural decomposition forcing).
    enum_payload = dict(base_payload)
    enum_payload.update(thinking_off)
    enum_payload["messages"] = [
        {"role": "user", "content": ENUM_INSTRUCTION + "\n\n" + prompt}
    ]

    def enum_errors(data: object) -> list[str]:
        if not isinstance(data, dict) or "concepts" not in data:
            return ["response must be an object with concepts"]
        concepts = data["concepts"]
        if not isinstance(concepts, list):
            return ["concepts must be a list"]
        if not concepts:
            reason = data.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                return ["empty concepts requires a non-empty reason"]
        titles = [c.get("title") for c in concepts if isinstance(c, dict)]
        if len(titles) != len(set(titles)):
            return ["concept titles must be unique"]
        return []

    enum = request_json(
        base_url, api_key, guided(enum_payload, "hippo_enum", ENUM_SCHEMA),
        enum_errors, "enumerate",
    )
    concepts = enum["concepts"]

    if not concepts:
        out = {
            "schema_version": 1,
            "disposition": "no_findings",
            "reason": enum["reason"].strip(),
            "findings": [],
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        return

    # Pass 2: one focused write per concept, sibling titles provided so
    # relates_to edges stay resolvable inside the batch.
    finding_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    finding_schema = finding_schema["properties"]["findings"]["items"]
    all_titles = [c["title"] for c in concepts]
    findings: list[dict] = []
    seen: set[str] = set()
    for concept in concepts:
        siblings = [t for t in all_titles if t != concept["title"]] or ["(none)"]
        write_payload = dict(base_payload)
        write_payload.update(thinking_off)
        write_payload["messages"] = [
            {
                "role": "user",
                "content": WRITE_INSTRUCTION.format(
                    title=concept["title"],
                    kind=concept["artifact_kind"],
                    indices=concept["fragment_indices"],
                    siblings=", ".join(siblings),
                )
                + "\n\n"
                + prompt,
            }
        ]
        item = request_json(
            base_url, api_key,
            guided(write_payload, "hippo_finding", finding_schema),
            finding_errors, f"write[{concept['title'][:30]}]",
        )
        if item["title"] in seen:
            sys.stderr.write(
                f"hippo-local-harness: dropped duplicate title {item['title']!r}\n"
            )
            continue
        seen.add(item["title"])
        findings.append(item)

    out = {
        "schema_version": 1,
        "disposition": "findings",
        "reason": None,
        "findings": findings,
    }
    errs = canonical_errors(out)
    if errs:
        die(5, f"assembled response failed validation: {'; '.join(errs[:4])}")
    sys.stdout.write(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
