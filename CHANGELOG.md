# Changelog

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
