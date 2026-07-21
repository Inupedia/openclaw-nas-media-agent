"""QAS v0.8.7 configuration and API adapter.

The contract in this module is intentionally limited to fields proven by the
locked image fixture. Unknown shapes stop automation instead of being guessed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from ..errors import DeploymentError
from ..models import Change, ChangePhase, ComponentResult, ComponentStatus, Severity


@dataclass(frozen=True)
class QasDesiredState:
    username: str
    password: str
    api_token: str
    cookies: tuple[str, ...]
    aria2_host_port: str
    aria2_secret: str
    aria2_dir: str = "/nas/downloads"


def derive_api_token(username: str, password: str) -> str:
    raw = f"token{username}{password}+-*/".encode("utf-8")
    return hashlib.md5(raw).hexdigest()[8:24]  # nosec: mirrors locked QAS contract


def normalize_cookies(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        if text.startswith("["):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                value = [text]
        else:
            value = [line for line in text.splitlines() if line.strip()]
    if not isinstance(value, (list, tuple)):
        raise DeploymentError(
            "QAS_COOKIE_INVALID",
            "QAS Cookie secret must be a JSON array or newline-separated text",
            status="manual_action_required",
            next_action="replace_qas_cookie_secret",
        )
    return tuple(str(item).strip() for item in value if str(item).strip())


def _manual_schema(message: str) -> DeploymentError:
    return DeploymentError(
        "QAS_SCHEMA_UNKNOWN",
        message,
        status="manual_action_required",
        next_action="complete_qas_configuration",
        severity="blocking",
    )


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _manual_schema(
            f"unable to read supported QAS configuration: {type(error).__name__}"
        ) from None
    if not isinstance(value, dict):
        raise _manual_schema("QAS configuration root must be an object")
    return value


def _validated_sections(
    data: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    webui = data.get("webui")
    plugins = data.get("plugins")
    cookie = data.get("cookie")
    if not isinstance(webui, dict) or not isinstance(plugins, dict) or not isinstance(cookie, list):
        raise _manual_schema("QAS v0.8.7 webui, plugins or cookie shape is unknown")
    aria2 = plugins.get("aria2")
    if not isinstance(aria2, dict):
        raise _manual_schema("QAS v0.8.7 aria2 plugin configuration is missing")
    for key in ("username", "password"):
        if key not in webui:
            raise _manual_schema(f"QAS webui.{key} field is missing")
    for key in ("host_port", "secret", "dir"):
        if key not in aria2:
            raise _manual_schema(f"QAS plugins.aria2.{key} field is missing")
    if not isinstance(data.get("push_config", {}), dict) or not isinstance(
        data.get("tasklist", []), list
    ):
        raise _manual_schema("QAS push_config or tasklist shape is unknown")
    return webui, aria2


class QasV1Adapter:
    def __init__(
        self,
        config_path: Path,
        base_url: str,
        *,
        urlopen: Callable = urllib.request.urlopen,
    ) -> None:
        self.config_path = Path(config_path)
        self.base_url = str(base_url).rstrip("/")
        self._urlopen = urlopen

    def discover(self, desired: QasDesiredState | None = None) -> ComponentResult:
        data = _read_json(self.config_path)
        webui, aria2 = _validated_sections(data)
        details: dict[str, object] = {
            "schema": "qas-v0.8.7",
            "configPath": str(self.config_path),
            "webuiUsername": "configured"
            if str(webui.get("username", "")).strip()
            else "missing",
            "webuiPassword": "configured" if str(webui.get("password", "")) else "missing",
            "cookie": "configured" if normalize_cookies(data.get("cookie", [])) else "missing",
            "aria2HostPort": "configured"
            if str(aria2.get("host_port", "")).strip()
            else "missing",
            "aria2Secret": "configured" if str(aria2.get("secret", "")) else "missing",
            "aria2Dir": "configured" if str(aria2.get("dir", "")).strip() else "missing",
        }
        if desired is not None:
            derived = derive_api_token(desired.username, desired.password)
            details["apiToken"] = "configured" if desired.api_token == derived else "invalid"
        missing = any(value in {"missing", "invalid"} for value in details.values())
        return ComponentResult(
            component="qas",
            status=ComponentStatus.MANUAL_ACTION_REQUIRED if missing else ComponentStatus.READY,
            required=True,
            enabled=True,
            next_action="complete_qas_configuration" if missing else "none",
            details=details,
            severity=Severity.BLOCKING if missing else None,
        )

    def plan(self, desired: QasDesiredState, backup_path: Path) -> tuple[Change, ...]:
        current = _read_json(self.config_path)
        _validated_sections(current)
        if desired.api_token != derive_api_token(desired.username, desired.password):
            raise DeploymentError(
                "QAS_API_TOKEN_MISMATCH",
                "QAS API token does not match the locked image derivation",
                status="manual_action_required",
                next_action="regenerate_qas_api_token",
            )
        return (
            Change(
                id="qas-backup-config",
                phase=ChangePhase.BACKUP,
                component="qas",
                action="copy_file",
                target=str(backup_path),
                before=None,
                after={"source": str(self.config_path)},
                side_effect=True,
                rollback={},
            ),
            Change(
                id="qas-write-config",
                phase=ChangePhase.SERVICE_CONFIG,
                component="qas",
                action="write_supported_fields",
                target=str(self.config_path),
                before={"schema": "qas-v0.8.7"},
                after={
                    "webuiUsername": desired.username,
                    "webuiPassword": "***",
                    "apiToken": "***",
                    "cookie": ["***"] if desired.cookies else [],
                    "aria2HostPort": desired.aria2_host_port,
                    "aria2Secret": "***",
                    "aria2Dir": desired.aria2_dir,
                },
                side_effect=True,
                rollback={"action": "restore_file", "source": str(backup_path)},
            ),
        )

    def apply_config(self, desired: QasDesiredState, backup_path: Path) -> ComponentResult:
        data = _read_json(self.config_path)
        webui, aria2 = _validated_sections(data)
        expected_token = derive_api_token(desired.username, desired.password)
        if desired.api_token != expected_token:
            raise DeploymentError(
                "QAS_API_TOKEN_MISMATCH",
                "QAS API token does not match the locked image derivation",
                status="manual_action_required",
                next_action="regenerate_qas_api_token",
            )
        backup = Path(backup_path)
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(self.config_path, backup)
        os.chmod(backup, 0o600)
        webui["username"] = desired.username
        webui["password"] = desired.password
        data["cookie"] = list(desired.cookies)
        aria2["host_port"] = desired.aria2_host_port
        aria2["secret"] = desired.aria2_secret
        aria2["dir"] = desired.aria2_dir
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.config_path.parent,
            prefix=".qas-config-",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
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
        return ComponentResult(
            component="qas",
            status=ComponentStatus.READY,
            required=True,
            enabled=True,
            details={"configured": True, "backupPath": str(backup)},
        )

    def _get_data(self, token: str) -> Mapping[str, object]:
        url = self.base_url + "/data?" + urllib.parse.urlencode({"token": token})
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with self._urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            raise DeploymentError(
                "QAS_API_UNREACHABLE",
                "QAS API read-back failed",
                status="manual_action_required",
                next_action="open_qas_webui",
                details={"reason": type(error).__name__},
            ) from None
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or not isinstance(payload.get("data"), dict)
        ):
            raise DeploymentError(
                "QAS_API_AUTH_FAILED",
                "QAS API did not accept the derived token",
                status="manual_action_required",
                next_action="complete_qas_configuration",
            )
        return payload["data"]

    def verify(self, desired: QasDesiredState) -> ComponentResult:
        actual = self._get_data(derive_api_token(desired.username, desired.password))
        plugins = actual.get("plugins")
        aria2 = plugins.get("aria2") if isinstance(plugins, dict) else None
        cookies = normalize_cookies(actual.get("cookie", []))
        checks = {
            "cookie": "configured" if cookies == desired.cookies else "invalid",
            "aria2HostPort": "configured"
            if isinstance(aria2, dict)
            and str(aria2.get("host_port")) == desired.aria2_host_port
            else "invalid",
            "aria2Secret": "configured"
            if isinstance(aria2, dict)
            and str(aria2.get("secret")) == desired.aria2_secret
            else "invalid",
            "aria2Dir": "configured"
            if isinstance(aria2, dict) and str(aria2.get("dir")) == desired.aria2_dir
            else "invalid",
            "apiToken": "configured",
        }
        valid = all(value == "configured" for value in checks.values())
        return ComponentResult(
            component="qas",
            status=ComponentStatus.READY
            if valid
            else ComponentStatus.MANUAL_ACTION_REQUIRED,
            required=True,
            enabled=True,
            next_action="none" if valid else "complete_qas_configuration",
            details=checks,
            severity=None if valid else Severity.BLOCKING,
        )
