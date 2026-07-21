"""Existing and managed proxy validation for PanSou."""
from __future__ import annotations

import json
from collections.abc import Mapping

from ..command import CommandRunner
from ..config import ProxySettings
from ..errors import DeploymentError
from ..models import Change, ChangePhase, ComponentResult, ComponentStatus, Severity
from ..secrets import SecretStore
from ..versions import VersionLock


def _public_controller(value: object) -> bool:
    text = str(value or "").strip().casefold()
    if not text:
        return False
    host = text.rsplit(":", 1)[0].strip("[]")
    return host not in {"127.0.0.1", "localhost", "::1"}


class ProxyAdapter:
    def __init__(
        self,
        settings: ProxySettings,
        secrets: SecretStore,
        versions: VersionLock,
    ) -> None:
        self.settings = settings
        self.secrets = secrets
        self.versions = versions

    def managed_config(self) -> Mapping[str, object]:
        if self.settings.mode != "managed" or not self.settings.singbox_config_secret:
            raise DeploymentError(
                "PROXY_CONFIG_REQUIRED",
                "managed proxy requires a sing-box JSON secret",
                status="manual_action_required",
                next_action="provide_singbox_config",
            )
        raw = self.secrets.read(self.settings.singbox_config_secret)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            raise DeploymentError(
                "PROXY_CONFIG_UNSUPPORTED",
                "managed proxy secret must be a complete sing-box JSON configuration",
                status="manual_action_required",
                next_action="provide_singbox_config",
            ) from None
        if not isinstance(value, dict):
            raise DeploymentError(
                "PROXY_CONFIG_UNSUPPORTED",
                "sing-box configuration must be a JSON object",
                status="manual_action_required",
                next_action="provide_singbox_config",
            )
        inbounds = value.get("inbounds")
        socks = [
            item
            for item in inbounds
            if isinstance(item, dict)
            and str(item.get("type", "")).casefold() in {"socks", "mixed"}
            and int(item.get("listen_port", 0) or 0) == 1080
        ] if isinstance(inbounds, list) else []
        if not socks:
            raise DeploymentError(
                "PROXY_SOCKS_INBOUND_MISSING",
                "sing-box config must expose an internal SOCKS or mixed inbound on port 1080",
                status="manual_action_required",
                next_action="provide_singbox_config",
            )
        experimental = value.get("experimental")
        clash = experimental.get("clash_api") if isinstance(experimental, dict) else None
        if isinstance(clash, dict) and _public_controller(clash.get("external_controller")):
            raise DeploymentError(
                "PROXY_PUBLIC_MANAGEMENT_FORBIDDEN",
                "sing-box management API must not bind a public interface",
                severity="security_block",
                next_action="restrict_singbox_management",
            )
        return value

    def plan(self) -> tuple[Change, ...]:
        if self.settings.mode != "managed":
            return ()
        self.managed_config()
        return (
            Change(
                id="proxy-compose-up",
                phase=ChangePhase.COMPOSE,
                component="proxy",
                action="compose_up",
                target="openclaw-media-proxy",
                after={
                    "image": self.versions.image("sing_box"),
                    "configSecret": self.settings.singbox_config_secret,
                    "hostPorts": [],
                    "network": "openclaw-media",
                },
                side_effect=True,
                rollback={"action": "compose_down_service", "service": "proxy"},
            ),
        )

    def verify(self, runner: CommandRunner) -> ComponentResult:
        if self.settings.mode != "managed":
            return ComponentResult(
                component="proxy",
                status=ComponentStatus.SKIPPED,
                required=False,
                enabled=False,
                details={"mode": self.settings.mode},
            )
        check = runner.run(
            [
                "docker",
                "exec",
                "openclaw-media-proxy",
                "sing-box",
                "check",
                "-c",
                "/etc/sing-box/config.json",
            ],
            timeout=30,
        )
        ready = check.returncode == 0
        return ComponentResult(
            component="proxy",
            status=ComponentStatus.READY if ready else ComponentStatus.DEGRADED,
            required=False,
            enabled=True,
            next_action="none" if ready else "fix_singbox_config",
            severity=None if ready else Severity.DEGRADED,
            details={"configValid": ready, "returncode": check.returncode},
        )
