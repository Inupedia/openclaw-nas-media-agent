import os
import tempfile
import unittest
from pathlib import Path

from deploy.installer.errors import DeploymentError
from deploy.installer.redaction import redact
from deploy.installer.secrets import SecretStore


@unittest.skipUnless(os.name == "posix", "secret mode tests require POSIX permissions")
class SecretTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "secrets"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.store = SecretStore(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_secret(self, name: str, value: str, mode: int = 0o600) -> Path:
        path = self.root / name
        path.write_text(value, encoding="utf-8")
        path.chmod(mode)
        return path

    def test_rejects_group_readable_secret(self):
        self.write_secret("qas_token", "token-123", 0o640)
        with self.assertRaises(DeploymentError) as ctx:
            self.store.read("qas_token")
        self.assertEqual(ctx.exception.severity, "security_block")

    def test_rejects_insecure_secret_root(self):
        self.root.chmod(0o750)
        with self.assertRaises(DeploymentError) as ctx:
            SecretStore(self.root)
        self.assertEqual(ctx.exception.severity, "security_block")

    def test_rejects_traversal_and_symlinks(self):
        outside = Path(self.temp_dir.name) / "outside"
        outside.write_text("secret", encoding="utf-8")
        outside.chmod(0o600)
        (self.root / "linked").symlink_to(outside)
        for name in ("../outside", "folder/value", "folder\\value", "linked"):
            with self.subTest(name=name):
                with self.assertRaises(DeploymentError) as ctx:
                    self.store.read(name)
                self.assertEqual(ctx.exception.severity, "security_block")

    def test_trims_exactly_one_trailing_newline(self):
        self.write_secret("value", "line-one\nline-two\n\n")
        self.assertEqual(self.store.read("value"), "line-one\nline-two\n")

    def test_metadata_digest_changes_without_hashing_content_into_repr(self):
        secret = self.write_secret("qas_token", "token-123\n")
        first = self.store.metadata_digest(["qas_token"])
        secret.write_text("different-value\n", encoding="utf-8")
        secret.chmod(0o600)
        second = self.store.metadata_digest(["qas_token"])
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")
        self.store.read("qas_token")
        representation = repr(self.store)
        self.assertIn("qas_token", representation)
        self.assertNotIn("token-123", representation)
        self.assertNotIn("different-value", representation)

    def test_redacts_embedded_values_without_mutating_source(self):
        value = {
            "url": "http://service/?token=token-123",
            "items": ["token-123", {"message": "prefix token-123 suffix"}],
        }
        result = redact(value, ["token-123"])
        self.assertEqual(
            result,
            {
                "url": "http://service/?token=***",
                "items": ["***", {"message": "prefix *** suffix"}],
            },
        )
        self.assertEqual(value["items"][0], "token-123")

    def test_longer_overlapping_secret_is_redacted_first(self):
        self.assertEqual(redact("abc123 and abc", ["abc", "abc123"]), "*** and ***")


if __name__ == "__main__":
    unittest.main()
