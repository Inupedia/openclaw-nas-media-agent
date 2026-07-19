"""Ensure documented mediactl examples parse with the real CLI."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from resource_agent import CliUsageError, parse_args


DOCS = [
    ROOT / "README.md",
    ROOT / "SKILL.md",
    *sorted((ROOT / "references").glob("*.md")),
]

PLACEHOLDERS = {
    "CANDIDATE_ID": "candidate-1",
    "NODE_ID": "node-1",
    "PLAN_ID": "plan-1",
    "TASK_ID": "rd-1",
    "RECOVERY_PLAN_ID": "plan-1",
    "QUERY": "牧神记",
    "作品名": "牧神记",
}


def _tokenize_line(line: str) -> list[str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "..." in line or "…" in line:
        return None
    # Keep only mediactl invocations.
    match = re.match(
        r"^(?:\{baseDir\}/bin/mediactl|bin/mediactl|mediactl)\s+(.+)$",
        line,
    )
    if not match:
        return None
    raw = match.group(1).strip()
    # Drop trailing comments.
    raw = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
    # Drop optional-bracket markers used in docs.
    raw = raw.replace("[", "").replace("]", "")
    if not raw or raw.startswith("|"):
        return None

    parts: list[str] = []
    token = ""
    in_quote = False
    for ch in raw:
        if ch in "\"'":
            in_quote = not in_quote
            continue
        if ch.isspace() and not in_quote:
            if token:
                parts.append(token)
                token = ""
            continue
        token += ch
    if token:
        parts.append(token)

    mapped: list[str] = []
    for part in parts:
        key = part.upper() if part.isascii() else part
        if part in PLACEHOLDERS:
            mapped.append(PLACEHOLDERS[part])
        elif key in PLACEHOLDERS:
            mapped.append(PLACEHOLDERS[key])
        else:
            mapped.append(part)
    return mapped or None


class DocCommandContractTests(unittest.TestCase):
    def test_documented_mediactl_commands_parse(self):
        fence = re.compile(r"```(?:bash|text|sh)?\n(.*?)```", re.S)
        seen: list[tuple[str, list[str]]] = []
        for path in DOCS:
            text = path.read_text(encoding="utf-8")
            for block in fence.findall(text):
                for line in block.splitlines():
                    mapped = _tokenize_line(line)
                    if not mapped:
                        continue
                    # Skip bare execute without --confirmed (forbidden in docs).
                    if mapped[:1] == ["execute"] and "--confirmed" not in mapped:
                        self.fail(
                            f"{path.name}: execute without --confirmed: {mapped}"
                        )
                    seen.append((path.name, mapped))
                    try:
                        parse_args(mapped)
                    except CliUsageError as error:
                        self.fail(f"{path.name}: {mapped} -> {error}")
                    except SystemExit as error:
                        self.fail(f"{path.name}: {mapped} -> SystemExit {error}")
        self.assertGreaterEqual(len(seen), 8)


if __name__ == "__main__":
    unittest.main()
