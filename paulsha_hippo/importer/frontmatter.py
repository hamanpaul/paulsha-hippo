"""Deterministic Stage 2 frontmatter and Markdown rendering."""

from __future__ import annotations

from .adapters.base import NormalizedSession


_BUCKET_TO_ARTIFACT = {
    "sessions": "session",
    "session": "session",
    "plans": "plan",
    "plan": "plan",
    "research": "research",
    "reports": "report",
    "report": "report",
}


def _value(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ")


def _required_value(value: object, default: str = "_unknown") -> str:
    rendered = _value(value)
    return rendered if rendered else default


# YAML plain scalars may not start with an indicator char (flow markers, block
# markers, etc.); a leading '[' or '{' otherwise opens an unterminated flow
# collection. See #139.
_YAML_INDICATOR_STARTS = ("[", "]", "{", "}", ",", "&", "*", "!", "|", ">", "%", "@", "`", "#", "\"", "'")


def _needs_yaml_quotes(value: str) -> bool:
    if value != value.strip():
        return True
    if value[:1] in _YAML_INDICATOR_STARTS:
        return True
    if value[:2] in ("- ", "? ", ": ") or value in ("-", "?", ":"):
        return True
    # A colon followed by whitespace, a flow delimiter, or end-of-scalar is a
    # YAML mapping indicator.  The end-of-scalar case matters for fallback
    # titles such as ``安裝 skill https:``; leaving it plain makes the whole
    # inbox document unparsable on the next Dream pass.
    for index, character in enumerate(value):
        if character != ":":
            continue
        if index + 1 == len(value) or value[index + 1] in " \t[]{} ,":
            return True
    return any(marker in value for marker in (": ", "#", "\"", "'", "\n", "\r"))


def _frontmatter_value(value: object) -> str:
    if value is None:
        return ""
    rendered = str(value)
    if not rendered or not _needs_yaml_quotes(rendered):
        return rendered
    escaped = (
        rendered.replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _required_frontmatter_value(value: object, default: str = "_unknown") -> str:
    rendered = "" if value is None else str(value)
    return _frontmatter_value(rendered if rendered else default)


def _artifact_name(classifier_bucket: str | None) -> str:
    if not classifier_bucket:
        return "session"
    return _BUCKET_TO_ARTIFACT.get(classifier_bucket, classifier_bucket.rstrip("s") or "session")


def _bullet_list(items: list[str]) -> list[str]:
    if not items:
        return ["- (none)"]
    return [f"- {item}" for item in items]


def _prompt_list(items: list[str]) -> list[str]:
    if not items:
        return ["- (none)"]
    return [f"{index}. {item}" for index, item in enumerate(items, start=1)]


def _conversation_list(items: list[str]) -> list[str]:
    if not items:
        return ["- (none)"]
    return [f"{index}. {item}" for index, item in enumerate(items, start=1)]


def render_markdown(
    session: NormalizedSession,
    *,
    project: str = "_unknown",
    classifier_bucket: str | None = "session",
    captured_at: str | None = None,
    memory_layer: str = "inbox",
    provenance_repo: str | None = None,
) -> str:
    source_artifact = _artifact_name(classifier_bucket)
    captured = captured_at or session.get("ended_at") or session.get("started_at") or "_unknown"
    repo_value = session.get("repo") if provenance_repo is None else provenance_repo
    lines = [
        "---",
        f"memory_layer: {_frontmatter_value(memory_layer)}",
        f"project: {_frontmatter_value(project or '_unknown')}",
        f"source_agent: {_frontmatter_value(session.get('tool'))}",
        f"source_session: {_frontmatter_value(session.get('session_id'))}",
        f"source_artifact: {_frontmatter_value(source_artifact)}",
        f"title: {_frontmatter_value(session.get('session_title'))}",
        f"title_source: {_frontmatter_value(session.get('title_source') or 'fallback')}",
        f"captured_at: {_frontmatter_value(captured)}",
        "provenance:",
        f"  repo: {_required_frontmatter_value(repo_value)}",
        f"  commit: {_required_frontmatter_value(session.get('commit'))}",
        f"  path: {_required_frontmatter_value(session.get('raw_payload_pointer'))}",
        "---",
        "",
        f"# Session {_value(session.get('session_id'))}",
        "",
        "## Summary",
        _value(session.get("assistant_summary")) or "(none)",
        "",
        "## Conversation",
        *_conversation_list(session.get("assistant_messages", [])),
        "",
        "## Source",
        f"- Tool: {_value(session.get('tool'))}",
        f"- Session: {_value(session.get('session_id'))}",
        f"- Raw payload: {_value(session.get('raw_payload_pointer'))}",
        "",
        "## CWD",
        _value(session.get("cwd")) or "(unknown)",
        "",
        "## Touched files",
        *_bullet_list(session.get("touched_files", [])),
        "",
        "## Referenced artifacts",
        *_bullet_list(session.get("referenced_artifacts", [])),
        "",
        "## Prompts",
        *_prompt_list(session.get("user_prompts", [])),
        "",
    ]
    if "atomization_replay" in session:
        replay = "true" if session.get("atomization_replay") is True else "false"
        lines.insert(9, f"atomization_replay: {replay}")
    return "\n".join(lines)
