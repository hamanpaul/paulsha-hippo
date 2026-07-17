from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ..ledger import processing, relations
from ..noise import DocCorpus, classify_noise
from . import slice_frontmatter, splitter
from .config import AtomizerConfig, is_safe_path_component, sanitize_project_component
from .llm_promoter import LLMPromoter, PromoteError
from .promoter import IdentityPromoter, Promoter
from .splitter import Fragment

LOGGER = logging.getLogger(__name__)
_ATOMIZER_INBOX_FILE_MAX_BYTES = 64 * 1024 * 1024


def _parse_frontmatter(text: str) -> tuple[Mapping[str, Any] | None, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None, text
    block = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:])
    try:
        import yaml  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return None, body
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        # Malformed frontmatter: return the unparseable sentinel so the caller skips
        # this one doc (recording a warning) instead of aborting the atomize pass (#139).
        return None, body
    if not isinstance(data, dict):
        return None, body
    return data, body


def _month(captured_at: str, now: str) -> str:
    base = captured_at if captured_at[:7].count("-") == 1 else now
    return base[:7] if len(base) >= 7 else now[:7]


def _raw_session_docs(memory_root: Path) -> list[Path]:
    inbox = memory_root / "inbox"
    slices_dir = inbox / "_slices"
    docs: list[Path] = []
    for path in sorted(inbox.rglob("*.md")):
        if slices_dir in path.parents:
            continue
        docs.append(path)
    return docs


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _archive_fragments(memory_root: Path, fragment_paths: list[Path], now: str) -> None:
    for frag_path in sorted(set(fragment_paths), key=lambda path: path.name):
        if not frag_path.exists():
            continue
        dst = memory_root / "archive" / "fragments" / _month("", now) / frag_path.name
        if dst.is_file():
            incoming = frag_path.read_bytes()
            if dst.read_bytes() == incoming:
                frag_path.unlink()
                continue
            digest = hashlib.sha256(incoming).hexdigest()[:12]
            dst = dst.with_name(f"{dst.stem}--{digest}{dst.suffix}")
        _move(frag_path, dst)


def _cache_path(memory_root: Path, cache_key: str) -> Path | None:
    if not LLMPromoter.is_valid_cache_key(cache_key):
        return None
    cache_root = (memory_root / "runtime" / "cache" / "atomize").resolve()
    candidate = (cache_root / f"{cache_key}.json").resolve()
    if candidate.parent != cache_root:
        return None
    return candidate


def _clear_cache_key(memory_root: Path, cache_key: str | None) -> None:
    if not cache_key:
        return
    candidate = _cache_path(memory_root, cache_key)
    if candidate is None:
        return
    try:
        candidate.unlink()
    except FileNotFoundError:
        return


def _retry_counter_path(memory_root: Path, cache_key: str) -> Path | None:
    if not LLMPromoter.is_valid_cache_key(cache_key):
        return None
    cache_root = (memory_root / "runtime" / "cache" / "atomize").resolve()
    candidate = (cache_root / f"{cache_key}.retries").resolve()
    if candidate.parent != cache_root:
        return None
    return candidate


def _clear_retry_counter(memory_root: Path, cache_key: str | None) -> None:
    if not cache_key:
        return
    counter = _retry_counter_path(memory_root, cache_key)
    if counter is None:
        return
    try:
        counter.unlink()
    except FileNotFoundError:
        return


_FAILED_EVIDENCE_DIRNAME = "_failed"


def _failed_evidence_path(memory_root: Path, session_key: str) -> Path:
    agent, _, session = session_key.partition(":")
    return (memory_root / "runtime" / "queue" / _FAILED_EVIDENCE_DIRNAME
            / f"{agent}__{session}.json")


def _read_attempts(counter: Path) -> int:
    try:
        return int(counter.read_text(encoding="utf-8").strip() or "0")
    except (FileNotFoundError, OSError, ValueError):
        return 0


