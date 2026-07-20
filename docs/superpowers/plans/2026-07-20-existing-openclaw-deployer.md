# Existing OpenClaw Deployer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production-ready deployment path for an NAS that already runs OpenClaw, so an Agent can discover the environment, generate an immutable plan, safely deploy or reuse QAS/PanSou/aria2, configure the Skill, verify the real chain, and roll back failures.

**Architecture:** `deploy/cli.py` is a standard-library launcher that creates a private deployment virtual environment and then delegates to a typed Python package under `deploy/installer/`. The package separates discovery, configuration, planning, rendering, execution, verification, and rollback; every external command is injected through a runner so unit tests never require a real NAS. Existing OpenClaw configuration is changed through a generated Compose override and generated Skill files instead of rewriting the user's original Compose file in place.

**Tech Stack:** Python 3.10+, `unittest`, PyYAML 6.x, Jinja2 3.1.x, jsonschema 4.x, Docker CLI, Docker Compose v2, standard-library `urllib`, JSON/YAML, POSIX file permissions.

## Global Constraints

- This plan implements only `deployment.mode: existing-openclaw`.
- First-class platforms are UGREEN UGOS and standard Linux Docker hosts.
- Synology, QNAP, TrueNAS, and Unraid remain experimental and must be reported as such.
- `deploy/config.yaml` is the only source of truth for non-sensitive deployment configuration.
- Secrets live in `deploy/secrets/`; the directory mode is `0700` and ordinary secret files are `0600`.
- Secrets never appear in Git, backups, plans, terminal JSON, stderr logs, or reports.
- Plan validity is 30 minutes. A plan becomes invalid when config, secret metadata, discovery facts, or managed files drift.
- `apply` requires both `--plan-id` and `--confirmed`.
- `verify --level full` requires `verification.allow_real_download: true`, a legal user-provided test share URL, and `--confirmed`.
- Full verification stops after `organize plan`; it must not run `organize execute`.
- The final deployment state is exactly one of `ready`, `degraded`, `manual_action_required`, `failed`, or `rolled_back`.
- `security_block` failures cannot be bypassed with a force flag.
- Never recursively chmod a formal media library.
- Do not assume aria2 runs as `nobody`; inspect its actual UID/GID.
- Do not use mutable image references such as `latest` in a committed version lock.
- Every task follows red-green-refactor TDD and ends with a focused commit.

## File Map

### New deployment entry points

- `deploy/cli.py`: dependency bootstrap and process re-exec only.
- `deploy/requirements.lock`: Python deployment dependencies with bounded versions.
- `deploy/config.example.yaml`: documented non-secret configuration example.
- `deploy/versions.yaml`: immutable container version lock and adapter mapping.
- `deploy/schemas/config.schema.json`: configuration contract.

### New deployment package

- `deploy/installer/cli.py`: argument parsing and command dispatch.
- `deploy/installer/models.py`: immutable dataclasses and enums shared across modules.
- `deploy/installer/output.py`: single-JSON stdout contract.
- `deploy/installer/errors.py`: typed deployment errors and severity mapping.
- `deploy/installer/command.py`: injected subprocess runner.
- `deploy/installer/config.py`: YAML loading, schema validation, defaults, and hashing.
- `deploy/installer/secrets.py`: secret references, permissions, metadata hashes, and redaction values.
- `deploy/installer/redaction.py`: recursive redaction for strings and structured data.
- `deploy/installer/runtime.py`: runtime paths, atomic JSON writes, locks, and identifiers.
- `deploy/installer/discovery.py`: host/Docker/container/network/path discovery.
- `deploy/installer/platforms/linux.py`: Linux facts and filesystem checks.
- `deploy/installer/platforms/ugos.py`: UGOS detection and capability flags.
- `deploy/installer/planning.py`: immutable change plan generation and validation.
- `deploy/installer/versions.py`: version-lock parsing and immutable image validation.
- `deploy/installer/renderer.py`: Jinja rendering and static validation.
- `deploy/installer/backup.py`: timestamped backups without secret content.
- `deploy/installer/executor.py`: transaction application and journal writing.
- `deploy/installer/rollback.py`: reverse journal execution.
- `deploy/installer/verifier.py`: L0-L6 verification orchestration.
- `deploy/installer/adapters/openclaw.py`: existing OpenClaw detection and Compose override generation.
- `deploy/installer/adapters/qas_v1.py`: pinned QAS configuration/API adapter.
- `deploy/installer/adapters/pansou.py`: PanSou configuration and Telegram reachability.
- `deploy/installer/adapters/aria2.py`: aria2 identity, RPC, and write probes.

### New templates and fixtures

- `deploy/templates/compose.dependencies.yml.j2`: QAS/PanSou/aria2 stack.
- `deploy/templates/compose.openclaw.override.yml.j2`: generated override for the existing OpenClaw service.
- `deploy/templates/routing.json.j2`: generated media routing.
- `tests/fixtures/docker/*.json`: sanitized Docker inspect/config responses.
- `tests/fixtures/qas-v1/*`: sanitized QAS configuration and API payloads from the pinned version.

### Existing files modified

- `.gitignore`: ignore runtime config, secrets, deployment venv, rendered output, and reports.
- `scripts/download_fs.py`: replace unconditional world-writable behavior with an explicit permission policy.
- `scripts/resource_agent.py`: readiness checks use effective write probes rather than `is_world_writable`.
- `tests/test_download_fs.py`: cover UID/GID/group permission strategies.
- `deploy/docker-compose.dependencies.yml`: become a generated-example wrapper or remain documented reference pointing to the version-locked template.
- `README.md`: make the new CLI the primary existing-OpenClaw path.
- `docs/AGENT_DEPLOY.md`: align the Agent contract with `init/discover/plan/apply/verify/rollback`.

