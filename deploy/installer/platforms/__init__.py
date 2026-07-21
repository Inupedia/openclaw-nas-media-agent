"""Platform capability facts used by discovery and planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformFacts:
    kind: str
    confidence: float
    architecture: str
    kernel: str
    supports_compose_v2: bool
    supports_posix_acl: bool | None
    filesystem_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "confidence": self.confidence,
            "architecture": self.architecture,
            "kernel": self.kernel,
            "supportsComposeV2": self.supports_compose_v2,
            "supportsPosixAcl": self.supports_posix_acl,
            "filesystemTypes": list(self.filesystem_types),
        }
