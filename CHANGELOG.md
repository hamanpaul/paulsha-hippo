# Changelog

本專案所有重大變更都會記錄在此檔案。

格式基於 [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-TW/1.1.0/)，
本專案遵循 hamanpaul project policy v1.0.12。

## [Unreleased]

### Added
- 自 `hamanpaul/new-project-template` 建立 repo 骨架（agent 檔 symlink 模式、Policy Check／Tests CI）

### Changed
- **conventions 引擎 1.0.7 → 1.0.12**：`.paul-project.yml` 與 CLAUDE.md 版本註記同步、`Policy Check` workflow re-pin 引擎到 1.0.12 SHA `5829015`（`policy_version` / `policy_engine_ref` 同步）；宣告 `tier: shareable` 啟用 R-21 機密掃描（deident gate day-1）
