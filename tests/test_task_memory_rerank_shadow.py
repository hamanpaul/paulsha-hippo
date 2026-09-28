"""#176：task-memory R75 重排序 shadow。只用合成筆記、fake search 與 fake JEV transport，不打真網路。"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest

from paulsha_hippo import h2_bench, task_memory_rerank
from paulsha_hippo.importer.config import ProjectConfig, ProjectsConfig
from paulsha_hippo.task_memory_provider import TaskMemoryProvider

FIXTURE = Path(__file__).parent / "fixtures" / "task_memory" / "cortex-note-fetch-request.json"
REPO = "hamanpaul/paulsha-cortex"
SLUG = "github.com/hamanpaul/paulsha-cortex"
SECRET_BODY = "unique-body-marker"


def _request(repo: str = REPO, mode: str = "inline") -> dict:
    envelope = json.loads(FIXTURE.read_text(encoding="utf-8"))
    envelope["project"] = repo
    envelope["delivery"]["mode"] = mode
    envelope["delivery"]["host_scope"]["repo"] = repo
    envelope["delivery"]["host_scope"]["read_scope"]["repo"] = repo
    return envelope


def _notes(tmp_path: Path, n: int = 12, private: tuple = ()) -> list:
    hits = []
    for i in range(1, n + 1):
        path = tmp_path / "knowledge" / f"note-{i}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = f"note {i} body {SECRET_BODY}" + (" mentions ACME internals" if i in private else "")
        path.write_text(f"---\nslice_id: note-{i}\ntitle: Note {i}\ncaptured_at: '2026-09-25T12:00:00Z'\n---\n{body}\n",
                        encoding="utf-8")
        hits.append({"slice_id": f"note-{i}", "project": SLUG, "title": f"Note {i}", "path": str(path),
                     "captured_at": "2026-09-25T12:00:00Z"})
    return hits


class Search:
    def __init__(self, hits, fail_on_limit=None):
        self.hits, self.calls, self.fail_on_limit = hits, [], fail_on_limit

    def __call__(self, _root, _query, *, project, limit, include_decayed):
        self.calls.append(limit)
        if limit == self.fail_on_limit:
            raise RuntimeError("boom")
        return [dict(h, project=project) for h in self.hits[:limit]]


class NoulTransport:
    def __init__(self, scores, fail=()):
        self.scores, self.fail, self.calls = scores, set(fail), []

    def __call__(self, url, headers, payload, timeout=None):
        title = payload["state"]["candidate_passage"]["title"]
        self.calls.append(title)
        if title in self.fail:
            return 500, {}, {}
        return 200, {"answers": {"relevant": {"noul": self.scores.get(title, 0.1)}},
                     "usage": {"input_tokens": 1000}}, {}


def _provider(tmp_path, search, environ, transport=None, repo_remote=SLUG):
    projects = ProjectsConfig(projects=(ProjectConfig(slug=SLUG, remotes=(repo_remote,)),))

    def factory(**kwargs):
        return h2_bench.JevClient(transport=transport, **kwargs)

    return TaskMemoryProvider(memory_root=tmp_path, projects=projects, search_fn=search, environ=environ,
                              rerank_jev_factory=factory)


def _env(tmp_path, **extra):
    terms = tmp_path / "deny.txt"
    terms.write_text("acme\n", encoding="utf-8")
    return {"HIPPO_TASK_MEMORY_RERANK": "shadow", "HIPPO_TASK_MEMORY_RERANK_DENY_TERMS": str(terms),
            "TYPESAFE_API_KEY": "k-test", **extra}


def _receipts(tmp_path):
    path = task_memory_rerank.receipt_path(tmp_path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_off_is_unchanged_and_writes_nothing(tmp_path):
    search = Search(_notes(tmp_path))
    payload = _provider(tmp_path, search, {}).provide(_request())
    assert search.calls == [3] and _receipts(tmp_path) == []
    assert [c["note_id"] for c in payload["candidates"]] == ["note-1", "note-2", "note-3"]


def test_shadow_keeps_output_identical_and_records_receipt(tmp_path):
    hits = _notes(tmp_path, private=(2,))
    off = _provider(tmp_path, Search(hits), {}).provide(_request())
    transport = NoulTransport({"Note 9": 0.95, "Note 5": 0.9, "Note 1": 0.2})
    search = Search(hits)
    shadow = _provider(tmp_path, search, _env(tmp_path), transport).provide(_request())
    assert shadow == off                                             # 正式輸出與 manifest 完全一致
    assert search.calls == [3, 12]
    receipt = _receipts(tmp_path)[0]
    assert receipt["schema"] == task_memory_rerank.RECEIPT_SCHEMA and receipt["fallback"] is None
    assert receipt["production_a_top3"] == ["note-1", "note-2", "note-3"]
    assert receipt["production_manifest_sha256"] == off["delivery"]["manifest"]["sha256"]
    assert receipt["shadow_r75_top3"][0] == "note-9"
    assert receipt["shadow_r75_top3"][1] == "note-2"                 # 不可送出的候選留在原位
    by_id = {c["note_id"]: c for c in receipt["candidates"]}
    assert by_id["note-2"]["egress"] == "excluded" and by_id["note-2"]["noul"] is None
    assert "Note 2" not in transport.calls and len(transport.calls) == 11
    text = json.dumps(receipt)
    assert SECRET_BODY not in text and "scoped parser memory" not in text   # 不存內容與原始 intent
    assert receipt["deny_terms"]["count"] == 1


def test_shadow_skips_non_cortex_projects(tmp_path):
    search = Search(_notes(tmp_path))
    provider = _provider(tmp_path, search, _env(tmp_path), NoulTransport({}), repo_remote="github.com/acme/demo")
    provider.provide(_request(repo="acme/demo"))
    assert search.calls == [3] and _receipts(tmp_path) == []


@pytest.mark.parametrize("drop,expect", [("HIPPO_TASK_MEMORY_RERANK_DENY_TERMS", "no-deny-terms"),
                                         ("TYPESAFE_API_KEY", "no-api-key")])
def test_shadow_without_policy_or_key_never_sends(tmp_path, drop, expect):
    env = _env(tmp_path)
    env.pop(drop)
    transport = NoulTransport({})
    search = Search(_notes(tmp_path))
    _provider(tmp_path, search, env, transport).provide(_request())
    assert _receipts(tmp_path)[0]["fallback"] == expect and transport.calls == [] and search.calls == [3]


def test_shadow_failures_never_change_output(tmp_path):
    hits = _notes(tmp_path)
    off = _provider(tmp_path, Search(hits), {}).provide(_request())
    out = _provider(tmp_path, Search(hits), _env(tmp_path), NoulTransport({}, fail={"Note 4"})).provide(_request())
    assert out == off and _receipts(tmp_path)[-1]["fallback"] == "error:transport"
    out = _provider(tmp_path, Search(hits, fail_on_limit=12), _env(tmp_path), NoulTransport({})).provide(_request())
    assert out == off and _receipts(tmp_path)[-1]["fallback"] == "search-error"
    assert copy.deepcopy(out) == off


def test_deferred_mode_records_pending_job_without_extra_work(tmp_path):
    search = Search(_notes(tmp_path))
    transport = NoulTransport({})
    provider = _provider(tmp_path, search, _env(tmp_path), transport)
    provider.defer_shadow = True
    out = provider.provide(_request())
    assert search.calls == [3] and transport.calls == [] and _receipts(tmp_path) == []
    job = provider.pending_shadow
    assert job["project_slug"] == SLUG and job["request"]["task_id"] == "task-857-fixture"
    assert [c["note_id"] for c in job["payload"]["candidates"]] == [c["note_id"] for c in out["candidates"]]
    assert job["payload"]["delivery"]["manifest"]["sha256"] == out["delivery"]["manifest"]["sha256"]
    text = json.dumps(job)
    assert SECRET_BODY not in text and len(text) < 4096            # 不含內容、寫入 pipe 不會阻塞


def test_spawn_detached_uses_private_job_file_and_new_session(tmp_path):
    seen = {}

    def popen(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        job_path = Path(argv[argv.index("--job") + 1])
        seen["mode"] = oct(job_path.stat().st_mode & 0o777)
        seen["job"] = json.loads(job_path.read_text(encoding="utf-8"))

    big = {"request": {"task_id": "t", "project": "x" * 100_000}, "project_slug": SLUG, "payload": {}}
    task_memory_rerank.spawn_detached(tmp_path, big, popen=popen)
    assert seen["argv"][1:3] == ["-m", "paulsha_hippo.task_memory_rerank"]
    assert seen["kwargs"]["start_new_session"] is True and seen["mode"] == "0o600"
    assert seen["job"]["project_slug"] == SLUG                       # 大 job 也不經 pipe，不會阻塞

    def failing(argv, **kwargs):
        raise OSError("no fork")

    before = set(task_memory_rerank.pending_dir(tmp_path).glob("job-*.json"))
    with pytest.raises(OSError):
        task_memory_rerank.spawn_detached(tmp_path, big, popen=failing)
    assert set(task_memory_rerank.pending_dir(tmp_path).glob("job-*.json")) == before   # 起行程失敗時刪掉 job 檔


def test_background_main_reads_and_deletes_job(tmp_path, monkeypatch):
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps({"request": {}, "project_slug": SLUG, "payload": {}}), encoding="utf-8")
    ran = []
    monkeypatch.setattr(task_memory_rerank, "run_deferred", lambda root, job: ran.append(job["project_slug"]))
    assert task_memory_rerank._main(["--memory-root", str(tmp_path), "--job", str(job_path)]) == 0
    assert ran == [SLUG] and not job_path.exists()


def test_pending_job_is_reset_between_calls(tmp_path):
    provider = _provider(tmp_path, Search(_notes(tmp_path)), _env(tmp_path), NoulTransport({}))
    provider.defer_shadow = True
    provider.provide(_request())
    assert provider.pending_shadow is not None
    provider.environ = {}
    provider.provide(_request())
    assert provider.pending_shadow is None


def test_cli_writes_output_before_spawning_shadow_and_ignores_spawn_errors(monkeypatch):
    from paulsha_hippo import cli

    order = []
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdin", io.BytesIO(b'{"schema_version":"1","task_id":"t"}'))
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", io.StringIO())

    def provide(self, request):
        assert self.defer_shadow is True
        self.pending_shadow = {"request": request, "project_slug": SLUG, "payload": {"ok": 1}}
        return {"ok": 1}

    def spawn(_root, job):
        order.append(("spawn", stdout.getvalue()))
        raise OSError("no fork")

    monkeypatch.setattr("paulsha_hippo.task_memory_provider.TaskMemoryProvider.provide", provide)
    monkeypatch.setattr(task_memory_rerank, "spawn_detached", spawn)
    assert cli.main(["task-memory", "provide", "--memory-root", "/tmp/hippo-memory"]) == 0
    assert order == [("spawn", '{"ok":1}\n')]


def test_shadow_requires_public_cortex_project_slug(tmp_path):
    hits = _notes(tmp_path)
    projects = ProjectsConfig(projects=(ProjectConfig(slug="private-proj", remotes=(SLUG,)),))
    search = Search([dict(h, project="private-proj") for h in hits])
    transport = NoulTransport({})
    provider = TaskMemoryProvider(memory_root=tmp_path, projects=projects, search_fn=search, environ=_env(tmp_path),
                                  rerank_jev_factory=lambda **kw: h2_bench.JevClient(transport=transport, **kw))
    provider.provide(_request())
    assert search.calls == [3] and transport.calls == [] and _receipts(tmp_path) == []


def test_deadline_expiry_during_sync_shadow_is_not_swallowed(tmp_path):
    from paulsha_hippo.task_memory_provider import _DeadlineExpired

    class DeadlineSearch(Search):
        def __call__(self, *args, **kwargs):
            if kwargs["limit"] == 12:
                raise _DeadlineExpired()
            return super().__call__(*args, **kwargs)

    provider = _provider(tmp_path, DeadlineSearch(_notes(tmp_path)), _env(tmp_path), NoulTransport({}))
    with pytest.raises(TimeoutError):
        provider.provide(_request())