---

### Task 1: Add the self-bootstrapping deployment CLI and JSON output contract

**Files:**
- Create: `deploy/cli.py`
- Create: `deploy/requirements.lock`
- Create: `deploy/installer/__init__.py`
- Create: `deploy/installer/cli.py`
- Create: `deploy/installer/output.py`
- Create: `deploy/installer/errors.py`
- Create: `tests/deploy/__init__.py`
- Create: `tests/deploy/test_cli.py`

**Interfaces:**
- Produces: `deploy.installer.cli.main(argv: list[str] | None = None) -> int`
- Produces: `DeploymentError(code, message, status, next_action, severity, details)`
- Produces: `result_payload(...) -> dict[str, object]`
- Produces: `emit(payload: dict[str, object], stream: TextIO = sys.stdout) -> None`

- [ ] **Step 1: Write the failing output-contract test**

```python
class OutputContractTests(unittest.TestCase):
    def test_emit_writes_one_json_document(self):
        stream = io.StringIO()
        emit(result_payload(ok=True, status="ready", next_action="none"), stream)
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["status"], "ready")
```

- [ ] **Step 2: Run the test and confirm the import failure**

Run: `python3 -m unittest tests.deploy.test_cli.OutputContractTests -v`

Expected: `ERROR` with `ModuleNotFoundError: No module named 'deploy.installer.output'`.

- [ ] **Step 3: Implement the minimal output and error types**

```python
@dataclass(frozen=True)
class DeploymentError(RuntimeError):
    code: str
    message: str
    status: str = "failed"
    next_action: str = "review_error"
    severity: str = "blocking"
    details: Mapping[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


def result_payload(*, ok: bool, status: str, next_action: str, data=None,
                   warnings=None, errors=None) -> dict[str, object]:
    return {
        "ok": ok,
        "status": status,
        "nextAction": next_action,
        "data": dict(data or {}),
        "warnings": list(warnings or []),
        "errors": list(errors or []),
    }


def emit(payload: dict[str, object], stream=sys.stdout) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
```

- [ ] **Step 4: Add the launcher dependency bootstrap test**

```python
class LauncherTests(unittest.TestCase):
    def test_runtime_python_prefers_private_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = root / ".deploy-venv" / "bin" / "python"
            expected.parent.mkdir(parents=True)
            expected.write_text("", encoding="utf-8")
            expected.chmod(0o755)
            self.assertEqual(resolve_runtime_python(root), expected)
```

- [ ] **Step 5: Implement `deploy/cli.py` as a standard-library-only launcher**

The launcher must create `.deploy-venv`, install `deploy/requirements.lock`, and re-exec `python -m deploy.installer.cli`. It must set `OPENCLAW_DEPLOY_BOOTSTRAPPED=1` before re-exec to prevent loops. A failed venv or pip operation emits one JSON error with `nextAction: install_python_venv_or_dependencies`.

- [ ] **Step 6: Lock bounded Python dependencies**

`deploy/requirements.lock`:

```text
Jinja2>=3.1,<4
jsonschema>=4.23,<5
PyYAML>=6.0,<7
```

- [ ] **Step 7: Add parser smoke tests for every command name**

Test `init`, `discover`, `plan`, `apply`, `verify`, `rollback`, and `versions check`; unimplemented handlers must return a structured `manual_action_required` result rather than a traceback.

- [ ] **Step 8: Run the focused test and full existing suite**

Run:

```bash
python3 -m unittest tests.deploy.test_cli -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass and stdout tests observe exactly one JSON line.

- [ ] **Step 9: Commit**

```bash
git add deploy/cli.py deploy/requirements.lock deploy/installer tests/deploy
git commit -m "feat(deploy): add bootstrap CLI and output contract"
```

---

### Task 2: Implement configuration schema, secret references, and redaction

**Files:**
- Create: `deploy/config.example.yaml`
- Create: `deploy/schemas/config.schema.json`
- Create: `deploy/installer/models.py`
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
- Produces: `SecretStore(root: Path).read(name: str) -> str`
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

    def test_requires_formal_library_paths(self):
        raw = minimal_config()
        del raw["nas"]["libraries"]["movie"]
        with self.assertRaises(DeploymentError) as ctx:
            load_config(write_yaml(raw))
        self.assertEqual(ctx.exception.severity, "security_block")
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m unittest tests.deploy.test_config -v`

Expected: import failure for `deploy.installer.config`.

- [ ] **Step 3: Define immutable configuration dataclasses**

```python
@dataclass(frozen=True)
class LibraryPaths:
    movie: Path
    drama: Path
    anime: Path
    documentary: Path
    show: Path
    other: Path

@dataclass(frozen=True)
class DeploymentConfig:
    project_dir: Path
    timezone: str
    platform: str
    downloads_dir: Path
    organizing_dir: Path
    libraries: LibraryPaths
    openclaw_container: str
    openclaw_workspace_host_dir: Path | None
    openclaw_config_host_path: Path | None
    qas: Mapping[str, object]
    aria2: Mapping[str, object]
    pansou: Mapping[str, object]
    verification: Mapping[str, object]
```

Use normalized absolute paths and reject paths containing NUL characters, relative components, or empty strings.

- [ ] **Step 4: Implement JSON Schema validation before dataclass construction**

