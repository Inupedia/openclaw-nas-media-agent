"""Journaled explicit-action deployment execution with resumable gates."""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import DeploymentError
from .models import Change, DeploymentPlan, DeploymentStatus
from .runtime import RuntimePaths, atomic_write_json, new_deployment_id


ALLOWED_ACTIONS = frozenset(
    {
        "create_directory",
        "set_mode",
        "set_owner",
        "set_acl",
        "copy_tree",
        "write_file",
        "create_network",
        "connect_network",
        "compose_up",
        "restart_container",
        "http_config_update",
        "run_browser_fallback",
        "run_verification",
    }
)


@dataclass(frozen=True)
class DeploymentResult:
    deployment_id: str
    status: DeploymentStatus
    completed_change_ids: tuple[str, ...] = ()
    next_action: str = "none"
    errors: tuple[Mapping[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "deploymentId": self.deployment_id,
            "status": self.status.value,
            "completedChangeIds": list(self.completed_change_ids),
            "nextAction": self.next_action,
            "errors": [dict(item) for item in self.errors],
        }


@dataclass
class ExecutionContext:
    runtime: RuntimePaths
    project_root: Path
    handlers: Mapping[str, Callable[[Change], object]]
    rollback_handlers: Mapping[str, Callable[[Mapping[str, object]], object]] = field(
        default_factory=dict
    )
    verify_safe: Callable[[], object] | None = None
    deployment_id: str | None = None


def _load_journal(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DeploymentError(
            "DEPLOYMENT_JOURNAL_UNAVAILABLE",
            "deployment journal is unavailable or invalid",
            next_action="review_deployment_journal",
        ) from None
    if not isinstance(value, dict):
        raise DeploymentError("DEPLOYMENT_JOURNAL_INVALID", "deployment journal is invalid")
    return value


def _write_journal(path: Path, journal: Mapping[str, object]) -> None:
    atomic_write_json(path, journal)


def _validate_actions(plan: DeploymentPlan, context: ExecutionContext) -> None:
    for change in plan.changes:
        if change.action not in ALLOWED_ACTIONS:
            raise DeploymentError(
                "DEPLOYMENT_ACTION_UNSUPPORTED",
                "deployment plan contains an unsupported action",
                severity="security_block",
                next_action="regenerate_plan",
                details={"action": change.action, "changeId": change.id},
            )
        if change.action not in context.handlers:
            raise DeploymentError(
                "DEPLOYMENT_ACTION_HANDLER_MISSING",
                "deployment action has no explicit handler",
                next_action="fix_deployer_handler",
                details={"action": change.action},
            )


def _rollback_completed(
    completed: list[Change],
    context: ExecutionContext,
) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = []
    for change in reversed(completed):
        inverse = dict(change.rollback)
        action = str(inverse.get("action") or "")
        handler = context.rollback_handlers.get(action)
        if not action or handler is None:
            continue
        try:
            handler(inverse)
        except Exception as error:
            errors.append(
                {
                    "code": "ROLLBACK_ACTION_FAILED",
                    "changeId": change.id,
                    "action": action,
                    "errorType": type(error).__name__,
                }
            )
    return errors


def _initial_journal(
    plan: DeploymentPlan,
    deployment_id: str,
    context: ExecutionContext,
) -> dict[str, object]:
    return {
        "deploymentId": deployment_id,
        "projectRoot": str(Path(context.project_root).resolve()),
        "planId": plan.plan_id,
        "status": "running",
        "completedChangeIds": [],
        "rollbackActions": [],
        "errors": [],
    }


def apply_plan(
    plan: DeploymentPlan,
    context: ExecutionContext,
    *,
    existing_journal: Mapping[str, object] | None = None,
) -> DeploymentResult:
    _validate_actions(plan, context)
    deployment_id = str(
        (existing_journal or {}).get("deploymentId")
        or context.deployment_id
        or new_deployment_id()
    )
    journal_path = context.runtime.journal_file(deployment_id)
    journal = dict(existing_journal or _initial_journal(plan, deployment_id, context))
    if Path(str(journal.get("projectRoot"))).resolve() != Path(context.project_root).resolve():
        raise DeploymentError(
            "DEPLOYMENT_PROJECT_MISMATCH",
            "deployment journal belongs to another project root",
            severity="security_block",
            next_action="use_correct_project_root",
        )
    completed_ids = [str(item) for item in journal.get("completedChangeIds", [])]
    completed: list[Change] = [
        change for change in plan.changes if change.id in set(completed_ids)
    ]
    degraded = str(journal.get("status") or "") == DeploymentStatus.DEGRADED.value
    _write_journal(journal_path, journal)

    for change in plan.changes:
        if change.id in completed_ids:
            continue
        try:
            outcome = context.handlers[change.action](change)
            if isinstance(outcome, Mapping) and outcome.get("manualActionRequired"):
                journal["status"] = "manual_action_required"
                journal["nextAction"] = str(
                    outcome.get("nextAction") or "complete_manual_action"
                )
                _write_journal(journal_path, journal)
                return DeploymentResult(
                    deployment_id,
                    DeploymentStatus.MANUAL_ACTION_REQUIRED,
                    tuple(completed_ids),
                    str(journal["nextAction"]),
                )
            if isinstance(outcome, Mapping) and str(outcome.get("status") or "") == DeploymentStatus.DEGRADED.value:
                degraded = True
                journal["status"] = DeploymentStatus.DEGRADED.value
                journal["nextAction"] = str(outcome.get("nextAction") or "review_optional_component")
            completed.append(change)
            completed_ids.append(change.id)
            journal["completedChangeIds"] = completed_ids
            rollback_actions = list(journal.get("rollbackActions", []))
            rollback_actions.append(dict(change.rollback))
            journal["rollbackActions"] = rollback_actions
            _write_journal(journal_path, journal)
        except Exception as error:
            original = {
                "code": getattr(error, "code", "DEPLOYMENT_CHANGE_FAILED"),
                "changeId": change.id,
                "action": change.action,
                "errorType": type(error).__name__,
            }
            rollback_errors = _rollback_completed(completed, context)
            all_errors = [original, *rollback_errors]
            status = (
                DeploymentStatus.FAILED
                if rollback_errors
                else DeploymentStatus.ROLLED_BACK
            )
            journal["status"] = status.value
            journal["errors"] = all_errors
            _write_journal(journal_path, journal)
            return DeploymentResult(
                deployment_id,
                status,
                tuple(completed_ids),
                "review_rollback_errors" if rollback_errors else "regenerate_plan",
                tuple(all_errors),
            )

    final_status = DeploymentStatus.DEGRADED if degraded else DeploymentStatus.READY
    if context.verify_safe is not None:
        try:
            outcome = context.verify_safe()
            outcome_status = getattr(outcome, "status", None)
            if outcome_status == DeploymentStatus.MANUAL_ACTION_REQUIRED:
                next_action = str(
                    getattr(outcome, "next_action", None)
                    or "complete_safe_verification"
                )
                journal["status"] = DeploymentStatus.MANUAL_ACTION_REQUIRED.value
                journal["nextAction"] = next_action
                _write_journal(journal_path, journal)
                return DeploymentResult(
                    deployment_id,
                    DeploymentStatus.MANUAL_ACTION_REQUIRED,
                    tuple(completed_ids),
                    next_action,
                )
            if outcome_status == DeploymentStatus.FAILED:
                raise DeploymentError(
                    "SAFE_VERIFICATION_FAILED",
                    "safe verification failed",
                    next_action=str(
                        getattr(outcome, "next_action", None)
                        or "inspect_safe_verification"
                    ),
                )
            if outcome_status == DeploymentStatus.DEGRADED:
                final_status = DeploymentStatus.DEGRADED
        except Exception as error:
            original = {
                "code": getattr(error, "code", "SAFE_VERIFICATION_FAILED"),
                "action": "run_verification",
                "errorType": type(error).__name__,
            }
            rollback_errors = _rollback_completed(completed, context)
            all_errors = [original, *rollback_errors]
            status = (
                DeploymentStatus.FAILED
                if rollback_errors
                else DeploymentStatus.ROLLED_BACK
            )
            journal["status"] = status.value
            journal["errors"] = all_errors
            _write_journal(journal_path, journal)
            return DeploymentResult(
                deployment_id,
                status,
                tuple(completed_ids),
                "review_rollback_errors" if rollback_errors else "regenerate_plan",
                tuple(all_errors),
            )

    journal["status"] = final_status.value
    journal["nextAction"] = "none"
    _write_journal(journal_path, journal)
    return DeploymentResult(
        deployment_id,
        final_status,
        tuple(completed_ids),
    )


def resume_deployment(
    deployment_id: str,
    plan: DeploymentPlan,
    context: ExecutionContext,
) -> DeploymentResult:
    journal = _load_journal(context.runtime.journal_file(deployment_id))
    if str(journal.get("planId")) != plan.plan_id:
        raise DeploymentError(
            "DEPLOYMENT_PLAN_MISMATCH",
            "resume plan does not match the deployment journal",
            status="manual_action_required",
            next_action="use_original_plan",
        )
    return apply_plan(plan, context, existing_journal=journal)
