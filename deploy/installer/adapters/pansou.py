"""PanSou planning and differentiated service/search verification."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..config import PanSouSettings
from ..errors import DeploymentError
from ..models import Change, ChangePhase, ComponentResult, ComponentStatus, Severity
from ..secrets import SecretStore


@dataclass(frozen=True)
class PanSouDiscovery:
    container_name: str | None
    running: bool
    health: str

    def to_dict(self) -> dict[str, object]:
        return {
            "containerName": self.container_name,
            "running": self.running,
            "health": self.health,
        }


def _container_name(item: Mapping[str, object]) -> str:
    return str(item.get("Names") or item.get("Name") or item.get("name") or "").lstrip("/")


class PanSouAdapter:
    def __init__(
        self,
        settings: PanSouSettings,
        secrets: SecretStore,
        *,
        base_url: str | None = None,
        opener: Callable = urllib.request.urlopen,
    ) -> None:
        self.settings = settings
        self.secrets = secrets
        self.base_url = (base_url or f"http://127.0.0.1:{settings.port}").rstrip("/")
        self._opener = opener

    def discover(self, containers: Sequence[Mapping[str, object]]) -> PanSouDiscovery:
        for item in containers:
            name = _container_name(item)
            image = str(item.get("Image") or item.get("image") or "").casefold()
            if name == "openclaw-media-pansou" or "pansou" in image:
                state = str(item.get("State") or item.get("state") or "").casefold()
                health = str(item.get("Health") or item.get("health") or "unknown").casefold()
                return PanSouDiscovery(name, state == "running", health)
        return PanSouDiscovery(None, False, "missing")

    def proxy_environment(self) -> dict[str, str]:
        mode = self.settings.proxy.mode
        if mode == "none":
            return {}
        if mode == "managed":
            return {"PROXY": "socks5://openclaw-media-proxy:1080"}
        if mode != "existing" or not self.settings.proxy.url_secret:
            raise DeploymentError(
                "PANSOU_PROXY_CONFIG_INVALID",
                "PanSou existing proxy requires a secret reference",
                status="manual_action_required",
                next_action="configure_pansou_proxy",
            )
        value = self.secrets.read(self.settings.proxy.url_secret).strip()
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            parsed = urllib.parse.SplitResult("", "", "", "", "")
        if parsed.scheme in {"socks5", "socks5h"} and parsed.hostname and parsed.port:
            return {"PROXY": value}
        if parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port:
            return {"HTTP_PROXY": value, "HTTPS_PROXY": value}
        raise DeploymentError(
            "PANSOU_PROXY_URL_INVALID",
            "PanSou proxy secret must contain an HTTP(S) or SOCKS5 URL",
            status="manual_action_required",
            next_action="fix_pansou_proxy_secret",
        )

    def plan(self) -> tuple[Change, ...]:
        if not self.settings.enabled or self.settings.mode == "disabled":
            return ()
        after: dict[str, object] = {
            "mode": self.settings.mode,
            "channels": list(self.settings.channels),
            "plugins": list(self.settings.plugins),
            "maxCandidates": self.settings.max_candidates,
            "proxyMode": self.settings.proxy.mode,
        }
        if self.settings.proxy.url_secret:
            after["proxySecret"] = self.settings.proxy.url_secret
        if self.settings.proxy.singbox_config_secret:
            after["singboxConfigSecret"] = self.settings.proxy.singbox_config_secret
        return (
            Change(
                id="pansou-configure",
                phase=ChangePhase.SERVICE_CONFIG,
                component="pansou",
                action="http_config_update",
                target="pansou://configuration",
                after=after,
                side_effect=True,
                rollback={"action": "restore_component_config", "component": "pansou"},
            ),
        )

    @staticmethod
    def _source_count(payload: object) -> tuple[int, int]:
        """Return total candidates and Telegram-backed candidates without trusting one schema."""
        total = 0
        telegram = 0

        def visit(value: object, inherited_source: str = "") -> None:
            nonlocal total, telegram
            if isinstance(value, Mapping):
                source = str(
                    value.get("source")
                    or value.get("src")
                    or value.get("channel")
                    or inherited_source
                ).casefold()
                if any(key in value for key in ("url", "link", "shareurl")):
                    total += 1
                    if "telegram" in source or source.startswith("tg"):
                        telegram += 1
                for child in value.values():
                    visit(child, source)
            elif isinstance(value, list):
                for child in value:
                    visit(child, inherited_source)

        visit(payload)
        return total, telegram

    def verify(self, query: str = "OpenClaw deploy verification sample") -> ComponentResult:
        if not self.settings.enabled or self.settings.mode == "disabled":
            return ComponentResult(
                component="pansou",
                status=ComponentStatus.SKIPPED,
                required=False,
                enabled=False,
                details={"reason": "disabled"},
            )
        proxy_configured = self.settings.proxy.mode != "none"
        params = urllib.parse.urlencode(
            {"kw": query, "cloud_types": "quark", "res": "all", "src": "all"}
        )
        service_healthy = False
        api_reachable = False
        total = 0
        telegram = 0
        error_type = ""
        try:
            request = urllib.request.Request(f"{self.base_url}/api/search?{params}")
            with self._opener(request, timeout=20) as response:
                service_healthy = 200 <= int(getattr(response, "status", 200)) < 500
                body = response.read().decode("utf-8")
            payload = json.loads(body)
            api_reachable = isinstance(payload, dict)
            total, telegram = self._source_count(payload)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            error_type = type(error).__name__
        telegram_reachable = telegram > 0
        ready = service_healthy and api_reachable and telegram_reachable
        status = ComponentStatus.READY if ready else ComponentStatus.DEGRADED
        return ComponentResult(
            component="pansou",
            status=status,
            required=False,
            enabled=True,
            next_action="none" if ready else "check_pansou_proxy_or_channels",
            severity=None if ready else Severity.DEGRADED,
            details={
                "serviceHealthy": service_healthy,
                "apiReachable": api_reachable,
                "telegramReachable": telegram_reachable,
                "proxyConfigured": proxy_configured,
                "sourceCount": total,
                "telegramSourceCount": telegram,
                "errorType": error_type,
            },
        )
