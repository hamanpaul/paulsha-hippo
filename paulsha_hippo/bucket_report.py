"""既有 knowledge bucket 合併的 dry-run impact report（#117）。

worktree 與主 repo 解析成不同 project 後，同一 repo 的 note 被拆進多個 knowledge
bucket（raw remote 形 `github.com-<owner>-<repo>--p-<hash>` 與 registered slug）。修好
resolver 只讓新資料進對桶；既有 note 的合併屬 P2（#148 分級），本模組只做**唯讀**
impact report：

- 以 union registry（預設疊加 `hippo registry backfill-remotes` 的補登計畫）重新推導每筆
  knowledge note 的目標 slug，彙總「哪個 bucket 會併到哪個 slug、各幾筆」。
- 目標 slug 為 path-safe 短 slug、且該 project 的 note 全數去向一致時，附上既有
  `hippo knowledge rekey`（先 `--dry-run`）的可執行指令；其餘標明不可直接執行的理由。
- frontmatter `project` 已是目標 slug、檔案卻不在該 slug 的 bucket 目錄（現行
  `project_directory_key` 或 legacy sanitize 形）者列為 relocation——rekey 以 frontmatter
  選取，處理不了純目錄錯置。
- `_unknown` note 依證據分類成因，並估算可回收比例。

本模組不寫任何檔案、不動 ledger／index；只讀 knowledge frontmatter、import ledger 與
memory root 內的 archive payload（provenance.path 不在 memory root 內者不讀）。
"""

from __future__ import annotations

import json
import shlex
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomizer.config import is_safe_path_component, project_directory_key, sanitize_project_component
from .importer.config import ProjectsConfig
from .importer.project_resolver import UNKNOWN_PROJECT, is_ephemeral_path, normalize_remote
from .moc import frontmatter_io as _fio

KIND_REGISTERED = "registered"
KIND_RAW_REMOTE = "raw-remote"
KIND_UNKNOWN = "unknown"
KIND_DIRNAME = "dirname-fallback"

CAUSE_ATOMIZER_COERCED = "atomizer-coerced"
CAUSE_PROVENANCE_ONLY = "provenance-only"
CAUSE_EPHEMERAL = "ephemeral-cwd"
CAUSE_NO_REMOTE = "no-remote-evidence"
CAUSE_NO_CWD = "no-cwd"
CAUSE_PAYLOAD_MISSING = "payload-missing"
CAUSE_AMBIGUOUS_REMOTE = "ambiguous-remote"

UNKNOWN_CAUSE_NOTES = {
    CAUSE_ATOMIZER_COERCED: "session 在 import ledger 已解析為具體 project，note 卻在蒸餾端被強制為 _unknown（可回收）",
    CAUSE_PROVENANCE_ONLY: "note 無 session 歸屬、但 provenance.repo 記有 remote（可回收）",
    CAUSE_EPHEMERAL: "session cwd 位於暫存目錄（sandbox／canary），無穩定身分（不可回收）",
    CAUSE_NO_REMOTE: "session cwd 不在 repo 或無 remote，也無 provenance（不可回收）",
    CAUSE_NO_CWD: "archive payload 未帶 cwd（不可回收）",
    CAUSE_PAYLOAD_MISSING: "provenance.path 的 archive payload 已不存在或不可讀（不可回收）",
    CAUSE_AMBIGUOUS_REMOTE: "remote 同時被多個 slug 認領，無法唯一決定目標（需先修 registry）",
}


def _looks_like_remote(value: str) -> bool:
    if not value or "/" not in value:
        return False
    return normalize_remote(value) == value and "." in value.split("/", 1)[0]


def _session_key_from_ledger(record: dict[str, Any]) -> str:
    key = record.get("logical_session_key")
    if isinstance(key, str) and key:
        return key
    idempotency = record.get("idempotency_key")
    if isinstance(idempotency, str) and idempotency:
        return ":".join(idempotency.split(":")[:2])
    return ""


