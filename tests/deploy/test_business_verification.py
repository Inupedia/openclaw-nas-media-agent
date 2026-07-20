import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from deploy.installer.business_verifier import (
    BusinessVerificationContext,
    run_mediactl,
    verify_full,
    verify_safe,
)
from deploy.installer.config import load_config
from deploy.installer.models import DeploymentStatus
from deploy.installer.secrets import SecretStore
from tests.deploy.test_config import minimal_config


class FixtureExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        args = list(argv[1:])
        self.calls.append(args)
        if args == ["check-ready"]:
            data = {"ok": True, "data": {"ready": True}}
        elif args and args[0] in {"search", "import-url"}:
            data = {
                "ok": True,
                "data": {
                    "candidates": [
                        {"candidateId": "candidate-demo"}
                    ]
                },
            }
        elif args and args[0] == "preview":
            data = {
                "ok": True,
                "data": {"candidateId": "candidate-demo"},
            }
        elif args and args[0] == "tree":
            data = {
                "ok": True,
                "data": {
                    "nodes": [
                        {"nodeId": "node-demo", "size": 1024}
                    ]
                },
            }
        elif args[:2] == ["plan", "download"]:
            data = {"ok": True, "data": {"planId": "plan-demo"}}
        elif args and args[0] == "execute":
            data = {"ok": True, "data": {"taskId": "task-demo"}}
        else:
            data = {"ok": True, "data": {}}
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(data) + "\n",
            stderr="",
        )


class BusinessVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        deploy = self.project / "deploy"
        deploy.mkdir()
        raw = minimal_config()
        raw["deployment"]["project_dir"] = str(self.project / "stack")
        raw["nas"]["downloads_dir"] = str(self.project / "downloads")
        raw["nas"]["organizing_dir"] = str(self.project / "organizing")
        for name in raw["nas"]["libraries"]:
            raw["nas"]["libraries"][name] = str(
                self.project / "media" / name
            )
        raw["verification"]["allow_real_download"] = True
        raw["verification"]["max_test_bytes"] = 4096
        config_path = deploy / "config.yaml"
        config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.config = load_config(config_path)
        secret_dir = deploy / "secrets"
        secret_dir.mkdir(mode=0o700)
        secret_dir.chmod(0o700)
        for name in self.config.secret_names():
            path = secret_dir / name
            value = (
                "https://pan.quark.cn/s/legal-test"
                if name
                == self.config.verification.full_test_share_url_secret
                else "secret"
            )
            path.write_text(value, encoding="utf-8")
            path.chmod(0o600)
        self.secrets = SecretStore(secret_dir)
        self.executor = FixtureExecutor()

    def tearDown(self):
        self.temp.cleanup()

    def context(self, confirmed=False):
        return BusinessVerificationContext(
            mediactl_path=self.project / "bin/mediactl",
            config=self.config,
            secrets=self.secrets,
            executor=self.executor,
            confirmed=confirmed,
        )

    def test_safe_flow_stops_after_plan_without_execute(self):
        result = verify_safe(self.context())
        self.assertEqual(result.status, DeploymentStatus.READY)
        self.assertEqual(
            self.executor.calls,
            [
                ["check-ready"],
                [
                    "search",
                    self.config.verification.safe_query,
                    "--media-type",
                    "other",
                ],
                ["preview", "candidate-demo"],
                ["tree", "candidate-demo"],
                [
                    "plan",
                    "download",
                    "candidate-demo",
                    "--node",
                    "node-demo",
                    "--media-type",
                    "other",
                ],
            ],
        )
        self.assertFalse(
            any(call and call[0] == "execute" for call in self.executor.calls)
        )

    def test_full_requires_all_three_independent_gates(self):
        result = verify_full(self.context(confirmed=False))
        self.assertEqual(
            result.status,
            DeploymentStatus.MANUAL_ACTION_REQUIRED,
        )
        self.assertIn(
            "confirmed",
            result.components[0].details["missingGates"],
        )

    def test_full_executes_bounded_download_but_never_organize_execute(self):
        result = verify_full(self.context(confirmed=True))
        self.assertEqual(result.status, DeploymentStatus.READY)
        self.assertIn(
            ["execute", "plan-demo", "--confirmed"],
            self.executor.calls,
        )
        self.assertIn(
            ["organize", "plan", "task-demo"],
            self.executor.calls,
        )
        self.assertNotIn(
            ["organize", "execute"],
            [call[:2] for call in self.executor.calls],
        )
        self.assertEqual(result.next_action, "stop")

    def test_rejects_trailing_terminal_prose(self):
        context = BusinessVerificationContext(
            mediactl_path=self.project / "bin/mediactl",
            config=self.config,
            secrets=self.secrets,
            executor=lambda argv: SimpleNamespace(
                returncode=0,
                stdout='{"ok":true}\nnot-json',
                stderr="",
            ),
        )
        with self.assertRaisesRegex(Exception, "prose"):
            run_mediactl(["check-ready"], context)


if __name__ == "__main__":
    unittest.main()
