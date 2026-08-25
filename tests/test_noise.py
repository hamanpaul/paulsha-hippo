from __future__ import annotations

import unittest

from paulsha_hippo.noise import (
    _weak_hit_count,
    build_corpus,
    classify_noise,
    episodic_reason,
)


# A fake agent-instruction document (CLAUDE.md/AGENTS.md shape) used as verbatim corpus.
_INSTRUCTION_DOC = (
    "# Project instructions\n"
    "## 6. 自主維護規則（agent-managed）\n"
    "- [multi_agent_devflow] 多線開發任務先由 master agent 拆出 todo 與 boundary。\n"
    "- [scope_violation] 子 agent 寫入超出宣告 scope 時必須先中止該寫入。\n"
    "## 動工前\n"
    "- [ ] 確認當前分支不是 `main`\n"
    "- [ ] 若本任務跨多個子項，先建議用 `git worktree` 拆開\n"
    "## 1. 薄核心原則\n"
    "- 路由：依任務型態載入對應 skills。\n"
    "- 硬規範：安全、不可破壞、品質底線。\n"
)


class ClassifyNoiseTests(unittest.TestCase):
    def test_structural_echo_headings_are_noise(self):
        for section in ("CWD", "Source", "Prompts", "Touched files",
                        "Referenced artifacts", "Summary"):
            body = f"## {section}\nsome value here that is fairly long but still echo\n"
            verdict = classify_noise({"atom_title": section.lower()}, body)
            self.assertTrue(verdict.is_noise, section)
            self.assertEqual(verdict.reason, f"structural-echo:{section}")

    def test_heading_only_body_is_noise(self):
        verdict = classify_noise({"atom_title": "x"}, "# Session dcbb8041-29ef-4a9f-a9e7-c408a65cbf20\n")
        self.assertTrue(verdict.is_noise)
        self.assertEqual(verdict.reason, "empty")

    def test_blank_body_is_noise(self):
        verdict = classify_noise({}, "   \n\n")
        self.assertTrue(verdict.is_noise)
        self.assertEqual(verdict.reason, "empty")

    def test_placeholder_phrases_are_noise(self):
        for phrase in ("由於目前尚未收到您的具體需求，請提供更多細節以便我協助您完成任務。",
                       "目前尚未收到您的具體需求或任務指令，請提供。",
                       "(無內容) 這是一個空的 session 沒有任何實際內容可供原子化處理。"):
            verdict = classify_noise({}, phrase + "\n")
            self.assertTrue(verdict.is_noise, phrase[:10])
            self.assertEqual(verdict.reason, "placeholder")

    def test_untitled_with_real_body_is_kept(self):
        body = ("## 動工前\n- [ ] 確認當前分支不是 `main`\n  - 若在 `main`，先開 "
                "`feature/<slug>` 分支\n- [ ] 跨多子項先用 `git worktree` 拆開\n")
        verdict = classify_noise({"atom_title": "untitled"}, body)
        self.assertFalse(verdict.is_noise)

    def test_real_short_fact_is_kept(self):
        body = "gh 2.45.0 的 pr checks 沒有 --json，要用 pr view --json statusCheckRollup 判 CI。\n"
        verdict = classify_noise({"atom_title": "ci-gating"}, body)
        self.assertFalse(verdict.is_noise)

    def test_real_short_content_is_kept_regardless_of_length(self):
        # 30-char CJK real conclusion — must survive (no length threshold).
        verdict = classify_noise({}, "這是一段足夠長的真實技術內容，描述某個具體結論與其理由說明。\n")
        self.assertFalse(verdict.is_noise)

    def test_structural_heading_with_substantial_prose_is_kept(self):
        # A real distilled note that merely *starts* with `## Summary` but carries
        # multiple prose lines must NOT be deleted as structural-echo (#139 finding 3).
        body = (
            "## Summary\n"
            "本次調查確認 dream 管線停擺的根因是 frontmatter escaping。\n"
            "atomize 與 moc 兩個 pass 因單一 poison-pill 檔整批 ParserError。\n"
            "修法是 per-file 隔離加寫入端 YAML escaping，兩者缺一不可。\n"
        )
        verdict = classify_noise({}, body)
        self.assertFalse(verdict.is_noise, verdict.reason)

    def test_importer_exclusive_heading_is_noise_even_with_multiline_content(self):
        # `## Prompts` / `## CWD` / `## Source` / `## Touched files` / `## Referenced artifacts`
        # are importer-template-exclusive section names — never a real standalone knowledge
        # atom — so they are structural-echo regardless of how many prose lines follow.
        body = (
            "## Prompts\n"
            "1. # AGENTS.md instructions for /home/paul_chen\n\n"
            "<INSTRUCTIONS>\n你是高度自主的互動式 CLI Agent。\n專長為嵌入式系統。\n</INSTRUCTIONS>\n"
            "2. 修 UART\n"
        )
        verdict = classify_noise({}, body)
        self.assertTrue(verdict.is_noise, verdict.reason)
        self.assertEqual(verdict.reason, "structural-echo:Prompts")

    def test_summary_guard_still_protects_real_multiline_summary(self):
        # The ≤1-prose-line guard remains ONLY for `## Summary`, the one heading that
        # legitimately appears in real notes.
        body = (
            "## Summary\n第一段真實結論說明背景與動機。\n"
            "第二段補充技術細節與取捨。\n第三段給出後續步驟。\n"
        )
        verdict = classify_noise({}, body)
        self.assertFalse(verdict.is_noise, verdict.reason)

    def test_session_metadata_heading_is_noise(self):
        for heading in ("### Session Metadata", "## Session Information", "# Session Metadata"):
            body = (heading + "\n- **Session ID**: `019ef36c-4a13-7231`\n"
                    "- **Working Directory**: `/workspace/prj`\n- **Tool**: `copilot-cli`\n")
            verdict = classify_noise({}, body)
            self.assertTrue(verdict.is_noise, heading)
            self.assertTrue(verdict.reason.startswith("structural-echo"), verdict.reason)

    def test_note_quoting_placeholder_phrase_mid_body_is_kept(self):
        # A real note that discusses the placeholder text (not opens with it) is kept.
        body = (
            "noise classifier 的 placeholder 規則設計：\n"
            "當 session 沒有實際內容時，importer 會寫入「尚未收到您的具體需求」這類佔位字串，"
            "因此 classifier 需在 body 開頭附近偵測到該字串才判為 placeholder，避免誤刪引用它的真筆記。\n"
        )
        verdict = classify_noise({}, body)
        self.assertFalse(verdict.is_noise, verdict.reason)


