"""Create and probe the Agent's managed download directories safely."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

DEFAULT_INCOMING_MODE = 0o770
# Backward-compatible exported name. The effective mode is read from
# RESOURCE_AGENT_INCOMING_MODE for each operation.
ARIA2_DIR_MODE = DEFAULT_INCOMING_MODE
AGENT_DIR_MODE = 0o750
_ALLOWED_INCOMING_MODES = {0o750, 0o770, 0o777}


class DownloadFsError(RuntimeError):
    pass


def _resolve(path: Path | str) -> Path:
    return Path(path).expanduser()


def incoming_mode() -> int:
    """Return the deployer-selected managed incoming mode.

    0750 is used when OpenClaw and aria2 share a UID, 0770 for a shared group
    or ACL-backed deployment, and 0777 only for the explicitly discovered
    fallback. Unknown values fail closed instead of silently widening access.
    """

    raw = os.environ.get("RESOURCE_AGENT_INCOMING_MODE")
    if raw is None:
        return DEFAULT_INCOMING_MODE
    text = str(raw).strip()
    if not text:
        raise DownloadFsError("RESOURCE_AGENT_INCOMING_MODE is empty")
    try:
        mode = int(text, 8)
    except ValueError as error:
        raise DownloadFsError("RESOURCE_AGENT_INCOMING_MODE must be octal") from error
    if mode not in _ALLOWED_INCOMING_MODES:
        raise DownloadFsError(
            "RESOURCE_AGENT_INCOMING_MODE must be one of 0750, 0770, or 0777"
        )
    return mode


def assert_under_downloads_root(path: Path | str, downloads_root: Path | str) -> Path:
    target = _resolve(path).resolve()
    root = _resolve(downloads_root).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise DownloadFsError(
            f"refusing chmod outside downloads root: {target} not under {root}"
        ) from error
    return target


def _relative_zone(path: Path, downloads_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(downloads_root.resolve())
    except ValueError:
        return "outside"
    if not relative.parts:
        return "downloads_root"
    if relative.parts[0] == ".incoming":
        return "incoming"
    if relative.parts[0] == ".ready":
        return "ready"
    if relative.parts[0] == ".quarantine":
        return "quarantine"
    return "other"


def _has_named_ancestor(path: Path, names: set[str], *, depth: int = 12) -> bool:
    current = path
    for _ in range(depth):
        if current.name in names:
            return True
        parent = current.parent
        if parent == current:
            break
        current = parent
    return False


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def ensure_aria2_writable(
    path: Path | str,
    *,
    downloads_root: Path | str | None = None,
) -> Path:
    """Create a managed directory and apply the deployer-selected mode.

    This function never decides that 0777 is required. The deployment planner
    makes that decision from the actual aria2 identity and exposes it through
    RESOURCE_AGENT_INCOMING_MODE.
    """

    target = _resolve(path)
    root = _resolve(downloads_root) if downloads_root is not None else None
    if root is not None:
        assert_under_downloads_root(target, root)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return target

    selected_incoming_mode = incoming_mode()
    if root is not None:
        zone = _relative_zone(target, root)
        if zone in {"downloads_root", "incoming"}:
            current = target
            for _ in range(12):
                resolved = current.resolve()
                current_zone = _relative_zone(resolved, root)
                if current_zone not in {"downloads_root", "incoming"}:
                    break
                _chmod(resolved, selected_incoming_mode)
                if resolved == root.resolve():
                    break
                parent = current.parent
                if parent == current:
                    break
                current = parent
            return target
        if zone in {"ready", "quarantine"}:
            _chmod(target, AGENT_DIR_MODE)
            return target
        # A caller with an explicit root must not use this helper for arbitrary
        # children outside the managed lanes.
        raise DownloadFsError(f"refusing chmod outside managed download lanes: {target}")

    if _has_named_ancestor(target, {".ready", ".quarantine"}):
        _chmod(target, AGENT_DIR_MODE)
        return target
    if _has_named_ancestor(target, {".incoming"}):
        _chmod(target, selected_incoming_mode)
        current = target.parent
        for _ in range(12):
            if current.name == ".incoming":
                _chmod(current, selected_incoming_mode)
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
        return target

    # Legacy callers may pass a top-level staging path whose name does not carry
    # lane information. Keep it private to the Agent rather than widening it.
    _chmod(target, AGENT_DIR_MODE)
    return target


def ensure_agent_dir(path: Path | str) -> Path:
    target = _resolve(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(target, AGENT_DIR_MODE)
    except OSError:
        pass
    return target


def ensure_managed_download_roots(downloads_root: Path | str) -> list[Path]:
    root = _resolve(downloads_root)
    incoming = root / ".incoming"
    ready = root / ".ready"
    quarantine = root / ".quarantine"
    ensure_aria2_writable(root, downloads_root=root)
    ensure_aria2_writable(incoming, downloads_root=root)
    ensure_agent_dir(ready)
    ensure_agent_dir(quarantine)
    return [root, incoming, ready, quarantine]


def probe_writable(path: Path | str) -> bool:
    """Test effective write access with an exclusive zero-byte probe."""

    target = _resolve(path)
    if not target.is_dir():
        return False
    probe = target / (
        f".openclaw-write-probe-{os.getpid()}-{secrets.token_hex(8)}"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            probe,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(descriptor)
        descriptor = None
        probe.unlink()
        return True
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            probe.unlink()
        except OSError:
            pass
        return False


def is_world_writable(path: Path | str) -> bool:
    """Compatibility helper; readiness no longer relies on this property."""

    try:
        mode = _resolve(path).stat().st_mode
    except OSError:
        return False
    return bool(mode & 0o002)