def _park_session(memory_root: Path, *, session_key: str, category: str, attempts: int,
                  cache_key: str, error_text: str, now: str, config_hash: str,
                  last_output_excerpt: str = "") -> None:
    """parked 終態：證據落盤 → 淘汰毒快取＋sidecar（保留 split fragments）→ 記 ledger。

    ledger append 放最後當 commit point：中途 crash 只會多一次 bounded 重試（fail-open），
    不會留下「已 parked 但毒快取還在」的半套狀態。

    去敏是本函式的職責（單一 choke point）。模型 stdout 可能完整回顯 private
    prompt，因此只落 bytes/hash，不保存可逆 excerpt；error 仍 fail-closed redaction。
    """
    error_text = processing.sanitize_error_text(error_text)
    cache_path = _cache_path(memory_root, cache_key)
    excerpt = last_output_excerpt
    if cache_path is not None and cache_path.exists():
        try:
            excerpt = cache_path.read_text(encoding="utf-8") or excerpt
        except (OSError, UnicodeError):
            excerpt = ""
    output_bytes = excerpt.encode("utf-8", errors="replace")
    evidence = {
        "session_key": session_key,
        "failure_category": category,
        "attempts": attempts,
        "cache_key": cache_key,
        "error": error_text,
        "ts": now,
        "last_output_bytes": len(output_bytes),
        "last_output_sha256": hashlib.sha256(output_bytes).hexdigest(),
    }
    _atomic_write(
        _failed_evidence_path(memory_root, session_key),
        json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n",
    )
    _clear_cache_key(memory_root, cache_key)
    _clear_retry_counter(memory_root, cache_key)
    processing.append_state(
        memory_root,
        session_key=session_key,
        state="parked",
        now=now,
        config_hash=config_hash,
        failure_category=category,
        attempts=attempts,
        cache_key=cache_key,
        error=error_text,
    )


def _residual_cache_keys(memory_root: Path, session_key: str) -> list[str]:
    """列出 session 遺留在 cache 目錄的所有 cache_key 變體（`.json`／`.retries`）。

    初始化失敗路徑（park_split_sessions）拿不到 promoter、無法重算 cache_key，
    只能從磁碟殘留反推。glob 安全：caller 已驗證 agent／session 為 safe path
    component（`*?[]` 皆被拒），session_key 可直接當字面 pattern。session 名
    本身含 `__` 時 prefix glob 可能撈到別的 session（`claude:a` 的 pattern 會
    命中 `claude:a__b` 的 sidecar）——以 rpartition 還原 session_key 精確比對。
    """
    cache_root = memory_root / "runtime" / "cache" / "atomize"
    if not cache_root.is_dir():
        return []
    keys: set[str] = set()
    for path in cache_root.glob(f"{session_key}__*"):
        if path.suffix not in {".json", ".retries"}:
            continue
        cache_key = path.stem
        if cache_key.rpartition("__")[0] != session_key:
            continue
        if not LLMPromoter.is_valid_cache_key(cache_key):
            continue
        keys.add(cache_key)
    return sorted(keys)


def park_split_sessions(memory_root: Path, *, error_text: str, now: str,
                        config_hash: str,
                        category: str = "backend_unavailable") -> list[str]:
    """#15 失敗鏈：atomizer 初始化即失敗（config 無效／promoter 建構失敗）時，
    把 eligible（state == split）sessions 立即 park（含證據落盤）。

    spec 契約「config 無效立即 parked」——否則 pending session 卡在 split、
    無 failure category／evidence，timer 每輪重複整輪失敗。回傳被 park 的
    session keys（排序後，決定性）。

    spec §3.1「進 parked 即淘汰 LLM output cache＋retry sidecar」對任何進入
    路徑無條件成立：本路徑從磁碟殘留反推真實 cache_key／attempts 落證據
    （取 attempts 最大的變體為主），並清除該 session 的「所有」sidecar 變體
    ——否則 requeue 後會繼承過期 retry 計數（殘留 attempts=5 時再 1 次失敗
    即重新 park），且 parked 證據的 cache_key／attempts 欄位失真。
    """
    parked: list[str] = []
    for session_key, state in sorted(processing.fold_states(memory_root).items()):
        if state != "split":
            continue
        agent, _, session = session_key.partition(":")
        if not all(is_safe_path_component(value) for value in (agent, session)):
            continue
        residual = _residual_cache_keys(memory_root, session_key)
        cache_key = ""
        attempts = 0
        for candidate in residual:
            counter = _retry_counter_path(memory_root, candidate)
            candidate_attempts = _read_attempts(counter) if counter is not None else 0
            if not cache_key or candidate_attempts > attempts:
                cache_key, attempts = candidate, candidate_attempts
        for candidate in residual:
            if candidate == cache_key:
                continue  # 主變體交給 _park_session（先讀 excerpt 證據再清）
            _clear_cache_key(memory_root, candidate)
            _clear_retry_counter(memory_root, candidate)
        _park_session(
            memory_root, session_key=session_key, category=category,
            attempts=attempts, cache_key=cache_key, error_text=error_text,
            now=now, config_hash=config_hash,
        )
        parked.append(session_key)
    return parked


