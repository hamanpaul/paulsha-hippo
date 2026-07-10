from __future__ import annotations

import hashlib
import json
import logging
import shutil
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
_LLM_PROMOTE_MAX_RETRIES = 5


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
_EVIDENCE_EXCERPT_MAX_CHARS = 2000


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
                  cache_key: str, error_text: str, now: str, config_hash: str) -> None:
    """parked 終態：證據落盤 → 淘汰毒快取＋sidecar（保留 split fragments）→ 記 ledger。

    ledger append 放最後當 commit point：中途 crash 只會多一次 bounded 重試（fail-open），
    不會留下「已 parked 但毒快取還在」的半套狀態。

    去敏是本函式的職責（單一 choke point）：error 與 last_output_excerpt 落盤前
    一律套用 policy secret redaction（fail-closed），caller 忘了先 sanitize 也不漏。
    """
    error_text = processing.sanitize_error_text(error_text)
    cache_path = _cache_path(memory_root, cache_key)
    excerpt = ""
    if cache_path is not None and cache_path.exists():
        try:
            excerpt = cache_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            excerpt = ""
    if excerpt:
        # redaction 先於截斷：先截斷可能把 token 斬半、令 pattern 失配而留下敏感前綴
        excerpt = processing.redact_secret_text(excerpt)[:_EVIDENCE_EXCERPT_MAX_CHARS]
    evidence = {
        "session_key": session_key,
        "failure_category": category,
        "attempts": attempts,
        "cache_key": cache_key,
        "error": error_text,
        "ts": now,
        "last_output_excerpt": excerpt,
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


def park_split_sessions(memory_root: Path, *, error_text: str, now: str,
                        config_hash: str,
                        category: str = "backend_unavailable") -> list[str]:
    """#15 失敗鏈：atomizer 初始化即失敗（config 無效／promoter 建構失敗）時，
    把 eligible（state == split）sessions 立即 park（含證據落盤）。

    spec 契約「config 無效立即 parked」——否則 pending session 卡在 split、
    無 failure category／evidence，timer 每輪重複整輪失敗。回傳被 park 的
    session keys（排序後，決定性）。
    """
    parked: list[str] = []
    for session_key, state in sorted(processing.fold_states(memory_root).items()):
        if state != "split":
            continue
        agent, _, session = session_key.partition(":")
        if not all(is_safe_path_component(value) for value in (agent, session)):
            continue
        _park_session(
            memory_root, session_key=session_key, category=category,
            attempts=0, cache_key="", error_text=error_text,
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
    """#15 失敗分類：backend_unavailable 立即 park；transient/invalid_output 記入單一
    attempts 預算（沿用 _LLM_PROMOTE_MAX_RETRIES），invalid_output 每次先淘汰毒快取，
    超限 park。回傳 (警告註記, 是否已 park)。非 LLM promoter 沿用既有 left-in-split。"""
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
    attempts = _read_attempts(counter)

    if category == "backend_unavailable":
        _park_session(
            memory_root, session_key=session_key, category=category,
            attempts=attempts, cache_key=cache_key, error_text=error_text,
            now=now, config_hash=config_hash,
        )
        return " (parked: backend_unavailable; 不重試，修復後 hippo requeue)", True

    attempts += 1
    if attempts > _LLM_PROMOTE_MAX_RETRIES:
        _park_session(
            memory_root, session_key=session_key, category=category,
            attempts=attempts, cache_key=cache_key, error_text=error_text,
            now=now, config_hash=config_hash,
        )
        return f" (parked: {category} after {attempts} attempts; cache evicted)", True

    counter.parent.mkdir(parents=True, exist_ok=True)
    counter.write_text(str(attempts), encoding="utf-8")
    if category == "invalid_output":
        promoter.clear_cache_for_fragments(fragments)
        return f" (cache cleared; retry {attempts}/{_LLM_PROMOTE_MAX_RETRIES})", False
    return f" (transient failure; retry {attempts}/{_LLM_PROMOTE_MAX_RETRIES})", False


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


def _has_unsupported_semantic_relations(promoted: list[slice_frontmatter.Slice]) -> str | None:
    for slice_ in promoted:
        for relation in slice_.relations:
            relation_type = relation.get("type") if isinstance(relation, Mapping) else None
            if relation_type not in {"relates_to", "mentions"}:
                return f"slice {slice_.slice_id} has unsupported semantic relation type: {relation_type!r}"
    return None


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
            raw_size = raw_path.stat().st_size
        except OSError:
            raw_size = None
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
        data, body = _parse_frontmatter(raw_path.read_text(encoding="utf-8"))
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
        if processing.state_of(memory_root, session_key) in {"split", "promoted", "parked"}:
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
        processing.append_state(memory_root, session_key=session_key, state="split",
                                now=now, config_hash=config_hash, fragments=len(bodies))
        archive = memory_root / "archive" / "sessions" / _month(captured_at, now) / f"{agent}__{session}.md"
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
        if state == "promoted":
            cache_key = event.get("cache_key")
            _clear_cache_key(memory_root, cache_key if isinstance(cache_key, str) else None)
            _clear_retry_counter(memory_root, cache_key if isinstance(cache_key, str) else None)
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
        title_to_slice_id = {
            slice_.title: slice_.slice_id for slice_ in promoted if slice_.title is not None
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
        for slice_, referenced_fragments in prepared_writes:
            verdict = classify_noise(slice_.frontmatter, slice_.body, doc_corpus=doc_corpus)
            if verdict.is_noise:
                noise_dropped += 1
                LOGGER.info("atomize: dropped noise slice %s:%s (%s)", session_key, slice_.slice_id, verdict.reason)
                continue
            knowledge_path = _knowledge_path_for(
                memory_root, sanitize_project_component(str(slice_.frontmatter["project"])), slice_.slice_id
            )
            _atomic_write(knowledge_path, slice_frontmatter.render(slice_))
            for frag_path, _ in referenced_fragments:
                relations.append_edge(
                    memory_root,
                    type="promoted_to",
                    frm=f"fragment:{frag_path.stem}",
                    to=f"slice:{slice_.slice_id}",
                    now=now,
                    config_hash=config_hash,
                )
            relations.append_edge(
                memory_root,
                type="distilled_from",
                frm=f"slice:{slice_.slice_id}",
                to=f"session:{session_key}",
                now=now,
                config_hash=config_hash,
            )
            _append_semantic_edges(
                memory_root,
                slice_=slice_,
                title_to_slice_id=title_to_slice_id,
                now=now,
                config_hash=config_hash,
                warnings=warnings,
            )
            slices_written += 1

        cache_key = None
        if isinstance(promoter, LLMPromoter):
            cache_key = promoter.cache_key_for_fragments([fragment for _, fragment in fragments])
        processing.append_state(
            memory_root,
            session_key=session_key,
            state="promoted",
            now=now,
            config_hash=config_hash,
            slices=len(promoted),
            cache_key=cache_key,
            **_promoter_metadata(promoter),
        )
        _archive_fragments(memory_root, [frag_path for frag_path, _ in fragments], now)
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
