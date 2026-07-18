import json
import re
from typing import Any


SCHEMA_VERSION = 1
FORBIDDEN_KEYS = {
    "authorization",
    "cookie",
    "headers",
    "qas_token",
    "aria2_rpc_secret",
    "set-cookie",
    "token",
}
INLINE_SECRET = re.compile(
    r"(?i)\b(authorization|cookie|set-cookie|token|secret)"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)


def _safe_string(value: str) -> Any:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return INLINE_SECRET.sub(r"\1=[REDACTED]", value)
    return safe_project(parsed)


def safe_project(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): safe_project(item)
            for key, item in value.items()
            if str(key).casefold() not in FORBIDDEN_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [safe_project(item) for item in value]
    if isinstance(value, str):
        return _safe_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def success(
    data: Any,
    *,
    terminal: bool = False,
    next_action: str = "none",
) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "ok": True,
        "terminal": bool(terminal),
        "nextAction": next_action,
        "data": safe_project(data),
        "error": None,
    }


def failure(code: str, message: str, *, next_action: str) -> dict:
    projected_message = safe_project(str(message))
    if not isinstance(projected_message, str):
        projected_message = "request failed"
    return {
        "schemaVersion": SCHEMA_VERSION,
        "ok": False,
        "terminal": True,
        "nextAction": next_action,
        "data": None,
        "error": {
            "code": str(code),
            "message": projected_message,
        },
    }
