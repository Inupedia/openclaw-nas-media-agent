import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPT_START = "<!-- AGENT_QUICK_DEPLOY_PROMPT_START -->"
PROMPT_END = "<!-- AGENT_QUICK_DEPLOY_PROMPT_END -->"


def extract_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(PROMPT_START) + len(PROMPT_START)
    end = text.index(PROMPT_END, start)
    return text[start:end].strip()


class AgentQuickstartContractTests(unittest.TestCase):
    def test_readme_and_quickstart_share_one_canonical_prompt(self):
        readme_prompt = extract_prompt(ROOT / "README.md")
        quickstart_prompt = extract_prompt(ROOT / "docs/deployment/QUICKSTART.md")
        self.assertEqual(readme_prompt, quickstart_prompt)
        for required in (
            "https://github.com/Inupedia/openclaw-nas-media-agent",
            "AGENTS.md",
            "docs/AGENT_DEPLOY.md",
            "docs/deployment/QUICKSTART.md",
            "docs/deployment/SECURITY.md",
            "docs/deployment/EXISTING_OPENCLAW.md",
            "docs/deployment/QAS_LOGIN.md",
            "docs/deployment/PROXY.md",
            "docs/deployment/TROUBLESHOOTING.md",
            "deploy/cli.py",
            "status",
            "nextAction",
            "verify --level safe",
        ):
            self.assertIn(required, readme_prompt)
        self.assertIn("不要承诺或尝试从空白主机自动安装 OpenClaw 本体", readme_prompt)

    def test_quickstart_tells_user_to_only_copy_and_paste(self):
        text = (ROOT / "docs/deployment/QUICKSTART.md").read_text(encoding="utf-8")
        self.assertIn("只需要做一件事", text)
        self.assertIn("复制下面整段内容", text)
        self.assertIn("粘贴", text)
        self.assertIn("Codex", text)
        self.assertIn("Claude Code", text)
        self.assertIn("Cursor", text)

    def test_agents_file_points_to_authoritative_contract(self):
        agents_path = ROOT / "AGENTS.md"
        self.assertTrue(agents_path.is_file(), "AGENTS.md must exist")
        text = agents_path.read_text(encoding="utf-8")
        for required in (
            "docs/AGENT_DEPLOY.md",
            "deploy/cli.py",
            "status",
            "nextAction",
            "manual_action_required",
        ):
            self.assertIn(required, text)
        self.assertRegex(text, re.compile(r"do not invent|不得.*另一套", re.IGNORECASE))
        self.assertRegex(text, re.compile(r"password|密码", re.IGNORECASE))
        self.assertRegex(text, re.compile(r"token", re.IGNORECASE))

    def test_manual_deployer_sequence_remains_available(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        manual = readme[readme.index("## 手动部署"):]
        commands = (
            "python3 deploy/cli.py init",
            "python3 deploy/cli.py discover",
            "python3 deploy/cli.py plan",
            "python3 deploy/cli.py apply --plan-id PLAN_ID --confirmed",
            "python3 deploy/cli.py verify --level safe",
        )
        previous = -1
        for command in commands:
            position = manual.index(command)
            self.assertGreater(position, previous)
            previous = position

    def test_prompt_contains_no_private_endpoint_or_secret_value(self):
        prompt = extract_prompt(ROOT / "README.md")
        forbidden = (
            r"https?://(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)",
            r"password\s*[:=]\s*[^<\s][^\s]*",
            r"token\s*[:=]\s*[^<\s][^\s]*",
        )
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, prompt, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
