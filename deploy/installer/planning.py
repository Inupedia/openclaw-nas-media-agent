"""Immutable deployment plans, conflict checks and drift validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .config import DeploymentConfig, config_digest
from .discovery import DiscoveryReport
from .errors import DeploymentError
from .models import Change, ChangePhase, DeploymentPlan
from .redaction import redact
from .runtime import new_plan_id
from .secrets import SecretStore

PLAN_VALIDITY_SECONDS = 30 * 60
_PHASE_ORDER = {phase: index for index, phase in enumerate(ChangePhase)}


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PlanFacts:
    config_digest: str
    secret_digest: str
    discovery_digest: str
    managed_files_digest: str

    @classmethod
    def from_plan(cls, plan: DeploymentPlan) -> "PlanFacts":
        return cls(
            config_digest=plan.config_digest,
            secret_digest=plan.secret_digest,
            discovery_digest=plan.discovery_digest,
            managed_files_digest=plan.managed_files_digest,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "configDigest": self.config_digest,
            "secretDigest": self.secret_digest,
            "discoveryDigest": self.discovery_digest,
            "managedFilesDigest": self.managed_files_digest,
        }


def _secret_values(config: DeploymentConfig, secrets: SecretStore) -> tuple[str, ...]:
    return tuple(
        value
        for name in config.secret_names()
        if (value := secrets.read(name))
    )


def _safe_changes(
    changes: Sequence[Change],
    *,
    secret_values: Sequence[str],
) -> tuple[Change, ...]:
    safe: list[Change] = []
    seen_ids: set[str] = set()
    target_states: dict[str, str] = {}
    for change in changes:
        if change.id in seen_ids:
            raise DeploymentError(
                "PLAN_CHANGE_ID_CONFLICT",
                "deployment change identifiers must be unique",
                next_action="fix_change_generation",
                details={"changeId": change.id},
            )
        seen_ids.add(change.id)
        redacted = redact(change.to_dict(), secret_values)
        if not isinstance(redacted, Mapping):
            raise DeploymentError("PLAN_CHANGE_INVALID", "deployment change is invalid")
        normalized = Change.from_dict(redacted)
        if normalized.action in {
            "write_file",
            "copy_tree",
            "http_config_update",
            "set_mode",
            "set_owner",
            "set_acl",
        }:
            final_digest = canonical_digest(normalized.after)
            existing = target_states.get(normalized.target)
            if existing is not None and existing != final_digest:
                raise DeploymentError(
                    "PLAN_TARGET_CONFLICT",
                    "multiple changes produce different final states for one target",
                    severity="security_block",
                    next_action="fix_change_generation",
                    details={"target": normalized.target},
                )
            target_states[normalized.target] = final_digest
        safe.append(normalized)
    return tuple(sorted(safe, key=lambda item: (_PHASE_ORDER[item.phase], item.id)))


def _managed_files_digest(changes: Sequence[Change]) -> str:
    managed = [
        {
            "target": change.target,
            "before": change.before,
        }
        for change in changes
        if change.action in {"write_file", "copy_tree", "http_config_update"}
    ]
    return canonical_digest(sorted(managed, key=lambda item: str(item["target"])))


def build_plan(
    config: DeploymentConfig,
    secrets: SecretStore,
    discovery: DiscoveryReport,
    changes: Sequence[Change],
    now: int,
) -> DeploymentPlan:
    values = _secret_values(config, secrets)
    safe_changes = _safe_changes(changes, secret_values=values)
    created_at = int(now)
    return DeploymentPlan(
        plan_id=new_plan_id(),
        created_at=created_at,
        expires_at=created_at + PLAN_VALIDITY_SECONDS,
        config_digest=config_digest(config),
        secret_digest=secrets.metadata_digest(config.secret_names()),
        discovery_digest=canonical_digest(discovery.to_dict()),
        managed_files_digest=_managed_files_digest(safe_changes),
        changes=safe_changes,
    )


def validate_plan(plan: DeploymentPlan, current_facts: PlanFacts, now: int) -> None:
    current_time = int(now)
    if current_time < plan.created_at:
        raise DeploymentError(
            "PLAN_TIME_INVALID",
            "deployment plan creation time is in the future",
            next_action="regenerate_plan",
        )
    if current_time >= plan.expires_at:
        raise DeploymentError(
            "PLAN_EXPIRED",
            "deployment plan has expired",
            status="manual_action_required",
            next_action="regenerate_plan",
            details={"expiresAt": plan.expires_at},
        )
    expected = PlanFacts.from_plan(plan).to_dict()
    actual = current_facts.to_dict()
    drift = [name for name in expected if expected[name] != actual[name]]
    if drift:
        raise DeploymentError(
            "PLAN_DRIFT",
            "deployment facts changed after the plan was created",
            status="manual_action_required",
            next_action="regenerate_plan",
            details={"drift": drift},
        )