def _load_ledger_projects(memory_root: Path) -> dict[str, str]:
    ledger = memory_root / "runtime" / "ledger" / "import.jsonl"
    projects: dict[str, str] = {}
    try:
        handle = ledger.open(encoding="utf-8", errors="replace")
    except OSError:
        return projects
    with handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # torn line：略過，不影響唯讀報告
            if not isinstance(record, dict):
                continue
            key = _session_key_from_ledger(record)
            project = record.get("project")
            if key and isinstance(project, str):
                projects[key] = project
    return projects


class _View:
    """補登後（或現況）的 union registry 視圖：slug 集合與 remote → slug 對應。"""

    def __init__(self, projects: ProjectsConfig):
        self.registered = {project.slug for project in projects.projects}
        owners: dict[str, set[str]] = defaultdict(set)
        for project in projects.projects:
            for remote in project.remotes:
                normalized = normalize_remote(remote)
                if normalized:
                    owners[normalized].add(project.slug)
        self.owners = owners

    def map_remote(self, remote: str) -> str | None:
        """remote → registered slug；未登記回 raw remote 形；多 slug 認領回 None。"""
        slugs = self.owners.get(remote, set())
        if len(slugs) > 1:
            return None
        return next(iter(slugs)) if slugs else remote

    def canonical(self, value: str) -> str | None:
        if value in self.registered:
            return value
        if _looks_like_remote(value):
            return self.map_remote(value)
        return value

    def kind(self, project: str) -> str:
        if project in self.registered:
            return KIND_REGISTERED
        if project == UNKNOWN_PROJECT:
            return KIND_UNKNOWN
        if _looks_like_remote(project):
            return KIND_RAW_REMOTE
        return KIND_DIRNAME


def _load_payload(memory_root: Path, provenance: dict[str, Any]) -> dict[str, Any] | None:
    raw = provenance.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        path = Path(raw).resolve()
        path.relative_to(memory_root.resolve())
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _classify_without_evidence(memory_root: Path, provenance: dict[str, Any]) -> str:
    payload = _load_payload(memory_root, provenance)
    if payload is None:
        return CAUSE_PAYLOAD_MISSING
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return CAUSE_NO_CWD
    if is_ephemeral_path(cwd):
        return CAUSE_EPHEMERAL
    return CAUSE_NO_REMOTE


def _note_session_key(frontmatter: dict[str, Any]) -> str:
    distilled = frontmatter.get("distilled_from")
    if isinstance(distilled, str) and distilled:
        return distilled
    agent = frontmatter.get("source_agent") or frontmatter.get("created_by")
    session = frontmatter.get("source_session")
    if agent and session:
        return f"{agent}:{session}"
    return ""


def _evaluate(
    frontmatter: dict[str, Any],
    *,
    view: _View,
    ledger: dict[str, str],
    memory_root: Path,
) -> dict[str, str | None]:
    """推導單筆 note 的目標 slug。

    回傳 `target`（None＝無法解析）、`cause`（歸屬證據來源或無法解析的成因）、
    `mismatch`（provenance remote 對到的 slug 與目標不同時，供人工複核）。
    """
    project = str(frontmatter.get("project") or UNKNOWN_PROJECT)
    provenance = frontmatter.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    repo = str(provenance.get("repo") or "")
    prov_remote = normalize_remote(repo) if repo and repo != UNKNOWN_PROJECT else ""

    def by_provenance() -> dict[str, str | None]:
        target = view.map_remote(prov_remote)
        if target is None:
            return {"target": None, "cause": CAUSE_AMBIGUOUS_REMOTE, "mismatch": None}
        return {"target": target, "cause": CAUSE_PROVENANCE_ONLY, "mismatch": None}

    def unresolved() -> dict[str, str | None]:
        cause = _classify_without_evidence(memory_root, provenance)
        return {"target": None, "cause": cause, "mismatch": None}

    if project == UNKNOWN_PROJECT:
        session_project = ledger.get(_note_session_key(frontmatter), "")
        if session_project and session_project != UNKNOWN_PROJECT:
            target = view.canonical(session_project)
            if target and (target in view.registered or _looks_like_remote(target)):
                return {"target": target, "cause": CAUSE_ATOMIZER_COERCED, "mismatch": None}
        return by_provenance() if prov_remote else unresolved()

    kind = view.kind(project)
    if kind in (KIND_REGISTERED, KIND_RAW_REMOTE):
        target = project if kind == KIND_REGISTERED else view.map_remote(project)
        if target is None:
            return {"target": None, "cause": CAUSE_AMBIGUOUS_REMOTE, "mismatch": None}
        mapped = view.map_remote(prov_remote) if prov_remote else None
        return {
            "target": target,
            "cause": None,
            "mismatch": mapped if mapped and mapped != target else None,
        }

    # dirname fallback bucket：只有 provenance remote 可作為歸屬證據
    return by_provenance() if prov_remote else unresolved()


