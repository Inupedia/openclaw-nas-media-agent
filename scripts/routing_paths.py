"""Derive downloads / library / organizing paths from one routing config."""

from __future__ import annotations

from pathlib import Path


def downloads_section(routing: dict) -> dict:
    section = routing.get("downloads")
    if not isinstance(section, dict) or not section.get("root"):
        raise ValueError("routing.downloads.root is required")
    return section


def downloads_root(routing: dict) -> Path:
    return Path(str(downloads_section(routing)["root"]))


def agent_downloads_root(routing: dict) -> Path:
    section = downloads_section(routing)
    return Path(str(section.get("agent_root") or section.get("host_root") or section["root"]))


def aria2_downloads_root(routing: dict) -> Path:
    section = downloads_section(routing)
    return Path(str(section.get("aria2_root") or "/nas/downloads"))


def agent_path_to_aria2(path: Path | str, routing: dict) -> str:
    """Map an OpenClaw/agent downloads path to the aria2 container path."""
    agent_root = agent_downloads_root(routing).resolve(strict=False)
    aria2_root = aria2_downloads_root(routing)
    resolved = Path(path).expanduser().resolve(strict=False)
    try:
        relative = resolved.relative_to(agent_root)
    except ValueError as error:
        raise ValueError(
            f"path is outside agent downloads root: {resolved}"
        ) from error
    return (aria2_root / relative).as_posix()


def staging_root(routing: dict) -> Path:
    section = downloads_section(routing)
    return Path(str(section.get("staging_root") or (downloads_root(routing) / ".incoming")))


def ready_root(routing: dict) -> Path:
    section = downloads_section(routing)
    return Path(str(section.get("ready_root") or (downloads_root(routing) / ".ready")))


def quarantine_root(routing: dict) -> Path:
    section = downloads_section(routing)
    return Path(
        str(section.get("quarantine_root") or (downloads_root(routing) / ".quarantine"))
    )


def final_roots(routing: dict) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for value in routing.values():
        if not isinstance(value, dict):
            continue
        final = value.get("final_root")
        if not final:
            continue
        path = Path(str(final))
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    return roots


def protected_roots(routing: dict) -> list[Path]:
    paths = routing.get("paths") if isinstance(routing.get("paths"), dict) else {}
    configured = list(paths.get("protected_roots") or [])
    if not configured:
        configured = list(downloads_section(routing).get("protected_roots") or [])
    if configured:
        return [Path(str(item)) for item in configured]
    # Fallback: unique parents of final_root entries.
    parents: list[Path] = []
    seen: set[str] = set()
    for final in final_roots(routing):
        parent = final.parent
        key = str(parent)
        if key in seen:
            continue
        seen.add(key)
        parents.append(parent)
    return parents


def organizing_root(routing: dict) -> Path:
    paths = routing.get("paths") if isinstance(routing.get("paths"), dict) else {}
    configured = paths.get("organizing_root") or downloads_section(routing).get(
        "organizing_root"
    )
    if configured:
        return Path(str(configured))
    # On UGREEN this NAS, /volume3/.openclaw-organizing is a different device
    # from /volume3/临时影视; stage under the already-mounted movie library root.
    return Path("/volume3/临时影视/.openclaw-organizing")


def path_guard_roots(routing: dict) -> tuple[list[Path], list[Path]]:
    """Return (allowed_roots, protected_roots) for PathGuard."""
    protected = protected_roots(routing)
    allowed = [
        downloads_root(routing),
        organizing_root(routing),
        *protected,
        *final_roots(routing),
    ]
    # Deduplicate while preserving order.
    unique: list[Path] = []
    seen: set[str] = set()
    for root in allowed:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique, protected
