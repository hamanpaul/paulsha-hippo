"""#151：memory_root 實際路徑落在持續同步／被掃描樹內的偵測與警示。

所有目錄皆為 tmp_path 合成；不碰真實 memory root 或 vault。
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from paulsha_hippo import cli, ops, storage
from paulsha_hippo.dream.lock import acquire_dream_lock


@pytest.fixture(autouse=True)
def _clear_sync_env(monkeypatch):
    monkeypatch.delenv("HIPPO_SYNC_MARKERS", raising=False)
    monkeypatch.delenv("HIPPO_SYNC_ROOTS", raising=False)


def _vault(base: Path) -> Path:
    vault = base / "notes"
    (vault / ".obsidian").mkdir(parents=True)
    return vault


def _store(root: Path) -> Path:
    for name in ("archive", "runtime", "inbox", "knowledge"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def test_memory_root_inside_obsidian_vault_is_flagged(tmp_path):
    vault = _vault(tmp_path)
    memory = _store(vault / "claw" / "memory")

    report = storage.check_placement(memory)

    assert [e.surface for e in report.warnings] == ["memory_root"]
    exposure = report.warnings[0]
    assert exposure.container.root == vault.resolve()
    assert exposure.container.marker == ".obsidian"
    assert exposure.container.kind == "Obsidian vault"
    assert report.infos == []


def test_symlinked_memory_root_is_resolved_before_checking(tmp_path):
    """#151 的實際佈局：~/.agents/memory 是指向 vault 內的 symlink。"""
    vault = _vault(tmp_path)
    real = _store(vault / "claw" / "memory")
    agents = tmp_path / "home" / ".agents"
    agents.mkdir(parents=True)
    link = agents / "memory"
    link.symlink_to(real, target_is_directory=True)

    report = storage.check_placement(link)

    assert report.resolved_root == real.resolve()
    assert [e.surface for e in report.warnings] == ["memory_root"]
    assert report.warnings[0].path == link
    assert report.warnings[0].resolved == real.resolve()


def test_recommended_layout_only_borrows_knowledge_into_vault(tmp_path):
    """P1 佈局：store 在 vault 外，只有 knowledge 以 symlink 借回 vault。"""
    vault = _vault(tmp_path)
    vault_knowledge = vault / "claw" / "memory" / "knowledge"
    vault_knowledge.mkdir(parents=True)
    memory = tmp_path / "home" / ".agents" / "memory"
    for name in ("archive", "runtime", "inbox"):
        (memory / name).mkdir(parents=True)
    (memory / "knowledge").symlink_to(vault_knowledge, target_is_directory=True)

    report = storage.check_placement(memory)

    assert report.warnings == []
    assert [e.surface for e in report.infos] == ["knowledge"]
    assert report.infos[0].container.root == vault.resolve()


def test_heavy_subtree_relocated_into_vault_is_flagged(tmp_path):
    vault = _vault(tmp_path)
    moved_archive = vault / "archive"
    moved_archive.mkdir()
    memory = tmp_path / "home" / ".agents" / "memory"
    for name in ("runtime", "inbox", "knowledge"):
        (memory / name).mkdir(parents=True)
    (memory / "archive").symlink_to(moved_archive, target_is_directory=True)

    report = storage.check_placement(memory)

    assert [e.surface for e in report.warnings] == ["archive"]


def test_store_outside_any_sync_tree_has_no_findings(tmp_path):
    _vault(tmp_path)
    memory = _store(tmp_path / "home" / ".agents" / "memory")

    report = storage.check_placement(memory)

    assert report.warnings == []
    assert report.infos == []


def test_configured_sync_root_env_is_additive(tmp_path, monkeypatch):
    synced = tmp_path / "cloud-drive"
    memory = _store(synced / "memory")
    monkeypatch.setenv("HIPPO_SYNC_ROOTS", str(synced))

    report = storage.check_placement(memory)

    assert [e.surface for e in report.warnings] == ["memory_root"]
    assert report.warnings[0].container.kind == "configured sync root"
    assert report.warnings[0].container.root == synced.resolve()


def test_marker_env_overrides_defaults_and_none_disables(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    in_vault = _store(vault / "memory")
    custom = tmp_path / "custom"
    (custom / ".syncroot").mkdir(parents=True)
    in_custom = _store(custom / "memory")

    monkeypatch.setenv("HIPPO_SYNC_MARKERS", ".syncroot")
    assert storage.check_placement(in_vault).warnings == []
    flagged = storage.check_placement(in_custom).warnings
    assert [e.container.marker for e in flagged] == [".syncroot"]

    monkeypatch.setenv("HIPPO_SYNC_MARKERS", "none")
    assert storage.check_placement(in_vault).warnings == []
    assert storage.check_placement(in_custom).warnings == []


def test_explicit_arguments_override_env(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    memory = _store(vault / "memory")
    monkeypatch.setenv("HIPPO_SYNC_MARKERS", "none")

    report = storage.check_placement(memory, markers=(".obsidian",), roots=())

    assert [e.surface for e in report.warnings] == ["memory_root"]


def _run_doctor(memory: Path, tmp_path: Path, monkeypatch) -> str:
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    (proc / "stat").write_text("cpu  0 0 0 0\nbtime 1700000000\n", encoding="ascii")
    monkeypatch.setenv("HIPPO_MEMORY_ROOT", str(memory))
    monkeypatch.setenv("PSC_MEMORY_ROOT", str(memory))
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        ops.run_doctor(proc_root=proc)
    return buffer.getvalue()


def test_doctor_warns_when_memory_root_is_inside_vault(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    memory = _store(vault / "claw" / "memory")

    out = _run_doctor(memory, tmp_path, monkeypatch)

    lines = [line for line in out.splitlines() if "storage 位置" in line]
    assert lines, out
    assert any("⚠" in line and "Obsidian vault" in line and "memory_root" in line
               for line in lines), lines


def test_doctor_reports_ok_for_recommended_layout(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    vault_knowledge = vault / "claw" / "memory" / "knowledge"
    vault_knowledge.mkdir(parents=True)
    memory = tmp_path / "home" / ".agents" / "memory"
    for name in ("archive", "runtime", "inbox"):
        (memory / name).mkdir(parents=True)
    (memory / "knowledge").symlink_to(vault_knowledge, target_is_directory=True)

    out = _run_doctor(memory, tmp_path, monkeypatch)

    lines = [line for line in out.splitlines() if "storage 位置" in line]
    assert any("✓" in line for line in lines), lines
    assert not any("⚠" in line for line in lines), lines
    assert any("knowledge" in line for line in lines), lines


def test_dream_run_warns_on_stderr_at_startup(tmp_path, capsys):
    vault = _vault(tmp_path)
    memory = _store(vault / "claw" / "memory")
    handle = acquire_dream_lock(memory)  # 讓 dream run 在警示後即因持鎖而 skip
    assert handle is not None
    try:
        rc = cli.main(["dream", "run", "--memory-root", str(memory), "--dry-run"])
    finally:
        handle.close()

    captured = capsys.readouterr()
    assert rc == 0
    assert "dream lock held" in captured.out
    assert "storage" in captured.err and "Obsidian vault" in captured.err


def test_dream_run_is_silent_when_store_is_outside_sync_trees(tmp_path, capsys):
    memory = _store(tmp_path / "home" / ".agents" / "memory")
    handle = acquire_dream_lock(memory)
    assert handle is not None
    try:
        cli.main(["dream", "run", "--memory-root", str(memory), "--dry-run"])
    finally:
        handle.close()

    assert "storage" not in capsys.readouterr().err
