# Project Registry 檔案契約（project-hippo.yaml）

> **schema_version: 1**（本文件對應）。producer：paulsha-hippo（本 repo）；consumer：cortex（paulshaclaw）等——**檔案契約、零 code 依賴**（雙方各自 parse，不共享程式）。
> 來源：issue #14；spec `docs/superpowers/specs/2026-07-10-all-issues-resolution-design.md` §3.3。

## 1. 路徑契約

- 預設落點：`~/.agents/config/paulsha/project-hippo.yaml`（與 cortex 手寫檔 `project-cortex.yaml` 同層；hippo 不產生、不讀取 `project-cortex.yaml` 內容，僅共享目錄）。
- 程式定位：`paulsha_hippo.paths.project_registry_path(memory_root_value=None)`，優先序與 `projects.yaml`（`projects_config_path`）同構：
  1. `PSC_CONFIG_ROOT` 已設 → `<基底>/.agents/config/paulsha/project-hippo.yaml`（`PSC_CONFIG_ROOT` 形如 `<HOME>/.config/paulshaclaw` 時基底取其上兩層，否則取其本身）。
  2. 呼叫端帶 memory_root → `<memory_root 上一層>/config/paulsha/project-hippo.yaml`。
  3. 否則 `<agents_root>/config/paulsha/project-hippo.yaml`（`agents_root` 預設 `~/.agents`，可由 `HIPPO_AGENTS_ROOT` / `PSC_AGENTS_ROOT` 覆寫）。
- 同目錄固定名輔助檔：lock `.project-hippo.yaml.lock`、暫存 `.project-hippo.yaml.tmp`（consumer 應忽略）。

## 2. Schema（v1）

YAML 子集。producer 只輸出下列結構；consumer 建議寬鬆解析（忽略未知欄位）。

- 檔頭：固定 3 行 `#` 註解（generated 宣告、override 指引、本文件路徑）。
- `schema_version`：int，必填，目前 `1`。
- `projects`：list（空集輸出 inline `projects: []`）。每項：
  - `slug`：str，必填——importer `resolve_project` 產出的 project 識別。**一律由 remote 正規化派生**（raw remote 形，或 config/registry 依 remote 匹配出的 slug）；dir-name / basename fallback slug 不寫入（見 §5 寫入 gate）。**與 `roots` 同源**：session cwd 位於 linked worktree 時，slug 以歸併後的主 repo root 重新推導（不得記 worktree 目錄名 slug 配主 repo root 的矛盾 mapping）。
  - `roots`：list[str]——絕對路徑；**linked worktree 一律歸併為主 repo root**（`git rev-parse --git-common-dir`）。
  - `remotes`：list[str]——正規化 remote 識別（見 §3）。
  - `aliases`：list[str]——v1 producer 恆輸出 `[]`（hippo 無 alias 發現來源；欄位保留前向相容），inline 形式。
- 空 list 輸出 inline `[]`；非空輸出 block list（`      - item`）。

## 3. Remote 正規化（去 credential、統一 scheme）

與 `paulsha_hippo.importer.project_resolver.normalize_remote` 同一實作：

- 去 credential：`https://token@github.com/...` → host 起首；scp 形 `user@host:...` 剝除 `user@`。
- 去 scheme：統一為無 scheme 的 `host/owner/repo` 識別形。
- host 小寫；`github.com` 之 owner/repo 一併小寫；非預設 port 保留為 `host:port`（ssh github 22 除外）。
- 去尾 `.git`（不分大小寫）、去尾 `/`。
- `owner/repo` 短形補 `github.com/` 前綴。

## 4. Determinism（byte-level 規則）

同一組輸入必產生逐 byte 相同輸出（producer contract test 錨定，見 §8）：

- 編碼 UTF-8、換行 LF、檔尾恰一個換行。
- `projects` 依 `slug` 字典序排序；`roots`/`remotes`/`aliases` 各自去重後字典序排序。
- 縮排固定：list 項 `  - slug: ...`、欄位 4 空格、子項 `      - `。
- **Scalar quoting**：所有動態字串值（`slug`、`roots`/`remotes` 各項、`aliases` 各項）一律輸出 YAML **double-quoted scalar**，escape 規則僅兩條：`\` → `\\`、`"` → `\"`，其餘字元原樣。double-quoted 樣式對 `#`（註解起始）、`: `（巢狀 mapping）、`[` `]` `,`（flow 語法）、前導／尾隨空白等特殊字元全部安全——標準 YAML parser 讀回值必等於原值。靜態 token（key、`schema_version` 整數值、空 list `[]`）不加引號。值域為單行字串（slug／絕對路徑／正規化 remote；換行與控制字元不在 v1 值域）。