class DocFragmentTests(unittest.TestCase):
    def setUp(self):
        self.corpus = build_corpus([_INSTRUCTION_DOC])

    def test_numbered_instruction_section_is_doc_fragment(self):
        # `## 6. ...` 章節 + ≥2 逐字內容行命中語料；尾部 session 雜訊不影響判定。
        body = (
            "## 6. 自主維護規則（agent-managed）\n"
            "- [multi_agent_devflow] 多線開發任務先由 master agent 拆出 todo 與 boundary。\n"
            "- [scope_violation] 子 agent 寫入超出宣告 scope 時必須先中止該寫入。\n"
            "</INSTRUCTIONS>\n2. 不相關的 session 對話雜訊。\n"
        )
        verdict = classify_noise({}, body, doc_corpus=self.corpus)
        self.assertTrue(verdict.is_noise, verdict.reason)
        self.assertEqual(verdict.reason, "doc-fragment")

    def test_non_numbered_agents_section_is_doc_fragment(self):
        body = (
            "## 動工前\n"
            "- [ ] 確認當前分支不是 `main`\n"
            "- [ ] 若本任務跨多個子項，先建議用 `git worktree` 拆開\n"
        )
        verdict = classify_noise({"title": "untitled"}, body, doc_corpus=self.corpus)
        self.assertTrue(verdict.is_noise, verdict.reason)
        self.assertEqual(verdict.reason, "doc-fragment")

    def test_real_numbered_note_not_in_corpus_is_kept(self):
        # 編號 heading 但內容為原創、未逐字命中語料 → 不可誤刪。
        body = (
            "## 1. 背景\n"
            "本研究探討一個全新的、語料中不存在的技術問題與其取捨。\n"
            "第二段給出原創的結論與後續步驟。\n"
        )
        verdict = classify_noise({}, body, doc_corpus=self.corpus)
        self.assertFalse(verdict.is_noise, verdict.reason)

    def test_heading_match_but_single_content_hit_is_kept(self):
        # heading 命中、但僅 1 條內容行命中語料（< 2）→ 保守保留。
        body = (
            "## 1. 薄核心原則\n"
            "- 路由：依任務型態載入對應 skills。\n"
            "這是一段語料中不存在的原創補充說明文字。\n"
        )
        verdict = classify_noise({}, body, doc_corpus=self.corpus)
        self.assertFalse(verdict.is_noise, verdict.reason)

    def test_without_corpus_doc_fragment_rule_is_inert(self):
        # 不傳 doc_corpus 時，doc-fragment 規則不啟用；該 body 非既有三類 → 非 noise。
        body = (
            "## 6. 自主維護規則（agent-managed）\n"
            "- [multi_agent_devflow] 多線開發任務先由 master agent 拆出 todo 與 boundary。\n"
            "- [scope_violation] 子 agent 寫入超出宣告 scope 時必須先中止該寫入。\n"
        )
        self.assertFalse(classify_noise({}, body).is_noise)
        self.assertFalse(classify_noise({}, body, doc_corpus=build_corpus([])).is_noise)


