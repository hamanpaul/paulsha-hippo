"""hippo 運維命令：init / doctor / install hooks / install service / dream supervise。

quickstart 面（spec §5.2/§5.5/§5.6）。全部 stdlib；systemd 偵測失敗一律走
fallback 指引而非硬錯（G3「先驗證再選路」）。
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from paulsha_hippo import paths
from paulsha_hippo.dream.lock import dream_lock_path as _dream_lock_path

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

def _stage_temp(path: Path, content: str) -> str:
    """把 content 寫入 path 同目錄的 fsync 過暫存檔，回傳暫存檔路徑。

    呼叫端稍後以 `os.replace` 一次性 commit（同檔案系統上為原子操作，讀者永不會
    看到半寫入的設定檔）。同目錄確保 replace 不跨 filesystem。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    return tmp


_INIT_LOCK_NAME = ".init.lock"


def _init_lock_path() -> Path:
    """init transaction lock 固定路徑（hippo config 目錄）。

    同一 config 目標的並行 init 以此 rendezvous inode 互斥；lock 檔本身不含資料、
    永不 unlink（unlink 會破壞互斥語意）。"""
    return paths.hippo_config_root() / _INIT_LOCK_NAME


def _commit_init_atomic(cfg: Path, cfg_body: str,
                        override: Path, override_body: str | None) -> set[Path]:
    """持 transaction lock 執行 init 檔案交易，回傳本交易新寫入的路徑集合。

    #15 Codex high（併發／rollback 危害）三層防護：

    1. 同一把 `fcntl.flock`（固定鎖檔 `<hippo_config_root>/.init.lock`）涵蓋
       「存在性檢查 → stage → commit → rollback」全程，讓並行 init 互斥——杜絕兩個
       init 各自 TOCTOU 通過存在性檢查後互相覆寫、或誤刪對方剛落地的有效 config。
    2. 既有 config/override 刻意不覆寫（保留使用者設定）；僅「交易前不存在」者才
       stage——已存在路徑（別人的 config）不入 to_write，rollback 全程不碰。
    3. rollback 只移除「可證明由本交易建立、且自建立後未被替換」的檔案：每個 commit
       落地當下記錄其 `(st_dev, st_ino)` 指紋，rollback 時重新 stat，指紋相符才 unlink。
       若並行 writer 在本交易 commit 後替換了同路徑（寫入自己的有效 config），指紋不符
       → 不刪，避免盲刪對方有效設定、留下 config/override 不一致——第一個 commit 已成功、
       第二個失敗時，還原成交易前狀態（本交易新建者移除、既存／被替換者不動）而非盲刪。
    """
    lock_path = _init_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        # --- 存在性檢查（持鎖，與 commit/rollback 同一臨界區）：僅交易前不存在者才 stage ---
        to_write: list[tuple[Path, str]] = []
        if not cfg.exists():
            to_write.append((cfg, cfg_body))
        if override_body is not None and not override.exists():
            to_write.append((override, override_body))

        staged: list[tuple[str, Path]] = []
        # committed: dst -> 本交易 os.replace 落地當下的 (st_dev, st_ino) 指紋
        committed: dict[Path, tuple[int, int]] = {}
        try:
            for dst, body in to_write:
                staged.append((_stage_temp(dst, body), dst))
            for tmp, dst in staged:
                os.replace(tmp, dst)
                st = os.stat(dst)
                committed[dst] = (st.st_dev, st.st_ino)
        except BaseException:
            # 只回復「本交易建立、且自落地後指紋未變（未被並行 writer 替換）」的檔案
            for dst, fingerprint in committed.items():
                try:
                    st = os.stat(dst)
                except OSError:
                    continue
                if (st.st_dev, st.st_ino) != fingerprint:
                    continue  # 已被並行 writer 替換成別的有效 config——絕不 unlink
                try:
                    os.unlink(dst)
                except OSError:
                    pass
            raise
        finally:
            for tmp, _ in staged:  # 清掉尚未 os.replace 的暫存檔（已 replace 者 unlink 無害失敗）
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return set(committed)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def run_init(*, memory_root: str | None, backend: str, base_url: str | None,
             api_key_env: str | None, model: str | None, assume_yes: bool) -> int:
    """產生 ~/.config/paulsha-hippo/config.yaml 與 atomizer override（backend preset）。

    G3「先驗證再選路」：所有 backend 參數與 executable 驗證、config／override 完整
    內容一律「在動任何檔案之前」完成；任一驗證失敗即回非零，絕不建立或修改任一
    設定檔——避免留下「宣告 claude-headless 卻缺 override」的半初始化不一致設定，
    使後續 doctor/dream 失敗或誤 park。通過後才以暫存檔＋atomic replace 一次性提交。
    """
    if backend not in _BACKENDS:
        print(f"init: 不支援的 backend: {backend}（可選 {', '.join(_BACKENDS)}）", file=sys.stderr)
        return 2
    root = memory_root or str(paths.memory_root())

    # --- 先驗證＋生成完整內容（純字串，不碰檔案系統） ---
    cfg_body = (
        f"memory_root: {root}\n"
        "distiller:\n"
        f"  backend: {backend}\n"
        + (f"  base_url: {base_url}\n" if base_url else "")
        + (f"  api_key_env: {api_key_env}\n" if api_key_env else "")
        + (f"  model: {model}\n" if model else "")
    )

    # backend preset → paulshaclaw 相容 atomizer override（load_config 既有掛點）
    if backend == "claude-headless":
        try:
            argv = resolve_backend_argv(["claude", "-p"])
        except BackendUnavailableError as exc:
            print(f"init: {exc}（請先安裝 claude CLI，或改用 --backend openai-compatible/custom-argv）",
                  file=sys.stderr)
            return 2
        override_body: str | None = (
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

    # --- 驗證全過；持 transaction lock 以暫存檔全數落地後，再 atomic replace 一次性提交 ---
    # 既有 config/override 刻意不覆寫（保留使用者設定）；未存在者才 stage。併發／rollback
    # 硬化（#15 Codex high）全數收斂於 `_commit_init_atomic`：存在性檢查→commit→rollback
    # 全程互斥，rollback 以 inode 指紋只移除本交易新建且未被替換者，絕不誤刪並行 writer
    # 剛落地的有效 config，杜絕「宣告 claude-headless 卻缺 override」的半初始化殘留。
    cfg = paths.hippo_config_root() / "config.yaml"
    override = paths.config_path("atomizer.override.yaml")
    committed = _commit_init_atomic(cfg, cfg_body, override, override_body)

    print(f"memory_root: {root}")
    print(f"distiller backend: {backend}")
    print(f"config: {cfg}{'' if cfg in committed else '（既存，未覆寫）'}")
    if override_body is not None:
        print(f"atomizer override: {override}{'' if override in committed else '（既存，未覆寫）'}")
    print("下一步：hippo install hooks && hippo install service --enable")
    return 0


# ---------------------------------------------------------------- doctor

def run_doctor(*, fix_backend: bool = False, live_probe: bool = False,
               proc_root: str | Path = "/proc") -> int:
    """健檢。backend 檢查預設為解析級（快速、免費、無副作用——shutil.which／
    is_file+X_OK，不喚起 backend）；live smoke probe（實際喚起 backend 一次，
    spec §4.1 恢復序列 gate 語意）僅在 `fix_backend=True`／`live_probe=True`／
    `HIPPO_DOCTOR_LIVE_PROBE=1` 時執行。跨批次呼叫端（PR-C/PR-D）見
    `_probe_backend_service_effective` 的契約說明。"""
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

    live = fix_backend or live_probe or _live_probe_env_enabled()
    probe_line, probe_failed = _probe_backend_service_effective(live=live)
    if probe_failed:
        print(probe_line, file=sys.stderr)
        failed = True
    else:
        print(probe_line)
    _print_runtime_health(memory_root, proc_root=proc_root)
    return 1 if failed else 0


_FALLBACK_SERVICE_PATH = "/usr/local/bin:/usr/bin:/bin"

_SHOW_ENV_ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
    "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"',
}