## 5. 寫入協定（producer 側）

- **Opt-in**：`~/.config/paulsha-hippo/config.yaml` 設 `project_registry.auto_write: true` 才寫（預設 off）。
- 觸發點：importer ingest 完成（dry-run 不寫）；`slug` 為 `_unknown`、或 roots 與 remotes 全空的 session 不寫。
- **寫入 gate（remotes 必須是真 remote）**：僅當 slug 由 remote 正規化派生（session 的 git remote，或顯式 payload remote 鍵 `remote_url` / `remote` 經 config/registry 匹配）才寫；驗證逐 remote 套用——只有個別通過驗證的 remote 寫入 `remotes`，payload 夾帶而未通過驗證的不相干 remote 不得搭便車落盤；dir-name / basename fallback slug 一律 skip（記 debug log）——杜絕 remoteless worktree 的自我矛盾 mapping，與已刪 cwd / git 逾時下「垃圾 slug 掛真 remote」的自我強化污染。path 形 `repo` 欄位（toplevel 路徑輸入）僅供解析比對，不得寫入 `remotes`。
- 互斥：固定名 lock `flock(LOCK_EX)`；原子性：寫 `.project-hippo.yaml.tmp` 後 `os.replace`；內容未變則跳寫（冪等）。
- Fail-open：registry 寫入失敗不影響 ingest 主流程（記 warning）。
- **分權**：generated 檔不允許手改（檔頭註明；手改內容會在下次寫入被 canonical 化覆蓋）。使用者 override 一律放 manual 檔——hippo 側 legacy `projects.yaml`，或 cortex 側 `project-cortex.yaml`。

## 6. 讀取端 merge 語義

- **hippo 讀取端**（`resolve_project` 預設載入）union-read：legacy `projects.yaml` ∪ `project-hippo.yaml`。同 slug 併 roots/remotes/aliases（manual 條目與值序在前）；alias 衝突 manual 優先（記 warning）。legacy `projects.yaml` 不搬移、不改寫（非破壞過渡）。
- **cortex 讀取端**：`project-cortex.yaml`（curated intent）∪ `project-hippo.yaml`（discovered activity），union 去重＝真正監控集——cortex 側行為不在本 repo 範圍，本文件僅保證檔案契約。
- Consumer 解析建議：忽略未知欄位；`schema_version` 大於已知版本時 best-effort 讀 v1 欄位。

## 7. 版本演進

- 破壞性變更（欄位改名／語義改變／**標準 YAML 解析結果改變**的格式變更）→ `schema_version` +1，並同步更新本文件與 producer contract test fixture。
- 純新增欄位 → 不 bump（consumer 忽略未知欄位）。
- **語義不變的表層格式調整 → 不 bump**：canonical bytes 改變、但標準 YAML 解析結果逐值相同者（如 scalar quoting 樣式），於本文件記載規則並同步 fixture 即可。前例（2026-07）：動態值由 plain scalar 改為一律 double-quoted（§4），修正 `#`／`: `／flow 字元被標準 parser 誤讀的缺陷——以標準 YAML parser 讀取的 consumer 不受影響；手寫 parser 的 consumer 需支援 double-quoted scalar（unquote + `\\`／`\"` unescape），並容忍歷史檔案的 plain 形（producer 下次寫入即 canonical 化為 quoted）。
- **Producer 前向防護（不降級）**：`record_discovery` 讀到既有檔 `schema_version` **高於**自身支援版本時，拒絕寫入並記 warning（回「未變更」）——舊版 producer 不得把新版檔案 parse→render 降級重繪（會刪除未知欄位；混版部署下即永久資料遺失），除非日後提供顯式 migration。低版→現版的 canonical 化升級寫入不受此限。

## 8. Canonical example（producer contract test 錨點）

下列範例由 `tests/test_project_registry.py::ProducerContractTests` 以固定輸入驅動真實 producer（`record_discovery`）產出，與 `tests/fixtures/registry/project-hippo.expected.yaml` 及本 block **逐 byte 比對**——三方不一致即測試 FAIL。

<!-- contract-fixture:tests/fixtures/registry/project-hippo.expected.yaml -->
```yaml
# GENERATED — 本檔由 paulsha-hippo 自動產生（project registry discovery record），請勿手改。
# 使用者 override 請寫 manual 檔（projects.yaml / project-cortex.yaml），詳見契約文件。
# contract: docs/project-registry-contract.md
schema_version: 1
projects:
  - slug: "github.com/acme/widget"
    roots:
      - "/data/projects/widget"
    remotes:
      - "github.com/acme/widget"
    aliases: []
  - slug: "scratch-notes"
    roots:
      - "/data/scratch/notes"
    remotes: []
    aliases: []
```