def test_pool_exclude_reason_review_and_canary():
    from paulsha_hippo.noise import pool_exclude_reason
    assert pool_exclude_reason({"artifact_kind": "review"}) == "review-record"
    assert pool_exclude_reason(
        {"artifact_kind": "task", "atom_title": "canary-claude task context"}) == "canary-fixture"
    assert pool_exclude_reason(
        {"artifact_kind": "task", "session_title": "smoke test execution"}) == "canary-fixture"
    # real knowledge is not excluded
    assert pool_exclude_reason({"artifact_kind": "spec", "atom_title": "LLM Atomizer"}) is None
    assert pool_exclude_reason({"artifact_kind": "task", "atom_title": "build P4 split"}) is None


def test_is_generic_title_hits_and_misses():
    from paulsha_hippo.noise import is_generic_title

    hits = (
        "overview",
        "problem",
        "untitled",
        "review-summary",
        "Review Summary",
        "report",
        "task",
        "todo",
        "TODO",
        "report-testpilot",
        "task-cockpit-swap",
        "todo_cleanup",
        "TODO list",
        "Report Testpilot",
    )
    misses = (
        "",
        None,
        "單一-com0-死因未解",
        "wi-fi-llapi-test-execution-workflow",
        "overview-of-uart-pinmux",
        "problem-with-dma-burst",
        "release-v0-2-0-preparation-execution",
        "todos",
        "subtask-routing",
    )

    for title in hits:
        assert is_generic_title(title), title
    for title in misses:
        assert not is_generic_title(title), title


def test_pool_exclude_reason_generic_title():
    from paulsha_hippo.noise import pool_exclude_reason

    assert pool_exclude_reason(
        {"artifact_kind": "report", "title": "overview"}
    ) == "generic-title"
    assert pool_exclude_reason(
        {"artifact_kind": "report", "atom_title": "Report Testpilot"}
    ) == "generic-title"
    assert pool_exclude_reason(
        {"artifact_kind": "report", "session_title": "TODO"}
    ) is None
    assert pool_exclude_reason(
        {"artifact_kind": "report", "title": "wi-fi-llapi-test-execution-workflow"}
    ) is None
    assert pool_exclude_reason(
        {"artifact_kind": "review", "title": "overview"}
    ) == "review-record"


