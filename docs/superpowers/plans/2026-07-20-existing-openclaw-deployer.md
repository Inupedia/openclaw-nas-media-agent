# Existing OpenClaw Deployer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-ready `existing-openclaw` deployment path that discovers an NAS safely, creates an immutable plan, deploys or reuses QAS, PanSou, aria2 and an optional proxy, configures the existing OpenClaw Skill and command allowlist, verifies the real chain, and rolls back failures.

**Architecture:** `deploy/cli.py` is a standard-library launcher. It creates a private deployment virtual environment and delegates to focused modules under `deploy/installer/`. Discovery is read-only, planning is immutable and expires after 30 minutes, apply is journaled and reversible, and every external command is injected through a runner so unit tests use fixtures rather than a live NAS.

**Tech Stack:** Python 3.10+, `unittest`, PyYAML 6.0.3, Jinja2 3.1.6, jsonschema 4.26.0, Playwright 1.61.0, Docker CLI, Docker Compose v2, standard-library `urllib`, JSON/YAML, POSIX permissions and optional POSIX ACL.

## Global Constraints

- This plan implements only `deployment.mode: existing-openclaw`.
- First-class platforms are UGREEN UGOS and standard Linux Docker hosts.
- Synology, QNAP, TrueNAS and Unraid remain experimental and must be reported as such.
- `deploy/config.yaml` is the only source of truth for non-sensitive deployment configuration.
- Secrets live in `deploy/secrets/`; the directory mode is `0700` and ordinary secret files are `0600`.
- Secrets never appear in Git, backups, plans, stdout JSON, stderr logs or reports.
- Plan validity is exactly 30 minutes.
- A plan becomes invalid when config, secret metadata, discovery facts or managed files drift.
- `apply` requires both `--plan-id` and `--confirmed`.
- `verify --level full` requires `verification.allow_real_download: true`, a legal user-provided Quark test share URL and `--confirmed`.
- Full verification stops after `organize plan`; it never runs `organize execute`.
- Final deployment state is exactly one of `ready`, `degraded`, `manual_action_required`, `failed` or `rolled_back`.
- `security_block` failures cannot be bypassed with a force option.
- Formal media libraries are never recursively chmoded, deleted or overwritten.
- aria2 identity is discovered; the deployer never assumes `nobody:nogroup`.
- Committed container images use immutable digests; `latest` is forbidden.
- Every task uses red-green-refactor TDD and ends with a focused commit.

## File Map

### Entry points and contracts

- `deploy/cli.py`: bootstrap and re-exec only.
- `deploy/requirements.in`: exact top-level Python dependencies.
- `deploy/requirements.lock`: full transitive lock with hashes.
- `deploy/config.example.yaml`: documented non-secret configuration.
- `deploy/versions.yaml`: immutable container image references and adapter names.
- `deploy/schemas/config.schema.json`: configuration schema.

### Deployment package

- `deploy/installer/cli.py`: parser and command dispatch.
- `deploy/installer/models.py`: shared immutable dataclasses and enums.
- `deploy/installer/errors.py`: typed deployment exceptions.
- `deploy/installer/output.py`: one-JSON-document stdout contract.
- `deploy/installer/command.py`: injected subprocess runner.
- `deploy/installer/config.py`: YAML load, schema validation and canonical digest.
- `deploy/installer/secrets.py`: secret permission checks and metadata digest.
- `deploy/installer/redaction.py`: recursive redaction.
- `deploy/installer/runtime.py`: runtime paths, locks and atomic writes.
- `deploy/installer/discovery.py`: host, Docker, container, network and mount discovery.
- `deploy/installer/planning.py`: immutable change plan generation and validation.
- `deploy/installer/versions.py`: version-lock validation and maintainer resolution helper.
- `deploy/installer/renderer.py`: strict template rendering and static validation.
- `deploy/installer/permissions.py`: aria2 write-permission strategy.
- `deploy/installer/backup.py`: secret-free backups.
- `deploy/installer/executor.py`: journaled apply and resume.
- `deploy/installer/rollback.py`: reverse journal execution.
- `deploy/installer/verifier.py`: L0-L6 verification.
- `deploy/installer/platforms/linux.py`: Linux capabilities.
- `deploy/installer/platforms/ugos.py`: UGOS detection and capabilities.
- `deploy/installer/adapters/openclaw_v1.py`: supported OpenClaw configuration profile.
- `deploy/installer/adapters/qas_v1.py`: pinned QAS configuration/API profile.
- `deploy/installer/adapters/qas_browser.py`: Playwright fallback for known QAS UI.
- `deploy/installer/adapters/pansou.py`: PanSou configuration and source verification.
- `deploy/installer/adapters/proxy.py`: optional managed sing-box profile.
- `deploy/installer/adapters/aria2.py`: RPC, identity, mount and write probes.

### Templates and fixtures

- `deploy/templates/compose.dependencies.yml.j2`: QAS, PanSou and aria2.
- `deploy/templates/compose.proxy.yml.j2`: optional sing-box service.
- `deploy/templates/compose.openclaw.override.yml.j2`: override for the discovered OpenClaw service.
- `deploy/templates/routing.json.j2`: generated Skill routing.
- `tests/fixtures/docker/`: sanitized Docker and Compose responses.
- `tests/fixtures/openclaw-v1/`: sanitized supported OpenClaw configuration.
- `tests/fixtures/qas-v1/`: sanitized QAS config, API and UI fixtures.

### Existing files modified

- `.gitignore`
- `scripts/download_fs.py`
- `scripts/resource_agent.py`
- `tests/test_download_fs.py`
- `deploy/docker-compose.dependencies.yml`
- `README.md`
- `docs/AGENT_DEPLOY.md`

---

### Task 1: Add the bootstrap CLI, exact dependency input and hashed lock

**Files:**
- Create: `deploy/cli.py`
- Create: `deploy/requirements.in`
- Create: `deploy/requirements.lock`
- Create: `deploy/installer/__init__.py`
- Create: `deploy/installer/cli.py`
- Create: `deploy/installer/errors.py`
- Create: `deploy/installer/output.py`
- Create: `tests/deploy/__init__.py`
- Create: `tests/deploy/test_cli.py`

