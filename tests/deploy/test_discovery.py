import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from deploy.installer.command import CommandResult
from deploy.installer.config import load_config
from deploy.installer.discovery import discover
from deploy.installer.errors import DeploymentError

from .test_config import minimal_config


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "docker"


def json_lines(value: list[dict]) -> str:
    return "\n".join(json.dumps(item, separators=(",", ":")) for item in value)


class FixtureRunner:
    def __init__(self, *, containers=None, inspect=None, compose=None):
        self.calls: list[tuple[str, ...]] = []
        self.containers = containers or json.loads(
            (FIXTURES / "containers.json").read_text(encoding="utf-8")
        )
        self.inspect = inspect or json.loads(
            (FIXTURES / "openclaw-inspect.json").read_text(encoding="utf-8")
        )
        self.compose = compose or json.loads(
            (FIXTURES / "openclaw-compose.json").read_text(encoding="utf-8")
        )
        self.networks = json.loads(
            (FIXTURES / "networks.json").read_text(encoding="utf-8")
        )

    def run(self, args, timeout=30):
        del timeout
        key = tuple(args)
        self.calls.append(key)
        if key == ("uname", "-s"):
            return CommandResult(key, 0, "Linux\n", "")
        if key == ("uname", "-m"):
            return CommandResult(key, 0, "x86_64\n", "")
        if key == ("cat", "/etc/os-release"):
            return CommandResult(key, 0, 'NAME="UGREEN NAS"\nID=ugos\n', "")
        if key == ("docker", "version", "--format", "{{json .}}"):
            return CommandResult(key, 0, '{"Client":{"Version":"28.0"}}\n', "")
        if key == ("docker", "compose", "version", "--short"):
            return CommandResult(key, 0, "2.35.0\n", "")
        if key == ("docker", "ps", "-a", "--format", "{{json .}}"):
            return CommandResult(key, 0, json_lines(self.containers), "")
        if key == ("docker", "network", "ls", "--format", "{{json .}}"):
            return CommandResult(key, 0, json_lines(self.networks), "")
        if len(key) == 3 and key[:2] == ("docker", "inspect"):
            inspected = copy.deepcopy(self.inspect)
            inspected[0]["Name"] = "/" + key[2]
            return CommandResult(key, 0, json.dumps(inspected), "")
        if key == (
            "docker",
            "compose",
            "-p",
            "openclaw",
            "config",
            "--format",
            "json",
        ):
            return CommandResult(key, 0, json.dumps(self.compose), "")
        if (
            len(key) == 7
            and key[:3] == ("docker", "compose", "-f")
            and key[-2:] == ("config", "--quiet")
        ):
            return CommandResult(key, 0, "", "")
        if len(key) == 4 and key[1:3] == ("-m", "json.tool"):
            return CommandResult(key, 0, "", "")
        if len(key) == 4 and key[:3] == ("stat", "-c", "%a:%u:%g:%F"):
            return CommandResult(key, 0, "755:1000:1000:directory\n", "")
        if len(key) == 3 and key[:2] == ("df", "-P"):
            output = (
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/mapper/data 1000000 1000 999000 1% /volume\n"
            )
            return CommandResult(key, 0, output, "")
        raise AssertionError(f"unexpected discovery command: {key!r}")


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config_path = Path(self.temp_dir.name) / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(minimal_config(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.config = load_config(config_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_identifies_unique_openclaw_compose_service(self):
        runner = FixtureRunner()
        report = discover(self.config, runner)
        self.assertEqual(report.openclaw.container_name, "openclaw-gateway")
        self.assertEqual(report.openclaw.compose_service, "gateway")
        self.assertEqual(report.openclaw.compose_project, "openclaw")
        self.assertEqual(report.openclaw.workspace_host_dir, Path("/volume1/openclaw"))
        self.assertEqual(report.platform.kind, "ugos")
        self.assertEqual(report.platform.architecture, "amd64")
        self.assertTrue(report.platform.supports_compose_v2)
        self.assertIsNone(report.platform.supports_posix_acl)
        serialized = report.to_dict()
        self.assertEqual(serialized["openclaw"]["containerName"], "openclaw-gateway")
        self.assertTrue(serialized["paths"])
        forbidden = {"mkdir", "create", "up", "start", "restart"}
        self.assertFalse(any(forbidden.intersection(call) for call in runner.calls))

    def test_zero_candidates_requires_explicit_container(self):
        containers = [
            {
                "ID": "222222222222",
                "Image": "cp0204/quark-auto-save:0.3.0",
                "Names": "openclaw-media-qas",
                "State": "running",
                "Status": "Up",
            }
        ]
        with self.assertRaises(DeploymentError) as ctx:
            discover(self.config, FixtureRunner(containers=containers))
        self.assertEqual(ctx.exception.status, "manual_action_required")
        self.assertEqual(ctx.exception.next_action, "specify_openclaw_container")

    def test_two_valid_candidates_require_selection(self):
        containers = json.loads((FIXTURES / "containers.json").read_text(encoding="utf-8"))
        containers.append(
            {
                "ID": "333333333333",
                "Image": "ghcr.io/openclaw/openclaw:1.0.0",
                "Names": "openclaw-worker",
                "State": "running",
                "Status": "Up",
            }
        )
        with self.assertRaises(DeploymentError) as ctx:
            discover(self.config, FixtureRunner(containers=containers))
        self.assertEqual(ctx.exception.status, "manual_action_required")
        self.assertEqual(ctx.exception.next_action, "choose_openclaw_container")

    def test_docker_run_only_candidate_is_not_rewritten(self):
        inspected = json.loads(
            (FIXTURES / "openclaw-inspect.json").read_text(encoding="utf-8")
        )
        inspected[0]["Config"]["Labels"] = {}
        with self.assertRaises(DeploymentError) as ctx:
            discover(self.config, FixtureRunner(inspect=inspected))
        self.assertEqual(
            ctx.exception.next_action,
            "convert_openclaw_to_compose_or_configure_manually",
        )


if __name__ == "__main__":
    unittest.main()
