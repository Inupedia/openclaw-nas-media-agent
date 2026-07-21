import tempfile
import unittest
from pathlib import Path

from deploy.installer.backup import create_backup
from deploy.installer.errors import DeploymentError
from deploy.installer.runtime import RuntimePaths


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "deploy").mkdir()
        self.runtime = RuntimePaths.for_project(self.project)
        self.data = self.project / "data"
        self.data.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_copies_supported_files_with_metadata_and_no_secret_values(self):
        config = self.data / "openclaw.json"
        config.write_text('{"safe":true}\n')
        config.chmod(0o640)
        state = self.data / "state.db"
        state.write_bytes(b"sqlite")
        manifest = create_backup(
            [config, state],
            self.runtime,
            "deploy1",
            allowed_roots=[self.project],
            secret_sentinels=["token-secret"],
        )
        self.assertEqual(len(manifest.entries), 2)
        self.assertTrue((manifest.backup_root / "manifest.json").is_file())
        self.assertEqual(manifest.entries[0].mode, 0o640)

    def test_rejects_secret_paths_and_secret_content(self):
        secret_dir = self.project / "deploy/secrets"
        secret_dir.mkdir()
        secret = secret_dir / "token"
        secret.write_text("value")
        with self.assertRaises(DeploymentError) as ctx:
            create_backup(
                [secret],
                self.runtime,
                "deploy2",
                allowed_roots=[self.project],
            )
        self.assertEqual(ctx.exception.severity, "security_block")
        source = self.data / "config.json"
        source.write_text("contains token-secret")
        with self.assertRaises(DeploymentError) as ctx:
            create_backup(
                [source],
                self.runtime,
                "deploy3",
                allowed_roots=[self.project],
                secret_sentinels=["token-secret"],
            )
        self.assertEqual(ctx.exception.code, "BACKUP_SECRET_CONTENT_FORBIDDEN")

    def test_preserves_internal_symlink_and_rejects_escape(self):
        target = self.data / "file"
        target.write_text("ok")
        link = self.data / "link"
        link.symlink_to("file")
        manifest = create_backup(
            [link],
            self.runtime,
            "deploy4",
            allowed_roots=[self.project],
        )
        self.assertEqual(manifest.entries[0].kind, "symlink")
        outside = Path(self.temp.name).parent / "outside-target"
        outside.write_text("x")
        escaping = self.data / "escaping"
        escaping.symlink_to(outside)
        with self.assertRaises(DeploymentError) as ctx:
            create_backup(
                [escaping],
                self.runtime,
                "deploy5",
                allowed_roots=[self.project],
            )
        self.assertEqual(ctx.exception.code, "BACKUP_SYMLINK_ESCAPE")
        outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