The schema must set `additionalProperties: false` for every object, enumerate `deployment.mode` to `existing-openclaw`, enumerate proxy modes to `none|existing|managed`, and cap candidate limits to the existing business limits.

- [ ] **Step 5: Write failing secret permission and redaction tests**

```python
class SecretTests(unittest.TestCase):
    def test_rejects_group_readable_secret(self):
        secret = self.root / "qas_token"
        secret.write_text("token-123", encoding="utf-8")
        secret.chmod(0o640)
        with self.assertRaises(DeploymentError) as ctx:
            self.store.read("qas_token")
        self.assertEqual(ctx.exception.severity, "security_block")

    def test_redacts_nested_and_embedded_values(self):
        value = {"url": "http://x/?token=token-123", "items": ["token-123"]}
        self.assertEqual(
            redact(value, ["token-123"]),
            {"url": "http://x/?token=***", "items": ["***"]},
        )
```

- [ ] **Step 6: Implement secret validation**

`SecretStore` must reject names containing `/`, `\\`, or `..`; verify the root is `0700`; verify files are regular files with mode `0600`; trim one trailing newline; and never expose values in `repr`.

- [ ] **Step 7: Extend `.gitignore`**

Add exactly:

```gitignore
.deploy-venv/
deploy/config.yaml
deploy/secrets/
deploy/runtime/
```

- [ ] **Step 8: Run focused and full tests**

```bash
python3 -m unittest tests.deploy.test_config tests.deploy.test_secrets -v
python3 -m unittest discover -s tests -v
```

- [ ] **Step 9: Commit**

```bash
git add .gitignore deploy/config.example.yaml deploy/schemas deploy/installer tests/deploy
git commit -m "feat(deploy): add validated config and secret isolation"
```

---

### Task 3: Add deterministic command execution and host discovery

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
- Produces: `CommandResult(args, returncode, stdout, stderr)`
- Produces: `CommandRunner.run(args: Sequence[str], timeout: int = 30) -> CommandResult`
- Produces: `DiscoveryReport`
- Produces: `discover(config: DeploymentConfig, runner: CommandRunner) -> DiscoveryReport`

- [ ] **Step 1: Write failing command-runner tests**

Test that arguments are passed as an argv list with `shell=False`, environment values are redacted from exceptions, timeout maps to `DISCOVERY_COMMAND_TIMEOUT`, and nonzero exit status is returned without automatically throwing.

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

Do not accept a command string API.

- [ ] **Step 3: Write failing discovery fixture tests**

```python
class DiscoveryTests(unittest.TestCase):
    def test_identifies_unique_openclaw_compose_service(self):
        report = discover(self.config, FixtureRunner(self.fixture_map))
        self.assertEqual(report.openclaw.container_name, "openclaw-gateway")
        self.assertEqual(report.openclaw.compose_service, "gateway")
        self.assertEqual(report.platform.kind, "ugos")

    def test_multiple_openclaw_candidates_require_selection(self):
        runner = FixtureRunner(with_second_openclaw(self.fixture_map))
        with self.assertRaises(DeploymentError) as ctx:
            discover(self.config, runner)
        self.assertEqual(ctx.exception.status, "manual_action_required")
```

- [ ] **Step 4: Implement read-only discovery commands**

Use only:

```text
uname -s
uname -m
cat /etc/os-release
docker version --format {{json .}}
docker compose version --short
docker ps -a --format {{json .}}
docker network ls --format {{json .}}
docker inspect <candidate>
docker compose -p <project> config --format json
stat -c %a:%u:%g:%F <path>
df -P <path>
```

Discovery must not create directories, networks, files, or containers.

- [ ] **Step 5: Implement OpenClaw candidate scoring**

A candidate is accepted only when all are true:

1. container name or image contains `openclaw` case-insensitively;
2. Compose labels identify project, service, working directory, and config files;
3. one mount destination is `/root/.openclaw/workspace` or ends with `/.openclaw/workspace`;
4. the Compose service from `docker compose config --format json` matches the running container.

Zero candidates returns `manual_action_required` with `nextAction: specify_openclaw_container`; multiple tied candidates return `nextAction: choose_openclaw_container`.

- [ ] **Step 6: Implement UGOS detection**

Classify as `ugos` when `/etc/os-release` content or known system markers contain `ugreen` or `ugos`; otherwise classify as `linux`. Store capabilities, not guessed behavior: `supports_posix_acl`, `supports_compose_v2`, `architecture`, and `filesystem_types`.

- [ ] **Step 7: Run tests**

```bash
python3 -m unittest tests.deploy.test_command tests.deploy.test_discovery -v
python3 -m unittest discover -s tests -v
```

- [ ] **Step 8: Commit**

```bash
git add deploy/installer/command.py deploy/installer/discovery.py deploy/installer/platforms tests/deploy tests/fixtures/docker
git commit -m "feat(deploy): add read-only NAS and Docker discovery"
```

---

### Task 4: Add runtime state, immutable plans, drift checks, and 30-minute expiry

**Files:**
- Create: `deploy/installer/runtime.py`
- Create: `deploy/installer/planning.py`
- Create: `tests/deploy/test_runtime.py`
- Create: `tests/deploy/test_plan.py`

**Interfaces:**
- Produces: `Change(id, component, action, target, before, after, side_effect, rollback)`
- Produces: `DeploymentPlan(plan_id, created_at, expires_at, config_digest, secret_digest, discovery_digest, changes)`
- Produces: `build_plan(config, secrets, discovery, adapters, now) -> DeploymentPlan`
- Produces: `validate_plan(plan, current_facts, now) -> None`
- Produces: `atomic_write_json(path: Path, payload: Mapping[str, object]) -> None`