def _unquote_show_environment_value(value: str) -> str:
    """還原 `systemctl show-environment` 對含特殊字元值的 `$'…'` shell quoting。"""
    if not (value.startswith("$'") and value.endswith("'") and len(value) > 2):
        return value
    return re.sub(
        r"\\(.)",
        lambda match: _SHOW_ENV_ESCAPES.get(match.group(1), match.group(1)),
        value[2:-1],
    )


def _service_manager_environment() -> dict[str, str] | None:
    """systemd user manager 環境（`systemctl --user show-environment`）完整快照。

    已安裝的 dream oneshot unit 無 Environment=/EnvironmentFile=，排程觸發時
    實際繼承的就是 user manager 環境——這是 doctor probe 的 service-effective
    真相來源（Codex 複驗 B1）。無 systemd user bus（CI／容器）→ None，呼叫端
    fallback 並標示近似。"""
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True, text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    env: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, sep, value = line.partition("=")
        if not sep or not key:
            continue
        env[key] = _unquote_show_environment_value(value)
    return env


def _service_effective_path_env() -> str:
    """systemd --user 服務實際看到的 PATH（非互動 shell；#15 根因是 NVM PATH 不在其中）。

    取不到（無 systemd／指令失敗）退保守預設。"""
    manager_env = _service_manager_environment()
    if manager_env is not None and manager_env.get("PATH"):
        return manager_env["PATH"]
    return _FALLBACK_SERVICE_PATH


