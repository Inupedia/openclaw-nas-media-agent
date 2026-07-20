import re
import shlex
import tempfile
import unittest
from pathlib import Path

from deploy.installer.cli import build_parser
from deploy.installer.config import load_config
from deploy.installer.renderer import build_compose_context, render_template
from deploy.installer.versions import VersionLock


ROOT = Path(__file__).resolve().parents[2]
DOCS = [
    ROOT / "README.md",
    ROOT / "docs/AGENT_DEPLOY.md",
    *sorted((ROOT / "docs/deployment").glob("*.md")),
]


class DocumentationExamplesTests(unittest.TestCase):
    def test_documented_deployer_commands_match_real_parser(self):
        parser = build_parser()
        commands = []
        pattern = re.compile(r"^python3 deploy/cli\.py\s+(.+)$", re.MULTILINE)
        for path in DOCS:
            commands.extend(pattern.findall(path.read_text(encoding="utf-8")))
        self.assertGreaterEqual(len(commands), 10)
        for command in commands:
            with self.subTest(command=command):
                parser.parse_args(shlex.split(command))

    def test_committed_compose_is_generated_from_template_and_lock(self):
        config = load_config(ROOT / "deploy/config.example.yaml")
        versions = VersionLock.load(ROOT / "deploy/versions.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            generated = Path(tmp) / "compose.yml"
            render_template(
                "compose.dependencies.yml.j2",
                build_compose_context(config, versions),
                generated,
                mode=0o644,
            )
            self.assertEqual(
                generated.read_text(encoding="utf-8"),
                (ROOT / "deploy/docker-compose.dependencies.yml").read_text(encoding="utf-8"),
            )
        text = (ROOT / "deploy/docker-compose.dependencies.yml").read_text()
        self.assertNotIn(":latest", text)
        self.assertNotIn("fixture-password", text)


if __name__ == "__main__":
    unittest.main()
