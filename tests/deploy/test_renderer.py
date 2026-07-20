import json
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from deploy.installer.command import CommandResult
from deploy.installer.config import load_config
from deploy.installer.errors import DeploymentError
from deploy.installer.renderer import (
    build_compose_context,
    build_routing,
    render_template,
    validate_compose,
    validate_json,
)
from deploy.installer.versions import VersionLock

from .test_config import minimal_config


class ValidationRunner:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def run(self, args, timeout=30):
        self.calls.append((tuple(args), timeout))
        return CommandResult(
            tuple(args),
            self.returncode,
            "" if self.returncode == 0 else "invalid",
            "" if self.returncode == 0 else "validation failed",
        )


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        config_path = self.root / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(minimal_config(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.config = load_config(config_path)
        self.versions = VersionLock.load(
            Path(__file__).resolve().parents[2] / "deploy" / "versions.yaml"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @unittest.skipUnless(os.name == "posix", "file mode assertions require POSIX")
    def test_compose_render_is_digest_locked_private_and_secret_free(self):
        destination = self.root / "compose.dependencies.yml"
        context = build_compose_context(self.config, self.versions)
        rendered = render_template(
            "compose.dependencies.yml.j2",
            context,
            destination,
            mode=0o644,
        )
        content = destination.read_text(encoding="utf-8")
        self.assertEqual(rendered.path, destination)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
        self.assertNotIn("latest", content)
        self.assertIn("@sha256:", content)
        self.assertIn("127.0.0.1:${QAS_PORT:-5005}:5005", content)
        self.assertIn("127.0.0.1:${PANSOU_PORT:-8888}:8888", content)
        self.assertIn("127.0.0.1:${ARIA2_RPC_PORT:-6800}:6800", content)
        self.assertIn("/nas/downloads", content)
        self.assertIn("openclaw-media", content)
        self.assertIn("${QAS_WEBUI_PASSWORD:?loaded by deployer}", content)
        self.assertIn("${ARIA2_RPC_SECRET:?loaded by deployer}", content)
        self.assertNotIn("token-123", content)
        parsed = yaml.safe_load(content)
        self.assertEqual(
            parsed["services"]["aria2"]["volumes"][1]["target"],
            "/nas/downloads",
        )

    def test_missing_template_context_is_blocking(self):
        with self.assertRaises(DeploymentError) as ctx:
            render_template(
                "compose.dependencies.yml.j2",
                {},
                self.root / "compose.yml",
            )
        self.assertEqual(ctx.exception.code, "TEMPLATE_CONTEXT_MISSING")

    def test_symlink_destination_is_rejected(self):
        outside = self.root / "outside.yml"
        outside.write_text("safe: true\n", encoding="utf-8")
        link = self.root / "compose.yml"
        link.symlink_to(outside)
        with self.assertRaises(DeploymentError) as ctx:
            render_template(
                "compose.dependencies.yml.j2",
                build_compose_context(self.config, self.versions),
                link,
            )
        self.assertEqual(ctx.exception.severity, "security_block")
        self.assertEqual(outside.read_text(encoding="utf-8"), "safe: true\n")

    @unittest.skipUnless(os.name == "posix", "file mode assertions require POSIX")
    def test_routing_contains_all_media_types_and_minimal_protected_parents(self):
        routing = build_routing(self.config)
        self.assertEqual(
            set(routing),
            {
                "movie",
                "tv",
                "drama",
                "anime",
                "documentary",
                "show",
                "other",
                "downloads",
                "paths",
            },
        )
        self.assertEqual(routing["tv"]["final_root"], routing["drama"]["final_root"])
        self.assertEqual(
            routing["paths"]["protected_roots"],
            ["/volume2/media", "/volume3/media"],
        )
        self.assertNotIn("/volume2/downloads", routing["paths"]["protected_roots"])
        destination = self.root / "routing.json"
        rendered = render_template(
            "routing.json.j2",
            {"routing_json": json.dumps(routing, ensure_ascii=False, indent=2)},
            destination,
            mode=0o600,
        )
        self.assertEqual(rendered.path, destination)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), routing)

    def test_static_validators_use_argv_and_block_failures(self):
        compose = self.root / "compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        routing = self.root / "routing.json"
        routing.write_text("{}\n", encoding="utf-8")
        runner = ValidationRunner()
        validate_compose(compose, runner)
        validate_json(routing, runner)
        self.assertEqual(
            runner.calls[0],
            (
                (
                    "env",
                    "QAS_WEBUI_PASSWORD=__OPENCLAW_VALIDATION_ONLY__",
                    "ARIA2_RPC_SECRET=__OPENCLAW_VALIDATION_ONLY__",
                    "docker",
                    "compose",
                    "-f",
                    str(compose),
                    "config",
                    "--quiet",
                ),
                60,
            ),
        )
        self.assertEqual(runner.calls[1][0][1:3], ("-m", "json.tool"))

        failing = ValidationRunner(returncode=1)
        with self.assertRaises(DeploymentError) as ctx:
            validate_compose(compose, failing)
        self.assertEqual(ctx.exception.code, "COMPOSE_VALIDATION_FAILED")
        self.assertEqual(ctx.exception.severity, "blocking")

    def test_rendered_digest_is_stable(self):
        context = build_compose_context(self.config, self.versions)
        first = render_template(
            "compose.dependencies.yml.j2",
            context,
            self.root / "first.yml",
        )
        second = render_template(
            "compose.dependencies.yml.j2",
            context,
            self.root / "second.yml",
        )
        self.assertEqual(first.digest, second.digest)
        self.assertRegex(first.digest, r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
