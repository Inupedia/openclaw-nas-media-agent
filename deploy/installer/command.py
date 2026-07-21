"""Injected subprocess execution for deterministic, testable discovery."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import DeploymentError
from .redaction import redact


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner:
    """Run argv-only commands; never invoke a shell."""

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        secret_values: Collection[str] = (),
        run_impl: Callable = subprocess.run,
    ) -> None:
        self.cwd = None if cwd is None else Path(cwd)
        self.env = None if env is None else dict(env)
        self.secret_values = tuple(str(value) for value in secret_values if str(value))
        self._run_impl = run_impl

    def run(self, args: Sequence[str], timeout: int = 30) -> CommandResult:
        if isinstance(args, (str, bytes)):
            raise TypeError("command arguments must be an argv sequence")
        argv = [str(item) for item in args]
        if not argv:
            raise TypeError("command arguments must not be empty")
        try:
            completed = self._run_impl(
                argv,
                cwd=None if self.cwd is None else str(self.cwd),
                env=self.env,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            safe_args = redact(argv, self.secret_values)
            raise DeploymentError(
                "DISCOVERY_COMMAND_TIMEOUT",
                "discovery command timed out",
                next_action="review_host_command",
                details={"args": safe_args, "timeout": int(timeout)},
            ) from None
        except OSError as error:
            safe_message = str(redact(str(error), self.secret_values))
            raise DeploymentError(
                "DISCOVERY_COMMAND_FAILED",
                f"discovery command could not start: {safe_message}",
                next_action="install_or_fix_host_command",
                details={"args": redact(argv, self.secret_values)},
            ) from None

        return CommandResult(
            args=tuple(argv),
            returncode=int(completed.returncode),
            stdout=str(redact(completed.stdout or "", self.secret_values)),
            stderr=str(redact(completed.stderr or "", self.secret_values)),
        )
