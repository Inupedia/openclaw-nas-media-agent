import json
import os
import tempfile
import unittest
from pathlib import Path

from deploy.installer.adapters.qas_v1 import (
    QasDesiredState,
    QasV1Adapter,
    derive_api_token,
    normalize_cookies,
)
from deploy.installer.errors import DeploymentError
from deploy.installer.models import ComponentStatus

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "qas-v1"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class QasAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "quark_config.json"
        self.config.write_text(
            (FIXTURES / "config.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.password = "fixture-password"
        self.token = derive_api_token("fixture-admin", self.password)
        self.desired = QasDesiredState(
            username="fixture-admin",
            password=self.password,
            api_token=self.token,
            cookies=("cookie-a", "cookie-b"),
            aria2_host_port="aria2:6800",
            aria2_secret="rpc-secret",
            aria2_dir="/nas/downloads",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_token_matches_locked_qas_algorithm(self):
        self.assertEqual(len(self.token), 16)
        self.assertEqual(
            self.token,
            derive_api_token("fixture-admin", "fixture-password"),
        )

    def test_cookie_normalization_accepts_json_and_lines(self):
        self.assertEqual(normalize_cookies('["a", " b "]'), ("a", "b"))
        self.assertEqual(normalize_cookies("a\nb\n"), ("a", "b"))

    def test_unknown_schema_requires_manual_configuration(self):
        self.config.write_text('{"cookie": []}\n', encoding="utf-8")
        adapter = QasV1Adapter(self.config, "http://qas:5005")
        with self.assertRaises(DeploymentError) as ctx:
            adapter.discover(self.desired)
        self.assertEqual(ctx.exception.status, "manual_action_required")
        self.assertEqual(
            ctx.exception.next_action,
            "complete_qas_configuration",
        )

    def test_plan_redacts_all_secret_values(self):
        adapter = QasV1Adapter(self.config, "http://qas:5005")
        changes = adapter.plan(self.desired, self.root / "backup.json")
        encoded = json.dumps([item.to_dict() for item in changes])
        self.assertNotIn(self.password, encoded)
        self.assertNotIn("rpc-secret", encoded)
        self.assertNotIn("cookie-a", encoded)
        self.assertNotIn(self.token, encoded)

    @unittest.skipUnless(os.name == "posix", "permission assertion requires POSIX")
    def test_apply_writes_only_supported_fields_and_private_backup(self):
        adapter = QasV1Adapter(self.config, "http://qas:5005")
        backup = self.root / "backups" / "qas.json"
        result = adapter.apply_config(self.desired, backup)
        self.assertEqual(result.status, ComponentStatus.READY)
        updated = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(updated["webui"]["username"], "fixture-admin")
        self.assertEqual(updated["webui"]["password"], self.password)
        self.assertEqual(updated["cookie"], ["cookie-a", "cookie-b"])
        self.assertEqual(
            updated["plugins"]["aria2"]["host_port"],
            "aria2:6800",
        )
        self.assertEqual(
            updated["plugins"]["aria2"]["secret"],
            "rpc-secret",
        )
        self.assertEqual(
            updated["plugins"]["aria2"]["dir"],
            "/nas/downloads",
        )
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_api_token_mismatch_is_rejected_before_write(self):
        wrong = QasDesiredState(
            username=self.desired.username,
            password=self.desired.password,
            api_token="wrong",
            cookies=self.desired.cookies,
            aria2_host_port=self.desired.aria2_host_port,
            aria2_secret=self.desired.aria2_secret,
            aria2_dir=self.desired.aria2_dir,
        )
        adapter = QasV1Adapter(self.config, "http://qas:5005")
        with self.assertRaises(DeploymentError) as ctx:
            adapter.apply_config(wrong, self.root / "backup.json")
        self.assertEqual(
            ctx.exception.next_action,
            "regenerate_qas_api_token",
        )

    def test_verify_reads_data_with_derived_query_token(self):
        seen = {}
        payload = {
            "success": True,
            "data": {
                "cookie": ["cookie-a", "cookie-b"],
                "plugins": {
                    "aria2": {
                        "host_port": "aria2:6800",
                        "secret": "rpc-secret",
                        "dir": "/nas/downloads",
                    }
                },
            },
        }

        def opener(request, timeout=0):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            return FakeResponse(payload)

        adapter = QasV1Adapter(
            self.config,
            "http://qas:5005",
            urlopen=opener,
        )
        result = adapter.verify(self.desired)
        self.assertEqual(result.status, ComponentStatus.READY)
        self.assertIn("token=" + self.token, seen["url"])
        self.assertNotIn(self.password, json.dumps(result.to_dict()))
        self.assertNotIn("rpc-secret", json.dumps(result.to_dict()))

    def test_verify_reports_invalid_without_exposing_values(self):
        payload = {
            "success": True,
            "data": {
                "cookie": [],
                "plugins": {
                    "aria2": {
                        "host_port": "bad",
                        "secret": "bad",
                        "dir": "/bad",
                    }
                },
            },
        }
        adapter = QasV1Adapter(
            self.config,
            "http://qas:5005",
            urlopen=lambda *args, **kwargs: FakeResponse(payload),
        )
        result = adapter.verify(self.desired)
        self.assertEqual(
            result.status,
            ComponentStatus.MANUAL_ACTION_REQUIRED,
        )
        encoded = json.dumps(result.to_dict())
        self.assertNotIn("rpc-secret", encoded)
        self.assertNotIn("cookie-a", encoded)


if __name__ == "__main__":
    unittest.main()