**Interfaces:**
- Produces: `deploy.installer.cli.main(argv: list[str] | None = None) -> int`
- Produces: `DeploymentError`
- Produces: `result_payload(...) -> dict[str, object]`
- Produces: `emit(payload: dict[str, object], stream: TextIO) -> None`

- [ ] **Step 1: Write the failing output-contract test**

```python
class OutputContractTests(unittest.TestCase):
    def test_emit_writes_exactly_one_json_document(self):
        stream = io.StringIO()
        emit(result_payload(ok=True, status="ready", next_action="none"), stream)
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["status"], "ready")
```

- [ ] **Step 2: Run the test and confirm the import failure**

Run: `python3 -m unittest tests.deploy.test_cli.OutputContractTests -v`

Expected: `ModuleNotFoundError` for `deploy.installer.output`.

- [ ] **Step 3: Implement the exception and output types**

```python
class DeploymentError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: str = "failed",
        next_action: str = "review_error",
        severity: str = "blocking",
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.next_action = next_action
        self.severity = severity
        self.details = dict(details or {})


def result_payload(*, ok: bool, status: str, next_action: str,
                   data=None, warnings=None, errors=None) -> dict[str, object]:
    return {
        "ok": ok,
        "status": status,
        "nextAction": next_action,
        "data": dict(data or {}),
        "warnings": list(warnings or []),
        "errors": list(errors or []),
    }
```

- [ ] **Step 4: Add launcher tests for private-venv preference and loop prevention**

```python
class LauncherTests(unittest.TestCase):
    def test_runtime_python_prefers_private_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / ".deploy-venv" / "bin" / "python"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("", encoding="utf-8")
            runtime.chmod(0o755)
            self.assertEqual(resolve_runtime_python(root), runtime)
```

- [ ] **Step 5: Implement `deploy/cli.py` using only the standard library**

The launcher creates `.deploy-venv`, installs with `pip install --require-hashes -r deploy/requirements.lock`, sets `OPENCLAW_DEPLOY_BOOTSTRAPPED=1`, and re-execs `python -m deploy.installer.cli`. Bootstrap failure emits one JSON error with `nextAction: install_python_venv_or_dependencies`.

- [ ] **Step 6: Commit exact top-level dependency input**

`deploy/requirements.in`:

```text
Jinja2==3.1.6
jsonschema==4.26.0
playwright==1.61.0
PyYAML==6.0.3
```

Generate the lock in a clean Python 3.10 virtual environment:

```bash
python3 -m venv .deploy-lock-venv
.deploy-lock-venv/bin/python -m pip install pip-tools==7.6.0
.deploy-lock-venv/bin/pip-compile --generate-hashes --output-file deploy/requirements.lock deploy/requirements.in
rm -rf .deploy-lock-venv
```

- [ ] **Step 7: Add parser smoke tests**

Test `init`, `discover`, `plan`, `apply`, `verify`, `rollback` and `versions check`. Unimplemented handlers return structured `manual_action_required`, never a traceback.

- [ ] **Step 8: Run focused and full tests**

```bash
python3 -m unittest tests.deploy.test_cli -v
python3 -m unittest discover -s tests -v
```

- [ ] **Step 9: Commit**

```bash
git add deploy/cli.py deploy/requirements.in deploy/requirements.lock deploy/installer tests/deploy
git commit -m "feat(deploy): add bootstrap CLI and locked runtime"
```

---

### Task 2: Define shared models, runtime state and atomic JSON writes

**Files:**
- Create: `deploy/installer/models.py`
- Create: `deploy/installer/runtime.py`
- Create: `tests/deploy/test_models.py`
- Create: `tests/deploy/test_runtime.py`

**Interfaces:**
- Produces: `DeploymentStatus`, `Severity`, `ChangePhase`
- Produces: `Change`, `DeploymentPlan`, `ComponentResult`, `VerificationResult`
- Produces: `RuntimePaths.for_project(project_root: Path) -> RuntimePaths`
- Produces: `atomic_write_json(path: Path, payload: Mapping[str, object]) -> None`

- [ ] **Step 1: Write enum and serialization tests**

```python
class ModelTests(unittest.TestCase):
    def test_status_values_are_closed(self):
        self.assertEqual(
            {item.value for item in DeploymentStatus},
            {"ready", "degraded", "manual_action_required", "failed", "rolled_back"},
        )
```

- [ ] **Step 2: Implement frozen dataclasses and explicit `to_dict()` methods**

Every serialized field uses camelCase at the JSON boundary and snake_case internally. Unknown status or severity strings raise `ValueError` during deserialization.

- [ ] **Step 3: Write atomic-write and permission tests**

Assert runtime directories are `0700`, JSON files are `0600`, interrupted writes leave the previous file intact, and symlink destinations are rejected.

- [ ] **Step 4: Implement `RuntimePaths`**

Use these exact paths:

```text
deploy/runtime/plan.json
deploy/runtime/reports/discovery.json
deploy/runtime/reports/apply.json
deploy/runtime/reports/verify.json
deploy/runtime/backups/{deployment_id}/
deploy/runtime/journals/{deployment_id}.json
```

Use `secrets.token_urlsafe(18)` for plan and deployment identifiers.

- [ ] **Step 5: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_models tests.deploy.test_runtime -v
python3 -m unittest discover -s tests -v
git add deploy/installer/models.py deploy/installer/runtime.py tests/deploy
git commit -m "feat(deploy): add immutable runtime models"
```

---

### Task 3: Implement schema-validated config, secret isolation and redaction

**Files:**
- Create: `deploy/config.example.yaml`
- Create: `deploy/schemas/config.schema.json`
- Create: `deploy/installer/config.py`
- Create: `deploy/installer/secrets.py`
- Create: `deploy/installer/redaction.py`
- Create: `tests/deploy/test_config.py`
- Create: `tests/deploy/test_secrets.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `DeploymentConfig`
- Produces: `load_config(path: Path) -> DeploymentConfig`
- Produces: `config_digest(config: DeploymentConfig) -> str`
- Produces: `SecretStore.read(name: str) -> str`
- Produces: `SecretStore.metadata_digest(names: Collection[str]) -> str`
- Produces: `redact(value: object, secret_values: Collection[str]) -> object`

