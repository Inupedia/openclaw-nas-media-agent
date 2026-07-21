import json
import tempfile
import unittest
from pathlib import Path

from deploy.installer.adapters.openclaw_v1 import (
    OpenClawV1Adapter,
    compose_command,
    constrained_config,
    resolve_paths,
)
from deploy.installer.command import CommandResult
from deploy.installer.discovery import OpenClawInstallation
from deploy.installer.errors import DeploymentError
from deploy.installer.models import ComponentStatus


class Runner:
    def __init__(self):
        self.calls = []

    def run(self, args, timeout=30):
        key = tuple(args)
        self.calls.append(key)
        if key[:2] == ("git", "-C"):
            return CommandResult(key, 0, "", "")
        if key[:3] == ("docker", "inspect", "openclaw-gateway"):
            return CommandResult(key, 0, "running\n", "")
        return CommandResult(key, 0, '{"ok":true}\n', "")


def installation(root):
    return OpenClawInstallation(
        container_name="openclaw-gateway",
        image="ghcr.io/openclaw/openclaw:1.0.0",
        compose_project="openclaw",
        compose_service="gateway",
        compose_working_dir=root / "compose",
        compose_config_files=(
            root / "compose/compose.yml",
            root / "compose/local.yml",
        ),
        workspace_host_dir=root / "workspace",
        workspace_container_dir=Path("/root/.openclaw/workspace"),
        networks=("openclaw_default",),
        health="healthy",
    )


class OpenClawAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "bin").mkdir()
        (self.repo / "bin/mediactl").write_text("#!/bin/sh\n")
        (self.repo / "deploy/runtime").mkdir(parents=True)
        (self.repo / "deploy/runtime/secret").write_text("x")
        self.config = self.root / "workspace/openclaw.json"
        self.config.parent.mkdir(parents=True)
        fixture = (
            Path(__file__).resolve().parents[1]
            / "fixtures/openclaw-v1/config.json"
        )
        self.config.write_text(fixture.read_text())
        self.installation = installation(self.root)
        self.runner = Runner()
        self.adapter = OpenClawV1Adapter(
            self.installation,
            self.config,
            self.repo,
            runner=self.runner,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_resolves_fixed_paths_and_compose_command_preserves_all_files(self):
        paths = resolve_paths(self.installation)
        self.assertEqual(
            paths.container_mediactl,
            Path(
                "/root/.openclaw/workspace/skills/"
                "resource-download-agent/bin/mediactl"
            ),
        )
        command = compose_command(self.installation, self.root / "override.yml")
        self.assertEqual(command[-3:], ["up", "-d", "gateway"])
        self.assertEqual(command.count("-f"), 3)

    def test_constrained_config_allows_only_absolute_mediactl(self):
        paths = resolve_paths(self.installation)
        updated = constrained_config(
            json.loads(self.config.read_text()),
            paths,
            {"QAS_BASE_URL": "http://qas:5005"},
        )
        policy = updated["tools"]["exec"]
        self.assertEqual(policy["security"], "allowlist")
        self.assertEqual(policy["ask"], "off")
        self.assertEqual(policy["allowlist"], [str(paths.container_mediactl)])
        text = json.dumps(policy)
        for command in ("bash", "sh", "python", "curl", "rm", "sudo"):
            self.assertNotIn(f'"{command}"', text)

    def test_unknown_exec_shape_requires_manual_action(self):
        with self.assertRaises(DeploymentError) as ctx:
            constrained_config(
                {"tools": {"exec": "unsafe"}},
                resolve_paths(self.installation),
                {},
            )
        self.assertEqual(ctx.exception.status, "manual_action_required")

    def test_skill_copy_excludes_private_runtime_and_marks_mediactl_executable(self):
        self.adapter.install_skill()
        target = self.adapter.paths.host_skill_path
        self.assertTrue((target / "bin/mediactl").stat().st_mode & 0o111)
        self.assertFalse((target / "deploy/runtime").exists())

    def test_apply_reads_back_supported_config_and_verify_uses_fixed_command(self):
        backup = self.root / "backup/openclaw.json"
        self.adapter.apply_config({"QAS_BASE_URL": "http://qas:5005"}, backup)
        data = json.loads(self.config.read_text())
        self.assertEqual(
            data["tools"]["exec"]["allowlist"],
            [str(self.adapter.paths.container_mediactl)],
        )
        self.assertTrue(backup.is_file())
        result = self.adapter.verify()
        self.assertEqual(result.status, ComponentStatus.READY)
        self.assertIn(
            str(self.adapter.paths.container_mediactl),
            self.runner.calls[-1],
        )


if __name__ == "__main__":
    unittest.main()
