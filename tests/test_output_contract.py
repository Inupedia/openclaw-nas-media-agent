import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from output_contract import failure, safe_project, success


class OutputContractTests(unittest.TestCase):
    def test_success_has_stable_schema(self):
        result = success(
            {"local": []},
            terminal=True,
            next_action="stop_local_exists",
        )

        self.assertEqual(
            result,
            {
                "schemaVersion": 1,
                "ok": True,
                "terminal": True,
                "nextAction": "stop_local_exists",
                "data": {"local": []},
                "error": None,
            },
        )

    def test_projection_removes_secrets_recursively(self):
        source = {
            "title": "Example",
            "cookie": "danger",
            "nested": {"Authorization": "Bearer danger"},
            "response": {
                "file_name": "E01.mkv",
                "set-cookie": "danger",
            },
        }

        serialized = json.dumps(safe_project(source), ensure_ascii=False).lower()

        self.assertNotIn("danger", serialized)
        self.assertNotIn("cookie", serialized)
        self.assertNotIn("authorization", serialized)
        self.assertIn("e01.mkv", serialized)

    def test_projection_sanitizes_stringified_json(self):
        source = {
            "raw": '{"token":"danger","file_name":"E01.mkv"}',
        }

        serialized = json.dumps(safe_project(source), ensure_ascii=False).lower()

        self.assertNotIn("danger", serialized)
        self.assertNotIn("token", serialized)
        self.assertIn("e01.mkv", serialized)

    def test_failure_never_returns_tracebacks(self):
        result = failure(
            "QAS_TIMEOUT",
            "request failed",
            next_action="retry_later",
        )

        self.assertEqual(
            result["error"],
            {"code": "QAS_TIMEOUT", "message": "request failed"},
        )
        self.assertNotIn("traceback", json.dumps(result).lower())


if __name__ == "__main__":
    unittest.main()
