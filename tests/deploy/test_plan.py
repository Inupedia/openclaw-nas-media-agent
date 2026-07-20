import json
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from deploy.installer.cli import run_discover, run_plan
from deploy.installer.command import CommandResult
from deploy.installer.config import load_config
from deploy.installer.discovery import discover
from deploy.installer.errors import DeploymentError
from deploy.installer.models import Change, ChangePhase
from deploy.installer.planning import PlanFacts, build_plan, validate_plan
from deploy.installer.secrets import SecretStore

from .test_config import minimal_config
from .test_discovery import FixtureRunner


def sample_change(
    *,
    change_id="change-1",
    phase=ChangePhase.FILESYSTEM,
    target="deploy/runtime/example.json",
    after=None,
):
    return Change(
        id=change_id,
        phase=phase,
        component="test",
        action="write_file",
        target=target,
        after={"value": 1} if after is None else after,
        side_effect=True,
        rollback={"action": "restore_file"},
    )


class InvalidComposeRunner(FixtureRunner):
    def run(self, args, timeout=30):
        key = tuple(args)
        if (
            len(key) == 7
            and key[:3] == ("docker", "compose", "-f")
            and key[-2:] == ("config", "--quiet")
        ):
            self.calls.append(key)
            return CommandResult(key, 1, "", "invalid compose")
        return super().run(args, timeout=timeout)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project = Path(self.temp_dir.name)
        deploy_dir = self.project / "deploy"
        deploy_dir.mkdir()
        config_path = deploy_dir / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(minimal_config(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        repository_versions = Path(__file__).resolve().parents[2] / "deploy" / "versions.yaml"
        (deploy_dir / "versions.yaml").write_text(
            repository_versions.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.config = load_config(config_path)
        secrets_dir = deploy_dir / "secrets"
        secrets_dir.mkdir(mode=0o700)
        secrets_dir.chmod(0o700)
        for name in self.config.secret_names():
            path = secrets_dir / name
            path.write_text("token-123\n", encoding="utf-8")
            path.chmod(0o600)
        self.secrets = SecretStore(secrets_dir)
        self.discovery = discover(self.config, FixtureRunner())

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_build_plan_expires_after_exactly_thirty_minutes_and_orders_phases(self):
        changes = (
            sample_change(change_id="later", phase=ChangePhase.RESTART, target="container"),
            sample_change(change_id="first", phase=ChangePhase.BACKUP, target="backup"),
        )
        plan = build_plan(self.config, self.secrets, self.discovery, changes, now=1000)
        self.assertEqual(plan.created_at, 1000)
        self.assertEqual(plan.expires_at, 2800)
        self.assertEqual([item.id for item in plan.changes], ["first", "later"])
        self.assertGreaterEqual(len(plan.plan_id), 20)
        self.assertNotIn("token-123", json.dumps(plan.to_dict()))

    def test_conflicting_final_states_are_rejected(self):
        changes = (
            sample_change(change_id="a", target="same", after={"value": 1}),
            sample_change(change_id="b", target="same", after={"value": 2}),
        )
        with self.assertRaises(DeploymentError) as ctx:
            build_plan(self.config, self.secrets, self.discovery, changes, now=1000)
        self.assertEqual(ctx.exception.code, "PLAN_TARGET_CONFLICT")

    def test_plan_expires_after_thirty_minutes(self):
        plan = build_plan(self.config, self.secrets, self.discovery, (), now=1000)
        with self.assertRaisesRegex(DeploymentError, "expired"):
            validate_plan(plan, PlanFacts.from_plan(plan), now=2801)

    def test_secret_metadata_drift_requires_new_plan(self):
        plan = build_plan(self.config, self.secrets, self.discovery, (), now=1000)
        facts = PlanFacts(
            config_digest=plan.config_digest,
            secret_digest="sha256:new",
            discovery_digest=plan.discovery_digest,
            managed_files_digest=plan.managed_files_digest,
        )
        with self.assertRaises(DeploymentError) as ctx:
            validate_plan(plan, facts, now=1100)
        self.assertEqual(ctx.exception.next_action, "regenerate_plan")
        self.assertEqual(ctx.exception.details["drift"], ["secretDigest"])

    def test_all_drift_dimensions_are_checked(self):
        plan = build_plan(self.config, self.secrets, self.discovery, (), now=1000)
        base = PlanFacts.from_plan(plan)
        for field in (
            "config_digest",
            "secret_digest",
            "discovery_digest",
            "managed_files_digest",
        ):
            with self.subTest(field=field):
                values = base.__dict__.copy()
                values[field] = "sha256:changed"
                with self.assertRaises(DeploymentError):
                    validate_plan(plan, PlanFacts(**values), now=1100)

    @unittest.skipUnless(os.name == "posix", "secret mode tests require POSIX")
    def test_cli_discover_and_plan_write_private_reports_without_secrets(self):
        discovery_payload = run_discover(self.project, runner=FixtureRunner())
        self.assertEqual(discovery_payload["status"], "ready")
        report = self.project / "deploy/runtime/reports/discovery.json"
        self.assertTrue(report.is_file())
        self.assertEqual(report.stat().st_mode & 0o777, 0o600)

        plan_payload = run_plan(
            self.project,
            runner=FixtureRunner(),
            changes=(sample_change(after={"value": "token-123"}),),
            now=1000,
        )
        self.assertEqual(plan_payload["status"], "ready_for_apply")
        self.assertEqual(plan_payload["nextAction"], "request_confirmation")
        plan_file = self.project / "deploy/runtime/plan.json"
        content = plan_file.read_text(encoding="utf-8")
        self.assertNotIn("token-123", content)
        self.assertNotIn("token-123", json.dumps(plan_payload))
        self.assertEqual(json.loads(content)["expiresAt"], 2800)
        self.assertTrue((self.project / "deploy/runtime/rendered/compose.dependencies.yml").is_file())
        self.assertTrue((self.project / "deploy/runtime/rendered/routing.json").is_file())

    def test_invalid_rendered_compose_prevents_plan_file(self):
        with self.assertRaises(DeploymentError) as ctx:
            run_plan(
                self.project,
                runner=InvalidComposeRunner(),
                now=1000,
            )
        self.assertEqual(ctx.exception.code, "COMPOSE_VALIDATION_FAILED")
        self.assertFalse((self.project / "deploy/runtime/plan.json").exists())


if __name__ == "__main__":
    unittest.main()
