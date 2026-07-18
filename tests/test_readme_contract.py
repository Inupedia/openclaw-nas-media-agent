import re
import unittest
from pathlib import Path


class ReadmeContractTests(unittest.TestCase):
    def setUp(self):
        self.readme_path = Path(__file__).resolve().parents[1] / "README.md"
        self.assertTrue(self.readme_path.is_file(), "README.md must exist")

    def test_readme_starts_with_agent_first_installation(self):
        content = self.readme_path.read_text(encoding="utf-8")
        first_install = content.index("请把这个 GitHub 项目安装到我的 NAS")
        manual_install = content.index("git clone")
        self.assertLess(first_install, manual_install)

    def test_readme_documents_verified_nas_flow_and_safety(self):
        content = self.readme_path.read_text(encoding="utf-8")
        for required in (
            "UGREEN",
            "/volume2/downloads",
            "/volume2/影视",
            "/volume3/临时影视",
            "mediactl",
            "QAS_BASE_URL",
            "ARIA2_RPC_URL",
            "drama",
            "中英双语",
            "https://github.com/Inupedia/openclaw-nas-media-agent.git",
        ):
            self.assertIn(required, content)

    def test_readme_contains_no_private_endpoint_or_credential_value(self):
        content = self.readme_path.read_text(encoding="utf-8")
        forbidden_patterns = (
            r"https?://(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)",
            r"OPENCLAW_GATEWAY_TOKEN\s*:\s*[\"'][^<][^\"']+[\"']",
            r"DEVICE_ID\s*:\s*[a-fA-F0-9]{16,}",
            r"password\s*[:=]\s*[\"'][^<][^\"']+[\"']",
        )
        for pattern in forbidden_patterns:
            self.assertIsNone(re.search(pattern, content, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
