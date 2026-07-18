import unittest
from pathlib import Path


class SkillContractTests(unittest.TestCase):
    def test_skill_declares_runtime_and_safety_contract(self):
        skill_path = Path(__file__).resolve().parents[1] / "SKILL.md"

        content = skill_path.read_text(encoding="utf-8")

        self.assertTrue(content.startswith("---\nname: resource-download-agent"))
        for required in (
            "QAS_BASE_URL",
            "QAS_TOKEN",
            "ARIA2_RPC_URL",
            "ARIA2_RPC_SECRET",
            "check-ready",
            "--json",
            "/volume2/影视",
            "/volume3/临时影视",
            "不要输出 Cookie",
            "确认",
        ):
            self.assertIn(required, content)


if __name__ == "__main__":
    unittest.main()
