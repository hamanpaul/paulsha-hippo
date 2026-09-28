"""唯讀、project-scoped task-memory provider 與 subprocess protocol。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from . import paths
from .importer.config import ProjectsConfig, default_projects_path
from .importer.project_resolver import normalize_remote
from .importer.registry import default_registry_path, load_union_projects_config
from .ledger.processing import redact_secret_text
from .moc import frontmatter_io
from .moc.search import search as moc_search
from .task_memory_payload import build_task_memory_payload, validate_task_memory_payload


HIPPO_EVIDENCE_SOURCE = "hippo"
TASK_MEMORY_MANIFEST_SCHEMA = "hippo/task-memory-manifest/v1"
MAX_CANDIDATES = 3
MAX_STDIN_BYTES = 64 * 1024
MAX_STDOUT_BYTES = 512 * 1024
MAX_RAW_NOTE_BYTES = 128 * 1024
MAX_DELIVERED_NOTE_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 10.0
_SCHEMA_MAJOR_RE = re.compile(r"(?:^|/)v?(\d+)(?:\.\d+)?$")
_NOTE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ERROR_EXIT_CODES = {
    "permission-denied": 10,
    "timeout": 11,
    "scope-mismatch": 12,
    "hash-mismatch": 13,
    "unsupported-schema": 14,
    "manifest-mismatch": 15,
    "invalid-request": 16,
    "size-limit": 17,
    "provider-error": 18,
}


class TaskMemoryProviderError(Exception):
    """有界、可機器判讀的 provider 錯誤；不攜帶 note 文字或路徑。"""

    def __init__(self, code: str) -> None:
        self.code = code if code in _ERROR_EXIT_CODES else "provider-error"
        self.exit_code = _ERROR_EXIT_CODES[self.code]
        super().__init__(self.code)


class _RequestError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DeadlineExpired(TimeoutError):
    pass


class TaskMemoryProvider:
    """以 Hippo 專案 registry 授權，重用 MOC FTS 搜尋的唯讀 provider。"""

    def __init__(
        self,
        *,
        memory_root: str | Path | None = None,
        projects: ProjectsConfig | None = None,
        search_fn: Callable[..., list[dict[str, Any]]] | None = None,
        environ: Mapping[str, str] | None = None,
        rerank_jev_factory: Callable[..., Any] | None = None,
        defer_shadow: bool = False,
    ) -> None:
        self.memory_root = Path(memory_root).expanduser() if memory_root is not None else paths.memory_root()
        self.projects = projects
        self.search_fn = search_fn or moc_search
        # #176：R75 重排序 shadow 的 feature flag 與注入點（預設 off，不影響正式輸出）
        self.environ = environ
        self.rerank_jev_factory = rerank_jev_factory
        # CLI 設 defer_shadow=True：正式輸出寫出後才由脫離的背景行程跑 shadow，不增加 pull 延遲
        self.defer_shadow = defer_shadow
        self.pending_shadow: dict[str, Any] | None = None

    def provide(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        """接收 Cortex request envelope，輸出有 hash manifest 的 provider payload。"""

        self.pending_shadow = None
        request = _validate_envelope(envelope)
        project_slug = self._resolve_project(request["project"])
        try:
            hits = self.search_fn(
                self.memory_root,
                request["intent"],
                project=project_slug,
                limit=MAX_CANDIDATES,
                include_decayed=False,
            )
        except PermissionError:
            raise TaskMemoryProviderError("permission-denied") from None
        except TimeoutError:
            raise TaskMemoryProviderError("timeout") from None
        except Exception:
            raise TaskMemoryProviderError("provider-error") from None

        mode = request["mode"]
        notes: list[dict[str, Any]] = []
        seen: set[str] = set()
        for rank, hit in enumerate(hits[:MAX_CANDIDATES], start=1):
            if not isinstance(hit, Mapping):
                raise TaskMemoryProviderError("provider-error")
            if hit.get("project") != project_slug:
                raise TaskMemoryProviderError("scope-mismatch")
            note_id = hit.get("slice_id")
            if not isinstance(note_id, str) or _NOTE_ID_RE.fullmatch(note_id) is None:
                continue
            if note_id in seen:
                continue
            seen.add(note_id)
            note = _read_note(self.memory_root, hit)
            content_hash = _sha256(note["content"].encode("utf-8"))
            excerpt = note["content"][:800].strip()
            candidate = {
                "ref": note_id,
                "note_id": note_id,
                "rank": rank,
                "summary": note["summary"],
                "authorization": {"status": "authorized"},
                "availability": {"status": "available"},
                "content_hash": content_hash,
                "content_version": note["content_version"],
                "applicability": [],
                "relevance_reason": "符合 task intent 的 project scoped 搜尋結果。",
                "source_time": note["source_time"],
                "project": request["project"],
                "_content": note["content"],
            }
            if mode == "inline" and excerpt:
                candidate["excerpt"] = excerpt
            notes.append(candidate)

        # 先交由 #146 純函式做欄位驗證、遮蔽與 excerpt 截斷，再為 inline
        # 綁定「實際會交給 Cortex 的 excerpt」bytes 計算 content_hash。
        candidate_input = [
            {key: value for key, value in note.items() if key != "_content"}
            for note in notes
        ]
        delivery = {
            "mode": mode,
            "capabilities": request["capabilities"],
        }
        try:
            payload = build_task_memory_payload(
                schema_version="1",
                task_id=request["task_id"],
                intent=request["intent"],
                candidates=candidate_input,
                delivery=delivery,
                evidence=[],
                producer={"id": "hippo-core", "version": "1"},
                adapter={"id": "hippo-task-memory-provider", "version": "1"},
            )
        except (TypeError, ValueError):
            raise TaskMemoryProviderError("provider-error") from None

        manifest_entries: list[dict[str, Any]] = []
        notes_by_id = {item["note_id"]: item for item in notes}
        for candidate in payload["candidates"]:
            note = notes_by_id[candidate["note_id"]]
            if mode == "inline":
                delivered = candidate.get("excerpt") or candidate["summary"]
                candidate["content_hash"] = _sha256(delivered.encode("utf-8"))
            else:
                delivered = note["_content"]
            entry = {
                "note_id": candidate["note_id"],
                "content_hash": candidate["content_hash"],
                "content_version": candidate["content_version"],
                "project": request["project"],
            }
            if mode == "snapshot":
                entry["content"] = delivered
            manifest_entries.append(entry)

        manifest = _build_manifest(
            task_id=request["task_id"],
            project=request["project"],
            entries=manifest_entries,
        )
        payload["project"] = request["project"]
        payload["delivery"]["manifest"] = manifest
        try:
            validated = validate_task_memory_payload(payload)
        except (TypeError, ValueError):
            raise TaskMemoryProviderError("provider-error") from None
        self._rerank_shadow(request, project_slug, validated)
        return validated

    def _rerank_shadow(self, request: Mapping[str, Any], project_slug: str, payload: Mapping[str, Any]) -> None:
        """#176：flag 為 shadow 時計算 R75 並寫 receipt；任何失敗都不影響已產生的正式輸出。"""
        env = os.environ if self.environ is None else self.environ
        if env.get("HIPPO_TASK_MEMORY_RERANK", "").strip().lower() != "shadow":
            return  # 預設 off：不 import、不做任何事
        from . import task_memory_rerank

        if self.defer_shadow:
            self.pending_shadow = task_memory_rerank.deferred_job(request, project_slug, payload)
            return
        try:
            receipt = task_memory_rerank.shadow(
                memory_root=self.memory_root, request=request, project_slug=project_slug,
                production_payload=payload, search_fn=self.search_fn, read_note=_read_note,
                environ=self.environ, jev_factory=self.rerank_jev_factory)
            if receipt is not None:
                task_memory_rerank.append_receipt(self.memory_root, receipt)
        except _DeadlineExpired:
            raise
        except Exception:  # noqa: BLE001 - shadow 不得影響正式輸出
            return

    def fetch(self, wrapper: Mapping[str, Any]) -> dict[str, str]:
        """依原 request、provider manifest 與 note id 回傳 redacted content。"""

        if not isinstance(wrapper, Mapping):
            raise TaskMemoryProviderError("invalid-request")
        request = _validate_envelope(wrapper.get("envelope"))
        if request["mode"] != "note_fetch":
            raise TaskMemoryProviderError("invalid-request")
        note_id = wrapper.get("note_id")
        if not isinstance(note_id, str) or _NOTE_ID_RE.fullmatch(note_id) is None:
            raise TaskMemoryProviderError("manifest-mismatch")
        project_slug = self._resolve_project(request["project"])
        entry = _validate_fetch_manifest(
            wrapper.get("manifest"),
            task_id=request["task_id"],
            project=request["project"],
            note_id=note_id,
        )
        try:
            hits = self.search_fn(
                self.memory_root,
                request["intent"],
                project=project_slug,
                limit=MAX_CANDIDATES,
                include_decayed=False,
            )
        except PermissionError:
            raise TaskMemoryProviderError("permission-denied") from None
        except TimeoutError:
            raise TaskMemoryProviderError("timeout") from None
        except Exception:
            raise TaskMemoryProviderError("provider-error") from None

        selected = next(
            (
                hit for hit in hits[:MAX_CANDIDATES]
                if isinstance(hit, Mapping) and hit.get("slice_id") == note_id
            ),
            None,
        )
        if selected is None:
            raise TaskMemoryProviderError("manifest-mismatch")
        if selected.get("project") != project_slug:
            raise TaskMemoryProviderError("scope-mismatch")
        note = _read_note(self.memory_root, selected)
        actual_hash = _sha256(note["content"].encode("utf-8"))
        if (
            actual_hash != entry["content_hash"]
            or note["content_version"] != entry["content_version"]
        ):
            raise TaskMemoryProviderError("hash-mismatch")
        if len(note["content"].encode("utf-8")) > MAX_DELIVERED_NOTE_BYTES:
            raise TaskMemoryProviderError("size-limit")
        return {
            "content": note["content"],
            "content_hash": actual_hash,
            "content_version": note["content_version"],
        }

    def _resolve_project(self, repo: str) -> str:
        normalized = normalize_remote(repo)
        if not normalized:
            raise TaskMemoryProviderError("scope-mismatch")
        projects = self.projects
        if projects is None:
            projects = load_union_projects_config(
                default_projects_path(self.memory_root),
                default_registry_path(self.memory_root),
            )
        matches = {
            project.slug
            for project in projects.projects
            if any(normalize_remote(remote) == normalized for remote in project.remotes)
        }
        if len(matches) != 1:
            raise TaskMemoryProviderError("scope-mismatch")
        return next(iter(matches))


