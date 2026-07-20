"""Safe and explicitly confirmed full mediactl business verification."""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import DeploymentConfig
from .errors import DeploymentError
from .models import (
    ComponentResult,
    ComponentStatus,
    DeploymentStatus,
    Severity,
    VerificationResult,
)
from .secrets import SecretStore


_ALLOWED_MEDIACTL_KEYS = frozenset(
    {"ok", "terminal", "nextAction", "data", "warnings", "errors", "error"}
)


@dataclass(frozen=True)
class BusinessVerificationContext:
    mediactl_path: Path
    config: DeploymentConfig
    secrets: SecretStore
    executor: Callable[[Sequence[str]], object]
    confirmed: bool = False


def run_mediactl(
    args: Sequence[str],
    context: BusinessVerificationContext,
) -> dict[str, object]:
    """Run one fixed mediactl argv and accept exactly one JSON document."""
    argv = [str(context.mediactl_path), *[str(item) for item in args]]
    completed = context.executor(argv)
    returncode = int(getattr(completed, "returncode", 0))
    stdout = str(getattr(completed, "stdout", ""))
    stderr = str(getattr(completed, "stderr", ""))
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(stdout.lstrip())
    except json.JSONDecodeError:
        raise DeploymentError(
            "MEDIACTL_OUTPUT_INVALID",
            "mediactl did not emit a JSON document",
            next_action="inspect_mediactl_output",
            details={"returncode": returncode, "stderrType": bool(stderr)},
        ) from None
    leading = len(stdout) - len(stdout.lstrip())
    if stdout[leading + end :].strip():
        raise DeploymentError(
            "MEDIACTL_OUTPUT_TRAILING_PROSE",
            "mediactl emitted terminal prose after JSON",
            severity="security_block",
            next_action="fix_mediactl_output",
        )
    if not isinstance(value, dict):
        raise DeploymentError(
            "MEDIACTL_OUTPUT_INVALID",
            "mediactl JSON result must be an object",
            next_action="fix_mediactl_output",
        )
    result = {
        str(key): item
        for key, item in value.items()
        if key in _ALLOWED_MEDIACTL_KEYS
    }
    if returncode != 0 or result.get("ok") is False:
        raise DeploymentError(
            "MEDIACTL_COMMAND_FAILED",
            "mediactl verification command failed",
            next_action=str(
                result.get("nextAction") or "inspect_mediactl_error"
            ),
            details={"argv": list(args), "returncode": returncode},
        )
    return result


def _first_mapping(
    value: object,
    keys: Sequence[str],
) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        if any(key in value for key in keys):
            return value
        for child in value.values():
            found = _first_mapping(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _first_mapping(child, keys)
            if found is not None:
                return found
    return None


def _identifier(payload: Mapping[str, object], *keys: str) -> str:
    found = _first_mapping(payload.get("data"), keys)
    if found is None:
        return ""
    for key in keys:
        value = found.get(key)
        if value:
            return str(value)
    return ""


def _select_node(payload: Mapping[str, object], max_bytes: int) -> str:
    candidates: list[tuple[int, str]] = []

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            node = (
                value.get("nodeId")
                or value.get("node_id")
                or value.get("id")
            )
            raw_size = (
                value.get("size")
                or value.get("bytes")
                or value.get("totalBytes")
            )
            try:
                size = int(raw_size or 0)
            except (TypeError, ValueError):
                size = 0
            if node and 0 < size <= int(max_bytes):
                candidates.append((size, str(node)))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload.get("data"))
    return min(candidates)[1] if candidates else ""


