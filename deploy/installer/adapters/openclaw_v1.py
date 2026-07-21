"""Supported existing OpenClaw Compose profile integration and exec confinement."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..command import CommandRunner
from ..discovery import OpenClawInstallation
from ..errors import DeploymentError
from ..models import Change, ChangePhase, ComponentResult, ComponentStatus, Severity


_EXCLUDED_NAMES = {".git", ".deploy-venv", ".venv", ".env", "__pycache__"}
_EXCLUDED_DEPLOY = {"runtime", "secrets", "config.yaml"}


@dataclass(frozen=True)
class OpenClawPaths:
    host_skill_path: Path
    container_skill_path: Path
    host_state_path: Path
    container_state_path: Path
    container_mediactl: Path

    def to_dict(self) -> dict[str, str]:
        return {
            key: str(value)
            for key, value in {
                "hostSkillPath": self.host_skill_path,
                "containerSkillPath": self.container_skill_path,
                "hostStatePath": self.host_state_path,
                "containerStatePath": self.container_state_path,
                "containerMediactl": self.container_mediactl,
            }.items()
        }


def resolve_paths(installation: OpenClawInstallation) -> OpenClawPaths:
    host_skill = installation.workspace_host_dir / "skills" / "resource-download-agent"
    container_skill = (
        installation.workspace_container_dir / "skills" / "resource-download-agent"
    )
    host_state = (
        installation.workspace_host_dir / "data" / "resource-download-agent" / "state.db"
    )
    container_state = (
        installation.workspace_container_dir
        / "data"
        / "resource-download-agent"
        / "state.db"
    )
    return OpenClawPaths(
        host_skill,
        container_skill,
        host_state,
        container_state,
        container_skill / "bin" / "mediactl",
    )


def compose_command(
    installation: OpenClawInstallation,
    override_path: Path,
) -> list[str]:
    command = ["docker", "compose"]
    for path in installation.compose_config_files:
        command.extend(["-f", str(path)])
    command.extend(
        [
            "-f",
            str(Path(override_path)),
            "up",
            "-d",
            installation.compose_service,
        ]
    )
    return command


def _validate_exec_shape(config: Mapping[str, object]) -> None:
    tools = config.get("tools")
    if tools is None:
        return
    if not isinstance(tools, Mapping):
        raise DeploymentError(
            "OPENCLAW_TOOLS_SHAPE_UNSUPPORTED",
            "OpenClaw tools configuration is not an object",
            status="manual_action_required",
            next_action="review_openclaw_config",
        )
    execute = tools.get("exec")
    if execute is not None and not isinstance(execute, Mapping):
        raise DeploymentError(
            "OPENCLAW_EXEC_SHAPE_UNSUPPORTED",
            "OpenClaw tools.exec configuration is unsupported",
            status="manual_action_required",
            next_action="review_openclaw_exec_config",
        )


def constrained_config(
    current: Mapping[str, object],
    paths: OpenClawPaths,
    env_refs: Mapping[str, str],
) -> dict[str, object]:
    _validate_exec_shape(current)
    result = json.loads(json.dumps(dict(current)))
    skills = result.setdefault("skills", {})
    if not isinstance(skills, dict):
        raise DeploymentError(
            "OPENCLAW_SKILLS_SHAPE_UNSUPPORTED",
            "OpenClaw skills configuration is unsupported",
            status="manual_action_required",
            next_action="review_openclaw_config",
        )
    entries = skills.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise DeploymentError(
            "OPENCLAW_SKILL_ENTRIES_UNSUPPORTED",
            "OpenClaw skills.entries configuration is unsupported",
            status="manual_action_required",
            next_action="review_openclaw_config",
        )
    entry = entries.setdefault("resource-download-agent", {})
    if not isinstance(entry, dict):
        raise DeploymentError(
            "OPENCLAW_SKILL_ENTRY_UNSUPPORTED",
            "resource-download-agent entry is unsupported",
            status="manual_action_required",
            next_action="review_openclaw_config",
        )
    environment = entry.setdefault("env", {})
    if not isinstance(environment, dict):
        raise DeploymentError(
            "OPENCLAW_SKILL_ENV_UNSUPPORTED",
            "resource-download-agent env configuration is unsupported",
            status="manual_action_required",
            next_action="review_openclaw_config",
        )
    environment.update({str(key): str(value) for key, value in env_refs.items()})
    environment["RESOURCE_AGENT_STATE_DB"] = str(paths.container_state_path)

    tools = result.setdefault("tools", {})
    assert isinstance(tools, dict)
    tools["exec"] = {
        "security": "allowlist",
        "ask": "off",
        "allowlist": [str(paths.container_mediactl)],
    }
    return result


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {
        name
        for name in names
        if name in _EXCLUDED_NAMES or name.endswith(".pyc")
    }
    if Path(directory).name == "deploy":
        ignored.update(name for name in names if name in _EXCLUDED_DEPLOY)
    return ignored


class OpenClawV1Adapter:
    def __init__(
        self,
        installation: OpenClawInstallation,
        config_path: Path,
        repository_root: Path,
        *,
        runner: CommandRunner,
    ) -> None:
        self.installation = installation
        self.config_path = Path(config_path)
        self.repository_root = Path(repository_root)
        self.runner = runner
        self.paths = resolve_paths(installation)

    def discover(self) -> dict[str, object]:
        if not self.installation.compose_config_files:
            raise DeploymentError(
                "OPENCLAW_COMPOSE_REQUIRED",
                "OpenClaw must be managed by Docker Compose",
                status="manual_action_required",
                next_action="convert_openclaw_to_compose_or_configure_manually",
            )
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise DeploymentError(
                "OPENCLAW_CONFIG_UNAVAILABLE",
                "supported OpenClaw config could not be read",
                status="manual_action_required",
                next_action="locate_openclaw_config",
            ) from None
        if not isinstance(config, dict):
            raise DeploymentError(
                "OPENCLAW_CONFIG_UNSUPPORTED",
                "OpenClaw config must be a JSON object",
                status="manual_action_required",
                next_action="review_openclaw_config",
            )
        _validate_exec_shape(config)
        return {
            "installation": self.installation.to_dict(),
            "paths": self.paths.to_dict(),
            "configSupported": True,
        }

    def plan(
        self,
        override_path: Path,
        env_refs: Mapping[str, str],
    ) -> tuple[Change, ...]:
        self.discover()
        return (
            Change(
                id="openclaw-install-skill",
                phase=ChangePhase.FILESYSTEM,
                component="openclaw",
                action="copy_tree",
                target=str(self.paths.host_skill_path),
                after={
                    "source": str(self.repository_root),
                    "exclusions": sorted(_EXCLUDED_NAMES | _EXCLUDED_DEPLOY),
                },
                side_effect=True,
                rollback={"action": "restore_tree", "path": str(self.paths.host_skill_path)},
            ),
            Change(
                id="openclaw-write-config",
                phase=ChangePhase.OPENCLAW_OVERRIDE,
                component="openclaw",
                action="write_file",
                target=str(self.config_path),
                after={
                    "mediactl": str(self.paths.container_mediactl),
                    "envKeys": sorted(env_refs),
                    "execSecurity": "allowlist",
                },
                side_effect=True,
                rollback={"action": "restore_file", "target": str(self.config_path)},
            ),
            Change(
                id="openclaw-compose-up",
                phase=ChangePhase.RESTART,
                component="openclaw",
                action="compose_up",
                target=self.installation.compose_service,
                after={"argv": compose_command(self.installation, override_path)},
                side_effect=True,
                rollback={
                    "action": "restart_container",
                    "container": self.installation.container_name,
                },
            ),
        )

    def install_skill(self) -> None:
        target = self.paths.host_skill_path
        if (target / ".git").exists():
            status = self.runner.run(
                ["git", "-C", str(target), "status", "--porcelain"]
            )
            if status.returncode != 0 or status.stdout.strip():
                raise DeploymentError(
                    "OPENCLAW_SKILL_DIRTY",
                    "existing Skill checkout has local changes",
                    status="manual_action_required",
                    next_action="review_skill_local_changes",
                )
        temporary = target.parent / f".{target.name}.deploy-tmp"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.repository_root, temporary, ignore=_copy_ignore)
        mediactl = temporary / "bin" / "mediactl"
        os.chmod(mediactl, 0o755)
        old = target.parent / f".{target.name}.previous"
        if old.exists():
            shutil.rmtree(old)
        if target.exists():
            os.replace(target, old)
        os.replace(temporary, target)

    def apply_config(
        self,
        env_refs: Mapping[str, str],
        backup_path: Path,
    ) -> None:
        current = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            raise DeploymentError(
                "OPENCLAW_CONFIG_UNSUPPORTED",
                "OpenClaw config must be an object",
            )
        updated = constrained_config(current, self.paths, env_refs)
        backup = Path(backup_path)
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(self.config_path, backup)
        os.chmod(backup, 0o600)
        descriptor, name = tempfile.mkstemp(
            dir=self.config_path.parent,
            prefix=".openclaw-",
            suffix=".json",
        )
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(updated, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.config_path)
            os.chmod(self.config_path, 0o600)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise

    def verify(self) -> ComponentResult:
        checks = [
            self.runner.run(
                [
                    "docker",
                    "inspect",
                    self.installation.container_name,
                    "--format",
                    "{{.State.Status}}",
                ]
            ),
            self.runner.run(
                [
                    "docker",
                    "exec",
                    self.installation.container_name,
                    "test",
                    "-x",
                    str(self.paths.container_mediactl),
                ]
            ),
            self.runner.run(
                [
                    "docker",
                    "exec",
                    self.installation.container_name,
                    str(self.paths.container_mediactl),
                    "check-ready",
                ],
                timeout=60,
            ),
        ]
        ready = all(item.returncode == 0 for item in checks)
        return ComponentResult(
            component="openclaw",
            status=ComponentStatus.READY if ready else ComponentStatus.FAILED,
            required=True,
            enabled=True,
            next_action="none" if ready else "inspect_openclaw_skill",
            severity=None if ready else Severity.BLOCKING,
            details={
                "containerHealthy": checks[0].stdout.strip() == "running",
                "mediactlExecutable": checks[1].returncode == 0,
                "checkReady": checks[2].returncode == 0,
            },
        )