- [ ] **Step 1: Write failing schema tests**

```python
class ConfigTests(unittest.TestCase):
    def test_rejects_full_stack_in_phase_one(self):
        raw = minimal_config()
        raw["deployment"]["mode"] = "full-stack"
        with self.assertRaisesRegex(DeploymentError, "existing-openclaw"):
            load_config(write_yaml(raw))

    def test_missing_movie_library_is_security_block(self):
        raw = minimal_config()
        del raw["nas"]["libraries"]["movie"]
        with self.assertRaises(DeploymentError) as ctx:
            load_config(write_yaml(raw))
        self.assertEqual(ctx.exception.severity, "security_block")
```

- [ ] **Step 2: Define the schema**

Set `additionalProperties: false` for every object. Enumerate deployment mode to `existing-openclaw`, service mode to `auto|reuse|managed|disabled`, proxy mode to `none|existing|managed`, and platform to `auto|ugos|linux|experimental`.

- [ ] **Step 3: Construct immutable configuration dataclasses**

Normalize all filesystem values to absolute `Path` objects. Reject NUL bytes, relative paths, empty strings and formal media libraries that are descendants of the download root.

- [ ] **Step 4: Write secret-permission and redaction tests**

```python
class SecretTests(unittest.TestCase):
    def test_rejects_group_readable_secret(self):
        secret = self.root / "qas_token"
        secret.write_text("token-123", encoding="utf-8")
        secret.chmod(0o640)
        with self.assertRaises(DeploymentError) as ctx:
            self.store.read("qas_token")
        self.assertEqual(ctx.exception.severity, "security_block")

    def test_redacts_embedded_values(self):
        value = {"url": "http://service/?token=token-123", "items": ["token-123"]}
        self.assertEqual(
            redact(value, ["token-123"]),
            {"url": "http://service/?token=***", "items": ["***"]},
        )
```

- [ ] **Step 5: Implement `SecretStore`**

Reject secret names containing `/`, `\\` or `..`. Require root mode `0700`, file mode `0600`, regular files and no symlink traversal. Trim one trailing newline only. `repr` exposes names, never values.

- [ ] **Step 6: Extend `.gitignore`**

```gitignore
.deploy-venv/
deploy/config.yaml
deploy/secrets/
deploy/runtime/
```

- [ ] **Step 7: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_config tests.deploy.test_secrets -v
python3 -m unittest discover -s tests -v
git add .gitignore deploy/config.example.yaml deploy/schemas deploy/installer/config.py deploy/installer/secrets.py deploy/installer/redaction.py tests/deploy
git commit -m "feat(deploy): add validated config and secret isolation"
```

---

### Task 4: Add deterministic command execution and read-only host discovery

**Files:**
- Create: `deploy/installer/command.py`
- Create: `deploy/installer/discovery.py`
- Create: `deploy/installer/platforms/__init__.py`
- Create: `deploy/installer/platforms/linux.py`
- Create: `deploy/installer/platforms/ugos.py`
- Create: `tests/deploy/test_command.py`
- Create: `tests/deploy/test_discovery.py`
- Create: `tests/fixtures/docker/containers.json`
- Create: `tests/fixtures/docker/openclaw-inspect.json`
- Create: `tests/fixtures/docker/networks.json`

**Interfaces:**
- Produces: `CommandResult`
- Produces: `CommandRunner.run(args: Sequence[str], timeout: int = 30) -> CommandResult`
- Produces: `discover(config: DeploymentConfig, runner: CommandRunner) -> DiscoveryReport`

- [ ] **Step 1: Write command-runner tests**

Assert argv lists are passed with `shell=False`, timeouts map to `DISCOVERY_COMMAND_TIMEOUT`, secret sentinels are redacted from failures, and nonzero return codes are represented without automatic exceptions.

- [ ] **Step 2: Implement the runner**

```python
completed = subprocess.run(
    list(args),
    cwd=cwd,
    env=env,
    text=True,
    capture_output=True,
    timeout=timeout,
    check=False,
)
```

Do not expose a command-string API.

- [ ] **Step 3: Write fixture-driven discovery tests**

```python
class DiscoveryTests(unittest.TestCase):
    def test_identifies_unique_openclaw_compose_service(self):
        report = discover(self.config, FixtureRunner(self.fixture_map))
        self.assertEqual(report.openclaw.container_name, "openclaw-gateway")
        self.assertEqual(report.openclaw.compose_service, "gateway")
        self.assertEqual(report.platform.kind, "ugos")
```

Also test zero candidates and two equally valid candidates return `manual_action_required` with distinct `nextAction` values.

- [ ] **Step 4: Implement read-only discovery calls**

Use runner arguments built from real variables:

```python
runner.run(["uname", "-s"])
runner.run(["uname", "-m"])
runner.run(["cat", "/etc/os-release"])
runner.run(["docker", "version", "--format", "{{json .}}"])
runner.run(["docker", "compose", "version", "--short"])
runner.run(["docker", "ps", "-a", "--format", "{{json .}}"])
runner.run(["docker", "network", "ls", "--format", "{{json .}}"])
runner.run(["docker", "inspect", candidate_name])
runner.run(["docker", "compose", "-p", compose_project, "config", "--format", "json"])
runner.run(["stat", "-c", "%a:%u:%g:%F", str(target_path)])
runner.run(["df", "-P", str(target_path)])
```

Discovery must not create directories, networks, files or containers.

- [ ] **Step 5: Implement OpenClaw candidate scoring**

Require OpenClaw name/image evidence, Compose labels, a writable workspace bind mount and a Compose service that matches the running container. Unsupported Docker-run-only installations return `nextAction: convert_openclaw_to_compose_or_configure_manually`.

- [ ] **Step 6: Implement UGOS and Linux capability detection**

Store `architecture`, `supports_compose_v2`, `supports_posix_acl`, filesystem types and platform confidence. Do not infer write behavior from platform name alone.

- [ ] **Step 7: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_command tests.deploy.test_discovery -v
python3 -m unittest discover -s tests -v
git add deploy/installer/command.py deploy/installer/discovery.py deploy/installer/platforms tests/deploy tests/fixtures/docker
git commit -m "feat(deploy): add read-only NAS and Docker discovery"
```

