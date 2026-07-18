import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from output_contract import safe_project


CREDENTIAL_MARKER = re.compile(
    r"(?i)(?:authorization|cookie|set-cookie|token|secret)\s*[:=]"
)


class SanitizerError(RuntimeError):
    pass


@dataclass(frozen=True)
class SanitizeReport:
    total_records: int
    redacted_records: int


def sanitize_jsonl(source: Path, destination: Path) -> SanitizeReport:
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve(strict=False)
    if source == destination:
        raise SanitizerError("destination must differ from source")
    if not destination.parent.is_dir():
        raise SanitizerError("destination parent is unavailable")

    total = 0
    redacted = 0
    source_mode = stat.S_IMODE(source.stat().st_mode)
    with source.open("r", encoding="utf-8", errors="replace") as reader:
        with destination.open("x", encoding="utf-8", newline="\n") as writer:
            for raw_line in reader:
                total += 1
                line = raw_line.rstrip("\r\n")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    if CREDENTIAL_MARKER.search(line):
                        sanitized = {
                            "type": "securityRedaction",
                            "content": "[REDACTED SENSITIVE TOOL RESULT]",
                        }
                        redacted += 1
                        writer.write(
                            json.dumps(
                                sanitized,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            + "\n"
                        )
                    else:
                        writer.write(line + "\n")
                    continue

                sanitized = safe_project(record)
                if sanitized != record:
                    redacted += 1
                writer.write(
                    json.dumps(
                        sanitized,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            writer.flush()
            os.fsync(writer.fileno())
    os.chmod(destination, source_mode)
    return SanitizeReport(
        total_records=total,
        redacted_records=redacted,
    )
