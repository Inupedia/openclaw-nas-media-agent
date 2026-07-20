import json
import tempfile
import unittest
from pathlib import Path

from deploy.installer.executor import ExecutionContext, apply_plan, resume_deployment
from deploy.installer.models import (
    Change,
    ChangePhase,
    DeploymentPlan,
    DeploymentStatus,
)
from deploy.installer.runtime import RuntimePaths


def change(name, action="write_file"):
    return Change(
        name,
        ChangePhase.FILESYSTEM,
        "test",
        action,
        name,
        side_effect=True,
        rollback={"action": "undo", "name": name},
    )


def plan(*changes):
    return DeploymentPlan(
        "plan1",
        1,
        9999999999,
        "c",
        "s",
        "d",
        "m",
        tuple(changes),
    )


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "deploy").mkdir()
        self.runtime = RuntimePaths.for_project(self.project)

    def tearDown(self):
        self.temp.cleanup()

    def test_failure_rolls_back_completed_changes_in_reverse_order(self):
        events = []

        def write(item):
            events.append("do:" + item.id)
            if item.id == "C":
                raise RuntimeError("boom")

        def undo(data):
            events.append("undo:" + data["name"])

        context = ExecutionContext(
            self.runtime,
            self.project,
            {"write_file": write},
            {"undo": undo},
            deployment_id="dep1",
        )
        result = apply_plan(
            plan(change("A"), change("B"), change("C")),
            context,
        )
        self.assertEqual(result.status, DeploymentStatus.ROLLED_BACK)
        self.assertEqual(
            events,
            ["do:A", "do:B", "do:C", "undo:B", "undo:A"],
        )

    def test_rollback_failure_preserves_both_errors(self):
        def write(item):
            if item.id == "B":
                raise RuntimeError("original")

        def undo(data):
            raise RuntimeError("rollback")

        context = ExecutionContext(
            self.runtime,
            self.project,
            {"write_file": write},
            {"undo": undo},
            deployment_id="dep2",
        )
        result = apply_plan(plan(change("A"), change("B")), context)
        self.assertEqual(result.status, DeploymentStatus.FAILED)
        self.assertEqual(len(result.errors), 2)

    def test_manual_gate_resumes_without_repeating_completed_action(self):
        events = []
        gated = {"value": True}

        def write(item):
            events.append(item.id)
            if item.id == "B" and gated["value"]:
                return {
                    "manualActionRequired": True,
                    "nextAction": "login",
                }
            return None

        context = ExecutionContext(
            self.runtime,
            self.project,
            {"write_file": write},
            deployment_id="dep3",
        )
        current_plan = plan(change("A"), change("B"), change("C"))
        first = apply_plan(current_plan, context)
        self.assertEqual(first.status, DeploymentStatus.MANUAL_ACTION_REQUIRED)
        gated["value"] = False
        second = resume_deployment("dep3", current_plan, context)
        self.assertEqual(second.status, DeploymentStatus.READY)
        self.assertEqual(events.count("A"), 1)

    def test_journal_persists_inverse_actions_for_explicit_rollback(self):
        context = ExecutionContext(
            self.runtime,
            self.project,
            {"write_file": lambda item: None},
            deployment_id="dep4",
        )
        result = apply_plan(plan(change("A"), change("B")), context)
        self.assertEqual(result.status, DeploymentStatus.READY)
        journal = json.loads(
            self.runtime.journal_file("dep4").read_text(encoding="utf-8")
        )
        self.assertEqual(
            journal["rollbackActions"],
            [
                {"action": "undo", "name": "A"},
                {"action": "undo", "name": "B"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
