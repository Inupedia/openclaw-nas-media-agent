# Existing OpenClaw Deployer Verification Record

**Date:** 2026-07-21
**Branch:** `feat/existing-openclaw-deployer`
**Plan:** `docs/superpowers/plans/2026-07-20-existing-openclaw-deployer.md`

## Automated evidence

- Full repository suite: `python3 -m unittest discover -s tests -v`
  - Result: **334 tests passed**, zero failures and zero errors.
- Static parsing:
  - `deploy/schemas/config.schema.json`: valid JSON.
  - `config/routing.json`: valid JSON.
  - deployment YAML and GitHub Actions workflows: valid YAML.
  - changed Python modules: `py_compile` succeeds.
- Diff hygiene:
  - `git diff --check` succeeds.
  - no runtime secrets, generated runtime state, temporary patch jobs, or writable CI permissions are part of the intended final change.

## Approved-design coverage

| Requirement | Evidence |
|---|---|
| `deploy/config.yaml` is the source of truth | strict loader/schema, initializer and CLI end-to-end tests |
| Secret directory/file modes | `SecretStore`, initializer and runtime tests enforce `0700`/`0600` |
| Immutable 30-minute plans | plan expiry and four-dimension drift tests |
| Immutable container images | digest-only `versions.yaml` and renderer tests |
| QAS config/API/browser fallback | locked v0.8.7 adapter and fallback tests |
| PanSou degraded state | adapter and executor aggregation tests |
| Existing and managed proxy modes | proxy validation, internal-only sing-box Compose and default-plan integration tests |
| aria2 identity, RPC and mount mapping | adapter and default verification integration tests |
| Narrow aria2 permissions | permission-strategy and effective-write tests |
| Existing OpenClaw integration | supported-profile adapter, override and Skill installation tests |
| Fixed command allowlist | OpenClaw adapter and Skill contract tests |
| File-based credentials | OpenClaw override, Skill metadata and resource-agent hydration tests |
| Transaction rollback and resume | executor, backup and rollback tests |
| Automatic safe verification | executor and default callback integration tests |
| Confirmed full verification | three independent gates and bounded-download tests |
| Protected formal media roots | config, routing, path guard and L4 verification tests |

## Final integration findings resolved

The final coverage review found that QAS/OpenClaw, PanSou/proxy and aria2 adapters existed independently but were not all invoked by the default plan. The default flow now:

1. renders and writes dependency, OpenClaw override and optional managed-proxy Compose files;
2. derives the locked QAS API token into a private runtime secret file;
3. initializes and verifies QAS;
4. installs/configures/verifies the OpenClaw Skill and fixed `mediactl` allowlist;
5. verifies aria2 runtime identity, authenticated RPC and shared mount mapping;
6. verifies PanSou and optional sing-box, preserving a usable deployment as `degraded` when optional discovery components fail;
7. runs the non-mutating safe business verification automatically after apply.

## External scenario status

The automated suite and fixture end-to-end flow are complete. Two plan steps require resources not available in this execution environment and therefore are **not claimed as completed**:

- a read-only dry run against an actual UGOS/Linux NAS with the user's existing OpenClaw installation;
- a controlled apply/safe verification/rollback against non-production QAS, PanSou, aria2 and OpenClaw services, plus optional confirmed full verification using a user-provided legal test share URL.

The pull request should remain a draft until that controlled environment run is recorded, or the maintainer explicitly accepts fixture/CI verification for the first review round.