- [ ] **Step 1: Write failing plan-expiry and drift tests**

```python
class PlanTests(unittest.TestCase):
    def test_plan_expires_after_thirty_minutes(self):
        plan = make_plan(created_at=1_000)
        with self.assertRaisesRegex(DeploymentError, "expired"):
            validate_plan(plan, matching_facts(), now=2_801)

    def test_secret_metadata_drift_invalidates_plan(self):
        plan = make_plan(secret_digest="sha256:old")
        facts = matching_facts(secret_digest="sha256:new")
        with self.assertRaises(DeploymentError) as ctx:
            validate_plan(plan, facts, now=1_100)
        self.assertEqual(ctx.exception.next_action, "regenerate_plan")
```

- [ ] **Step 2: Implement canonical hashing**

Use UTF-8 JSON with `sort_keys=True`, compact separators, and SHA-256. Hash secret metadata as name, inode where available, size, mode, and modification nanoseconds; never hash secret content into reports.

- [ ] **Step 3: Implement unpredictable identifiers**

Use `secrets.token_urlsafe(18)` for `plan_id` and `deployment_id`. Runtime files live under `deploy/runtime/` and are written with mode `0600` using a temporary file plus `os.replace`.

- [ ] **Step 4: Implement plan ordering and conflict checks**

Sort changes by these phases:

```text
backup
filesystem
network
compose
service_config
openclaw_override
restart
verification
```

Reject two changes that write the same target with different `after` values.

- [ ] **Step 5: Add CLI handlers for `discover` and `plan`**

`discover` writes `reports/discovery.json`. `plan` writes `plan.json` and returns `status: ready_for_apply`, `nextAction: request_confirmation`, a redacted change list, and the 30-minute expiry timestamp.

- [ ] **Step 6: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_runtime tests.deploy.test_plan -v
python3 -m unittest discover -s tests -v
git add deploy/installer/runtime.py deploy/installer/planning.py deploy/installer/cli.py tests/deploy
git commit -m "feat(deploy): add immutable plans and drift protection"
```

---

### Task 5: Add immutable image locking and deterministic template rendering

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
- Produces: `render_template(name: str, context: Mapping[str, object], destination: Path) -> RenderedFile`

- [ ] **Step 1: Write failing mutable-reference tests**

```python
class VersionTests(unittest.TestCase):
    def test_rejects_latest(self):
        with self.assertRaises(DeploymentError) as ctx:
            VersionLock.from_dict({"qas": {"image": "cp0204/quark-auto-save:latest"}})
        self.assertEqual(ctx.exception.severity, "security_block")

    def test_requires_digest(self):
        with self.assertRaises(DeploymentError):
            VersionLock.from_dict({"qas": {"image": "cp0204/quark-auto-save:1.0"}})
```

- [ ] **Step 2: Add a version-resolution helper used only by maintainers**

Create an internal function that runs `docker buildx imagetools inspect IMAGE --format '{{json .Manifest.Digest}}'`, validates `sha256:[0-9a-f]{64}`, and writes `IMAGE@DIGEST`. During implementation, resolve each currently tested QAS, PanSou, and aria2 image and commit only immutable references. The command must fail rather than preserve a mutable tag when digest resolution is unavailable.

- [ ] **Step 3: Write failing renderer tests**

Assert the rendered Compose contains no `latest`, binds management ports to `127.0.0.1`, mounts the host download directory to `/nas/downloads` for aria2, and references secret files without embedding their content.

- [ ] **Step 4: Implement strict Jinja rendering**

Use `StrictUndefined`, autoescape disabled for config files, UTF-8 output, atomic writes, and file modes `0600` for rendered environment/config files and `0644` for non-secret Compose files.

- [ ] **Step 5: Render routing from configuration**

The template must generate `movie`, `tv`, `drama`, `anime`, `documentary`, `show`, `other`, `downloads`, and `paths`; `tv.final_root` must equal `drama.final_root`; `protected_roots` must be the unique parents that cover every configured formal library.

- [ ] **Step 6: Validate rendered files before planning them**

Run `docker compose -f rendered-compose config --quiet` and `python3 -m json.tool rendered-routing.json`. A validation failure is blocking and no apply plan may be generated.

- [ ] **Step 7: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_versions tests.deploy.test_renderer -v
python3 -m unittest discover -s tests -v
git add deploy/versions.yaml deploy/installer/versions.py deploy/installer/renderer.py deploy/templates tests/deploy
git commit -m "feat(deploy): lock images and render deterministic config"
```

---

### Task 6: Implement QAS, PanSou, and aria2 discovery/reuse adapters

**Files:**
- Create: `deploy/installer/adapters/__init__.py`
- Create: `deploy/installer/adapters/qas_v1.py`
- Create: `deploy/installer/adapters/pansou.py`
- Create: `deploy/installer/adapters/aria2.py`
- Create: `tests/deploy/test_qas_adapter.py`
- Create: `tests/deploy/test_pansou_adapter.py`
- Create: `tests/deploy/test_aria2_adapter.py`
- Create: `tests/fixtures/qas-v1/config.json`
- Create: `tests/fixtures/qas-v1/data-response.json`

**Interfaces:**
- Produces on every adapter: `discover(report, config)`, `plan(current, desired) -> list[Change]`, `verify(context) -> ComponentResult`
- Produces: `Aria2Adapter.runtime_identity() -> RuntimeIdentity(uid: int, gid: int, groups: tuple[int, ...])`