def verify_safe(context: BusinessVerificationContext) -> VerificationResult:
    commands: list[list[str]] = []

    def call(args: list[str]) -> dict[str, object]:
        commands.append(args)
        return run_mediactl(args, context)

    try:
        call(["check-ready"])
        search = call(
            [
                "search",
                context.config.verification.safe_query,
                "--media-type",
                "other",
            ]
        )
        candidate = _identifier(
            search,
            "candidateId",
            "candidate_id",
            "id",
        )
        if not candidate:
            raise DeploymentError(
                "SAFE_VERIFICATION_CANDIDATE_MISSING",
                "safe verification search returned no candidate",
                status="manual_action_required",
                next_action="provide_legal_test_share_url",
            )
        call(["preview", candidate])
        tree = call(["tree", candidate])
        node = _select_node(
            tree,
            context.config.verification.max_test_bytes,
        )
        if not node:
            raise DeploymentError(
                "SAFE_VERIFICATION_NODE_MISSING",
                "safe verification found no bounded test node",
                status="manual_action_required",
                next_action="choose_legal_test_candidate",
            )
        call(
            [
                "plan",
                "download",
                candidate,
                "--node",
                node,
                "--media-type",
                "other",
            ]
        )
    except DeploymentError as error:
        status = (
            DeploymentStatus.MANUAL_ACTION_REQUIRED
            if error.status == "manual_action_required"
            else DeploymentStatus.FAILED
        )
        return VerificationResult(
            level="safe",
            status=status,
            components=(
                ComponentResult(
                    "business_safe",
                    ComponentStatus.MANUAL_ACTION_REQUIRED
                    if status == DeploymentStatus.MANUAL_ACTION_REQUIRED
                    else ComponentStatus.FAILED,
                    True,
                    True,
                    error.next_action,
                    details={
                        "commands": commands,
                        "errorCode": error.code,
                    },
                    severity=(
                        None
                        if status == DeploymentStatus.MANUAL_ACTION_REQUIRED
                        else Severity.BLOCKING
                    ),
                ),
            ),
            next_action=error.next_action,
        )
    return VerificationResult(
        level="safe",
        status=DeploymentStatus.READY,
        components=(
            ComponentResult(
                "business_safe",
                ComponentStatus.READY,
                True,
                True,
                details={
                    "commands": commands,
                    "executedDownload": False,
                },
            ),
        ),
    )


def verify_full(context: BusinessVerificationContext) -> VerificationResult:
    settings = context.config.verification
    missing: list[str] = []
    if not settings.allow_real_download:
        missing.append("allow_real_download")
    if not context.confirmed:
        missing.append("confirmed")
    share_url = ""
    try:
        share_url = context.secrets.read(
            settings.full_test_share_url_secret
        ).strip()
    except DeploymentError:
        missing.append("full_test_share_url")
    if not share_url:
        missing.append("full_test_share_url")
    if missing:
        return VerificationResult(
            level="full",
            status=DeploymentStatus.MANUAL_ACTION_REQUIRED,
            components=(
                ComponentResult(
                    "business_full",
                    ComponentStatus.MANUAL_ACTION_REQUIRED,
                    True,
                    True,
                    "confirm_full_verification",
                    details={"missingGates": sorted(set(missing))},
                ),
            ),
            next_action="confirm_full_verification",
        )

    commands: list[list[str]] = []

    def call(args: list[str]) -> dict[str, object]:
        commands.append(args)
        return run_mediactl(args, context)

    try:
        imported = call(
            ["import-url", share_url, "--media-type", "other"]
        )
        candidate = _identifier(
            imported,
            "candidateId",
            "candidate_id",
            "id",
        )
        if not candidate:
            raise DeploymentError(
                "FULL_VERIFICATION_CANDIDATE_MISSING",
                "test share URL did not create a candidate",
                next_action="replace_full_test_share_url",
            )
        tree = call(["tree", candidate])
        node = _select_node(tree, settings.max_test_bytes)
        if not node:
            raise DeploymentError(
                "FULL_VERIFICATION_NODE_TOO_LARGE",
                "no test node is within the configured size limit",
                status="manual_action_required",
                next_action="choose_smaller_test_share",
            )
        planned = call(
            [
                "plan",
                "download",
                candidate,
                "--node",
                node,
                "--media-type",
                "other",
            ]
        )
        plan_id = _identifier(planned, "planId", "plan_id", "id")
        executed = call(["execute", plan_id, "--confirmed"])
        task_id = _identifier(executed, "taskId", "task_id", "id")
        call(["downloads", "show", task_id])
        call(["downloads", "validate", task_id])
        call(["organize", "plan", task_id])
    except DeploymentError as error:
        return VerificationResult(
            level="full",
            status=(
                DeploymentStatus.MANUAL_ACTION_REQUIRED
                if error.status == "manual_action_required"
                else DeploymentStatus.FAILED
            ),
            components=(
                ComponentResult(
                    "business_full",
                    ComponentStatus.MANUAL_ACTION_REQUIRED
                    if error.status == "manual_action_required"
                    else ComponentStatus.FAILED,
                    True,
                    True,
                    error.next_action,
                    details={
                        "commands": commands,
                        "errorCode": error.code,
                    },
                    severity=(
                        None
                        if error.status == "manual_action_required"
                        else Severity.BLOCKING
                    ),
                ),
            ),
            next_action=error.next_action,
        )
    return VerificationResult(
        level="full",
        status=DeploymentStatus.READY,
        components=(
            ComponentResult(
                "business_full",
                ComponentStatus.READY,
                True,
                True,
                details={
                    "commands": commands,
                    "organizeExecuted": False,
                    "nextAction": "stop",
                },
            ),
        ),
        next_action="stop",
    )
