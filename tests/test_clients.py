import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from aria2_client import Aria2Client
from qas_client import ClientError, QasClient, share_url_for_directory


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class QasClientTests(unittest.TestCase):
    def test_get_config_adds_token_to_query_and_hides_it_from_repr(self):
        opener = Mock(return_value=FakeResponse({"success": True, "data": {}}))
        client = QasClient(
            "http://nas:5005",
            "secret token",
            opener=opener,
        )

        result = client.get_config()

        request = opener.call_args.args[0]
        self.assertIn("token=secret+token", request.full_url)
        self.assertEqual(result, {})
        self.assertNotIn("secret token", repr(client))

    def test_get_share_posts_json(self):
        opener = Mock(
            return_value=FakeResponse(
                {
                    "success": True,
                    "data": {
                        "share": {"title": "Example"},
                        "list": [{"file_name": "Example.mkv", "dir": False}],
                    },
                }
            )
        )
        client = QasClient("http://nas:5005", "token", opener=opener)

        result = client.get_share("https://pan.quark.cn/s/example")

        request = opener.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            json.loads(request.data),
            {"shareurl": "https://pan.quark.cn/s/example"},
        )
        self.assertEqual(result["share"]["title"], "Example")

    def test_share_url_for_directory_builds_deep_link(self):
        self.assertEqual(
            share_url_for_directory("https://pan.quark.cn/s/abc/", "fid-1"),
            "https://pan.quark.cn/s/abc#/list/share/fid-1",
        )

    def test_get_share_posts_stoken_and_deep_link(self):
        opener = Mock(
            return_value=FakeResponse(
                {
                    "success": True,
                    "data": {"list": [], "stoken": "tok"},
                }
            )
        )
        client = QasClient("http://nas:5005", "token", opener=opener)

        client.get_share(
            "https://pan.quark.cn/s/example",
            stoken="tok",
            pdir_fid="dir-fid",
        )

        body = json.loads(opener.call_args.args[0].data)
        self.assertEqual(
            body["shareurl"],
            "https://pan.quark.cn/s/example#/list/share/dir-fid",
        )
        self.assertEqual(body["stoken"], "tok")

    def test_get_share_preview_samples_one_random_folder(self):
        root_url = "https://pan.quark.cn/s/crime"
        responses = {
            root_url: {
                "success": True,
                "data": {
                    "share": {
                        "title": "犯罪心理S01~S16",
                        "size": 202848184964,
                        "file_only_num": 327,
                    },
                    "stoken": "st",
                    "list": [
                        {
                            "file_name": "犯罪心理S01~S16",
                            "dir": True,
                            "fid": "root-dir",
                            "include_items": 16,
                        }
                    ],
                },
            },
            f"{root_url}#/list/share/root-dir": {
                "success": True,
                "data": {
                    "stoken": "st",
                    "list": [
                        {"file_name": "S01", "dir": True, "fid": "s01"},
                        {"file_name": "S02", "dir": True, "fid": "s02"},
                    ],
                },
            },
            f"{root_url}#/list/share/s01": {
                "success": True,
                "data": {
                    "list": [
                        {
                            "file_name": "犯罪心理.S01E01.1080P.mkv",
                            "dir": False,
                            "size": 1,
                            "fid": "f1",
                        }
                    ],
                },
            },
            f"{root_url}#/list/share/s02": {
                "success": True,
                "data": {
                    "list": [
                        {
                            "file_name": "犯罪心理.S02E01.720P.mkv",
                            "dir": False,
                            "size": 1,
                            "fid": "f2",
                        }
                    ],
                },
            },
        }

        def opener(request, timeout=30):
            body = json.loads(request.data)
            payload = responses[body["shareurl"]]
            return FakeResponse(payload)

        client = QasClient("http://nas:5005", "token", opener=opener)
        result = client.get_share_preview(
            root_url,
            rng=__import__("random").Random(0),
        )

        sample_names = [
            item["file_name"]
            for item in result["list"]
            if not item.get("dir")
        ]
        self.assertEqual(len(sample_names), 1)
        self.assertTrue(sample_names[0].endswith(".mkv"))
        self.assertEqual(result["preview"]["mode"], "sample")
        self.assertLessEqual(result["preview"]["requests"], 4)
        self.assertEqual(result["share"]["file_only_num"], 327)

    def test_http_error_never_contains_token(self):
        error = urllib.error.HTTPError(
            "http://nas:5005/data?token=top-secret",
            500,
            "Server Error",
            {},
            io.BytesIO(b"failed"),
        )
        client = QasClient(
            "http://nas:5005",
            "top-secret",
            opener=Mock(side_effect=error),
        )

        with self.assertRaises(ClientError) as raised:
            client.get_config()

        self.assertNotIn("top-secret", str(raised.exception))
        self.assertIn("HTTP 500", str(raised.exception))

    def test_malformed_json_raises_client_error(self):
        client = QasClient(
            "http://nas:5005",
            "token",
            opener=Mock(return_value=FakeResponse(b"not-json")),
        )

        with self.assertRaisesRegex(ClientError, "invalid JSON"):
            client.get_config()


class Aria2ClientTests(unittest.TestCase):
    def test_rpc_injects_token_without_exposing_it(self):
        opener = Mock(
            return_value=FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": "resource-download-agent",
                    "result": {"version": "1.36.0"},
                }
            )
        )
        client = Aria2Client(
            "http://127.0.0.1:6801/jsonrpc",
            "rpc-secret",
            opener=opener,
        )

        result = client.get_version()

        request = opener.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body["params"][0], "token:rpc-secret")
        self.assertEqual(result["version"], "1.36.0")
        self.assertNotIn("rpc-secret", repr(client))

    def test_status_requests_bounded_fields(self):
        opener = Mock(
            return_value=FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": "resource-download-agent",
                    "result": [],
                }
            )
        )
        client = Aria2Client("http://aria2/jsonrpc", "secret", opener=opener)

        self.assertEqual(client.tell_active(), [])

        body = json.loads(opener.call_args.args[0].data)
        self.assertEqual(body["method"], "aria2.tellActive")
        self.assertIn("completedLength", body["params"][1])
        self.assertIn("errorMessage", body["params"][1])

    def test_stopped_result_can_be_removed(self):
        opener = Mock(
            return_value=FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": "resource-download-agent",
                    "result": "abc",
                }
            )
        )
        client = Aria2Client("http://aria2/jsonrpc", "secret", opener=opener)

        self.assertEqual(client.remove_result("abc"), "abc")

        body = json.loads(opener.call_args.args[0].data)
        self.assertEqual(body["method"], "aria2.removeDownloadResult")


if __name__ == "__main__":
    unittest.main()
