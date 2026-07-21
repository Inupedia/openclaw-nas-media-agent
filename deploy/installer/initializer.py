"""Configuration-only initializer for human and Agent deployment paths."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TextIO

import yaml

from .config import DeploymentConfig, load_config
from .errors import DeploymentError


def _write_private_text(path: Path, text: str, mode: int = 0o600) -> None:
    target = Path(path)
    if target.is_symlink():
        raise DeploymentError(
            "INIT_TARGET_SYMLINK",
            "initializer refuses to replace a symlink",
            severity="security_block",
            next_action="remove_symlink",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, mode)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _prompt(
    input_stream: TextIO,
    prompt_stream: TextIO,
    label: str,
    default: str,
) -> str:
    prompt_stream.write(f"{label} [{default}]: ")
    prompt_stream.flush()
    value = input_stream.readline()
    if value == "":
        return default
    return value.strip() or default


def _interactive_raw(input_stream: TextIO, prompt_stream: TextIO) -> dict[str, object]:
    platform = _prompt(input_stream, prompt_stream, "Platform (ugos/linux/auto)", "ugos")
    project_dir = _prompt(
        input_stream,
        prompt_stream,
        "Deployment directory",
        "/volume1/docker/openclaw-media",
    )
    downloads = _prompt(
        input_stream,
        prompt_stream,
        "Downloads directory",
        "/volume2/downloads",
    )
    media_root = _prompt(
        input_stream,
        prompt_stream,
        "Default media root",
        "/volume2/media",
    )
    movie = _prompt(
        input_stream,
        prompt_stream,
        "Movie library",
        f"{media_root}/Movie",
    )
    drama = _prompt(
        input_stream,
        prompt_stream,
        "Drama library",
        f"{media_root}/Drama",
    )
    anime = _prompt(
        input_stream,
        prompt_stream,
        "Anime library",
        f"{media_root}/Anime",
    )
    documentary = _prompt(
        input_stream,
        prompt_stream,
        "Documentary library",
        f"{media_root}/Documentary",
    )
    show = _prompt(
        input_stream,
        prompt_stream,
        "Show library",
        f"{media_root}/Shows",
    )
    other = _prompt(
        input_stream,
        prompt_stream,
        "Other library",
        f"{media_root}/Others",
    )
    proxy_mode = _prompt(
        input_stream,
        prompt_stream,
        "PanSou proxy mode (none/existing/managed)",
        "existing",
    )
    proxy: dict[str, object] = {"mode": proxy_mode}
    if proxy_mode == "existing":
        proxy["url_secret"] = "pansou_proxy_url"
    elif proxy_mode == "managed":
        proxy["singbox_config_secret"] = "singbox_config.json"
    return {
        "schema_version": 1,
        "deployment": {
            "mode": "existing-openclaw",
            "platform": platform,
            "project_dir": project_dir,
            "timezone": "Asia/Shanghai",
            "allow_reuse_existing_services": True,
        },
        "nas": {
            "downloads_dir": downloads,
            "organizing_dir": f"{media_root}/.openclaw-organizing",
            "libraries": {
                "movie": movie,
                "drama": drama,
                "anime": anime,
                "documentary": documentary,
                "show": show,
                "other": other,
            },
        },
        "openclaw": {
            "container_name": "auto",
            "workspace_host_dir": "auto",
            "config_host_path": "auto",
        },
        "qas": {
            "mode": "auto",
            "port": 5005,
            "username": "admin",
            "password_secret": "qas_webui_password",
            "api_token_secret": "qas_token",
            "quark_cookie_secret": "quark_cookie",
        },
        "aria2": {
            "mode": "auto",
            "rpc_port": 6800,
            "rpc_secret": "aria2_rpc_secret",
            "uid": "auto",
            "gid": "auto",
        },
        "pansou": {
            "enabled": True,
            "mode": "auto",
            "port": 8888,
            "channels": ["tgsearchers3"],
            "plugins": [],
            "max_candidates": 50,
            "proxy": proxy,
        },
        "jiaofu": {
            "enabled": False,
            "storage_state_secret": "jiaofu_storage_state.json",
            "max_candidates": 20,
        },
        "verification": {
            "safe": True,
            "safe_query": "OpenClaw deploy verification sample",
            "allow_real_download": False,
            "full_test_share_url_secret": "full_test_share_url",
            "max_test_bytes": 104857600,
        },
    }


def _ensure_secret_files(config: DeploymentConfig, project_root: Path) -> tuple[str, ...]:
    secret_dir = Path(project_root) / "deploy" / "secrets"
    if secret_dir.is_symlink():
        raise DeploymentError(
            "INIT_SECRET_DIR_SYMLINK",
            "secret directory must not be a symlink",
            severity="security_block",
            next_action="remove_secret_symlink",
        )
    secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(secret_dir, 0o700)
    created: list[str] = []
    for name in config.secret_names():
        target = secret_dir / name
        if target.is_symlink():
            raise DeploymentError(
                "INIT_SECRET_SYMLINK",
                "secret files must not be symlinks",
                severity="security_block",
                next_action="remove_secret_symlink",
                details={"name": name},
            )
        if not target.exists():
            target.touch(mode=0o600)
            created.append(name)
        os.chmod(target, 0o600)
    return tuple(created)


def run_init(
    input_stream: TextIO,
    prompt_stream: TextIO,
    project_root: Path,
    *,
    non_interactive: bool = False,
    config_source: Path | None = None,
) -> tuple[DeploymentConfig, tuple[str, ...]]:
    root = Path(project_root).resolve()
    deploy_dir = root / "deploy"
    if not deploy_dir.is_dir():
        raise DeploymentError(
            "DEPLOY_DIRECTORY_MISSING",
            "initializer must run from the repository root",
            next_action="run_from_repository_root",
        )
    target = deploy_dir / "config.yaml"
    if non_interactive:
        if config_source is None:
            raise DeploymentError(
                "INIT_CONFIG_SOURCE_REQUIRED",
                "non-interactive init requires --config-source",
                status="manual_action_required",
                next_action="provide_config_source",
            )
        source = Path(config_source)
        if source.is_symlink():
            raise DeploymentError(
                "INIT_SOURCE_SYMLINK",
                "configuration source must not be a symlink",
                severity="security_block",
                next_action="provide_regular_config_source",
            )
        try:
            source.resolve().relative_to((deploy_dir / "secrets").resolve())
            raise DeploymentError(
                "INIT_SOURCE_IN_SECRETS",
                "configuration source must not be stored under deploy/secrets",
                severity="security_block",
                next_action="move_config_source",
            )
        except ValueError:
            pass
        config = load_config(source)
        text = source.read_text(encoding="utf-8")
    else:
        raw = _interactive_raw(input_stream, prompt_stream)
        descriptor, name = tempfile.mkstemp(prefix="openclaw-media-init-", suffix=".yaml")
        os.close(descriptor)
        temporary = Path(name)
        try:
            temporary.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            config = load_config(temporary)
            text = temporary.read_text(encoding="utf-8")
        finally:
            temporary.unlink(missing_ok=True)
    _write_private_text(target, text, 0o600)
    created = _ensure_secret_files(config, root)
    return config, created
