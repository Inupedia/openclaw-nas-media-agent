"""Load and enforce the machine-readable mediactl command contract."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class ContractError(RuntimeError):
    """Raised when an invocation violates config/commands.yaml."""


def contract_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "commands.yaml"


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in inner.split(",")]
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    if text in {"true", "false"}:
        return text == "true"
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    return text


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the subset used by config/commands.yaml (stdlib only)."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if "\t" in raw_line[:indent]:
            raise ContractError(f"tabs not allowed in commands.yaml:{lineno}")
        line = raw_line.strip()
        if ":" not in line:
            raise ContractError(f"invalid mapping at commands.yaml:{lineno}")
        key, _, remainder = line.partition(":")
        key = key.strip()
        remainder = remainder.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if remainder == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(remainder)
    return root


def load_command_contract(path: Path | None = None) -> dict[str, Any]:
    target = path or contract_path()
    data = parse_simple_yaml(target.read_text(encoding="utf-8"))
    commands = data.get("commands")
    if not isinstance(commands, dict) or not commands:
        raise ContractError("commands.yaml missing commands mapping")
    return data


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
        raise ContractError(f"unknown command contract key: {key}")
    return spec


def env_flag_enabled(name: str) -> bool:
    raw = str(os.environ.get(name, "")).strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def enforce_invocation(args, *, contract: dict[str, Any] | None = None) -> str:
    """Enforce confirmation and env gates from the contract. Returns command key."""
    data = contract or load_command_contract()
    key = command_key(args)
    spec = get_command_spec(key, data)
    if spec.get("confirmation") == "required" and not bool(
        getattr(args, "confirmed", False)
    ):
        raise ContractError(f"{key} requires --confirmed")
    for name in spec.get("requires_env") or []:
        if name == "QUARK_RECOVERY_ENABLED":
            if not env_flag_enabled(name):
                raise ContractError(
                    "Quark recovery is disabled (QUARK_RECOVERY_ENABLED)"
                )
        elif not os.environ.get(name):
            raise ContractError(f"missing environment: {name}")
    return key


def required_services(key: str, contract: dict[str, Any] | None = None) -> set[str]:
    spec = get_command_spec(key, contract)
    services = spec.get("requires_services") or []
    if not isinstance(services, list):
        raise ContractError(f"{key}.requires_services must be a list")
    return {str(item) for item in services}
