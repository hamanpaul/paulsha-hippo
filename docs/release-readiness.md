# 0.1.1 release candidate readiness

This repository contains the implementation side of Issue #34/#39. The
authoritative machine-readable gate list is
[`reports/verify/release-readiness-matrix.json`](../reports/verify/release-readiness-matrix.json).
It intentionally starts with every gate `pending`, with no candidate commit or
wheel hash, until the main agent runs the artifact-bound and live checks.

## Implemented local contract

- Distillation uses the canonical external headless profile router. Hippo does
  not own HTTP/TCP provider clients, API keys, OAuth, provider URLs, or secret
  stores.
- `hippo install all --force --dry-run` plans only manifest-owned changes;
  real apply additionally requires a reviewed `--runtime-plan` covering writer/
  service fencing, compensation, doctor, and enabled-profile probes. Protected
  memory, ledger, index, recovery, project-registry, shell-rc, launcher, and
  credential paths are rejected. Shared JSON uses owned-entry compensation.
- `hippo upgrade plan|prepare|apply|rollback` stages one hash-bound artifact
  with a write-ahead manifest and writer fence. Real apply requires a complete
  reviewed `--command-plan`, including the pipx package switch, rollback switch,
  registry producer/consumer attestation, doctor, and effective profile/hash.
- Publication journals keep targets and relation edges invisible until a
  matching commit marker. Incomplete journals are recovered before the next
  atomization pass.
- `hippo recovery plan --source-manifest <prior-manifest>` 沿用先前已審查的
  frozen source set，但以目前安裝候選版重新產生 code/config/registry pins 與
  planned artifacts；live archive 後續新增的 active session 不會擴張既定
  recovery scope，authority manifest 自身漂移則 fail closed。

## Evidence boundary

The following are not claimed by local tests: installed systemd hook/service
chains, service-effective profile probes, live fallback against real CLIs,
production recovery, the 53-session disposition, three timer-triggered soak
cycles, consumer `offered → Read`, release tag, wheel publication, or Issue
#34 closure. The main agent must attach rerunnable evidence for those gates to
the readiness matrix using the exact candidate commit and wheel SHA-256.

The timer/load gate remains unchanged: this implementation does not alter the
hourly timer or its load/memory eligibility behavior.
