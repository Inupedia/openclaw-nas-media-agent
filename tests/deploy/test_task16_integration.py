import json
import tempfile
import unittest
from pathlib import Path

import yaml

from deploy.installer.cli import (
    _build_safe_verify_callback,
    _default_execution_context,
    run_apply,
    run_plan,
)
from deploy.installer.component_integration import atomic_write_secret
from deploy.installer.errors import DeploymentError
from deploy.installer.executor import ExecutionContext
from deploy.installer.runtime import RuntimePaths
from deploy.installer.config import load_config
from deploy.installer.discovery import discover
from deploy.installer.secrets import SecretStore
from tests.deploy.test_business_verification import FixtureExecutor
from deploy.installer.config import load_config
from deploy.installer.adapters.qas_v1 import derive_api_token
from tests.deploy.test_config import minimal_config
from tests.deploy.test_discovery import FixtureRunner


class DefaultIntegrationPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "deploy").mkdir()
        (self.project / "config").mkdir()
        (self.project / "config/routing.json").write_text("{}\n", encoding="utf-8")

        raw = minimal_config()
        raw["deployment"]["project_dir"] = str(self.project / "stack")
        raw["nas"]["downloads_dir"] = str(self.project / "downloads")
        raw["nas"]["organizing_dir"] = str(self.project / "organizing")
        for name in raw["nas"]["libraries"]:
            raw["nas"]["libraries"][name] = str(self.project / "media" / name)

        workspace = self.project / "openclaw-workspace"
        workspace.mkdir()
        openclaw_config = workspace / "openclaw.json"
        fixture = (
            Path(__file__).resolve().parents[1]
            / "fixtures/openclaw-v1/config.json"
        )
        openclaw_config.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
        raw["openclaw"]["workspace_host_dir"] = str(workspace)
        raw["openclaw"]["config_host_path"] = str(openclaw_config)

        (self.project / "deploy/config.yaml").write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        versions = Path(__file__).resolve().parents[2] / "deploy/versions.yaml"
        (self.project / "deploy/versions.yaml").write_text(
            versions.read_text(encoding="utf-8"), encoding="utf-8"
        )
        config = load_config(self.project / "deploy/config.yaml")
        secret_dir = self.project / "deploy/secrets"
        secret_dir.mkdir(mode=0o700)
        secret_dir.chmod(0o700)
        values = {
            config.qas.password_secret: "fixture-password",
            config.qas.api_token_secret: "",
            config.qas.quark_cookie_secret: "fixture-cookie",
            config.aria2.rpc_secret: "fixture-aria-secret",
            config.pansou.proxy.url_secret: "socks5://127.0.0.1:1080",
            config.verification.full_test_share_url_secret: "",
        }
        for name in config.secret_names():
            path = secret_dir / name
            path.write_text(values.get(name, ""), encoding="utf-8")
            path.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_plan_connects_qas_and_openclaw_adapters(self):
        payload = run_plan(self.project, runner=FixtureRunner(), now=1000)
        changes = payload["data"]["changes"]
        ids = {item["id"] for item in changes}
        self.assertTrue(
            {
                "write-derived-qas-token",
                "qas-initialize",
                "qas-restart",
                "qas-verify",
                "write-openclaw-override",
                "openclaw-install-skill",
                "openclaw-write-config",
                "openclaw-compose-up",
                "openclaw-verify",
            }.issubset(ids)
        )
        plan_text = json.dumps(payload, ensure_ascii=False)
        expected_token = derive_api_token("admin", "fixture-password")
        self.assertNotIn(expected_token, plan_text)
        self.assertNotIn("fixture-aria-secret", plan_text)
        derived_change = next(item for item in changes if item["id"] == "write-derived-qas-token")
        self.assertNotIn("digest", derived_change["after"])
        qas_change = next(item for item in changes if item["id"] == "qas-initialize")
        self.assertNotIn("apiTokenDigest", qas_change["after"])

        override = self.project / "deploy/runtime/rendered/compose.openclaw.override.yml"
        content = override.read_text(encoding="utf-8")
        self.assertIn("/run/secrets/openclaw_media_qas_token", content)
        self.assertIn("/run/secrets/openclaw_media_aria2_rpc_secret", content)
        self.assertGreaterEqual(content.count("read_only: true"), 8)




    def test_default_plan_verifies_pansou_and_aria2(self):
        payload = run_plan(self.project, runner=FixtureRunner(), now=1000)
        ids = {item["id"] for item in payload["data"]["changes"]}
        self.assertIn("pansou-verify", ids)
        self.assertIn("aria2-verify", ids)

    def test_managed_proxy_is_rendered_and_joined_to_dependency_compose(self):
        config_path = self.project / "deploy/config.yaml"
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["pansou"]["proxy"] = {
            "mode": "managed",
            "singbox_config_secret": "singbox_config.json",
        }
        config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        secret = self.project / "deploy/secrets/singbox_config.json"
        secret.write_text(
            json.dumps(
                {
                    "inbounds": [
                        {"type": "socks", "listen": "0.0.0.0", "listen_port": 1080}
                    ],
                    "outbounds": [{"type": "direct"}],
                }
            ),
            encoding="utf-8",
        )
        secret.chmod(0o600)

        payload = run_plan(self.project, runner=FixtureRunner(), now=1000)
        changes = payload["data"]["changes"]
        ids = {item["id"] for item in changes}
        self.assertTrue(
            {
                "write-managed-proxy-compose",
                "proxy-verify",
                "pansou-verify",
                "aria2-verify",
            }.issubset(ids)
        )
        compose_change = next(
            item for item in changes if item["id"] == "compose-dependencies-up"
        )
        argv = compose_change["after"]["argv"]
        proxy_target = str(self.project / "stack/compose.proxy.yml")
        self.assertIn(proxy_target, argv)
        rendered = self.project / "deploy/runtime/rendered/compose.proxy.yml"
        content = rendered.read_text(encoding="utf-8")
        self.assertIn("openclaw-media-proxy", content)
        self.assertNotIn("ports:", content)
        self.assertNotIn("outbounds", json.dumps(payload, ensure_ascii=False))

    def test_default_context_registers_component_handlers_and_writes_derived_secret(self):
        config = load_config(self.project / "deploy/config.yaml")
        secrets = SecretStore(self.project / "deploy/secrets")
        report = discover(config, FixtureRunner())
        runtime = RuntimePaths.for_project(self.project)
        context = _default_execution_context(
            self.project,
            config,
            secrets,
            runtime,
            report,
            verify_safe_callback=lambda: None,
        )
        self.assertTrue(
            {"copy_tree", "http_config_update", "restart_container", "run_verification"}
            .issubset(context.handlers)
        )
        planned = run_plan(self.project, runner=FixtureRunner(), now=1000)
        change = next(
            item for item in planned["data"]["changes"]
            if item["id"] == "write-derived-qas-token"
        )
        from deploy.installer.models import Change
        context.handlers["write_file"](Change.from_dict(change))
        target = Path(change["target"])
        self.assertEqual(
            target.read_text(encoding="utf-8").strip(),
            derive_api_token("admin", "fixture-password"),
        )
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_default_safe_callback_runs_the_non_mutating_business_flow(self):
        config = load_config(self.project / "deploy/config.yaml")
        secrets = SecretStore(self.project / "deploy/secrets")
        callback = _build_safe_verify_callback(
            self.project,
            config,
            secrets,
            FixtureExecutor(),
        )
        result = callback()
        self.assertEqual(result.status.value, "ready")

    def test_existing_secret_bearing_qas_config_is_not_copied_into_general_backup(self):
        qas_config = self.project / "stack/qas/config/config/quark_config.json"
        qas_config.parent.mkdir(parents=True)
        fixture = json.loads(
            (Path(__file__).resolve().parents[1] / "fixtures/qas-v1/config.json").read_text(encoding="utf-8")
        )
        fixture["webui"]["password"] = "fixture-password"
        fixture["cookie"] = ["fixture-cookie"]
        fixture["plugins"]["aria2"]["secret"] = "fixture-aria-secret"
        qas_config.write_text(json.dumps(fixture), encoding="utf-8")
        planned = run_plan(self.project, runner=FixtureRunner(), now=1000)
        runtime = RuntimePaths.for_project(self.project)
        action_names = {
            "create_directory", "write_file", "copy_tree", "create_network",
            "compose_up", "http_config_update", "restart_container", "run_verification",
        }
        context = ExecutionContext(
            runtime,
            self.project,
            {name: (lambda change: None) for name in action_names},
            deployment_id="task16-backup",
        )
        applied = run_apply(
            self.project,
            plan_id=planned["data"]["planId"],
            confirmed=True,
            runner=FixtureRunner(),
            execution_context=context,
            now=1100,
        )
        self.assertEqual(applied["status"], "ready")

    def test_private_secret_writer_rejects_symlink_parent(self):
        outside = self.project / "outside"
        outside.mkdir()
        parent = self.project / "deploy/runtime/linked-secrets"
        parent.parent.mkdir(parents=True, exist_ok=True)
        parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(DeploymentError) as ctx:
            atomic_write_secret(parent / "qas_token", "secret")
        self.assertEqual(ctx.exception.severity, "security_block")
        self.assertFalse((outside / "qas_token").exists())


if __name__ == "__main__":
    unittest.main()
