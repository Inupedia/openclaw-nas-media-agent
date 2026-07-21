import tempfile
import unittest
from pathlib import Path

import yaml

from deploy.installer.config import load_config
from deploy.installer.discovery import discover
from deploy.installer.models import (
    ComponentResult,
    ComponentStatus,
    DeploymentStatus,
    Severity,
)
from deploy.installer.planning import PlanFacts, build_plan
from deploy.installer.secrets import SecretStore
from deploy.installer.verifier import (
    VerificationContext,
    aggregate_status,
    verify,
)
from deploy.installer.versions import VersionLock
from tests.deploy.test_config import minimal_config
from tests.deploy.test_discovery import FixtureRunner


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        deploy = self.project / "deploy"
        deploy.mkdir()
        raw = minimal_config()
        raw["deployment"]["project_dir"] = str(self.project / "stack")
        raw["nas"]["downloads_dir"] = str(self.project / "downloads")
        raw["nas"]["organizing_dir"] = str(self.project / "organizing")
        for name in raw["nas"]["libraries"]:
            raw["nas"]["libraries"][name] = str(
                self.project / "media" / name
            )
        config_path = deploy / "config.yaml"
        config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.config = load_config(config_path)
        secret_dir = deploy / "secrets"
        secret_dir.mkdir(mode=0o700)
        secret_dir.chmod(0o700)
        for name in self.config.secret_names():
            path = secret_dir / name
            path.write_text("sentinel-" + name, encoding="utf-8")
            path.chmod(0o600)
        self.secrets = SecretStore(secret_dir)
        self.versions = VersionLock.load(
            Path(__file__).resolve().parents[2] / "deploy/versions.yaml"
        )

    def tearDown(self):
        self.temp.cleanup()

    def context(self, **overrides):
        values = dict(
            config=self.config,
            secrets=self.secrets,
            versions=self.versions,
            expected_mediactl=(
                "/workspace/skills/resource-download-agent/bin/mediactl"
            ),
            effective_exec_allowlist=(
                "/workspace/skills/resource-download-agent/bin/mediactl",
            ),
            protected_operation_check=lambda: True,
        )
        values.update(overrides)
        return VerificationContext(**values)

    def test_aggregation_precedence(self):
        optional = ComponentResult(
            "pansou",
            ComponentStatus.DEGRADED,
            False,
            True,
            "check_proxy",
            severity=Severity.DEGRADED,
        )
        self.assertEqual(
            aggregate_status([optional], [])[0],
            DeploymentStatus.DEGRADED,
        )
        gate = ComponentResult(
            "qas",
            ComponentStatus.MANUAL_ACTION_REQUIRED,
            True,
            True,
            "login",
        )
        self.assertEqual(
            aggregate_status([optional, gate], [])[0],
            DeploymentStatus.MANUAL_ACTION_REQUIRED,
        )
        security = {
            "id": "security",
            "ok": False,
            "severity": "security_block",
        }
        self.assertEqual(
            aggregate_status([gate], [security])[0],
            DeploymentStatus.FAILED,
        )

    def test_all_enabled_checks_ready(self):
        ready = ComponentResult("qas", ComponentStatus.READY, True, True)
        result = verify(
            "component",
            self.context(component_checks=(lambda: ready,)),
        )
        self.assertEqual(result.status, DeploymentStatus.READY)
        self.assertTrue(all(item["ok"] for item in result.checks))

    def test_optional_pansou_failure_is_degraded(self):
        failed = ComponentResult(
            "pansou",
            ComponentStatus.FAILED,
            False,
            True,
            "check_pansou",
        )
        result = verify(
            "component",
            self.context(component_checks=(lambda: failed,)),
        )
        self.assertEqual(result.status, DeploymentStatus.DEGRADED)

    def test_report_secret_and_broad_allowlist_are_security_failures(self):
        report = self.project / "report.json"
        report.write_text("sentinel-qas_token", encoding="utf-8")
        result = verify(
            "component",
            self.context(
                report_paths=(report,),
                effective_exec_allowlist=("/bin/bash",),
            ),
        )
        self.assertEqual(result.status, DeploymentStatus.FAILED)
        failed_ids = {
            item["id"] for item in result.checks if not item["ok"]
        }
        self.assertIn("l4.exec_allowlist", failed_ids)
        self.assertIn("l4.report_redaction", failed_ids)

    def test_expired_plan_is_verified_as_rejected(self):
        discovery = discover(self.config, FixtureRunner())
        plan = build_plan(self.config, self.secrets, discovery, (), now=1000)
        result = verify(
            "component",
            self.context(
                plan=plan,
                current_plan_facts=PlanFacts.from_plan(plan),
                now=plan.expires_at,
            ),
        )
        check = next(
            item
            for item in result.checks
            if item["id"] == "l4.expired_plan_rejected"
        )
        self.assertTrue(check["ok"])


if __name__ == "__main__":
    unittest.main()