---

### Task 5: Add immutable plans, conflict detection, drift checks and expiry

**Files:**
- Create: `deploy/installer/planning.py`
- Create: `tests/deploy/test_plan.py`
- Modify: `deploy/installer/cli.py`

**Interfaces:**
- Produces: `build_plan(config, secrets, discovery, changes, now) -> DeploymentPlan`
- Produces: `validate_plan(plan, current_facts, now) -> None`

- [ ] **Step 1: Write expiry and drift tests**

```python
class PlanTests(unittest.TestCase):
    def test_plan_expires_after_thirty_minutes(self):
        plan = make_plan(created_at=1000, expires_at=2800)
        with self.assertRaisesRegex(DeploymentError, "expired"):
            validate_plan(plan, matching_facts(), now=2801)

    def test_secret_metadata_drift_requires_new_plan(self):
        plan = make_plan(secret_digest="sha256:old")
        with self.assertRaises(DeploymentError) as ctx:
            validate_plan(plan, matching_facts(secret_digest="sha256:new"), now=1100)
        self.assertEqual(ctx.exception.next_action, "regenerate_plan")
```

- [ ] **Step 2: Implement canonical SHA-256 hashing**

Use UTF-8 JSON with sorted keys and compact separators. Secret metadata includes name, size, mode, inode when available and modification nanoseconds; secret content is never put into a report.

- [ ] **Step 3: Implement ordered phases and conflict detection**

Use this order:

```text
backup
filesystem
network
compose
service_config
openclaw_config
restart
verification
```

Reject two changes that write the same target with different final states.

- [ ] **Step 4: Add CLI `discover` and `plan` handlers**

`discover` writes `reports/discovery.json`. `plan` writes `plan.json` and emits `ready_for_apply`, `request_confirmation`, redacted changes and an exact expiry timestamp.

- [ ] **Step 5: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_plan -v
python3 -m unittest discover -s tests -v
git add deploy/installer/planning.py deploy/installer/cli.py tests/deploy/test_plan.py
git commit -m "feat(deploy): add immutable plans and drift protection"
```

---

### Task 6: Lock container images and render deterministic configuration

**Files:**
- Create: `deploy/versions.yaml`
- Create: `deploy/installer/versions.py`
- Create: `deploy/installer/renderer.py`
- Create: `deploy/templates/compose.dependencies.yml.j2`
- Create: `deploy/templates/routing.json.j2`
- Create: `tests/deploy/test_versions.py`
- Create: `tests/deploy/test_renderer.py`

**Interfaces:**
- Produces: `VersionLock.load(path: Path) -> VersionLock`
- Produces: `VersionLock.image(component: str) -> str`
- Produces: `render_template(name, context, destination) -> RenderedFile`

- [ ] **Step 1: Write mutable-reference rejection tests**

```python
class VersionTests(unittest.TestCase):
    def test_rejects_latest(self):
        with self.assertRaises(DeploymentError) as ctx:
            VersionLock.from_dict({"qas": {"image": "cp0204/quark-auto-save:latest"}})
        self.assertEqual(ctx.exception.severity, "security_block")

    def test_requires_sha256_digest(self):
        with self.assertRaises(DeploymentError):
            VersionLock.from_dict({"qas": {"image": "cp0204/quark-auto-save:1.0"}})
```

- [ ] **Step 2: Implement the maintainer-only resolver**

Run `docker buildx imagetools inspect` for the tested QAS, PanSou, aria2, sing-box and Playwright images, validate `sha256:` plus 64 lowercase hexadecimal characters, and commit `repository@sha256:digest` values. If resolution fails, the task fails; no mutable tag is committed.

- [ ] **Step 3: Write strict-renderer tests**

Assert Compose output contains no `latest`, binds management ports to `127.0.0.1`, mounts aria2 downloads at `/nas/downloads`, references secrets without embedding values and uses the shared `openclaw-media` network.

- [ ] **Step 4: Implement rendering**

Use Jinja `StrictUndefined`, UTF-8, atomic writes, mode `0600` for generated config/environment files and `0644` for Compose files.

- [ ] **Step 5: Render routing**

Generate `movie`, `tv`, `drama`, `anime`, `documentary`, `show`, `other`, `downloads` and `paths`. `tv.final_root` equals `drama.final_root`. Protected roots cover all formal libraries and exclude the download root.

- [ ] **Step 6: Validate rendered files before planning**

Run Docker Compose config validation and `python3 -m json.tool` on generated routing. A failure is blocking and prevents plan generation.

- [ ] **Step 7: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_versions tests.deploy.test_renderer -v
python3 -m unittest discover -s tests -v
git add deploy/versions.yaml deploy/installer/versions.py deploy/installer/renderer.py deploy/templates tests/deploy
git commit -m "feat(deploy): lock images and render deterministic config"
```

---

### Task 7: Replace unconditional `0777` with a discovered aria2 permission strategy

**Files:**
- Create: `deploy/installer/permissions.py`
- Create: `tests/deploy/test_permissions.py`
- Modify: `scripts/download_fs.py`
- Modify: `scripts/resource_agent.py`
- Modify: `tests/test_download_fs.py`

**Interfaces:**
- Produces: `choose_permission_plan(path_stat, aria_identity, acl_supported) -> PermissionPlan`
- Produces: `probe_writable(path: Path) -> bool`

- [ ] **Step 1: Replace tests that require world-writable directories**

