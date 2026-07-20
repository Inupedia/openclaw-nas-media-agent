"""UGREEN UGOS detection without assuming filesystem write behavior."""

from __future__ import annotations

from . import PlatformFacts
from .linux import normalize_architecture


def is_ugos(os_release: str) -> bool:
    value = str(os_release).casefold()
    return "ugreen" in value or "ugos" in value


def ugos_facts(
    *,
    kernel: str,
    architecture: str,
    compose_available: bool,
    filesystem_types: tuple[str, ...] = (),
) -> PlatformFacts:
    return PlatformFacts(
        kind="ugos",
        confidence=0.95,
        architecture=normalize_architecture(architecture),
        kernel=str(kernel).strip() or "unknown",
        supports_compose_v2=bool(compose_available),
        supports_posix_acl=None,
        filesystem_types=tuple(sorted(set(filesystem_types))),
    )
