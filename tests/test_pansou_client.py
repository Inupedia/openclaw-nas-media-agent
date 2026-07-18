import json
import sys
import unittest
import urllib.error
import urllib.parse
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pansou_client import PanSouClient, PanSouError, query_variants


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class RecordingOpener:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.payload, Exception):
            raise self.payload
        return FakeResponse(self.payload)


class QueryVariantTests(unittest.TestCase):
    def test_attached_sequel_number_gets_ordered_variants(self):
        self.assertEqual(
            query_variants("幼女战记2"),
            ["幼女战记2", "幼女战记", "幼女战记 第2季", "幼女战记 S02"],
        )

    def test_numeric_titles_are_not_expanded(self):
        self.assertEqual(query_variants("2046"), ["2046"])
        self.assertEqual(query_variants("86"), ["86"])


class PanSouClientTests(unittest.TestCase):
    @staticmethod
    def _payload(items):
        return json.dumps(
            {
                "code": 0,
                "message": "success",
                "data": {"merged_by_type": {"quark": items}},
            },
            ensure_ascii=False,
        ).encode("utf-8")

    def test_search_projects_only_normalized_quark_results(self):
        opener = RecordingOpener(
            self._payload(
                [
                    {
                        "url": "https://pan.quark.cn/s/valid-one/?pwd=secret#part",
                        "note": "幼女战记 第二季 1080P",
                        "datetime": "2026-07-18",
                    },
                    {"url": "https://example.com/not-quark", "note": "ignore"},
                    {"url": "http://pan.quark.cn/s/insecure", "note": "ignore"},
                ]
            )
        )
        client = PanSouClient("http://private-pansou:8888", opener=opener)

        results = client.search("幼女战记2")

        self.assertEqual(
            results,
            [
                {
                    "taskname": "幼女战记 第二季 1080P",
                    "shareurl": "https://pan.quark.cn/s/valid-one",
                    "discoverySource": "pansou",
                    "datetime": "2026-07-18",
                }
            ],
        )
        request, timeout = opener.requests[0]
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertEqual(params["kw"], ["幼女战记2"])
        self.assertEqual(params["cloud_types"], ["quark"])
        self.assertEqual(params["res"], ["all"])
        self.assertEqual(params["src"], ["all"])
        self.assertEqual(timeout, 30)

    def test_limit_is_applied_after_url_deduplication(self):
        items = [
            {"url": "https://pan.quark.cn/s/duplicate", "note": "first"},
            {"url": "https://pan.quark.cn/s/duplicate/", "note": "duplicate"},
            {"url": "https://pan.quark.cn/s/second", "note": "second"},
            {"url": "https://pan.quark.cn/s/third", "note": "third"},
        ]
        client = PanSouClient(
            "http://private-pansou:8888",
            opener=RecordingOpener(self._payload(items)),
            max_candidates=2,
        )

        results = client.search("example")

        self.assertEqual(
            [item["taskname"] for item in results],
            ["first", "second"],
        )

    def test_repr_and_errors_never_expose_endpoint_or_response(self):
        endpoint = "http://private-pansou:8888"
        client = PanSouClient(endpoint, opener=RecordingOpener(b"not-json"))

        self.assertNotIn(endpoint, repr(client))
        with self.assertRaisesRegex(PanSouError, "invalid JSON") as caught:
            client.search("example")
        self.assertNotIn(endpoint, str(caught.exception))
        self.assertNotIn("not-json", str(caught.exception))

    def test_network_error_is_bounded(self):
        private_reason = "connection to private-pansou failed"
        opener = RecordingOpener(urllib.error.URLError(private_reason))
        client = PanSouClient("http://private-pansou:8888", opener=opener)

        with self.assertRaisesRegex(PanSouError, "connection failed") as caught:
            client.search("example")

        self.assertNotIn(private_reason, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
