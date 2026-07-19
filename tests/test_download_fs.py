import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from download_fs import (
    AGENT_DIR_MODE,
    ARIA2_DIR_MODE,
    DownloadFsError,
    ensure_aria2_writable,
    ensure_managed_download_roots,
    is_world_writable,
)


class DownloadFsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ensure_aria2_writable_sets_world_writable_under_incoming(self):
        downloads = self.base / "downloads"
        target = downloads / ".incoming" / "rd-demo"
        ensure_aria2_writable(target, downloads_root=downloads)
        self.assertTrue(target.is_dir())
        self.assertEqual(target.stat().st_mode & 0o777, ARIA2_DIR_MODE)
        self.assertTrue(is_world_writable(target))
        self.assertTrue(is_world_writable(target.parent))

    def test_ready_and_quarantine_are_not_world_writable(self):
        downloads = self.base / "volume2" / "downloads"
        roots = ensure_managed_download_roots(downloads)
        self.assertEqual(len(roots), 4)
        incoming = downloads / ".incoming"
        ready = downloads / ".ready"
        quarantine = downloads / ".quarantine"
        self.assertTrue(is_world_writable(incoming))
        if os.name == "posix":
            self.assertFalse(is_world_writable(ready))
            self.assertFalse(is_world_writable(quarantine))
            self.assertEqual(ready.stat().st_mode & 0o777, AGENT_DIR_MODE)
            self.assertEqual(quarantine.stat().st_mode & 0o777, AGENT_DIR_MODE)
        else:
            self.assertTrue(ready.is_dir())
            self.assertTrue(quarantine.is_dir())

    def test_refuses_chmod_outside_downloads_root(self):
        downloads = self.base / "downloads"
        downloads.mkdir()
        outside = self.base / "other"
        with self.assertRaises(DownloadFsError):
            ensure_aria2_writable(outside, downloads_root=downloads)


if __name__ == "__main__":
    unittest.main()