- [ ] **Step 1: Capture the pinned QAS fixture without guessing its schema**

Start the exact digest chosen in Task 5 in an isolated temporary directory, create only dummy credentials, inspect `/app/config`, call `/data` with a dummy token, and save sanitized field structure under `tests/fixtures/qas-v1/`. Replace every credential value with `***` while preserving types and key names. Commit no Cookie, token, share URL, or private address.

- [ ] **Step 2: Write QAS adapter tests from the captured fixture**

Cover: configured WebUI credentials, API token state, Cookie presence/shape, aria2 plugin URL, RPC secret configured state, read-back verification, and unknown schema returning `manual_action_required` with `nextAction: complete_qas_configuration`.

- [ ] **Step 3: Implement QAS configuration-first and API-second logic**

The adapter may write only fields demonstrated by the pinned fixture. After a write it must restart QAS, call `/data`, and compare normalized states. Browser fallback is not silently invoked; when config/API initialization cannot complete, return a manual action containing the local WebUI URL and a checklist, then allow `apply --resume DEPLOYMENT_ID` after the user completes login.

- [ ] **Step 4: Write PanSou adapter tests**

Cover proxy modes `none`, `existing`, and `managed`; generated environment variables; channel list rendering; HTTP health; search API reachability; Telegram source count; and optional failure mapping to `degraded`.

- [ ] **Step 5: Implement PanSou proxy environment mapping**

- SOCKS5 URL: set `PROXY` only when supported by the pinned image contract.
- HTTP proxy URL: set `HTTP_PROXY` and `HTTPS_PROXY` to the same secret reference.
- `none`: set none of these variables.
- Never include the proxy URL in plan/report JSON.

- [ ] **Step 6: Write aria2 adapter tests**

Cover container identity parsing, RPC authentication, download path mapping, a temporary write probe under `.incoming/.deploy-probe-<id>`, cleanup of only that probe, and rejection when the configured host mount does not map to `/nas/downloads`.

- [ ] **Step 7: Implement aria2 identity and RPC checks**

Use `docker exec CONTAINER id -u`, `id -g`, and `id -G`; use JSON-RPC `aria2.getVersion` with `token:<secret>`; do not submit a real download during component verification.

- [ ] **Step 8: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_qas_adapter tests.deploy.test_pansou_adapter tests.deploy.test_aria2_adapter -v
python3 -m unittest discover -s tests -v
git add deploy/installer/adapters tests/deploy tests/fixtures/qas-v1
git commit -m "feat(deploy): add dependency discovery and initialization adapters"
```

---

### Task 7: Implement existing OpenClaw integration through a generated Compose override

**Files:**
- Create: `deploy/installer/adapters/openclaw.py`
- Create: `deploy/templates/compose.openclaw.override.yml.j2`
- Create: `tests/deploy/test_openclaw_adapter.py`
- Create: `tests/fixtures/docker/openclaw-compose.json`

**Interfaces:**
- Produces: `OpenClawInstallation`
- Produces: `OpenClawAdapter.plan_override(...) -> list[Change]`
- Produces: `OpenClawAdapter.compose_command(override_path: Path) -> list[str]`

- [ ] **Step 1: Write failing tests for supported and unsupported installations**

Supported fixture requirements:

- Docker Compose labels expose project name, working directory, config files, and service name.
- One workspace bind mount is identified.
- The original Compose config can be rendered to JSON.

Unsupported Docker run-only installations must return `manual_action_required` with `nextAction: convert_openclaw_to_compose_or_configure_manually`; the adapter must not rewrite an unknown container.

- [ ] **Step 2: Implement workspace and Skill target resolution**

Derive:

```text
host skill path = <workspace host>/skills/resource-download-agent
container skill path = <workspace destination>/skills/resource-download-agent
host state path = <workspace host>/data/resource-download-agent/state.db
container state path = <workspace destination>/data/resource-download-agent/state.db
```

Do not accept a workspace mount that is read-only.

- [ ] **Step 3: Render an override instead of modifying the original Compose file**

The override must address the discovered service name and add:

- the shared `openclaw-media` network;
- the host download mount;
- formal media library mounts;
- generated Skill environment variables;
- the fixed absolute `mediactl` path expected by the OpenClaw allowlist.

The original Compose files remain untouched and are backed up only for audit.

- [ ] **Step 4: Generate the exact Compose command**

```python
args = ["docker", "compose", "-p", project]
for path in original_config_files:
    args.extend(["-f", str(path)])
args.extend(["-f", str(override_path), "up", "-d", service])
```

Reject missing original files or Compose config drift before execution.

- [ ] **Step 5: Plan Skill installation safely**

If the target does not exist, plan a copy from the current repository checkout excluding `.git`, `.deploy-venv`, `deploy/runtime`, `deploy/secrets`, and local `.env`. If it is a Git checkout with local modifications, return `manual_action_required`; never force reset. If it matches the current commit, plan no change.

- [ ] **Step 6: Add OpenClaw readiness checks**

Verify the container is healthy, the Skill directory is visible, `bin/mediactl` is executable, the generated environment is present without printing values, and the fixed CLI can execute `check-ready`.

- [ ] **Step 7: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_openclaw_adapter -v
python3 -m unittest discover -s tests -v
git add deploy/installer/adapters/openclaw.py deploy/templates/compose.openclaw.override.yml.j2 tests/deploy tests/fixtures/docker
git commit -m "feat(deploy): integrate existing OpenClaw with an override"
```

---

### Task 8: Replace unconditional world-writable download permissions with an explicit policy