def _probe_environment() -> tuple[dict[str, str], bool]:
    """構造 backend probe 的執行環境。回傳 (env, service_effective)。

    Codex 複驗 B1：早前 probe 繼承當前程序 os.environ（僅替換 PATH）——API key
    只 export 在互動 shell 時 doctor 誤判健康（requeue 後 dream service 仍認證
    失敗再度 parked）；反向（key 只在 manager env）亦誤判故障。改以
    `systemctl --user show-environment` 顯式構造：oneshot unit 無 Environment/
    EnvironmentFile，manager env 即 service-effective env——與 systemd-run
    transient unit 所見等價，且 timeout／判定行為留在本程序內可控（systemd-run
    中途被殺會殘留 transient unit），CI 亦可決定性測試。無 user bus →
    fallback 現行近似（os.environ + 保守 PATH），呼叫端必須在輸出標示
    「近似，非 service-effective」。"""
    manager_env = _service_manager_environment()
    if manager_env is not None:
        env = dict(manager_env)
        env.setdefault("PATH", _FALLBACK_SERVICE_PATH)
        return env, True
    return {**os.environ, "PATH": _FALLBACK_SERVICE_PATH}, False


_PROBE_TIMEOUT_SECS = 60
_PROBE_SMOKE_PROMPT = (
    "hippo doctor smoke probe: reply with the single word ok and nothing else."
)
_PROBE_MAX_TOKENS = 32

_LIVE_PROBE_ENV_VAR = "HIPPO_DOCTOR_LIVE_PROBE"


