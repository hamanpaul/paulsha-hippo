"""A/B task-memory R75 mode; all search and JEV calls are deterministic fakes."""

from __future__ import annotations

import builtins
import hashlib
import json
import time
from pathlib import Path

import pytest

from paulsha_hippo import h2_bench, task_memory_rerank
from paulsha_hippo.importer.config import ProjectConfig, ProjectsConfig
from paulsha_hippo.task_memory_provider import TaskMemoryProvider, TaskMemoryProviderError, _DeadlineExpired, deadline

FIXTURE = Path(__file__).parent / "fixtures" / "task_memory" / "cortex-note-fetch-request.json"
REPO = "hamanpaul/paulsha-cortex"
SLUG = "github.com/hamanpaul/paulsha-cortex"


def _request(repo: str = REPO, *, task_id: str = "task-ab-fixture", intent: str | None = None) -> dict:
    envelope = json.loads(FIXTURE.read_text(encoding="utf-8"))
    envelope["task_id"] = task_id
    envelope["intent"] = intent or envelope["intent"]
    envelope["project"] = repo
    envelope["delivery"]["mode"] = "inline"
    envelope["delivery"]["host_scope"]["repo"] = repo
    envelope["delivery"]["host_scope"]["read_scope"]["repo"] = repo
    return envelope


def _notes(tmp_path: Path, count: int = 12) -> list[dict]:
    hits = []
    for index in range(1, count + 1):
        note_id = f"note-{index}"
        path = tmp_path / "knowledge" / f"{note_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nslice_id: {note_id}\ntitle: Note {index}\ncaptured_at: '2026-09-25T12:00:00Z'\n---\n"
            f"Note {index} contains a specific engineering constraint.\n",
            encoding="utf-8",
        )
        hits.append({"slice_id": note_id, "project": SLUG, "title": f"Note {index}",
                     "path": str(path), "captured_at": "2026-09-25T12:00:00Z"})
    return hits


class FakeSearch:
    def __init__(self, hits: list[dict], *, fail_limit: int | None = None, wrong_scope_at_12: bool = False):
        self.hits = hits
        self.calls: list[int] = []
        self.fail_limit = fail_limit
        self.wrong_scope_at_12 = wrong_scope_at_12

    def __call__(self, _root, _query, *, project, limit, include_decayed):
        self.calls.append(limit)
        if limit == self.fail_limit:
            raise RuntimeError("search failed")
        results = [dict(hit, project=project) for hit in self.hits[:limit]]
        if limit == 12 and self.wrong_scope_at_12 and results:
            results[-1]["project"] = "github.com/acme/private"
        return results


class FakeJev:
    def __init__(self, scores: dict[str, float], *, failure: str | None = None):
        self.scores = scores
        self.failure = failure
        self.calls: list[str] = []

    def judge(self, request):
        title = request["state"]["candidate_passage"]["title"]
        self.calls.append(title)
        if self.failure == "error":
            raise h2_bench.BenchError("transport", "fake transport error")
        if self.failure == "timeout":
            raise TimeoutError("fake request timeout")
        return ({"answers": {"relevant": {"noul": self.scores.get(title, 0.05)}},
                 "usage": {"input_tokens": 1000}}, 1, 1)


def _env(tmp_path: Path, *, seed: str | None = None, **extra) -> dict[str, str]:
    deny = tmp_path / "deny.txt"
    deny.write_text("acme\n", encoding="utf-8")
    env = {"HIPPO_TASK_MEMORY_RERANK": "ab", "HIPPO_TASK_MEMORY_RERANK_DENY_TERMS": str(deny),
           "TYPESAFE_API_KEY": "test-key"}
    if seed is not None:
        env[task_memory_rerank.AB_SEED_ENV] = seed
    env.update(extra)
    return env


