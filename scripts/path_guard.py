from pathlib import Path


class PathGuardError(RuntimeError):
    pass


class PathGuard:
    def __init__(self, allowed_roots):
        self.roots = tuple(
            Path(root).expanduser().resolve(strict=True)
            for root in allowed_roots
        )
        if not self.roots:
            raise PathGuardError("at least one allowed root is required")

    def _assert_contained(self, path: Path) -> Path:
        if not any(path.is_relative_to(root) for root in self.roots):
            raise PathGuardError("path is outside allowed media roots")
        return path

    def resolve_existing(self, path: str) -> Path:
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except (FileNotFoundError, RuntimeError, OSError) as error:
            raise PathGuardError(f"source path is not safely resolvable: {error}") from None
        return self._assert_contained(resolved)

    def resolve_target(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        try:
            resolved = candidate.resolve(strict=False)
        except (RuntimeError, OSError) as error:
            raise PathGuardError(f"target path is not safely resolvable: {error}") from None
        return self._assert_contained(resolved)

    def assert_mutable(self, path: Path) -> None:
        resolved = self._assert_contained(Path(path).resolve(strict=False))
        if resolved in self.roots:
            raise PathGuardError("media root itself is immutable")
