import json
import tempfile
import unittest
from pathlib import Path

from deploy.installer.adapters.aria2 import Aria2Adapter
from deploy.installer.command import CommandResult
from deploy.installer.models import ComponentStatus, Severity


class Runner:
    def __init__(self, root):
        self.root = str(root)
        self.calls = []

    def run(self, args, timeout=30):
        key = tuple(args)
        self.calls.append(key)
        if key[-2:] == ("id", "-u"):
            return CommandResult(key, 0, "1001\n", "")
        if key[-2:] == ("id", "-g"):
            return CommandResult(key, 0, "100\n", "")
        if key[-2:] == ("id", "-G"):
            return CommandResult(key, 0, "100 101\n", "")
        if key[:3] == ("docker", "inspect", "aria2"):
            return CommandResult(
                key,
                0,
                json.dumps(
                    [{"Source": self.root, "Destination": "/nas/downloads"}]
                ),
                "",
            )
        raise AssertionError(key)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class Rpc:
    def __init__(self, root):
        self.root = root
        self.requests = []

    def __call__(self, request, timeout=10):
        payload = json.loads(request.data)
        self.requests.append(payload)
        method = payload["method"]
        if method == "aria2.getVersion":
            return Response({"result": {"version": "1.37.0"}})
        if method == "aria2.addUri":
            (self.root / ".incoming/.deploy-probe-demo/probe.bin").write_bytes(b"ok")
            return Response({"result": "gid-demo"})
        return Response({"result": "OK"})


class Aria2AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "downloads"
        (self.root / ".incoming").mkdir(parents=True)
        self.runner = Runner(self.root)
        self.rpc = Rpc(self.root)
        self.adapter = Aria2Adapter(
            "aria2",
            "http://127.0.0.1:6800/jsonrpc",
            "secret-value",
            self.root,
            runner=self.runner,
            opener=self.rpc,
            sleep=lambda _: None,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_runtime_identity_uses_fixed_id_commands(self):
        identity = self.adapter.runtime_identity()
        self.assertEqual(identity.uid, 1001)
        self.assertEqual(identity.gid, 100)
        self.assertEqual(identity.groups, (100, 101))

    def test_rpc_is_authenticated_and_secret_not_reported(self):
        result = self.adapter.verify_rpc()
        self.assertEqual(result.status, ComponentStatus.READY)
        self.assertEqual(self.rpc.requests[0]["params"][0], "token:secret-value")
        self.assertNotIn("secret-value", json.dumps(result.to_dict()))

    def test_mount_source_must_match_openclaw_and_configured_host_path(self):
        ready = self.adapter.verify_mount(self.root)
        self.assertEqual(ready.status, ComponentStatus.READY)
        failed = self.adapter.verify_mount(self.root.parent / "other")
        self.assertEqual(failed.status, ComponentStatus.FAILED)
        self.assertEqual(failed.severity, Severity.SECURITY_BLOCK)

    def test_controlled_write_probe_removes_only_probe_artifacts(self):
        keep = self.root / ".incoming/keep.txt"
        keep.write_text("keep")
        result = self.adapter.verify_write_probe(
            "demo",
            "https://fixture.invalid/probe.bin",
            wait_attempts=1,
        )
        self.assertEqual(result.status, ComponentStatus.READY)
        self.assertTrue(keep.exists())
        self.assertFalse((self.root / ".incoming/.deploy-probe-demo").exists())
        methods = [item["method"] for item in self.rpc.requests]
        self.assertIn("aria2.addUri", methods)
        self.assertIn("aria2.removeDownloadResult", methods)


if __name__ == "__main__":
    unittest.main()