def _provider(tmp_path: Path, search: FakeSearch, env: dict[str, str], jev: FakeJev | None = None,
              *, repo_remote: str = SLUG, defer_shadow: bool = False) -> TaskMemoryProvider:
    projects = ProjectsConfig(projects=(ProjectConfig(slug=SLUG, remotes=(repo_remote,)),))

    def jev_factory(**_kwargs):
        if jev is None:
            raise AssertionError("JEV factory must not be called")
        return jev

    return TaskMemoryProvider(memory_root=tmp_path, projects=projects, search_fn=search, environ=env,
                              rerank_jev_factory=jev_factory, defer_shadow=defer_shadow)


def _arm_task_id(arm: str, seed: str | None = None) -> str:
    env = {task_memory_rerank.AB_SEED_ENV: seed} if seed else {}
    return next(f"task-ab-{index}" for index in range(1000)
                if task_memory_rerank.ab_arm(f"task-ab-{index}", env) == arm)


def _receipts(tmp_path: Path) -> list[dict]:
    path = task_memory_rerank.receipt_path(tmp_path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_ab_grouping_is_stable_and_seed_controls_group():
    task_id = "same-work-item"
    default_seed = task_memory_rerank.DEFAULT_AB_SEED
    first = task_memory_rerank.ab_arm(task_id, {})
    assert task_memory_rerank.ab_arm(task_id, {}) == first
    expected_byte = hashlib.sha256(f"{default_seed}:{task_id}".encode()).digest()[0]
    assert first == ("R75" if expected_byte % 2 == 0 else "A")

    changed_seed = next(f"alternate-{index}" for index in range(1000)
                        if task_memory_rerank.ab_arm(task_id, {task_memory_rerank.AB_SEED_ENV: f"alternate-{index}"}) != first)
    assert task_memory_rerank.ab_arm(task_id, {task_memory_rerank.AB_SEED_ENV: changed_seed}) != first


def test_mode_accepts_ab_case_insensitively_and_unknown_values_are_off():
    assert task_memory_rerank.mode({task_memory_rerank.RERANK_ENV: " AB "}) == "ab"
    assert task_memory_rerank.mode({task_memory_rerank.RERANK_ENV: "Shadow"}) == "shadow"
    assert task_memory_rerank.mode({task_memory_rerank.RERANK_ENV: "anything-else"}) == "off"


def test_a_arm_matches_off_and_records_ab_receipt(tmp_path):
    hits = _notes(tmp_path)
    request = _request(task_id=_arm_task_id("A"))
    off = _provider(tmp_path, FakeSearch(hits), {}).provide(request)
    jev = FakeJev({"Note 9": 0.99, "Note 5": 0.95, "Note 1": 0.1})
    search = FakeSearch(hits)
    result = _provider(tmp_path, search, _env(tmp_path), jev).provide(request)

    assert result == off
    assert json.dumps(result, ensure_ascii=False, separators=(",", ":")) == json.dumps(
        off, ensure_ascii=False, separators=(",", ":"))
    assert result["delivery"]["manifest"]["sha256"] == off["delivery"]["manifest"]["sha256"]
    assert search.calls == [3, 12]
    receipt = _receipts(tmp_path)[0]
    assert receipt["arm"] == receipt["applied_arm"] == "A"
    assert receipt["production_a_top3"] == ["note-1", "note-2", "note-3"]
    assert receipt["delivery_manifest_sha256"] == off["delivery"]["manifest"]["sha256"]
    assert receipt["shadow_r75_top3"]


def test_a_arm_keeps_cli_shadow_deferred(tmp_path):
    hits = _notes(tmp_path)
    request = _request(task_id=_arm_task_id("A"))
    off = _provider(tmp_path, FakeSearch(hits), {}).provide(request)
    search = FakeSearch(hits)
    provider = _provider(tmp_path, search, _env(tmp_path), FakeJev({}), defer_shadow=True)
    result = provider.provide(request)

    assert result == off
    assert search.calls == [3]
    assert provider.pending_shadow["arm"] == "A"
    assert provider.pending_shadow["payload"]["delivery"]["manifest"]["sha256"] == off["delivery"]["manifest"]["sha256"]


def test_r75_arm_delivers_reranked_notes_and_receipt(tmp_path):
    hits = _notes(tmp_path)
    request = _request(task_id=_arm_task_id("R75"))
    off = _provider(tmp_path, FakeSearch(hits), {}).provide(request)
    jev = FakeJev({"Note 9": 0.99, "Note 5": 0.95, "Note 1": 0.1})
    search = FakeSearch(hits)
    result = _provider(tmp_path, search, _env(tmp_path), jev).provide(request)

    receipt = _receipts(tmp_path)[0]
    delivered_ids = [candidate["note_id"] for candidate in result["candidates"]]
    assert receipt["arm"] == receipt["applied_arm"] == "R75"
    assert receipt["fallback"] is None
    assert receipt["production_a_top3"] == ["note-1", "note-2", "note-3"]
    assert delivered_ids == receipt["shadow_r75_top3"]
    assert delivered_ids != [candidate["note_id"] for candidate in off["candidates"]]
    assert receipt["delivery_manifest_sha256"] == result["delivery"]["manifest"]["sha256"]
    assert receipt["wall_ms"] >= 0 and float(receipt["cost_usd"]) > 0
    assert search.calls == [3, 12]


def test_r75_validate_failure_falls_back_to_a(tmp_path, monkeypatch):
    """R75 的 payload 建得起來但 manifest／validate 失敗時，也要回退 A，不得變成 provider-error。"""
    from paulsha_hippo import task_memory_provider as provider_module

    hits = _notes(tmp_path)
    request = _request(task_id=_arm_task_id("R75"))
    off = _provider(tmp_path, FakeSearch(hits), {}).provide(request)
    a_ids = [candidate["note_id"] for candidate in off["candidates"]]
    original_validate = provider_module.validate_task_memory_payload

    def reject_r75_payload(payload):
        if [candidate["note_id"] for candidate in payload["candidates"]] != a_ids:
            raise ValueError("fake validation failure for the R75 set")
        return original_validate(payload)

    monkeypatch.setattr(provider_module, "validate_task_memory_payload", reject_r75_payload)
    jev = FakeJev({"Note 9": 0.99, "Note 5": 0.95, "Note 1": 0.1})
    result = _provider(tmp_path, FakeSearch(hits), _env(tmp_path), jev).provide(request)
    receipt = _receipts(tmp_path)[0]

    assert result == off
    assert receipt["arm"] == "R75" and receipt["applied_arm"] == "A"
    assert receipt["fallback"] == "payload-error"
    assert receipt["shadow_r75_top3"] and receipt["shadow_r75_top3"] != a_ids
    assert receipt["delivery_manifest_sha256"] == off["delivery"]["manifest"]["sha256"]


@pytest.mark.parametrize(
    ("case", "expected"),
    [("jev-error", "error:transport"), ("jev-timeout", "jev-timeout"), ("deny", "deny"),
     ("no-api-key", "no-api-key"), ("search-error", "search-error"), ("scope-hit", "scope-mismatch")],
)
def test_r75_failures_fall_back_to_a_with_receipt(tmp_path, case, expected):
    hits = _notes(tmp_path)
    request = _request(task_id=_arm_task_id("R75"),
                       intent="ACME private constraint" if case == "deny" else None)
    env = _env(tmp_path)
    jev = FakeJev({"Note 9": 0.99}, failure="error" if case == "jev-error" else
                  "timeout" if case == "jev-timeout" else None)
    search = FakeSearch(hits, fail_limit=12 if case == "search-error" else None,
                        wrong_scope_at_12=case == "scope-hit")
    if case == "no-api-key":
        env.pop("TYPESAFE_API_KEY")
    result = _provider(tmp_path, search, env, None if case == "no-api-key" else jev).provide(request)
    off = _provider(tmp_path, FakeSearch(hits), {}).provide(request)
    receipt = _receipts(tmp_path)[0]

    assert result == off
    assert receipt["arm"] == "R75" and receipt["applied_arm"] == "A"
    assert receipt["fallback"] == expected
    assert receipt["delivery_manifest_sha256"] == off["delivery"]["manifest"]["sha256"]
    if case == "no-api-key":
        assert search.calls == [3]


def test_r75_without_deny_terms_falls_back_before_scoring(tmp_path):
    hits = _notes(tmp_path)
    request = _request(task_id=_arm_task_id("R75"))
    env = _env(tmp_path)
    env.pop(task_memory_rerank.DENY_TERMS_ENV)
    search = FakeSearch(hits)
    result = _provider(tmp_path, search, env, None).provide(request)
    off = _provider(tmp_path, FakeSearch(hits), {}).provide(request)
    receipt = _receipts(tmp_path)[0]
    assert result == off
    assert search.calls == [3]
    assert receipt["fallback"] == "no-deny-terms" and receipt["applied_arm"] == "A"


def test_out_of_scope_task_is_off_with_fallback_receipt(tmp_path):
    hits = _notes(tmp_path)
    request = _request(repo="acme/demo")
    projects = ProjectsConfig(projects=(ProjectConfig(slug=SLUG, remotes=("acme/demo",)),))
    off = TaskMemoryProvider(memory_root=tmp_path, projects=projects, search_fn=FakeSearch(hits), environ={}).provide(request)
    search = FakeSearch(hits)
    env = _env(tmp_path)
    result = TaskMemoryProvider(memory_root=tmp_path, projects=projects, search_fn=search, environ=env).provide(request)
    receipt = _receipts(tmp_path)[0]
    assert result == off
    assert search.calls == [3]
    assert receipt["fallback"] == "out-of-scope"
    assert receipt["applied_arm"] == "A"


def test_off_mode_does_not_import_task_memory_rerank(tmp_path, monkeypatch):
    search = FakeSearch(_notes(tmp_path))
    provider = _provider(tmp_path, search, {})
    original_import = builtins.__import__
    attempted = []

    def reject_rerank_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "paulsha_hippo" and "task_memory_rerank" in fromlist:
            attempted.append(name)
            raise AssertionError("off mode imported task_memory_rerank")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", reject_rerank_import)
    provider.provide(_request())
    assert attempted == []


def test_provider_deadline_from_sync_r75_is_not_swallowed(tmp_path):
    class DeadlineSearch(FakeSearch):
        def __call__(self, root, query, *, project, limit, include_decayed):
            self.calls.append(limit)
            if limit == 12:
                raise _DeadlineExpired()
            if limit == self.fail_limit:
                raise RuntimeError("search failed")
            return [dict(hit, project=project) for hit in self.hits[:limit]]

    search = DeadlineSearch(_notes(tmp_path))
    request = _request(task_id=_arm_task_id("R75"))
    provider = _provider(tmp_path, search, _env(tmp_path), FakeJev({}))
    with pytest.raises(_DeadlineExpired):
        provider.provide(request)
    assert search.calls == [3, 12]


def test_sigalrm_provider_deadline_returns_timeout_not_a_fallback(tmp_path):
    class SlowSearch(FakeSearch):
        def __call__(self, root, query, *, project, limit, include_decayed):
            self.calls.append(limit)
            if limit == 12:
                time.sleep(0.2)
            return [dict(hit, project=project) for hit in self.hits[:limit]]

    provider = _provider(tmp_path, SlowSearch(_notes(tmp_path)), _env(tmp_path), FakeJev({}))
    request = _request(task_id=_arm_task_id("R75"))
    with pytest.raises(TaskMemoryProviderError) as raised:
        with deadline(seconds=0.05):
            provider.provide(request)
    assert raised.value.code == "timeout"
