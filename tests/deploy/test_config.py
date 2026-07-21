import tempfile
import unittest
from pathlib import Path

import yaml

from deploy.installer.config import config_digest, load_config
from deploy.installer.errors import DeploymentError


def minimal_config() -> dict:
    return {
        "schema_version": 1,
        "deployment": {
            "mode": "existing-openclaw",
            "platform": "ugos",
            "project_dir": "/volume1/docker/openclaw-media",
            "timezone": "Asia/Shanghai",
            "allow_reuse_existing_services": True,
        },
        "nas": {
            "downloads_dir": "/volume2/downloads",
            "organizing_dir": "/volume3/media/.openclaw-organizing",
            "libraries": {
                "movie": "/volume3/media/Movie",
                "drama": "/volume2/media/Drama",
                "anime": "/volume2/media/Anime",
                "documentary": "/volume2/media/Documentary",
                "show": "/volume2/media/Shows",
                "other": "/volume2/media/Others",
            },
        },
        "openclaw": {
            "container_name": "auto",
            "workspace_host_dir": "auto",
            "config_host_path": "auto",
        },
        "qas": {
            "mode": "auto",
            "port": 5005,
            "username": "admin",
            "password_secret": "qas_webui_password",
            "api_token_secret": "qas_token",
            "quark_cookie_secret": "quark_cookie",
        },
        "aria2": {
            "mode": "auto",
            "rpc_port": 6800,
            "rpc_secret": "aria2_rpc_secret",
            "uid": "auto",
            "gid": "auto",
        },
        "pansou": {
            "enabled": True,
            "mode": "auto",
            "port": 8888,
            "channels": ["tgsearchers3"],
            "plugins": [],
            "max_candidates": 50,
            "proxy": {
                "mode": "existing",
                "url_secret": "pansou_proxy_url",
            },
        },
        "jiaofu": {
            "enabled": False,
            "storage_state_secret": "jiaofu_storage_state.json",
            "max_candidates": 20,
        },
        "verification": {
            "safe": True,
            "safe_query": "OpenClaw deploy verification sample",
            "allow_real_download": False,
            "full_test_share_url_secret": "full_test_share_url",
            "max_test_bytes": 104857600,
        },
    }


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_yaml(self, value: dict) -> Path:
        path = self.root / "config.yaml"
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    def test_rejects_full_stack_in_phase_one(self):
        raw = minimal_config()
        raw["deployment"]["mode"] = "full-stack"
        with self.assertRaisesRegex(DeploymentError, "existing-openclaw"):
            load_config(self.write_yaml(raw))

    def test_missing_movie_library_is_security_block(self):
        raw = minimal_config()
        del raw["nas"]["libraries"]["movie"]
        with self.assertRaises(DeploymentError) as ctx:
            load_config(self.write_yaml(raw))
        self.assertEqual(ctx.exception.severity, "security_block")

    def test_rejects_unknown_configuration_fields(self):
        raw = minimal_config()
        raw["deployment"]["surprise"] = True
        with self.assertRaises(DeploymentError) as ctx:
            load_config(self.write_yaml(raw))
        self.assertEqual(ctx.exception.code, "CONFIG_SCHEMA_INVALID")

    def test_normalizes_paths_and_auto_openclaw_locations(self):
        config = load_config(self.write_yaml(minimal_config()))
        self.assertEqual(config.downloads_dir, Path("/volume2/downloads"))
        self.assertEqual(config.libraries.movie, Path("/volume3/media/Movie"))
        self.assertIsNone(config.openclaw.workspace_host_dir)
        self.assertIsNone(config.openclaw.config_host_path)
        self.assertEqual(config.pansou.proxy.mode, "existing")

    def test_rejects_relative_or_parent_traversal_paths(self):
        for value in ("relative/path", "/volume2/downloads/../media"):
            with self.subTest(value=value):
                raw = minimal_config()
                raw["nas"]["downloads_dir"] = value
                with self.assertRaises(DeploymentError) as ctx:
                    load_config(self.write_yaml(raw))
                self.assertEqual(ctx.exception.severity, "security_block")

    def test_formal_library_must_not_be_inside_download_root(self):
        raw = minimal_config()
        raw["nas"]["libraries"]["movie"] = "/volume2/downloads/Movie"
        with self.assertRaises(DeploymentError) as ctx:
            load_config(self.write_yaml(raw))
        self.assertEqual(ctx.exception.code, "LIBRARY_INSIDE_DOWNLOADS")
        self.assertEqual(ctx.exception.severity, "security_block")

    def test_digest_is_stable_for_equivalent_config(self):
        first = load_config(self.write_yaml(minimal_config()))
        second_raw = minimal_config()
        second_raw["pansou"]["channels"] = ["tgsearchers3"]
        second = load_config(self.write_yaml(second_raw))
        self.assertEqual(config_digest(first), config_digest(second))
        self.assertRegex(config_digest(first), r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
