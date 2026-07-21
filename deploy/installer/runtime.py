"""Private runtime paths and atomic JSON persistence."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import DeploymentError

_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _security_error(code: str, message: str) -> DeploymentError:
    return DeploymentError(
        code,
        message,
        severity="security_block",
        next_action="review_runtime_path",
    )


def _validate_identifier(value: str) -> str:
    identifier = str(value)
    if not identifier or not _IDENTIFIER_PATTERN.fullmatch(identifier) or ".." in identifier:
        raise _security_error("INVALID_RUNTIME_ID", "invalid runtime identifier")
    return identifier


def _ensure_private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise _security_error("RUNTIME_SYMLINK", f"runtime directory is a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise _security_error("RUNTIME_NOT_DIRECTORY", f"runtime path is not a directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
    os.chmod(path, _PRIVATE_DIR_MODE)
    return path


@dataclass(frozen=True)
class RuntimePaths:
    project_root: Path
    root: Path
    rendered_dir: Path
    reports_dir: Path
    backups_dir: Path
    journals_dir: Path
    plan_file: Path
    discovery_report: Path
    apply_report: Path
    verify_report: Path

    @classmethod
    def for_project(cls, project_root: Path) -> "RuntimePaths":
        project = Path(project_root).resolve()
        deploy_dir = project / "deploy"
        if deploy_dir.is_symlink():
            raise _security_error("DEPLOY_SYMLINK", "deploy directory must not be a symlink")
        if not deploy_dir.is_dir():
            raise DeploymentError(
                "DEPLOY_DIRECTORY_MISSING",
                f"deploy directory does not exist: {deploy_dir}",
                next_action="run_from_repository_root",
            )
        root = _ensure_private_directory(deploy_dir / "runtime")
        rendered = _ensure_private_directory(root / "rendered")
        reports = _ensure_private_directory(root / "reports")
        backups = _ensure_private_directory(root / "backups")
        journals = _ensure_private_directory(root / "journals")
        return cls(
            project_root=project,
            root=root,
            rendered_dir=rendered,
            reports_dir=reports,
            backups_dir=backups,
            journals_dir=journals,
            plan_file=root / "plan.json",
            discovery_report=reports / "discovery.json",
            apply_report=reports / "apply.json",
            verify_report=reports / "verify.json",
        )

    def backup_dir(self, deployment_id: str) -> Path:
        identifier = _validate_identifier(deployment_id)
        return _ensure_private_directory(self.backups_dir / identifier)

    def journal_file(self, deployment_id: str) -> Path:
        identifier = _validate_identifier(deployment_id)
        return self.journals_dir / f"{identifier}.json"


def new_plan_id() -> str:
    return secrets.token_urlsafe(18)


def new_deployment_id() -> str:
    return secrets.token_urlsafe(18)


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically replace a private JSON file without following symlinks."""

    target = Path(path)
    parent = target.parent
    if parent.is_symlink():
        raise _security_error("RUNTIME_PARENT_SYMLINK", f"runtime parent is a symlink: {parent}")
    if not parent.is_dir():
        raise DeploymentError(
            "RUNTIME_PARENT_MISSING",
            f"runtime parent does not exist: {parent}",
            next_action="initialize_runtime",
        )
    if target.is_symlink():
        raise _security_error("RUNTIME_FILE_SYMLINK", f"runtime file is a symlink: {target}")
    if target.exists() and not target.is_file():
        raise _security_error("RUNTIME_FILE_INVALID", f"runtime target is not a file: {target}")

    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, _PRIVATE_FILE_MODE)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                dict(payload),
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, _PRIVATE_FILE_MODE)
    except Exception:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
