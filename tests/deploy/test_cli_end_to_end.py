import io
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from deploy.installer.cli import main
from deploy.installer.executor import ExecutionContext
from deploy.installer.runtime import RuntimePaths
from tests.deploy.test_business_verification import FixtureExecutor
from tests.deploy.test_config import minimal_config
from tests.deploy.test_discovery import FixtureRunner


class CliEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "deploy").mkdir()
        (self.project / "config").mkdir()
        (self.project / "config/routing.json").write_text("{}\n")
        source = self.project / "source.yaml"
        raw = minimal_config()
        raw["deployment"]["project_dir"] = str(self.project / "stack")
        raw["nas"]["downloads_dir"] = str(self.project / "downloads")
        raw["nas"]["organizing_dir"] = str(self.project / "organizing")
        for name in raw["nas"]["libraries"]:
            raw["nas"]["libraries"][name] = str(self.project / "media" / name)
        workspace = self.project / "openclaw-workspace"
        workspace.mkdir()
        openclaw_config = workspace / "openclaw.json"
        fixture = (
            Path(__file__).resolve().parents[1]
            / "fixtures/openclaw-v1/config.json"
        )
        openclaw_config.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
        raw["openclaw"]["workspace_host_dir"] = str(workspace)
        raw["openclaw"]["config_host_path"] = str(openclaw_config)
        source.write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.source = source
        versions = Path(__file__).resolve().parents[2] / "deploy/versions.yaml"
        (self.project / "deploy/versions.yaml").write_text(versions.read_text())
        self.fixture_runner = FixtureRunner()
        self.events = []

    def tearDown(self):
        self.temp.cleanup()

    def context_factory(self, runtime: RuntimePaths):
        action_names = {
            "create_directory",
            "write_file",
            "create_network",
            "compose_up",
            "copy_tree",
            "http_config_update",
            "restart_container",
            "run_verification",
        }
        rollback_names = {
            "remove_empty_directory",
            "restore_file",
            "remove_network",
            "compose_down",
            "remove_file",
            "restore_tree",
            "restart_container",
        }
        return ExecutionContext(
            runtime,
            self.project,
            {
                name: (lambda change, action=name: self.events.append("do:" + action))
                for name in action_names
            },
            {
                name: (lambda data, action=name: self.events.append("undo:" + action))
                for name in rollback_names
            },
            deployment_id="fixture-deploy",
        )

    def command(self, argv, **kwargs):
        stream = io.StringIO()
        code = main(
            argv,
            stream=stream,
            prompt_stream=io.StringIO(),
            project_root=self.project,
            runner_factory=lambda: self.fixture_runner,
            execution_context_factory=self.context_factory,
            **kwargs,
        )
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        return code, json.loads(lines[0]), stream.getvalue()

    def test_fixture_init_discover_plan_apply_verify_and_rollback(self):
        code, payload, _ = self.command(
            [
                "init",
                "--non-interactive",
                "--config-source",
                str(self.source),
            ]
        )
        self.assertEqual(code, 0)
        secret_dir = self.project / "deploy/secrets"
        secret_values = {
            "pansou_proxy_url": "socks5://127.0.0.1:1080",
            "qas_webui_password": "fixture-password",
            "qas_token": "",
            "quark_cookie": "fixture-cookie",
            "aria2_rpc_secret": "fixture-aria-secret",
            "full_test_share_url": "",
        }
        for path in secret_dir.iterdir():
            path.write_text(secret_values.get(path.name, ""))
            path.chmod(0o600)

        self.assertEqual(self.command(["discover"])[1]["status"], "ready")
        planned = self.command(["plan"])[1]
        plan_id = planned["data"]["planId"]
        applied = self.command(
            ["apply", "--plan-id", plan_id, "--confirmed"]
        )[1]
        self.assertEqual(applied["status"], "ready")

        media = FixtureExecutor()
        verified = self.command(
            ["verify", "--level", "safe"],
            mediactl_executor=media,
        )[1]
        self.assertEqual(verified["status"], "ready")

        rolled = self.command(
            [
                "rollback",
                "--deployment-id",
                "fixture-deploy",
                "--confirmed",
            ]
        )[1]
        self.assertEqual(rolled["status"], "rolled_back")

        artifact_text = "".join(
            path.read_text(encoding="utf-8")
            for path in (self.project / "deploy/runtime").rglob("*.json")
        )
        self.assertNotIn("socks5://127.0.0.1:1080", artifact_text)
        self.assertNotIn("fixture-aria-secret", artifact_text)


if __name__ == "__main__":
    unittest.main()
