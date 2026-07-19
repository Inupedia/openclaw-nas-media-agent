import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from library_scanner import health, scan


class LibraryScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        movie = self.root / "Movie (2024)"
        show = self.root / "Drama" / "Show" / "Season 01"
        movie.mkdir()
        show.mkdir(parents=True)
        (movie / "Movie (2024).mkv").write_bytes(b"video")
        (movie / "Movie (2024).zh.ass").write_text("subtitle")
        (show / "Show - S01E01.mkv").write_bytes(b"episode")
        (show / "Show - S01E02.mkv").write_bytes(b"")
        (show / "orphan.srt").write_text("subtitle")
        (show / "download.aria2").write_text("control")
        stale = show / "old.part"
        stale.write_bytes(b"partial")
        old = time.time() - 9 * 86400
        os.utime(stale, (old, old))
        ignored = self.root / ".incoming" / "ignored.mkv"
        ignored.parent.mkdir()
        ignored.write_bytes(b"x")

    def tearDown(self):
        self.temp.cleanup()

    def test_scan_skips_staging_and_collects_media_metadata(self):
        entries = scan(self.root)
        paths = {entry.path.name for entry in entries}
        self.assertIn("Show - S01E01.mkv", paths)
        self.assertNotIn("ignored.mkv", paths)
        episode = next(e for e in entries if e.path.name.endswith("E01.mkv"))
        self.assertEqual((episode.season, episode.episode), (1, 1))

    def test_scan_parses_bare_leading_episode_numbers(self):
        anime = self.root / "Anime" / "牧神记"
        anime.mkdir(parents=True)
        (anime / "01 4K.mp4").write_bytes(b"a")
        (anime / "91 4K.mp4").write_bytes(b"b")
        entries = {
            entry.path.name: entry
            for entry in scan(anime)
            if entry.kind == "video"
        }
        self.assertEqual(entries["01 4K.mp4"].episode, 1)
        self.assertEqual(entries["91 4K.mp4"].episode, 91)

    def test_health_separates_problems(self):
        report = health(scan(self.root), now=time.time())
        self.assertEqual(report["healthyMedia"], 2)
        self.assertEqual(report["zeroByteMedia"], 1)
        self.assertEqual(report["orphanSubtitles"], 1)
        self.assertEqual(report["controlFiles"], 1)
        self.assertEqual(report["stalePartFiles"], 1)


if __name__ == "__main__":
    unittest.main()
