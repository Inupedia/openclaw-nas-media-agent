"""Typed errors for the deployment command surface."""

from __future__ import annotations

from collections.abc import Mapping


class DeploymentError(RuntimeError):
    """A safe, machine-readable deployment failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: str = "failed",
        next_action: str = "review_error",
        severity: str = "blocking",
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.next_action = next_action
        self.severity = severity
        self.details = dict(details or {})