Test these ordered strategies:

1. same UID with the narrowest writable mode;
2. shared GID with mode `0770`;
3. POSIX ACL for the aria2 UID;
4. mode `0777` only as a final fallback for downloads root and `.incoming`;
5. `.ready` and `.quarantine` remain Agent-owned;
6. formal libraries are never permission-change targets.

- [ ] **Step 2: Run tests and observe the expected failures**

Run: `python3 -m unittest tests.test_download_fs tests.deploy.test_permissions -v`

Expected: current hard-coded `ARIA2_DIR_MODE = 0o777` causes failures.

- [ ] **Step 3: Implement pure permission planning**

Return explicit `set_owner`, `set_mode` and `set_acl` changes without mutating the filesystem. These changes are displayed in the deployment plan before confirmation.

- [ ] **Step 4: Restrict business-code mutation**

`ensure_aria2_writable` creates only managed download directories and applies `RESOURCE_AGENT_INCOMING_MODE`, defaulting to `0770`. The deployer sets `0777` only when discovery proved narrower strategies unusable.

- [ ] **Step 5: Replace `is_world_writable` readiness logic**

Create and delete a unique zero-byte probe under `.incoming`. Effective write success is the requirement; world writability is not.

- [ ] **Step 6: Run tests and commit**

```bash
python3 -m unittest tests.test_download_fs tests.deploy.test_permissions -v
python3 -m unittest discover -s tests -v
git add deploy/installer/permissions.py scripts/download_fs.py scripts/resource_agent.py tests/test_download_fs.py tests/deploy/test_permissions.py
git commit -m "fix(storage): use discovered aria2 permissions"
```

---

### Task 8: Implement QAS config/API initialization and Playwright fallback

**Files:**
- Create: `deploy/installer/adapters/qas_v1.py`
- Create: `deploy/installer/adapters/qas_browser.py`
- Create: `tests/deploy/test_qas_adapter.py`
- Create: `tests/deploy/test_qas_browser.py`
- Create: `tests/fixtures/qas-v1/config.json`
- Create: `tests/fixtures/qas-v1/data-response.json`
- Create: `tests/fixtures/qas-v1/ui-contract.json`

**Interfaces:**
- Produces: `QasV1Adapter.discover()`, `plan()`, `apply_config()`, `verify()`
- Produces: `QasBrowserFallback.run(base_url, desired_state, runner) -> BrowserResult`

- [ ] **Step 1: Capture sanitized fixtures from the exact locked QAS digest**

Start the locked image in an isolated temporary directory using dummy values. Record config key names and types, `/data` response shape and stable UI selectors. Replace every credential, Cookie, share URL and address with `***` before committing fixtures.

- [ ] **Step 2: Write config/API adapter tests**

Cover WebUI username/password state, API token state, Cookie normalization, aria2 plugin URL, RPC secret state, read-back verification and unknown schema behavior. Unknown schema returns `manual_action_required` with `nextAction: complete_qas_configuration`.

- [ ] **Step 3: Implement config-first and API-second initialization**

Write only fields proven by the fixture. Back up before writing, restart QAS, call `/data`, normalize values and compare desired versus actual state. Reports use `configured`, `missing` and `invalid`, never secret values.

- [ ] **Step 4: Write Playwright fallback tests**

Use a local HTML fixture served by `http.server`. Assert the fallback fills only known non-auth fields, clicks the known save control, never logs form values and returns `manual_action_required` when login, QR or CAPTCHA UI is detected.

- [ ] **Step 5: Implement the browser fallback in an ephemeral locked Playwright container**

Bind-mount a generated automation script read-only, attach the container to `openclaw-media`, access QAS by service DNS and remove the container after execution. The browser is headless. It does not bypass identity challenges. When user login is required, return the local QAS WebUI URL and persist a resumable gate.

- [ ] **Step 6: Add resume verification**

After the user completes login or QR verification, `apply --resume DEPLOYMENT_ID --confirmed` reruns QAS read-back and continues only when Cookie, token and aria2 integration validate.

- [ ] **Step 7: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_qas_adapter tests.deploy.test_qas_browser -v
python3 -m unittest discover -s tests -v
git add deploy/installer/adapters/qas_v1.py deploy/installer/adapters/qas_browser.py tests/deploy tests/fixtures/qas-v1
git commit -m "feat(deploy): initialize QAS with browser fallback"
```

---

### Task 9: Implement PanSou and optional managed proxy profiles

**Files:**
- Create: `deploy/installer/adapters/pansou.py`
- Create: `deploy/installer/adapters/proxy.py`
- Create: `deploy/templates/compose.proxy.yml.j2`
- Create: `tests/deploy/test_pansou_adapter.py`
- Create: `tests/deploy/test_proxy_adapter.py`

**Interfaces:**
- Produces: `PanSouAdapter.discover()`, `plan()`, `verify()`
- Produces: `ProxyAdapter.plan()`, `verify()`

- [ ] **Step 1: Write PanSou mode tests**

Cover `none`, `existing` and `managed`; channel rendering; HTTP health; search API response; Telegram source count; and optional failure aggregation to `degraded`.

- [ ] **Step 2: Implement existing-proxy mapping**

SOCKS5 input maps to the pinned PanSou-supported proxy variable. HTTP input maps to `HTTP_PROXY` and `HTTPS_PROXY`. Proxy URLs are loaded from secret files at apply time and never written to plans or reports.

- [ ] **Step 3: Write managed-proxy tests**

Require a user-supplied `singbox_config.json` secret, mount it read-only, expose only an internal Docker-network SOCKS5 endpoint and reject configurations that bind management or proxy ports to public host interfaces.

- [ ] **Step 4: Implement managed sing-box profile**

The project supplies no proxy provider, account or node. The user supplies a legal, valid sing-box configuration. Unsupported subscription formats return `manual_action_required` with `nextAction: provide_singbox_config`.

- [ ] **Step 5: Implement differentiated verification**

Report `serviceHealthy`, `apiReachable`, `telegramReachable`, `proxyConfigured` and `sourceCount`. PanSou enabled but Telegram unreachable yields `degraded`, not `ready` and not a core-chain failure.

- [ ] **Step 6: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_pansou_adapter tests.deploy.test_proxy_adapter -v
python3 -m unittest discover -s tests -v
git add deploy/installer/adapters/pansou.py deploy/installer/adapters/proxy.py deploy/templates/compose.proxy.yml.j2 tests/deploy
git commit -m "feat(deploy): add PanSou and managed proxy profiles"
```