class EpisodicReasonTests(unittest.TestCase):
    def test_mostly_status_lines_is_episodic(self):
        # review round 1 fixture adjustment: 「下一步：開 PR。」原本靠舊版 bare-word
        # 「下一步」單獨命中湊出 3/3；新版強弱訊號拆分後「下一步」只是弱訊號，單行
        # 沒有第二個弱訊號佐證就不算 session-state 行，故該行不再計入命中。其餘 2 行
        # （本次修改僅限／session 結束時尚未 commit）都是強訊號，仍以 2/3（≥0.5）判定
        # episodic，body 內容本身不變。
        body = "## 狀態\n本次修改僅限 README-ARC.md。\nsession 結束時尚未 commit。\n下一步：開 PR。\n"
        self.assertEqual(episodic_reason("ot-ti-mirror 本地建置環境重現與 SOP", body), "body:session-state:2/3")
        self.assertFalse(classify_noise({}, body).is_noise)   # 不是 deletion-grade

    def test_status_minority_with_real_steps_is_kept(self):
        body = ("用 docker create --rm 建容器。\ncmake 統一 3.31.6。\n~/bin 必須先存在 PATH 才會生效。\n"
                "session 結束時尚未 commit。\n")
        self.assertIsNone(episodic_reason("SOP", body))

    def test_handoff_title_is_episodic(self):
        self.assertEqual(episodic_reason("session-handoff-2026-08-12", "任何內容\n"), "title:session-state")
        self.assertIsNone(episodic_reason("Release Button 現有角色", "DIO24 用途分析。\n"))

    # --- review round 1: strong/weak signal split -------------------------------
    # Reviewer 舉出的三句耐久技術敘述：只含弱訊號（目前狀態／下一步／handoff 其中
    # 一種，且各只出現一次、沒有第二個不同弱訊號佐證），單行 body 一律不得降層。
    def test_current_state_machine_prose_is_not_episodic(self):
        self.assertIsNone(
            episodic_reason("狀態機設計筆記", "目前狀態機的初始化流程如下所述。\n")
        )

    def test_next_step_crc_prose_is_not_episodic(self):
        self.assertIsNone(
            episodic_reason("CRC 驗證流程", "韌體升級的下一步是驗證 CRC checksum。\n")
        )

    def test_handoff_register_prose_is_not_episodic(self):
        self.assertIsNone(
            episodic_reason(
                "CC2674 暫存器對照", "handoff register 在 CC2674 上位於 0x4008_1000。\n"
            )
        )

    # --- diagnosis positives：104 個誤降級案例的實際型態，強訊號單行即降層 -------
    def test_bare_not_yet_commit_is_episodic(self):
        self.assertEqual(episodic_reason("筆記", "尚未 commit\n"), "body:session-state:1/1")

    def test_session_end_not_yet_commit_single_line_is_episodic(self):
        self.assertEqual(
            episodic_reason("筆記", "session 結束時尚未 commit。\n"),
            "body:session-state:1/1",
        )

    def test_scope_limited_not_yet_commit_is_episodic(self):
        self.assertEqual(
            episodic_reason("筆記", "本次修改僅限 README，尚未 commit。\n"),
            "body:session-state:1/1",
        )

    # --- 強訊號只出現在 fenced code block 內：整段視為程式碼，不得觸發降層 -------
    def test_strong_phrase_only_inside_closed_code_fence_is_kept(self):
        body = "```\n尚未 commit\nsession 結束時尚未 commit\n```\n"
        self.assertIsNone(episodic_reason("筆記", body))

    def test_strong_phrase_only_inside_unclosed_code_fence_is_kept(self):
        body = "說明如下。\n```\n尚未 commit\nsession 結束時尚未 commit\n"
        self.assertIsNone(episodic_reason("筆記", body))

    # --- 2 行 body、1 行強訊號 → 剛好卡在 EPISODIC_RATIO=0.5 門檻上 --------------
    def test_two_line_body_one_strong_line_hits_ratio_floor(self):
        body = "cmake 版本鎖定 3.31.6。\n尚未 commit。\n"
        self.assertEqual(episodic_reason("筆記", body), "body:session-state:1/2")

    # --- review round 2 finding：同一 weak pattern 在同一行內重複出現（不重疊
    # span）不該湊出 ≥2 命中。需要「≥2 個不同 pattern」佐證，而非「≥2 個不重疊
    # span」——即使兩個 span 都來自同一個 pattern。 --------------------------
    def test_weak_hit_count_dedupes_repeated_same_pattern(self):
        # round 2b：`status` 已從 weak pattern 移除（見下方 test_repeated_status_
        # word_is_not_episodic），改用仍在清單中的 `handoff` 驗證同一 pattern 重複
        # 出現只算 1 次佐證，不影響本測試原本要驗證的 dedup 語意。
        self.assertEqual(_weak_hit_count("handoff handoff handoff"), 1)

    def test_repeated_weak_word_alone_is_not_episodic(self):
        # 「目前」重複兩次仍只是同一個 weak pattern，不構成兩個不同訊號佐證。
        body = "此驅動目前運作正常，目前未發現異常。\ncmake 版本鎖定 3.31.6。\n"
        self.assertIsNone(episodic_reason("驅動筆記", body))

    def test_repeated_status_word_is_not_episodic(self):
        # round 2b：`status` 已整個從 weak pattern 移除（過於通用，見常數旁註解），
        # 這行現在沒有任何 weak 命中（0，並非「重複同一 pattern 只算 1」），仍應維持
        # 不觸發——保留本測試作為 status 相關硬體敘述的回歸覆蓋。
        body = (
            "The status field mirrors the status register bit for CM3.\n"
            "This is unrelated durable prose line.\n"
        )
        self.assertIsNone(episodic_reason("Register notes", body))

    def test_two_different_weak_words_still_counts(self):
        # 佐證仍然成立的正例：同一行內有 2 個「不同」weak pattern（handoff／尚未），
        # 且皆非 strong 命中。
        body = "handoff 尚未確定，仍在討論中。\ncmake 版本鎖定 3.31.6。\n"
        self.assertEqual(episodic_reason("筆記", body), "body:session-state:1/2")

    # --- review round 2b: reviewer 第三個未解案例（round 2 report 標記為
    # structurally unreachable within round 2 scope）。「handoff status register」是
    # CC2674 真實硬體暫存器名稱，複合名詞裡的 status/handoff 不該被當成 session
    # 交接訊號。修法：strong 的 `handoff (?:status|state|note)` 改為需要冒號／行尾
    # 分隔符才算（比照 `目前狀態：`），且把過於通用的 `status` 從 weak 移除
    # （英文硬體／韌體敘述常見「status register」「status field」，診斷語料的真正
    # 正例都不是靠裸字 `status` 撐起來的）。--------------------------------------
    def test_handoff_status_register_compound_is_not_episodic(self):
        # round 2 report 逐字引用的第三個誤降級案例：CM0/CM3 handoff 暫存器說明。
        body = (
            "HANDOFF register（handoff status register）位於 0x4008_1000，"
            "用於 CM0 與 CM3 之間切換。\n"
            "此暫存器於開機時自動初始化，無須軟體介入。\n"
        )
        self.assertIsNone(episodic_reason("CC2674 暫存器對照", body))

    def test_handoff_status_colon_is_episodic(self):
        # 冒號分隔的 handoff status/note 仍是強訊號（真正的 session 交接陳述）。
        self.assertEqual(
            episodic_reason("筆記", "handoff status: pending push\n"),
            "body:session-state:1/1",
        )
        self.assertEqual(
            episodic_reason("筆記", "Handoff note:\n"),
            "body:session-state:1/1",
        )


    # --- 全支線 review I2：標題規則太鬆 -------------------------------------------
    # 舊規則 `\bhandoff\b|狀態$` 是「一擊即中、body 完全不看」，但這兩個形狀在耐久
    # 技術筆記的標題裡極常見（硬體暫存器名、燈號/狀態對照表），整篇因此被誤降層、
    # 從檢索池消失。改成兩級：強形（session-handoff…／session 交接／交接狀態／
    # handoff status|state|note ＋分隔符或行尾）自己就算數；裸 `handoff`／`狀態$`
    # 只是弱訊號，要 body 裡至少有一行強訊號佐證才降層。

    def test_bare_handoff_title_with_durable_body_is_kept(self):
        body = ("handoff register 在 CC2674 上位於 0x4008_1000。\n"
                "此暫存器於開機時自動初始化，無須軟體介入。\n")
        self.assertIsNone(episodic_reason("CC2674 handoff register 對照", body))

    def test_status_suffix_title_with_durable_body_is_kept(self):
        body = ("LED0 恆亮代表已連線，閃爍代表配對中。\n"
                "燈號由 PWM0 驅動，週期 1 kHz。\n")
        self.assertIsNone(episodic_reason("LED 燈號狀態", body))

    def test_strong_title_forms_demote_on_their_own(self):
        # body 完全是耐久內容也照降：這些措辭只在描述 session/交接自身時才成立。
        durable = "cmake 版本鎖定 3.31.6。\n~/bin 必須先存在 PATH 才會生效。\n"
        for title in ("session-handoff-2026-08-12", "handoff status:", "Handoff note:",
                      "session 交接", "交接狀態"):
            with self.subTest(title=title):
                self.assertEqual(episodic_reason(title, durable), "title:session-state")

    def test_weak_title_demotes_when_body_has_a_strong_line(self):
        # 弱標題 ＋ body 有強訊號行（但比例 1/3 < EPISODIC_RATIO，body 規則自己不會降）。
        body = ("LED0 恆亮代表已連線。\n燈號由 PWM0 驅動。\n本次修改僅限 README。\n")
        self.assertIsNone(episodic_reason("LED 燈號", body))          # 標題無命中 → 不降
        self.assertEqual(episodic_reason("LED 燈號狀態", body), "title:session-state+body")
        self.assertEqual(episodic_reason("CC2674 handoff register", body),
                         "title:session-state+body")

    def test_weak_title_ignores_strong_phrases_inside_code_fence(self):
        # body 的佐證一樣要走 `_episodic_content_lines`：圍籬內的字面文字不算佐證。
        body = "LED0 恆亮代表已連線。\n```\n本次修改僅限 README。\n```\n"
        self.assertIsNone(episodic_reason("LED 燈號狀態", body))


if __name__ == "__main__":
    unittest.main()
