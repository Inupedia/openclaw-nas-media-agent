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
            "删除",
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

    def test_skill_declares_every_injected_environment_variable(self):
        self.assertIn("metadata:", self.frontmatter)
        for variable in (
            "QAS_BASE_URL",
            "QAS_TOKEN",
            "PANSOU_BASE_URL",
            "PANSOU_MAX_CANDIDATES",
            "ARIA2_RPC_URL",
            "ARIA2_RPC_SECRET",
            "RESOURCE_AGENT_STATE_DB",
        ):
            self.assertIn(variable, self.frontmatter)

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

    def test_skill_requires_user_selected_specification_choices(self):
        self.assertIn("drama", self.content)
        self.assertIn("specificationGroups", self.content)
        self.assertIn("中英双语", self.content)
        self.assertIn("不得自动选择", self.content)

    def test_skill_permanently_protects_formal_libraries(self):
        self.assertIn("/volume2/影视", self.content)
        self.assertIn("/volume3/临时影视", self.content)
        self.assertIn("永远不得删除", self.content)
        self.assertIn("不得提供手工删除命令", self.content)
        self.assertIn("不得建议放宽", self.content)
        self.assertIn("不要提供替代删除途径或下一步", self.content)
        self.assertIn("整条回复必须且只能是", self.content)


if __name__ == "__main__":
    unittest.main()
