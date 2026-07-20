"""Command parser and dispatch for the deterministic deployer."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TextIO

from .errors import DeploymentError
from .output import emit, result_payload


class CliUsageError(ValueError):
    """Raised instead of allowing argparse to print prose or exit."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError("invalid command or arguments")


def build_parser() -> SafeArgumentParser:
    parser = SafeArgumentParser(
        prog="openclaw-media-deploy",
        add_help=False,
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument("--help", action="store_true", dest="show_help")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", add_help=False)
    init.add_argument("--non-interactive", action="store_true")
    init.add_argument("--config-source")

    subparsers.add_parser("discover", add_help=False)
    subparsers.add_parser("plan", add_help=False)

    apply_parser = subparsers.add_parser("apply", add_help=False)
    apply_source = apply_parser.add_mutually_exclusive_group(required=True)
    apply_source.add_argument("--plan-id")
    apply_source.add_argument("--resume", dest="deployment_id")
    apply_parser.add_argument("--confirmed", action="store_true")

    verify = subparsers.add_parser("verify", add_help=False)
    verify.add_argument("--level", choices=("safe", "full"), default="safe")
    verify.add_argument("--confirmed", action="store_true")

    rollback = subparsers.add_parser("rollback", add_help=False)
    rollback.add_argument("--deployment-id", required=True)
    rollback.add_argument("--confirmed", action="store_true")

    versions = subparsers.add_parser("versions", add_help=False)
    versions_subparsers = versions.add_subparsers(dest="versions_command", required=True)
    versions_subparsers.add_parser("check", add_help=False)

    return parser


def _help_payload() -> dict[str, object]:
    return result_payload(
        ok=True,
        status="ready",
        next_action="none",
        data={
            "program": "openclaw-media-deploy",
            "commands": [
                "init",
                "discover",
                "plan",
                "apply",
                "verify",
                "rollback",
                "versions check",
            ],
        },
    )


def _pending_payload(args: argparse.Namespace) -> dict[str, object]:
    command = str(args.command)
    if command == "versions":
        command = f"versions.{args.versions_command}"
    return result_payload(
        ok=False,
        status="manual_action_required",
        next_action="implementation_pending",
        data={"command": command},
        warnings=[
            {
                "code": "IMPLEMENTATION_PENDING",
                "message": "This deployment command is defined but not implemented yet.",
            }
        ],
    )


def _error_payload(error: DeploymentError) -> dict[str, object]:
    return result_payload(
        ok=False,
        status=error.status,
        next_action=error.next_action,
        errors=[
            {
                "code": error.code,
                "message": str(error),
                "severity": error.severity,
                "details": error.details,
            }
        ],
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    stream: TextIO | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    target = sys.stdout if stream is None else stream
    try:
        args = build_parser().parse_args(arguments)
        if getattr(args, "show_help", False) or not getattr(args, "command", None):
            emit(_help_payload(), target)
            return 0
        emit(_pending_payload(args), target)
        return 2
    except CliUsageError:
        error = DeploymentError(
            "INVALID_ARGUMENTS",
            "invalid command or arguments",
            next_action="review_command",
        )
        emit(_error_payload(error), target)
        return 2
    except DeploymentError as error:
        emit(_error_payload(error), target)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
