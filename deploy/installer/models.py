"""Immutable data contracts shared by deployment modules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeploymentStatus(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    MANUAL_ACTION_REQUIRED = "manual_action_required"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ComponentStatus(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    MANUAL_ACTION_REQUIRED = "manual_action_required"
    FAILED = "failed"
    SKIPPED = "skipped"


class Severity(str, Enum):
    WARNING = "warning"
    DEGRADED = "degraded"
    BLOCKING = "blocking"
    SECURITY_BLOCK = "security_block"


class ChangePhase(str, Enum):
    BACKUP = "backup"
    FILESYSTEM = "filesystem"
    NETWORK = "network"
    COMPOSE = "compose"
    SERVICE_CONFIG = "service_config"
    OPENCLAW_OVERRIDE = "openclaw_override"
    RESTART = "restart"
    VERIFICATION = "verification"


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _mapping(value: object, *, field_name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return {str(key): item for key, item in value.items()}


@dataclass(frozen=True)
class Change:
    id: str
    phase: ChangePhase
    component: str
    action: str
    target: str
    before: object = None
    after: object = None
    side_effect: bool = False
    rollback: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "phase": self.phase.value,
            "component": self.component,
            "action": self.action,
            "target": self.target,
            "before": _json_value(self.before),
            "after": _json_value(self.after),
            "sideEffect": self.side_effect,
            "rollback": _json_value(self.rollback),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Change":
        data = _mapping(value, field_name="change")
        return cls(
            id=str(data["id"]),
            phase=ChangePhase(str(data["phase"])),
            component=str(data["component"]),
            action=str(data["action"]),
            target=str(data["target"]),
            before=data.get("before"),
            after=data.get("after"),
            side_effect=bool(data.get("sideEffect", False)),
            rollback=_mapping(data.get("rollback", {}), field_name="rollback"),
        )


@dataclass(frozen=True)
class DeploymentPlan:
    plan_id: str
    created_at: int
    expires_at: int
    config_digest: str
    secret_digest: str
    discovery_digest: str
    managed_files_digest: str
    changes: tuple[Change, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "planId": self.plan_id,
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
            "configDigest": self.config_digest,
            "secretDigest": self.secret_digest,
            "discoveryDigest": self.discovery_digest,
            "managedFilesDigest": self.managed_files_digest,
            "changes": [change.to_dict() for change in self.changes],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "DeploymentPlan":
        data = _mapping(value, field_name="deployment plan")
        raw_changes = data.get("changes", [])
        if not isinstance(raw_changes, Sequence) or isinstance(raw_changes, (str, bytes)):
            raise ValueError("changes must be an array")
        return cls(
            plan_id=str(data["planId"]),
            created_at=int(data["createdAt"]),
            expires_at=int(data["expiresAt"]),
            config_digest=str(data["configDigest"]),
            secret_digest=str(data["secretDigest"]),
            discovery_digest=str(data["discoveryDigest"]),
            managed_files_digest=str(data.get("managedFilesDigest", "")),
            changes=tuple(Change.from_dict(_mapping(item, field_name="change")) for item in raw_changes),
        )


@dataclass(frozen=True)
class ComponentResult:
    component: str
    status: ComponentStatus
    required: bool
    enabled: bool
    next_action: str = "none"
    details: Mapping[str, object] = field(default_factory=dict)
    severity: Severity | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "component": self.component,
            "status": self.status.value,
            "required": self.required,
            "enabled": self.enabled,
            "nextAction": self.next_action,
            "details": _json_value(self.details),
        }
        if self.severity is not None:
            payload["severity"] = self.severity.value
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ComponentResult":
        data = _mapping(value, field_name="component result")
        raw_severity = data.get("severity")
        return cls(
            component=str(data["component"]),
            status=ComponentStatus(str(data["status"])),
            required=bool(data["required"]),
            enabled=bool(data["enabled"]),
            next_action=str(data.get("nextAction", "none")),
            details=_mapping(data.get("details", {}), field_name="details"),
            severity=Severity(str(raw_severity)) if raw_severity is not None else None,
        )


@dataclass(frozen=True)
class VerificationResult:
    level: str
    status: DeploymentStatus
    components: tuple[ComponentResult, ...] = ()
    checks: tuple[Mapping[str, object], ...] = ()
    next_action: str = "none"

    def to_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "status": self.status.value,
            "components": [component.to_dict() for component in self.components],
            "checks": [_json_value(check) for check in self.checks],
            "nextAction": self.next_action,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "VerificationResult":
        data = _mapping(value, field_name="verification result")
        raw_components = data.get("components", [])
        raw_checks = data.get("checks", [])
        if not isinstance(raw_components, Sequence) or isinstance(raw_components, (str, bytes)):
            raise ValueError("components must be an array")
        if not isinstance(raw_checks, Sequence) or isinstance(raw_checks, (str, bytes)):
            raise ValueError("checks must be an array")
        checks: list[Mapping[str, object]] = []
        for raw_check in raw_checks:
            check = _mapping(raw_check, field_name="check")
            if "severity" in check:
                check["severity"] = Severity(str(check["severity"])).value
            checks.append(check)
        return cls(
            level=str(data["level"]),
            status=DeploymentStatus(str(data["status"])),
            components=tuple(
                ComponentResult.from_dict(_mapping(item, field_name="component result"))
                for item in raw_components
            ),
            checks=tuple(checks),
            next_action=str(data.get("nextAction", "none")),
        )