---

### Task 10: Implement aria2 reuse, identity, RPC and mount verification

**Files:**
- Create: `deploy/installer/adapters/aria2.py`
- Create: `tests/deploy/test_aria2_adapter.py`

**Interfaces:**
- Produces: `Aria2Adapter.runtime_identity() -> RuntimeIdentity`
- Produces: `Aria2Adapter.verify_rpc() -> ComponentResult`
- Produces: `Aria2Adapter.verify_mount() -> ComponentResult`

- [ ] **Step 1: Write identity and mount tests**

Cover `id -u`, `id -g`, `id -G`, `/nas/downloads` mount source, RPC authentication and rejection when the OpenClaw-visible host source differs from the aria2 mount source.

- [ ] **Step 2: Implement identity discovery**

Use:

```python
runner.run(["docker", "exec", container_name, "id", "-u"])
runner.run(["docker", "exec", container_name, "id", "-g"])
runner.run(["docker", "exec", container_name, "id", "-G"])
```

- [ ] **Step 3: Implement authenticated RPC verification**

Call `aria2.getVersion` with `token:` plus the secret. Redact the secret from URL, request and exception output.

- [ ] **Step 4: Implement a controlled write probe**

Create `.incoming/.deploy-probe-{deployment_id}`, ask aria2 to write a tiny configured legal probe object, confirm host visibility and remove only the probe GID and probe files. A failed probe reports exact mapping metadata without leaking internal credentials.

- [ ] **Step 5: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_aria2_adapter -v
python3 -m unittest discover -s tests -v
git add deploy/installer/adapters/aria2.py tests/deploy/test_aria2_adapter.py
git commit -m "feat(deploy): verify aria2 identity RPC and mounts"
```

---

### Task 11: Integrate the existing OpenClaw service and enforce its command allowlist

**Files:**
- Create: `deploy/installer/adapters/openclaw_v1.py`
- Create: `deploy/templates/compose.openclaw.override.yml.j2`
- Create: `tests/deploy/test_openclaw_adapter.py`
- Create: `tests/fixtures/openclaw-v1/config.json`
- Create: `tests/fixtures/openclaw-v1/compose.json`
- Create: `tests/fixtures/openclaw-v1/version.txt`

**Interfaces:**
- Produces: `OpenClawV1Adapter.discover()`, `plan()`, `apply_config()`, `verify()`
- Produces: `compose_command(installation, override_path) -> list[str]`

- [ ] **Step 1: Capture a sanitized supported OpenClaw profile**

From the exact tested OpenClaw version, record version output, Compose JSON, workspace mount, `skills.entries.resource-download-agent.env` shape and `tools.exec` configuration shape. Remove all credentials and private addresses.

- [ ] **Step 2: Write supported and unsupported profile tests**

A supported Compose-managed installation resolves one writable workspace. Docker-run-only or unknown config profiles return `manual_action_required`; the adapter never guesses or rewrites an unknown container.

- [ ] **Step 3: Resolve concrete Skill paths**

```python
host_skill_path = workspace.host_source / "skills" / "resource-download-agent"
container_skill_path = workspace.container_destination / "skills" / "resource-download-agent"
host_state_path = workspace.host_source / "data" / "resource-download-agent" / "state.db"
container_state_path = workspace.container_destination / "data" / "resource-download-agent" / "state.db"
```

- [ ] **Step 4: Render a Compose override without modifying original Compose files**

Add the shared network, download mount, formal media mounts and non-secret environment references to the discovered service. Build the command by appending every discovered original `-f` file followed by the generated override and `up -d SERVICE_NAME`.

- [ ] **Step 5: Implement safe Skill installation/update**

Copy the current repository checkout excluding `.git`, `.deploy-venv`, `deploy/runtime`, `deploy/secrets` and local `.env`. Existing Git checkout with local modifications returns `manual_action_required`; never force reset.

- [ ] **Step 6: Write allowlist security tests**

Assert the generated OpenClaw configuration sets `tools.exec.security` to `allowlist`, sets `tools.exec.ask` to `off`, and permits only the fixed absolute `bin/mediactl` path. Assert `bash`, `sh`, `python`, `curl`, `rm` and `sudo` are absent.

- [ ] **Step 7: Implement supported-profile configuration merge**

Back up `openclaw.json`, merge Skill environment references and the fixed exec policy, write atomically, restart through Compose and read the file back. Unsupported `tools.exec` shape returns `manual_action_required` rather than overwriting it.

- [ ] **Step 8: Verify OpenClaw**

Check container health, Skill visibility, executable mode, effective fixed command policy and `mediactl check-ready` JSON.

- [ ] **Step 9: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_openclaw_adapter -v
python3 -m unittest discover -s tests -v
git add deploy/installer/adapters/openclaw_v1.py deploy/templates/compose.openclaw.override.yml.j2 tests/deploy tests/fixtures/openclaw-v1
git commit -m "feat(deploy): integrate and constrain existing OpenClaw"
```

---

### Task 12: Implement secret-free backup, journaled apply, resume and rollback

**Files:**
- Create: `deploy/installer/backup.py`
- Create: `deploy/installer/executor.py`
- Create: `deploy/installer/rollback.py`
- Create: `tests/deploy/test_backup.py`
- Create: `tests/deploy/test_executor.py`
- Create: `tests/deploy/test_rollback.py`
- Modify: `deploy/installer/cli.py`

