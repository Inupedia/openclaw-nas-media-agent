"""Strict deterministic rendering for Compose and media routing."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateError,
    TemplateNotFound,
    UndefinedError,
)

from .command import CommandRunner
from .config import DeploymentConfig
from .discovery import OpenClawInstallation
from .errors import DeploymentError
from .versions import VersionLock


@dataclass(frozen=True)
class RenderedFile:
    path: Path
    digest: str
    mode: int
    size: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "digest": self.digest,
            "mode": f"{self.mode:04o}",
            "size": self.size,
        }


def _template_root() -> Path:
    return Path(__file__).resolve().parents[1] / "templates"


def _yaml_quote(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _environment() -> Environment:
    environment = Environment(
        loader=FileSystemLoader(str(_template_root())),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    environment.filters["yaml_quote"] = _yaml_quote
    return environment


def _security_error(code: str, message: str) -> DeploymentError:
    return DeploymentError(
        code,
        message,
        severity="security_block",
        next_action="review_render_destination",
    )


def _atomic_write_text(path: Path, content: str, mode: int) -> RenderedFile:
    destination = Path(path)
    if mode not in {0o600, 0o644}:
        raise DeploymentError(
            "RENDER_MODE_INVALID",
            "rendered file mode must be 0600 or 0644",
            next_action="fix_renderer_mode",
        )
    parent = destination.parent
    if parent.is_symlink():
        raise _security_error(
            "RENDER_PARENT_SYMLINK",
            f"render destination parent is a symlink: {parent}",
        )
    if parent.exists() and not parent.is_dir():
        raise _security_error(
            "RENDER_PARENT_INVALID",
            f"render destination parent is not a directory: {parent}",
        )
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.is_symlink():
        raise _security_error(
            "RENDER_TARGET_SYMLINK",
            f"render destination is a symlink: {destination}",
        )
    if destination.exists() and not destination.is_file():
        raise _security_error(
            "RENDER_TARGET_INVALID",
            f"render destination is not a regular file: {destination}",
        )

    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    encoded = content.encode("utf-8")
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, mode)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return RenderedFile(
        path=destination,
        digest="sha256:" + hashlib.sha256(encoded).hexdigest(),
        mode=mode,
        size=len(encoded),
    )


def render_template(
    name: str,
    context: Mapping[str, object],
    destination: Path,
    *,
    mode: int | None = None,
) -> RenderedFile:
    """Render one known template with StrictUndefined and atomic output."""

    template_name = str(name)
    selected_mode = (
        0o644 if template_name.startswith("compose.") else 0o600
    ) if mode is None else int(mode)
    try:
        template = _environment().get_template(template_name)
        content = template.render(dict(context))
    except TemplateNotFound:
        raise DeploymentError(
            "TEMPLATE_NOT_FOUND",
            f"deployment template does not exist: {template_name}",
            next_action="restore_deployment_templates",
        ) from None
    except UndefinedError as error:
        raise DeploymentError(
            "TEMPLATE_CONTEXT_MISSING",
            "deployment template context is incomplete",
            next_action="fix_renderer_context",
            details={"template": template_name, "reason": str(error)},
        ) from None
    except TemplateError as error:
        raise DeploymentError(
            "TEMPLATE_RENDER_FAILED",
            "deployment template could not be rendered",
            next_action="fix_deployment_template",
            details={"template": template_name, "reason": type(error).__name__},
        ) from None
    return _atomic_write_text(Path(destination), content, selected_mode)


def build_compose_context(
    config: DeploymentConfig,
    versions: VersionLock,
) -> dict[str, object]:
    """Build a non-secret context for the dependency Compose template."""

    project = config.project_dir
    if config.pansou.proxy.mode == "existing":
        proxy_ref = "${PANSOU_PROXY_URL:-}"
    elif config.pansou.proxy.mode == "managed":
        proxy_ref = "socks5://openclaw-media-proxy:1080"
    else:
        proxy_ref = ""
    return {
        "images": {
            "qas": versions.image("qas"),
            "pansou": versions.image("pansou"),
            "aria2": versions.image("aria2"),
        },
        "timezone": config.timezone,
        "paths": {
            "qas_config": str(project / "qas" / "config"),
            "pansou_cache": str(project / "pansou" / "cache"),
            "aria2_config": str(project / "aria2" / "config"),
            "downloads": str(config.downloads_dir),
        },
        "ports": {
            "qas": f"127.0.0.1:${{QAS_PORT:-{config.qas.port}}}:5005",
            "pansou": f"127.0.0.1:${{PANSOU_PORT:-{config.pansou.port}}}:8888",
            "aria2_rpc": (
                f"127.0.0.1:${{ARIA2_RPC_PORT:-{config.aria2.rpc_port}}}:6800"
            ),
        },
        "qas": {
            "username": config.qas.username,
        },
        "pansou": {
            "channels": ",".join(config.pansou.channels),
            "plugins": ",".join(config.pansou.plugins),
        },
        "aria2": {
            "puid": (
                str(config.aria2.uid)
                if config.aria2.uid is not None
                else "${PUID:-1000}"
            ),
            "pgid": (
                str(config.aria2.gid)
                if config.aria2.gid is not None
                else "${PGID:-1000}"
            ),
        },
        "secret_refs": {
            "qas_webui_password": "${QAS_WEBUI_PASSWORD:?loaded by deployer}",
            "aria2_rpc_secret": "${ARIA2_RPC_SECRET:?loaded by deployer}",
            "pansou_proxy_url": proxy_ref,
            "pansou_http_proxy": "${PANSOU_HTTP_PROXY:-}" if proxy_ref else "",
            "pansou_https_proxy": "${PANSOU_HTTPS_PROXY:-}" if proxy_ref else "",
        },
    }


def build_openclaw_override_context(
    config: DeploymentConfig,
    installation: OpenClawInstallation,
    *,
    qas_token_file: Path,
    aria2_secret_file: Path,
) -> dict[str, object]:
    return {
        "service_name": installation.compose_service,
        "downloads_host": str(config.downloads_dir),
        "movie_host": str(config.libraries.movie),
        "drama_host": str(config.libraries.drama),
        "anime_host": str(config.libraries.anime),
        "documentary_host": str(config.libraries.documentary),
        "show_host": str(config.libraries.show),
        "other_host": str(config.libraries.other),
        "qas_token_file": str(Path(qas_token_file)),
        "aria2_secret_file": str(Path(aria2_secret_file)),
        "pansou_max_candidates": str(config.pansou.max_candidates),
    }


def validate_compose_stack(
    installation: OpenClawInstallation,
    override_path: Path,
    runner: CommandRunner,
) -> None:
    argv = ["docker", "compose"]
    for config_file in installation.compose_config_files:
        argv.extend(["-f", str(config_file)])
    argv.extend(["-f", str(Path(override_path)), "config", "--quiet"])
    result = runner.run(argv, timeout=60)
    if result.returncode != 0:
        raise DeploymentError(
            "OPENCLAW_OVERRIDE_VALIDATION_FAILED",
            "rendered OpenClaw Compose override is invalid",
            severity="blocking",
            next_action="fix_openclaw_override",
            details={"stderr": result.stderr[-500:]},
        )


def render_openclaw_override(
    config: DeploymentConfig,
    installation: OpenClawInstallation,
    destination: Path,
    *,
    qas_token_file: Path,
    aria2_secret_file: Path,
    runner: CommandRunner,
) -> RenderedFile:
    rendered = render_template(
        "compose.openclaw.override.yml.j2",
        build_openclaw_override_context(
            config,
            installation,
            qas_token_file=qas_token_file,
            aria2_secret_file=aria2_secret_file,
        ),
        destination,
        mode=0o644,
    )
    validate_compose_stack(installation, rendered.path, runner)
    return rendered



def render_proxy_compose(
    config: DeploymentConfig,
    versions: VersionLock,
    secrets_root: Path,
    destination: Path,
    runner: CommandRunner,
) -> RenderedFile:
    secret_name = config.pansou.proxy.singbox_config_secret
    if config.pansou.proxy.mode != "managed" or not secret_name:
        raise DeploymentError(
            "PROXY_RENDER_NOT_MANAGED",
            "managed proxy Compose can only be rendered for managed mode",
            next_action="configure_managed_proxy",
        )
    rendered = render_template(
        "compose.proxy.yml.j2",
        {
            "image": versions.image("sing_box"),
            "config_path": str(Path(secrets_root) / secret_name),
        },
        destination,
        mode=0o644,
    )
    validate_compose(rendered.path, runner)
    return rendered

def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _protected_roots(config: DeploymentConfig) -> list[str]:
    downloads = config.downloads_dir
    candidates: list[Path] = []
    for library in config.libraries.values():
        candidate = library.parent
        if candidate == downloads or _is_under(downloads, candidate):
            candidate = library
        if candidate == downloads or _is_under(candidate, downloads):
            raise DeploymentError(
                "PROTECTED_ROOT_CONFLICT",
                "protected media root conflicts with the download root",
                severity="security_block",
                next_action="separate_download_and_library_paths",
                details={"path": str(candidate)},
            )
        candidates.append(candidate)

    minimal: list[Path] = []
    for candidate in sorted(
        set(candidates),
        key=lambda item: (len(item.parts), str(item)),
    ):
        if any(
            candidate == parent or _is_under(candidate, parent)
            for parent in minimal
        ):
            continue
        minimal.append(candidate)
    return [str(path) for path in sorted(minimal, key=str)]


def build_routing(config: DeploymentConfig) -> dict[str, object]:
    downloads = config.downloads_dir
    staging = downloads / ".incoming"
    routes = {
        "movie": ("/OpenClaw/Movies", config.libraries.movie),
        "tv": ("/OpenClaw/TV", config.libraries.drama),
        "drama": ("/OpenClaw/TV", config.libraries.drama),
        "anime": ("/OpenClaw/Anime", config.libraries.anime),
        "documentary": ("/OpenClaw/Documentary", config.libraries.documentary),
        "show": ("/OpenClaw/Shows", config.libraries.show),
        "other": ("/OpenClaw/Others", config.libraries.other),
    }
    result: dict[str, object] = {}
    for media_type, (cloud_prefix, final_root) in routes.items():
        result[media_type] = {
            "cloud_prefix": cloud_prefix,
            "aria2_prefix": "downloads/.incoming",
            "staging_root": str(staging),
            "final_root": str(final_root),
        }
    result["downloads"] = {
        "root": str(downloads),
        "host_root": str(downloads),
        "agent_root": str(downloads),
        "aria2_root": "/nas/downloads",
        "staging_root": str(staging),
        "ready_root": str(downloads / ".ready"),
        "quarantine_root": str(downloads / ".quarantine"),
    }
    result["paths"] = {
        "protected_roots": _protected_roots(config),
        "organizing_root": str(config.organizing_dir),
    }
    return result


def validate_compose(path: Path, runner: CommandRunner) -> None:
    target = Path(path)
    result = runner.run(
        [
            "env",
            "QAS_WEBUI_PASSWORD=__OPENCLAW_VALIDATION_ONLY__",
            "ARIA2_RPC_SECRET=__OPENCLAW_VALIDATION_ONLY__",
            "docker",
            "compose",
            "-f",
            str(target),
            "config",
            "--quiet",
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise DeploymentError(
            "COMPOSE_VALIDATION_FAILED",
            "rendered Docker Compose configuration is invalid",
            next_action="fix_rendered_compose",
            details={"path": str(target), "stderr": result.stderr[:500]},
        )


def validate_json(path: Path, runner: CommandRunner) -> None:
    target = Path(path)
    result = runner.run(
        [sys.executable, "-m", "json.tool", str(target)],
        timeout=30,
    )
    if result.returncode != 0:
        raise DeploymentError(
            "JSON_VALIDATION_FAILED",
            "rendered JSON configuration is invalid",
            next_action="fix_rendered_json",
            details={"path": str(target), "stderr": result.stderr[:500]},
        )


def render_and_validate(
    config: DeploymentConfig,
    versions: VersionLock,
    output_dir: Path,
    runner: CommandRunner,
) -> tuple[RenderedFile, RenderedFile]:
    destination = Path(output_dir)
    compose = render_template(
        "compose.dependencies.yml.j2",
        build_compose_context(config, versions),
        destination / "compose.dependencies.yml",
        mode=0o644,
    )
    routing_data = build_routing(config)
    routing = render_template(
        "routing.json.j2",
        {"routing_json": json.dumps(routing_data, ensure_ascii=False, indent=2)},
        destination / "routing.json",
        mode=0o600,
    )
    validate_compose(compose.path, runner)
    validate_json(routing.path, runner)
    return compose, routing
