# Changelog

## 0.4.3

### Behavior / reliability

- `search --update` expands nested Quark share trees when shallow preview hides newer episodes.
- Update mode only chases gap fills and the contiguous next episode (`local_max+1`), skipping short-drama jump packs.
- Quarantined tasks no longer reserve episode slots in `pending_episode_refs`.
- Download manifests reserve only episodes present in selected files (not the full candidate `newEpisodes` list).
- Organize supports merge-into-existing title folders and EXDEV (cross-device) copy fallback.
- `mediactl` hydrates missing skill env from OpenClaw `openclaw.json` so chat/tool exec sees `QAS_*` / `ARIA2_*` without manual injection.

### Tests

- Nested update inventory, tip-narrowing, quarantine pending exclusion, organize merge/conflict, env hydrate.

## 0.4.2

### Contract / runtime

- Replaced `commands.yaml` + custom parser with `config/commands.json` (stdlib JSON + schema validation).
- Multi-dimensional effect fields: `reads` / `writes` / `external_mutation` / `media_mutation`.
- Recovery `preconditions` and retry policy are enforced from the contract (no hard-coded allowed states).
- Stable error codes: `CONFIRMATION_REQUIRED`, `RECOVERY_DISABLED`, `PLAN_STALE`, `ORGANIZE_PLAN_STALE`, etc.
- `downloads list` returns `attentionRequired` + top-level `nextAction=review_download_tasks` when needed.
- `downloads.validate` no longer requires aria2 RPC.
- Recover execute verifies immutable `manifestHash` (fid/name/size) before attempt++; cloud changes → `PLAN_STALE`.
- Organize execute re-hashes source files; mismatch → `ORGANIZE_PLAN_STALE` / `revalidate_download`.
- `.ready` / `.quarantine` use `0750` (not world-writable); only downloads root + `.incoming` stay `0777`.
- `ensure_aria2_writable(..., downloads_root=)` refuses chmod outside managed downloads root.
- `check-ready` works with `qas=None` (QAS soft-checked when credentials exist).

## 0.4.1

### Behavior / contract

- Skill no longer uses OpenClaw `requires.env` for QAS; protected-library refusal loads without QAS.
- Runtime dependencies come from `config/commands.yaml` (`requires_services` / `requires_env` / `confirmation`).
- CLI enforces `--confirmed` and Quark recovery env gates via the same contract file.
- `check-ready` no longer requires QAS (aria2 only).
- `library lookup` / `organize` remain usable without QAS credentials.

### Docs / tests

- Skill version `0.4.1`; safety docs describe load-vs-runtime dependency split.
- Contract parser + enforcement tests; skill contract asserts absent Skill-level `requires.env`.

## 0.4.0

### Behavior

- **Breaking (Agent contract):** `downloads list` / `downloads show` are read-only sync again. They no longer auto-recover aria2 error 16 or re-push Quark downloads.
- **New:** `downloads recover plan TASK_ID` and `downloads recover execute PLAN_ID --confirmed`.
- Recovery requires eligible state (`error` / `partial_failed`, error codes `{16}`, no valid staging bytes), attempt budget, cooldown, and `--confirmed`.
- Attempt counter increments **before** Quark/aria2 calls; failures are persisted with `recovery.lastErrorCode` / `cooldownUntil`.
- Formal `recovery` object and `nextAction` values: `confirm_recover`, `enable_quark_recovery`, `recovery_exhausted`.

### Security / config

- Quark direct recovery is **off by default** (`QUARK_RECOVERY_ENABLED=false`).
- Optional: `QUARK_RECOVERY_MAX_ATTEMPTS` (default 2), `QUARK_RECOVERY_COOLDOWN_SECONDS` (default 300).
- Skill / safety docs describe credential boundaries (QAS Cookie, drive-pc.quark.cn, no Cookie in JSON/logs).

### Docs / tests

- Skill version `0.4.0`; README and references no longer show bare `execute PLAN_ID` or auto-recover on list/show.
- Contract tests cover no-auto-recover on list/cancel, confirmed recover, attempt accounting, and documented command parsing.

### Migration

- Existing tasks keep working; `recovery_json` / `recover_attempts` columns are added on open.
- To allow recovery on NAS: set `QUARK_RECOVERY_ENABLED=true` in OpenClaw skill env, then use recover plan → confirm → execute.
- Old one-shot `downloads recover TASK_ID` is removed; use plan/execute.

## 0.3.0

Prior Slim Skill + Quark cookie normalize / staging permission fixes (see git history `7f27cb6`, `daaedc5`).
