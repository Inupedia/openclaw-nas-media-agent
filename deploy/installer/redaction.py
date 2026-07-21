"""Recursive value redaction for plans, reports and errors."""

from __future__ import annotations

from collections.abc import Collection, Mapping


def _secrets(values: Collection[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value)}, key=len, reverse=True))


def _redact_text(value: str, secrets: tuple[str, ...]) -> str:
    result = value
    for secret in secrets:
        result = result.replace(secret, "***")
    return result


def redact(value: object, secret_values: Collection[str]) -> object:
    """Return a redacted copy without mutating the supplied value."""

    secrets = _secrets(secret_values)

    def visit(item: object) -> object:
        if isinstance(item, str):
            return _redact_text(item, secrets)
        if isinstance(item, bytes):
            text = item.decode("utf-8", errors="replace")
            return _redact_text(text, secrets)
        if isinstance(item, Mapping):
            return {
                _redact_text(str(key), secrets): visit(nested)
                for key, nested in item.items()
            }
        if isinstance(item, list):
            return [visit(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(visit(nested) for nested in item)
        if isinstance(item, set):
            return [visit(nested) for nested in sorted(item, key=repr)]
        return item

    return visit(value)
