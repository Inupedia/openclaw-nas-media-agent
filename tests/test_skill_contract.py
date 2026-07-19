import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KNOWN_STATUSES = {
    "starting",
    "submitted",
    "active",
    "waiting",
    "paused",
    "complete",
    "partial_failed",
    "error",
    "ready",
    "quarantined",
    "organized",
    "cancelled",
}
KNOWN_NOTES = {
    "transfer_idle",
    "staging_only",
    "staging_missing",
    "aria2_error_18",
    "aria2_partial_failed",
}
KNOWN_NEXT_ACTIONS = {
    "stop_local_exists",
    "already_up_to_date",
    "choose_candidate",
    "choose_tree_nodes",
    "incremental_selection_unavailable",
    "ready_to_organize",
    "quarantine_download",
    "ready",
    "monitor_download",
    "review_plan",
}


class SkillContractTests(unittest.TestCase):
    def setUp(self):
        self.skill_path = ROOT / "SKILL.md"
        self.content = self.skill_path.read_text(encoding="utf-8")
        self.frontmatter = self.content.split("---", 2)[1]
        self.references = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "references").glob("*.md"))
        )
        self.bundle = f"{self.content}\n{self.references}"

    def test_description_is_trigger_rich_and_bounded(self):
        self.assertTrue(
            self.content.startswith("---\nname: resource-download-agent")
        )
        description = None
        for line in self.frontmatter.splitlines():
            if line.startswith("description:"):
                description = line.split(":", 1)[1].strip()
                break
        self.assertIsNotNone(description)
        self.assertLessEqual(len(description), 160)
        for trigger in (
            "搜索",
            "预览",
            "影视",
            "动画",
            "追更",
            "补集",
            "暂停",
            "删除",
        ):
            self.assertIn(trigger, description)
        self.assertNotIn("转码", description)
        self.assertNotIn("压缩", description)

    def test_skill_exposes_portable_mediactl_entrypoint(self):
        self.assertIn("{baseDir}/bin/mediactl", self.content)
        self.assertNotIn(
            "/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl",
            self.bundle,
        )
        for forbidden in (
            "python3 ",
            "curl ",
            "/run_script_now",
            "plan-download",
        ):
            self.assertNotIn(forbidden, self.bundle)

    def test_skill_declares_every_injected_environment_variable(self):
        self.assertIn("metadata:", self.frontmatter)
        self.assertIn("bins:", self.frontmatter)
        self.assertIn("python3", self.frontmatter)
        self.assertIn("linux", self.frontmatter)
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
        # Optional discovery/download deps must not gate skill loading.
        requires_env = re.search(
            r"requires:\s*\n(?:[ \t]+.+\n)*?[ \t]+env:\s*\n((?:[ \t]+- .+\n)+)",
            self.frontmatter,
        )
        self.assertIsNotNone(requires_env)
        required = requires_env.group(1)
        self.assertIn("QAS_BASE_URL", required)
        self.assertIn("QAS_TOKEN", required)
        self.assertNotIn("PANSOU_BASE_URL", required)
        self.assertNotIn("ARIA2_RPC_URL", required)
        self.assertNotIn("RESOURCE_AGENT_STATE_DB", required)

    def test_skill_requires_terminal_stop_and_nas_first_output(self):
        self.assertIn("terminal", self.content)
        self.assertIn("stop_local_exists", self.content)
        self.assertIn("already_up_to_date", self.content)
        self.assertLess(
            self.content.index("NAS 本地"),
            self.content.index("远端候选"),
        )

    def test_skill_declares_download_staging_and_no_improvisation(self):
        self.assertIn("/volume2/downloads", self.bundle)
        self.assertIn("不得改用其他工具", self.content)
        self.assertIn("不得输出 Cookie", self.content)
        self.assertIn("incremental_selection_unavailable", self.bundle)

    def test_skill_requires_user_selected_specification_choices(self):
        self.assertIn("drama", self.bundle)
        self.assertIn("specificationGroups", self.bundle)
        self.assertIn("中英双语", self.bundle)
        self.assertIn("不得自动", self.bundle)
        self.assertIn("tree", self.bundle)
        self.assertIn("--node", self.bundle)
        self.assertIn("choose_tree_nodes", self.content)
        self.assertIn("execute PLAN_ID --confirmed", self.content)

    def test_skill_permanently_protects_formal_libraries(self):
        self.assertIn("/volume2/影视", self.content)
        self.assertIn("/volume3/临时影视", self.content)
        self.assertIn("永不删除", self.content)
        self.assertIn(
            "拒绝：OpenClaw 不会删除或协助删除受保护媒体库中的内容。",
            self.content,
        )
        self.assertIn("不得提供手工删除命令", self.bundle)
        self.assertIn("不得建议放宽", self.bundle)

    def test_reference_statuses_match_known_enumerations(self):
        statuses_doc = (ROOT / "references" / "statuses.md").read_text(
            encoding="utf-8"
        )
        mentioned_statuses = set(
            re.findall(r"`(partial_failed|complete|ready|quarantined|organized|submitted|error|cancelled|active|waiting|paused|starting)`", statuses_doc)
        )
        self.assertTrue(mentioned_statuses)
        self.assertTrue(mentioned_statuses.issubset(KNOWN_STATUSES))
        self.assertIn("aria2_partial_failed", statuses_doc)
        self.assertNotIn("aria2_mixed", self.bundle)
        for note in KNOWN_NOTES:
            self.assertIn(note, statuses_doc)


if __name__ == "__main__":
    unittest.main()
