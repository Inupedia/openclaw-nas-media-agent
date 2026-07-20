import unittest
from dataclasses import FrozenInstanceError

from deploy.installer.models import (
    Change,
    ChangePhase,
    ComponentResult,
    ComponentStatus,
    DeploymentPlan,
    DeploymentStatus,
    Severity,
    VerificationResult,
)


class ModelTests(unittest.TestCase):
    def test_status_values_are_closed(self):
        self.assertEqual(
            {item.value for item in DeploymentStatus},
            {"ready", "degraded", "manual_action_required", "failed", "rolled_back"},
        )
        self.assertEqual(
            {item.value for item in Severity},
            {"warning", "degraded", "blocking", "security_block"},
        )

    def test_change_serializes_camel_case_and_is_frozen(self):
        change = Change(
            id="change-1",
            phase=ChangePhase.FILESYSTEM,
            component="runtime",
            action="write_file",
            target="deploy/runtime/plan.json",
            before=None,
            after={"ok": True},
            side_effect=True,
            rollback={"action": "restore_file"},
        )
        self.assertEqual(
            change.to_dict(),
            {
                "id": "change-1",
                "phase": "filesystem",
                "component": "runtime",
                "action": "write_file",
                "target": "deploy/runtime/plan.json",
                "before": None,
                "after": {"ok": True},
                "sideEffect": True,
                "rollback": {"action": "restore_file"},
            },
        )
        with self.assertRaises(FrozenInstanceError):
            change.target = "other"

    def test_plan_round_trip_validates_status_values(self):
        change = Change(
            id="change-1",
            phase=ChangePhase.BACKUP,
            component="runtime",
            action="backup",
            target="config.json",
        )
        plan = DeploymentPlan(
            plan_id="plan-1",
            created_at=100,
            expires_at=1900,
            config_digest="sha256:config",
            secret_digest="sha256:secret",
            discovery_digest="sha256:discovery",
            managed_files_digest="sha256:files",
            changes=(change,),
        )
        restored = DeploymentPlan.from_dict(plan.to_dict())
        self.assertEqual(restored, plan)
        invalid = plan.to_dict()
        invalid["changes"][0]["phase"] = "unknown"
        with self.assertRaises(ValueError):
            DeploymentPlan.from_dict(invalid)

    def test_verification_round_trip_rejects_unknown_component_status(self):
        result = VerificationResult(
            level="L0-L4",
            status=DeploymentStatus.DEGRADED,
            components=(
                ComponentResult(
                    component="pansou",
                    status=ComponentStatus.DEGRADED,
                    required=False,
                    enabled=True,
                    next_action="check_proxy",
                    details={"sourceCount": 0},
                ),
            ),
            checks=({"name": "schema", "ok": True, "severity": Severity.WARNING.value},),
            next_action="check_proxy",
        )
        restored = VerificationResult.from_dict(result.to_dict())
        self.assertEqual(restored, result)
        invalid = result.to_dict()
        invalid["components"][0]["status"] = "unknown"
        with self.assertRaises(ValueError):
            VerificationResult.from_dict(invalid)


if __name__ == "__main__":
    unittest.main()