**Interfaces:**
- Produces: `create_backup(...) -> BackupManifest`
- Produces: `apply_plan(plan, context) -> DeploymentResult`
- Produces: `resume_deployment(deployment_id, context) -> DeploymentResult`
- Produces: `rollback(deployment_id, context) -> DeploymentResult`

- [ ] **Step 1: Write backup exclusion tests**

Secrets, `.env`, storage-state files and files containing a configured secret sentinel are rejected. Compose, routing, supported OpenClaw config, Skill source and the state database are copied with metadata.

- [ ] **Step 2: Implement backups under `RuntimePaths.backup_dir(deployment_id)`**

Record source, backup relative path, mode, owner IDs, SHA-256 and restore action. Preserve symlinks as symlinks and reject links escaping allowed roots.

- [ ] **Step 3: Write transaction failure tests**

Use changes A, B and C where C fails. Assert B and A reverse in that order and final status is `rolled_back`. If rollback B fails, final status is `failed` with both original and rollback errors.

- [ ] **Step 4: Implement explicit change handlers**

Support only:

```text
create_directory
set_mode
set_owner
set_acl
copy_tree
write_file
create_network
connect_network
compose_up
restart_container
http_config_update
run_browser_fallback
run_verification
```

Unknown action types are rejected before any side effect.

- [ ] **Step 5: Implement resumable manual gates**

Persist completed change IDs. `apply --resume DEPLOYMENT_ID --confirmed` reruns drift checks and continues at the first unapplied change without repeating completed non-idempotent actions.

- [ ] **Step 6: Wire `apply` and `rollback`**

`apply` validates the plan, backs up, executes and automatically runs `verify safe`. `rollback` requires `--confirmed` and rejects deployment IDs from another project root.

- [ ] **Step 7: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_backup tests.deploy.test_executor tests.deploy.test_rollback -v
python3 -m unittest discover -s tests -v
git add deploy/installer/backup.py deploy/installer/executor.py deploy/installer/rollback.py deploy/installer/cli.py tests/deploy
git commit -m "feat(deploy): add transactional apply and rollback"
```

---

### Task 13: Implement L0-L4 component and security verification

**Files:**
- Create: `deploy/installer/verifier.py`
- Create: `tests/deploy/test_verifier.py`

**Interfaces:**
- Produces: `verify(level: str, context) -> VerificationResult`

- [ ] **Step 1: Write state aggregation tests**

Rules:

- any `security_block` or required component failure yields `failed`;
- a required user gate yields `manual_action_required`;
- optional PanSou/proxy failure yields `degraded`;
- all enabled checks passing yields `ready`;
- a disabled optional component is `skipped`.

- [ ] **Step 2: Implement L0 static checks**

Validate schema, secret modes, immutable images, rendered Compose/JSON, ports, real paths, free space, architecture, protected-root coverage and formal-library separation from downloads.

- [ ] **Step 3: Implement L1 health checks**

Use Docker health status when defined. Otherwise use an adapter API probe and report `healthSource: adapter_probe`; running container state alone is insufficient.

- [ ] **Step 4: Implement L2 network checks**

Probe OpenClaw to QAS, PanSou and aria2 by Docker DNS. The verifier may run a fixed internal Python probe through `docker exec`; that probe is not added to the Agent-facing allowlist.

- [ ] **Step 5: Implement L3 component checks**

Verify QAS token/Cookie/plugin read-back, PanSou Telegram source behavior, proxy reachability, aria2 RPC/write mapping, OpenClaw Skill visibility and `mediactl check-ready`.

- [ ] **Step 6: Implement L4 security checks**

Verify formal libraries are not download targets, allowlist contains only fixed `mediactl`, report files contain no secret sentinels, expired plans fail and a fixed `mediactl` protected-path operation is refused. Production verification does not execute the unit test suite.

- [ ] **Step 7: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_verifier -v
python3 -m unittest discover -s tests -v
git add deploy/installer/verifier.py tests/deploy/test_verifier.py
git commit -m "feat(deploy): add layered security verification"
```

---

### Task 14: Implement `safe` and confirmed `full` business verification

**Files:**
- Modify: `deploy/installer/verifier.py`
- Modify: `deploy/installer/cli.py`
- Create: `tests/deploy/test_business_verification.py`

**Interfaces:**
- Produces: `run_mediactl(args: Sequence[str]) -> dict[str, object]`
- Produces: `verify_safe(context) -> VerificationResult`
- Produces: `verify_full(context) -> VerificationResult`

- [ ] **Step 1: Write concrete safe-flow tests**

Use these fixture calls:

```text
check-ready
search OpenClaw deploy verification sample --media-type other
preview candidate-demo
 tree candidate-demo
plan download candidate-demo --node node-demo --media-type other
```

The leading whitespace before `tree` is not part of the argv. Assert no `execute` and no `organize execute` call occurs, and no real download task is created.

- [ ] **Step 2: Implement safe verification**

Search may be replaced by a user-provided legal share URL when remote search is unstable. Parse only JSON fields `ok`, `terminal`, `nextAction`, `data`, `warnings`, `errors` and `error`. Reject extra terminal prose.

- [ ] **Step 3: Write full-flow gate tests**

Independently require `allow_real_download`, `full_test_share_url` secret and CLI `--confirmed`. Missing gates return `manual_action_required`.

- [ ] **Step 4: Implement the existing CLI syntax exactly**

```text
import-url LEGAL_SHARE_URL --media-type other
tree CANDIDATE_ID
plan download CANDIDATE_ID --node NODE_ID --media-type other
execute PLAN_ID --confirmed
downloads show TASK_ID
downloads validate TASK_ID
organize plan TASK_ID
stop
```

Select a node only when its reported size is within the configured test limit. Never run `organize execute`.

- [ ] **Step 5: Implement failure boundaries**

On timeout or validation failure, leave real downloaded content in place, report the managed path and remove only `.deploy-probe-*` artifacts.