def _handle_promote_failure(
    memory_root: Path,
    promoter: Promoter,
    fragments: list[Fragment],
    exc: "PromoteError",
    *,
    session_key: str,
    now: str,
    config_hash: str,
) -> tuple[str, bool]:
    """Park a failed LLM chunk after its bounded in-call retry budget is exhausted.

    `LLMPromoter` owns the exact per-chunk attempt budget.  Retrying the same split
    again on later scheduler runs would silently exceed that contract, so every
    exhausted LLM failure is terminal until an explicit `hippo requeue`.  Non-LLM
    promoters retain the legacy left-in-split behavior.
    """
    if not isinstance(promoter, LLMPromoter) or not fragments:
        return "", False
    category = getattr(exc, "category", "invalid_output")
    if category not in processing.PARKED_FAILURE_CATEGORIES:
        category = "invalid_output"
    error_text = processing.sanitize_error_text(str(exc))
    cache_key = promoter.cache_key_for_fragments(fragments)
    counter = _retry_counter_path(memory_root, cache_key)
    if counter is None:
        return "", False
    attempts = int(getattr(exc, "attempts", 0))
    promoter.clear_last_chunk_caches()
    promoter.clear_cache_for_fragments(fragments)
    _park_session(
        memory_root,
        session_key=session_key,
        category=category,
        attempts=attempts,
        cache_key=cache_key,
        error_text=error_text,
        now=now,
        config_hash=config_hash,
        last_output_excerpt=str(getattr(promoter, "last_raw_output", "")),
    )
    return f" (parked: {category} after {attempts} bounded chunk attempt(s))", True


def _promoter_metadata(promoter: Promoter) -> dict[str, str]:
    if isinstance(promoter, IdentityPromoter):
        return {"promoter": "identity"}
    if isinstance(promoter, LLMPromoter):
        skill_text = getattr(promoter, "_skill", "")
        return {
            "promoter": "llm",
            "model": str(getattr(promoter, "_model", "unknown")),
            "skill_hash": hashlib.sha256(skill_text.encode("utf-8")).hexdigest(),
        }
    return {}


def _fragment_refs_for_slice(
    slice_: slice_frontmatter.Slice,
    fragments_by_index: dict[int, tuple[Path, Fragment]],
    fragments_by_ref: dict[str, tuple[Path, Fragment]],
) -> list[tuple[Path, Fragment]]:
    source_fragments = slice_.frontmatter.get("source_fragments")
    if isinstance(source_fragments, list) and source_fragments:
        return [fragments_by_index[int(index)] for index in source_fragments]

    fragment_ref = slice_.frontmatter.get("fragment_ref")
    if isinstance(fragment_ref, str) and fragment_ref:
        return [fragments_by_ref[fragment_ref]]

    raise KeyError(f"slice {slice_.slice_id} is missing source fragment references")


def _prepare_slice_writes(
    promoted: list[slice_frontmatter.Slice],
    *,
    fragments_by_index: dict[int, tuple[Path, Fragment]],
    fragments_by_ref: dict[str, tuple[Path, Fragment]],
) -> list[tuple[slice_frontmatter.Slice, list[tuple[Path, Fragment]]]]:
    prepared: list[tuple[slice_frontmatter.Slice, list[tuple[Path, Fragment]]]] = []
    for slice_ in promoted:
        prepared.append(
            (
                slice_,
                _fragment_refs_for_slice(slice_, fragments_by_index, fragments_by_ref),
            )
        )
    return prepared


def _knowledge_path_for(memory_root: Path, project: str, slice_id: str) -> Path:
    project_dir = memory_root / "knowledge" / str(project)
    if project_dir.exists():
        for candidate in sorted(project_dir.glob(f"*--{slice_id}.md")):
            return candidate
        legacy = project_dir / f"{slice_id}.md"
        if legacy.exists():
            return legacy
    return project_dir / f"{slice_id}.md"


def _append_semantic_edges(
    memory_root: Path,
    *,
    slice_: slice_frontmatter.Slice,
    title_to_slice_id: dict[str, str],
    now: str,
    config_hash: str,
    warnings: list[str],
) -> None:
    for relation in slice_.relations:
        relation_type = relation["type"]
        if relation_type == "relates_to":
            target_title = str(relation["target_title"])
            target_slice_id = title_to_slice_id.get(target_title)
            if target_slice_id is None:
                warnings.append(
                   f"slice:{slice_.slice_id}: relates_to target_title {target_title!r} not found; edge skipped"
                )
                continue
            relations.append_edge(
                memory_root,
                type="relates_to",
                frm=f"slice:{slice_.slice_id}",
                to=f"slice:{target_slice_id}",
                now=now,
                config_hash=config_hash,
            )
            continue

        if relation_type == "mentions":
            relations.append_edge(
                memory_root,
                type="mentions",
                frm=f"slice:{slice_.slice_id}",
                to=f"entity:{relation['entity']}",
                now=now,
                config_hash=config_hash,
            )
            continue

        warnings.append(
            f"slice:{slice_.slice_id}: unsupported semantic relation type {relation_type!r}; edge skipped"
        )


