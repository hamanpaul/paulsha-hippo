"""hippo 運維命令：init / doctor / install hooks / install service / dream supervise。

quickstart 面（spec §5.2/§5.5/§5.6）。全部 stdlib；systemd 偵測失敗一律走
fallback 指引而非硬錯（G3「先驗證再選路」）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from paulsha_hippo import paths

_PKG_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_ROOT.parent

_BACKENDS = ("claude-headless", "openai-compatible", "custom-argv")


class BackendUnavailableError(ValueError):
    """backend argv[0] 在目前環境解析不到可執行檔（#15；PR-D preset registry 重用）。"""


def resolve_backend_argv(argv: list[str]) -> list[str]:
    """argv[0] 以 shutil.which 絕對路徑化（跨批次共享契約：PR-A 建立、PR-D 重用）。

    - 以 os.path.abspath 正規化、不 resolve symlink（nvm shim 需保留原 symlink 層）。
    - 空 argv 或 which 找不到 → BackendUnavailableError（ValueError 子類）。
    """
    if not argv or not argv[0]:
        raise BackendUnavailableError("backend argv 不可為空")
    resolved = shutil.which(argv[0])
    if resolved is None:
        raise BackendUnavailableError(f"backend executable 找不到：{argv[0]}")
    return [os.path.abspath(resolved), *argv[1:]]


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
        try:
            argv = resolve_backend_argv(["claude", "-p"])
        except BackendUnavailableError as exc:
            print(f"init: {exc}（請先安裝 claude CLI，或改用 --backend openai-compatible/custom-argv）",
                  file=sys.stderr)
            return 2
        override_body = (
            "schema_version: \"1\"\n"
            "agent_exec:\n"
            "  command:\n"
            + "".join(f"    - {token}\n" for token in argv)
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

def run_doctor(*, fix_backend: bool = False) -> int:
    if fix_backend:
        code, message = _fix_backend_override()
        print(message, file=sys.stderr if code else sys.stdout)
        if code:
            return code
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

    probe_line, probe_failed = _probe_backend_service_effective()
    if probe_failed:
        print(probe_line, file=sys.stderr)
        failed = True
    else:
        print(probe_line)
    return 1 if failed else 0


def _service_effective_path_env() -> str:
    """systemd --user 服務實際看到的 PATH（非互動 shell；#15 根因是 NVM PATH 不在其中）。

    取不到（無 systemd／指令失敗）退保守預設。"""
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True, text=True,
        )
    except OSError:
        completed = None
    if completed is not None and completed.returncode == 0:
        for line in completed.stdout.splitlines():
            if line.startswith("PATH="):
                return line[len("PATH="):]
    return "/usr/local/bin:/usr/bin:/bin"


def _probe_backend_service_effective() -> tuple[str, bool]:
    """以 service-effective 環境驗證 atomizer backend 可執行。回傳 (報告行, is_failure)。

    dream service template 固定 --promoter llm，故 argv backend 解析不到一律 FAIL，
    不因 config 的 default promoter 軟化。openai-compatible 非 argv backend，
    probe 由 PR-D preset registry 接手。"""
    from paulsha_hippo.atomizer import config as atomizer_config

    try:
        cfg, _ = atomizer_config.load_config()
    except Exception as exc:  # config 壞掉本身就是 backend 不可用級的問題
        return f"FAIL distiller backend config 無法載入：{exc}", True
    if cfg.agent_exec_backend == "openai-compatible":
        return (
            f"- distiller backend：openai-compatible（{cfg.agent_exec_base_url}；"
            "probe 由 PR-D preset 接手）",
            False,
        )
    command = atomizer_config.resolve_command_argv(cfg.agent_exec_command)
    argv0 = command[0]
    if Path(argv0).is_absolute():
        ok = Path(argv0).is_file() and os.access(argv0, os.X_OK)
    else:
        ok = shutil.which(argv0, path=_service_effective_path_env()) is not None
    if ok:
        return f"- distiller backend：✓ {argv0}（service-effective 可執行）", False
    return (
        f"FAIL distiller backend：{argv0} 在 service-effective 環境解析不到"
        "（hippo doctor --fix-backend 可嘗試自動遷移）",
        True,
    )


_COMMAND_LIST_HEAD_RE = re.compile(r"^(\s*-\s+)(\S+)\s*$")


def _rewrite_override_command_head(text: str, bare: str, resolved: str) -> tuple[str, bool]:
    """單點改寫：agent_exec.command 清單第一項 == bare 的 token 換成 resolved。

    只處理 init 產生的 override 結構（`command:` 下第一個 `- <token>`）；
    結構對不上回 (原文, False)，交上層報「需人工」。"""
    lines = text.splitlines(keepends=True)
    in_command = False
    for index, line in enumerate(lines):
        if line.strip() == "command:":
            in_command = True
            continue
        if in_command:
            match = _COMMAND_LIST_HEAD_RE.match(line.rstrip("\n"))
            if match and match.group(2) == bare:
                newline = "\n" if line.endswith("\n") else ""
                lines[index] = f"{match.group(1)}{resolved}{newline}"
                return "".join(lines), True
            return "".join(lines), False
    return "".join(lines), False


def _fix_backend_override() -> tuple[int, str]:
    """冪等 migration（#15 既有部署救援）：override 內 service-effective 解析不到的
    裸 backend 命令 → 備份原檔 → 改寫為絕對路徑。回傳 (exit_code, 訊息)。"""
    from paulsha_hippo.atomizer import config as atomizer_config

    override = paths.config_path("atomizer.override.yaml")
    if not override.is_file():
        return 0, f"fix-backend: override 不存在（{override}），無可遷移"
    try:
        cfg, _ = atomizer_config.load_config()
    except Exception as exc:
        return 1, f"fix-backend: config 無法載入：{exc}"
    if cfg.agent_exec_backend == "openai-compatible":
        return 0, "fix-backend: openai-compatible backend 無 argv，無可遷移"
    command = atomizer_config.resolve_command_argv(cfg.agent_exec_command)
    argv0 = command[0]
    if Path(argv0).is_absolute():
        return 0, f"fix-backend: argv[0] 已是絕對路徑（{argv0}），無可遷移"
    if shutil.which(argv0, path=_service_effective_path_env()) is not None:
        return 0, f"fix-backend: {argv0} 在 service-effective PATH 已可解析，無可遷移"
    try:
        resolved = resolve_backend_argv([argv0])[0]
    except BackendUnavailableError as exc:
        return 1, f"fix-backend: {exc}（互動環境也解析不到，請先安裝 backend）"
    text = override.read_text(encoding="utf-8")
    new_text, replaced = _rewrite_override_command_head(text, argv0, resolved)
    if not replaced:
        return 1, (f"fix-backend: override 未含 agent_exec.command 首項 {argv0!r}，"
                   "無法自動改寫（請手動修改或重跑 hippo init）")
    backup = override.with_name(override.name + ".bak")
    shutil.copy2(override, backup)
    override.write_text(new_text, encoding="utf-8")
    return 0, f"fix-backend: {argv0} → {resolved}（備份：{backup}）"


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
