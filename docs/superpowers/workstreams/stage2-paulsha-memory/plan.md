# stage2-paulsha-memory / plan

## Scope

- Stage: 2
- 目標: 建立 `paulsha-memory`（由 `obs-auto-moc` 調校）記憶中樞流程
- 先決依賴: Stage 0 baseline
- In scope: `paulshaclaw/memory/`、`paulshaclaw/janitor/`、`openspec/specs/stage2/`
- Out of scope: Stage 1 core/bot 實作、Stage 3 gate 引擎

## Steps

### Phase 1: Scope 與邊界
1. 固化 Stage2 scope（含 atomize 邊界、non-runtime 聲明）。

### Phase 2: Memory pipeline
1. 建立 inbox -> work-centric -> knowledge 路由規則。
2. 建立 decayed/reactivation 事件流程。

### Phase 3: 服務化與驗證
1. 建立 janitor service 流程與排程。
2. 完成 Stage2 integration 測試與證據樣板。

## Relevant files

- `openspec/specs/stage2/scope.md`
- `paulshaclaw/memory/`
- `paulshaclaw/janitor/`
- `openspec/specs/stage0/tool-matrix.md`

## Verification

1. importer/classifier/replay 路徑可完整跑通。
2. decayed/reactivation 可正確寫入事件。
3. Stage2 integration 測試通過並有證據。

## Decisions

- `paulsha-memory` 最終需回寫 `custom-skills`，回寫前需通過 Stage 2 測試。
