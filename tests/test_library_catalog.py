import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from library_catalog import LibraryCatalog, query_title


class LibraryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "Anime"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_quoted_title_is_extracted_before_intent_words(self):
        self.assertEqual(
            query_title("搜索《凡人修仙传》动画资源，先预览，不要下载"),
            "凡人修仙传",
        )

    def test_lookup_finds_normalized_title_and_lists_episodes(self):
        season = self.root / "凡人修仙传 (2020)" / "Season 01"
        season.mkdir(parents=True)
        (season / "凡人修仙传 (2020) - S01E001 - 风起天南.mkv").write_bytes(
            b"x"
        )
        (season / "凡人修仙传 (2020) - S01E002.mkv").write_bytes(b"x")
        (season / "凡人修仙传 (2020) - S01E002.ass").write_text(
            "subtitle",
            encoding="utf-8",
        )

        result = LibraryCatalog({"anime": self.root}).lookup(
            "搜索《凡人修仙传》动画资源",
            "anime",
        )

        self.assertTrue(result["found"])
        self.assertEqual(result["title"], "凡人修仙传")
        self.assertEqual(result["fileCount"], 2)
        self.assertEqual(
            result["episodes"],
            [
                {"season": 1, "episode": 1},
                {"season": 1, "episode": 2},
            ],
        )
        self.assertEqual(result["path"], str((self.root / "凡人修仙传 (2020)").resolve()))

    def test_lookup_does_not_match_similar_but_different_title(self):
        (self.root / "凡人修仙记").mkdir()

        result = LibraryCatalog({"anime": self.root}).lookup(
            "凡人修仙传",
            "anime",
        )

        self.assertFalse(result["found"])

    def test_catalog_skips_hidden_download_area(self):
        incoming = self.root / ".incoming" / "凡人修仙传"
        incoming.mkdir(parents=True)
        (incoming / "凡人修仙传.S01E001.mkv").write_bytes(b"x")

        result = LibraryCatalog({"anime": self.root}).lookup(
            "凡人修仙传",
            "anime",
        )

        self.assertFalse(result["found"])

    def test_catalog_skips_symlinked_work_directory(self):
        outside = self.base / "outside" / "凡人修仙传"
        outside.mkdir(parents=True)
        (outside / "凡人修仙传.S01E001.mkv").write_bytes(b"x")
        link = self.root / "凡人修仙传"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")

        result = LibraryCatalog({"anime": self.root}).lookup(
            "凡人修仙传",
            "anime",
        )

        self.assertFalse(result["found"])

    def test_unknown_media_type_searches_all_configured_roots(self):
        work = self.root / "凡人修仙传"
        work.mkdir()
        (work / "凡人修仙传.S01E001.1080P.mkv").write_bytes(b"x")

        result = LibraryCatalog({"anime": self.root}).lookup(
            "凡人修仙传",
            None,
        )

        self.assertTrue(result["found"])
        self.assertEqual(result["mediaType"], "anime")
        self.assertEqual(result["resolutions"], ["1080p"])


if __name__ == "__main__":
    unittest.main()