**Files:**
- Modify: `scripts/download_fs.py`
- Modify: `scripts/resource_agent.py`
- Modify: `tests/test_download_fs.py`
- Create: `deploy/installer/permissions.py`
- Create: `tests/deploy/test_permissions.py`

**Interfaces:**
- Produces: `PermissionPlan(strategy, uid, gid, mode, acl_entries, changes)`
- Produces: `choose_permission_plan(path_stat, aria_identity, acl_supported) -> PermissionPlan`
- Produces in business code: `probe_writable(path: Path) -> bool`

- [ ] **Step 1: Replace old tests that require `0777`**

Add tests for these ordered strategies:

1. same UID, mode `0750` or stricter when writable;
2. shared GID, mode `0770`;
3. POSIX ACL grant to aria2 UID;
4. fallback `0777` only for the downloads root and `.incoming`;
5. `.ready` and `.quarantine` remain Agent-owned;
6. formal libraries are never chmod targets.

- [ ] **Step 2: Run the modified tests and observe failures**

Run: `python3 -m unittest tests.test_download_fs tests.deploy.test_permissions -v`

Expected: failures because current code always uses `ARIA2_DIR_MODE = 0o777`.

- [ ] **Step 3: Implement pure permission planning**

`choose_permission_plan` must have no filesystem side effects. It returns exact `chown`, `chmod`, or `setfacl` changes for the deployment planner to display and confirm.

- [ ] **Step 4: Restrict business-code permission mutation**

Change `ensure_aria2_writable` so it only creates managed directories and applies a mode supplied through `RESOURCE_AGENT_INCOMING_MODE`; default to `0770`, not `0777`. Existing installations requiring `0777` receive that value from the generated environment after deployment discovery.

- [ ] **Step 5: Replace `is_world_writable` readiness logic**

In `resource_agent.py`, readiness must create and delete a uniquely named zero-byte probe under `.incoming`, report a safe error if that fails, and never treat world writability itself as the requirement.

- [ ] **Step 6: Run tests and commit**

```bash
python3 -m unittest tests.test_download_fs tests.deploy.test_permissions -v
python3 -m unittest discover -s tests -v
git add scripts/download_fs.py scripts/resource_agent.py tests/test_download_fs.py deploy/installer/permissions.py tests/deploy/test_permissions.py
git commit -m "fix(storage): use discovered aria2 permission strategy"
```

---

### Task 9: Implement backup, transaction execution, resume, and rollback

**Files:**
- Create: `deploy/installer/backup.py`
- Create: `deploy/installer/executor.py`
- Create: `deploy/installer/rollback.py`
- Create: `tests/deploy/test_backup.py`
- Create: `tests/deploy/test_executor.py`
- Create: `tests/deploy/test_rollback.py`

**Interfaces:**
- Produces: `BackupManifest`
- Produces: `ExecutionJournal`
- Produces: `apply_plan(plan, context) -> DeploymentResult`
- Produces: `rollback(deployment_id, context) -> DeploymentResult`

- [ ] **Step 1: Write backup exclusion tests**

Assert that secrets, `.env`, storage-state files, and files matching configured secret values are rejected from backup. Ordinary Compose, routing, OpenClaw config, Skill source, and state database files are copied with metadata.

- [ ] **Step 2: Implement backups**

Backups live under `deploy/runtime/backups/<deployment-id>/`. Each item records source, backup relative path, mode, owner IDs, SHA-256, and restore behavior. Symlinks are recorded as symlinks and must not be followed outside the allowed roots.

- [ ] **Step 3: Write transaction failure tests**

Create fake changes A, B, C where C fails. Assert A and B reverse in reverse order, the journal records every attempted action, and final state is `rolled_back`. If rollback B fails, final state is `failed` with both the original and rollback errors preserved.

- [ ] **Step 4: Implement change handlers**

Support explicit handlers only:

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
run_verification
```

Unknown action types are a blocking error before any side effect.

- [ ] **Step 5: Implement resumable manual gates**

When QAS requires login/configuration, persist the journal with state `manual_action_required`. `apply --resume DEPLOYMENT_ID --confirmed` re-runs drift checks and resumes at the first unapplied change; it must not repeat completed non-idempotent actions.

- [ ] **Step 6: Wire CLI `apply` and `rollback`**

`apply` validates the plan, creates backups, executes, automatically runs `verify safe`, and returns the final structured state. `rollback` requires `--confirmed` and refuses a deployment ID that does not belong to the current project directory.

- [ ] **Step 7: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_backup tests.deploy.test_executor tests.deploy.test_rollback -v
python3 -m unittest discover -s tests -v
git add deploy/installer/backup.py deploy/installer/executor.py deploy/installer/rollback.py deploy/installer/cli.py tests/deploy
git commit -m "feat(deploy): add transactional apply and rollback"
```

---

### Task 10: Implement L0-L4 component and security verification

**Files:**
- Create: `deploy/installer/verifier.py`
- Create: `tests/deploy/test_verifier.py`

**Interfaces:**
- Produces: `VerificationResult(level, status, components, checks, next_action)`
- Produces: `verify(level: str, context) -> VerificationResult`

- [ ] **Step 1: Write failing state-aggregation tests**

Rules:

- any `security_block` or required component failure => `failed`;
- required manual gate => `manual_action_required`;
- optional PanSou failure => `degraded`;
- all enabled checks pass => `ready`;
- a disabled optional component is `skipped`, not `failed`.

- [ ] **Step 2: Implement L0 static checks**