def _validate_envelope(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, Mapping):
        raise TaskMemoryProviderError("invalid-request")
    schema = envelope.get("schema_version")
    major_match = _SCHEMA_MAJOR_RE.search(str(schema)) if schema is not None else None
    if major_match is None:
        raise TaskMemoryProviderError("invalid-request")
    if int(major_match.group(1)) != 1:
        raise TaskMemoryProviderError("unsupported-schema")
    task_id = envelope.get("task_id")
    intent = envelope.get("intent")
    project = envelope.get("project")
    delivery = envelope.get("delivery")
    if (
        not isinstance(task_id, str) or not task_id or len(task_id) > 256
        or not isinstance(intent, str) or not intent.strip() or len(intent) > 280
        or not isinstance(project, str) or not project
        or not isinstance(delivery, Mapping)
    ):
        raise TaskMemoryProviderError("invalid-request")
    mode = delivery.get("mode")
    if mode not in {"inline", "snapshot", "note_fetch"}:
        raise TaskMemoryProviderError("invalid-request")
    capabilities = delivery.get("capabilities")
    host_scope = delivery.get("host_scope")
    if not isinstance(capabilities, Mapping) or not isinstance(host_scope, Mapping):
        raise TaskMemoryProviderError("invalid-request")
    if any(
        key in capabilities and not isinstance(capabilities[key], bool)
        for key in ("inline", "snapshot", "note_fetch")
    ):
        raise TaskMemoryProviderError("invalid-request")
    normalized_capabilities = {
        key: capabilities.get(key, False)
        for key in ("inline", "snapshot", "note_fetch")
    }
    if not normalized_capabilities[mode]:
        raise TaskMemoryProviderError("invalid-request")
    repo = host_scope.get("repo")
    allowed_sources = host_scope.get("allowed_evidence_sources")
    if not isinstance(repo, str) or repo != project:
        raise TaskMemoryProviderError("scope-mismatch")
    if not isinstance(allowed_sources, list) or any(not isinstance(item, str) for item in allowed_sources):
        raise TaskMemoryProviderError("invalid-request")
    if HIPPO_EVIDENCE_SOURCE not in allowed_sources:
        raise TaskMemoryProviderError("permission-denied")
    return {
        "task_id": task_id,
        "intent": intent.strip(),
        "project": project,
        "mode": mode,
        "capabilities": normalized_capabilities,
    }


