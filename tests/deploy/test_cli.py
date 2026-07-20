import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deploy.cli import (
    BOOTSTRAP_ENV,
    build_runtime_argv,
    resolve_runtime_python,
    should_bootstrap,
)
from deploy.installer.cli import build_parser, main
from deploy.installer.errors import DeploymentError
from deploy.installer.output import emit, result_payload


class OutputContractTests(unittest.TestCase):
    def test_emit_writes_exactly_one_json_document(self):
        stream = io.StringIO()
        emit(result_payload(ok=True, status="ready", next_action="none"), stream)
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["status"], "ready")

    def test_deployment_error_preserves_machine_fields(self):
        error = DeploymentError(
            "EXAMPLE",
            "safe message",
            status="manual_action_required",
            next_action="provide_input",
            severity="warning",
            details={"field": "value"},
        )
        self.assertEqual(error.code, "EXAMPLE")
        self.assertEqual(error.status, "manual_action_required")
        self.assertEqual(error.next_action, "provide_input")
        self.assertEqual(error.severity, "warning")
        self.assertEqual(error.details, {"field": "value"})
        self.assertEqual(str(error), "safe message")


class LauncherTests(unittest.TestCase):
    def test_runtime_python_prefers_private_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / ".deploy-venv" / "bin" / "python"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("", encoding="utf-8")
            runtime.chmod(0o755)
            self.assertEqual(resolve_runtime_python(root), runtime)

    def test_bootstrap_is_skipped_after_reexec(self):
        self.assertFalse(should_bootstrap({BOOTSTRAP_ENV: "1"}))
        self.assertTrue(should_bootstrap({}))

    def test_runtime_argv_uses_module_entrypoint(self):
        runtime = Path("/tmp/runtime-python")
        self.assertEqual(
            build_runtime_argv(runtime, ["discover"]),
            [str(runtime), "-m", "deploy.installer.cli", "discover"],
        )


class ParserTests(unittest.TestCase):
    def test_parser_recognizes_all_planned_commands(self):
        parser = build_parser()
        cases = (
            (["init"], "init"),
            (["discover"], "discover"),
            (["plan"], "plan"),
            (["apply", "--plan-id", "plan-1", "--confirmed"], "apply"),
            (["verify", "--level", "safe"], "verify"),
            (["rollback", "--deployment-id", "deployment-1", "--confirmed"], "rollback"),
            (["versions", "check"], "versions"),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(parser.parse_args(argv).command, expected)

    def test_unimplemented_command_returns_structured_manual_action(self):
        stream = io.StringIO()
        code = main(["discover"], stream=stream)
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "manual_action_required")
        self.assertEqual(payload["nextAction"], "implementation_pending")
        self.assertEqual(payload["data"]["command"], "discover")

    def test_invalid_arguments_return_json_without_traceback(self):
        stream = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False):
            code = main(["not-a-command"], stream=stream)
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["errors"][0]["code"], "INVALID_ARGUMENTS")
        self.assertNotIn("Traceback", stream.getvalue())

    def test_help_uses_json_contract(self):
        stream = io.StringIO()
        code = main(["--help"], stream=stream)
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertIn("commands", payload["data"])


if __name__ == "__main__":
    unittest.main()
