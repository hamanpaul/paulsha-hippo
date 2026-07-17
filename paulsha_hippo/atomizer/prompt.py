from __future__ import annotations

from .splitter import Fragment

_CANONICAL_RESPONSE_SCHEMA = (
    '{"schema_version":1,"disposition":"findings|no_findings",'
    '"reason":null|string,"findings":[...]}'
)


def build_prompt(skill_text: str, fragments: list[Fragment], known_projects: list[str]) -> str:
    parts = [
        skill_text,
        "",
        "## Known projects (choose exactly one per slice, or _unknown)",
        ", ".join(known_projects) if known_projects else "_unknown",
        "",
    ]
    # The importer already resolved this session's project; surface it as a hint so
    # the model attributes to it by default instead of guessing from content. Only
    # when it is a known project (else leave the model to pick from the list).
    session_project = fragments[0].project if fragments else ""
    if session_project and session_project in known_projects:
        parts.append("## This session's project")
        parts.append(
            f"This session was captured in project: {session_project}. "
            "Use this exact project for every finding; do not re-home the source session."
        )
        parts.append("")
    parts.append("## Session fragments to atomize")
    for fragment in fragments:
        label = f"fragment {fragment.fragment_index}"
        if fragment.part_count > 1:
            label += f" part {fragment.part_index}/{fragment.part_count}"
        parts.append(f"[{label}]")
        parts.append(fragment.body)
        parts.append("")
    parts.append("## Output")
    parts.append(f"Return ONLY this canonical JSON object shape: {_CANONICAL_RESPONSE_SCHEMA}")
    parts.append("Use disposition=findings with one or more findings and reason=null.")
    parts.append("Use disposition=no_findings only with findings=[] and a non-empty reason.")
    parts.append("The first character must be `{` and the last character must be `}`.")
    parts.append("Do NOT create files, write files, save files, or claim that you updated any file or index.")
    parts.append("Do NOT return prose, narration, summaries, markdown fences, or any text before or after the JSON object.")
    return "\n".join(parts)