def _build_manifest(
    *, task_id: str, project: str, entries: list[dict[str, Any]]
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": TASK_MEMORY_MANIFEST_SCHEMA,
        "task_id": task_id,
        "project": project,
        "entries": entries,
    }
    manifest["sha256"] = _sha256(_canonical_json(manifest))
    return manifest


def _validate_fetch_manifest(
    manifest: Any, *, task_id: str, project: str, note_id: str
) -> dict[str, str]:
    if not isinstance(manifest, Mapping):
        raise TaskMemoryProviderError("manifest-mismatch")
    if (
        manifest.get("schema") != TASK_MEMORY_MANIFEST_SCHEMA
        or manifest.get("task_id") != task_id
        or manifest.get("project") != project
    ):
        raise TaskMemoryProviderError("scope-mismatch")
    entries = manifest.get("entries")
    digest = manifest.get("sha256")
    if (
        not isinstance(entries, list)
        or len(entries) > MAX_CANDIDATES
        or not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
    ):
        raise TaskMemoryProviderError("manifest-mismatch")
    unsigned = dict(manifest)
    unsigned.pop("sha256", None)
    try:
        expected = _sha256(_canonical_json(unsigned))
    except (TypeError, ValueError, RecursionError):
        raise TaskMemoryProviderError("manifest-mismatch") from None
    if digest != expected:
        raise TaskMemoryProviderError("manifest-mismatch")
    found: dict[str, str] | None = None
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "note_id", "content_hash", "content_version", "project"
        }:
            # Provider manifests deliberately contain no path-like or other
            # caller-directed locator fields.
            raise TaskMemoryProviderError("manifest-mismatch")
        item_id = entry.get("note_id")
        item_hash = entry.get("content_hash")
        item_version = entry.get("content_version")
        if (
            not isinstance(item_id, str)
            or _NOTE_ID_RE.fullmatch(item_id) is None
            or item_id in seen
            or not isinstance(item_hash, str)
            or _SHA256_RE.fullmatch(item_hash) is None
            or not isinstance(item_version, str)
            or not item_version
            or entry.get("project") != project
        ):
            raise TaskMemoryProviderError("manifest-mismatch")
        seen.add(item_id)
        if item_id == note_id:
            found = {"content_hash": item_hash, "content_version": item_version}
    if found is None:
        raise TaskMemoryProviderError("manifest-mismatch")
    return found


