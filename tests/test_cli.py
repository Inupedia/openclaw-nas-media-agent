import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from resource_agent import (
    AgentError,
    ResourceAgent,
    _load_runtime,
    _pansou_limit,
    main,
)
from state_store import StateStore


class FakeAria:
    def __init__(self):
        self.active = []
        self.waiting = []
        self.stopped = []
        self.calls = []

    def get_version(self):
        return {"version": "1.36.0"}

    def tell_active(self):
        return self.active

    def tell_waiting(self):
        return self.waiting

    def tell_stopped(self):
        return self.stopped

    def pause(self, gid):
        self.calls.append(("pause", gid))
        return gid

    def unpause(self, gid):
        self.calls.append(("resume", gid))
        return gid

    def remove(self, gid):
        self.calls.append(("cancel", gid))
        return gid

    def remove_result(self, gid):
        self.calls.append(("remove_result", gid))
        return gid


class FakeQas:
    def __init__(self, config=None):
        self.config = config or {"cookie": "configured"}

    def get_config(self):
        return self.config


class ResourceAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp_dir.name) / "state.db")
        self.aria = FakeAria()
        self.agent = ResourceAgent(
            store=self.store,
            qas=FakeQas(),
            aria=self.aria,
        )
        self.store.upsert_task(
            {
                "task_id": "rd-test",
                "title": "Test",
                "media_type": "movie",
                "qas_task_name": "Test",
                "aria2_gids": [],
                "aria2_dir": "/nas/临时影视/.incoming/rd-test",
                "staging_path": "/volume3/临时影视/.incoming/rd-test",
                "final_path": "/volume3/临时影视/Movie/Test",
                "status": "submitted",
            }
        )

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_list_correlates_aria_task_and_calculates_progress(self):
        self.aria.active = [
            {
                "gid": "abc",
                "status": "active",
                "totalLength": "1048576",
                "completedLength": "131072",
                "downloadSpeed": "65536",
                "dir": "/nas/临时影视/.incoming/rd-test",
                "files": [],
            }
        ]

        result = self.agent.downloads_list()

        task = result["tasks"][0]
        self.assertEqual(task["taskId"], "rd-test")
        self.assertEqual(task["aria2Gids"], ["abc"])
        self.assertEqual(task["progress"], 12.5)
        self.assertEqual(task["etaSeconds"], 14)

    def test_pause_only_controls_managed_gid(self):
        self.aria.active = [
            {
                "gid": "abc",
                "status": "active",
                "totalLength": "100",
                "completedLength": "10",
                "downloadSpeed": "1",
                "dir": "/nas/临时影视/.incoming/rd-test",
                "files": [],
            },
            {
                "gid": "foreign",
                "status": "active",
                "totalLength": "100",
                "completedLength": "10",
                "downloadSpeed": "1",
                "dir": "/downloads/not-managed",
                "files": [],
            },
        ]

        self.agent.downloads_control("rd-test", "pause")

        self.assertEqual(self.aria.calls, [("pause", "abc")])

    def test_unknown_task_is_rejected(self):
        with self.assertRaisesRegex(AgentError, "task not found"):
            self.agent.downloads_control("missing", "pause")

    def test_task_without_correlated_gid_is_rejected(self):
        with self.assertRaisesRegex(AgentError, "no managed aria2 task"):
            self.agent.downloads_control("rd-test", "cancel")

    def test_cancel_uses_remove_result_for_stopped_task(self):
        self.aria.stopped = [
            {
                "gid": "abc",
                "status": "error",
                "totalLength": "100",
                "completedLength": "0",
                "downloadSpeed": "0",
                "dir": "/nas/临时影视/.incoming/rd-test",
                "files": [],
                "errorMessage": "Download aborted.",
            }
        ]

        self.agent.downloads_control("rd-test", "cancel")

        self.assertEqual(self.aria.calls, [("remove_result", "abc")])

    def test_check_ready_reports_ready(self):
        roots = [
            Path(self.temp_dir.name) / "volume2",
            Path(self.temp_dir.name) / "volume3",
        ]
        for root in roots:
            root.mkdir()

        result = self.agent.check_ready(roots)

        self.assertTrue(result["ok"])
        self.assertEqual(result["nextAction"], "ready")

    def test_check_ready_reports_missing_cookie(self):
        agent = ResourceAgent(
            store=self.store,
            qas=FakeQas(config={"cookie": ""}),
            aria=self.aria,
        )

        result = agent.check_ready([Path(self.temp_dir.name)])

        self.assertFalse(result["ok"])
        self.assertEqual(result["nextAction"], "configure_qas_cookie")

    def test_main_emits_versioned_safe_error(self):
        output = io.StringIO()

        with patch.dict(os.environ, {}, clear=True):
            with contextlib.redirect_stdout(output):
                exit_code = main(["check-ready"])

        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["schemaVersion"], 1)
        self.assertTrue(result["terminal"])
        self.assertEqual(result["error"]["code"], "AGENT_ERROR")
        self.assertNotIn("traceback", output.getvalue().lower())

    def test_pansou_candidate_limit_is_safely_bounded(self):
        self.assertEqual(_pansou_limit(None), 50)
        self.assertEqual(_pansou_limit("25"), 25)
        for invalid in ("invalid", "0", "-1", "101"):
            self.assertEqual(_pansou_limit(invalid), 50)

    def test_runtime_loader_creates_private_pansou_client(self):
        state_path = Path(self.temp_dir.name) / "runtime-state.db"
        environment = {
            "QAS_BASE_URL": "http://qas.invalid",
            "QAS_TOKEN": "qas-secret",
            "PANSOU_BASE_URL": "http://pansou.invalid",
            "PANSOU_MAX_CANDIDATES": "25",
            "ARIA2_RPC_URL": "http://aria.invalid",
            "ARIA2_RPC_SECRET": "aria-secret",
            "RESOURCE_AGENT_STATE_DB": str(state_path),
        }

        with patch.dict(os.environ, environment, clear=True):
            runtime = _load_runtime()

        self.assertEqual(len(runtime), 6)
        pansou = runtime[5]
        self.assertEqual(pansou.max_candidates, 25)
        self.assertNotIn(environment["PANSOU_BASE_URL"], repr(pansou))
        runtime[1].close()


if __name__ == "__main__":
    unittest.main()
