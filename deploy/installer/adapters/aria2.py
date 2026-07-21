"""aria2 reuse, runtime identity, authenticated RPC and mount/write verification."""
from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..command import CommandRunner
from ..errors import DeploymentError
from ..models import ComponentResult, ComponentStatus, Severity


@dataclass(frozen=True)
class RuntimeIdentity:
    uid: int
    gid: int
    groups: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        return {"uid": self.uid, "gid": self.gid, "groups": list(self.groups)}


class Aria2Adapter:
    def __init__(
        self,
        container_name: str,
        rpc_url: str,
        secret: str,
        host_downloads: Path,
        *,
        runner: CommandRunner,
        opener: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.container_name = str(container_name)
        self.rpc_url = str(rpc_url)
        self._secret = str(secret)
        self.host_downloads = Path(host_downloads).resolve()
        self.runner = runner
        self._opener = opener
        self._sleep = sleep

    def runtime_identity(self) -> RuntimeIdentity:
        values: list[str] = []
        for args in (("id", "-u"), ("id", "-g"), ("id", "-G")):
            result = self.runner.run(["docker", "exec", self.container_name, *args])
            if result.returncode != 0:
                raise DeploymentError(
                    "ARIA2_IDENTITY_UNAVAILABLE",
                    "unable to determine aria2 runtime identity",
                    status="manual_action_required",
                    next_action="inspect_aria2_container",
                    details={"command": list(args), "returncode": result.returncode},
                )
            values.append(result.stdout.strip())
        try:
            uid = int(values[0])
            gid = int(values[1])
            groups = tuple(sorted({int(item) for item in values[2].split()}))
        except ValueError:
            raise DeploymentError(
                "ARIA2_IDENTITY_INVALID",
                "aria2 identity command returned invalid numeric values",
                next_action="inspect_aria2_container",
            ) from None
        return RuntimeIdentity(uid, gid, groups)

    def _rpc(self, method: str, params: list[object] | None = None) -> object:
        payload = {
            "jsonrpc": "2.0",
            "id": "openclaw-media-deployer",
            "method": f"aria2.{method}",
            "params": [f"token:{self._secret}", *(params or [])],
        }
        request = urllib.request.Request(
            self.rpc_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=10) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise DeploymentError(
                "ARIA2_RPC_UNREACHABLE",
                "aria2 RPC verification failed",
                next_action="configure_aria2_rpc",
                details={"reason": type(error).__name__},
            ) from None
        if not isinstance(value, dict) or "error" in value:
            message = str(
                value.get("error", "invalid response")
                if isinstance(value, dict)
                else "invalid response"
            )
            message = message.replace(self._secret, "***")
            raise DeploymentError(
                "ARIA2_RPC_REJECTED",
                "aria2 RPC rejected authenticated request",
                next_action="configure_aria2_rpc",
                details={"error": message[:300]},
            )
        return value.get("result")

    def verify_rpc(self) -> ComponentResult:
        result = self._rpc("getVersion")
        ready = isinstance(result, Mapping) and bool(result.get("version"))
        return ComponentResult(
            component="aria2_rpc",
            status=ComponentStatus.READY if ready else ComponentStatus.FAILED,
            required=True,
            enabled=True,
            next_action="none" if ready else "configure_aria2_rpc",
            severity=None if ready else Severity.BLOCKING,
            details={
                "authenticated": ready,
                "version": str(result.get("version", ""))
                if isinstance(result, Mapping)
                else "",
            },
        )

    def mount_source(self) -> Path:
        result = self.runner.run(
            ["docker", "inspect", self.container_name, "--format", "{{json .Mounts}}"]
        )
        if result.returncode != 0:
            raise DeploymentError(
                "ARIA2_MOUNT_UNAVAILABLE",
                "unable to inspect aria2 mounts",
                next_action="inspect_aria2_container",
            )
        try:
            mounts = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise DeploymentError(
                "ARIA2_MOUNT_INVALID",
                "aria2 mount inspection returned invalid JSON",
                next_action="inspect_aria2_container",
            ) from None
        if not isinstance(mounts, list):
            mounts = []
        for item in mounts:
            if isinstance(item, dict) and item.get("Destination") == "/nas/downloads":
                source = item.get("Source")
                if source:
                    return Path(str(source)).resolve()
        raise DeploymentError(
            "ARIA2_DOWNLOAD_MOUNT_MISSING",
            "aria2 does not mount /nas/downloads",
            severity="security_block",
            next_action="fix_aria2_download_mount",
        )

    def verify_mount(self, openclaw_visible_source: Path | None = None) -> ComponentResult:
        source = self.mount_source()
        expected = self.host_downloads
        openclaw_source = (
            Path(openclaw_visible_source).resolve()
            if openclaw_visible_source
            else expected
        )
        ready = source == expected == openclaw_source
        return ComponentResult(
            component="aria2_mount",
            status=ComponentStatus.READY if ready else ComponentStatus.FAILED,
            required=True,
            enabled=True,
            next_action="none" if ready else "align_download_mounts",
            severity=None if ready else Severity.SECURITY_BLOCK,
            details={
                "aria2Source": str(source),
                "configuredSource": str(expected),
                "openclawSource": str(openclaw_source),
                "containerDestination": "/nas/downloads",
            },
        )

    def verify_write_probe(
        self,
        deployment_id: str,
        probe_url: str,
        *,
        wait_attempts: int = 20,
    ) -> ComponentResult:
        safe_id = "".join(
            ch for ch in str(deployment_id) if ch.isalnum() or ch in "-_"
        )
        if not safe_id or safe_id != str(deployment_id):
            raise DeploymentError(
                "ARIA2_PROBE_ID_INVALID",
                "invalid deployment id for aria2 probe",
            )
        probe_dir = self.host_downloads / ".incoming" / f".deploy-probe-{safe_id}"
        if probe_dir.exists():
            raise DeploymentError(
                "ARIA2_PROBE_PATH_EXISTS",
                "aria2 probe path already exists",
                severity="security_block",
                next_action="inspect_probe_path",
                details={"path": str(probe_dir)},
            )
        probe_dir.mkdir(parents=True, mode=0o770)
        gid = ""
        observed = False
        try:
            gid_value = self._rpc(
                "addUri",
                [
                    [str(probe_url)],
                    {
                        "dir": f"/nas/downloads/.incoming/.deploy-probe-{safe_id}",
                        "out": "probe.bin",
                        "max-connection-per-server": "1",
                        "allow-overwrite": "false",
                    },
                ],
            )
            gid = str(gid_value or "")
            for _ in range(max(1, int(wait_attempts))):
                if (probe_dir / "probe.bin").is_file():
                    observed = True
                    break
                self._sleep(0.1)
        finally:
            if gid:
                try:
                    self._rpc("remove", [gid])
                except DeploymentError:
                    pass
                try:
                    self._rpc("removeDownloadResult", [gid])
                except DeploymentError:
                    pass
            shutil.rmtree(probe_dir, ignore_errors=True)
        return ComponentResult(
            component="aria2_write_probe",
            status=ComponentStatus.READY if observed else ComponentStatus.FAILED,
            required=True,
            enabled=True,
            next_action="none" if observed else "fix_aria2_write_mapping",
            severity=None if observed else Severity.BLOCKING,
            details={
                "hostRoot": str(self.host_downloads),
                "containerRoot": "/nas/downloads",
                "probeObserved": observed,
                "probeGidCreated": bool(gid),
            },
        )
