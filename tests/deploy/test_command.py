import subprocess
import unittest
from unittest import mock

from deploy.installer.command import CommandRunner
from deploy.installer.errors import DeploymentError


class CommandRunnerTests(unittest.TestCase):
    def test_passes_argv_without_shell_and_preserves_nonzero_result(self):
        completed = subprocess.CompletedProcess(
            args=["docker", "version"],
            returncode=7,
            stdout="out",
            stderr="err",
        )
        run_impl = mock.Mock(return_value=completed)
        runner = CommandRunner(run_impl=run_impl)
        result = runner.run(["docker", "version"], timeout=12)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "out")
        self.assertEqual(result.stderr, "err")
        run_impl.assert_called_once_with(
            ["docker", "version"],
            cwd=None,
            env=None,
            text=True,
            capture_output=True,
            timeout=12,
            check=False,
            shell=False,
        )

    def test_rejects_command_strings(self):
        runner = CommandRunner(run_impl=mock.Mock())
        with self.assertRaises(TypeError):
            runner.run("docker ps")

    def test_timeout_maps_to_typed_error_without_secret(self):
        run_impl = mock.Mock(side_effect=subprocess.TimeoutExpired(["cmd", "token-123"], 3))
        runner = CommandRunner(run_impl=run_impl, secret_values=["token-123"])
        with self.assertRaises(DeploymentError) as ctx:
            runner.run(["cmd", "token-123"], timeout=3)
        self.assertEqual(ctx.exception.code, "DISCOVERY_COMMAND_TIMEOUT")
        self.assertNotIn("token-123", str(ctx.exception))
        self.assertNotIn("token-123", repr(ctx.exception.details))

    def test_output_and_os_errors_are_redacted(self):
        completed = subprocess.CompletedProcess(
            args=["cmd"],
            returncode=1,
            stdout="token-123",
            stderr="failed token-123",
        )
        runner = CommandRunner(
            run_impl=mock.Mock(return_value=completed),
            secret_values=["token-123"],
        )
        result = runner.run(["cmd"])
        self.assertEqual(result.stdout, "***")
        self.assertEqual(result.stderr, "failed ***")

        failing = CommandRunner(
            run_impl=mock.Mock(side_effect=OSError("token-123 unavailable")),
            secret_values=["token-123"],
        )
        with self.assertRaises(DeploymentError) as ctx:
            failing.run(["cmd"])
        self.assertNotIn("token-123", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
