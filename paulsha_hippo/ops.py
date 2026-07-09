"""hippo 運維命令：init / doctor / install hooks / install service / dream supervise。

quickstart 面（spec §5.2/§5.5/§5.6）。全部 stdlib；systemd 偵測失敗一律走
fallback 指引而非硬錯（G3「先驗證再選路」）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from paulsha_hippo import paths

_PKG_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_ROOT.parent

_BACKENDS = ("claude-headless", "openai-compatible", "custom-argv")


# ---------------------------------------------------------------- init

def _write_if_absent(path: Path, content: str, *, force: bool = False) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def run_init(*, memory_root: str | None, backend: str, base_url: str | None,
             api_key_env: str | None, model: str | None, assume_yes: bool) -> int:
    """產生 ~/.config/paulsha-hippo/config.yaml 與 atomizer override（backend preset）。"""
    if backend not in _BACKENDS:
        print(f"init: 不支援的 backend: {backend}（可選 {', '.join(_BACKENDS)}）", file=sys.stderr)
        return 2
    root = memory_root or str(paths.memory_root())

    cfg_dir = paths.hippo_config_root()
    cfg = cfg_dir / "config.yaml"
    wrote_cfg = _write_if_absent(
        cfg,
        (
            f"memory_root: {root}\n"
            "distiller:\n"
            f"  backend: {backend}\n"
            + (f"  base_url: {base_url}\n" if base_url else "")
            + (f"  api_key_env: {api_key_env}\n" if api_key_env else "")
            + (f"  model: {model}\n" if model else "")
        ),
        force=assume_yes and not cfg.exists(),
    )

    # backend preset → paulshaclaw 相容 atomizer override（load_config 既有掛點）
    override = paths.config_path("atomizer.override.yaml")
    if backend == "claude-headless":
        override_body = (
            "schema_version: \"1\"\n"
            "agent_exec:\n"
            "  command:\n    - claude\n    - -p\n"
            + (f"  model: {model}\n" if model else "")
        )
    elif backend == "openai-compatible":
        if not base_url:
            print("init: openai-compatible 需要 --base-url", file=sys.stderr)
            return 2
        override_body = (
            "schema_version: \"1\"\n"
            "agent_exec:\n"
            "  backend: openai-compatible\n"
            f"  base_url: {base_url}\n"
            + (f"  api_key_env: {api_key_env}\n" if api_key_env else "")
            + (f"  model: {model}\n" if model else "")
        )
    else:  # custom-argv：不動 override（沿 atomizer.yaml 或既有 override）
        override_body = None
    wrote_override = False
    if override_body is not None:
        wrote_override = _write_if_absent(override, override_body)

    print(f"memory_root: {root}")
    print(f"distiller backend: {backend}")
    print(f"config: {cfg}{'（既存，未覆寫）' if not wrote_cfg and cfg.exists() else ''}")
    if override_body is not None:
        print(f"atomizer override: {override}{'（既存，未覆寫）' if not wrote_override else ''}")
    print("下一步：hippo install hooks && hippo install service --enable")
    return 0


# ---------------------------------------------------------------- doctor

def run_doctor() -> int:
    report = paths.resolution_report()
    failed = False
    print("# hippo doctor")
    for key, value in report.items():
        if key == "conflict":
            continue
        print(f"- {key}: {value}")
    if "conflict" in report:
        print(f"FAIL 雙 root 不一致：{report['conflict']}", file=sys.stderr)
        failed = True

    memory_root = paths.memory_root()
    hooks_dir = memory_root / "hooks"
    print(f"- hooks 部署：{'✓ ' + str(hooks_dir) if hooks_dir.is_dir() else '未部署（hippo install hooks）'}")

    if _systemd_user_available():
        state = subprocess.run(
            ["systemctl", "--user", "is-active", "paulsha-hippo-dream.timer"],
            capture_output=True, text=True,
        ).stdout.strip()
        print(f"- dream timer：{state or 'unknown'}")
    else:
        print("- systemd --user 不可用（fallback：hippo dream supervise）")

    agent = shutil.which("claude")
    print(f"- claude CLI：{'✓ ' + agent if agent else '未找到（claude-headless 檔位需要）'}")
    return 1 if failed else 0


# ---------------------------------------------------------------- install hooks

def run_install_hooks(*, memory_root: str | None, repo_root: str | None) -> int:
    script = _PKG_ROOT / "hooks" / "install.sh"
    argv = ["bash", str(script), "--repo-root", repo_root or str(_REPO_ROOT)]
    # 一律經單一權威 resolver（#2 對抗審查 F3）：未給旗標時用 paths.memory_root()，
    # 避免 install.sh 落回自身預設造成 doctor/CLI 與 hooks 寫入分家。
    argv += ["--memory-root", memory_root or str(paths.memory_root())]
    completed = subprocess.run(argv)
    return completed.returncode


# ---------------------------------------------------------------- install service

def _systemd_user_available() -> bool:
    if shutil.which("systemctl") is None:
        return False
    completed = subprocess.run(
        ["systemctl", "--user", "is-system-running"], capture_output=True, text=True
    )
    return completed.stdout.strip() in {"running", "degraded"}


_UNIT_DIR_NAME = ".config/systemd/user"


def run_install_service(*, enable: bool, home_dir: str | None = None) -> int:
    home = Path(home_dir).expanduser() if home_dir else Path.home()
    src_dir = _PKG_ROOT / "dream" / "systemd"
    unit_dir = home / _UNIT_DIR_NAME
    if not _systemd_user_available():
        print("systemd --user 不可用。fallback：以任一 supervisor 執行前景模式：")
        print("  hippo dream supervise   # interval + require-idle，等價 systemd timer 語意")
        return 0
    unit_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for src_name, dst_name in (
        ("paulsha-memory-dream.service", "paulsha-hippo-dream.service"),
        ("paulsha-memory-dream.timer", "paulsha-hippo-dream.timer"),
    ):
        src = src_dir / src_name
        text = src.read_text(encoding="utf-8").replace("paulsha-memory-dream", "paulsha-hippo-dream")
        # ExecStart 綁定當前 interpreter：pipx / venv 隔離安裝下，template 寫死的
        # /usr/bin/env python3（全域 python）會 import 不到 paulsha_hippo
        # （ModuleNotFoundError → 服務啟動即 exit 1）。改用 sys.executable 指向
        # 實際安裝環境的 python，確保 systemd 服務能載入套件。
        text = text.replace("/usr/bin/env python3", sys.executable)
        dst = unit_dir / dst_name
        dst.write_text(text, encoding="utf-8")
        written.append(str(dst))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    for w in written:
        print(f"installed: {w}")
    linger = subprocess.run(
        ["loginctl", "show-user", os.environ.get("USER", ""), "--property=Linger"],
        capture_output=True, text=True,
    ).stdout.strip()
    if linger != "Linger=yes":
        print("提醒：開機自起需 loginctl enable-linger $USER")
    if enable:
        completed = subprocess.run(
            ["systemctl", "--user", "enable", "--now", "paulsha-hippo-dream.timer"]
        )
        if completed.returncode != 0:
            return completed.returncode
        print("enabled: paulsha-hippo-dream.timer")
    return 0


# ---------------------------------------------------------------- dream supervise

def _dream_timer_active() -> bool:
    """True 當 systemd dream timer 已接管（active）。systemctl 缺失/非 active → False。"""
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "is-active", "paulsha-hippo-dream.timer"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return completed.stdout.strip() == "active"


def run_dream_supervise(*, interval: int, extra_argv: list[str] | None = None,
                        once: bool = False, runner=None,
                        timer_active=_dream_timer_active) -> int:
    """前景常駐：每 interval 秒跑一次 dream run --require-idle。

    systemd dream timer 已接管時讓位（避免雙跑）；首輪延後一個 interval。
    """
    if timer_active():
        print("systemd dream timer 已接管；supervise 讓位（不啟動前景 loop）")
        return 0
    from paulsha_hippo import cli as hippo_cli

    argv = ["dream", "run", "--require-idle", "--promoter", "llm"] + list(extra_argv or [])
    run = runner or (lambda: hippo_cli.main(list(argv)))
    while True:
        time.sleep(interval)
        try:
            run()
        except Exception as exc:  # noqa: BLE001 —常駐不因單輪失敗而死
            print(f"dream supervise: 單輪失敗（{exc}），下一輪重試", file=sys.stderr)
        if once:
            return 0
