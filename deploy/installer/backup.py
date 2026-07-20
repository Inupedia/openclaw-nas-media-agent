"""Secret-free backup manifests for deployment transactions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import DeploymentError
from .runtime import RuntimePaths, atomic_write_json


@dataclass(frozen=True)
class BackupEntry:
    source: Path
    backup_relative: Path
    mode: int
    uid: int
    gid: int
    sha256: str
    kind: str
    restore_action: str
    link_target: str | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "source": str(self.source),
            "backupRelative": str(self.backup_relative),
            "mode": f"{self.mode:04o}",
            "uid": self.uid,
            "gid": self.gid,
            "sha256": self.sha256,
            "kind": self.kind,
            "restoreAction": self.restore_action,
        }
        if self.link_target is not None:
            result["linkTarget"] = self.link_target
        return result


@dataclass(frozen=True)
class BackupManifest:
    deployment_id: str
    project_root: Path
    backup_root: Path
    entries: tuple[BackupEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "deploymentId": self.deployment_id,
            "projectRoot": str(self.project_root),
            "backupRoot": str(self.backup_root),
            "entries": [entry.to_dict() for entry in self.entries],
        }


def _inside(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve(strict=False)
    for root in roots:
        try:
            resolved.relative_to(root.resolve(strict=False))
            return True
        except ValueError:
            continue
    return False


def _source_inside(path: Path, roots: Sequence[Path]) -> bool:
    absolute = path.absolute()
    for root in roots:
        try:
            absolute.relative_to(root.absolute())
            return True
        except ValueError:
            continue
    return False


def _forbidden_name(path: Path) -> bool:
    name = path.name.casefold()
    parts = {part.casefold() for part in path.parts}
    return (
        name == ".env"
        or name.endswith(".env")
        or "storage_state" in name
        or "storage-state" in name
        or ("deploy" in parts and "secrets" in parts)
    )


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _contains_sentinel(path: Path, sentinels: Sequence[bytes]) -> bool:
    if not sentinels:
        return False
    with path.open("rb") as handle:
        overlap = max(len(value) for value in sentinels) - 1
        previous = b""
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return any(value in previous for value in sentinels)
            data = previous + chunk
            if any(value in data for value in sentinels):
                return True
            previous = data[-overlap:] if overlap > 0 else b""


def _iter_sources(sources: Sequence[Path]) -> Iterable[Path]:
    for source in sources:
        target = Path(source)
        yield target
        if target.is_dir() and not target.is_symlink():
            for path in sorted(target.rglob("*"), key=lambda item: str(item)):
                yield path


def create_backup(
    sources: Sequence[Path],
    runtime: RuntimePaths,
    deployment_id: str,
    *,
    allowed_roots: Sequence[Path],
    secret_sentinels: Sequence[str] = (),
) -> BackupManifest:
    """Copy explicit sources into a private backup without capturing credentials."""

    allowed = tuple(Path(root).resolve(strict=False) for root in allowed_roots)
    backup_root = runtime.backup_dir(deployment_id)
    payload_root = backup_root / "payload"
    payload_root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(payload_root, 0o700)
    sentinel_bytes = tuple(
        value.encode("utf-8") for value in secret_sentinels if str(value)
    )
    entries: list[BackupEntry] = []
    seen: set[Path] = set()

    for source in _iter_sources(tuple(Path(item) for item in sources)):
        absolute = source.absolute()
        if absolute in seen or not os.path.lexists(absolute):
            continue
        seen.add(absolute)
        if not _source_inside(absolute, allowed):
            raise DeploymentError(
                "BACKUP_SOURCE_OUTSIDE_ALLOWED_ROOT",
                "backup source is outside approved roots",
                severity="security_block",
                next_action="review_backup_sources",
                details={"source": str(absolute)},
            )
        if _forbidden_name(absolute):
            raise DeploymentError(
                "BACKUP_SECRET_PATH_FORBIDDEN",
                "secret or session-state paths must not be backed up",
                severity="security_block",
                next_action="remove_secret_from_backup_set",
                details={"source": str(absolute)},
            )
        metadata = absolute.lstat()
        relative = Path(f"entry-{len(entries):04d}")
        destination = payload_root / relative
        kind = "file"
        link_target: str | None = None
        digest = "sha256:" + hashlib.sha256(b"").hexdigest()
        restore_action = "restore_file"

        if stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
            restore_action = "restore_symlink"
            link_target = os.readlink(absolute)
            resolved_target = (absolute.parent / link_target).resolve(strict=False)
            if not _inside(resolved_target, allowed):
                raise DeploymentError(
                    "BACKUP_SYMLINK_ESCAPE",
                    "backup symlink escapes approved roots",
                    severity="security_block",
                    next_action="remove_escaping_symlink",
                    details={"source": str(absolute)},
                )
            destination.symlink_to(link_target)
            digest = "sha256:" + hashlib.sha256(link_target.encode()).hexdigest()
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
            restore_action = "restore_directory"
            destination.mkdir(mode=0o700)
        elif stat.S_ISREG(metadata.st_mode):
            if _contains_sentinel(absolute, sentinel_bytes):
                raise DeploymentError(
                    "BACKUP_SECRET_CONTENT_FORBIDDEN",
                    "backup source contains a configured secret value",
                    severity="security_block",
                    next_action="remove_secret_from_backup_source",
                    details={"source": str(absolute)},
                )
            shutil.copy2(absolute, destination, follow_symlinks=False)
            os.chmod(destination, 0o600)
            digest = _digest_file(absolute)
        else:
            raise DeploymentError(
                "BACKUP_SOURCE_TYPE_UNSUPPORTED",
                "backup source is not a regular file, directory or symlink",
                severity="security_block",
                next_action="review_backup_sources",
                details={"source": str(absolute)},
            )

        entries.append(
            BackupEntry(
                source=absolute,
                backup_relative=Path("payload") / relative,
                mode=stat.S_IMODE(metadata.st_mode),
                uid=metadata.st_uid,
                gid=metadata.st_gid,
                sha256=digest,
                kind=kind,
                restore_action=restore_action,
                link_target=link_target,
            )
        )

    manifest = BackupManifest(
        deployment_id=str(deployment_id),
        project_root=runtime.project_root,
        backup_root=backup_root,
        entries=tuple(entries),
    )
    atomic_write_json(backup_root / "manifest.json", manifest.to_dict())
    return manifest


def load_backup_manifest(path: Path) -> BackupManifest:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = tuple(
        BackupEntry(
            source=Path(item["source"]),
            backup_relative=Path(item["backupRelative"]),
            mode=int(str(item["mode"]), 8),
            uid=int(item["uid"]),
            gid=int(item["gid"]),
            sha256=str(item["sha256"]),
            kind=str(item["kind"]),
            restore_action=str(item["restoreAction"]),
            link_target=item.get("linkTarget"),
        )
        for item in value.get("entries", [])
    )
    return BackupManifest(
        deployment_id=str(value["deploymentId"]),
        project_root=Path(value["projectRoot"]),
        backup_root=Path(value["backupRoot"]),
        entries=entries,
    )
