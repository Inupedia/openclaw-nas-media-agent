"""Single-document JSON output contract for deployment commands."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO


def result_payload(
    *,
    ok: bool,
    status: str,
    next_action: str,
    data: Mapping[str, object] | None = None,
    warnings: Sequence[object] | None = None,
    errors: Sequence[object] | None = None,
) -> dict[str, object]:
    """Build the stable top-level deployment result envelope."""

    return {
        "ok": bool(ok),
        "status": str(status),
        "nextAction": str(next_action),
        "data": dict(data or {}),
        "warnings": list(warnings or []),
        "errors": list(errors or []),
    }


def emit(payload: Mapping[str, object], stream: TextIO | None = None) -> None:
    """Write exactly one compact JSON document followed by one newline."""

    target = sys.stdout if stream is None else stream
    target.write(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
