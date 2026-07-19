"""Keep download staging directories writable by aria2 (nobody)."""

from __future__ import annotations

import os
from pathlib import Path

# aria2-pro runs aria2c as nobody:nogroup. OpenClaw/Skill often create
# directories as root; without other-write, downloads abort with error 16/18.
ARIA2_DIR_MODE = 0o777


def ensure_aria2_writable(path: Path | str) -> Path:
    """Create ``path`` (and missing parents) mode 0777 for aria2 nobody."""
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return target
    current = target
    # chmod leaf + parents while they look like managed download dirs
    for _ in range(8):
        try:
            os.chmod(current, ARIA2_DIR_MODE)
        except OSError:
            pass
        parent = current.parent
        if parent == current:
            break
        name = current.name
        # Stop above the downloads root once we leave managed zones.
        if name in {".incoming", ".ready", ".quarantine"} or current.name.startswith(
            "rd-"
        ):
            current = parent
            continue
        if name == "downloads" or str(current).endswith("/downloads"):
            try:
                os.chmod(current, ARIA2_DIR_MODE)
            except OSError:
                pass
            break
        current = parent
    return target


def ensure_managed_download_roots(downloads_root: Path | str) -> list[Path]:
    root = Path(downloads_root)
    managed = [
        root,
        root / ".incoming",
        root / ".ready",
        root / ".quarantine",
    ]
    for path in managed:
        ensure_aria2_writable(path)
    return managed


def is_world_writable(path: Path | str) -> bool:
    try:
        mode = Path(path).stat().st_mode
    except OSError:
        return False
    return bool(mode & 0o002)
