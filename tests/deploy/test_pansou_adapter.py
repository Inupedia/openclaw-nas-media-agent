import json
import tempfile
import unittest
from pathlib import Path

import yaml

from deploy.installer.adapters.pansou import PanSouAdapter
from deploy.installer.config import load_config
from deploy.installer.models import ComponentStatus
from deploy.installer.secrets import SecretStore
from tests.deploy.test_config import minimal_config


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class PanSouAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        secrets = self.root / "secrets"
        secrets.mkdir(mode=0o700)
        secrets.chmod(0o700)
        proxy = secrets / "pansou_proxy_url"
        proxy.write_text("socks5://127.0.0.1:1080\n", encoding="utf-8")
        proxy.chmod(0o600)
        self.store = SecretStore(secrets)

    def tearDown(self):
        self.temp.cleanup()

    def settings(self, mode="existing"):
        raw = minimal_config()
        raw["pansou"]["proxy"] = {"mode": mode}
        if mode == "existing":
            raw["pansou"]["proxy"]["url_secret"] = "pansou_proxy_url"
        if mode == "managed":
            raw["pansou"]["proxy"]["singbox_config_secret"] = "singbox_config.json"
        path = self.root / f"{mode}.yaml"
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        return load_config(path).pansou

    def test_existing_proxy_maps_socks_and_plan_never_contains_value(self):
        adapter = PanSouAdapter(self.settings(), self.store)
        self.assertEqual(adapter.proxy_environment(), {"PROXY": "socks5://127.0.0.1:1080"})
        plan = adapter.plan()[0].to_dict()
        self.assertIn("pansou_proxy_url", json.dumps(plan))
        self.assertNotIn("127.0.0.1:1080", json.dumps(plan))

    def test_http_proxy_maps_both_standard_variables(self):
        path = self.root / "secrets/pansou_proxy_url"
        path.write_text("http://proxy.local:3128", encoding="utf-8")
        path.chmod(0o600)
        adapter = PanSouAdapter(self.settings(), self.store)
        self.assertEqual(
            adapter.proxy_environment(),
            {
                "HTTP_PROXY": "http://proxy.local:3128",
                "HTTPS_PROXY": "http://proxy.local:3128",
            },
        )

    def test_none_has_no_proxy_environment(self):
        self.assertEqual(
            PanSouAdapter(self.settings("none"), self.store).proxy_environment(),
            {},
        )

    def test_telegram_results_are_ready_and_missing_results_degraded(self):
        payload = {
            "data": {
                "merged_by_type": {
                    "quark": [
                        {
                            "url": "https://pan.quark.cn/s/demo",
                            "source": "telegram:tgsearchers3",
                        }
                    ]
                }
            }
        }
        ready = PanSouAdapter(
            self.settings(),
            self.store,
            opener=lambda *a, **k: Response(payload),
        ).verify()
        self.assertEqual(ready.status, ComponentStatus.READY)
        self.assertTrue(ready.details["telegramReachable"])
        degraded = PanSouAdapter(
            self.settings(),
            self.store,
            opener=lambda *a, **k: Response({"data": {}}),
        ).verify()
        self.assertEqual(degraded.status, ComponentStatus.DEGRADED)
        self.assertEqual(degraded.next_action, "check_pansou_proxy_or_channels")

    def test_discovery_uses_exact_service_or_image_evidence(self):
        adapter = PanSouAdapter(self.settings(), self.store)
        found = adapter.discover(
            [
                {
                    "Names": "openclaw-media-pansou",
                    "State": "running",
                    "Health": "healthy",
                }
            ]
        )
        self.assertTrue(found.running)
        self.assertEqual(found.container_name, "openclaw-media-pansou")


if __name__ == "__main__":
    unittest.main()
