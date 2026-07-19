"""Load and enforce the machine-readable mediactl command contract (JSON)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ALLOWED_CONFIRMATIONS = {"none", "required"}
ALLOWED_SERVICES = {"qas", "aria2"}
ALLOWED_EXTERNAL = {
    False,
    "remote_query",
    "download",
    "download_control",
    "probe",
}


class ContractError(RuntimeError):
    """Raised when an invocation violates config/commands.json."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "CONTRACT_ERROR",
        next_action: str = "review_error",
    ):
        super().__init__(message)
        self.code = code
        self.next_action = next_action


def contract_path() -> Path:
    base = Path(__file__).resolve().parents[1] / "config"
    json_path = base / "commands.json"
    if json_path.is_file():
        return json_path
    # Legacy fallback during migration.
    return base / "commands.yaml"


def load_command_contract(path: Path | None = None) -> dict[str, Any]:
    target = path or contract_path()
    text = target.read_text(encoding="utf-8")
    if target.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        raise ContractError(
            "commands.yaml is no longer supported; use config/commands.json",
            code="CONTRACT_FORMAT_ERROR",
            next_action="review_error",
        )
    validate_command_contract(data)
    return data


def validate_command_contract(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ContractError("contract root must be an object")
    commands = data.get("commands")
    if not isinstance(commands, dict) or not commands:
        raise ContractError("commands.json missing commands mapping")
    known_fields = {
        "confirmation",
        "requires_services",
        "requires_env",
        "reads",
        "writes",
        "external_mutation",
        "media_mutation",
        "credential_access",
        "preconditions",
        "notes",
    }
    for key, spec in commands.items():
        if not isinstance(spec, dict):
            raise ContractError(f"{key}: spec must be an object")
        unknown = set(spec) - known_fields
        if unknown:
            raise ContractError(f"{key}: unknown fields {sorted(unknown)}")
        confirmation = spec.get("confirmation", "none")
        if confirmation not in ALLOWED_CONFIRMATIONS:
            raise ContractError(f"{key}: invalid confirmation {confirmation!r}")
        services = spec.get("requires_services") or []
        if not isinstance(services, list) or any(
            item not in ALLOWED_SERVICES for item in services
        ):
            raise ContractError(f"{key}: invalid requires_services")
        external = spec.get("external_mutation", False)
        if external not in ALLOWED_EXTERNAL:
            raise ContractError(f"{key}: invalid external_mutation")
        if "media_mutation" in spec and not isinstance(spec["media_mutation"], bool):
            raise ContractError(f"{key}: media_mutation must be bool")
        pre = spec.get("preconditions")
        if pre is not None and not isinstance(pre, dict):
            raise ContractError(f"{key}: preconditions must be an object")


def command_key(args) -> str:
    command = getattr(args, "command", None)
    if command == "library":
        return f"library.{getattr(args, 'library_command', 'lookup')}"
    if command == "plan":
        return f"plan.{getattr(args, 'plan_command', 'download')}"
    if command == "share":
        return f"share.{getattr(args, 'share_command', 'open')}"
    if command == "organize":
        return f"organize.{getattr(args, 'organize_command')}"
    if command == "downloads":
        sub = getattr(args, "download_command", None)
        if sub == "recover":
            return f"downloads.recover.{getattr(args, 'recover_command')}"
        return f"downloads.{sub}"
    return str(command)


def get_command_spec(key: str, contract: dict[str, Any] | None = None) -> dict[str, Any]:
    data = contract or load_command_contract()
    spec = data["commands"].get(key)
    if not isinstance(spec, dict):
        raise ContractError(
            f"unknown command contract key: {key}",
            code="UNKNOWN_COMMAND",
            next_action="use_mediactl_help",
        )
    return spec


def env_flag_enabled(name: str) -> bool:
    raw = str(os.environ.get(name, "")).strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def enforce_invocation(args, *, contract: dict[str, Any] | None = None) -> str:
    """Enforce confirmation and env gates. Returns command key."""
    data = contract or load_command_contract()
    key = command_key(args)
    spec = get_command_spec(key, data)
    if spec.get("confirmation") == "required" and not bool(
        getattr(args, "confirmed", False)
    ):
        raise ContractError(
            f"{key} requires --confirmed",
            code="CONFIRMATION_REQUIRED",
            next_action="confirm_action",
        )
    for name in spec.get("requires_env") or []:
        if name == "QUARK_RECOVERY_ENABLED":
            if not env_flag_enabled(name):
                raise ContractError(
                    "Quark recovery is disabled (QUARK_RECOVERY_ENABLED)",
                    code="RECOVERY_DISABLED",
                    next_action="enable_quark_recovery",
                )
        elif not os.environ.get(name):
            raise ContractError(
                f"missing environment: {name}",
                code="DEPENDENCY_MISSING",
                next_action="configure_dependency",
            )
    return key


def required_services(key: str, contract: dict[str, Any] | None = None) -> set[str]:
    spec = get_command_spec(key, contract)
    services = spec.get("requires_services") or []
    return {str(item) for item in services}


def recovery_preconditions(contract: dict[str, Any] | None = None) -> dict[str, Any]:
    data = contract or load_command_contract()
    spec = get_command_spec("downloads.recover.execute", data)
    pre = spec.get("preconditions") or {}
    return {
        "taskStates": set(pre.get("taskStates") or ["error", "partial_failed"]),
        "errorCodesExact": set(str(x) for x in (pre.get("errorCodesExact") or ["16"])),
        "stagingPayload": bool(pre.get("stagingPayload", False)),
    }


def quark_retry_policy(contract: dict[str, Any] | None = None) -> dict[str, Any]:
    data = contract or load_command_contract()
    policy = ((data.get("retryPolicy") or {}).get("quarkRecovery") or {})
    return {
        "defaultMaxAttempts": int(policy.get("defaultMaxAttempts") or 2),
        "maxAllowedAttempts": int(policy.get("maxAllowedAttempts") or 5),
        "defaultCooldownSeconds": int(policy.get("defaultCooldownSeconds") or 300),
        "maxAttemptsEnv": str(
            policy.get("maxAttemptsEnv") or "QUARK_RECOVERY_MAX_ATTEMPTS"
        ),
        "cooldownEnv": str(
            policy.get("cooldownEnv") or "QUARK_RECOVERY_COOLDOWN_SECONDS"
        ),
    }


def leaf_command_keys_from_parser(parse_args_fn) -> set[str]:
    """Best-effort enumeration via dry argv samples (for contract completeness tests)."""
    samples = [
        ["check-ready"],
        ["library", "lookup", "x"],
        ["search", "x"],
        ["share", "open", "https://pan.quark.cn/s/x"],
        ["import-url", "https://pan.quark.cn/s/x"],
        ["preview", "c1"],
        ["tree", "c1"],
        ["plan", "download", "c1", "--node", "n1"],
        ["execute", "p1", "--confirmed"],
        ["downloads", "list"],
        ["downloads", "show", "t1"],
        ["downloads", "pause", "t1"],
        ["downloads", "resume", "t1"],
        ["downloads", "cancel", "t1"],
        ["downloads", "validate", "t1"],
        ["downloads", "recover", "plan", "t1"],
        ["downloads", "recover", "execute", "p1", "--confirmed"],
        ["organize", "plan", "t1"],
        ["organize", "execute", "p1", "--confirmed"],
    ]
    keys = set()
    for argv in samples:
        keys.add(command_key(parse_args_fn(argv)))
    return keys
