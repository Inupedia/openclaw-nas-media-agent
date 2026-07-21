"""Layered L0-L4 deployment and security verification."""
from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import DeploymentConfig
from .errors import DeploymentError
from .models import (
    ComponentResult,
    ComponentStatus,
    DeploymentPlan,
    DeploymentStatus,
    Severity,
    VerificationResult,
)
from .planning import PlanFacts, validate_plan
from .secrets import SecretStore
from .versions import VersionLock


@dataclass(frozen=True)
class VerificationContext:
    config: DeploymentConfig
    secrets: SecretStore
    versions: VersionLock
    rendered_compose: Path | None = None
    rendered_routing: Path | None = None
    component_checks: tuple[Callable[[], ComponentResult], ...] = ()
    network_checks: tuple[Callable[[], ComponentResult], ...] = ()
    report_paths: tuple[Path, ...] = ()
    expected_mediactl: str = ""
    effective_exec_allowlist: tuple[str, ...] = ()
    plan: DeploymentPlan | None = None
    current_plan_facts: PlanFacts | None = None
    protected_operation_check: Callable[[], bool] | None = None
    architecture: str = "amd64"
    minimum_free_bytes: int = 64 * 1024 * 1024
    now: int | None = None


def _check(
    check_id: str,
    ok: bool,
    *,
    severity: Severity = Severity.BLOCKING,
    details: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": check_id,
        "ok": bool(ok),
        "severity": severity.value,
        "details": dict(details or {}),
    }


def aggregate_status(
    components: Sequence[ComponentResult],
    checks: Sequence[Mapping[str, object]],
) -> tuple[DeploymentStatus, str]:
    for item in checks:
        if (
            not bool(item.get("ok", False))
            and str(item.get("severity")) == Severity.SECURITY_BLOCK.value
        ):
            return DeploymentStatus.FAILED, "resolve_security_block"
    for component in components:
        if component.severity == Severity.SECURITY_BLOCK:
            return DeploymentStatus.FAILED, component.next_action
        if component.status == ComponentStatus.MANUAL_ACTION_REQUIRED:
            return DeploymentStatus.MANUAL_ACTION_REQUIRED, component.next_action
    for item in checks:
        if (
            not bool(item.get("ok", False))
            and str(item.get("severity")) == Severity.BLOCKING.value
        ):
            return DeploymentStatus.FAILED, "fix_verification_failure"
    for component in components:
        if component.required and component.status == ComponentStatus.FAILED:
            return DeploymentStatus.FAILED, component.next_action
    for component in components:
        if component.status == ComponentStatus.DEGRADED or (
            not component.required and component.status == ComponentStatus.FAILED
        ):
            return DeploymentStatus.DEGRADED, component.next_action
    for item in checks:
        if not bool(item.get("ok", False)):
            return DeploymentStatus.DEGRADED, "review_verification_warnings"
    return DeploymentStatus.READY, "none"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _load_rendered(path: Path, *, json_file: bool) -> object:
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if json_file else yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise DeploymentError(
            "VERIFICATION_RENDERED_CONFIG_INVALID",
            "rendered deployment configuration is invalid",
            next_action="regenerate_plan",
            details={"path": str(path), "reason": type(error).__name__},
        ) from None


