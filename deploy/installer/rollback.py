"""Explicit rollback of journaled deployment changes and file backups."""
from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from .backup import load_backup_manifest
from .errors import DeploymentError
from .executor import DeploymentResult, ExecutionContext
from .models import DeploymentStatus
from .runtime import atomic_write_json


def restore_backup(manifest_path: Path) -> None:
    manifest = load_backup_manifest(manifest_path)
    for entry in reversed(manifest.entries):
        destination = entry.source
        backup = manifest.backup_root / entry.backup_relative
        if entry.kind == "directory":
            destination.mkdir(parents=True, exist_ok=True)
        elif entry.kind == "symlink":
            if os.path.lexists(destination):
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(entry.link_target or "")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination, follow_symlinks=False)
        if not destination.is_symlink():
            os.chmod(destination, entry.mode)


def rollback(
    deployment_id: str,
    context: ExecutionContext,
    *,
    inverse_handlers: Mapping[
        str, Callable[[Mapping[str, object]], object]
    ] | None = None,
) -> DeploymentResult:
    journal_path = context.runtime.journal_file(deployment_id)
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DeploymentError(
            "DEPLOYMENT_JOURNAL_UNAVAILABLE",
            "cannot rollback without a valid deployment journal",
            next_action="review_deployment_journal",
        ) from None
    if Path(str(journal.get("projectRoot"))).resolve() != Path(
        context.project_root
    ).resolve():
        raise DeploymentError(
            "DEPLOYMENT_PROJECT_MISMATCH",
            "deployment journal belongs to another project root",
            severity="security_block",
            next_action="use_correct_project_root",
        )
    handlers = dict(context.rollback_handlers)
    handlers.update(dict(inverse_handlers or {}))
    errors: list[dict[str, object]] = []
    actions = list(journal.get("rollbackActions", []))
    for inverse in reversed(actions):
        if not isinstance(inverse, dict):
            continue
        action = str(inverse.get("action") or "")
        handler = handlers.get(action)
        if handler is None:
            continue
        try:
            handler(inverse)
        except Exception as error:
            errors.append(
                {
                    "code": "ROLLBACK_ACTION_FAILED",
                    "action": action,
                    "errorType": type(error).__name__,
                }
            )
    manifest_path = context.runtime.backup_dir(deployment_id) / "manifest.json"
    if manifest_path.is_file():
        try:
            restore_backup(manifest_path)
        except Exception as error:
            errors.append(
                {
                    "code": "BACKUP_RESTORE_FAILED",
                    "errorType": type(error).__name__,
                }
            )
    status = DeploymentStatus.FAILED if errors else DeploymentStatus.ROLLED_BACK
    journal["status"] = status.value
    journal["errors"] = errors
    atomic_write_json(journal_path, journal)
    return DeploymentResult(
        str(deployment_id),
        status,
        tuple(str(item) for item in journal.get("completedChangeIds", [])),
        "review_rollback_errors" if errors else "none",
        tuple(errors),
    )
