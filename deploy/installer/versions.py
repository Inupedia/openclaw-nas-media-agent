"""Immutable container image version lock and maintainer resolver."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import yaml

from .command import CommandRunner
from .errors import DeploymentError

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE = re.compile(r"^(?P<repository>[A-Za-z0-9._:/-]+)@(?P<digest>sha256:[0-9a-f]{64})$")
_REQUIRED_COMPONENTS = frozenset({"qas", "pansou", "aria2", "sing_box", "playwright"})
_ALLOWED_ENTRY_KEYS = frozenset({"image", "adapter", "release"})


def _security_error(code: str, message: str, *, details=None) -> DeploymentError:
    return DeploymentError(
        code,
        message,
        severity="security_block",
        next_action="fix_version_lock",
        details=details or {},
    )


def _repository_has_tag(repository: str) -> bool:
    leaf = repository.rsplit("/", 1)[-1]
    return ":" in leaf


@dataclass(frozen=True)
class ImageVersion:
    image: str
    adapter: str
    release: str

    def to_dict(self) -> dict[str, str]:
        return {
            "image": self.image,
            "adapter": self.adapter,
            "release": self.release,
        }


@dataclass(frozen=True)
class VersionLock:
    components: Mapping[str, ImageVersion]
    schema_version: int = 1
    resolved_at: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "VersionLock":
        if not isinstance(value, Mapping):
            raise DeploymentError("VERSION_LOCK_INVALID", "version lock must be an object")
        if "components" in value:
            schema_version = int(value.get("schema_version", 0))
            if schema_version != 1:
                raise DeploymentError(
                    "VERSION_SCHEMA_UNSUPPORTED",
                    "version lock schema_version must be 1",
                    next_action="update_deployer",
                )
            raw_components = value.get("components")
            resolved_at = str(value.get("resolved_at") or "")
        else:
            schema_version = 1
            raw_components = value
            resolved_at = ""
        if not isinstance(raw_components, Mapping) or not raw_components:
            raise DeploymentError(
                "VERSION_LOCK_INVALID",
                "version lock components must be a non-empty object",
            )

        parsed: dict[str, ImageVersion] = {}
        for raw_name, raw_entry in raw_components.items():
            name = str(raw_name)
            if not isinstance(raw_entry, Mapping):
                raise DeploymentError(
                    "VERSION_ENTRY_INVALID",
                    f"version entry for {name} must be an object",
                )
            unknown = sorted(set(str(key) for key in raw_entry) - _ALLOWED_ENTRY_KEYS)
            if unknown:
                raise DeploymentError(
                    "VERSION_ENTRY_INVALID",
                    f"version entry for {name} contains unsupported fields",
                    details={"fields": unknown},
                )
            image = str(raw_entry.get("image") or "").strip()
            adapter = str(raw_entry.get("adapter") or "").strip()
            release = str(raw_entry.get("release") or "").strip()
            if ":latest" in image.casefold() or image.casefold().endswith("/latest"):
                raise _security_error(
                    "MUTABLE_IMAGE_REFERENCE",
                    f"mutable image reference is forbidden for {name}",
                    details={"component": name},
                )
            match = _IMAGE.fullmatch(image)
            if match is None:
                code = "IMAGE_DIGEST_REQUIRED" if "@sha256:" not in image else "IMAGE_DIGEST_INVALID"
                raise _security_error(
                    code,
                    f"component {name} must use repository@sha256:digest",
                    details={"component": name},
                )
            repository = match.group("repository")
            if _repository_has_tag(repository):
                raise _security_error(
                    "IMAGE_TAG_FORBIDDEN",
                    f"component {name} image must omit a tag when pinned by digest",
                    details={"component": name},
                )
            if not adapter:
                raise DeploymentError(
                    "VERSION_ADAPTER_MISSING",
                    f"component {name} requires an adapter name",
                    next_action="fix_version_lock",
                )
            parsed[name] = ImageVersion(
                image=image,
                adapter=adapter,
                release=release,
            )
        return cls(
            components=MappingProxyType(dict(sorted(parsed.items()))),
            schema_version=schema_version,
            resolved_at=resolved_at,
        )

    @classmethod
    def load(cls, path: Path) -> "VersionLock":
        target = Path(path)
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        except OSError as error:
            raise DeploymentError(
                "VERSION_LOCK_MISSING",
                f"version lock is unavailable: {target}",
                next_action="restore_version_lock",
                details={"reason": type(error).__name__},
            ) from None
        except yaml.YAMLError as error:
            raise DeploymentError(
                "VERSION_LOCK_INVALID",
                "version lock is not valid YAML",
                next_action="fix_version_lock",
                details={"reason": type(error).__name__},
            ) from None
        lock = cls.from_dict(raw)
        missing = sorted(_REQUIRED_COMPONENTS - set(lock.components))
        if missing:
            raise DeploymentError(
                "VERSION_COMPONENT_MISSING",
                "version lock is missing required components",
                next_action="resolve_required_images",
                details={"components": missing},
            )
        return lock

    def image(self, component: str) -> str:
        try:
            return self.components[str(component)].image
        except KeyError:
            raise DeploymentError(
                "VERSION_COMPONENT_UNKNOWN",
                f"unknown version-locked component: {component}",
                next_action="fix_component_name",
            ) from None

    def adapter(self, component: str) -> str:
        try:
            return self.components[str(component)].adapter
        except KeyError:
            raise DeploymentError(
                "VERSION_COMPONENT_UNKNOWN",
                f"unknown version-locked component: {component}",
                next_action="fix_component_name",
            ) from None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "resolved_at": self.resolved_at,
            "components": {
                name: entry.to_dict()
                for name, entry in self.components.items()
            },
        }


def _repository_without_tag(image: str) -> str:
    value = str(image).strip().split("@", 1)[0]
    slash = value.rfind("/")
    colon = value.rfind(":")
    if colon > slash:
        value = value[:colon]
    if not value:
        raise DeploymentError(
            "IMAGE_REFERENCE_INVALID",
            "image repository is empty",
            next_action="fix_image_reference",
        )
    return value


def resolve_image_digest(image: str, runner: CommandRunner) -> str:
    """Resolve a maintainer-supplied test tag to repository@manifest-digest."""

    reference = str(image).strip()
    if not reference:
        raise DeploymentError("IMAGE_REFERENCE_INVALID", "image reference is empty")
    result = runner.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            reference,
            "--format",
            "{{.Manifest.Digest}}",
        ],
        timeout=120,
    )
    digest = result.stdout.strip()
    if result.returncode != 0:
        raise DeploymentError(
            "IMAGE_DIGEST_RESOLUTION_FAILED",
            "unable to resolve immutable image digest",
            next_action="check_registry_and_image_tag",
            details={"image": reference, "returncode": result.returncode},
        )
    if _DIGEST.fullmatch(digest) is None:
        raise DeploymentError(
            "IMAGE_DIGEST_INVALID",
            "registry returned an invalid image digest",
            next_action="check_registry_response",
            details={"image": reference},
        )
    return f"{_repository_without_tag(reference)}@{digest}"
