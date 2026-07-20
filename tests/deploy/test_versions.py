import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deploy.installer.command import CommandResult
from deploy.installer.errors import DeploymentError
from deploy.installer.versions import VersionLock, resolve_image_digest


class VersionTests(unittest.TestCase):
    def test_rejects_latest(self):
        with self.assertRaises(DeploymentError) as ctx:
            VersionLock.from_dict(
                {"qas": {"image": "cp0204/quark-auto-save:latest", "adapter": "qas_v1"}}
            )
        self.assertEqual(ctx.exception.severity, "security_block")

    def test_requires_sha256_digest(self):
        with self.assertRaises(DeploymentError) as ctx:
            VersionLock.from_dict(
                {"qas": {"image": "cp0204/quark-auto-save:v0.8.7", "adapter": "qas_v1"}}
            )
        self.assertEqual(ctx.exception.code, "IMAGE_DIGEST_REQUIRED")

    def test_rejects_invalid_or_uppercase_digest(self):
        for digest in ("sha256:1234", "sha256:" + "A" * 64):
            with self.subTest(digest=digest):
                with self.assertRaises(DeploymentError):
                    VersionLock.from_dict(
                        {
                            "qas": {
                                "image": f"cp0204/quark-auto-save@{digest}",
                                "adapter": "qas_v1",
                            }
                        }
                    )

    def test_loads_committed_lock_and_exposes_digest_only_images(self):
        lock_path = Path(__file__).resolve().parents[2] / "deploy" / "versions.yaml"
        lock = VersionLock.load(lock_path)
        self.assertEqual(
            set(lock.components),
            {"qas", "pansou", "aria2", "sing_box", "playwright"},
        )
        for component in lock.components:
            image = lock.image(component)
            self.assertIn("@sha256:", image)
            self.assertNotIn(":latest", image)
            self.assertRegex(image, r"@sha256:[0-9a-f]{64}$")
        self.assertEqual(lock.adapter("qas"), "qas_v1")

    def test_missing_required_component_fails_on_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "versions.yaml"
            path.write_text(
                "schema_version: 1\ncomponents:\n  qas:\n"
                "    image: cp0204/quark-auto-save@sha256:"
                + "1" * 64
                + "\n    adapter: qas_v1\n",
                encoding="utf-8",
            )
            with self.assertRaises(DeploymentError) as ctx:
                VersionLock.load(path)
            self.assertEqual(ctx.exception.code, "VERSION_COMPONENT_MISSING")

    def test_maintainer_resolver_returns_repository_at_digest(self):
        digest = "sha256:" + "a" * 64
        runner = mock.Mock()
        runner.run.return_value = CommandResult(
            ("docker",), 0, digest + "\n", ""
        )
        resolved = resolve_image_digest(
            "registry.example:5000/team/image:v1.2.3",
            runner,
        )
        self.assertEqual(
            resolved,
            f"registry.example:5000/team/image@{digest}",
        )
        runner.run.assert_called_once_with(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                "registry.example:5000/team/image:v1.2.3",
                "--format",
                "{{.Manifest.Digest}}",
            ],
            timeout=120,
        )

    def test_resolver_failure_never_preserves_mutable_reference(self):
        runner = mock.Mock()
        runner.run.return_value = CommandResult(("docker",), 1, "", "not found")
        with self.assertRaises(DeploymentError) as ctx:
            resolve_image_digest("example/image:latest", runner)
        self.assertEqual(ctx.exception.code, "IMAGE_DIGEST_RESOLUTION_FAILED")
        self.assertNotIn("not found", ctx.exception.details.get("image", ""))


if __name__ == "__main__":
    unittest.main()
