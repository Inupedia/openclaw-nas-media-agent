import tempfile
import unittest
from pathlib import Path

from deploy.installer.executor import ExecutionContext
from deploy.installer.models import DeploymentStatus
from deploy.installer.rollback import rollback
from deploy.installer.runtime import RuntimePaths, atomic_write_json


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "deploy").mkdir()
        self.runtime = RuntimePaths.for_project(self.project)

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_journal_from_other_project(self):
        atomic_write_json(
            self.runtime.journal_file("dep"),
            {
                "deploymentId": "dep",
                "projectRoot": "/other",
                "completedChangeIds": [],
            },
        )
        context = ExecutionContext(self.runtime, self.project, {})
        with self.assertRaisesRegex(Exception, "another project"):
            rollback("dep", context)

    def test_explicit_rollback_runs_reverse_actions(self):
        events = []
        atomic_write_json(
            self.runtime.journal_file("dep2"),
            {
                "deploymentId": "dep2",
                "projectRoot": str(self.project.resolve()),
                "completedChangeIds": ["a", "b"],
                "rollbackActions": [
                    {"action": "undo", "name": "a"},
                    {"action": "undo", "name": "b"},
                ],
            },
        )
        context = ExecutionContext(
            self.runtime,
            self.project,
            {},
            {"undo": lambda item: events.append(item["name"])},
        )
        result = rollback("dep2", context)
        self.assertEqual(result.status, DeploymentStatus.ROLLED_BACK)
        self.assertEqual(events, ["b", "a"])


if __name__ == "__main__":
    unittest.main()
