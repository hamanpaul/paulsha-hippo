### Changed

- Issue #39 distillation now uses one declarative external headless CLI router
  for atomization, title repair, and SkillOpt. Tiered fallback, stdin-only
  prompts, minimal non-secret environment, bounded attempts, circuit state,
  cache identity, provenance, and safe failure evidence are enforced.
- Direct Hippo provider HTTP/TCP, API-key/OAuth configuration, and repo-owned
  Gemma proxy/launcher surfaces are removed from the release package.
- Router cache envelopes retain bounded fallback provenance and corrupted
  router envelopes are re-executed instead of replayed as raw output.
