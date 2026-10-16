#!/usr/bin/env python3
"""Grok Build SessionStart hook.

Grok 的 SessionStart 只讀頂層 systemMessage。沒有可用的 prompt-time
additionalContext，所以這裡帶 recall_guidance，請模型在需要時自己呼叫 hippo recall。

Memory root: PSC_MEMORY_ROOT env var (default ~/.agents/memory).
任何例外記到 log/hooks.log，仍印空 systemMessage，exit 0。
"""

from __future__ import annotations

import json
import sys

import _bootstrap  # sibling module; hooks dir is on sys.path[0]

_bootstrap.ensure_repo_on_path()

TOOL = "grok"


def main() -> int:
    from paulsha_hippo.hooks._wakeup_common import (
        compute_brief_and_record,
        log_warn,
        memory_root,
        read_payload,
    )

    root = memory_root()
    payload = read_payload(root, TOOL)
    brief = ""
    try:
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "unknown")
        # 沒有 prompt-time hook。systemMessage 是否進模型尚未以 MEMO-TOKEN 實測，
        # 仍注入顯式 recall 指引，不假裝 SessionStart 等於任務檢索。
        brief = compute_brief_and_record(
            root,
            TOOL,
            session_id,
            payload.get("cwd"),
            recall_guidance=True,
        )
    except Exception as exc:
        log_warn(root, TOOL, f"failed to build output: {exc}")
        brief = ""
    print(json.dumps({"systemMessage": brief}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
