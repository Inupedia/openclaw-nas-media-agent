"""Standard Linux platform normalization."""

from __future__ import annotations

from . import PlatformFacts


def normalize_architecture(value: str) -> str:
    raw = str(value).strip().casefold()
    if raw in {"x86_64", "amd64"}:
        return "amd64"
    if raw in {"aarch64", "arm64"}:
        return "arm64"
    return raw or "unknown"


def linux_facts(
    *,
    kernel: str,
    architecture: str,
    compose_available: bool,
    filesystem_types: tuple[str, ...] = (),
) -> PlatformFacts:
    return PlatformFacts(
        kind="linux",
        confidence=0.70,
        architecture=normalize_architecture(architecture),
        kernel=str(kernel).strip() or "unknown",
        supports_compose_v2=bool(compose_available),
        supports_posix_acl=None,
        filesystem_types=tuple(sorted(set(filesystem_types))),
    )