Validate schema, secrets permissions, immutable images, rendered Compose, JSON routing, port conflicts, real host paths, free space, architecture, protected-root coverage, and that no formal library equals or sits inside the download root.

- [ ] **Step 3: Implement L1 health checks**

Use Docker health status when defined. When an image lacks a healthcheck, run the adapter's explicit API probe and report `healthSource: adapter_probe`; do not treat `docker ps` running state alone as healthy.

- [ ] **Step 4: Implement L2 network checks**

Run probes from the OpenClaw container to QAS, PanSou, aria2 RPC, and DNS names. Use a fixed Python one-liner only inside the deployment verifier, not the OpenClaw allowlist exposed to the Agent.

- [ ] **Step 5: Implement L3 component checks**

Verify QAS token/Cookie/plugin state, PanSou Telegram-source behavior, aria2 authenticated RPC and write probe, Skill presence, and `mediactl check-ready` JSON.

- [ ] **Step 6: Implement L4 security checks**

Verify formal media roots are not direct download targets, generated OpenClaw command policy names only the fixed absolute `mediactl`, secret values are absent from all reports, expired plans are rejected, and protected-root deletion tests from the existing suite pass.

- [ ] **Step 7: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_verifier -v
python3 -m unittest discover -s tests -v
git add deploy/installer/verifier.py tests/deploy/test_verifier.py
git commit -m "feat(deploy): add layered component and security verification"
```

---

### Task 11: Implement `safe` and user-confirmed `full` business verification

**Files:**
- Modify: `deploy/installer/verifier.py`
- Modify: `deploy/installer/cli.py`
- Create: `tests/deploy/test_business_verification.py`

**Interfaces:**
- Produces: `run_mediactl(args: Sequence[str]) -> dict[str, object]`
- Produces: `verify_safe(context) -> VerificationResult`
- Produces: `verify_full(context) -> VerificationResult`

- [ ] **Step 1: Write safe-verification tests**

Use a fake `mediactl` responder and assert the sequence:

```text
check-ready
search <configured safe query> --media-type other
preview <candidateId>
tree <candidateId>
plan download <candidateId> --node <nodeId> --media-type other
```

Assert no `execute` or `organize execute` call occurs and no `.incoming` task directory remains.

- [ ] **Step 2: Implement safe verification**

Prefer a user-provided legal share URL when search sources are unstable; otherwise use the configured harmless query. The verifier must parse only JSON fields `ok`, `status`, `nextAction`, `data`, `warnings`, and `errors` and reject extra terminal prose.

- [ ] **Step 3: Write full-verification gating tests**

Test all three required gates independently:

1. `allow_real_download` must be true;
2. `full_test_share_url` secret must exist and be a supported Quark URL;
3. CLI must include `--confirmed`.

Each missing gate returns `manual_action_required`, not `failed`.

- [ ] **Step 4: Implement full verification sequence**

Run:

```text
import/preview legal share
select configured small node
plan download
execute plan --confirmed
poll downloads show until terminal or timeout
downloads validate
organize plan
stop
```

Record task IDs and generated paths. Never call `organize execute`. On timeout, leave downloaded files in place and report their exact redacted managed path for manual cleanup.

- [ ] **Step 5: Add test cleanup boundaries**

The verifier may remove only its own `.deploy-probe-*` files. It must not automatically delete real downloaded content, even after a failed full verification.

- [ ] **Step 6: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_business_verification -v
python3 -m unittest discover -s tests -v
git add deploy/installer/verifier.py deploy/installer/cli.py tests/deploy/test_business_verification.py
git commit -m "feat(deploy): add safe and confirmed full verification"
```

---

### Task 12: Complete the interactive initializer and end-to-end CLI contract

**Files:**
- Modify: `deploy/installer/cli.py`
- Modify: `deploy/config.example.yaml`
- Create: `tests/deploy/test_init_wizard.py`
- Create: `tests/deploy/test_cli_end_to_end.py`

**Interfaces:**
- Produces: `run_init(input_stream, output_stream, project_root) -> DeploymentConfig`

- [ ] **Step 1: Write initializer transcript tests**

Test a complete noninteractive transcript that chooses UGOS, supplies project/download/library paths, selects service reuse mode `auto`, enables PanSou with an existing proxy, and creates named empty secret files. Assert the initializer never asks for or echoes secret values.

- [ ] **Step 2: Implement the wizard as a config generator only**

It writes `deploy/config.yaml`, creates `deploy/secrets/` as `0700`, creates missing secret files as empty `0600`, and returns `nextAction: fill_secret_files_then_run_discover`. It must not run Docker discovery or apply changes.

- [ ] **Step 3: Implement noninteractive overrides for Agents**

Support:

```bash
python3 deploy/cli.py init --non-interactive --config-source /path/input.yaml
```

The source is schema-validated and copied atomically; it cannot point inside `deploy/secrets/` or be a symlink escaping the project root.

- [ ] **Step 4: Add an end-to-end fake-runner test**

Exercise `init -> discover -> plan -> apply -> verify safe -> rollback` using fixture Docker responses and temporary directories. Assert every command emits one JSON document and secrets are absent from captured stdout/stderr/runtime reports.

- [ ] **Step 5: Run tests and commit**

```bash
python3 -m unittest tests.deploy.test_init_wizard tests.deploy.test_cli_end_to_end -v
python3 -m unittest discover -s tests -v
git add deploy/installer/cli.py deploy/config.example.yaml tests/deploy
git commit -m "feat(deploy): complete initializer and CLI workflow"
```

---

### Task 13: Update documentation, reference Compose, and CI

