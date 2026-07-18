import unittest
from pathlib import Path


class SkillContractTests(unittest.TestCase):
    def setUp(self):
        self.skill_path = Path(__file__).resolve().parents[1] / "SKILL.md"
        self.content = self.skill_path.read_text(encoding="utf-8")
        self.frontmatter = self.content.split("---", 2)[1]

    def test_description_is_trigger_rich(self):
        self.assertTrue(
            self.content.startswith("---\nname: resource-download-agent")
        )
        for trigger in (
            "搜索",
            "预览",
            "影视",
            "动画",
            "追更",
            "补集",
            "暂停",
            "转码",
        ):
            self.assertIn(trigger, self.frontmatter)

    def test_skill_exposes_only_fixed_mediactl(self):
        self.assertIn(
            "/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl",
            self.content,
        )
        for forbidden in (
            "python3 ",
            "curl ",
            "/run_script_now",
            "plan-download",
        ):
            self.assertNotIn(forbidden, self.content)

    def test_skill_requires_terminal_stop_and_nas_first_output(self):
        self.assertIn("terminal", self.content)
        self.assertIn("stop_local_exists", self.content)
        self.assertIn("already_up_to_date", self.content)
        self.assertLess(
            self.content.index("NAS 本地"),
            self.content.index("远端候选"),
        )

    def test_skill_declares_download_staging_and_no_improvisation(self):
        self.assertIn("/volume2/downloads", self.content)
        self.assertIn("不要改用其他命令", self.content)
        self.assertIn("不要输出 Cookie", self.content)
        self.assertIn("incremental_selection_unavailable", self.content)


if __name__ == "__main__":
    unittest.main()