- [ ] **Step 6: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_business_verification -v
python3 -m unittest discover -s tests -v
git add deploy/installer/verifier.py deploy/installer/cli.py tests/deploy/test_business_verification.py
git commit -m "feat(deploy): add safe and confirmed full verification"
```

---

### Task 15: Complete the initializer, end-to-end CLI, docs and CI

**Files:**
- Modify: `deploy/installer/cli.py`
- Modify: `deploy/config.example.yaml`
- Modify: `deploy/docker-compose.dependencies.yml`
- Modify: `README.md`
- Modify: `docs/AGENT_DEPLOY.md`
- Create: `docs/deployment/QUICKSTART.md`
- Create: `docs/deployment/EXISTING_OPENCLAW.md`
- Create: `docs/deployment/QAS_LOGIN.md`
- Create: `docs/deployment/PROXY.md`
- Create: `docs/deployment/TROUBLESHOOTING.md`
- Create: `docs/deployment/SECURITY.md`
- Create: `.github/workflows/deploy-tests.yml`
- Create: `tests/deploy/test_init_wizard.py`
- Create: `tests/deploy/test_cli_end_to_end.py`
- Create: `tests/deploy/test_docs_examples.py`

**Interfaces:**
- Produces: `run_init(input_stream, output_stream, project_root) -> DeploymentConfig`

- [ ] **Step 1: Write initializer transcript tests**

Choose UGOS, provide project/download/library paths, use service mode `auto`, enable PanSou with existing proxy and create named empty secret files. Assert the wizard never asks for or echoes secret values.

- [ ] **Step 2: Implement `init` as configuration generation only**

Write `deploy/config.yaml`, create `deploy/secrets/` as `0700`, create missing secret files as empty `0600`, and return `nextAction: fill_secret_files_then_run_discover`. Do not call Docker.

- [ ] **Step 3: Add an Agent-friendly noninteractive mode**

```bash
python3 deploy/cli.py init --non-interactive --config-source /tmp/openclaw-media-config.yaml
```

Validate schema before atomic copy. Reject a source under `deploy/secrets/` and symlinks escaping the project root.

- [ ] **Step 4: Write fixture end-to-end tests**

Exercise `init`, `discover`, `plan`, `apply`, `verify safe` and `rollback`. Every command emits one JSON document and no secret sentinel appears in stdout, stderr or runtime reports.

- [ ] **Step 5: Test documentation commands against the real parser**

Extract fenced `python3 deploy/cli.py` commands and reject unknown commands or flags.

- [ ] **Step 6: Update the user and Agent documentation**

Primary flow:

```bash
git clone https://github.com/Inupedia/openclaw-nas-media-agent.git
cd openclaw-nas-media-agent
python3 deploy/cli.py init
python3 deploy/cli.py discover
python3 deploy/cli.py plan
python3 deploy/cli.py apply --plan-id PLAN_ID --confirmed
python3 deploy/cli.py verify --level safe
```

State that login, QR, CAPTCHA and dangerous-operation confirmation remain user actions.

- [ ] **Step 7: Make the committed dependency Compose a checked generated reference**

Render it from the same template and version lock in tests. It contains immutable image references and no secret values.

- [ ] **Step 8: Add CI for Python 3.10, 3.11 and 3.12**

Install with `--require-hashes`, run `python -m unittest discover -s tests -v`, validate committed JSON/YAML and run the docs command test. CI uses fixtures only.

- [ ] **Step 9: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_init_wizard tests.deploy.test_cli_end_to_end tests.deploy.test_docs_examples -v
python3 -m unittest discover -s tests -v
git add deploy README.md docs .github/workflows tests/deploy
git commit -m "docs(deploy): publish deterministic existing OpenClaw workflow"
```

---

### Task 16: Perform final scenario verification and prepare for review

**Files:**
- Modify only files required by verified failures from the commands below.

- [ ] **Step 1: Run the full suite in a clean process**

Run: `python3 -m unittest discover -s tests -v`

Expected: zero failures and zero errors. Existing platform-specific skips require explicit reasons.

- [ ] **Step 2: Run fixture end-to-end verification**

Run: `python3 -m unittest tests.deploy.test_cli_end_to_end -v`

Expected: fake apply reaches `ready`, rollback reaches `rolled_back`, and no secret sentinel appears in artifacts.

- [ ] **Step 3: Run static checks**

```bash
python3 -m json.tool deploy/schemas/config.schema.json >/dev/null
python3 -m json.tool config/routing.json >/dev/null
git diff --check
git status --short
```

Expected: no parsing errors, whitespace errors or untracked secret/runtime files.

- [ ] **Step 4: Check approved-design coverage**

Record evidence for config source of truth, secret modes, 30-minute plans, immutable images, QAS config/API/browser fallback, PanSou degraded state, managed proxy, aria2 identity and permission strategy, OpenClaw allowlist, safe/full gates, transaction rollback and protected media roots.

- [ ] **Step 5: Run a real read-only UGOS or Linux dry run**

```bash
python3 deploy/cli.py discover
python3 deploy/cli.py plan
```

Expected: success or a precise `manual_action_required`; no Docker object is created and no file outside `deploy/runtime/` changes.

- [ ] **Step 6: Run controlled apply, safe verification and rollback**

Use a dedicated non-production OpenClaw/QAS/PanSou/aria2 environment and non-production paths. Confirm the plan, apply, verify safe, rollback and compare backup hashes.

- [ ] **Step 7: Commit only verified fixes when files changed**

```bash
git add -u
git commit -m "fix(deploy): address end-to-end verification findings"
```

Skip the commit when `git status --short` is empty.

## Follow-on Plans

After this plan is implemented and reviewed, create separate plans in this order:

1. `2026-07-20-jiaofu-runner.md` — isolated Playwright/Chromium discovery service and login-state lifecycle.
2. `2026-07-20-full-stack-openclaw.md` — blank-NAS OpenClaw Compose, OpenAI-Compatible model setup and Web/local chat.
3. `2026-07-20-deployer-upgrades-and-platforms.md` — explicit upgrades, Installer-container entry and additional NAS adapters.

Each follow-on plan reuses the interfaces and state contracts established here rather than creating a second deployment system.