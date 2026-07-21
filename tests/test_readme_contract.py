import re
import unittest
from pathlib import Path


class ReadmeContractTests(unittest.TestCase):
    def setUp(self):
        self.readme_path = Path(__file__).resolve().parents[1] / "README.md"
        self.assertTrue(self.readme_path.is_file(), "README.md must exist")

    def test_readme_starts_with_agent_first_installation(self):
        content = self.readme_path.read_text(encoding="utf-8")
        first_install = content.index("python3 deploy/cli.py init")
        manual_install = content.index("## 手动部署")
        self.assertLess(first_install, manual_install)
        self.assertIn("确定性部署", content[:manual_install])

    def test_readme_documents_verified_nas_flow_and_safety(self):
        content = self.readme_path.read_text(encoding="utf-8")
        for required in (
            "UGREEN",
            "/nas/downloads",
            "正式媒体库",
            "mediactl",
            "QAS_BASE_URL",
            "PANSOU_BASE_URL",
            "ARIA2_RPC_URL",
            ".env.example",
            "drama",
            "中英字幕",
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

    def test_readme_documents_local_and_aggregated_preview_flow(self):
        content = self.readme_path.read_text(encoding="utf-8")
        self.assertIn("NAS 本地", content)
        self.assertIn("QAS", content)
        self.assertIn("PanSou", content)
        self.assertIn("预览夸克分享", content)
        self.assertIn("candidateId", content)

    def test_repository_declares_mit_license(self):
        license_path = self.readme_path.parent / "LICENSE"
        self.assertTrue(license_path.is_file(), "LICENSE must exist")
        license_text = license_path.read_text(encoding="utf-8")
        readme_text = self.readme_path.read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2026 Inupedia", license_text)
        self.assertIn("MIT License", readme_text)


if __name__ == "__main__":
    unittest.main()
