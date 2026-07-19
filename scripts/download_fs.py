"""Keep download staging directories writable by aria2 (nobody) within managed roots."""

from __future__ import annotations

import os
from pathlib import Path

# aria2-pro runs aria2c as nobody:nogroup. Only downloads root + .incoming
# need world-writable. .ready / .quarantine are Agent-owned trust boundaries.
ARIA2_DIR_MODE = 0o777
AGENT_DIR_MODE = 0o750


class DownloadFsError(RuntimeError):
    pass


def _resolve(path: Path | str) -> Path:
    return Path(path).expanduser()


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


def _is_aria2_zone(path: Path, downloads_root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(downloads_root.resolve())
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        return True  # downloads root itself
    if parts[0] == ".incoming":
        return True
    return False


def ensure_aria2_writable(
    path: Path | str,
    *,
    downloads_root: Path | str | None = None,
) -> Path:
    """Create path; chmod 0777 only for downloads root / .incoming trees."""
    target = _resolve(path)
    root = _resolve(downloads_root) if downloads_root is not None else None
    if root is not None:
        assert_under_downloads_root(target, root)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return target

    if root is None:
        # Legacy callers without root: only chmod leaf if name suggests incoming/task.
        name = target.name
        if name in {".ready", ".quarantine"}:
            try:
                os.chmod(target, AGENT_DIR_MODE)
            except OSError:
                pass
            return target
        try:
            os.chmod(target, ARIA2_DIR_MODE)
        except OSError:
            pass
        parent = target.parent
        if parent.name == ".incoming" or parent.name == "downloads":
            try:
                os.chmod(parent, ARIA2_DIR_MODE)
            except OSError:
                pass
        return target

    current = target
    for _ in range(12):
        try:
            resolved = current.resolve()
        except OSError:
            break
        if not _is_aria2_zone(resolved, root):
            # Still ensure Agent zones exist with tighter mode when we walk into them.
            if resolved.name in {".ready", ".quarantine"}:
                try:
                    os.chmod(resolved, AGENT_DIR_MODE)
                except OSError:
                    pass
            break
        try:
            os.chmod(resolved, ARIA2_DIR_MODE)
        except OSError:
            pass
        if resolved == root.resolve():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
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


def is_world_writable(path: Path | str) -> bool:
    try:
        mode = _resolve(path).stat().st_mode
    except OSError:
        return False
    return bool(mode & 0o002)
