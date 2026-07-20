"""Pure aria2 permission strategy planning.

The deployer decides which explicit filesystem changes are necessary before
execution. This module never mutates the filesystem itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .errors import DeploymentError


class PermissionStrategy(str, Enum):
    SAME_UID = "same_uid"
    SHARED_GID = "shared_gid"
    POSIX_ACL = "posix_acl"
    WORLD_WRITABLE_FALLBACK = "world_writable_fallback"
    AGENT_OWNED = "agent_owned"


@dataclass(frozen=True)
class RuntimeIdentity:
    uid: int
    gid: int
    groups: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.uid < 0 or self.gid < 0 or any(group < 0 for group in self.groups):
            raise ValueError("runtime identity values must be non-negative")


@dataclass(frozen=True)
class PathStat:
    path: Path
    uid: int
    gid: int
    mode: int
    zone: str

    def __post_init__(self) -> None:
        if self.uid < 0 or self.gid < 0:
            raise ValueError("path ownership values must be non-negative")
        if self.mode < 0 or self.mode > 0o7777:
            raise ValueError("path mode is invalid")


@dataclass(frozen=True)
class PermissionChange:
    action: str
    target: Path
    uid: int | None = None
    gid: int | None = None
    mode: int | None = None
    acl_entry: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "action": self.action,
            "target": str(self.target),
        }
        if self.uid is not None:
            payload["uid"] = self.uid
        if self.gid is not None:
            payload["gid"] = self.gid
        if self.mode is not None:
            payload["mode"] = f"{self.mode:04o}"
        if self.acl_entry is not None:
            payload["aclEntry"] = self.acl_entry
        return payload


@dataclass(frozen=True)
class PermissionPlan:
    strategy: PermissionStrategy
    uid: int
    gid: int
    mode: int
    acl_entries: tuple[str, ...]
    changes: tuple[PermissionChange, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy.value,
            "uid": self.uid,
            "gid": self.gid,
            "mode": f"{self.mode:04o}",
            "aclEntries": list(self.acl_entries),
            "changes": [change.to_dict() for change in self.changes],
        }


def _mode_change(path_stat: PathStat, mode: int) -> tuple[PermissionChange, ...]:
    if path_stat.mode & 0o777 == mode:
        return ()
    return (
        PermissionChange(
            action="set_mode",
            target=path_stat.path,
            mode=mode,
        ),
    )


def choose_permission_plan(
    path_stat: PathStat,
    aria_identity: RuntimeIdentity,
    acl_supported: bool,
) -> PermissionPlan:
    """Choose the narrowest supported write strategy for one managed path."""

    zone = path_stat.zone
    if zone == "formal_library":
        raise DeploymentError(
            "FORMAL_LIBRARY_PERMISSION_CHANGE",
            "formal media libraries are never permission-change targets",
            severity="security_block",
            next_action="remove_formal_library_permission_change",
            details={"path": str(path_stat.path)},
        )

    if zone in {"ready", "quarantine"}:
        mode = 0o750
        return PermissionPlan(
            strategy=PermissionStrategy.AGENT_OWNED,
            uid=path_stat.uid,
            gid=path_stat.gid,
            mode=mode,
            acl_entries=(),
            changes=_mode_change(path_stat, mode),
        )

    if zone not in {"downloads_root", "incoming"}:
        raise DeploymentError(
            "PERMISSION_ZONE_UNSUPPORTED",
            "permission planning is limited to managed download paths",
            severity="security_block",
            next_action="review_permission_target",
            details={"path": str(path_stat.path), "zone": zone},
        )

    if path_stat.uid == aria_identity.uid:
        mode = 0o750
        return PermissionPlan(
            strategy=PermissionStrategy.SAME_UID,
            uid=path_stat.uid,
            gid=path_stat.gid,
            mode=mode,
            acl_entries=(),
            changes=_mode_change(path_stat, mode),
        )

    groups = set(aria_identity.groups) | {aria_identity.gid}
    if path_stat.gid in groups:
        mode = 0o770
        return PermissionPlan(
            strategy=PermissionStrategy.SHARED_GID,
            uid=path_stat.uid,
            gid=path_stat.gid,
            mode=mode,
            acl_entries=(),
            changes=_mode_change(path_stat, mode),
        )

    if acl_supported:
        mode = 0o750
        entry = f"u:{aria_identity.uid}:rwx"
        return PermissionPlan(
            strategy=PermissionStrategy.POSIX_ACL,
            uid=path_stat.uid,
            gid=path_stat.gid,
            mode=mode,
            acl_entries=(entry,),
            changes=(
                PermissionChange(
                    action="set_acl",
                    target=path_stat.path,
                    acl_entry=entry,
                ),
            ),
        )

    mode = 0o777
    return PermissionPlan(
        strategy=PermissionStrategy.WORLD_WRITABLE_FALLBACK,
        uid=path_stat.uid,
        gid=path_stat.gid,
        mode=mode,
        acl_entries=(),
        changes=_mode_change(path_stat, mode),
    )
