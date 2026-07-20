import io
import os
import tempfile
import unittest
from pathlib import Path

from deploy.installer.initializer import run_init


class InitWizardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "deploy").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_interactive_wizard_writes_config_and_empty_private_secrets(self):
        media = self.project / "media"
        values = [
            "ugos",
            str(self.project / "stack"),
            str(self.project / "downloads"),
            str(media),
            str(media / "Movie"),
            str(media / "Drama"),
            str(media / "Anime"),
            str(media / "Documentary"),
            str(media / "Shows"),
            str(media / "Others"),
            "existing",
        ]
        prompts = io.StringIO()
        config, created = run_init(
            io.StringIO("\n".join(values) + "\n"),
            prompts,
            self.project,
        )
        self.assertEqual(config.platform, "ugos")
        self.assertTrue((self.project / "deploy/config.yaml").is_file())
        secret_dir = self.project / "deploy/secrets"
        self.assertEqual(secret_dir.stat().st_mode & 0o777, 0o700)
        self.assertIn("pansou_proxy_url", created)
        for name in config.secret_names():
            path = secret_dir / name
            self.assertTrue(path.is_file())
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), "")
        text = prompts.getvalue().casefold()
        self.assertNotIn("cookie value", text)
        self.assertNotIn("api key", text)

    def test_noninteractive_validates_source_and_rejects_symlink(self):
        source = self.project / "source.yaml"
        source.write_text(
            (Path(__file__).resolve().parents[2] / "deploy/config.example.yaml").read_text(),
            encoding="utf-8",
        )
        config, _ = run_init(
            io.StringIO(),
            io.StringIO(),
            self.project,
            non_interactive=True,
            config_source=source,
        )
        self.assertEqual(config.mode, "existing-openclaw")
        link = self.project / "source-link.yaml"
        link.symlink_to(source)
        with self.assertRaisesRegex(Exception, "symlink"):
            run_init(
                io.StringIO(),
                io.StringIO(),
                self.project,
                non_interactive=True,
                config_source=link,
            )


if __name__ == "__main__":
    unittest.main()