def static_checks(context: VerificationContext) -> tuple[dict[str, object], ...]:
    config = context.config
    checks: list[dict[str, object]] = []
    checks.append(_check("l0.mode", config.mode == "existing-openclaw"))
    secret_ok = True
    secret_error = ""
    try:
        for name in config.secret_names():
            context.secrets.read(name)
    except DeploymentError as error:
        secret_ok = False
        secret_error = error.code
    checks.append(
        _check(
            "l0.secrets",
            secret_ok,
            severity=Severity.SECURITY_BLOCK,
            details={"errorCode": secret_error},
        )
    )
    images_ok = True
    try:
        for component in context.versions.components:
            context.versions.image(component)
    except (DeploymentError, AttributeError):
        images_ok = False
    checks.append(
        _check("l0.images", images_ok, severity=Severity.SECURITY_BLOCK)
    )
    for check_id, path, is_json in (
        ("l0.compose", context.rendered_compose, False),
        ("l0.routing", context.rendered_routing, True),
    ):
        if path is None:
            checks.append(_check(check_id, True, details={"skipped": True}))
            continue
        try:
            value = _load_rendered(path, json_file=is_json)
            valid = isinstance(value, dict)
        except DeploymentError:
            valid = False
        checks.append(_check(check_id, valid))
    ports = [config.qas.port, config.pansou.port, config.aria2.rpc_port]
    checks.append(_check("l0.ports", len(ports) == len(set(ports))))
    all_paths = [
        config.downloads_dir,
        config.organizing_dir,
        *config.libraries.values(),
    ]
    checks.append(
        _check(
            "l0.paths_absolute",
            all(path.is_absolute() for path in all_paths),
            severity=Severity.SECURITY_BLOCK,
        )
    )
    separated = all(
        not _is_under(library, config.downloads_dir)
        and not _is_under(config.downloads_dir, library)
        for library in config.libraries.values()
    )
    checks.append(
        _check(
            "l0.library_separation",
            separated,
            severity=Severity.SECURITY_BLOCK,
        )
    )
    existing_anchor = next((path for path in all_paths if path.exists()), None)
    free_bytes = 0
    if existing_anchor is not None:
        try:
            free_bytes = shutil.disk_usage(existing_anchor).free
        except OSError:
            free_bytes = 0
    checks.append(
        _check(
            "l0.free_space",
            existing_anchor is None or free_bytes >= context.minimum_free_bytes,
            details={
                "freeBytes": free_bytes,
                "minimumBytes": context.minimum_free_bytes,
            },
        )
    )
    checks.append(
        _check(
            "l0.architecture",
            context.architecture in {"amd64", "arm64"},
            details={"architecture": context.architecture},
        )
    )
    return tuple(checks)


def security_checks(context: VerificationContext) -> tuple[dict[str, object], ...]:
    checks: list[dict[str, object]] = []
    expected = context.expected_mediactl
    allowlist_ok = bool(expected) and context.effective_exec_allowlist == (expected,)
    checks.append(
        _check(
            "l4.exec_allowlist",
            allowlist_ok,
            severity=Severity.SECURITY_BLOCK,
            details={"entryCount": len(context.effective_exec_allowlist)},
        )
    )
    sentinels: list[str] = []
    for name in context.config.secret_names():
        value = context.secrets.read(name)
        if value:
            sentinels.append(value)
    report_clean = True
    dirty_reports: list[str] = []
    for path in context.report_paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        if any(value in text for value in sentinels):
            report_clean = False
            dirty_reports.append(str(path))
    checks.append(
        _check(
            "l4.report_redaction",
            report_clean,
            severity=Severity.SECURITY_BLOCK,
            details={"dirtyReports": dirty_reports},
        )
    )
    if context.plan is not None and context.current_plan_facts is not None:
        expired_rejected = False
        try:
            validate_plan(
                context.plan,
                context.current_plan_facts,
                max(
                    context.plan.expires_at,
                    int(context.now or time.time()),
                ),
            )
        except DeploymentError as error:
            expired_rejected = error.code == "PLAN_EXPIRED"
        checks.append(
            _check(
                "l4.expired_plan_rejected",
                expired_rejected,
                severity=Severity.SECURITY_BLOCK,
            )
        )
    if context.protected_operation_check is not None:
        try:
            refused = bool(context.protected_operation_check())
        except Exception:
            refused = False
        checks.append(
            _check(
                "l4.protected_path_refused",
                refused,
                severity=Severity.SECURITY_BLOCK,
            )
        )
    return tuple(checks)


def verify(level: str, context: VerificationContext) -> VerificationResult:
    if level not in {"safe", "full", "component"}:
        raise DeploymentError(
            "VERIFICATION_LEVEL_INVALID",
            "verification level is unsupported",
            next_action="review_command",
        )
    checks = [*static_checks(context)]
    components: list[ComponentResult] = []
    for callback in context.network_checks:
        components.append(callback())
    for callback in context.component_checks:
        components.append(callback())
    checks.extend(security_checks(context))
    status, next_action = aggregate_status(components, checks)
    return VerificationResult(
        level=level,
        status=status,
        components=tuple(components),
        checks=tuple(checks),
        next_action=next_action,
    )
