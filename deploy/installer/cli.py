"""Command parser and dispatch for the deterministic existing-OpenClaw deployer."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from .backup import create_backup
from .business_verifier import (
    BusinessVerificationContext,
    verify_full,
    verify_safe,
)
from .command import CommandRunner
from .config import config_digest, load_config
from .discovery import discover
from .errors import DeploymentError
from .executor import ExecutionContext, apply_plan, resume_deployment
from .initializer import run_init
from .models import Change, ChangePhase, DeploymentPlan
from .output import emit, result_payload
from .planning import PlanFacts, build_plan, canonical_digest, validate_plan
from .renderer import render_and_validate
from .rollback import restore_backup, rollback as rollback_deployment
from .runtime import RuntimePaths, atomic_write_json, new_deployment_id
from .secrets import SecretStore
from .versions import VersionLock


class CliUsageError(ValueError):
    """Raised instead of allowing argparse to print prose or exit."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise CliUsageError("invalid command or arguments")


def build_parser() -> SafeArgumentParser:
    parser = SafeArgumentParser(
        prog="openclaw-media-deploy",
        add_help=False,
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument("--help", action="store_true", dest="show_help")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", add_help=False)
    init.add_argument("--non-interactive", action="store_true")
    init.add_argument("--config-source")

    subparsers.add_parser("discover", add_help=False)
    subparsers.add_parser("plan", add_help=False)

    apply_parser = subparsers.add_parser("apply", add_help=False)
    apply_source = apply_parser.add_mutually_exclusive_group(required=True)
    apply_source.add_argument("--plan-id")
    apply_source.add_argument("--resume", dest="deployment_id")
    apply_parser.add_argument("--confirmed", action="store_true")

    verify = subparsers.add_parser("verify", add_help=False)
    verify.add_argument("--level", choices=("safe", "full"), default="safe")
    verify.add_argument("--confirmed", action="store_true")

    rollback = subparsers.add_parser("rollback", add_help=False)
    rollback.add_argument("--deployment-id", required=True)
    rollback.add_argument("--confirmed", action="store_true")

    versions = subparsers.add_parser("versions", add_help=False)
    versions_subparsers = versions.add_subparsers(
        dest="versions_command",
        required=True,
    )
    versions_subparsers.add_parser("check", add_help=False)
    return parser


def _help_payload() -> dict[str, object]:
    return result_payload(
        ok=True,
        status="ready",
        next_action="none",
        data={
            "program": "openclaw-media-deploy",
            "commands": [
                "init",
                "discover",
                "plan",
                "apply",
                "verify",
                "rollback",
                "versions check",
            ],
        },
    )


def _error_payload(error: DeploymentError) -> dict[str, object]:
    return result_payload(
        ok=False,
        status=error.status,
        next_action=error.next_action,
        errors=[
            {
                "code": error.code,
                "message": str(error),
                "severity": error.severity,
                "details": error.details,
            }
        ],
    )


def _project_root(value: Path | None = None) -> Path:
    return Path(__file__).resolve().parents[2] if value is None else Path(value).resolve()


def run_init_command(
    project_root: Path,
    *,
    input_stream: TextIO,
    prompt_stream: TextIO,
    non_interactive: bool = False,
    config_source: Path | None = None,
) -> dict[str, object]:
    config, created = run_init(
        input_stream,
        prompt_stream,
        _project_root(project_root),
        non_interactive=non_interactive,
        config_source=config_source,
    )
    return result_payload(
        ok=True,
        status="manual_action_required",
        next_action="fill_secret_files_then_run_discover",
        data={
            "mode": config.mode,
            "configPath": str(_project_root(project_root) / "deploy/config.yaml"),
            "secretFiles": list(config.secret_names()),
            "createdSecretFiles": list(created),
        },
    )


def run_discover(
    project_root: Path,
    *,
    runner: CommandRunner | None = None,
) -> dict[str, object]:
    root = _project_root(project_root)
    config = load_config(root / "deploy/config.yaml")
    runtime = RuntimePaths.for_project(root)
    active_runner = runner or CommandRunner(cwd=root)
    report = discover(config, active_runner)
    report_data = report.to_dict()
    atomic_write_json(runtime.discovery_report, report_data)
    return result_payload(
        ok=True,
        status="ready",
        next_action="run_plan",
        data=report_data,
    )


def _default_changes(root: Path, config, runtime: RuntimePaths) -> tuple[Change, ...]:
    compose_target = config.project_dir / "compose.dependencies.yml"
    routing_target = root / "config/routing.json"
    directories = (
        config.project_dir,
        config.project_dir / "qas/config",
        config.project_dir / "pansou/cache",
        config.project_dir / "aria2/config",
        config.downloads_dir,
        config.downloads_dir / ".incoming",
        config.downloads_dir / ".ready",
        config.downloads_dir / ".quarantine",
    )
    changes: list[Change] = []
    for index, path in enumerate(directories):
        changes.append(
            Change(
                id=f"directory-{index:02d}",
                phase=ChangePhase.FILESYSTEM,
                component="filesystem",
                action="create_directory",
                target=str(path),
                after={"mode": "0770" if path.name in {".incoming", "downloads"} else "0750"},
                side_effect=True,
                rollback={"action": "remove_empty_directory", "path": str(path)},
            )
        )
    changes.extend(
        [
            Change(
                "network-openclaw-media",
                ChangePhase.NETWORK,
                "docker",
                "create_network",
                "openclaw-media",
                after={"name": "openclaw-media"},
                side_effect=True,
                rollback={"action": "remove_network", "name": "openclaw-media"},
            ),
            Change(
                "write-dependency-compose",
                ChangePhase.FILESYSTEM,
                "compose",
                "write_file",
                str(compose_target),
                after={"source": str(runtime.rendered_dir / "compose.dependencies.yml"), "mode": "0644"},
                side_effect=True,
                rollback={"action": "restore_file"},
            ),
            Change(
                "write-routing",
                ChangePhase.FILESYSTEM,
                "routing",
                "write_file",
                str(routing_target),
                after={"source": str(runtime.rendered_dir / "routing.json"), "mode": "0644"},
                side_effect=True,
                rollback={"action": "restore_file"},
            ),
            Change(
                "compose-dependencies-up",
                ChangePhase.COMPOSE,
                "dependencies",
                "compose_up",
                "openclaw-media-dependencies",
                after={
                    "argv": [
                        "docker",
                        "compose",
                        "-f",
                        str(compose_target),
                        "up",
                        "-d",
                    ]
                },
                side_effect=True,
                rollback={
                    "action": "compose_down",
                    "argv": ["docker", "compose", "-f", str(compose_target), "down"],
                },
            ),
        ]
    )
    return tuple(changes)


def run_plan(
    project_root: Path,
    *,
    runner: CommandRunner | None = None,
    changes: Sequence[Change] | None = None,
    now: int | None = None,
) -> dict[str, object]:
    root = _project_root(project_root)
    config = load_config(root / "deploy/config.yaml")
    runtime = RuntimePaths.for_project(root)
    secrets = SecretStore(root / "deploy/secrets")
    active_runner = runner or CommandRunner(cwd=root)
    report = discover(config, active_runner)
    atomic_write_json(runtime.discovery_report, report.to_dict())
    versions = VersionLock.load(root / "deploy/versions.yaml")
    render_and_validate(config, versions, runtime.rendered_dir, active_runner)
    selected_changes = _default_changes(root, config, runtime) if changes is None else tuple(changes)
    plan = build_plan(
        config,
        secrets,
        report,
        selected_changes,
        int(time.time()) if now is None else int(now),
    )
    plan_data = plan.to_dict()
    atomic_write_json(runtime.plan_file, plan_data)
    return result_payload(
        ok=True,
        status="ready_for_apply",
        next_action="request_confirmation",
        data=plan_data,
    )


def _load_plan(runtime: RuntimePaths) -> DeploymentPlan:
    try:
        value = json.loads(runtime.plan_file.read_text(encoding="utf-8"))
        return DeploymentPlan.from_dict(value)
    except (OSError, json.JSONDecodeError, ValueError, KeyError):
        raise DeploymentError(
            "PLAN_UNAVAILABLE",
            "deployment plan is unavailable or invalid",
            status="manual_action_required",
            next_action="run_plan",
        ) from None


def _copy_atomic(source: Path, target: Path, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.deploy-tmp"
    shutil.copy2(source, temporary)
    os.chmod(temporary, mode)
    os.replace(temporary, target)


def _execution_environment(config, secrets: SecretStore) -> dict[str, str]:
    environment = dict(os.environ)
    environment["QAS_WEBUI_PASSWORD"] = secrets.read(config.qas.password_secret)
    environment["ARIA2_RPC_SECRET"] = secrets.read(config.aria2.rpc_secret)
    if config.pansou.proxy.url_secret:
        value = secrets.read(config.pansou.proxy.url_secret)
        environment["PANSOU_PROXY_URL"] = value
        environment["PANSOU_HTTP_PROXY"] = value if value.startswith("http") else ""
        environment["PANSOU_HTTPS_PROXY"] = value if value.startswith("http") else ""
    return environment


def _default_execution_context(
    root: Path,
    config,
    secrets: SecretStore,
    runtime: RuntimePaths,
    *,
    verify_safe_callback: Callable[[], object] | None,
) -> ExecutionContext:
    runner = CommandRunner(
        cwd=root,
        env=_execution_environment(config, secrets),
        secret_values=[secrets.read(name) for name in config.secret_names()],
    )

    def create_directory(change: Change):
        path = Path(change.target)
        path.mkdir(parents=True, exist_ok=True)
        mode = int(str((change.after or {}).get("mode", "0750")), 8) if isinstance(change.after, Mapping) else 0o750
        os.chmod(path, mode)

    def write_file(change: Change):
        after = change.after if isinstance(change.after, Mapping) else {}
        source = Path(str(after.get("source") or ""))
        if not source.is_file():
            raise DeploymentError("WRITE_SOURCE_MISSING", "planned rendered source is missing", next_action="run_plan")
        _copy_atomic(source, Path(change.target), int(str(after.get("mode", "0600")), 8))

    def create_network(change: Change):
        inspected = runner.run(["docker", "network", "inspect", change.target])
        if inspected.returncode != 0:
            created = runner.run(["docker", "network", "create", change.target])
            if created.returncode != 0:
                raise DeploymentError("NETWORK_CREATE_FAILED", "unable to create Docker network", next_action="fix_docker_network")

    def compose_up(change: Change):
        after = change.after if isinstance(change.after, Mapping) else {}
        argv = after.get("argv")
        if not isinstance(argv, list) or not argv:
            raise DeploymentError("COMPOSE_ARGV_MISSING", "compose action has no argv")
        result = runner.run([str(item) for item in argv], timeout=180)
        if result.returncode != 0:
            raise DeploymentError("COMPOSE_UP_FAILED", "Docker Compose failed", next_action="inspect_compose_logs", details={"stderr": result.stderr[-500:]})

    handlers = {
        "create_directory": create_directory,
        "write_file": write_file,
        "create_network": create_network,
        "compose_up": compose_up,
    }
    return ExecutionContext(
        runtime,
        root,
        handlers,
        verify_safe=verify_safe_callback,
    )


def _current_facts(config, secrets, report, plan: DeploymentPlan) -> PlanFacts:
    return PlanFacts(
        config_digest=config_digest(config),
        secret_digest=secrets.metadata_digest(config.secret_names()),
        discovery_digest=canonical_digest(report.to_dict()),
        managed_files_digest=plan.managed_files_digest,
    )


def run_apply(
    project_root: Path,
    *,
    plan_id: str | None = None,
    resume_id: str | None = None,
    confirmed: bool = False,
    runner: CommandRunner | None = None,
    execution_context: ExecutionContext | None = None,
    verify_safe_callback: Callable[[], object] | None = None,
    now: int | None = None,
) -> dict[str, object]:
    if not confirmed:
        raise DeploymentError("CONFIRMATION_REQUIRED", "apply requires --confirmed", status="manual_action_required", next_action="confirm_apply")
    root = _project_root(project_root)
    runtime = RuntimePaths.for_project(root)
    plan = _load_plan(runtime)
    if plan_id is not None and plan.plan_id != plan_id:
        raise DeploymentError("PLAN_ID_MISMATCH", "provided plan id does not match plan.json", status="manual_action_required", next_action="run_plan")
    config = load_config(root / "deploy/config.yaml")
    secrets = SecretStore(root / "deploy/secrets")
    active_runner = runner or CommandRunner(cwd=root)
    report = discover(config, active_runner)
    validate_plan(plan, _current_facts(config, secrets, report, plan), int(time.time()) if now is None else int(now))
    context = execution_context or _default_execution_context(
        root,
        config,
        secrets,
        runtime,
        verify_safe_callback=verify_safe_callback,
    )
    sources = [
        Path(change.target)
        for change in plan.changes
        if change.action in {"write_file", "copy_tree"}
        and Path(change.target).exists()
    ]
    if resume_id:
        result = resume_deployment(resume_id, plan, context)
    else:
        deployment_id = context.deployment_id or new_deployment_id()
        context.deployment_id = deployment_id
        manifest = create_backup(
            sources,
            runtime,
            deployment_id,
            allowed_roots=[
                root,
                config.project_dir,
                config.downloads_dir,
                *config.libraries.values(),
            ],
            secret_sentinels=[
                secrets.read(name) for name in config.secret_names()
            ],
        )
        result = apply_plan(plan, context)
        if result.status.value in {"rolled_back", "failed"}:
            try:
                restore_backup(manifest.backup_root / "manifest.json")
            except Exception:
                pass
    payload = result.to_dict()
    atomic_write_json(runtime.apply_report, payload)
    return result_payload(ok=result.status.value in {"ready", "degraded"}, status=result.status.value, next_action=result.next_action, data=payload, errors=list(result.errors))


def _subprocess_mediactl(argv: Sequence[str]):
    return subprocess.run(list(argv), text=True, capture_output=True, check=False, timeout=120)


def run_verify(
    project_root: Path,
    *,
    level: str,
    confirmed: bool = False,
    executor: Callable[[Sequence[str]], object] = _subprocess_mediactl,
) -> dict[str, object]:
    root = _project_root(project_root)
    runtime = RuntimePaths.for_project(root)
    config = load_config(root / "deploy/config.yaml")
    secrets = SecretStore(root / "deploy/secrets")
    context = BusinessVerificationContext(root / "bin/mediactl", config, secrets, executor, confirmed)
    result = verify_safe(context) if level == "safe" else verify_full(context)
    data = result.to_dict()
    atomic_write_json(runtime.verify_report, data)
    return result_payload(ok=result.status.value in {"ready", "degraded"}, status=result.status.value, next_action=result.next_action, data=data)


def run_rollback(
    project_root: Path,
    deployment_id: str,
    *,
    confirmed: bool,
    execution_context: ExecutionContext | None = None,
) -> dict[str, object]:
    if not confirmed:
        raise DeploymentError("CONFIRMATION_REQUIRED", "rollback requires --confirmed", status="manual_action_required", next_action="confirm_rollback")
    root = _project_root(project_root)
    runtime = RuntimePaths.for_project(root)
    context = execution_context or ExecutionContext(runtime, root, {})
    result = rollback_deployment(deployment_id, context)
    return result_payload(ok=result.status.value == "rolled_back", status=result.status.value, next_action=result.next_action, data=result.to_dict(), errors=list(result.errors))


def run_versions(project_root: Path) -> dict[str, object]:
    lock = VersionLock.load(_project_root(project_root) / "deploy/versions.yaml")
    return result_payload(ok=True, status="ready", next_action="none", data={"schemaVersion": lock.schema_version, "resolvedAt": lock.resolved_at, "components": {name: item.to_dict() for name, item in lock.components.items()}})


def main(
    argv: Sequence[str] | None = None,
    *,
    stream: TextIO | None = None,
    input_stream: TextIO | None = None,
    prompt_stream: TextIO | None = None,
    project_root: Path | None = None,
    runner_factory: Callable[[], CommandRunner] = CommandRunner,
    execution_context_factory: Callable[[RuntimePaths], ExecutionContext] | None = None,
    mediactl_executor: Callable[[Sequence[str]], object] = _subprocess_mediactl,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    target = sys.stdout if stream is None else stream
    source = sys.stdin if input_stream is None else input_stream
    prompts = sys.stderr if prompt_stream is None else prompt_stream
    root = _project_root(project_root)
    try:
        args = build_parser().parse_args(arguments)
        if getattr(args, "show_help", False) or not getattr(args, "command", None):
            emit(_help_payload(), target)
            return 0
        if args.command == "init":
            payload = run_init_command(root, input_stream=source, prompt_stream=prompts, non_interactive=bool(getattr(args, "non_interactive", False)), config_source=Path(args.config_source) if getattr(args, "config_source", None) else None)
        elif args.command == "discover":
            payload = run_discover(root, runner=runner_factory())
        elif args.command == "plan":
            payload = run_plan(root, runner=runner_factory())
        elif args.command == "apply":
            runtime = RuntimePaths.for_project(root)
            context = execution_context_factory(runtime) if execution_context_factory else None
            payload = run_apply(root, plan_id=getattr(args, "plan_id", None), resume_id=getattr(args, "deployment_id", None), confirmed=bool(getattr(args, "confirmed", False)), runner=runner_factory(), execution_context=context)
        elif args.command == "verify":
            payload = run_verify(root, level=args.level, confirmed=bool(getattr(args, "confirmed", False)), executor=mediactl_executor)
        elif args.command == "rollback":
            runtime = RuntimePaths.for_project(root)
            context = execution_context_factory(runtime) if execution_context_factory else None
            payload = run_rollback(root, args.deployment_id, confirmed=bool(args.confirmed), execution_context=context)
        else:
            payload = run_versions(root)
        emit(payload, target)
        return 0 if payload["ok"] else (2 if payload["status"] == "manual_action_required" else 1)
    except CliUsageError:
        error = DeploymentError("INVALID_ARGUMENTS", "invalid command or arguments", next_action="review_command")
        emit(_error_payload(error), target)
        return 2
    except DeploymentError as error:
        emit(_error_payload(error), target)
        return 2 if error.status == "manual_action_required" else 1


if __name__ == "__main__":
    raise SystemExit(main())