def _read_note(memory_root: Path, hit: Mapping[str, Any]) -> dict[str, str]:
    raw_path = hit.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise TaskMemoryProviderError("scope-mismatch")
    try:
        raw_bytes, metadata = _read_beneath_knowledge(memory_root, raw_path)
        text = raw_bytes.decode("utf-8")
    except PermissionError:
        raise TaskMemoryProviderError("permission-denied") from None
    except FileNotFoundError:
        raise TaskMemoryProviderError("provider-error") from None
    except UnicodeDecodeError:
        raise TaskMemoryProviderError("provider-error") from None
    except OSError:
        raise TaskMemoryProviderError("provider-error") from None
    frontmatter, body = frontmatter_io.read(text)
    content = redact_secret_text(body)
    home = str(Path.home())
    if home and home != "/":
        content = content.replace(home, "~")
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_DELIVERED_NOTE_BYTES:
        raise TaskMemoryProviderError("size-limit")
    title = frontmatter.get("title") if isinstance(frontmatter, Mapping) else None
    if not isinstance(title, str) or not title.strip():
        title = hit.get("title")
    if not isinstance(title, str) or not title.strip():
        title = next((line.strip() for line in content.splitlines() if line.strip()), "Untitled note")
    source_time = None
    if isinstance(frontmatter, Mapping):
        source_time = frontmatter.get("captured_at") or frontmatter.get("source_time") or frontmatter.get("created_at")
    if source_time is None:
        source_time = hit.get("captured_at")
    if not isinstance(source_time, str) or not source_time.strip():
        source_time = datetime.fromtimestamp(metadata.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "content": content,
        "content_version": "sha256:" + _sha256(raw_bytes),
        "summary": title.strip(),
        "source_time": source_time.strip(),
    }


