#!/usr/bin/env python3
"""Standard-library bootstrap for the deterministic deployment runtime."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

BOOTSTRAP_ENV = "OPENCLAW_DEPLOY_BOOTSTRAPPED"
VENV_NAME = ".deploy-venv"
LOCK_RELATIVE_PATH = Path("deploy/requirements.lock")
LOCK_MARKER = ".requirements-lock.sha256"


def _private_runtime_python(project_root: Path) -> Path:
    if os.name == "nt":
        return project_root / VENV_NAME / "Scripts" / "python.exe"
    return project_root / VENV_NAME / "bin" / "python"


def resolve_runtime_python(project_root: Path) -> Path:
    """Prefer the private deployment runtime when it is executable."""

    candidate = _private_runtime_python(Path(project_root))
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return Path(sys.executable)


def should_bootstrap(environment: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    return str(env.get(BOOTSTRAP_ENV, "")).strip() != "1"


def build_runtime_argv(runtime_python: Path, argv: Sequence[str]) -> list[str]:
    return [str(runtime_python), "-m", "deploy.installer.cli", *list(argv)]


def _lock_digest(lock_path: Path) -> str:
    return hashlib.sha256(lock_path.read_bytes()).hexdigest()


def _run_checked(args: Sequence[str], *, cwd: Path) -> None:
    completed = subprocess.run(
        list(args),
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return
    message = (completed.stderr or completed.stdout or "command failed").strip()
    raise RuntimeError(message)


def ensure_runtime(project_root: Path) -> Path:
    """Create and synchronize the private deployment virtual environment."""

    root = Path(project_root).resolve()
    lock_path = root / LOCK_RELATIVE_PATH
    if not lock_path.is_file():
        raise RuntimeError(f"missing dependency lock: {lock_path}")

    runtime = _private_runtime_python(root)
    if not runtime.is_file():
        _run_checked(
            [sys.executable, "-m", "venv", str(root / VENV_NAME)],
            cwd=root,
        )
    if not runtime.is_file():
        raise RuntimeError("private deployment Python was not created")

    digest = _lock_digest(lock_path)
    marker = root / VENV_NAME / LOCK_MARKER
    current = ""
    try:
        current = marker.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if current != digest:
        _run_checked(
            [
                str(runtime),
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "-r",
                str(lock_path),
            ],
            cwd=root,
        )
        marker.write_text(digest + "\n", encoding="utf-8")
    return runtime


def _emit_bootstrap_failure(message: str) -> None:
    payload = {
        "ok": False,
        "status": "failed",
        "nextAction": "install_python_venv_or_dependencies",
        "data": {},
        "warnings": [],
        "errors": [
            {
                "code": "BOOTSTRAP_FAILED",
                "message": message,
                "severity": "blocking",
                "details": {},
            }
        ],
    }
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    project_root = Path(__file__).resolve().parents[1]
    if not should_bootstrap():
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        from deploy.installer.cli import main as installer_main

        return installer_main(arguments)

    try:
        runtime = ensure_runtime(project_root)
        environment = dict(os.environ)
        environment[BOOTSTRAP_ENV] = "1"
        os.chdir(project_root)
        os.execve(
            str(runtime),
            build_runtime_argv(runtime, arguments),
            environment,
        )
    except Exception as error:  # bootstrap must never leak a traceback
        _emit_bootstrap_failure(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
