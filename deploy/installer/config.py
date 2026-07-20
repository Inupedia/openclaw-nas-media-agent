"""Strict YAML configuration loading for the existing-OpenClaw deployer."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml

from .errors import DeploymentError


@dataclass(frozen=True)
class LibraryPaths:
    movie: Path
    drama: Path
    anime: Path
    documentary: Path
    show: Path
    other: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "movie": str(self.movie),
            "drama": str(self.drama),
            "anime": str(self.anime),
            "documentary": str(self.documentary),
            "show": str(self.show),
            "other": str(self.other),
        }

    def values(self) -> tuple[Path, ...]:
        return (
            self.movie,
            self.drama,
            self.anime,
            self.documentary,
            self.show,
            self.other,
        )


@dataclass(frozen=True)
class OpenClawSettings:
    container_name: str
    workspace_host_dir: Path | None
    config_host_path: Path | None

    def to_dict(self) -> dict[str, object]:
        return {
            "container_name": self.container_name,
            "workspace_host_dir": (
                str(self.workspace_host_dir) if self.workspace_host_dir is not None else "auto"
            ),
            "config_host_path": (
                str(self.config_host_path) if self.config_host_path is not None else "auto"
            ),
        }


@dataclass(frozen=True)
class QasSettings:
    mode: str
    port: int
    username: str
    password_secret: str
    api_token_secret: str
    quark_cookie_secret: str

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "port": self.port,
            "username": self.username,
            "password_secret": self.password_secret,
            "api_token_secret": self.api_token_secret,
            "quark_cookie_secret": self.quark_cookie_secret,
        }


@dataclass(frozen=True)
class Aria2Settings:
    mode: str
    rpc_port: int
    rpc_secret: str
    uid: int | None
    gid: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "rpc_port": self.rpc_port,
            "rpc_secret": self.rpc_secret,
            "uid": self.uid if self.uid is not None else "auto",
            "gid": self.gid if self.gid is not None else "auto",
        }


@dataclass(frozen=True)
class ProxySettings:
    mode: str
    url_secret: str | None = None
    singbox_config_secret: str | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"mode": self.mode}
        if self.url_secret is not None:
            result["url_secret"] = self.url_secret
        if self.singbox_config_secret is not None:
            result["singbox_config_secret"] = self.singbox_config_secret
        return result


@dataclass(frozen=True)
class PanSouSettings:
    enabled: bool
    mode: str
    port: int
    channels: tuple[str, ...]
    plugins: tuple[str, ...]
    max_candidates: int
    proxy: ProxySettings

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "port": self.port,
            "channels": list(self.channels),
            "plugins": list(self.plugins),
            "max_candidates": self.max_candidates,
            "proxy": self.proxy.to_dict(),
        }


@dataclass(frozen=True)
class JiaofuSettings:
    enabled: bool
    storage_state_secret: str
    max_candidates: int

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "storage_state_secret": self.storage_state_secret,
            "max_candidates": self.max_candidates,
        }


@dataclass(frozen=True)
class VerificationSettings:
    safe: bool
    safe_query: str
    allow_real_download: bool
    full_test_share_url_secret: str
    max_test_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "safe": self.safe,
            "safe_query": self.safe_query,
            "allow_real_download": self.allow_real_download,
            "full_test_share_url_secret": self.full_test_share_url_secret,
            "max_test_bytes": self.max_test_bytes,
        }


@dataclass(frozen=True)
class DeploymentConfig:
    schema_version: int
    mode: str
    platform: str
    project_dir: Path
    timezone: str
    allow_reuse_existing_services: bool
    downloads_dir: Path
    organizing_dir: Path
    libraries: LibraryPaths
    openclaw: OpenClawSettings
    qas: QasSettings
    aria2: Aria2Settings
    pansou: PanSouSettings
    jiaofu: JiaofuSettings
    verification: VerificationSettings

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "deployment": {
                "mode": self.mode,
                "platform": self.platform,
                "project_dir": str(self.project_dir),
                "timezone": self.timezone,
                "allow_reuse_existing_services": self.allow_reuse_existing_services,
            },
            "nas": {
                "downloads_dir": str(self.downloads_dir),
                "organizing_dir": str(self.organizing_dir),
                "libraries": self.libraries.to_dict(),
            },
            "openclaw": self.openclaw.to_dict(),
            "qas": self.qas.to_dict(),
            "aria2": self.aria2.to_dict(),
            "pansou": self.pansou.to_dict(),
            "jiaofu": self.jiaofu.to_dict(),
            "verification": self.verification.to_dict(),
        }

    def secret_names(self) -> tuple[str, ...]:
        names = {
            self.qas.password_secret,
            self.qas.api_token_secret,
            self.qas.quark_cookie_secret,
            self.aria2.rpc_secret,
            self.verification.full_test_share_url_secret,
        }
        if self.pansou.proxy.url_secret:
            names.add(self.pansou.proxy.url_secret)
        if self.pansou.proxy.singbox_config_secret:
            names.add(self.pansou.proxy.singbox_config_secret)
        if self.jiaofu.enabled:
            names.add(self.jiaofu.storage_state_secret)
        return tuple(sorted(names))


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "schemas" / "config.schema.json"


def _load_schema() -> dict[str, object]:
    try:
        value = json.loads(_schema_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentError(
            "CONFIG_SCHEMA_UNAVAILABLE",
            "deployment configuration schema is unavailable",
            details={"reason": type(error).__name__},
        ) from None
    if not isinstance(value, dict):
        raise DeploymentError("CONFIG_SCHEMA_UNAVAILABLE", "configuration schema is invalid")
    return value


def _schema_error(raw: object) -> DeploymentError | None:
    validator = jsonschema.Draft202012Validator(_load_schema())
    errors = sorted(
        validator.iter_errors(raw),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    if not errors:
        return None
    error = errors[0]
    path_parts = [str(part) for part in error.absolute_path]
    path = ".".join(path_parts) or "$"
    security = path_parts[:2] == ["nas", "libraries"]
    return DeploymentError(
        "CONFIG_SCHEMA_INVALID",
        f"configuration at {path} is invalid: {error.message}",
        severity="security_block" if security else "blocking",
        next_action="fix_config",
        details={"path": path, "validator": str(error.validator)},
    )


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DeploymentError("CONFIG_SCHEMA_INVALID", f"{name} must be an object")
    return value


def _absolute_path(value: object, name: str) -> Path:
    text = str(value)
    raw = Path(text)
    if not text or "\x00" in text or not raw.is_absolute() or ".." in raw.parts:
        raise DeploymentError(
            "CONFIG_PATH_INVALID",
            f"{name} must be an absolute path without parent traversal",
            severity="security_block",
            next_action="fix_config_path",
            details={"field": name},
        )
    return Path(os.path.normpath(text))


def _auto_path(value: object, name: str) -> Path | None:
    if value == "auto":
        return None
    return _absolute_path(value, name)


def _auto_id(value: object) -> int | None:
    return None if value == "auto" else int(value)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def load_config(path: Path) -> DeploymentConfig:
    target = Path(path)
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except OSError as error:
        raise DeploymentError(
            "CONFIG_READ_FAILED",
            f"unable to read deployment config: {target}",
            next_action="create_config",
            details={"reason": type(error).__name__},
        ) from None
    except yaml.YAMLError as error:
        raise DeploymentError(
            "CONFIG_YAML_INVALID",
            "deployment config is not valid YAML",
            next_action="fix_config",
            details={"reason": type(error).__name__},
        ) from None

    schema_error = _schema_error(raw)
    if schema_error is not None:
        raise schema_error
    root = _object(raw, "config")
    deployment = _object(root["deployment"], "deployment")
    nas = _object(root["nas"], "nas")
    libraries_raw = _object(nas["libraries"], "nas.libraries")
    openclaw_raw = _object(root["openclaw"], "openclaw")
    qas_raw = _object(root["qas"], "qas")
    aria_raw = _object(root["aria2"], "aria2")
    pansou_raw = _object(root["pansou"], "pansou")
    proxy_raw = _object(pansou_raw["proxy"], "pansou.proxy")
    jiaofu_raw = _object(root["jiaofu"], "jiaofu")
    verification_raw = _object(root["verification"], "verification")

    downloads = _absolute_path(nas["downloads_dir"], "nas.downloads_dir")
    libraries = LibraryPaths(
        movie=_absolute_path(libraries_raw["movie"], "nas.libraries.movie"),
        drama=_absolute_path(libraries_raw["drama"], "nas.libraries.drama"),
        anime=_absolute_path(libraries_raw["anime"], "nas.libraries.anime"),
        documentary=_absolute_path(
            libraries_raw["documentary"], "nas.libraries.documentary"
        ),
        show=_absolute_path(libraries_raw["show"], "nas.libraries.show"),
        other=_absolute_path(libraries_raw["other"], "nas.libraries.other"),
    )
    for library in libraries.values():
        if _inside(library, downloads):
            raise DeploymentError(
                "LIBRARY_INSIDE_DOWNLOADS",
                "formal media libraries must not be inside the download root",
                severity="security_block",
                next_action="separate_download_and_library_paths",
                details={"library": str(library), "downloads": str(downloads)},
            )

    config = DeploymentConfig(
        schema_version=int(root["schema_version"]),
        mode=str(deployment["mode"]),
        platform=str(deployment["platform"]),
        project_dir=_absolute_path(deployment["project_dir"], "deployment.project_dir"),
        timezone=str(deployment["timezone"]),
        allow_reuse_existing_services=bool(deployment["allow_reuse_existing_services"]),
        downloads_dir=downloads,
        organizing_dir=_absolute_path(nas["organizing_dir"], "nas.organizing_dir"),
        libraries=libraries,
        openclaw=OpenClawSettings(
            container_name=str(openclaw_raw["container_name"]),
            workspace_host_dir=_auto_path(
                openclaw_raw["workspace_host_dir"], "openclaw.workspace_host_dir"
            ),
            config_host_path=_auto_path(
                openclaw_raw["config_host_path"], "openclaw.config_host_path"
            ),
        ),
        qas=QasSettings(
            mode=str(qas_raw["mode"]),
            port=int(qas_raw["port"]),
            username=str(qas_raw["username"]),
            password_secret=str(qas_raw["password_secret"]),
            api_token_secret=str(qas_raw["api_token_secret"]),
            quark_cookie_secret=str(qas_raw["quark_cookie_secret"]),
        ),
        aria2=Aria2Settings(
            mode=str(aria_raw["mode"]),
            rpc_port=int(aria_raw["rpc_port"]),
            rpc_secret=str(aria_raw["rpc_secret"]),
            uid=_auto_id(aria_raw["uid"]),
            gid=_auto_id(aria_raw["gid"]),
        ),
        pansou=PanSouSettings(
            enabled=bool(pansou_raw["enabled"]),
            mode=str(pansou_raw["mode"]),
            port=int(pansou_raw["port"]),
            channels=tuple(str(item) for item in pansou_raw["channels"]),
            plugins=tuple(str(item) for item in pansou_raw["plugins"]),
            max_candidates=int(pansou_raw["max_candidates"]),
            proxy=ProxySettings(
                mode=str(proxy_raw["mode"]),
                url_secret=(
                    str(proxy_raw["url_secret"]) if "url_secret" in proxy_raw else None
                ),
                singbox_config_secret=(
                    str(proxy_raw["singbox_config_secret"])
                    if "singbox_config_secret" in proxy_raw
                    else None
                ),
            ),
        ),
        jiaofu=JiaofuSettings(
            enabled=bool(jiaofu_raw["enabled"]),
            storage_state_secret=str(jiaofu_raw["storage_state_secret"]),
            max_candidates=int(jiaofu_raw["max_candidates"]),
        ),
        verification=VerificationSettings(
            safe=bool(verification_raw["safe"]),
            safe_query=str(verification_raw["safe_query"]),
            allow_real_download=bool(verification_raw["allow_real_download"]),
            full_test_share_url_secret=str(
                verification_raw["full_test_share_url_secret"]
            ),
            max_test_bytes=int(verification_raw["max_test_bytes"]),
        ),
    )
    return config


def config_digest(config: DeploymentConfig) -> str:
    canonical = json.dumps(
        config.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()
