import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deploy.installer.errors import DeploymentError
from deploy.installer.runtime import RuntimePaths, atomic_write_json, new_deployment_id, new_plan_id


@unittest.skipUnless(os.name == "posix", "permission assertions require POSIX modes")
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        (self.project_root / "deploy").mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_runtime_paths_match_contract_and_use_private_modes(self):
        paths = RuntimePaths.for_project(self.project_root)
        self.assertEqual(paths.plan_file, self.project_root / "deploy/runtime/plan.json")
        self.assertEqual(
            paths.discovery_report,
            self.project_root / "deploy/runtime/reports/discovery.json",
        )
        self.assertEqual(paths.apply_report, self.project_root / "deploy/runtime/reports/apply.json")
        self.assertEqual(paths.verify_report, self.project_root / "deploy/runtime/reports/verify.json")
        self.assertEqual(
            paths.backup_dir("deployment-1"),
            self.project_root / "deploy/runtime/backups/deployment-1",
        )
        self.assertEqual(
            paths.journal_file("deployment-1"),
            self.project_root / "deploy/runtime/journals/deployment-1.json",
        )
        for directory in (
            paths.root,
            paths.reports_dir,
            paths.backups_dir,
            paths.journals_dir,
            paths.backup_dir("deployment-1"),
        ):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_atomic_json_write_sets_mode_and_round_trips(self):
        paths = RuntimePaths.for_project(self.project_root)
        atomic_write_json(paths.plan_file, {"status": "ready", "name": "测试"})
        self.assertEqual(paths.plan_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            json.loads(paths.plan_file.read_text(encoding="utf-8")),
            {"status": "ready", "name": "测试"},
        )

    def test_failed_replace_preserves_previous_document(self):
        paths = RuntimePaths.for_project(self.project_root)
        atomic_write_json(paths.plan_file, {"version": 1})
        original = paths.plan_file.read_bytes()
        with mock.patch("deploy.installer.runtime.os.replace", side_effect=OSError("stop")):
            with self.assertRaises(OSError):
                atomic_write_json(paths.plan_file, {"version": 2})
        self.assertEqual(paths.plan_file.read_bytes(), original)
        self.assertEqual(list(paths.plan_file.parent.glob(".plan.json.*.tmp")), [])

    def test_symlink_destination_is_rejected(self):
        paths = RuntimePaths.for_project(self.project_root)
        outside = self.project_root / "outside.json"
        outside.write_text('{"safe":true}', encoding="utf-8")
        paths.plan_file.symlink_to(outside)
        with self.assertRaises(DeploymentError) as ctx:
            atomic_write_json(paths.plan_file, {"safe": False})
        self.assertEqual(ctx.exception.severity, "security_block")
        self.assertEqual(json.loads(outside.read_text(encoding="utf-8")), {"safe": True})

    def test_identifiers_are_unpredictable_and_path_safe(self):
        plan_ids = {new_plan_id() for _ in range(20)}
        deployment_ids = {new_deployment_id() for _ in range(20)}
        self.assertEqual(len(plan_ids), 20)
        self.assertEqual(len(deployment_ids), 20)
        for identifier in plan_ids | deployment_ids:
            self.assertGreaterEqual(len(identifier), 20)
            self.assertNotIn("/", identifier)
            self.assertNotIn("..", identifier)

    def test_runtime_root_symlink_is_rejected(self):
        outside = self.project_root / "outside"
        outside.mkdir()
        (self.project_root / "deploy/runtime").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(DeploymentError) as ctx:
            RuntimePaths.for_project(self.project_root)
        self.assertEqual(ctx.exception.severity, "security_block")


if __name__ == "__main__":
    unittest.main()
