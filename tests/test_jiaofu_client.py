import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from jiaofu_client import JiaofuClient, JiaofuError, title_matches_query


class JiaofuClientTests(unittest.TestCase):
    def test_title_matches_query_accepts_sequel_base(self):
        self.assertTrue(title_matches_query("幼女战记2", "幼女战记 第二季 1080P"))
        self.assertTrue(title_matches_query("幼女战记2", "【幼女战记2】全12集"))
        self.assertFalse(title_matches_query("幼女战记2", "间谍过家家"))

    def test_search_filters_and_caps_browser_results(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state.json"
            state.write_text("{}", encoding="utf-8")

            def fake_browser(query):
                return [
                    {
                        "title": "幼女战记 第二季",
                        "shareurl": "https://pan.quark.cn/s/abc123",
                    },
                    {
                        "title": "无关资源",
                        "shareurl": "https://pan.quark.cn/s/other",
                    },
                    {
                        "title": "幼女战记2 合集",
                        "shareurl": "https://pan.quark.cn/s/abc123",
                    },
                    {
                        "title": "幼女战记 S02",
                        "shareurl": "https://pan.quark.cn/s/def456",
                    },
                ]

            client = JiaofuClient(
                state,
                max_candidates=1,
                browser_factory=fake_browser,
            )
            results = client.search("幼女战记2")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["shareurl"], "https://pan.quark.cn/s/abc123")

    def test_missing_storage_state_raises(self):
        with self.assertRaises(JiaofuError):
            JiaofuClient("/missing/jiaofu_storage_state.json")


if __name__ == "__main__":
    unittest.main()
