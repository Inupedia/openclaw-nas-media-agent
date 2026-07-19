import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from qas_client import ClientError
from quark_client import QuarkClient


class QuarkClientTests(unittest.TestCase):
    def test_list_files_and_download_entries(self):
        responses = [
            {
                "code": 0,
                "data": [{"fid": "dir1"}],
            },
            {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "fid": "f1",
                            "file_name": "ep.mkv",
                            "size": 12,
                            "dir": False,
                            "file_type": 1,
                        },
                        {
                            "fid": "d1",
                            "file_name": "subdir",
                            "dir": True,
                            "file_type": 0,
                        },
                    ]
                },
                "metadata": {"_total": 2},
            },
            {
                "code": 0,
                "data": [
                    {
                        "fid": "f1",
                        "file_name": "ep.mkv",
                        "download_url": "https://cdn.example/ep.mkv",
                    }
                ],
            },
        ]
        opener = MagicMock()

        def fake_urlopen(request, timeout=30):
            payload = responses.pop(0)
            response = MagicMock()
            response.read.return_value = json.dumps(payload).encode("utf-8")
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            return response

        opener.side_effect = fake_urlopen
        client = QuarkClient("cookie=abc", opener=opener)
        files = client.list_files("/OpenClaw/TV/rd-1")
        self.assertEqual(files, [{"fid": "f1", "name": "ep.mkv", "size": 12}])
        entries = client.download_entries(["f1"])
        self.assertEqual(
            entries,
            [
                {
                    "fid": "f1",
                    "name": "ep.mkv",
                    "download_url": "https://cdn.example/ep.mkv",
                }
            ],
        )

    def test_empty_cookie_rejected(self):
        with self.assertRaises(ClientError):
            QuarkClient("")


if __name__ == "__main__":
    unittest.main()
