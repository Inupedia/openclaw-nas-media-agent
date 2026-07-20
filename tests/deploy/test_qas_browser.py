import json
import unittest

from deploy.installer.adapters.qas_browser import QasBrowserFallback
from deploy.installer.adapters.qas_v1 import QasDesiredState, derive_api_token
from deploy.installer.command import CommandResult
from deploy.installer.errors import DeploymentError


class FixtureRunner:
    def __init__(self, payload=None, returncode=0):
        self.payload = payload or {
            "status": "manual_action_required",
            "nextAction": "open_qas_webui",
            "details": {"gate": "login_or_identity_challenge"},
        }
        self.returncode = returncode
        self.calls = []

    def run(self, args, timeout=30):
        self.calls.append((tuple(args), timeout))
        return CommandResult(
            tuple(args),
            self.returncode,
            json.dumps(self.payload),
            "failed" if self.returncode else "",
        )


class QasBrowserTests(unittest.TestCase):
    def setUp(self):
        password = "browser-password"
        self.desired = QasDesiredState(
            username="admin",
            password=password,
            api_token=derive_api_token("admin", password),
            cookies=("cookie-secret",),
            aria2_host_port="aria2:6800",
            aria2_secret="rpc-secret",
        )
        self.image = (
            "mcr.microsoft.com/playwright/python:v1.61.0-noble@sha256:"
            + "a" * 64
        )

    def test_runs_ephemeral_locked_container_without_secret_args(self):
        runner = FixtureRunner()
        result = QasBrowserFallback(self.image).run(
            "http://qas:5005",
            self.desired,
            runner,
        )
        self.assertEqual(result.next_action, "open_qas_webui")
        args, timeout = runner.calls[0]
        self.assertEqual(
            args[:4],
            ("docker", "run", "--rm", "--network"),
        )
        self.assertIn("openclaw-media", args)
        self.assertIn(self.image, args)
        self.assertEqual(timeout, 90)
        joined = " ".join(args)
        self.assertNotIn("browser-password", joined)
        self.assertNotIn("cookie-secret", joined)
        self.assertNotIn("rpc-secret", joined)

    def test_known_non_auth_page_can_submit_and_continue(self):
        runner = FixtureRunner(
            {
                "status": "ready",
                "nextAction": "verify_qas",
                "details": {"submitted": True},
            }
        )
        result = QasBrowserFallback(self.image).run(
            "http://qas:5005",
            self.desired,
            runner,
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.next_action, "verify_qas")

    def test_browser_failure_requires_manual_webui(self):
        runner = FixtureRunner(returncode=1)
        with self.assertRaises(DeploymentError) as ctx:
            QasBrowserFallback(self.image).run(
                "http://qas:5005",
                self.desired,
                runner,
            )
        self.assertEqual(ctx.exception.status, "manual_action_required")
        self.assertEqual(ctx.exception.next_action, "open_qas_webui")


if __name__ == "__main__":
    unittest.main()