def _read_beneath_knowledge(memory_root: Path, raw_path: str) -> tuple[bytes, os.stat_result]:
    root = memory_root.resolve() / "knowledge"
    candidate = Path(raw_path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise TaskMemoryProviderError("scope-mismatch")
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise TaskMemoryProviderError("scope-mismatch") from None
    if not relative.parts or candidate.suffix.lower() != ".md":
        raise TaskMemoryProviderError("scope-mismatch")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    dir_fd = os.open(root, directory_flags)
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        note_fd = os.open(relative.parts[-1], file_flags, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    try:
        info = os.fstat(note_fd)
        if not stat.S_ISREG(info.st_mode):
            raise TaskMemoryProviderError("scope-mismatch")
        stream = os.fdopen(note_fd, "rb")
        note_fd = -1
        with stream:
            raw_bytes = stream.read(MAX_RAW_NOTE_BYTES + 1)
        if len(raw_bytes) > MAX_RAW_NOTE_BYTES:
            raise TaskMemoryProviderError("size-limit")
        return raw_bytes, info
    finally:
        if note_fd >= 0:
            os.close(note_fd)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@contextmanager
def deadline(seconds: float = DEFAULT_TIMEOUT_SECONDS) -> Iterator[None]:
    """Unix CLI 用 bounded deadline；直接函式呼叫仍可注入 TimeoutError 驗證。"""

    if seconds <= 0 or threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        if seconds <= 0:
            raise TaskMemoryProviderError("invalid-request")
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def expire(_signum: int, _frame: Any) -> None:
        raise _DeadlineExpired()

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    except _DeadlineExpired:
        raise TaskMemoryProviderError("timeout") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, max(0.001, previous_timer[0] - seconds), previous_timer[1])


def read_stdin_bounded(stream: Any, limit: int = MAX_STDIN_BYTES) -> bytes:
    """Read at most limit+1 bytes without buffering unbounded protocol input."""

    source = getattr(stream, "buffer", stream)
    try:
        data = source.read(limit + 1)
    except (OSError, ValueError):
        raise TaskMemoryProviderError("invalid-request") from None
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, bytes):
        raise TaskMemoryProviderError("invalid-request")
    if len(data) > limit:
        raise TaskMemoryProviderError("size-limit")
    return data


def parse_protocol_json(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise TaskMemoryProviderError("invalid-request") from None
    if not isinstance(value, dict):
        raise TaskMemoryProviderError("invalid-request")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate protocol key")
        result[key] = value
    return result


def serialize_protocol_json(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = _canonical_json(dict(value))
    except (TypeError, ValueError, RecursionError):
        raise TaskMemoryProviderError("provider-error") from None
    if len(encoded) > MAX_STDOUT_BYTES:
        raise TaskMemoryProviderError("size-limit")
    return encoded + b"\n"
