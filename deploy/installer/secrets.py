"""Filesystem-backed secret references with strict POSIX permissions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Collection
from pathlib import Path

from .errors import DeploymentError

_SECRET_DIR_MODE = 0o700
_SECRET_FILE_MODE = 0o600
_SECRET_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _security_error(code: str, message: str, *, details=None) -> DeploymentError:
    return DeploymentError(
        code,
        message,
        severity="security_block",
        next_action="fix_secret_permissions",
        details=details or {},
    )


class SecretStore:
    """Read direct child secret files without following symlinks."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._seen_names: set[str] = set()
        self._validate_root()

    def __repr__(self) -> str:
        names = ",".join(sorted(self._seen_names))
        return f"SecretStore(root={self.root!s}, names=[{names}])"

    def _validate_root(self) -> None:
        if self.root.is_symlink():
            raise _security_error("SECRET_ROOT_SYMLINK", "secret root must not be a symlink")
        try:
            info = self.root.stat()
        except OSError:
            raise DeploymentError(
                "SECRET_ROOT_MISSING",
                f"secret root does not exist: {self.root}",
                status="manual_action_required",
                next_action="create_secret_directory",
            ) from None
        if not stat.S_ISDIR(info.st_mode):
            raise _security_error("SECRET_ROOT_INVALID", "secret root must be a directory")
        mode = stat.S_IMODE(info.st_mode)
        if mode != _SECRET_DIR_MODE:
            raise _security_error(
                "SECRET_ROOT_MODE",
                "secret root permissions must be 0700",
                details={"mode": f"{mode:04o}"},
            )

    @staticmethod
    def _validate_name(name: str) -> str:
        value = str(name)
        if (
            not value
            or not _SECRET_NAME.fullmatch(value)
            or "/" in value
            or "\\" in value
            or ".." in value
            or value in {".", ".."}
        ):
            raise _security_error("SECRET_NAME_INVALID", "invalid secret name")
        return value

    def _path(self, name: str) -> Path:
        return self.root / self._validate_name(name)

    def _validated_file_stat(self, name: str) -> tuple[Path, os.stat_result]:
        self._validate_root()
        path = self._path(name)
        try:
            info = path.lstat()
        except OSError:
            raise DeploymentError(
                "SECRET_MISSING",
                f"secret file is missing: {name}",
                status="manual_action_required",
                next_action="fill_secret_file",
                details={"name": name},
            ) from None
        if stat.S_ISLNK(info.st_mode):
            raise _security_error("SECRET_SYMLINK", "secret files must not be symlinks")
        if not stat.S_ISREG(info.st_mode):
            raise _security_error("SECRET_NOT_FILE", "secret path must be a regular file")
        mode = stat.S_IMODE(info.st_mode)
        if mode != _SECRET_FILE_MODE:
            raise _security_error(
                "SECRET_FILE_MODE",
                "secret file permissions must be 0600",
                details={"name": name, "mode": f"{mode:04o}"},
            )
        return path, info

    def read(self, name: str) -> str:
        value = self._validate_name(name)
        path, _ = self._validated_file_stat(value)
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise DeploymentError(
                "SECRET_READ_FAILED",
                f"unable to read secret file: {value}",
                next_action="replace_secret_file",
                details={"reason": type(error).__name__},
            ) from None
        self._seen_names.add(value)
        if content.endswith("\r\n"):
            return content[:-2]
        if content.endswith("\n"):
            return content[:-1]
        return content

    def metadata_digest(self, names: Collection[str]) -> str:
        self._validate_root()
        records: list[dict[str, object]] = []
        for raw_name in sorted({self._validate_name(name) for name in names}):
            path = self._path(raw_name)
            try:
                info = path.lstat()
            except OSError:
                records.append({"name": raw_name, "exists": False})
                continue
            if stat.S_ISLNK(info.st_mode):
                raise _security_error("SECRET_SYMLINK", "secret files must not be symlinks")
            if not stat.S_ISREG(info.st_mode):
                raise _security_error("SECRET_NOT_FILE", "secret path must be a regular file")
            mode = stat.S_IMODE(info.st_mode)
            if mode != _SECRET_FILE_MODE:
                raise _security_error(
                    "SECRET_FILE_MODE",
                    "secret file permissions must be 0600",
                    details={"name": raw_name, "mode": f"{mode:04o}"},
                )
            records.append(
                {
                    "name": raw_name,
                    "exists": True,
                    "inode": int(info.st_ino),
                    "size": int(info.st_size),
                    "mode": mode,
                    "mtimeNs": int(info.st_mtime_ns),
                }
            )
        encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()