def _live_probe_env_enabled() -> bool:
    """`HIPPO_DOCTOR_LIVE_PROBE=1`（或 true/yes/on）→ 裸 doctor 也升級 live probe。"""
    return os.environ.get(_LIVE_PROBE_ENV_VAR, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _exec_probe_service_effective(command: list[str], probe_env: dict[str, str],
                                  *, runner=subprocess.run) -> tuple[bool, str]:
    """以 probe_env（見 `_probe_environment`）對 backend argv 送 bounded smoke prompt。

    doctor 是恢復序列的前置 gate（spec §4.1「實際喚起 backend 一次」）。早前
    判定把 timeout 與 126/127 以外的任何 exit code 都視為 PASS——backend hang
    （上游卡住）與認證／model／quota／config 錯誤（一律非零 exit）全被誤判為
    健康，gate 綠燈後 requeue 立即再度失敗或 parked。改為 fail-closed：經 stdin
    送 bounded smoke prompt（比照 AgentExecClient.run 的餵入方式），timeout 內
    exit 0 且 stdout 非空（可解析回應）才 PASS；timeout、任何非零 exit（含
    126/127）、空輸出、exec 失敗（ENOENT／EACCES、shebang interpreter 斷鏈、
    輸出非 UTF-8 位元組令 text=True decode 失敗）一律 FAIL。

    Codex 複驗 B1：環境由呼叫端以 `_probe_environment()` 顯式構造（manager env
    或標示近似的 fallback），本函式不再自行繼承 os.environ——僅疊加
    HIPPO_SELF_SESSION=1。

    #7：probe 實跑的是 configured backend argv（預設 `claude -p`），必須比照
    agent_exec.AgentExecClient.run 注入 HIPPO_SELF_SESSION=1——使用者已安裝的
    SessionEnd/PreCompact hooks 讀到此標記即早退，否則 doctor 探測會被當成
    真實 session 寫回 queue，重新引入遞迴自捕捉／queue 污染。
    """
    env = {**probe_env, "HIPPO_SELF_SESSION": "1"}
    try:
        completed = runner(
            command,
            env=env,
            input=_PROBE_SMOKE_PROMPT,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return False, (f"smoke prompt {_PROBE_TIMEOUT_SECS}s 內未完成"
                       "（backend hang／上游卡住；fail-closed）")
    except FileNotFoundError:
        return False, "exec 失敗：檔案或 shebang interpreter 不存在"
    except PermissionError:
        return False, "exec 失敗：無執行權限"
    except OSError as exc:
        return False, f"exec 失敗：{exc}"
    except UnicodeDecodeError as exc:
        # text=True 以 locale 編碼解 stdout/stderr，backend 吐非 UTF-8 位元組
        # （跑錯 binary／crash dump／locale 錯亂的錯誤文字）時 decode 於 run()
        # 內拋 UnicodeDecodeError（ValueError 子類，非 OSError）。此正是本 probe
        # 要偵測的故障類；比照恢復 gate fail-closed 語意判 FAIL，不得逸出崩潰 CLI。
        return False, (f"exec 失敗：backend 輸出非 UTF-8 位元組"
                       f"（跑錯 binary／crash dump／locale 錯亂；fail-closed）：{exc}")
    if completed.returncode != 0:
        stderr_tail = " ".join(str(completed.stderr or "").split())[:200]
        return False, (f"exit {completed.returncode}（smoke prompt 失敗；"
                       f"認證／model／quota／config 錯誤同屬此類）：{stderr_tail}")
    if not str(completed.stdout or "").strip():
        return False, "exit 0 但回應為空（無可解析輸出）"
    return True, "smoke prompt exit 0、回應非空"


def _probe_openai_compatible(cfg, probe_env: dict[str, str],
                             *, env_label: str) -> tuple[str, bool]:
    """openai-compatible 檔位的實際 probe：bounded smoke prompt 打 /v1/chat/completions。

    先前僅回「probe 由 PR-D preset 接手」即綠燈——恢復 gate 拿不到真實可用性
    判定（端點掛掉／認證失效照樣 exit 0）。改為 fail-closed：端點不可達、HTTP
    錯誤、timeout、回應缺 choices[0].message.content 或內容為空（HttpAgentClient
    對以上一律拋例外）→ FAIL。max_tokens／timeout 均受限，probe 不做實際工作。

    Codex 複驗 B1：API key（`api_key_env`）從注入的 probe_env 解析，而非 doctor
    所在互動 shell 的 os.environ——否則只 export 在 shell 的 key 會令 probe
    誤判健康，排程的 dream service 實際仍認證失敗。"""
    from paulsha_hippo.atomizer.agent_exec import HttpAgentClient

    client = HttpAgentClient(
        cfg.agent_exec_base_url,
        cfg.agent_exec_model,
        api_key_env=cfg.agent_exec_api_key_env or None,
        timeout=_PROBE_TIMEOUT_SECS,
        max_tokens=_PROBE_MAX_TOKENS,
        env=probe_env,
    )
    try:
        client.run(_PROBE_SMOKE_PROMPT)
    except Exception as exc:  # noqa: BLE001 —probe fail-closed：任何失敗都判 FAIL
        detail = " ".join(str(exc).split())[:200]
        return (
            f"FAIL distiller backend：openai-compatible（{cfg.agent_exec_base_url}）"
            f"{env_label} smoke probe 失敗：{detail}",
            True,
        )
    return (
        f"- distiller backend：✓ openai-compatible（{cfg.agent_exec_base_url}；"
        f"{env_label} smoke probe 有非空回應）",
        False,
    )


def _probe_backend_service_effective(*, live: bool = False) -> tuple[str, bool]:
    """以 service-effective 環境檢查 atomizer backend。回傳 (報告行, is_failure)。

    兩檔行為（跨批次共享契約 6——PR-C/PR-D 對 `run_doctor` 的呼叫端必讀）：
    - `live=False`（裸 `hippo doctor` 預設）：純解析檢查——argv backend 以
      service-effective PATH `shutil.which`（絕對路徑則 is_file+X_OK）；
      openai-compatible 只驗 config 可載入、不打端點。快速、免費、無副作用，
      不喚起 backend、不產生 API 成本。
    - `live=True`（`--fix-backend`／`--probe-live`／`HIPPO_DOCTOR_LIVE_PROBE=1`）：
      對 configured backend 真送 bounded smoke prompt——argv 走真實 exec
      （`_exec_probe_service_effective`，60s timeout），openai-compatible 以
      HttpAgentClient 直打 `/v1/chat/completions`；fail-closed（spec §4.1
      恢復序列 gate「實際喚起 backend 一次」語意）。

    dream service template 固定 --promoter llm，故 backend 檢查不過一律 FAIL，
    不因 config 的 default promoter 軟化。probe 環境經 `_probe_environment()`
    顯式構造（B1）；無 systemd user bus 時 fallback 近似並在報告行標示，
    避免把近似判定當成 service-effective 真相。"""
    from paulsha_hippo.atomizer import config as atomizer_config

    try:
        cfg, _ = atomizer_config.load_config()
    except Exception as exc:  # config 壞掉本身就是 backend 不可用級的問題
        return f"FAIL distiller backend config 無法載入：{exc}", True
    probe_env, service_effective = _probe_environment()
    env_label = ("service-effective" if service_effective
                 else "近似，非 service-effective（無 systemd user bus）")
    if cfg.agent_exec_backend == "openai-compatible":
        if not live:
            return (
                f"- distiller backend：openai-compatible（{cfg.agent_exec_base_url}；"
                "config 可載入；即時 smoke probe 需 --probe-live／--fix-backend）",
                False,
            )
        return _probe_openai_compatible(cfg, probe_env, env_label=env_label)
    command = list(atomizer_config.resolve_command_argv(cfg.agent_exec_command))
    argv0 = command[0]
    if not Path(argv0).is_absolute():
        resolved = shutil.which(argv0, path=probe_env.get("PATH", _FALLBACK_SERVICE_PATH))
        if resolved is None:
            return (
                f"FAIL distiller backend：{argv0} 在 {env_label} 環境解析不到"
                "（hippo doctor --fix-backend 可嘗試自動遷移）",
                True,
            )
        command[0] = resolved
    if not live:
        if Path(command[0]).is_file() and os.access(command[0], os.X_OK):
            return (
                f"- distiller backend：✓ {command[0]}（{env_label} 解析檢查；"
                "即時 smoke probe 需 --probe-live／--fix-backend）",
                False,
            )
        return (
            f"FAIL distiller backend：{command[0]} 在 {env_label} 環境不可執行"
            "（hippo doctor --fix-backend 可嘗試自動遷移）",
            True,
        )
    ok, detail = _exec_probe_service_effective(command, probe_env)
    if ok:
        return (
            f"- distiller backend：✓ {command[0]}（{env_label} smoke probe；{detail}）",
            False,
        )
    return (
        f"FAIL distiller backend：{' '.join(command)} 在 {env_label} 環境 smoke probe 失敗"
        f"（{detail}；hippo doctor --fix-backend 可嘗試自動遷移）",
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


# ---------------------------------------------------------------- runtime hygiene (#19)

_TEMP_WORKTREE_SEGMENTS = {".psc_tmp", ".test-work"}
_KEEP_LOCK_NAMES = {"import-ledger.lock", "dream.lock"}


def _iter_pids(proc_root: Path) -> list[int]:
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return []
    return sorted(int(name) for name in entries if name.isdigit())


def _read_cmdline(proc_root: Path, pid: int) -> list[str]:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\x00") if part]


def _read_started_at(proc_root: Path, pid: int) -> str:
    """btime + starttime/SC_CLK_TCK → ISO UTC；任一環節失敗回 'unknown'（診斷 fail-open）。"""
    try:
        btime: int | None = None
        for line in (proc_root / "stat").read_text(
                encoding="ascii", errors="replace").splitlines():
            if line.startswith("btime "):
                btime = int(line.split()[1])
                break
        if btime is None:
            return "unknown"
        stat = (proc_root / str(pid) / "stat").read_text(
            encoding="ascii", errors="replace")
        fields = stat.rpartition(")")[2].split()
        starttime = int(fields[19])  # 整行第 22 欄（')' 後第 20 個 token）
        ticks = os.sysconf("SC_CLK_TCK")
        return datetime.fromtimestamp(btime + starttime // ticks,
                                      tz=timezone.utc).isoformat()
    except (OSError, ValueError, IndexError):
        return "unknown"


def _read_cwd(proc_root: Path, pid: int) -> str | None:
    try:
        return os.readlink(proc_root / str(pid) / "cwd")
    except OSError:
        return None


def scan_hippo_processes(*, proc_root: str | Path = "/proc") -> list[dict[str, object]]:
    """列出 cmdline 涉及 paulsha_hippo（或 argv[0] 為 hippo）的其他進程。

    只讀 /proc、不發任何 signal；排除自身 PID。proc_root 可注入假目錄供測試。
    """
    root = Path(proc_root)
    records: list[dict[str, object]] = []
    for pid in _iter_pids(root):
        if pid == os.getpid():
            continue
        argv = _read_cmdline(root, pid)
        if not argv:
            continue
        is_hippo = any("paulsha_hippo" in token for token in argv) or Path(argv[0]).name == "hippo"
        if not is_hippo:
            continue
        records.append({
            "pid": pid,
            "argv": argv,
            "cmdline": " ".join(argv),
            "started_at": _read_started_at(root, pid),
            "cwd": _read_cwd(root, pid),
        })
    return records


def dream_process_report(*, proc_root: str | Path = "/proc",
                         canonical_interpreter: str | None = None
                         ) -> list[dict[str, object]]:
    """dream/supervise 進程健康報告素材：附 non_canonical 標記與 reasons。

    只報告，不自動 kill（#19）。reason tokens：
      interpreter-mismatch —— argv[0]（絕對路徑時）的 bin 目錄與本安裝環境的
                              interpreter 目錄不同（比目錄層、不跟 python symlink）
      cwd-missing          —— 進程 cwd 已不存在（多半是被清掉的暫存 worktree）
      cwd-temp-worktree    —— 進程 cwd 位於暫存區（.psc_tmp / .test-work / tempdir）
    """
    # 只 resolve 目錄層、不對最終 python 檔跟 symlink：venv 的 bin/python3 多半是
    # 指向共用 base interpreter 的 symlink，若對整條 argv[0] 做 resolve，兩個不同
    # venv 會收斂成同一真實路徑、同一 parent，interpreter-mismatch 永不觸發——正是
    # 暫存 worktree 各自 .venv 共用同一 base Python 的最常見情境（#19 回歸）。
    canonical_bin = Path(canonical_interpreter or sys.executable).parent.resolve(strict=False)
    reports: list[dict[str, object]] = []
    for record in scan_hippo_processes(proc_root=proc_root):
        argv_value = record.get("argv")
        if not isinstance(argv_value, list):
            continue
        argv = [str(token) for token in argv_value]
        if "dream" not in argv:
            continue
        reasons: list[str] = []
        if argv[0].startswith("/"):
            argv0_bin = Path(argv[0]).parent.resolve(strict=False)
            if argv0_bin != canonical_bin:
                reasons.append("interpreter-mismatch")
        cwd = record.get("cwd")
        if isinstance(cwd, str):
            cwd_path = Path(cwd)
            if not cwd_path.exists():
                reasons.append("cwd-missing")
            elif any(part in _TEMP_WORKTREE_SEGMENTS for part in cwd_path.parts) or str(
                    cwd_path).startswith(tempfile.gettempdir() + os.sep):
                reasons.append("cwd-temp-worktree")
        report = dict(record)
        report["non_canonical"] = bool(reasons)
        report["reasons"] = reasons
        reports.append(report)
    return reports


def dream_lock_status(memory_root: Path) -> str:
    """點時探測 dream lock：absent / free / held / unknown。

    以 LOCK_EX|LOCK_NB 探測並立即釋放，不長持；探測瞬間與同時啟動的
    dream run 存在極小視窗（對方 LOCK_NB 會失敗跳過一輪），屬診斷面可接受成本。
    """
    lock_path = _dream_lock_path(Path(memory_root))
    if not lock_path.exists():
        return "absent"
    try:
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return "held"
            fcntl.flock(handle, fcntl.LOCK_UN)
            return "free"
    except OSError:
        return "unknown"


def _print_runtime_health(memory_root: Path, *,
                          proc_root: str | Path = "/proc") -> None:
    """doctor 的 runtime 健康報告段落（#19）：只報告，不自動 kill、不影響 exit code。"""
    print(f"- dream lock（runtime/locks/dream.lock）：{dream_lock_status(memory_root)}")
    reports = dream_process_report(proc_root=proc_root)
    if not reports:
        print("- dream/supervise 進程：無")
        return
    print(f"- dream/supervise 進程：{len(reports)} 個（只報告，不自動 kill）")
    for report in reports:
        reasons_value = report.get("reasons")
        reasons = [str(reason) for reason in reasons_value] if isinstance(reasons_value, list) else []
        if report["non_canonical"]:
            mark = "non-canonical[" + ",".join(reasons) + "]"
        else:
            mark = "canonical"
        print(f"  - pid={report['pid']} start={report['started_at']} {mark} "
              f"cwd={report['cwd']} cmdline={report['cmdline']}")


def cleanup_legacy_locks(memory_root: Path, *, apply: bool = False,
                         proc_root: str | Path = "/proc") -> dict[str, object]:
    """#19：legacy per-session lock 檔一次性清理（僅維護窗口執行）。

    #19 教訓：執行中直接 unlink lock 檔會破壞 flock 互斥（新開者 rendezvous 到新
    inode）。因此雙層安全閘：
      1. 進程閘：偵測到其他 paulsha_hippo/hippo 進程（可能是尚未升版的 importer）
         → apply 直接拒絕（result["blocked"]），一檔不刪。
      2. flock 閘：逐檔 LOCK_EX|LOCK_NB 探測，busy 檔跳過（result["busy"]）。
    keep-set：import-ledger.lock、dream.lock（契約 3）、index-rebuild.lock（MOC
    重建互斥鎖，與本 PR 的 per-session lock 無關，永久 flock rendezvous inode——
    名稱由 search.index_lock_path 唯一定義、import 派生避免字面字串漂移）、
    lock_shard_XX.lock（契約 4）。非 .lock 檔一律不碰。預設 dry-run（apply=False）只列清單。
    """
    from paulsha_hippo.importer.pipeline import is_shard_lock_name
    from paulsha_hippo.moc.search import index_lock_path

    locks_dir = Path(memory_root) / "runtime" / "locks"
    keep_names = _KEEP_LOCK_NAMES | {index_lock_path(Path(memory_root)).name}
    others = scan_hippo_processes(proc_root=proc_root)
    legacy: list[str] = []
    kept: list[str] = []
    if locks_dir.is_dir():
        for path in sorted(locks_dir.iterdir()):
            if not path.is_file() or path.suffix != ".lock":
                continue
            if path.name in keep_names or is_shard_lock_name(path.name):
                kept.append(path.name)
            else:
                legacy.append(path.name)
    result: dict[str, object] = {
        "locks_dir": str(locks_dir),
        "legacy": legacy,
        "kept": kept,
        "other_processes": [{"pid": r["pid"], "cmdline": r["cmdline"]} for r in others],
        "applied": False,
        "deleted": [],
        "busy": [],
    }
    if not apply:
        return result
    if others:
        result["blocked"] = "偵測到其他 hippo 進程，維護窗口未確立；拒絕清理"
        return result
    deleted: list[str] = []
    busy: list[str] = []
    for name in legacy:
        path = locks_dir / name
        try:
            with path.open("a+", encoding="utf-8") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    busy.append(name)
                    continue
                try:
                    path.unlink()
                    deleted.append(name)
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            busy.append(name)
    result["applied"] = True
    result["deleted"] = deleted
    result["busy"] = busy
    return result
