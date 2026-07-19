import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from videomgr_client import VideomgrClient, _normalize_search_payload


class VideomgrClientTests(unittest.TestCase):
    def test_normalize_search_payload_extracts_paths(self):
        payload = {
            "movies_list": {
                "video_arr": [
                    {
                        "video_info": {
                            "name": "星际穿越",
                            "year": 2014,
                            "type": 1,
                            "tmdb_id": 157336,
                            "score": 8.48,
                            "file_path": [
                                "/volume3/临时影视/Movie/星际穿越 Interstellar (2014).mkv"
                            ],
                        }
                    }
                ]
            }
        }
        results = _normalize_search_payload(payload)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "星际穿越")
        self.assertEqual(results[0]["mediaType"], "movie")
        self.assertEqual(results[0]["year"], 2014)
        self.assertTrue(results[0]["filePaths"][0].endswith(".mkv"))

    def test_search_appends_token_query_and_parses_success(self):
        body = json.dumps(
            {
                "code": 200,
                "msg": "success",
                "data": {
                    "movies_list": {
                        "video_arr": [
                            {
                                "video_info": {
                                    "name": "星际穿越",
                                    "year": 2014,
                                    "type": 1,
                                    "file_path": ["/data/星际穿越.mkv"],
                                }
                            }
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ).encode()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return body

        client = VideomgrClient(
            base_url="http://videomgr.invalid",
            token="abc123",
        )
        with patch(
            "videomgr_client.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as mocked:
            results = client.search("星际穿越")

        self.assertEqual(len(results), 1)
        requested = mocked.call_args[0][0]
        url = requested.full_url if hasattr(requested, "full_url") else str(requested)
        self.assertIn("token=abc123", url)
        self.assertIn("keyword=", url)

    def test_search_skips_expired_token_and_tries_next(self):
        expired = json.dumps({"code": 1024, "msg": "expired", "data": {}}).encode()
        ok = json.dumps(
            {
                "code": 200,
                "data": {
                    "movies_list": {
                        "video_arr": [
                            {
                                "video_info": {
                                    "name": "星际穿越",
                                    "type": 1,
                                    "file_path": ["/data/a.mkv"],
                                }
                            }
                        ]
                    }
                },
            }
        ).encode()
        responses = [expired, ok]

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self.payload

        client = VideomgrClient(
            base_url="http://videomgr.invalid",
            token_provider=lambda: ["dead", "live"],
        )

        def fake_open(request, timeout=0):
            return FakeResponse(responses.pop(0))

        with patch("videomgr_client.urllib.request.urlopen", side_effect=fake_open):
            results = client.search("星际穿越")

        self.assertEqual(results[0]["name"], "星际穿越")


if __name__ == "__main__":
    unittest.main()