**Files:**
- Modify: `README.md`
- Modify: `docs/AGENT_DEPLOY.md`
- Modify: `deploy/docker-compose.dependencies.yml`
- Create: `docs/deployment/QUICKSTART.md`
- Create: `docs/deployment/EXISTING_OPENCLAW.md`
- Create: `docs/deployment/QAS_LOGIN.md`
- Create: `docs/deployment/PROXY.md`
- Create: `docs/deployment/TROUBLESHOOTING.md`
- Create: `docs/deployment/SECURITY.md`
- Create: `.github/workflows/deploy-tests.yml`
- Create: `tests/deploy/test_docs_examples.py`

**Interfaces:**
- Documentation commands must match the CLI parser exactly.

- [ ] **Step 1: Write command-example tests before changing docs**

Extract fenced commands beginning with `python3 deploy/cli.py` from README and deployment docs, parse them with the real CLI parser, and fail on unknown flags or commands.

- [ ] **Step 2: Replace the README deployment prompt with the deterministic flow**

Primary path:

```bash
git clone https://github.com/Inupedia/openclaw-nas-media-agent.git
cd openclaw-nas-media-agent
python3 deploy/cli.py init
python3 deploy/cli.py discover
python3 deploy/cli.py plan
python3 deploy/cli.py apply --plan-id PLAN_ID --confirmed
python3 deploy/cli.py verify --level safe
```

State plainly that login/captcha and dangerous-operation confirmations remain user actions.

- [ ] **Step 3: Rewrite `docs/AGENT_DEPLOY.md` as an execution contract**

The Agent must read structured JSON, show `changes`, request confirmation only when `nextAction` requires it, and never edit Compose/routing/QAS configuration manually when the deployer supports the operation.

- [ ] **Step 4: Convert the old Compose file into a checked reference**

Generate it from the same version lock/template during tests, or add a header stating it is generated and a test comparing normalized Compose output. It must contain immutable image references and no real secrets.

- [ ] **Step 5: Add CI**

Workflow requirements:

```yaml
on:
  pull_request:
  push:
    branches: [main]
jobs:
  test:
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
```

Install `deploy/requirements.lock`, run `python -m unittest discover -s tests -v`, and run static parsing of every committed YAML/JSON file. CI uses fixtures only and never requires real accounts.

- [ ] **Step 6: Run all verification commands**

```bash
python3 -m unittest discover -s tests -v
python3 -m json.tool deploy/schemas/config.schema.json >/dev/null
python3 -m json.tool config/routing.json >/dev/null
python3 deploy/cli.py --help >/tmp/deploy-help.json
```

Expected: tests pass; JSON parsing succeeds; CLI help follows the single-JSON contract.

- [ ] **Step 7: Commit**

```bash
git add README.md docs deploy/docker-compose.dependencies.yml .github/workflows tests/deploy/test_docs_examples.py
git commit -m "docs(deploy): publish existing OpenClaw quickstart and CI"
```

---

### Task 14: Perform final scenario verification and prepare the implementation branch for review

**Files:**
- Modify only files required to fix failures discovered by the commands below.

- [ ] **Step 1: Run the full unit suite from a clean process**

Run: `python3 -m unittest discover -s tests -v`

Expected: zero failures and zero errors. Skips are acceptable only for existing platform-specific symlink tests with an explicit reason.

- [ ] **Step 2: Run fixture end-to-end verification**

Run: `python3 -m unittest tests.deploy.test_cli_end_to_end -v`

Expected: the fake deployment reaches `ready`, then rollback reaches `rolled_back`, and no secret sentinel appears in captured artifacts.

- [ ] **Step 3: Verify plan coverage against the approved design**

Check and record evidence for: two-stage confirmation, config source of truth, secret modes, version immutability, QAS read-back, PanSou degraded state, aria2 identity/write probe, OpenClaw override, safe verification, full verification gates, transaction rollback, and protected media roots.

- [ ] **Step 4: Inspect repository changes**

```bash
git status --short
git diff --check
git log --oneline --decorate -15
```

Expected: no untracked secret/runtime files, no whitespace errors, and one focused commit per completed task.

- [ ] **Step 5: Run a real UGOS or standard Linux dry run without applying**

```bash
python3 deploy/cli.py discover
python3 deploy/cli.py plan
```

Expected: discovery and plan succeed or return a precise `manual_action_required`; no files outside `deploy/runtime/` are changed and no Docker object is created.

- [ ] **Step 6: Run a controlled real `apply` and `verify safe`**

Use a dedicated test OpenClaw/QAS/PanSou/aria2 environment and non-production directories. Confirm the plan, apply it, run safe verification, then rollback and compare backed-up configuration hashes.

- [ ] **Step 7: Commit verification-only fixes, if any**

```bash
git add <only-files-changed-by-verified-fixes>
git commit -m "fix(deploy): address end-to-end verification findings"
```

Do not create an empty commit when no fixes were needed.

## Follow-on Plans

After this plan is implemented and reviewed, write separate implementation plans in this order:

1. `2026-07-20-jiaofu-runner.md` — isolated Playwright/Chromium service, login-state lifecycle, HTTP API, and `mediactl` migration.
2. `2026-07-20-full-stack-openclaw.md` — blank-NAS Compose, OpenAI-Compatible model setup, and Web/local chat entry.
3. `2026-07-20-deployer-upgrades-and-platforms.md` — explicit version upgrades, Installer container entry, and additional NAS platform adapters.

Each follow-on plan must preserve the interfaces and state contracts established here rather than creating a second deployment path.