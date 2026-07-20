import json
import tempfile
import unittest
from pathlib import Path

import yaml

from deploy.installer.adapters.proxy import ProxyAdapter
from deploy.installer.command import CommandResult
from deploy.installer.config import load_config
from deploy.installer.errors import DeploymentError
from deploy.installer.models import ComponentStatus
from deploy.installer.secrets import SecretStore
from deploy.installer.versions import VersionLock
from tests.deploy.test_config import minimal_config


class Runner:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def run(self, args, timeout=30):
        self.calls.append((tuple(args), timeout))
        return CommandResult(tuple(args), self.returncode, "", "")


class ProxyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        secret_dir = self.root / "secrets"
        secret_dir.mkdir(mode=0o700)
        secret_dir.chmod(0o700)
        self.secret = secret_dir / "singbox_config.json"
        self.write_config(
            {
                "inbounds": [
                    {"type": "socks", "listen": "0.0.0.0", "listen_port": 1080}
                ],
                "outbounds": [{"type": "direct"}],
            }
        )
        self.store = SecretStore(secret_dir)
        raw = minimal_config()
        raw["pansou"]["proxy"] = {
            "mode": "managed",
            "singbox_config_secret": "singbox_config.json",
        }
        config_path = self.root / "config.yaml"
        config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.settings = load_config(config_path).pansou.proxy
        self.versions = VersionLock.load(
            Path(__file__).resolve().parents[2] / "deploy/versions.yaml"
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, value):
        self.secret.write_text(json.dumps(value), encoding="utf-8")
        self.secret.chmod(0o600)

    def test_managed_profile_has_immutable_image_and_no_host_ports(self):
        adapter = ProxyAdapter(self.settings, self.store, self.versions)
        change = adapter.plan()[0].to_dict()
        self.assertRegex(change["after"]["image"], r"@sha256:[0-9a-f]{64}$")
        self.assertEqual(change["after"]["hostPorts"], [])
        self.assertNotIn("outbounds", json.dumps(change))

    def test_rejects_subscription_text_and_public_management_api(self):
        self.secret.write_text(
            "https://subscription.invalid/list",
            encoding="utf-8",
        )
        self.secret.chmod(0o600)
        with self.assertRaises(DeploymentError) as ctx:
            ProxyAdapter(self.settings, self.store, self.versions).plan()
        self.assertEqual(ctx.exception.next_action, "provide_singbox_config")
        self.write_config(
            {
                "inbounds": [{"type": "socks", "listen_port": 1080}],
                "outbounds": [{"type": "direct"}],
                "experimental": {
                    "clash_api": {"external_controller": "0.0.0.0:9090"}
                },
            }
        )
        with self.assertRaises(DeploymentError) as ctx:
            ProxyAdapter(self.settings, self.store, self.versions).plan()
        self.assertEqual(ctx.exception.severity, "security_block")

    def test_verify_runs_fixed_sing_box_check(self):
        runner = Runner()
        result = ProxyAdapter(self.settings, self.store, self.versions).verify(runner)
        self.assertEqual(result.status, ComponentStatus.READY)
        self.assertEqual(
            runner.calls[0][0][:3],
            ("docker", "exec", "openclaw-media-proxy"),
        )


if __name__ == "__main__":
    unittest.main()