def _expected_dirs(project: str) -> set[str]:
    """project 的合法 bucket 目錄名：現行 project_directory_key 與 legacy sanitize 形
    （atomizer／rekey 讀取端兩者皆接受）。"""
    return {project_directory_key(project), sanitize_project_component(project)}


def _iter_bucket_notes(bucket: Path):
    for path in sorted(bucket.rglob("*.md")):
        if path.name.endswith("-moc.md"):
            continue
        try:
            frontmatter, _body = _fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            yield None
            continue
        yield frontmatter


def _proposal_command(memory_root: Path, from_project: str, to_slug: str) -> str:
    return " ".join(
        [
            "hippo knowledge rekey",
            "--memory-root",
            shlex.quote(str(memory_root)),
            "--from",
            shlex.quote(from_project),
            "--to",
            shlex.quote(to_slug),
            "--dry-run",
        ]
    )


def build_bucket_report(
    memory_root: str | Path,
    projects: ProjectsConfig,
    *,
    planned_remote_backfill: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(memory_root)
    view = _View(projects)
    ledger = _load_ledger_projects(root)
    knowledge = root / "knowledge"

    buckets: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    moves: dict[tuple[str, str], int] = Counter()
    relocations: dict[tuple[str, str], int] = Counter()
    totals_by_project: Counter[str] = Counter()
    unknown_causes: Counter[str] = Counter()
    unknown_total = 0
    unknown_recoverable = 0

    for bucket in sorted(path for path in knowledge.iterdir() if path.is_dir()) if knowledge.is_dir() else []:
        projects_seen: Counter[str] = Counter()
        move: Counter[str] = Counter()
        relocate: Counter[str] = Counter()
        unresolved: Counter[str] = Counter()
        mismatch: Counter[str] = Counter()
        stay = 0
        non_knowledge = 0
        unreadable = 0
        for frontmatter in _iter_bucket_notes(bucket):
            if frontmatter is None:
                unreadable += 1
                continue
            if frontmatter.get("memory_layer") != "knowledge":
                non_knowledge += 1
                continue
            project = str(frontmatter.get("project") or UNKNOWN_PROJECT)
            projects_seen[project] += 1
            totals_by_project[project] += 1
            result = _evaluate(frontmatter, view=view, ledger=ledger, memory_root=root)
            target = result["target"]
            if project == UNKNOWN_PROJECT:
                unknown_total += 1
                unknown_causes[result["cause"] or CAUSE_NO_REMOTE] += 1
                if target is not None:
                    unknown_recoverable += 1
            if result["mismatch"]:
                mismatch[result["mismatch"]] += 1
            if target is None:
                unresolved[result["cause"] or CAUSE_NO_REMOTE] += 1
            elif target == project:
                # frontmatter 已是目標 slug，仍須比對實際所在目錄：搬移中斷等情況下檔案
                # 可能留在舊 bucket，只看 frontmatter 會誤算成 stay（審查 #161-1）。
                if bucket.name in _expected_dirs(project):
                    stay += 1
                else:
                    relocate[target] += 1
                    relocations[(bucket.name, target)] += 1
            else:
                move[target] += 1
                moves[(project, target)] += 1
        knowledge_notes = sum(projects_seen.values())
        if not knowledge_notes:
            skipped.append({"bucket": bucket.name, "non_knowledge": non_knowledge, "unreadable": unreadable})
            continue
        dominant = projects_seen.most_common(1)[0][0]
        record: dict[str, Any] = {
            "bucket": bucket.name,
            "project": dominant,
            "kind": view.kind(dominant),
            "notes": knowledge_notes,
            "stay": stay,
            "move": dict(move.most_common()),
            "relocate": dict(relocate.most_common()),
            "unresolved": dict(unresolved.most_common()),
            "provenance_mismatch": dict(mismatch.most_common()),
            "non_knowledge": non_knowledge,
            "unreadable": unreadable,
        }
        if len(projects_seen) > 1:
            record["projects"] = dict(projects_seen.most_common())
        buckets.append(record)

    moves_by_from: dict[str, Counter[str]] = defaultdict(Counter)
    for (from_project, to_slug), count in moves.items():
        moves_by_from[from_project][to_slug] += count
    proposals: list[dict[str, Any]] = []
    for (from_project, to_slug), count in sorted(moves.items(), key=lambda item: (-item[1], item[0])):
        reason = None
        if from_project == UNKNOWN_PROJECT:
            reason = "_unknown 去向分散：rekey 以 project key 全量搬移，需逐 slice 遷移（P2）"
        elif not is_safe_path_component(to_slug):
            reason = "目標為 raw remote 形：rekey --to 需 path-safe 短 slug，須先在 registry 登記短 slug"
        elif moves_by_from[from_project] != Counter({to_slug: totals_by_project[from_project]}):
            reason = "同一 project 的 note 去向不一或含未解析者：rekey 會全量搬移，需逐 slice 遷移"
        executable = reason is None
        proposals.append(
            {
                "from_project": from_project,
                "from_bucket": project_directory_key(from_project),
                "to_slug": to_slug,
                "to_bucket": project_directory_key(to_slug),
                "notes": count,
                "rekey_executable": executable,
                "command": _proposal_command(root, from_project, to_slug) if executable else None,
                "reason": reason,
            }
        )

    relocation_rows = [
        {
            "from_bucket": from_bucket,
            "project": project,
            "to_bucket": project_directory_key(project),
            "notes": count,
            "rekey_executable": False,
            "command": None,
            "reason": "frontmatter project 已正確、檔案位於其他 bucket：rekey 以 frontmatter "
            "project 選取且拒絕 --from 等於 --to，需逐檔搬移（P2）",
        }
        for (from_bucket, project), count in sorted(
            relocations.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    return {
        "dry_run": True,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
        "memory_root": str(root),
        "planned_remote_backfill": planned_remote_backfill or [],
        "totals": {
            "buckets": len(buckets),
            "notes": sum(bucket["notes"] for bucket in buckets),
            "stay": sum(bucket["stay"] for bucket in buckets),
            "move": sum(moves.values()),
            "relocate": sum(relocations.values()),
            "unresolved": sum(sum(bucket["unresolved"].values()) for bucket in buckets),
        },
        "merge_proposals": proposals,
        "relocations": relocation_rows,
        "unknown_causes": dict(unknown_causes.most_common()),
        "unknown_cause_notes": {key: UNKNOWN_CAUSE_NOTES[key] for key in unknown_causes},
        "unknown_recoverable": {"notes": unknown_recoverable, "total": unknown_total},
        "buckets": buckets,
        "skipped_buckets": skipped,
    }
