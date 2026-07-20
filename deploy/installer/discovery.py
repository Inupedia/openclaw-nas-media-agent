"""Read-only NAS, Docker and existing OpenClaw discovery."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .command import CommandResult, CommandRunner
from .config import DeploymentConfig
from .errors import DeploymentError
from .platforms import PlatformFacts
from .platforms.linux import linux_facts
from .platforms.ugos import is_ugos, ugos_facts


@dataclass(frozen=True)
class PathFact:
    path: Path
    exists: bool
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    kind: str | None = None
    device: str | None = None
    mount_point: str | None = None
    available_kib: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "exists": self.exists,
            "mode": None if self.mode is None else f"{self.mode:04o}",
            "uid": self.uid,
            "gid": self.gid,
            "kind": self.kind,
            "device": self.device,
            "mountPoint": self.mount_point,
            "availableKiB": self.available_kib,
        }


@dataclass(frozen=True)
class OpenClawInstallation:
    container_name: str
    image: str
    compose_project: str
    compose_service: str
    compose_working_dir: Path
    compose_config_files: tuple[Path, ...]
    workspace_host_dir: Path
    workspace_container_dir: Path
    networks: tuple[str, ...]
    health: str

    def to_dict(self) -> dict[str, object]:
        return {
            "containerName": self.container_name,
            "image": self.image,
            "composeProject": self.compose_project,
            "composeService": self.compose_service,
            "composeWorkingDir": str(self.compose_working_dir),
            "composeConfigFiles": [str(path) for path in self.compose_config_files],
            "workspaceHostDir": str(self.workspace_host_dir),
            "workspaceContainerDir": str(self.workspace_container_dir),
            "networks": list(self.networks),
            "health": self.health,
        }


@dataclass(frozen=True)
class DiscoveryReport:
    platform: PlatformFacts
    docker_version: Mapping[str, object]
    compose_version: str
    openclaw: OpenClawInstallation
    containers: tuple[Mapping[str, object], ...]
    networks: tuple[Mapping[str, object], ...]
    paths: tuple[PathFact, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform.to_dict(),
            "dockerVersion": dict(self.docker_version),
            "composeVersion": self.compose_version,
            "openclaw": self.openclaw.to_dict(),
            "containers": [dict(item) for item in self.containers],
            "networks": [dict(item) for item in self.networks],
            "paths": [item.to_dict() for item in self.paths],
        }


def _required(
    result: CommandResult,
    *,
    code: str,
    message: str,
    next_action: str,
) -> str:
    if result.returncode != 0:
        raise DeploymentError(
            code,
            message,
            status="manual_action_required",
            next_action=next_action,
            details={"returncode": result.returncode, "stderr": result.stderr[:500]},
        )
    return result.stdout.strip()


def _json_object(text: str, *, code: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise DeploymentError(code, "discovery command returned invalid JSON") from None
    if not isinstance(value, dict):
        raise DeploymentError(code, "discovery command did not return an object")
    return value


def _json_array(text: str, *, code: str) -> list[dict[str, object]]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise DeploymentError(code, "discovery command returned invalid JSON") from None
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DeploymentError(code, "discovery command did not return an object array")
    return [dict(item) for item in value]


def _json_lines(text: str, *, code: str) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise DeploymentError(code, "discovery command returned invalid JSON lines") from None
        if not isinstance(value, dict):
            raise DeploymentError(code, "discovery JSON line must be an object")
        values.append(dict(value))
    return values


def _container_name(row: Mapping[str, object]) -> str:
    return str(row.get("Names") or row.get("Name") or "").split(",", 1)[0].strip()


def _container_image(row: Mapping[str, object]) -> str:
    return str(row.get("Image") or "").strip()


def _has_openclaw_evidence(row: Mapping[str, object], explicit: str | None) -> bool:
    name = _container_name(row)
    if explicit is not None:
        return name == explicit
    evidence = f"{name} {_container_image(row)}".casefold()
    return "openclaw" in evidence


def _labels(inspect: Mapping[str, object]) -> Mapping[str, object]:
    config = inspect.get("Config")
    if not isinstance(config, Mapping):
        return {}
    labels = config.get("Labels")
    return labels if isinstance(labels, Mapping) else {}


def _workspace_mount(inspect: Mapping[str, object]) -> tuple[Path, Path] | None:
    mounts = inspect.get("Mounts")
    if not isinstance(mounts, Sequence) or isinstance(mounts, (str, bytes)):
        return None
    for item in mounts:
        if not isinstance(item, Mapping):
            continue
        destination = str(item.get("Destination") or "")
        source = str(item.get("Source") or "")
        read_write = bool(item.get("RW", False))
        if (
            str(item.get("Type") or "") == "bind"
            and destination.endswith("/.openclaw/workspace")
            and source
            and read_write
        ):
            return Path(source), Path(destination)
    return None


def _network_names(inspect: Mapping[str, object]) -> tuple[str, ...]:
    network_settings = inspect.get("NetworkSettings")
    if not isinstance(network_settings, Mapping):
        return ()
    networks = network_settings.get("Networks")
    if not isinstance(networks, Mapping):
        return ()
    return tuple(sorted(str(name) for name in networks))


def _health(inspect: Mapping[str, object]) -> str:
    state = inspect.get("State")
    if not isinstance(state, Mapping):
        return "unknown"
    health = state.get("Health")
    if isinstance(health, Mapping) and health.get("Status"):
        return str(health["Status"])
    return str(state.get("Status") or "unknown")


def _compose_files(raw: object) -> tuple[Path, ...]:
    text = str(raw or "")
    return tuple(Path(item.strip()) for item in text.split(",") if item.strip())


def _candidate(
    row: Mapping[str, object],
    *,
    runner: CommandRunner,
) -> OpenClawInstallation | None:
    name = _container_name(row)
    inspect_result = runner.run(["docker", "inspect", name])
    if inspect_result.returncode != 0:
        return None
    inspected = _json_array(inspect_result.stdout, code="DOCKER_INSPECT_INVALID")
    if len(inspected) != 1:
        return None
    item = inspected[0]
    labels = _labels(item)
    required_labels = (
        "com.docker.compose.project",
        "com.docker.compose.service",
        "com.docker.compose.project.working_dir",
        "com.docker.compose.project.config_files",
    )
    if any(not str(labels.get(label) or "").strip() for label in required_labels):
        return None
    workspace = _workspace_mount(item)
    if workspace is None:
        return None
    project = str(labels["com.docker.compose.project"])
    service = str(labels["com.docker.compose.service"])
    compose_result = runner.run(
        ["docker", "compose", "-p", project, "config", "--format", "json"]
    )
    if compose_result.returncode != 0:
        return None
    compose = _json_object(compose_result.stdout, code="COMPOSE_CONFIG_INVALID")
    services = compose.get("services")
    if not isinstance(services, Mapping) or service not in services:
        return None
    config = item.get("Config")
    image = (
        str(config.get("Image") or "")
        if isinstance(config, Mapping)
        else _container_image(row)
    )
    return OpenClawInstallation(
        container_name=name,
        image=image,
        compose_project=project,
        compose_service=service,
        compose_working_dir=Path(str(labels["com.docker.compose.project.working_dir"])),
        compose_config_files=_compose_files(
            labels["com.docker.compose.project.config_files"]
        ),
        workspace_host_dir=workspace[0],
        workspace_container_dir=workspace[1],
        networks=_network_names(item),
        health=_health(item),
    )


def _parse_stat(path: Path, result: CommandResult) -> PathFact:
    if result.returncode != 0:
        return PathFact(path=path, exists=False)
    parts = result.stdout.strip().split(":", 3)
    if len(parts) != 4:
        return PathFact(path=path, exists=True)
    try:
        mode = int(parts[0], 8)
        uid = int(parts[1])
        gid = int(parts[2])
    except ValueError:
        mode = uid = gid = None
    return PathFact(
        path=path,
        exists=True,
        mode=mode,
        uid=uid,
        gid=gid,
        kind=parts[3],
    )


def _with_df(fact: PathFact, result: CommandResult) -> PathFact:
    if result.returncode != 0:
        return fact
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return fact
    columns = lines[-1].split()
    if len(columns) < 6:
        return fact
    try:
        available = int(columns[3])
    except ValueError:
        available = None
    return PathFact(
        path=fact.path,
        exists=fact.exists,
        mode=fact.mode,
        uid=fact.uid,
        gid=fact.gid,
        kind=fact.kind,
        device=columns[0],
        mount_point=columns[-1],
        available_kib=available,
    )


def _configured_paths(config: DeploymentConfig) -> tuple[Path, ...]:
    paths = {
        config.project_dir,
        config.downloads_dir,
        config.organizing_dir,
        *config.libraries.values(),
    }
    if config.openclaw.workspace_host_dir is not None:
        paths.add(config.openclaw.workspace_host_dir)
    if config.openclaw.config_host_path is not None:
        paths.add(config.openclaw.config_host_path)
    return tuple(sorted(paths, key=str))


def discover(config: DeploymentConfig, runner: CommandRunner) -> DiscoveryReport:
    kernel = _required(
        runner.run(["uname", "-s"]),
        code="HOST_UNAVAILABLE",
        message="unable to identify host kernel",
        next_action="run_on_linux_host",
    )
    architecture = _required(
        runner.run(["uname", "-m"]),
        code="HOST_UNAVAILABLE",
        message="unable to identify host architecture",
        next_action="run_on_linux_host",
    )
    os_release_result = runner.run(["cat", "/etc/os-release"])
    os_release = os_release_result.stdout if os_release_result.returncode == 0 else ""
    docker_text = _required(
        runner.run(["docker", "version", "--format", "{{json .}}"]),
        code="DOCKER_UNAVAILABLE",
        message="Docker is unavailable",
        next_action="install_or_start_docker",
    )
    docker_version = _json_object(docker_text, code="DOCKER_VERSION_INVALID")
    compose_result = runner.run(["docker", "compose", "version", "--short"])
    compose_available = compose_result.returncode == 0 and bool(compose_result.stdout.strip())
    if not compose_available:
        raise DeploymentError(
            "COMPOSE_UNAVAILABLE",
            "Docker Compose v2 is required",
            status="manual_action_required",
            next_action="install_docker_compose_v2",
        )
    containers_text = _required(
        runner.run(["docker", "ps", "-a", "--format", "{{json .}}"]),
        code="DOCKER_LIST_FAILED",
        message="unable to list Docker containers",
        next_action="grant_docker_access",
    )
    networks_text = _required(
        runner.run(["docker", "network", "ls", "--format", "{{json .}}"]),
        code="DOCKER_NETWORK_LIST_FAILED",
        message="unable to list Docker networks",
        next_action="grant_docker_access",
    )
    containers = _json_lines(containers_text, code="DOCKER_LIST_INVALID")
    networks = _json_lines(networks_text, code="DOCKER_NETWORK_LIST_INVALID")

    explicit = (
        None
        if config.openclaw.container_name == "auto"
        else config.openclaw.container_name
    )
    evidence_rows = [row for row in containers if _has_openclaw_evidence(row, explicit)]
    if not evidence_rows:
        raise DeploymentError(
            "OPENCLAW_NOT_FOUND",
            "no OpenClaw container candidate was found",
            status="manual_action_required",
            next_action="specify_openclaw_container",
        )
    valid = [
        installation
        for row in evidence_rows
        if (installation := _candidate(row, runner=runner)) is not None
    ]
    if not valid:
        raise DeploymentError(
            "OPENCLAW_UNSUPPORTED_INSTALLATION",
            "OpenClaw is not a supported Compose-managed installation",
            status="manual_action_required",
            next_action="convert_openclaw_to_compose_or_configure_manually",
        )
    if len(valid) > 1:
        raise DeploymentError(
            "OPENCLAW_AMBIGUOUS",
            "multiple valid OpenClaw containers were found",
            status="manual_action_required",
            next_action="choose_openclaw_container",
            details={"candidates": [item.container_name for item in valid]},
        )

    path_facts: list[PathFact] = []
    for path in _configured_paths(config):
        fact = _parse_stat(
            path,
            runner.run(["stat", "-c", "%a:%u:%g:%F", str(path)]),
        )
        fact = _with_df(fact, runner.run(["df", "-P", str(path)]))
        path_facts.append(fact)

    platform = (
        ugos_facts(
            kernel=kernel,
            architecture=architecture,
            compose_available=compose_available,
        )
        if is_ugos(os_release)
        else linux_facts(
            kernel=kernel,
            architecture=architecture,
            compose_available=compose_available,
        )
    )
    return DiscoveryReport(
        platform=platform,
        docker_version=docker_version,
        compose_version=compose_result.stdout.strip(),
        openclaw=valid[0],
        containers=tuple(containers),
        networks=tuple(networks),
        paths=tuple(path_facts),
    )
