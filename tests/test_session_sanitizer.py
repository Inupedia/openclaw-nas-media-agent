import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from session_sanitizer import SanitizerError, sanitize_jsonl


class SessionSanitizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_sanitizer_redacts_nested_and_stringified_secrets(self):
        source = self.root / "session.jsonl"
        records = [
            {
                "type": "toolResult",
                "content": '{"cookie":"secret-cookie","title":"凡人修仙传"}',
            },
            {
                "type": "message",
                "content": {
                    "title": "保留内容",
                    "Authorization": "Bearer secret-token",
                },
            },
        ]
        source.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        destination = self.root / "sanitized.jsonl"

        report = sanitize_jsonl(source, destination)

        text = destination.read_text(encoding="utf-8")
        self.assertNotIn("secret-cookie", text)
        self.assertNotIn("secret-token", text)
        self.assertIn("凡人修仙传", text)
        self.assertIn("保留内容", text)
        self.assertEqual(report.total_records, 2)
        self.assertEqual(report.redacted_records, 2)

    def test_malformed_credential_line_is_replaced(self):
        source = self.root / "session.jsonl"
        source.write_text(
            "not-json cookie=secret-cookie\n",
            encoding="utf-8",
        )
        destination = self.root / "sanitized.jsonl"

        report = sanitize_jsonl(source, destination)

        text = destination.read_text(encoding="utf-8")
        self.assertNotIn("secret-cookie", text)
        self.assertIn("securityRedaction", text)
        self.assertEqual(report.redacted_records, 1)

    def test_source_cannot_be_modified_in_place(self):
        source = self.root / "session.jsonl"
        source.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(SanitizerError, "destination"):
            sanitize_jsonl(source, source)


if __name__ == "__main__":
    unittest.main()