def _semantic_edge_specs(
    slice_: slice_frontmatter.Slice,
    *,
    title_to_slice_id: dict[str, str],
    warnings: list[str],
) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for relation in slice_.relations:
        relation_type = relation["type"]
        if relation_type == "relates_to":
            target_title = str(relation["target_title"])
            target_slice_id = title_to_slice_id.get(target_title)
            if target_slice_id is None:
                warnings.append(
                    f"slice:{slice_.slice_id}: relates_to target_title {target_title!r} not found; edge skipped"
                )
                continue
            specs.append(
                {
                    "type": "relates_to",
                    "from": f"slice:{slice_.slice_id}",
                    "to": f"slice:{target_slice_id}",
                }
            )
        elif relation_type == "mentions":
            specs.append(
                {
                    "type": "mentions",
                    "from": f"slice:{slice_.slice_id}",
                    "to": f"entity:{relation['entity']}",
                }
            )
    return specs


def _append_publication_event(memory_root: Path, event: dict[str, Any]) -> None:
    path = memory_root / "runtime" / "ledger" / "publication.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_session(
    memory_root: Path,
    *,
    session_key: str,
    accepted_writes: list[
        tuple[slice_frontmatter.Slice, list[tuple[Path, Fragment]]]
    ],
    title_to_slice_id: dict[str, str],
    now: str,
    config_hash: str,
    warnings: list[str],
) -> str:
    """Stage every accepted atom, then materialize one idempotent publication.

    Slice IDs are content-derived.  An existing target must therefore be byte-identical;
    a collision fails closed instead of overwriting another publication.  The relation
    set is appended under one lock and a deterministic journal makes a process crash
    replayable while the processing state remains `split`.
    """
    rendered_rows: list[tuple[Path, bytes, slice_frontmatter.Slice, list[tuple[Path, Fragment]]]] = []
    identity = []
    for slice_, referenced_fragments in accepted_writes:
        target = _knowledge_path_for(
            memory_root,
            sanitize_project_component(str(slice_.frontmatter["project"])),
            slice_.slice_id,
        )
        rendered = slice_frontmatter.render(slice_).encode("utf-8")
        identity.append((slice_.slice_id, hashlib.sha256(rendered).hexdigest()))
        rendered_rows.append((target, rendered, slice_, referenced_fragments))
    publication_id = hashlib.sha256(
        json.dumps(
            {"session": session_key, "config": config_hash, "slices": identity},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    stage_root = memory_root / "runtime" / "staging" / "atomize" / publication_id
    staged: list[tuple[Path, Path, bytes]] = []
    for index, (target, rendered, _, _) in enumerate(rendered_rows):
        if target.is_file() and target.read_bytes() != rendered:
            raise PromoteError(f"slice publication collision at {target}")
        stage = stage_root / f"{index:04d}.md"
        stage.parent.mkdir(parents=True, exist_ok=True)
        with stage.open("wb") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        staged.append((stage, target, rendered))
    if staged:
        _fsync_parent(staged[0][0])

    edge_specs: list[dict[str, str]] = []
    for _, _, slice_, referenced_fragments in rendered_rows:
        for frag_path, _ in referenced_fragments:
            edge_specs.append(
                {
                    "type": "promoted_to",
                    "from": f"fragment:{frag_path.stem}",
                    "to": f"slice:{slice_.slice_id}",
                }
            )
        edge_specs.append(
            {
                "type": "distilled_from",
                "from": f"slice:{slice_.slice_id}",
                "to": f"session:{session_key}",
            }
        )
        for predecessor in slice_.frontmatter.get("supersedes", []):
            edge_specs.append(
                {
                    "type": "supersedes",
                    "from": f"slice:{slice_.slice_id}",
                    "to": f"slice:{predecessor}",
                }
            )
        edge_specs.extend(
            _semantic_edge_specs(
                slice_, title_to_slice_id=title_to_slice_id, warnings=warnings
            )
        )
    _append_publication_event(
        memory_root,
        {
            "event": "staged",
            "publication_id": publication_id,
            "session_key": session_key,
            "targets": [str(target) for _, target, _ in staged],
            "ts": now,
        },
    )
    relations.append_edges(memory_root, edge_specs, now=now, config_hash=config_hash)
    created: list[Path] = []
    try:
        for stage, target, rendered in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                stage.unlink()
                continue
            os.replace(stage, target)
            _fsync_parent(target)
            if target.read_bytes() != rendered:
                raise OSError(f"publication verification failed: {target}")
            created.append(target)
    except Exception:
        for target in created:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
    return publication_id


def _has_unsupported_semantic_relations(promoted: list[slice_frontmatter.Slice]) -> str | None:
    for slice_ in promoted:
        for relation in slice_.relations:
            relation_type = relation.get("type") if isinstance(relation, Mapping) else None
            if relation_type not in {"relates_to", "mentions"}:
                return f"slice {slice_.slice_id} has unsupported semantic relation type: {relation_type!r}"
    return None


def _canonical_title(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _attach_unambiguous_supersedes(
    memory_root: Path,
    promoted: list[slice_frontmatter.Slice],
) -> list[slice_frontmatter.Slice]:
    """Link a changed body only when source, project, and canonical title agree.

    Zero or multiple matches are intentionally left parallel for manual review.
    """
    existing: list[Mapping[str, Any]] = []
    knowledge = memory_root / "knowledge"
    if knowledge.is_dir():
        for path in sorted(knowledge.rglob("*.md")):
            try:
                frontmatter, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                continue
            if frontmatter is not None:
                existing.append(frontmatter)
    result = []
    for slice_ in promoted:
        frontmatter = dict(slice_.frontmatter)
        title_key = "atom_title" if frontmatter.get("atom_title") else "session_title"
        title = _canonical_title(frontmatter.get(title_key))
        matches = [
            item
            for item in existing
            if item.get("slice_id") != slice_.slice_id
            and item.get("distilled_from") == frontmatter.get("distilled_from")
            and item.get("project") == frontmatter.get("project")
            and _canonical_title(item.get(title_key)) == title
            and item.get("checksum") != frontmatter.get("checksum")
        ]
        if title and len(matches) == 1:
            predecessor = str(matches[0].get("slice_id") or "")
            if predecessor:
                frontmatter["supersedes"] = [predecessor]
                slice_ = replace(slice_, frontmatter=frontmatter)
        result.append(slice_)
    return result


def _promote_fragments(
    promoter: Promoter,
    fragments: list[Fragment],
    config: AtomizerConfig,
) -> list[slice_frontmatter.Slice]:
    try:
        return promoter.promote(fragments, config)
    except PromoteError:
        raise
    except Exception as exc:
        raise PromoteError(f"unexpected promoter failure: {exc}", category="transient") from exc


def _split_pass(memory_root: Path, config: AtomizerConfig, config_hash: str, now: str,
                dry_run: bool, warnings: list[str]) -> tuple[int, dict[str, list[Fragment]]]:
    count = 0
    dry_run_fragments: dict[str, list[Fragment]] = {}
    for raw_path in _raw_session_docs(memory_root):
        path_session_key = f"{raw_path.parent.parent.name}:{raw_path.stem}"
        try:
            raw_bytes = raw_path.read_bytes()
            raw_size = len(raw_bytes)
            raw_text = raw_bytes.decode("utf-8")
        except (OSError, UnicodeError):
            warnings.append(f"{raw_path}: unreadable inbox document; skipped")
            continue
        source_inbox_hash = hashlib.sha256(raw_bytes).hexdigest()
        if raw_size is not None and raw_size > _ATOMIZER_INBOX_FILE_MAX_BYTES:
            if processing.state_of(memory_root, path_session_key) == "skipped":
                continue
            warning = (
                f"{raw_path}: exceeds {_ATOMIZER_INBOX_FILE_MAX_BYTES} bytes; "
                "session skipped (file too large)"
            )
            warnings.append(warning)
            LOGGER.warning(warning)
            processing.append_state(
                memory_root,
                session_key=path_session_key,
                state="skipped",
                now=now,
                config_hash=config_hash,
                skip_reason="file too large",
                skipped_bytes=raw_size,
            )
            continue
        data, body = _parse_frontmatter(raw_text)
        if data is not None and data.get("atomization_replay") is False:
            # Recovery importer output remains inspectable in inbox, while an
            # explicit replay workflow owns any future LLM mutation.
            continue
        if data is None or not data.get("project") or not data.get("source_session"):
            warnings.append(f"{raw_path}: unparseable or missing project/source_session; skipped")
            continue
        agent = str(data.get("source_agent", "_unknown"))
        session = str(data["source_session"])
        project = str(data["project"])
        unsafe_fields = [
            field for field, value in (("source_agent", agent), ("source_session", session))
            if not is_safe_path_component(value)
        ]
        if unsafe_fields:
            warnings.append(f"{raw_path}: unsafe path field(s) {', '.join(unsafe_fields)}; skipped")
            continue
        project_path = sanitize_project_component(project)
        session_key = f"{agent}:{session}"
        current_event = processing.fold_events(memory_root).get(session_key)
        current_state = str(current_event.get("state", "")) if current_event else ""
        if current_state in {"split", "parked"}:
            continue
        if current_state in {"promoted", "no-findings"}:
            prior_hash = current_event.get("source_inbox_hash") if current_event else None
            # Old terminal events have no source pin.  They remain terminal until an
            # explicit recovery/requeue operation; never guess that an old inbox copy
            # is a new capture.  New-format events re-open only on a proven byte change.
            if not isinstance(prior_hash, str) or prior_hash == source_inbox_hash:
                continue
        captured_at = str(data.get("captured_at", now))
        provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
        provenance = {k: str(provenance.get(k, "")) for k in ("repo", "commit", "path")}
        source_artifact = str(data.get("source_artifact", "session"))
        session_title = str(data.get("title", ""))

        bodies = splitter.split(body, config)
        if dry_run:
            fragments = []
            for index, frag_body in enumerate(bodies):
                fragments.append(Fragment(
                    project=project, source_agent=agent, source_session=session,
                    source_artifact=source_artifact, captured_at=captured_at,
                    provenance=provenance, fragment_index=index, body=frag_body,
                    session_title=session_title))
            dry_run_fragments[session_key] = fragments
            count += 1
            continue
        for index, frag_body in enumerate(bodies):
            frag_path = (memory_root / "inbox" / "_slices" / project_path
                         / f"{agent}__{session}__{index:03d}.md")
            _atomic_write(frag_path, _render_fragment(
                project, agent, session, source_artifact, captured_at, provenance, index, frag_body, session_title))
            relations.append_edge(memory_root, type="fragment_of",
                                  frm=f"fragment:{agent}__{session}__{index:03d}",
                                  to=f"session:{session_key}", now=now, config_hash=config_hash)
        processing.append_state(
            memory_root,
            session_key=session_key,
            state="split",
            now=now,
            config_hash=config_hash,
            fragments=len(bodies),
            source_inbox_hash=source_inbox_hash,
        )
        archive = memory_root / "archive" / "sessions" / _month(captured_at, now) / f"{agent}__{session}.md"
        if archive.exists() and archive.read_bytes() != raw_bytes:
            archive = archive.with_name(
                f"{archive.stem}--{source_inbox_hash[:12]}{archive.suffix}"
            )
        _move(raw_path, archive)
        count += 1
    return count, dry_run_fragments


def _render_fragment(project, agent, session, source_artifact, captured_at, provenance, index, body, session_title="") -> str:
    lines = ["---", "memory_layer: inbox", f"project: {project}",
             f"source_agent: {agent}", f"source_session: {session}",
             f"source_artifact: {source_artifact}", f"captured_at: {captured_at}",
             f"session_title: {json.dumps(session_title, ensure_ascii=False)}",
             "provenance:", f"  repo: {provenance.get('repo', '')}",
             f"  commit: {provenance.get('commit', '')}", f"  path: {provenance.get('path', '')}",
             f"fragment_index: {index}", f"parent_session_ref: {agent}:{session}", "---"]
    return "\n".join(lines) + "\n" + body


def _read_fragment(path: Path) -> Fragment | None:
    data, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    if data is None or not data.get("project") or not data.get("source_session"):
        return None
    project = str(data["project"])
    agent = str(data.get("source_agent", "_unknown"))
    session = str(data["source_session"])
    # project is rich metadata (may contain '/', e.g. github.com/owner/repo) — it is
    # sanitized to a path-safe component only where used as a directory. Only the
    # agent/session, which ARE used directly as path components, must be path-safe.
    if not all(is_safe_path_component(value) for value in (agent, session)):
        return None
    provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    provenance = {k: str(provenance.get(k, "")) for k in ("repo", "commit", "path")}
    return Fragment(project=project, source_agent=agent,
                    source_session=session,
                    source_artifact=str(data.get("source_artifact", "session")),
                    captured_at=str(data.get("captured_at", "")), provenance=provenance,
                    fragment_index=int(data.get("fragment_index", 0)), body=body,
                    session_title=str(data.get("session_title", "")))


def _promote_pass(memory_root: Path, config: AtomizerConfig, config_hash: str, now: str,
                  dry_run: bool, promoter: Promoter, warnings: list[str],
                  dry_run_fragments: dict[str, list[Fragment]],
                  doc_corpus: "DocCorpus | None" = None) -> tuple[int, int]:
    slices_written = 0
    noise_dropped = 0

    # In dry_run mode, only preview freshly split raw sessions. Existing split backlog
    # must stay mutation-free: no LLM call, no cache changes, no retry-sidecar writes.
    if dry_run:
        for session_key, fragments in dry_run_fragments.items():
            try:
                promoted = _promote_fragments(promoter, fragments, config)
            except PromoteError as exc:
                # 警告文字會進 dream ledger／journald：例外訊息一律先去敏
                warnings.append(
                    f"{session_key}: {processing.sanitize_error_text(str(exc))}; "
                    f"session {session_key} left in split"
                )
                continue
            has_error = False
            for slice_ in promoted:
                errors = slice_frontmatter.validate(slice_.frontmatter, slice_.body)
                if errors:
                    warnings.append(
                        f"dry_run {session_key}: slice validation failed: {errors}; session {session_key} left in split"
                    )
                    has_error = True
                    break
            if has_error:
                continue
            for slice_ in promoted:
                verdict = classify_noise(slice_.frontmatter, slice_.body, doc_corpus=doc_corpus)
                if verdict.is_noise:
                    noise_dropped += 1
                    LOGGER.info("atomize: dropped noise slice %s:%s (%s)", session_key, slice_.slice_id, verdict.reason)
                    continue
                slices_written += 1
        return slices_written, noise_dropped

    events = processing.fold_events(memory_root)
    for session_key, event in events.items():
        state = str(event.get("state", ""))
        agent, _, session = session_key.partition(":")
        if not all(is_safe_path_component(value) for value in (agent, session)):
            warnings.append(f"session {session_key}: unsafe processing ledger session key; skipped")
            continue
        frag_dir_glob = sorted((memory_root / "inbox" / "_slices").rglob(f"{agent}__{session}__*.md"))
        if state in {"promoted", "no-findings"}:
            cache_key = event.get("cache_key")
            _clear_cache_key(memory_root, cache_key if isinstance(cache_key, str) else None)
            _clear_retry_counter(memory_root, cache_key if isinstance(cache_key, str) else None)
            chunk_cache_keys = event.get("chunk_cache_keys")
            if isinstance(chunk_cache_keys, list):
                for chunk_cache_key in chunk_cache_keys:
                    if isinstance(chunk_cache_key, str):
                        _clear_cache_key(memory_root, chunk_cache_key)
            else:
                for residual in _residual_cache_keys(memory_root, session_key):
                    _clear_cache_key(memory_root, residual)
            if frag_dir_glob:
                _archive_fragments(memory_root, frag_dir_glob, now)
            continue
        if state != "split":
            continue
        if not frag_dir_glob:
            warnings.append(f"session {session_key}: split state has no fragment files; skipped")
            continue

        # Phase 1: Read all fragments and build candidate slices
        fragments: list[tuple[Path, Fragment]] = []
        has_error = False
        for frag_path in frag_dir_glob:
            fragment = _read_fragment(frag_path)
            if fragment is None:
                warnings.append(f"{frag_path}: unreadable fragment; session {session_key} skipped")
                has_error = True
                break
            fragments.append((frag_path, fragment))

        if has_error:
            continue

        try:
            promoted = _promote_fragments(promoter, [fragment for _, fragment in fragments], config)
        except PromoteError as exc:
            note, parked = _handle_promote_failure(
                memory_root,
                promoter,
                [fragment for _, fragment in fragments],
                exc,
                session_key=session_key,
                now=now,
                config_hash=config_hash,
            )
            outcome = "parked" if parked else "left in split"
            warnings.append(
                f"{session_key}: {processing.sanitize_error_text(str(exc))}; "
                f"session {session_key} {outcome}{note}"
            )
            continue

        cache_key = None
        if isinstance(promoter, LLMPromoter):
            cache_key = promoter.cache_key_for_fragments([fragment for _, fragment in fragments])

        if not promoted:
            if isinstance(promoter, LLMPromoter) and promoter.last_disposition == "no_findings":
                processing.append_state(
                    memory_root,
                    session_key=session_key,
                    state="no-findings",
                    now=now,
                    config_hash=config_hash,
                    slices=0,
                    accepted_slices=0,
                    no_findings_reasons=list(promoter.no_findings_reasons),
                    chunk_cache_keys=list(promoter.last_chunk_cache_keys),
                    source_inbox_hash=event.get("source_inbox_hash"),
                    cache_key=cache_key,
                    **_promoter_metadata(promoter),
                )
                _archive_fragments(memory_root, [frag_path for frag_path, _ in fragments], now)
                promoter.clear_last_chunk_caches()
                _clear_cache_key(memory_root, cache_key)
                _clear_retry_counter(memory_root, cache_key)
                continue

            exc = PromoteError(
                "promoter returned no accepted slices without explicit no_findings",
                category="invalid_output",
            )
            if isinstance(promoter, LLMPromoter):
                promoter.clear_last_chunk_caches()
            note, parked = _handle_promote_failure(
                memory_root,
                promoter,
                [fragment for _, fragment in fragments],
                exc,
                session_key=session_key,
                now=now,
                config_hash=config_hash,
            )
            outcome = "parked" if parked else "left in split"
            warnings.append(f"{session_key}: {exc}; session {session_key} {outcome}{note}")
            continue

        promoted = _attach_unambiguous_supersedes(memory_root, promoted)

        # Phase 2: Validate all slices before any writes
        for slice_ in promoted:
            errors = slice_frontmatter.validate(slice_.frontmatter, slice_.body)
            if errors:
                warnings.append(
                    f"session {session_key}: slice validation failed: {errors}; session {session_key} left in split"
                )
                has_error = True
                break

        if has_error:
            continue

        # Phase 3: All validated - now write slices, relations, and archive
        fragments_by_index = {
            fragment.fragment_index: (frag_path, fragment) for frag_path, fragment in fragments
        }
        fragments_by_ref = {
            frag_path.stem: (frag_path, fragment) for frag_path, fragment in fragments
        }
        relation_error = _has_unsupported_semantic_relations(promoted)
        if relation_error is not None:
            warnings.append(f"session {session_key}: {relation_error}; session {session_key} left in split")
            continue
        try:
            prepared_writes = _prepare_slice_writes(
                promoted,
                fragments_by_index=fragments_by_index,
                fragments_by_ref=fragments_by_ref,
            )
        except KeyError as exc:
            warnings.append(f"session {session_key}: {exc}; session {session_key} left in split")
            continue
        accepted_writes: list[
            tuple[slice_frontmatter.Slice, list[tuple[Path, Fragment]]]
        ] = []
        for slice_, referenced_fragments in prepared_writes:
            verdict = classify_noise(slice_.frontmatter, slice_.body, doc_corpus=doc_corpus)
            if verdict.is_noise:
                noise_dropped += 1
                LOGGER.info("atomize: dropped noise slice %s:%s (%s)", session_key, slice_.slice_id, verdict.reason)
                continue
            accepted_writes.append((slice_, referenced_fragments))

        if not accepted_writes:
            exc = PromoteError(
                "all proposed findings were rejected by deterministic validation",
                category="invalid_output",
            )
            if isinstance(promoter, LLMPromoter):
                promoter.clear_last_chunk_caches()
            note, parked = _handle_promote_failure(
                memory_root,
                promoter,
                [fragment for _, fragment in fragments],
                exc,
                session_key=session_key,
                now=now,
                config_hash=config_hash,
            )
            outcome = "parked" if parked else "left in split"
            warnings.append(f"{session_key}: {exc}; session {session_key} {outcome}{note}")
            continue

        title_to_slice_id = {
            slice_.title: slice_.slice_id
            for slice_, _ in accepted_writes
            if slice_.title is not None
        }
        publication_id = _publish_session(
            memory_root,
            session_key=session_key,
            accepted_writes=accepted_writes,
            title_to_slice_id=title_to_slice_id,
            now=now,
            config_hash=config_hash,
            warnings=warnings,
        )

        processing.append_state(
            memory_root,
            session_key=session_key,
            state="promoted",
            now=now,
            config_hash=config_hash,
            slices=len(accepted_writes),
            accepted_slices=len(accepted_writes),
            cache_key=cache_key,
            publication_id=publication_id,
            source_inbox_hash=event.get("source_inbox_hash"),
            chunk_cache_keys=(
                list(promoter.last_chunk_cache_keys)
                if isinstance(promoter, LLMPromoter)
                else []
            ),
            **_promoter_metadata(promoter),
        )
        _append_publication_event(
            memory_root,
            {
                "event": "committed",
                "publication_id": publication_id,
                "session_key": session_key,
                "accepted_slices": len(accepted_writes),
                "ts": now,
            },
        )
        slices_written += len(accepted_writes)
        _archive_fragments(memory_root, [frag_path for frag_path, _ in fragments], now)
        if isinstance(promoter, LLMPromoter):
            promoter.clear_last_chunk_caches()
        _clear_cache_key(memory_root, cache_key)
        _clear_retry_counter(memory_root, cache_key)
    return slices_written, noise_dropped


def run(memory_root: Path, *, config: AtomizerConfig, config_hash: str, now: str,
        dry_run: bool = False, promoter: Promoter | None = None,
        doc_corpus: "DocCorpus | None" = None) -> dict[str, Any]:
    promoter = promoter or IdentityPromoter()
    warnings: list[str] = []
    split, dry_run_fragments = _split_pass(memory_root, config, config_hash, now, dry_run, warnings)
    slices, noise_dropped = _promote_pass(memory_root, config, config_hash, now, dry_run, promoter, warnings, dry_run_fragments, doc_corpus)
    return {
        "summary": {"split_sessions": split, "slices": slices, "skipped": len(warnings),
                    "noise_dropped": noise_dropped,
                    "config_hash": config_hash, "dry_run": dry_run},
        "warnings": warnings,
    }
